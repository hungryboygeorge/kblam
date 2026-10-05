"""Paths in review records, verbatim tags and decision evidence (SPEC §5.1.2 Paths).

One function gives every path its canonical key; K10, the records, decision evidence and K13 all
use it. Records keep the path as written, for display.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from kblam.config import STATE_DIR, Config

DRIVE_RE = re.compile(r"^[A-Za-z]:")


class PathRefused(ValueError):
    """A path kblam will not use: bad syntax, or it resolves outside the repository. str() is the reason."""


def syntax_problem(raw: str) -> str | None:
    """Why `raw` is not a repo-relative path, or None.

    Refused: empty, absolute (`/x`, `\\x`), a `..` segment, a drive letter (`C:/x`) or drive-relative
    path (`C:x`), a UNC path (`\\\\host\\share`, `//host/share`), and any `:` (an alternate data
    stream). Backslashes count as separators. Filesystem-free.
    """
    if not raw:
        return "empty path"
    slash = raw.replace("\\", "/")
    if slash.startswith("//"):
        return "UNC path"
    if slash.startswith("/"):
        return "absolute path"
    if DRIVE_RE.match(slash):
        return "drive letter" if slash[2:3] == "/" else "drive-relative path"
    if ":" in slash:
        return "':' (an alternate data stream)"
    if ".." in slash.split("/"):
        return "'..' segment"
    return None


def _segments(raw: str) -> list[str]:
    """`raw` as path segments: backslashes read as separators, empty and `.` segments dropped."""
    return [part for part in raw.replace("\\", "/").split("/") if part not in ("", ".")]


def _under(target: Path, root: Path) -> bool:
    """`target` is `root` or lies under it, case-insensitively on Windows."""
    target_text, root_text = os.path.normcase(str(target)), os.path.normcase(str(root))
    if target_text == root_text:
        return True
    if not root_text.endswith(os.sep):
        root_text += os.sep
    return target_text.startswith(root_text)


def _relative(target: Path, root: Path) -> str:
    """What `target` is below `root`, from their normcased text (case-folded on Windows), same separators."""
    target_text, root_text = os.path.normcase(str(target)), os.path.normcase(str(root))
    return target_text[len(root_text.rstrip(os.sep)) + 1:]


def resolve(cfg: Config, raw: str) -> Path:
    """The absolute path `raw` names, symlinks followed (Path.resolve(strict=False)).

    Raises PathRefused for a syntax problem, or when the resolved target is outside cfg.repo_root.
    The target need not exist.
    """
    problem = syntax_problem(raw)
    if problem is not None:
        raise PathRefused(problem)
    root = cfg.repo_root.resolve()
    target = root.joinpath(*_segments(raw)).resolve(strict=False)
    if not _under(target, root):
        raise PathRefused("resolves outside the repository")
    return target


def canonical_key(cfg: Config, raw: str) -> str:
    """The canonical key of `raw`: backslashes become '/', a leading './' is dropped, the path is
    resolved as in resolve(), made relative to the resolved repo root as a POSIX string, and on Windows
    case-folded with os.path.normcase (keeping '/' as the separator). Raises PathRefused as resolve()."""
    target = resolve(cfg, raw)
    key = _relative(target, cfg.repo_root.resolve()).replace(os.sep, "/")
    if not key or key == ".":
        raise PathRefused("the repository root itself")
    return key


def protected(cfg: Config, resolved: Path) -> str | None:
    """The protected root a resolved target is or lies under, or None: 'findings' (cfg.findings_dir),
    'review' (cfg.review_dir), 'state' (.kblam/) or 'history' (a cfg.history_dirs folder). Such a path
    is never a source or primary evidence (SPEC §5.1.2). Compared as resolved paths, case-insensitively
    on Windows."""
    target = Path(resolved).resolve(strict=False)
    root = cfg.repo_root.resolve()
    roots = [("findings", cfg.findings_dir), ("review", cfg.review_dir), ("state", STATE_DIR)]
    roots += [("history", directory) for directory in cfg.history_dirs if directory.strip("/")]
    for name, directory in roots:
        if _under(target, (root / directory).resolve(strict=False)):
            return name
    return None
