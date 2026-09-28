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
V1_KEYS = ("scope", "quantities", "evidence")  # fingerprint v1: with id and claim, lists in file order
V1_FINGERPRINT_RE = re.compile(r"[0-9a-f]{8}")   # a v1 fingerprint (SPEC §5.1): 8 hex digits
FINGERPRINT_DIGITS = 12                          # a v2 fingerprint's length


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


def split_scope(values, separator: str) -> list[str]:
    """The scope values as parts: each value split at `separator` ([kb] scope_separator; "" never splits),
    stripped, empty parts dropped, de-duplicated and sorted (SPEC §4, §5.1, §6.1)."""
    parts = set()
    for value in values or ():
        pieces = str(value).split(separator) if separator else [str(value)]
        parts.update(p.strip() for p in pieces if p.strip())
    return sorted(parts)


def _evidence_path(path: str) -> str:
    """An evidence path as the fingerprint sees it: `\\` as `/`, a leading `./` and a trailing `/` removed."""
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/") if len(path) > 1 else path


def _sort_key(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _quantity_key(item) -> tuple:
    """Quantities sort by normalised name (as §6.3 compares names), value and unit; a malformed one after
    them, by its JSON, so the order is total whatever the file holds (K1 reports it)."""
    if isinstance(item, dict):
        name, value, unit = item.get("name"), item.get("value"), item.get("unit")
        if (isinstance(name, str) and isinstance(value, (int, float)) and not isinstance(value, bool)
                and (unit is None or isinstance(unit, str))):
            return (0, " ".join(name.split()).casefold(), float(value), " ".join((unit or "").split()),
                    _sort_key(item))
    return (1, "", 0.0, "", _sort_key(item))


def _digest(content: dict) -> str:
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def fingerprint(finding: Finding, separator: str) -> str:
    """Fingerprint v2 (SPEC §5.1): the first 12 hex digits of the sha256 of the canonical JSON of the
    finding's id, claim, label, scope, quantities and evidence.

    Whitespace runs in the claim are collapsed, so reflowing it is not a change. Every list is in a
    canonical order, so reordering one is not an edit either: scope values split at `separator` ([kb]
    scope_separator), de-duplicated and sorted; evidence paths normalised (`_evidence_path`) and sorted;
    quantities sorted by normalised name, value and unit, each as written. The label counts, so demoting
    or promoting a finding makes its dependents suspect. The title, depends_on, anchors and the body after
    the claim paragraph are not covered. A value K1 reports as malformed still hashes as written.
    """
    meta = finding.meta if isinstance(finding.meta, dict) else {}
    scope, evidence, quantities = (plain_data(meta.get(key)) for key in ("scope", "evidence", "quantities"))
    if isinstance(scope, list):
        scope = split_scope(scope, separator)
    if isinstance(evidence, list):
        evidence = sorted((_evidence_path(e) if isinstance(e, str) else e for e in evidence), key=_sort_key)
    if isinstance(quantities, list):
        quantities = sorted(quantities, key=_quantity_key)
    content = {"id": finding.file_id, "claim": " ".join(finding.claim.split()),
               "label": plain_data(meta.get("label")), "scope": scope, "evidence": evidence,
               "quantities": quantities}
    return _digest(content)[:FINGERPRINT_DIGITS]


def fingerprint_v1(finding: Finding) -> str:
    """Fingerprint v1, the format before M6.10: the first 8 hex digits over the id, claim, scope, quantities
    and evidence, each list in file order (SPEC §5.1). Only kblam upgrade and the old-format checks use it,
    to recognise stamps and state recorded before v2."""
    meta = finding.meta if isinstance(finding.meta, dict) else {}
    content = {"id": finding.file_id, "claim": " ".join(finding.claim.split())}
    content.update({key: plain_data(meta.get(key)) for key in V1_KEYS})
    return _digest(content)[:8]


def is_v1_fingerprint(value) -> bool:
    """Whether `value` is in the v1 format: exactly 8 hex digits, where v2 has 12 (SPEC §5.1)."""
    return isinstance(value, str) and V1_FINGERPRINT_RE.fullmatch(value) is not None


def fingerprint_as(recorded, finding: Finding, separator: str) -> str:
    """`finding`'s fingerprint in the format of `recorded`: v1 for a value recorded before fingerprint v2,
    so that such a stamp or item still recognises the version it meant until kblam upgrade replaces it."""
    return fingerprint_v1(finding) if is_v1_fingerprint(recorded) else fingerprint(finding, separator)


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
