"""One passing and at least one failing case per rule (K3 and fingerprints: test_deps.py)."""

from __future__ import annotations

import random

import pytest

from kblam import rules
from kblam.finding import fingerprint
from kblam.view import load_view

from conftest import finding_text

CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."


def run(kb, rule):
    return rule(load_view(kb.cfg))


def messages(issues) -> str:
    return "\n".join(i.message for i in issues)


def test_clean_kb_passes_every_rule(kb):
    kb.add("F-0001", "sensor-curve-types", CLAIM_A)
    stamp = fingerprint(load_view(kb.cfg).findings[0])
    kb.add("F-0002", "motor-warmup", CLAIM_B, topic="motor", extra=f"depends_on:\n  F-0001: {stamp}\n")
    assert kb.issues() == []


# --- K1 ---------------------------------------------------------------------------------------


def test_k1_accepts_quoted_anchors_and_full_schema(kb):
    kb.add("F-0001", "sensor", CLAIM_A, extra=(
        'anchors: ["0x1A2B3C", "DTC 0x84"]\n'
        "quantities:\n  - {name: type1/type0 curve ratio, value: 1.0017, unit: ratio}\n"
        'check: "uv run python ratio.py"\n'
    ))
    assert run(kb, rules.k1_schema) == []


def test_k1_rejects_unquoted_hex_anchor(kb):
    kb.add("F-0001", "sensor", CLAIM_A, extra='anchors: [0x1A2B3C, "DTC 0x84"]\n')
    issues = run(kb, rules.k1_schema)
    assert [i.code for i in issues] == ["K1"]
    assert issues[0].line == 9
    assert 'quote it: "0x1A2B3C"' in issues[0].message


def test_k1_rejects_id_filename_mismatch(kb):
    kb.write("findings/calibration/F-0001-sensor.md", finding_text("F-0002", CLAIM_A))
    kb.reindex()
    issues = run(kb, rules.k1_schema)
    assert len(issues) == 1
    assert "does not match the filename ID F-0001" in issues[0].message


def test_k1_rejects_topic_folder_mismatch(kb):
    kb.write("findings/motor/F-0001-sensor.md", finding_text("F-0001", CLAIM_A, topic="calibration"))
    kb.reindex()
    issues = run(kb, rules.k1_schema)
    assert len(issues) == 1
    assert "does not match the folder 'motor'" in issues[0].message


def test_k1_rejects_label_outside_vocabulary(kb):
    kb.add("F-0001", "sensor", CLAIM_A, label="confirmed")
    issues = run(kb, rules.k1_schema)
    assert len(issues) == 1
    assert "label 'confirmed' is not in the vocabulary" in issues[0].message
    assert "observed, decoded, inferred, unknown" in issues[0].message


def test_k1_rejects_scope_outside_vocabulary(kb):
    kb.add("F-0001", "sensor", CLAIM_A, scope="[MX-300]")
    assert "scope 'MX-300' is not in the vocabulary" in messages(run(kb, rules.k1_schema))


def test_k1_rejects_unknown_and_missing_keys(kb):
    text = finding_text("F-0001", CLAIM_A, extra="withdrawn_by: F-0009\n").replace("verified: 2026-09-22\n", "")
    kb.write("findings/calibration/F-0001-sensor.md", text)
    kb.reindex()
    text = messages(run(kb, rules.k1_schema))
    assert "unknown frontmatter key 'withdrawn_by'" in text
    assert "missing required key 'verified'" in text


def test_k1_rejects_duplicate_ids_and_bad_yaml(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write("findings/motor/F-0001-motor.md", finding_text("F-0001", CLAIM_B, topic="motor"))
    kb.write("findings/motor/F-0003-broken.md", "---\nid: [unclosed\n---\n\nClaim.\n")
    kb.reindex()
    text = messages(run(kb, rules.k1_schema))
    assert text.count("ID F-0001 is also used by") == 2
    assert "frontmatter is not valid YAML" in text


# --- K2 ---------------------------------------------------------------------------------------


def test_k2_accepts_existing_evidence_and_dependency(kb):
    kb.add("F-0001", "sensor", CLAIM_A, evidence="[evidence/2026-09-22-ratio/log.txt]")
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: null\n")
    assert run(kb, rules.k2_references) == []


def test_k2_rejects_missing_evidence_and_dependency(kb):
    kb.add("F-0001", "sensor", CLAIM_A, evidence="[evidence/nope/, ../outside.txt]",
           extra="depends_on:\n  F-0099: abc\n")
    text = messages(run(kb, rules.k2_references))
    assert "evidence path evidence/nope/ does not exist" in text
    assert "../outside.txt points outside the repository root" in text
    assert "depends_on names F-0099, which is not a finding" in text


def test_k2_rejects_empty_or_absent_evidence(kb):
    kb.add("F-0001", "sensor", CLAIM_A, evidence="[]")
    kb.write("findings/motor/F-0002-motor.md",
             finding_text("F-0002", CLAIM_B, topic="motor").replace("evidence: [evidence/2026-09-22-ratio/]\n", ""))
    kb.reindex()
    issues = run(kb, rules.k2_references)
    assert [(i.path.split("/")[-1], i.code) for i in issues] == [("F-0001-sensor.md", "K2"), ("F-0002-motor.md", "K2")]
    assert all("at least one repo-relative path" in i.message for i in issues)
    assert run(kb, rules.k1_schema) == []  # evidence presence is K2's rule, not K1's


# --- K4 ---------------------------------------------------------------------------------------


def test_k4_accepts_direct_statement(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body="The update rate is 60 Hz; a correct reading needs line 0.")
    assert run(kb, rules.k4_history_language) == []


def test_k4_rejects_revision_language_in_body_and_title(kb):
    kb.add("F-0001", "sensor", CLAIM_A, title="Superseded curve types",
           body="This was previously believed to be\ntwo gains. It was wrong.")
    issues = run(kb, rules.k4_history_language)
    found = {(i.line, i.message.split('"')[1]) for i in issues}
    assert found == {(3, "supersed"), (13, "previously believed"), (14, "was wrong")}
    assert "State only the current fact" in issues[0].message


def test_k4_matches_stems_at_word_start_only(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=(
        "The shading correction and dark correction run per line.\n"
        "An unsuperseded, irretractable aside.\n"
        "Superseded by nothing. RETRACTED here.\n"
        "It Was Falsified. Refuted ahead."))
    found = sorted(i.message.split('"')[1] for i in run(kb, rules.k4_history_language))
    assert found == ["refuted", "retract", "supersed", "was falsified"]


@pytest.mark.parametrize("sentence", [
    "The dark frame is used to subtract the offset.",           # "used to"
    "Write the previously read buffers back unchanged.",        # "previously"
    "The exposure is no longer than 5 ms.",                     # "no longer"
    "The revised firmware reads the table.",                    # "revised"
    "The shading correction: a per-column gain.",               # "correction:"
    "The prediction is falsifiable.",                           # "falsif"
])
def test_k4_passes_legitimate_uses_of_dropped_terms(kb, sentence):
    kb.add("F-0001", "sensor", sentence, body=sentence)
    assert run(kb, rules.k4_history_language) == []


@pytest.mark.parametrize("sentence, term", [
    ("The gain model was falsified by run 3.", "was falsified"),
    ("The offset was previously believed to be fixed.", "previously believed"),
    ("The 60 s figure is no longer true.", "no longer true"),
    ("The two-gain model is refuted by the ratio.", "refuted"),
])
def test_k4_new_terms_still_fire(kb, sentence, term):
    kb.add("F-0001", "sensor", sentence)
    assert term in {i.message.split('"')[1] for i in run(kb, rules.k4_history_language)}


def test_k4_k5_exempt_passing_verbatim_excerpt(kb):
    kb.write("evidence/2026-09-22-ratio/notes.txt", "The gain in F-0001 firmware was wrong.\n")
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body=_with_excerpt(
        "evidence/2026-09-22-ratio/notes.txt:1", "The gain in F-0001 firmware was wrong."))
    view = load_view(kb.cfg)
    assert rules.k4_history_language(view) == []
    assert rules.k5_id_near_history(view) == []
    assert rules.k10_verbatim(view) == []


def test_k4_k5_do_not_exempt_failing_or_unchecked_excerpts(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body=_with_excerpt(
        "evidence/2026-09-22-ratio/log.txt:1", "F-0001 was wrong."))
    kb.add("F-0003", "dump", "The dump header is eight bytes long.", topic="dump", body=_with_excerpt(
        "evidence/2026-09-22-ratio/dump.bin:@0x0", "superseded header"))
    codes = sorted((i.path.split("/")[-1], i.code) for i in kb.issues())
    assert codes == [("F-0002-motor.md", "K10"), ("F-0002-motor.md", "K4"), ("F-0002-motor.md", "K5"),
                     ("F-0003-dump.md", "K4")]


# --- K5 ---------------------------------------------------------------------------------------


def test_k5_accepts_id_mention_without_history_language(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body="The warm-up curve uses the gain from F-0001.")
    assert run(kb, rules.k5_id_near_history) == []


def test_k5_rejects_other_finding_called_wrong(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body="F-0001 was wrong on the gains.")
    issues = run(kb, rules.k5_id_near_history)
    assert len(issues) == 1
    assert issues[0].path.endswith("F-0002-motor.md")
    assert "rewrite it in place (kblam edit F-0001)" in issues[0].message


def test_k5_ignores_term_far_from_id(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    filler = " ".join(["word"] * 20)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body=f"See F-0001. {filler} no longer true.")
    assert run(kb, rules.k5_id_near_history) == []


# --- K6 ---------------------------------------------------------------------------------------


def test_k6_accepts_claim_at_word_limit(kb):
    kb.add("F-0001", "sensor", " ".join(["word"] * 250))
    assert run(kb, rules.k6_length) == []


def test_k6_rejects_long_claim_and_long_file(kb):
    kb.add("F-0001", "sensor", " ".join(["word"] * 251), body="\n".join(["x"] * 300))
    text = messages(run(kb, rules.k6_length))
    assert "claim paragraph has 251 words (max 250)" in text
    assert "(max 300)" in text


def test_k6_rejects_missing_and_non_prose_claim(kb):
    kb.write("findings/calibration/F-0001-a.md", finding_text("F-0001", CLAIM_A).split("**Claim.**")[0])
    text = finding_text("F-0002", CLAIM_B, topic="calibration").replace("**Claim.** ", "## Heading\n")
    kb.write("findings/calibration/F-0002-b.md", text)
    kb.reindex()
    text = messages(run(kb, rules.k6_length))
    assert "no claim paragraph" in text
    assert "must be the claim in prose" in text


# --- K7 ---------------------------------------------------------------------------------------


def test_k7_accepts_generated_index(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    assert run(kb, rules.k7_index) == []
    index = (kb.findings / "INDEX.md").read_text(encoding="utf-8")
    assert "| [F-0001](calibration/F-0001-sensor.md) | Title of F-0001 | observed | MX-200 |" in index


def test_index_escapes_table_pipes(kb):
    kb.add("F-0001", "sensor", CLAIM_A, title='"Type 0 | type 1 ratio"')
    index = (kb.findings / "INDEX.md").read_text(encoding="utf-8")
    assert r"| Type 0 \| type 1 ratio |" in index


def test_k7_detects_hand_edited_index(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    index = kb.findings / "INDEX.md"
    index.write_bytes(index.read_bytes().replace(b"Title of F-0001", b"A better title"))
    issues = run(kb, rules.k7_index)
    assert len(issues) == 1 and "Run kblam index" in issues[0].message


def test_k7_detects_missing_index(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    (kb.findings / "INDEX.md").unlink()
    assert "INDEX.md is missing" in messages(run(kb, rules.k7_index))


# --- K8 ---------------------------------------------------------------------------------------


def test_k8_accepts_findings_and_index_only(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    assert run(kb, rules.k8_stray_files) == []


def test_k8_rejects_stray_summary(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write("findings/summary.md", "# Summary\n")
    kb.write("findings/calibration/handoff.md", "# Handoff\n")
    issues = run(kb, rules.k8_stray_files)
    assert [i.path for i in issues] == ["findings/calibration/handoff.md", "findings/summary.md"]


def test_k8_rejects_any_non_finding_file_and_misplaced_findings(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write("findings/calibration/notes.txt", "scratch\n")
    kb.write("findings/data.csv", "a,b\n")
    kb.write("findings/F-0002-at-root.md", finding_text("F-0002", CLAIM_B))
    kb.write("findings/calibration/deep/F-0003-nested.md", finding_text("F-0003", CLAIM_B))
    kb.write("findings/calibration/INDEX.md", "# not the index\n")
    issues = run(kb, rules.k8_stray_files)
    assert [i.path for i in issues] == [
        "findings/F-0002-at-root.md",
        "findings/calibration/INDEX.md",
        "findings/calibration/deep/F-0003-nested.md",
        "findings/calibration/notes.txt",
        "findings/data.csv",
    ]
    assert "must sit directly in a topic folder" in issues[0].message
    assert "holds only findings" in issues[-1].message
    assert load_view(kb.cfg).findings[0].file_id == "F-0001"  # misplaced files are not findings
    assert len(load_view(kb.cfg).findings) == 1


# --- K9 ---------------------------------------------------------------------------------------


def test_k9_accepts_distinct_claims_on_same_subject(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "sensor-2", "The sensor curve page holds two types, stored as 2048 16-bit words each.")
    assert run(kb, rules.k9_duplicates) == []


def test_k9_rejects_near_duplicate_claim(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    near = ("The two sensor curve types agree to roughly 0.1% (median ratio 1.0017 on line 0); "
            "so they are not two analog gains")
    kb.add("F-0002", "sensor-again", near)
    issues = run(kb, rules.k9_duplicates)
    assert len(issues) == 1
    assert issues[0].path.endswith("F-0002-sensor-again.md")
    assert "edit F-0001 instead" in issues[0].message


@pytest.mark.parametrize("threshold", [0.1, 1 / 3, 0.5, 0.56, 0.75, 0.8, 0.9, 1.0])
def test_k9_candidate_pairs_include_every_pair_at_or_above_the_threshold(threshold):
    """similar_pairs prunes K9's all-pairs comparison. It may return extra pairs (K9 measures each),
    but never drop one whose similarity, computed as K9 computes it, reaches the threshold: random
    sets over a small vocabulary, duplicates, empty sets, and pairs whose ratio lands exactly on a
    threshold (9/10, 4/5, 3/4, 14/25, 1/2, 1/3), where float rounding decides: 0.56 * 25 is
    14.000000000000002 while 14 / 25 >= 0.56, so a bound computed without slack drops that pair."""
    rng = random.Random(9)
    vocabulary = [f"t{i}" for i in range(30)]
    sets = [frozenset(rng.sample(vocabulary, rng.randint(0, 14))) for _ in range(160)]
    sets += [sets[3], frozenset(), sets[40]]
    for shared, extra in ((9, 1), (4, 1), (3, 1), (14, 11), (1, 1), (1, 2), (12, 3)):
        base = [f"x{len(sets)}-{i}" for i in range(shared)]
        sets += [frozenset(base + [f"a{len(sets)}-{i}" for i in range(extra)]), frozenset(base)]
    pairs = rules.similar_pairs(sets, threshold)
    assert pairs == sorted(set(pairs)) and all(i < j for i, j in pairs)
    expected = {(i, j) for i in range(len(sets)) for j in range(i + 1, len(sets))
                if sets[i] and sets[j] and len(sets[i] & sets[j]) / len(sets[i] | sets[j]) >= threshold}
    assert expected <= set(pairs)
    assert expected  # the case is not vacuous


# --- K10 --------------------------------------------------------------------------------------


def _with_excerpt(tag: str, excerpt: str, *, quote: bool = True) -> str:
    if quote:
        block = "\n".join("> " + line for line in excerpt.split("\n"))
    else:
        block = "```\n" + excerpt + "\n```"
    return f"Detail follows.\n\n<!-- verbatim: {tag} -->\n{block}"


def test_k10_accepts_real_excerpt_in_range(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_with_excerpt(
        "evidence/2026-09-22-ratio/log.txt:2-3",
        "The two curve types agree to 0.1% on line 0.\nMedian ratio 1.0017 across 2048 pixels."))
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body=_with_excerpt(
        "evidence/2026-09-22-ratio/log.txt:3", "ratio 1.0017 across", quote=False))
    assert run(kb, rules.k10_verbatim) == []


def test_k10_rejects_paraphrase(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_with_excerpt(
        "evidence/2026-09-22-ratio/log.txt:2", "The two curve types match to 0.1% on line 0."))
    assert "does not occur verbatim in evidence/2026-09-22-ratio/log.txt:2-2" in messages(
        run(kb, rules.k10_verbatim))


def test_k10_rejects_wrong_range_and_says_where(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_with_excerpt(
        "evidence/2026-09-22-ratio/log.txt:1", "Median ratio 1.0017 across 2048 pixels."))
    text = messages(run(kb, rules.k10_verbatim))
    assert "not within evidence/2026-09-22-ratio/log.txt:1-1" in text
    assert "occurs at evidence/2026-09-22-ratio/log.txt:3-3" in text


def test_k10_rejects_range_past_end_of_file(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_with_excerpt("evidence/2026-09-22-ratio/log.txt:4-9", "x"))
    assert "outside evidence/2026-09-22-ratio/log.txt, which has 4 lines" in messages(
        run(kb, rules.k10_verbatim))


def test_k10_rejects_missing_source_file(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_with_excerpt("evidence/missing.txt:1", "anything"))
    assert "verbatim source evidence/missing.txt does not exist" in messages(run(kb, rules.k10_verbatim))


def test_k10_exempts_binary_source(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_with_excerpt(
        "evidence/2026-09-22-ratio/dump.bin:@0x10", "not in the file at all"))
    assert run(kb, rules.k10_verbatim) == []


def test_k10_checks_byte_offset_in_text_source(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_with_excerpt("evidence/2026-09-22-ratio/log.txt:@0x5", "one of"))
    assert run(kb, rules.k10_verbatim) == []
    kb.add("F-0001", "sensor", CLAIM_A, body=_with_excerpt("evidence/2026-09-22-ratio/log.txt:@0x6", "one of"))
    assert "byte offset 0x6" in messages(run(kb, rules.k10_verbatim))


def test_k10_rejects_tag_without_block(kb):
    kb.add("F-0001", "sensor", CLAIM_A,
           body="<!-- verbatim: evidence/2026-09-22-ratio/log.txt:1 -->\n\n> Line one of the capture log.")
    assert "directly followed (next line)" in messages(run(kb, rules.k10_verbatim))


# --- K11 --------------------------------------------------------------------------------------

HISTORY_TEXT = "# retired topic\n\nA20 measured +0.397 on the odd pair.\n"


def _reported_kb(kb, *, extra_kb: str = ""):
    from conftest import KBLAM_TOML, NO_EMBEDDINGS
    toml = KBLAM_TOML.replace('"unknown"]', '"unknown", "reported"]') + extra_kb
    kb.write("kblam.toml", toml + NO_EMBEDDINGS)
    kb.write("history/topic-findings.md", HISTORY_TEXT)
    return kb


def test_k11_accepts_reported_finding_quoting_history(kb):
    _reported_kb(kb)
    kb.add("F-0001", "sensor", CLAIM_A, label="reported", evidence="[history/topic-findings.md]",
           body=_with_excerpt("history/topic-findings.md:3", "A20 measured +0.397 on the odd pair."))
    assert run(kb, rules.k11_reported) == []
    assert kb.issues() == []


def test_k11_rejects_reported_finding_without_history_excerpt(kb):
    _reported_kb(kb)
    kb.add("F-0001", "sensor", CLAIM_A, label="reported", body=_with_excerpt(
        "evidence/2026-09-22-ratio/log.txt:3", "Median ratio 1.0017 across 2048 pixels."))
    issues = run(kb, rules.k11_reported)
    assert [i.code for i in issues] == ["K11"]
    assert "quotes the retired document" in messages(issues)


def test_k11_rejects_history_as_evidence_for_other_labels(kb):
    _reported_kb(kb)
    kb.add("F-0001", "sensor", CLAIM_A, evidence="[./history/topic-findings.md, evidence/2026-09-22-ratio/]")
    text = messages(run(kb, rules.k11_reported))
    assert "./history/topic-findings.md is a retired document, not evidence" in text
    assert "label the finding reported" in text


def test_k11_allows_history_in_prose_and_excerpts_of_other_labels(kb):
    _reported_kb(kb)
    kb.add("F-0001", "sensor", CLAIM_A, body="Detail: history/topic-findings.md A20 says so.\n\n" + _with_excerpt(
        "history/topic-findings.md:3", "A20 measured +0.397 on the odd pair."))
    assert run(kb, rules.k11_reported) == []


def test_k11_rejects_dependency_on_reported_finding(kb):
    _reported_kb(kb)
    kb.add("F-0001", "sensor", CLAIM_A, label="reported", evidence="[history/topic-findings.md]",
           body=_with_excerpt("history/topic-findings.md:3", "A20 measured +0.397 on the odd pair."))
    stamp = fingerprint(load_view(kb.cfg).findings[0])
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=f"depends_on:\n  F-0001: {stamp}\n")
    text = messages(run(kb, rules.k11_reported))
    assert "depends_on names F-0001, which is reported" in text
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", label="reported", evidence="[history/topic-findings.md]",
           extra=f"depends_on:\n  F-0001: {stamp}\n",
           body=_with_excerpt("history/topic-findings.md:1", "# retired topic"))
    assert run(kb, rules.k11_reported) == []


def test_k11_reported_checks_off_when_label_not_in_vocabulary(kb):
    kb.write("history/topic-findings.md", HISTORY_TEXT)
    kb.add("F-0001", "sensor", CLAIM_A, evidence="[history/topic-findings.md]")
    text = messages(run(kb, rules.k11_reported))
    assert "is a retired document, not evidence: cite what the claim rests on" in text
    assert "label the finding" not in text


def test_k11_history_dirs_are_configurable(kb):
    _reported_kb(kb, extra_kb='history_dirs = ["docs/old"]\n')
    kb.write("docs/old/a.md", HISTORY_TEXT)
    kb.add("F-0001", "sensor", CLAIM_A, evidence="[history/topic-findings.md, docs/old/a.md]")
    text = messages(run(kb, rules.k11_reported))
    assert "docs/old/a.md is a retired document" in text
    assert "history/topic-findings.md is a retired" not in text
