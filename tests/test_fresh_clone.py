"""SPEC §8 item 3, "A new clone": a clone has the committed findings but no .kblam/, so no tree.hash. The
Stop hook then runs only the deterministic rules and never asks Jev; `kblam validate --record`, or the
bootstrap of a put, ack or index, records the tree without asking Jev and marks every finding as accepted
from the repository, which `kblam audit` checks later. Real `git clone`s under tmp_path, isolated from
system and global git config; Jev is the fake transport from test_check, and nothing touches the network."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from kblam import cli
from kblam.finding import fingerprint
from kblam.jev import CACHE_NAME, PairCache
from kblam.treehash import read_recorded, tree_digest_v2
from kblam.view import load_view

from conftest import KB, finding_text
from test_check import E1, E2, N, jkb  # noqa: F401 (jkb is a fixture)
from test_hook import POINTER, blocked, call, hkb, no_project_dir, stop  # noqa: F401 (fixtures)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(root, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=kblam test", "-c", "user.email=test@example.invalid",
                    "-c", "commit.gpgsign=false", *args], cwd=root, capture_output=True, check=True)


@pytest.fixture
def origin(hkb, monkeypatch):
    """A repository whose committed KB holds two findings that Jev, if asked, would call one fact."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hkb.root.parent / "gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(hkb.root.parent))
    hkb.add("F-0001", "motor", E1)
    hkb.add("F-0002", "drift", N)
    hkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    hkb.write(".gitignore", ".kblam/\n")
    git(hkb.root, "init", "-q")
    git(hkb.root, "add", "-A")
    git(hkb.root, "commit", "-q", "--no-verify", "-m", "two findings")
    return hkb


def clone_of(origin) -> KB:
    target = origin.root.parent / "clone"
    git(origin.root.parent, "clone", "-q", str(origin.root), str(target))
    return KB(target)


@pytest.fixture
def clone(origin) -> KB:
    return clone_of(origin)


@pytest.fixture
def no_jev_check(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("the Stop hook ran the Jev check on a new clone")

    monkeypatch.setattr("kblam.review.check_findings", refuse)


def checked(kb, finding_id: str) -> bool:
    finding = next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)
    return PairCache(kb.cfg.state_dir / CACHE_NAME).was_checked(finding_id, fingerprint(finding, "/"))


def run(kb, capsys, *args: str) -> tuple[int, str]:
    capsys.readouterr()
    code = cli.main(["--root", str(kb.root), *args])
    out, err = capsys.readouterr()
    return code, out + err


def test_the_stop_hook_on_a_new_clone_validates_without_jev(origin, clone, monkeypatch, capsys, no_jev_check):
    assert not (clone.root / ".kblam").exists() and (clone.findings / "calibration" / "F-0001-motor.md").is_file()
    for event, extra in (("Stop", {}), ("SubagentStop", {"agent_id": "a1", "agent_type": "Explore"})):
        assert call(event, {**stop(clone, event=event), **extra}, monkeypatch, capsys) == (0, None, "")
    assert origin.fake.requests == []
    assert not (clone.root / ".kblam").exists()  # nothing recorded: no tree.hash, no checked marks


def test_validate_record_on_a_new_clone_accepts_the_findings_without_jev(origin, clone, monkeypatch, capsys):
    code, out = run(clone, capsys, "validate", "--record")
    assert code == 0, out
    assert "kblam validate: OK (2 findings); recorded .kblam/tree.hash for this tree" in out
    assert ("kblam validate: there was no .kblam/tree.hash (a new clone, or .kblam/ was deleted), so Jev was not "
            "asked: 2 finding(s) accepted from the repository as checked at their current fingerprints. kblam "
            "audit checks them with Jev") in out
    assert origin.fake.requests == []
    assert read_recorded(clone.cfg) == (2, clone.cfg.review_dir, tree_digest_v2(load_view(clone.cfg)))
    assert checked(clone, "F-0001") and checked(clone, "F-0002")

    code, out = run(clone, capsys, "check")  # later checks cover only what changes after the clone
    assert code == 0 and "every finding has been checked at its current fingerprint" in out
    assert call("Stop", stop(clone), monkeypatch, capsys) == (0, None, "")
    assert origin.fake.requests == []

    code, out = run(clone, capsys, "audit")  # and kblam audit checks the accepted findings
    assert code == 1 and "same_fact F-0002 vs F-0001" in out and origin.fake.requests


def test_a_second_validate_record_checks_what_changed_since(origin, clone, capsys):
    """Once tree.hash exists, --record checks with Jev again: only the first one is a baseline."""
    assert run(clone, capsys, "validate", "--record")[0] == 0
    clone.write("findings/tray/F-0003-tray.md", finding_text("F-0003", E2, topic="tray"))  # a git pull, say
    assert run(clone, capsys, "index")[0] == 0
    code, out = run(clone, capsys, "validate", "--record")
    assert code == 0 and "accepted from the repository" not in out
    assert [c for c in origin.fake.revision_claims()] == [E2]  # F-0003 alone was checked
    assert checked(clone, "F-0003")


def test_a_put_on_a_new_clone_records_the_baseline(origin, clone, capsys):
    staged = clone.write(".kblam/staging/F-0003-tray.md", finding_text("F-0003", E2, topic="tray"))
    code, out = run(clone, capsys, "put", str(staged))
    assert code == 0, out
    assert "changed outside kblam" not in out
    assert read_recorded(clone.cfg) == (2, clone.cfg.review_dir, tree_digest_v2(load_view(clone.cfg)))
    assert checked(clone, "F-0001") and checked(clone, "F-0002")  # accepted from the repository
    assert checked(clone, "F-0003")                                # checked by its own put
    assert (E1, N) not in origin.fake.relation_pairs() and (N, E1) not in origin.fake.relation_pairs()


def test_a_new_clone_of_a_failing_tree_is_blocked_and_recorded_only_once_clean(origin, monkeypatch, capsys,
                                                                               no_jev_check):
    """A tree committed with --no-verify can fail the rules: the clone's Stop hook blocks on them, and
    neither a bootstrap nor validate --record records it until it is clean."""
    origin.write("findings/calibration/notes.md", "scratch\n")  # K8
    git(origin.root, "add", "-A")
    git(origin.root, "commit", "-q", "--no-verify", "-m", "a stray file")
    clone = clone_of(origin)

    reason = blocked(call("Stop", stop(clone), monkeypatch, capsys)[1])
    assert "K8 findings/calibration/notes.md" in reason and reason.endswith(POINTER)
    code, out = run(clone, capsys, "index")
    assert code == 0 and "tree.hash not advanced" in out and read_recorded(clone.cfg) is None
    code, out = run(clone, capsys, "validate", "--record")
    assert code == 1 and "tree.hash not recorded" in out and read_recorded(clone.cfg) is None
    assert not checked(clone, "F-0001")

    (clone.findings / "calibration" / "notes.md").unlink()  # the fix K8 asks for (removing a stray file passes)
    code, out = run(clone, capsys, "validate", "--record")
    assert code == 0 and "2 finding(s) accepted from the repository" in out
    assert origin.fake.requests == []
