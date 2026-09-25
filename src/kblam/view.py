"""An in-memory snapshot of `findings/`, so rules can run on the tree as it is or as it would be."""

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
    # Results the rules derive from these files, computed once per view (like `findings`): K4, K5 and
    # K10 all need each finding's verbatim excerpts checked, and many excerpts quote one source.
    memo: dict = field(default_factory=dict, repr=False, compare=False)

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
    return KBView(cfg=cfg, files=files)
