"""SPEC §12 M6.11 acceptance tests, group 1: *Assertions* (A2).

Every test here drives the real CLI (`m611_helpers.kblam`) from a state its docstring states, and asserts
the whole outcome of every command it runs: the exit status, the complete stdout and stderr, the files the
command changed (whole-tree snapshots from `m611_helpers.tree`/`changed`, taken around each command, so a
stray write by any of them fails the test that ran it), the `validate` result afterwards and, for every
feature command that reads the source, that `source_repo.snapshot()` is equal before and after. Offline
and deterministic: the frozen date pins `created` and decision dates, the fixture KBs enable no Jev
verdict, and the sources are nested Git repositories under `tmp_path`.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path

import m611_helpers as m
from kblam import records
from kblam.finding import yaml_rt

# The same alias a group file declares: autouse, so every test here runs on the pinned date.
frozen_today = m.frozen_today

CHALLENGES = "research-review/challenges"
REVIEW_INDEX = "research-review/INDEX.md"
STAGE = ".kblam/review-staging/SC-0001.yaml"
RECEIPT = ".kblam/review-receipts/SC-0001.json"
EDIT_BASE = ".kblam/review-receipts/SC-0001.edit-base.json"
REGISTRY = ".kblam/review-ids"
TREE_HASH = ".kblam/tree.hash"
RECORD = f"{CHALLENGES}/SC-0001.yaml"
POINTER = "Load the kblam-write skill for how to fix this."
ZERO64 = "0" * 64

# What each command changes, so a stray write by any of them is visible.
A_STAGE = {STAGE, RECEIPT}                              # challenge new, then the author's filling
A_EDIT = {STAGE, EDIT_BASE}                             # challenge edit
A_PUT = {STAGE, RECORD, REVIEW_INDEX, REGISTRY, TREE_HASH}
A_DECIDE = {RECORD, REVIEW_INDEX, TREE_HASH}

F1 = "findings/calibration/F-0001-ratio.md"             # the fixture findings (kb.add), in test order
F2 = "findings/calibration/F-0002-ratio.md"
FINDINGS_INDEX = "findings/INDEX.md"
A_FINDING = {FINDINGS_INDEX, TREE_HASH}                 # what a fixture kb.add adds beside the finding

# The SC fields m611_helpers fills on a staged challenge, as `challenge show` prints them after the
# standard filling; every test spells out the line its own record gives.
SHOW_TAIL = (
    "proposition: The printed byte equality follows from the printed byte values\n"
    "scope: MX-100 capture transcription\n"
    "classification: contradicted\n"
    "basis:\n"
    "  {source} {basis_state}: row 102: printed byte values (internal-inconsistency, observed)\n"
    "usable: The printed byte values may be cited as a report.\n"
    "limits: Do not infer the capture bytes from this row.\n"
    "linked findings: none\n"
    "decisions:\n"
    "  none\n")

# A source whose line 1 and line 2 are the same sentence: the text occurs twice, so which occurrence a
# challenge pins, and which matches lie inside its `lines`, are both observable.
REPEATS_PATH = "notes/repeats.md"
REPEATS = f"{m.SOURCE_REPO}/{REPEATS_PATH}"
REPEATS_TEXT = "Two bytes are equal.\nTwo bytes are equal.\nOther text.\n"
REPEATED = "Two bytes are equal."

# A later version of the trace, committed after a challenge pinned the first one: the assertion text is
# gone from the working tree, so an evaluation that read the working file would report it missing.
TRACE_V2 = ("# MX-100 full-scan trace (second pass)\n"
            "Row 101: bytes 0x3A 0x3B\n"
            "Row 102: not transcribed in this pass.\n"
            "Row 103: bytes 0x40 0x41\n")

@contextmanager
def _changes(kb, expected, what: str):
    """Run one command inside the block and assert it changed exactly the paths in `expected` (or none).

    The snapshot is whole-tree, so a command that writes anything it should not — a stray file, a touched
    source, a rewrite of a file it only reads — fails the test that ran it."""
    before = m.tree(kb)
    yield
    assert m.changed(before, m.tree(kb)) == set(expected), f"{what} changed other files"


def _ok(run: m.Run, what: str, out: str) -> m.Run:
    """The run exited 0, printed exactly `out` and wrote nothing to stderr."""
    assert run.code == 0, f"{what} exited {run.code}:\n{run.out}{run.err}"
    assert run.err == "", f"{what} wrote to stderr: {run.err}"
    assert run.out == out, f"{what} printed:\n{run.out}"
    return run


def _record(path: Path) -> dict:
    """The record file's mapping, as kblam's round-trip loader reads it."""
    return yaml_rt().load(path.read_bytes().decode("utf-8"))


def _sha256(text: str) -> str:
    """sha256 of `text`'s UTF-8 bytes: how kblam hashes an assertion and a source file."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rewrite_assertion(kb, path: Path, **fields) -> None:
    """The author's edit of a staged assertion before the put: a narrowing, or a value put must verify.
    The write is the test's own, so it is bracketed like a command."""
    with _changes(kb, {STAGE}, "the author's edit of the staged assertion"):
        data = _record(path)
        data["source"]["assertion"].update(fields)
        path.write_bytes(records.dump(data))


def _k14_same_bytes(sc: str, finding: str, blob: str) -> str:
    """The K14 error a confirmed challenge gives an excerpt of `finding` that quotes its assertion text
    at the pinned version of notes/repeats.md (SPEC §5.2.4 K14, the same-bytes rule)."""
    return (f"K14 findings/calibration/{finding}-ratio.md:15: {sc} challenges this quoted assertion at "
            f"{REPEATS}@{blob[:12]}:2-2; edit the finding or have this use reviewed "
            f"(kblam use review {sc} {finding} 1 --by NAME --proponent NAME). K10 is checked separately.")


def _retire(rec_id: str) -> str:
    """The step a stale or unavailable reference message ends with (SPEC §5.2.3 Evaluation)."""
    return (f"; restore the original bytes, or retire the record and file a new one (kblam review decide "
            f"{rec_id} --status stale --by NAME --reason TEXT --expect D)")


def _stale_warning(rec_id: str, label: str = "") -> str:
    """The pattern of a K13 stale-reference warning about `rec_id`'s record file, as validate prints it:
    "K13 warning research-review/challenges/SC-0001.yaml:<line>: [label: ]the source changed since
    SC-0001 was written; restore the original bytes, or retire the record and file a new one (...)"."""
    return (rf"K13 warning research-review/challenges/{rec_id}\.yaml:\d+: {re.escape(label)}the source "
            rf"changed since {rec_id} was written" + re.escape(_retire(rec_id)))


# --- the assertion's exact text (A2) --------------------------------------------------------------


def test_a_challenge_captures_the_assertion_lines_exactly(kb, source_repo):
    """Start: no records; resources/mx-docs/notes/full-scan-trace.md is committed (LF) and clean.
    `kblam challenge new <trace> --lines 2-3 --by reviewer-a` exits 0 and stages SC-0001.yaml whose
    assertion is exactly lines 2-3 joined, with no final newline, the sha256 of that text and
    occurrence 1; the author's `kblam put` exits 0 with no diagnostic. Staging changes only the staged
    file and its receipt; the put changes exactly the record, research-review/INDEX.md,
    .kblam/review-ids and .kblam/tree.hash and removes the staged file; `kblam challenge show` prints
    the whole record with the two lines verbatim under "assertion: lines 2-3" and changes nothing; and
    `kblam validate` exits 0 ("OK (0 findings)") and changes nothing. The source is unchanged. A2."""
    source_before = source_repo.snapshot()

    with _changes(kb, A_STAGE, "challenge new and the author's filling"):
        staged = m.stage_challenge(kb, source_repo, "2-3", by="reviewer-a")
    text = f"{m.LINE2}\n{m.LINE3}"
    assert _record(staged.path)["source"] == {
        "path": m.TRACE, "sha256": _sha256(m.TRACE_TEXT), "repo": m.SOURCE_REPO,
        "commit": source_repo.head(), "blob": source_repo.blob(m.TRACE_PATH), "snapshot": None,
        "assertion": {"lines": [2, 3], "text": text, "sha256": _sha256(text), "occurrence": 1},
    }

    with _changes(kb, A_PUT, "put"):
        _ok(m.put(kb, staged), "put", f"kblam put: {staged.id} -> {RECORD}\n")
    assert _record(m.record_path(kb, staged.id))["source"]["assertion"] == {
        "lines": [2, 3], "text": text, "sha256": _sha256(text), "occurrence": 1}

    with _changes(kb, set(), "challenge show"):
        _ok(m.kblam(kb, "challenge", "show", staged.id), "challenge show",
            f"{staged.id} open\n"
            f"subject digest: {m.expect(kb, staged.id)}\n"
            f"source: {m.TRACE}\n"
            f"version: {source_repo.blob(m.TRACE_PATH)}\n"
            "state: current\n"
            "assertion: lines 2-3\n"
            f"  {m.LINE2}\n"
            f"  {m.LINE3}\n"
            + SHOW_TAIL.format(source=m.TRACE, basis_state="current"))

    with _changes(kb, set(), "validate"):
        _ok(m.validate(kb), "validate", "kblam validate: OK (0 findings)\n")
    assert source_repo.snapshot() == source_before


# --- the source edited above the assertion (A2) ---------------------------------------------------


def test_a_source_edited_above_the_assertion_goes_stale_and_is_never_re_targeted(kb, source_repo):
    """Start: the trace is committed and clean; an uncommitted edit of its header leaves the working
    file with other bytes than HEAD's blob, so `challenge new --lines 3-3 --by reviewer-a` records a
    provisional source (repo, commit and blob all null) with the working file's sha256, and the
    author's `kblam put` exits 0. A second edit above the assertion then leaves that reference stale:
    `kblam validate` exits 0 with one K13 warning about the source, "the source changed since SC-0001
    was written", and one for the basis entry read with it, and changes no file. The record's bytes,
    its assertion and its source sha256 are exactly as written — kblam never re-targets a reference at
    the bytes it now finds — and `challenge show` reads "state: stale". Source repository: unchanged.
    A2."""
    first = m.TRACE_TEXT.replace("(transcribed)", "(transcribed, first pass)")
    source_repo.write(m.TRACE_PATH, first)
    source_before = source_repo.snapshot()

    with _changes(kb, A_STAGE, "challenge new and the author's filling"):
        staged = m.stage_challenge(kb, source_repo, "3-3", by="reviewer-a")
    source = _record(staged.path)["source"]
    assert (source["repo"], source["commit"], source["blob"], source["snapshot"]) == (None,) * 4
    assert source["sha256"] == _sha256(first)

    with _changes(kb, A_PUT, "put"):
        _ok(m.put(kb, staged), "put", f"kblam put: {staged.id} -> {RECORD}\n")
    assert source_repo.snapshot() == source_before      # staging and putting left the source alone

    source_repo.write(m.TRACE_PATH, m.TRACE_TEXT.replace("(transcribed)", "(transcribed, later pass)"))
    installed_before = m.record_path(kb, staged.id).read_bytes()
    read_before = source_repo.snapshot()

    with _changes(kb, set(), "validate"):
        run = m.validate(kb)

    assert run.code == 0, run.out
    warnings = run.out.splitlines()
    assert re.fullmatch(_stale_warning(staged.id), warnings[0])
    assert re.fullmatch(_stale_warning(staged.id, "basis[0]: "), warnings[1])
    assert warnings[2:] == ["kblam validate: OK (0 findings)"] and run.err == ""
    assert source_repo.snapshot() == read_before
    assert m.record_path(kb, staged.id).read_bytes() == installed_before

    data = _record(m.record_path(kb, staged.id))
    assert data["source"]["assertion"]["lines"] == [3, 3]
    assert data["source"]["sha256"] == _sha256(first)
    with _changes(kb, set(), "challenge show"):
        _ok(m.kblam(kb, "challenge", "show", staged.id), "challenge show",
            f"{staged.id} open\n"
            f"subject digest: {m.expect(kb, staged.id)}\n"
            f"source: {m.TRACE}\n"
            "version: provisional\n"                    # never rewritten to a pin of the new bytes
            "state: stale (the working file has other bytes)\n"
            "assertion: lines 3-3\n"
            f"  {m.LINE3}\n"
            + SHOW_TAIL.format(source=m.TRACE, basis_state="stale (the working file has other bytes)"))
    assert source_repo.snapshot() == read_before


def test_a_source_edited_above_the_assertion_before_the_put_is_refused(kb, source_repo):
    """Start: the trace is committed and clean; an uncommitted edit of its header makes `challenge new
    --lines 3-3 --by reviewer-a` record a provisional source with those bytes, and the author fills and
    stages SC-0001. A second uncommitted edit above the assertion follows, so the bytes `challenge new`
    captured are gone: `kblam put` exits 1 with "the source changed since kblam challenge new; run it
    again"; nothing is installed, the staged file stands, no other file changes, and `kblam validate`
    exits 0 ("OK (0 findings)"). The source repository is unchanged. A2."""
    source_repo.write(m.TRACE_PATH, m.TRACE_TEXT.replace("(transcribed)", "(transcribed, first pass)"))
    source_before = source_repo.snapshot()
    with _changes(kb, A_STAGE, "challenge new and the author's filling"):
        staged = m.stage_challenge(kb, source_repo, "3-3", by="reviewer-a")
    assert source_repo.snapshot() == source_before      # challenge new read the source only
    source_repo.write(m.TRACE_PATH, m.TRACE_TEXT.replace("(transcribed)", "(transcribed, later pass)"))
    read_before = source_repo.snapshot()

    with _changes(kb, set(), "the refused put"):
        run = m.put(kb, staged)

    assert run.code == 1 and run.out == ""
    assert run.err == f"kblam put: the source changed since kblam challenge new; run it again {POINTER}\n"
    assert staged.path.is_file() and not m.record_path(kb, staged.id).exists()
    assert m.registry(kb) is None
    assert source_repo.snapshot() == read_before
    with _changes(kb, set(), "validate"):
        _ok(m.validate(kb), "validate", "kblam validate: OK (0 findings)\n")
    assert source_repo.snapshot() == read_before


# --- a pinned, confirmed challenge after another commit (A2) ---------------------------------------


def test_a_pinned_confirmed_challenge_is_evaluated_at_its_blob_after_a_new_commit(kb, source_repo):
    """Start: the trace is committed and clean, so `challenge new --lines 3-3 --by reviewer-a` pins the
    source at HEAD's commit and blob, and reviewer-b confirms SC-0001 (both commands exit 0). A later
    commit rewrites the trace so the assertion text is gone from the working tree; `kblam validate`
    still exits 0 with no diagnostic at all, because the assertion and the K13 pin checks are evaluated
    at the pinned blob, and `review list` still calls the record current. `challenge show` reads
    "version: <blob of the first commit>" and "state: pinned". Validate changes no file, and the source
    repository is unchanged by the run. A2."""
    source_before = source_repo.snapshot()

    with _changes(kb, A_STAGE, "challenge new and the author's filling"):
        staged = m.stage_challenge(kb, source_repo, "3-3", by="reviewer-a")
    with _changes(kb, A_PUT, "put"):
        _ok(m.put(kb, staged), "put", f"kblam put: {staged.id} -> {RECORD}\n")
    sc = staged.id
    with _changes(kb, A_DECIDE, "review decide"):
        _ok(m.decide(kb, sc, "confirmed", by="reviewer-b"), "review decide",
            f"kblam review decide: {sc} is now confirmed (subject digest {m.expect(kb, sc)[:12]})\n")

    pinned = _record(m.record_path(kb, sc))["source"]
    assert pinned["commit"] == source_repo.head()
    assert pinned["blob"] == source_repo.blob(m.TRACE_PATH)
    assert source_repo.snapshot() == source_before      # new, put and decide left the source alone

    source_repo.commit(m.TRACE_PATH, TRACE_V2, "second pass")
    assert m.LINE3 not in (source_repo.root / m.TRACE_PATH).read_text(encoding="utf-8")
    read_before = source_repo.snapshot()

    with _changes(kb, set(), "validate"):
        _ok(m.validate(kb), "validate", "kblam validate: OK (0 findings)\n")

    with _changes(kb, set(), "challenge show"):
        _ok(m.kblam(kb, "challenge", "show", sc), "challenge show",
            f"{sc} confirmed\n"
            f"subject digest: {m.expect(kb, sc)}\n"
            f"source: {m.TRACE}\n"
            f"version: {pinned['blob']}\n"
            "state: pinned\n"
            "assertion: lines 3-3\n"
            f"  {m.LINE3}\n"
            + SHOW_TAIL.format(source=m.TRACE, basis_state="pinned").replace(
                "decisions:\n  none\n",
                f"decisions:\n  2026-09-28 reviewer-b confirmed: reviewed the record "
                f"bind {m.expect(kb, sc)[:12]}\n"))

    with _changes(kb, set(), "review list"):
        _ok(m.kblam(kb, "review", "list"), "review list",
            f"SC-0001 challenge confirmed {m.expect(kb, sc)[:12]} {m.TRACE}:3-3 current\n")
    assert source_repo.snapshot() == read_before


# --- the text twice within `lines`, and the occurrence the record pins (A2) -------------------------


def test_a_first_put_refuses_an_assertion_narrowed_to_text_that_occurs_twice(kb, source_repo):
    """Start: notes/repeats.md holds the same sentence on lines 1 and 2, committed and clean, and
    `challenge new --lines 1-2 --by reviewer-a` is accepted (the captured two-line block matches once).
    The author then narrows `text` to the repeated sentence, which occurs twice within lines 1-2:
    `kblam put` exits 1 with "SC-0001: the assertion text occurs 2 times within lines 1-2; narrow it to
    one; widen or narrow the assertion's lines and put it again". Nothing is installed, the staged file
    stands, no other file changes, `kblam validate` then exits 0 ("OK (0 findings)"), and the source
    repository is unchanged. A2."""
    source_repo.commit(REPEATS_PATH, REPEATS_TEXT, "repeats")
    source_before = source_repo.snapshot()

    with _changes(kb, A_STAGE, "challenge new and the author's filling"):
        staged = m.stage_challenge(kb, source_repo, "1-2", by="reviewer-a", path=REPEATS)
    _rewrite_assertion(kb, staged.path, text=REPEATED, sha256=None, occurrence=None)
    read_before = source_repo.snapshot()

    with _changes(kb, set(), "the refused put"):
        run = m.put(kb, staged)

    assert run.code == 1 and run.out == ""
    assert run.err == (
        f"kblam put: SC-0001: the assertion text occurs 2 times within lines 1-2; narrow it to one; "
        f"widen or narrow the assertion's lines and put it again {POINTER}\n")
    assert staged.path.is_file() and not m.record_path(kb, staged.id).exists()
    assert m.registry(kb) is None
    assert source_repo.snapshot() == read_before
    with _changes(kb, set(), "validate"):
        _ok(m.validate(kb), "validate", "kblam validate: OK (0 findings)\n")
    assert source_repo.snapshot() == source_before


def test_only_the_occurrence_inside_the_lines_is_the_assertion(kb, source_repo):
    """Start: notes/repeats.md holds the same sentence on lines 1 and 2, committed and clean; F-0001
    quotes line 1 and F-0002 quotes line 2 (installed as fixture setup). `challenge new --lines 2-2
    --by reviewer-a` finds one match inside its lines, so it captures occurrence 2 — the second match in
    the file — and the put exits 0. reviewer-b's `review decide --status confirmed` exits 0 and names
    F-0002, and not F-0001, as newly affected; `kblam challenge uses` lists F-0002's excerpt and not
    F-0001's; `kblam validate` exits 1 with that one K14 error at the pinned blob and lines 2-2. The
    excerpt quoting the identical bytes at line 1 is untouched: the assertion is the occurrence it
    pinned, and kblam never re-targets it to another match. The source repository is unchanged. A2."""
    source_repo.commit(REPEATS_PATH, REPEATS_TEXT, "repeats")
    source_before = source_repo.snapshot()

    with _changes(kb, {F1} | A_FINDING, "the fixture F-0001"):
        m.quoting_finding(kb, "F-0001", source_repo, "1-1", path=REPEATS,
                          claim="Row 101 of the MX-100 trace is the first of two equal rows.")
    with _changes(kb, {F2} | A_FINDING, "the fixture F-0002"):
        m.quoting_finding(kb, "F-0002", source_repo, "2-2", path=REPEATS,
                          claim="Row 102 of the MX-100 trace repeats the first row.")

    with _changes(kb, A_STAGE, "challenge new and the author's filling"):
        staged = m.stage_challenge(kb, source_repo, "2-2", by="reviewer-a", path=REPEATS)
    sc = staged.id
    with _changes(kb, A_PUT, "put"):
        _ok(m.put(kb, staged), "put", f"kblam put: {sc} -> {RECORD}\n")
    installed = _record(m.record_path(kb, sc))["source"]
    assert installed["assertion"] == {"lines": [2, 2], "text": REPEATED, "sha256": _sha256(REPEATED),
                                      "occurrence": 2}
    blob = installed["blob"]

    with _changes(kb, A_DECIDE, "review decide"):
        decided = _ok(m.decide(kb, sc, "confirmed", by="reviewer-b"), "review decide",
                      f"kblam review decide: {sc} is now confirmed (subject digest "
                      f"{m.expect(kb, sc)[:12]})\n"
                      f"kblam review decide: {sc} now affects F-0002; run kblam challenge uses {sc} for "
                      f"each excerpt and the command that fixes it\n"
                      f"{_k14_same_bytes(sc, 'F-0002', blob)}\n"
                      f"kblam review decide: done, but kblam validate still fails (1 error(s) listed "
                      f"above, owned by other findings or records)\n")
    assert "F-0001" not in decided.out                            # the same bytes outside `lines`

    with _changes(kb, set(), "challenge uses"):
        _ok(m.kblam(kb, "challenge", "uses", sc), "challenge uses",
            f"F-0002 findings/calibration/F-0002-ratio.md:15 excerpt 1 same: error; "
            f"kblam use review {sc} F-0002 1 --by NAME --proponent NAME\n")

    read_before = source_repo.snapshot()
    with _changes(kb, set(), "validate"):
        failing = m.validate(kb)

    assert failing.code == 1 and failing.err == ""
    assert failing.out == f"{_k14_same_bytes(sc, 'F-0002', blob)}\nkblam validate: 1 error(s) in findings/\n"
    assert source_repo.snapshot() == read_before
    assert source_repo.snapshot() == source_before


# --- narrowing before and after the first put (A2) ------------------------------------------------


def test_narrowing_the_assertion_before_the_first_put_is_accepted(kb, source_repo):
    """Start: the trace is committed and clean; `challenge new --lines 2-4 --by reviewer-a` captures
    lines 2-4. The author narrows `lines` to 3-3 and `text` to line 3, both inside what was captured:
    `kblam put` exits 0 with no diagnostic, and the installed SC-0001 has that assertion — lines 3-3,
    the narrowed text, its sha256 and occurrence 1 — with the source pinned at HEAD. The put changes
    exactly the record, research-review/INDEX.md, .kblam/review-ids and .kblam/tree.hash, and removes
    the staged file; `kblam validate` exits 0 afterwards, and the source repository is unchanged. A2."""
    source_before = source_repo.snapshot()

    with _changes(kb, A_STAGE, "challenge new and the author's filling"):
        staged = m.stage_challenge(kb, source_repo, "2-4", by="reviewer-a")
    _rewrite_assertion(kb, staged.path, lines=[3, 3], text=m.LINE3, sha256=None, occurrence=None)

    with _changes(kb, A_PUT, "put"):
        _ok(m.put(kb, staged), "put", f"kblam put: {staged.id} -> {RECORD}\n")
    installed = _record(m.record_path(kb, staged.id))["source"]
    assert installed["assertion"] == {"lines": [3, 3], "text": m.LINE3, "sha256": _sha256(m.LINE3),
                                      "occurrence": 1}
    assert installed["blob"] == source_repo.blob(m.TRACE_PATH)

    with _changes(kb, set(), "validate"):
        _ok(m.validate(kb), "validate", "kblam validate: OK (0 findings)\n")
    assert source_repo.snapshot() == source_before


def test_narrowing_the_assertion_after_the_first_put_is_refused(kb, source_repo):
    """Start: SC-0001 is installed by a first put and still open, pinned at HEAD. `kblam challenge edit
    SC-0001` exits 0 and stages a copy with its edit-base receipt; the author narrows the assertion's
    text in that copy. `kblam put` exits 1 with "SC-0001: source is not a free field, so it must be put
    as installed. Only proposition, scope, classification, basis, usable, limits, linked_findings change
    through an edit; run kblam challenge edit SC-0001 again": the assertion never changes after the
    first put. No file changes, the staged copy stands, the installed record is byte-identical, and
    `kblam validate` still exits 0. The source repository is unchanged. A2."""
    source_before = source_repo.snapshot()

    with _changes(kb, A_STAGE, "challenge new and the author's filling"):
        staged = m.stage_challenge(kb, source_repo, "3-3", by="reviewer-a")
    with _changes(kb, A_PUT, "put"):
        _ok(m.put(kb, staged), "put", f"kblam put: {staged.id} -> {RECORD}\n")
    installed_before = m.record_path(kb, staged.id).read_bytes()

    with _changes(kb, A_EDIT, "challenge edit"):
        edit = _ok(m.kblam(kb, "challenge", "edit", staged.id), "challenge edit",
                   f"{kb.root / Path(STAGE)}\n")
    copy = Path(edit.out.strip())
    assert copy == kb.root / ".kblam" / "review-staging" / "SC-0001.yaml"
    _rewrite_assertion(kb, copy, text="bytes 0x3A 0x3B; the two bytes are equal.")
    read_before = source_repo.snapshot()

    with _changes(kb, set(), "the refused put"):
        run = m.put(kb, copy)

    assert run.code == 1 and run.out == ""
    assert run.err == (
        "kblam put: SC-0001: source is not a free field, so it must be put as installed. Only "
        "proposition, scope, classification, basis, usable, limits, linked_findings change through an "
        f"edit; run kblam challenge edit SC-0001 again {POINTER}\n")
    assert copy.is_file()
    assert m.record_path(kb, staged.id).read_bytes() == installed_before
    assert source_repo.snapshot() == read_before
    with _changes(kb, set(), "validate"):
        _ok(m.validate(kb), "validate", "kblam validate: OK (0 findings)\n")
    assert source_repo.snapshot() == source_before


# --- a wrong non-null assertion.sha256 (A2) -------------------------------------------------------


def test_a_first_put_refuses_a_wrong_assertion_sha256(kb, source_repo):
    """Start: the trace is committed and clean; `challenge new --lines 3-3 --by reviewer-a` captured the
    assertion and its text's sha256, and the author fills the record but replaces that sha256 with a
    non-null wrong value. `kblam put` exits 1 with "SC-0001: assertion.sha256 is 0…0, but the
    assertion's text hashes to <its real sha256>; set it to null and put it again, and put writes it".
    Nothing is installed, the staged file stands, no other file changes, `kblam validate` then exits 0
    ("OK (0 findings)"), and the source repository is unchanged. A2."""
    source_before = source_repo.snapshot()

    with _changes(kb, A_STAGE, "challenge new and the author's filling"):
        staged = m.stage_challenge(kb, source_repo, "3-3", by="reviewer-a")
    _rewrite_assertion(kb, staged.path, sha256=ZERO64)
    read_before = source_repo.snapshot()

    with _changes(kb, set(), "the refused put"):
        run = m.put(kb, staged)

    assert run.code == 1 and run.out == ""
    assert run.err == (
        f"kblam put: SC-0001: assertion.sha256 is {ZERO64}, but the assertion's text hashes to "
        f"{_sha256(m.LINE3)}; set it to null and put it again, and put writes it {POINTER}\n")
    assert staged.path.is_file() and not m.record_path(kb, staged.id).exists()
    assert m.registry(kb) is None
    assert source_repo.snapshot() == read_before
    with _changes(kb, set(), "validate"):
        _ok(m.validate(kb), "validate", "kblam validate: OK (0 findings)\n")
    assert source_repo.snapshot() == source_before
