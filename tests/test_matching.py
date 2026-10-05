"""The matching primitives of SPEC §5.2.3 and CU `tag_sha256` (matching.py)."""

from __future__ import annotations

import hashlib
import random

import pytest

from kblam.matching import assertion_match, byte_to_char_map, find_all, line_starts, tag_sha256

# --- find_all ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "needle", "expected"), [
    ("aaaa", "aaa", [0, 1]),           # overlaps count: one character on from each start
    ("aaaa", "a", [0, 1, 2, 3]),
    ("abc", "abc", [0]),
    ("abc", "d", []),
    ("aaa", "aaaa", []),
    ("abc", "", []),
    ("", "", []),
    ("", "a", []),
    ("ééé", "éé", [0, 1]),   # indices are characters, not bytes
])
def test_find_all(text, needle, expected):
    assert find_all(text, needle) == expected


# --- line_starts -------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    ("", [0]),
    ("a", [0]),
    ("a\nb", [0, 2]),
    ("a\nb\n", [0, 2]),        # the final newline ends line 2; it does not open an empty line 3
    ("\n", [0]),
    ("\na", [0, 1]),
    ("a\n\nb", [0, 2, 3]),
    ("a\n\nb\n", [0, 2, 3]),
])
def test_line_starts(text, expected):
    assert line_starts(text) == expected


def k10_lines(text: str) -> list[str]:
    """K10's own view of a source's lines (rules._excerpt_problem)."""
    lines = text.split("\n")
    return lines[:len(lines) - (1 if text.endswith("\n") else 0)]


@pytest.mark.parametrize("text", ["", "a", "a\n", "a\nb", "a\nb\n", "\n", "a\n\nb\n"])
def test_line_starts_agrees_with_k10_line_count(text):
    assert len(line_starts(text)) == len(k10_lines(text))


@pytest.mark.parametrize(("text", "lines"), [
    ("alpha\nbeta\ngamma\n", (1, 1)),
    ("alpha\nbeta\ngamma\n", (1, 3)),
    ("alpha\nbeta\ngamma\n", (3, 3)),
    ("alpha\nbeta\ngamma", (3, 3)),
    ("alpha\nbeta\ngamma\n", (2, 2)),
    ("a\n\nb\n", (2, 2)),
])
def test_cited_range_slice_matches_k10(text, lines):
    """The window assertion_match bounds a match by is K10's cited revision of the range."""
    first, last = lines
    starts = line_starts(text)
    begin = starts[first - 1]
    end = starts[last] - 1 if last < len(starts) else len(text) - (1 if text.endswith("\n") else 0)
    assert text[begin:end] == "\n".join(k10_lines(text)[first - 1:last])


# --- assertion_match ---------------------------------------------------------------------------

ALPHA = "alpha\nbeta\ngamma\n"


def test_assertion_match_returns_occurrence_and_span():
    assert assertion_match(ALPHA, "beta", (2, 2)) == (1, (6, 10))


def test_assertion_match_spans_several_lines():
    assert assertion_match(ALPHA, "alpha\nbeta", (1, 2)) == (1, (0, 10))


def test_assertion_match_last_line_without_final_newline():
    assert assertion_match("a\nb", "b", (2, 2)) == (1, (2, 3))


def test_occurrence_counts_matches_over_the_whole_text():
    """Lines 63-65 hold the second of three copies; occurrence is 2, not 1."""
    text = "x\nx\nx\n"
    assert assertion_match(text, "x", (3, 3)) == (3, (4, 5))
    assert assertion_match(text, "x", (2, 2)) == (2, (2, 3))


def test_assertion_match_ambiguous_within_the_lines():
    assert assertion_match("x x\n", "x", (1, 1)) == \
        "the assertion text occurs 2 times within lines 1-1; narrow it to one"


def test_assertion_match_counts_overlapping_matches_as_ambiguous():
    assert assertion_match("aaa\n", "aa", (1, 1)) == \
        "the assertion text occurs 2 times within lines 1-1; narrow it to one"


def test_assertion_match_absent_within_the_lines_but_present_elsewhere():
    assert assertion_match("x\nfoo\n", "x", (2, 2)) == \
        "the assertion text does not occur within lines 2-2"


def test_assertion_match_rejects_a_match_spanning_the_line_boundary():
    """The cited line's newline is not part of it (K10 joins the cited lines without one)."""
    assert assertion_match("ab\ncd\n", "b\nc", (1, 1)) == \
        "the assertion text does not occur within lines 1-1"


def test_assertion_match_empty_needle_never_matches():
    assert assertion_match(ALPHA, "", (1, 3)) == \
        "the assertion text does not occur within lines 1-3"


@pytest.mark.parametrize("lines", [(0, 1), (2, 1), (3, 4), (1, 99)])
def test_assertion_match_lines_outside_the_text(lines):
    assert assertion_match(ALPHA, "beta", lines) == \
        "lines %d-%d are outside the source (it has 3 lines)" % lines


def test_assertion_match_on_an_empty_source_has_one_empty_line():
    assert assertion_match("", "x", (1, 1)) == \
        "the assertion text does not occur within lines 1-1"


# --- byte_to_char_map --------------------------------------------------------------------------


def reference_map(data: bytes) -> list[int]:
    """The docstring the slow way: decode every prefix, dropping a multibyte character cut by it."""
    out = []
    for i in range(len(data) + 1):
        prefix = data[:i]
        text = None
        for cut in range(4):
            try:
                text = prefix[:len(prefix) - cut].decode("utf-8")
                break
            except UnicodeDecodeError:
                continue
        assert text is not None, prefix
        out.append(len(text.replace("\r\n", "\n").replace("\r", "\n")))
    return out


BYTE_INPUTS = [
    b"",
    b"a",
    b"abc",
    b"a\nb",
    b"a\nb\n",
    b"a\rb",
    b"a\r",
    b"a\r\n",
    b"a\r\nb\r\n",
    b"\r\n\r\n",
    b"a\r\rb",
    b"\xc3\xa9",                       # 2-byte character
    b"a\xc3\xa9b",
    b"\xe2\x82\xac",                   # 3-byte character
    b"a\xf0\x9f\x98\x80b",             # 4-byte character
    b"\xc3\xa9\r\n\xe2\x82\xac\rx",
    b"a\r\n\xc3\xa9\nb",
    b"a\xc3",                          # cut inside a 2-byte character
    b"a\xe2\x82",                      # cut inside a 3-byte character
    b"\xf0\x9f\x98",                   # cut inside a 4-byte character
    b"\xc3\xa9\r\xc3",                 # a cut character after a lone CR
    ("line one\nline two\nééé\r\nlast\r\n" * 3).encode("utf-8"),
]


@pytest.mark.parametrize("data", BYTE_INPUTS)
def test_byte_to_char_map_matches_a_prefix_by_prefix_reference(data):
    assert byte_to_char_map(data) == reference_map(data)


def test_byte_to_char_map_matches_the_reference_on_generated_inputs():
    rng = random.Random(20260928)
    pieces = ["a", "\n", "\r", "\r\n", "z", "é", "€", "\U0001f600", "0"]
    for _ in range(300):
        data = "".join(rng.choice(pieces) for _ in range(rng.randrange(12))).encode("utf-8")
        data = data[:rng.randrange(len(data) + 1)]      # sometimes cut inside a character
        assert byte_to_char_map(data) == reference_map(data), data


def test_byte_to_char_map_length_and_anchors():
    m = byte_to_char_map(b"a\r\nb")
    assert len(m) == len(b"a\r\nb") + 1
    assert m[0] == 0 and m[4] == 3
    assert [byte_to_char_map(x)[-1] for x in (b"a\r", b"a\r\n")] == [2, 2]


def test_byte_to_char_map_multibyte_bytes_share_one_index():
    """A byte offset inside a character maps to the character's own index."""
    assert byte_to_char_map(b"\xc3\xa9") == [0, 0, 1]
    assert byte_to_char_map(b"a\xe2\x82\xacb") == [0, 1, 1, 1, 2, 3]


# --- tag_sha256 --------------------------------------------------------------------------------


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


FENCED = [
    "claim prose",
    "<!-- verbatim: a.md:1-2 -->",
    "```",
    "x",
    "```",
    "after",
]

BLOCKQUOTE = [
    "claim prose",
    "<!-- verbatim: b.md:3 -->",
    "> quoted line",
    "> another line",
    "after",
]

INDENTED = [
    "<!-- verbatim: c.md:7 -->",
    "  ```",
    "  indented",
    "  ```",
]


def test_tag_sha256_fenced_block_is_the_literal_slice():
    literal = b"<!-- verbatim: a.md:1-2 -->\n```\nx\n```"
    assert tag_sha256(FENCED, 1, 5) == sha(literal)


def test_tag_sha256_blockquote_is_the_literal_slice():
    literal = b"<!-- verbatim: b.md:3 -->\n> quoted line\n> another line"
    assert tag_sha256(BLOCKQUOTE, 1, 4) == sha(literal)


def test_tag_sha256_keeps_indentation():
    literal = b"<!-- verbatim: c.md:7 -->\n  ```\n  indented\n  ```"
    assert tag_sha256(INDENTED, 0, 4) == sha(literal)


def test_tag_sha256_adds_no_final_newline():
    literal = b"<!-- verbatim: a.md:1-2 -->\n```\nx\n```"
    assert tag_sha256(FENCED, 1, 5) != sha(literal + b"\n")
    assert tag_sha256(FENCED, 1, 5) != sha(b"\n" + literal)


def test_tag_sha256_of_a_whole_finding_body():
    assert tag_sha256(BLOCKQUOTE, 0, len(BLOCKQUOTE)) == sha("\n".join(BLOCKQUOTE).encode("utf-8"))
