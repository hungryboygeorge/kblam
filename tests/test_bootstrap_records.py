"""SPEC §8 item 3 and §5.2.6 "Upgrade": a new clone, or a KB whose .kblam/ was deleted, holds review records
and no tree.hash. When the full deterministic validation, K13-K15 included, is clean, `kblam validate
--record`, the first write (a put of a finding or of a record) and `kblam init --update` record the format-2
tree.hash, create the registry from the records present, accept every finding from the repository and ask
Jev nothing. A failed bootstrap leaves tree.hash missing and says what to do; `kblam validate --record` then
creates no registry either. Jev is the fake transport from test_check, and nothing touches the network."""

from __future__ import annotations

import contextlib
import hashlib
import io
import shutil
import subprocess

import pytest

import m611_helpers as m
from kblam import init
from kblam.finding import fingerprint
from kblam.jev import CACHE_NAME, PairCache
from kblam.treehash import read_recorded, tree_digest_v2
from kblam.view import load_view

from conftest import KB
from test_check import E2, jkb  # noqa: F401 (jkb is a fixture, and hkb is built on it)
from test_fresh_clone import git
from test_hook import call, hkb, stop  # noqa: F401 (hkb is a fixture)
from test_m611_group4 import excerpt_line, line_with, same_bytes_message

REVIEW = "research-review"
FINDING = "findings/calibration/F-0001-ratio.md"      # F-0001 as `quoting_finding` installs it
MOTOR = "findings/motor/F-0002-motor.md"
CLAIM_B = "The pump motor reaches steady output after 90 seconds of warm-up at 4000 rpm."
QUESTION = "Does an independent measurement establish the claim?"
PENDING = f"claim-task-0001 open replication of F-0002: {QUESTION}"
BASELINE = ("kblam validate: there was no .kblam/tree.hash (a new clone, or .kblam/ was deleted), so Jev was not "
            "asked: 2 finding(s) accepted from the repository as checked at their current fingerprints. kblam "
            "audit checks them with Jev")
MISSING = ("there is no .kblam/tree.hash (a new clone, or .kblam/ was deleted), and the tree as it was before this "
           "write fails kblam validate, so kblam did not record tree.hash for it; the write itself is done. Run "
           "kblam validate, fix anything it lists, then run kblam validate --record.")


def unrecorded(command: str) -> str:
    """The whole stderr of `kblam rm`, `renumber` or `upgrade` with review records and no tree.hash: none
    of them writes the registry, so none of them bootstraps."""
    return (f"kblam {command}: there is no .kblam/tree.hash (a new clone, or .kblam/ was deleted), and kblam "
            f"{command} does not record one while {REVIEW}/ holds review records; tree.hash not advanced. Run "
            f"kblam validate, fix anything it lists, then run kblam validate --record.\n")


@pytest.fixture
def no_jev_check(monkeypatch):
    """The Jev check of the whole KB (`kblam check`, `validate --record` with a tree.hash) must not run."""
    def refuse(*args, **kwargs):
        pytest.fail("the Jev check of the knowledge base ran on a bootstrap")

    monkeypatch.setattr("kblam.review.check_findings", refuse)


def reviewed(kb, source_repo, *, use: bool = True) -> None:
    """F-0001 quotes the trace's line 3, which source-challenge-0001 (confirmed) challenges, and checked-use-0001 (approved)
    covers that excerpt; F-0002 is a plain finding with claim-task-0001, an open task, on it. Without `use`, the
    excerpt is left uncovered: a K14 error."""
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    sc = m.confirmed_challenge(kb, source_repo, "3-3", by="reviewer-a", decider="reviewer-b")
    if use:
        m.approved_use(kb, sc, "F-0001", proponent="researcher-a", reviewer="reviewer-b")
    m.open_task(kb, "F-0002", by="researcher-a", proponent="researcher-a")


def clone(kb) -> None:
    """The state a fresh clone leaves: no .kblam/ at all (no tree.hash, registry or Jev state)."""
    shutil.rmtree(kb.root / ".kblam")
    kb.fake.requests.clear()


def recorded(kb) -> bool:
    """Whether tree.hash is the format-2 line of the tree as it is now."""
    return read_recorded(kb.cfg) == (2, REVIEW, tree_digest_v2(load_view(kb.cfg)))


def accepted(kb, *finding_ids: str) -> bool:
    """Whether each finding is marked checked at its current fingerprint."""
    view = load_view(kb.cfg)
    cache = PairCache(kb.cfg.state_dir / CACHE_NAME)
    return all(cache.was_checked(f.file_id, fingerprint(f, kb.cfg.scope_separator))
               for f in view.findings if f.file_id in finding_ids)


def present(kb) -> list[str]:
    """The IDs of the records in the review root, sorted."""
    return sorted(r.id for r in load_view(kb.cfg).records)


def bootstrapped(kb, *, registry: list[str]) -> None:
    """tree.hash is format 2 for the tree as it is, the registry is exactly `registry` (the records
    present), and both findings are accepted from the repository."""
    assert recorded(kb)
    assert m.registry(kb) == registry == present(kb)
    assert accepted(kb, "F-0001", "F-0002")


def unbootstrapped(kb) -> bool:
    """Whether the KB still has no tree.hash."""
    return not (kb.cfg.state_dir / "tree.hash").exists()


def bootstraps_as_named(kb, pending: str, findings: int) -> None:
    """D49 for the "Run kblam validate, fix anything it lists, then run kblam validate --record" of a
    tree that is clean now: validate passes, and validate --record records tree.hash, creates the
    registry from the records present and accepts every finding from the repository."""
    status = f"kblam validate: OK ({findings} findings); 1 pending task(s)"
    assert m.validate(kb) == m.Run(0, f"{pending}\n{status}\n", "")
    baseline = BASELINE.replace("2 finding(s)", f"{findings} finding(s)")
    assert m.validate(kb, "--record") == m.Run(
        0, f"{pending}\n{status}; recorded .kblam/tree.hash for this tree\n{baseline}\n", "")
    assert recorded(kb)
    assert m.registry(kb) == present(kb)
    assert accepted(kb, *(f.file_id for f in load_view(kb.cfg).findings))


# --- clean: every path bootstraps, asking Jev nothing ---------------------------------------------


def test_validate_record_bootstraps_a_clone_with_records(jkb, source_repo, no_jev_check):
    reviewed(jkb, source_repo)
    clone(jkb)

    run = m.validate(jkb, "--record")

    assert run == m.Run(0, f"{PENDING}\nkblam validate: OK (2 findings); 1 pending task(s); recorded "
                           f".kblam/tree.hash for this tree\n{BASELINE}\n", "")
    bootstrapped(jkb, registry=["checked-use-0001", "claim-task-0001", "source-challenge-0001"])
    assert jkb.fake.requests == []
    assert m.validate(jkb) == m.Run(0, f"{PENDING}\nkblam validate: OK (2 findings); 1 pending task(s)\n", "")


def test_the_put_of_a_finding_bootstraps_a_clone_with_records(jkb, source_repo, no_jev_check):
    """The put checks its own finding with Jev, as every put does; the bootstrap asks about no other."""
    reviewed(jkb, source_repo)
    clone(jkb)
    staged = m.stage_finding(jkb, None, m.finding_text("F-0003", E2, topic="tray"), slug="tray")

    run = m.put(jkb, staged)

    assert run.code == 0 and run.err == "", run.out + run.err
    assert run.out.startswith("kblam put: F-0003 -> findings/tray/F-0003-tray.md\n"), run.out
    bootstrapped(jkb, registry=["checked-use-0001", "claim-task-0001", "source-challenge-0001"])
    assert accepted(jkb, "F-0003")                                   # checked by its own put
    assert jkb.fake.requests and all(b["state"]["new"]["claim"] == E2 for b in jkb.fake.requests)


def test_the_put_of_a_record_bootstraps_a_clone_with_records(jkb, source_repo, no_jev_check):
    reviewed(jkb, source_repo)
    clone(jkb)
    staged = m.stage_task(jkb, "F-0001", by="researcher-a", proponent="researcher-a")

    run = m.put(jkb, staged)

    assert run == m.Run(0, f"kblam put: {staged.id} -> {REVIEW}/tasks/{staged.id}.yaml\n", "")
    bootstrapped(jkb, registry=["checked-use-0001", "claim-task-0001", "claim-task-0002", "source-challenge-0001"])
    assert staged.id == "claim-task-0002"
    assert jkb.fake.requests == []


@pytest.fixture
def git_kb(jkb, monkeypatch):
    """jkb as a git repository, run from its root as `kblam init` must be, with the hook check stubbed
    (it has its own tests) and system and global git config ignored."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(jkb.root.parent / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(jkb.root.parent))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    subprocess.run(["git", "init", "-q", str(jkb.root)], check=True, capture_output=True)
    monkeypatch.chdir(jkb.root)
    monkeypatch.setattr(init.Init, "check_hooks", lambda self, installed, findings_dir: None)
    return jkb


def init_update() -> m.Run:
    """`kblam init --update` (init takes no --root: it runs where it is started)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = m.main(["init", "--update"])
    return m.Run(code, out.getvalue(), err.getvalue())


def test_init_update_bootstraps_a_clone_with_records(git_kb, source_repo, no_jev_check):
    """init's review-index step creates the registry from the records present, and its tree.hash step
    then bootstraps."""
    reviewed(git_kb, source_repo)
    clone(git_kb)

    run = init_update()

    assert run.code == 0 and run.err == "", run.out + run.err
    assert run.out.endswith("  created   .kblam/tree.hash\nkblam init: done. Review the files above and commit "
                            "them.\n"), run.out
    bootstrapped(git_kb, registry=["checked-use-0001", "claim-task-0001", "source-challenge-0001"])
    assert git_kb.fake.requests == []


# --- dirty: nothing is recorded, and doing what the message says bootstraps -------------------------
# A dirty put of a finding, and the index write and init --update on a dirty tree, have their own tests:
# test_put_review.py test_a_missing_tree_hash_with_a_failing_record_leaves_a_put_unrecorded,
# test_init_review.py test_a_missing_tree_hash_with_a_failing_record_is_kept, and test_treehash.py
# test_bootstrap_refuses_a_tree_that_fails_the_rules and
# test_a_missing_tree_hash_with_a_failing_record_is_not_bootstrapped.


def sha(kb, path: str) -> str:
    return hashlib.sha256((kb.root / path).read_bytes()).hexdigest()


def index_deleted(kb, source_repo) -> str:
    """reviewed(), then the review root's INDEX.md deleted: K13. Returns the error validate prints."""
    reviewed(kb, source_repo)
    (kb.root / REVIEW / "INDEX.md").unlink()
    return f"K13 {REVIEW}/INDEX.md: INDEX.md is missing; run kblam review index"


def use_missing(kb, source_repo) -> str:
    """reviewed() without checked-use-0001, so F-0001's excerpt of the challenged line is uncovered: K14."""
    reviewed(kb, source_repo, use=False)
    return f"K14 {FINDING}:{excerpt_line(kb.root / FINDING)}: {same_bytes_message(source_repo)}"


def task_stale(kb, source_repo) -> str:
    """reviewed(), then F-0002's body extended outside kblam after claim-task-0001 bound its bytes: K15."""
    reviewed(kb, source_repo)
    bound = sha(kb, MOTOR)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", body="A first detail.")
    record = m.record_path(kb, "claim-task-0001")
    return (f"K15 {REVIEW}/tasks/claim-task-0001.yaml:{line_with(record, bound)}: F-0002's file now hashes to "
            f"{sha(kb, MOTOR)}, not the {bound} claim-task-0001 was bound to (the binding covers the whole file, not "
            f"only the fingerprint); reread it, then run kblam review rebind claim-task-0001 --by NAME --reason TEXT "
            f"--expect D")


@pytest.mark.parametrize("dirty, pending", [(index_deleted, True), (use_missing, True), (task_stale, False)],
                         ids=["K13", "K14", "K15"])
def test_validate_record_records_nothing_on_a_clone_that_fails(jkb, source_repo, no_jev_check, dirty,
                                                                pending):
    """No tree.hash and no registry: a failed validate --record writes nothing. A stale task (K15) is
    not listed as pending. The count names where the error is: a record's (K13, K15) or a finding's (K14)."""
    error = dirty(jkb, source_repo)
    clone(jkb)
    root = "findings" if dirty is use_missing else REVIEW

    run = m.validate(jkb, "--record")

    assert run == m.Run(1, f"{error}\n" + (f"{PENDING}\n" if pending else "") +
                        f"kblam validate: 1 error(s) in {root}/; tree.hash not recorded\n", "")
    assert unbootstrapped(jkb)
    assert m.registry(jkb) is None
    assert jkb.fake.requests == []


def test_the_put_of_a_record_on_a_clone_that_fails_is_not_recorded(jkb, source_repo, no_jev_check):
    """The put goes in and creates the registry, from the records present and its own; tree.hash stays
    missing and the put says what to do."""
    error = task_stale(jkb, source_repo)
    clone(jkb)
    staged = m.stage_task(jkb, "F-0001", by="researcher-a", proponent="researcher-a")

    run = m.put(jkb, staged)

    assert run == m.Run(0, f"kblam put: claim-task-0002 -> {REVIEW}/tasks/claim-task-0002.yaml\n{error}\nkblam put: done, but "
                           f"kblam validate still fails (1 error(s) listed above, owned by other findings or "
                           f"records)\n", f"kblam put claim-task-0002: {MISSING}\n")
    assert unbootstrapped(jkb)
    assert m.registry(jkb) == ["checked-use-0001", "claim-task-0001", "claim-task-0002", "source-challenge-0001"] == present(jkb)
    assert jkb.fake.requests == []


def test_a_put_on_a_clone_that_fails_says_what_to_do_and_doing_it_bootstraps(jkb, source_repo,
                                                                              no_jev_check):
    """D49: the put's message names kblam validate; validate lists the K15 error, whose rebind command
    runs as printed (it is a write on a tree that still fails, so it records nothing either); then
    validate passes and validate --record bootstraps."""
    error = task_stale(jkb, source_repo)
    clone(jkb)
    staged = m.stage_finding(jkb, None, m.finding_text("F-0003", E2, topic="tray"), slug="tray")

    run = m.put(jkb, staged)

    assert run == m.Run(0, f"kblam put: F-0003 -> findings/tray/F-0003-tray.md\n{error}\nkblam put: done, but "
                           f"kblam validate still fails (1 error(s) listed above that this put did not "
                           f"refuse)\n", f"kblam put F-0003-tray.md: {MISSING}\n")
    assert unbootstrapped(jkb)
    assert m.validate(jkb) == m.Run(1, f"{error}\nkblam validate: 1 error(s) in {REVIEW}/\n", "")

    rebound = m.rebind(jkb, "claim-task-0001", by="reviewer-b")

    assert rebound == m.Run(0, f"kblam review rebind: claim-task-0001 rebound, now open (subject digest "
                               f"{m.expect(jkb, 'claim-task-0001')[:12]})\n", f"kblam review rebind claim-task-0001: {MISSING}\n")
    assert unbootstrapped(jkb)
    bootstraps_as_named(jkb, PENDING, 3)
    bootstrapped(jkb, registry=["checked-use-0001", "claim-task-0001", "source-challenge-0001"])
    assert accepted(jkb, "F-0003")


# --- a real git clone ------------------------------------------------------------------------------


LOG = "evidence/2026-09-22-ratio/log.txt"       # a plain file the KB's own repository commits


class CommittedLog:
    """Stands in for conftest's SourceRepo where m611_helpers reads a source: the evidence log the outer
    repository commits, so a clone has it (a nested source repository would not be cloned)."""

    def __init__(self, kb):
        self.kb_root = kb.root

    def kb_path(self, path: str = LOG) -> str:
        return path


@pytest.fixture
def origin(hkb, monkeypatch):
    """reviewed() on the evidence log, committed with .kblam/ ignored as `kblam init` leaves it; system and
    global git config are ignored. `hkb` puts the fake Jev behind the Stop hook's own check too, so a Jev
    call from the hook would be recorded rather than reach the network."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hkb.root.parent / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(hkb.root.parent))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    hkb.write(".gitignore", ".kblam/\n")
    git(hkb.root, "init", "-q")
    git(hkb.root, "add", "-A")
    git(hkb.root, "commit", "-q", "--no-verify", "-m", "evidence")
    reviewed(hkb, CommittedLog(hkb))
    git(hkb.root, "add", "-A")
    git(hkb.root, "commit", "-q", "--no-verify", "-m", "two findings and their review records")
    hkb.fake.requests.clear()
    return hkb


def clone_of(origin, name: str) -> KB:
    git(origin.root.parent, "clone", "-q", str(origin.root), name)
    return KB(origin.root.parent / name)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_git_clone_with_review_records_bootstraps(origin, monkeypatch, capsys, no_jev_check):
    """A clone has the findings and records but no .kblam/: the Stop hook runs only the deterministic
    rules, which pass, and records nothing. validate --record bootstraps one clone and the put of a task
    another; afterwards validate passes and the Stop hook is silent. Jev is never asked about F-0001 or
    F-0002."""
    first = clone_of(origin, "clone")
    assert (first.root / LOG).is_file() and not (first.root / ".kblam").exists()
    assert present(first) == ["checked-use-0001", "claim-task-0001", "source-challenge-0001"]

    assert call("Stop", stop(first), monkeypatch, capsys) == (0, None, "")
    assert not (first.root / ".kblam").exists()
    assert m.validate(first, "--record") == m.Run(0, f"{PENDING}\nkblam validate: OK (2 findings); 1 pending "
                                                     f"task(s); recorded .kblam/tree.hash for this tree\n"
                                                     f"{BASELINE}\n", "")
    bootstrapped(first, registry=["checked-use-0001", "claim-task-0001", "source-challenge-0001"])
    assert m.validate(first) == m.Run(0, f"{PENDING}\nkblam validate: OK (2 findings); 1 pending task(s)\n", "")
    assert call("Stop", stop(first), monkeypatch, capsys) == (0, None, "")

    second = clone_of(origin, "second")
    staged = m.stage_task(second, "F-0001", by="researcher-a", proponent="researcher-a")
    assert m.put(second, staged) == m.Run(0, f"kblam put: claim-task-0002 -> {REVIEW}/tasks/claim-task-0002.yaml\n", "")
    bootstrapped(second, registry=["checked-use-0001", "claim-task-0001", "claim-task-0002", "source-challenge-0001"])
    pending = f"{PENDING}\nclaim-task-0002 open replication of F-0001: {QUESTION}"
    assert m.validate(second) == m.Run(0, f"{pending}\nkblam validate: OK (2 findings); 2 pending task(s)\n", "")
    assert call("Stop", stop(second), monkeypatch, capsys) == (0, None, "")
    assert origin.fake.requests == []
