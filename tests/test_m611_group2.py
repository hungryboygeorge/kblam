"""SPEC §12 M6.11 test group 2 (pins): the acceptance tests for Git pins, snapshots and the states the
one resolver gives them (SPEC §5.2.2 File references and Git pins, §5.2.6), driven through the real CLI
(`kblam.cli.main`, in process) on the nested Git fixture repository.

Every test's docstring states its starting records and their statuses and the source's state, the
command and its actor (`--by`), the exit status, the diagnostics, the files it changed (the whole-tree
snapshot of `m611_helpers.tree`/`changed`; `set()` means a refusal or a read-only command changed
nothing), the validation afterwards, and the acceptance criteria it demonstrates: Acceptance 1 (no
feature command writes source bytes or Git administrative state in any source repository, which
`source_repo.snapshot()` shows) and Acceptance 2 (an excerpt is surfaced or covered at the exact pinned
version -- here, that a challenge is read at the pin it recorded, not at whatever the working file now
holds).

Offline and deterministic; `_today` is pinned at TODAY. System and global Git config are ignored by the
fixture. The three tests that need a repository shape (a submodule, a linked worktree, SHA-256 object
IDs) skip, with the reason git gave, where this platform's Git cannot build it.
"""

from __future__ import annotations

import hashlib
import subprocess

import pytest

import m611_helpers as m
from conftest import SOURCE_REPO, TRACE_PATH, TRACE_TEXT, SourceRepo
from kblam import gitpin, paths, records, sources
from kblam.finding import plain_data, yaml_rt

frozen_today = m.frozen_today        # created and a decision's date are TODAY: exact bytes and show output

REVIEW = "research-review"
CHALLENGES = f"{REVIEW}/challenges"
STAGING = ".kblam/review-staging"
RECEIPTS = ".kblam/review-receipts"
REGISTRY = ".kblam/review-ids"
TREE_HASH = ".kblam/tree.hash"
TRACE = m.TRACE
README = "evidence/2026-09-22-ratio/README.md"
SNAPSHOT = "snapshots/full-scan-trace.md"
# The step every stale or unavailable reference message ends with (SPEC §5.2.3 Evaluation).
RETIRE_PINNED = (f"; restore the pinned bytes of {TRACE}, or retire the record and file a new one (kblam "
                 "review decide source-challenge-0001 --status stale --by NAME --reason TEXT --expect D)")

# A second version of the trace: line 3 (the line the challenges below quote) holds other text, so a
# challenge whose assertion is checked against the wrong version is an occurrence error, not a pass.
V2_TEXT = TRACE_TEXT.replace("the two bytes are equal.", "the two bytes differ by 0x01.")
V2_LINE3 = "Row 102: bytes 0x3A 0x3B; the two bytes differ by 0x01."
V3_TEXT = V2_TEXT + "Row 104: bytes 0x50 0x51\n"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def changes(kb, action):
    """(what `action` returned, the KB paths it changed): the whole-tree comparison every test asserts
    on, so a stray write by the command under test fails the test."""
    before = m.tree(kb)
    result = action()
    return result, m.changed(before, m.tree(kb))


def validates_clean(kb) -> m.Run:
    """`kblam validate` exits 0 and changes no file: a read-only command, whose whole-tree snapshot
    must be byte-identical after it (SPEC §5.2.4: warnings never block)."""
    run, touched = changes(kb, lambda: m.validate(kb))
    m.ok(run, "validate")
    assert touched == set()
    return run


def source_of(kb, rec_id: str) -> dict:
    """The installed record's `source` mapping, as kblam wrote it (SPEC §5.2.3)."""
    data = plain_data(yaml_rt().load(m.record_path(kb, rec_id).read_bytes().decode("utf-8")))
    return dict(data["source"])


def edit_record(kb, rec_id: str, **fields) -> None:
    """Hand-edit an installed record's top-level fields: fixture setup for a state no command writes
    (a wrong pin). `challenge pin` never replaces a pin, and a decided record cannot be edited."""
    path = m.record_path(kb, rec_id)
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    for key, value in fields.items():
        data[key] = value
    path.write_bytes(records.dump(data))


def drop_objects(repo: SourceRepo, keep: str) -> None:
    """Fixture setup: make every commit but `keep` unreachable and prune it, so its objects are gone."""
    repo.git("reset", "--hard", keep)
    repo.git("reflog", "expire", "--expire=now", "--all")
    repo.git("gc", "--prune=now", "--quiet")


def gone(repo: SourceRepo, oid: str) -> bool:
    """Whether this Git really dropped the object (some builds keep it; the tests then skip)."""
    return not repo.git("cat-file", "-t", oid, check=False).strip()


# The working paths a command in these tests legitimately writes inside the KB's own worktree.
KBLAM_OWNED = ("research-review/", ".kblam/")


def kb_worktree(repo: SourceRepo) -> dict:
    """A full `SourceRepo.snapshot()` of the KB's own worktree with the working paths kblam writes
    (the review root and .kblam/) filtered out of `files` and of the status text. Everything else --
    HEAD, its symbolic ref, refs, the index and every other working byte, including the findings root
    -- stays in, so a command that commits, stages (`git update-index`), checks out or writes anything
    else fails the comparison (Acceptance 1)."""
    snap = repo.snapshot()
    snap["files"] = {path: data for path, data in snap["files"].items()
                     if not path.startswith(KBLAM_OWNED)}
    snap["status"] = "\n".join(line for line in snap["status"].splitlines()
                               if not any(owned in line for owned in KBLAM_OWNED))
    return snap


PIN_REFUSED = (f"kblam challenge pin: the blob at {TRACE} in its worktree's HEAD does not hold exactly "
               f"these bytes, so source-challenge-0001's source stays provisional. Commit them and pin it again, or "
               f"pin a copy with --snapshot PATH\n")


# --- a dirty worktree ------------------------------------------------------------------------------


def test_a_dirty_working_file_is_unpinned_and_pinned_only_by_snapshot(kb, source_repo):
    """Start: the nested source repository resources/mx-docs, whose notes/full-scan-trace.md is
    committed and then edited without a commit; no records. `kblam challenge new
    resources/mx-docs/notes/full-scan-trace.md --lines 3-3 --by reviewer-a` exits 0 and stages
    source-challenge-0001, changing .kblam/review-staging/source-challenge-0001.yaml and .kblam/review-receipts/source-challenge-0001.json:
    its source carries the working file's sha256 and no pin, because HEAD's blob holds other bytes
    (SPEC §5.2.2: pin automatically only when the blob has exactly the working bytes). `kblam put`
    exits 0 and installs it (research-review/challenges/source-challenge-0001.yaml, research-review/INDEX.md,
    .kblam/review-ids, .kblam/tree.hash; the staged file goes), and `validate` exits 0.
    `kblam challenge pin source-challenge-0001` then exits 1 with "the blob at ... does not hold exactly these
    bytes, so source-challenge-0001's source stays provisional ... or pin a copy with --snapshot PATH", changing
    nothing. `kblam challenge pin source-challenge-0001 --snapshot snapshots/full-scan-trace.md`, a copy of the
    dirty bytes, exits 0 with "source-challenge-0001 pinned" and writes the record and .kblam/tree.hash alone;
    `validate` exits 0 and `challenge show` prints "version: snapshots/full-scan-trace.md" and
    "state: current" (the working file still holds those bytes). The working file is then rewritten
    once more: `challenge show` prints "state: pinned" with the same assertion line, and `validate`
    exits 0 -- the snapshot, not the working file, is what keeps the judgement at its version
    (Acceptance 1, Acceptance 2: the way to pin bytes HEAD does not hold is a snapshot)."""
    dirty = TRACE_TEXT + "Row 104: written after the commit.\n"
    source_repo.write(TRACE_PATH, dirty)
    src_before = source_repo.snapshot()

    staged, touched = changes(kb, lambda: m.stage_challenge(kb, source_repo, "3-3", by="reviewer-a"))
    assert touched == {f"{STAGING}/source-challenge-0001.yaml", f"{RECEIPTS}/source-challenge-0001.json"}
    data = plain_data(yaml_rt().load(staged.path.read_bytes().decode("utf-8")))
    assert data["source"]["sha256"] == sha256_hex(dirty.encode("utf-8"))
    assert (data["source"]["repo"], data["source"]["commit"], data["source"]["blob"],
            data["source"]["snapshot"]) == (None, None, None, None)
    assert data["source"]["assertion"]["text"] == m.LINE3

    run, touched = changes(kb, lambda: m.put(kb, staged))
    m.ok(run, "put")
    assert touched == {f"{CHALLENGES}/source-challenge-0001.yaml", f"{REVIEW}/INDEX.md", REGISTRY, TREE_HASH,
                       f"{STAGING}/source-challenge-0001.yaml"}
    validates_clean(kb)

    run, touched = changes(kb, lambda: m.pin_challenge(kb, "source-challenge-0001"))
    assert run.code == 1
    assert run.err == PIN_REFUSED
    assert touched == set()
    validates_clean(kb)
    assert source_repo.snapshot() == src_before     # no command has written in the source repository

    kb.write(SNAPSHOT, dirty)
    run, touched = changes(kb, lambda: m.pin_challenge(kb, "source-challenge-0001", snapshot=SNAPSHOT))
    m.ok(run, "challenge pin")
    assert run.out == (f"kblam challenge pin: source-challenge-0001 pinned "
                       f"(subject digest {m.expect(kb, 'source-challenge-0001')[:12]})\n")
    assert touched == {f"{CHALLENGES}/source-challenge-0001.yaml", TREE_HASH}
    source = source_of(kb, "source-challenge-0001")
    assert source["snapshot"] == SNAPSHOT
    assert (source["repo"], source["commit"], source["blob"]) == (None, None, None)
    assert source_repo.snapshot() == src_before

    validates_clean(kb)
    show, touched = changes(kb, lambda: m.kblam(kb, "challenge", "show", "source-challenge-0001"))
    m.ok(show, "challenge show")
    assert f"version: {SNAPSHOT}" in show.out
    assert "state: current" in show.out      # the working file still holds the snapped bytes
    assert touched == set()

    # With the working file rewritten again, the snapshot is what keeps the source available and
    # pinned: the record is never re-targeted to the new working bytes (SPEC §5.2.2).
    source_repo.write(TRACE_PATH, dirty + "Row 105: and again.\n")
    src_before = source_repo.snapshot()
    show, touched = changes(kb, lambda: m.kblam(kb, "challenge", "show", "source-challenge-0001"))
    m.ok(show, "challenge show")
    assert f"version: {SNAPSHOT}" in show.out
    assert "state: pinned" in show.out
    assert f"\n  {m.LINE3}\n" in show.out
    assert touched == set()
    validates_clean(kb)
    assert source_repo.snapshot() == src_before


def test_a_pinned_challenge_is_read_at_its_blob_while_the_worktree_is_dirty(kb, source_repo):
    """Start: source-challenge-0001 confirmed on resources/mx-docs/notes/full-scan-trace.md lines 3-3, pinned to the
    owner's HEAD commit and blob (reviewer-a created it, reviewer-b confirmed it); validate exits 0.
    The working file is then rewritten without a commit, so its bytes differ from the pinned ones.
    `kblam challenge show source-challenge-0001` exits 0 and prints "version: <blob>" and "state: pinned", and the
    assertion text it prints is still the pinned line -- a pinned reference is read at its blob and
    needs no working file (SPEC §5.2.2), so the judgement stays at the version it was made on.
    `kblam validate` exits 0 with no K13 line. Neither command changes a file, and the source
    repository is unchanged (Acceptance 1, Acceptance 2)."""
    blob = source_repo.blob(TRACE_PATH)
    sc = m.confirmed_challenge(kb, source_repo, "3-3", by="reviewer-a", decider="reviewer-b")
    validates_clean(kb)

    source_repo.write(TRACE_PATH, TRACE_TEXT.replace(m.LINE3, "Row 102: rewritten, not committed."))
    src_before = source_repo.snapshot()

    show, touched = changes(kb, lambda: m.kblam(kb, "challenge", "show", sc))
    m.ok(show, "challenge show")
    assert show.out.startswith(f"{sc} confirmed\n")
    assert f"version: {blob}" in show.out
    assert "state: pinned" in show.out
    assert f"\n  {m.LINE3}\n" in show.out      # the assertion is the pinned version's line
    assert touched == set()

    run, touched = changes(kb, lambda: m.validate(kb))
    m.ok(run, "validate")
    assert "K13" not in run.out
    assert touched == set()
    assert source_repo.snapshot() == src_before


def test_a_crlf_checkout_of_an_lf_blob_is_pinned_only_by_snapshot(kb, source_repo):
    """Start: the nested source repository configured with core.autocrlf=true, so checking the
    committed LF trace out again gives a CRLF working file that differs from the blob. `kblam
    challenge new ... --lines 3-3 --by reviewer-a` exits 0: source-challenge-0001's sha256 is the CRLF file's,
    its assertion is the LF line (a source is read as LF-normalised text), and its source is
    unpinned because HEAD's blob holds the LF bytes -- a CRLF checkout is not pinned automatically
    (SPEC §5.2.2). `kblam put` exits 0 and `validate` exits 0. `kblam challenge pin source-challenge-0001` exits 1
    with "... does not hold exactly these bytes ... or pin a copy with --snapshot PATH" and changes
    nothing; `kblam challenge pin source-challenge-0001 --snapshot snapshots/full-scan-trace.md`, a copy of the
    CRLF bytes, exits 0 and writes the record and .kblam/tree.hash alone. `validate` then exits 0 and
    `challenge show` prints "version: snapshots/full-scan-trace.md" and "state: current"; once the
    working file is checked out again as LF, the same show prints "state: pinned" -- the snapshot
    keeps those bytes (Acceptance 1, Acceptance 2)."""
    source_repo.git("config", "core.autocrlf", "true")
    (source_repo.root / TRACE_PATH).unlink()
    source_repo.git("checkout", "--", TRACE_PATH)
    crlf = (source_repo.root / TRACE_PATH).read_bytes()
    assert b"\r\n" in crlf and crlf != TRACE_TEXT.encode("utf-8")
    src_before = source_repo.snapshot()

    staged, touched = changes(kb, lambda: m.stage_challenge(kb, source_repo, "3-3", by="reviewer-a"))
    assert touched == {f"{STAGING}/source-challenge-0001.yaml", f"{RECEIPTS}/source-challenge-0001.json"}
    data = plain_data(yaml_rt().load(staged.path.read_bytes().decode("utf-8")))
    assert data["source"]["sha256"] == sha256_hex(crlf)
    assert (data["source"]["repo"], data["source"]["commit"], data["source"]["blob"]) == (None, None, None)
    assert data["source"]["assertion"]["text"] == m.LINE3     # LF text, matched against the LF source

    run, touched = changes(kb, lambda: m.put(kb, staged))
    m.ok(run, "put")
    assert touched == {f"{CHALLENGES}/source-challenge-0001.yaml", f"{REVIEW}/INDEX.md", REGISTRY, TREE_HASH,
                       f"{STAGING}/source-challenge-0001.yaml"}
    validates_clean(kb)

    run, touched = changes(kb, lambda: m.pin_challenge(kb, "source-challenge-0001"))
    assert run.code == 1
    assert run.err == PIN_REFUSED
    assert touched == set()
    assert source_repo.snapshot() == src_before     # no command has written in the source repository

    kb.write(SNAPSHOT, crlf)
    run, touched = changes(kb, lambda: m.pin_challenge(kb, "source-challenge-0001", snapshot=SNAPSHOT))
    m.ok(run, "challenge pin")
    assert touched == {f"{CHALLENGES}/source-challenge-0001.yaml", TREE_HASH}
    assert source_of(kb, "source-challenge-0001")["snapshot"] == SNAPSHOT
    assert source_repo.snapshot() == src_before

    validates_clean(kb)
    show, touched = changes(kb, lambda: m.kblam(kb, "challenge", "show", "source-challenge-0001"))
    m.ok(show, "challenge show")
    assert f"version: {SNAPSHOT}" in show.out
    assert "state: current" in show.out      # the CRLF working file still holds the snapped bytes
    assert touched == set()

    # The working file checked out as LF again no longer holds the pinned CRLF bytes: the snapshot is
    # what keeps the source available and pinned, and the assertion is still read at those bytes.
    source_repo.git("config", "core.autocrlf", "false")
    (source_repo.root / TRACE_PATH).unlink()
    source_repo.git("checkout", "--", TRACE_PATH)
    assert (source_repo.root / TRACE_PATH).read_bytes() == TRACE_TEXT.encode("utf-8")
    src_before = source_repo.snapshot()
    show, touched = changes(kb, lambda: m.kblam(kb, "challenge", "show", "source-challenge-0001"))
    m.ok(show, "challenge show")
    assert f"version: {SNAPSHOT}" in show.out
    assert "state: pinned" in show.out
    assert f"\n  {m.LINE3}\n" in show.out
    assert touched == set()
    validates_clean(kb)
    assert source_repo.snapshot() == src_before


# --- the pinned version is gone ---------------------------------------------------------------------


def test_a_pinned_version_that_is_gone_is_a_k13_error_for_a_confirmed_challenge(kb, source_repo):
    """Start: source-challenge-0001 confirmed (reviewer-a created it, reviewer-b confirmed it -- "the actor" for the
    decision) with its source pinned to the second commit's blob and no snapshot; validate exits 0.
    The first commit is then checked out and the second commit and its blob are pruned, so no copy
    with the pinned bytes can be read while the working file still holds other bytes. `kblam
    validate` exits 1 and reports K13 twice for source-challenge-0001, once for the source and once for its basis
    entry, each "the pinned version is not present" -- never "the source now says ..." (SPEC §5.2.3
    Evaluation), because a pinned reference is unavailable, not stale; the Severity table makes that
    an error on an effective record and a warning while the challenge is open, which the next test
    shows. `kblam challenge show source-challenge-0001` exits 0 and prints "state: unavailable". Neither command
    changes a file, and the source repository is unchanged (Acceptance 1, Acceptance 2)."""
    first = source_repo.head()
    source_repo.commit(TRACE_PATH, V2_TEXT, "row 102 reworded")
    blob = source_repo.blob(TRACE_PATH)
    sc = m.confirmed_challenge(kb, source_repo, "3-3", by="reviewer-a", decider="reviewer-b")
    assert source_of(kb, sc)["blob"] == blob
    validates_clean(kb)

    drop_objects(source_repo, first)
    if not gone(source_repo, blob):
        pytest.skip("this git kept the unreachable object, so the pinned version is not gone")
    src_before = source_repo.snapshot()

    run, touched = changes(kb, lambda: m.validate(kb))
    assert run.code == 1
    lines = [line for line in run.out.splitlines() if "the pinned version is not present" in line]
    assert len(lines) == 2
    # "K13 <path>:<line>: <message>": the source's message, and the basis entry's, which names itself.
    labels = [line.split(": ", 2)[1] for line in lines]
    assert sorted(labels) == ["basis[0]", "the pinned version is not present" + RETIRE_PINNED]
    assert all(line.startswith(f"K13 {CHALLENGES}/source-challenge-0001.yaml:") for line in lines)
    assert all(line.endswith(": the pinned version is not present" + RETIRE_PINNED) for line in lines)
    assert "the working file" not in run.out            # the pinned bytes are gone, not the file
    assert touched == set()

    show, touched = changes(kb, lambda: m.kblam(kb, "challenge", "show", sc))
    m.ok(show, "challenge show")
    assert show.out.startswith(f"{sc} confirmed\n")
    assert "state: unavailable" in show.out
    assert touched == set()
    assert source_repo.snapshot() == src_before


def test_a_pinned_version_that_is_gone_is_a_warning_while_the_challenge_is_open(kb, source_repo):
    """Start: source-challenge-0001 open (reviewer-a created it) with its source pinned to the second commit's blob
    and no snapshot; validate exits 0. The first commit is checked out and the second commit and its
    blob are pruned. `kblam validate` then exits 0: the same two K13 lines as for a confirmed
    challenge are printed with level "warning", because an unavailable reference is a warning while a
    record is open and an error once it is effective (SPEC §5.2.4 Severity) -- a pending judgement
    does not block, and the summary still reads "kblam validate: OK (0 findings)". The command changes
    nothing, and the source repository is unchanged (Acceptance 1, Acceptance 2)."""
    first = source_repo.head()
    source_repo.commit(TRACE_PATH, V2_TEXT, "row 102 reworded")
    blob = source_repo.blob(TRACE_PATH)
    sc = m.challenge(kb, source_repo, "3-3", by="reviewer-a")
    assert source_of(kb, sc)["blob"] == blob
    validates_clean(kb)

    drop_objects(source_repo, first)
    if not gone(source_repo, blob):
        pytest.skip("this git kept the unreachable object, so the pinned version is not gone")
    src_before = source_repo.snapshot()

    run, touched = changes(kb, lambda: m.validate(kb))
    assert run.code == 0
    lines = [line for line in run.out.splitlines() if "the pinned version is not present" in line]
    assert len(lines) == 2
    assert all(line.startswith(f"K13 warning {CHALLENGES}/source-challenge-0001.yaml:") for line in lines)
    assert "kblam validate: OK (0 findings)" in run.out
    assert touched == set()
    assert source_repo.snapshot() == src_before


# --- a pin the commit's tree does not hold ----------------------------------------------------------


def test_a_blob_the_commits_tree_does_not_hold_at_that_path_is_a_k13_error(kb, source_repo):
    """Start: source-challenge-0001 open (reviewer-a created it) pinned to the trace's commit and blob; validate
    exits 0. The record is then hand-edited to name another blob that exists in the same repository
    (the blob of notes/other.md, committed afterwards) -- no command writes a wrong pin, since
    `challenge pin` never replaces one and a decided record cannot be edited. `kblam validate` exits
    1 and reports K13 twice for source-challenge-0001, once for the source and once for its basis entry: "the tree
    of <commit> holds <trace blob> at notes/full-scan-trace.md, not <other blob>" (SPEC §5.2.2 Git
    pins: the commit's tree must hold the blob at the path). The command changes no file, and the
    source repository is unchanged (Acceptance 1)."""
    head = source_repo.head()
    sc = m.challenge(kb, source_repo, "3-3", by="reviewer-a")
    validates_clean(kb)
    source_repo.commit("notes/other.md", "other notes\n", "other")
    trace_blob, other_blob = source_repo.blob(TRACE_PATH), source_repo.blob("notes/other.md")

    source = {**source_of(kb, sc), "blob": other_blob}
    edit_record(kb, sc, source=source)
    src_before = source_repo.snapshot()

    run, touched = changes(kb, lambda: m.validate(kb))
    assert run.code == 1
    want = f"the tree of {head} holds {trace_blob} at {TRACE_PATH}, not {other_blob}"
    lines = [line for line in run.out.splitlines() if line.startswith("K13 ")]
    assert len(lines) == 2
    assert sum(line.endswith(f"source: {want}") for line in lines) == 1
    assert sum(line.endswith(f"basis[0]: {want}") for line in lines) == 1
    assert touched == set()
    assert source_repo.snapshot() == src_before


# --- the worktree shapes a pin must get right -------------------------------------------------------


def test_a_nested_source_repository_pins_to_its_own_toplevel_not_the_kb_root(kb, source_repo):
    """Start: the KB root is itself a Git worktree with evidence/2026-09-22-ratio/README.md committed,
    and it holds the nested source repository resources/mx-docs. `kblam challenge new
    evidence/2026-09-22-ratio/README.md --lines 1-1 --by reviewer-a` and `challenge new
    resources/mx-docs/notes/full-scan-trace.md --lines 3-3 --by reviewer-a`, each followed by `kblam
    put`, exit 0; the first pair changes research-review/challenges/source-challenge-0001.yaml,
    research-review/INDEX.md, .kblam/review-ids, .kblam/tree.hash and
    .kblam/review-receipts/source-challenge-0001.json, and the second the same with source-challenge-0002 (each staged file is
    created by its `challenge new` and removed by its `put`). source-challenge-0001 records repo "." (the KB's own
    worktree) and source-challenge-0002 records repo "resources/mx-docs", with the owning commit and blob: a nested
    repository is a worktree in its own right, so the pin names it rather than the enclosing one
    (SPEC §5.2.2 Git pins). `kblam validate` then exits 0 and changes nothing. Neither command
    commits, stages or checks anything out in either worktree: the nested source repository's whole
    snapshot, and the KB's own worktree snapshot (HEAD, refs, the index and every working byte, with
    only kblam's own review root and .kblam/ filtered out), are each equal before and after every
    command -- so a change staged in the KB's own index, such as `git update-index
    --assume-unchanged` on the pinned README, fails the test (Acceptance 1)."""
    kb_repo = SourceRepo(kb.root, ".").init()
    kb_repo.commit(README, "manifest\n", "evidence")
    src_before, kb_before = source_repo.snapshot(), kb_worktree(kb_repo)

    own, touched = changes(kb, lambda: m.challenge(kb, source_repo, 1, by="reviewer-a", path=README))
    assert touched == {f"{CHALLENGES}/source-challenge-0001.yaml", f"{REVIEW}/INDEX.md", REGISTRY, TREE_HASH,
                       f"{RECEIPTS}/source-challenge-0001.json"}
    assert source_repo.snapshot() == src_before
    assert kb_worktree(kb_repo) == kb_before
    nested, touched = changes(kb, lambda: m.challenge(kb, source_repo, "3-3", by="reviewer-a"))
    assert touched == {f"{CHALLENGES}/source-challenge-0002.yaml", f"{REVIEW}/INDEX.md", REGISTRY, TREE_HASH,
                       f"{RECEIPTS}/source-challenge-0002.json"}
    assert source_repo.snapshot() == src_before
    assert kb_worktree(kb_repo) == kb_before

    own_source = source_of(kb, own)
    assert (own_source["path"], own_source["sha256"], own_source["repo"], own_source["commit"],
            own_source["blob"], own_source["snapshot"]) == (README, sha256_hex(b"manifest\n"), ".",
                                                           kb_repo.head(), kb_repo.blob(README), None)
    assert source_of(kb, nested)["repo"] == SOURCE_REPO
    assert source_of(kb, nested)["commit"] == source_repo.head()
    assert source_of(kb, nested)["blob"] == source_repo.blob(TRACE_PATH)

    run, touched = changes(kb, lambda: m.validate(kb))
    m.ok(run, "validate")
    assert "K13" not in run.out
    assert touched == set()
    assert source_repo.snapshot() == src_before
    assert kb_worktree(kb_repo) == kb_before


def test_a_source_in_a_submodule_pins_to_the_submodule(kb, source_repo):
    """Start: resources/mx-specs is its own repository with MX-100/pinout.md committed; it is added to
    the nested source repository as the submodule vendor/mx-specs, whose checkout holds the file.
    `kblam challenge new resources/mx-docs/vendor/mx-specs/MX-100/pinout.md --lines 1-1 --by
    reviewer-a`, followed by `kblam put`, exits 0 and changes
    research-review/challenges/source-challenge-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash and .kblam/review-receipts/source-challenge-0001.json (the staged file is created by
    `challenge new` and removed by `put`); source-challenge-0001 records that submodule's toplevel, commit and blob
    -- a submodule is a repository in its own right and its `.git` is a file, so the pin names it,
    not the parent (SPEC §5.2.2 Git pins). `kblam validate` exits 0 and changes nothing. Both
    repositories' whole snapshots -- HEAD, refs, index and every working byte -- are equal before and
    after every command: the submodule's own index is inside the parent's .git/modules, which the
    parent's snapshot leaves out, so it is compared on its own and a staged-only change there fails
    the test. The test skips, naming git's own refusal, where this platform's Git will not add a
    file-protocol submodule (Acceptance 1)."""
    specs = SourceRepo(kb.root, "resources/mx-specs").init()
    specs.commit("MX-100/pinout.md", "pin 1: VCC\n", "pinout")
    vendor = "vendor/mx-specs"
    added = subprocess.run(["git", "-C", str(source_repo.root), "-c", "protocol.file.allow=always",
                            "submodule", "add", specs.root.as_posix(), vendor],
                           capture_output=True, text=True, check=False)
    if added.returncode:
        pytest.skip(f"this git will not add a file-protocol submodule: {added.stderr.strip()}")
    source_repo.git("commit", "-q", "-m", "vendor the pinout")
    raw = f"{SOURCE_REPO}/{vendor}/MX-100/pinout.md"
    sub_repo = SourceRepo(kb.root, f"{SOURCE_REPO}/{vendor}")
    src_before, sub_before = source_repo.snapshot(), sub_repo.snapshot()

    sc, touched = changes(kb, lambda: m.challenge(kb, source_repo, 1, by="reviewer-a", path=raw))
    assert touched == {f"{CHALLENGES}/source-challenge-0001.yaml", f"{REVIEW}/INDEX.md", REGISTRY, TREE_HASH,
                       f"{RECEIPTS}/source-challenge-0001.json"}
    assert source_repo.snapshot() == src_before
    assert sub_repo.snapshot() == sub_before
    source = source_of(kb, sc)
    assert source["repo"] == f"{SOURCE_REPO}/{vendor}"
    assert source["commit"] == specs.head()
    assert source["blob"] == specs.blob("MX-100/pinout.md")

    run, touched = changes(kb, lambda: m.validate(kb))
    m.ok(run, "validate")
    assert "K13" not in run.out
    assert touched == set()
    assert source_repo.snapshot() == src_before
    assert sub_repo.snapshot() == sub_before


def test_a_source_in_a_linked_worktree_pins_to_the_linked_worktree(kb, source_repo):
    """Start: resources/mx-docs-alt is a linked worktree of the nested source repository (branch
    "alt"), whose checkout of notes/full-scan-trace.md is committed and clean. `kblam challenge new
    resources/mx-docs-alt/notes/full-scan-trace.md --lines 3-3 --by reviewer-a`, followed by `kblam
    put`, exits 0 and changes research-review/challenges/source-challenge-0001.yaml, research-review/INDEX.md,
    .kblam/review-ids, .kblam/tree.hash and .kblam/review-receipts/source-challenge-0001.json (the staged file is
    created by `challenge new` and removed by `put`); source-challenge-0001 records repo "resources/mx-docs-alt"
    with that worktree's HEAD commit and the trace's blob -- a linked worktree is a worktree in its
    own right, and its `.git` is a file (SPEC §5.2.2 Git pins). `kblam validate` exits 0 and changes
    nothing. The linked worktree's whole snapshot -- HEAD, refs, index and every working byte, which
    `m611_helpers.tree` leaves out of the KB tree -- and the parent repository's whole snapshot are
    each equal before and after every command above, so a stray working file left in
    resources/mx-docs-alt fails the test. The test skips, naming git's own refusal, where this
    platform's Git will not add a linked worktree (Acceptance 1)."""
    linked = kb.root / "resources" / "mx-docs-alt"
    added = subprocess.run(["git", "-C", str(source_repo.root), "worktree", "add", "-q", "-b", "alt",
                            str(linked)], capture_output=True, text=True, check=False)
    if added.returncode:
        pytest.skip(f"this git will not add a linked worktree: {added.stderr.strip()}")
    raw = f"resources/mx-docs-alt/{TRACE_PATH}"
    linked_repo = SourceRepo(kb.root, "resources/mx-docs-alt")
    src_before, linked_before = source_repo.snapshot(), linked_repo.snapshot()

    sc, touched = changes(kb, lambda: m.challenge(kb, source_repo, "3-3", by="reviewer-a", path=raw))
    assert touched == {f"{CHALLENGES}/source-challenge-0001.yaml", f"{REVIEW}/INDEX.md", REGISTRY, TREE_HASH,
                       f"{RECEIPTS}/source-challenge-0001.json"}
    assert source_repo.snapshot() == src_before
    assert linked_repo.snapshot() == linked_before
    source = source_of(kb, sc)
    assert source["repo"] == "resources/mx-docs-alt"
    assert source["commit"] == source_repo.head()
    assert source["blob"] == source_repo.blob(TRACE_PATH)

    run, touched = changes(kb, lambda: m.validate(kb))
    m.ok(run, "validate")
    assert "K13" not in run.out
    assert touched == set()
    assert source_repo.snapshot() == src_before
    assert linked_repo.snapshot() == linked_before


@pytest.fixture
def sha256_repo(kb, monkeypatch):
    """A source repository using SHA-256 object IDs; skipped where this git cannot make one."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(kb.root.parent / "no-global-gitconfig"))
    try:
        repo = SourceRepo(kb.root, "resources/sha256-docs").init("--object-format=sha256")
    except subprocess.CalledProcessError as exc:
        reason = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr)
        pytest.skip(f"this git cannot create a SHA-256 repository: {reason.strip()}")
    if repo.git("rev-parse", "--show-object-format", check=False) != "sha256":
        pytest.skip("this git ignored --object-format=sha256")
    repo.commit(TRACE_PATH, TRACE_TEXT, "trace")
    return repo


def test_a_sha256_repository_records_64_hex_object_ids(kb, sha256_repo):
    """Start: resources/sha256-docs is a repository whose object format is SHA-256, with the trace
    committed. `kblam challenge new
    resources/sha256-docs/notes/full-scan-trace.md --lines 3-3 --by reviewer-a`, followed by `kblam
    put`, exits 0 and changes research-review/challenges/source-challenge-0001.yaml, research-review/INDEX.md,
    .kblam/review-ids, .kblam/tree.hash and .kblam/review-receipts/source-challenge-0001.json (the staged file is
    created by `challenge new` and removed by `put`); source-challenge-0001 records that repository's toplevel, HEAD
    commit and blob as 64 lowercase hex digits -- the length `git rev-parse --show-object-format`
    implies (SPEC §5.2.2 Git pins). `kblam validate` exits 0 (the pin verifies), changes nothing, and
    the repository's whole snapshot is equal before and after both commands. The test skips, naming
    git's own refusal, where this platform's Git cannot make a SHA-256 repository (Acceptance 1)."""
    raw = sha256_repo.kb_path()
    src_before = sha256_repo.snapshot()

    sc, touched = changes(kb, lambda: m.challenge(kb, sha256_repo, "3-3", by="reviewer-a", path=raw))
    assert touched == {f"{CHALLENGES}/source-challenge-0001.yaml", f"{REVIEW}/INDEX.md", REGISTRY, TREE_HASH,
                       f"{RECEIPTS}/source-challenge-0001.json"}
    source = source_of(kb, sc)
    assert source["repo"] == "resources/sha256-docs"
    assert source["commit"] == sha256_repo.head()
    assert source["blob"] == sha256_repo.blob(TRACE_PATH)
    assert len(source["commit"]) == len(source["blob"]) == 64
    assert all(c in "0123456789abcdef" for c in source["commit"] + source["blob"])

    run, touched = changes(kb, lambda: m.validate(kb))
    m.ok(run, "validate")
    assert "K13" not in run.out
    assert touched == set()
    assert sha256_repo.snapshot() == src_before


# --- two versions of one path in one validation -----------------------------------------------------


def test_two_versions_of_one_path_are_checked_at_their_own_blobs_and_read_once(kb, source_repo,
                                                                              monkeypatch):
    """Start: source-challenge-0001 (reviewer-a created it at the first commit) quotes line 3 of the trace as it was
    then, and source-challenge-0002 (reviewer-a, at the second commit) quotes line 3 as the second commit reworded
    it; both are `challenge new` plus `kblam put` pairs, open and pinned to their own commit and
    blob, and each pair changes research-review/challenges/<ID>.yaml, research-review/INDEX.md,
    .kblam/review-ids, .kblam/tree.hash and .kblam/review-receipts/<ID>.json (its staged file is
    created by its `challenge new` and removed by its `put`), while the source repository's whole
    snapshot stays equal. A third commit then gives the working file other bytes again, so neither
    record is current. `kblam validate` exits 0: source-challenge-0001's assertion is found in the bytes of its own
    blob and source-challenge-0002's in its own, each at the exact pinned version (a resolver that mixed the two
    versions would report an occurrence error), and a counting wrapper shows the one source reader
    reads the working file once and each of the two blobs once (SPEC §5.2.6: two versions of one path
    never share an entry, and each distinct identity is read once per validation). `kblam challenge
    show` prints each record's own version and assertion text. Both commands change no file, and the
    source repository is unchanged (Acceptance 1, Acceptance 2)."""
    src_before = source_repo.snapshot()
    older, touched = changes(kb, lambda: m.challenge(kb, source_repo, "3-3", by="reviewer-a"))
    assert touched == {f"{CHALLENGES}/source-challenge-0001.yaml", f"{REVIEW}/INDEX.md", REGISTRY, TREE_HASH,
                       f"{RECEIPTS}/source-challenge-0001.json"}
    assert source_repo.snapshot() == src_before
    first_blob = source_repo.blob(TRACE_PATH)

    source_repo.commit(TRACE_PATH, V2_TEXT, "row 102 reworded")
    src_before = source_repo.snapshot()
    newer, touched = changes(kb, lambda: m.challenge(kb, source_repo, "3-3", by="reviewer-a"))
    assert touched == {f"{CHALLENGES}/source-challenge-0002.yaml", f"{REVIEW}/INDEX.md", REGISTRY, TREE_HASH,
                       f"{RECEIPTS}/source-challenge-0002.json"}
    assert source_repo.snapshot() == src_before
    second_blob = source_repo.blob(TRACE_PATH)

    source_repo.commit(TRACE_PATH, V3_TEXT, "row 104")
    src_before = source_repo.snapshot()

    assert source_of(kb, older)["blob"] == first_blob
    assert source_of(kb, newer)["blob"] == second_blob
    for rec_id, version, assertion in ((older, first_blob, m.LINE3), (newer, second_blob, V2_LINE3)):
        show, touched = changes(kb, lambda rec_id=rec_id: m.kblam(kb, "challenge", "show", rec_id))
        m.ok(show, "challenge show")
        assert f"version: {version}" in show.out
        assert "state: pinned" in show.out
        assert f"\n  {assertion}\n" in show.out
        assert touched == set()

    key = paths.canonical_key(kb.cfg, TRACE)
    blobs, workings = [], []
    real_blob, real_working = gitpin.read_blob, sources.SourceReader._working_bytes
    monkeypatch.setattr(gitpin, "read_blob",
                        lambda toplevel, oid: (blobs.append(oid), real_blob(toplevel, oid))[1])

    def counted_working(reader, canonical, target):
        if canonical == key:
            workings.append(canonical)
        return real_working(reader, canonical, target)

    monkeypatch.setattr(sources.SourceReader, "_working_bytes", counted_working)

    run, touched = changes(kb, lambda: m.validate(kb))
    m.ok(run, "validate")
    assert "K13" not in run.out
    assert sorted(blobs) == sorted([first_blob, second_blob])   # each version's bytes, read once
    assert workings == [key]                                   # the working file, read once
    assert touched == set()
    assert source_repo.snapshot() == src_before
