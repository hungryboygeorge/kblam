"""M6.10 identity of what Jev is asked (SPEC §6.2, §6.4, §6.5, §9): one prompt id per question, what
[jev.thresholds] records (a legacy combined prompt_id is still accepted while it is current), `kblam
prompt-id`, a prompt mismatch demoting only the verdicts of the question whose wording changed, and the
pair cache keyed by each question's own id and the sides' state hashes. Jev is the fake of
test_check.py; nothing here touches the network."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tomllib
from contextlib import closing

import pytest

from kblam import cli, jev, jev_prompts
from kblam.check import parse_policy
from kblam.config import ConfigError
from kblam.finding import Finding
from kblam.jev import PairCache, Side
from kblam.store import edit_finding
from kblam.view import load_view

from conftest import DEFAULT_PROMPT_ID, KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, TEMPLATE_TOML
from test_check import E1, N, THRESHOLDS, do_put, jkb, kinds, run, stage  # noqa: F401 (jkb is a fixture)
from test_jev import JEV_TOML, SERVED

RELATION_ID = "6d79e4e0e409"   # SPEC §6.2: the ids of the shipped default wording
REVISION_ID = "d9a34c823fe3"
COMBINED_ID = "4dda2f781f12"
OTHER_ID = "0123456789ab"      # an id recorded for some other wording
OTHER_MODEL = "typesafe/jev-1.14-20261101"

RELATION_TEXT = "The claims are about different subjects."
REVISION_TEXT = "The claim states a fact directly, without referring to an earlier claim."


def canonical_hash(payload) -> str:
    """The first 12 hex digits of the sha256 of `payload` as canonical JSON, written out independently."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def per_question(relation: str = RELATION_ID, revision: str = REVISION_ID) -> str:
    """test_check's [jev.thresholds] with each question's own id in place of the combined prompt_id."""
    legacy = f'prompt_id = "{DEFAULT_PROMPT_ID}"\n'
    assert legacy in THRESHOLDS
    return THRESHOLDS.replace(legacy, f'relation_prompt_id = "{relation}"\nrevision_prompt_id = "{revision}"\n')


def configure(kb, thresholds: str, prompt: str = PROMPT_TOML) -> None:
    kb.write("kblam.toml", KBLAM_TOML + NO_EMBEDDINGS + JEV_TOML + thresholds + prompt)


# --- the ids -------------------------------------------------------------------------------------


def test_each_question_of_the_shipped_default_has_its_own_id():
    relation, revision = jev_prompts.load_questions(tomllib.loads(TEMPLATE_TOML)["jev"]["prompt"])
    shape = jev_prompts.SHAPE_VERSION
    assert jev_prompts.relation_prompt_id(relation) == canonical_hash({"shape": shape, "relation": relation}) \
        == RELATION_ID
    assert jev_prompts.revision_prompt_id(revision) == canonical_hash({"shape": shape, "revision": revision}) \
        == REVISION_ID
    assert jev_prompts.prompt_id(relation, revision) == COMBINED_ID == DEFAULT_PROMPT_ID  # the combined id stays


def test_the_template_records_each_questions_id_instead_of_prompt_id():
    recorded = tomllib.loads(TEMPLATE_TOML)["jev"]["thresholds"]
    assert "prompt_id" not in recorded
    policy = parse_policy(recorded)
    assert (policy.prompt_id, policy.relation_prompt_id, policy.revision_prompt_id) == (None, RELATION_ID, REVISION_ID)


def test_jev_settings_expose_all_three_ids(kb):
    settings = jev.jev_settings(kb.cfg)
    assert (settings.prompt_id, settings.relation_prompt_id, settings.revision_prompt_id) == \
           (COMBINED_ID, RELATION_ID, REVISION_ID)


def test_editing_one_question_changes_only_its_own_id(kb, monkeypatch):
    def ids(prompt: str = PROMPT_TOML) -> tuple[str, str, str]:
        configure(kb, "", prompt)
        settings = jev.jev_settings(kb.cfg)
        return settings.prompt_id, settings.relation_prompt_id, settings.revision_prompt_id

    assert RELATION_TEXT in PROMPT_TOML and REVISION_TEXT in PROMPT_TOML  # the edits below must bite
    combined, relation, revision = ids(PROMPT_TOML.replace(RELATION_TEXT, "The claims are about other subjects."))
    assert combined != COMBINED_ID and relation != RELATION_ID and revision == REVISION_ID
    combined, relation, revision = ids(PROMPT_TOML.replace(REVISION_TEXT, "The claim states a fact directly."))
    assert combined != COMBINED_ID and relation == RELATION_ID and revision != REVISION_ID
    monkeypatch.setattr(jev_prompts, "SHAPE_VERSION", 2)  # a state shape belongs to both questions
    combined, relation, revision = ids()
    assert combined != COMBINED_ID and relation != RELATION_ID and revision != REVISION_ID


def test_prompt_id_prints_the_combined_id_then_each_questions(kb, capsys):
    assert cli.main(["--root", str(kb.root), "prompt-id"]) == 0
    assert capsys.readouterr().out == (f"kblam prompt-id: {COMBINED_ID}\n"
                                       f"relation: {RELATION_ID}\n"
                                       f"revision: {REVISION_ID}\n")


# --- [jev.thresholds] --------------------------------------------------------------------------------

FACT = {"p": 0.69, "confidence": 0.61, "mode": "reject"}
REVISION = {"noul": 0.75, "mode": "reject"}


def test_each_questions_id_is_accepted():
    policy = parse_policy({"served_model": SERVED, "relation_prompt_id": RELATION_ID,
                           "revision_prompt_id": REVISION_ID, "same_fact": FACT, "revision": REVISION})
    assert (policy.prompt_id, policy.relation_prompt_id, policy.revision_prompt_id) == (None, RELATION_ID, REVISION_ID)


def test_a_legacy_combined_prompt_id_is_accepted():
    policy = parse_policy({"served_model": SERVED, "prompt_id": COMBINED_ID, "same_fact": FACT, "revision": REVISION})
    assert (policy.prompt_id, policy.relation_prompt_id, policy.revision_prompt_id) == (COMBINED_ID, None, None)


def test_only_the_ids_of_the_questions_an_enabled_verdict_reads_are_required():
    only_relation = parse_policy({"served_model": SERVED, "relation_prompt_id": RELATION_ID, "same_fact": FACT})
    assert only_relation.asks_relation and not only_relation.asks_revision
    only_revision = parse_policy({"served_model": SERVED, "revision_prompt_id": REVISION_ID, "revision": REVISION})
    assert only_revision.asks_revision and not only_revision.asks_relation
    assert not parse_policy({}).enabled  # nothing enabled: nothing required


@pytest.mark.parametrize("thresholds, message", [
    ({"served_model": SERVED, "prompt_id": COMBINED_ID, "relation_prompt_id": RELATION_ID,
      "revision_prompt_id": REVISION_ID, "same_fact": FACT},
     "[jev.thresholds] records both prompt_id and relation_prompt_id and revision_prompt_id"),
    ({"prompt_id": COMBINED_ID, "revision_prompt_id": REVISION_ID},  # both styles, even with nothing enabled
     "[jev.thresholds] records both prompt_id and revision_prompt_id"),
    ({"served_model": SERVED, "revision_prompt_id": REVISION_ID, "same_fact": FACT, "revision": REVISION},
     "[jev.thresholds] relation_prompt_id must be the id of the prompt's relation question these thresholds were "
     "calibrated on, as kblam prompt-id prints it (its \"relation:\" line), e.g. \"6d79e4e0e409\""),
    ({"served_model": SERVED, "relation_prompt_id": RELATION_ID, "revision": REVISION},
     "[jev.thresholds] revision_prompt_id must be the id of the prompt's revision question"),
    ({"served_model": SERVED, "revision_prompt_id": REVISION_ID, "low_confidence_review": 0.3},  # reads the relation
     "[jev.thresholds] relation_prompt_id must be"),
    ({"served_model": SERVED, "relation_prompt_id": "", "same_fact": FACT},
     "[jev.thresholds] relation_prompt_id must be"),
    ({"served_model": SERVED, "prompt_id": 7, "revision": REVISION},
     "[jev.thresholds] prompt_id must be the id of the prompt"),
    ({"relation_prompt_id": RELATION_ID, "same_fact": FACT},
     "[jev.thresholds] served_model must name the served model"),
])
def test_bad_prompt_ids_are_config_errors_naming_the_key(thresholds, message):
    with pytest.raises(ConfigError, match=re.escape(message)):
        parse_policy(thresholds)


# --- a mismatch demotes the verdicts of what differs (§6.4) ------------------------------------------

BOTH_REJECT = [("same_fact", "reject"), ("revision", "reject")]
BOTH_REVIEW = [("same_fact", "review"), ("revision", "review")]


@pytest.mark.parametrize("thresholds, served, expected, named", [
    (per_question(), SERVED, BOTH_REJECT, None),
    (per_question(relation=OTHER_ID), SERVED, [("same_fact", "review"), ("revision", "reject")],
     f"relation prompt {RELATION_ID} (thresholds: {OTHER_ID})"),
    (per_question(revision=OTHER_ID), SERVED, [("same_fact", "reject"), ("revision", "review")],
     f"revision prompt {REVISION_ID} (thresholds: {OTHER_ID})"),
    (THRESHOLDS, SERVED, BOTH_REJECT, None),  # a legacy prompt_id equal to the combined id vouches for both
    (THRESHOLDS.replace(DEFAULT_PROMPT_ID, OTHER_ID), SERVED, BOTH_REVIEW,  # and for neither once it differs
     f"prompt {COMBINED_ID} (thresholds: {OTHER_ID})"),
    (per_question(), OTHER_MODEL, BOTH_REVIEW, f"served model {OTHER_MODEL} (thresholds: {SERVED})"),
])
def test_a_mismatch_demotes_only_the_verdicts_of_what_differs(jkb, thresholds, served, expected, named):
    configure(jkb, thresholds)
    jkb.fake.served = served
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    jkb.fake.nouls[N] = 0.9
    result = do_put(jkb, stage(jkb, "F-0002", "drift", N))
    assert kinds(result) == expected
    warning = result.check.mismatch
    if named is None:
        assert warning is None
        return
    assert warning.startswith(f"the [jev.thresholds] in kblam.toml were calibrated on {SERVED}, but this check "
                              f"used {named}. ")
    if expected == BOTH_REVIEW:
        assert "No Jev verdict of this check rejects: every one that fires is a review item." in warning
    elif expected[0][1] == "review":
        assert ("No verdict of the relation question (same_fact, cannot_both_be_true, restates_and_extends) "
                "rejects: every one that fires is a review item. The revision question's thresholds still "
                "apply.") in warning
        assert "revision prompt" not in warning
    else:
        assert ("No revision verdict rejects: one that fires is a review item. The relation question's "
                "thresholds still apply.") in warning
        assert "relation prompt" not in warning
    assert warning.endswith("Re-calibrate (SPEC §10) and update [jev.thresholds]")


def test_the_warning_is_printed_naming_what_differs(jkb, capsys):
    configure(jkb, per_question(revision=OTHER_ID))
    jkb.fake.nouls[N] = 0.9
    assert run(jkb, "put", str(stage(jkb, "F-0001", "drift", N))) == 0  # the demoted revision verdict reviews
    out = capsys.readouterr().out
    assert f"kblam put: WARNING: the [jev.thresholds] in kblam.toml were calibrated on {SERVED}, but this check " \
           f"used revision prompt {REVISION_ID} (thresholds: {OTHER_ID})" in out


def test_a_question_the_check_did_not_ask_warns_nothing(jkb):
    configure(jkb, per_question(relation=OTHER_ID))
    jkb.fake.nouls[N] = 0.9
    result = do_put(jkb, stage(jkb, "F-0001", "drift", N))  # no other finding, so no relation question
    assert kinds(result) == [("revision", "reject")] and result.check.mismatch is None


# --- the pair cache: each question's own id and the sides' state hashes (§6.5) -------------------


def finding(claim: str, **meta) -> Finding:
    base = {"scope": ["MX-200", "MX-100"], "evidence": ["evidence/a/"], "label": "observed", "title": "A title"}
    return Finding(path="findings/x/F-0001-a.md", raw=b"", file_id="F-0001", meta={**base, **meta}, claim=claim)


def test_a_state_hash_is_the_hash_of_the_side_as_sent():
    side = Side.of(finding("The motor  reaches\nsteady output."))
    state = {"claim": "The motor reaches steady output.", "scope": ["MX-200", "MX-100"]}
    assert side.state() == state and side.state_hash == jev_prompts.state_hash(state) == canonical_hash(state)
    # an edit Jev does not see keeps it (the fingerprint follows the evidence and the quantities)
    for unseen in ({"evidence": ["evidence/b/"]}, {"quantities": [{"name": "q", "value": 1}]},
                   {"label": "inferred"}, {"title": "Another title"}):
        assert Side.of(finding("The motor reaches steady output.", **unseen)).state_hash == side.state_hash
    assert Side.of(finding("The motor reaches steady output.", evidence=["evidence/b/"])).fingerprint \
        != side.fingerprint
    # one Jev sees changes it, a reordered scope included: the state is hashed exactly as it is sent
    for seen in (finding("The motor reaches steady output quickly."), finding(side.claim, scope=["MX-200"]),
                 finding(side.claim, scope=["MX-100", "MX-200"])):
        assert Side.of(seen).state_hash != side.state_hash


def answer_rows(kb) -> list[tuple]:
    with closing(sqlite3.connect(kb.root / ".kblam" / "pairs.sqlite")) as conn:
        return sorted(conn.execute("SELECT expected_model, prompt_id, kind, existing_fp, new_fp FROM answers"))


def test_the_pair_cache_is_keyed_by_each_questions_id_and_the_state_hashes_sent(jkb):
    jkb.add("F-0001", "motor", E1)
    assert do_put(jkb, stage(jkb, "F-0002", "drift", N)).ok
    relation, = [b["state"] for b in jkb.fake.requests if "existing" in b["state"]]
    revision, = [b["state"] for b in jkb.fake.requests if "existing" not in b["state"]]
    assert answer_rows(jkb) == sorted([
        (SERVED, RELATION_ID, "relation", canonical_hash(relation["existing"]), canonical_hash(relation["new"])),
        (SERVED, REVISION_ID, "revision", "", canonical_hash(revision["new"]))])


def test_each_questions_answers_are_cached_under_its_own_id(jkb):
    configure(jkb, per_question())
    jkb.add("F-0001", "motor", E1)
    jkb.add("F-0002", "drift", N)

    def asked() -> tuple[int, int]:
        return len(jkb.fake.relation_pairs()), len(jkb.fake.revision_claims())

    assert run(jkb, "check", "F-0002") == 0 and asked() == (1, 1)
    reworded_revision = PROMPT_TOML.replace(REVISION_TEXT, "The claim states a fact directly.")
    configure(jkb, per_question(), reworded_revision)
    assert run(jkb, "check", "F-0002") == 0 and asked() == (1, 2)  # the relation answer is still current
    configure(jkb, per_question(), reworded_revision.replace(RELATION_TEXT, "The claims are about other subjects."))
    assert run(jkb, "check", "F-0002") == 0 and asked() == (2, 2)  # and now the revision answer is


def test_an_edit_jev_does_not_see_asks_nothing_but_is_checked_at_its_new_fingerprint(jkb):
    jkb.add("F-0001", "motor", E1)
    first = do_put(jkb, stage(jkb, "F-0002", "drift", N))
    assert first.ok
    count = len(jkb.fake.requests)
    jkb.write("evidence/new-run/log.txt", "x\n")
    staged = edit_finding(jkb.cfg, "F-0002")
    staged.write_text(staged.read_text(encoding="utf-8").replace(
        "evidence: [evidence/2026-09-22-ratio/]", "evidence: [evidence/2026-09-22-ratio/, evidence/new-run/log.txt]"),
        encoding="utf-8", newline="\n")
    again = do_put(jkb, staged)
    assert again.ok and len(jkb.fake.requests) == count  # every answer came from the cache
    assert again.check.fingerprint != first.check.fingerprint and again.check.candidates == ["F-0001"]
    cache = PairCache(jkb.root / ".kblam" / "pairs.sqlite")
    assert cache.was_checked("F-0002", again.check.fingerprint)  # a finding is checked at its fingerprint


def test_audit_looks_answers_up_by_state_hash(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.add("F-0002", "drift", N)
    assert run(jkb, "audit") == 0
    count = len(jkb.fake.requests)
    jkb.write("evidence/new-run/log.txt", "x\n")
    jkb.add("F-0002", "drift", N, evidence="[evidence/2026-09-22-ratio/, evidence/new-run/log.txt]")
    assert run(jkb, "audit") == 0  # a new fingerprint, the same state: nothing to ask
    assert "every candidate pair and revision question has a cached answer" in capsys.readouterr().out
    assert len(jkb.fake.requests) == count


def test_rows_keyed_by_fingerprints_no_longer_match_and_are_kept(jkb):
    """A cache written before M6.10 keys answers by the combined prompt id and fingerprints: they are
    not reused, and they are not deleted either (kblam upgrade re-keys them)."""
    jkb.add("F-0001", "motor", E1)
    jkb.add("F-0002", "drift", N)
    existing, new = (Side.of(f) for f in load_view(jkb.cfg).findings)
    cache = PairCache(jkb.root / ".kblam" / "pairs.sqlite")
    call = {"requested_model": "typesafe/jev-1.13", "served_model": SERVED, "expected_served_model": SERVED,
            "input_tokens": 700, "output_tokens": 8, "cost": 0.00003, "latency_s": 0.5, "attempts": 1,
            "generation_id": "gen-old", "provider": "TypeSafe"}
    same_fact = {"winner": "same_fact", "confidence": 0.91,
                 "probabilities": {o: (0.93 if o == "same_fact" else 0.0175) for o in jev_prompts.RELATION_OPTIONS}}
    old = [(SERVED, COMBINED_ID, "relation", existing.fingerprint, new.fingerprint),
           (SERVED, COMBINED_ID, "revision", "", new.fingerprint)]
    cache.put(old[0], SERVED, same_fact, call)
    cache.put(old[1], SERVED, {"noul": 0.99}, call)
    assert run(jkb, "check", "F-0002") == 0  # the fake's answers, not the old rows' same_fact and 0.99
    assert jkb.fake.relation_pairs() == [(E1, N)] and jkb.fake.revision_claims() == [N]
    assert all(cache.contains(key, SERVED) for key in old)
