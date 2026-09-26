"""M6.10's validator changes (SPEC §5): [kb] topics in K1, evidence_roots in K2, K5's own terms, K10's hex
excerpts and K12, and the [kb] keys the kblam.toml template gains for them (SPEC §9). A passing and at
least one failing case for each; the scope symbols are in test_scope_symbols.py."""

from __future__ import annotations

import pytest

from kblam import rules
from kblam.config import load_config
from kblam.view import load_view

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, TEMPLATE_TOML, finding_text
from test_rules import CLAIM_A, CLAIM_B, HISTORY_TEXT, _with_excerpt, messages, run

# An ELF-like header: NUL bytes throughout, so K10 reads the file as binary.
FRAME = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8 + b"\x02\x00\x3e\x00"
FRAME_PATH = "evidence/2026-09-22-ratio/frame.bin"
DUMP_PATH = "evidence/2026-09-22-ratio/dump.bin"   # the fixture's b"\x00\x01binary\xff"
LOG_PATH = "evidence/2026-09-22-ratio/log.txt"     # the fixture's SOURCE_TEXT


def configure(kb, kb_lines: str = "", *, reported: bool = False) -> None:
    """The fixture's kblam.toml with `kb_lines` added to its [kb] table, and "reported" in the labels
    when `reported`."""
    toml = KBLAM_TOML + kb_lines
    if reported:
        toml = toml.replace('"unknown"]', '"unknown", "reported"]')
    kb.write("kblam.toml", toml + NO_EMBEDDINGS + PROMPT_TOML)


# --- K1: [kb] topics ------------------------------------------------------------------------------


def test_k1_accepts_any_topic_while_topics_is_empty(kb):
    kb.add("F-0001", "sensor", CLAIM_A, topic="sensor-lab")
    assert run(kb, rules.k1_schema) == []


def test_k1_accepts_a_topic_in_the_vocabulary(kb):
    configure(kb, 'topics = ["calibration", "motor"]\n')
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    assert run(kb, rules.k1_schema) == []


def test_k1_rejects_a_topic_outside_the_vocabulary(kb):
    configure(kb, 'topics = ["calibration", "motor"]\n')
    kb.add("F-0001", "handoff", CLAIM_A, topic="wp-7")
    issues = run(kb, rules.k1_schema)
    assert [(i.code, i.line) for i in issues] == [("K1", 4)]
    assert issues[0].message == "topic 'wp-7' is not in the vocabulary; use one of: calibration, motor"


def test_k1_reports_a_topic_outside_the_vocabulary_and_its_folder_separately(kb):
    configure(kb, 'topics = ["calibration", "motor"]\n')
    kb.write("findings/calibration/F-0001-sensor.md", finding_text("F-0001", CLAIM_A, topic="wp-7"))
    kb.reindex()
    text = messages(run(kb, rules.k1_schema))
    assert "topic 'wp-7' is not in the vocabulary" in text
    assert "topic 'wp-7' does not match the folder 'calibration'" in text


# --- K2: [kb] evidence_roots ----------------------------------------------------------------------


def test_k2_accepts_evidence_under_a_root_however_the_path_is_written(kb):
    kb.add("F-0001", "sensor", CLAIM_A,
           evidence="[./evidence/2026-09-22-ratio/log.txt, 'evidence\\2026-09-22-ratio\\README.md']")
    assert run(kb, rules.k2_references) == []


def test_k2_accepts_evidence_under_every_configured_root(kb):
    configure(kb, 'evidence_roots = ["evidence", "./bench-runs/", "captures/2026"]\n')
    kb.write("bench-runs/run-1/trace.csv", "t,v\n")
    kb.write("captures/2026/a.bin", b"\x00")
    kb.add("F-0001", "sensor", CLAIM_A,
           evidence="[evidence/2026-09-22-ratio/, bench-runs/run-1/trace.csv, captures/2026/a.bin]")
    assert run(kb, rules.k2_references) == []


def test_k2_rejects_evidence_outside_the_roots_and_names_them(kb):
    kb.write("src/fit.py", "print(1)\n")
    kb.add("F-0001", "sensor", CLAIM_A, evidence="[evidence/2026-09-22-ratio/, src/fit.py]")
    issues = run(kb, rules.k2_references)
    assert [(i.code, i.line) for i in issues] == [("K2", 7)]
    assert issues[0].message == ("evidence path src/fit.py is not under an evidence root (evidence/); cite "
                                 "evidence that lies there, relative to the repository root")


def test_k2_matches_roots_by_whole_segments(kb):
    configure(kb, 'evidence_roots = ["evidence", "bench"]\n')
    kb.write("evidence-old/run.txt", "x\n")
    kb.write("bench-runs/run.txt", "x\n")
    kb.write("src/fit.py", "x\n")
    kb.add("F-0001", "sensor", CLAIM_A,
           evidence="[evidence-old/run.txt, bench-runs/run.txt, evidence/../src/fit.py]")
    text = messages(run(kb, rules.k2_references))
    assert text.count("is not under an evidence root (evidence/, bench/)") == 3


def test_k2_keeps_its_other_checks_for_paths_under_a_root(kb):
    kb.add("F-0001", "sensor", CLAIM_A, evidence="[evidence/nope/, ../outside.txt]",
           extra="depends_on:\n  F-0001: abc\n")
    text = messages(run(kb, rules.k2_references))
    assert "evidence path evidence/nope/ does not exist" in text
    assert "../outside.txt points outside the repository root" in text
    assert "a finding cannot depend on itself" in text
    assert "not under an evidence root" not in text  # one issue per path


def test_k2_empty_evidence_message_names_the_roots(kb):
    configure(kb, 'evidence_roots = ["evidence", "bench-runs"]\n')
    kb.add("F-0001", "sensor", CLAIM_A, evidence="[]")
    assert "at least one repo-relative path under an evidence root (evidence/, bench-runs/)" in messages(
        run(kb, rules.k2_references))


def test_k2_accepts_history_as_evidence_of_a_reported_finding(kb):
    configure(kb, reported=True)
    kb.write("history/topic-findings.md", HISTORY_TEXT)
    kb.add("F-0001", "sensor", CLAIM_A, label="reported",
           evidence="[evidence/2026-09-22-ratio/, ./history/topic-findings.md]",
           body=_with_excerpt("history/topic-findings.md:3", "A20 measured +0.397 on the odd pair."))
    assert run(kb, rules.k2_references) == []
    assert kb.issues() == []


def test_k2_rejects_history_as_evidence_of_any_other_finding(kb):
    configure(kb, reported=True)
    kb.write("history/topic-findings.md", HISTORY_TEXT)
    kb.add("F-0001", "sensor", CLAIM_A, evidence="[history/topic-findings.md]")
    assert "history/topic-findings.md is not under an evidence root (evidence/);" in messages(
        run(kb, rules.k2_references))
    assert "is a retired document" in messages(run(kb, rules.k11_reported))


def test_k2_names_the_history_folders_to_a_reported_finding(kb):
    configure(kb, reported=True)
    kb.write("src/fit.py", "x\n")
    kb.add("F-0001", "sensor", CLAIM_A, label="reported", evidence="[src/fit.py]")
    assert ("evidence path src/fit.py is not under an evidence root (evidence/) or a history folder "
            "(history/); cite evidence") in messages(run(kb, rules.k2_references))


def test_k2_history_exception_is_off_with_the_reported_label(kb):
    configure(kb, 'reported_label = ""\n', reported=True)
    kb.write("history/topic-findings.md", HISTORY_TEXT)
    kb.add("F-0001", "sensor", CLAIM_A, label="reported", evidence="[history/topic-findings.md]")
    text = messages(run(kb, rules.k2_references))
    assert "history/topic-findings.md is not under an evidence root (evidence/);" in text
    assert "history folder" not in text


# --- K5: [kb] history_id_terms --------------------------------------------------------------------


K5_TERMS = 'history_id_terms = ["Wrong", "replaces", "instead of", "corrected"]\n'


def k5_terms(issues) -> list[tuple[str, str]]:
    """(other finding's ID, term) for each K5 issue."""
    return [(i.message.split()[0], i.message.split('"')[1]) for i in issues]


def test_k5_uses_the_k4_terms_while_history_id_terms_is_absent(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor",
           body="This replaces F-0001. F-0001 was wrong on the gains.")
    assert k5_terms(run(kb, rules.k5_id_near_history)) == [("F-0001", "was wrong")]


def test_k5_uses_its_own_terms_when_set(kb):
    configure(kb, K5_TERMS)
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor",
           body="This replaces F-0001 for the MX-200.\n\nF-0001 is superseded.")
    issues = run(kb, rules.k5_id_near_history)
    assert k5_terms(issues) == [("F-0001", "replaces")]  # "supersed" is K4's, not K5's, here
    assert issues[0].line == 13
    assert "Do not describe another finding as wrong, changed or replaced" in issues[0].message
    assert "rewrite it in place (kblam edit F-0001)" in issues[0].message
    assert [i.message.split('"')[1] for i in run(kb, rules.k4_history_language)] == ["supersed"]


def test_k5_own_terms_match_as_k4_terms_do(kb):
    configure(kb, K5_TERMS)
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body=(
        "F-0001 lists the Wrongly scaled gain.\n"      # case-insensitive, and a term is a word start
        "Use F-0001 INSTEAD\n   OF the table.\n"        # whitespace runs between a term's words
        "F-0001 holds uncorrected values."))           # "corrected" inside a word does not match
    found = sorted(k5_terms(run(kb, rules.k5_id_near_history)))
    assert found == [("F-0001", "instead of"), ("F-0001", "wrong")]


def test_k5_passes_its_own_terms_away_from_another_id(kb):
    configure(kb, K5_TERMS)
    kb.add("F-0001", "sensor", CLAIM_A)
    filler = " ".join(["word"] * 20)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor",
           body=f"See F-0001. {filler} The wrong connector replaces nothing.")
    assert run(kb, rules.k5_id_near_history) == []


def test_k5_reports_once_per_id_and_term_in_title_and_body(kb):
    configure(kb, K5_TERMS)
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", title="Motor gain replaces F-0001",
           body="This replaces F-0001. It replaces F-0001 on MX-100 too.")
    assert k5_terms(run(kb, rules.k5_id_near_history)) == [("F-0001", "replaces")]


def test_k5_exempts_a_passing_excerpt_from_its_own_terms(kb):
    configure(kb, K5_TERMS)
    kb.write("evidence/2026-09-22-ratio/notes.txt", "Rev B replaces F-0001 wiring.\n")
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body=_with_excerpt(
        "evidence/2026-09-22-ratio/notes.txt:1", "Rev B replaces F-0001 wiring."))
    assert run(kb, rules.k5_id_near_history) == []
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body=_with_excerpt(
        "evidence/2026-09-22-ratio/notes.txt:1", "Rev C replaces F-0001 wiring."))
    assert k5_terms(run(kb, rules.k5_id_near_history)) == [("F-0001", "replaces")]


def test_k5_with_an_empty_term_list_matches_nothing(kb):
    configure(kb, "history_id_terms = []\n")
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body="F-0001 was wrong on the gains.")
    assert run(kb, rules.k5_id_near_history) == []
    assert len(run(kb, rules.k4_history_language)) == 1  # K4 keeps its own terms


# --- K10: hex excerpts ----------------------------------------------------------------------------


def _hex(tag: str, block: str) -> str:
    """A hex excerpt: `<!-- verbatim: TAG hex -->` and a fenced block."""
    return _with_excerpt(f"{tag} hex", block, quote=False)


def test_verbatim_tag_accepts_the_hex_form():
    tag = rules.VERBATIM_RE.match("<!-- verbatim: evidence/a.bin:@0x1F0 hex -->")
    assert tag and tag.group("path") == "evidence/a.bin" and tag.group("offset") == "1F0" and tag.group("hex")
    assert not rules.VERBATIM_RE.match("<!-- verbatim: evidence/a.bin:@0x1F0 -->").group("hex")
    for line in ("<!-- verbatim: a.bin:@0x1F0hex -->", "<!-- verbatim: a.bin:12 hex -->",
                 "<!-- verbatim: a.bin:@0x1F0 hexdump -->"):
        assert rules.VERBATIM_RE.match(line) is None, line


def test_k10_accepts_hex_bytes_of_a_binary_source_with_nul_bytes(kb):
    kb.write(FRAME_PATH, FRAME)
    kb.add("F-0001", "header", CLAIM_A,
           body=_hex(f"{FRAME_PATH}:@0x0", "7f 45 4c 46 02 01 01 00\n00 00 00 00"))
    kb.add("F-0002", "machine", CLAIM_B, topic="motor",
           body=_hex(f"{FRAME_PATH}:@0x10", "0x02 0x00 0x3E 0x00"))
    kb.add("F-0003", "tail", "The dump ends in 0xFF.", topic="dump", body=_hex(f"{DUMP_PATH}:@0x7", "79FF"))
    assert run(kb, rules.k10_verbatim) == []
    assert kb.issues() == []


def test_k10_accepts_hex_bytes_of_a_text_source(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_hex(f"{LOG_PATH}:@0x5", "6f6e6520 6f66"))  # "one of"
    assert run(kb, rules.k10_verbatim) == []


def test_k10_rejects_hex_bytes_that_differ_and_says_where(kb):
    kb.write(FRAME_PATH, FRAME)
    kb.add("F-0001", "header", CLAIM_A, body=_hex(f"{FRAME_PATH}:@0x0", "7f 45 4c 46 01"))
    issues = run(kb, rules.k10_verbatim)
    assert [(i.code, i.line) for i in issues] == [("K10", 15)]
    assert issues[0].message == (f"the hex excerpt differs from {FRAME_PATH} at byte offset 0x4: it has 01 "
                                 f"where the source has 02; copy the bytes from a hex dump of the source, "
                                 f"never retype them")


def test_k10_rejects_hex_bytes_at_the_wrong_offset_and_says_where_they_are(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_hex(f"{DUMP_PATH}:@0x0", "62 69 6e 61 72 79"))  # "binary"
    text = messages(run(kb, rules.k10_verbatim))
    assert f"differs from {DUMP_PATH} at byte offset 0x0: it has 62 where the source has 00" in text
    assert "these bytes occur at byte offset 0x2, so check the cited offset" in text


def test_k10_rejects_hex_bytes_past_the_end_of_the_source(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_hex(f"{DUMP_PATH}:@0x8", "ff 00"))
    assert (f"the hex excerpt's 2 bytes from byte offset 0x8 run past the end of {DUMP_PATH}, which has 9 "
            f"bytes") in messages(run(kb, rules.k10_verbatim))


@pytest.mark.parametrize("block, problem", [
    ("00 1", "the hex excerpt holds 1, an odd number of hex digits; write every byte as two digits"),
    ("0x0 0x01", "the hex excerpt holds 0x0, an odd number of hex digits"),
    ("00 zz", "the hex excerpt holds 'zz', which is not hex digits"),
    ("0x00, 0x01", "the hex excerpt holds '0x00,', which is not hex digits"),
    ("00000000: 0001 6269", "the hex excerpt holds '00000000:', which is not hex digits"),
    ("0x", "the hex excerpt holds '0x', which is not hex digits"),
    ("", "the hex excerpt is empty"),
    ("  \n  ", "the hex excerpt is empty"),
])
def test_k10_rejects_a_malformed_hex_block(kb, block, problem):
    kb.add("F-0001", "sensor", CLAIM_A, body=_hex(f"{DUMP_PATH}:@0x0", block))
    issues = run(kb, rules.k10_verbatim)
    assert [i.code for i in issues] == ["K10"] and problem in issues[0].message


def test_k10_rejects_a_hex_excerpt_that_is_not_fenced(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_with_excerpt(f"{DUMP_PATH}:@0x0 hex", "00 01"))
    assert "a hex excerpt is a fenced code block of hex byte pairs, not a blockquote" in messages(
        run(kb, rules.k10_verbatim))
    kb.add("F-0001", "sensor", CLAIM_A, body=f"<!-- verbatim: {DUMP_PATH}:@0x0 hex -->\n00 01")
    assert ("a hex verbatim tag must be directly followed (next line) by a fenced code block of hex byte "
            "pairs") in messages(run(kb, rules.k10_verbatim))


def test_k10_malformed_tag_message_names_the_hex_form(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=_with_excerpt(f"{DUMP_PATH}:3 hex", "00", quote=False))
    text = messages(run(kb, rules.k10_verbatim))
    assert text.startswith("malformed verbatim tag")
    assert "<!-- verbatim: path:@0xOFFSET hex -->" in text


def test_k10_a_matching_hex_excerpt_counts_as_verified(kb):
    """Like a matching text excerpt, and unlike a text excerpt of a binary source, so K4 and K5 skip it."""
    kb.write(FRAME_PATH, FRAME)
    kb.add("F-0001", "header", CLAIM_A, body=_hex(f"{FRAME_PATH}:@0x0", "7f 45 4c 46"))
    kb.add("F-0002", "wrong", CLAIM_B, topic="motor", body=_hex(f"{FRAME_PATH}:@0x0", "7f 45 4c 47"))
    kb.add("F-0003", "text", "The frame starts with 0x7F.", topic="dump",
           body=_with_excerpt(f"{FRAME_PATH}:@0x1", "ELF"))  # text of a binary source: not checked
    view = load_view(kb.cfg)
    verified = {f.file_id: [(e.problem is None, e.verified) for e in rules.verbatim_excerpts(view, f)]
                for f in view.findings}
    assert verified == {"F-0001": [(True, True)], "F-0002": [(False, False)], "F-0003": [(True, False)]}


# --- K12: [kb] verbatim_blockquotes ---------------------------------------------------------------


UNTAGGED = "Detail follows.\n\n> The datasheet says the gain is fixed."


def test_k12_is_off_by_default(kb):
    kb.add("F-0001", "sensor", CLAIM_A, body=UNTAGGED)
    assert run(kb, rules.k12_blockquotes) == []
    assert kb.issues() == []


def test_k12_accepts_tagged_blockquotes_and_quotes_that_are_not_blockquotes(kb):
    configure(kb, "verbatim_blockquotes = true\n")
    kb.add("F-0001", "sensor", CLAIM_A, body=(
        _with_excerpt(f"{LOG_PATH}:2-3", "The two curve types agree to 0.1% on line 0.\n"
                                         "Median ratio 1.0017 across 2048 pixels.")
        + "\n\n```\n> a prompt in a code block\n```\n\n    > an indented code block"))
    kb.add("F-0002", "dump", CLAIM_B, topic="motor", body=_hex(f"{DUMP_PATH}:@0x0", "00 01"))
    assert run(kb, rules.k12_blockquotes) == []
    assert kb.issues() == []


def test_k12_rejects_an_untagged_blockquote(kb):
    configure(kb, "verbatim_blockquotes = true\n")
    kb.add("F-0001", "sensor", CLAIM_A, body=UNTAGGED + "\n> A second line of it.")
    issues = run(kb, rules.k12_blockquotes)
    assert [(i.code, i.line) for i in issues] == [("K12", 15)]  # one issue for the run, at its first line
    assert "put <!-- verbatim: path:LINES --> on the line directly before it" in issues[0].message
    assert "write the paraphrase as prose" in issues[0].message


def test_k12_finds_blockquotes_up_to_three_spaces_in_and_outside_fences(kb):
    configure(kb, "verbatim_blockquotes = true\n")
    kb.add("F-0001", "sensor", CLAIM_A, body=(
        "   > three spaces is still a blockquote\n"
        "\n"
        f"<!-- verbatim: {LOG_PATH}:1 -->\n"
        "> Line one of the capture log.\n"
        "\n"
        f"<!-- verbatim: {LOG_PATH}:1 -->\n"
        "\n"
        "> a blank line between tag and quote\n"
        "~~~\n"
        "unclosed fence\n"
        "> inside it\n"))
    assert [i.line for i in run(kb, rules.k12_blockquotes)] == [13, 20]


def test_k12_leaves_a_blockquote_after_a_malformed_tag_to_k10(kb):
    configure(kb, "verbatim_blockquotes = true\n")
    kb.add("F-0001", "sensor", CLAIM_A,
           body="<!-- verbatim: log.txt line 1 -->\n> Line one of the capture log.")
    assert run(kb, rules.k12_blockquotes) == []
    assert [i.code for i in kb.issues()] == ["K10"]


def test_k12_issues_sort_by_rule_number(kb):
    configure(kb, "verbatim_blockquotes = true\n")
    kb.add("F-0001", "sensor", CLAIM_A, body="Detail.\n\n> The old figure was wrong.")
    assert [(i.line, i.code) for i in kb.issues()] == [(15, "K4"), (15, "K12")]


# --- the kblam.toml template ----------------------------------------------------------------------


SPEC_K5_TERMS = ("wrong", "incorrect", "mistaken", "erroneous", "corrects", "corrected", "correction",
                 "replaces", "replaced", "instead of", "contradicts", "contradicted", "outdated", "obsolete",
                 "invalid", "revises", "revised")


def test_template_carries_the_m610_kb_keys_with_the_spec_values(tmp_path):
    (tmp_path / "kblam.toml").write_text(TEMPLATE_TOML, encoding="utf-8")
    cfg = load_config(root=tmp_path)
    assert cfg.topics == () and cfg.adjudicators == ()
    assert cfg.history_id_terms == SPEC_K5_TERMS
    assert cfg.verbatim_blockquotes is True
    assert (cfg.scope_separator, cfg.scope_wildcard) == ("/", "any")


def test_a_kb_on_the_template_turns_on_k12_and_k5s_own_terms(kb):
    kb.write("kblam.toml", TEMPLATE_TOML)
    kb.add("F-0001", "sensor", CLAIM_A, scope="[any]",
           body=_with_excerpt(f"{LOG_PATH}:3", "Median ratio 1.0017 across 2048 pixels."))
    assert kb.issues() == []
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", scope="[any]",
           body="This replaces F-0001 for the new board.\n\n> The old board used another gain.")
    assert sorted(i.code for i in kb.issues()) == ["K12", "K5"]
