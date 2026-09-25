"""Parse one finding file: YAML frontmatter, body, and the claim paragraph (SPEC §4)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import PurePosixPath

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError, YAMLError

FILENAME_RE = re.compile(r"^(F-\d{4,})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
ID_RE = re.compile(r"^F-\d{4,}$")
ID_IN_TEXT_RE = re.compile(r"(?<![\w-])F-\d{4,}(?![\w-])")
CLAIM_MARKER_RE = re.compile(r"^\*\*Claim[.:]\*\*\s*")
FINGERPRINT_KEYS = ("scope", "quantities", "evidence")  # with id and claim: what a finding asserts


def yaml_rt() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    return yaml


def id_number(finding_id: str) -> int:
    return int(finding_id[2:])


def format_id(number: int) -> str:
    return f"F-{number:04d}"


def normalise_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def plain_data(value):
    """ruamel round-trip values as plain dicts, lists, strings and numbers."""
    if isinstance(value, dict):
        return {str(k): plain_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain_data(v) for v in value]
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def fingerprint(finding: Finding) -> str:
    """First 8 hex digits of sha256 over the finding's id, claim, scope, quantities and evidence.

    Whitespace runs in the claim are collapsed, so reflowing it is not a change; the title,
    depends_on and the body after the claim paragraph are not covered (SPEC §5 K3).
    """
    meta = finding.meta if isinstance(finding.meta, dict) else {}
    content = {"id": finding.file_id, "claim": " ".join(finding.claim.split())}
    content.update({key: plain_data(meta.get(key)) for key in FINGERPRINT_KEYS})
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:8]


@dataclass
class Finding:
    path: str                      # repo-relative POSIX path
    raw: bytes
    file_id: str | None = None     # ID taken from the filename
    slug: str | None = None
    text: str | None = None        # decoded, newlines normalised
    meta: object = None            # ruamel CommentedMap when parsing succeeded
    body_start_line: int = 0       # 1-based file line of the first body line
    body_lines: list[str] = field(default_factory=list)
    claim: str = ""                # claim paragraph, **Claim.** marker removed
    claim_line: int = 0
    claim_first_line: str = ""     # first raw line of the claim paragraph
    parse_errors: list[tuple[int | None, str]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def ok(self) -> bool:
        return not self.parse_errors and isinstance(self.meta, dict)

    def key_line(self, key: str) -> int | None:
        """1-based file line of a top-level frontmatter key."""
        try:
            return self.meta.lc.key(key)[0] + 2  # +1 for 1-based, +1 for the opening ---
        except (AttributeError, KeyError, TypeError):
            return None

    def item_line(self, key: str, index) -> int | None:
        try:
            return self.meta[key].lc.item(index)[0] + 2
        except (AttributeError, KeyError, TypeError, IndexError):
            return self.key_line(key)

    def item_source(self, key: str, index) -> str | None:
        """The raw YAML token of a sequence item, e.g. `0x1A2B3C` as written."""
        try:
            line, col = self.meta[key].lc.item(index)
        except (AttributeError, KeyError, TypeError, IndexError):
            return None
        source = self.text.split("\n")[line + 1]
        match = re.match(r"[^,\]\s#]+", source[col:])
        return match.group(0) if match else None


def parse_finding(path: str, raw: bytes) -> Finding:
    finding = Finding(path=path, raw=raw)
    match = FILENAME_RE.match(finding.name)
    if match:
        finding.file_id, finding.slug = match.group(1), match.group(2)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        finding.parse_errors.append((None, f"file is not valid UTF-8 ({exc.reason} at byte {exc.start})"))
        return finding
    text = normalise_newlines(text.removeprefix("﻿"))
    finding.text = text
    lines = text.split("\n")

    if not lines or lines[0].rstrip() != "---":
        finding.parse_errors.append((1, "file must start with a '---' line opening the YAML frontmatter"))
        return finding
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        finding.parse_errors.append((1, "frontmatter is not closed by a '---' line"))
        return finding

    try:
        meta = yaml_rt().load("\n".join(lines[1:end]) + "\n")
    except MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        line = mark.line + 2 if mark is not None else None
        problem = exc.problem or exc.context or "invalid YAML"
        finding.parse_errors.append((line, f"frontmatter is not valid YAML: {problem}"))
        return finding
    except YAMLError as exc:
        finding.parse_errors.append((2, f"frontmatter is not valid YAML: {exc}"))
        return finding
    if not isinstance(meta, dict):
        finding.parse_errors.append((2, "frontmatter must be a YAML mapping of keys to values"))
        return finding
    finding.meta = meta

    finding.body_start_line = end + 2
    finding.body_lines = lines[end + 1:]
    _extract_claim(finding)
    return finding


def _extract_claim(finding: Finding) -> None:
    body = finding.body_lines
    i = 0
    while i < len(body) and not body[i].strip():
        i += 1
    if i == len(body):
        return
    start = i
    while i < len(body) and body[i].strip():
        i += 1
    paragraph = " ".join(line.strip() for line in body[start:i])
    finding.claim_line = finding.body_start_line + start
    finding.claim_first_line = body[start]
    finding.claim = CLAIM_MARKER_RE.sub("", paragraph).strip()
