"""Staging and reading review records (SPEC §5.2.5): `challenge new/edit/show/uses`, `task
new/edit/show`, `use review` and `review list`. Offline, deterministic: dates come from a fixed
_today, and the source of a challenge is a nested Git repository built by the source_repo fixture."""

from __future__ import annotations

import datetime
import hashlib
import json

import pytest

from conftest import (KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, SOURCE_REPO, TRACE_PATH, TRACE_TEXT,
                      dump_record, record_data)
from kblam import cli, journal, k13, k14, matching, records, review_stage, writes
from kblam.decisions import subject_digest
from kblam.finding import fingerprint
from kblam.review_index import generate_review_index
from kblam.sources import sha256_hex
from kblam.store import StoreError
from kblam.view import load_view

from test_k14 import (CLAIM, LINE3, NEW_TEXT, REVIEW, TRACE, add_finding, deciding,
                      put as install_record, scene, sc, use)

TODAY = datetime.date(2026, 9, 28)
SC_DIR = f"{REVIEW}/{records.KINDS['source-challenge']}"
CT_DIR = f"{REVIEW}/{records.KINDS['claim-task']}"
CU_DIR = f"{REVIEW}/{records.KINDS['checked-use']}"
STAGING = ".kblam/review-staging"
RECEIPTS = ".kblam/review-receipts"
LINE2 = "Row 101: bytes 0x3A 0x3B"           # TRACE_TEXT line 2
WORD = "the two bytes are equal"             # TRACE_TEXT line 3, the assertion of sc()


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    """Every staged `created` is TODAY, so the receipts can be compared exactly."""
    monkeypatch.setattr(review_stage, "_today", lambda: TODAY)


# --- helpers ------------------------------------------------------------------------------------


def sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def parse(path) -> records.Record:
    """The staged file kblam wrote, as a parsed record: it parses, and staging accepts every blank."""
    rec = records.parse_record(f"{STAGING}/{path.name}", path.read_bytes())
    assert rec.data is not None, rec.error
    assert records.schema_issues(rec, staged=True) == []
    return rec


def staged(path) -> dict:
    return parse(path).data


def receipt(kb, name: str) -> dict:
    return json.loads((kb.root / RECEIPTS / name).read_text(encoding="utf-8"))


def receipt_names(kb) -> list[str]:
    return sorted(p.name for p in (kb.root / RECEIPTS).iterdir())


def installed(kb, kind: str, data: dict) -> None:
    """Write a record straight into the review root (fixture setup: put is review_write's, U17)."""
    install_record(kb, kind, data)


def ct(kb, status: str = "open", *, rec_id: str = "claim-task-0001", fid: str = "F-0001") -> dict:
    """A task bound to the finding as it is now, with the decision `status` needs."""
    finding = next(f for f in load_view(kb.cfg).findings if f.file_id == fid)
    return deciding("claim-task", record_data("claim-task", rec_id, status=status, finding=fid,
                                      claim_fingerprint=fingerprint(finding, kb.cfg.scope_separator),
                                      base_file_sha256=sha256_hex(finding.raw)))


def assert_nothing_staged(kb, name: str) -> None:
    assert not (kb.root / STAGING / name).exists()
    assert not (kb.root / RECEIPTS / f"{name}.json").exists()


# --- allocation ---------------------------------------------------------------------------------


def test_allocation_stays_above_every_place_a_number_is_kept(kb):
    kb.write(f"{SC_DIR}/source-challenge-0003.yaml", "junk\n")
    kb.write(f"{REVIEW}/elsewhere/source-challenge-0004.yaml", "junk\n")      # any folder under the review root
    kb.write(f"{STAGING}/source-challenge-0005.yaml", "junk\n")
    kb.write(".kblam/review-ids", json.dumps(["source-challenge-0006"]))
    kb.write(f"{RECEIPTS}/source-challenge-0007.json", "{}")
    assert review_stage.allocate_record_id(kb.cfg, "source-challenge") == "source-challenge-0008"


def test_allocation_counts_a_record_file_at_any_depth_or_name_position(kb):
    kb.write(f"{SC_DIR}/older/source-challenge-0042.yaml", "junk\n")
    assert review_stage.allocate_record_id(kb.cfg, "source-challenge") == "source-challenge-0043"


def test_allocation_is_per_prefix(kb):
    kb.write(f"{STAGING}/claim-task-0009.yaml", "junk\n")
    kb.write(f"{RECEIPTS}/checked-use-0004.json", "{}")
    assert review_stage.allocate_record_id(kb.cfg, "source-challenge") == "source-challenge-0001"
    assert review_stage.allocate_record_id(kb.cfg, "claim-task") == "claim-task-0010"
    assert review_stage.allocate_record_id(kb.cfg, "checked-use") == "checked-use-0005"


def test_a_gap_in_the_numbering_is_kept(kb):
    kb.write(f"{SC_DIR}/source-challenge-0004.yaml", "junk\n")
    kb.write(f"{STAGING}/source-challenge-0002.yaml", "junk\n")
    assert review_stage.allocate_record_id(kb.cfg, "source-challenge") == "source-challenge-0005"


def test_four_digits_becomes_five_after_9999(kb):
    kb.write(f"{SC_DIR}/source-challenge-9999.yaml", "junk\n")
    assert review_stage.allocate_record_id(kb.cfg, "source-challenge") == "source-challenge-10000"


def test_an_edit_base_receipt_keeps_the_number_too(kb):
    kb.write(f"{RECEIPTS}/source-challenge-0011.edit-base.json", "{}")
    assert review_stage.allocate_record_id(kb.cfg, "source-challenge") == "source-challenge-0012"


def test_a_name_that_is_no_id_is_ignored_rather_than_crashing_allocation(kb):
    for name in ("source-challenge-abc.json", "source-challenge-.json", "source-challenge-0001x.json",
                 "source-challenge-0002.yaml.bak"):
        kb.write(f"{RECEIPTS}/{name}", "{}")
    kb.write(".kblam/review-ids",
             json.dumps(["source-challenge-x", "source-challenge-0007", "claim-task-0003",
                         "source-challenge-"]))
    kb.write(f"{STAGING}/source-challenge-0005.yaml", "junk\n")
    assert review_stage.allocate_record_id(kb.cfg, "source-challenge") == "source-challenge-0008"
    assert review_stage.allocate_record_id(kb.cfg, "claim-task") == "claim-task-0004"


def test_an_unknown_prefix_is_refused(kb):
    with pytest.raises(StoreError, match="is not a record kind"):
        review_stage.allocate_record_id(kb.cfg, "F")


def test_an_unreadable_registry_refuses_allocation(kb):
    kb.write(".kblam/review-ids", "this is not json")
    with pytest.raises(StoreError, match="cannot be read"):
        review_stage.allocate_record_id(kb.cfg, "source-challenge")


# --- challenge new ------------------------------------------------------------------------------


def test_challenge_new_pins_a_committed_source_and_writes_the_receipt(kb, source_repo):
    path = review_stage.challenge_new(kb.cfg, source_repo.kb_path(), (2, 2), "researcher-a")
    assert path == kb.root / STAGING / "source-challenge-0001.yaml"
    rec = parse(path)
    assert list(rec.data) == list(records.KEYS["source-challenge"])
    source = rec.data["source"]
    assert source == {"path": TRACE, "sha256": sha(TRACE_TEXT), "repo": SOURCE_REPO,
                      "commit": source_repo.head(), "blob": source_repo.blob(TRACE_PATH),
                      "snapshot": None,
                      "assertion": {"lines": [2, 2], "text": LINE2, "sha256": sha(LINE2),
                                    "occurrence": 1}}
    assert rec.data["created"] == "2026-09-28"
    assert rec.data["creator"] == "researcher-a"
    assert rec.data["status"] == "open"
    assert (rec.data["proposition"], rec.data["scope"], rec.data["classification"],
            rec.data["basis"], rec.data["usable"], rec.data["limits"],
            rec.data["linked_findings"], rec.data["decisions"]) == ("", [], "", [], "", "", [], [])
    assert receipt(kb, "source-challenge-0001.json") == {
        "id": "source-challenge-0001", "created": "2026-09-28", "creator": "researcher-a",
        "source": {key: source[key] for key in records.REF_KEYS},
        "captured": {"lines": [2, 2], "text": LINE2}}


def test_a_dirty_working_file_stays_provisional(kb, source_repo):
    source_repo.write(TRACE_PATH, TRACE_TEXT + "Row 104: more\n")
    data = staged(review_stage.challenge_new(kb.cfg, TRACE, (2, 2), "researcher-a"))
    assert data["source"]["sha256"] == sha(TRACE_TEXT + "Row 104: more\n")
    assert (data["source"]["repo"], data["source"]["commit"], data["source"]["blob"]) == (None,) * 3


def test_a_crlf_working_file_is_captured_as_lf_text_and_stays_provisional(kb, source_repo):
    crlf = TRACE_TEXT.replace("\n", "\r\n")
    source_repo.write(TRACE_PATH, crlf)
    data = staged(review_stage.challenge_new(kb.cfg, TRACE, (1, 4), "researcher-a"))
    assert data["source"]["sha256"] == sha(crlf)
    assert data["source"]["commit"] is None          # the blob holds the LF bytes, the file does not
    assert data["source"]["assertion"]["text"] == TRACE_TEXT.rstrip("\n")


def test_the_captured_text_is_the_lines_without_a_final_newline(kb, source_repo):
    data = staged(review_stage.challenge_new(kb.cfg, TRACE, (2, 3), "researcher-a"))
    captured = "Row 101: bytes 0x3A 0x3B\nRow 102: bytes 0x3A 0x3B; the two bytes are equal."
    assert data["source"]["assertion"]["text"] == captured
    assert not captured.endswith("\n")
    assert data["source"]["assertion"]["sha256"] == sha(captured)


def test_the_occurrence_counts_overlapping_matches_in_the_whole_source(kb, source_repo):
    source_repo.commit("notes/overlap.md", "abababa\nababa\n")
    path_text = source_repo.kb_path("notes/overlap.md")
    data = staged(review_stage.challenge_new(kb.cfg, path_text, (2, 2), "researcher-a"))
    # "ababa" starts at 0, 2 (overlapping) and 8; the one within line 2 is the third.
    assert data["source"]["assertion"] == {"lines": [2, 2], "text": "ababa", "sha256": sha("ababa"),
                                           "occurrence": 3}


def test_an_ambiguous_assertion_is_refused_with_matching_s_own_message(kb, source_repo, monkeypatch):
    # The captured text is exactly the lines' span, so assertion_match finds one match for it; the
    # refusal path is what carries matching's wording through when it does not (put's narrowing case).
    message = "the assertion text occurs 2 times within lines 2-2; narrow it to one"
    monkeypatch.setattr(review_stage.matching, "assertion_match", lambda *args, **kwargs: message)
    with pytest.raises(StoreError) as exc:
        review_stage.challenge_new(kb.cfg, TRACE, (2, 2), "researcher-a")
    assert str(exc.value) == message
    assert_nothing_staged(kb, "source-challenge-0001")


def test_a_range_that_holds_no_text_is_refused(kb):
    kb.write("blank.md", "Row one\n   \nRow three\n")
    with pytest.raises(StoreError) as exc:
        review_stage.challenge_new(kb.cfg, "blank.md", (2, 2), "researcher-a")
    assert str(exc.value) == "lines 2-2 hold no text to challenge"
    kb.write("empty.md", "")
    with pytest.raises(StoreError) as exc:
        review_stage.challenge_new(kb.cfg, "empty.md", (1, 1), "researcher-a")
    assert str(exc.value) == "lines 1-1 hold no text to challenge"
    assert_nothing_staged(kb, "source-challenge-0001")


def test_lines_outside_the_source_are_refused_with_matching_s_message(kb, source_repo):
    with pytest.raises(StoreError) as exc:
        review_stage.challenge_new(kb.cfg, TRACE, (2, 9), "researcher-a")
    assert str(exc.value) == "lines 2-9 are outside the source (it has 4 lines)"
    assert_nothing_staged(kb, "source-challenge-0001")


def test_a_line_range_that_is_not_a_b_range_is_refused(kb, source_repo):
    for bad in ((3, 2), (0, 1), 3, (1, 2, 3)):
        with pytest.raises(StoreError, match="must be A-B"):
            review_stage.challenge_new(kb.cfg, TRACE, bad, "researcher-a")


@pytest.mark.parametrize("data", [b"\x00\x01binary", b"\xff\xfe\xfd"])
def test_a_binary_source_is_refused(kb, source_repo, data):
    source_repo.commit("notes/blob.bin", data)
    with pytest.raises(StoreError) as exc:
        review_stage.challenge_new(kb.cfg, source_repo.kb_path("notes/blob.bin"), (1, 1), "reviewer-a")
    assert str(exc.value) == "a binary source cannot be challenged in schema 1"
    assert_nothing_staged(kb, "source-challenge-0001")


@pytest.mark.parametrize("rel", [f"{SC_DIR}/source-challenge-0001.yaml", ".kblam/tree.hash", "history/old.md",
                                 "findings/calibration/F-0001-ratio.md"])
def test_a_protected_path_is_never_a_source(kb, rel):
    kb.write(rel, "text\n")
    with pytest.raises(StoreError, match="is never a source"):
        review_stage.challenge_new(kb.cfg, rel, (1, 1), "researcher-a")


def test_a_directory_and_a_missing_file_are_refused(kb):
    with pytest.raises(StoreError, match="evidence is a directory, not a file"):
        review_stage.challenge_new(kb.cfg, "evidence", (1, 1), "researcher-a")
    with pytest.raises(StoreError, match="evidence/nope.md does not exist"):
        review_stage.challenge_new(kb.cfg, "evidence/nope.md", (1, 1), "researcher-a")
    assert not (kb.root / STAGING).exists()


@pytest.mark.parametrize("bad,problem", [
    ("/etc/passwd", "absolute path"),
    ("../outside.md", "'..' segment"),
    ("C:/x.md", "drive letter"),
    ("resources/mx-docs/x.md:stream", "an alternate data stream"),
])
def test_a_bad_path_is_refused_with_its_own_reason(kb, bad, problem):
    with pytest.raises(StoreError) as exc:
        review_stage.challenge_new(kb.cfg, bad, (1, 1), "researcher-a")
    assert problem in str(exc.value)
    assert not (kb.root / STAGING).exists()


def test_a_history_folder_is_named_as_kblam_toml_configures_it(kb):
    kb.write("kblam.toml", KBLAM_TOML + 'history_dirs = ["retired"]\n' + NO_EMBEDDINGS + PROMPT_TOML)
    kb.write("retired/old.md", "text\n")
    with pytest.raises(StoreError, match="lies under retired/"):
        review_stage.challenge_new(kb.cfg, "retired/old.md", (1, 1), "researcher-a")


def test_a_malformed_by_name_is_refused(kb, source_repo):
    with pytest.raises(StoreError, match="is not a name"):
        review_stage.challenge_new(kb.cfg, TRACE, (1, 1), "not a name")
    assert not (kb.root / STAGING).exists()


def test_the_source_path_is_kept_as_written(kb, source_repo):
    written = f"./{TRACE}".replace("/", "\\")
    data = staged(review_stage.challenge_new(kb.cfg, written, (2, 2), "researcher-a"))
    assert data["source"]["path"] == TRACE
    assert receipt(kb, "source-challenge-0001.json")["source"]["path"] == TRACE


def test_an_existing_receipt_is_never_rewritten(kb, source_repo):
    kb.write(f"{RECEIPTS}/source-challenge-0001.json", "mine\n")
    path = review_stage.challenge_new(kb.cfg, TRACE, (2, 2), "researcher-a")
    assert path.name == "source-challenge-0002.yaml"
    assert (kb.root / RECEIPTS / "source-challenge-0001.json").read_text(encoding="utf-8") == "mine\n"


# --- challenge edit -----------------------------------------------------------------------------


def test_challenge_edit_copies_the_installed_bytes_and_records_the_edit_base(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo, "open"))
    before = (kb.root / SC_DIR / "source-challenge-0001.yaml").read_bytes()
    path = review_stage.challenge_edit(kb.cfg, "source-challenge-0001")
    assert path == kb.root / STAGING / "source-challenge-0001.yaml"
    assert path.read_bytes() == before
    assert receipt(kb, "source-challenge-0001.edit-base.json") == {"id": "source-challenge-0001", "sha256": sha256_hex(before)}
    assert receipt_names(kb) == ["source-challenge-0001.edit-base.json"]


def test_editing_a_challenge_that_is_not_open_is_refused(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo, "confirmed"))
    with pytest.raises(StoreError) as exc:
        review_stage.challenge_edit(kb.cfg, "source-challenge-0001")
    assert str(exc.value) == "source-challenge-0001 is confirmed; only an open challenge can be edited"
    assert not (kb.root / STAGING).exists()


def test_editing_a_staged_challenge_again_is_refused(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo, "open"))
    review_stage.challenge_edit(kb.cfg, "source-challenge-0001")
    with pytest.raises(StoreError) as exc:
        review_stage.challenge_edit(kb.cfg, "source-challenge-0001")
    assert "source-challenge-0001 is already staged at .kblam/review-staging/source-challenge-0001.yaml" in str(exc.value)
    assert "delete it to start again" in str(exc.value)


def test_editing_an_unknown_or_malformed_challenge_is_refused(kb):
    with pytest.raises(StoreError, match="is not installed at research-review/challenges/source-challenge-0001.yaml"):
        review_stage.challenge_edit(kb.cfg, "source-challenge-0001")
    with pytest.raises(StoreError, match="is not a challenge ID like source-challenge-0001"):
        review_stage.challenge_edit(kb.cfg, "F-0001")
    kb.write(f"{SC_DIR}/source-challenge-0001.yaml", "- not a mapping\n")
    with pytest.raises(StoreError) as exc:
        review_stage.challenge_edit(kb.cfg, "source-challenge-0001")
    assert str(exc.value) == ("research-review/challenges/source-challenge-0001.yaml did not parse (not a YAML "
                             "mapping); run kblam validate and do what its line for research-review/"
                             "challenges/source-challenge-0001.yaml says; once it is fixed, run kblam challenge edit "
                             "source-challenge-0001 again")
    assert not (kb.root / STAGING).exists()


# --- task new and edit --------------------------------------------------------------------------


def test_task_new_binds_the_finding_and_writes_the_receipt(kb):
    kb.add("F-0001", "ratio", CLAIM)
    finding = load_view(kb.cfg).findings[0]
    path = review_stage.task_new(kb.cfg, "F-0001", "replication", "researcher-a", "researcher-b")
    assert path == kb.root / STAGING / "claim-task-0001.yaml"
    rec = parse(path)
    assert list(rec.data) == list(records.KEYS["claim-task"])
    assert rec.data == {
        "schema": 1, "id": "claim-task-0001", "created": "2026-09-28", "creator": "researcher-a",
        "proponent": "researcher-b", "status": "open", "kind": "replication", "finding": "F-0001",
        "claim_fingerprint": fingerprint(finding, kb.cfg.scope_separator), "base_file_sha256": sha256_hex(finding.raw),
        "question": "", "method": "", "outcomes": {"supports": "", "refutes": "", "inconclusive": ""},
        "controls": [], "stop": "", "expected_evidence": [], "decisions": []}
    assert receipt(kb, "claim-task-0001.json") == {
        "id": "claim-task-0001", "created": "2026-09-28", "creator": "researcher-a", "proponent": "researcher-b",
        "kind": "replication", "finding": "F-0001", "claim_fingerprint": fingerprint(finding, kb.cfg.scope_separator),
        "base_file_sha256": sha256_hex(finding.raw)}


def test_task_new_refuses_a_kind_that_is_not_a_task_kind(kb):
    kb.add("F-0001", "ratio", CLAIM)
    with pytest.raises(StoreError, match="replication, confirmation"):
        review_stage.task_new(kb.cfg, "F-0001", "re-run", "researcher-a", "researcher-b")
    assert not (kb.root / STAGING).exists()


def test_task_new_refuses_a_finding_that_is_missing_or_present_twice(kb):
    with pytest.raises(StoreError, match="is not a finding ID like F-0137"):
        review_stage.task_new(kb.cfg, "F-1", "replication", "researcher-a", "researcher-b")
    with pytest.raises(StoreError, match="F-0001 is not in findings/"):
        review_stage.task_new(kb.cfg, "F-0001", "replication", "researcher-a", "researcher-b")
    kb.add("F-0001", "ratio", CLAIM)
    kb.write("findings/other/F-0001-twin.md", kb.root.joinpath(
        "findings/calibration/F-0001-ratio.md").read_text(encoding="utf-8"))
    with pytest.raises(StoreError, match="is the ID of 2 findings"):
        review_stage.task_new(kb.cfg, "F-0001", "replication", "researcher-a", "researcher-b")
    assert not (kb.root / STAGING).exists()


def test_task_new_refuses_a_finding_that_does_not_parse(kb):
    kb.write("findings/calibration/F-0001-broken.md", "no frontmatter at all\n")
    with pytest.raises(StoreError, match="F-0001 does not parse; run kblam validate"):
        review_stage.task_new(kb.cfg, "F-0001", "replication", "researcher-a", "researcher-b")
    assert not (kb.root / STAGING).exists()


def test_task_edit_copies_the_installed_bytes_and_records_the_edit_base(kb):
    kb.add("F-0001", "ratio", CLAIM)
    installed(kb, "claim-task", ct(kb))
    before = (kb.root / CT_DIR / "claim-task-0001.yaml").read_bytes()
    path = review_stage.task_edit(kb.cfg, "claim-task-0001")
    assert path.read_bytes() == before
    assert receipt(kb, "claim-task-0001.edit-base.json") == {"id": "claim-task-0001", "sha256": sha256_hex(before)}


def test_editing_a_staged_task_again_is_refused(kb):
    kb.add("F-0001", "ratio", CLAIM)
    installed(kb, "claim-task", ct(kb))
    review_stage.task_edit(kb.cfg, "claim-task-0001")
    with pytest.raises(StoreError) as exc:
        review_stage.task_edit(kb.cfg, "claim-task-0001")
    assert "claim-task-0001 is already staged at .kblam/review-staging/claim-task-0001.yaml" in str(exc.value)
    assert "delete it to start again" in str(exc.value)


def test_editing_a_task_that_is_not_open_is_refused(kb):
    kb.add("F-0001", "ratio", CLAIM)
    installed(kb, "claim-task", ct(kb, "confirmed"))
    with pytest.raises(StoreError) as exc:
        review_stage.task_edit(kb.cfg, "claim-task-0001")
    assert str(exc.value) == "claim-task-0001 is confirmed; only an open task can be edited"
    with pytest.raises(StoreError, match="is not a task ID like claim-task-0001"):
        review_stage.task_edit(kb.cfg, "source-challenge-0001")
    with pytest.raises(StoreError, match="is not installed at research-review/tasks/claim-task-0002.yaml"):
        review_stage.task_edit(kb.cfg, "claim-task-0002")


# --- use review ---------------------------------------------------------------------------------


def test_use_review_binds_the_challenge_the_finding_and_the_citation(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", LINE3)
    path = review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")
    assert path == kb.root / STAGING / "checked-use-0001.yaml"
    rec = parse(path)
    view, reader = scene(kb)
    challenge = next(r for r in view.records if r.id == "source-challenge-0001")
    finding = view.findings[0]
    match = matching.finding_matches(view, reader, finding)[0]
    assert list(rec.data) == list(records.KEYS["checked-use"])
    assert rec.data == {
        "schema": 1, "id": "checked-use-0001", "created": "2026-09-28", "creator": "reviewer-b",
        "proponent": "researcher-a", "status": "open", "challenge": "source-challenge-0001",
        "challenge_bind": subject_digest("source-challenge", challenge.data), "finding": "F-0001",
        "finding_fingerprint": fingerprint(finding, kb.cfg.scope_separator), "finding_file_sha256": sha256_hex(finding.raw),
        "citation": {"ordinal": 1, "path": TRACE, "range": list(match.range),
                     "tag_sha256": match.tag_sha256},
        "disposition": "", "reason": "", "decisions": []}
    assert receipt(kb, "checked-use-0001.json") == {
        "id": "checked-use-0001", "created": "2026-09-28", "creator": "reviewer-b", "proponent": "researcher-a",
        "challenge": "source-challenge-0001", "challenge_bind": subject_digest("source-challenge", challenge.data),
        "finding": "F-0001", "finding_fingerprint": fingerprint(finding, kb.cfg.scope_separator),
        "finding_file_sha256": sha256_hex(finding.raw),
        "citation": {"ordinal": 1, "path": TRACE, "range": list(match.range),
                     "tag_sha256": match.tag_sha256}}


def test_use_review_refuses_each_precondition_with_its_own_message(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", LINE3)

    with pytest.raises(StoreError, match="is not installed at research-review/challenges/source-challenge-0002.yaml"):
        review_stage.use_review(kb.cfg, "source-challenge-0002", "F-0001", 1, "reviewer-b", "researcher-a")
    with pytest.raises(StoreError, match="is not a challenge ID like source-challenge-0001"):
        review_stage.use_review(kb.cfg, "F-0001", "F-0001", 1, "reviewer-b", "researcher-a")
    with pytest.raises(StoreError, match="is not a finding ID like F-0137"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-1", 1, "reviewer-b", "researcher-a")
    with pytest.raises(StoreError, match="ordinal 0 must be an integer >= 1"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 0, "reviewer-b", "researcher-a")
    with pytest.raises(StoreError, match="--proponent 'not a name' is not a name"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "not a name")
    with pytest.raises(StoreError, match="F-0002 is not in findings/"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0002", 1, "reviewer-b", "researcher-a")
    with pytest.raises(StoreError, match="has no excerpt 2; it has 1 verbatim"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 2, "reviewer-b", "researcher-a")

    kb.write(f"{SC_DIR}/source-challenge-0001.yaml", "- not a record\n")
    with pytest.raises(StoreError, match="did not parse"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")
    assert not (kb.root / STAGING).exists()
    assert not (kb.root / RECEIPTS).exists()


@pytest.mark.parametrize("status", ["open", "rejected", "stale"])
def test_use_review_needs_a_confirmed_challenge(kb, source_repo, status):
    installed(kb, "source-challenge", sc(source_repo, status))
    add_finding(kb, "3", LINE3)
    with pytest.raises(StoreError, match=rf"source-challenge-0001 is {status}; only a confirmed challenge can be used"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")


def test_use_review_needs_an_available_source(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo, pin=False))
    add_finding(kb, "3", LINE3)
    (source_repo.root / TRACE_PATH).unlink()
    with pytest.raises(StoreError) as exc:
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")
    message = str(exc.value)
    assert message == (
        f"source-challenge-0001's source is not available (the working file is missing); restore the version source-challenge-0001 "
        f"was judged on, or retire it (kblam review decide source-challenge-0001 --status stale --by NAME --reason "
        f"TEXT --expect D) and write a new challenge "
        f"(kblam challenge new {TRACE} --lines A-B --by NAME)")
    assert "challenge pin" not in message      # pin takes an open challenge, and this one is confirmed


def test_use_review_refuses_a_finding_that_does_not_parse_or_is_present_twice(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    kb.write("findings/calibration/F-0001-broken.md", "no frontmatter at all\n")
    with pytest.raises(StoreError, match="F-0001 does not parse; run kblam validate"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")
    (kb.root / "findings/calibration/F-0001-broken.md").unlink()
    kb.add("F-0001", "ratio", CLAIM)
    kb.write("findings/other/F-0001-twin.md", kb.root.joinpath(
        "findings/calibration/F-0001-ratio.md").read_text(encoding="utf-8"))
    with pytest.raises(StoreError, match="is the ID of 2 findings"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")


def test_use_review_refuses_an_excerpt_that_fails_k10(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", "Row 102: bytes 0x3A 0x3C; the two bytes are equal.")
    with pytest.raises(StoreError, match="is not a verified text match"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")


def test_use_review_refuses_a_binary_exempt_excerpt(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    source_repo.commit("notes/blob.bin", b"\x00\x01binary")
    add_finding(kb, "@0x0", "anything at all", path=source_repo.kb_path("notes/blob.bin"))
    with pytest.raises(StoreError, match="is binary-exempt"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")


def test_use_review_refuses_an_excerpt_the_challenge_does_not_affect(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "2", LINE2)                     # beside the assertion, not affected by it
    with pytest.raises(StoreError, match=rf"source-challenge-0001 does not affect excerpt 1 of F-0001 \({TRACE}:2-2\)"):
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")


# --- challenge uses -----------------------------------------------------------------------------


def relation_lines(kb, challenge_id: str = "source-challenge-0001") -> list[str]:
    return review_stage.challenge_uses(kb.cfg, challenge_id).splitlines()


def excerpt_line(kb, extra: str = "") -> str:
    """The reported line of the one excerpt of F-0001, with the finding path and line K14 gives it."""
    view, reader = scene(kb)
    finding = view.findings[0]
    match = matching.finding_matches(view, reader, finding)[0]
    where = f"{finding.path}:{finding.body_start_line + match.start}"
    return f"F-0001 {where} excerpt 1{extra}"


def test_challenge_uses_lists_an_affected_excerpt_and_the_command(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", LINE3)
    assert relation_lines(kb) == [
        f"{excerpt_line(kb)} same: error; kblam use review source-challenge-0001 F-0001 1 --by NAME --proponent NAME"]
    assert review_stage.challenge_uses(kb.cfg, "source-challenge-0001").endswith("\n")


def test_a_covering_use_is_named_instead_of_an_error(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", LINE3)
    installed(kb, "checked-use", use(kb))
    assert relation_lines(kb) == [f"{excerpt_line(kb)} same: covered by checked-use-0001"]


def open_decide(use_id: str = "checked-use-0001", proponent: str = "researcher-a") -> str:
    """The step `challenge uses` names where an open use already covers the excerpt: the decision that
    approves it, with a `--by` of its own (k14._review_step)."""
    return (f"kblam review decide {use_id} --status approved --by NAME --reason TEXT --expect D; its --by "
            f"must not be its proponent ({use_id}'s proponent is {proponent})")


def test_an_open_use_makes_the_line_name_the_decide_that_clears_k14(kb, source_repo):
    """D49 for N1: where an open use already names the excerpt, `challenge uses` names that use's approving
    decision rather than a second `kblam use review`, and running the decision it prints approves the use
    and leaves K14 clean."""
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", LINE3)
    data = use(kb, status="open")
    installed(kb, "checked-use", data)
    assert relation_lines(kb) == [f"{excerpt_line(kb)} same: error; {open_decide()}"]

    view = load_view(kb.cfg)
    kb.write(view.review_index_path, generate_review_index(view))
    assert cli.main(["--root", str(kb.root), "review", "decide", "checked-use-0001", "--status", "approved",
                     "--by", "reviewer-b", "--reason", "the excerpt really is used for the printed bytes",
                     "--expect", subject_digest("checked-use", data)]) == 0
    assert relation_lines(kb) == [f"{excerpt_line(kb)} same: covered by checked-use-0001"]


def test_an_open_use_makes_a_version_unproved_line_name_the_same_decide(kb, source_repo):
    """N1: the "version unproved" line names the open use's approving decision too, beside the new
    challenge that version needs."""
    installed(kb, "source-challenge", sc(source_repo))
    source_repo.write(TRACE_PATH, NEW_TEXT)
    add_finding(kb, "4", LINE3)
    installed(kb, "checked-use", use(kb, status="open"))
    assert relation_lines(kb) == [
        f"{excerpt_line(kb)} other: error; {open_decide()}; or kblam challenge new {TRACE} --lines 4-4 --by NAME"]


def test_a_version_unproved_excerpt_names_both_commands(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    source_repo.write(TRACE_PATH, NEW_TEXT)
    add_finding(kb, "4", LINE3)
    assert relation_lines(kb) == [
        f"{excerpt_line(kb)} other: error; kblam use review source-challenge-0001 F-0001 1 --by NAME --proponent NAME; "
        f"or kblam challenge new {TRACE} --lines 4-4 --by NAME"]


def test_a_range_overlap_is_a_warning_to_read_the_finding(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", "Row 102: bytes 0x3A 0x3B")
    assert relation_lines(kb) == [f"{excerpt_line(kb)} range: warning; kblam edit F-0001"]


def test_a_range_overlap_stays_a_warning_even_when_a_use_covers_it(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", "Row 102: bytes 0x3A 0x3B")
    installed(kb, "checked-use", use(kb))                # a current use of that very excerpt
    view, reader = scene(kb)
    rec = next(r for r in view.records if r.id == "checked-use-0001")
    assert k13.use_current(view, reader, rec) is True
    assert relation_lines(kb) == [f"{excerpt_line(kb)} range: warning; kblam edit F-0001"]


def test_the_evidence_and_prose_warnings_are_each_listed_once(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    kb.add("F-0001", "ratio", CLAIM, body=f"See {TRACE} for the trace.", evidence=f"[{TRACE}]")
    lines = relation_lines(kb)
    assert [line.split(": ")[-1] for line in lines] == ["warning; kblam edit F-0001"] * 2
    assert " evidence: warning; " in lines[0]
    assert " prose: warning; " in lines[1]
    assert lines[0].startswith("F-0001 findings/calibration/F-0001-ratio.md:")


def test_challenge_uses_of_a_challenge_that_is_not_confirmed_says_so(kb, source_repo):
    for status in ("open", "rejected", "stale"):
        installed(kb, "source-challenge", sc(source_repo, status))
        assert relation_lines(kb) == [f"source-challenge-0001 is {status}; only a confirmed challenge affects findings"]


def test_challenge_uses_reports_nothing_for_a_confirmed_challenge_nothing_relates_to(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "2", LINE2)
    assert relation_lines(kb) == ["no finding excerpt or reference relates to source-challenge-0001"]


def test_challenge_relations_is_empty_for_a_challenge_k14_does_not_confirm(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo, "open"))
    add_finding(kb, "3", LINE3)
    view, reader = scene(kb)
    assert k14.challenge_relations(view, reader, "source-challenge-0001") == []
    assert k14.challenge_relations(view, reader, "source-challenge-0009") == []


def test_challenge_uses_refuses_an_unknown_or_malformed_challenge(kb):
    with pytest.raises(StoreError, match="is not installed at research-review/challenges/source-challenge-0001.yaml"):
        review_stage.challenge_uses(kb.cfg, "source-challenge-0001")
    kb.write(f"{SC_DIR}/source-challenge-0001.yaml", "- not a mapping\n")
    with pytest.raises(StoreError, match="did not parse"):
        review_stage.challenge_uses(kb.cfg, "source-challenge-0001")


# --- show ---------------------------------------------------------------------------------------


def test_challenge_show_prints_the_digest_the_version_and_the_decision(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo, "confirmed"))
    rec = next(r for r in load_view(kb.cfg).records if r.id == "source-challenge-0001")
    digest = subject_digest("source-challenge", rec.data)
    lines = review_stage.challenge_show(kb.cfg, "source-challenge-0001").splitlines()
    assert lines[0] == "source-challenge-0001 confirmed"
    assert lines[1] == f"subject digest: {digest}"
    assert lines[2] == f"source: {TRACE}"
    assert lines[3] == f"version: {source_repo.blob(TRACE_PATH)}"
    assert lines[4] == "state: current"
    assert lines[5] == "assertion: lines 3-3"
    assert lines[6] == f"  {WORD}"
    assert "proposition: The printed byte equality follows from the printed byte values" in lines
    assert "scope: MX-100 capture transcription" in lines
    assert "classification: contradicted" in lines
    assert "basis:" in lines
    assert (f"  {TRACE} current: row 102: printed byte values (internal-inconsistency, observed)"
            in lines)
    assert "usable: The printed byte values may be cited as a report." in lines
    assert "limits: Do not infer the capture bytes from this row." in lines
    assert "linked findings: none" in lines
    assert lines[-1].endswith(f"confirmed: reviewed the record bind {digest[:12]}")
    assert review_stage.challenge_show(kb.cfg, "source-challenge-0001").endswith("\n")


def test_challenge_show_reports_a_provisional_and_an_unavailable_source(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo, "open", pin=False))
    text = review_stage.challenge_show(kb.cfg, "source-challenge-0001")
    assert "version: provisional" in text
    assert "state: current" in text
    (source_repo.root / TRACE_PATH).unlink()
    text = review_stage.challenge_show(kb.cfg, "source-challenge-0001")
    assert "state: unavailable (the working file is missing)" in text


def test_challenge_show_refuses_a_malformed_record(kb):
    with pytest.raises(StoreError, match="is not installed at"):
        review_stage.challenge_show(kb.cfg, "source-challenge-0001")
    kb.write(f"{SC_DIR}/source-challenge-0001.yaml", "- not a mapping\n")
    with pytest.raises(StoreError) as exc:
        review_stage.challenge_show(kb.cfg, "source-challenge-0001")
    assert str(exc.value) == ("research-review/challenges/source-challenge-0001.yaml did not parse (not a YAML "
                             "mapping); run kblam validate and do what its line for research-review/"
                             "challenges/source-challenge-0001.yaml says")


def test_task_show_prints_the_binding_and_whether_it_holds(kb):
    kb.add("F-0001", "ratio", CLAIM)
    installed(kb, "claim-task", ct(kb))
    lines = review_stage.task_show(kb.cfg, "claim-task-0001").splitlines()
    rec = next(r for r in load_view(kb.cfg).records if r.id == "claim-task-0001")
    finding = load_view(kb.cfg).findings[0]
    assert lines[0] == "claim-task-0001 open"
    assert lines[1] == f"subject digest: {subject_digest('claim-task', rec.data)}"
    assert lines[2] == "kind: replication"
    assert lines[3] == "finding: F-0001"
    assert lines[4] == "proponent: researcher-a"
    assert lines[5] == (f"binding: fingerprint {fingerprint(finding, kb.cfg.scope_separator)}, file sha256 "
                        f"{sha256_hex(finding.raw)}")
    assert lines[6] == "binding: current"
    assert "question: Does an independent measurement establish the claim?" in lines
    assert "outcomes.supports: The ratio is within 0.1%." in lines
    assert "outcomes.inconclusive: The capture is too noisy to tell." in lines
    assert "controls: same firmware version" in lines
    assert "expected evidence: an evidence/ capture package" in lines
    assert any(line.startswith("decisions:") for line in lines)

    kb.add("F-0001", "ratio", CLAIM + " Edited.")
    lines = review_stage.task_show(kb.cfg, "claim-task-0001").splitlines()
    bound = [line for line in lines if line.startswith("binding: ")]
    assert bound[0] == (f"binding: fingerprint {rec.data['claim_fingerprint']}, file sha256 "
                        f"{rec.data['base_file_sha256']}")
    problems = bound[1:]
    assert "binding: current" not in problems
    assert any("'s fingerprint is now " in line for line in problems)
    assert any("file now hashes to " in line for line in problems)


# --- review list --------------------------------------------------------------------------------


def listed(kb, **kw):
    return review_stage.review_list(kb.cfg, **kw)


def test_review_list_orders_by_kind_then_id_and_shows_each_subject(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo, "confirmed"))
    installed(kb, "source-challenge", record_data("source-challenge", "source-challenge-0010"))               # a stale one: sha256 is a placeholder
    installed(kb, "source-challenge", record_data("source-challenge", "source-challenge-0002"))
    add_finding(kb, "3", LINE3)
    installed(kb, "claim-task", ct(kb))
    installed(kb, "checked-use", use(kb, status="open"))
    items = listed(kb)
    assert [item.id for item in items] == ["source-challenge-0001", "source-challenge-0002", "source-challenge-0010", "claim-task-0001", "checked-use-0001"]
    assert [item.kind for item in items] == ["source-challenge", "source-challenge", "source-challenge", "claim-task", "checked-use"]
    assert review_stage.format_listed(items[0]) == (
        f"source-challenge-0001 challenge confirmed {items[0].digest[:12]} {TRACE}:3-3 current")
    assert review_stage.format_listed(items[1]) == (
        f"source-challenge-0002 challenge open {items[1].digest[:12]} {TRACE}:3-3 stale")
    assert review_stage.format_listed(items[3]) == (
        f"claim-task-0001 task open {items[3].digest[:12]} replication of F-0001 current")
    assert review_stage.format_listed(items[4]) == (
        f"checked-use-0001 use open {items[4].digest[:12]} source-challenge-0001 in F-0001 excerpt 1 current")
    assert items[0].digest == subject_digest("source-challenge", next(
        r for r in load_view(kb.cfg).records if r.id == "source-challenge-0001").data)


def test_review_list_open_filter_and_skips_records_that_do_not_parse(kb, source_repo):
    installed(kb, "source-challenge", sc(source_repo, "confirmed"))
    installed(kb, "source-challenge", record_data("source-challenge", "source-challenge-0002", status="rejected"))
    installed(kb, "source-challenge", record_data("source-challenge", "source-challenge-0003"))
    kb.write(f"{SC_DIR}/source-challenge-0004.yaml", "- not a mapping\n")
    kb.write(f"{SC_DIR}/notes.yaml", "junk\n")
    assert [item.id for item in listed(kb, only_open=True)] == ["source-challenge-0003"]
    assert [item.status for item in listed(kb)] == ["confirmed", "rejected", "open"]


# --- the lock, the journal and the review root --------------------------------------------------


def test_a_staging_command_recovers_a_leftover_journal(kb, source_repo, capsys):
    journal.begin(kb.cfg, "put", ["findings/calibration/F-0001-ratio.md"])
    path = review_stage.challenge_new(kb.cfg, TRACE, (2, 2), "researcher-a")
    assert path.is_file()
    assert not kb.cfg.journal_path.exists()
    assert "the interrupted put may be partial" in capsys.readouterr().err


def test_a_staging_command_does_not_refuse_a_changed_review_root(kb, source_repo):
    installed(kb, "source-challenge", record_data("source-challenge", "source-challenge-0001"))
    kb.write(".kblam/tree.hash", f"kblam-tree-v2 other-review {'a' * 64}\n")
    assert writes.mutation_refusal(kb.cfg) is not None      # a mutating command would refuse here
    path = review_stage.challenge_new(kb.cfg, TRACE, (2, 2), "researcher-a")
    assert path.is_file()
    assert (kb.root / RECEIPTS / "source-challenge-0002.json").is_file()


def test_staging_leaves_the_review_root_the_registry_and_tree_hash_alone(kb, source_repo):
    kb.add("F-0001", "ratio", CLAIM)
    kb.write(f"{SC_DIR}/source-challenge-0001.yaml", dump_record(record_data("source-challenge", "source-challenge-0001")))
    tree_hash = (kb.root / ".kblam/tree.hash").read_bytes()
    review_before = {p.relative_to(kb.root).as_posix(): p.read_bytes()
                     for p in (kb.root / REVIEW).rglob("*") if p.is_file()}
    review_stage.challenge_new(kb.cfg, TRACE, (2, 2), "researcher-a")
    review_stage.task_new(kb.cfg, "F-0001", "replication", "researcher-a", "researcher-b")
    assert (kb.root / ".kblam/tree.hash").read_bytes() == tree_hash
    assert not (kb.root / ".kblam/review-ids").exists()
    assert {p.relative_to(kb.root).as_posix(): p.read_bytes()
            for p in (kb.root / REVIEW).rglob("*") if p.is_file()} == review_before


# --- R3e 1b: the command to run again once an unparseable record is fixed --------------------------


def test_use_review_names_the_command_to_run_again_after_a_damaged_challenge(kb, source_repo):
    """D49 for the step `use review` adds when the challenge it needs does not parse (R3e 1b): the refusal
    names kblam validate's line for the file and the command to run again once it is fixed. The probe puts
    the file back as it was and runs that command, which then stages the use."""
    installed(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", LINE3)
    path = f"{SC_DIR}/source-challenge-0001.yaml"
    sound = (kb.root / path).read_bytes()
    kb.write(path, "- not a mapping\n")

    with pytest.raises(StoreError) as exc:
        review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")
    assert str(exc.value) == (
        f"{path} did not parse (not a YAML mapping); run kblam validate and do what its line for {path} "
        f"says; once it is fixed, run kblam use review source-challenge-0001 F-0001 1 --by reviewer-b --proponent "
        f"researcher-a again")

    (kb.root / path).write_bytes(sound)                    # the fix its line names, run
    staged = review_stage.use_review(kb.cfg, "source-challenge-0001", "F-0001", 1, "reviewer-b", "researcher-a")
    assert staged == kb.root / STAGING / "checked-use-0001.yaml"    # the command it says to run again, run
