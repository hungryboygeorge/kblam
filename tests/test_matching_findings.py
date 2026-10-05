"""The shared match result of K10 and K13 (SPEC §5.1.4 K13 paragraph 1: matching.finding_matches)."""

from __future__ import annotations

import hashlib

import pytest

from kblam import rules
from kblam.matching import finding_matches, tag_sha256
from kblam.sources import SourceReader
from kblam.view import load_view

from conftest import SOURCE_TEXT

CLAIM = "The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0)."
NOTES = "evidence/notes.txt"
NOTES_TEXT = "alpha one\nalpha two\nalpha three\n"
MISSING = "evidence/missing.txt"
LOG = "evidence/2026-09-22-ratio/log.txt"
DUMP = "evidence/2026-09-22-ratio/dump.bin"
# 'A', 'é' as two bytes, then a CRLF: a byte offset is not a character index in this file.
TRACE = "evidence/trace.txt"
TRACE_BYTES = b"A\xc3\xa9\r\nRow 102: bytes 0x3A 0x3B\r\n"

MALFORMED_MESSAGE = ("malformed verbatim tag; use <!-- verbatim: path:LINE -->, "
                     "<!-- verbatim: path:FIRST-LAST --> or <!-- verbatim: path:@0xOFFSET -->")
MISSING_MESSAGE = (f"verbatim source {MISSING} does not exist; cite an existing file relative to the "
                   f"repository root")


def quoted(tag: str, excerpt: str) -> str:
    """A verbatim tag and its blockquote, as a finding body (K10 strips the `> ` markers)."""
    block = "\n".join("> " + line for line in excerpt.split("\n"))
    return f"Detail follows.\n\n<!-- verbatim: {tag} -->\n{block}"


def view_reader_finding(kb, finding_id: str = "F-0001"):
    view = load_view(kb.cfg)
    finding = next(f for f in view.findings if f.file_id == finding_id)
    return view, SourceReader(kb.cfg, view), finding


def matches_for(kb, finding_id: str = "F-0001"):
    view, reader, finding = view_reader_finding(kb, finding_id)
    return finding_matches(view, reader, finding)


def notes_finding(kb, tag: str, excerpt: str, body: str | None = None) -> None:
    kb.write(NOTES, NOTES_TEXT)
    kb.add("F-0001", "sensor", CLAIM, body=body if body is not None else quoted(tag, excerpt))


# --- spans --------------------------------------------------------------------------------------


def test_two_occurrences_within_the_cited_lines_give_two_spans(kb):
    notes_finding(kb, f"{NOTES}:1-2", "alpha")
    matches = matches_for(kb)
    assert len(matches) == 1
    match = matches[0]
    assert match.spans == ((0, 5), (10, 15))    # "alpha two" starts at 10; the third is past line 2
    assert (match.problem, match.verified, match.binary) == (None, True, False)
    assert (match.path, match.key, match.range, match.is_offset) == (NOTES, NOTES, (1, 2), False)
    assert match.text == "alpha"
    assert match.source_sha256 == hashlib.sha256(NOTES_TEXT.encode("utf-8")).hexdigest()


def test_an_occurrence_outside_the_cited_lines_is_not_a_span(kb):
    notes_finding(kb, f"{NOTES}:1", "alpha")
    # "alpha" occurs three times in the file, but only line 1 is cited.
    assert matches_for(kb)[0].spans == ((0, 5),)


def test_offset_tag_in_a_crlf_multibyte_source_maps_to_the_right_span(kb):
    kb.write(TRACE, TRACE_BYTES)
    kb.add("F-0001", "sensor", CLAIM, body=quoted(f"{TRACE}:@0x5", "Row 102: bytes 0x3A 0x3B"))
    match = matches_for(kb)[0]
    # Byte 5 of the file is "R"; in the LF text "Aé\nRow 102: ..." that is character 3.
    assert match.spans == ((3, 27),)
    assert (match.problem, match.verified, match.binary) == (None, True, False)
    assert match.range == (5,)
    assert match.is_offset is True
    assert match.source_sha256 == hashlib.sha256(TRACE_BYTES).hexdigest()


def test_offset_tag_accepts_the_crlf_form_and_spans_the_crlf_as_one_character(kb):
    kb.write(TRACE, TRACE_BYTES)
    # The excerpt is LF; the file holds the same text with CRLF, which K10 accepts at an offset.
    kb.add("F-0001", "sensor", CLAIM, body=quoted(f"{TRACE}:@0x0", "Aé\nRow 102"))
    match = matches_for(kb)[0]
    assert (match.problem, match.verified) == (None, True)
    assert match.spans == ((0, 10),)


def test_a_path_paths_refuses_keeps_k10s_read_and_has_no_key(kb):
    # A `..` segment that stays inside the repository: rules._repo_path accepts it, paths refuses it.
    excerpt = "The two curve types agree to 0.1% on line 0.\nMedian ratio 1.0017 across 2048 pixels."
    tag = "evidence/2026-09-22-ratio/../2026-09-22-ratio/log.txt:2-3"
    kb.add("F-0001", "sensor", CLAIM, body=quoted(tag, excerpt))
    match = matches_for(kb)[0]
    assert (match.problem, match.verified) == (None, True)
    assert match.key is None                                  # K13 matches on the key, so it ignores this
    at = SOURCE_TEXT.index(excerpt)
    assert match.spans == ((at, at + len(excerpt)),)


# --- binary -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("rel", "data"), [
    (DUMP, b"\x00\x01binary\xff"),          # a NUL byte
    ("evidence/not-utf8.bin", b"\xff\xfe\xfd"),   # not UTF-8, no NUL
])
def test_binary_source_is_exempt_not_verified(kb, rel, data):
    kb.write(rel, data)
    kb.add("F-0001", "sensor", CLAIM, body=quoted(f"{rel}:@0x10", "not in the file at all"))
    match = matches_for(kb)[0]
    assert (match.problem, match.verified, match.binary) == (None, False, True)
    assert match.spans == ()
    assert match.source_sha256 == hashlib.sha256(data).hexdigest()


# --- K10's problems -----------------------------------------------------------------------------


@pytest.mark.parametrize(("body", "problems"), [
    (f"<!-- verbatim: {NOTES} -->\n\n> alpha one", 1),                  # malformed tag
    (f"<!-- verbatim: {NOTES}:1 -->\n\nnot a block at all", 1),         # no block after the tag
    (f"<!-- verbatim: {NOTES}:1 -->\n```\nalpha one", 1),               # fenced block never closed
    (f"<!-- verbatim: {NOTES}:1 -->\n>", 1),                            # empty excerpt
    ("<!-- verbatim: ../escape.txt:1 -->\n> alpha one", 1),             # outside the repository root
    (f"<!-- verbatim: {MISSING}:1 -->\n> alpha one", 1),                # no such file
    (quoted(f"{NOTES}:1", "alpha zer"), 1),                             # paraphrase
    (quoted(f"{NOTES}:3", "alpha one"), 1),                             # wrong range; occurs at line 1
    (quoted(f"{NOTES}:4-9", "alpha"), 1),                               # range past the end of the file
    (quoted(f"{NOTES}:@0x1", "alpha"), 1),                              # wrong byte offset
    (quoted(f"{NOTES}:@0xFFFF", "alpha"), 1),                           # offset past the end
    (quoted(f"{DUMP}:@0x10", "not in the file at all"), 0),             # binary: no problem at all
    (quoted(f"{NOTES}:1-2", "alpha") + "\n" + quoted(f"{NOTES}:1", "alpha zer"), 1),   # one good, one bad
], ids=["malformed", "no-block", "unclosed", "empty", "outside", "missing", "paraphrase",
        "wrong-range", "past-end", "bad-offset", "offset-past-end", "binary", "two-tags"])
def test_each_problem_is_the_message_k10_reports(kb, body, problems):
    notes_finding(kb, NOTES, "alpha one", body=body)
    view, reader, finding = view_reader_finding(kb)
    found = [m.problem for m in finding_matches(view, reader, finding) if m.problem is not None]
    assert len(found) == problems
    assert found == [i.message for i in rules.k10_verbatim(view)]


def test_the_moved_messages_are_pinned(kb):
    notes_finding(kb, NOTES, "alpha one")
    assert matches_for(kb)[0].problem == MALFORMED_MESSAGE
    kb.add("F-0001", "sensor", CLAIM, body=quoted(f"{MISSING}:1", "x"))
    assert matches_for(kb)[0].problem == MISSING_MESSAGE


# --- ordinals and tag_sha256 --------------------------------------------------------------------


def test_ordinals_count_malformed_tags(kb):
    body = f"<!-- verbatim: {NOTES} -->\n\n> alpha one\n\n" + quoted(f"{NOTES}:2", "alpha two")
    notes_finding(kb, NOTES, "alpha one", body=body)
    matches = matches_for(kb)
    assert [m.ordinal for m in matches] == [1, 2]
    assert [m.text for m in matches] == [None, "alpha two"]
    first = matches[0]
    assert (first.path, first.key, first.range, first.is_offset) == ("", None, (), False)
    assert (first.problem, first.verified, first.binary, first.spans) == (MALFORMED_MESSAGE, False,
                                                                          False, ())
    assert first.end == first.start + 1


def test_tag_sha256_covers_the_tag_and_its_block(kb):
    tag_line = f"<!-- verbatim: {NOTES}:1 -->"
    notes_finding(kb, NOTES, "alpha one",
                  body=f"Detail follows.\n\n{tag_line}\n> alpha one\n> alpha two")
    match = matches_for(kb)[0]
    assert match.text == "alpha one\nalpha two"
    assert match.tag_sha256 == hashlib.sha256(
        "\n".join([tag_line, "> alpha one", "> alpha two"]).encode("utf-8")).hexdigest()
    view, _, finding = view_reader_finding(kb)
    assert match.tag_sha256 == tag_sha256(finding.body_lines, match.start, match.end)


# --- the cache ----------------------------------------------------------------------------------


def test_a_second_call_for_the_same_revision_is_a_cache_hit(kb):
    notes_finding(kb, f"{NOTES}:1-2", "alpha")
    view, reader, finding = view_reader_finding(kb)
    first = finding_matches(view, reader, finding)
    reads = dict(reader.reads)
    assert finding_matches(view, reader, finding) is first
    assert dict(reader.reads) == reads


def test_a_new_revision_of_the_finding_is_matched_again(kb):
    notes_finding(kb, f"{NOTES}:1-2", "alpha")
    view, reader, finding = view_reader_finding(kb)
    first = finding_matches(view, reader, finding)
    kb.add("F-0001", "sensor", CLAIM, body=quoted(f"{NOTES}:3", "alpha"))
    view, reader, finding = view_reader_finding(kb)
    second = finding_matches(view, reader, finding)
    assert second is not first
    assert (second[0].range, second[0].spans) == ((3, 3), ((20, 25),))


# --- one read per source per validation ---------------------------------------------------------


def test_validate_reads_each_source_once(kb, monkeypatch):
    made = []

    class Recording(SourceReader):
        def __init__(self, cfg, view):
            super().__init__(cfg, view)
            made.append(self)

    monkeypatch.setattr(rules, "SourceReader", Recording)
    notes_finding(kb, f"{NOTES}:1-2", "alpha")
    kb.add("F-0002", "motor", "The motor warm-up drift settles within 90 seconds of power-on.",
           topic="motor", body=quoted(f"{NOTES}:3", "alpha"))
    rules.validate(load_view(kb.cfg))
    assert len(made) == 1
    reads = made[0].reads
    assert reads[("working", NOTES)] == 1
    assert all(count == 1 for count in reads.values())
