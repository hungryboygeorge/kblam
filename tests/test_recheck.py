"""`kblam recheck` (SPEC §4, §7, §8.3): a finding's check: command runs only in recheck, and only once it
is approved on this machine, by the agent that read its block and ran `--approve` (the default) or, when
kblam.toml sets recheck_person_approval, by a person at a terminal. The approvals live in the git
directory, where no commit can write, and a command runs without the Jev key's variable. The commands here
are local Python scripts under tmp_path; nothing touches the network."""

from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import time

import pytest

from kblam import cli, recheck
from kblam.config import ConfigError, load_config
from kblam.gitdir import git_common_dir
from kblam.recheck import find_program, named_files, shown

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, finding_text
from test_hook import call, denied, stop, tool

PY = shlex.quote(sys.executable)
EVIDENCE = "evidence/2026-09-22-ratio"  # the fixture KB's evidence package
MARK = f"{EVIDENCE}/mark.py"
# Writes ran-<name> in the directory it runs in (the repository root), prints a line, and exits with the
# code given (0 by default).
MARK_SCRIPT = """\
import sys
with open("ran-" + sys.argv[1], "a") as handle:
    handle.write("x")
print("ratio 1.0017 for", sys.argv[1])
sys.exit(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
"""
CLAIMS = {
    "F-0001": "The sensor's two curve types agree to within a tenth of a percent over the capture.",
    "F-0002": "The fan controller holds its duty cycle at forty percent while the lid is closed.",
    "F-0003": "The bootloader checks a CRC32 over the first sixteen kilobytes before it jumps.",
}
AT_TERMINAL = recheck.at_terminal
APPROVALS = ".git/kblam/recheck-approved.jsonl"  # where a clone keeps them, out of every commit's reach
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout


@pytest.fixture(autouse=True)
def a_clone(request):
    """Every knowledge base here is a git work tree, as a kblam project is: recheck keeps its approvals in the
    git directory and runs nothing outside one."""
    if "kb" in request.fixturenames:
        git(request.getfixturevalue("kb").root, "init", "-q")


def mark(name: str, exit_code: int = 0) -> str:
    return f"{PY} {MARK} {name}" + (f" {exit_code}" if exit_code else "")


def add_check(kb, finding_id: str, command: str):
    """A finding in the tree whose check: is `command` (written as a YAML double-quoted string)."""
    return kb.add(finding_id, f"check-{finding_id[2:]}", CLAIMS[finding_id],
                  extra=f"check: {json.dumps(command)}\n")


def ran(kb) -> list[str]:
    return sorted(p.name.removeprefix("ran-") for p in kb.root.glob("ran-*"))


def run(kb, capsys, monkeypatch, *args: str, answers: list[str] | None = None) -> tuple[int, str, str]:
    """`kblam recheck <args>` at a terminal where a person gives `answers` in turn, or, when `answers` is
    None, with no terminal, as in an agent's shell tool, where nothing may be asked."""
    if answers is None:
        monkeypatch.setattr(recheck, "at_terminal", AT_TERMINAL)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("no terminal: nothing may be asked"))
    else:
        replies = iter(answers)
        monkeypatch.setattr(recheck, "at_terminal", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    capsys.readouterr()
    code = cli.main(["--root", str(kb.root), "recheck", *args])
    out, err = capsys.readouterr()
    return code, out, err


def approval_command(out: str) -> list[str]:
    """The arguments of the `kblam recheck F-NNNN --approve DIGEST` command the last block printed, taken
    from the output as an agent reading it would (run() supplies the program name and --root)."""
    line = next(line for line in out.splitlines() if "To approve it, run: " in line)
    command = line.split("To approve it, run: ", 1)[1].split()
    assert command[:2] == ["kblam", "recheck"], command
    return command[2:]


def log_lines(kb) -> list[dict]:
    path = kb.root / ".kblam" / "recheck.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def mkb(kb):
    """The fixture KB with the marker script in its evidence package."""
    kb.write(MARK, MARK_SCRIPT)
    return kb


# --- the required cases -------------------------------------------------------------------------


def test_an_approved_check_that_passes(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch, answers=["y"])
    assert code == 0 and ran(mkb) == ["F-0001"]
    assert "kblam recheck: F-0001 passed (exit 0 after " in out
    assert out.endswith("kblam recheck: 1 check(s): 1 passed\n")

    # approved on this machine, so an agent's shell with no terminal runs it too, from the repository root
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert code == 0 and "F-0001 passed" in out
    assert (mkb.root / "ran-F-0001").read_text(encoding="utf-8") == "xx"
    assert (mkb.root / ".kblam/recheck/F-0001.log").read_text(encoding="utf-8").strip() == "ratio 1.0017 for F-0001"


def test_a_failing_check_is_reported_with_its_output(mkb, capsys, monkeypatch):
    command = mark("F-0001", exit_code=3)
    add_check(mkb, "F-0001", command)
    code, out, _ = run(mkb, capsys, monkeypatch, answers=["y"])
    assert code == 1
    assert ("kblam recheck: F-0001 FAILED (exit 3 after " in out
            and "its output is in .kblam/recheck/F-0001.log, ending:\n  | ratio 1.0017 for F-0001\n" in out)
    assert "F-0001's key number did not reproduce, or its command broke" in out and "kblam edit F-0001" in out
    assert out.endswith("kblam recheck: 1 check(s): 0 passed, 1 failed. Load the kblam-write skill for how to "
                        "fix this.\n")
    last = log_lines(mkb)[-1]
    assert (last["id"], last["outcome"], last["exit_code"], last["terminal"]) == ("F-0001", "failed", 3, True)
    assert last["approver"] == "person"  # approved at the terminal this run asked at
    assert last["command"] == hashlib.sha256(command.encode()).hexdigest()
    assert command not in (mkb.root / ".kblam/recheck.jsonl").read_text(encoding="utf-8")  # digests, not text


def test_without_a_terminal_an_unapproved_command_is_not_run(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, err = run(mkb, capsys, monkeypatch)
    assert code == 1 and ran(mkb) == [] and err == ""
    assert f"kblam recheck: F-0001 not run: not approved on this machine (new command): {mark('F-0001')}\n" in out
    assert (f"  command: {mark('F-0001')}\n"
            f"  argv:    {json.dumps(shlex.split(mark('F-0001')))}\n"
            f"  program: {sys.executable}\n"
            f"  pinned:  {MARK}\n") in out
    args = approval_command(out)  # the block shows the agent exactly what would approve this command
    assert args[:2] == ["F-0001", "--approve"]
    assert re.fullmatch(r"[0-9a-f]{12}", args[-1])
    assert "  That digest names this command and these files; kblam refuses it once either changes.\n" in out
    assert ("  Approve it only if the command does what this finding's check: needs and nothing else. If\n"
            "  anything looks wrong -- a program unrelated to the finding, deleting or sending anything, a\n"
            "  path outside this repository, or a character hidden in an escaped command -- do not approve\n"
            "  it: leave the finding as it is and tell the user.\n") in out
    assert "1 check(s): 0 passed, 1 not approved" in out
    assert not (mkb.root / APPROVALS).exists()
    assert log_lines(mkb)[-1]["outcome"] == "not_approved"


def test_a_command_changed_after_approval_needs_a_person_again(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    assert run(mkb, capsys, monkeypatch, answers=["y"])[0] == 0
    add_check(mkb, "F-0001", mark("F-0001") + " --again")  # the finding's check: rewritten, e.g. by a git pull
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert code == 1 and ran(mkb) == ["F-0001"]  # only the approved run
    assert "F-0001 not run: not approved on this machine (command changed since approval)" in out

    add_check(mkb, "F-0001", mark("F-0001"))  # back to the approved command, which needs nobody
    assert run(mkb, capsys, monkeypatch)[0] == 0 and (mkb.root / "ran-F-0001").read_text() == "xx"


def test_ids_select_the_checks_that_run(mkb, capsys, monkeypatch):
    for finding_id in ("F-0001", "F-0002"):
        add_check(mkb, finding_id, mark(finding_id))
    mkb.add("F-0003", "plain", CLAIMS["F-0003"])
    assert run(mkb, capsys, monkeypatch, answers=["y", "y"])[0] == 0
    (mkb.root / "ran-F-0001").unlink()
    (mkb.root / "ran-F-0002").unlink()

    code, out, _ = run(mkb, capsys, monkeypatch, "F-0002", "F-0002")
    assert code == 0 and ran(mkb) == ["F-0002"] and "F-0001" not in out
    assert out.endswith("kblam recheck: 1 check(s): 1 passed\n")

    for ids, message in ((["F-0002", "F-0003"], "F-0003 has no check: command, so there is nothing to recheck"),
                         (["F-0002", "F-0009"], "F-0009 is not in findings/"),
                         (["F-0002", "0002"], "'0002' is not a finding ID like F-0137")):
        code, out, err = run(mkb, capsys, monkeypatch, *ids)
        assert (code, out) == (1, "") and err == f"kblam recheck: {message}\n"
    assert ran(mkb) == ["F-0002"]  # a refused request runs nothing


# --- asking a person ----------------------------------------------------------------------------


def test_at_a_terminal_every_command_is_shown_and_asked_before_any_runs(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    add_check(mkb, "F-0002", mark("F-0002"))
    code, out, _ = run(mkb, capsys, monkeypatch, answers=["n", "yes"])
    assert code == 1 and ran(mkb) == ["F-0002"]
    assert out.index("F-0002's check: is not approved") < out.index("F-0002 running")  # both asked first
    assert out.index("F-0001's check: is not approved") < out.index("F-0002's check: is not approved")
    assert (f"  command: {mark('F-0001')}\n"
            f"  argv:    {json.dumps(shlex.split(mark('F-0001')))}\n"
            f"  program: {sys.executable}\n"
            f"  pinned:  {MARK}\n") in out
    assert "for at most 600 s" in out
    assert "F-0001 not run: not approved on this machine (you did not approve it)" in out
    assert [a["id"] for a in recheck.load_approvals(mkb.cfg)] == ["F-0002"]
    assert [line["outcome"] for line in log_lines(mkb)] == ["declined", "passed"]


def test_a_changed_script_needs_a_person_again(mkb, capsys, monkeypatch):
    """The approval pins each repository file the command names: a script changed by a git pull is shown
    to a person before it runs again, even though the command string is the same."""
    add_check(mkb, "F-0001", mark("F-0001"))
    assert run(mkb, capsys, monkeypatch, answers=["y"])[0] == 0
    mkb.write(MARK, MARK_SCRIPT + "# changed\n")
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert code == 1 and (mkb.root / "ran-F-0001").read_text() == "x"
    assert f"F-0001 not run: not approved on this machine ({MARK} changed since approval)" in out

    code, out, _ = run(mkb, capsys, monkeypatch, answers=["y"])
    assert code == 0 and f"({MARK} changed since approval)" in out  # the prompt says why it asks again


def test_a_script_changed_after_its_approval_in_the_same_run_is_not_run(mkb, capsys, monkeypatch):
    """Named files are hashed again just before each check runs: here F-0001's check rewrites the script
    F-0002's check runs, after a person approved both."""
    mkb.write(f"{EVIDENCE}/touch.py", "import sys\nopen(sys.argv[1], 'a').write('# touched\\n')\n")
    add_check(mkb, "F-0001", f"{PY} {EVIDENCE}/touch.py {MARK}")
    add_check(mkb, "F-0002", mark("F-0002"))
    code, out, _ = run(mkb, capsys, monkeypatch, answers=["y", "y"])
    assert code == 1 and ran(mkb) == []
    assert "F-0001 passed" in out
    assert f"F-0002 not run: not approved on this machine ({MARK} changed since approval)" in out
    assert "Run kblam recheck F-0002 again to see it and decide." in out


def test_the_prompt_shows_a_hostile_command_escaped(kb, capsys, monkeypatch):
    """An escape sequence could erase the command from the terminal, and a right-to-left override could
    reorder it, so the person would approve something other than what they read."""
    add_check(kb, "F-0001", f"{PY} -c pass \x1b[2K\rbenign \u202etxt.exe")
    code, out, _ = run(kb, capsys, monkeypatch, answers=["n"])
    assert code == 1 and "\x1b" not in out and "\u202e" not in out and ran(kb) == []
    # shown() is ascii(), which closes with ' unless the text holds a ' and no ": on Windows PY is the
    # interpreter's path, which shlex.quote wraps in ' (a backslash is not shell-safe), so there it closes with ".
    end = '"' if "'" in PY else "'"
    assert (f"\\x1b[2K\\rbenign \\u202etxt.exe{end}  (shown escaped: it holds characters other than printable "
            f"ASCII; the argv is what runs)\n") in out
    assert '"-c", "pass", "\\u001b[2K", "benign", "\\u202etxt.exe"]\n' in out


RED_SCRIPT = r"""import sys
sys.stdout.buffer.write(b"\x1b[31mred\x1b[0m \xe2\x80\xaeok\n")
sys.exit(1)
"""


def test_a_failed_checks_output_is_shown_with_control_characters_escaped(kb, capsys, monkeypatch):
    kb.write(f"{EVIDENCE}/red.py", RED_SCRIPT)
    add_check(kb, "F-0001", f"{PY} {EVIDENCE}/red.py")
    code, out, _ = run(kb, capsys, monkeypatch, answers=["y"])
    assert code == 1 and "  | \\x1b[31mred\\x1b[0m \\u202eok\n" in out and "\x1b" not in out


def test_list_shows_each_command_and_its_state_and_runs_nothing(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    add_check(mkb, "F-0002", mark("F-0002"))
    assert run(mkb, capsys, monkeypatch, "F-0001", "F-0002", answers=["y", "y"])[0] == 0
    (mkb.root / "ran-F-0001").unlink()
    (mkb.root / "ran-F-0002").unlink()
    add_check(mkb, "F-0002", mark("F-0002") + " 0")
    add_check(mkb, "F-0003", f"{PY} 'unclosed")
    code, out, err = run(mkb, capsys, monkeypatch, "--list")
    assert (code, err, ran(mkb)) == (0, "", [])
    assert out == (f"kblam recheck: F-0001 approved: {mark('F-0001')}\n"
                   f"kblam recheck: F-0002 not approved (command changed since approval): {mark('F-0002')} 0\n"
                   f"kblam recheck: F-0003 cannot run: {PY} 'unclosed\n"
                   f"  its check: cannot be split into a program and arguments (No closing quotation); fix the "
                   f"quoting with kblam edit F-0003\n"
                   f"kblam recheck --list: 3 check(s): 1 approved, 1 not approved on this machine, 1 cannot run; "
                   f"nothing was run\n")


# --- the agent approves a check (the default: recheck_person_approval = false) ----------------------


def test_an_agent_approves_the_digest_its_block_printed_and_the_check_runs(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch)  # no terminal: the block is shown, nothing runs
    assert code == 1 and ran(mkb) == []
    code, out, err = run(mkb, capsys, monkeypatch, *approval_command(out))  # the printed command, as printed
    assert (code, err) == (0, "") and ran(mkb) == ["F-0001"]
    assert "kblam recheck: F-0001 running: " in out and "F-0001 passed (exit 0 after " in out
    assert out.endswith("kblam recheck: 1 check(s): 1 passed\n")
    assert [a["approver"] for a in recheck.load_approvals(mkb.cfg)] == ["agent"]
    last = log_lines(mkb)[-1]
    assert (last["id"], last["outcome"], last["approver"], last["terminal"]) == ("F-0001", "passed", "agent", False)

    (mkb.root / "ran-F-0001").unlink()  # approved now: an ordinary recheck runs it, asking nobody
    assert run(mkb, capsys, monkeypatch)[0] == 0 and ran(mkb) == ["F-0001"]
    assert log_lines(mkb)[-1]["approver"] == "agent"


def test_an_approved_check_that_fails_exits_1(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001", exit_code=3))
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert code == 1
    code, out, err = run(mkb, capsys, monkeypatch, *approval_command(out))
    assert (code, err) == (1, "") and "F-0001 FAILED (exit 3 after " in out
    assert out.endswith("kblam recheck: 1 check(s): 0 passed, 1 failed. Load the kblam-write skill for how to "
                        "fix this.\n")
    assert log_lines(mkb)[-1]["approver"] == "agent"


def test_a_digest_that_is_not_the_commands_is_refused_with_the_new_block(mkb, capsys, monkeypatch):
    """A command or a named file changed since the block was shown: the digest read then approves something
    else now, so it is refused, nothing is recorded, and the block to read again is printed."""
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch)
    stale = approval_command(out)[-1]
    add_check(mkb, "F-0001", mark("F-0001") + " 0")  # the finding's check: rewritten, e.g. by a git pull
    code, out, err = run(mkb, capsys, monkeypatch, "F-0001", "--approve", stale)
    assert (code, err) == (1, "") and ran(mkb) == [] and not (mkb.root / APPROVALS).exists()
    assert (f"kblam recheck: F-0001 was not approved: {stale} is not the digest of its check: command and files "
            f"as they are now: it was copied wrong, or the command or a file it names changed since it was "
            f"shown. Read the block below again, then approve the digest it prints, or leave the finding as it "
            f"is.\n") in out
    assert f"  command: {mark('F-0001')} 0\n" in out
    fresh = approval_command(out)[-1]
    assert fresh != stale
    assert run(mkb, capsys, monkeypatch, "F-0001", "--approve", fresh)[0] == 0
    assert ran(mkb) == ["F-0001"] and [a["approver"] for a in recheck.load_approvals(mkb.cfg)] == ["agent"]


def test_a_pinned_file_changed_after_an_agent_approved_needs_approving_again(mkb, capsys, monkeypatch):
    """The approval pins each repository file the command names: a script changed by a git pull is shown
    again, with a new digest, before it runs."""
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert run(mkb, capsys, monkeypatch, *approval_command(out))[0] == 0
    mkb.write(MARK, MARK_SCRIPT + "# changed\n")
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert code == 1 and (mkb.root / "ran-F-0001").read_text() == "x"
    assert f"F-0001 not run: not approved on this machine ({MARK} changed since approval)" in out
    assert f"  command: {mark('F-0001')}\n" in out
    assert run(mkb, capsys, monkeypatch, *approval_command(out))[0] == 0
    assert (mkb.root / "ran-F-0001").read_text() == "xx"


def test_a_pinned_file_changed_between_the_block_and_the_approval_is_refused(mkb, capsys, monkeypatch):
    """The digest names the script as it was when the block was shown; a pull that changes it before the
    agent approves would approve something the agent never read, so the stale digest is refused."""
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch)
    stale = approval_command(out)[-1]
    mkb.write(MARK, MARK_SCRIPT + "# changed\n")
    code, out, err = run(mkb, capsys, monkeypatch, "F-0001", "--approve", stale)
    assert (code, err, ran(mkb)) == (1, "", []) and not (mkb.root / APPROVALS).exists()
    assert (f"kblam recheck: F-0001 was not approved: {stale} is not the digest of its check: command and files "
            f"as they are now") in out
    fresh = approval_command(out)[-1]
    assert fresh != stale and run(mkb, capsys, monkeypatch, "F-0001", "--approve", fresh)[0] == 0
    assert ran(mkb) == ["F-0001"]


def test_the_digest_is_read_without_regard_to_case_or_surrounding_spaces(mkb, capsys, monkeypatch):
    """A digest pasted in capitals, or with a space that came with it, is the same digest: the check runs."""
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch)
    digest = approval_command(out)[-1]
    assert digest != digest.upper()
    assert run(mkb, capsys, monkeypatch, "F-0001", "--approve", f" {digest.upper()} ")[0] == 0
    assert ran(mkb) == ["F-0001"]


@pytest.mark.parametrize("value", ["", "3f2a9c1b7e", "zzzzzzzzzzzz", "F-0001"])
def test_a_mistyped_digest_is_refused_and_says_so(mkb, capsys, monkeypatch, value):
    """A short, empty or otherwise wrong value was blamed on a change to the command; the refusal now names
    both the copy mistake and a change, and prints the block to approve from again."""
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert (code, ran(mkb)) == (1, [])
    code, out, err = run(mkb, capsys, monkeypatch, "F-0001", "--approve", value)
    assert (code, err) == (1, "") and ran(mkb) == [] and not (mkb.root / APPROVALS).exists()
    was_given = shown(value.strip().lower()) or "''"  # the compared value; empty would leave a gap
    assert (f"kblam recheck: F-0001 was not approved: {was_given} is not the digest of its check: "
            f"command and files as they are now: it was copied wrong, or the command or a file it names changed "
            f"since it was shown. Read the block below again, then approve the digest it prints, or leave the "
            f"finding as it is.\n") in out
    assert run(mkb, capsys, monkeypatch, *approval_command(out))[0] == 0  # the block it tells the agent to read
    assert ran(mkb) == ["F-0001"]


def test_another_findings_digest_is_refused(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    add_check(mkb, "F-0002", mark("F-0002"))
    code, out, _ = run(mkb, capsys, monkeypatch, "F-0002")
    other = approval_command(out)[-1]
    code, out, err = run(mkb, capsys, monkeypatch, "F-0001", "--approve", other)
    assert (code, err, ran(mkb)) == (1, "", []) and not (mkb.root / APPROVALS).exists()
    assert (f"kblam recheck: F-0001 was not approved: {other} is not the digest of its check: command and files "
            f"as they are now: it was copied wrong") in out


def test_the_log_line_names_no_approver_for_a_check_that_was_not_run(mkb, capsys, monkeypatch):
    """Nothing approved these two, so the log credits no one: a JSON null, not "person" and not "agent"."""
    add_check(mkb, "F-0001", mark("F-0001"))
    add_check(mkb, "F-0002", "kblam-no-such-program --version")
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert code == 1 and ran(mkb) == []
    lines = {line["id"]: line for line in log_lines(mkb)}
    assert (lines["F-0001"]["outcome"], lines["F-0001"]["approver"]) == ("not_approved", None)
    assert (lines["F-0002"]["outcome"], lines["F-0002"]["approver"]) == ("not_started", None)


@pytest.mark.parametrize("ids, got", [([], "none"), (["F-0001", "F-0002"], "F-0001, F-0002")])
def test_approving_needs_exactly_one_id(mkb, capsys, monkeypatch, ids, got):
    for finding_id in ("F-0001", "F-0002"):
        add_check(mkb, finding_id, mark(finding_id))
    code, out, err = run(mkb, capsys, monkeypatch, *ids, "--approve", "0" * 12)
    assert (code, out) == (1, "") and ran(mkb) == []
    assert err == (f"kblam recheck: --approve approves the one check whose block you read, so it needs exactly one "
                   f"finding ID; {got} was given. Run kblam recheck F-NNNN for the one finding you mean: it prints "
                   f"that check's block and the --approve command to run (kblam recheck --list shows which findings "
                   f"have a check: command)\n")
    code, out, err = run(mkb, capsys, monkeypatch, "--list")  # one command the refusal names: it runs
    assert (code, err) == (0, "") and out.count("not approved (new command)") == 2
    # D49: the other one works too: the block it prints, then the approve command that block prints
    code, out, err = run(mkb, capsys, monkeypatch, "F-0001")
    assert (code, err, ran(mkb)) == (1, "", [])
    assert run(mkb, capsys, monkeypatch, *approval_command(out))[0] == 0
    assert ran(mkb) == ["F-0001"]


def test_approving_an_already_approved_command_with_another_digest_is_refused(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert run(mkb, capsys, monkeypatch, *approval_command(out))[0] == 0
    (mkb.root / "ran-F-0001").unlink()
    code, out, err = run(mkb, capsys, monkeypatch, "F-0001", "--approve", "0" * 12)
    assert (code, out) == (1, "") and ran(mkb) == []
    check = recheck.collect(mkb.cfg, ["F-0001"])[0][0]
    assert err == (f"kblam recheck: F-0001 is already approved as it is now, so there is nothing to approve, and "
                   f"000000000000 is not the digest of its check: command and files "
                   f"({recheck.approval_digest(check)}). Run kblam recheck F-0001 to run it\n")
    assert run(mkb, capsys, monkeypatch, "F-0001")[0] == 0 and ran(mkb) == ["F-0001"]  # the command it names


@pytest.mark.parametrize(("value", "read_as"), [
    (" ABCDEF012345 ", "abcdef012345"),            # capitals and spaces: the value kblam read and compared
    ("", "''"),                                    # an empty one would otherwise leave a gap in the sentence
])
def test_the_already_approved_refusal_shows_the_value_it_compared(mkb, capsys, monkeypatch, value, read_as):
    """The already-approved branch names the value kblam read, not the argument as typed: a digest pasted
    in capitals or with spaces reads as its stripped lower-case form, and an empty one as two quotes."""
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert run(mkb, capsys, monkeypatch, *approval_command(out))[0] == 0
    (mkb.root / "ran-F-0001").unlink()
    code, out, err = run(mkb, capsys, monkeypatch, "F-0001", "--approve", value)
    assert (code, out) == (1, "") and ran(mkb) == []
    check = recheck.collect(mkb.cfg, ["F-0001"])[0][0]
    assert err == (f"kblam recheck: F-0001 is already approved as it is now, so there is nothing to approve, and "
                   f"{read_as} is not the digest of its check: command and files "
                   f"({recheck.approval_digest(check)}). Run kblam recheck F-0001 to run it\n")


def test_approving_a_command_that_cannot_run_is_refused(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", "kblam-no-such-program --version")
    code, out, err = run(mkb, capsys, monkeypatch, "F-0001", "--approve", "0" * 12)
    assert (code, out) == (1, "") and ran(mkb) == [] and not (mkb.root / APPROVALS).exists()
    assert err == ("kblam recheck: F-0001 cannot be approved: kblam-no-such-program was not found on PATH; it "
                   "cannot run on this machine, so there is nothing to approve: leave the finding as it is and "
                   "tell the user\n")


def test_approving_a_command_that_cannot_be_split_keeps_the_edit_step_it_names(mkb, capsys, monkeypatch):
    """That problem already says what to do (kblam edit), so the refusal names no other step."""
    add_check(mkb, "F-0001", f"{PY} 'unclosed")
    code, out, err = run(mkb, capsys, monkeypatch, "F-0001", "--approve", "0" * 12)
    assert (code, out) == (1, "") and ran(mkb) == []
    assert err == ("kblam recheck: F-0001 cannot be approved: its check: cannot be split into a program and "
                   "arguments (No closing quotation); fix the quoting with kblam edit F-0001\n")


def test_list_and_approve_together_are_refused(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, err = run(mkb, capsys, monkeypatch, "--list", "--approve", "0" * 12)
    assert (code, out) == (1, "") and ran(mkb) == []
    assert err == ("kblam recheck: --list prints each check: command and runs nothing, while --approve approves "
                   "one command and runs it; use one or the other\n")


# --- the machine requires a person (recheck_person_approval = true) ---------------------------------


def person_only(kb) -> None:
    kb.write("kblam.toml", KBLAM_TOML + "recheck_person_approval = true\n" + NO_EMBEDDINGS + PROMPT_TOML)


def test_with_person_approval_the_prompt_stays_and_an_agent_may_not_approve(mkb, capsys, monkeypatch):
    person_only(mkb)
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, err = run(mkb, capsys, monkeypatch)  # no terminal: today's report, with no block to approve
    assert (code, err) == (1, "") and ran(mkb) == []
    assert "A person approves it by running kblam recheck F-0001 at a terminal" in out
    assert "an agent asks the user to do that" in out and "never runs an unapproved one itself" in out
    assert "To approve it, run: " not in out

    code, out, err = run(mkb, capsys, monkeypatch, "F-0001", "--approve", "0" * 12)
    assert (code, out) == (1, "") and ran(mkb) == [] and not (mkb.root / APPROVALS).exists()
    assert err == ("kblam recheck: --approve is refused: kblam.toml sets recheck_person_approval = true, so only "
                   "a person at a terminal approves a check: command. Ask the user to run kblam recheck F-0001 at "
                   "a terminal, which shows the command and asks them\n")

    # any other number of IDs: nothing to name, so the old direction stays
    code, out, err = run(mkb, capsys, monkeypatch, "F-0001", "F-0002", "--approve", "0" * 12)
    assert (code, out) == (1, "") and ran(mkb) == []
    assert err == ("kblam recheck: --approve is refused: kblam.toml sets recheck_person_approval = true, so only "
                   "a person at a terminal approves a check: command. Ask the user to run kblam recheck with that "
                   "finding's ID at a terminal, which shows the command and asks them\n")

    code, out, _ = run(mkb, capsys, monkeypatch, answers=["y"])  # a person at a terminal, as before
    assert code == 0 and ran(mkb) == ["F-0001"]
    assert [a["approver"] for a in recheck.load_approvals(mkb.cfg)] == ["person"]
    assert log_lines(mkb)[-1]["approver"] == "person"


def test_with_person_approval_an_id_kblam_has_not_looked_at_is_refused_by_collect(mkb, capsys, monkeypatch):
    """The person-approval refusal names the one ID, so it checks that ID first: one kblam has not looked at
    gets collect's own refusal rather than a step about a finding that is not there."""
    person_only(mkb)
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, err = run(mkb, capsys, monkeypatch, "F-9999", "--approve", "0" * 12)
    assert (code, out) == (1, "") and ran(mkb) == []
    assert err == f"kblam recheck: F-9999 is not in {mkb.cfg.findings_dir}/\n"
    assert "Ask the user to run kblam recheck F-9999" not in err


def test_an_agent_approval_does_not_count_once_a_person_is_required(mkb, capsys, monkeypatch):
    """Switching recheck_person_approval on revokes an agent's approvals: the state says so and the check
    does not run until a person approves it at a terminal."""
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert run(mkb, capsys, monkeypatch, *approval_command(out))[0] == 0
    (mkb.root / "ran-F-0001").unlink()
    person_only(mkb)
    code, out, err = run(mkb, capsys, monkeypatch)
    assert (code, err) == (1, "") and ran(mkb) == []
    assert ("F-0001 not run: not approved on this machine (it was approved by an agent, and kblam.toml sets "
            "recheck_person_approval = true, so only a person's approval counts on this machine)") in out
    assert run(mkb, capsys, monkeypatch, "F-0001", "--approve", "0" * 12)[0] == 1
    assert run(mkb, capsys, monkeypatch, answers=["y"])[0] == 0 and ran(mkb) == ["F-0001"]


def test_setting_person_approval_back_to_false_lets_the_agents_approval_count_again(mkb, capsys, monkeypatch):
    """The key revokes an agent's approvals while it is true and nothing else: setting it back to false runs
    them again, without a second approval (SPEC §7)."""
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert run(mkb, capsys, monkeypatch, *approval_command(out))[0] == 0
    (mkb.root / "ran-F-0001").unlink()
    person_only(mkb)
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert (code, ran(mkb)) == (1, [])
    mkb.write("kblam.toml", KBLAM_TOML + "recheck_person_approval = false\n" + NO_EMBEDDINGS + PROMPT_TOML)
    assert run(mkb, capsys, monkeypatch)[0] == 0 and ran(mkb) == ["F-0001"]
    assert log_lines(mkb)[-1]["approver"] == "agent"


@pytest.mark.parametrize("value", ["null", '"Agent"', '"person "', "true", "0"])
def test_an_approver_value_kblam_does_not_write_counts_as_an_agent(mkb, capsys, monkeypatch, value):
    """Only "person" is a person's approval: a line whose value was altered cannot pass itself off as one,
    so it reads as an agent's, which recheck_person_approval = true then revokes (SPEC §7)."""
    add_check(mkb, "F-0001", mark("F-0001"))
    assert run(mkb, capsys, monkeypatch, answers=["y"])[0] == 0
    path = mkb.root / APPROVALS
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    record["approver"] = json.loads(value)
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert [a["approver"] for a in recheck.load_approvals(mkb.cfg)] == ["agent"]
    (mkb.root / "ran-F-0001").unlink()
    assert run(mkb, capsys, monkeypatch)[0] == 0 and ran(mkb) == ["F-0001"]  # by default an agent's counts
    person_only(mkb)
    (mkb.root / "ran-F-0001").unlink()
    code, out, _ = run(mkb, capsys, monkeypatch)
    assert code == 1 and ran(mkb) == [] and "it was approved by an agent" in out


def test_an_approval_line_without_an_approver_reads_as_a_person(mkb, capsys, monkeypatch):
    """Only a person could approve before kblam recorded who did, so such a line is a person's, and it still
    counts when the machine later requires one."""
    add_check(mkb, "F-0001", mark("F-0001"))
    assert run(mkb, capsys, monkeypatch, answers=["y"])[0] == 0
    path = mkb.root / APPROVALS
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    del record["approver"]
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    (mkb.root / "ran-F-0001").unlink()
    assert run(mkb, capsys, monkeypatch)[0] == 0 and ran(mkb) == ["F-0001"]
    assert [a["approver"] for a in recheck.load_approvals(mkb.cfg)] == ["person"]
    assert log_lines(mkb)[-1]["approver"] == "person"
    person_only(mkb)
    (mkb.root / "ran-F-0001").unlink()
    assert run(mkb, capsys, monkeypatch)[0] == 0 and ran(mkb) == ["F-0001"]


def test_recheck_person_approval_defaults_to_false_and_must_be_true_or_false(kb):
    assert kb.cfg.recheck_person_approval is False
    for value in ("1", '"yes"', '"true"'):
        kb.write("kblam.toml", KBLAM_TOML + f"recheck_person_approval = {value}\n" + NO_EMBEDDINGS + PROMPT_TOML)
        with pytest.raises(ConfigError, match=r"\[kb\] recheck_person_approval must be true or false"):
            load_config(root=kb.root)
    kb.write("kblam.toml", KBLAM_TOML + "recheck_person_approval = true\n" + NO_EMBEDDINGS + PROMPT_TOML)
    assert load_config(root=kb.root).recheck_person_approval is True


def test_the_hook_allows_an_agent_to_approve_and_still_denies_writing_the_approvals(kb, monkeypatch, capsys):
    """The command kblam prints is what the agent runs, so the PreToolUse hook must let it through, from
    either shell tool; writing the approvals file by hand stays denied, kblam included."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for tool_name, command in (("Bash", "kblam recheck F-0001 --approve 3f2a9c1b7e04"),
                               ("Bash", "cd repo && kblam recheck F-0001 --approve 3f2a9c1b7e04"),
                               ("PowerShell", "kblam recheck F-0001 --approve 3f2a9c1b7e04")):
        assert call("PreToolUse", tool(kb, tool_name, command=command), monkeypatch, capsys) == (0, None, "")
    for command in (f"kblam recheck F-0001 --approve 3f2a9c1b7e04 >> {APPROVALS}",
                    f"kblam recheck F-0001 --approve 3f2a9c1b7e04; echo x > {APPROVALS}"):
        code, answer, _ = call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys)
        assert code == 0 and "kblam recheck's approvals" in denied(answer)


# --- running ------------------------------------------------------------------------------------


SPAWN_SCRIPT = """\
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2); open('survived', 'w').write('x')"])
print("spawned", flush=True)
time.sleep(60)
"""


def test_a_check_past_the_timeout_is_killed_with_every_process_it_started(kb, capsys, monkeypatch):
    kb.write("kblam.toml", KBLAM_TOML + "recheck_timeout_seconds = 1\n" + NO_EMBEDDINGS + PROMPT_TOML)
    kb.write(f"{EVIDENCE}/spawn.py", SPAWN_SCRIPT)
    add_check(kb, "F-0001", f"{PY} {EVIDENCE}/spawn.py")
    start = time.monotonic()
    code, out, _ = run(kb, capsys, monkeypatch, answers=["y"])
    assert time.monotonic() - start < 30
    assert code == 1 and "F-0001 FAILED (timed out after 1 s; it and every process it started were killed)" in out
    assert "  | spawned\n" in out  # the grandchild had started before the timeout
    assert "raise [kb] recheck_timeout_seconds" in out
    assert log_lines(kb)[-1]["outcome"] == "timed_out"
    time.sleep(max(0.0, start + 3.5 - time.monotonic()))  # past the grandchild's two seconds
    assert not (kb.root / "survived").exists()


def test_a_command_that_cannot_run_is_a_failure(kb, capsys, monkeypatch):
    add_check(kb, "F-0001", "kblam-no-such-program --version")
    add_check(kb, "F-0002", 'python "unclosed')
    code, out, _ = run(kb, capsys, monkeypatch, answers=[])  # nothing is asked about a command that cannot run
    assert code == 1
    assert "kblam recheck: F-0001 could not run: kblam-no-such-program was not found on PATH\n" in out
    assert "kblam recheck: F-0002 could not run: its check: cannot be split into a program and arguments" in out
    assert out.endswith("2 check(s): 0 passed, 2 could not run. Load the kblam-write skill for how to fix this.\n")


def test_a_bare_program_name_is_looked_up_on_path_never_in_the_current_directory(tmp_path, monkeypatch):
    here = tmp_path / "here"
    here.mkdir()
    program = here / ("tool.exe" if sys.platform == "win32" else "tool")
    program.write_bytes(b"#!/bin/sh\nexit 0\n")
    program.chmod(0o755)
    monkeypatch.chdir(here)
    assert find_program("tool", tmp_path, path="") == (None, "tool was not found on PATH")
    assert find_program("tool", tmp_path, path=".")[0] is None  # a relative PATH entry is the current directory
    assert find_program("tool", tmp_path, path=str(here)) == (str(program), None)
    assert find_program(f"here/{program.name}", tmp_path) == (str(program), None)  # relative to the root


def test_on_windows_pathext_applies_and_a_batch_file_is_refused(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv.exe").write_bytes(b"MZ")
    (bin_dir / "npm.cmd").write_bytes(b"@echo off\n")
    lookup = functools.partial(find_program, root=tmp_path, windows=True, path=str(bin_dir),
                               pathext=".COM;.EXE;.BAT;.CMD")
    assert lookup("uv") == (str(bin_dir / "uv.exe"), None)
    assert lookup("uv.exe") == (str(bin_dir / "uv.exe"), None)
    for name in ("npm", "npm.cmd"):
        program, why = lookup(name)
        assert program is None and "is a batch file" in why and "cmd.exe" in why


def test_the_approval_pins_each_repository_file_the_command_names(kb, tmp_path):
    kb.write(f"{EVIDENCE}/ratio.py", "print(1)\n")
    outside = tmp_path / "outside.py"
    outside.write_text("print(2)\n", encoding="utf-8")
    argv = ["python", f"{EVIDENCE}/ratio.py", f"--log={EVIDENCE}/log.txt", EVIDENCE, str(outside), "absent.txt",
            "-v", "python"]
    files = named_files(kb.root, argv)
    assert list(files) == [f"{EVIDENCE}/log.txt", f"{EVIDENCE}/ratio.py"]  # not the directory, nor outside
    assert files[f"{EVIDENCE}/ratio.py"] == hashlib.sha256(b"print(1)\n").hexdigest()
    assert list(named_files(kb.root, [f"./{EVIDENCE}/ratio.py"])) == [f"{EVIDENCE}/ratio.py"]  # the program


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows resolves a name without a PATHEXT suffix")
def test_the_program_pathext_resolves_to_is_pinned(kb, capsys, monkeypatch):
    """`tools/run` runs `tools/run.exe` on Windows, which no argument names: the approval pins the file that
    runs, so a changed program file is reported as changed since approval and needs approving again."""
    kb.write("tools/run.exe", b"MZ first\n")
    add_check(kb, "F-0001", "tools/run --version")
    code, out, _ = run(kb, capsys, monkeypatch)
    check = recheck.collect(kb.cfg, ["F-0001"])[0][0]
    assert (code, check.program, list(check.files)) == (1, str(kb.root / "tools" / "run.exe"), ["tools/run.exe"])
    assert "  pinned:  tools/run.exe\n" in out
    first = recheck.approval_digest(check)
    recheck.record_approval(kb.cfg, check)
    kb.write("tools/run.exe", b"MZ second\n")
    code, out, _ = run(kb, capsys, monkeypatch)
    assert code == 1 and "F-0001 not run: not approved on this machine (tools/run.exe changed since approval)" in out
    assert recheck.approval_digest(recheck.collect(kb.cfg, ["F-0001"])[0][0]) != first


def test_the_program_a_path_entry_inside_the_repository_resolves_to_is_pinned(kb, capsys, monkeypatch):
    """A bare name is looked up in PATH, which may name a directory inside the repository: the file that then
    runs is a repository file no argument names, so the approval pins it (SPEC §7)."""
    name = "tool.exe" if sys.platform == "win32" else "tool"
    kb.write(f"bin/{name}", b"MZ first\n" if sys.platform == "win32" else "#!/bin/sh\nexit 0\n")
    (kb.root / "bin" / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(kb.root / "bin"))
    add_check(kb, "F-0001", "tool --version")
    code, out, _ = run(kb, capsys, monkeypatch)
    check = recheck.collect(kb.cfg, ["F-0001"])[0][0]
    assert (code, check.program, list(check.files)) == (1, str(kb.root / "bin" / name), [f"bin/{name}"])
    assert f"  pinned:  bin/{name}\n" in out
    first = recheck.approval_digest(check)
    recheck.record_approval(kb.cfg, check)
    kb.write(f"bin/{name}", b"MZ second\n" if sys.platform == "win32" else "#!/bin/sh\nexit 1\n")
    code, out, _ = run(kb, capsys, monkeypatch)
    assert code == 1 and f"F-0001 not run: not approved on this machine (bin/{name} changed since approval)" in out
    assert recheck.approval_digest(recheck.collect(kb.cfg, ["F-0001"])[0][0]) != first


def test_shown_escapes_everything_but_printable_ascii():
    assert shown("uv run python evidence/x.py --n=3") == "uv run python evidence/x.py --n=3"
    assert shown("echo \x1b[2K\rok") == "'echo \\x1b[2K\\rok'"
    assert shown("python \u202etxt.py") == "'python \\u202etxt.py'"
    assert shown("pyth\u043en x.py") == "'pyth\\u043en x.py'"  # a Cyrillic o that looks like a Latin one


class Stream:
    def __init__(self, tty: bool):
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


@pytest.mark.parametrize("stdin, stdout, expected", [(True, True, True), (True, False, False),
                                                     (False, True, False), (None, True, False)])
def test_a_person_is_asked_only_when_stdin_and_stdout_are_a_terminal(monkeypatch, stdin, stdout, expected):
    monkeypatch.setattr(sys, "stdin", None if stdin is None else Stream(stdin))
    monkeypatch.setattr(sys, "stdout", Stream(stdout))
    assert recheck.at_terminal() is expected


# --- what else holds ----------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name, tool_input", [
    ("Write", {"file_path": APPROVALS, "content": "{}"}),
    ("Edit", {"file_path": APPROVALS}),
    ("Bash", {"command": f"echo '{{}}' >> {APPROVALS}"}),
    ("Bash", {"command": "rm -rf .git/kblam"}),
    ("PowerShell", {"command": f"Add-Content {APPROVALS} x"}),
])
def test_an_agent_cannot_write_the_approvals(kb, monkeypatch, capsys, tool_name, tool_input):
    """The approvals are safe from agents because the PreToolUse hook protects their folder (SPEC §8)."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    code, answer, _ = call("PreToolUse", tool(kb, tool_name, **tool_input), monkeypatch, capsys)
    reason = denied(answer)
    assert code == 0 and "That folder holds the check: commands approved on this machine" in reason
    assert "an agent approves a command by running kblam recheck with --approve and the digest of the " \
           "block printed for it" in reason
    assert "An agent never writes that file itself" in reason


def test_only_recheck_runs_a_check(mkb, capsys, monkeypatch):
    """SPEC §7: put, validate, check, audit and the Stop hook read findings with a check: but never run it."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    add_check(mkb, "F-0001", mark("F-0001"))
    staged = mkb.write(".kblam/staging/F-0002-staged.md",
                       finding_text("F-0002", CLAIMS["F-0002"], extra=f"check: {json.dumps(mark('F-0002'))}\n"))
    root = ["--root", str(mkb.root)]
    for args in (["put", str(staged)], ["validate"], ["check", "F-0001", "F-0002"], ["audit"],
                 ["validate", "--record"]):
        assert cli.main(root + args) == 0, args
    # a change outside kblam, so the Stop hook checks and validates the tree
    mkb.write("findings/calibration/F-0001-check-0001.md",
              finding_text("F-0001", CLAIMS["F-0001"] + " It was measured twice.",
                           extra=f"check: {json.dumps(mark('F-0001'))}\n"))
    assert call("Stop", stop(mkb), monkeypatch, capsys)[0] == 0
    assert ran(mkb) == []


def test_a_finding_that_cannot_be_read_fails_the_run(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    mkb.write("findings/calibration/F-0002-broken.md", "no frontmatter here\n")
    code, out, _ = run(mkb, capsys, monkeypatch, answers=["y"])
    assert code == 1 and ran(mkb) == ["F-0001"]
    assert ("kblam recheck: findings/calibration/F-0002-broken.md cannot be read, so its check: (if it has one) "
            "was not run; run kblam validate\n") in out
    assert out.endswith("1 check(s): 1 passed, 1 finding(s) not considered. Load the kblam-write skill for how "
                        "to fix this.\n")
    code, out, err = run(mkb, capsys, monkeypatch, "F-0002")
    assert code == 1 and "F-0002-broken.md cannot be read; run kblam validate and fix that first" in err


def test_an_unreadable_approvals_file_refuses_the_run(mkb, capsys, monkeypatch):
    add_check(mkb, "F-0001", mark("F-0001"))
    mkb.write(APPROVALS, "not json\n")
    code, out, err = run(mkb, capsys, monkeypatch)
    assert (code, out, ran(mkb)) == (1, "", [])
    assert f"{APPROVALS}:1 cannot be read; only kblam writes it" in err


def test_with_no_check_commands_there_is_nothing_to_do(kb, capsys, monkeypatch):
    kb.add("F-0001", "plain", CLAIMS["F-0001"])
    assert run(kb, capsys, monkeypatch) == (0, "kblam recheck: no finding has a check: command\n", "")


def test_recheck_timeout_seconds_defaults_to_600_and_must_be_positive(kb):
    assert kb.cfg.recheck_timeout_seconds == 600
    for value in ("0", "-5"):
        kb.write("kblam.toml", KBLAM_TOML + f"recheck_timeout_seconds = {value}\n" + NO_EMBEDDINGS + PROMPT_TOML)
        with pytest.raises(ConfigError, match=r"\[kb\] recheck_timeout_seconds must be > 0"):
            load_config(root=kb.root)
    kb.write("kblam.toml", KBLAM_TOML + "recheck_timeout_seconds = 2.5\n" + NO_EMBEDDINGS + PROMPT_TOML)
    assert load_config(root=kb.root).recheck_timeout_seconds == 2.5


# --- what keeps a command someone else wrote from running (SPEC §8.3) -------------------------------


ENV_SCRIPT = """\
import os, sys
print(*(name + "=" + os.environ.get(name, "absent") for name in sys.argv[1:]))
"""


def test_a_check_runs_without_the_variable_the_jev_key_is_read_from(kb, capsys, monkeypatch):
    kb.write(f"{EVIDENCE}/env.py", ENV_SCRIPT)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("KBLAM_TEST_OTHER", "kept")
    add_check(kb, "F-0001", f"{PY} {EVIDENCE}/env.py OPENROUTER_API_KEY KBLAM_TEST_OTHER")
    code, out, _ = run(kb, capsys, monkeypatch, answers=["y"])
    assert code == 0 and "with stdin closed and without OPENROUTER_API_KEY, for at most 600 s" in out
    output = (kb.root / ".kblam/recheck/F-0001.log").read_text(encoding="utf-8")
    assert output.split() == ["OPENROUTER_API_KEY=absent", "KBLAM_TEST_OTHER=kept"]


def test_the_variable_removed_is_the_one_this_machine_reads_the_key_from(kb, capsys, monkeypatch, home):
    (home / "kblam").mkdir()
    (home / "kblam" / "config.toml").write_text('[jev]\nkey_env = "KBLAM_TEST_KEY"\n', encoding="utf-8")
    kb.write(f"{EVIDENCE}/env.py", ENV_SCRIPT)
    monkeypatch.setenv("KBLAM_TEST_KEY", "sk-or-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-the-key-here")
    add_check(kb, "F-0001", f"{PY} {EVIDENCE}/env.py KBLAM_TEST_KEY OPENROUTER_API_KEY")
    assert run(kb, capsys, monkeypatch, answers=["y"])[0] == 0
    output = (kb.root / ".kblam/recheck/F-0001.log").read_text(encoding="utf-8")
    assert output.split() == ["KBLAM_TEST_KEY=absent", "OPENROUTER_API_KEY=not-the-key-here"]


def test_an_approval_a_commit_brings_is_never_used(mkb, capsys, monkeypatch, tmp_path):
    """A pull writes tracked files over ignored ones, so approvals kept under .kblam/ could come from anyone
    who can push: here someone approves on their machine, copies the approvals to where kblam once kept them
    and commits them past .gitignore. A clone refuses to act on the tracked .kblam/ file, and once it is
    untracked, still runs nothing: its approvals are in its own git directory, which no commit reaches."""
    add_check(mkb, "F-0001", mark("F-0001"))
    assert run(mkb, capsys, monkeypatch, answers=["y"])[0] == 0
    (mkb.root / "ran-F-0001").unlink()
    mkb.write(".kblam/recheck-approved.jsonl", (mkb.root / APPROVALS).read_bytes())
    mkb.write(".gitignore", ".kblam/\nran-*\n")
    git(mkb.root, "add", "-A")
    git(mkb.root, "add", "-f", ".kblam/recheck-approved.jsonl")
    git(mkb.root, "commit", "-qm", "findings")

    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(mkb.root), str(clone))
    assert (clone / ".kblam/recheck-approved.jsonl").is_file() and not (clone / APPROVALS).exists()
    monkeypatch.setattr(recheck, "at_terminal", AT_TERMINAL)  # an agent's shell: no terminal
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("no terminal: nothing may be asked"))
    capsys.readouterr()
    assert cli.main(["--root", str(clone), "recheck"]) == 1
    assert "git tracks .kblam/recheck-approved.jsonl, under .kblam/" in capsys.readouterr().err

    git(clone, "rm", "-q", "-r", "--cached", ".kblam")  # untracked, but the planted file is still there
    assert cli.main(["--root", str(clone), "recheck"]) == 1
    out = capsys.readouterr().out
    assert "F-0001 not run: not approved on this machine (new command)" in out
    assert not (clone / "ran-F-0001").exists()


def test_outside_a_git_work_tree_recheck_runs_nothing(kb, capsys, monkeypatch):
    import shutil

    shutil.rmtree(kb.root / ".git")
    if git_common_dir(kb.root) is not None:
        pytest.skip("the temporary directory is inside a git work tree")
    kb.write(MARK, MARK_SCRIPT)
    add_check(kb, "F-0001", mark("F-0001"))
    code, out, err = run(kb, capsys, monkeypatch, answers=[])
    assert (code, out, ran(kb)) == (1, "", [])
    assert "is not in a git work tree. kblam recheck keeps the approvals in the repository's git " in err
    assert "leave the finding as it is and tell the user" in err


def test_linked_work_trees_share_one_clones_approvals(kb, tmp_path):
    git(kb.root, "commit", "-q", "--allow-empty", "-m", "start")
    linked = tmp_path / "linked"
    git(kb.root, "worktree", "add", "-q", str(linked))
    assert git_common_dir(linked) == git_common_dir(kb.root / "findings") == (kb.root / ".git").resolve()


def test_a_git_file_naming_its_directory_is_followed(tmp_path):
    """A submodule's work tree has a .git file naming its git directory, which has no commondir."""
    (tmp_path / "top" / ".git" / "modules" / "sub").mkdir(parents=True)
    (tmp_path / "top" / "sub").mkdir()
    (tmp_path / "top" / "sub" / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")
    assert git_common_dir(tmp_path / "top" / "sub") == (tmp_path / "top" / ".git" / "modules" / "sub").resolve()


needs_symlinks = pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need a privilege on Windows")


@needs_symlinks
def test_a_link_at_a_checks_output_file_is_replaced_not_written_through(mkb, capsys, monkeypatch, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n", encoding="utf-8")
    add_check(mkb, "F-0001", mark("F-0001"))
    output = mkb.root / ".kblam/recheck/F-0001.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.symlink_to(victim)
    assert run(mkb, capsys, monkeypatch, answers=["y"])[0] == 0
    assert victim.read_text(encoding="utf-8") == "keep\n"
    assert not output.is_symlink() and output.read_text(encoding="utf-8").strip() == "ratio 1.0017 for F-0001"
    assert [p.name for p in output.parent.iterdir()] == ["F-0001.log"]  # no temporary file left behind


@needs_symlinks
@pytest.mark.parametrize("name", ["recheck.jsonl", "recheck"])
def test_a_link_where_recheck_writes_refuses_the_run(mkb, capsys, monkeypatch, tmp_path, name):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    add_check(mkb, "F-0001", mark("F-0001"))
    (mkb.root / ".kblam" / name).symlink_to(elsewhere / "x" if name == "recheck.jsonl" else elsewhere)
    code, out, err = run(mkb, capsys, monkeypatch, answers=[])
    assert (code, out, ran(mkb)) == (1, "", [])
    assert f".kblam/{name} is a symbolic link, and kblam recheck writes there only to files of its own" in err
    assert list(elsewhere.iterdir()) == []


@needs_symlinks
def test_a_link_at_the_approvals_file_refuses_the_run(mkb, capsys, monkeypatch, tmp_path):
    (mkb.root / ".git" / "kblam").mkdir()
    (mkb.root / APPROVALS).symlink_to(tmp_path / "approvals.jsonl")
    add_check(mkb, "F-0001", mark("F-0001"))
    code, out, err = run(mkb, capsys, monkeypatch)
    assert (code, out, ran(mkb)) == (1, "", [])
    assert f"{APPROVALS} is a symbolic link; only kblam writes that file" in err
