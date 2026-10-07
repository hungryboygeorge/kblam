"""Record schemas: parse_record and schema_issues (SPEC §5.2.2, §5.2.3)."""

from __future__ import annotations

from datetime import date

import pytest

from kblam.records import KINDS, KEYS, STATUSES, parse_record, schema_issues

from conftest import DROP, SOURCE_REPO, TRACE_PATH, ZERO64, dump_record, record_data, record_text

SOURCE = f"{SOURCE_REPO}/{TRACE_PATH}"
OID = "a" * 40
OID64 = "b" * 64


def record_path(kind: str, rec_id: str) -> str:
    return f"research-review/{KINDS[kind]}/{rec_id}.yaml"


def build(record_kind: str, rec_id: str | None = None, **fields) -> dict:
    """record_data with `fields` applied; going through a mapping keeps a field named `kind` possible."""
    data = record_data(record_kind, rec_id or f"{record_kind}-0001")
    for key, value in fields.items():
        if value is DROP:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def parse(record_kind: str, rec_id: str | None = None, *, name: str | None = None, body=None, **fields):
    """The parsed record for `record_kind`; `fields` replace top-level keys, as in record_data."""
    rec_id = rec_id or f"{record_kind}-0001"
    raw = body if body is not None else dump_record(build(record_kind, rec_id, **fields))
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return parse_record(f"research-review/{KINDS[record_kind]}/{name or rec_id + '.yaml'}", raw)


def rows(rec, staged: bool = False) -> list[tuple]:
    """(code, message, owner, path, line) for each issue."""
    return [(i.code, i.message, i.owner, i.path, i.line) for i in schema_issues(rec, staged=staged)]


def line_of(rec, key: str) -> int:
    """The 1-based line a top-level key is written on."""
    return next(i + 1 for i, line in enumerate(rec.raw.decode("utf-8").splitlines())
                if line.startswith(f"{key}:"))


def expect(kind: str, code: str, message: str, rec=None, key: str | None = None, rec_id=None) -> tuple:
    """The expected issue; `key` is the top-level key whose line it reports (None: line 0)."""
    rec_id = rec_id or (rec.id if rec is not None else None) or f"{kind}-0001"
    return (code, message, rec_id, record_path(kind, rec_id), line_of(rec, key) if key else 0)


def code_of(kind: str, key: str) -> str:
    """An independent expectation of the §5.2.2/§5.2.3 split, including proponent as common."""
    common = ("schema", "id", "created", "creator", "status", "decisions", "proponent")
    return "K13" if kind != "CT" or key in common else "K15"


def source_ref(**fields) -> dict:
    ref = {"path": SOURCE, "sha256": ZERO64, "repo": None, "commit": None, "blob": None, "snapshot": None,
           "assertion": {"lines": [3, 3], "text": "the two bytes are equal", "sha256": ZERO64,
                         "occurrence": 1}}
    ref.update(fields)
    return ref


def assertion(**fields) -> dict:
    entry = {"lines": [3, 3], "text": "the two bytes are equal", "sha256": ZERO64, "occurrence": 1}
    entry.update(fields)
    return entry


def basis_entry(**fields) -> dict:
    entry = {"path": SOURCE, "sha256": ZERO64, "repo": None, "commit": None, "blob": None, "snapshot": None,
             "locator": "row 102: printed byte values", "role": "internal-inconsistency",
             "provenance": "observed"}
    entry.update(fields)
    return entry


def citation(**fields) -> dict:
    entry = {"ordinal": 1, "path": SOURCE, "range": [2, 3], "tag_sha256": ZERO64}
    entry.update(fields)
    return entry


def evidence_entry(**fields) -> dict:
    entry = {"path": SOURCE, "sha256": ZERO64, "repo": None, "commit": None, "blob": None, "snapshot": None,
             "locator": "row 102", "provenance": "observed"}
    entry.update(fields)
    return entry


def decision(**fields) -> dict:
    entry = {"date": "2026-09-28", "by": "reviewer-b", "status": "confirmed", "reason": "row 102 differs",
             "evidence": [evidence_entry()], "bind": ZERO64}
    entry.update(fields)
    return entry


@pytest.mark.parametrize("value,message", [
    (DROP, "missing key 'proponent'"),
    (7, "proponent: 7 is not a name"),
    ("not a name", "proponent: 'not a name' is not a name"),
])
def test_ct_proponent_is_a_common_format_k13_error(value, message):
    rec = parse("CT", proponent=value)
    assert rows(rec) == [expect("CT", "K13", message, rec,
                               key="proponent" if value is not DROP else None)]


# --- parse_record -------------------------------------------------------------------------------


def test_parse_reads_the_file_name_and_the_mapping():
    text = record_text("SC", "SC-0007")
    rec = parse_record(record_path("SC", "SC-0007"), text.encode("utf-8"))
    assert (rec.id, rec.kind, rec.error) == ("SC-0007", "SC", None)
    assert rec.data["id"] == "SC-0007" and rec.data["schema"] == 1
    assert rec.meta is not None and rec.raw == text.encode("utf-8")
    assert rec.status == "open"
    assert schema_issues(rec, staged=False) == []


@pytest.mark.parametrize("name", ["SC-0001.yml", "SC-1.yaml", "index.yaml", "SC-0001.yaml.txt",
                                  "sc-0001.yaml"])
def test_a_file_name_that_is_not_an_id_gives_no_id_or_kind(name):
    rec = parse_record(f"research-review/challenges/{name}", record_text("SC").encode("utf-8"))
    assert (rec.id, rec.kind) == (None, None)
    # How the review root names its files is K13's own check (§5.2.4), not schema_issues'.
    assert schema_issues(rec, staged=False) == []


def test_not_utf8():
    rec = parse("SC", body=b"\xff\xfe\x00 not utf-8 \x80")
    assert rec.data is None and rec.meta is None and rec.error == "not UTF-8"
    assert rows(rec) == [expect("SC", "K13", "not UTF-8")]


@pytest.mark.parametrize("text", ["schema: 1\nid: [unclosed\n", "schema: 1\n\tid: SC-0001\n"])
def test_invalid_yaml(text):
    rec = parse("SC", body=text)
    assert rec.data is None and rec.error.startswith("not valid YAML: ")
    assert rows(rec) == [expect("SC", "K13", rec.error)]


@pytest.mark.parametrize("text", ["", "- schema\n- 1\n", "just a scalar\n", "null\n"])
def test_not_a_yaml_mapping(text):
    rec = parse("SC", body=text)
    assert rec.data is None and rec.error == "not a YAML mapping"
    assert rows(rec) == [expect("SC", "K13", "not a YAML mapping")]


def test_crlf_and_a_bom_parse_like_lf():
    text = record_text("SC")
    plain = parse_record(record_path("SC", "SC-0001"), text.encode("utf-8"))
    for raw in (text.replace("\n", "\r\n").encode("utf-8"), b"\xef\xbb\xbf" + text.encode("utf-8")):
        rec = parse("SC", body=raw)
        assert rec.error is None and rec.data == plain.data


def test_key_line_and_status():
    rec = parse("SC")
    assert rec.key_line("source") == line_of(rec, "source")
    assert rec.key_line("nope") == 0
    assert parse("SC", status="confirmed").status == "confirmed"
    assert parse("SC", status=5).status is None
    assert parse("SC", body=b"not utf-8 \xff").status is None


# --- the whole record ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["SC", "CT", "CU"])
@pytest.mark.parametrize("staged", [False, True])
def test_a_valid_record_has_no_issues(kind, staged):
    assert rows(parse(kind), staged=staged) == []


@pytest.mark.parametrize("kind", ["SC", "CT", "CU"])
def test_unknown_top_level_key_is_K13_even_for_a_task(kind):
    rec = parse(kind, colour="blue")
    assert rows(rec) == [expect(kind, "K13", "unknown key 'colour'", rec, "colour")]


DROPPED = [(kind, key) for kind in ("SC", "CT", "CU") for key in KEYS[kind]]


@pytest.mark.parametrize("kind,key", DROPPED)
def test_missing_top_level_key(kind, key):
    rec = parse(kind, **{key: DROP})
    code = code_of(kind, key)
    assert rows(rec) == [expect(kind, code, f"missing key {key!r}")]


def test_issues_are_reported_in_field_order_with_unknown_keys_first():
    rec = parse("SC", colour="blue", usable=DROP, limits=None)
    assert [i.message for i in schema_issues(rec, staged=False)] == [
        "unknown key 'colour'", "missing key 'usable'", "limits: required"]


# --- schema, id, created, names, status ---------------------------------------------------------


def test_unsupported_schema_version_stops_the_rest():
    rec = parse("SC", schema=2, proposition=DROP, colour="blue", limits=None)
    assert rows(rec) == [expect("SC", "K13", "unsupported schema version 2", rec, "schema")]


@pytest.mark.parametrize("value,message", [(True, "unsupported schema version True"),
                                           ("1", "unsupported schema version 1"),
                                           (1.0, "unsupported schema version 1.0")])
def test_schema_must_be_the_integer_1(value, message):
    rec = parse("SC", schema=value)
    assert rows(rec) == [expect("SC", "K13", message, rec, "schema")]


def test_missing_schema_key_is_reported_and_the_rest_still_checked():
    rec = parse("SC", schema=DROP, limits=None)
    assert rows(rec) == [expect("SC", "K13", "missing key 'schema'"),
                         expect("SC", "K13", "limits: required", rec, "limits")]


def test_id_must_match_the_file_name():
    """The step names the command that works in the state: git restores a file it holds, and a file git
    does not hold at that path (a hand rename or copy) has no step an agent may run."""
    rec = parse("SC", "SC-0001", id="SC-0002")
    assert rows(rec) == [expect("SC", "K13",
                                "id: 'SC-0002' does not match the file name's ID (SC-0001); git does not "
                                "hold a file at research-review/challenges/SC-0001.yaml, and records are "
                                "never renamed, so leave it as it is and tell the user", rec, "id")]
    tracked = [i.message for i in schema_issues(rec, staged=False, tracked=lambda path: True)]
    assert tracked == ["id: 'SC-0002' does not match the file name's ID (SC-0001); restore the record's "
                       "file from git (git restore research-review/challenges/SC-0001.yaml)"]


@pytest.mark.parametrize("value,message", [("X-0001", "id: 'X-0001' is not an ID"),
                                           ("SC-1", "id: 'SC-1' is not an ID"),
                                           (5, "id: 5 is not an ID"),
                                           (None, "id: required")])
def test_id_syntax(value, message):
    rec = parse("SC", id=value)
    assert rows(rec) == [expect("SC", "K13", message, rec, "id")]


def test_created_accepts_a_date_object_and_an_iso_string():
    assert rows(parse("SC", created=date(2026, 9, 28))) == []
    assert rows(parse("SC", created="2026-09-28")) == []


@pytest.mark.parametrize("value,message", [("28-09-2026", "created: expected a date (YYYY-MM-DD)"),
                                           ("20260928", "created: expected a date (YYYY-MM-DD)"),
                                           ("2026-09-28T00:00:00", "created: expected a date (YYYY-MM-DD)"),
                                           (20260928, "created: expected a date (YYYY-MM-DD)"),
                                           (None, "created: required")])
def test_created_must_be_a_yyyy_mm_dd_date(value, message):
    rec = parse("SC", created=value)
    assert rows(rec) == [expect("SC", "K13", message, rec, "created")]


@pytest.mark.parametrize("value", ["reviewer a", "@reviewer", "-reviewer", "", 5, None])
def test_creator_must_be_a_name(value):
    rec = parse("SC", creator=value)
    message = "creator: required" if value in ("", None) else f"creator: {value!r} is not a name"
    assert rows(rec) == [expect("SC", "K13", message, rec, "creator")]
    assert rows(parse("SC", creator="reviewer.a@host-1_x")) == []


def test_proponent_is_a_name_in_both_kinds():
    for kind in ("CT", "CU"):
        rec = parse(kind, proponent="not a name")
        assert rows(rec) == [expect(kind, code_of(kind, "proponent"),
                                    "proponent: 'not a name' is not a name", rec, "proponent")]


@pytest.mark.parametrize("kind,value", [("SC", "approved"), ("CT", "approved"), ("CU", "rejected")])
def test_status_is_the_kinds_vocabulary(kind, value):
    rec = parse(kind, status=value)
    assert rows(rec) == [expect(kind, "K13", f"status: {value!r} is not one of " + ", ".join(STATUSES[kind]),
                                rec, "status")]


# --- blank values and staging -------------------------------------------------------------------

BLANK_FIELDS = {"SC": ["proposition", "scope", "classification", "basis", "usable", "limits"],
                "CT": ["question", "method", "outcomes", "controls", "stop", "expected_evidence"],
                "CU": ["reason"]}
BLANKS = [(kind, field) for kind, fields in BLANK_FIELDS.items() for field in fields]


@pytest.mark.parametrize("kind,field", BLANKS)
def test_blank_requires_staging_and_a_staged_blank_is_allowed(kind, field):
    rec = parse(kind, **{field: None})
    assert rows(rec) == [expect(kind, code_of(kind, field), f"{field}: required", rec, field)]
    assert rows(rec, staged=True) == []
    assert rows(parse(kind, **{field: "   "}), staged=True) == []


@pytest.mark.parametrize("kind", ["SC", "CT", "CU"])
def test_a_staged_record_with_every_free_field_blank(kind):
    fields = BLANK_FIELDS[kind]
    rec = parse(kind, **{field: None for field in fields})
    assert rows(rec, staged=True) == []
    assert [i.message for i in schema_issues(rec, staged=False)] == [f"{field}: required" for field in fields]


@pytest.mark.parametrize("kind,field", [("SC", "proposition"), ("CT", "question"), ("CU", "reason")])
def test_a_staged_value_that_is_present_is_still_checked(kind, field):
    rec = parse(kind, **{field: 5})
    assert rows(rec, staged=True) == [expect(kind, code_of(kind, field),
                                             f"{field}: expected a non-empty string", rec, field)]


@pytest.mark.parametrize("kind,field,message", [
    ("SC", "scope", "scope: expected a list of non-empty strings"),
    ("SC", "basis", "basis: expected a list of basis entries"),
    ("CT", "controls", "controls: expected a list of non-empty strings"),
    ("CT", "expected_evidence", "expected_evidence: expected a list of non-empty strings")])
def test_a_staged_blank_list_is_allowed_but_a_wrong_type_is_not(kind, field, message):
    assert rows(parse(kind, **{field: []}), staged=True) == []
    rec = parse(kind, **{field: "not a list"})
    assert rows(rec, staged=True) == [expect(kind, code_of(kind, field), message, rec, field)]


@pytest.mark.parametrize("kind,field", [("SC", "scope"), ("CT", "controls"), ("CT", "expected_evidence")])
def test_a_blank_list_entry(kind, field):
    rec = parse(kind, **{field: ["ok", ""]})
    assert rows(rec) == [expect(kind, code_of(kind, field), f"{field}[1]: required", rec, field)]
    assert rows(rec, staged=True) == []
    rec = parse(kind, **{field: ["ok", 5]})
    assert rows(rec) == [expect(kind, code_of(kind, field),
                                f"{field}[1]: expected a non-empty string", rec, field)]


# --- linked findings ----------------------------------------------------------------------------

def test_linked_findings():
    assert rows(parse("SC", linked_findings=[])) == []
    assert rows(parse("SC", linked_findings=["F-0001", "F-0002"])) == []
    rec = parse("SC", linked_findings=["F-0001", "F-1"])
    assert rows(rec) == [expect("SC", "K13", "linked_findings[1]: 'F-1' is not a finding ID (F-NNNN)",
                                rec, "linked_findings")]
    rec = parse("SC", linked_findings=None)
    assert rows(rec) == [expect("SC", "K13", "linked_findings: required", rec, "linked_findings")]
    assert rows(rec, staged=True) == []
    rec = parse("SC", linked_findings="F-0001")
    assert rows(rec) == [expect("SC", "K13", "linked_findings: expected a list of finding IDs",
                                rec, "linked_findings")]


# --- file references ----------------------------------------------------------------------------

def test_a_valid_source_and_its_pins():
    assert rows(parse("SC")) == []
    assert rows(parse("SC", source=source_ref(repo=SOURCE_REPO, commit=OID, blob=OID64))) == []
    assert rows(parse("SC", source=source_ref(snapshot="evidence/2026-09-27-trace/README.md"))) == []


def test_source_keys():
    rec = parse("SC", source=source_ref(colour="blue"))
    assert rows(rec) == [expect("SC", "K13", "source: unknown key 'colour'", rec, "source")]
    ref = source_ref()
    del ref["sha256"]
    rec = parse("SC", source=ref)
    assert rows(rec) == [expect("SC", "K13", "source: missing key 'sha256'", rec, "source")]
    rec = parse("SC", source=None)
    assert rows(rec) == [expect("SC", "K13", "source: required", rec, "source")]
    assert rows(rec, staged=True) == []
    rec = parse("SC", source="a/path.md")
    assert rows(rec) == [expect("SC", "K13", "source: expected a file reference with an assertion",
                                rec, "source")]


@pytest.mark.parametrize("raw,reason", [("/etc/passwd", "absolute path"),
                                        ("../notes/trace.md", "'..' segment"),
                                        ("notes/x:y.md", "':' (an alternate data stream)")])
def test_path_syntax_is_checked_where_a_path_appears(raw, reason):
    rec = parse("SC", source=source_ref(path=raw))
    assert rows(rec) == [expect("SC", "K13", f"source.path: {reason}", rec, "source")]
    rec = parse("SC", source=source_ref(snapshot=raw))
    assert rows(rec) == [expect("SC", "K13", f"source.snapshot: {reason}", rec, "source")]
    rec = parse("SC", basis=[basis_entry(path=raw)])
    assert rows(rec) == [expect("SC", "K13", f"basis[0].path: {reason}", rec, "basis")]
    rec = parse("CU", citation=citation(path=raw))
    assert rows(rec) == [expect("CU", "K13", f"citation.path: {reason}", rec, "citation")]


def test_repo_commit_and_blob_are_all_set_or_all_null():
    rec = parse("SC", source=source_ref(commit=OID))
    assert rows(rec) == [expect("SC", "K13", "source: repo, commit and blob must be all set or all null",
                                rec, "source")]
    rec = parse("SC", source=source_ref(repo=SOURCE_REPO, commit=OID))
    assert rows(rec) == [expect("SC", "K13", "source: repo, commit and blob must be all set or all null",
                                rec, "source")]
    assert rows(parse("SC", source=source_ref(repo=SOURCE_REPO, commit=OID, blob=OID))) == []
    assert rows(parse("SC", source=source_ref(repo=None, commit=None, blob=None))) == []


@pytest.mark.parametrize("field,message", [
    ("sha256", "source.sha256: expected 64 lowercase hex digits"),
    ("commit", "source.commit: expected a hex object ID of 40 or 64 digits"),
    ("blob", "source.blob: expected a hex object ID of 40 or 64 digits")])
def test_hex_and_object_id_syntax(field, message):
    ref = source_ref(repo=SOURCE_REPO, commit=OID, blob=OID)
    ref[field] = "Zz" * 20 if field == "sha256" else "A" * 40
    rec = parse("SC", source=ref)
    assert rows(rec) == [expect("SC", "K13", message, rec, "source")]


def test_a_missing_reference_path():
    rec = parse("SC", source=source_ref(path=None))
    assert rows(rec) == [expect("SC", "K13", "source.path: required", rec, "source")]


# --- the SC's assertion, basis and judgement ----------------------------------------------------

def test_assertion_is_a_mapping_of_its_four_keys():
    rec = parse("SC", source=source_ref(assertion=None))
    assert rows(rec) == [expect("SC", "K13", "source.assertion: required", rec, "source")]
    assert rows(rec, staged=True) == []
    rec = parse("SC", source=source_ref(assertion=assertion(colour=1)))
    assert rows(rec) == [expect("SC", "K13", "source.assertion: unknown key 'colour'", rec, "source")]
    entry = assertion()
    del entry["occurrence"]
    rec = parse("SC", source=source_ref(assertion=entry))
    assert rows(rec) == [expect("SC", "K13", "source.assertion: missing key 'occurrence'", rec, "source")]
    rec = parse("SC", source=source_ref(assertion="row 102"))
    assert rows(rec) == [expect("SC", "K13", "source.assertion: expected a mapping of lines, text, "
                                             "sha256 and occurrence", rec, "source")]


@pytest.mark.parametrize("lines", [[3], [5, 3], [0, 3], [3, "5"], "3-5", []])
def test_assertion_lines_are_a_line_range(lines):
    rec = parse("SC", source=source_ref(assertion=assertion(lines=lines)))
    assert rows(rec) == [expect("SC", "K13", "source.assertion.lines: expected [A, B] with 1 <= A <= B",
                                rec, "source")]


def test_assertion_text_sha256_and_occurrence():
    rec = parse("SC", source=source_ref(assertion=assertion(text="")))
    assert rows(rec) == [expect("SC", "K13", "source.assertion.text: required", rec, "source")]
    rec = parse("SC", source=source_ref(assertion=assertion(sha256=None)))
    assert rows(rec) == [expect("SC", "K13", "source.assertion.sha256: required", rec, "source")]
    rec = parse("SC", source=source_ref(assertion=assertion(occurrence=0)))
    assert rows(rec) == [expect("SC", "K13", "source.assertion.occurrence: expected an integer >= 1",
                                rec, "source")]
    rec = parse("SC", source=source_ref(assertion=assertion(sha256=None, occurrence=None)))
    assert rows(rec, staged=True) == []


def test_booleans_are_not_integers():
    rec = parse("SC", source=source_ref(assertion=assertion(occurrence=True)))
    assert rows(rec) == [expect("SC", "K13", "source.assertion.occurrence: expected an integer >= 1",
                                rec, "source")]
    rec = parse("SC", source=source_ref(assertion=assertion(lines=[True, True])))
    assert rows(rec) == [expect("SC", "K13", "source.assertion.lines: expected [A, B] with 1 <= A <= B",
                                rec, "source")]
    rec = parse("CU", citation=citation(ordinal=True))
    assert rows(rec) == [expect("CU", "K13", "citation.ordinal: expected an integer >= 1", rec, "citation")]
    rec = parse("CU", citation=citation(range=[True]))
    assert rows(rec) == [expect("CU", "K13", "citation.range: expected [A, B] with 1 <= A <= B, "
                                             "or [N] with N >= 0", rec, "citation")]


def test_basis_entries():
    assert rows(parse("SC")) == []
    rec = parse("SC", basis=[])
    assert rows(rec) == [expect("SC", "K13", "basis: required", rec, "basis")]
    assert rows(rec, staged=True) == []
    rec = parse("SC", basis=[basis_entry(), basis_entry(role="foo")])
    assert rows(rec) == [expect("SC", "K13",
                                "basis[1].role: 'foo' is not one of counterevidence, "
                                "internal-inconsistency, missing-support, model-mismatch", rec, "basis")]


def test_basis_entry_keys_and_values():
    rec = parse("SC", basis=[basis_entry(colour="blue")])
    assert rows(rec) == [expect("SC", "K13", "basis[0]: unknown key 'colour'", rec, "basis")]
    entry = basis_entry()
    del entry["role"]
    rec = parse("SC", basis=[entry])
    assert rows(rec) == [expect("SC", "K13", "basis[0]: missing key 'role'", rec, "basis")]
    rec = parse("SC", basis=[basis_entry(provenance="Bad Name")])
    assert rows(rec) == [expect("SC", "K13", "basis[0].provenance: 'Bad Name' is not a name", rec, "basis")]
    rec = parse("SC", basis=[basis_entry(locator="")])
    assert rows(rec) == [expect("SC", "K13", "basis[0].locator: required", rec, "basis")]
    rec = parse("SC", basis=["a/path.md"])
    assert rows(rec) == [expect("SC", "K13", "basis[0]: expected a basis entry", rec, "basis")]


def test_a_staged_basis_entry_may_leave_its_hash_and_pin_null():
    rec = parse("SC", basis=[basis_entry(sha256=None, repo=None, commit=None, blob=None)])
    assert rows(rec) == [expect("SC", "K13", "basis[0].sha256: required", rec, "basis")]
    assert rows(rec, staged=True) == []
    assert rows(parse("SC", basis=[basis_entry()])) == []       # provisional: no pin needed


def test_scope_classification_usable_and_limits():
    rec = parse("SC", classification="wrong")
    assert rows(rec) == [expect("SC", "K13",
                                "classification: 'wrong' is not one of contradicted, unsupported, "
                                "wrong_model", rec, "classification")]
    rec = parse("SC", scope=["MX-100", ""], usable="", limits=None)
    assert [i.message for i in schema_issues(rec, staged=False)] == [
        "scope[1]: required", "usable: required", "limits: required"]
    assert rows(rec, staged=True) == []


# --- the CT's fields ----------------------------------------------------------------------------

def test_task_fields():
    rec = parse("CT", kind="other")
    assert rows(rec) == [expect("CT", "K15", "kind: 'other' is not one of replication, confirmation",
                                rec, "kind")]
    assert rows(parse("CT", kind="confirmation")) == []
    rec = parse("CT", finding="SC-0001")
    assert rows(rec) == [expect("CT", "K15", "finding: 'SC-0001' is not a finding ID (F-NNNN)",
                                rec, "finding")]
    rec = parse("CT", claim_fingerprint="3fa9c1d")
    assert rows(rec) == [expect("CT", "K15", "claim_fingerprint: expected 12 lowercase hex digits",
                                rec, "claim_fingerprint")]
    rec = parse("CT", base_file_sha256="abc")
    assert rows(rec) == [expect("CT", "K15", "base_file_sha256: expected 64 lowercase hex digits",
                                rec, "base_file_sha256")]
    assert rows(parse("CT", finding="F-0014", claim_fingerprint="0badf00d0000")) == []


def test_outcomes_has_exactly_its_three_keys():
    assert rows(parse("CT")) == []
    rec = parse("CT", outcomes={"supports": "a", "refutes": "b"})
    assert rows(rec) == [expect("CT", "K15", "outcomes: missing key 'inconclusive'", rec, "outcomes")]
    rec = parse("CT", outcomes={"supports": "a", "refutes": "b", "inconclusive": "c", "maybe": "d"})
    assert rows(rec) == [expect("CT", "K15", "outcomes: unknown key 'maybe'", rec, "outcomes")]
    rec = parse("CT", outcomes={"supports": "", "refutes": "b", "inconclusive": "c"})
    assert rows(rec) == [expect("CT", "K15", "outcomes.supports: required", rec, "outcomes")]
    rec = parse("CT", outcomes={"supports": "a", "refutes": "b", "inconclusive": None})
    assert rows(rec, staged=True) == []
    rec = parse("CT", outcomes=[])
    assert rows(rec) == [expect("CT", "K15", "outcomes: expected a mapping of supports, refutes, "
                                             "inconclusive", rec, "outcomes")]


def test_ct_field_codes():
    rec = parse("CT", question=None)
    assert rows(rec) == [expect("CT", "K15", "question: required", rec, "question")]
    rec = parse("CT", colour="blue")
    assert rows(rec) == [expect("CT", "K13", "unknown key 'colour'", rec, "colour")]
    rec = parse("CT", decisions=[decision(bind="xy")])
    assert rows(rec) == [expect("CT", "K13", "decisions[0].bind: expected 64 lowercase hex digits",
                                rec, "decisions")]


# --- the CU's fields ----------------------------------------------------------------------------

def test_use_fields():
    assert rows(parse("CU")) == []
    rec = parse("CU", challenge="CT-0001")
    assert rows(rec) == [expect("CU", "K13", "challenge: 'CT-0001' is not a challenge ID (SC-NNNN)",
                                rec, "challenge")]
    rec = parse("CU", disposition="other")
    assert rows(rec) == [expect("CU", "K13", "disposition: 'other' is not one of "
                                             "unaffected_raw_bytes, rewritten_claim", rec, "disposition")]
    rec = parse("CU", finding_fingerprint="0BADF00D")
    assert rows(rec) == [expect("CU", "K13", "finding_fingerprint: expected 12 lowercase hex digits",
                                rec, "finding_fingerprint")]
    rec = parse("CU", finding_file_sha256="x")
    assert rows(rec) == [expect("CU", "K13", "finding_file_sha256: expected 64 lowercase hex digits",
                                rec, "finding_file_sha256")]
    rec = parse("CU", challenge_bind="x")
    assert rows(rec) == [expect("CU", "K13", "challenge_bind: expected 64 lowercase hex digits",
                                rec, "challenge_bind")]


def test_citation_accepts_a_line_range_and_an_offset_range():
    assert rows(parse("CU", citation=citation(range=[63, 65]))) == []
    assert rows(parse("CU", citation=citation(range=[0]))) == []
    assert rows(parse("CU", citation=citation(range=[4096]))) == []


@pytest.mark.parametrize("value", [[5, 3], [], [1, 2, 3], "63-65", [0, 0], None])
def test_citation_range_shape(value):
    rec = parse("CU", citation=citation(range=value))
    message = "citation.range: required" if value is None else \
        "citation.range: expected [A, B] with 1 <= A <= B, or [N] with N >= 0"
    assert rows(rec) == [expect("CU", "K13", message, rec, "citation")]


def test_citation_keys_and_values():
    rec = parse("CU", citation=citation(ordinal=0))
    assert rows(rec) == [expect("CU", "K13", "citation.ordinal: expected an integer >= 1",
                                rec, "citation")]
    rec = parse("CU", citation=citation(colour="blue"))
    assert rows(rec) == [expect("CU", "K13", "citation: unknown key 'colour'", rec, "citation")]
    entry = citation()
    del entry["tag_sha256"]
    rec = parse("CU", citation=entry)
    assert rows(rec) == [expect("CU", "K13", "citation: missing key 'tag_sha256'", rec, "citation")]
    rec = parse("CU", citation=citation(tag_sha256=None))
    assert rows(rec) == [expect("CU", "K13", "citation.tag_sha256: required", rec, "citation")]
    rec = parse("CU", citation=None)
    assert rows(rec) == [expect("CU", "K13", "citation: required", rec, "citation")]
    assert rows(rec, staged=True) == []


# --- decisions ----------------------------------------------------------------------------------

def test_decisions_may_be_empty_at_every_stage():
    assert rows(parse("SC", decisions=[])) == []
    assert rows(parse("SC", decisions=[]), staged=True) == []
    rec = parse("SC", decisions=None)
    assert rows(rec) == [expect("SC", "K13", "decisions: required", rec, "decisions")]
    assert rows(rec, staged=True) == []
    rec = parse("SC", decisions=5)
    assert rows(rec) == [expect("SC", "K13", "decisions: expected a list of decision entries",
                                rec, "decisions")]


def test_a_valid_decision():
    assert rows(parse("SC", status="confirmed", decisions=[decision()])) == []
    assert rows(parse("CT", status="confirmed", decisions=[decision()])) == []


def test_decision_entry_shape():
    rec = parse("SC", decisions=["nope"])
    assert rows(rec) == [expect("SC", "K13", "decisions[0]: expected a mapping of decision fields",
                                rec, "decisions")]
    entry = decision()
    del entry["bind"]
    rec = parse("SC", decisions=[entry])
    assert rows(rec) == [expect("SC", "K13", "decisions[0]: missing key 'bind'", rec, "decisions")]
    rec = parse("SC", decisions=[decision(colour="blue")])
    assert rows(rec) == [expect("SC", "K13", "decisions[0]: unknown key 'colour'", rec, "decisions")]


@pytest.mark.parametrize("fields,message", [
    ({"date": "yesterday"}, "decisions[0].date: expected a date (YYYY-MM-DD)"),
    ({"date": None}, "decisions[0].date: required"),
    ({"by": "not a name"}, "decisions[0].by: 'not a name' is not a name"),
    ({"status": "approved"}, "decisions[0].status: 'approved' is not one of open, confirmed, rejected, stale"),
    ({"reason": ""}, "decisions[0].reason: required"),
    ({"bind": "xy"}, "decisions[0].bind: expected 64 lowercase hex digits")])
def test_decision_field_values(fields, message):
    rec = parse("SC", decisions=[decision(**fields)])
    assert rows(rec) == [expect("SC", "K13", message, rec, "decisions")]


def test_decision_evidence_entries():
    assert rows(parse("SC", decisions=[decision(evidence=[])])) == []
    entry = evidence_entry()
    del entry["locator"]
    rec = parse("SC", decisions=[decision(evidence=[entry])])
    assert rows(rec) == [expect("SC", "K13", "decisions[0].evidence[0]: missing key 'locator'",
                                rec, "decisions")]
    entry = evidence_entry(colour="blue")
    rec = parse("SC", decisions=[decision(evidence=[entry])])
    assert rows(rec) == [expect("SC", "K13", "decisions[0].evidence[0]: unknown key 'colour'",
                                rec, "decisions")]
    entry = evidence_entry(provenance="Not A Name")
    rec = parse("SC", decisions=[decision(evidence=[entry])])
    assert rows(rec) == [expect("SC", "K13",
                                "decisions[0].evidence[0].provenance: 'Not A Name' is not a name",
                                rec, "decisions")]
    rec = parse("SC", decisions=[decision(evidence=["a/path.md"])])
    assert rows(rec) == [expect("SC", "K13", "decisions[0].evidence[0]: expected an evidence entry",
                                rec, "decisions")]


# --- identity, lines and the fixture ------------------------------------------------------------

def test_every_issue_names_the_record_and_the_top_level_key_line():
    text = record_text("SC", "SC-0004", proposition="", basis=[basis_entry(role="foo")])
    rec = parse_record(record_path("SC", "SC-0004"), text.encode("utf-8"))
    issues = schema_issues(rec, staged=False)
    assert [i.code for i in issues] == ["K13", "K13"]
    assert all(i.owner == "SC-0004" and i.path == record_path("SC", "SC-0004") for i in issues)
    assert [i.line for i in issues] == [line_of(rec, "proposition"), line_of(rec, "basis")]
    assert issues[0].level == "error" and issues[0].is_error


JUNK = [None, "", "x", 0, 1, True, 1.5, [], [1], {}, {"a": 1}, date(2026, 9, 28)]


@pytest.mark.parametrize("kind", ["SC", "CT", "CU"])
def test_no_junk_value_makes_schema_issues_raise(kind):
    """Anything the round-trip loader can produce is reported, never a traceback: K13 runs on a
    hand-edited tree, and every later unit calls this on every record."""
    for key in KEYS[kind]:
        for junk in JUNK:
            for staged in (False, True):
                schema_issues(parse(kind, **{key: junk}), staged=staged)
    for junk in JUNK:
        nested = [parse("SC", source=source_ref(assertion=junk)),
                  parse("SC", basis=[junk]),
                  parse("SC", decisions=[junk]),
                  parse("SC", decisions=[decision(evidence=[junk])]),
                  parse("SC", decisions=[decision(evidence=junk)]),
                  parse("CT", outcomes=junk),
                  parse("CU", citation=junk)]
        for rec in nested:
            for staged in (False, True):
                schema_issues(rec, staged=staged)


@pytest.mark.parametrize("kind", ["SC", "CT", "CU"])
def test_the_conftest_builder_carries_exactly_the_kinds_keys(kind):
    """The fixture's records are the schema: a key the builder omits is a key nobody writes."""
    assert set(record_data(kind)) == set(KEYS[kind])
