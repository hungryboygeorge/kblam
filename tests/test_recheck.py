"""`kblam recheck [F-…]` (SPEC §7, §8.3; M6.10): the findings' check: commands, run through the shell from
the repository root without the Jev key's variable, and only once a person approved them on this machine
at a terminal. The commands here are bash, so a test that runs one is skipped where recheck would not use
bash. Nothing here touches the network."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import time

import pytest

from kblam import cli, init, recheck

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML

SHELL = init._hook_shell()  # the shell recheck runs commands through
needs_bash = pytest.mark.skipif(SHELL is None or SHELL[0] != "bash", reason="the commands here are bash")

CLAIMS = {
    "F-0001": "The pump motor reaches steady output after 90 seconds of warm-up at 4000 rpm.",
    "F-0002": "The sensor's two curve types agree to about 0.1%, so they are not two analog gains.",
    "F-0003": "The bus handshake starts with the bytes 0x55 0xAA followed by a length field.",
}
QUESTION = "Run and approve this command on this machine? [y/N] "
NOT_A_TERMINAL = (
    "kblam recheck: a person approves each check: command on this machine by running kblam recheck at an "
    "interactive terminal, and this is not one, so the commands not approved yet were skipped. The commands "
    "come from committed findings, which any contributor can write. An agent asks the user to run kblam "
    "recheck.")


def not_at_terminal(monkeypatch) -> None:
    """stdin is not a terminal, as in an agent's Bash or PowerShell tool, and nothing may ask."""
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail(f"asked with no terminal: {prompt}"))


@pytest.fixture(autouse=True)
def no_terminal(monkeypatch):
    not_at_terminal(monkeypatch)


def at_terminal(monkeypatch, *answers: str) -> list[str]:
    """A person at an interactive terminal who gives `answers` in turn; returns the questions asked."""
    asked, replies = [], list(answers)

    class Terminal:
        def isatty(self) -> bool:
            return True

    def reply(prompt: str = "") -> str:
        asked.append(prompt)
        return replies.pop(0)

    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr("builtins.input", reply)
    return asked


def add(kb, finding_id: str, command: str | None = None):
    """A finding with `command` as its check: (a JSON string is a YAML double-quoted scalar)."""
    extra = "" if command is None else f"check: {json.dumps(command)}\n"
    return kb.add(finding_id, "note", CLAIMS[finding_id], extra=extra)


def approve(kb, *commands: str) -> None:
    recheck.record_approvals(kb.cfg, [recheck.command_digest(c) for c in commands])


def run(kb, capsys, *ids: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = cli.main(["--root", str(kb.root), "recheck", *ids])
    out, err = capsys.readouterr()
    return code, out, err


# --- running the commands ---------------------------------------------------------------------------


@needs_bash
def test_an_approved_check_that_exits_0_passes(kb, capsys, monkeypatch):
    command = "test -f evidence/2026-09-22-ratio/log.txt"  # true only in the repository root
    add(kb, "F-0001", command)
    approve(kb, command)
    monkeypatch.chdir(kb.root.parent)
    assert run(kb, capsys) == (0, "pass F-0001\nkblam recheck: 1 passed\n", "")


@needs_bash
def test_a_failing_check_shows_its_exit_status_and_the_end_of_its_output(kb, capsys):
    command = "for i in {1..30}; do echo line $i; done; echo 'ratio 1.0200, expected 1.0017' >&2; exit 3"
    add(kb, "F-0001", command)
    approve(kb, command)
    code, out, _ = run(kb, capsys)
    assert code == 1
    assert out.splitlines() == ["FAIL F-0001 (exit 3)", "  ... 11 earlier line(s) not shown",
                                *(f"  line {i}" for i in range(12, 31)), "  ratio 1.0200, expected 1.0017",
                                "kblam recheck: 1 failed"]


@needs_bash
def test_a_check_past_the_timeout_is_killed_with_everything_it_started(kb, capsys, monkeypatch):
    monkeypatch.setattr(recheck, "TIMEOUT_SECONDS", 0.3)
    command = "(sleep 0.6; touch late) & wait"  # a child that outlives a kill of the shell alone
    add(kb, "F-0001", command)
    approve(kb, command)
    start = time.monotonic()
    assert run(kb, capsys) == (1, "TIMEOUT F-0001\nkblam recheck: 1 timed out (limit 0.3 s)\n", "")
    assert time.monotonic() - start < 3
    time.sleep(0.6)
    assert not (kb.root / "late").exists()


@needs_bash
def test_a_command_the_os_cannot_start_fails(kb, capsys):
    command = "echo a\x00b"  # a NUL byte cannot be passed to a process
    add(kb, "F-0001", command)
    approve(kb, command)
    code, out, _ = run(kb, capsys)
    assert code == 1
    assert out.startswith("FAIL F-0001 (not run: embedded null ")  # "byte" on POSIX, "character" on Windows
    assert out.endswith("\nkblam recheck: 1 failed\n")


@needs_bash
def test_the_jev_key_variable_is_removed_and_the_rest_of_the_environment_kept(kb, capsys, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-a-real-key")
    monkeypatch.setenv("KBLAM_TEST_VISIBLE", "yes")
    command = 'test -z "${OPENROUTER_API_KEY+set}" && test "$KBLAM_TEST_VISIBLE" = yes'
    add(kb, "F-0001", command)
    approve(kb, command)
    assert run(kb, capsys)[:2] == (0, "pass F-0001\nkblam recheck: 1 passed\n")


@needs_bash
def test_the_variable_the_machine_config_names_is_the_one_removed(kb, capsys, monkeypatch, home):
    (home / "kblam").mkdir()
    (home / "kblam" / "config.toml").write_text('[jev]\nkey_env = "KBLAM_TEST_JEV_KEY"\n', encoding="utf-8")
    monkeypatch.setenv("KBLAM_TEST_JEV_KEY", "sk-or-test-not-a-real-key")
    command = 'test -z "${KBLAM_TEST_JEV_KEY+set}"'
    add(kb, "F-0001", command)
    approve(kb, command)
    assert run(kb, capsys)[:2] == (0, "pass F-0001\nkblam recheck: 1 passed\n")


@needs_bash
def test_the_default_key_variable_is_removed_when_the_jev_settings_do_not_load(kb, capsys, monkeypatch):
    kb.write("kblam.toml", KBLAM_TOML + NO_EMBEDDINGS + "no_such_key = 1\n" + PROMPT_TOML)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-a-real-key")
    command = 'test -z "${OPENROUTER_API_KEY+set}"'
    add(kb, "F-0001", command)
    approve(kb, command)
    assert run(kb, capsys)[:2] == (0, "pass F-0001\nkblam recheck: 1 passed\n")


# --- approval -----------------------------------------------------------------------------------------


def test_an_unapproved_check_is_skipped_without_a_terminal(kb, capsys):
    add(kb, "F-0001", "touch ran")
    code, out, err = run(kb, capsys)
    assert (code, err) == (1, "")
    assert out.splitlines() == ["skipped F-0001 (not approved)", "kblam recheck: 1 skipped", NOT_A_TERMINAL]
    assert not (kb.root / "ran").exists()
    assert not (kb.root / ".kblam" / "checks-approved").exists()


@needs_bash
def test_a_person_at_a_terminal_approves_a_check_and_it_runs(kb, capsys, monkeypatch):
    add(kb, "F-0001", "touch ran")
    asked = at_terminal(monkeypatch, "y")
    code, out, _ = run(kb, capsys)
    assert code == 0 and asked == [QUESTION]
    assert out == ("kblam recheck: the check: command of F-0001 is not approved on this machine:\n"
                   "  touch ran\n"
                   "pass F-0001\n"
                   "kblam recheck: 1 passed\n")
    assert (kb.root / "ran").exists()
    digest = hashlib.sha256(b"touch ran").hexdigest()
    assert (kb.root / ".kblam" / "checks-approved").read_text(encoding="ascii") == f"{digest}\n"

    not_at_terminal(monkeypatch)  # approved on this machine: an agent's shell runs it without asking
    assert run(kb, capsys) == (0, "pass F-0001\nkblam recheck: 1 passed\n", "")

    (kb.root / "findings" / "calibration" / "F-0001-note.md").unlink()
    add(kb, "F-0001", "touch ran again")  # a changed command is a new one
    code, out, _ = run(kb, capsys)
    assert code == 1 and out.startswith("skipped F-0001 (not approved)\n")


def test_answering_no_skips_the_check_and_approves_nothing(kb, capsys, monkeypatch):
    add(kb, "F-0001", "touch ran")
    at_terminal(monkeypatch, "n")
    code, out, _ = run(kb, capsys)
    assert code == 1
    assert out.endswith("  touch ran\nskipped F-0001 (not approved)\nkblam recheck: 1 skipped\n")
    assert not (kb.root / "ran").exists()
    assert not (kb.root / ".kblam" / "checks-approved").exists()


def test_a_command_several_findings_share_is_asked_about_once(kb, capsys, monkeypatch):
    add(kb, "F-0001", "exit 0")
    add(kb, "F-0002", "exit 0")
    asked = at_terminal(monkeypatch, "n")
    code, out, _ = run(kb, capsys)
    assert code == 1 and asked == [QUESTION]
    assert out == ("kblam recheck: the check: command of F-0001, F-0002 is not approved on this machine:\n"
                   "  exit 0\n"
                   "skipped F-0001 (not approved)\n"
                   "skipped F-0002 (not approved)\n"
                   "kblam recheck: 2 skipped\n")


def test_the_question_shows_control_characters_escaped(kb, capsys, monkeypatch):
    """A carriage return or an escape sequence would let a command hide its start from the person asked."""
    add(kb, "F-0001", "touch hidden\r\x1b[2Kecho harmless")
    at_terminal(monkeypatch, "n")
    code, out, _ = run(kb, capsys)
    assert "\n  touch hidden\\r\\x1b[2Kecho harmless\n" in out
    assert "\r" not in out and "\x1b" not in out
    assert not list(kb.root.glob("hidden*"))


# --- selection ----------------------------------------------------------------------------------------


def test_with_no_check_commands_there_is_nothing_to_run(kb, capsys):
    nothing = "kblam recheck: no finding in findings/ has a check: command; nothing to run\n"
    assert run(kb, capsys) == (0, nothing, "")
    add(kb, "F-0001")
    assert run(kb, capsys) == (0, nothing, "")
    assert run(kb, capsys, "F-0001") == (
        0, "kblam recheck: F-0001 has no check: command\nkblam recheck: nothing to run\n", "")


@needs_bash
def test_only_the_named_findings_are_rechecked(kb, capsys):
    add(kb, "F-0001", "touch one")
    add(kb, "F-0002", "touch two")
    add(kb, "F-0003")
    approve(kb, "touch one", "touch two")
    assert run(kb, capsys, "F-0002", "F-0003", "F-0002") == (
        0, "kblam recheck: F-0003 has no check: command\npass F-0002\nkblam recheck: 1 passed\n", "")
    assert (kb.root / "two").exists() and not (kb.root / "one").exists()
    assert run(kb, capsys) == (0, "pass F-0001\npass F-0002\nkblam recheck: 2 passed\n", "")


def test_an_unknown_or_malformed_id_is_an_error(kb, capsys):
    add(kb, "F-0001", "touch ran")
    approve(kb, "touch ran")
    assert run(kb, capsys, "F-12") == (1, "", "kblam recheck: 'F-12' is not a finding ID like F-0137\n")
    assert run(kb, capsys, "F-0001", "F-0099") == (
        1, "", "kblam recheck: F-0099 is not a readable finding in findings/; run kblam validate\n")
    assert not (kb.root / "ran").exists()
