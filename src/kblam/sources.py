"""File references and the one source reader per validation (SPEC §5.2.2 File references, §5.2.6)."""

from __future__ import annotations

import collections
import enum
import hashlib
from dataclasses import dataclass
from pathlib import Path

from kblam import gitpin, paths
from kblam.config import Config
from kblam.gitpin import GitPin

# What a STALE or UNAVAILABLE reference says. K13 writes the record-specific sentence from the state
# (SPEC §5.2.2 resolver); these are the short generic lines it builds on, and the parenthesis after
# MESSAGE_NO_COPY names what was missing.
MESSAGE_UNREADABLE = "the path cannot be read as a file"
MESSAGE_MISSING = "the working file is missing"
MESSAGE_OTHER_BYTES = "the working file has other bytes"
MESSAGE_NO_COPY = "no copy with these bytes can be read"
MESSAGE_NO_SHA256 = "the reference has no sha256 to check"
MESSAGE_NO_SNAPSHOT = "the snapshot is missing or has other bytes"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_key(cfg: Config, raw) -> str | None:
    """paths.canonical_key of a value a record supplied, or None when the value is no path at all."""
    if not isinstance(raw, str):
        return None
    try:
        return paths.canonical_key(cfg, raw)
    except paths.PathRefused:
        return None


@dataclass(frozen=True)
class FileRef:
    """A challenge's source, a basis entry or a decision's evidence entry (SPEC §5.2.2).

    Values as parsed: records.schema_issues reports bad types, so resolve() must tolerate any value
    here that is not a string (treat it as missing).
    """
    path: str
    sha256: str | None
    repo: str | None
    commit: str | None
    blob: str | None
    snapshot: str | None

    @property
    def pin(self) -> GitPin | None:
        """The Git pin, when repo, commit and blob are all set."""
        if self.repo is not None and self.commit is not None and self.blob is not None:
            return GitPin(self.repo, self.commit, self.blob)
        return None

    @property
    def pinned(self) -> bool:
        """A Git pin or a snapshot; without either the reference is provisional."""
        return self.pin is not None or self.snapshot is not None


class State(enum.Enum):
    CURRENT = "current"          # the working file hashes to sha256
    PINNED = "pinned"            # the verified blob, or the snapshot, hashes to sha256
    STALE = "stale"              # provisional, and the working file has other bytes
    UNAVAILABLE = "unavailable"  # pinned with no copy of those bytes readable; or provisional and missing

    @property
    def available(self) -> bool:
        return self in (State.CURRENT, State.PINNED)


@dataclass(frozen=True)
class Resolved:
    state: State
    data: bytes | None        # the bytes that hash to sha256, when available
    message: str              # for STALE and UNAVAILABLE, what the diagnostic says; "" otherwise
    key: str | None = None    # the canonical key of ref.path; None when the path is refused
    error: str | None = None  # a structural problem (path refused, a directory, an invalid pin, a basis
                              # sha256 that differs from the source's): an error at every status


class SourceReader:
    """Serves every source read of one validation (K4, K5, K10, K13-K15): each identity is read once.

    Cache keys, which are also the `reads` identities:
      ("working", canonical key)           a working-tree file; findings/ and review-root paths come
                                           from the view (absent there means missing)
      ("blob", str(toplevel), oid)         a Git blob, read with gitpin.read_blob
      ("snapshot", canonical key, sha256)  a snapshot file
    Each entry holds the bytes (or None). `reads[identity]` counts actual reads; a cache hit does not
    count, so tests can assert one read per identity. `matches` is the per-finding-revision cache that
    matching.finding_matches uses.
    """

    def __init__(self, cfg: Config, view, *, trust_state: bool = True) -> None:
        self.cfg = cfg
        self.view = view
        self.trust_state = trust_state
        self.reads: collections.Counter = collections.Counter()
        self.matches: dict = {}
        self._cache: dict = {}
        self._view_keys: dict[str, dict[str, str]] = {}   # "findings"/"review" -> canonical key -> path

    def _untrusted_state(self, target: Path) -> bool:
        return (not self.trust_state
                and paths._under(target.resolve(strict=False), self.cfg.state_dir.resolve(strict=False)))

    def state_refused(self, raw) -> bool:
        """Whether `raw` names a path this reader refuses to read for trust: it lies under `.kblam/`
        and this validation does not trust machine state (SPEC §5.2.6 Committed state). The bytes are
        never read, however sound the file is, so a reference to such a path is unavailable here and no
        step that restores those bytes can work from this clone. A value that is not a path at all, or
        one paths.resolve refuses, is False: the reference's own error reports it."""
        if not isinstance(raw, str):
            return False
        try:
            target = paths.resolve(self.cfg, raw)
        except paths.PathRefused:
            return False
        return self._untrusted_state(target)

    def refused_for_trust(self, ref: FileRef) -> str | None:
        """The first path `ref` names — the reference's own path, its snapshot, or the repository its pin
        names — that this reader refuses to read for trust (state_refused), or None when it names no such
        path. Those bytes are unread however sound they are, so the reference is unavailable here and no
        step that restores them can work from this validation."""
        if self.trust_state:
            return None
        for raw in (ref.path, ref.snapshot, ref.repo):
            if isinstance(raw, str) and self.state_refused(raw):
                return raw
        return None

    def working(self, raw: str) -> bytes | None:
        """The working bytes of a repo-relative path; None if it is refused, missing or a directory."""
        try:
            key = paths.canonical_key(self.cfg, raw)
            target = paths.resolve(self.cfg, raw)
        except paths.PathRefused:
            return None
        if self._untrusted_state(target):
            return None
        return self._once(("working", key), lambda: self._working_bytes(key, target))

    def verbatim(self, full: Path) -> bytes | None:
        """K10's accepted path, using the same snapshot as record reads without narrowing its syntax."""
        if self._untrusted_state(full):
            return None
        rel = full.relative_to(self.cfg.repo_root.resolve()).as_posix()
        key = _canonical_key(self.cfg, rel)
        if key is not None:
            return self._once(("working", key), lambda: self._working_bytes(key, full))
        # K10 accepts names (for example a colon) that record paths do not. They still use candidate
        # bytes under either protected root, and each accepted resolved path is read only once.
        def read():
            if rel in self.view.files:
                return self.view.files[rel]
            if rel in self.view.review_files:
                return self.view.review_files[rel]
            if rel.startswith((self.cfg.findings_dir + "/", self.cfg.review_dir + "/")):
                return None
            return _read_file(full)

        return self._once(("verbatim", str(full)), read)

    def blob(self, toplevel: Path, oid: str) -> bytes | None:
        if self._untrusted_state(toplevel):
            return None
        return self._once(("blob", str(toplevel), oid), lambda: gitpin.read_blob(toplevel, oid))

    def snapshot(self, raw: str, sha256: str) -> bytes | None:
        """The snapshot file's bytes if they hash to sha256; else None."""
        try:
            key = paths.canonical_key(self.cfg, raw)
            target = paths.resolve(self.cfg, raw)
        except paths.PathRefused:
            return None
        if self._untrusted_state(target):
            return None
        return self._once(("snapshot", key, sha256), lambda: self._snapshot_bytes(target, sha256))

    def resolve(self, ref: FileRef, *, pinned_source: FileRef | None = None) -> Resolved:
        """Evaluate a reference (SPEC §5.2.2): the first of current, pinned, stale, unavailable that
        applies.

        A pin is always verified (gitpin.verify_pin with read=self.blob): "invalid" sets `error`, "absent"
        only makes the pin unusable. `pinned_source`: when `ref` has no pin and no snapshot and its
        canonical key equals pinned_source's, `ref` is evaluated with pinned_source's pin and snapshot (a
        basis entry on the source itself, SPEC §5.2.3 Basis), and a ref.sha256 that differs from
        pinned_source.sha256 is an `error`.
        """
        raw = ref.path
        if not isinstance(raw, str):
            return Resolved(State.UNAVAILABLE, None, MESSAGE_UNREADABLE)
        try:
            key = paths.canonical_key(self.cfg, raw)
            target = paths.resolve(self.cfg, raw)
        except paths.PathRefused as exc:
            return Resolved(State.UNAVAILABLE, None, MESSAGE_UNREADABLE, None, f"path {raw!r}: {exc}")
        if self._untrusted_state(target):
            return Resolved(State.UNAVAILABLE, None, MESSAGE_UNREADABLE, key)
        if target.is_dir():
            return Resolved(State.UNAVAILABLE, None, MESSAGE_UNREADABLE, key,
                            f"{raw!r} is a directory, not a file")

        pin = ref.pin
        snapshot = ref.snapshot if isinstance(ref.snapshot, str) else None
        expected = ref.sha256 if isinstance(ref.sha256, str) else None
        error = None
        if pinned_source is not None and pin is None and snapshot is None:
            if _canonical_key(self.cfg, pinned_source.path) == key:
                pin = pinned_source.pin
                if isinstance(pinned_source.snapshot, str):
                    snapshot = pinned_source.snapshot
                if isinstance(pinned_source.sha256, str):
                    if expected is None:
                        expected = pinned_source.sha256
                    elif expected != pinned_source.sha256:
                        error = f"sha256 {expected} differs from the source's {pinned_source.sha256}"
        if expected is None:
            # Nothing to compare bytes against: records.schema_issues reports the type.
            return Resolved(State.UNAVAILABLE, None, MESSAGE_NO_SHA256, key, error)

        if not self.trust_state and pin is not None and isinstance(pin.repo, str):
            try:
                repository = paths.resolve(self.cfg, pin.repo)
            except paths.PathRefused:
                pass  # pin verification below reports the existing structural path error
            else:
                if self._untrusted_state(repository):
                    return Resolved(State.UNAVAILABLE, None, MESSAGE_UNREADABLE, key)
        # The pin is verified even when the working file is current: a wrong pin is a structural error.
        working = self.working(raw)
        pinned_bytes = None
        reason = ""
        if pin is not None:
            check = gitpin.verify_pin(self.cfg, raw, expected, pin, read=self.blob)
            if check.status == "ok":
                pinned_bytes = check.data
            else:
                reason = check.message
                if check.status == "invalid" and error is None:
                    error = check.message

        if working is not None and sha256_hex(working) == expected:
            return Resolved(State.CURRENT, working, "", key, error)
        if pinned_bytes is not None:
            return Resolved(State.PINNED, pinned_bytes, "", key, error)
        if snapshot is not None:
            data = self.snapshot(snapshot, expected)
            if data is not None:
                return Resolved(State.PINNED, data, "", key, error)
            reason = reason or MESSAGE_NO_SNAPSHOT

        if pin is None and snapshot is None:
            if working is None:
                return Resolved(State.UNAVAILABLE, None, MESSAGE_MISSING, key, error)
            return Resolved(State.STALE, None, MESSAGE_OTHER_BYTES, key, error)
        message = MESSAGE_NO_COPY if not reason else f"{MESSAGE_NO_COPY} ({reason})"
        return Resolved(State.UNAVAILABLE, None, message, key, error)

    # --- the reads behind those cache identities ------------------------------------------------

    def _once(self, identity: tuple, read):
        """The bytes of `identity`, read at most once: the first lookup counts one read whether or not it
        found bytes, and a later lookup is a cache hit, including for a None (SPEC §5.2.6)."""
        if identity not in self._cache:
            self.reads[identity] += 1
            self._cache[identity] = read()
        return self._cache[identity]

    def _working_bytes(self, key: str, target: Path) -> bytes | None:
        """The bytes of a repo-relative file: under the findings or review root the view's bytes, which is
        all a validation sees there, and otherwise the file on disk. None when neither holds a file."""
        if self._untrusted_state(target):
            return None
        where = paths.protected(self.cfg, target)
        if where in ("findings", "review"):
            return self._view_bytes(where, key)
        return _read_file(target)

    def _view_bytes(self, where: str, key: str) -> bytes | None:
        """The view's bytes for a canonical key, or None when the view does not hold that path. The view's
        keys are repo-relative POSIX paths in their on-disk case, so they are compared by canonical key
        (case-folded on Windows); the map is built once per reader."""
        names = self.view.files if where == "findings" else self.view.review_files
        index = self._view_keys.get(where)
        if index is None:
            index = {}
            for path in names:
                canonical = _canonical_key(self.cfg, path)
                if canonical is not None:
                    index.setdefault(canonical, path)
            self._view_keys[where] = index
        path = index.get(key)
        return None if path is None else names[path]

    def _snapshot_bytes(self, target: Path, sha256: str) -> bytes | None:
        """The snapshot file's bytes when they hash to sha256, else None: a copy with other bytes serves a
        reference no better than no copy at all."""
        if self._untrusted_state(target):
            return None
        data = _read_file(target)
        return data if data is not None and sha256_hex(data) == sha256 else None


def _read_file(target: Path) -> bytes | None:
    """A regular file's bytes; None when it is missing, a directory or unreadable (validation never
    raises on a source it cannot read: the reference is then unavailable)."""
    try:
        return target.read_bytes() if target.is_file() else None
    except OSError:
        return None
