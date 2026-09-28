"""`.kblam/tree.hash`: a digest of everything under `findings/`, and the tree.hash rule with its
bootstrap (SPEC §3, §8)."""

from __future__ import annotations

import hashlib
import sys

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


def current_digest(cfg: Config) -> str:
    return tree_digest(load_view(cfg))


def write_tree_hash(cfg: Config, digest: str) -> None:
    from kblam.store import atomic_write  # avoid an import cycle

    atomic_write(cfg.state_dir / "tree.hash", (digest + "\n").encode("ascii"))


def read_tree_hash(cfg: Config) -> str | None:
    path = cfg.state_dir / "tree.hash"
    return path.read_text(encoding="ascii").strip() if path.is_file() else None


def as_kblam_left_it(cfg: Config, digest: str) -> bool:
    """True if `digest` (taken before a write, under the lock) is the tree kblam last recorded.

    With no tree.hash yet (a bootstrap: a new KB, a new clone, or .kblam/ deleted), a KB root that holds
    no findings counts as kblam's. One that holds findings counts only if it passes the deterministic
    rules, which the committing machines' pre-commit hooks ran, and its findings are then accepted from
    the repository (accept_from_repository), so later checks cover what changes after this point. A tree
    that fails them stays unrecorded until kblam validate --record (SPEC §8, the tree.hash rule and
    item 3, "A new clone").
    """
    recorded = read_tree_hash(cfg)
    if recorded is not None:
        return recorded == digest
    view = load_view(cfg)
    if not view.findings:
        return True
    from kblam.rules import validate  # the validator: needed only for a bootstrap

    if validate(view):
        return False
    accept_from_repository(cfg, view)
    return True


def accept_from_repository(cfg: Config, view: KBView) -> int:
    """Mark every finding in `view` as checked at its current fingerprint without asking Jev, and return
    how many. For a tree kblam first records on this machine (a new clone, or .kblam/ deleted): the
    committing machines checked its findings, and checking them all again cannot finish in a Stop hook.
    `kblam check` then asks only about what changes after this point; `kblam audit` checks the rest
    (SPEC §8 item 3)."""
    from kblam.finding import fingerprint
    from kblam.jev import CACHE_NAME, PairCache  # the Jev client's module: loaded only when needed

    cache = PairCache(cfg.state_dir / CACHE_NAME)
    accepted = [f for f in view.findings if f.ok]
    for finding in accepted:
        cache.mark_checked(finding.file_id, fingerprint(finding, cfg.scope_separator))
    return len(accepted)


def record_after_write(cfg: Config, clean_before: bool, command: str) -> bool:
    """The tree.hash rule (SPEC §8): advance tree.hash only across kblam's own write.

    If findings/ had been changed outside kblam before the write, tree.hash stays stale so the Stop
    hook still validates that change; `kblam validate --record` is the only way to accept it.
    """
    if clean_before:
        write_tree_hash(cfg, current_digest(cfg))
        return True
    print(f"kblam {command}: {cfg.findings_dir}/ was changed outside kblam since kblam last wrote it; "
          f"tree.hash not advanced. Run kblam validate --record once the change is validated.", file=sys.stderr)
    return False
