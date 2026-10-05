"""SPEC §8.3: `.kblam/` is this machine's own state, and a pull writes tracked files over ignored ones, so a
commit that holds files there would replace every clone's tree.hash, review items and cached answers. While
git tracks anything under `.kblam/`, every kblam command refuses and the Stop hook checks the tree as a new
clone's and blocks. Real git repositories under tmp_path; nothing here touches the network."""

from __future__ import annotations

import pytest

from kblam import cli
from kblam.gitdir import tracked_state
from kblam.treehash import read_recorded

from test_approval import commit, git, gkb  # noqa: F401  (gkb is a fixture)
from test_hook import blocked, call, stop

PROBLEM = "under .kblam/, which holds this machine's own state"


def run(kb, capsys, *args: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = cli.main(["--root", str(kb.root), *args])
    out, err = capsys.readouterr()
    return code, out, err


def track(kb, *paths: str) -> None:
    """Commit files under .kblam/ past .gitignore, as `git add -f` does."""
    git(kb, "add", "-f", *paths)
    git(kb, "commit", "-q", "--no-verify", "-m", "state")


def test_a_pull_replaces_an_ignored_file_with_a_tracked_one(gkb, tmp_path):
    """The premise, checked against the git in use: git treats an ignored file as expendable."""
    clone = tmp_path / "clone"
    git(gkb, "clone", "-q", str(gkb.root), str(clone))
    (clone / ".kblam").mkdir()
    (clone / ".kblam" / "tree.hash").write_text("this machine's\n", encoding="ascii")
    gkb.write(".kblam/tree.hash", "someone else's\n")
    track(gkb, ".kblam/tree.hash")
    git(gkb, "-C", str(clone), "pull", "-q", "--no-rebase", "origin", "HEAD")
    assert (clone / ".kblam" / "tree.hash").read_text(encoding="ascii") == "someone else's\n"


def test_every_command_refuses_while_git_tracks_state(gkb, capsys):
    track(gkb, ".kblam/tree.hash")
    for args in (["validate"], ["validate", "--record"], ["items"], ["index"], ["deps", "F-0001"], ["cost"]):
        code, out, err = run(gkb, capsys, *args)
        assert (code, out) == (1, ""), args
        assert f"kblam {args[0]}: git tracks .kblam/tree.hash, {PROBLEM}" in err
        assert "git rm -r --cached .kblam" in err and "delete .kblam/, then run kblam validate --record" in err
        assert err.rstrip().endswith("Load the kblam-write skill for how to fix this.")


def test_untracking_the_state_lets_kblam_act_again(gkb, capsys):
    gkb.write(".kblam/review.jsonl", "")
    track(gkb, ".kblam/tree.hash", ".kblam/review.jsonl")
    assert tracked_state(gkb.cfg) == [".kblam/review.jsonl", ".kblam/tree.hash"]
    git(gkb, "rm", "-q", "-r", "--cached", ".kblam")  # staged: the index no longer lists them
    assert tracked_state(gkb.cfg) == []
    assert run(gkb, capsys, "validate")[0] == 0


def test_a_commit_that_stages_state_is_refused(gkb, capsys):
    """The pre-commit hook runs validate --commit, which refuses while the index holds a .kblam/ file."""
    gkb.write(".kblam/checks.jsonl", "{}\n")
    git(gkb, "add", "-f", ".kblam/checks.jsonl")
    code, _, err = run(gkb, capsys, "validate", "--commit")
    assert code == 1 and f"git tracks .kblam/checks.jsonl, {PROBLEM}" in err


def test_a_tracked_link_named_kblam_counts(gkb, capsys, tmp_path):
    """A committed link at .kblam would carry every state write elsewhere; git lists the link itself."""
    import shutil
    import sys

    if sys.platform == "win32":
        pytest.skip("symbolic links need a privilege on Windows")
    shutil.rmtree(gkb.root / ".kblam")
    (gkb.root / ".kblam").symlink_to(tmp_path)
    track(gkb, ".kblam")
    assert tracked_state(gkb.cfg) == [".kblam"]
    assert run(gkb, capsys, "validate")[0] == 1


def test_the_stop_hook_blocks_on_tracked_state_and_ignores_its_tree_hash(gkb, monkeypatch, capsys):
    """A tree.hash that a commit brings matches the committed tree, so trusting it would keep the Stop hook
    quiet about findings nobody checked on this machine."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert read_recorded(gkb.cfg) is not None  # the fixture recorded the tree
    track(gkb, ".kblam/tree.hash")
    code, answer, _ = call("Stop", stop(gkb), monkeypatch, capsys)
    reason = blocked(answer)
    assert code == 0 and f"kblam: git tracks .kblam/tree.hash, {PROBLEM}" in reason
    assert "Until then the knowledge base is checked as on a new clone." in reason

    # the loop guard still releases an agent that cannot fix it
    code, answer, err = call("Stop", stop(gkb, active=True), monkeypatch, capsys)
    assert code == 0 and answer["systemMessage"].startswith("kblam hook Stop: git still tracks files under .kblam/")


def test_the_stop_hook_reports_rule_failures_too(gkb, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    gkb.write("findings/calibration/stray.txt", "not a finding\n")  # K8
    track(gkb, ".kblam/tree.hash")
    reason = blocked(call("Stop", stop(gkb), monkeypatch, capsys)[1])
    assert "checked as on a new clone, and it fails kblam validate:\n" in reason and "K8" in reason


def test_outside_git_nothing_is_tracked(kb, capsys):
    assert tracked_state(kb.cfg) == [] and run(kb, capsys, "validate")[0] == 0
