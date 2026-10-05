"""M6.10 `kblam ack` showing the target as the dependent recorded it (SPEC §7 ack): the version in git history
with the recorded fingerprint, beside the current claim. git never makes the ack fail. The git tests build a
repository under tmp_path and are skipped when git is not installed."""

from __future__ import annotations

import pytest

from kblam.cli import main
from kblam.store import ack, edit_finding, put
from kblam.treehash import read_recorded, tree_digest_v2
from kblam.view import load_view

from conftest import finding_text
from test_rm_renumber import commit_all, fp_of, git, needs_git, no_outer_git  # noqa: F401 (no_outer_git: fixture)

OLD = "The two sensor curve types agree to about 0.1%, so they are not two analog gains."
NEW = "The two sensor curve types agree to within 0.2%, so they are not two analog gains."
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."


def run(kb, *args) -> int:
    return main(["--root", str(kb.root), *args])


def with_dependent(kb):
    """F-0001, and F-0002 stamped against it by put."""
    kb.add("F-0001", "sensor", OLD)
    staged = kb.write(".kblam/staging/F-0002-motor.md",
                      finding_text("F-0002", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: null\n"))
    assert put(kb.cfg, staged).ok


def rewrite(kb, old: str, new: str, *, rename: str | None = None) -> None:
    staged = edit_finding(kb.cfg, "F-0001")
    text = staged.read_text(encoding="utf-8").replace(old, new)
    if rename:
        staged.unlink()
        staged = staged.with_name(rename)
        text = text.replace("topic: calibration", "topic: optics")
    staged.write_text(text, encoding="utf-8", newline="\n")
    result = put(kb.cfg, staged)
    assert result.ok and result.suspect == ["F-0002"], [i.format(result.view) for i in result.issues]


@needs_git
def test_ack_prints_the_recorded_version_beside_the_current_one(kb, capsys):
    git(kb, "init", "-q")
    with_dependent(kb)
    recorded = commit_all(kb, "F-0001 and its dependent")
    rewrite(kb, "about 0.1%", "within 0.2%")
    commit_all(kb, "rewrite F-0001")
    capsys.readouterr()

    assert run(kb, "ack", "F-0002", "F-0001") == 0
    out = capsys.readouterr().out.splitlines()
    short = git(kb, "rev-parse", "--short", recorded)
    assert out[:2] == [f"F-0001 as recorded (commit {short}): {OLD}", f"F-0001 now: {NEW}"]
    assert out[2].startswith("kblam ack: F-0002 depends_on F-0001 set to ")
    assert kb.issues() == [] and read_recorded(kb.cfg) == (2, kb.cfg.review_dir, tree_digest_v2(load_view(kb.cfg)))


@needs_git
def test_ack_finds_the_recorded_version_under_an_earlier_path(kb):
    """A topic or slug change is a new path, and the rewrite may leave git no similarity to follow."""
    git(kb, "init", "-q")
    with_dependent(kb)
    recorded = commit_all(kb, "F-0001 and its dependent")
    rewrite(kb, OLD, "Lens flare falls off with the square of the aperture number, which the test chart shows.",
            rename="F-0001-flare.md")
    assert not (kb.findings / "calibration" / "F-0001-sensor.md").exists()
    commit_all(kb, "rewrite and move F-0001")

    result = ack(kb.cfg, "F-0002", "F-0001")
    assert (result.claim_then, result.then_commit) == (OLD, git(kb, "rev-parse", "--short", recorded))
    assert result.claim_now.startswith("Lens flare") and result.history_note is None


@needs_git
def test_ack_without_the_recorded_version_in_history_says_to_reread_and_acks(kb, capsys):
    git(kb, "init", "-q")
    with_dependent(kb)
    rewrite(kb, "about 0.1%", "within 0.2%")  # the version F-0002 recorded was never committed
    commit_all(kb, "F-0001 as rewritten")
    capsys.readouterr()

    assert run(kb, "ack", "F-0002", "F-0001") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("kblam ack: no version of F-0001 in git history has the fingerprint F-0002 recorded (")
    assert out[0].endswith("), so kblam cannot show what changed; re-read F-0001 in full")
    assert out[1] == f"F-0001 now: {NEW}"
    assert kb.issues() == []


@pytest.mark.parametrize("git_missing", [False, True])
def test_ack_outside_a_repository_or_without_git_notes_it_and_acks(kb, capsys, monkeypatch, tmp_path, git_missing):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(kb.root.parent))  # kb.root is not in a repository
    if git_missing:
        (tmp_path / "empty").mkdir()
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with_dependent(kb)
    rewrite(kb, "about 0.1%", "within 0.2%")
    capsys.readouterr()

    assert run(kb, "ack", "F-0002", "F-0001") == 0
    note, now, done = capsys.readouterr().out.splitlines()
    assert note.startswith("kblam ack: could not look in git history for F-0001 as F-0002 recorded it (")
    assert ("cannot run git" if git_missing else "not a git repository") in note
    assert note.endswith("); re-read F-0001 in full")
    assert now == f"F-0001 now: {NEW}"
    assert done.startswith("kblam ack: F-0002 depends_on F-0001 set to ")
    assert kb.issues() == []


def test_ack_of_an_unstamped_dependency_has_no_earlier_version(kb, capsys):
    kb.add("F-0001", "sensor", OLD)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: null\n")
    capsys.readouterr()
    assert run(kb, "ack", "F-0002", "F-0001") == 0
    assert capsys.readouterr().out.splitlines()[:2] == [
        "kblam ack: F-0002 recorded no fingerprint for F-0001 (null), so there is no earlier version of F-0001 "
        "to show; re-read F-0001 in full",
        f"F-0001 now: {OLD}"]


def test_ack_of_a_current_dependency_shows_no_versions(kb, capsys):
    with_dependent(kb)
    capsys.readouterr()
    assert run(kb, "ack", "F-0002", "F-0001") == 0
    assert capsys.readouterr().out == (f"kblam ack: F-0002 depends_on F-0001 is already current "
                                       f"({fp_of(kb.findings / 'calibration' / 'F-0001-sensor.md')})\n")


@needs_git
def test_ack_finds_the_version_an_old_format_stamp_recorded(kb, capsys):
    """A stamp from before fingerprint v2 (SPEC §5.1) still names the version it was checked against."""
    from kblam.finding import fingerprint_v1
    from kblam.view import load_view

    git(kb, "init", "-q")
    kb.add("F-0001", "sensor", OLD)
    old = fingerprint_v1(next(f for f in load_view(kb.cfg).findings if f.file_id == "F-0001"))
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=f"depends_on:\n  F-0001: '{old}'\n")
    recorded = commit_all(kb, "F-0001 and a dependent stamped by the kblam before M6.10")
    rewrite(kb, "about 0.1%", "within 0.2%")
    commit_all(kb, "rewrite F-0001")
    capsys.readouterr()

    assert run(kb, "ack", "F-0002", "F-0001") == 0
    out = capsys.readouterr().out.splitlines()
    short = git(kb, "rev-parse", "--short", recorded)
    assert out[:2] == [f"F-0001 as recorded (commit {short}): {OLD}", f"F-0001 now: {NEW}"]
    assert kb.issues() == []  # the old stamp is gone: ack wrote a v2 one
