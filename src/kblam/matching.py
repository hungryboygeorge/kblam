"""Text matching shared by assertions, K10 and K13 (SPEC §5.1.3 Assertion, CU tag_sha256; §5.1.4 K13).

Text is the source decoded as UTF-8 with line endings normalised to LF (finding.normalise_newlines),
nothing else normalised. Spans are half-open character ranges [start, end) in that text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from kblam import paths
from kblam.finding import normalise_newlines

# A byte that ends a run of plain ASCII: a CR (it may pair with a following LF) or the first byte of
# a multibyte character (byte_to_char_map).
_ODD_BYTE = re.compile(rb"[\r\x80-\xff]")

# The K10 messages for a tag whose block or source K10 never reaches; the rest of K10's messages come
# from rules._excerpt_after and rules._excerpt_problem, which build them for this match.
MESSAGE_MALFORMED_TAG = ("malformed verbatim tag; use <!-- verbatim: path:LINE -->, "
                         "<!-- verbatim: path:FIRST-LAST --> or <!-- verbatim: path:@0xOFFSET -->")
MESSAGE_EMPTY_EXCERPT = "the verbatim excerpt is empty"
MESSAGE_NO_SOURCE = ("verbatim source {source} does not exist; cite an existing file relative to the "
                     "repository root")


def find_all(text: str, needle: str) -> list[int]:
    """Every start index of `needle` in `text`, advancing one character after each start, so overlapping
    matches count ("aaa" in "aaaa" gives [0, 1]). An empty needle gives []."""
    if not needle:
        return []
    found: list[int] = []
    i = text.find(needle)
    while i >= 0:
        found.append(i)
        i = text.find(needle, i + 1)
    return found


def line_starts(text: str) -> list[int]:
    """The character index at which each 1-based line starts: line_starts(t)[n - 1] is line n's start."""
    starts = [0]
    i = text.find("\n")
    while i >= 0:
        starts.append(i + 1)
        i = text.find("\n", i + 1)
    if len(starts) > 1 and starts[-1] == len(text):
        starts.pop()  # the newline ends the last line; K10 counts "a\n" as one line, not two
    return starts


def assertion_match(text: str, needle: str, lines: tuple[int, int]) -> tuple[int, tuple[int, int]] | str:
    """Locate an assertion (SPEC §5.1.3): (occurrence, span), or a refusal message.

    `lines` = (A, B) are inclusive 1-based lines of `text`; a match counts only if it lies wholly within
    them (start at or after line A's start; end at or before the end of line B, its newline excluded).
    Exactly one such match: occurrence is its 1-based index among ALL matches of find_all(text, needle),
    span is (start, start + len(needle)). None within the lines, or more than one, gives a message that
    says which (e.g. "the assertion text does not occur within lines 63-65", "the assertion text occurs
    2 times within lines 63-65; narrow it to one"), or that the lines are outside the text.
    """
    first, last = lines
    starts = line_starts(text)
    if first < 1 or last < first or last > len(starts):
        return f"lines {first}-{last} are outside the source (it has {len(starts)} lines)"
    where = f"lines {first}-{last}"
    begin = starts[first - 1]
    end = starts[last] - 1 if last < len(starts) else len(text) - (1 if text.endswith("\n") else 0)
    matches = find_all(text, needle)
    within = [start for start in matches if start >= begin and start + len(needle) <= end]
    if not within:
        return f"the assertion text does not occur within {where}"
    if len(within) > 1:
        return f"the assertion text occurs {len(within)} times within {where}; narrow it to one"
    start = within[0]
    return matches.index(start) + 1, (start, start + len(needle))


def byte_to_char_map(data: bytes) -> list[int]:
    """m, with len(m) == len(data) + 1: m[i] is the length of the LF-normalised text decoded from data[:i]
    (a partial multibyte character at the cut is dropped), so a raw byte offset maps to a text index.
    CRLF maps both bytes to one LF; a lone CR is one LF. `data` is valid UTF-8 (callers check)."""
    size = len(data)
    m = [0] * (size + 1)
    n = 0        # characters in the LF-normalised text of the decoded prefix
    cr = False   # the prefix ends with a CR, already counted as one LF
    i = 0
    while i < size:
        byte = data[i]
        if byte < 0x80 and not (cr and byte == 0x0A):
            if byte == 0x0D:
                n += 1
                cr = True
                i += 1
                m[i] = n
            else:
                cr = False
                # a run of plain ASCII, one character per byte. It holds no CR (the run stops at
                # one), so no CRLF pair and no special case for LF; filled in one go, not per byte.
                odd = _ODD_BYTE.search(data, i)
                j = odd.start() if odd else size
                m[i + 1:j + 1] = range(n + 1, n + (j - i) + 1)
                n += j - i
                i = j
            continue
        if byte == 0x0A:
            # the LF of a CRLF pair: already counted, as the CR's own LF
            cr = False
            i += 1
            m[i] = n
            continue
        width = 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
        if i + width > size:
            # the sequence is cut off by the prefix: drop it, and so for every longer prefix
            m[i + 1:] = [n] * (size - i)
            break
        for k in range(i + 1, i + width):
            m[k] = n
        i += width
        n += 1
        cr = False
        m[i] = n
    return m


def tag_sha256(body_lines: list[str], start: int, end: int) -> str:
    """sha256 hex of UTF-8("\\n".join(body_lines[start:end])): the verbatim tag's line through the end of
    its block (end exclusive), fences or blockquote markers and indentation included, no final newline
    (SPEC §5.1.3 CU citation)."""
    return hashlib.sha256("\n".join(body_lines[start:end]).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExcerptMatch:
    """K10's and K13's one match result for one verbatim tag of a finding (SPEC §5.1.4 K13 paragraph 1)."""
    ordinal: int                  # 1-based position among the finding's verbatim tags (malformed ones count)
    start: int                    # body-line index of the tag
    end: int                      # body-line index just past the block
    path: str                     # the tag's source path as written ("" for a malformed tag)
    key: str | None               # paths.canonical_key of path; None when refused or malformed
    is_offset: bool               # a :@0x tag
    range: tuple[int, ...]        # (first, last) lines, or (offset,); () for a malformed tag
    text: str | None              # the excerpt text (blockquote markers stripped, LF), or None
    source_sha256: str | None     # sha256 of the raw source bytes matched against
    spans: tuple[tuple[int, int], ...]  # every half-open span in the source's LF text within the range
    problem: str | None           # the K10 failure message, byte-identical to K10's, or None
    verified: bool                # a verified text match (problem is None and the source is text)
    binary: bool                  # binary-exempt: problem is None, source binary, not verified
    tag_sha256: str               # tag_sha256(body_lines, start, end)


def finding_matches(view, reader, finding) -> list[ExcerptMatch]:
    """Every verbatim excerpt of `finding` (a finding.Finding with meta), in body order, read through
    `reader` (sources.SourceReader), cached in reader.matches per finding revision (path, raw sha256).
    For a line tag, spans are all find_all matches of the excerpt text lying wholly within the cited
    lines; for an offset tag, the raw-byte match at the offset mapped through byte_to_char_map."""
    revision = (finding.path, hashlib.sha256(finding.raw).hexdigest())
    if revision in reader.matches:
        return reader.matches[revision]
    matches = [_excerpt_match(view, reader, finding, ordinal, index)
               for ordinal, index in enumerate(_tag_lines(finding.body_lines), start=1)]
    reader.matches[revision] = matches
    return matches


def _rules():
    """rules' K10 parse and verdict helpers, imported at call time: rules imports this module, and
    K10's messages and verdicts stay in one place (rules._excerpt_after, rules._excerpt_problem)
    rather than in a copy here that could drift from them."""
    from kblam import rules
    return rules


def _tag_lines(body: list[str]) -> list[int]:
    """The body-line indices of the verbatim tags, in body order. A malformed tag counts: K10 reports
    it, so it holds an ordinal."""
    tag_start = _rules().VERBATIM_START_RE
    return [i for i, line in enumerate(body) if tag_start.match(line)]


def _excerpt_match(view, reader, finding, ordinal: int, index: int) -> ExcerptMatch:
    """K10's one result for the verbatim tag at body line `index`, `ordinal` its 1-based position among
    the finding's tags. Every field but the spans comes from K10's own steps, so K10's accept/reject
    behaviour and messages are the ones it had before this result existed."""
    rules = _rules()
    body = finding.body_lines
    tag = rules.VERBATIM_RE.match(body[index])
    excerpt = None
    if tag is None:
        path, is_offset, cited = "", False, ()
        end, problem = index + 1, MESSAGE_MALFORMED_TAG
    else:
        path = tag.group("path").strip()
        is_offset = tag.group("offset") is not None
        cited = _range(tag)
        excerpt, end, problem = rules._excerpt_after(body, index + 1)
    fields = dict(ordinal=ordinal, start=index, end=end, path=path, key=None, is_offset=is_offset,
                  range=cited, text=excerpt, source_sha256=None, spans=(), problem=problem,
                  verified=False, binary=False, tag_sha256=tag_sha256(body, index, end))
    if problem is not None:
        return ExcerptMatch(**fields)
    if not excerpt.strip():
        return ExcerptMatch(**{**fields, "problem": MESSAGE_EMPTY_EXCERPT})
    full, refusal = rules._repo_path(view, path)
    if refusal is not None:
        return ExcerptMatch(**{**fields, "problem": f"verbatim source {path} {refusal}"})
    data, key = _read_source(view, reader, path, full)
    fields["key"] = key
    if data is None:
        return ExcerptMatch(**{**fields, "problem": MESSAGE_NO_SOURCE.format(source=path)})
    fields["source_sha256"] = hashlib.sha256(data).hexdigest()
    text = _source_text(data)
    if text is None:
        # Binary-exempt: K10 does not check it (the finding's check: command does), and an unchecked
        # excerpt is not exempt from K4/K5. There is no span to map.
        return ExcerptMatch(**{**fields, "binary": True})
    problem = rules._excerpt_problem(tag, path, excerpt, data, text)
    if problem is not None:
        return ExcerptMatch(**{**fields, "problem": problem})
    return ExcerptMatch(**{**fields, "verified": True, "spans": _spans(tag, excerpt, data, text)})


def _range(tag: re.Match) -> tuple[int, ...]:
    """The tag's cited range: (first, last) lines, or (offset,) for a :@0x tag."""
    if tag.group("offset") is not None:
        return (int(tag.group("offset"), 16),)
    first = int(tag.group("first"))
    return (first, int(tag.group("last") or first))


def _read_source(view, reader, source: str, full: Path) -> tuple[bytes | None, str | None]:
    """(bytes, canonical key) of a source path K10 accepted. A path K10 accepts but paths refuses (a
    `..` segment that stays inside the repository, a `:` in a name) keeps K10's own read and gets no
    key: K13 matches on the key, so it ignores such an excerpt, and K10's verdict does not change."""
    try:
        key = paths.canonical_key(reader.cfg, source)
    except paths.PathRefused:
        return _rules()._source_bytes(view, full), None
    return reader.working(source), key


def _source_text(data: bytes) -> str | None:
    """The source's LF-normalised text, or None for a binary source: a NUL byte or not UTF-8."""
    if b"\0" in data:
        return None
    try:
        return normalise_newlines(data.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def _spans(tag: re.Match, excerpt: str, data: bytes, text: str) -> tuple[tuple[int, int], ...]:
    """Every half-open span at which the excerpt matches within the cited range, in the source's LF
    text. K10 has already accepted the citation, so there is at least one."""
    if tag.group("offset") is not None:
        offset = int(tag.group("offset"), 16)
        for form in (excerpt, excerpt.replace("\n", "\r\n")):
            raw = form.encode("utf-8")
            if data.startswith(raw, offset):
                m = byte_to_char_map(data)
                return ((m[offset], m[offset + len(raw)]),)
        return ()
    first = int(tag.group("first"))
    last = int(tag.group("last") or first)
    starts = line_starts(text)
    begin = starts[first - 1]
    if last < len(starts):
        end = starts[last] - 1
    else:
        end = len(text) - (1 if text.endswith("\n") else 0)
    return tuple((at, at + len(excerpt)) for at in find_all(text, excerpt)
                 if at >= begin and at + len(excerpt) <= end)
