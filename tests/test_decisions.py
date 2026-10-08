"""Subject digests, the status-change table, independence and the stored-decision checks (SPEC §5.2.2
Subject digest, Decisions, Status changes). Offline: records are built as data, no parsing, no git."""

from __future__ import annotations

import datetime
import hashlib
import json
import re

import pytest

from conftest import ZERO64, record_data
from kblam.decisions import (SUBJECT_FIELDS, canonical_json, decision_issues, independence_problem,
                             needs_independence, subject_digest, transition_problem)
from kblam.records import STATUSES, Record

KINDS = ("source-challenge", "claim-task", "checked-use")
CREATOR, PROPONENT, THIRD = "reviewer-a", "researcher-a", "reviewer-b"
FOLDERS = {"source-challenge": "challenges", "claim-task": "tasks", "checked-use": "uses"}
KIND_WORD = {"source-challenge": "challenge", "claim-task": "task", "checked-use": "use"}   # the words a refusal names its kind by

# SPEC §5.2.2 Subject digest, verbatim: the fields each kind's digest covers, in table order.
SPEC_SUBJECTS = {
    "source-challenge": ("id", "source", "proposition", "scope", "classification", "basis", "usable", "limits"),
    "claim-task": ("id", "kind", "finding", "claim_fingerprint", "base_file_sha256", "question", "method",
           "outcomes", "controls", "stop", "expected_evidence", "proponent"),
    "checked-use": ("id", "challenge", "challenge_bind", "finding", "finding_fingerprint", "finding_file_sha256",
           "citation", "disposition", "reason", "proponent"),
}

# SPEC §5.2.2 "Independent means", verbatim: the fields `by` must differ from.
SPEC_SELF = {"source-challenge": ("creator",), "claim-task": ("creator", "proponent"), "checked-use": ("proponent",)}

# SPEC §5.2.2 Status changes, the three rows that belong to one kind.
SPEC_TABLE = {
    "source-challenge": {"open": ("confirmed", "rejected"), "confirmed": ("stale",), "rejected": ("stale",)},
    "claim-task": {"open": ("confirmed", "not_reproduced", "inconclusive"), "confirmed": ("stale",),
           "not_reproduced": ("stale",), "inconclusive": ("stale",)},
    "checked-use": {"open": ("approved", "withdrawn"), "approved": ("stale",), "withdrawn": ("stale",)},
}


def record(kind: str, **fields) -> Record:
    """A Record built directly from record_data, as records.parse_record would give it."""
    rec_id = fields.pop("rec_id", None) or f"{kind}-0001"
    data = record_data(kind, rec_id, **fields)
    return Record(path=f"research-review/{FOLDERS[kind]}/{rec_id}.yaml", id=rec_id, kind=kind, data=data,
                  raw=b"")


def stored(kind: str, statuses: list[str], *, by=THIRD, bind: str | None = None, **fields) -> Record:
    """A stored record that took `statuses` in order. `by` is one name or one per decision; `bind` is the
    subject digest each decision records (the record's own, unless a test breaks it)."""
    rec = record(kind, status=statuses[-1] if statuses else "open", **fields)
    digest = bind if bind is not None else subject_digest(kind, rec.data)
    names = [by] * len(statuses) if isinstance(by, str) else list(by)
    rec.data["decisions"] = [
        {"date": datetime.date(2026, 9, 28), "by": name, "status": status, "reason": f"decided {status}",
         "evidence": [], "bind": digest}
        for name, status in zip(names, statuses)]
    return rec


def messages(issues) -> list[str]:
    return [i.message for i in issues]


class _KeyLines:
    """The one method of ruamel's line container that Record.key_line uses."""

    def __init__(self, lines: dict[str, int]):
        self.lines = lines

    def key(self, key: str) -> tuple[int, int]:
        return (self.lines[key], 0)


def meta(**lines: int):
    holder = type("Meta", (), {})()
    holder.lc = _KeyLines(lines)
    return holder


# --- canonical JSON and the subject digest --------------------------------------------------------


def test_canonical_json_sorts_keys_and_keeps_utf8():
    assert canonical_json({"b": 1, "a": "café"}) == b'{"a":"caf\xc3\xa9","b":1}'
    assert canonical_json({"z": {"b": None, "a": [1, 2]}, "a": False}) == b'{"a":false,"z":{"a":[1,2],"b":null}}'


def test_canonical_json_does_not_crash_on_a_parsed_date():
    assert canonical_json({"created": datetime.date(2026, 9, 28)}) == b'{"created":"2026-09-28"}'


def test_subject_fields_are_the_spec_lists():
    assert SUBJECT_FIELDS == SPEC_SUBJECTS


@pytest.mark.parametrize("kind", KINDS)
def test_subject_digest_is_the_sha256_of_the_canonical_spec_subject(kind):
    data = record_data(kind, f"{kind}-0001")
    mapping = {field: data.get(field) for field in SPEC_SUBJECTS[kind]}
    text = json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = subject_digest(kind, data)
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


@pytest.mark.parametrize("kind", KINDS)
def test_every_subject_field_changes_the_digest(kind):
    digest = subject_digest(kind, record_data(kind, f"{kind}-0001"))
    for field in SPEC_SUBJECTS[kind]:
        changed = record_data(kind, f"{kind}-0001")
        changed[field] = "changed"                   # a claim task's subject fields include `kind`
        assert subject_digest(kind, changed) != digest, field


def test_the_digest_ignores_fields_outside_the_subject():
    before = subject_digest("source-challenge", record_data("source-challenge", "source-challenge-0001"))
    after = subject_digest("source-challenge", record_data("source-challenge", "source-challenge-0001", status="confirmed",
                                            creator="reviewer-c", linked_findings=["F-0001"],
                                            decisions=[{"status": "confirmed"}]))
    assert after == before


def test_a_missing_subject_field_hashes_as_null():
    data = record_data("source-challenge", "source-challenge-0001")
    without = {key: value for key, value in data.items() if key != "limits"}
    assert subject_digest("source-challenge", without) == subject_digest("source-challenge", {**without, "limits": None})


def test_a_use_challenge_bind_is_part_of_its_digest():
    base = subject_digest("checked-use", record_data("checked-use", "checked-use-0001"))
    rebound = subject_digest("checked-use", record_data("checked-use", "checked-use-0001", challenge_bind="a" * 64))
    assert rebound != base


# --- the status-change table ----------------------------------------------------------------------


def allowed(kind: str, old: str, new: str) -> bool:
    """The §5.2.2 table re-derived from the SPEC text, independent of the module's data."""
    if new == old:                                   # rebind keeps the status: a task's and a use's, never stale
        return kind in ("claim-task", "checked-use") and old != "stale"
    if old == "open":
        return new == "stale" or new in SPEC_TABLE[kind].get("open", ())
    if new == "open":                                # open, and a source challenge's confirmed, may reopen
        return not (kind == "source-challenge" and old == "confirmed")
    return new in SPEC_TABLE[kind].get(old, ())


def test_the_transition_table_holds_for_every_cell():
    checked = 0
    for kind in KINDS:
        for old in STATUSES[kind]:
            for new in STATUSES[kind]:
                checked += 1
                problem = transition_problem(kind, old, new)
                assert (problem is None) == allowed(kind, old, new), f"{kind} {old}->{new}: {problem}"
                assert needs_independence(kind, old, new) == (
                    new != "open" and not (old == "open" and new == "stale")), f"{kind} {old}->{new}"
    assert checked == 4 * 4 + 5 * 5 + 4 * 4


def test_the_allowed_cells_are_33():
    cells = {kind: sum(allowed(kind, old, new) for old in STATUSES[kind] for new in STATUSES[kind])
             for kind in KINDS}
    assert cells == {"source-challenge": 7, "claim-task": 15, "checked-use": 11}


def test_a_confirmed_challenge_is_never_reopened():
    assert transition_problem("source-challenge", "confirmed", "open") == (
        "a confirmed challenge is never reopened; retire it with --status stale and file a new challenge")


@pytest.mark.parametrize("kind, old, new, phrase", [
    ("source-challenge", "rejected", "confirmed", "cannot go from rejected to confirmed"),
    ("source-challenge", "confirmed", "rejected", "cannot go from confirmed to rejected"),
    ("source-challenge", "stale", "confirmed", "cannot go from stale to confirmed"),
    ("claim-task", "open", "approved", "cannot go from open to approved"),
    ("claim-task", "confirmed", "not_reproduced", "cannot go from confirmed to not_reproduced"),
    ("claim-task", "stale", "confirmed", "cannot go from stale to confirmed"),
    ("checked-use", "open", "confirmed", "cannot go from open to confirmed"),
    ("checked-use", "approved", "withdrawn", "cannot go from approved to withdrawn"),
])
def test_a_refused_cell_says_which_statuses_are_left(kind, old, new, phrase):
    problem = transition_problem(kind, old, new)
    assert phrase in problem
    assert problem.endswith("a decision can set") is False   # the hint lists them, so it is never empty


def test_a_refusal_names_the_kind_and_has_no_trailing_period():
    for kind in KINDS:
        for old in STATUSES[kind]:
            for new in STATUSES[kind]:
                problem = transition_problem(kind, old, new)
                if problem is None:
                    continue
                assert problem == problem.lower(), problem
                assert not problem.endswith("."), problem
                assert KIND_WORD[kind] in problem, problem


@pytest.mark.parametrize("kind", ("claim-task", "checked-use"))
def test_rebind_keeps_a_task_or_use_status(kind):
    for status in STATUSES[kind]:
        assert (transition_problem(kind, status, status) is None) == (status != "stale")


def test_a_challenge_has_no_rebind():
    for status in STATUSES["source-challenge"]:
        assert transition_problem("source-challenge", status, status) is not None


def test_a_retired_record_is_not_re_decided_to_stale():
    for kind in KINDS:
        problem = transition_problem(kind, "stale", "stale")
        assert "stays as audit data" in problem or "no rebind" in problem


def test_an_unknown_status_gets_a_refusal_that_names_no_transition():
    assert transition_problem("source-challenge", "bogus", "confirmed") == (
        "a challenge cannot go from bogus to confirmed: bogus is not a status a decision sets")


# --- independence ---------------------------------------------------------------------------------


@pytest.mark.parametrize("kind, actor, role", [
    ("source-challenge", CREATOR, "creator"),
    ("claim-task", CREATOR, "creator"),
    ("claim-task", PROPONENT, "proponent"),
    ("checked-use", PROPONENT, "proponent"),
])
def test_a_self_decision_names_the_role(kind, actor, role):
    data = record_data(kind, f"{kind}-0001")
    assert independence_problem(kind, data, actor) == (
        f"{actor} is {kind}-0001's {role}; a closing decision needs someone else")


@pytest.mark.parametrize("kind, actor", [("source-challenge", PROPONENT), ("source-challenge", THIRD), ("claim-task", THIRD), ("checked-use", CREATOR),
                                         ("checked-use", THIRD)])
def test_a_third_person_or_the_creator_of_a_use_is_independent(kind, actor):
    assert independence_problem(kind, record_data(kind, f"{kind}-0001"), actor) is None


def test_a_ct_creator_is_named_before_its_proponent():
    data = record_data("claim-task", "claim-task-0001", creator=PROPONENT)
    assert "is claim-task-0001's creator" in independence_problem("claim-task", data, PROPONENT)


def test_a_record_without_an_id_names_no_id():
    data = record_data("source-challenge", "source-challenge-0001")
    del data["id"]
    assert " is the record's creator; " in independence_problem("source-challenge", data, CREATOR)


@pytest.mark.parametrize("kind", KINDS)
def test_every_allowed_closing_cell_refuses_a_self_decision(kind):
    data = record_data(kind, f"{kind}-0001")
    closing = [(old, new) for old in STATUSES[kind] for new in STATUSES[kind]
               if transition_problem(kind, old, new) is None and needs_independence(kind, old, new)]
    assert closing, kind
    for old, new in closing:
        for actor in (CREATOR, PROPONENT):
            self_decision = actor in {data.get(role) for role in SPEC_SELF[kind]}
            assert (independence_problem(kind, data, actor) is not None) == self_decision, (kind, old, new,
                                                                                            actor)
        assert independence_problem(kind, data, THIRD) is None


# --- decision_issues ------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_an_open_record_with_no_decisions_is_clean(kind):
    assert decision_issues(record(kind)) == []


@pytest.mark.parametrize("kind, statuses", [
    ("source-challenge", ["confirmed"]),
    ("source-challenge", ["rejected"]),
    ("source-challenge", ["confirmed", "stale"]),
    ("claim-task", ["confirmed"]),
    ("claim-task", ["not_reproduced"]),
    ("claim-task", ["inconclusive"]),
    ("claim-task", ["confirmed", "stale"]),
    ("claim-task", ["confirmed", "confirmed"]),
    ("checked-use", ["approved"]),
    ("checked-use", ["withdrawn"]),
    ("checked-use", ["approved", "stale"]),
    ("checked-use", ["approved", "approved"]),
])
def test_a_legal_decision_sequence_is_clean(kind, statuses):
    assert decision_issues(stored(kind, statuses)) == []


def test_a_use_creator_may_approve_it():
    assert decision_issues(stored("checked-use", ["approved"], by=CREATOR)) == []


def test_the_status_must_be_the_last_decision_s():
    rec = stored("source-challenge", ["confirmed"])
    rec.data["status"] = "rejected"                  # status is not a subject field, so the bind still holds
    issues = decision_issues(rec)
    assert messages(issues) == ["status is rejected but the last decision set confirmed; a record's "
                                "status is its last decision's status"]


def test_a_status_with_no_decision_is_an_error():
    issues = decision_issues(record("source-challenge", status="confirmed"))
    assert messages(issues) == ["status is confirmed with no decisions; a record with no decisions is "
                                "open, so set status: open or append the decision that made it confirmed"]


def test_a_hand_edit_of_a_decided_record_is_an_error():
    rec = stored("source-challenge", ["confirmed"])
    rec.data["proposition"] = "a different proposition"
    issues = decision_issues(rec)
    assert len(issues) == 1
    assert "the last decision's bind is" in issues[0].message
    assert "changed by hand" in issues[0].message


def test_a_retired_record_keeps_its_bind():
    rec = stored("source-challenge", ["confirmed", "stale"])
    rec.data["proposition"] = "a different proposition"
    assert len(decision_issues(rec)) == 1


def test_an_earlier_bind_is_audit_data_and_is_not_checked():
    rec = stored("source-challenge", ["confirmed", "stale"])
    rec.data["decisions"][0]["bind"] = ZERO64
    assert decision_issues(rec) == []


def test_an_illegal_stored_sequence_names_the_decision():
    rec = stored("source-challenge", ["confirmed", "open"])
    issues = decision_issues(rec)
    assert len(issues) == 1
    assert issues[0].message.startswith("decisions[1]: ")
    assert "never reopened" in issues[0].message


def test_an_illegal_stored_status_change_names_the_decision():
    rec = stored("source-challenge", ["rejected", "confirmed"])
    issues = decision_issues(rec)
    assert len(issues) == 1
    assert issues[0].message.startswith("decisions[1]: a challenge cannot go from rejected to confirmed")


@pytest.mark.parametrize("kind, actor, role", [
    ("source-challenge", CREATOR, "creator"),
    ("claim-task", CREATOR, "creator"),
    ("claim-task", PROPONENT, "proponent"),
    ("checked-use", PROPONENT, "proponent"),
])
def test_a_stored_self_decision_is_an_error(kind, actor, role):
    status = "approved" if kind == "checked-use" else "confirmed"
    issues = decision_issues(stored(kind, [status], by=actor))
    assert messages(issues) == [f"decisions[0]: {actor} is {kind}-0001's {role}; a closing decision needs "
                                f"someone else"]


@pytest.mark.parametrize("kind, status, actor", [("claim-task", "confirmed", CREATOR),
                                                 ("claim-task", "confirmed", PROPONENT),
                                                 ("checked-use", "approved", PROPONENT)])
def test_a_stored_self_rebind_is_an_error(kind, status, actor):
    issues = decision_issues(stored(kind, [status, status], by=[THIRD, actor]))
    assert len(issues) == 1
    assert issues[0].message.startswith("decisions[1]: ") and f"is {kind}-0001's" in issues[0].message


def test_every_problem_is_reported_in_order():
    rec = stored("source-challenge", ["confirmed"], by=CREATOR)
    rec.data["decisions"][-1]["bind"] = ZERO64
    rec.data["status"] = "rejected"
    issues = decision_issues(rec)
    assert len(issues) == 3
    assert messages(issues) == [
        "decisions[0]: reviewer-a is source-challenge-0001's creator; a closing decision needs someone else",
        "status is rejected but the last decision set confirmed; a record's status is its last "
        "decision's status",
        f"the last decision's bind is {ZERO64} but the record's subject digest is "
        f"{subject_digest('source-challenge', rec.data)}; a decided record cannot be edited, so the record was changed "
        f"by hand",
    ]


def test_a_missing_decisions_key_behaves_as_an_empty_list():
    rec = record("source-challenge")
    del rec.data["decisions"]
    assert decision_issues(rec) == []
    rec.data["status"] = "confirmed"
    assert len(decision_issues(rec)) == 1


@pytest.mark.parametrize("decisions", ["not a list", None, 42, {"0": {}}])
def test_a_decisions_value_that_is_not_a_list_is_schema_issues_business(decisions):
    rec = record("source-challenge")
    rec.data["decisions"] = decisions
    assert decision_issues(rec) == []


@pytest.mark.parametrize("kind, data", [(None, record_data("source-challenge")), ("source-challenge", None), ("source-challenge", []),
                                        ("XX", {"status": "confirmed"}), ("source-challenge", "text")])
def test_an_unparsed_record_has_no_decision_issues(kind, data):
    rec = Record(path="research-review/challenges/source-challenge-0001.yaml", id="source-challenge-0001", kind=kind, data=data, raw=b"")
    assert decision_issues(rec) == []


def test_an_unreadable_decision_is_schema_issues_business():
    rec = record("source-challenge", status="confirmed")
    bind = subject_digest("source-challenge", rec.data)
    for entry in ({"by": THIRD, "bind": bind},                  # no status
                  {"status": 7, "by": THIRD, "bind": bind},
                  {"status": "confirmed", "bind": bind},        # no by
                  {"status": "confirmed", "by": 7, "bind": bind},
                  {"status": "confirmed", "by": THIRD},         # no bind
                  {"status": "confirmed", "by": THIRD, "bind": 7},
                  {"status": "confirmed", "by": CREATOR, "bind": bind},   # a readable self-decision
                  "a string", 42, None, ["a", "list"], {"status": "confirmed", "by": THIRD}):
        rec.data["decisions"] = [entry]
        expected = 0 if entry != {"status": "confirmed", "by": CREATOR, "bind": bind} else 1
        assert len(decision_issues(rec)) == expected, entry


def test_a_status_outside_the_vocabulary_is_schema_issues_business():
    rec = record("source-challenge", status="confirmedd")
    rec.data["decisions"] = [{"status": "confirmedd", "by": CREATOR, "bind": ZERO64}]
    assert decision_issues(rec) == []
    rec.data["status"] = "confirmed"
    rec.data["decisions"] = [{"status": "confirmedd", "by": CREATOR,
                              "bind": subject_digest("source-challenge", rec.data)}]
    assert decision_issues(rec) == []


def test_a_decision_issue_carries_the_k13_fields():
    rec = stored("source-challenge", ["confirmed"])
    rec.data["status"] = "rejected"
    rec.meta = meta(status=5, decisions=10)
    issue, = decision_issues(rec)
    assert (issue.code, issue.level, issue.owner, issue.path) == ("K13", "error", "source-challenge-0001", rec.path)
    assert issue.line == 6                           # key_line is 1-based
    assert issue.is_error


def test_a_decision_issue_points_at_the_decisions_key():
    rec = stored("source-challenge", ["confirmed", "open"])
    rec.meta = meta(status=5, decisions=10)
    issue, = decision_issues(rec)
    assert issue.line == 11


def test_without_line_metadata_the_line_is_zero():
    rec = stored("source-challenge", ["confirmed"])
    rec.data["status"] = "rejected"
    assert [i.line for i in decision_issues(rec)] == [0]
