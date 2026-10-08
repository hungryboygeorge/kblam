"""K15, claim task bindings (SPEC §5.2.4 K15, §5.2.3 Claim task)."""

from __future__ import annotations

import datetime
import json

import pytest

from conftest import ZERO64, dump_record, finding_text, record_data
from kblam.cli import main
from kblam.decisions import decision_issues, subject_digest
from kblam.finding import fingerprint
from kblam.k15 import k15, k15_pending, task_binding_problems
from kblam.records import schema_issues
from kblam.review_index import generate_review_index
from kblam.sources import SourceReader, sha256_hex
from kblam.view import load_view

REVIEW = "research-review"
CT = "claim-task-0001"
CT_PATH = f"{REVIEW}/tasks/{CT}.yaml"
MANIFEST = "evidence/2026-09-22-ratio/README.md"     # the kb fixture's manifest, outside the KB's own roots
LOG = "evidence/2026-09-22-ratio/log.txt"
FINDING = "findings/calibration/F-0001-ratio-agrees.md"
CLAIM = "The MX-200 and MX-100 curve types agree to 0.1% on line 0."
DATE = datetime.date(2026, 9, 28)
QUESTION = "Does an independent measurement establish the claim?"
REBIND = f"kblam review rebind {CT} --by NAME --reason TEXT --expect D"
REBIND_EVIDENCE = f"{REBIND} --evidence PROVENANCE:PATH:LOCATOR"


def finding_of(view, finding_id: str = "F-0001"):
    return next(f for f in view.findings if f.file_id == finding_id)


def task_data(view, rec_id: str = CT, **fields) -> dict:
    """A claim task bound to F-0001 as `view` now holds it; `fields` replace top-level keys."""
    finding = finding_of(view)
    data = record_data("claim-task", rec_id)
    data["claim_fingerprint"] = fingerprint(finding, view.cfg.scope_separator)
    data["base_file_sha256"] = sha256_hex(finding.raw)
    data.update(fields)
    return data


def evidence_entry(path: str, *, sha256: str | None = None, provenance: str = "observed", **fields) -> dict:
    entry = {"path": path, "sha256": sha256 or ZERO64, "repo": None, "commit": None, "blob": None,
             "snapshot": None, "locator": "section 1: the printed ratio", "provenance": provenance}
    entry.update(fields)
    return entry


def primary_entry(kb) -> dict:
    """A primary evidence entry on the fixture's manifest: a resolved path outside findings/, the review
    root and every history_dirs folder."""
    return evidence_entry(MANIFEST, sha256=sha256_hex((kb.root / MANIFEST).read_bytes()))


def decide(data: dict, status: str, entries: list[dict] = (), *, by: str = "reviewer-b") -> dict:
    """`data` with the decision that sets `status`: independent, and bound to the subject digest as parsed."""
    data["status"] = status
    data["decisions"] = [{"date": DATE, "by": by, "status": status,
                          "reason": "Reread the capture and repeated the run under the same controls.",
                          "evidence": list(entries), "bind": ZERO64}]
    data["decisions"][0]["bind"] = subject_digest("claim-task", data)
    return data


def write(kb, data: dict, rec_id: str = CT) -> None:
    kb.write(f"{REVIEW}/tasks/{rec_id}.yaml", dump_record(data))


def load(kb, rec_id: str = CT):
    """(the parsed record, the view, the reader) for the review root as the KB now stands."""
    view = load_view(kb.cfg)
    rec = next(r for r in view.records if r.id == rec_id)
    return rec, view, SourceReader(kb.cfg, view)


def run(kb, data: dict, rec_id: str = CT):
    """`load` after writing `data`, with the fixture's own sanity checked: K13 would otherwise be the one
    reporting it, and these tests are about K15."""
    write(kb, data, rec_id)
    rec, view, reader = load(kb, rec_id)
    assert schema_issues(rec, staged=False) == []
    assert decision_issues(rec) == []
    return rec, view, reader


def shape(issues) -> list[tuple]:
    """(code, level, owner, path, line, message) for each issue."""
    return [(i.code, i.level, i.owner, i.path, i.line, i.message) for i in issues]


@pytest.fixture
def task_kb(kb):
    kb.add("F-0001", "ratio-agrees", CLAIM)
    return kb


@pytest.mark.parametrize("problem", ["fingerprint", "file_sha", "missing_finding",
                                     "unavailable_evidence", "no_primary"])
def test_task_identity_is_restored_before_each_rebind_suggestion(task_kb, problem, capsys):
    data = task_data(load_view(task_kb.cfg))
    evidence_problem = problem in ("unavailable_evidence", "no_primary")
    if problem == "unavailable_evidence":
        raw = b"independent measurement\n"
        decide(data, "confirmed", [evidence_entry(LOG, sha256=sha256_hex(raw))])
    elif problem == "no_primary":
        decide(data, "confirmed")
    allocation = {key: str(data[key]) for key in ("id", "created", "creator", "proponent")}
    task_kb.write(f".kblam/review-receipts/{CT}.json", json.dumps(allocation) + "\n")
    backup = dump_record(data)
    finding_backup = (task_kb.root / FINDING).read_bytes()
    if problem == "fingerprint":
        task_kb.add("F-0001", "ratio-agrees", "The curves disagree by 5% on line 0.")
    elif problem == "file_sha":
        task_kb.add("F-0001", "ratio-agrees", CLAIM, title="A retitled finding")
    elif problem == "missing_finding":
        (task_kb.root / FINDING).unlink()
    data["creator"] = "another-reviewer"
    write(task_kb, data)
    task_kb.write(f"{REVIEW}/INDEX.md", generate_review_index(load_view(task_kb.cfg)))
    capsys.readouterr()                              # exclude the fixture's finding-index warnings

    def cli(*args):
        return main(["--root", str(task_kb.root), *args])

    assert cli("validate") == 1
    captured = capsys.readouterr()
    if problem == "missing_finding":
        assert cli("task", "show", CT) == 0
        captured = capsys.readouterr()
    recovery = (f"restore {CT} from git (its identity changed after allocation), then run "
                "kblam validate again")
    assert recovery in captured.out and "kblam review rebind" not in captured.out
    assert captured.err == ""
    if problem == "missing_finding":
        task_kb.write(FINDING, finding_backup)        # the separate finding prerequisite restored first
    if problem == "unavailable_evidence":
        task_kb.write(LOG, raw)                      # the message also names these bytes to restore
    blocked = ["review", "rebind", CT, "--by", "reviewer-b", "--reason", "reviewed-after-restoration",
               "--expect", subject_digest("claim-task", data)]
    if evidence_problem:
        blocked += ["--evidence", f"observed:{MANIFEST}:section 1"]
    assert cli(*blocked) == 1
    captured = capsys.readouterr()
    assert "creator is 'another-reviewer', but it was allocated as" in captured.out
    if problem == "missing_finding":
        (task_kb.root / FINDING).unlink()
    if problem == "unavailable_evidence":
        (task_kb.root / LOG).unlink()
    task_kb.write(CT_PATH, backup)                    # the named task restored from git
    assert cli("validate") == 1
    captured = capsys.readouterr()
    if problem == "missing_finding":
        assert cli("task", "show", CT) == 0
        captured = capsys.readouterr()
    expected = REBIND_EVIDENCE if evidence_problem else REBIND
    assert expected in captured.out and recovery not in captured.out
    command = captured.out[captured.out.index("kblam review rebind"):].splitlines()[0].split()
    values = {"NAME": "reviewer-b", "TEXT": "reviewed-after-restoration",
              "D": subject_digest("claim-task", load(task_kb)[0].data),
              "PROVENANCE:PATH:LOCATOR": f"observed:{MANIFEST}:section 1"}
    command = [values.get(token, token) for token in command][1:]
    if problem == "missing_finding":
        task_kb.write(FINDING, finding_backup)
    if problem == "unavailable_evidence":
        task_kb.write(LOG, raw)
    assert cli(*command) == 0
    capsys.readouterr()


# --- the binding (SPEC §5.2.3 Claim task, "A task binds both") ------------------------------------


def test_a_bound_open_task_is_pending(task_kb):
    data = task_data(load_view(task_kb.cfg))
    rec, view, reader = run(task_kb, data)

    assert task_binding_problems(view, rec) == []
    assert k15(view, reader) == []
    assert k15_pending(view, reader) == [f"{CT} open replication of F-0001: {QUESTION}"]


def test_the_pending_line_names_the_task_kind(task_kb):
    data = task_data(load_view(task_kb.cfg), kind="confirmation")
    _rec, view, reader = run(task_kb, data)

    assert k15_pending(view, reader) == [f"{CT} open confirmation of F-0001: {QUESTION}"]


def test_a_changed_claim_is_a_fingerprint_error(task_kb):
    data = task_data(load_view(task_kb.cfg))
    bound, bound_sha = data["claim_fingerprint"], data["base_file_sha256"]
    rec, _view, _reader = run(task_kb, data)
    fingerprint_line, sha_line = rec.key_line("claim_fingerprint"), rec.key_line("base_file_sha256")

    task_kb.add("F-0001", "ratio-agrees", "The MX-200 and MX-100 curve types agree to 5% on line 0.")
    rec, view, reader = load(task_kb)
    finding = finding_of(view)
    now, now_sha = fingerprint(finding, view.cfg.scope_separator), sha256_hex(finding.raw)
    assert now != bound and now_sha != bound_sha

    assert shape(k15(view, reader)) == [
        ("K15", "error", CT, CT_PATH, fingerprint_line,
         f"F-0001's fingerprint is now {now}, not the {bound} {CT} was bound to; reread it, then run "
         f"{REBIND}"),
        ("K15", "error", CT, CT_PATH, sha_line,
         f"F-0001's file now hashes to {now_sha}, not the {bound_sha} {CT} was bound to (the binding "
         f"covers the whole file, not only the fingerprint); reread it, then run {REBIND}"),
    ]
    assert k15_pending(view, reader) == []


def test_a_retitled_finding_is_a_file_bytes_error(task_kb):
    data = task_data(load_view(task_kb.cfg))
    bound = data["base_file_sha256"]
    rec, _view, _reader = run(task_kb, data)
    sha_line = rec.key_line("base_file_sha256")

    task_kb.add("F-0001", "ratio-agrees", CLAIM, title="A retitled finding")
    rec, view, reader = load(task_kb)
    finding = finding_of(view)
    now = sha256_hex(finding.raw)
    assert fingerprint(finding, view.cfg.scope_separator) == data["claim_fingerprint"]     # the title is outside the fingerprint

    message = (f"F-0001's file now hashes to {now}, not the {bound} {CT} was bound to (the binding covers "
               f"the whole file, not only the fingerprint); reread it, then run {REBIND}")
    assert shape(k15(view, reader)) == [("K15", "error", CT, CT_PATH, sha_line, message)]
    assert task_binding_problems(view, rec) == [message]
    assert k15_pending(view, reader) == []


def test_a_binding_that_no_longer_holds_is_also_an_error_on_a_closed_task(task_kb):
    data = task_data(load_view(task_kb.cfg))
    decide(data, "confirmed", [primary_entry(task_kb)])
    _rec, view, reader = run(task_kb, data)
    assert k15(view, reader) == []               # the closed task is bound and its evidence is available

    task_kb.add("F-0001", "ratio-agrees", CLAIM, title="A retitled finding")
    _rec, view, reader = load(task_kb)

    assert [(i.code, i.level, i.owner) for i in k15(view, reader)] == [("K15", "error", CT)]


def test_a_closed_tasks_binding_fix_command_cites_primary_evidence(task_kb):
    """SPEC §5.2.5: a rebind keeps the status and passes its closing checks again, so the command that
    fixes a confirmed task's binding must carry --evidence; an open task's must not."""
    view = load_view(task_kb.cfg)
    closed, open_ = task_data(view), task_data(view)
    decide(closed, "confirmed", [primary_entry(task_kb)])
    _rec, _view, _reader = run(task_kb, closed)
    task_kb.add("F-0001", "ratio-agrees", CLAIM, title="A retitled finding")
    bound = closed["base_file_sha256"]

    closed_rec, view, _reader = load(task_kb)
    now = sha256_hex(finding_of(view).raw)
    assert task_binding_problems(view, closed_rec) == [
        f"F-0001's file now hashes to {now}, not the {bound} {CT} was bound to (the binding covers the "
        f"whole file, not only the fingerprint); reread it, then run {REBIND_EVIDENCE}"]

    write(task_kb, open_)                       # the same binding, on an open task
    open_rec, view, _reader = load(task_kb)
    assert task_binding_problems(view, open_rec) == [
        f"F-0001's file now hashes to {now}, not the {bound} {CT} was bound to (the binding covers the "
        f"whole file, not only the fingerprint); reread it, then run {REBIND}"]


def test_a_retired_task_gets_no_binding_check(task_kb):
    data = task_data(load_view(task_kb.cfg))
    data["claim_fingerprint"], data["base_file_sha256"] = "deadbeef0000", ZERO64
    decide(data, "stale")
    rec, view, reader = run(task_kb, data)

    assert k15(view, reader) == []
    assert k15_pending(view, reader) == []
    assert len(task_binding_problems(view, rec)) == 2     # still there for the commands that list it


def test_a_finding_that_names_no_finding_is_left_to_k13(task_kb):
    data = task_data(load_view(task_kb.cfg), finding="F-0042")
    rec, view, reader = run(task_kb, data)

    assert task_binding_problems(view, rec) == [
        f"F-0042 names no finding in the KB; restore it, then run {REBIND}"]
    assert k15(view, reader) == []                        # the dangling link is K13's row
    assert k15_pending(view, reader) == []


def test_a_finding_id_used_twice_is_left_to_k1(task_kb):
    data = task_data(load_view(task_kb.cfg))
    rec, view, reader = run(task_kb, data)

    # A second finding with the same ID, written directly: the index is not what this test is about.
    task_kb.write("findings/calibration/F-0001-ratio-again.md", finding_text("F-0001", CLAIM))
    _rec, view, reader = load(task_kb)

    assert task_binding_problems(view, rec) == [
        f"F-0001 is the ID of 2 findings, so {CT}'s binding cannot be checked until the IDs are unique; "
        f"K1 reports the duplicate IDs"]
    assert k15(view, reader) == []
    assert k15_pending(view, reader) == []


def test_a_record_that_did_not_parse_is_left_to_k13(task_kb):
    task_kb.write(CT_PATH, "[not, a, mapping]\n")
    rec, view, reader = load(task_kb)
    assert rec.data is None

    assert task_binding_problems(view, rec) == []
    assert k15(view, reader) == []
    assert k15_pending(view, reader) == []


def test_a_binding_field_of_the_wrong_shape_is_left_to_the_schema(task_kb):
    data = task_data(load_view(task_kb.cfg), claim_fingerprint="nope", base_file_sha256="0")
    write(task_kb, data)
    rec, view, reader = load(task_kb)

    assert [i.code for i in schema_issues(rec, staged=False)] == ["K15", "K15"]   # schema_issues' own report
    assert task_binding_problems(view, rec) == []
    assert k15(view, reader) == []


def test_a_use_or_challenge_record_is_not_a_task(task_kb):
    task_kb.write(f"{REVIEW}/uses/checked-use-0001.yaml",
                  dump_record(record_data("checked-use", "checked-use-0001", finding_fingerprint="deadbeef0000")))
    task_kb.write(f"{REVIEW}/challenges/source-challenge-0001.yaml", dump_record(record_data("source-challenge", "source-challenge-0001")))
    _rec, view, reader = load(task_kb, "checked-use-0001")

    assert k15(view, reader) == []               # a checked use's binding is K13's warning row
    assert k15_pending(view, reader) == []


# --- the closing evidence (SPEC §5.2.3 "A closed task is current") --------------------------------


@pytest.mark.parametrize("status", ("confirmed", "not_reproduced"))
def test_a_closed_task_needs_primary_evidence(task_kb, status):
    data = task_data(load_view(task_kb.cfg))
    decide(data, status, [evidence_entry(LOG, sha256=sha256_hex((task_kb.root / LOG).read_bytes()),
                                         provenance="inferred")])
    rec, view, reader = run(task_kb, data)

    assert shape(k15(view, reader)) == [
        ("K15", "error", CT, CT_PATH, rec.key_line("decisions"),
         f"{CT} is {status} and its effective decision has no evidence entry whose provenance is in "
         f"[review] primary_provenance and whose path is outside findings/, the review root and every "
         f"history_dirs folder; cite one with {REBIND_EVIDENCE}")]
    assert k15_pending(view, reader) == []


def test_evidence_under_findings_is_not_primary(task_kb):
    data = task_data(load_view(task_kb.cfg))
    decide(data, "confirmed", [evidence_entry(FINDING, sha256=sha256_hex((task_kb.root / FINDING).read_bytes()))])
    rec, view, reader = run(task_kb, data)

    assert [i.message for i in k15(view, reader)] == [
        f"{CT} is confirmed and its effective decision has no evidence entry whose provenance is in "
        f"[review] primary_provenance and whose path is outside findings/, the review root and every "
        f"history_dirs folder; cite one with {REBIND_EVIDENCE}"]
    assert rec.key_line("decisions") > 0


def test_an_inconclusive_task_needs_no_primary_evidence(task_kb):
    data = task_data(load_view(task_kb.cfg))
    decide(data, "inconclusive", [])
    rec, view, reader = run(task_kb, data)

    assert k15(view, reader) == []
    assert k15_pending(view, reader) == []


def test_the_effective_evidence_must_be_available(task_kb):
    data = task_data(load_view(task_kb.cfg))
    decide(data, "confirmed", [evidence_entry(MANIFEST)])       # ZERO64: the working file has other bytes
    rec, view, reader = run(task_kb, data)

    assert shape(k15(view, reader)) == [
        ("K15", "error", CT, CT_PATH, rec.key_line("decisions"),
         f"the evidence {MANIFEST!r} of {CT}'s effective decision is stale: the working file has other "
         f"bytes; restore those bytes, then run {REBIND_EVIDENCE}")]


def test_pinned_evidence_with_no_readable_copy_is_unavailable(task_kb):
    data = task_data(load_view(task_kb.cfg))
    entry = evidence_entry(MANIFEST, snapshot="evidence/2026-09-22-ratio/absent-copy.md")
    decide(data, "confirmed", [entry])
    rec, view, reader = run(task_kb, data)

    assert [i.message for i in k15(view, reader)] == [
        f"the evidence {MANIFEST!r} of {CT}'s effective decision is unavailable: no copy with these bytes "
        f"can be read (the snapshot is missing or has other bytes); restore those bytes, then run "
        f"{REBIND_EVIDENCE}"]


def test_an_open_task_gets_no_evidence_check(task_kb):
    data = task_data(load_view(task_kb.cfg))
    data["decisions"] = [{"date": DATE, "by": "reviewer-b", "status": "open",
                          "reason": "Rebound to the revised claim.",
                          "evidence": [evidence_entry(MANIFEST)], "bind": ZERO64}]   # a stale reference
    _rec, view, reader = run(task_kb, data)

    assert k15(view, reader) == []               # the row reports nothing for an open task
    assert k15_pending(view, reader) == [f"{CT} open replication of F-0001: {QUESTION}"]


# --- pending lines (SPEC §5.2.4 K15) --------------------------------------------------------------


def test_pending_lines_are_in_id_order(task_kb):
    view = load_view(task_kb.cfg)
    write(task_kb, task_data(view, "claim-task-0002"), "claim-task-0002")
    write(task_kb, task_data(view, "claim-task-0009", question="Does the ninth run agree?"), "claim-task-0009")
    write(task_kb, task_data(view, "claim-task-0010", question="Does the tenth run agree?"), "claim-task-0010")
    _rec, view, reader = load(task_kb, "claim-task-0002")

    assert k15_pending(view, reader) == [
        f"claim-task-0002 open replication of F-0001: {QUESTION}",
        "claim-task-0009 open replication of F-0001: Does the ninth run agree?",
        "claim-task-0010 open replication of F-0001: Does the tenth run agree?",
    ]


def test_pending_skips_a_task_k13_reports(task_kb):
    view = load_view(task_kb.cfg)
    write(task_kb, task_data(view), CT)
    write(task_kb, task_data(view, "claim-task-0002", question=""), "claim-task-0002")
    misstated = task_data(view, "claim-task-0003")               # the last decision is not the record's status
    misstated["decisions"] = [{"date": DATE, "by": "reviewer-b", "status": "confirmed",
                               "reason": "Looks reproduced.", "evidence": [], "bind": ZERO64}]
    write(task_kb, misstated, "claim-task-0003")
    _rec, view, reader = load(task_kb)

    assert len(schema_issues(next(r for r in view.records if r.id == "claim-task-0002"), staged=False)) == 1
    assert len(decision_issues(next(r for r in view.records if r.id == "claim-task-0003"))) == 1
    assert k15_pending(view, reader) == [f"{CT} open replication of F-0001: {QUESTION}"]
    assert k15(view, reader) == []


def test_pending_skips_a_task_whose_binding_changed(task_kb):
    data = task_data(load_view(task_kb.cfg), claim_fingerprint="deadbeef0000")
    write(task_kb, data)
    rec, view, reader = load(task_kb)

    assert k15_pending(view, reader) == []
    assert shape(k15(view, reader)) == [
        ("K15", "error", CT, CT_PATH, rec.key_line("claim_fingerprint"),
         f"F-0001's fingerprint is now {fingerprint(finding_of(view), view.cfg.scope_separator)}, not the deadbeef0000 {CT} was bound to; "
         f"reread it, then run {REBIND}")]
