"""Review records: SC- challenges, CT- tasks, CU- uses (SPEC §5.2.2, §5.2.3 field tables).

Parsing and structural checks only: no filesystem or git access here.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import PurePosixPath

from ruamel.yaml.error import MarkedYAMLError, YAMLError

from kblam import paths
from kblam.finding import ID_RE as FINDING_ID_RE
from kblam.finding import normalise_newlines, plain_data, yaml_rt
from kblam.rules import Issue
from kblam.sources import FileRef

KINDS = {"SC": "challenges", "CT": "tasks", "CU": "uses"}   # ID prefix -> kind folder
ID_RE = re.compile(r"^(SC|CT|CU)-\d{4,}$")
FILENAME_RE = re.compile(r"^((SC|CT|CU)-\d{4,})\.yaml$")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]*")        # fullmatch; case-sensitive (SPEC §5.2.2)
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{12}$")
OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SCHEMA = 1

STATUSES = {
    "SC": ("open", "confirmed", "rejected", "stale"),
    "CT": ("open", "confirmed", "not_reproduced", "inconclusive", "stale"),
    "CU": ("open", "approved", "withdrawn", "stale"),
}
EFFECTIVE = {"SC": ("confirmed",), "CT": ("confirmed", "not_reproduced", "inconclusive"), "CU": ("approved",)}
CLOSED = {"SC": ("rejected",), "CT": (), "CU": ("withdrawn",)}  # close without being effective
RETIRED = "stale"

CLASSIFICATIONS = ("contradicted", "unsupported", "wrong_model")
ROLES = ("counterevidence", "internal-inconsistency", "missing-support", "model-mismatch")
TASK_KINDS = ("replication", "confirmation")
DISPOSITIONS = ("unaffected_raw_bytes", "rewritten_claim")
OUTCOME_KEYS = ("supports", "refutes", "inconclusive")

COMMON_KEYS = ("schema", "id", "created", "creator", "status", "decisions", "proponent")
REF_KEYS = ("path", "sha256", "repo", "commit", "blob", "snapshot")
ASSERTION_KEYS = ("lines", "text", "sha256", "occurrence")
DECISION_KEYS = ("date", "by", "status", "reason", "evidence", "bind")
KEYS = {  # every allowed top-level key per kind, in the order records are written
    "SC": ("schema", "id", "created", "creator", "status", "source", "proposition", "scope",
           "classification", "basis", "usable", "limits", "linked_findings", "decisions"),
    "CT": ("schema", "id", "created", "creator", "proponent", "status", "kind", "finding",
           "claim_fingerprint", "base_file_sha256", "question", "method", "outcomes", "controls", "stop",
           "expected_evidence", "decisions"),
    "CU": ("schema", "id", "created", "creator", "proponent", "status", "challenge", "challenge_bind",
           "finding", "finding_fingerprint", "finding_file_sha256", "citation", "disposition", "reason",
           "decisions"),
}

PIN_KEYS = ("repo", "commit", "blob")                        # the Git pin: all three or none
CITATION_KEYS = ("ordinal", "path", "range", "tag_sha256")
SC_ID_RE = re.compile(r"^SC-\d{4,}$")


@dataclass
class Record:
    path: str                  # repo-relative POSIX path as found (or the staged file's display path)
    id: str | None             # the ID the filename gives, or None
    kind: str | None           # "SC" | "CT" | "CU" from the filename, or None
    data: dict | None          # finding.plain_data of the YAML mapping; None if it did not parse as one
    raw: bytes
    error: str | None = None   # why data is None (not UTF-8, invalid YAML, not a mapping)
    meta: object = field(default=None, repr=False)  # the ruamel round-trip mapping, for line numbers

    def key_line(self, key: str) -> int:
        """1-based line of a top-level key, or 0."""
        try:
            return self.meta.lc.key(key)[0] + 1
        except (AttributeError, KeyError, TypeError):
            return 0

    @property
    def status(self) -> str | None:
        value = self.data.get("status") if isinstance(self.data, dict) else None
        return value if isinstance(value, str) else None


def parse_record(path: str, raw: bytes) -> Record:
    """Parse a record file with the round-trip loader (finding.yaml_rt); id and kind from the filename."""
    rec = Record(path=path, id=None, kind=None, data=None, raw=raw)
    match = FILENAME_RE.match(PurePosixPath(path).name)
    if match:
        rec.id, rec.kind = match.group(1), match.group(2)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        rec.error = "not UTF-8"
        return rec

    try:
        meta = yaml_rt().load(normalise_newlines(text.removeprefix("\N{BYTE ORDER MARK}")))
    except MarkedYAMLError as exc:
        rec.error = f"not valid YAML: {exc.problem or exc.context or 'invalid YAML'}"
        return rec
    except YAMLError as exc:
        rec.error = f"not valid YAML: {exc}"
        return rec
    if not isinstance(meta, dict):
        rec.error = "not a YAML mapping"
        return rec

    rec.meta = meta
    rec.data = plain_data(meta)
    return rec


def schema_code(kind: str, key: str) -> str:
    """K13 for §5.2.2 fields (including proponent), K15 for a CT's §5.2.3 fields."""
    return "K13" if kind != "CT" or key in COMMON_KEYS else "K15"


def schema_issues(rec: Record, *, staged: bool) -> list[Issue]:
    """Structural checks of the §5.2.2 and §5.2.3 field tables, in field order.

    Unknown and missing keys; types (booleans are not integers); non-empty strings; names (NAME_RE);
    line and offset ranges; hex and object-ID syntax; repo/commit/blob all set or all null; path syntax
    (paths.syntax_problem, no filesystem); the kind's vocabularies; `schema` other than 1 gives
    "unsupported schema version N"; `id` equal to the filename's ID; `outcomes` with exactly its three
    keys; decision entries (DECISION_KEYS; evidence entries are file references plus `locator` and
    `provenance`; the provenance vocabulary is checked by K13, which has the config). Blank values ("",
    null, [] where content is required) are errors unless `staged`, where they are allowed.
    Every issue: code "K13" (K15 for a CT's §5.2.3 fields, excluding proponent), level "error",
    owner rec.id or "",
    path rec.path, line rec.key_line(<top-level key>) or 0.
    """
    if rec.data is None:
        return [Issue(rec.path, 0, "K13", rec.error or "record did not parse", "error", rec.id or "")]
    kind = rec.kind
    if kind not in KEYS:
        return []   # not a record at all: how the review root names its files is K13's own check (§5.2.4)

    data = rec.data
    issues: list[Issue] = []

    def add(key: str, message: str, code: str | None = None) -> None:
        code = code or schema_code(kind, key)
        issues.append(Issue(rec.path, rec.key_line(key), code, message, "error", rec.id or ""))

    if "schema" in data:
        value = data["schema"]
        if not (_is_int(value) and value == SCHEMA):
            add("schema", f"unsupported schema version {value}")
            return issues        # an unknown schema: the field tables below do not apply
    else:
        add("schema", "missing key 'schema'")

    for key in data:
        if key not in KEYS[kind]:
            add(key, f"unknown key {key!r}", "K13")     # no field of the kind's table allows it
    for key in KEYS[kind]:
        if key == "schema":
            continue
        if key not in data:
            add(key, f"missing key {key!r}")
            continue
        checker, argument = FIELD_CHECKS[key]
        checker(rec, key, key, data[key], staged, add, argument)
    return issues


def dump(mapping) -> bytes:
    """The bytes kblam writes for a record: `mapping` (plain data, or a round-trip mapping from
    parse_record's meta, whose comments are kept) as block-style YAML, width 4096, UTF-8, LF, one final
    newline. Staging (review_stage) and record writes (review_write) both use it, so a record's bytes do
    not depend on which command wrote it."""
    yaml = yaml_rt()
    yaml.default_flow_style = False
    yaml.width = 4096
    buffer = io.StringIO()
    yaml.dump(mapping, buffer)
    return buffer.getvalue().encode("utf-8")


def file_ref(mapping: dict) -> FileRef:
    """A FileRef from a parsed reference mapping; missing keys become None."""
    return FileRef(*(mapping.get(key) for key in REF_KEYS))


# --- values (SPEC §5.2.2 Values) ---------------------------------------------------------------


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _missing(value) -> bool:
    """No value at all: null, or a string with no non-whitespace character."""
    return value is None or (isinstance(value, str) and not value.strip())


def _blank(value) -> bool:
    """No content: `_missing`, or an empty list or mapping (§5.2.2 Blank values)."""
    return _missing(value) or (isinstance(value, (list, dict)) and not value)


def _ok_date(value) -> bool:
    if isinstance(value, datetime):
        return False                # a datetime is not a date, as in K1
    if isinstance(value, date):
        return True
    if isinstance(value, str):
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return len(value) == 10
    return False


def _check_id(rec, top_key, prefix, value, staged, add, argument):
    if not (isinstance(value, str) and ID_RE.match(value)):
        if staged and _blank(value):
            return
        add(top_key, "id: required" if _missing(value) else f"id: {value!r} is not an ID")
        return
    if rec.id is not None and value != rec.id:
        add(top_key, f"id: {value!r} does not match the file name's ID ({rec.id})")


def _check_date(rec, top_key, prefix, value, staged, add, argument):
    if staged and _blank(value):
        return
    if not _ok_date(value):
        add(top_key, f"{prefix}: required" if _missing(value) else f"{prefix}: expected a date (YYYY-MM-DD)")


def _check_name(rec, top_key, prefix, value, staged, add, argument):
    if isinstance(value, str) and value.strip() and NAME_RE.fullmatch(value):
        return
    if staged and _blank(value):
        return
    add(top_key, f"{prefix}: required" if _missing(value) else f"{prefix}: {value!r} is not a name")


def _check_text(rec, top_key, prefix, value, staged, add, argument):
    if isinstance(value, str) and value.strip():
        return
    if staged and _blank(value):
        return
    add(top_key, f"{prefix}: required" if _missing(value) else f"{prefix}: expected a non-empty string")


def _check_text_list(rec, top_key, prefix, value, staged, add, argument):
    if staged and _blank(value):
        return
    if not isinstance(value, list):
        add(top_key, f"{prefix}: required" if _missing(value)
            else f"{prefix}: expected a list of non-empty strings")
        return
    if not value and not staged:
        add(top_key, f"{prefix}: required")
        return
    for i, item in enumerate(value):
        if isinstance(item, str) and item.strip():
            continue
        if staged and _blank(item):
            continue
        add(top_key, f"{prefix}[{i}]: required" if _missing(item)
            else f"{prefix}[{i}]: expected a non-empty string")


def _check_enum(rec, top_key, prefix, value, staged, add, vocabulary):
    if isinstance(value, str) and value in vocabulary:
        return
    if staged and _blank(value):
        return
    add(top_key, f"{prefix}: required" if _missing(value)
        else f"{prefix}: {value!r} is not one of {', '.join(vocabulary)}")


def _check_status(rec, top_key, prefix, value, staged, add, argument):
    _check_enum(rec, top_key, prefix, value, staged, add, STATUSES[rec.kind])


def _check_hex64(rec, top_key, prefix, value, staged, add, argument):
    if isinstance(value, str) and HEX64_RE.fullmatch(value):
        return
    if staged and _blank(value):
        return
    add(top_key, f"{prefix}: required" if _missing(value)
        else f"{prefix}: expected 64 lowercase hex digits")


def _check_fingerprint(rec, top_key, prefix, value, staged, add, argument):
    if isinstance(value, str) and FINGERPRINT_RE.fullmatch(value):
        return
    if staged and _blank(value):
        return
    add(top_key, f"{prefix}: required" if _missing(value)
        else f"{prefix}: expected 12 lowercase hex digits")


def _check_oid(rec, top_key, prefix, value, staged, add, nullable):
    if nullable and value is None:
        return
    if isinstance(value, str) and OID_RE.fullmatch(value):
        return
    if staged and _blank(value):
        return
    add(top_key, f"{prefix}: required" if _missing(value)
        else f"{prefix}: expected a hex object ID of 40 or 64 digits")


def _check_path(rec, top_key, prefix, value, staged, add, nullable):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        if staged and _blank(value):
            return
        add(top_key, f"{prefix}: required" if _missing(value) else f"{prefix}: expected a path")
        return
    problem = paths.syntax_problem(value)
    if problem:
        add(top_key, f"{prefix}: {problem}")


def _check_int_min(rec, top_key, prefix, value, staged, add, minimum):
    if _is_int(value) and value >= minimum:
        return
    if staged and _blank(value):
        return
    add(top_key, f"{prefix}: required" if _missing(value)
        else f"{prefix}: expected an integer >= {minimum}")


def _check_range(rec, top_key, prefix, value, staged, add, allow_offset):
    expected = "expected [A, B] with 1 <= A <= B"
    if allow_offset:
        expected += ", or [N] with N >= 0"
    if isinstance(value, list) and all(_is_int(item) for item in value):
        if len(value) == 2 and 1 <= value[0] <= value[1]:
            return
        if allow_offset and len(value) == 1 and value[0] >= 0:
            return
    if staged and _missing(value):
        return
    add(top_key, f"{prefix}: required" if _missing(value) else f"{prefix}: {expected}")


def _check_id_ref(rec, top_key, prefix, value, staged, add, argument):
    regex, label = argument
    if isinstance(value, str) and regex.match(value):
        return
    if staged and _blank(value):
        return
    add(top_key, f"{prefix}: required" if _missing(value) else f"{prefix}: {value!r} is not a {label}")


def _check_finding_ids(rec, top_key, prefix, value, staged, add, argument):
    if staged and _blank(value):
        return
    if not isinstance(value, list):
        add(top_key, f"{prefix}: required" if _missing(value)
            else f"{prefix}: expected a list of finding IDs")
        return
    for i, item in enumerate(value):        # [] is allowed at every stage
        if isinstance(item, str) and FINDING_ID_RE.match(item):
            continue
        if staged and _blank(item):
            continue
        add(top_key, f"{prefix}[{i}]: {item!r} is not a finding ID (F-NNNN)")


# --- file references, decisions and the kind-specific fields ------------------------------------


def _ref_mapping(rec, top_key, prefix, mapping, staged, add, extra_keys=()):
    """One file reference (SPEC §5.2.2): REF_KEYS and the caller's extra keys, plus the pin rule."""
    for name in mapping:
        if name not in REF_KEYS and name not in extra_keys:
            add(top_key, f"{prefix}: unknown key {name!r}")
    for name in REF_KEYS + tuple(extra_keys):     # every extra key of a reference is required too
        if name not in mapping:
            add(top_key, f"{prefix}: missing key {name!r}")
    if "path" in mapping:
        _check_path(rec, top_key, f"{prefix}.path", mapping["path"], staged, add, nullable=False)
    if "sha256" in mapping:
        _check_hex64(rec, top_key, f"{prefix}.sha256", mapping["sha256"], staged, add, None)
    if "repo" in mapping:
        _check_path(rec, top_key, f"{prefix}.repo", mapping["repo"], staged, add, nullable=True)
    if "commit" in mapping:
        _check_oid(rec, top_key, f"{prefix}.commit", mapping["commit"], staged, add, nullable=True)
    if "blob" in mapping:
        _check_oid(rec, top_key, f"{prefix}.blob", mapping["blob"], staged, add, nullable=True)
    if "snapshot" in mapping:
        _check_path(rec, top_key, f"{prefix}.snapshot", mapping["snapshot"], staged, add, nullable=True)
    if all(name in mapping for name in PIN_KEYS):
        set_keys = [name for name in PIN_KEYS if not _missing(mapping[name])]
        if 0 < len(set_keys) < len(PIN_KEYS):
            add(top_key, f"{prefix}: repo, commit and blob must be all set or all null")


def _check_source(rec, top_key, prefix, value, staged, add, argument):
    if staged and _blank(value):
        return
    if not isinstance(value, dict):
        add(top_key, f"{prefix}: required" if _missing(value)
            else f"{prefix}: expected a file reference with an assertion")
        return
    _ref_mapping(rec, top_key, prefix, value, staged, add, extra_keys=("assertion",))
    if "assertion" in value:
        _assertion(rec, top_key, f"{prefix}.assertion", value["assertion"], staged, add)


def _assertion(rec, top_key, prefix, value, staged, add):
    if staged and _blank(value):
        return
    if not isinstance(value, dict):
        add(top_key, f"{prefix}: required" if _missing(value)
            else f"{prefix}: expected a mapping of lines, text, sha256 and occurrence")
        return
    for name in value:
        if name not in ASSERTION_KEYS:
            add(top_key, f"{prefix}: unknown key {name!r}")
    for name in ASSERTION_KEYS:
        if name not in value:
            add(top_key, f"{prefix}: missing key {name!r}")
    if "lines" in value:
        _check_range(rec, top_key, f"{prefix}.lines", value["lines"], staged, add, allow_offset=False)
    if "text" in value:
        _check_text(rec, top_key, f"{prefix}.text", value["text"], staged, add, None)
    if "sha256" in value:
        _check_hex64(rec, top_key, f"{prefix}.sha256", value["sha256"], staged, add, None)
    if "occurrence" in value:
        _check_int_min(rec, top_key, f"{prefix}.occurrence", value["occurrence"], staged, add, 1)


def _check_basis(rec, top_key, prefix, value, staged, add, argument):
    if staged and _blank(value):
        return
    if not isinstance(value, list):
        add(top_key, f"{prefix}: required" if _missing(value)
            else f"{prefix}: expected a list of basis entries")
        return
    if not value and not staged:
        add(top_key, f"{prefix}: required")
        return
    for i, entry in enumerate(value):
        if staged and _blank(entry):
            continue
        if not isinstance(entry, dict):
            add(top_key, f"{prefix}[{i}]: required" if _missing(entry)
                else f"{prefix}[{i}]: expected a basis entry")
            continue
        entry_prefix = f"{prefix}[{i}]"
        _ref_mapping(rec, top_key, entry_prefix, entry, staged, add,
                     extra_keys=("locator", "role", "provenance"))
        if "locator" in entry:
            _check_text(rec, top_key, f"{entry_prefix}.locator", entry["locator"], staged, add, None)
        if "role" in entry:
            _check_enum(rec, top_key, f"{entry_prefix}.role", entry["role"], staged, add, ROLES)
        if "provenance" in entry:
            _check_name(rec, top_key, f"{entry_prefix}.provenance", entry["provenance"], staged, add, None)


def _check_outcomes(rec, top_key, prefix, value, staged, add, argument):
    if staged and _blank(value):
        return
    if not isinstance(value, dict):
        add(top_key, f"{prefix}: required" if _missing(value)
            else f"{prefix}: expected a mapping of {', '.join(OUTCOME_KEYS)}")
        return
    for name in OUTCOME_KEYS:
        if name not in value:
            add(top_key, f"{prefix}: missing key {name!r}")
    for name in value:
        if name not in OUTCOME_KEYS:
            add(top_key, f"{prefix}: unknown key {name!r}")
    for name in OUTCOME_KEYS:
        if name in value:
            _check_text(rec, top_key, f"{prefix}.{name}", value[name], staged, add, None)


def _check_citation(rec, top_key, prefix, value, staged, add, argument):
    if staged and _blank(value):
        return
    if not isinstance(value, dict):
        add(top_key, f"{prefix}: required" if _missing(value)
            else f"{prefix}: expected a mapping of {', '.join(CITATION_KEYS)}")
        return
    for name in value:
        if name not in CITATION_KEYS:
            add(top_key, f"{prefix}: unknown key {name!r}")
    for name in CITATION_KEYS:
        if name not in value:
            add(top_key, f"{prefix}: missing key {name!r}")
    if "ordinal" in value:
        _check_int_min(rec, top_key, f"{prefix}.ordinal", value["ordinal"], staged, add, 1)
    if "path" in value:
        _check_path(rec, top_key, f"{prefix}.path", value["path"], staged, add, nullable=False)
    if "range" in value:
        _check_range(rec, top_key, f"{prefix}.range", value["range"], staged, add, allow_offset=True)
    if "tag_sha256" in value:
        _check_hex64(rec, top_key, f"{prefix}.tag_sha256", value["tag_sha256"], staged, add, None)


def _check_decisions(rec, top_key, prefix, value, staged, add, argument):
    if staged and _blank(value):
        return
    if not isinstance(value, list):
        add(top_key, f"{prefix}: required" if _missing(value)
            else f"{prefix}: expected a list of decision entries")
        return
    for i, entry in enumerate(value):       # [] is allowed at every stage
        if staged and _blank(entry):
            continue
        if not isinstance(entry, dict):
            add(top_key, f"{prefix}[{i}]: required" if _missing(entry)
                else f"{prefix}[{i}]: expected a mapping of decision fields")
            continue
        entry_prefix = f"{prefix}[{i}]"
        for name in entry:
            if name not in DECISION_KEYS:
                add(top_key, f"{entry_prefix}: unknown key {name!r}")
        for name in DECISION_KEYS:
            if name not in entry:
                add(top_key, f"{entry_prefix}: missing key {name!r}")
        if "date" in entry:
            _check_date(rec, top_key, f"{entry_prefix}.date", entry["date"], staged, add, None)
        if "by" in entry:
            _check_name(rec, top_key, f"{entry_prefix}.by", entry["by"], staged, add, None)
        if "status" in entry:
            _check_enum(rec, top_key, f"{entry_prefix}.status", entry["status"], staged, add,
                        STATUSES[rec.kind])
        if "reason" in entry:
            _check_text(rec, top_key, f"{entry_prefix}.reason", entry["reason"], staged, add, None)
        if "evidence" in entry:
            _evidence(rec, top_key, f"{entry_prefix}.evidence", entry["evidence"], staged, add)
        if "bind" in entry:
            _check_hex64(rec, top_key, f"{entry_prefix}.bind", entry["bind"], staged, add, None)


def _evidence(rec, top_key, prefix, value, staged, add):
    if staged and _blank(value):
        return
    if not isinstance(value, list):
        add(top_key, f"{prefix}: required" if _missing(value)
            else f"{prefix}: expected a list of evidence entries")
        return
    for j, entry in enumerate(value):       # [] is allowed at every stage
        if staged and _blank(entry):
            continue
        if not isinstance(entry, dict):
            add(top_key, f"{prefix}[{j}]: required" if _missing(entry)
                else f"{prefix}[{j}]: expected an evidence entry")
            continue
        entry_prefix = f"{prefix}[{j}]"
        _ref_mapping(rec, top_key, entry_prefix, entry, staged, add,
                     extra_keys=("locator", "provenance"))
        if "locator" in entry:
            _check_text(rec, top_key, f"{entry_prefix}.locator", entry["locator"], staged, add, None)
        if "provenance" in entry:
            _check_name(rec, top_key, f"{entry_prefix}.provenance", entry["provenance"], staged, add, None)


FIELD_CHECKS = {  # top-level key -> (checker, the checker's extra argument)
    "id": (_check_id, None),
    "created": (_check_date, None),
    "creator": (_check_name, None),
    "status": (_check_status, None),
    "decisions": (_check_decisions, None),
    "proponent": (_check_name, None),
    "source": (_check_source, None),
    "proposition": (_check_text, None),
    "scope": (_check_text_list, None),
    "classification": (_check_enum, CLASSIFICATIONS),
    "basis": (_check_basis, None),
    "usable": (_check_text, None),
    "limits": (_check_text, None),
    "linked_findings": (_check_finding_ids, None),
    "kind": (_check_enum, TASK_KINDS),
    "finding": (_check_id_ref, (FINDING_ID_RE, "finding ID (F-NNNN)")),
    "claim_fingerprint": (_check_fingerprint, None),
    "base_file_sha256": (_check_hex64, None),
    "question": (_check_text, None),
    "method": (_check_text, None),
    "outcomes": (_check_outcomes, None),
    "controls": (_check_text_list, None),
    "stop": (_check_text, None),
    "expected_evidence": (_check_text_list, None),
    "challenge": (_check_id_ref, (SC_ID_RE, "challenge ID (SC-NNNN)")),
    "challenge_bind": (_check_hex64, None),
    "finding_fingerprint": (_check_fingerprint, None),
    "finding_file_sha256": (_check_hex64, None),
    "citation": (_check_citation, None),
    "disposition": (_check_enum, DISPOSITIONS),
    "reason": (_check_text, None),
}
