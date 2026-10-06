"""SPEC §8 item 4, "What is validated": `kblam validate --commit` checks the commit being made. Files
under the KB root must be staged as they are on disk, open items block only commits to the KB, committed
evidence is immutable, untracked evidence gets a warning, and the staged kblam.resolutions.jsonl must
parse. Real git repositories under tmp_path, isolated from system and global git config (the gkb fixture
of test_approval); nothing here touches the network."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from kblam import approval
from kblam.finding import fingerprint
from kblam.review import ReviewItem, save_items
from kblam.view import load_view

from conftest import finding_text
from test_approval import commit, git, gkb, run  # noqa: F401 (gkb is a fixture)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

CLAIM_A = "The motor reaches steady output after 90 seconds of warm-up."
CLAIM_B = "The media tray reports its type through two contact pins read at load time."
RESOLUTION = {"sides": [["F-0001", "0123456789ab"], ["F-0002", "ba9876543210"]], "kind": "distinct",
              "reason": "one is the MX-100 figure", "date": "2026-09-26"}


def validate_commit(kb, capsys) -> tuple[int, str]:
    code, out, err = run(kb, capsys, "validate", "--commit")
    return code, out + err


def put(kb, capsys, finding_id: str, slug: str, claim: str, **kw) -> None:
    """`kblam put` of a new finding, as an agent does it."""
    staged = kb.write(f".kblam/staging/{finding_id}-{slug}.md", finding_text(finding_id, claim, **kw))
    code, out, err = run(kb, capsys, "put", str(staged))
    assert code == 0, out + err


def open_unchecked_item(kb, finding_id: str) -> ReviewItem:
    """An open unchecked item on the finding at its current fingerprint, as a Jev outage leaves one."""
    finding = next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)
    item = ReviewItem("U-0000abcd", "unchecked", "open", finding_id, fingerprint(finding, "/"),
                      message="Jev could not answer 1 question(s) for this finding (test)", created="2026-09-26")
    save_items(kb.cfg, [item])
    return item


def resolutions(*lines: str) -> str:
    return "".join(f"{line}\n" for line in lines)


# --- the committed tree -----------------------------------------------------------------------------


def test_a_commit_that_holds_the_kb_as_it_is_on_disk_passes(gkb, capsys):
    put(gkb, capsys, "F-0001", "motor", CLAIM_A)
    git(gkb, "add", "-A")
    code, out = validate_commit(gkb, capsys)
    assert code == 0 and "kblam validate: OK (1 findings)" in out and "warning" not in out


def test_unstaged_or_untracked_files_under_the_kb_root_refuse_the_commit(gkb, capsys):
    put(gkb, capsys, "F-0001", "motor", CLAIM_A)
    git(gkb, "add", "findings/INDEX.md")  # the index without the finding it lists: part of a put
    code, out = validate_commit(gkb, capsys)
    assert code == 1
    assert ("kblam validate: this commit does not hold the files under findings/ as they are on disk: "
            "findings/calibration/F-0001-motor.md (untracked). kblam validates the files on disk, so the commit "
            "must hold them as they are: stage them (git add findings/) or discard those changes, then commit "
            "again") in out
    assert "1 problem(s) with the commit being made" in out

    git(gkb, "add", "-A")
    assert validate_commit(gkb, capsys)[0] == 0
    commit(gkb)
    path = gkb.findings / "calibration" / "F-0001-motor.md"
    path.write_text(path.read_text(encoding="utf-8").replace("2026-09-22", "2026-09-23"), encoding="utf-8")
    code, out = validate_commit(gkb, capsys)
    assert code == 1 and "findings/calibration/F-0001-motor.md (modified, not staged)" in out


def test_a_staged_rename_is_read_as_one_entry(gkb, capsys):
    """`git status -z` gives a rename two paths; the entry after it is read as its own."""
    gkb.write("findings/calibration/notes.md", "scratch\n")
    commit(gkb, "a stray file (K8), committed with --no-verify")
    git(gkb, "mv", "findings/calibration/notes.md", "findings/calibration/notes-2.md")
    gkb.write("findings/calibration/notes-2.md", "scratch, edited after the rename\n")
    gkb.write("findings/calibration/other.md", "untracked\n")
    code, out = validate_commit(gkb, capsys)
    assert code == 1
    assert ("as they are on disk: findings/calibration/notes-2.md (modified, not staged), "
            "findings/calibration/other.md (untracked). kblam validates") in out


def test_changes_outside_the_kb_root_may_stay_unstaged(gkb, capsys):
    gkb.write("notes/plan.md", "untracked scratch\n")
    gkb.write("evidence/2026-09-22-ratio/new.txt", "an evidence file not yet added\n")
    assert validate_commit(gkb, capsys)[0] == 0


# --- open items ---------------------------------------------------------------------------------------


def test_open_items_block_only_a_commit_that_changes_the_kb(gkb, capsys):
    put(gkb, capsys, "F-0001", "motor", CLAIM_A)
    commit(gkb, "F-0001")
    item = open_unchecked_item(gkb, "F-0001")

    gkb.write("src/tool.py", "print('a code-only change')\n")
    git(gkb, "add", "src/tool.py")
    code, out = validate_commit(gkb, capsys)
    assert code == 0 and item.describe() in out
    assert ("kblam validate: note: this commit changes nothing under findings/ or kblam.resolutions.jsonl, so the "
            "1 open item(s) above do not block it") in out
    assert run(gkb, capsys, "validate")[0] == 1  # the item still fails a plain validate and the Stop hook
    commit(gkb, "code")

    put(gkb, capsys, "F-0002", "tray", CLAIM_B, topic="tray")
    git(gkb, "add", "-A")
    code, out = validate_commit(gkb, capsys)
    assert code == 1 and "1 open item(s) in .kblam/review.jsonl" in out and "note:" not in out
    commit(gkb, "F-0002")


def test_open_items_block_a_commit_that_changes_the_resolutions(gkb, capsys):
    put(gkb, capsys, "F-0001", "motor", CLAIM_A)
    commit(gkb, "F-0001")
    open_unchecked_item(gkb, "F-0001")
    gkb.write("kblam.resolutions.jsonl", resolutions(json.dumps(RESOLUTION)))
    git(gkb, "add", "kblam.resolutions.jsonl")
    code, out = validate_commit(gkb, capsys)
    assert code == 1 and "1 open item(s) in .kblam/review.jsonl" in out


# --- immutable evidence -------------------------------------------------------------------------------


EVIDENCE = "evidence/2026-09-22-ratio"


@pytest.mark.parametrize("change, shown", [
    (lambda kb: kb.write(f"{EVIDENCE}/log.txt", "rewritten\n"), f"{EVIDENCE}/log.txt (modified)"),
    (lambda kb: git(kb, "rm", "-q", f"{EVIDENCE}/dump.bin"), f"{EVIDENCE}/dump.bin (deleted)"),
    (lambda kb: git(kb, "mv", f"{EVIDENCE}/README.md", f"{EVIDENCE}/MANIFEST.md"),
     f"{EVIDENCE}/README.md (moved to {EVIDENCE}/MANIFEST.md)"),
    (lambda kb: git(kb, "mv", f"{EVIDENCE}/log.txt", "notes/log.txt"), f"{EVIDENCE}/log.txt (moved to notes/log.txt)"),
], ids=["modified", "deleted", "renamed", "moved-out"])
def test_a_commit_may_not_change_committed_evidence(gkb, capsys, change, shown):
    (gkb.root / "notes").mkdir()
    change(gkb)
    git(gkb, "add", "-A")
    code, out = validate_commit(gkb, capsys)
    assert code == 1
    assert f"kblam validate: this commit changes evidence the last commit holds: {shown}. Evidence is immutable (P3)" \
           in out
    assert "add a new evidence package instead" in out and "git commit --no-verify" in out


@pytest.mark.skipif(sys.platform == "win32", reason="needs a symlink")
def test_changing_the_type_of_committed_evidence_is_refused(gkb, capsys):
    target = gkb.root / EVIDENCE / "log.txt"
    target.unlink()
    target.symlink_to("README.md")
    git(gkb, "add", "-A")
    code, out = validate_commit(gkb, capsys)
    assert code == 1 and f"{EVIDENCE}/log.txt (type changed)" in out


def test_history_dirs_are_immutable_too(gkb, capsys):
    gkb.write("history/calibration-notes.md", "A20: the ratio is 1.0017.\n")
    commit(gkb, "retire a document")
    gkb.write("history/calibration-notes.md", "A20: the ratio is 1.002.\n")
    git(gkb, "add", "-A")
    code, out = validate_commit(gkb, capsys)
    assert code == 1 and "history/calibration-notes.md (modified)" in out


def test_adding_evidence_passes(gkb, capsys):
    gkb.write(f"{EVIDENCE}-2/README.md", "a new package\n")
    gkb.write(f"{EVIDENCE}/extra.txt", "a new file in a committed package\n")
    gkb.write("notes/capture.csv", "1,2\n")
    commit(gkb, "notes")
    git(gkb, "mv", "notes/capture.csv", f"{EVIDENCE}-2/capture.csv")  # moved in from outside the folder
    git(gkb, "add", "-A")
    code, out = validate_commit(gkb, capsys)
    assert code == 0, out


def test_evidence_folders_follow_the_configuration(gkb, capsys, monkeypatch):
    gkb.write("kblam.toml", (gkb.root / "kblam.toml").read_text(encoding="utf-8").replace(
        "[kb]\n", '[kb]\nevidence_roots = ["./bench-runs/"]\nhistory_dirs = []\n'))
    approval.record_approval(gkb.cfg, (gkb.root / "kblam.toml").read_bytes())
    gkb.write("bench-runs/run-1/trace.csv", "1,2\n")
    commit(gkb, "configure")
    gkb.write(f"{EVIDENCE}/log.txt", "no longer an evidence root\n")
    gkb.write("bench-runs/run-1/trace.csv", "3,4\n")
    git(gkb, "add", "-A")
    code, out = validate_commit(gkb, capsys)
    assert code == 1 and "bench-runs/run-1/trace.csv (modified)" in out and f"{EVIDENCE}/log.txt" not in out


def test_the_first_commit_has_no_evidence_to_change(tmp_path, kb, capsys, monkeypatch):
    """With no HEAD there is nothing the commit could change: everything is added."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(kb.root.parent / "gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(kb.root.parent))
    kb.write(".gitignore", ".kblam/\n")
    git(kb, "init", "-q")
    approval.record_approval(kb.cfg, (kb.root / "kblam.toml").read_bytes())  # the first commit adds kblam.toml
    git(kb, "add", "-A")
    code, out = validate_commit(kb, capsys)
    assert code == 0, out


# --- untracked evidence ---------------------------------------------------------------------------------


def test_untracked_evidence_and_verbatim_sources_get_a_warning(gkb, capsys):
    gkb.write("evidence/new-run/log.txt", "Line one of the new run.\n")
    gkb.write(".gitignore", ".kblam/\n*.bin\n")
    gkb.write("evidence/2026-09-22-ratio/capture.bin", b"\x00ignored by git\n")
    put(gkb, capsys, "F-0001", "motor", CLAIM_A, evidence="[evidence/new-run/]",
        body="<!-- verbatim: evidence/new-run/log.txt:1 -->\n> Line one of the new run.")
    put(gkb, capsys, "F-0002", "tray", CLAIM_B, topic="tray",
        evidence="[evidence/2026-09-22-ratio/capture.bin, evidence/2026-09-22-ratio/log.txt]")
    git(gkb, "add", "findings", ".gitignore")
    code, out = validate_commit(gkb, capsys)
    assert code == 0, out  # a warning, not a refusal
    assert ("kblam validate: warning: evidence/new-run is not tracked by git, so F-0001, which cites it as evidence, "
            "passes K2 here and fails on every clone. Commit it (git add evidence/new-run), or cite a file that is "
            "committed") in out
    assert ("warning: evidence/new-run/log.txt is not tracked by git, so F-0001, which cites it as a verbatim "
            "source, passes K10 here") in out
    assert "warning: evidence/2026-09-22-ratio/capture.bin is not tracked by git, so F-0002" in out
    assert "evidence/2026-09-22-ratio/log.txt is not tracked" not in out  # committed

    git(gkb, "add", "evidence/new-run")
    code, out = validate_commit(gkb, capsys)
    assert code == 0 and out.count("warning:") == 1 and "capture.bin" in out


def untracked(path: str, finding_id: str, what: str, rule: str) -> str:
    """The pre-commit warning for one finding citing an untracked `path`."""
    return (f"kblam validate: warning: {path} is not tracked by git, so {finding_id}, which cites it {what}, "
            f"passes {rule} here and fails on every clone. Commit it (git add {path}), or cite a file that is "
            f"committed\n")


def quote(path: str) -> str:
    """A verbatim excerpt of line 1 of `path`, whose text is "Line one."."""
    return f"<!-- verbatim: {path}:1 -->\n> Line one."


def test_a_path_in_a_nested_repository_under_an_evidence_root_gets_no_warning(gkb, capsys):
    """That repository holds the cited file, not this one: a nested repository made by git init, and one
    whose .git is a file (git init --separate-git-dir, the form a linked worktree or a submodule has). An
    ordinary untracked path beside them still gets the warning."""
    gkb.write("evidence/vendor/spec.txt", "Line one.\n")
    git(gkb, "init", "-q", "evidence/vendor")
    gkb.write("evidence/linked/spec.txt", "Line one.\n")
    git(gkb, "init", "-q", "--separate-git-dir", str(gkb.root.parent / "linked.git"), "evidence/linked")
    assert (gkb.root / "evidence/linked/.git").is_file()
    gkb.write("evidence/new-run/log.txt", "Line one.\n")
    put(gkb, capsys, "F-0001", "motor", CLAIM_A, evidence="[evidence/vendor/spec.txt, evidence/linked/]",
        body=f"{quote('evidence/vendor/spec.txt')}\n\n{quote('evidence/linked/spec.txt')}")
    put(gkb, capsys, "F-0002", "tray", CLAIM_B, topic="tray", evidence="[evidence/new-run/log.txt]")
    git(gkb, "add", "findings")

    assert validate_commit(gkb, capsys) == (
        0, untracked("evidence/new-run/log.txt", "F-0002", "as evidence", "K2") + "kblam validate: OK (2 findings)\n")


def in_nested(path: str, finding_id: str, what: str, rule: str, repository: str) -> str:
    """The pre-commit warning for one finding citing `path`, untracked and inside the nested Git repository
    `repository`, which no evidence root exempts: git add would stage nothing there."""
    return (f"kblam validate: warning: {path} is not tracked by git, so {finding_id}, which cites it {what}, "
            f"passes {rule} here and fails on every clone. It is inside the git repository {repository}/, whose "
            f"files this repository does not track, so git add cannot commit it. If {repository}/ is a source "
            f"repository, it belongs in [kb] evidence_roots in kblam.toml, which only a person changes: ask the "
            f"user to add \"{repository}\" there and run kblam approve-config before committing. Otherwise cite a "
            f"file that is committed\n")


def test_a_nested_repository_outside_an_evidence_root_does_not_hold_what_a_finding_cites(gkb, capsys):
    """Only a nested repository under an evidence root counts: one elsewhere (vendor/specs, cited as a
    verbatim source) gets the warning, and so does a path under the evidence root lab/evidence whose
    .git lies above that root (lab/.git), since nothing above an evidence root is looked at. git add
    stages nothing inside either, so the warning names the repository and [kb] evidence_roots instead.
    D49: git add of the cited path leaves the warning; adding each repository to [kb] evidence_roots, as
    the user does and approves, removes it."""
    gkb.write("kblam.toml", (gkb.root / "kblam.toml").read_text(encoding="utf-8").replace(
        "[kb]\n", '[kb]\nevidence_roots = ["evidence", "lab/evidence"]\n'))
    approval.record_approval(gkb.cfg, (gkb.root / "kblam.toml").read_bytes())
    commit(gkb, "configure")
    gkb.write("vendor/specs/spec.txt", "Line one.\n")
    git(gkb, "init", "-q", "vendor/specs")
    gkb.write("lab/evidence/run-1/log.txt", "Line one.\n")
    git(gkb, "init", "-q", "lab")
    put(gkb, capsys, "F-0001", "motor", CLAIM_A, evidence="[lab/evidence/run-1/log.txt]",
        body=quote("vendor/specs/spec.txt"))
    git(gkb, "add", "findings")

    warned = (0, in_nested("lab/evidence/run-1/log.txt", "F-0001", "as evidence", "K2", "lab") +
              in_nested("vendor/specs/spec.txt", "F-0001", "as a verbatim source", "K10", "vendor/specs") +
              "kblam validate: OK (1 findings)\n")
    assert validate_commit(gkb, capsys) == warned

    git(gkb, "add", "lab/evidence/run-1/log.txt", "vendor/specs/spec.txt")   # exits 0 and stages nothing
    assert validate_commit(gkb, capsys) == warned

    gkb.write("kblam.toml", (gkb.root / "kblam.toml").read_text(encoding="utf-8").replace(
        'evidence_roots = ["evidence", "lab/evidence"]', 'evidence_roots = ["evidence", "lab/evidence", "lab", '
                                                         '"vendor/specs"]'))
    approval.record_approval(gkb.cfg, (gkb.root / "kblam.toml").read_bytes())   # kblam approve-config
    git(gkb, "add", "kblam.toml")
    assert validate_commit(gkb, capsys) == (0, "kblam validate: OK (1 findings)\n")


# --- kblam.resolutions.jsonl ----------------------------------------------------------------------------


def test_the_staged_resolutions_must_parse(gkb, capsys):
    good = resolutions(json.dumps(RESOLUTION), "", json.dumps({**RESOLUTION, "kind": "not_revision"}))
    gkb.write("kblam.resolutions.jsonl", good)
    git(gkb, "add", "kblam.resolutions.jsonl")
    code, out = validate_commit(gkb, capsys)
    assert code == 0, out

    missing_date = {k: v for k, v in RESOLUTION.items() if k != "date"}
    gkb.write("kblam.resolutions.jsonl", good + resolutions("{not json", "[1, 2]", json.dumps(missing_date)))
    git(gkb, "add", "kblam.resolutions.jsonl")
    code, out = validate_commit(gkb, capsys)
    assert code == 1
    assert ("kblam validate: the kblam.resolutions.jsonl this commit holds does not parse: lines 4, 5 and 6 are "
            "not JSON objects with the keys sides, kind, reason and date. Only kblam resolve writes it") in out

    gkb.write("kblam.resolutions.jsonl", "{not json\n")  # the working copy; the commit holds what is staged
    git(gkb, "add", "kblam.resolutions.jsonl")
    code, out = validate_commit(gkb, capsys)
    assert code == 1 and "line 1 is not a JSON object with the keys" in out
    gkb.write("kblam.resolutions.jsonl", good)
    assert validate_commit(gkb, capsys)[0] == 1  # still staged as broken
    git(gkb, "add", "kblam.resolutions.jsonl")
    assert validate_commit(gkb, capsys)[0] == 0


def test_resolutions_outside_the_index_are_not_checked(gkb, capsys):
    gkb.write("kblam.resolutions.jsonl", "{not json\n")  # untracked: the commit does not hold it
    assert validate_commit(gkb, capsys)[0] == 0


def test_resolutions_that_are_not_utf8_do_not_parse(gkb, capsys):
    gkb.write("kblam.resolutions.jsonl", b"\xff\xfe{}\n")
    git(gkb, "add", "kblam.resolutions.jsonl")
    code, out = validate_commit(gkb, capsys)
    assert code == 1 and "does not parse: it is not UTF-8 text" in out


# --- kblam.toml and a real git commit ---------------------------------------------------------------------


def test_the_kblam_toml_approval_still_applies(gkb, capsys):
    gkb.write("kblam.toml", (gkb.root / "kblam.toml").read_text(encoding="utf-8").replace(
        'scopes = ["MX-200", "MX-100", "any"]', 'scopes = ["MX-200", "any"]'))
    git(gkb, "add", "kblam.toml")
    code, out = validate_commit(gkb, capsys)
    assert code == 1 and "this commit changes kblam.toml" in out and "kblam.toml needs approval" in out


def git_commit(kb, *args: str) -> subprocess.CompletedProcess:
    """`git commit` with the hooks, as a person or an agent runs it."""
    return subprocess.run(["git", "-c", "user.name=kblam test", "-c", "user.email=test@example.invalid",
                           "-c", "commit.gpgsign=false", "commit", "-q", *args],
                          cwd=kb.root, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("kblam") is None or shutil.which("sh") is None,
                    reason="runs the POSIX pre-commit hook, with kblam on PATH")
def test_the_pre_commit_hook_checks_the_commit_git_makes(gkb, capsys):
    """The installed pre-commit hook runs these checks inside `git commit`, which holds the index lock, and
    sees the temporary index that `git commit -a` builds."""
    from kblam.init import ASSETS

    hook = gkb.root / ".git" / "hooks" / "pre-commit"
    hook.write_bytes((ASSETS / "pre-commit").read_bytes())
    os.chmod(hook, 0o755)
    put(gkb, capsys, "F-0001", "motor", CLAIM_A)
    git(gkb, "add", "findings/INDEX.md")
    refused = git_commit(gkb, "-m", "part of a put")
    assert refused.returncode == 1
    assert "findings/calibration/F-0001-motor.md (untracked)" in refused.stdout + refused.stderr
    assert "kblam pre-commit: commit refused (kblam validate exit 1)" in refused.stderr

    git(gkb, "add", "-A")
    assert git_commit(gkb, "-m", "F-0001").returncode == 0

    (gkb.root / EVIDENCE / "log.txt").write_text("rewritten\n", encoding="utf-8")
    refused = git_commit(gkb, "-a", "-m", "edit evidence")  # staged only in git's temporary index
    assert refused.returncode == 1 and f"{EVIDENCE}/log.txt (modified)" in refused.stdout + refused.stderr
    assert git_commit(gkb, "-a", "--no-verify", "-m", "a person's deliberate change").returncode == 0
