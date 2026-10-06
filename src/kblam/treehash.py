"""`.kblam/tree.hash`: a digest of everything under `findings/`, and the tree.hash rule with its
bootstrap (SPEC §3, §8)."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import PurePosixPath

from kblam.config import Config
from kblam.view import KBView, load_view


def tree_digest(view: KBView) -> str:
    """sha256 over the sorted paths (relative to findings/) and bytes of every file."""
    h = hashlib.sha256()
    for path in sorted(view.files, key=lambda p: view.rel_to_findings(p).as_posix()):
        data = view.files[path]
        rel = view.rel_to_findings(path).as_posix().encode("utf-8")
        h.update(rel + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
    return h.hexdigest()


# --- format 2 (SPEC §5.2.6) ---------------------------------------------------------------------

V2_TAG = "kblam-tree-v2"
HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def tree_digest_v2(view: KBView) -> str:
    """sha256 over b"kblam-tree-v2\0<findings root>\0<review root>\0", then, for each file of view.files
    and view.review_files in sorted order of its domain-separated name ("f/<path relative to the findings
    root>" or "r/<path relative to the review root>"), that name (UTF-8), b"\0", its byte length in
    decimal ASCII, b"\0" and its bytes."""
    cfg = view.cfg
    h = hashlib.sha256()
    h.update(V2_TAG.encode("utf-8") + b"\0" + cfg.findings_dir.encode("utf-8") + b"\0"
             + cfg.review_dir.encode("utf-8") + b"\0")
    named: dict[str, bytes] = {}
    for path, data in view.files.items():
        named["f/" + view.rel_to_findings(path).as_posix()] = data
    for path, data in view.review_files.items():
        named["r/" + PurePosixPath(path).relative_to(cfg.review_dir).as_posix()] = data
    for name in sorted(named):
        data = named[name]
        h.update(name.encode("utf-8") + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
    return h.hexdigest()


def format_line(review_root: str, digest: str) -> str:
    """The format-2 file content: "kblam-tree-v2 <review root> <64 hex>\n"."""
    return f"{V2_TAG} {review_root} {digest}\n"


def parse_line(text: str) -> tuple[int, str | None, str]:
    """(format, review root or None, hex) of a tree.hash's text (surrounding whitespace ignored): a bare
    hex line is format 1 with root None; "kblam-tree-v2 <root> <hex>" is format 2. Anything else raises
    ValueError."""
    line = text.strip()
    if HEX_RE.fullmatch(line):
        return 1, None, line
    parts = line.split(" ")
    if len(parts) == 3 and parts[0] == V2_TAG and parts[1] and HEX_RE.fullmatch(parts[2]):
        return 2, parts[1], parts[2]
    raise ValueError(f"unrecognized tree.hash line: {line!r}")


def read_recorded(cfg: Config) -> tuple[int, str | None, str] | None:
    """parse_line of .kblam/tree.hash, or None if there is none. Unparseable text counts as format 1 with
    that text as its digest (it never matches)."""
    path = cfg.state_dir / "tree.hash"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        return parse_line(text)
    except ValueError:
        return 1, None, text.strip()


def write_tree_hash_v2(cfg: Config, view: KBView) -> None:
    """Write format_line(cfg.review_dir, tree_digest_v2(view)) with store.atomic_write."""
    from kblam.store import atomic_write  # avoid an import cycle

    line = format_line(cfg.review_dir, tree_digest_v2(view))
    atomic_write(cfg.state_dir / "tree.hash", line.encode("utf-8"))


def _holds_records(cfg: Config, root: str) -> bool:
    """Whether `<root>/<kind folder>/*.yaml` exists under cfg.repo_root.

    `root` may come from tree.hash, so anything that is not a plain relative POSIX path holds no records:
    the check never looks outside the repository for a file kblam did not write there.
    """
    from kblam.records import KINDS

    if not root or root.startswith("/") or "\\" in root or ":" in root:
        return False
    parts = PurePosixPath(root).parts
    if ".." in parts or not parts:
        return False
    base = cfg.repo_root.joinpath(*parts)
    return any(any(base.glob(f"{folder}/*.yaml")) for folder in set(KINDS.values()))


def root_problem(cfg: Config, registered: set[str] | None) -> str | None:
    """K13's and every mutating command's root check (SPEC §5.2.6): when tree.hash is format 2 and records
    a root other than cfg.review_dir, "the review root changed from X to Y in kblam.toml; schema 1 fixes it
    at init" - unless neither root holds a record file (<root>/<kind folder>/*.yaml) and `registered`
    (registry.read_ids) is None or empty, in which case None. None for format 1 or no tree.hash."""
    recorded = read_recorded(cfg)
    if recorded is None:
        return None
    fmt, recorded_root, _digest = recorded
    if fmt != 2 or recorded_root == cfg.review_dir:
        return None
    if not registered and not _holds_records(cfg, recorded_root) and not _holds_records(cfg, cfg.review_dir):
        return None
    return (f"the review root changed from {recorded_root} to {cfg.review_dir} in kblam.toml; "
            f"schema 1 fixes it at init")


def clean_before_v2(cfg: Config, view: KBView, has_records: bool, *, creates_registry: bool = True) -> bool:
    """The tree.hash rule before a write (SPEC §8, §5.2.6): True if tree.hash is format 2 with this root
    and tree_digest_v2(view). With no marker, an empty KB with no records is kblam's; a populated tree
    bootstraps only when the full deterministic rules, K13-K15 included, report no error, and its
    findings are then accepted from the repository. With records present the bootstrap also creates the
    registry from them, which the caller's write does (writes.registry_after); `rm`, `renumber` and
    `upgrade` write no registry, so they pass creates_registry=False and do not bootstrap while records
    exist. A format-1 marker never matches."""
    recorded = read_recorded(cfg)
    if recorded is None:
        if has_records and not creates_registry:
            return False
        if not view.findings and not has_records:
            return True
        from kblam.rules import errors, validate

        if errors(validate(view)):
            return False
        accept_from_repository(cfg, view)
        return True
    fmt, root, digest = recorded
    return fmt == 2 and root == cfg.review_dir and digest == tree_digest_v2(view)


def accept_from_repository(cfg: Config, view: KBView) -> int:
    """Mark every finding in `view` as checked at its current fingerprint without asking Jev, and return
    how many. For a tree kblam first records on this machine (a new clone, or .kblam/ deleted): the
    committing machines checked its findings, and checking them all again cannot finish in a Stop hook.
    `kblam check` then asks only about what changes after this point; `kblam audit` checks the rest
    (SPEC §8 item 3)."""
    from kblam.finding import fingerprint
    from kblam.jev import CACHE_NAME, PairCache

    cache = PairCache(cfg.state_dir / CACHE_NAME)
    accepted = [f for f in view.findings if f.ok]
    for finding in accepted:
        cache.mark_checked(finding.file_id, fingerprint(finding, cfg.scope_separator))
    return len(accepted)


def record_after_write_v2(cfg: Config, clean_before: bool, command: str, *, creates_registry: bool = True) -> bool:
    """After a write: if clean_before, write the format-2 tree.hash of the tree as it is now
    (load_view) and return True. Otherwise leave tree.hash as it is, print a warning to stderr, and return
    False. The warning names why: a missing tree.hash while records exist, for a command that writes no
    registry (creates_registry=False, as clean_before_v2 takes it); a missing one whose tree failed the
    bootstrap's validation; a format-1 tree.hash; or an out-of-band change."""
    if clean_before:
        write_tree_hash_v2(cfg, load_view(cfg))
        return True
    recorded = read_recorded(cfg)
    if recorded is None and not creates_registry and _holds_records(cfg, cfg.review_dir):
        print(f"kblam {command}: there is no .kblam/tree.hash (a new clone, or .kblam/ was deleted), and kblam "
              f"{command} does not record one while {cfg.review_dir}/ holds review records; tree.hash not "
              f"advanced. Run kblam validate, fix anything it lists, then run kblam validate --record.",
              file=sys.stderr)
    elif recorded is None:
        print(f"kblam {command}: there is no .kblam/tree.hash (a new clone, or .kblam/ was deleted), and the "
              f"tree as it was before this write fails kblam validate, so kblam did not record it; tree.hash "
              f"not advanced. Run kblam validate, fix anything it lists, then run kblam validate --record.",
              file=sys.stderr)
    elif recorded[0] == 1:
        print(f"kblam {command}: .kblam/tree.hash is in the old format; tree.hash not advanced. "
              f"Run kblam validate --record once the tree validates.", file=sys.stderr)
    else:
        print(f"kblam {command}: {cfg.findings_dir}/ or {cfg.review_dir}/ was changed outside kblam since "
              f"kblam last wrote it; tree.hash not advanced. Run kblam validate --record once the change "
              f"is validated.", file=sys.stderr)
    return False
