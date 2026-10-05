"""An in-memory snapshot of `findings/` and the review root, so rules can run on the tree as it is or as
it would be (SPEC §5.1.6)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path, PurePosixPath

from kblam.config import Config
from kblam.finding import FILENAME_RE, Finding, parse_finding

INDEX_NAME = "INDEX.md"


@dataclass
class KBView:
    cfg: Config
    files: dict[str, bytes]                  # repo-relative POSIX path -> bytes, under cfg.findings_dir
    display: dict[str, str] = field(default_factory=dict)  # path shown in messages, when it differs
    # The review root's files (SPEC §5.1): repo-relative POSIX path -> bytes, under cfg.review_dir.
    # K1-K11 and K8 see only `files`.
    review_files: dict[str, bytes] = field(default_factory=dict)
    # Paths under the review root that are symlinks: neither a record nor the index may be one (K12).
    # A flagged path need not be in review_files (a dangling link, or a link to a directory).
    review_symlinks: set[str] = field(default_factory=set)

    @property
    def index_path(self) -> str:
        return f"{self.cfg.findings_dir}/{INDEX_NAME}"

    def rel_to_findings(self, path: str) -> PurePosixPath:
        return PurePosixPath(path).relative_to(self.cfg.findings_dir)

    def is_finding_path(self, path: str) -> bool:
        """A finding file directly inside a topic folder; anything else is K8's business."""
        rel = self.rel_to_findings(path)
        return len(rel.parts) == 2 and FILENAME_RE.match(rel.name) is not None

    @cached_property
    def findings(self) -> list[Finding]:
        parsed = [parse_finding(p, b) for p, b in self.files.items() if self.is_finding_path(p)]
        return sorted(parsed, key=_finding_order)

    @property
    def review_index_path(self) -> str:
        return f"{self.cfg.review_dir}/{INDEX_NAME}"

    @cached_property
    def records(self) -> list:
        """Every `<review root>/<kind folder>/<name>.yaml`, parsed (records.Record), in path order.

        Whether the name is a valid ID for that folder is K12's business, not this property's.
        """
        from kblam.records import KINDS, parse_record

        folders = set(KINDS.values())
        parsed = []
        for path in sorted(self.review_files):
            rel = PurePosixPath(path).relative_to(self.cfg.review_dir)
            if len(rel.parts) == 2 and rel.parts[0] in folders and rel.name.endswith(".yaml"):
                parsed.append(parse_record(path, self.review_files[path]))
        return parsed

    def show(self, path: str) -> str:
        return self.display.get(path, path)


def _finding_order(finding: Finding) -> tuple[int, str]:
    number = int(finding.file_id[2:]) if finding.file_id else 0
    return number, finding.path


def load_view(cfg: Config) -> KBView:
    files: dict[str, bytes] = {}
    base = cfg.findings_path
    if base.is_dir():
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                full = Path(dirpath) / name
                rel = full.relative_to(cfg.repo_root).as_posix()
                files[rel] = full.read_bytes()
    review_files, review_symlinks = _load_review(cfg)
    return KBView(cfg=cfg, files=files, review_files=review_files, review_symlinks=review_symlinks)


def _load_review(cfg: Config) -> tuple[dict[str, bytes], set[str]]:
    """Every file under the review root, at any depth and whatever its name (K12 decides which of them
    are records). Directory links are not followed; every symlink is reported by path, whether or not
    its bytes are readable, and only a link to a readable file also contributes bytes."""
    files: dict[str, bytes] = {}
    symlinks: set[str] = set()
    root = cfg.review_path
    if not root.is_dir():  # absent, or not a directory: there is nothing to load
        return files, symlinks
    for dirpath, dirnames, filenames in os.walk(root):
        for name in (*dirnames, *filenames):
            full = Path(dirpath) / name
            if full.is_symlink():
                symlinks.add(full.relative_to(cfg.repo_root).as_posix())
        dirnames[:] = [n for n in dirnames if not (Path(dirpath) / n).is_symlink()]
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink() and not full.is_file():
                continue  # dangling, or a link to a directory: reported, but it has no bytes
            files[full.relative_to(cfg.repo_root).as_posix()] = full.read_bytes()
    return files, symlinks
