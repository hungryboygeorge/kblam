"""K13, review record integrity (SPEC §5.2.4 K13 and the Severity table; §5.2.3 Evaluation, Basis,
Confirmation). Records are written into the review root as YAML and read back through view.load_view, so
the checks run on parsed records; sources come from the nested Git fixture repository. Offline.

The severity rows are table-driven: one test per row, parametrized over the status columns.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

import pytest

from conftest import DROP, SOURCE_REPO, TRACE_PATH, TRACE_TEXT, ZERO64, dump_record, record_data, record_text
from kblam import decisions, matching, records, rules, sources, writes
from kblam.cli import main
from kblam.decisions import subject_digest
from kblam.finding import fingerprint, yaml_rt
from kblam.gitdir import committed_record
from kblam.k13 import _use_blocking_issues, challenge_info, k13, use_binding_problems, use_current
from kblam.review_index import generate_review_index
from kblam.sources import SourceReader
from kblam.treehash import format_line
from kblam.view import load_view

from test_approval import commit, git, gkb  # noqa: F401  (gkb is a fixture)
from test_k14 import LINE3, add_finding, quoted, use as quoted_use

REVIEW = "research-review"
TRACE = f"{SOURCE_REPO}/{TRACE_PATH}"
SHA = hashlib.sha256(TRACE_TEXT.encode("utf-8")).hexdigest()
WORD = "the two bytes are equal"                     # TRACE line 3
WORD_SHA = hashlib.sha256(WORD.encode("utf-8")).hexdigest()
TAG = hashlib.sha256(b"tag and block").hexdigest()   # the stand-in's tag_sha256
DECIDER = "reviewer-b"                               # independent of creator and proponent
FINDING = "F-0001"

# The real matcher, captured before the autouse `excerpts` fixture replaces it. The tests that prove a
# `kblam use review` suggestion restore it: that command reads the finding's real verbatim excerpts.
REAL_FINDING_MATCHES = matching.finding_matches

# The CU binding row's advice, as it prints for the fixture's CU-0001 (SPEC §5.2.5 `review rebind`).
REBIND_FIX = "run kblam review rebind CU-0001 --by NAME --reason TEXT --expect D"


def retire(rec_id: str, path: str = TRACE, *, pinned: bool = False) -> str:
    """The step a stale or unavailable reference message ends with (SPEC §5.2.3 Evaluation): restore the
    bytes the record was written against at `path`, or retire it and file a new one."""
    restore = (f"restore the pinned bytes of {path}" if pinned
               else f"restore {path} to the bytes {rec_id} was written against")
    return (f"; {restore}, or retire the record and file a new one (kblam review decide {rec_id} "
            f"--status stale --by NAME --reason TEXT --expect D)")


def retire_missing(rec_id: str) -> str:
    """The same step where the message already names the file: "restore it"."""
    return (f"; restore it, or retire the record and file a new one (kblam review decide {rec_id} "
            f"--status stale --by NAME --reason TEXT --expect D)")
RETIRE_USE_FIX = "kblam review decide CU-0001 --status stale --by NAME --reason TEXT --expect D"
NEW_USE_FIX = (f"retire this use ({RETIRE_USE_FIX}); if the finding still quotes the assertion, make "
               "sure kblam validate verifies that excerpt as a text match (fix the excerpt or its "
               "citation), then stage a new use (kblam use review SC-0001 F-0001 <excerpt-ordinal> --by "
               "NAME --proponent NAME)")
PIN_CONFIRM_FIX = (
    "pin SC-0001 (kblam challenge pin SC-0001 --expect D, adding --snapshot PATH where its worktree's "
    "HEAD doesn't hold the source's bytes), confirm it (kblam review decide SC-0001 --status confirmed "
    "--by NAME --reason TEXT --expect D), then run kblam validate again")
CONFIRM_USE_FIX = (
    "confirm SC-0001 (kblam review decide SC-0001 --status confirmed --by NAME --reason TEXT --expect D), "
    "then run kblam validate again")
RESTORE_SOURCE_FIX = "restore the version SC-0001 was judged on, then run kblam validate again"
RESTORE_TEXT_FIX = (
    "restore the source text excerpt 1 quotes (or fix the excerpt so kblam validate verifies it), "
    f"then run kblam validate again; or retire this use ({RETIRE_USE_FIX})")
# A citation that is no excerpt reference leaves the use structurally invalid, so every write command
# refuses it and the only advice is to restore the installed record (SPEC §5.2.3 CU citation).
RESTORE_USE_FIX = ("restore CU-0001 from git (its citation is not a valid excerpt reference), then run "
                   "kblam validate again")
# A schema records cannot check vouches for no field, so the same restore applies before the citation is
# read at all (SPEC §5.2.2 Values).
RESTORE_SCHEMA_FIX = ("restore CU-0001 from git (its schema version is not supported), then run kblam "
                      "validate again")
# Any other structure error of the use's own blocks every write command too (SPEC §5.2.4 K13 Structure).
RESTORE_STRUCTURE_FIX = ("restore CU-0001 from git (its structure is invalid; kblam validate names the "
                         "problem), then run kblam validate again")

# The confirmation row's provisional-source message, as it prints for the fixture's SC-0001, and the
# retire command it names. A pin takes an open challenge (SPEC §5.2.5 `challenge pin`), so a confirmed
# challenge is retired and challenged again (§5.2.3 Evaluation, §5.2.2 "Status changes"); the new
# challenge is staged, filled, put — which pins a source its worktree's HEAD holds — and only a source
# HEAD does not hold needs `challenge pin --snapshot` (§5.2.5 Receipts and put, §5.2.2 Git pins).
CONFIRMATION_FIX = (
    "SC-0001 is confirmed with a provisional source; a confirmation needs a pinned source, and only an "
    "open challenge is pinned (a confirmed challenge's source is fixed from the decision that closes "
    "it). Restore it from git, or retire it (kblam review decide SC-0001 --status stale --by NAME "
    "--reason TEXT --expect D) and write a new challenge (kblam challenge new "
    f"{TRACE} --lines A-B --by NAME, fill the staged record and kblam put it, which pins the source "
    "when its worktree's HEAD holds those bytes; where it does not, pin the installed challenge with "
    "kblam challenge pin SC-NNNN --expect D --snapshot PATH)")
RETIRE = ("review", "decide", "SC-0001", "--status", "stale", "--by", DECIDER, "--reason",
          "the source was never pinned, so this confirmation needs a new challenge")

# Every (kind, status) the Severity table has a column for: records.STATUSES is the vocabulary.
CASES = [(kind, status) for kind, statuses in records.STATUSES.items() for status in statuses]


# --- fixtures and builders ------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def excerpts(monkeypatch) -> dict:
    """A stand-in for matching.finding_matches, which unit U12 is still writing: the tests hand-build
    ExcerptMatch results, keyed by finding ID. The lead replaces it with the real function."""
    table: dict[str, list] = {}
    monkeypatch.setattr(matching, "finding_matches",
                        lambda view, reader, finding: list(table.get(finding.file_id, [])))
    return table


@pytest.fixture
def kb_ready(kb, source_repo, excerpts):
    """The KB every record test binds against: finding F-0001, the confirmed challenge SC-0001 and the
    excerpt the use cites."""
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.")
    put(kb, "SC", sc("confirmed", repo=source_repo))
    excerpts[FINDING] = [excerpt()]
    return kb


def put(kb, kind: str, data: dict) -> str:
    """Write one record at its canonical path; the path it went to."""
    path = f"{REVIEW}/{records.KINDS[kind]}/{data['id']}.yaml"
    kb.write(path, dump_record(data))
    return path


def k13_issues(kb, *, index: bool = True) -> list:
    """Every K13 issue of the tree as it is. `index=False` leaves the review index as the test wrote it."""
    if index:
        kb.write(f"{REVIEW}/INDEX.md", generate_review_index(load_view(kb.cfg)))
    view = load_view(kb.cfg)
    return k13(view, SourceReader(kb.cfg, view))


def view_and_reader(kb):
    view = load_view(kb.cfg)
    return view, SourceReader(kb.cfg, view)


def record_of(view, rec_id: str):
    return next(rec for rec in view.records if rec.id == rec_id)


def digest_of(kb, rec_id: str) -> str:
    """The installed record's subject digest, as show, list and `--expect` take it."""
    rec = record_of(load_view(kb.cfg), rec_id)
    return subject_digest(rec.kind, rec.data)


def use_problems(kb, rec_id: str = "CU-0001") -> list[str]:
    """The installed use's binding problems, as K13 and the write commands compute them."""
    view, reader = view_and_reader(kb)
    return use_binding_problems(view, reader, record_of(view, rec_id))


def messages_of(kb, **kw) -> list[tuple[str, str]]:
    return [(issue.level, issue.message) for issue in k13_issues(kb, **kw)]


def pin_messages(kb) -> list[str]:
    """The confirmation row's provisional-source message, if the tree reports it."""
    return [issue.message for issue in k13_issues(kb)
            if "a confirmation needs a pinned source" in issue.message]


def run(kb, *argv: str) -> int:
    """`kblam --root <kb> <argv>` in process, as the console entry point runs it."""
    return main(["--root", str(kb.root), *argv])


def fill(path: Path, **fields) -> None:
    """Fill a staged record's blank fields, as its author does, and write it back with LF endings."""
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    data.update(fields)
    path.write_bytes(records.dump(data))


def sc_fields() -> dict:
    """The blank SC fields the author fills before the first put (SPEC §5.2.5): one basis entry on the
    source itself, with the null sha256 and pin that put fills in from the working file."""
    return {"proposition": "The printed byte equality follows from the printed byte values",
            "scope": ["MX-100 capture transcription"],
            "classification": "contradicted",
            "basis": [{"path": TRACE, "sha256": None, "repo": None, "commit": None, "blob": None,
                       "snapshot": None, "locator": "row 102: printed byte values",
                       "role": "internal-inconsistency", "provenance": "observed"}],
            "usable": "The printed byte values may be cited as a report.",
            "limits": "Do not infer the capture bytes from this row."}


def deciding(kind: str, data: dict) -> dict:
    """Append the one decision `data`'s status needs, with the subject digest it binds (SPEC §5.2.2)."""
    status = data["status"]
    if status != "open":
        data["decisions"] = [{"date": datetime.date(2026, 9, 28), "by": DECIDER, "status": status,
                              "reason": "reviewed the record", "evidence": [], "bind": None}]
        data["decisions"][0]["bind"] = subject_digest(kind, data)
    return data


def ref(path: str = TRACE, sha: str = SHA, **fields) -> dict:
    """A file reference (SPEC §5.2.2); repo, commit and blob follow as a Git pin."""
    reference = {"path": path, "sha256": sha, "repo": None, "commit": None, "blob": None, "snapshot": None}
    reference.update(fields)
    return reference


def source(path: str = TRACE, sha: str = SHA, *, lines=(3, 3), text: str = WORD, occurrence: int = 1,
           **fields) -> dict:
    """A challenge's source: a reference plus the assertion its text and occurrence describe."""
    reference = ref(path, sha, **fields)
    reference["assertion"] = {"lines": list(lines), "text": text, "occurrence": occurrence,
                              "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    return reference


def pinned_source(repo, **fields) -> dict:
    """The fixture source pinned to the nested repository's HEAD commit (SPEC §5.2.2 Git pins): an open
    challenge whose source is pinned, which is what a confirmation needs."""
    return source(repo=SOURCE_REPO, commit=repo.head(), blob=repo.blob(TRACE_PATH), **fields)


def basis(sha: str = SHA, *, path: str = TRACE, provenance: str = "observed",
          role: str = "internal-inconsistency", **fields) -> dict:
    """A basis entry on the source itself (SPEC §5.2.3 Basis): no pin of its own, so it reads at the
    source's pin, and its sha256 is the source's."""
    entry = ref(path, sha, **fields)
    entry.update({"locator": "row 102: the printed byte values", "role": role, "provenance": provenance})
    return entry


def sc(status: str = "open", repo=None, **fields) -> dict:
    """A challenge with an available source that matches its assertion and one primary basis entry. A
    confirmed challenge also carries the pin its confirmation needs."""
    if "source" not in fields:
        fields["source"] = source(**({"repo": SOURCE_REPO, "commit": repo.head(),
                                      "blob": repo.blob(TRACE_PATH)} if repo is not None and
                                     status in records.EFFECTIVE["SC"] else {}))
    fields.setdefault("basis", [basis(sha=fields["source"]["sha256"], path=fields["source"]["path"])])
    return deciding("SC", record_data("SC", "SC-0001", status=status, **fields))


def finding_of(kb, finding_id: str = FINDING):
    return next(finding for finding in load_view(kb.cfg).findings if finding.file_id == finding_id)


def quoted_scene(kb, source_repo) -> None:
    """A confirmed challenge on the trace and a finding that quotes its assertion: the scene the real
    matcher relates, so `kblam use review` has an affected excerpt to stage against."""
    put(kb, "SC", sc("confirmed", repo=source_repo))
    add_finding(kb, "3", LINE3)


def ct(kb, status: str = "open", finding_id: str = FINDING, **fields) -> dict:
    """A task bound to the finding as it is now."""
    finding = finding_of(kb, finding_id)
    data = record_data("CT", "CT-0001", status=status, finding=finding_id,
                       claim_fingerprint=fingerprint(finding, kb.cfg.scope_separator),
                       base_file_sha256=sources.sha256_hex(finding.raw))
    data.update(fields)
    return deciding("CT", data)


def challenge_binding(kb, challenge_id: str = "SC-0001") -> str:
    """The subject digest of the installed challenge, as a use binds it."""
    view = load_view(kb.cfg)
    return subject_digest("SC", record_of(view, challenge_id).data)


def cu(kb, status: str = "open", *, finding_id: str = FINDING, citation=None, **fields) -> dict:
    """A use of the finding's excerpt, bound to the finding and to SC-0001 as they are now."""
    finding = finding_of(kb, finding_id)
    data = record_data("CU", "CU-0001", status=status, challenge="SC-0001",
                       challenge_bind=challenge_binding(kb), finding=finding_id,
                       finding_fingerprint=fingerprint(finding, kb.cfg.scope_separator),
                       finding_file_sha256=sources.sha256_hex(finding.raw),
                       citation=citation or {"ordinal": 1, "path": TRACE, "range": [3, 3],
                                             "tag_sha256": TAG})
    data.update(fields)
    return deciding("CU", data)


def excerpt(ordinal: int = 1, *, path: str = TRACE, span_range=(3, 3), tag_sha256: str = TAG,
            verified: bool = True, binary: bool = False, problem: str | None = None):
    """One hand-built matching.ExcerptMatch, as U12's finding_matches will return it."""
    return matching.ExcerptMatch(ordinal=ordinal, start=10, end=12, path=path, key=None, is_offset=False,
                                 range=tuple(span_range), text=WORD, source_sha256=SHA,
                                 spans=((10, 30),), problem=problem, verified=verified, binary=binary,
                                 tag_sha256=tag_sha256)


def clean(kb, kind: str, status: str, repo=None) -> dict:
    """The record of this kind and status with nothing for the Severity table to report."""
    if kind == "SC":
        return sc(status, repo=repo)
    if kind == "CT":
        return ct(kb, status)
    return cu(kb, status)


def dangling(issues) -> list[tuple[str, str]]:
    """The Dangling link row's issues, told apart from the CU binding row's."""
    return [(issue.level, issue.message) for issue in issues
            if "which is no finding" in issue.message or "names no record" in issue.message]


def bindings(issues) -> list[tuple[str, str]]:
    """The CU binding row's issues: _use_issues appends each of them at line 0, owned by CU-0001 (a
    dangling link carries the key's line, so it is not one of these)."""
    return [(issue.level, issue.message) for issue in issues
            if issue.code == "K13" and issue.owner == "CU-0001" and issue.line == 0]


# --- no report at all ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind,status", CASES)
def test_a_clean_record_of_every_kind_and_status_reports_nothing(kb_ready, source_repo, kind, status):
    put(kb_ready, kind, clean(kb_ready, kind, status, source_repo))
    assert k13_issues(kb_ready) == []


def test_a_review_root_without_records_reports_nothing(kb):
    """No review root, no index: a project that never wrote a record owes neither."""
    assert k13_issues(kb) == []


# --- the Severity table, one test per row --------------------------------------------------------


@pytest.mark.parametrize("kind,status", CASES)
def test_structure_row_is_an_error_at_every_status(kb_ready, source_repo, kind, status):
    """An unknown key stands for the whole Structure row: YAML, schema, types, path syntax, hash and pin
    syntax, IDs, decisions, status or bind mismatch, transition, independence."""
    data = clean(kb_ready, kind, status, source_repo)
    data["surplus"] = 1
    put(kb_ready, kind, data)
    issues = k13_issues(kb_ready)
    assert [(i.level, i.owner) for i in issues] == [("error", data["id"])]
    assert "unknown key 'surplus'" in issues[0].message


@pytest.mark.parametrize("kind,status", CASES)
def test_dangling_row_level_by_status(kb_ready, source_repo, kind, status):
    """A link that names nothing: error while open and when effective, warning when closed or retired."""
    level = "error" if status == "open" or status in records.EFFECTIVE[kind] else "warning"
    missing = "F-0009"
    fields = {"linked_findings": [missing]} if kind == "SC" else {"finding": missing}
    if kind == "CU":
        fields["challenge"] = "SC-0009"
    put(kb_ready, kind, clean(kb_ready, kind, status, source_repo) | fields)
    found = dangling(k13_issues(kb_ready))
    assert len(found) == (2 if kind == "CU" else 1)
    assert {found_level for found_level, _message in found} == {level}


@pytest.mark.parametrize("status", records.STATUSES["SC"])
def test_availability_row_level_by_status(kb_ready, source_repo, status):
    """A stale source: warning while open, error when confirmed, nothing when rejected or retired. The
    basis entry on the source is stale with it."""
    stale = "a" * 64
    put(kb_ready, "SC", sc(status, source=source(sha=stale), basis=[basis(sha=stale)]))
    found = messages_of(kb_ready)
    expected = {"open": ["warning", "warning"], "confirmed": ["error", "error"]}.get(status, [])
    assert [level for level, _message in found] == expected
    assert [message for _level, message in found] == [
        "the source changed since SC-0001 was written" + retire("SC-0001"),
        "basis[0]: the source changed since SC-0001 was written" + retire("SC-0001")][:len(expected)]


@pytest.mark.parametrize("status", records.STATUSES["CU"])
def test_cu_binding_row_level_by_status(kb_ready, source_repo, status):
    """A use whose finding has changed: warning while open and while approved, nothing else. K14 is what
    blocks."""
    data = cu(kb_ready, status, finding_fingerprint="0badf00d0000", finding_file_sha256=ZERO64)
    put(kb_ready, "CU", data)
    expected = "warning" if status in ("open", "approved") else None
    found = bindings(k13_issues(kb_ready))
    if expected is None:
        assert found == []
    else:
        assert [level for level, _message in found] == [expected, expected]
        assert all(message.endswith(f"({REBIND_FIX})")
                   for _l, message in found)


@pytest.mark.parametrize("status", records.STATUSES["SC"])
def test_confirmation_row_level_by_status(kb_ready, source_repo, status):
    """A confirmed challenge with a provisional source: error when confirmed, nothing at any other status
    (an available provisional source is fine while a challenge is open)."""
    put(kb_ready, "SC", sc(status))
    found = [(i.level, i.owner) for i in k13_issues(kb_ready)
             if "a confirmation needs a pinned source" in i.message]
    assert found == ([("error", "SC-0001")] if status == "confirmed" else [])


def test_a_ct_binding_mismatch_is_not_k13s(kb_ready, source_repo):
    """The CT binding row belongs to K15: K13 reports nothing for a task whose binding no longer holds."""
    put(kb_ready, "CT", ct(kb_ready, "confirmed", claim_fingerprint="0badf00d0000",
                           base_file_sha256=ZERO64))
    assert messages_of(kb_ready) == []


# --- structure: paths, pins, the assertion, decisions --------------------------------------------


def test_a_refused_path_in_a_record_is_a_structure_error(kb_ready, source_repo):
    """records.schema_issues reports the path's syntax; K13 does not report it twice."""
    put(kb_ready, "SC", sc(source=source(path="/outside/trace.md")))
    assert messages_of(kb_ready) == [("error", "source.path: absolute path"),
                                     ("error", "basis[0].path: absolute path")]


def test_a_pin_that_does_not_verify_is_a_structure_error(kb_ready, source_repo):
    """A commit field that names a blob is a wrong pin, not a missing one (SPEC §5.2.2 Git pins). The
    basis entry on the source reads at the source's pin, so it reports the broken pin too."""
    blob = source_repo.blob(TRACE_PATH)
    put(kb_ready, "SC", sc(source=source(repo=SOURCE_REPO, commit=blob, blob=blob)))
    assert messages_of(kb_ready) == [("error", f"source: {blob} is a blob, not a commit"),
                                     ("error", f"basis[0]: {blob} is a blob, not a commit")]


def test_an_assertion_that_does_not_match_its_source_is_a_structure_error(kb_ready, source_repo):
    put(kb_ready, "SC", sc(source=source(text="bytes the trace never states")))
    assert messages_of(kb_ready) == [
        ("error", "assertion: the assertion text does not occur within lines 3-3")]


def test_an_assertion_that_occurs_twice_within_its_lines_is_a_structure_error(kb_ready, source_repo):
    """TRACE lines 2 and 3 both hold "bytes 0x3A 0x3B" (SPEC §5.2.3 Assertion, uniqueness)."""
    put(kb_ready, "SC", sc(source=source(lines=(2, 3), text="bytes 0x3A 0x3B")))
    assert messages_of(kb_ready) == [
        ("error", "assertion: the assertion text occurs 2 times within lines 2-3; narrow it to one")]


def test_an_assertion_sha256_that_does_not_match_its_text_is_a_structure_error(kb_ready, source_repo):
    wrong = "a" * 64
    data = sc()
    data["source"]["assertion"]["sha256"] = wrong     # the text and the lines still match the source
    put(kb_ready, "SC", deciding("SC", data))
    assert messages_of(kb_ready) == [
        ("error", f"assertion.sha256 is {wrong}, but the assertion's text hashes to {WORD_SHA}")]


def test_an_assertion_occurrence_that_names_another_match_is_a_structure_error(kb_ready, source_repo):
    put(kb_ready, "SC", sc(source=source(occurrence=2)))
    assert messages_of(kb_ready) == [
        ("error", "assertion.occurrence is 2, but the match within lines 3-3 is occurrence 1 among all "
                  "its matches")]


def test_a_binary_source_cannot_be_challenged(kb_ready):
    """SPEC §5.2.3 Assertion: a NUL byte, or a file that is not UTF-8, cannot be challenged in schema 1."""
    nul = b"row 102: bytes\x00\x3a"
    kb_ready.write("evidence/nul.txt", nul)
    put(kb_ready, "SC", sc(source=source("evidence/nul.txt", hashlib.sha256(nul).hexdigest(),
                                         text="bytes")))
    assert messages_of(kb_ready) == [
        ("error", "assertion: the source is binary (it holds a NUL byte), and schema 1 cannot challenge a "
                  "binary source")]


def test_a_source_that_is_not_utf8_cannot_be_challenged(kb_ready):
    binary = b"\x00\x01binary\xff"
    put(kb_ready, "SC", sc(source=source("evidence/2026-09-22-ratio/dump.bin",
                                         hashlib.sha256(binary).hexdigest(), text="binary")))
    assert messages_of(kb_ready) == [
        ("error", "assertion: the source is binary (it is not valid UTF-8), and schema 1 cannot challenge "
                  "a binary source")]


def test_a_status_that_differs_from_the_last_decision_is_an_error(kb_ready, source_repo):
    data = sc("confirmed")
    data["status"] = "open"
    put(kb_ready, "SC", data)
    assert [level for level, _message in messages_of(kb_ready)] == ["error"]


def test_a_record_that_did_not_parse_gets_structure_errors_only(kb_ready):
    kb_ready.write(f"{REVIEW}/challenges/SC-0001.yaml", "schema: 1\nid: [broken\n")
    issues = k13_issues(kb_ready)
    assert [(i.level, i.owner, i.code) for i in issues] == [("error", "SC-0001", "K13")]
    assert "not valid YAML" in issues[0].message


def test_a_malformed_status_gets_structure_errors_only(kb_ready):
    kb_ready.write(f"{REVIEW}/challenges/SC-0001.yaml",
                   "schema: 1\nid: SC-0001\nstatus: settled\n")
    issues = k13_issues(kb_ready)
    assert all(issue.level == "error" for issue in issues)
    assert any("status: 'settled' is not one of open, confirmed, rejected, stale" in issue.message
               for issue in issues)


# --- layout: strays, symlinks, the index, the root, the registry ---------------------------------


def test_a_file_that_is_no_record_is_a_stray(kb):
    kb.write(f"{REVIEW}/notes.md", "notes\n")
    issues = k13_issues(kb)
    assert [(i.path, i.level, i.owner) for i in issues] == [(f"{REVIEW}/notes.md", "error", "")]
    assert "Record each challenge, task or use with kblam challenge new" in issues[0].message


def test_a_record_in_the_wrong_kind_folder_is_a_stray(kb, source_repo):
    """A record-named file kblam does not know and that has no canonical copy: the advice is to restage
    it, since a hand-placed file has no allocation receipt for `kblam put` (SPEC §5.2.5 Receipts)."""
    path = f"{REVIEW}/tasks/SC-0001.yaml"
    kb.write(path, dump_record(sc()))
    found = [issue.message for issue in k13_issues(kb) if issue.path == path]
    assert found == [
        "a challenge must sit directly in its kind's folder (research-review/challenges/SC-0001.yaml); "
        "this ID is not an installed record or a registered ID, so restage it with kblam challenge new, "
        "kblam task new or kblam use review, each of which writes the receipt kblam put needs"]


def test_a_stray_record_kblam_does_not_know_says_to_restage_it(kb, source_repo, capsys):
    """The command a stray needs is the one that runs: `kblam put` refuses a file with no receipt, and
    kblam never moves or deletes a record file, while `challenge new` stages the same challenge and
    writes the receipt (SPEC §5.2.5 Receipts and put, §8)."""
    path = f"{REVIEW}/challenges/notes/SC-0007.yaml"
    kb.write(path, dump_record(sc("open", id="SC-0007")))
    expected = (f"a challenge must sit directly in its kind's folder ({REVIEW}/challenges/SC-0007.yaml); "
                f"this ID is not an installed record or a registered ID, so restage it with kblam "
                f"challenge new, kblam task new or kblam use review, each of which writes the receipt "
                f"kblam put needs")
    assert [(i.level, i.owner, i.message) for i in k13_issues(kb) if i.path == path] == \
        [("error", "", expected)]
    assert run(kb, "put", str(kb.root / path)) == 1
    assert "no allocation receipt" in capsys.readouterr().err
    assert run(kb, "challenge", "new", TRACE, "--lines", "3-3", "--by", "reviewer-c") == 0


def test_a_stray_allocated_draft_does_not_claim_its_receipt_is_missing(kb, source_repo, capsys):
    assert run(kb, "challenge", "new", TRACE, "--lines", "3-3", "--by", "reviewer-c") == 0
    staged = Path(capsys.readouterr().out.strip())
    assert (kb.root / ".kblam/review-receipts/SC-0001.json").is_file()
    path = f"{REVIEW}/challenges/notes/SC-0001.yaml"
    kb.write(path, staged.read_bytes())
    expected = (f"a challenge must sit directly in its kind's folder ({REVIEW}/challenges/SC-0001.yaml); "
                f"this ID is not an installed record or a registered ID, so restage it with kblam "
                f"challenge new, kblam task new or kblam use review, each of which writes the receipt "
                f"kblam put needs")
    assert [issue.message for issue in k13_issues(kb) if issue.path == path] == [expected]
    assert run(kb, "challenge", "new", TRACE, "--lines", "3-3", "--by", "reviewer-c") == 0


def test_a_stray_copy_of_a_record_kblam_knows_says_to_restore_the_root(kb_ready, source_repo):
    """A second copy of a record kblam knows: its place is the canonical path, its file may not be
    removed, and a copy carries no receipt, so the advice is to restore the review root from git
    (SPEC §5.2.6, §8 "Removal of a record file ... is denied")."""
    path = f"{REVIEW}/challenges/notes/SC-0001.yaml"
    kb_ready.write(path, dump_record(sc("confirmed", repo=source_repo)))
    expected = (f"a challenge must sit directly in its kind's folder ({REVIEW}/challenges/SC-0001.yaml); "
                f"this ID is a record kblam knows, so restore {REVIEW}/ from git (the record's place is "
                f"its canonical path)")
    found = [issue.message for issue in k13_issues(kb_ready)
             if "must sit directly in its kind's folder" in issue.message]
    assert found == [expected]


def test_a_stray_for_a_registered_id_says_to_restore_the_root(kb, source_repo):
    """The registry holds an ID with no canonical copy: kblam knows the record, so the stray is restored
    rather than restaged (SPEC §5.2.6)."""
    path = f"{REVIEW}/challenges/notes/SC-0007.yaml"
    kb.write(path, dump_record(sc("open", id="SC-0007")))
    kb.write(".kblam/review-ids", json.dumps(["SC-0007"]) + "\n")
    expected = (f"a challenge must sit directly in its kind's folder ({REVIEW}/challenges/SC-0007.yaml); "
                f"this ID is a record kblam knows, so restore {REVIEW}/ from git (the record's place is "
                f"its canonical path)")
    found = [issue.message for issue in k13_issues(kb)
             if "must sit directly in its kind's folder" in issue.message]
    assert found == [expected]


def test_a_symlinked_file_in_the_review_root_is_an_error(kb):
    kb.write(f"{REVIEW}/challenges/SC-0001.yaml", dump_record(sc()))
    link = kb.root / REVIEW / "challenges" / "SC-0002.yaml"
    try:
        link.symlink_to(kb.root / REVIEW / "challenges" / "SC-0001.yaml")
    except OSError:
        pytest.skip("this OS denies symlink creation")
    issues = k13_issues(kb, index=False)
    assert [(i.path, i.level) for i in issues if "symlink" in i.message] == [
        (f"{REVIEW}/challenges/SC-0002.yaml", "error")]


def test_a_dangling_symlink_is_an_error(kb):
    link = kb.root / REVIEW / "gone.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(kb.root / "nothing-here")
    except OSError:
        pytest.skip("this OS denies symlink creation")
    issues = k13_issues(kb)
    assert [(i.path, i.level, i.owner) for i in issues] == [(f"{REVIEW}/gone.md", "error", "")]
    assert "may not be symlinks" in issues[0].message


def test_a_missing_review_index_is_an_error(kb):
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.")
    put(kb, "CT", ct(kb, "open"))
    assert messages_of(kb, index=False) == [
        ("error", "INDEX.md is missing; run kblam review index")]


def test_a_hand_edited_review_index_is_an_error(kb):
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.")
    put(kb, "CT", ct(kb, "open"))
    kb.write(f"{REVIEW}/INDEX.md", "# Review index\n")
    assert messages_of(kb, index=False) == [
        ("error", "INDEX.md differs from the generated review index; it is never edited by hand. Run kblam "
                  "review index to regenerate it")]


def test_the_root_recorded_in_tree_hash_is_the_one_kblam_toml_names(kb):
    kb.write(f"{REVIEW}/challenges/SC-0001.yaml", dump_record(sc()))
    kb.write(".kblam/tree.hash", format_line("old-review", ZERO64))
    issues = [issue for issue in k13_issues(kb) if issue.path == "kblam.toml"]
    assert [(i.owner, i.line, i.message) for i in issues] == [
        ("", 0, "the review root changed from old-review to research-review in kblam.toml; schema 1 fixes "
                "it at init")]


def test_a_registered_id_without_a_record_is_an_error(kb):
    kb.write(f"{REVIEW}/challenges/.keep", "")
    kb.write(".kblam/review-ids", json.dumps(["SC-0001", "CT-0002"]) + "\n")
    issues = k13_issues(kb, index=False)
    assert [(i.path, i.owner, i.message) for i in issues if i.owner] == [
        (f"{REVIEW}/challenges/SC-0001.yaml", "SC-0001",
         "SC-0001 is missing from research-review/; records are never deleted or renamed; restore it from "
         "git"),
        (f"{REVIEW}/tasks/CT-0002.yaml", "CT-0002",
         "CT-0002 is missing from research-review/; records are never deleted or renamed; restore it from "
         "git")]


def test_two_records_that_claim_one_id_are_an_error_on_each(kb):
    """`id` matches the filename (schema_issues) and IDs are unique (K13): the ID is the filename's, and a
    file whose data claims another record's ID is a second claim of it."""
    put(kb, "SC", sc())
    put(kb, "SC", sc() | {"id": "SC-0001"})
    kb.write(f"{REVIEW}/challenges/SC-0002.yaml",
             dump_record(sc() | {"id": "SC-0001"}))
    issues = [i for i in k13_issues(kb) if "claimed by more than one" in i.message]
    assert sorted(i.path for i in issues) == [f"{REVIEW}/challenges/SC-0001.yaml",
                                              f"{REVIEW}/challenges/SC-0002.yaml"]
    assert all(i.level == "error" for i in issues)


# --- the shared challenge and use state ---------------------------------------------------------


def test_challenge_info_resolves_the_source_and_locates_the_assertion(kb_ready, source_repo):
    view, reader = view_and_reader(kb_ready)
    info = challenge_info(view, reader, record_of(view, "SC-0001"))
    assert info.key == TRACE
    assert info.confirmed is True
    assert info.available is True
    assert info.digest == subject_digest("SC", record_of(view, "SC-0001").data)
    start, end = info.span
    assert TRACE_TEXT[start:end] == WORD
    assert info.span == (TRACE_TEXT.index(WORD), TRACE_TEXT.index(WORD) + len(WORD))


def test_a_use_of_a_bound_challenge_and_excerpt_is_current(kb_ready, source_repo):
    put(kb_ready, "CU", cu(kb_ready, "approved"))
    view, reader = view_and_reader(kb_ready)
    rec = record_of(view, "CU-0001")
    assert use_binding_problems(view, reader, rec) == []
    assert use_current(view, reader, rec) is True


def test_an_open_use_is_not_current(kb_ready, source_repo):
    put(kb_ready, "CU", cu(kb_ready, "open"))
    view, reader = view_and_reader(kb_ready)
    assert use_current(view, reader, record_of(view, "CU-0001")) is False


@pytest.mark.parametrize("field,value,expected,advice", [
    ("challenge", "SC-0009", "the challenge SC-0009 is not a record in research-review;",
     "restore SC-0009 from git, then run kblam validate again"),
    ("challenge_bind", ZERO64, "changed since this use was bound", REBIND_FIX),
    ("finding", "F-0009", "the finding F-0009 is not in findings/",
     "restore F-0009 from git, then run kblam validate again"),
    ("finding_fingerprint", "0badf00d0000", "changed since this use was bound", REBIND_FIX),
    ("finding_file_sha256", ZERO64, "file bytes changed since this use was bound", REBIND_FIX),
])
def test_a_use_whose_binding_broke_says_what_changed(kb_ready, source_repo, field, value, expected,
                                                     advice):
    """Each problem names what changed and the command that fixes it: rebind where the record is
    installed and the citation holds, and restoring the record first where it is gone."""
    put(kb_ready, "CU", cu(kb_ready, "approved", **{field: value}))
    view, reader = view_and_reader(kb_ready)
    problems = use_binding_problems(view, reader, record_of(view, "CU-0001"))
    assert any(expected in problem for problem in problems)
    assert all(problem.endswith(f"({advice})") for problem in problems)
    assert use_current(view, reader, record_of(view, "CU-0001")) is False


def test_a_use_of_an_unconfirmed_challenge_is_not_current(kb, source_repo, excerpts):
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.")
    put(kb, "SC", sc("open"))
    excerpts[FINDING] = [excerpt()]
    put(kb, "CU", cu(kb, "approved"))
    view, reader = view_and_reader(kb)
    problems = use_binding_problems(view, reader, record_of(view, "CU-0001"))
    assert any("SC-0001 is open, not confirmed" in problem for problem in problems)


# --- the command each broken binding names: the one that runs in that state -----------------------


def test_a_use_of_a_missing_challenge_says_to_restore_it(kb_ready, source_repo):
    """The challenge is not installed: restore it, then validate to check the remaining prerequisites
    before rebinding (SPEC §5.2.6, §5.2.5 `review rebind`)."""
    put(kb_ready, "CU", cu(kb_ready, "approved"))
    path = f"{REVIEW}/challenges/SC-0001.yaml"
    (kb_ready.root / path).unlink()
    expected = (f"the challenge SC-0001 is not a record in {REVIEW}; a use binds one confirmed "
                f"challenge (restore SC-0001 from git, then run kblam validate again)")
    assert use_problems(kb_ready) == [expected]
    assert [("warning", expected)] == bindings(k13_issues(kb_ready))
    kb_ready.write(path, dump_record(sc("confirmed", repo=source_repo)))
    k13_issues(kb_ready)
    assert run(kb_ready, "validate") == 0
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the challenge is back", "--expect", digest_of(kb_ready, "CU-0001")) == 0


def test_a_use_of_an_open_challenge_says_to_confirm_it(kb, source_repo, excerpts):
    """An open pinned challenge takes the closing decision first; validate checks any remaining rebind
    prerequisites (SPEC §5.2.2 "Status changes", §5.2.3 Confirmation, §5.2.5 `review rebind`)."""
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.")
    put(kb, "SC", sc("open", source=pinned_source(source_repo)))
    excerpts[FINDING] = [excerpt()]
    put(kb, "CU", cu(kb, "approved"))
    expected = f"the challenge SC-0001 is open, not confirmed ({CONFIRM_USE_FIX})"
    assert use_problems(kb) == [expected]
    assert [("warning", expected)] == bindings(k13_issues(kb))
    assert run(kb, "review", "decide", "SC-0001", "--status", "confirmed", "--by", DECIDER, "--reason",
               "the pin holds and the basis is primary", "--expect",
               digest_of(kb, "SC-0001")) == 0
    assert run(kb, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the challenge is confirmed", "--expect", digest_of(kb, "CU-0001")) == 0


@pytest.mark.parametrize("status", ["rejected", "stale"])
def test_a_use_of_a_closed_challenge_says_to_retire_it(kb_ready, source_repo, status):
    """A rejected or retired challenge took the judgement this use bound with it, and rebind binds a
    confirmed challenge only: the advice retires the use, and that decision runs (SPEC §5.2.2 "Status
    changes", §5.2.3 Evaluation, §5.2.5 `review decide`)."""
    put(kb_ready, "CU", cu(kb_ready, "approved"))
    put(kb_ready, "SC", sc(status, source=pinned_source(source_repo)))
    expected = (f"the challenge SC-0001 is {status}, not confirmed (SC-0001 is {status}, so the "
                f"judgement this use bound is gone; retire this use ({RETIRE_USE_FIX}))")
    assert use_problems(kb_ready) == [expected]
    assert run(kb_ready, "review", "decide", "CU-0001", "--status", "stale", "--by", DECIDER,
               "--reason", "the challenge it bound was retired", "--expect",
               digest_of(kb_ready, "CU-0001")) == 0


def test_a_use_of_an_unavailable_source_says_to_restore_it(kb_ready, source_repo):
    """The version the challenge was judged on is not present: rebind refuses, so the advice restores
    the working file and then rebinds, which runs once it is back (SPEC §5.2.3 Evaluation, §5.2.5
    `review rebind`)."""
    put(kb_ready, "SC", sc("confirmed"))
    put(kb_ready, "CU", cu(kb_ready, "approved"))
    (source_repo.root / TRACE_PATH).unlink()
    expected = f"the challenge SC-0001's source is not available ({RESTORE_SOURCE_FIX})"
    assert use_problems(kb_ready) == [expected]
    source_repo.write(TRACE_PATH, TRACE_TEXT)
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason", "the source is back",
               "--expect", digest_of(kb_ready, "CU-0001")) == 0


@pytest.mark.parametrize("state", ["missing", "unreadable"])
def test_a_use_whose_finding_is_gone_says_to_restore_it(kb_ready, source_repo, state):
    """The finding is missing or does not parse: restore or fix it, then validate the remaining rebind
    prerequisites (SPEC §5.2.3 "A use is current", §5.2.5 `review rebind`)."""
    put(kb_ready, "CU", cu(kb_ready, "approved"))
    path = f"findings/calibration/{FINDING}-ratio.md"
    if state == "missing":
        (kb_ready.root / path).unlink()
        expected = (f"the finding {FINDING} is not in findings/ (restore {FINDING} from git, then run "
                    f"kblam validate again)")
    else:
        kb_ready.write(path, "---\nid: F-0001\ntitle: [broken\n---\n\n**Claim.** x\n")
        expected = (f"{FINDING}'s file does not parse, so the use's binding cannot hold (fix {FINDING} "
                    f"(kblam validate names the problem), then run kblam validate again)")
    assert expected in use_problems(kb_ready)
    kb_ready.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.")
    k13_issues(kb_ready)
    assert run(kb_ready, "validate") == 0
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason", "the finding is back",
               "--expect", digest_of(kb_ready, "CU-0001")) == 0


def test_a_use_whose_excerpt_is_gone_says_to_stage_a_new_use(kb, source_repo, monkeypatch):
    """The excerpt the use cites is gone and no other carries its tag: rebind refuses it, so the advice
    is the new use that stages the same use against a current excerpt, and that command runs (SPEC
    §5.2.5 `review rebind`, `use review`)."""
    monkeypatch.setattr(matching, "finding_matches", REAL_FINDING_MATCHES)
    quoted_scene(kb, source_repo)
    put(kb, "CU", cu(kb, "approved", citation={"ordinal": 2, "path": TRACE, "range": [3, 3],
                                               "tag_sha256": ZERO64}))
    expected = f"the finding has no excerpt 2 (it has 1) ({NEW_USE_FIX})"
    assert use_problems(kb) == [expected]
    assert run(kb, "review", "decide", "CU-0001", "--status", "stale", "--by", DECIDER,
               "--reason", "the cited excerpt is gone", "--expect", digest_of(kb, "CU-0001")) == 0
    assert run(kb, "use", "review", "SC-0001", "F-0001", "1", "--by", "reviewer-c",
               "--proponent", "researcher-a") == 0


@pytest.mark.parametrize("citation,expected", [
    ("the printed byte values", "the citation does not describe an excerpt"),
    ({"path": TRACE}, "the citation names no excerpt ordinal"),
    ({"ordinal": "2", "path": TRACE, "range": [3, 3], "tag_sha256": TAG},
     "the citation names no excerpt ordinal"),
    ({"ordinal": 0, "path": TRACE, "range": [3, 3], "tag_sha256": TAG},
     "the citation names no excerpt ordinal"),
    ({"ordinal": 1, "path": TRACE, "range": 3, "tag_sha256": TAG},
     "excerpt 1 covers 3-3, not the cited 3"),
    # An unknown key makes the record invalid too, even where every cited value names an excerpt.
    ({"ordinal": 2, "path": TRACE, "range": [3, 3], "tag_sha256": TAG, "note": "x"},
     "the finding has no excerpt 2 (it has 1)"),
])
def test_a_malformed_citation_says_to_restore_the_use(kb_ready, source_repo, citation, expected):
    """A citation that is no excerpt reference at all — not a mapping, a missing key, a wrong type or
    range, an unknown key — leaves the use structurally invalid: `review decide` refuses it (K13
    structure errors), so the binding problem carries the restore rather than row i's retire. Once the
    record is back, the use is clean and validate passes (SPEC §5.2.3 CU citation, §5.2.5 `review
    rebind`)."""
    path = put(kb_ready, "CU", cu(kb_ready, "approved"))
    original = (kb_ready.root / path).read_bytes()
    put(kb_ready, "CU", cu(kb_ready, "approved", citation=citation))
    assert use_problems(kb_ready) == [f"{expected} ({RESTORE_USE_FIX})"]
    view, reader = view_and_reader(kb_ready)
    assert _use_blocking_issues(view, reader, record_of(view, "CU-0001"))
    assert [("warning", f"{expected} ({RESTORE_USE_FIX})")] == bindings(k13_issues(kb_ready))
    assert run(kb_ready, "review", "decide", "CU-0001", "--status", "stale", "--by", DECIDER, "--reason",
               "the citation is malformed", "--expect", digest_of(kb_ready, "CU-0001")) == 1
    kb_ready.write(path, original)                      # what restoring the installed record writes back
    assert k13_issues(kb_ready) == []
    assert run(kb_ready, "validate") == 0
    assert use_problems(kb_ready) == []


@pytest.mark.parametrize("status", ["open", "approved"])
@pytest.mark.parametrize("citation", ["the printed byte values", None])
def test_an_unsupported_schema_says_to_restore_the_use(kb_ready, source_repo, capsys, status,
                                                       citation):
    """A schema records does not check vouches for no field's type, so the recovery must not read the
    citation: validate reports the structure error in place of raising, and the advice restores the
    record (SPEC §5.2.2 Values, §5.2.4 K13 Structure)."""
    data = cu(kb_ready, status)
    data["schema"] = 2
    data["citation"] = citation
    put(kb_ready, "CU", deciding("CU", data))
    capsys.readouterr()
    assert run(kb_ready, "validate") == 1
    assert "unsupported schema version 2" in capsys.readouterr().out
    view, reader = view_and_reader(kb_ready)
    assert _use_blocking_issues(view, reader, record_of(view, "CU-0001"))
    assert use_problems(kb_ready) == [
        f"the citation does not describe an excerpt ({RESTORE_SCHEMA_FIX})"]
    assert run(kb_ready, "review", "decide", "CU-0001", "--status", "stale", "--by", DECIDER,
               "--reason", "the schema is not supported", "--expect", digest_of(kb_ready, "CU-0001")) == 1
    assert use_problems(kb_ready) == [
        f"the citation does not describe an excerpt ({RESTORE_SCHEMA_FIX})"]


@pytest.mark.parametrize("status", ["open", "approved"])
@pytest.mark.parametrize("fault,diagnostic", [("missing_schema", "missing key 'schema'"),
                                              ("extra_key", "unknown key 'surplus'")])
def test_a_use_structure_error_says_to_restore_it(kb_ready, source_repo, capsys, status, fault,
                                                  diagnostic):
    """Any structure error of the use's own blocks every write command (SPEC §5.2.4 K13 Structure,
    §5.2.5 `review rebind`, SPEC 253): the recovery prints the restore and no mutation, the rebind it no
    longer prints is refused in that state, and once the record is back validate names row k and that
    rebind runs."""
    good = cu(kb_ready, status, challenge_bind=ZERO64)
    path = put(kb_ready, "CU", good)
    original = (kb_ready.root / path).read_bytes()
    data = cu(kb_ready, status, challenge_bind=ZERO64)
    if fault == "missing_schema":
        del data["schema"]
    else:
        data["surplus"] = 1
    put(kb_ready, "CU", deciding("CU", data))
    changed = (f"the challenge SC-0001 changed since this use was bound: its subject digest is "
               f"{challenge_binding(kb_ready)}, not {ZERO64}")
    assert use_problems(kb_ready) == [f"{changed} ({RESTORE_STRUCTURE_FIX})"]
    view, reader = view_and_reader(kb_ready)
    assert _use_blocking_issues(view, reader, record_of(view, "CU-0001"))
    assert all("kblam review rebind" not in problem for problem in use_problems(kb_ready))
    capsys.readouterr()
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the binding changed", "--expect", digest_of(kb_ready, "CU-0001")) == 1
    assert diagnostic in capsys.readouterr().out
    kb_ready.write(path, original)
    assert use_problems(kb_ready) == [f"{changed} ({REBIND_FIX})"]
    k13_issues(kb_ready)                                # the index the put above left behind
    capsys.readouterr()
    assert run(kb_ready, "validate") == 0
    assert f"({REBIND_FIX})" in capsys.readouterr().out
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the structure is restored", "--expect", digest_of(kb_ready, "CU-0001")) == 0


@pytest.mark.parametrize("status", ["open", "approved"])
@pytest.mark.parametrize("fault,diagnostic", [
    ("independence", "is CU-0001's proponent; a closing decision needs someone else"),
    ("transition", "a use cannot go from withdrawn to approved"),
    ("evidence_directory", "is a directory, not a file"),
    ("evidence_pin", "not " + "b" * 40),
])
def test_a_use_with_blocking_history_says_to_restore_it(kb_ready, source_repo, capsys, status,
                                                       fault, diagnostic):
    """A command appends to history, so it cannot fix a historical transition, independence or evidence
    structure error. Restore the record before rebinding (SPEC §5.2.4 Structure, D26)."""
    good = cu(kb_ready, status, challenge_bind=ZERO64)
    path = put(kb_ready, "CU", good)
    original = (kb_ready.root / path).read_bytes()
    data = cu(kb_ready, "approved", challenge_bind=ZERO64)
    if fault == "independence":
        data["decisions"][0]["by"] = data["proponent"]
    elif fault == "transition":
        earlier = dict(data["decisions"][0], status="withdrawn")
        data["decisions"].insert(0, earlier)
    elif fault == "evidence_directory":
        kb_ready.write("evidence/2026-09-22-ratio/manifest.md", "the manifest\n")
        data["decisions"][0]["evidence"] = [
            {**ref("evidence/2026-09-22-ratio", ZERO64), "locator": "the manifest",
             "provenance": "observed"}]
    else:
        data["decisions"][0]["evidence"] = [
            {**ref(repo=SOURCE_REPO, commit=source_repo.head(), blob="b" * 40),
             "locator": "row 102", "provenance": "observed"}]
    if status == "open":
        data["status"] = "open"
        data["decisions"].append(dict(data["decisions"][-1], status="open", by=DECIDER,
                                      evidence=[]))
    put(kb_ready, "CU", data)
    view, reader = view_and_reader(kb_ready)
    rec = record_of(view, "CU-0001")
    assert not records.schema_issues(rec, staged=False)
    blocked = _use_blocking_issues(view, reader, rec)
    assert any(diagnostic in issue.message for issue in blocked)
    assert _use_blocking_issues(view, reader, rec, status="stale")
    if fault.startswith("evidence_"):
        assert not decisions.decision_issues(rec)
    problems = use_problems(kb_ready)
    assert problems and all(problem.endswith(f"({RESTORE_STRUCTURE_FIX})") for problem in problems)
    assert all("kblam review rebind" not in problem and "kblam review decide" not in problem
               for problem in problems)
    for command in [("review", "rebind", "CU-0001"),
                    ("review", "decide", "CU-0001", "--status", "stale")]:
        capsys.readouterr()
        assert run(kb_ready, *command, "--by", DECIDER, "--reason", "the binding changed", "--expect",
                   digest_of(kb_ready, "CU-0001")) == 1
        assert diagnostic in capsys.readouterr().out
    kb_ready.write(path, original)
    view, reader = view_and_reader(kb_ready)
    assert _use_blocking_issues(view, reader, record_of(view, "CU-0001")) == []
    k13_issues(kb_ready)
    capsys.readouterr()
    assert run(kb_ready, "validate") == 0
    assert f"({REBIND_FIX})" in capsys.readouterr().out
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the history is restored", "--expect", digest_of(kb_ready, "CU-0001")) == 0


@pytest.mark.parametrize("status", ["open", "approved"])
def test_a_duplicate_use_id_blocks_rebind_and_retirement(kb_ready, source_repo, capsys, status):
    path = put(kb_ready, "CU", cu(kb_ready, status, challenge_bind=ZERO64))
    copy = f"{REVIEW}/uses/CU-0002.yaml"
    original = dump_record(cu(kb_ready, status, id="CU-0002"))
    kb_ready.write(copy, (kb_ready.root / path).read_bytes())
    view, reader = view_and_reader(kb_ready)
    assert any("claimed by more than one record file" in issue.message
               for issue in _use_blocking_issues(view, reader, record_of(view, "CU-0001")))
    assert all(problem.endswith(f"({RESTORE_STRUCTURE_FIX})") for problem in use_problems(kb_ready))
    for command in [("review", "rebind", "CU-0001"),
                    ("review", "decide", "CU-0001", "--status", "stale")]:
        capsys.readouterr()
        assert run(kb_ready, *command, "--by", DECIDER, "--reason", "the binding changed", "--expect",
                   digest_of(kb_ready, "CU-0001")) == 1
        assert "claimed by more than one record file" in capsys.readouterr().out
    kb_ready.write(copy, original)                    # restore the other record's own ID
    view, reader = view_and_reader(kb_ready)
    assert _use_blocking_issues(view, reader, record_of(view, "CU-0001")) == []
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the duplicate is gone", "--expect", digest_of(kb_ready, "CU-0001")) == 0


@pytest.mark.parametrize("status", ["open", "approved"])
@pytest.mark.parametrize("retire", [False, True])
def test_a_sole_misplaced_use_blocks_the_canonical_write(kb_ready, source_repo, capsys, status, retire):
    """The writer creates the canonical copy without removing the misplaced one; the projected guard
    must see the duplicate that either rebind or retirement would create."""
    canonical = f"{REVIEW}/uses/CU-0001.yaml"
    misplaced = f"{REVIEW}/tasks/CU-0001.yaml"
    original = dump_record(cu(kb_ready, status, challenge_bind=ZERO64))
    kb_ready.write(misplaced, original)
    if retire:
        put(kb_ready, "SC", sc("stale", source=pinned_source(source_repo)))
    view, reader = view_and_reader(kb_ready)
    assert [rec.path for rec in view.records if rec.id == "CU-0001"] == [misplaced]
    assert not any("claimed by more than one" in issue.message for issue in k13(view, reader))
    blocked = _use_blocking_issues(view, reader, record_of(view, "CU-0001"),
                                  status="stale" if retire else status)
    assert {issue.path for issue in blocked if "claimed by more than one" in issue.message} == {
        canonical, misplaced}
    assert all(problem.endswith(f"({RESTORE_STRUCTURE_FIX})") for problem in use_problems(kb_ready))
    assert all("kblam review rebind" not in problem and "kblam review decide" not in problem
               for problem in use_problems(kb_ready))
    command = ("review", "decide", "CU-0001", "--status", "stale") if retire else (
        "review", "rebind", "CU-0001")
    capsys.readouterr()
    assert run(kb_ready, *command, "--by", DECIDER, "--reason", "renew the decision", "--expect",
               digest_of(kb_ready, "CU-0001")) == 1
    assert "claimed by more than one record file" in capsys.readouterr().out
    kb_ready.write(canonical, original)
    (kb_ready.root / misplaced).unlink()               # restoring the canonical layout from git
    recovery = ("SC-0001 is stale, so the judgement this use bound is gone; "
                f"retire this use ({RETIRE_USE_FIX})") if retire else REBIND_FIX
    assert all(problem.endswith(f"({recovery})") for problem in use_problems(kb_ready))
    k13_issues(kb_ready)
    capsys.readouterr()
    assert run(kb_ready, "validate") == 0
    assert f"({recovery})" in capsys.readouterr().out
    assert run(kb_ready, *command, "--by", DECIDER, "--reason", "the layout is restored", "--expect",
               digest_of(kb_ready, "CU-0001")) == 0


@pytest.mark.parametrize("fault", ["status", "schema", "missing_key", "wrong_type"])
def test_a_malformed_challenge_is_restored_before_rebind(kb_ready, source_repo, capsys, fault):
    put(kb_ready, "CU", cu(kb_ready, "approved", challenge_bind=ZERO64))
    data = sc("confirmed", repo=source_repo)
    path = f"{REVIEW}/challenges/SC-0001.yaml"
    original = (kb_ready.root / path).read_bytes()
    if fault == "status":
        data["status"] = "approved"                    # not in the SC vocabulary
    elif fault == "schema":
        data["schema"] = 2
    elif fault == "missing_key":
        del data["source"]
    else:
        data["source"] = "not a reference"
    put(kb_ready, "SC", data)
    recovery = "restore SC-0001 from git, then run kblam validate again"
    problems = use_problems(kb_ready)
    assert problems and all(problem.endswith(f"({recovery})") for problem in problems)
    assert all("kblam review rebind" not in problem for problem in problems)
    if fault == "status":
        capsys.readouterr()
        assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
                   "renew the binding", "--expect", digest_of(kb_ready, "CU-0001")) == 1
        assert "the challenge SC-0001 is approved, not confirmed" in capsys.readouterr().err
    kb_ready.write(path, original)
    k13_issues(kb_ready)
    capsys.readouterr()
    assert run(kb_ready, "validate") == 0
    assert f"({REBIND_FIX})" in capsys.readouterr().out
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the challenge is restored", "--expect", digest_of(kb_ready, "CU-0001")) == 0


@pytest.mark.parametrize("fault,diagnostic", [
    ("registry", "review-ids"),
    ("root", "the review root changed from old-review"),
])
def test_global_mutation_gates_are_fixed_before_rebind(kb_ready, source_repo, capsys, fault,
                                                      diagnostic):
    put(kb_ready, "CU", cu(kb_ready, "approved", challenge_bind=ZERO64))
    if fault == "registry":
        path = ".kblam/review-ids"
        original = json.dumps(["SC-0001", "CU-0001"]) + "\n"
        kb_ready.write(path, "not JSON\n")
    else:
        path = ".kblam/tree.hash"
        original = format_line(REVIEW, ZERO64)
        kb_ready.write(path, format_line("old-review", ZERO64))
    recovery = ("fix the review-root problem kblam validate reports first (it blocks every write), "
                "then run kblam validate again")
    problems = use_problems(kb_ready)
    assert problems and all(problem.endswith(f"({recovery})") for problem in problems)
    assert all("kblam review rebind" not in problem for problem in problems)
    capsys.readouterr()
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "renew the binding", "--expect", digest_of(kb_ready, "CU-0001")) == 1
    assert diagnostic in capsys.readouterr().err
    kb_ready.write(path, original)
    k13_issues(kb_ready)
    capsys.readouterr()
    assert run(kb_ready, "validate") == 0
    assert f"({REBIND_FIX})" in capsys.readouterr().out
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the write gate is restored", "--expect", digest_of(kb_ready, "CU-0001")) == 0


@pytest.mark.parametrize("refusal", [None, "the write gate is broken"])
def test_the_global_mutation_gate_is_checked_once_per_validate(kb_ready, source_repo, monkeypatch,
                                                             refusal):
    for rid in ["CU-0001", "CU-0002"]:
        put(kb_ready, "CU", cu(kb_ready, "approved", id=rid, challenge_bind=ZERO64))
    calls = []

    def gate(cfg):
        calls.append(cfg)
        return refusal

    monkeypatch.setattr(writes, "mutation_refusal", gate)
    view = load_view(kb_ready.cfg)
    for count in [1, 2]:
        rules.validate(view)                          # each validation has its own shared reader
        assert len(calls) == count


@pytest.mark.parametrize("prerequisite", ["missing", "open", "stale", "use_structure"])
def test_global_gate_recovery_has_the_required_priority(kb_ready, source_repo, prerequisite):
    data = cu(kb_ready, "approved", challenge_bind=ZERO64)
    if prerequisite == "missing":
        data["challenge"] = "SC-0009"
    elif prerequisite == "open":
        put(kb_ready, "SC", sc("open"))
    elif prerequisite == "stale":
        put(kb_ready, "SC", sc("stale", source=pinned_source(source_repo)))
    else:
        data["surplus"] = 1
    put(kb_ready, "CU", deciding("CU", data))
    kb_ready.write(".kblam/review-ids", "not JSON\n")
    recovery = RESTORE_STRUCTURE_FIX if prerequisite == "use_structure" else (
        "fix the review-root problem kblam validate reports first (it blocks every write), "
        "then run kblam validate again")
    assert all(problem.endswith(f"({recovery})") for problem in use_problems(kb_ready))
    assert all("kblam review rebind" not in problem and "kblam review decide" not in problem
               and "kblam challenge pin" not in problem for problem in use_problems(kb_ready))


@pytest.mark.parametrize("copy_state", ["identical", "unreadable"])
def test_an_ambiguous_finding_is_fixed_before_rebind(kb_ready, source_repo, capsys, monkeypatch,
                                                   copy_state):
    from kblam import review_write

    lookup = review_write._one_finding
    calls = []

    def one_finding(cfg, view, finding_id, rec_id, status, reopen):
        calls.append((finding_id, rec_id, status, reopen))
        return lookup(cfg, view, finding_id, rec_id, status, reopen)

    monkeypatch.setattr(review_write, "_one_finding", one_finding)
    put(kb_ready, "CU", cu(kb_ready, "approved", challenge_bind=ZERO64))
    copy = "findings/calibration/F-0001-copy.md"
    original = finding_of(kb_ready).raw
    kb_ready.write(copy, original if copy_state == "identical" else
                   "---\nid: F-0001\ntitle: [broken\n---\n\n**Claim.** x\n")
    recovery = ("fix F-0001 so exactly one file in findings/ holds it (kblam validate names them), "
                "then run kblam validate again")
    problems = use_problems(kb_ready)
    assert calls == [("F-0001", "CU-0001", "approved", False)]
    assert problems and all(problem.endswith(f"({recovery})") for problem in problems)
    assert all("kblam review rebind" not in problem for problem in problems)
    capsys.readouterr()
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "renew the binding", "--expect", digest_of(kb_ready, "CU-0001")) == 1
    assert "F-0001 is not one readable finding" in capsys.readouterr().err
    (kb_ready.root / copy).unlink()
    k13_issues(kb_ready)
    capsys.readouterr()
    assert run(kb_ready, "validate") == 0
    assert f"({REBIND_FIX})" in capsys.readouterr().out
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the finding is unique again", "--expect", digest_of(kb_ready, "CU-0001")) == 0


@pytest.mark.parametrize("status", ["open", "approved"])
@pytest.mark.parametrize("change", ["clean", "challenge_bind", "finding_fingerprint",
                                    "finding_file_sha256", "citation_path", "citation_range",
                                    "citation_ordinal", "evidence_missing", "evidence_stale"])
def test_the_use_guard_agrees_with_rebind_on_cured_state(kb_ready, source_repo, status, change):
    """Only persistent structure blocks the guard: rebind renews the bindings and makes the old effective
    decision's merely missing or changed evidence historical (SPEC §5.2.4 Severity)."""
    data = cu(kb_ready, status)
    if change == "challenge_bind":
        data[change] = ZERO64
    elif change == "finding_fingerprint":
        data[change] = "0badf00d0000"
    elif change == "finding_file_sha256":
        data[change] = ZERO64
    elif change == "citation_path":
        data["citation"]["path"] = "evidence/other.md"
    elif change == "citation_range":
        data["citation"]["range"] = [2, 3]
    elif change == "citation_ordinal":
        data["citation"]["ordinal"] = 2
    elif change.startswith("evidence_"):
        if status == "open":
            data["decisions"] = [{"date": datetime.date(2026, 9, 28), "by": DECIDER, "status": "open",
                                  "reason": "reviewed the record", "evidence": [],
                                  "bind": subject_digest("CU", data)}]
        data["decisions"][-1]["evidence"] = [
            {**ref("evidence/manifest.md", ZERO64), "locator": "the manifest",
             "provenance": "observed"}]
        if change == "evidence_stale":
            kb_ready.write("evidence/manifest.md", "changed evidence\n")
    if status == "approved":
        data["decisions"][-1]["bind"] = subject_digest("CU", data)
    put(kb_ready, "CU", data)
    view, reader = view_and_reader(kb_ready)
    assert _use_blocking_issues(view, reader, record_of(view, "CU-0001")) == []
    assert all(problem.endswith(f"({REBIND_FIX})") for problem in use_problems(kb_ready))
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "renew the bindings and decision", "--expect", digest_of(kb_ready, "CU-0001")) == 0


@pytest.mark.parametrize("status", ["open", "approved"])
@pytest.mark.parametrize("change", ["challenge_missing", "finding_missing", "citation_unkept",
                                    "excerpt_unverified", "excerpt_binary"])
def test_the_use_guard_agrees_with_retirement_on_cured_state(kb_ready, source_repo, excerpts, status,
                                                            change):
    data = cu(kb_ready, status)
    if change == "challenge_missing":
        data["challenge"] = "SC-0009"
    elif change == "finding_missing":
        data["finding"] = "F-0009"
    elif change == "citation_unkept":
        data["citation"]["tag_sha256"] = ZERO64
    else:
        excerpts[FINDING] = [excerpt(verified=False, binary=change == "excerpt_binary",
                                    problem="the quote is gone")]
    if status == "approved":
        data["decisions"][-1]["bind"] = subject_digest("CU", data)
    put(kb_ready, "CU", data)
    view, reader = view_and_reader(kb_ready)
    assert _use_blocking_issues(view, reader, record_of(view, "CU-0001")) == []
    assert run(kb_ready, "review", "decide", "CU-0001", "--status", "stale", "--by", DECIDER,
               "--reason", "the use no longer applies", "--expect", digest_of(kb_ready, "CU-0001")) == 0


@pytest.mark.parametrize("fault", ["no_decisions", "last_bind", "last_status"])
@pytest.mark.parametrize("retire", [False, True])
def test_endpoint_errors_are_checked_after_the_proposed_decision(kb_ready, source_repo, fault, retire):
    """Appendable endpoint errors are not historical damage: the selected command cures them."""
    data = cu(kb_ready, "approved", challenge_bind=ZERO64)
    if fault == "no_decisions":
        data["decisions"] = []
    elif fault == "last_bind":
        data["decisions"][-1]["bind"] = ZERO64
    else:
        data["status"] = "open"                        # approved -> open is a valid appended decision
    put(kb_ready, "CU", data)
    if retire:
        put(kb_ready, "SC", sc("stale", source=pinned_source(source_repo)))
    view, reader = view_and_reader(kb_ready)
    rec = record_of(view, "CU-0001")
    assert decisions.decision_issues(rec)
    assert _use_blocking_issues(view, reader, rec, status="stale" if retire else rec.status) == []
    recovery = ("SC-0001 is stale, so the judgement this use bound is gone; "
                f"retire this use ({RETIRE_USE_FIX})") if retire else REBIND_FIX
    assert all(problem.endswith(f"({recovery})") for problem in use_problems(kb_ready))
    command = ("review", "decide", "CU-0001", "--status", "stale") if retire else (
        "review", "rebind", "CU-0001")
    assert run(kb_ready, *command, "--by", DECIDER, "--reason", "renew the decision", "--expect",
               digest_of(kb_ready, "CU-0001")) == 0


def test_the_guard_uses_the_selected_commands_transition(kb_ready, source_repo):
    """The same endpoint discrepancy blocks rebind but not stale: withdrawn -> approved is invalid,
    whereas withdrawn -> stale is allowed (SPEC §5.2.2 Status changes)."""
    data = cu(kb_ready, "withdrawn", challenge_bind=ZERO64)
    data["status"] = "approved"
    put(kb_ready, "CU", data)
    view, reader = view_and_reader(kb_ready)
    rec = record_of(view, "CU-0001")
    assert _use_blocking_issues(view, reader, rec, status="approved")
    assert _use_blocking_issues(view, reader, rec, status="stale") == []
    assert all(problem.endswith(f"({RESTORE_STRUCTURE_FIX})") for problem in use_problems(kb_ready))
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "renew the decision", "--expect", digest_of(kb_ready, "CU-0001")) == 1
    put(kb_ready, "SC", sc("stale", source=pinned_source(source_repo)))
    recovery = ("SC-0001 is stale, so the judgement this use bound is gone; "
                f"retire this use ({RETIRE_USE_FIX})")
    assert all(problem.endswith(f"({recovery})") for problem in use_problems(kb_ready))
    assert run(kb_ready, "review", "decide", "CU-0001", "--status", "stale", "--by", DECIDER,
               "--reason", "the judgement is retired", "--expect", digest_of(kb_ready, "CU-0001")) == 0


def test_the_new_use_advice_names_an_excerpt_that_does_not_verify(kb, source_repo, monkeypatch, capsys):
    """The reviewer's state: the cited excerpt is gone and the finding's remaining excerpt no longer
    verifies, so `use review` refuses the new use. The advice names that condition, the command it warns
    about is refused in that state, and restoring the source makes the same command run (SPEC §5.2.5
    `review rebind`, `use review`)."""
    monkeypatch.setattr(matching, "finding_matches", REAL_FINDING_MATCHES)
    quoted_scene(kb, source_repo)
    put(kb, "CU", cu(kb, "approved", citation={"ordinal": 2, "path": TRACE, "range": [3, 3],
                                               "tag_sha256": ZERO64}))
    source_repo.write(TRACE_PATH, TRACE_TEXT.replace(LINE3, "Row 102: changed text."))
    assert use_problems(kb) == [f"the finding has no excerpt 2 (it has 1) ({NEW_USE_FIX})"]
    assert run(kb, "review", "decide", "CU-0001", "--status", "stale", "--by", DECIDER,
               "--reason", "the cited excerpt is gone", "--expect", digest_of(kb, "CU-0001")) == 0
    capsys.readouterr()
    assert run(kb, "use", "review", "SC-0001", "F-0001", "1", "--by", "reviewer-c",
               "--proponent", "researcher-a") == 1
    assert "is not a verified text match" in capsys.readouterr().err
    source_repo.write(TRACE_PATH, TRACE_TEXT)
    assert run(kb, "use", "review", "SC-0001", "F-0001", "1", "--by", "reviewer-c",
               "--proponent", "researcher-a") == 0


def test_a_use_whose_excerpt_tag_is_ambiguous_says_to_retire_and_restage(kb, source_repo, monkeypatch):
    monkeypatch.setattr(matching, "finding_matches", REAL_FINDING_MATCHES)
    quoted_scene(kb, source_repo)
    body = quoted(f"{TRACE}:3", LINE3)
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.", body=f"{body}\n\n{body}")
    view, reader = view_and_reader(kb)
    matches = matching.finding_matches(view, reader, finding_of(kb))
    assert len(matches) == 2 and matches[0].tag_sha256 == matches[1].tag_sha256
    put(kb, "CU", cu(kb, "approved", citation={"ordinal": 3, "path": TRACE, "range": [3, 3],
                                               "tag_sha256": matches[0].tag_sha256}))
    assert use_problems(kb) == [f"the finding has no excerpt 3 (it has 2) ({NEW_USE_FIX})"]
    assert run(kb, "review", "decide", "CU-0001", "--status", "stale", "--by", DECIDER,
               "--reason", "the cited tag is ambiguous", "--expect", digest_of(kb, "CU-0001")) == 0
    assert run(kb, "use", "review", "SC-0001", "F-0001", "1", "--by", "reviewer-c",
               "--proponent", "researcher-a") == 0


def test_a_use_whose_excerpt_moved_says_to_rebind(kb, source_repo, monkeypatch):
    """The cited ordinal is gone but exactly one excerpt still carries its tag: rebind relocates the
    citation to it, so that is the advice, and it runs (SPEC §5.2.5 `review rebind`)."""
    monkeypatch.setattr(matching, "finding_matches", REAL_FINDING_MATCHES)
    quoted_scene(kb, source_repo)
    view, reader = view_and_reader(kb)
    tag = next(m.tag_sha256 for m in matching.finding_matches(view, reader, finding_of(kb)))
    put(kb, "CU", cu(kb, "approved", citation={"ordinal": 2, "path": TRACE, "range": [3, 3],
                                               "tag_sha256": tag}))
    expected = f"the finding has no excerpt 2 (it has 1) ({REBIND_FIX})"
    assert use_problems(kb) == [expected]
    assert run(kb, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason", "the excerpt moved",
               "--expect", digest_of(kb, "CU-0001")) == 0


def test_a_citation_that_names_another_excerpt_is_a_binding_problem(kb_ready, source_repo, requests=None):
    put(kb_ready, "CU", cu(kb_ready, "approved",
                           citation={"ordinal": 2, "path": TRACE, "range": [3, 3], "tag_sha256": TAG}))
    view, reader = view_and_reader(kb_ready)
    problems = use_binding_problems(view, reader, record_of(view, "CU-0001"))
    assert any("the finding has no excerpt 2 (it has 1)" in problem for problem in problems)


def test_a_citation_that_names_the_wrong_range_or_tag_is_a_binding_problem(kb_ready, source_repo):
    put(kb_ready, "CU", cu(kb_ready, "approved",
                           citation={"ordinal": 1, "path": TRACE, "range": [2, 3], "tag_sha256": ZERO64}))
    view, reader = view_and_reader(kb_ready)
    problems = use_binding_problems(view, reader, record_of(view, "CU-0001"))
    assert any("covers 3-3, not the cited 2-3" in problem for problem in problems)
    assert any(f"tag_sha256 {TAG}, not the cited {ZERO64}" in problem for problem in problems)


def test_a_binary_exempt_excerpt_never_qualifies_as_a_use(kb_ready, source_repo, excerpts):
    excerpts[FINDING] = [excerpt(verified=False, binary=True)]
    put(kb_ready, "CU", cu(kb_ready, "approved"))
    view, reader = view_and_reader(kb_ready)
    problems = use_binding_problems(view, reader, record_of(view, "CU-0001"))
    assert any("binary-exempt, which never qualifies" in problem for problem in problems)


def test_an_excerpt_that_does_not_pass_k10_is_a_binding_problem(kb_ready, source_repo, excerpts):
    excerpts[FINDING] = [excerpt(verified=False, problem="the excerpt does not occur at line 3")]
    put(kb_ready, "CU", cu(kb_ready, "approved"))
    view, reader = view_and_reader(kb_ready)
    problems = use_binding_problems(view, reader, record_of(view, "CU-0001"))
    assert any("is not a verified text match (the excerpt does not occur at line 3)" in problem
               for problem in problems)


def test_a_use_of_an_unreadable_challenge_says_to_restore_it(kb_ready, source_repo):
    put(kb_ready, "CU", cu(kb_ready, "approved"))
    path = f"{REVIEW}/challenges/SC-0001.yaml"
    kb_ready.write(path, "schema: 1\nid: [broken\n")
    expected = ("the challenge SC-0001 did not parse, so the use's binding cannot hold "
                "(restore SC-0001 from git, then run kblam validate again)")
    assert use_problems(kb_ready) == [expected]
    kb_ready.write(path, dump_record(sc("confirmed", repo=source_repo)))
    k13_issues(kb_ready)
    assert run(kb_ready, "validate") == 0
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the challenge parses again", "--expect", digest_of(kb_ready, "CU-0001")) == 0


def test_an_open_challenges_missing_source_is_restored_before_confirmation(kb, source_repo, excerpts):
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.")
    put(kb, "SC", sc("open"))
    excerpts[FINDING] = [excerpt()]
    put(kb, "CU", cu(kb, "open"))
    (source_repo.root / TRACE_PATH).unlink()
    recovery = "restore SC-0001's source, then run kblam validate again"
    assert use_problems(kb) == [
        f"the challenge SC-0001 is open, not confirmed ({recovery})",
        f"the challenge SC-0001's source is not available ({recovery})"]
    source_repo.write(TRACE_PATH, TRACE_TEXT)
    k13_issues(kb)
    assert run(kb, "validate") == 0
    assert use_problems(kb) == [f"the challenge SC-0001 is open, not confirmed ({PIN_CONFIRM_FIX})"]


@pytest.mark.parametrize("checkout", ["committed", "dirty", "crlf"])
def test_an_open_provisional_challenge_is_pinned_before_confirmation(kb, source_repo, excerpts,
                                                                    checkout):
    raw = TRACE_TEXT.encode("utf-8")
    if checkout == "dirty":
        raw += b"Row 104: bytes 0x41 0x42\n"
    elif checkout == "crlf":
        raw = raw.replace(b"\n", b"\r\n")
    source_repo.write(TRACE_PATH, raw)
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.")
    put(kb, "SC", sc("open", source=source(sha=sources.sha256_hex(raw))))
    excerpts[FINDING] = [excerpt()]
    put(kb, "CU", cu(kb, "open"))
    expected = f"the challenge SC-0001 is open, not confirmed ({PIN_CONFIRM_FIX})"
    assert use_problems(kb) == [expected]
    assert bindings(k13_issues(kb)) == [("warning", expected)]
    extra = []
    if checkout != "committed":
        copy = "evidence/2026-09-28-trace-copy/trace.md"
        kb.write(copy, raw)
        extra = ["--snapshot", copy]
    assert run(kb, "challenge", "pin", "SC-0001", "--expect", digest_of(kb, "SC-0001"), *extra) == 0
    assert run(kb, "review", "decide", "SC-0001", "--status", "confirmed", "--by", DECIDER,
               "--reason", "the pinned source supports the judgement", "--expect",
               digest_of(kb, "SC-0001")) == 0
    assert run(kb, "validate") == 0
    assert all(problem.endswith(f"({REBIND_FIX})") for problem in use_problems(kb))
    assert run(kb, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the challenge is pinned and confirmed", "--expect", digest_of(kb, "CU-0001")) == 0


def test_a_stale_challenge_and_changed_digest_share_retirement_advice(kb_ready, source_repo):
    put(kb_ready, "CU", cu(kb_ready, "approved", challenge_bind=ZERO64,
                           finding_fingerprint="0badf00d0000"))
    put(kb_ready, "SC", sc("stale", source=pinned_source(source_repo)))
    recovery = ("SC-0001 is stale, so the judgement this use bound is gone; "
                f"retire this use ({RETIRE_USE_FIX})")
    problems = use_problems(kb_ready)
    assert len(problems) == 3
    assert all(problem.endswith(f"({recovery})") for problem in problems)
    assert all("kblam review rebind" not in problem for problem in problems)
    assert run(kb_ready, "review", "decide", "CU-0001", "--status", "stale", "--by", DECIDER,
               "--reason", "the judgement is retired", "--expect", digest_of(kb_ready, "CU-0001")) == 0


def test_an_unavailable_challenge_and_changed_finding_share_restore_advice(kb_ready, source_repo,
                                                                         capsys):
    put(kb_ready, "SC", sc("confirmed"))
    put(kb_ready, "CU", cu(kb_ready, "approved", finding_fingerprint="0badf00d0000"))
    (source_repo.root / TRACE_PATH).unlink()
    problems = use_problems(kb_ready)
    assert len(problems) == 2
    assert all(problem.endswith(f"({RESTORE_SOURCE_FIX})") for problem in problems)
    assert all("kblam review rebind" not in problem for problem in problems)
    source_repo.write(TRACE_PATH, TRACE_TEXT)
    capsys.readouterr()
    assert run(kb_ready, "validate") == 1            # the confirmed SC's source is still provisional
    assert f"({REBIND_FIX})" in capsys.readouterr().out
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the judged source is back", "--expect", digest_of(kb_ready, "CU-0001")) == 0


def test_an_open_provisional_challenge_and_changed_citation_share_pin_advice(kb_ready, source_repo):
    put(kb_ready, "SC", sc("open"))
    put(kb_ready, "CU", cu(kb_ready, "open", challenge_bind=ZERO64,
                           citation={"ordinal": 1, "path": TRACE, "range": [2, 3], "tag_sha256": TAG}))
    problems = use_problems(kb_ready)
    assert len(problems) == 3
    assert all(problem.endswith(f"({PIN_CONFIRM_FIX})") for problem in problems)
    assert all("kblam review rebind" not in problem for problem in problems)
    assert run(kb_ready, "challenge", "pin", "SC-0001", "--expect", digest_of(kb_ready, "SC-0001")) == 0
    assert run(kb_ready, "review", "decide", "SC-0001", "--status", "confirmed", "--by", DECIDER,
               "--reason", "the source is pinned", "--expect", digest_of(kb_ready, "SC-0001")) == 0
    assert run(kb_ready, "validate") == 0
    assert all(problem.endswith(f"({REBIND_FIX})") for problem in use_problems(kb_ready))
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the citation range changed", "--expect", digest_of(kb_ready, "CU-0001")) == 0


def test_an_approved_use_of_an_unverified_real_excerpt_says_to_restore_text(kb, source_repo,
                                                                         monkeypatch):
    monkeypatch.setattr(matching, "finding_matches", REAL_FINDING_MATCHES)
    quoted_scene(kb, source_repo)
    put(kb, "CU", quoted_use(kb))
    source_repo.write(TRACE_PATH, TRACE_TEXT.replace(LINE3, "Row 102: changed text."))
    view, reader = view_and_reader(kb)
    [match] = matching.finding_matches(view, reader, finding_of(kb))
    assert not match.verified and not match.binary
    assert challenge_info(view, reader, record_of(view, "SC-0001")).available
    assert use_problems(kb) == [
        f"excerpt 1 is not a verified text match ({match.problem}) ({RESTORE_TEXT_FIX})"]
    source_repo.write(TRACE_PATH, TRACE_TEXT)
    k13_issues(kb)
    assert run(kb, "validate") == 0
    assert run(kb, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the quoted text is restored", "--expect", digest_of(kb, "CU-0001")) == 0


def test_an_approved_use_of_a_binary_real_excerpt_says_to_restore_or_retire(kb, source_repo,
                                                                        monkeypatch):
    monkeypatch.setattr(matching, "finding_matches", REAL_FINDING_MATCHES)
    quoted_scene(kb, source_repo)
    source_repo.write(TRACE_PATH, b"\x00\x01binary")
    add_finding(kb, "@0x0", "anything at all")
    put(kb, "CU", quoted_use(kb))
    assert use_problems(kb) == [
        f"excerpt 1 is binary-exempt, which never qualifies as a use ({RESTORE_TEXT_FIX})"]
    assert run(kb, "review", "decide", "CU-0001", "--status", "stale", "--by", DECIDER,
               "--reason", "binary excerpts never qualify", "--expect", digest_of(kb, "CU-0001")) == 0


def test_a_changed_challenge_digest_with_no_other_failure_says_to_rebind(kb_ready, source_repo):
    put(kb_ready, "CU", cu(kb_ready, "approved", challenge_bind=ZERO64))
    expected = (f"the challenge SC-0001 changed since this use was bound: its subject digest is "
                f"{challenge_binding(kb_ready)}, not {ZERO64} ({REBIND_FIX})")
    assert use_problems(kb_ready) == [expected]
    assert run(kb_ready, "review", "rebind", "CU-0001", "--by", DECIDER, "--reason",
               "the judgement was checked again", "--expect", digest_of(kb_ready, "CU-0001")) == 0


# --- confirmation ------------------------------------------------------------------------------


def test_a_confirmed_challenge_needs_primary_support(kb_ready, source_repo):
    put(kb_ready, "SC", sc("confirmed", repo=source_repo,
                           basis=[basis(provenance="inferred", role="internal-inconsistency")]))
    found = [i.message for i in k13_issues(kb_ready) if "no primary support" in i.message]
    assert len(found) == 1
    assert "provenance is one of observed, decoded" in found[0]


def test_a_confirmed_challenge_needs_an_independent_reviewer(kb_ready, source_repo):
    data = sc("confirmed", repo=source_repo)
    data["decisions"] = [{"date": datetime.date(2026, 9, 28), "by": "reviewer-a", "status": "confirmed",
                          "reason": "self review", "evidence": [],
                          "bind": subject_digest("SC", data)}]
    put(kb_ready, "SC", data)
    assert any("is SC-0001's creator; a closing decision needs someone else" in i.message
               for i in k13_issues(kb_ready))


@pytest.mark.parametrize("classification,role,message", [
    ("contradicted", "model-mismatch", "no basis entry has role counterevidence or internal-inconsistency"),
    ("wrong_model", "internal-inconsistency", "no basis entry has role model-mismatch"),
])
def test_a_confirmed_challenge_needs_the_role_its_classification_requires(kb_ready, source_repo,
                                                                          classification, role, message):
    put(kb_ready, "SC", sc("confirmed", repo=source_repo, classification=classification,
                           basis=[basis(role=role)]))
    assert any(message in i.message for i in k13_issues(kb_ready))


def test_a_confirmed_challenge_needs_its_basis_available(kb_ready, source_repo):
    """A basis entry on another file, whose bytes are gone: the availability row, an error at confirmed."""
    gone = "a" * 64
    put(kb_ready, "SC", sc("confirmed", repo=source_repo,
                           basis=[basis(), {**ref("evidence/gone.md", gone), "locator": "the manifest",
                                            "role": "counterevidence", "provenance": "observed"}]))
    found = messages_of(kb_ready)
    assert found == [("error", "basis[1]: the working file evidence/gone.md is missing"
                               + retire_missing("SC-0001"))]


def allocation(kb, data: dict) -> None:
    """The allocation identity as JSON stores it, even when YAML parsed created as a date."""
    payload = {field: value.isoformat() if isinstance(value, datetime.date) else value
               for field, value in data.items() if field in ("id", "created", "creator", "proponent")}
    kb.write(f".kblam/review-receipts/{data['id']}.json", json.dumps(payload) + "\n")


@pytest.mark.parametrize("kind,field", [
    ("SC", "created"), ("SC", "creator"),
    ("CT", "created"), ("CT", "creator"), ("CT", "proponent"),
    ("CU", "created"), ("CU", "creator"), ("CU", "proponent"),
])
def test_hand_changed_identity_is_a_structure_error(kb_ready, source_repo, kind, field):
    data = clean(kb_ready, kind, "open", source_repo)
    allocation(kb_ready, data)
    original = str(data[field])
    now = "2026-09-27" if field == "created" else "another-reviewer"
    data[field] = now
    path = put(kb_ready, kind, data)
    rec = record_of(load_view(kb_ready.cfg), data["id"])
    issues = k13_issues(kb_ready)
    assert [(i.code, i.path, i.line, i.level, i.owner, i.message) for i in issues] == [
        ("K13", path, rec.key_line(field), "error", rec.id,
         f"{field} is {now!r}, but it was allocated as {original!r} (id, created, creator and proponent "
         "never change after allocation)")]


@pytest.mark.parametrize("created", ["2026-09-28", datetime.date(2026, 9, 28),
                                     "2026-09-27", datetime.date(2026, 9, 27)])
def test_identity_dates_compare_as_iso_text(kb_ready, source_repo, created):
    data = sc(created="2026-09-28")
    allocation(kb_ready, data)
    data["created"] = created
    path = put(kb_ready, "SC", data)
    rec = record_of(load_view(kb_ready.cfg), "SC-0001")
    expected = [] if str(created) == "2026-09-28" else [
        ("K13", path, rec.key_line("created"), "error", "SC-0001",
         "created is '2026-09-27', but it was allocated as '2026-09-28' (id, created, creator and "
         "proponent never change after allocation)")]
    assert [(i.code, i.path, i.line, i.level, i.owner, i.message)
            for i in k13_issues(kb_ready)] == expected


@pytest.mark.parametrize("receipt", [None, "not JSON\n", "[]\n", "{}\n", '{"creator": 7}\n'])
def test_absent_or_partial_allocation_proves_no_identity_change(kb_ready, source_repo, receipt):
    put(kb_ready, "SC", sc(creator="another-reviewer"))
    if receipt is not None:
        kb_ready.write(".kblam/review-receipts/SC-0001.json", receipt)
    assert k13_issues(kb_ready) == []


@pytest.mark.parametrize("field,value,message", [
    ("created", None, "missing key 'created'"),
    ("created", 7, "created: expected a date (YYYY-MM-DD)"),
    ("created", "not a date", "created: expected a date (YYYY-MM-DD)"),
    ("creator", None, "missing key 'creator'"),
    ("creator", 7, "creator: 7 is not a name"),
    ("creator", "not a name", "creator: 'not a name' is not a name"),
    ("proponent", None, "missing key 'proponent'"),
    ("proponent", 7, "proponent: 7 is not a name"),
    ("proponent", "not a name", "proponent: 'not a name' is not a name"),
    ("id", "CT-0009", "id: 'CT-0009' does not match the file name's ID (CT-0001); git's last commit does "
                      "not hold a file at research-review/tasks/CT-0001.yaml, and records are never renamed, "
                      "so leave it as it is and tell the user"),
])
def test_invalid_identity_has_only_its_schema_error(kb_ready, source_repo, field, value, message):
    data = ct(kb_ready)
    allocation(kb_ready, data)
    if value is None:
        data.pop(field)
    else:
        data[field] = value
    path = f"{REVIEW}/tasks/CT-0001.yaml"
    kb_ready.write(path, dump_record(data))
    rec = record_of(load_view(kb_ready.cfg), "CT-0001")
    assert [(i.code, i.path, i.line, i.level, i.owner, i.message)
            for i in k13_issues(kb_ready)] == [
                ("K13", path, rec.key_line(field), "error", "CT-0001", message)]


def test_committed_record_answers_only_for_a_copy_kblam_reads_as_that_record(gkb):
    """The restore step is built on kblam.gitdir.committed_record, which reads the blob of
    `git show HEAD:<path>` and answers True only for bytes that parse with the file name's ID: a commit
    that holds the damage (or a stray file) must not be answered with a restore that would put those same
    bytes back and print the same step again. A path outside ASCII is no special case here: git prints
    the blob itself, so nothing needs quoting."""
    path = "research-review/challenges/SC-0001.yaml"
    non_ascii = "research-review/challenges/résumé/SC-0002.yaml"
    gkb.write(path, record_text("SC", "SC-0001"))
    gkb.write(non_ascii, record_text("SC", "SC-0002"))
    commit(gkb, "two records")

    assert committed_record(gkb.cfg, path) is True
    assert committed_record(gkb.cfg, non_ascii) is True

    gkb.write(path, record_text("SC", "SC-0001", id=DROP))     # the damage, and it is committed
    commit(gkb, "the damage")
    assert committed_record(gkb.cfg, path) is False
    assert committed_record(gkb.cfg, "research-review/challenges/notes.yaml") is False   # not in HEAD
    gkb.write("research-review/challenges/SC-0003.yaml", record_text("SC", "SC-0003"))
    assert committed_record(gkb.cfg, "research-review/challenges/SC-0003.yaml") is False  # worktree only
    git(gkb, "add", "-A")
    assert committed_record(gkb.cfg, "research-review/challenges/SC-0003.yaml") is False  # index only


def test_committed_record_answers_no_in_a_repository_with_no_commit(gkb):
    """A repository whose HEAD names no commit (a fresh `git init`) holds nothing to restore: False."""
    path = "research-review/challenges/SC-0001.yaml"
    gkb.write(path, record_text("SC", "SC-0001"))
    git(gkb, "add", "-A")
    commit(gkb, "the record")

    git(gkb, "symbolic-ref", "HEAD", "refs/heads/unborn")       # HEAD names no commit

    assert committed_record(gkb.cfg, path) is False


@pytest.mark.parametrize("status", ["stale", "rejected", "outside-the-vocabulary"])
def test_identity_is_checked_even_at_retired_or_invalid_status(kb_ready, source_repo, status):
    data = sc(status if status != "outside-the-vocabulary" else "open")
    allocation(kb_ready, data)
    data["created"] = "2026-09-27"
    data["status"] = status
    put(kb_ready, "SC", data)
    identity = [i for i in k13_issues(kb_ready) if "allocated as" in i.message]
    assert len(identity) == 1 and identity[0].level == "error"


def test_noncanonical_record_has_no_identity_error(kb_ready, source_repo):
    data = sc()
    allocation(kb_ready, data)
    data["creator"] = "another-reviewer"
    path = f"{REVIEW}/SC-0001.yaml"
    kb_ready.write(path, dump_record(data))
    (kb_ready.root / f"{REVIEW}/challenges/SC-0001.yaml").unlink()
    issues = k13_issues(kb_ready)
    assert len(issues) == 1 and issues[0].path == path
    assert "allocated as" not in issues[0].message


def test_changed_use_identity_is_restored_before_binding_recovery(kb_ready, source_repo):
    data = cu(kb_ready, "approved")
    allocation(kb_ready, data)
    data["creator"] = "another-reviewer"
    data["challenge_bind"] = ZERO64
    put(kb_ready, "CU", deciding("CU", data))
    assert use_problems(kb_ready) and all(problem.endswith(f"({RESTORE_STRUCTURE_FIX})")
                                         for problem in use_problems(kb_ready))


@pytest.mark.parametrize("fault", ["registry", "root"])
@pytest.mark.parametrize("problem", ["missing_index", "changed_index", "unregistered"])
def test_layout_recovery_obeys_the_global_write_gate(kb_ready, source_repo, fault, problem, capsys):
    k13_issues(kb_ready)
    if problem == "missing_index":
        path = f"{REVIEW}/INDEX.md"
        (kb_ready.root / path).unlink()
        diagnosis = "INDEX.md is missing; "
    elif problem == "changed_index":
        path = f"{REVIEW}/INDEX.md"
        kb_ready.write(path, "hand-edited index\n")
        diagnosis = ("INDEX.md differs from the generated review index; it is never edited by hand. ")
    else:
        path = f"{REVIEW}/misplaced/SC-0009.yaml"
        kb_ready.write(path, dump_record(sc(id="SC-0009")))
        diagnosis = (f"a challenge must sit directly in its kind's folder ({REVIEW}/challenges/"
                     "SC-0009.yaml); this ID is not an installed record or a registered ID, so ")
        kb_ready.write(f"{REVIEW}/INDEX.md", generate_review_index(load_view(kb_ready.cfg)))
    gate_path = ".kblam/review-ids" if fault == "registry" else ".kblam/tree.hash"
    original = '["SC-0001"]\n' if fault == "registry" else format_line(REVIEW, ZERO64)
    kb_ready.write(gate_path, "not JSON\n" if fault == "registry" else format_line("old-review", ZERO64))
    recovery = ("fix the review-root problem kblam validate reports first (it blocks every write), "
                "then run kblam validate again")
    assert run(kb_ready, "validate") == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert f"K13 {path}: {diagnosis}{recovery}\n" in captured.out
    if fault == "registry":
        assert "restore it from a backup" in captured.out
    else:
        assert "kblam.toml: the review root changed from old-review" in captured.out
    kb_ready.write(gate_path, original)                # restore the registry/root that validate names
    assert run(kb_ready, "validate") == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    message = next(line for line in captured.out.splitlines() if line.startswith(f"K13 {path}: "))
    assert recovery not in message
    if problem.endswith("index"):
        assert "kblam review index" in message
        command = message[message.index("kblam review index"):].split()[:3]
        assert run(kb_ready, *command[1:]) == 0
        capsys.readouterr()
        assert not any(i.path == path for i in k13_issues(kb_ready, index=False))
    else:
        command = message[message.index("kblam challenge new"):].split(",", 1)[0].split()
        assert run(kb_ready, *command[1:], TRACE, "--lines", "3-3", "--by", "reviewer-c") == 0
        staged = Path(capsys.readouterr().out.strip())
        fill(staged, **sc_fields())
        assert "kblam put needs" in message
        assert run(kb_ready, "put", str(staged)) == 0
        capsys.readouterr()


def test_confirmed_provisional_source_fixes_registry_before_retirement(kb_ready, source_repo, capsys):
    put(kb_ready, "SC", sc("confirmed"))
    k13_issues(kb_ready)
    registry = ".kblam/review-ids"
    original = '["SC-0001"]\n'
    kb_ready.write(registry, "not JSON\n")
    rec = record_of(load_view(kb_ready.cfg), "SC-0001")
    recovery = ("fix the review-root problem kblam validate reports first (it blocks every write), "
                "then run kblam validate again")
    diagnosis = CONFIRMATION_FIX.split(" Restore it from git", 1)[0]
    registry_message = (".kblam/review-ids cannot be read (Expecting value: line 1 column 1 (char 0)); "
                        "kblam writes it, so restore it from a backup, or delete it and run kblam "
                        "validate --record")
    assert run(kb_ready, "validate") == 1
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == (
        f"K13 {registry}: {registry_message}\nK13 {rec.path}:{rec.key_line('source')}: "
        f"{diagnosis} Restore it from git, or {recovery}\nkblam validate: 2 error(s) in research-review/\n", "")
    assert "kblam review decide" not in captured.out
    kb_ready.write(registry, original)                 # restore the backup the registry message names
    assert run(kb_ready, "validate") == 1
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == (
        f"K13 {rec.path}:{rec.key_line('source')}: {CONFIRMATION_FIX}\n"
        "kblam validate: 1 error(s) in research-review/\n", "")
    command = captured.out.split("retire it (", 1)[1].split(")", 1)[0].split()
    command = [DECIDER if token == "NAME" else "retire-unpinned-confirmation" if token == "TEXT"
               else challenge_binding(kb_ready) if token == "D" else token for token in command]
    assert command[:2] == ["kblam", "review"]
    assert run(kb_ready, *command[1:]) == 0
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == (
        "kblam review decide: SC-0001 is now stale "
        f"(subject digest {challenge_binding(kb_ready)[:12]})\n",
        "kblam review decide SC-0001: findings/ or research-review/ was changed outside kblam since "
        "kblam last wrote it; tree.hash not advanced. Run kblam validate --record once the change is "
        "validated.\n")


def printed_command(message: str, prefix: str, digest: str) -> list[str]:
    """A parenthesized recovery command with the named placeholders supplied by the reviewer."""
    command = message[message.index(prefix):].split(")", 1)[0].split(", adding", 1)[0].split()
    values = {"NAME": DECIDER, "TEXT": "reviewed-after-restoration", "D": digest}
    return [values.get(token, token) for token in command][1:]


def test_challenge_identity_is_restored_before_its_printed_retirement(kb_ready, source_repo, capsys):
    data = sc("confirmed")
    allocation(kb_ready, data)
    backup = dump_record(data)
    data["creator"] = "another-reviewer"
    path = put(kb_ready, "SC", data)
    k13_issues(kb_ready)
    assert run(kb_ready, "validate") == 1
    captured = capsys.readouterr()
    recovery = ("Restore SC-0001 from git (its identity changed after allocation), then run "
                "kblam validate again")
    assert recovery in captured.out and "kblam review decide" not in captured.out
    assert captured.err == ""
    assert run(kb_ready, *RETIRE, "--expect", challenge_binding(kb_ready)) == 1
    captured = capsys.readouterr()
    assert "creator is 'another-reviewer', but it was allocated as" in captured.out
    kb_ready.write(path, backup)                      # the named record restored from git
    assert run(kb_ready, "validate") == 1
    captured = capsys.readouterr()
    assert CONFIRMATION_FIX in captured.out
    command = printed_command(captured.out, "kblam review decide", challenge_binding(kb_ready))
    assert run(kb_ready, *command) == 0
    capsys.readouterr()


@pytest.mark.parametrize("pinned", [False, True])
def test_open_challenge_identity_is_restored_before_pin_and_confirm(kb_ready, source_repo, pinned,
                                                                   capsys):
    data = sc("open", source=pinned_source(source_repo) if pinned else source())
    allocation(kb_ready, data)
    backup = dump_record(data)
    put(kb_ready, "SC", data)
    put(kb_ready, "CU", cu(kb_ready, "approved"))
    data["creator"] = "another-reviewer"
    path = put(kb_ready, "SC", data)
    k13_issues(kb_ready)
    assert run(kb_ready, "validate") == 1
    captured = capsys.readouterr()
    assert ("restore SC-0001 from git (its identity changed after allocation), then run "
            "kblam validate again") in captured.out
    assert all(command not in captured.out for command in ("kblam challenge pin", "kblam review decide"))
    blocked = (["challenge", "pin", "SC-0001"] if not pinned else
               ["review", "decide", "SC-0001", "--status", "confirmed", "--by", DECIDER,
                "--reason", "reviewed-after-restoration"])
    assert run(kb_ready, *blocked, "--expect", challenge_binding(kb_ready)) == 1
    captured = capsys.readouterr()
    assert "creator is 'another-reviewer', but it was allocated as" in captured.out
    kb_ready.write(path, backup)
    assert run(kb_ready, "validate") == 0             # CU binding problems are warnings, not errors
    captured = capsys.readouterr()
    expected = CONFIRM_USE_FIX if pinned else PIN_CONFIRM_FIX
    assert expected in captured.out
    if not pinned:
        command = printed_command(captured.out, "kblam challenge pin", challenge_binding(kb_ready))
        assert run(kb_ready, *command) == 0
        capsys.readouterr()
        assert run(kb_ready, "validate") == 0
        captured = capsys.readouterr()
        assert CONFIRM_USE_FIX in captured.out
    command = printed_command(captured.out, "kblam review decide", challenge_binding(kb_ready))
    assert run(kb_ready, *command) == 0
    capsys.readouterr()


@pytest.mark.parametrize("problem", ["rebind", "challenge_stale", "missing_excerpt", "unverified_excerpt"])
def test_use_identity_is_restored_before_its_printed_write(kb_ready, source_repo, problem, capsys,
                                                         excerpts, monkeypatch):
    retire = problem != "rebind"
    if problem == "challenge_stale":
        put(kb_ready, "SC", sc("stale", source=pinned_source(source_repo)))
    elif problem == "missing_excerpt":
        excerpts[FINDING] = []
    elif problem == "unverified_excerpt":
        excerpts[FINDING] = [excerpt(verified=False, problem="the source text changed")]
    data = cu(kb_ready, "approved", challenge_bind=ZERO64)
    allocation(kb_ready, data)
    backup = dump_record(data)
    data["creator"] = "another-reviewer"
    path = put(kb_ready, "CU", data)
    k13_issues(kb_ready)
    assert run(kb_ready, "validate") == 1
    captured = capsys.readouterr()
    assert RESTORE_STRUCTURE_FIX in captured.out
    assert "kblam review rebind" not in captured.out and "kblam review decide" not in captured.out
    blocked = (["review", "decide", "CU-0001", "--status", "stale"] if retire else
               ["review", "rebind", "CU-0001"])
    assert run(kb_ready, *blocked, "--by", DECIDER, "--reason", "reviewed-after-restoration",
               "--expect", digest_of(kb_ready, "CU-0001")) == 1
    captured = capsys.readouterr()
    assert "creator is 'another-reviewer', but it was allocated as" in captured.out
    kb_ready.write(path, backup)
    assert run(kb_ready, "validate") == 0
    captured = capsys.readouterr()
    expected = RETIRE_USE_FIX if retire else REBIND_FIX
    assert expected in captured.out
    original_advice = captured.out
    command = printed_command(captured.out, expected.removeprefix("run ").split(" --by", 1)[0],
                              digest_of(kb_ready, "CU-0001"))
    assert run(kb_ready, *command) == 0
    capsys.readouterr()
    if problem == "missing_excerpt":
        assert NEW_USE_FIX in original_advice
        monkeypatch.setattr(matching, "finding_matches", REAL_FINDING_MATCHES)
        add_finding(kb_ready, "3", LINE3)             # the conditional excerpt repair the advice names
        capsys.readouterr()
        command = printed_command(original_advice, "kblam use review", "unused")
        command = ["1" if token == "<excerpt-ordinal>" else token for token in command]
        assert run(kb_ready, *command) == 0
        capsys.readouterr()
    elif problem == "unverified_excerpt":
        assert RESTORE_TEXT_FIX in original_advice


@pytest.mark.parametrize("retire", [False, True])
def test_another_records_identity_does_not_refuse_the_use_write(kb_ready, source_repo, retire, capsys):
    challenge = sc("stale" if retire else "confirmed", source=pinned_source(source_repo))
    allocation(kb_ready, challenge)
    challenge["creator"] = "another-reviewer"
    put(kb_ready, "SC", challenge)
    put(kb_ready, "CU", cu(kb_ready, "approved", challenge_bind=ZERO64))
    k13_issues(kb_ready)
    assert run(kb_ready, "validate") == 1
    captured = capsys.readouterr()
    assert "creator is 'another-reviewer', but it was allocated as" in captured.out
    expected = RETIRE_USE_FIX if retire else REBIND_FIX
    assert expected in captured.out
    command = printed_command(captured.out, expected.removeprefix("run ").split(" --by", 1)[0],
                              digest_of(kb_ready, "CU-0001"))
    assert run(kb_ready, *command) == 0
    captured = capsys.readouterr()
    assert "creator is 'another-reviewer', but it was allocated as" in captured.out
    assert "done, but kblam validate still fails" in captured.out


@pytest.mark.parametrize("fault", ["registry", "root"])
def test_nonrecord_stray_only_gates_staging_when_registry_cannot_be_read(kb_ready, source_repo, fault,
                                                                      capsys, monkeypatch):
    quoted_scene(kb_ready, source_repo)
    monkeypatch.setattr(matching, "finding_matches", REAL_FINDING_MATCHES)
    path = f"{REVIEW}/notes.txt"
    kb_ready.write(path, "stray notes\n")
    k13_issues(kb_ready)
    gate_path = ".kblam/review-ids" if fault == "registry" else ".kblam/tree.hash"
    original = '["SC-0001"]\n' if fault == "registry" else format_line(REVIEW, ZERO64)
    kb_ready.write(gate_path, "not JSON\n" if fault == "registry" else format_line("old-review", ZERO64))
    assert run(kb_ready, "validate") == 1
    captured = capsys.readouterr()
    message = next(line for line in captured.out.splitlines() if line.startswith(f"K13 {path}: "))
    diagnosis = (f"K13 {path}: {REVIEW}/ holds only SC-, CT- and CU- records in their kind's folder and "
                 "the generated INDEX.md. ")
    normal = (diagnosis + "Record each challenge, task or use with kblam challenge new, kblam task new "
              "or kblam use review, then delete this file")
    if fault == "registry":
        assert message == (diagnosis + "fix the review-root problem kblam validate reports first "
                           "(it blocks every write), then run kblam validate again; delete this file")
        assert "restore it from a backup" in captured.out
        assert run(kb_ready, "challenge", "new", TRACE, "--lines", "3-3", "--by", DECIDER) == 1
        assert "review-ids cannot be read" in capsys.readouterr().err
        kb_ready.write(gate_path, original)
        assert run(kb_ready, "validate") == 1
        captured = capsys.readouterr()
        assert normal in captured.out
    else:
        assert message == normal                    # staging ignores only a root mismatch
    command = normal[normal.index("kblam challenge new"):].split(",", 1)[0].split()
    assert run(kb_ready, *command[1:], TRACE, "--lines", "3-3", "--by", DECIDER) == 0
    capsys.readouterr()
    assert "kblam task new" in normal
    assert run(kb_ready, "task", "new", "F-0001", "--kind", "replication", "--by", DECIDER,
               "--proponent", "researcher-a") == 0
    capsys.readouterr()
    assert "kblam use review" in normal
    assert run(kb_ready, "use", "review", "SC-0001", "F-0001", "1", "--by", DECIDER,
               "--proponent", "researcher-a") == 0
    capsys.readouterr()
    (kb_ready.root / path).unlink()                  # retain and follow the non-kblam delete advice


def test_unreadable_registry_reuses_the_failed_validation_read(kb_ready, source_repo, monkeypatch):
    from kblam import registry

    put(kb_ready, "SC", sc("confirmed"))
    put(kb_ready, "CU", cu(kb_ready, "approved", challenge_bind=ZERO64))
    kb_ready.write(f"{REVIEW}/notes.txt", "stray notes\n")
    kb_ready.write(f"{REVIEW}/misplaced/SC-0009.yaml", dump_record(sc(id="SC-0009")))
    kb_ready.write(".kblam/review-ids", "not JSON\n")
    read_ids = registry.read_ids
    calls = []

    def read(cfg):
        calls.append(cfg)
        return read_ids(cfg)

    monkeypatch.setattr(registry, "read_ids", read)
    issues = k13_issues(kb_ready, index=False)
    assert len(calls) == 1
    assert any(i.path == ".kblam/review-ids" and "restore it from a backup" in i.message
               for i in issues)
    assert all("kblam review decide" not in i.message and "kblam review rebind" not in i.message
               and "kblam review index" not in i.message for i in issues)


def test_the_pin_message_names_commands_that_run(kb_ready, source_repo):
    """A confirmed challenge whose available source is provisional is the one state that reports it: the
    row is checked only when confirmed, and an unavailable source reports the availability row instead
    (open, rejected and stale never reach it)."""
    put(kb_ready, "SC", sc("confirmed"))
    reported = [(issue.level, issue.owner, issue.message) for issue in k13_issues(kb_ready)
                if "a confirmation needs a pinned source" in issue.message]
    assert reported == [("error", "SC-0001", CONFIRMATION_FIX)]


def test_a_confirmed_challenge_cannot_be_pinned(kb_ready, source_repo, capsys):
    """Why that message no longer offers a pin: `challenge pin` takes an open challenge, so on this
    record the command it used to name is refused (SPEC §5.2.5)."""
    put(kb_ready, "SC", sc("confirmed"))
    assert run(kb_ready, "challenge", "pin", "SC-0001",
               "--expect", challenge_binding(kb_ready)) == 1
    assert "only an open challenge can be pinned" in capsys.readouterr().err


def test_the_retire_command_the_pin_message_names_runs(kb_ready, source_repo, capsys):
    """The message's first command, run on the state that printed it: confirmed -> stale needs no pin and
    is the retirement §5.2.3 prescribes for a judgement whose version is gone."""
    put(kb_ready, "SC", sc("confirmed"))
    assert run(kb_ready, *RETIRE, "--expect", challenge_binding(kb_ready)) == 0
    capsys.readouterr()
    assert pin_messages(kb_ready) == []
    assert record_of(load_view(kb_ready.cfg), "SC-0001").status == "stale"


# --- D49: the step each stale or unavailable reference message ends with ---------------------------
#
# Each of the three messages names the same way out: restore the bytes the record was written against,
# or retire the record and file a new one. The tests reach the state that prints the message, take the
# retire command from the printed text, and run it: retiring is what clears the reference, because the
# availability row reports nothing at a retired status (SPEC §5.2.4 Severity).


def other_basis(path: str = "evidence/2026-09-22-ratio/README.md", sha: str = SHA) -> dict:
    """A second basis entry, off the source itself, so its reference resolves on its own."""
    return {**ref(path, sha), "locator": "the manifest", "role": "counterevidence",
            "provenance": "observed"}


def retired(kb_ready, message: str, capsys) -> None:
    """The K13 error `message` on the record, the retire command the same output prints, run on that
    state, and a clean validate afterwards."""
    assert run(kb_ready, "validate") == 1
    out = capsys.readouterr().out
    assert message in out, out
    assert run(kb_ready, *printed_command(out, "kblam review decide", challenge_binding(kb_ready))) == 0
    capsys.readouterr()
    assert run(kb_ready, "validate") == 0
    assert record_of(load_view(kb_ready.cfg), "SC-0001").status == "stale"


def test_the_stale_reference_message_names_the_retire_command_that_runs(kb_ready, source_repo, capsys):
    """D49 for "the source changed since SC-0001 was written": a confirmed challenge whose second basis
    file holds other bytes than the record's sha256 reports the K13 error with its step, and the retire
    command the same output names runs on that state and leaves a clean tree."""
    put(kb_ready, "SC", sc("confirmed", repo=source_repo, basis=[basis(), other_basis(sha="a" * 64)]))
    retired(kb_ready, "basis[1]: the source changed since SC-0001 was written"
            + retire("SC-0001", "evidence/2026-09-22-ratio/README.md"), capsys)


def test_the_missing_reference_message_names_the_retire_command_that_runs(kb_ready, source_repo, capsys):
    """D49 for "the working file <path> is missing": a confirmed challenge whose second basis file is
    gone reports the K13 error with its step, and the retire command the same output names runs on that
    state and leaves a clean tree."""
    put(kb_ready, "SC", sc("confirmed", repo=source_repo, basis=[basis(), other_basis()]))
    (kb_ready.root / "evidence/2026-09-22-ratio/README.md").unlink()
    retired(kb_ready, "basis[1]: the working file evidence/2026-09-22-ratio/README.md is missing"
            + retire_missing("SC-0001"), capsys)


def test_the_pinned_reference_message_names_the_retire_command_that_runs(kb_ready, source_repo, capsys):
    """D49 for "the pinned version is not present": a confirmed challenge pinned by snapshot, whose
    snapshot and working bytes are both gone, reports the K13 error for the source and its basis entry
    with the step, and the retire command the same output names runs on that state and leaves a clean
    tree."""
    dirty = TRACE_TEXT + "Row 104: bytes 0x41 0x42\n"
    source_repo.write(TRACE_PATH, dirty)
    copy = "evidence/2026-09-28-trace-copy/trace.md"
    kb_ready.write(copy, dirty)
    put(kb_ready, "SC", sc("confirmed", source=source(sha=hashlib.sha256(dirty.encode("utf-8")).hexdigest(),
                                                      snapshot=copy)))
    (kb_ready.root / copy).unlink()
    source_repo.write(TRACE_PATH, dirty + "Row 105: bytes 0x51 0x52\n")
    message = "the pinned version is not present" + retire("SC-0001", TRACE, pinned=True)
    assert run(kb_ready, "validate") == 1
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.endswith(message)]
    assert len(lines) == 2 and lines[1].endswith(f"basis[0]: {message}"), out
    assert run(kb_ready, *printed_command(out, "kblam review decide", challenge_binding(kb_ready))) == 0
    capsys.readouterr()
    assert run(kb_ready, "validate") == 0
    assert record_of(load_view(kb_ready.cfg), "SC-0001").status == "stale"


def open_warning(kb_ready, message: str, capsys) -> None:
    """The K13 warning `message` on an open record, the retire command the same output prints, run on
    that state, and a clean validate afterwards. The reference row is a warning while the record is
    open (SPEC §5.2.3 Evaluation), so `kblam validate` exits 0; `kblam review decide --status stale` is
    allowed from `open` for every kind (SPEC §5.2.2 Status changes), so the step runs as printed."""
    assert run(kb_ready, "review", "index") == 0          # the fixture wrote the record by hand
    capsys.readouterr()
    assert run(kb_ready, "validate") == 0
    out = capsys.readouterr().out
    assert message in out, out
    assert run(kb_ready, *printed_command(out, "kblam review decide", challenge_binding(kb_ready))) == 0
    capsys.readouterr()
    assert run(kb_ready, "validate") == 0
    assert record_of(load_view(kb_ready.cfg), "SC-0001").status == "stale"


def test_an_open_records_stale_reference_names_the_retire_command_that_runs(kb_ready, source_repo, capsys):
    """D49 for "the source changed since SC-0001 was written" on an OPEN challenge: the row is a warning
    (exit 0), and the retire command the same output names runs on that state and leaves a clean tree."""
    put(kb_ready, "SC", sc("open", basis=[basis(), other_basis(sha="a" * 64)]))
    open_warning(kb_ready, "basis[1]: the source changed since SC-0001 was written"
                 + retire("SC-0001", "evidence/2026-09-22-ratio/README.md"), capsys)


def test_an_open_records_missing_reference_names_the_retire_command_that_runs(kb_ready, source_repo, capsys):
    """D49 for "the working file <path> is missing" on an OPEN challenge: the row is a warning (exit 0),
    and the retire command the same output names runs on that state and leaves a clean tree."""
    put(kb_ready, "SC", sc("open", basis=[basis(), other_basis()]))
    (kb_ready.root / "evidence/2026-09-22-ratio/README.md").unlink()
    open_warning(kb_ready, "basis[1]: the working file evidence/2026-09-22-ratio/README.md is missing"
                 + retire_missing("SC-0001"), capsys)


def test_an_open_records_pinned_reference_names_the_retire_command_that_runs(kb_ready, source_repo, capsys):
    """D49 for "the pinned version is not present" on an OPEN challenge: an open record may carry a
    snapshot pin, the row is a warning (exit 0), and the retire command the same output names runs on
    that state and leaves a clean tree."""
    dirty = TRACE_TEXT + "Row 104: bytes 0x41 0x42\n"
    source_repo.write(TRACE_PATH, dirty)
    copy = "evidence/2026-09-28-trace-copy/trace.md"
    kb_ready.write(copy, dirty)
    put(kb_ready, "SC", sc("open", source=source(sha=hashlib.sha256(dirty.encode("utf-8")).hexdigest(),
                                                 snapshot=copy)))
    (kb_ready.root / copy).unlink()
    source_repo.write(TRACE_PATH, dirty + "Row 105: bytes 0x51 0x52\n")
    open_warning(kb_ready, "the pinned version is not present" + retire("SC-0001", TRACE, pinned=True), capsys)


def test_the_new_challenge_command_the_pin_message_names_pins_a_committed_source(
        kb_ready, source_repo, capsys):
    """Where the owning worktree's HEAD holds the source's bytes, the message's first branch runs as
    written: `challenge new` pins the staged source, the filled record puts it, and no pin command is
    needed for the installed challenge (SPEC §5.2.2 Git pins, §5.2.5 put)."""
    put(kb_ready, "SC", sc("confirmed"))
    assert run(kb_ready, *RETIRE, "--expect", challenge_binding(kb_ready)) == 0
    capsys.readouterr()
    assert run(kb_ready, "challenge", "new", TRACE, "--lines", "3-3", "--by", "reviewer-c") == 0
    staged = Path(capsys.readouterr().out.strip())
    staged_ref = yaml_rt().load(staged.read_bytes().decode("utf-8"))["source"]
    assert (staged_ref["repo"], staged_ref["commit"], staged_ref["blob"]) == \
        (SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    fill(staged, **sc_fields())
    assert run(kb_ready, "put", str(staged)) == 0
    capsys.readouterr()
    installed = record_of(load_view(kb_ready.cfg), "SC-0002")
    source_ref = installed.data["source"]
    assert (source_ref["repo"], source_ref["commit"], source_ref["blob"]) == \
        (SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    assert source_ref["snapshot"] is None
    assert run(kb_ready, "challenge", "pin", "SC-0002", "--expect",
               digest_of(kb_ready, "SC-0002")) == 1
    assert "already pinned" in capsys.readouterr().err
    assert pin_messages(kb_ready) == []


def test_the_snapshot_pin_command_the_pin_message_names_runs(kb_ready, source_repo, capsys):
    """Where HEAD does not hold them (an uncommitted working file), `challenge new` leaves the source
    provisional, the put installs it that way, and the message's --snapshot pin is what pins the
    installed challenge (SPEC §5.2.2 Git pins, §5.2.5 put and `challenge pin`)."""
    dirty = TRACE_TEXT + "Row 104: bytes 0x41 0x42\n"
    source_repo.write(TRACE_PATH, dirty)
    sha = hashlib.sha256(dirty.encode("utf-8")).hexdigest()
    put(kb_ready, "SC", sc("confirmed", source=source(sha=sha)))
    assert run(kb_ready, *RETIRE, "--expect", challenge_binding(kb_ready)) == 0
    capsys.readouterr()
    assert run(kb_ready, "challenge", "new", TRACE, "--lines", "3-3", "--by", "reviewer-c") == 0
    staged = Path(capsys.readouterr().out.strip())
    staged_ref = yaml_rt().load(staged.read_bytes().decode("utf-8"))["source"]
    assert [staged_ref[key] for key in ("repo", "commit", "blob", "snapshot")] == [None] * 4
    fill(staged, **sc_fields())
    assert run(kb_ready, "put", str(staged)) == 0
    capsys.readouterr()
    assert record_of(load_view(kb_ready.cfg), "SC-0002").data["source"]["snapshot"] is None
    copy = "evidence/2026-09-28-trace-copy/trace.md"     # the project-owned copy of those bytes
    kb_ready.write(copy, dirty)
    assert run(kb_ready, "challenge", "pin", "SC-0002", "--snapshot", copy,
               "--expect", digest_of(kb_ready, "SC-0002")) == 0
    capsys.readouterr()
    installed = record_of(load_view(kb_ready.cfg), "SC-0002")
    assert installed.data["source"]["snapshot"] == copy
    assert [installed.data["source"][key] for key in ("repo", "commit", "blob")] == [None] * 3


# --- decisions ---------------------------------------------------------------------------------


def test_a_stale_decision_evidence_reference_is_a_warning_while_open(kb_ready, source_repo):
    """An open use may still be rebound to its own status (SPEC §5.2.2 Status changes), and its missing
    evidence is the availability row."""
    data = cu(kb_ready, "open")
    data["decisions"] = [{"date": datetime.date(2026, 9, 28), "by": DECIDER, "status": "open",
                          "reason": "rechecked", "bind": ZERO64,
                          "evidence": [{**ref("evidence/gone.md", "b" * 64), "locator": "the manifest",
                                        "provenance": "observed"}]}]
    put(kb_ready, "CU", data)
    assert messages_of(kb_ready) == [("warning", "decisions[0].evidence[0]: the working file "
                                                 "evidence/gone.md is missing" + retire_missing("CU-0001"))]


def test_a_missing_decision_evidence_reference_is_nothing_once_retired(kb_ready, source_repo):
    data = cu(kb_ready, "stale")
    data["decisions"][0]["evidence"] = [{**ref("evidence/gone.md", "b" * 64), "locator": "the manifest",
                                         "provenance": "observed"}]
    data["decisions"][0]["bind"] = subject_digest("CU", data)
    put(kb_ready, "CU", data)
    assert messages_of(kb_ready) == []


def test_a_confirmed_challenges_decision_evidence_is_checked(kb_ready, source_repo):
    """A challenge's decision evidence is K13's; its availability row is an error at a confirmed status."""
    data = sc("confirmed", repo=source_repo)
    data["decisions"][0]["evidence"] = [{**ref("evidence/gone.md", "b" * 64), "locator": "the manifest",
                                         "provenance": "observed"}]
    data["decisions"][0]["bind"] = subject_digest("SC", data)
    put(kb_ready, "SC", data)
    assert messages_of(kb_ready) == [("error", "decisions[0].evidence[0]: the working file "
                                               "evidence/gone.md is missing" + retire_missing("SC-0001"))]


@pytest.mark.parametrize("kind,status", [("SC", "rejected"), ("SC", "stale"), ("CU", "withdrawn"),
                                         ("CU", "stale")])
def test_the_structure_of_decision_evidence_is_an_error_at_every_status(kb, kind, status):
    """The availability row reports nothing when a record is closed or retired (SPEC §5.2.4 Severity),
    but a structurally wrong reference is an error at every status."""
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.")
    put(kb, "SC", sc("stale"))
    data = sc(status) if kind == "SC" else cu(kb, status)
    data["decisions"][0]["evidence"] = [{**ref("evidence", "b" * 64), "locator": "the manifest",
                                         "provenance": "observed"}]
    put(kb, kind, data)
    found = [(issue.level, issue.owner, issue.message) for issue in k13_issues(kb)
             if "is a directory, not a file" in issue.message]
    assert found == [("error", data["id"],
                      "decisions[0].evidence[0]: 'evidence' is a directory, not a file")]


def test_only_the_effective_decisions_evidence_is_checked_for_availability(kb_ready, source_repo):
    """An approved use whose first decision's evidence is gone is not an issue: only the effective
    decision's evidence must be available (SPEC §5.2.4 K15)."""
    data = cu(kb_ready, "approved")
    earlier = {"date": datetime.date(2026, 9, 27), "by": DECIDER, "status": "open", "reason": "rechecked",
               "bind": ZERO64, "evidence": [{**ref("evidence/gone.md", "b" * 64), "locator": "the manifest",
                                            "provenance": "observed"}]}
    data["decisions"] = [earlier, data["decisions"][0]]
    put(kb_ready, "CU", data)
    assert messages_of(kb_ready) == []


def test_the_effective_decisions_missing_evidence_is_reported(kb_ready, source_repo):
    data = cu(kb_ready, "approved")
    data["decisions"][0]["evidence"] = [{**ref("evidence/gone.md", "b" * 64), "locator": "the manifest",
                                         "provenance": "observed"}]
    put(kb_ready, "CU", data)
    assert messages_of(kb_ready) == [("error", "decisions[0].evidence[0]: the working file "
                                               "evidence/gone.md is missing" + retire_missing("CU-0001"))]


@pytest.mark.parametrize("status", records.STATUSES["CT"])
def test_a_task_reports_only_the_structure_of_its_decision_evidence(kb, status):
    """K15 owns a task's evidence availability (SPEC §5.2.4 K15), so a task whose decision evidence is
    gone reports nothing here; a reference that is structurally wrong is still K13's."""
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.")
    gone = {**ref("evidence/gone.md", "b" * 64), "locator": "the manifest", "provenance": "observed"}
    data = ct(kb, status)
    if status == "open":
        data["decisions"] = [{"date": datetime.date(2026, 9, 28), "by": DECIDER, "status": "open",
                              "reason": "rechecked", "bind": ZERO64, "evidence": [gone]}]
    else:
        data["decisions"][0]["evidence"] = [gone]
        data["decisions"][0]["bind"] = subject_digest("CT", data)
    put(kb, "CT", data)
    assert messages_of(kb) == []
    data["decisions"][0]["evidence"] = [
        {**ref("evidence", "b" * 64), "locator": "the manifest", "provenance": "observed"}]
    data["decisions"][0]["bind"] = subject_digest("CT", data)
    put(kb, "CT", data)
    assert messages_of(kb) == [("error", "decisions[0].evidence[0]: 'evidence' is a directory, not a "
                                          "file")]


# --- owners and levels -------------------------------------------------------------------------


def test_every_issue_names_its_owner_and_level(kb_ready, source_repo):
    put(kb_ready, "SC", sc(source=source(sha="a" * 64), basis=[basis(sha="a" * 64)]))
    put(kb_ready, "CT", ct(kb_ready, "open", finding="F-0009"))
    issues = k13_issues(kb_ready)
    assert {(i.code, i.level, i.owner) for i in issues} == {("K13", "warning", "SC-0001"),
                                                            ("K13", "error", "CT-0001")}


def test_the_output_is_deterministic(kb_ready, source_repo):
    put(kb_ready, "SC", sc(source=source(sha="a" * 64), basis=[basis(sha="a" * 64)]))
    assert k13_issues(kb_ready) == k13_issues(kb_ready)
