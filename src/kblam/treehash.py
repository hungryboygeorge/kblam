"""`.kblam/tree.hash`: a digest of everything under `findings/` (SPEC §3, §8)."""

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
    """True if `digest` (taken before a write) is the tree kblam last recorded, or none is recorded yet."""
    recorded = read_tree_hash(cfg)
    return recorded is None or recorded == digest


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
