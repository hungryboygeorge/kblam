"""K14, affected uses (SPEC §5.2.4 K14; §12 M6.11 test group 3). Findings quote a nested Git source repository
through the real matching.finding_matches, and confirmed challenges and uses are written as records into the
review root and read back through view.load_view. Offline."""

from __future__ import annotations

import datetime
import hashlib

import pytest

from conftest import SOURCE_REPO, TRACE_PATH, TRACE_TEXT, dump_record, record_data
from kblam import records, rules
from kblam.decisions import subject_digest
from kblam.finding import fingerprint
from kblam.k14 import affected_triples, k14
from kblam.matching import finding_matches
from kblam.sources import SourceReader, sha256_hex
from kblam.view import load_view

REVIEW = "research-review"
TRACE = f"{SOURCE_REPO}/{TRACE_PATH}"
WORD = "the two bytes are equal"                     # TRACE_TEXT line 3
LINE3 = "Row 102: bytes 0x3A 0x3B; the two bytes are equal."
CLAIM = "The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0)."
DECIDER = "reviewer-b"


# --- builders -----------------------------------------------------------------------------------


def sha_of(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def deciding(kind: str, data: dict) -> dict:
    """Append the one decision `data`'s status needs, with the subject digest it binds."""
    if data["status"] != "open":
        data["decisions"] = [{"date": datetime.date(2026, 9, 28), "by": DECIDER, "status": data["status"],
                              "reason": "reviewed the record", "evidence": [], "bind": None}]
        data["decisions"][0]["bind"] = subject_digest(kind, data)
    return data


def sc(repo, status: str = "confirmed", *, rec_id: str = "SC-0001", rel: str = TRACE_PATH,
       text: str = WORD, lines=(3, 3), pin: bool = True) -> dict:
    """A challenge on the source's working bytes as they are now, pinned at HEAD when `pin`."""
    data = record_data("SC", rec_id, status=status)
    sha = sha_of((repo.root / rel).read_bytes())
    source = data["source"]
    source.update(path=repo.kb_path(rel), sha256=sha)
    if pin:
        source.update(repo=SOURCE_REPO, commit=repo.head(), blob=repo.blob(rel))
    source["assertion"] = {"lines": list(lines), "text": text, "sha256": sha_of(text), "occurrence": 1}
    data["basis"][0].update(path=repo.kb_path(rel), sha256=sha)
    return deciding("SC", data)


def put(kb, kind: str, data: dict) -> None:
    kb.write(f"{REVIEW}/{records.KINDS[kind]}/{data['id']}.yaml", dump_record(data))


def quoted(tag: str, excerpt: str) -> str:
    """A verbatim tag and its blockquote, as a finding body."""
    block = "\n".join("> " + line for line in excerpt.split("\n"))
    return f"Detail follows.\n\n<!-- verbatim: {tag} -->\n{block}"


def add_finding(kb, tag: str, excerpt: str, *, path: str = TRACE, fid: str = "F-0001", **kw) -> None:
    kb.add(fid, "ratio", CLAIM, body=quoted(f"{path}:{tag}", excerpt), **kw)


def scene(kb):
    """(view, reader) of the tree as it is now."""
    view = load_view(kb.cfg)
    return view, SourceReader(kb.cfg, view)


def k14_of(kb) -> list:
    view, reader = scene(kb)
    return k14(view, reader)


def errors_of(issues) -> list:
    return [i for i in issues if i.level == "error"]


def warnings_of(issues) -> list:
    return [i for i in issues if i.level == "warning"]


def use(kb, *, status: str = "approved", challenge: str = "SC-0001", fid: str = "F-0001",
        ordinal: int = 1, rec_id: str = "CU-0001") -> dict:
    """A use bound to the finding, challenge and excerpt as they are now."""
    view, reader = scene(kb)
    finding = next(f for f in view.findings if f.file_id == fid)
    match = next(m for m in finding_matches(view, reader, finding) if m.ordinal == ordinal)
    rec = next(r for r in view.records if r.id == challenge)
    data = record_data("CU", rec_id, status=status, challenge=challenge, finding=fid,
                       challenge_bind=subject_digest("SC", rec.data),
                       finding_fingerprint=fingerprint(finding, kb.cfg.scope_separator), finding_file_sha256=sha256_hex(finding.raw),
                       citation={"ordinal": ordinal, "path": match.path, "range": list(match.range),
                                 "tag_sha256": match.tag_sha256})
    return deciding("CU", data)


def same_bytes_message(repo, blob: bool = True, ordinal: int = 1, lines: str = "3-3") -> str:
    version = repo.blob(TRACE_PATH)[:12] if blob else sha_of(TRACE_TEXT)[:12]
    return (f"SC-0001 challenges this quoted assertion at {TRACE}@{version}:{lines}; edit the finding or "
            f"have this use reviewed (kblam use review SC-0001 F-0001 {ordinal} --by NAME --proponent NAME). "
            f"K10 is checked separately.")


NEW_TEXT = "# a heading added above\n" + TRACE_TEXT      # another version of the source: every line moves


# --- same bytes ---------------------------------------------------------------------------------


def test_same_bytes_and_an_intersecting_excerpt_is_an_error_with_the_spec_message(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    add_finding(kb, "3", LINE3)
    view, reader = scene(kb)
    finding = view.findings[0]
    issues = k14(view, reader)
    assert [(i.code, i.level, i.owner, i.path) for i in issues] == [
        ("K14", "error", "F-0001", finding.path)]
    [match] = finding_matches(view, reader, finding)
    assert issues[0].line == finding.body_start_line + match.start == 15
    assert issues[0].message == same_bytes_message(source_repo)
    assert issues[0].format(view).startswith(f"K14 {finding.path}:{issues[0].line}: SC-0001 challenges ")


def test_an_unpinned_source_names_the_sha256_prefix(kb, source_repo):
    put(kb, "SC", sc(source_repo, pin=False))
    add_finding(kb, "3", LINE3)
    assert [i.message for i in errors_of(k14_of(kb))] == [same_bytes_message(source_repo, blob=False)]


def test_a_current_approved_use_covers_the_excerpt(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    add_finding(kb, "3", LINE3)
    put(kb, "CU", use(kb))
    assert k14_of(kb) == []


def test_two_matches_in_the_cited_range_where_only_the_second_intersects(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    add_finding(kb, "3", "bytes")           # "bytes 0x3A" is outside the assertion, "bytes are equal" is in it
    view, reader = scene(kb)
    assert len(finding_matches(view, reader, view.findings[0])[0].spans) == 2
    assert [i.message for i in errors_of(k14_of(kb))] == [same_bytes_message(source_repo)]


def test_a_range_that_overlaps_without_quoting_is_a_warning(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    add_finding(kb, "3", "Row 102: bytes 0x3A 0x3B")
    issues = k14_of(kb)
    assert errors_of(issues) == []
    assert [(i.level, i.owner) for i in issues] == [("warning", "F-0001")]
    assert issues[0].message.startswith(f"the cited range {TRACE}:3-3 overlaps lines 3-3 of SC-0001's assertion")


def test_an_offset_tag_never_gets_the_range_warning(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    offset = TRACE_TEXT.index("Row 102")
    add_finding(kb, f"@0x{offset:X}", "Row 102: bytes 0x3A 0x3B")
    assert k14_of(kb) == []


def test_a_range_beside_the_assertion_reports_nothing(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    add_finding(kb, "2", "Row 101: bytes 0x3A 0x3B")
    assert k14_of(kb) == []


def test_two_identical_copies_are_separate_uses_told_apart_by_ordinal(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    block = quoted(f"{TRACE}:3", LINE3)
    kb.add("F-0001", "ratio", CLAIM, body=f"{block}\n\n{block}")
    put(kb, "CU", use(kb, ordinal=1))
    issues = k14_of(kb)
    assert [i.message for i in errors_of(issues)] == [same_bytes_message(source_repo, ordinal=2)]


def test_offset_tag_in_a_crlf_multibyte_source_maps_to_the_right_span(kb, source_repo):
    data = "Aé\r\nRow 102: bytes 0x3A 0x3B; the two bytes are equal.\r\n".encode("utf-8")
    source_repo.commit("notes/crlf.md", data)
    put(kb, "SC", sc(source_repo, rel="notes/crlf.md", lines=(2, 2)))
    path = source_repo.kb_path("notes/crlf.md")
    inside = data.index(b"the two bytes")
    outside = data.index(b"Row 102")
    add_finding(kb, f"@0x{inside:X}", "the two bytes", path=path)
    assert len(errors_of(k14_of(kb))) == 1
    add_finding(kb, f"@0x{outside:X}", "Row 102: bytes 0x3A 0x3B; ", path=path)
    assert k14_of(kb) == []


# --- different bytes ----------------------------------------------------------------------------


def test_another_version_quoting_the_assertion_is_version_unproved(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    blob = source_repo.blob(TRACE_PATH)[:12]
    source_repo.write(TRACE_PATH, NEW_TEXT)
    add_finding(kb, "4", LINE3)
    view, reader = scene(kb)
    issues = k14(view, reader)
    assert [(i.level, i.owner) for i in issues] == [("error", "F-0001")]
    assert issues[0].message == (
        f"SC-0001 was judged on {TRACE}@{blob} only, and this excerpt quotes its assertion text from "
        f"another version of that file. This does not show that the version is wrong: challenge it "
        f"(kblam challenge new {TRACE} --lines 4-4 --by NAME) or have this use reviewed "
        f"(kblam use review SC-0001 F-0001 1 --by NAME --proponent NAME).")


def test_version_unproved_for_an_offset_tag_names_the_lines_its_span_covers(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    source_repo.write(TRACE_PATH, NEW_TEXT)
    offset = NEW_TEXT.index(LINE3)
    add_finding(kb, f"@0x{offset:X}", LINE3)
    [issue] = errors_of(k14_of(kb))
    assert f"(kblam challenge new {TRACE} --lines 4-4 --by NAME)" in issue.message


def test_an_excerpt_inside_the_assertion_text_is_also_version_unproved(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    source_repo.write(TRACE_PATH, NEW_TEXT)
    add_finding(kb, "4", "two bytes")
    assert len(errors_of(k14_of(kb))) == 1


def test_another_version_that_does_not_quote_the_assertion_reports_nothing(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    source_repo.write(TRACE_PATH, NEW_TEXT)
    add_finding(kb, "3", "Row 101: bytes 0x3A 0x3B")
    assert k14_of(kb) == []


def test_a_current_use_covers_a_version_unproved_excerpt(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    source_repo.write(TRACE_PATH, NEW_TEXT)
    add_finding(kb, "4", LINE3)
    put(kb, "CU", use(kb))
    assert k14_of(kb) == []


# --- what covers an excerpt ---------------------------------------------------------------------


@pytest.mark.parametrize("status", ["open", "withdrawn", "stale"])
def test_a_use_that_is_not_approved_does_not_cover(kb, source_repo, status):
    put(kb, "SC", sc(source_repo))
    add_finding(kb, "3", LINE3)
    put(kb, "CU", use(kb, status=status))
    assert len(errors_of(k14_of(kb))) == 1


def test_a_use_with_a_broken_binding_does_not_cover(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    add_finding(kb, "3", LINE3)
    put(kb, "CU", use(kb))
    kb.add("F-0001", "ratio", CLAIM + " Edited.", body=quoted(f"{TRACE}:3", LINE3))
    assert len(errors_of(k14_of(kb))) == 1


@pytest.mark.parametrize("change", ["ordinal", "challenge", "finding"])
def test_a_use_of_another_excerpt_or_challenge_or_finding_does_not_cover(kb, source_repo, change):
    put(kb, "SC", sc(source_repo))
    add_finding(kb, "3", LINE3)
    data = use(kb)
    if change == "ordinal":
        data["citation"]["ordinal"] = 2
    else:
        data[change] = "SC-0002" if change == "challenge" else "F-0002"
    put(kb, "CU", data)
    assert len(errors_of(k14_of(kb))) == 1


# --- what K14 leaves to others ------------------------------------------------------------------


def test_an_excerpt_failing_k10_is_not_k14s(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    add_finding(kb, "3", "Row 102: bytes 0x3A 0x3C; the two bytes are equal.")
    view, reader = scene(kb)
    assert [i.code for i in rules.k10_verbatim(view, reader)] == ["K10"]
    assert k14(view, reader) == []


def test_a_binary_exempt_excerpt_is_not_k14s(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    source_repo.write(TRACE_PATH, b"\x00\x01binary")       # the same path is now a binary file
    add_finding(kb, "@0x0", "anything at all")
    view, reader = scene(kb)
    [match] = finding_matches(view, reader, view.findings[0])
    assert match.binary and match.key is not None
    assert k14(view, reader) == []


@pytest.mark.parametrize("status", ["open", "rejected", "stale"])
def test_a_challenge_that_is_not_confirmed_reports_nothing(kb, source_repo, status):
    put(kb, "SC", sc(source_repo, status))
    add_finding(kb, "3", LINE3, evidence=f"[{TRACE}]")
    assert k14_of(kb) == []


# --- references ---------------------------------------------------------------------------------


def test_a_finding_listing_the_challenged_source_in_evidence_gets_a_warning(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    kb.add("F-0001", "ratio", CLAIM, evidence=f"[{TRACE}]")
    view, reader = scene(kb)
    [issue] = k14(view, reader)
    finding = view.findings[0]
    assert (issue.level, issue.owner, issue.line) == ("warning", "F-0001", finding.key_line("evidence"))
    assert issue.message == (
        f"evidence lists {TRACE}, which SC-0001 challenges; a listed source is not shown to be safe, so "
        f"check what this finding takes from it against SC-0001's assertion and limits (kblam challenge "
        f"uses SC-0001 lists what SC-0001 affects)")


def test_evidence_spelled_another_way_still_names_the_source(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    kb.add("F-0001", "ratio", CLAIM, evidence=f"[./{TRACE}]")
    assert [i.level for i in k14_of(kb)] == ["warning"]


def test_prose_naming_the_source_path_gets_a_warning(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    kb.add("F-0001", "ratio", CLAIM, body=f"See {TRACE} for the trace.")
    view, reader = scene(kb)
    [issue] = k14(view, reader)
    assert (issue.level, issue.owner) == ("warning", "F-0001")
    finding = view.findings[0]
    assert issue.line == finding.body_start_line + next(
        i for i, line in enumerate(finding.body_lines) if TRACE in line)
    assert issue.message.startswith(f"the text names {TRACE}, which SC-0001 challenges;")


def test_a_path_inside_a_verbatim_block_is_not_prose(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    kb.add("F-0001", "ratio", CLAIM,
           body=f"<!-- verbatim: {TRACE}:2 -->\n> Row 101: bytes 0x3A 0x3B")
    assert k14_of(kb) == []


# --- affected_triples ---------------------------------------------------------------------------


def test_affected_triples_include_covered_excerpts_and_exclude_warnings(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    block = quoted(f"{TRACE}:3", LINE3)
    warn = quoted(f"{TRACE}:3", "Row 102: bytes 0x3A 0x3B")
    kb.add("F-0001", "ratio", CLAIM, body=f"{block}\n\n{warn}", evidence=f"[{TRACE}]")
    put(kb, "CU", use(kb))
    view, reader = scene(kb)
    matches = finding_matches(view, reader, view.findings[0])
    assert errors_of(k14(view, reader)) == []           # the use covers the excerpt
    assert affected_triples(view, reader, "F-0001") == {("SC-0001", TRACE, matches[0].tag_sha256)}
    assert affected_triples(view, reader, "F-0002") == set()


def test_affected_triples_include_version_unproved_excerpts(kb, source_repo):
    put(kb, "SC", sc(source_repo))
    source_repo.write(TRACE_PATH, NEW_TEXT)
    add_finding(kb, "4", LINE3)
    view, reader = scene(kb)
    [match] = finding_matches(view, reader, view.findings[0])
    assert affected_triples(view, reader, "F-0001") == {("SC-0001", TRACE, match.tag_sha256)}
