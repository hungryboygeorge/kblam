"""M6.10 `kblam items` (SPEC §7, §6.4, §10.7): the open items, the rejected items whose finding went in
reworded (with the `closed_fp` put records), and the per-verdict counts behind recalibration. Jev is the fake
transport from test_check; nothing here touches the network."""

from __future__ import annotations

import pytest

from kblam import items
from kblam.finding import fingerprint
from kblam.review import ReviewItem, load_items, save_items
from kblam.store import edit_finding
from kblam.view import load_view

from test_check import E1, N, THRESHOLDS, jkb, run, set_config, stage  # noqa: F401 (jkb is a fixture)

CLAIM_A = "The two sensor curve types agree to about 0.1%, so they are not two analog gains."
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."


def current_fp(kb, finding_id: str) -> str:
    return fingerprint(next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id), "/")


def item(n: int, kind: str, verdict: str | None, *, new_id: str = "F-0101", new_fp: str = "00000000000a",
         existing_id: str | None = "F-0100", existing_fp: str | None = "00000000000b", status: str = "closed",
         close_reason: str | None = None, closed_fp: str | None = None) -> ReviewItem:
    closed = status == "closed"
    return ReviewItem(f"R-{n:08x}", kind, status, new_id, new_fp, existing_id, existing_fp, verdict,
                      message="m", created="2026-09-25T00:00:00Z",
                      closed="2026-09-26T00:00:00Z" if closed else None,
                      close_reason=(close_reason or "F-0101 changed") if closed else None, closed_fp=closed_fp)


# --- the plain listing ---------------------------------------------------------------------------


def test_items_lists_every_open_item_reconciled_against_the_tree(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    fp1, fp2 = current_fp(kb, "F-0001"), current_fp(kb, "F-0002")
    listed = [
        item(1, "review", "restates_and_extends", new_id="F-0002", new_fp=fp2, existing_id="F-0001",
             existing_fp=fp1, status="open"),
        item(2, "rejected", "same_fact", new_id="F-0003", new_fp="00000000000c", existing_id="F-0001",
             existing_fp=fp1, status="open"),  # its finding is staged: never in the tree, still open
        ReviewItem("U-00000003", "unchecked", "open", "F-0001", fp1, message="Jev could not answer 1 question(s)",
                   created="t"),
    ]
    stale = item(4, "review", "same_fact", new_id="F-0002", new_fp=fp2, existing_id="F-0001",
                 existing_fp="00000000000d", status="open")  # F-0001 changed since: reconciled away
    save_items(kb.cfg, [*listed, stale, item(5, "review", "same_fact")])
    raw = (kb.cfg.state_dir / "review.jsonl").read_bytes()
    capsys.readouterr()

    assert run(kb, "items") == 0
    assert capsys.readouterr().out.splitlines() == [i.describe() for i in listed] + [
        "kblam items: 3 open item(s) (1 review, 1 rejected, 1 unchecked); open review and unchecked items fail "
        "kblam validate, rejected ones do not"]
    assert (kb.cfg.state_dir / "review.jsonl").read_bytes() == raw  # read-only


def test_items_with_nothing_open_says_so(kb, capsys):
    assert run(kb, "items") == 0
    assert capsys.readouterr().out == "kblam items: no open review, rejected or unchecked items\n"


# --- --reworded ---------------------------------------------------------------------------------


def test_put_records_the_fingerprint_that_went_in_and_items_reworded_lists_it(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", N))) == 4
    rejected, = load_items(jkb.cfg)

    reworded = "Motor speed stops drifting after the drive has warmed for a minute and a half."
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", reworded))) == 0
    closed, = load_items(jkb.cfg)
    went_in = current_fp(jkb, "F-0002")
    assert (closed.id, closed.close_reason, closed.closed_fp) == (rejected.id, "F-0002 was put", went_in)
    assert went_in != rejected.new_fp
    capsys.readouterr()

    assert run(jkb, "items", "--reworded") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("kblam items --reworded: 1 rejected item(s) whose finding later went in at another "
                             "fingerprint while the other side of the pair stayed as it was. Each is either the "
                             "correction the reject asked for or rewording to pass the check")
    assert out[1] == (f"  {rejected.id} same_fact F-0002 vs F-0001: rejected at {rejected.new_fp}, went in at "
                      f"{went_in} ({closed.closed}); F-0001 is unchanged at {rejected.existing_fp}")
    assert "kblam rm <id> --merged-into <that finding>" in out[2]

    staged = edit_finding(jkb.cfg, "F-0001")  # the other side changes: no longer a rewording to look at
    staged.write_text(staged.read_text(encoding="utf-8").replace("90 seconds", "95 seconds"),
                      encoding="utf-8", newline="\n")
    assert run(jkb, "put", str(staged)) == 0
    capsys.readouterr()
    assert run(jkb, "items", "--reworded") == 0
    assert capsys.readouterr().out == ("kblam items --reworded: no rejected item's finding went in at another "
                                       "fingerprint while the other side of the pair stayed as it was\n")


def test_items_reworded_takes_only_rejected_items_a_put_closed_at_another_fingerprint(kb):
    kb.add("F-0100", "base", CLAIM_A)
    base = current_fp(kb, "F-0100")
    listed = [item(1, "rejected", "same_fact", existing_fp=base, closed_fp="00000000000e"),
              item(2, "rejected", "revision", existing_id=None, existing_fp=None, closed_fp="00000000000e")]
    save_items(kb.cfg, [
        *listed,
        item(3, "rejected", "same_fact", existing_fp=base, closed_fp="00000000000a"),  # went in unchanged
        item(4, "rejected", "same_fact", existing_fp=base, close_reason="distinct: other phase"),  # no closed_fp
        item(5, "rejected", "same_fact", existing_fp="00000000000f", closed_fp="00000000000e"),  # F-0100 changed
        item(6, "rejected", "same_fact", existing_id="F-0200", existing_fp=base, closed_fp="00000000000e"),  # gone
        item(7, "rejected", "same_fact", existing_fp=base, status="open"),
        item(8, "review", "same_fact", existing_fp=base, closed_fp="00000000000e"),
    ])
    assert [i.id for i in items.reworded(kb.cfg)] == [i.id for i in listed]


# --- --stats (SPEC §10.7) -----------------------------------------------------------------------


def closed_items(start: int, count: int, distinct: int, kind: str, verdict: str) -> list[ReviewItem]:
    """`count` closed items, the first `distinct` of them closed by kblam resolve --distinct."""
    return [item(start + n, kind, verdict,
                 close_reason="distinct: they differ" if n < distinct else "F-0101 changed")
            for n in range(count)]


def test_items_stats_counts_each_verdict_and_applies_the_recalibration_rule(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    fp1 = current_fp(jkb, "F-0001")
    save_items(jkb.cfg, [
        *closed_items(0, 20, 3, "rejected", "same_fact"),          # 15% > 10%: due
        *closed_items(100, 5, 5, "review", "same_fact"),           # a reject verdict is judged on rejected items
        *closed_items(200, 20, 2, "rejected", "cannot_both_be_true"),  # 10% is not above 10%
        *closed_items(300, 19, 19, "rejected", "revision"),        # 19 closed: too few to judge
        *closed_items(400, 19, 11, "review", "restates_and_extends"),
        item(499, "review", "restates_and_extends", new_id="F-0001", new_fp="00000000000c", existing_id=None,
             existing_fp=None, status="open"),                     # F-0001 changed: reconciled, closed otherwise
        *closed_items(500, 30, 15, "review", "low_confidence"),    # 50% is not above 50%
        *closed_items(600, 25, 25, "review", "quantity_conflict"),  # code, not Jev: not counted
        ReviewItem("U-00000001", "unchecked", "closed", "F-0001", fp1, created="t", closed="t",
                   close_reason="checked"),
    ])
    rows = {row.verdict: row for row in items.stats(jkb.cfg)}
    assert list(rows) == ["same_fact", "cannot_both_be_true", "revision", "restates_and_extends", "low_confidence"]
    counts = {v: (r.mode, r.rejected_closed, r.rejected_distinct, r.review_closed, r.review_distinct, r.due)
              for v, r in rows.items()}
    assert counts == {
        "same_fact": ("reject", 20, 3, 5, 5, True),
        "cannot_both_be_true": ("reject", 20, 2, 0, 0, False),
        "revision": ("reject", 19, 19, 0, 0, False),
        "restates_and_extends": ("review", 0, 0, 20, 11, True),
        "low_confidence": ("review", 0, 0, 30, 15, False),
    }
    capsys.readouterr()

    assert run(jkb, "items", "--stats") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == ("  same_fact (reject): rejected items 20 closed (3 distinct, 17 otherwise), review items 5 "
                      "closed (5 distinct, 0 otherwise); recalibration due: 3 of 20 closed rejected items distinct "
                      "(15%), above the 10% a reject verdict allows")
    assert out[2].endswith("; keeps its mode: 2 of 20 closed rejected items distinct (10%), within the 10% a "
                           "reject verdict allows")
    assert out[3].endswith("; keeps its mode: 19 of the 20 closed rejected items needed to judge it")
    assert out[4].endswith("; recalibration due: 11 of 20 closed review items distinct (55%), above the 50% a "
                           "review verdict allows")
    assert out[5].endswith("; keeps its mode: 15 of 30 closed review items distinct (50%), within the 50% a "
                           "review verdict allows")
    assert out[6] == ("kblam items --stats: recalibrate same_fact, restates_and_extends (SPEC §10.3-§10.5, on "
                      "pairs that include long claims); until then each keeps its mode")
    assert len(out) == 7 and "quantity_conflict" not in "\n".join(out)


def test_items_stats_leaves_a_disabled_verdict_unjudged(jkb, capsys):
    set_config(jkb, THRESHOLDS.replace("low_confidence_review = 0.49\n", ""))
    save_items(jkb.cfg, closed_items(0, 25, 25, "review", "low_confidence"))
    row = items.stats(jkb.cfg)[-1]
    assert (row.verdict, row.mode, row.review_closed, row.counted, row.due) == ("low_confidence", None, 25, None,
                                                                                False)
    assert run(jkb, "items", "--stats") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[5] == ("  low_confidence (disabled): rejected items 0 closed (0 distinct, 0 otherwise), review items "
                      "25 closed (25 distinct, 0 otherwise); disabled in [jev.thresholds], so no bar applies")
    assert out[6] == "kblam items --stats: no verdict is due for recalibration"


def test_items_stats_prints_a_share_that_does_not_round_onto_the_bar(jkb, capsys):
    save_items(jkb.cfg, closed_items(0, 48, 5, "rejected", "same_fact"))
    assert run(jkb, "items", "--stats") == 0
    assert capsys.readouterr().out.splitlines()[1].endswith(
        "; recalibration due: 5 of 48 closed rejected items distinct (10.4%), above the 10% a reject verdict allows")


@pytest.mark.parametrize("closed, distinct, mode, due", [
    (20, 2, "reject", False), (20, 3, "reject", True), (19, 19, "reject", False), (40, 4, "reject", False),
    (41, 5, "reject", True), (20, 10, "review", False), (20, 11, "review", True), (100, 51, "review", True),
])
def test_recalibration_is_due_only_past_the_bar_after_twenty_closed_items(closed, distinct, mode, due):
    kind = {"reject": ("rejected", closed, distinct, 0, 0), "review": ("review", 0, 0, closed, distinct)}[mode]
    row = items.VerdictStats("same_fact", mode, review_closed=kind[3], review_distinct=kind[4],
                             rejected_closed=kind[1], rejected_distinct=kind[2])
    assert row.counted == (kind[0], closed, distinct) and row.due is due
