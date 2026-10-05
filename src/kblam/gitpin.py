"""Git pins of file references (SPEC §5.1.2 Git pins).

Read-only: kblam never checks out, writes or stages anything in a source repository. Every git call
is `subprocess.run(["git", "-C", <dir>, ...], check=False, capture_output=True)`.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kblam import paths
from kblam.config import Config

HEX_RE = re.compile(r"[0-9a-f]+")


@dataclass(frozen=True)
class GitPin:
    repo: str      # the owning worktree's toplevel relative to the repository root, POSIX; "." for itself
    commit: str    # lowercase hex, 40 (SHA-1) or 64 (SHA-256)
    blob: str


@dataclass(frozen=True)
class PinCheck:
    """What verify_pin found.

    ok: the whole relationship verified; `data` holds the blob's bytes, which hash to sha256.
    absent: the pin may be right but cannot be checked here: the repo directory is missing or is not a
      Git worktree, or the commit or blob object is missing. The reference is then unavailable (SPEC
      §5.1.2 resolver), not structurally wrong. `message` says what is missing.
    invalid: the pin is wrong: `repo` is not the file's owning worktree, an object ID has the wrong
      length or is not lowercase hex, the commit object is not a commit, the commit's tree does not
      hold `blob` at the path, or the blob's bytes do not hash to sha256. A K12 structural error;
      `message` says which.
    """
    status: str            # "ok" | "absent" | "invalid"
    message: str = ""
    data: bytes | None = None


def _git(directory: Path, *args: str) -> subprocess.CompletedProcess:
    """One git command. Nothing kblam runs through here writes to a repository (SPEC §5.1.2)."""
    return subprocess.run(["git", "-C", str(directory), *args], check=False, capture_output=True)


def _out(done: subprocess.CompletedProcess) -> str:
    """A single-value command's stdout as text, without the newline. git prints repository paths as
    UTF-8; surrogateescape keeps a path that is not valid UTF-8 round-trippable on POSIX."""
    return done.stdout.decode("utf-8", "surrogateescape").strip()


def _same(left: Path, right: Path) -> bool:
    """Path equality, case-folded on Windows (SPEC §5.1.2 Paths)."""
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _under(path: Path, base: Path) -> str | None:
    """`path` relative to `base` as a POSIX path, or None when path is not under base; "" when they are
    the same directory. Case-folded on Windows (SPEC §5.1.2 Paths)."""
    full, root = str(path), str(base)
    folded, prefix = os.path.normcase(full), os.path.normcase(root).rstrip(os.sep) + os.sep
    if folded == os.path.normcase(root):
        return ""
    if not folded.startswith(prefix):
        return None
    return Path(full[len(prefix):]).as_posix()


def _existing(directory: Path) -> Path | None:
    """The nearest existing directory at or above `directory`, as a resolved Path, or None."""
    directory = directory.resolve()
    while not directory.is_dir():
        if directory.parent == directory:
            return None
        directory = directory.parent
    return directory


def _toplevel_in(directory: Path) -> Path | None:
    """`git -C <directory> rev-parse --show-toplevel`: the toplevel as a resolved Path, or None when
    that directory is in no worktree."""
    done = _git(directory, "rev-parse", "--show-toplevel")
    if done.returncode:
        return None
    text = _out(done)
    return Path(text).resolve() if text else None


def _tree_blob(toplevel: Path, commit: str, rel: str) -> str | None:
    """The blob object ID that `git ls-tree -z <commit> -- <rel>` maps `rel` to, or None when that tree
    holds no blob there: no entry at all, a directory, or a submodule (an entry of type `commit`)."""
    done = _git(toplevel, "ls-tree", "-z", commit, "--", rel)
    if done.returncode:
        return None
    entry = done.stdout.split(b"\x00", 1)[0]
    meta, _, _path = entry.partition(b"\t")     # "<mode> <type> <oid>\t<path>" (SPEC §5.1.2)
    fields = meta.split(b" ")
    if len(fields) != 3 or fields[1] != b"blob":
        return None
    return fields[2].decode("ascii")


def owning_worktree(path: Path) -> Path | None:
    """`git -C <dir> rev-parse --show-toplevel`, run in the nearest existing directory at or above
    path.parent: the toplevel as a resolved Path, or None if that directory is in no worktree. Nested
    repositories, submodules and linked worktrees are their own toplevel (their `.git` may be a file)."""
    directory = _existing(path.parent)
    return None if directory is None else _toplevel_in(directory)


def oid_length(toplevel: Path) -> int:
    """40 or 64, from `git -C <toplevel> rev-parse --show-object-format` (sha1 or sha256); 40 when this
    git does not know the option."""
    done = _git(toplevel, "rev-parse", "--show-object-format")
    return 64 if not done.returncode and _out(done) == "sha256" else 40


def read_blob(toplevel: Path, blob: str) -> bytes | None:
    """`git -C <toplevel> cat-file blob <blob>`: the raw blob bytes (no filters), or None if absent."""
    done = _git(toplevel, "cat-file", "blob", blob)
    return done.stdout if not done.returncode else None


def verify_pin(cfg: Config, raw_path: str, sha256: str, pin: GitPin, *,
               read: Callable[[Path, str], bytes | None] = read_blob) -> PinCheck:
    """Verify a stored pin (SPEC §5.1.2): cfg.repo_root / pin.repo is the owning worktree of the resolved
    path (paths.resolve); both IDs are lowercase hex of oid_length's length; `cat-file -t <commit>` prints
    commit; `ls-tree <commit> -- <path relative to the toplevel>` maps the path to `blob`; and
    `read(toplevel, blob)` hashes to sha256. Callers pass `read` to share a cache
    (sources.SourceReader.blob). A raw_path that paths refuses is invalid."""
    try:
        target = paths.resolve(cfg, raw_path)
    except paths.PathRefused as exc:
        return PinCheck("invalid", f"path {raw_path!r}: {exc}")
    if not isinstance(pin.repo, str):
        return PinCheck("invalid", f"repo {pin.repo!r} is not a path")
    try:
        recorded = paths.resolve(cfg, pin.repo)
    except paths.PathRefused as exc:
        return PinCheck("invalid", f"repo {pin.repo!r}: {exc}")
    if not recorded.is_dir():
        return PinCheck("absent", f"the repository {pin.repo} is not here")
    toplevel = _toplevel_in(recorded)
    if toplevel is None or not _same(toplevel, recorded):
        return PinCheck("absent", f"{pin.repo} is not the top level of a Git worktree")
    owner = owning_worktree(target)
    if owner is None or not _same(owner, recorded):
        return PinCheck("invalid", f"{pin.repo} is not the worktree that owns {raw_path}")
    length = oid_length(toplevel)
    for name, oid in (("commit", pin.commit), ("blob", pin.blob)):
        if not isinstance(oid, str) or len(oid) != length or HEX_RE.fullmatch(oid) is None:
            return PinCheck("invalid", f"{name} {oid!r} is not {length} lowercase hex digits")
    kind = _git(toplevel, "cat-file", "-t", pin.commit)
    if kind.returncode:
        return PinCheck("absent", f"commit {pin.commit} is not in {pin.repo}")
    if _out(kind) != "commit":
        return PinCheck("invalid", f"{pin.commit} is a {_out(kind)}, not a commit")
    rel = _under(target, toplevel)
    if not rel:
        return PinCheck("invalid", f"{raw_path} is the worktree itself, not a file in it")
    found = _tree_blob(toplevel, pin.commit, rel)
    if found is None:
        return PinCheck("invalid", f"the tree of {pin.commit} holds no blob at {rel}")
    if found != pin.blob:
        return PinCheck("invalid", f"the tree of {pin.commit} holds {found} at {rel}, not {pin.blob}")
    data = read(toplevel, pin.blob)
    if data is None:
        return PinCheck("absent", f"blob {pin.blob} is not in {pin.repo}")
    if hashlib.sha256(data).hexdigest() != sha256:
        return PinCheck("invalid", f"blob {pin.blob} does not hash to {sha256}")
    return PinCheck("ok", "", data)


def auto_pin(cfg: Config, raw_path: str, sha256: str, *,
             read: Callable[[Path, str], bytes | None] = read_blob) -> GitPin | None:
    """The pin to record for a file, or None: the owning worktree's HEAD commit, when HEAD's tree holds a
    blob at the path whose raw bytes hash to `sha256`. `challenge new`, basis entries and `--evidence`
    pass the working file's sha256 (SPEC §5.1.2: pin automatically only when HEAD's blob has exactly the
    working bytes); `challenge pin` passes source.sha256. None when the file is in no worktree, is not in
    HEAD's tree, has other bytes there (a dirty file, or a CRLF checkout of an LF blob), or there is no
    HEAD."""
    try:
        target = paths.resolve(cfg, raw_path)
    except paths.PathRefused:
        return None
    toplevel = owning_worktree(target)
    if toplevel is None:
        return None
    repo = _under(toplevel, cfg.repo_root.resolve())
    if repo is None:
        return None
    rel = _under(target, toplevel)
    if not rel:
        return None
    head = _git(toplevel, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    commit = _out(head) if not head.returncode else ""
    if not commit:
        return None
    blob = _tree_blob(toplevel, commit, rel)
    if blob is None:
        return None
    data = read(toplevel, blob)
    if data is None or hashlib.sha256(data).hexdigest() != sha256:
        return None
    return GitPin(repo or ".", commit, blob)
