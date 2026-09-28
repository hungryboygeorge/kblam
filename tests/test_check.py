"""M5: candidate selection, quantity comparison, the §6.4 decision policy in put, and the review
workflow (review.jsonl, check, check --pending, audit, resolve, validate). Jev is a fake transport
answering by claim text; nothing here touches the network except the opt-in live test."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager

import httpx2
import pytest

from kblam import cli, jev_prompts, store
from kblam.check import Similarity, parse_policy, scopes_overlap, select_candidates, tokens
from kblam.config import ConfigError
from kblam.jev import JevClient
from kblam.review import load_items
from kblam.store import edit_finding, put
from kblam.treehash import read_tree_hash
from kblam.view import load_view

from conftest import DEFAULT_PROMPT_ID, KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, finding_text
from test_jev import JEV_TOML, KEY, SERVED

# The fixtures' prompt is the template's default (PROMPT_TOML), so its thresholds carry that id.
THRESHOLDS = """
[jev.thresholds]
served_model = "typesafe/jev-1.13-20260917"
prompt_id = "PROMPT_ID"
same_fact            = { p = 0.67, confidence = 0.59, mode = "reject" }
cannot_both_be_true  = { p = 0.59, confidence = 0.49, mode = "reject" }
restates_and_extends = { p = 0.47, confidence = 0.34, mode = "review" }
revision             = { noul = 0.75, mode = "reject" }
low_confidence_review = 0.49
""".replace("PROMPT_ID", DEFAULT_PROMPT_ID)

E1 = "The motor reaches steady output after 90 seconds of warm-up."
E2 = "The media tray reports its type through two contact pins read at load time."
N = "Motor speed stops drifting once a minute and a half of warm-up has passed."


class ScriptedJev:
    """Answers by claim text: `relations[(existing claim, new claim)] = (winner, p, confidence)` and
    `nouls[claim]`; anything else is `unrelated` at 0.9 / 0.9 and noul 0.05."""

    def __init__(self):
        self.relations: dict[tuple[str, str], tuple[str, float, float]] = {}
        self.nouls: dict[str, float] = {}
        self.served = SERVED
        self.fail = False
        self.requests: list[dict] = []
        self.on_request = None

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        if self.on_request:
            self.on_request(body)
        if self.fail:
            return httpx2.Response(401, json={"error": {"code": 401, "message": "User not found."}})
        state, answers = body["state"], {}
        for name, question in body["questions"].items():
            if question["type"] == "choice":
                pair = (state["existing"]["claim"], state["new"]["claim"])
                winner, p, confidence = self.relations.get(pair, ("unrelated", 0.9, 0.9))
                rest = (1 - p) / (len(jev_prompts.RELATION_OPTIONS) - 1)
                probabilities = {o: (p if o == winner else rest) for o in jev_prompts.RELATION_OPTIONS}
                answers[name] = {"type": "choice", "choice": winner, "confidence": confidence,
                                 "probabilities": probabilities}
            else:
                answers[name] = {"type": "noul", "noul": self.nouls.get(state["new"]["claim"], 0.05)}
        return httpx2.Response(200, json={"id": "gen-1", "provider": "TypeSafe", "model": self.served,
                                          "answers": answers, "usage": {"input_tokens": 700, "output_tokens": 8,
                                                                        "cost": 0.00003}})

    def relation_pairs(self) -> list[tuple[str, str]]:
        return [(b["state"]["existing"]["claim"], b["state"]["new"]["claim"])
                for b in self.requests if "existing" in b["state"]]

    def revision_claims(self) -> list[str]:
        return [b["state"]["new"]["claim"] for b in self.requests if "existing" not in b["state"]]


def set_config(kb, thresholds: str = THRESHOLDS, jev_extra: str = "") -> None:
    kb.write("kblam.toml", KBLAM_TOML + NO_EMBEDDINGS + JEV_TOML + jev_extra + thresholds + PROMPT_TOML)


@pytest.fixture
def jkb(kb, monkeypatch):
    set_config(kb)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    kb.fake = ScriptedJev()
    kb.factory = lambda cfg: JevClient(cfg, transport=httpx2.MockTransport(kb.fake), sleep=lambda s: None)
    monkeypatch.setattr(cli, "JevClient", kb.factory)
    return kb


def stage(kb, finding_id: str, slug: str, claim: str, **kw):
    return kb.write(f".kblam/staging/{finding_id}-{slug}.md", finding_text(finding_id, claim, **kw))


def run(kb, *args) -> int:
    return cli.main(["--root", str(kb.root), *args])


def do_put(kb, path):
    return put(kb.cfg, path, client_factory=kb.factory)


def kinds(result) -> list[tuple[str, str]]:
    return [(v.verdict, v.mode) for v in result.check.verdicts]


def open_ids(kb) -> list[str]:
    return [i.id for i in load_items(kb.cfg) if i.open]


# --- §6.4 firing rule ---------------------------------------------------------------------------


@pytest.mark.parametrize("winner, p, confidence, expected", [
    ("same_fact", 0.67, 0.59, ("same_fact", "reject")),
    ("same_fact", 0.66, 0.59, None),
    ("same_fact", 0.67, 0.58, None),
    ("cannot_both_be_true", 0.59, 0.49, ("cannot_both_be_true", "reject")),
    ("cannot_both_be_true", 0.58, 0.49, None),
    ("cannot_both_be_true", 0.59, 0.48, ("low_confidence", "review")),
    ("restates_and_extends", 0.47, 0.34, ("restates_and_extends", "review")),
    ("restates_and_extends", 0.46, 0.50, None),
    ("restates_and_extends", 0.47, 0.33, ("low_confidence", "review")),
    ("compatible_same_subject", 0.90, 0.95, None),
])
def test_relation_verdict_fires_at_its_boundary(jkb, winner, p, confidence, expected):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = (winner, p, confidence)
    result = do_put(jkb, stage(jkb, "F-0002", "drift", N))
    assert kinds(result) == ([expected] if expected else [])
    assert result.ok == (expected is None or expected[1] == "review")


@pytest.mark.parametrize("noul, fires", [(0.75, True), (0.74, False)])
def test_revision_fires_at_its_boundary(jkb, noul, fires):
    jkb.fake.nouls[N] = noul
    result = do_put(jkb, stage(jkb, "F-0001", "drift", N))
    assert kinds(result) == ([("revision", "reject")] if fires else [])


def test_low_confidence_band_on_and_off(jkb):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("compatible_same_subject", 0.6, 0.40)
    assert kinds(do_put(jkb, stage(jkb, "F-0002", "drift", N))) == [("low_confidence", "review")]
    assert "Jev could not place this against F-0001 (winner compatible_same_subject, confidence 0.40)" in \
           load_items(jkb.cfg)[0].message

    set_config(jkb, THRESHOLDS.replace("low_confidence_review = 0.49\n", ""))
    jkb.fake.relations[(E1, N + " At 4000 rpm.")] = ("compatible_same_subject", 0.6, 0.40)
    assert kinds(do_put(jkb, stage(jkb, "F-0003", "drift-rpm", N + " At 4000 rpm.", topic="motor"))) == []


# --- put: reject, review, unchecked -------------------------------------------------------------


def test_reject_reports_every_verdict_and_leaves_findings_unchanged(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.add("F-0002", "tray", E2)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    jkb.fake.relations[(E2, N)] = ("restates_and_extends", 0.60, 0.70)
    jkb.fake.nouls[N] = 0.9
    before, hash_before = jkb.snapshot(), read_tree_hash(jkb.cfg)
    staged = stage(jkb, "F-0003", "drift", N)

    assert run(jkb, "put", str(staged)) == cli.EXIT_REJECTED == 4
    out = capsys.readouterr().out
    # §6.4: each rejecting verdict is recorded as a rejected item and printed with its ID
    by_verdict = {i.verdict: i for i in load_items(jkb.cfg)}
    assert sorted(by_verdict) == ["revision", "same_fact"]  # the review-mode verdict records nothing
    assert all(i.kind == "rejected" and i.open and i.new_id == "F-0003" for i in by_verdict.values())
    same = by_verdict["same_fact"]
    revision = by_verdict["revision"]
    assert (same.new_id, same.existing_id) == ("F-0003", "F-0001") and revision.existing_id is None
    assert f"rejected {same.id} same_fact F-0003 vs F-0001 (p 0.93, confidence 0.91): F-0001 already states " \
           f"this; edit F-0001" in out
    assert f"rejected {revision.id} revision F-0003 (noul 0.90): this reads as a correction" in out
    assert "review restates_and_extends F-0003 vs F-0002 (p 0.60, confidence 0.70): this restates F-0002" in out
    assert "rejected F-0003 by the Jev check (2 reject verdict(s)); findings/ is unchanged" in out
    assert jkb.snapshot() == before and read_tree_hash(jkb.cfg) == hash_before and staged.exists()
    # relation once per candidate pair, revision once per finding
    pairs = jkb.fake.relation_pairs()
    assert len(pairs) == 2 and set(pairs) == {(E1, N), (E2, N)} and jkb.fake.revision_claims() == [N]


def test_review_item_fails_validate_until_resolved_and_distinct_is_not_raised_again(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("restates_and_extends", 0.60, 0.70)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", N))) == 0
    out = capsys.readouterr().out
    item_id, = open_ids(jkb)
    assert f"kblam put: review {item_id} restates_and_extends F-0002 vs F-0001" in out

    assert run(jkb, "validate") == 1
    out = capsys.readouterr().out
    assert f"review {item_id} restates_and_extends F-0002 vs F-0001 (p 0.60, confidence 0.70)" in out
    assert "1 open item(s) in .kblam/review.jsonl" in out

    assert run(jkb, "resolve", item_id, "--distinct", "different warm-up phases") == 0
    assert f"closed {item_id}" in capsys.readouterr().out
    assert run(jkb, "validate") == 0
    capsys.readouterr()

    assert run(jkb, "check", "F-0002") == 0  # same pair, same fingerprints: suppressed
    assert "resolved as distinct, not raised: restates_and_extends F-0002 vs F-0001" in capsys.readouterr().out
    staged = edit_finding(jkb.cfg, "F-0002")  # a body-only edit keeps the fingerprint
    staged.write_text(staged.read_text(encoding="utf-8") + "\nMore detail.\n", encoding="utf-8", newline="\n")
    assert do_put(jkb, staged).review == []
    assert open_ids(jkb) == []
    assert run(jkb, "resolve", item_id, "--distinct", "again") == 1
    assert "already closed (distinct: different warm-up phases)" in capsys.readouterr().err


def test_review_item_closes_when_a_fingerprint_changes(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("restates_and_extends", 0.60, 0.70)
    assert do_put(jkb, stage(jkb, "F-0002", "drift", N)).review
    staged = edit_finding(jkb.cfg, "F-0001")
    staged.write_text(staged.read_text(encoding="utf-8").replace("90 seconds", "95 seconds"),
                      encoding="utf-8", newline="\n")
    result = do_put(jkb, staged)  # re-checks the pair (F-0002 is now `existing`); nothing fires
    assert result.ok and result.review == []
    item, = load_items(jkb.cfg)
    assert (item.status, item.close_reason) == ("closed", "F-0001 changed")
    assert run(jkb, "validate") == 0


def test_jev_unavailable_is_unchecked_until_check_pending(jkb, monkeypatch, capsys):
    jkb.add("F-0001", "motor", E1)
    monkeypatch.delenv("OPENROUTER_API_KEY")
    result = do_put(jkb, stage(jkb, "F-0002", "drift", N))
    assert result.ok and (jkb.findings / "calibration" / "F-0002-drift.md").is_file()  # accepted, not passed
    assert result.unchecked is not None and "no Jev API key" in result.unchecked.message
    assert run(jkb, "validate") == 1
    assert f"unchecked {result.unchecked.id} F-0002" in capsys.readouterr().out

    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    jkb.fake.fail = True  # reachable, but refusing
    assert run(jkb, "check", "--pending") == 1
    assert "401" in capsys.readouterr().out and run(jkb, "validate") == 1

    jkb.fake.fail = False
    assert run(jkb, "check", "--pending") == 0
    assert run(jkb, "validate") == 0
    capsys.readouterr()
    assert run(jkb, "check", "--pending") == 0
    assert "no unchecked items" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["served", "prompt"])
def test_model_or_prompt_mismatch_turns_rejects_into_review(jkb, capsys, change):
    """Thresholds calibrated on another model, or on another prompt, reject nothing (SPEC §6.4)."""
    if change == "served":
        jkb.fake.served = "typesafe/jev-1.14-20261101"
    else:
        set_config(jkb, THRESHOLDS.replace(DEFAULT_PROMPT_ID, "0123456789ab"))
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", N))) == 0
    out = capsys.readouterr().out
    assert "WARNING: the [jev.thresholds] in kblam.toml were calibrated on typesafe/jev-1.13-20260917" in out
    assert "No Jev verdict of this check rejects" in out and "Re-calibrate (SPEC §10)" in out
    if change == "prompt":  # the warning names both ids: what the check used, and what it recorded
        assert f"prompt {DEFAULT_PROMPT_ID} (thresholds: 0123456789ab)" in out
    item, = load_items(jkb.cfg)
    assert (item.verdict, item.status) == ("same_fact", "open")


# --- §6.4 rejected items: a put the Jev check refused ---------------------------------------------


def rejected(kb) -> list:
    return [i for i in load_items(kb.cfg) if i.kind == "rejected"]


def test_a_refused_put_records_a_rejected_item_that_validate_ignores(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    staged = stage(jkb, "F-0002", "drift", N)

    assert run(jkb, "put", str(staged)) == 4
    out = capsys.readouterr().out
    item, = load_items(jkb.cfg)
    assert (item.kind, item.status, item.verdict) == ("rejected", "open", "same_fact")
    assert (item.new_id, item.existing_id) == ("F-0002", "F-0001")
    assert re.fullmatch(r"R-[0-9a-f]{8}", item.id)
    assert f"rejected {item.id} same_fact F-0002 vs F-0001 (p 0.93, confidence 0.91): {item.message}" in out
    assert staged.exists() and not (jkb.findings / "calibration" / "F-0002-drift.md").exists()

    assert run(jkb, "validate") == 0  # the finding is staged, so the item never fails validate
    assert "OK" in capsys.readouterr().out
    assert [i.id for i in rejected(jkb)] == [item.id]


def test_resolving_a_rejected_item_lets_the_unchanged_staged_file_through(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    staged = stage(jkb, "F-0002", "drift", N)
    assert run(jkb, "put", str(staged)) == 4
    item, = load_items(jkb.cfg)
    capsys.readouterr()

    assert run(jkb, "resolve", item.id, "--distinct", "the MX-100 uses the other warm-up phase") == 0
    closed, = load_items(jkb.cfg)
    assert (closed.status, closed.close_reason) == ("closed", "distinct: the MX-100 uses the other warm-up phase")

    assert run(jkb, "put", str(staged)) == 0
    out = capsys.readouterr().out
    assert "resolved as distinct, not raised: same_fact F-0002 vs F-0001" in out
    assert "kblam put: F-0002 -> findings/calibration/F-0002-drift.md" in out
    assert (jkb.findings / "calibration" / "F-0002-drift.md").is_file()
    assert run(jkb, "validate") == 0


def test_a_changed_staged_file_closes_the_item_and_a_fresh_reject_records_a_new_one(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", N))) == 4
    first, = load_items(jkb.cfg)

    changed = N + " At 4000 rpm."
    jkb.fake.relations[(E1, changed)] = ("same_fact", 0.94, 0.92)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", changed))) == 4

    items = {i.id: i for i in load_items(jkb.cfg)}
    assert len(items) == 2  # the item at the old fingerprint, and the one this put raised
    assert items[first.id].close_reason == "the staged finding changed"
    fresh, = [i for i in items.values() if i.open]
    assert fresh.id != first.id and fresh.new_fp != first.new_fp
    assert run(jkb, "validate") == 0


def test_a_successful_put_closes_the_rejected_item(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", N))) == 4
    item, = load_items(jkb.cfg)

    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", E2))) == 0  # the staged file was edited
    closed, = load_items(jkb.cfg)
    assert (closed.id, closed.status, closed.close_reason) == (item.id, "closed", "F-0002 was put")
    assert run(jkb, "validate") == 0


def test_a_quantity_reject_closes_the_stale_item_and_records_none(jkb, capsys):
    jkb.add("F-0001", "motor", E1, extra=quantity("curve ratio", 1.0017))
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", N))) == 4
    first, = load_items(jkb.cfg)

    changed = N + " At 4000 rpm."
    jkb.fake.relations[(E1, changed)] = ("unrelated", 0.9, 0.9)
    q = quantity("curve ratio", 1.0020)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", changed, extra=q))) == 4
    closed, = load_items(jkb.cfg)
    assert (closed.id, closed.verdict, closed.close_reason) == (first.id, "same_fact",
                                                                "the staged finding changed")


def test_resolving_a_closed_or_unknown_item_errors(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", N))) == 4
    item, = load_items(jkb.cfg)
    capsys.readouterr()

    assert run(jkb, "resolve", item.id, "--distinct", "one is the MX-100 figure") == 0
    assert run(jkb, "resolve", item.id, "--distinct", "again") == 1
    assert "already closed (distinct: one is the MX-100 figure)" in capsys.readouterr().err
    assert run(jkb, "resolve", "R-00000000", "--distinct", "x") == 1
    assert "no item R-00000000" in capsys.readouterr().err


# --- §6.1 candidates and scope ------------------------------------------------------------------


def test_scope_overlap_rules():
    assert scopes_overlap(["MX-100/MX-200"], ["MX-100"])
    assert scopes_overlap(["any"], ["MX-100"]) and scopes_overlap(["MX-200"], ["any"])
    assert not scopes_overlap(["MX-100"], ["MX-200"])


def test_different_scope_pairs_are_never_sent(jkb):
    jkb.add("F-0001", "motor", E1, scope="[MX-100]")
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    result = do_put(jkb, stage(jkb, "F-0002", "drift", N))
    assert result.ok and kinds(result) == []
    assert jkb.fake.relation_pairs() == [] and jkb.fake.revision_claims() == [N]
    log = [json.loads(line) for line in (jkb.root / ".kblam" / "checks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[-1]["different_scope"] == ["F-0001"] and log[-1]["candidates"] == []


NEW_EVIDENCE = "[evidence/new-run/]"  # shared by no fixture finding unless a test says so


def selection_for(kb, finding_id: str, max_candidates: int = 30, topic_bonus: float = 0.2,
                  link_bonus: float = 0.15):
    view = load_view(kb.cfg)
    finding = next(f for f in view.findings if f.file_id == finding_id)
    return select_candidates(view, finding, max_candidates, topic_bonus, link_bonus=link_bonus)


def scored_selection(kb, finding_id: str, scores: dict[str, float], *, link_bonus: float = 0.15,
                     topic_bonus: float = 0.0, embedding: bool = True):
    """select_candidates with the raw scores given by finding ID (absent means 0), so the bonus
    arithmetic is exact."""
    view = load_view(kb.cfg)
    finding = next(f for f in view.findings if f.file_id == finding_id)
    paths = {f.file_id: f.path for f in view.findings}
    similarity = Similarity("embed test" if embedding else "bm25", {paths[i]: s for i, s in scores.items()},
                            topic_bonus, embedding=embedding)
    return select_candidates(view, finding, 30, topic_bonus, similarity, link_bonus=link_bonus)


def ids(candidates) -> list[str]:
    return [c.finding_id for c in candidates]


ANCHOR = 'anchors: ["0x1A2B3C"]\n'


def test_tokens_keep_identifiers_whole():
    text = "The routine `foo_bar()` at 0x1A2B3C on the MX-200, see /usr/lib/x.so. Warm-up -- done..."
    assert tokens(text) == ["routine", "foo_bar", "0x1a2b3c", "mx-200", "see", "/usr/lib/x.so", "warm-up", "done"]


def test_dependencies_come_first_then_score_with_bonuses(jkb):
    jkb.add("F-0001", "similar", "The motor reaches steady output after 90 seconds.", title="Motor warm-up")
    jkb.add("F-0002", "anchor", "Claim about the controller.", title="Controller routine",
            extra='anchors: ["0x001a2b3c"]\n')
    jkb.add("F-0003", "dep", "The media tray reports its type.", title="Media tray")
    jkb.add("F-0004", "evidence", "The stepper drive moves in half steps.", title="Drive",
            evidence="[evidence/new-run/log.txt]")
    jkb.add("F-0005", "auto-hex", "The routine at 0x1a2b3c clears the buffer.", title="Buffer routine")
    jkb.add("F-0006", "weak", "The motor is a brushless unit.", title="Motor type")
    jkb.add("F-0010", "self", E1, title="Motor warm-up time", evidence=NEW_EVIDENCE,
            extra=ANCHOR + 'depends_on: {F-0003: null}\n')
    selection = selection_for(jkb, "F-0010")
    # the dependency, then every other finding by raw score plus topic and link bonuses: the linked
    # F-0002 and F-0004 share no token, so their link bonus alone ranks them behind the similar ones
    assert ids(selection.candidates) == ["F-0003", "F-0001", "F-0006", "F-0002", "F-0004"]
    link = 0.15 * selection.candidates[1].similar  # F-0001 has N's top raw score
    assert [c.reasons() for c in selection.candidates[:1] + selection.candidates[3:]] == [
        ["depends_on"],
        ["anchor 0x1A2B3C", f"similar {link:.4f} (link {link:.4f})"],
        ["evidence evidence/new-run", f"similar {link:.4f} (link {link:.4f})"]]
    assert re.fullmatch(r"similar \d+\.\d{4} \(bm25 \d+\.\d{4} \+ topic \d+\.\d{4}\)",
                        selection.candidates[1].reasons()[0])
    assert selection.candidates[1].score > selection.candidates[2].score > selection.candidates[3].score > 0
    # a hex address in the claim is not an anchor; with no shared token F-0005 is not a candidate
    assert "F-0005" not in ids(selection.candidates + selection.over_budget + selection.different_scope)


def test_a_dependency_ranks_first_despite_a_lower_score(jkb):
    jkb.add("F-0001", "dep", "First dependency.")
    jkb.add("F-0002", "close", "Close but unlinked.")
    jkb.add("F-0003", "linked-dep", "Second dependency.", extra=ANCHOR)
    jkb.add("F-0010", "self", E1, evidence=NEW_EVIDENCE,
            extra=ANCHOR + 'depends_on: {F-0001: null, F-0003: null}\n')
    selection = scored_selection(jkb, "F-0010", {"F-0001": 0.2, "F-0002": 0.9, "F-0003": 0.1})
    # among dependencies the link bonus counts too: F-0003's 0.1 + 0.15 x 0.9 passes F-0001's 0.2
    assert ids(selection.candidates) == ["F-0003", "F-0001", "F-0002"]
    assert [c.reasons() for c in selection.candidates] == [
        ["depends_on", "anchor 0x1A2B3C"], ["depends_on"], ["similar 0.9000 (embed)"]]


def test_a_link_overtakes_within_the_bonus_and_not_beyond_it(jkb):
    jkb.add("F-0001", "top", "Top.")
    jkb.add("F-0002", "beyond", "Beyond the bonus.")
    jkb.add("F-0003", "linked", "Linked.", extra=ANCHOR)
    jkb.add("F-0004", "within", "Within the bonus.")
    jkb.add("F-0010", "self", E1, evidence=NEW_EVIDENCE, extra=ANCHOR)
    scores = {"F-0001": 1.0, "F-0002": 0.8, "F-0003": 0.6, "F-0004": 0.7}
    selection = scored_selection(jkb, "F-0010", scores)
    # F-0003 gets 0.15 x 1.0: past F-0004 (0.1 ahead), not past F-0002 (0.2 ahead)
    assert ids(selection.candidates) == ["F-0001", "F-0002", "F-0003", "F-0004"]
    assert [c.reasons() for c in selection.candidates[2:]] == [
        ["anchor 0x1A2B3C", "similar 0.7500 (embed 0.6000 + link 0.1500)"], ["similar 0.7000 (embed)"]]
    # link_bonus = 0: pure score order, and the linked finding's reason is its raw score
    plain = scored_selection(jkb, "F-0010", scores, link_bonus=0.0)
    assert ids(plain.candidates) == ["F-0001", "F-0002", "F-0004", "F-0003"]
    assert plain.candidates[3].reasons() == ["anchor 0x1A2B3C", "similar 0.6000 (embed)"]


def test_several_links_earn_one_bonus(jkb):
    jkb.add("F-0001", "top", "Top.")
    jkb.add("F-0002", "many", "Many links.", evidence="[evidence/new-run/log.txt]",
            extra='anchors: ["0x1a2b3c", "Motor  Timer"]\n')
    jkb.add("F-0003", "one", "One link.", extra=ANCHOR)
    jkb.add("F-0010", "self", E1, evidence=NEW_EVIDENCE, extra='anchors: ["0x1A2B3C", "motor timer"]\n')
    selection = scored_selection(jkb, "F-0010", {"F-0001": 1.0, "F-0002": 0.5, "F-0003": 0.5})
    assert [c.link_bonus for c in selection.candidates] == [0.0, 0.15, 0.15]
    assert ids(selection.candidates) == ["F-0001", "F-0002", "F-0003"]  # equal scores: ties by ID
    assert selection.candidates[1].reasons() == [
        "anchor 0x1A2B3C", "anchor motor timer", "evidence evidence/new-run",
        "similar 0.6500 (embed 0.5000 + link 0.1500)"]


def test_a_linked_finding_with_no_shared_token_is_a_bm25_candidate(jkb):
    jkb.add("F-0001", "tray", E2, title="Media tray pins", extra=ANCHOR)
    jkb.add("F-0002", "motor", "The motor reaches steady output.", title="Motor")
    jkb.add("F-0003", "self", E1, title="Motor warm-up", evidence=NEW_EVIDENCE, extra=ANCHOR)
    selection = selection_for(jkb, "F-0003")
    assert ids(selection.candidates) == ["F-0002", "F-0001"]
    linked = selection.candidates[1]
    link = 0.15 * selection.candidates[0].similar
    assert linked.similar == 0.0 and linked.bonus == 0.0 and linked.link_bonus == link
    assert linked.reasons() == ["anchor 0x1A2B3C", f"similar {link:.4f} (link {link:.4f})"]
    # with no bonus at all it is still a candidate, at a score of 0
    unlinked_order = selection_for(jkb, "F-0003", link_bonus=0.0)
    assert ids(unlinked_order.candidates) == ["F-0002", "F-0001"]
    assert unlinked_order.candidates[1].reasons() == ["anchor 0x1A2B3C", "similar 0.0000"]


def test_bm25_reasons_name_each_nonzero_term(jkb):
    jkb.add("F-0001", "all", "All three terms.", extra=ANCHOR)
    jkb.add("F-0002", "top", "Top, elsewhere.", topic="optics")
    jkb.add("F-0003", "topic", "Topic only.")
    jkb.add("F-0010", "self", E1, evidence=NEW_EVIDENCE, extra=ANCHOR)
    selection = scored_selection(jkb, "F-0010", {"F-0001": 0.5, "F-0002": 1.0, "F-0003": 0.4},
                                 topic_bonus=0.2, embedding=False)
    assert [c.reasons() for c in selection.candidates] == [
        ["similar 1.0000"],
        ["anchor 0x1A2B3C", "similar 0.8500 (bm25 0.5000 + topic 0.2000 + link 0.1500)"],
        ["similar 0.6000 (bm25 0.4000 + topic 0.2000)"]]


def test_same_fact_in_another_topic_with_no_link_is_a_candidate(jkb):
    jkb.write("evidence/new-run/log.txt", "x\n")
    jkb.add("F-0001", "motor", E1, topic="motor", title="Motor warm-up")
    restated ="After 90 seconds of warm-up the motor output is steady."
    jkb.fake.relations[(E1, restated)] = ("same_fact", 0.93, 0.91)
    result = do_put(jkb, stage(jkb, "F-0002", "steady", restated, topic="timing", title="Steady output",
                               evidence=NEW_EVIDENCE))
    assert kinds(result) == [("same_fact", "reject")]
    log = [json.loads(line) for line in (jkb.root / ".kblam" / "checks.jsonl").read_text(encoding="utf-8").splitlines()]
    candidate, = log[-1]["candidates"]
    assert candidate["id"] == "F-0001" and re.fullmatch(r"similar \d+\.\d{4}", candidate["reasons"][0])


def test_large_topic_picks_by_similarity_not_by_id(jkb):
    for number in range(1, 35):  # 34 weak matches: they share only "pump"
        jkb.add(f"F-{number:04d}", f"feature-{number}", f"The pump feature {number} works.",
                title=f"Feature {number}")
    for number in range(35, 41):  # 6 strong matches, at the highest IDs
        jkb.add(f"F-{number:04d}", f"motor-{number}", f"The pump motor reaches steady output after {number} seconds.",
                title="Motor warm-up")
    jkb.add("F-0041", "self", "The pump motor reaches steady output after 90 seconds of warm-up.",
            title="Motor warm-up", evidence=NEW_EVIDENCE)
    selection = selection_for(jkb, "F-0041")
    assert set(ids(selection.candidates[:6])) == {f"F-{n:04d}" for n in range(35, 41)}
    assert ids(selection.candidates[6:]) == [f"F-{n:04d}" for n in range(1, 25)]  # equal scores: ties by ID
    assert ids(selection.over_budget) == [f"F-{n:04d}" for n in range(25, 35)]


def test_zero_score_finding_is_never_a_candidate(jkb):
    jkb.write("evidence/new-run/log.txt", "x\n")
    jkb.add("F-0001", "tray", E2, title="Media tray pins")  # same topic and scope, no shared token
    result = do_put(jkb, stage(jkb, "F-0002", "drift", N, title="Motor drift", evidence=NEW_EVIDENCE))
    assert result.ok and result.check.candidates == [] and result.check.over_budget == []
    assert jkb.fake.relation_pairs() == []


def test_disjoint_scopes_do_not_count_against_the_budget(jkb):
    jkb.add("F-0001", "mx100", E1, title="Motor warm-up", scope="[MX-100]")  # the best match, but MX-100
    jkb.add("F-0002", "a", "The motor reaches steady output.", title="Motor")
    jkb.add("F-0003", "b", "The motor warm-up is timed by firmware.", title="Motor timer")
    jkb.add("F-0004", "self", E1, title="Motor warm-up", evidence=NEW_EVIDENCE)
    selection = selection_for(jkb, "F-0004", max_candidates=2)
    assert ids(selection.different_scope) == ["F-0001"]
    assert sorted(ids(selection.candidates)) == ["F-0002", "F-0003"] and selection.over_budget == []


def test_topic_bonus_ranks_the_same_topic_first(jkb):
    jkb.add("F-0001", "elsewhere", "The motor reaches steady output.", title="Motor", topic="optics")
    jkb.add("F-0002", "here", "The motor reaches steady output.", title="Motor")
    jkb.add("F-0003", "self", E1, title="Motor warm-up", evidence=NEW_EVIDENCE)
    assert ids(selection_for(jkb, "F-0003").candidates) == ["F-0002", "F-0001"]
    assert ids(selection_for(jkb, "F-0003", topic_bonus=0.0).candidates) == ["F-0001", "F-0002"]


def test_put_asks_before_the_lock_and_only_new_pairs_under_it(jkb, monkeypatch):
    jkb.add("F-0001", "motor", E1)
    real_lock = store.kb_lock
    lock_file = jkb.root / ".kblam" / "lock"
    under_lock = []
    jkb.fake.on_request = lambda body: under_lock.append(
        (body["state"].get("existing", {}).get("claim"), lock_file.exists()))

    @contextmanager
    def lock_after_another_writer(cfg, command):
        if command.startswith("put") and not getattr(lock_after_another_writer, "done", False):
            lock_after_another_writer.done = True
            jkb.add("F-0002", "tray", E2)  # lands between the pre-lock ask and the lock
        with real_lock(cfg, command):
            yield

    monkeypatch.setattr(store, "kb_lock", lock_after_another_writer)
    jkb.fake.relations[(E2, N)] = ("same_fact", 0.93, 0.91)
    result = do_put(jkb, stage(jkb, "F-0003", "drift", N))
    assert kinds(result) == [("same_fact", "reject")]  # decided against the tree as it is under the lock
    assert sorted(under_lock, key=str) == sorted([(E1, False), (None, False), (E2, True)], key=str)


# --- §6.3 quantities ----------------------------------------------------------------------------


def quantity(name: str, value, unit: str = "ratio") -> str:
    return f"quantities:\n  - {{name: {name}, value: {value}, unit: \"{unit}\"}}\n"


def test_quantity_conflict_rejects_without_jev(kb, capsys):
    kb.add("F-0001", "motor", E1, extra=quantity("Curve  Ratio", 1.0017))
    staged = stage(kb, "F-0002", "drift", N, extra=quantity("curve ratio", 1.0020))
    assert run(kb, "put", str(staged)) == 4
    captured = capsys.readouterr()
    assert ("reject quantity_conflict F-0002 vs F-0001: F-0001 gives curve ratio = 1.0017 ratio and this "
            "finding gives 1.002 ratio; rewrite F-0001 in place") in captured.out
    assert "enables no Jev verdict, so Jev was not asked" in captured.err
    assert staged.exists() and load_items(kb.cfg) == []  # §6.3 is code, not Jev: nothing is recorded


def test_quantity_units_tolerance_and_scope(jkb):
    jkb.add("F-0001", "motor", E1, extra=quantity("curve ratio", 1.0017))
    unit = do_put(jkb, stage(jkb, "F-0002", "drift", N, extra=quantity("curve ratio", 1.0017, "%")))
    assert kinds(unit) == [("quantity_conflict", "reject")] and unit.rejected_items == []
    assert load_items(jkb.cfg) == []
    other_scope = do_put(jkb, stage(jkb, "F-0002", "drift", N, scope="[MX-100]",
                                    extra=quantity("curve ratio", 2.0)))
    assert other_scope.ok
    set_config(jkb, jev_extra="quantity_rel_tolerance = 0.001\n")
    close = do_put(jkb, stage(jkb, "F-0003", "near", E2, extra=quantity("curve ratio", 1.0020)))
    assert close.ok and kinds(close) == []


def test_quantity_conflict_reaches_a_finding_that_is_not_a_candidate(jkb):
    jkb.write("evidence/new-run/log.txt", "x\n")
    jkb.add("F-0001", "tray", E2, title="Media tray pins", topic="tray", extra=quantity("curve ratio", 1.0017))
    result = do_put(jkb, stage(jkb, "F-0002", "drift", N, title="Motor drift", evidence=NEW_EVIDENCE,
                               extra=quantity("curve ratio", 1.5)))
    assert kinds(result) == [("quantity_conflict", "reject")]
    assert jkb.fake.relation_pairs() == []  # not a candidate, so Jev was not asked about the pair
    other_scope = do_put(jkb, stage(jkb, "F-0002", "drift", N, title="Motor drift", evidence=NEW_EVIDENCE,
                                    scope="[MX-100]", extra=quantity("curve ratio", 1.5)))
    assert other_scope.ok and kinds(other_scope) == []


# --- check, audit, validate --record ------------------------------------------------------------


def test_audit_asks_only_uncached_pairs(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.add("F-0002", "drift", N)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    assert run(jkb, "audit") == 1  # a reject verdict on findings already in the tree is a review item
    assert "review R-" in capsys.readouterr().out
    assert jkb.fake.relation_pairs() == [(E1, N)] and sorted(jkb.fake.revision_claims()) == sorted([E1, N])
    count = len(jkb.fake.requests)
    assert run(jkb, "audit") == 0
    assert "every candidate pair and revision question has a cached answer" in capsys.readouterr().out
    assert len(jkb.fake.requests) == count


def test_validate_record_checks_changed_findings_first(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.add("F-0002", "drift", N)  # written outside put: never checked
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    # A stale tree.hash, not none: with none, --record accepts the tree without Jev (test_fresh_clone.py).
    (jkb.root / ".kblam" / "tree.hash").write_text("stale\n", encoding="ascii")
    assert run(jkb, "validate", "--record") == 1
    out = capsys.readouterr().out
    assert "same_fact F-0002 vs F-0001" in out and "tree.hash not recorded" in out
    assert read_tree_hash(jkb.cfg) == "stale"
    item_id, = open_ids(jkb)
    assert run(jkb, "resolve", item_id, "--distinct", "one is the MX-100 figure") == 0
    assert run(jkb, "validate", "--record") == 0
    assert "recorded .kblam/tree.hash" in capsys.readouterr().out
    count = len(jkb.fake.requests)
    assert run(jkb, "check") == 0  # every finding checked at its current fingerprint
    assert len(jkb.fake.requests) == count


def test_check_rejects_bad_arguments(jkb, capsys):
    assert run(jkb, "check", "--pending", "F-0001") == 2
    assert run(jkb, "check", "F-0042") == 1
    assert "F-0042 is not a readable finding" in capsys.readouterr().err
    assert run(jkb, "resolve", "R-00000000", "--distinct", "x") == 1


# --- configuration ------------------------------------------------------------------------------


def test_spec_section_9_jev_config_is_accepted(jkb):
    set_config(jkb, jev_extra='max_candidates = 30\nquantity_rel_tolerance = 0.0\nworkers = 6\n')
    policy = parse_policy(JevClient(jkb.cfg).settings.thresholds)
    assert policy.verdicts["same_fact"].p == 0.67 and policy.verdicts["revision"].noul == 0.75
    assert policy.low_confidence_review == 0.49 and policy.served_model == SERVED
    assert policy.prompt_id == DEFAULT_PROMPT_ID == JevClient(jkb.cfg).settings.prompt_id


@pytest.mark.parametrize("thresholds, message", [
    ({"served_model": SERVED, "prompt_id": DEFAULT_PROMPT_ID,
      "unrelated": {"p": 0.5, "confidence": 0.5, "mode": "reject"}},
     "unknown [jev.thresholds] key(s) unrelated"),
    ({"served_model": SERVED, "prompt_id": DEFAULT_PROMPT_ID,
      "same_fact": {"p": 0.5, "confidence": 0.5, "mode": "block"}},
     "same_fact mode must be one of reject, review"),
    ({"served_model": SERVED, "prompt_id": DEFAULT_PROMPT_ID, "revision": {"p": 0.5, "mode": "reject"}},
     "revision must be { noul = 0.75"),
    ({"prompt_id": DEFAULT_PROMPT_ID, "same_fact": {"p": 0.5, "confidence": 0.5, "mode": "reject"}},
     "served_model must name the served model"),
    ({"served_model": SERVED, "same_fact": {"p": 0.5, "confidence": 0.5, "mode": "reject"}},
     "prompt_id must be the id of the prompt"),
    ({"served_model": SERVED, "prompt_version": 2, "low_confidence_review": 1.5},
     "[jev.thresholds] prompt_version is gone: the Jev questions now live in kblam.toml under "
     "[jev.prompt.relation] and [jev.prompt.revision], and [jev.thresholds] carries relation_prompt_id and "
     "revision_prompt_id"),
    ({"served_model": SERVED, "prompt_id": DEFAULT_PROMPT_ID, "low_confidence_review": 1.5},
     "low_confidence_review must be a number from 0 to 1"),
])
def test_bad_thresholds_are_config_errors(thresholds, message):
    with pytest.raises(ConfigError, match=re.escape(message)):
        parse_policy(thresholds)


def test_no_thresholds_means_no_requests(kb):
    fake = ScriptedJev()
    kb.add("F-0001", "motor", E1)
    result = put(kb.cfg, stage(kb, "F-0002", "drift", N),
                 client_factory=lambda cfg: JevClient(cfg, transport=httpx2.MockTransport(fake)))
    assert result.ok and result.unchecked is None and fake.requests == []
    assert not result.check.jev_enabled


# --- opt-in live test ---------------------------------------------------------------------------


@pytest.mark.skipif(not (os.environ.get("KBLAM_LIVE_JEV") and os.environ.get("OPENROUTER_API_KEY")),
                    reason="live Jev test: set KBLAM_LIVE_JEV=1 and OPENROUTER_API_KEY (costs about $0.0001)")
def test_live_put_check(kb):
    set_config(kb)
    kb.add("F-0001", "motor", E1)
    result = put(kb.cfg, stage(kb, "F-0002", "drift", "The motor output is steady after 90 seconds of warm-up."))
    assert result.check is not None and result.check.complete, result.check.unavailable
    assert result.check.mismatch is None
