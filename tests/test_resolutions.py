"""M6.10 committed resolutions (SPEC §6.4 "Resolutions", §3, §6.3): `kblam resolve` appends to
kblam.resolutions.jsonl on the sides' state hashes, later checks (on any clone) suppress what a
resolution covers and what a pre-M6.10 row of pairs.sqlite covers, a resolution survives an edit Jev
does not see and lapses with the claim or the scope, a quantity_conflict is never suppressed or
resolved, a damaged file fails loudly, and a `revision` item's messages. Jev is the fake of
test_check.py; nothing here touches the network."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from kblam import cli, jev_prompts, resolutions
from kblam.config import RESOLUTIONS_NAME, load_config
from kblam.finding import parse_finding
from kblam.jev import PairCache, Side
from kblam.review import ReviewItem, load_items, save_items
from kblam.store import edit_finding
from kblam.view import load_view

from test_check import E1, E2, N, do_put, jkb, kinds, open_ids, quantity, run, stage  # noqa: F401 (jkb is a fixture)

REASON = "the MX-100 figure is the other warm-up phase"


def state(claim: str, scope: list[str] | None = None) -> str:
    """The state hash of a side as the fixtures write it (scope [MX-200] unless given)."""
    return jev_prompts.state_hash(jev_prompts.side_state(claim, scope or ["MX-200"]))


def lines(kb) -> list[dict]:
    return [json.loads(line) for line in (kb.root / RESOLUTIONS_NAME).read_text(encoding="utf-8").splitlines()]


def utc_days() -> set[str]:
    return {datetime.now(timezone.utc).strftime("%Y-%m-%d")}


def distinct_rows(kb) -> int:
    with closing(sqlite3.connect(kb.root / ".kblam" / "pairs.sqlite")) as conn:
        return conn.execute("SELECT COUNT(*) FROM distinct_pairs").fetchone()[0]


def fingerprint_of(kb, finding_id: str) -> str:
    return Side.of(next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)).fingerprint


def restated(kb) -> str:
    """F-0001 in the tree and F-0002 put over it as a restatement: returns the open review item's ID."""
    kb.add("F-0001", "motor", E1)
    kb.fake.relations[(E1, N)] = ("restates_and_extends", 0.60, 0.70)
    assert run(kb, "put", str(stage(kb, "F-0002", "drift", N))) == 0
    item_id, = open_ids(kb)
    return item_id


def rewrite(kb, finding_id: str, old: str, new: str):
    """`kblam edit` a finding, replace `old` by `new` in the staged copy, and put it."""
    staged = edit_finding(kb.cfg, finding_id)
    text = staged.read_text(encoding="utf-8")
    assert old in text
    staged.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    return do_put(kb, staged)


# --- what resolve records ------------------------------------------------------------------------


def test_resolving_a_pair_appends_a_distinct_resolution_on_both_state_hashes(jkb, capsys):
    item_id = restated(jkb)
    item, = load_items(jkb.cfg)
    assert (item.new_state, item.existing_state) == (state(N), state(E1))  # the item carries them
    days = utc_days()
    capsys.readouterr()

    assert run(jkb, "resolve", item_id, "--distinct", f"  {REASON}\n") == 0
    out = capsys.readouterr().out
    assert f"kblam resolve: closed {item_id} (restates_and_extends F-0002 vs F-0001): distinct: {REASON}" in out
    assert "recorded the resolution in kblam.resolutions.jsonl (SPEC §6.4); commit it" in out
    text = (jkb.root / RESOLUTIONS_NAME).read_text(encoding="utf-8")
    record = json.loads(text)
    assert text == json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"  # one line, keys sorted
    assert record["sides"] == [["F-0001", state(E1)], ["F-0002", state(N)]]  # sorted
    assert (record["kind"], record["reason"]) == ("distinct", REASON)
    assert record["date"] in days | utc_days()
    assert distinct_rows(jkb) == 0  # pairs.sqlite no longer holds resolutions
    assert run(jkb, "validate") == 0


def test_resolving_a_revision_item_records_not_revision_on_its_one_side(jkb, capsys):
    jkb.fake.nouls[N] = 0.9
    staged = stage(jkb, "F-0001", "drift", N)
    assert run(jkb, "put", str(staged)) == 4
    item, = load_items(jkb.cfg)
    assert (item.kind, item.verdict, item.new_state, item.existing_state) == ("rejected", "revision", state(N), None)

    assert run(jkb, "resolve", item.id, "--distinct", "it gives the warm-up time as measured") == 0
    record, = lines(jkb)
    assert (record["kind"], record["sides"]) == ("not_revision", [["F-0001", state(N)]])
    capsys.readouterr()
    assert run(jkb, "put", str(staged)) == 0  # the unchanged staged finding now goes in
    assert "not raised: revision F-0001" in capsys.readouterr().out


def test_resolve_appends_and_never_rewrites_an_earlier_line(jkb):
    earlier = resolutions.resolution(resolutions.DISTINCT, [("F-0009", "0" * 12), ("F-0008", "1" * 12)], "earlier")
    assert earlier.sides == (("F-0008", "1" * 12), ("F-0009", "0" * 12))
    old = earlier.line().rstrip("\n").encode("utf-8")  # as a merge might leave it: no final newline
    jkb.write(RESOLUTIONS_NAME, old)
    item_id = restated(jkb)
    assert run(jkb, "resolve", item_id, "--distinct", REASON) == 0
    data = (jkb.root / RESOLUTIONS_NAME).read_bytes()
    assert data.startswith(old + b"\n") and data.count(b"\n") == 2
    assert [r["reason"] for r in lines(jkb)] == ["earlier", REASON]


def test_a_crlf_checkout_and_a_line_separator_in_a_reason_still_parse(jkb):
    one = resolutions.resolution(resolutions.NOT_REVISION, [("F-0001", "a" * 12)], "first\u2028line")
    two = resolutions.resolution(resolutions.DISTINCT, [("F-0001", "a" * 12), ("F-0002", "b" * 12)], "second")
    jkb.write(RESOLUTIONS_NAME, (one.line() + two.line()).replace("\n", "\r\n"))
    assert resolutions.load(jkb.cfg) == [one, two]


# --- what a resolution suppresses ------------------------------------------------------------------


def test_a_resolution_suppresses_the_pair_in_either_direction(jkb, capsys):
    item_id = restated(jkb)
    assert run(jkb, "resolve", item_id, "--distinct", REASON) == 0
    capsys.readouterr()
    assert run(jkb, "check", "F-0002") == 0
    assert "resolved as distinct, not raised: restates_and_extends F-0002 vs F-0001" in capsys.readouterr().out
    jkb.fake.relations[(N, E1)] = ("same_fact", 0.93, 0.91)  # asked the other way round: the same pair
    assert run(jkb, "check", "F-0001") == 0
    assert "not raised: same_fact F-0001 vs F-0002" in capsys.readouterr().out
    assert open_ids(jkb) == []


@pytest.mark.parametrize("old, new", [
    ("evidence: [evidence/2026-09-22-ratio/]", "evidence: [evidence/2026-09-22-ratio/log.txt]"),
    ("verified: 2026-09-22\n", "verified: 2026-09-22\n" + quantity("warm-up", 90, "s")),
    ("label: observed", "label: inferred"),
    ("title: Title of F-0002", "title: Motor drift after warm-up"),
])
def test_a_resolution_survives_an_edit_jev_does_not_see(jkb, old, new):
    item_id = restated(jkb)
    fingerprint = fingerprint_of(jkb, "F-0002")
    assert run(jkb, "resolve", item_id, "--distinct", REASON) == 0
    result = rewrite(jkb, "F-0002", old, new)
    assert result.ok and result.review == [] and open_ids(jkb) == []
    assert [v.verdict for v in result.check.suppressed] == ["restates_and_extends"]
    if "title" not in old and "label" not in old:  # fingerprint v1 covers neither
        assert result.check.fingerprint != fingerprint  # so it is the state hash that kept the resolution


@pytest.mark.parametrize("old, new", [
    ("warm-up has passed.", "warm-up has passed at 4000 rpm."),
    ("scope: [MX-200]", "scope: [MX-200, MX-100]"),
])
def test_a_resolution_lapses_when_the_claim_or_the_scope_changes(jkb, old, new):
    item_id = restated(jkb)
    assert run(jkb, "resolve", item_id, "--distinct", REASON) == 0
    jkb.fake.relations[(E1, N.replace(old, new))] = ("restates_and_extends", 0.60, 0.70)
    result = rewrite(jkb, "F-0002", old, new)
    assert result.ok and result.check.suppressed == []
    assert [(i.verdict, i.new_id, i.existing_id) for i in result.review] == [
        ("restates_and_extends", "F-0002", "F-0001")]


def test_a_not_revision_resolution_survives_an_evidence_edit_and_lapses_with_the_claim(jkb):
    jkb.fake.nouls[N] = 0.9
    staged = stage(jkb, "F-0001", "drift", N)
    assert do_put(jkb, staged).rejected
    item, = load_items(jkb.cfg)
    assert run(jkb, "resolve", item.id, "--distinct", "it gives the warm-up time as measured") == 0
    assert do_put(jkb, staged).ok
    assert rewrite(jkb, "F-0001", "evidence: [evidence/2026-09-22-ratio/]",
                   "evidence: [evidence/2026-09-22-ratio/log.txt]").ok
    corrected = N.replace("has passed.", "has passed, not the 60 seconds stated before.")
    jkb.fake.nouls[corrected] = 0.95
    result = rewrite(jkb, "F-0001", "warm-up has passed.", "warm-up has passed, not the 60 seconds stated before.")
    assert kinds(result) == [("revision", "reject")]


def test_rows_pairs_sqlite_held_before_m610_still_suppress(jkb, capsys):
    """Resolutions recorded before M6.10 stay in pairs.sqlite, keyed by fingerprints, until kblam
    upgrade moves them; they still suppress what they cover."""
    jkb.add("F-0001", "motor", E1)
    jkb.add("F-0002", "drift", N)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    jkb.fake.nouls[N] = 0.9
    cache = PairCache(jkb.root / ".kblam" / "pairs.sqlite")
    fp1, fp2 = fingerprint_of(jkb, "F-0001"), fingerprint_of(jkb, "F-0002")
    cache.mark_distinct(("F-0001", fp1), ("F-0002", fp2), "legacy: distinct warm-up phases")
    cache.mark_distinct(("F-0002", fp2), None, "legacy: a direct statement")  # a revision item's row
    assert run(jkb, "check", "F-0002") == 0
    out = capsys.readouterr().out
    assert "not raised: same_fact F-0002 vs F-0001" in out and "not raised: revision F-0002" in out
    assert open_ids(jkb) == [] and not (jkb.root / RESOLUTIONS_NAME).exists()


def test_a_committed_resolution_reaches_another_clone(jkb, tmp_path, capsys):
    item_id = restated(jkb)
    assert run(jkb, "resolve", item_id, "--distinct", REASON) == 0
    clone = tmp_path / "clone"
    shutil.copytree(jkb.root, clone, ignore=shutil.ignore_patterns(".kblam"))  # another machine: no .kblam/
    capsys.readouterr()
    assert cli.main(["--root", str(clone), "check", "F-0002"]) == 0
    assert "resolved as distinct, not raised: restates_and_extends F-0002 vs F-0001" in capsys.readouterr().out
    assert load_items(load_config(root=clone)) == []


# --- quantity conflicts: never suppressed, never resolved (§6.3) ---------------------------------------


@pytest.mark.parametrize("source", ["kblam.resolutions.jsonl", "pairs.sqlite"])
def test_no_resolution_suppresses_a_quantity_conflict(jkb, source):
    jkb.add("F-0001", "motor", E1, extra=quantity("warm-up", 90, "s"))
    staged = stage(jkb, "F-0002", "drift", N, extra=quantity("warm-up", 60, "s"))
    new = Side.of(parse_finding("findings/calibration/F-0002-drift.md", staged.read_bytes()))
    if source == "pairs.sqlite":
        PairCache(jkb.root / ".kblam" / "pairs.sqlite").mark_distinct(
            ("F-0001", fingerprint_of(jkb, "F-0001")), ("F-0002", new.fingerprint), REASON)
    else:
        resolutions.append(jkb.cfg, resolutions.resolution(
            resolutions.DISTINCT, [("F-0001", state(E1)), ("F-0002", new.state_hash)], REASON))
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)  # a verdict on the pair that the resolution covers
    result = do_put(jkb, staged)
    assert kinds(result) == [("quantity_conflict", "reject")]
    assert [v.verdict for v in result.check.suppressed] == ["same_fact"]


def conflicting(kb, relation=("unrelated", 0.9, 0.9)) -> dict[str, str]:
    """F-0001 and F-0002 in the tree giving `warm-up` different values; `kblam check F-0002` raises a
    quantity_conflict review item. Returns the open items' IDs by verdict."""
    kb.add("F-0001", "motor", E1, extra=quantity("warm-up", 90, "s"))
    kb.add("F-0002", "drift", N, extra=quantity("warm-up", 60, "s"))
    kb.fake.relations[(E1, N)] = relation
    assert run(kb, "check", "F-0002") == 1
    return {i.verdict: i.id for i in load_items(kb.cfg) if i.open}


def test_resolve_refuses_a_quantity_conflict(jkb, capsys):
    item_id = conflicting(jkb)["quantity_conflict"]
    capsys.readouterr()
    assert run(jkb, "resolve", item_id, "--distinct", "they measure different things") == 1
    err = capsys.readouterr().err
    assert f"{item_id} is a quantity_conflict, which code found and no resolution covers (SPEC §6.3)" in err
    assert "If the two findings measure different things, rename the quantity in one of them" in err
    assert "otherwise edit a finding so they give one value (kblam edit F-0001 or kblam edit F-0002)" in err
    assert open_ids(jkb) == [item_id] and not (jkb.root / RESOLUTIONS_NAME).exists()


def test_resolving_a_pair_leaves_its_quantity_conflict_open(jkb, capsys):
    items = conflicting(jkb, relation=("same_fact", 0.93, 0.91))
    assert sorted(items) == ["quantity_conflict", "same_fact"]
    assert run(jkb, "resolve", items["same_fact"], "--distinct", REASON) == 0
    assert open_ids(jkb) == [items["quantity_conflict"]] and run(jkb, "validate") == 1
    capsys.readouterr()
    assert run(jkb, "check", "F-0002") == 1
    out = capsys.readouterr().out
    assert "not raised: same_fact F-0002 vs F-0001" in out and "review " + items["quantity_conflict"] in out


# --- a damaged file fails loudly -------------------------------------------------------------------

GOOD = resolutions.resolution(resolutions.DISTINCT, [("F-0001", "a" * 12), ("F-0002", "b" * 12)], "ok").line()


def resolution_line(**changes) -> str:
    base = {"sides": [["F-0001", "a" * 12], ["F-0002", "b" * 12]], "kind": "distinct", "reason": "why",
            "date": "2026-09-26"}
    base.update(changes)
    return json.dumps({k: v for k, v in base.items() if v is not None})


@pytest.mark.parametrize("line, problem", [
    ("{not json", "not JSON"),
    ("[]", "not a JSON object with exactly the keys date, kind, reason, sides"),
    (resolution_line(extra=1), "not a JSON object with exactly the keys"),
    (resolution_line(reason=None), "not a JSON object with exactly the keys"),
    (resolution_line(kind="same"), "kind must be distinct or not_revision, got 'same'"),
    (resolution_line(sides=[["F-0001", "a" * 12]]),
     "sides must be 2 [finding ID, state hash] pair(s) for kind distinct"),
    (resolution_line(kind="not_revision"),
     "sides must be 1 [finding ID, state hash] pair(s) for kind not_revision"),
    (resolution_line(sides=[["F-0001", "a" * 8], ["F-0002", "b" * 12]]), "sides must be 2"),  # a fingerprint
    (resolution_line(sides=[["F-1", "a" * 12], ["F-0002", "b" * 12]]), "sides must be 2"),
    (resolution_line(sides="F-0001"), "sides must be 2"),
    (resolution_line(reason=" "), "reason must be a non-empty string"),
    (resolution_line(date="2026-9-26"), "date must be YYYY-MM-DD, got '2026-9-26'"),
    (resolution_line(date="2026-02-30"), "date '2026-02-30' is not a calendar date"),
])
def test_a_line_that_is_not_a_resolution_is_an_error_naming_the_file_and_line(jkb, line, problem):
    jkb.write(RESOLUTIONS_NAME, GOOD + "\n" + line + "\n")  # a blank line is not an error
    with pytest.raises(resolutions.ResolutionError) as caught:
        resolutions.load(jkb.cfg)
    message = str(caught.value)
    assert message.startswith(f"{RESOLUTIONS_NAME}:3: ") and problem in message
    assert "restore the file from git" in message.lower()


def test_a_file_that_is_not_utf8_is_an_error_naming_the_line(jkb):
    jkb.write(RESOLUTIONS_NAME, GOOD.encode("utf-8") + b'{"reason": "\xff"}\n')
    with pytest.raises(resolutions.ResolutionError, match=f"^{RESOLUTIONS_NAME}:2: not UTF-8 text"):
        resolutions.load(jkb.cfg)


def test_a_damaged_file_stops_every_command_that_reads_it(jkb, capsys):
    item_id = restated(jkb)
    damaged = GOOD + "{not json\n"
    jkb.write(RESOLUTIONS_NAME, damaged)
    capsys.readouterr()
    for args in (["check", "F-0002"], ["audit"], ["put", str(stage(jkb, "F-0003", "tray", E2))],
                 ["resolve", item_id, "--distinct", REASON]):
        assert run(jkb, *args) == 2, args
        assert f"{RESOLUTIONS_NAME}:2: not JSON" in capsys.readouterr().err
    assert (jkb.root / RESOLUTIONS_NAME).read_text(encoding="utf-8") == damaged  # resolve appended nothing
    assert open_ids(jkb) == [item_id]


# --- the wording of a revision item (§6.4) ------------------------------------------------------------


def test_a_revision_items_messages_speak_of_a_direct_statement(jkb, capsys):
    jkb.fake.nouls[N] = 0.9
    assert run(jkb, "put", str(stage(jkb, "F-0001", "drift", N))) == 4
    out = capsys.readouterr().out
    item, = load_items(jkb.cfg)
    assert (f"rejected {item.id} revision F-0001 (noul 0.90): this reads as a correction; rewrite the original "
            f"finding instead. The put was refused: send {item.id} to the librarian, which resolves it only when "
            f"F-0001 states a fact directly rather than correcting an earlier claim "
            f"(kblam resolve {item.id} --distinct \"<reason>\")") in out
    assert "two findings" not in out

    assert run(jkb, "resolve", item.id, "--distinct", "  ") == 1
    assert ("--distinct needs a reason: say why F-0001 states a fact directly rather than correcting an earlier "
            "claim") in capsys.readouterr().err

    review = ReviewItem(**(asdict(item) | {"kind": "review"}))  # as kblam check records it for a finding in the tree
    assert ("Edit a finding, or if F-0001 states a fact directly rather than correcting an earlier claim: "
            f"kblam resolve {item.id} --distinct") in review.describe()


def test_a_pair_items_messages_keep_their_wording(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", N))) == 4
    item, = load_items(jkb.cfg)
    assert "which resolves it only when the two findings state distinct facts" in capsys.readouterr().out
    assert run(jkb, "resolve", item.id, "--distinct", "") == 1
    assert "--distinct needs a reason: say why the two findings state different facts" in capsys.readouterr().err
    review = ReviewItem(**(asdict(item) | {"kind": "review"}))
    assert "Edit a finding, or if they are distinct: kblam resolve" in review.describe()


# --- items recorded before M6.10 carry no state hashes -----------------------------------------------


def strip_states(kb) -> None:
    items = load_items(kb.cfg)
    for item in items:
        item.new_state = item.existing_state = None
    save_items(kb.cfg, items)


def test_an_older_review_item_is_resolved_with_the_state_hashes_in_the_tree(jkb):
    item_id = restated(jkb)
    strip_states(jkb)
    assert run(jkb, "resolve", item_id, "--distinct", REASON) == 0
    record, = lines(jkb)
    assert record["sides"] == [["F-0001", state(E1)], ["F-0002", state(N)]]


def test_an_older_rejected_item_takes_its_state_hashes_from_the_next_refused_put(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    staged = stage(jkb, "F-0002", "drift", N)
    assert run(jkb, "put", str(staged)) == 4
    item, = load_items(jkb.cfg)
    strip_states(jkb)
    capsys.readouterr()
    assert run(jkb, "resolve", item.id, "--distinct", REASON) == 1
    err = capsys.readouterr().err
    assert f"{item.id} was recorded by an older kblam" in err and "kblam put the staged F-0002 again" in err
    assert run(jkb, "put", str(staged)) == 4
    again, = load_items(jkb.cfg)
    assert (again.id, again.new_state, again.existing_state) == (item.id, state(N), state(E1))
    assert run(jkb, "resolve", item.id, "--distinct", REASON) == 0
    assert run(jkb, "put", str(staged)) == 0
