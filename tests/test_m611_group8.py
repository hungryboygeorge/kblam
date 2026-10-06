"""SPEC §12 M6.11 acceptance tests, test group 8: deletion (A6).

"*Deletion* (A6): a record deleted or renamed (K13 "missing"; `review index` and `validate --record` do
not forget it; `validate --record --forget-missing` does, printing each ID); after a fresh clone, the
registry created from the records present."

Every test drives the real CLI in process (m611_helpers.kblam) and states, in its docstring, the start
state, the command and actor, the exit status, the diagnostics, the files changed, the validation result
afterwards and the acceptance criterion it demonstrates. Every CLI invocation — the setup calls of
`challenge new`, the author's filling and `put`, `task new`, `review decide`, and every refusal and
read-only call — runs inside `changes`: the whole KB tree (m611_helpers.tree) and the source repository
(SourceRepo.snapshot) are compared before and after, as an exact set of changed paths (`set()` for a
read-only call, and for a record-level refusal apart from the §6 check state `validate --record` writes
before it validates) and as equal source state. Every Run is asserted in full: exit code, whole stdout,
and stderr byte for byte (the write commands here carry a tree.hash note on stderr, and a `validate
--record` over a KB with a finding carries the §6 Jev note). The tests' own fixture edits — deleting or
renaming a record file, restoring bytes, removing `.kblam/` to stand in for a clone — sit outside those
windows. No test calls `accept_tree`: this group's subject is K13 (the missing record and the registry)
and tree.hash behaviour, and `accept_tree` re-records tree.hash unconditionally. Offline and
deterministic: `frozen_today` pins `created` and a decision's `date` at 2026-09-28, and the fixtures
enable no Jev verdict.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

import m611_helpers as m
from conftest import dump_record
from kblam.finding import fingerprint, yaml_rt
from kblam.jev import CACHE_NAME, PairCache
from kblam.treehash import read_recorded, tree_digest_v2
from kblam.view import load_view

# The same alias a group file declares: autouse, so every test in this module runs on the pinned date.
frozen_today = m.frozen_today

REVIEW = "research-review"
CHALLENGES = f"{REVIEW}/challenges"
TASKS = f"{REVIEW}/tasks"
INDEX = f"{REVIEW}/INDEX.md"
STAGING = ".kblam/review-staging"
RECEIPTS = ".kblam/review-receipts"
REGISTRY = ".kblam/review-ids"
TREE_HASH = ".kblam/tree.hash"
# `validate --record` runs the §6 check phase before the record validation, so it also writes the check
# state: one appended line per finding to `.kblam/checks.jsonl`, and its pair cache and open items.
CHECKS = ".kblam/checks.jsonl"
PAIRS = ".kblam/pairs.sqlite"
REVIEW_ITEMS = ".kblam/review.jsonl"
CHECK_STATE = {CHECKS, PAIRS, REVIEW_ITEMS}          # a KB with a finding to check
CHECK_STATE_EMPTY = {PAIRS, REVIEW_ITEMS}            # no finding, so nothing is appended to the log

OK = "kblam validate: OK (0 findings)"
OK_ONE = "kblam validate: OK (1 findings)"
RECORDED = "; recorded .kblam/tree.hash for this tree"
NOT_RECORDED = "; tree.hash not recorded"
PENDING = "CT-0001 open replication of F-0001: Does an independent measurement establish the claim?"
# The §6 check phase's own output for the one finding the clone tests install.
JEV_NOTE = ("kblam validate --record: [jev.thresholds] enables no Jev verdict, so Jev was not asked "
            "(quantities were compared)\n")
# The index is generated, so deleting or renaming a record leaves it stale until `review index` runs.
STALE_INDEX = (f"K13 {INDEX}: INDEX.md differs from the generated review index; it is never edited by "
               f"hand. Run kblam review index to regenerate it")
# SPEC §5.2.6: the report a registered ID with no record gets; line 0, so no line number is shown.
MISSING = (f"{{rec_id}} is missing from {REVIEW}/; records are never deleted or renamed; restore it from "
           f"git")
# SPEC §5.2.6 and §8 item 3: the note a write prints when tree.hash cannot advance.
OUT_OF_BAND = (f"kblam review index: findings/ or {REVIEW}/ was changed outside kblam since kblam last "
               f"wrote it; tree.hash not advanced. Run kblam validate --record once the change is "
               f"validated.\n")
# SPEC §8 item 3: what `validate --record` adds when it bootstraps a clone's one finding.
BASELINE = ("kblam validate: there was no .kblam/tree.hash (a new clone, or .kblam/ was deleted), so Jev was "
            "not asked: 1 finding(s) accepted from the repository as checked at their current fingerprints. "
            "kblam audit checks them with Jev")
INDEX_WRITTEN = f"kblam review index: wrote {INDEX}\n"

NOTHING: set[str] = set()

# The SC free fields the author fills (SPEC §5.2.3): a claim, a scope, `contradicted`, one basis entry
# on the source itself, a usable remainder and limits. The basis entry's `sha256` and pin are left null
# for `put` to fill in.
SC_FIELDS = {
    "proposition": "The printed byte equality follows from the printed byte values",
    "scope": ["MX-100 capture transcription"],
    "classification": "contradicted",
    "basis": [{"path": m.TRACE, "sha256": None, "repo": None, "commit": None, "blob": None,
               "snapshot": None, "locator": "row 102: printed byte values",
               "role": "internal-inconsistency", "provenance": "observed"}],
    "usable": "The printed byte values may be cited as a report.",
    "limits": "Do not infer the capture bytes from this row.",
}
CT_FIELDS = {
    "question": "Does an independent measurement establish the claim?",
    "method": "Repeat the capture with the documented settings.",
    "outcomes": {"supports": "The ratio is within 0.1%.", "refutes": "The ratio differs by more.",
                 "inconclusive": "The capture is too noisy to tell."},
    "controls": ["same firmware version"],
    "stop": "Stop after three captures.",
    "expected_evidence": ["an evidence/ capture package"],
}


# --- bracketing every command ---------------------------------------------------------------------


@contextlib.contextmanager
def changes(kb, source_repo, expected=NOTHING):
    """Bracket one CLI invocation: the whole KB tree and the source repository just before it and just
    after it. `expected` is the exact set of KB paths the call may change — empty for a read-only call,
    and for a record-level refusal apart from the §6 check state `validate --record` writes first. A
    stray write, in the KB (kblam's own state included) or in the source repository, fails here."""
    before, source_before = m.tree(kb), source_repo.snapshot()
    yield
    assert m.changed(before, m.tree(kb)) == set(expected), "the KB tree changed otherwise"
    assert source_repo.snapshot() == source_before, "the source repository changed"


def cli(kb, source_repo, expected, *argv: object) -> m.Run:
    """One `kblam` invocation inside `changes`; the Run, for the caller to assert in full."""
    with changes(kb, source_repo, expected):
        return m.kblam(kb, *argv)


def exactly(run: m.Run, out: str, what: str, *, code: int = 0, err: str = "") -> m.Run:
    """A Run asserted in full: this exit code, this whole stdout, this stderr."""
    assert run.code == code, f"{what} exited {run.code}:\n{run.out}{run.err}"
    assert run.err == err, f"{what} wrote to stderr:\n{run.err}"
    assert run.out == out, f"{what} printed:\n{run.out}"
    return run


def read(kb, source_repo, *argv: object, out: str, code: int = 0, err: str = "") -> m.Run:
    """A read-only command (`validate`, `review list`), bracketed: it changes nothing in the KB and
    nothing in the source repository."""
    run = cli(kb, source_repo, NOTHING, *argv)
    return exactly(run, out, " ".join(str(arg) for arg in argv), code=code, err=err)


def record_rel(kb, rec_id: str) -> str:
    """Where the record is installed, relative to the KB root."""
    return m.record_path(kb, rec_id).relative_to(kb.root).as_posix()


def roots_of(snapshot: dict[str, bytes]) -> dict[str, bytes]:
    """The files of findings/ and the review root in a tree() snapshot: the two roots the SPEC's writes
    cover, leaving out kblam's own state under `.kblam/`."""
    return {name: data for name, data in snapshot.items()
            if name.startswith("findings/") or name.startswith(f"{REVIEW}/")}


def check_line(kb, finding_id: str) -> str:
    """The §6 check line `validate --record` prints for a finding: its ID, K3 fingerprint and candidate
    count. The fingerprint is derived through the library, never hard-coded."""
    finding = next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)
    return f"kblam validate --record: {finding_id} ({fingerprint(finding, kb.cfg.scope_separator)}): 0 candidate(s)"


def recorded(kb) -> bool:
    """Whether tree.hash is the format-2 line of the tree as it is now."""
    return read_recorded(kb.cfg) == (2, REVIEW, tree_digest_v2(load_view(kb.cfg)))


def accepted(kb, finding_id: str) -> bool:
    """Whether the finding is marked checked at its current fingerprint (a bootstrap accepts it so)."""
    finding = next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)
    return PairCache(kb.cfg.state_dir / CACHE_NAME).was_checked(finding_id,
                                                                fingerprint(finding, kb.cfg.scope_separator))


def key_line(path: Path, key: str) -> int:
    """The 1-based line of a top-level key in a record file: the line K13 reports an issue about it on."""
    lines = path.read_bytes().decode("utf-8").splitlines()
    return next(i for i, line in enumerate(lines, 1) if line.startswith(f"{key}:"))


# --- the setup commands, one bracketed call each ---------------------------------------------------


def fill(path: Path, **fields) -> None:
    """The author's filling of the staged record's free fields: a fixture edit, outside every command
    window. `put` verifies the rest of the staged file against the receipt."""
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    data.update(fields)
    path.write_bytes(dump_record(data).encode("utf-8"))


def new_challenge(kb, source_repo, rec_id: str, lines: str, *, by: str = "reviewer-a") -> Path:
    """`kblam challenge new <source> --lines A-B --by NAME` (bracketed: the staged record and its
    allocation receipt); the staged file, which its author may edit. The command prints its path."""
    rel = f"{STAGING}/{rec_id}.yaml"
    run = cli(kb, source_repo, {rel, f"{RECEIPTS}/{rec_id}.json"},
              "challenge", "new", m.TRACE, "--lines", lines, "--by", by)
    exactly(run, f"{kb.root / rel}\n", "challenge new")
    return kb.root / rel


def install(kb, source_repo, staged: Path, rec_id: str) -> None:
    """`kblam put <staged SC- or CT->` (bracketed: the record, the review index, the registry and
    tree.hash; the staged file is gone), printing the record's path."""
    rel = record_rel(kb, rec_id)
    run = cli(kb, source_repo,
              {staged.relative_to(kb.root).as_posix(), rel, INDEX, REGISTRY, TREE_HASH},
              "put", str(staged))
    exactly(run, f"kblam put: {rec_id} -> {rel}\n", "put")
    assert not staged.exists()


def challenge(kb, source_repo, rec_id: str, lines: str, *, by: str = "reviewer-a") -> None:
    """A whole challenge through the CLI: `challenge new` (bracketed), the author's filling (a fixture
    edit) and `put` (bracketed)."""
    staged = new_challenge(kb, source_repo, rec_id, lines, by=by)
    fill(staged, **SC_FIELDS)
    install(kb, source_repo, staged, rec_id)


def task(kb, source_repo, rec_id: str, finding: str, *, by: str = "reviewer-a",
         proponent: str = "researcher-a") -> None:
    """A whole claim task through the CLI: `task new` (bracketed: the staged record and its allocation
    receipt), the researcher's filling (a fixture edit) and `put` (bracketed)."""
    rel = f"{STAGING}/{rec_id}.yaml"
    run = cli(kb, source_repo, {rel, f"{RECEIPTS}/{rec_id}.json"},
              "task", "new", finding, "--kind", "replication", "--by", by, "--proponent", proponent)
    exactly(run, f"{kb.root / rel}\n", "task new")
    staged = kb.root / rel
    fill(staged, **CT_FIELDS)
    install(kb, source_repo, staged, rec_id)


def confirm(kb, source_repo, rec_id: str, *, by: str = "reviewer-b",
            reason: str = "read the source and pinned it") -> None:
    """`kblam review decide <ID> --status confirmed --by NAME --reason TEXT --expect D` (bracketed: the
    record, the review index and tree.hash; the registry does not change)."""
    digest = m.expect(kb, rec_id)
    run = cli(kb, source_repo, {record_rel(kb, rec_id), INDEX, TREE_HASH},
              "review", "decide", rec_id, "--status", "confirmed", "--by", by, "--reason", reason,
              "--expect", digest)
    exactly(run, f"kblam review decide: {rec_id} is now confirmed (subject digest {digest[:12]})\n",
            "review decide")


def forget(kb, source_repo, *, code: int, changed: set[str], err: str = "") -> m.Run:
    """`kblam validate --record --forget-missing` (bracketed: `changed` is the exact set of KB paths it
    may write — the registry, tree.hash when the validation after the drop is clean, and the §6 check
    state it appends to). The Run, whose whole output and stderr the caller asserts."""
    run = cli(kb, source_repo, changed, "validate", "--record", "--forget-missing")
    assert run.code == code, run.out + run.err
    assert run.err == err, run.err
    return run


def forgot(rec_id: str) -> str:
    """The line `--forget-missing` prints for one ID (SPEC §5.2.6)."""
    return f"kblam validate --record: forgot {rec_id} (no record in {REVIEW}/)"


def missing(kb, rec_id: str) -> str:
    """The K13 issue for a registered ID whose record is gone, displayed at its canonical path."""
    return f"K13 {record_rel(kb, rec_id)}: {MISSING.format(rec_id=rec_id)}"


# --- a record deleted ------------------------------------------------------------------------------


def test_a_deleted_record_is_missing_and_restoring_it_clears_the_report(kb, source_repo):
    """Start: no findings, no records; the trace is committed and clean, so `challenge new` pins the
    source it stages. Commands, each bracketed: `challenge new --lines 3-3 --by reviewer-a` (writes the
    staged record and its allocation receipt), the author's filling (a fixture edit), `kblam put` (writes
    the record, the review index, the registry and tree.hash; the staged file is gone), `kblam review
    decide SC-0001 --status confirmed --by reviewer-b --reason ... --expect D` (writes the record, the
    review index and tree.hash), `kblam validate` and `kblam review list` (reads: exit 0, the validation
    OK and the confirmed challenge listed). Fixture edit: the installed record file is deleted out of
    band, as a checkout that lost it leaves the KB. Commands after it, each bracketed: `kblam validate`
    (a read: it fails), `kblam review index` (writes the review index from the records present and nothing
    else), `kblam validate --record` (refused: the record write frame writes nothing, though its §6 check
    phase writes `.kblam/pairs.sqlite` and `.kblam/review.jsonl` first), then — after the fixture edit
    that restores the record's bytes and the index's bytes as `git checkout` would — `kblam validate` and
    `kblam review list` (reads). Files changed: the staged record and its receipt, then the record, the
    review index, the registry and tree.hash, then the record, the review index and tree.hash, then the
    review index alone (its bytes change to the empty index), then the check pair cache and open-item
    file, and nothing else — all five reads change nothing, in the KB or the source repository.
    Diagnostics: the K13 issue "INDEX.md differs from the generated review index ..." on
    research-review/INDEX.md (line 0), then "SC-0001 is missing from research-review/; records are never
    deleted or renamed; restore it from git" on the canonical path the record should have, then "kblam
    validate: 2 error(s) in research-review/"; after `review index` the one missing error and "kblam validate: 1
    error(s) in research-review/; tree.hash not recorded". `review index` exits 0 with "kblam review index: wrote
    research-review/INDEX.md" and the stderr note that tree.hash was not advanced because the two roots
    changed outside kblam. The registry still lists SC-0001 after all of it: neither `review index` nor
    `validate --record` forgets a registered ID. Restoring the two files puts both roots back byte for
    byte and validate exits 0 again. Acceptance 6: a deleted record is reported, and no write silently
    drops its registration."""
    challenge(kb, source_repo, "SC-0001", "3-3")
    confirm(kb, source_repo, "SC-0001")
    digest = m.expect(kb, "SC-0001")
    read(kb, source_repo, "validate", out=f"{OK}\n")
    read(kb, source_repo, "review", "list",
         out=f"SC-0001 challenge confirmed {digest[:12]} {m.TRACE}:3-3 current\n")
    assert m.registry(kb) == ["SC-0001"]

    before = m.tree(kb)                                   # the tree as the last command left it
    (kb.root / record_rel(kb, "SC-0001")).unlink()        # fixture edit: the record is gone

    read(kb, source_repo, "validate", code=1, out="\n".join([
        STALE_INDEX,
        missing(kb, "SC-0001"),
        "kblam validate: 2 error(s) in research-review/",
    ]) + "\n")

    run = cli(kb, source_repo, {INDEX}, "review", "index")
    exactly(run, INDEX_WRITTEN, "review index", err=OUT_OF_BAND)
    assert m.registry(kb) == ["SC-0001"]                  # the index never forgets a registered ID

    run = cli(kb, source_repo, CHECK_STATE_EMPTY, "validate", "--record")
    exactly(run, "\n".join([
        missing(kb, "SC-0001"),
        f"kblam validate: 1 error(s) in research-review/{NOT_RECORDED}",
    ]) + "\n", "validate --record", code=1)
    assert m.registry(kb) == ["SC-0001"]                  # nor does validate --record

    for rel in (record_rel(kb, "SC-0001"), INDEX):        # fixture edit: restore from git
        (kb.root / rel).write_bytes(before[rel])
    assert roots_of(m.tree(kb)) == roots_of(before), "the restore did not put the tree back"

    read(kb, source_repo, "validate", out=f"{OK}\n")
    read(kb, source_repo, "review", "list",
         out=f"SC-0001 challenge confirmed {digest[:12]} {m.TRACE}:3-3 current\n")
    assert m.registry(kb) == ["SC-0001"]


# --- a record renamed ------------------------------------------------------------------------------


def test_a_renamed_record_is_missing_and_the_forget_drop_stands(kb, source_repo):
    """Start: no findings, no records; the trace is committed and clean. Commands, each bracketed:
    `challenge new --lines 3-3 --by reviewer-a` (staged record and receipt), the author's filling (a
    fixture edit), `kblam put` (the record, the review index, the registry and tree.hash; the staged file
    is gone). Fixture edit: the record file is renamed in its own folder, SC-0001.yaml to SC-0002.yaml —
    neither a deletion nor an edit kblam can follow. Commands after it, each bracketed: `kblam validate`
    (a read: it fails), `kblam review index` (rewrites the index), `kblam validate --record` (refused: no
    record write, but its §6 check phase writes `.kblam/pairs.sqlite` and `.kblam/review.jsonl` first),
    `kblam validate --record --forget-missing` (drops the ID and prints it; still refused, because the
    renamed file is an error of its own), `kblam validate` (a read). Files changed: the staged record and
    its receipt, then the record, the review index, the registry and tree.hash, then the review index
    alone, then the pair cache and open-item file, then the registry alone (the drop), and nothing else —
    the two reads change nothing, in the KB or the source repository. Diagnostics: the stale-index issue,
    the missing-record issue on research-review/challenges/SC-0001.yaml, and the K13 issue on the renamed
    file's `id` line, "id: 'SC-0001' does not match the file name's ID (SC-0002)", then "kblam validate:
    3 error(s) in research-review/"; `review index` exits 0 with its one line and the out-of-band note on stderr;
    `validate --record` exits 1, writes no record and still lists SC-0001 in the registry; `validate
    --record --forget-missing` exits 1, prints "forgot SC-0001 (no record in research-review/)", empties
    the registry and records no tree.hash — the drop stands while the tree still fails; the read afterwards
    shows only the id mismatch, and the renamed file is still on disk byte for byte. Acceptance 6: a
    renamed record is reported, and the flag's drop is not undone by a failing validation."""
    challenge(kb, source_repo, "SC-0001", "3-3")
    assert m.registry(kb) == ["SC-0001"]

    old = kb.root / record_rel(kb, "SC-0001")
    renamed = old.with_name("SC-0002.yaml")               # fixture edit: a rename, in place
    old.rename(renamed)
    mismatch = (f"K13 {CHALLENGES}/SC-0002.yaml:{key_line(renamed, 'id')}: id: 'SC-0001' does not match "
                f"the file name's ID (SC-0002)")

    read(kb, source_repo, "validate", code=1, out="\n".join([
        STALE_INDEX,
        missing(kb, "SC-0001"),
        mismatch,
        "kblam validate: 3 error(s) in research-review/",
    ]) + "\n")

    run = cli(kb, source_repo, {INDEX}, "review", "index")
    exactly(run, INDEX_WRITTEN, "review index", err=OUT_OF_BAND)
    assert m.registry(kb) == ["SC-0001"]

    run = cli(kb, source_repo, CHECK_STATE_EMPTY, "validate", "--record")
    exactly(run, "\n".join([
        missing(kb, "SC-0001"),
        mismatch,
        f"kblam validate: 2 error(s) in research-review/{NOT_RECORDED}",
    ]) + "\n", "validate --record", code=1)
    assert m.registry(kb) == ["SC-0001"]

    run = forget(kb, source_repo, code=1, changed={REGISTRY})
    assert run.out == "\n".join([
        forgot("SC-0001"),
        mismatch,
        f"kblam validate: 1 error(s) in research-review/{NOT_RECORDED}",
    ]) + "\n"
    assert m.registry(kb) == []

    read(kb, source_repo, "validate", code=1, out="\n".join([
        mismatch,
        "kblam validate: 1 error(s) in research-review/",
    ]) + "\n")
    assert renamed.is_file()                               # kblam removed nothing: it reported


def test_a_moved_record_is_reported_as_a_known_record_out_of_place(kb, source_repo):
    """Start: no findings, no records; the trace is committed and clean. Commands, each bracketed:
    `challenge new --lines 3-3 --by reviewer-a` (staged record and receipt), the author's filling (a
    fixture edit), `kblam put` (the record, the review index, the registry and tree.hash; the staged file
    is gone). Fixture edit: the record file is moved out of its kind's folder, to
    research-review/SC-0001.yaml — a rename the review root cannot hold. Commands after it, each
    bracketed: `kblam validate` (a read: it fails), `kblam review index` (rewrites the index),
    `kblam validate --record` (refused: no record write, but its §6 check phase writes
    `.kblam/pairs.sqlite` and `.kblam/review.jsonl` first), `kblam validate --record --forget-missing`
    (drops the ID and prints it; the moved file keeps failing), `kblam validate` (a read). Files changed:
    the staged record and its receipt, then the record, the review index, the registry and tree.hash, then
    the review index alone, then the pair cache and open-item file, then the registry alone, and nothing
    else — the two reads change nothing, in the KB or the source repository. Diagnostics: the stale-index
    issue; then, on the moved file and only while the registry still lists SC-0001, "a challenge must sit
    directly in its kind's folder (research-review/challenges/SC-0001.yaml); this ID is a record kblam
    knows, so restore research-review/ from git (the record's place is its canonical path)" beside the
    missing-record issue, and "kblam validate: 3 error(s) in research-review/"; `validate --record` exits 1 and
    writes no record; `validate --record --forget-missing` exits 1, prints "forgot SC-0001 (no record in
    research-review/)", empties the registry, records no tree.hash, and the moved file's message becomes
    the one for an ID kblam does not know, "this ID is not an installed record or a registered ID, so
    restage it with kblam challenge new, kblam task new or kblam use review, each of which writes the
    receipt kblam put needs"; the read afterwards still exits 1 on that message. Acceptance 6: a renamed
    record is reported with the recovery that fits it, and the flag drops the registration, never the
    file."""
    challenge(kb, source_repo, "SC-0001", "3-3")

    old = kb.root / record_rel(kb, "SC-0001")
    moved = kb.root / REVIEW / "SC-0001.yaml"             # fixture edit: out of its kind's folder
    old.rename(moved)

    canonical = f"{CHALLENGES}/SC-0001.yaml"
    known = (f"a challenge must sit directly in its kind's folder ({canonical}); this ID is a record "
             f"kblam knows, so restore {REVIEW}/ from git (the record's place is its canonical path)")
    unknown = (f"a challenge must sit directly in its kind's folder ({canonical}); this ID is not an "
               f"installed record or a registered ID, so restage it with kblam challenge new, kblam task "
               f"new or kblam use review, each of which writes the receipt kblam put needs")

    read(kb, source_repo, "validate", code=1, out="\n".join([
        STALE_INDEX,
        f"K13 {REVIEW}/SC-0001.yaml: {known}",
        missing(kb, "SC-0001"),
        "kblam validate: 3 error(s) in research-review/",
    ]) + "\n")

    run = cli(kb, source_repo, {INDEX}, "review", "index")
    exactly(run, INDEX_WRITTEN, "review index", err=OUT_OF_BAND)
    assert m.registry(kb) == ["SC-0001"]

    run = cli(kb, source_repo, CHECK_STATE_EMPTY, "validate", "--record")
    exactly(run, "\n".join([
        f"K13 {REVIEW}/SC-0001.yaml: {known}",
        missing(kb, "SC-0001"),
        f"kblam validate: 2 error(s) in research-review/{NOT_RECORDED}",
    ]) + "\n", "validate --record", code=1)
    assert m.registry(kb) == ["SC-0001"]

    run = forget(kb, source_repo, code=1, changed={REGISTRY})
    assert run.out == "\n".join([
        forgot("SC-0001"),
        f"K13 {REVIEW}/SC-0001.yaml: {unknown}",
        f"kblam validate: 1 error(s) in research-review/{NOT_RECORDED}",
    ]) + "\n"
    assert m.registry(kb) == []

    read(kb, source_repo, "validate", code=1, out="\n".join([
        f"K13 {REVIEW}/SC-0001.yaml: {unknown}",
        "kblam validate: 1 error(s) in research-review/",
    ]) + "\n")
    assert moved.is_file()


# --- what the flag forgets, and what it prints ------------------------------------------------------


def test_forget_missing_prints_each_id_it_drops(kb, source_repo):
    """Start: finding F-0001 installed through `kb.add` (a fixture edit, which indexes it and records the
    tree), no records; the trace is committed and clean. Commands, each bracketed: `challenge new --lines
    3-3 --by reviewer-a` (staged record and receipt), the author's filling (a fixture edit), `kblam put`
    (the record, the review index, the registry and tree.hash; the staged file is gone), `task new F-0001
    --kind replication --by reviewer-a --proponent researcher-a` (a second staged record and its receipt),
    the researcher's filling (a fixture edit), `kblam put` (the task record, the review index, the
    registry and tree.hash; the staged file is gone), `kblam validate` and `kblam review list` (reads:
    exit 0; validate prints the pending-task line, and review list lists both open records). Fixture edit:
    both record files are deleted out of band. Commands after it, each bracketed: `kblam validate` (a
    read: it fails), `kblam review index` (rewrites the index), `kblam validate --record` (refused: no
    record write, but its §6 check phase writes the check state first),
    `kblam validate --record --forget-missing` (drops both IDs and prints each), `kblam validate` (a
    read). Files changed: the two staged records and their receipts, then the two records with the review
    index, registry and tree.hash, then the review index alone, then `.kblam/checks.jsonl`,
    `.kblam/pairs.sqlite` and `.kblam/review.jsonl` (the check phase over F-0001), then `.kblam/checks.jsonl`
    again with the registry and tree.hash (the drop and the clean recording), and nothing else — the read
    changes nothing, in the KB or the source repository. Diagnostics: the stale-index issue and one
    missing-record issue per ID — on research-review/challenges/SC-0001.yaml and
    research-review/tasks/CT-0001.yaml — then "kblam validate: 3 error(s) in research-review/"; `review index`
    exits 0 and keeps both IDs registered; `validate --record` exits 1, prints the check line "kblam
    validate --record: F-0001 (<fingerprint>): 0 candidate(s)" and the §6 Jev note on stderr, and keeps
    both IDs; `validate --record --forget-missing` exits 0, prints that check line, then "forgot CT-0001
    (no record in research-review/)" and "forgot SC-0001 (no record in research-review/)" — one line per
    ID, in ID order — then "kblam validate: OK (1 findings); recorded .kblam/tree.hash for this tree", and
    empties the registry. Acceptance 6: every registered ID with no record is reported, and each is
    printed as it is dropped."""
    kb.add("F-0001", "ratio", m.CLAIM)                    # fixture edit: the finding the task binds
    challenge(kb, source_repo, "SC-0001", "3-3")
    task(kb, source_repo, "CT-0001", "F-0001")
    assert m.registry(kb) == ["CT-0001", "SC-0001"]

    read(kb, source_repo, "validate", out=f"{PENDING}\n{OK_ONE}; 1 pending task(s)\n")
    read(kb, source_repo, "review", "list", out="\n".join([
        f"SC-0001 challenge open {m.expect(kb, 'SC-0001')[:12]} {m.TRACE}:3-3 current",
        f"CT-0001 task open {m.expect(kb, 'CT-0001')[:12]} replication of F-0001 current",
    ]) + "\n")

    for rec_id in ("SC-0001", "CT-0001"):                 # fixture edit: both records are gone
        (kb.root / record_rel(kb, rec_id)).unlink()

    read(kb, source_repo, "validate", code=1, out="\n".join([
        STALE_INDEX,
        missing(kb, "SC-0001"),
        missing(kb, "CT-0001"),
        "kblam validate: 3 error(s) in research-review/",
    ]) + "\n")

    run = cli(kb, source_repo, {INDEX}, "review", "index")
    exactly(run, INDEX_WRITTEN, "review index", err=OUT_OF_BAND)
    assert m.registry(kb) == ["CT-0001", "SC-0001"]

    run = cli(kb, source_repo, CHECK_STATE, "validate", "--record")
    exactly(run, "\n".join([
        check_line(kb, "F-0001"),
        missing(kb, "SC-0001"),
        missing(kb, "CT-0001"),
        f"kblam validate: 2 error(s) in research-review/{NOT_RECORDED}",
    ]) + "\n", "validate --record", code=1, err=JEV_NOTE)
    assert m.registry(kb) == ["CT-0001", "SC-0001"]

    run = forget(kb, source_repo, code=0, changed={CHECKS, REGISTRY, TREE_HASH}, err=JEV_NOTE)
    assert run.out == "\n".join([
        check_line(kb, "F-0001"),
        forgot("CT-0001"),
        forgot("SC-0001"),
        f"{OK_ONE}{RECORDED}",
    ]) + "\n"
    assert m.registry(kb) == []
    read(kb, source_repo, "validate", out=f"{OK_ONE}\n")


# --- after a fresh clone ---------------------------------------------------------------------------


def test_a_fresh_clone_creates_the_registry_from_the_records_present(kb, source_repo):
    """Start: finding F-0001 installed through `kb.add` (a fixture edit), no records; the trace is
    committed and clean. Commands, each bracketed: `challenge new --lines 3-3 --by reviewer-a` (staged
    record and receipt), the author's filling (a fixture edit), `kblam put` (the record, the review index,
    the registry and tree.hash; staged file gone), `kblam review decide SC-0001 --status confirmed --by
    reviewer-b --reason ...
    --expect D` (the record, the review index and tree.hash), `task new F-0001 --kind replication --by
    reviewer-a --proponent researcher-a` (a second staged record and its receipt), the researcher's
    filling (a fixture edit), `kblam put` (the task record, the review index, the registry and tree.hash;
    staged file gone). Fixture edit: `.kblam/` is removed entirely — the state a fresh clone of the
    project leaves, since `kblam init` puts `.kblam/` in `.gitignore` while the records and the review
    index are committed and stay. Commands after it, each bracketed: `kblam validate` (a read: exit 0),
    `kblam review index` (the clone's first write: the tree validates cleanly, so it bootstraps, writing
    the registry from the records present, tree.hash and the accepted-from-the-repository mark in
    `.kblam/pairs.sqlite`, asking Jev nothing), then, after `.kblam/` is removed again (a fixture edit),
    `kblam validate --record` (the same bootstrap by the explicit path), `kblam review list` (a read).
    Files changed: the two staged records and their receipts, then each record with the review index,
    registry and tree.hash, then the registry, tree.hash and `.kblam/pairs.sqlite` (each bootstrap);
    neither read changes anything, in the KB or the source repository. Diagnostics: `review index` exits
    0 with "kblam review index: wrote research-review/INDEX.md and .kblam/tree.hash" and nothing on
    stderr; the registry it writes holds both records present, in ID order; `validate --record` exits 0,
    printing the pending-task line, "kblam validate: OK (1 findings); 1 pending task(s); recorded
    .kblam/tree.hash for this tree" and the note that Jev was not asked and 1 finding was accepted from
    the repository, writing the same registry. Acceptance 6: the registry is created from the records
    present, so a clone loses no registration."""
    kb.add("F-0001", "ratio", m.CLAIM)                    # fixture edit: the finding the task binds
    challenge(kb, source_repo, "SC-0001", "3-3")
    confirm(kb, source_repo, "SC-0001")
    task(kb, source_repo, "CT-0001", "F-0001")
    assert m.registry(kb) == ["CT-0001", "SC-0001"]

    shutil.rmtree(kb.root / ".kblam")                     # fixture edit: the state a clone does not have
    assert not (kb.root / REGISTRY).exists() and not (kb.root / TREE_HASH).exists()
    assert (kb.root / record_rel(kb, "SC-0001")).is_file() and (kb.root / INDEX).is_file()

    read(kb, source_repo, "validate", out=f"{PENDING}\n{OK_ONE}; 1 pending task(s)\n")

    run = cli(kb, source_repo, {REGISTRY, TREE_HASH, PAIRS}, "review", "index")
    exactly(run, f"kblam review index: wrote {INDEX} and .kblam/tree.hash\n", "review index")
    assert m.registry(kb) == ["CT-0001", "SC-0001"]       # from the records present, not from nothing
    assert recorded(kb) and accepted(kb, "F-0001")

    shutil.rmtree(kb.root / ".kblam")                     # fixture edit: a clone again
    run = cli(kb, source_repo, {REGISTRY, TREE_HASH, PAIRS}, "validate", "--record")
    exactly(run, "\n".join([
        PENDING,
        f"{OK_ONE}; 1 pending task(s){RECORDED}",
        BASELINE,
    ]) + "\n", "validate --record")
    assert m.registry(kb) == ["CT-0001", "SC-0001"]
    assert recorded(kb) and accepted(kb, "F-0001")

    read(kb, source_repo, "review", "list", out="\n".join([
        f"SC-0001 challenge confirmed {m.expect(kb, 'SC-0001')[:12]} {m.TRACE}:3-3 current",
        f"CT-0001 task open {m.expect(kb, 'CT-0001')[:12]} replication of F-0001 current",
    ]) + "\n")
