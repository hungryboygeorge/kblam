"""validate wiring of K13-K15 (SPEC §5.2.4), end to end through the real matching.finding_matches: findings
quote a nested source repository, and challenges and uses are records in the review root. Offline."""

from __future__ import annotations

from conftest import record_data
from kblam import k13, rules, sources
from kblam.review_index import generate_review_index
from kblam.view import load_view

from test_k14 import (CLAIM, LINE3, REVIEW, TRACE, add_finding, deciding, put, quoted, same_bytes_message, sc,
                      scene, use)


def by_code(issues, code: str) -> list:
    return [i for i in issues if i.code == code]


def scenario(kb, source_repo, *, approved: bool = False) -> None:
    """A finding that quotes the assertion of a confirmed challenge, and the review index that goes with it."""
    put(kb, "SC", sc(source_repo))
    add_finding(kb, "3", LINE3)
    if approved:
        put(kb, "CU", use(kb))
    kb.write(f"{REVIEW}/INDEX.md", generate_review_index(load_view(kb.cfg)))


# --- the three end-to-end cases -----------------------------------------------------------------


def test_an_affected_excerpt_without_a_use_is_one_k14_error(kb, source_repo):
    scenario(kb, source_repo)
    issues = kb.issues()
    assert [(i.level, i.owner, i.message) for i in by_code(issues, "K14")] == [
        ("error", "F-0001", same_bytes_message(source_repo))]
    assert by_code(issues, "K13") == []


def test_an_approved_use_bound_to_the_excerpt_makes_the_validation_clean(kb, source_repo):
    scenario(kb, source_repo, approved=True)
    view, reader = scene(kb)
    rec = next(r for r in view.records if r.id == "CU-0001")
    assert k13.use_current(view, reader, rec) is True
    assert by_code(kb.issues(), "K14") == []
    assert rules.errors(kb.issues()) == []


def test_editing_the_finding_breaks_the_binding_and_the_error_returns(kb, source_repo):
    scenario(kb, source_repo, approved=True)
    kb.add("F-0001", "ratio", CLAIM + " Edited.", body=quoted(f"{TRACE}:3", LINE3))
    kb.write(f"{REVIEW}/INDEX.md", generate_review_index(load_view(kb.cfg)))
    issues = kb.issues()
    bindings = [i for i in by_code(issues, "K13") if "kblam review rebind" in i.message]
    assert bindings and {(i.level, i.owner) for i in bindings} == {("warning", "CU-0001")}
    assert [(i.level, i.owner) for i in by_code(issues, "K14")] == [("error", "F-0001")]


# --- ordering, owners and the one reader --------------------------------------------------------


def test_the_result_is_sorted_deterministic_and_every_review_issue_has_an_owner(kb, source_repo):
    scenario(kb, source_repo)
    put(kb, "CT", deciding("CT", record_data("CT", "CT-0001", finding="F-0001",
                                             claim_fingerprint="0badf00d")))   # bound to nothing real
    kb.write(f"{REVIEW}/INDEX.md", generate_review_index(load_view(kb.cfg)))
    first, second = kb.issues(), kb.issues()
    assert first == second
    assert first == sorted(set(first), key=lambda i: (i.path, i.line, int(i.code[1:]), i.message))
    review = [i for i in first if i.code in ("K13", "K14", "K15")]
    assert {i.code for i in review} >= {"K14", "K15"}
    assert all(i.owner for i in review), [i for i in review if not i.owner]
    assert {i.owner for i in by_code(first, "K15")} == {"CT-0001"}


def test_one_source_reader_serves_the_whole_validation(kb, source_repo, monkeypatch):
    scenario(kb, source_repo, approved=True)
    made = []

    class Counting(sources.SourceReader):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(rules, "SourceReader", Counting)
    kb.issues()
    assert len(made) == 1
    assert made[0].reads and all(count == 1 for count in made[0].reads.values())


# --- a malformed registry -----------------------------------------------------------------------


def test_a_malformed_registry_is_one_k13_error_and_validate_still_runs(kb, source_repo):
    scenario(kb, source_repo)
    kb.write(".kblam/review-ids", "this is not json")
    issues = kb.issues()
    registry_issues = [i for i in by_code(issues, "K13") if i.path == ".kblam/review-ids"]
    assert [(i.line, i.level, i.owner) for i in registry_issues] == [(0, "error", "")]
    assert "cannot be read" in registry_issues[0].message
    assert len(by_code(issues, "K14")) == 1
