"""`kblam recheck [F-…]`: run the findings' `check:` commands (SPEC §4, §7, §8.3; M6.10).

A finding's optional `check:` is a command whose re-run reproduces the finding's key number: it compares
what it computes with the finding and exits non-zero on a mismatch (§4). recheck runs the command of each
named finding, or of every finding that has one, from the repository root through bash, or through
`pwsh -NoProfile -Command` where there is no bash (the shell init's hook check finds, §7.1), with a 600 s
timeout and without the environment variable the Jev API key is read from. A check passes when its command
exits 0.

The commands come from committed findings, which any contributor can write, and they run with the user's
rights (§8.3). So a command runs only once a person has approved it on this machine: the sha256 of each
approved command is a line of `.kblam/checks-approved`, which the deny hooks protect like the rest of
`.kblam/`. At an interactive terminal recheck shows each command nobody has approved and asks; an agent's
shell is not a terminal, so there such a command is listed as skipped, and a skip fails the run as a
failed check does.
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass

from kblam.config import Config, ConfigError
from kblam.finding import ID_RE
from kblam.init import _hook_shell
from kblam.jev import DEFAULT_JEV, jev_settings
from kblam.lock import kb_lock
from kblam.store import atomic_write
from kblam.view import load_view

APPROVALS_NAME = "checks-approved"  # .kblam/checks-approved: the sha256 of each approved command, one a line
TIMEOUT_SECONDS = 600               # per command (SPEC §7)
KILL_WAIT_SECONDS = 5               # how long a killed command's output pipe may take to close
OUTPUT_LINES = 20                   # how much of a failed command's output is shown: its last lines
QUESTION = "Run and approve this command on this machine? [y/N] "

EXIT_OK = 0
EXIT_FAILED = 1


class RecheckError(Exception):
    """A request recheck will not carry out (an unknown finding ID, no shell); the message says why."""


@dataclass(frozen=True)
class Check:
    finding_id: str
    command: str

    @property
    def digest(self) -> str:
        return command_digest(self.command)


def command_digest(command: str) -> str:
    """What `.kblam/checks-approved` records for an approved command: the sha256 of its UTF-8 bytes."""
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def _approvals_path(cfg: Config):
    return cfg.state_dir / APPROVALS_NAME


def approvals(cfg: Config) -> list[str]:
    path = _approvals_path(cfg)
    if not path.is_file():
        return []
    text = path.read_text(encoding="ascii", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


def record_approvals(cfg: Config, digests: list[str]) -> None:
    """Add `digests` to `.kblam/checks-approved`, creating it if needed; under the lock, as approve-config
    records its approvals, so two people approving at once cannot lose one."""
    with kb_lock(cfg, "recheck"):
        known = approvals(cfg)
        new = [d for d in dict.fromkeys(digests) if d not in known]
        if new:
            atomic_write(_approvals_path(cfg), "".join(f"{d}\n" for d in [*known, *new]).encode("ascii"))


def _command(finding) -> str | None:
    """The finding's `check:` command: a non-empty string (K1 reports any other value)."""
    value = finding.meta.get("check")
    return str(value) if isinstance(value, str) and value.strip() else None


def check_environment(cfg: Config) -> dict[str, str]:
    """os.environ without the variable the Jev API key is read from: `[jev] key_env`, which the per-machine
    config may change, or its default when the settings do not load. A check: command is anyone's code."""
    try:
        name = jev_settings(cfg).key_env
    except ConfigError:
        name = DEFAULT_JEV["key_env"]
    if sys.platform == "win32":  # variable names are case-insensitive there
        return {k: v for k, v in os.environ.items() if k.upper() != name.upper()}
    return {k: v for k, v in os.environ.items() if k != name}


def _shown(line: str) -> str:
    """`line` with every unprintable character escaped: a carriage return, an escape sequence or a bidi
    control in a command would otherwise hide part of it from the person approving it."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in line)


def _ask(cfg: Config, unapproved: list[Check]) -> set[str]:
    """Ask the person at the terminal about each command nobody approved on this machine, once per distinct
    command, and record the ones approved. Returns their digests."""
    by_digest: dict[str, list[Check]] = {}
    for check in unapproved:
        by_digest.setdefault(check.digest, []).append(check)
    approved = []
    for digest, group in by_digest.items():
        print(f"kblam recheck: the check: command of {', '.join(c.finding_id for c in group)} is not approved "
              f"on this machine:")
        for line in group[0].command.rstrip("\n").split("\n"):  # only \n: the shell reads \r as a character
            print(f"  {_shown(line)}")
        try:
            answer = input(QUESTION)
        except EOFError:
            answer = ""
            print()
        if answer.strip().lower() in ("y", "yes"):
            approved.append(digest)
    if approved:
        record_approvals(cfg, approved)
    return set(approved)


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill the shell and every process it started: its process group on POSIX (it runs in a session of its
    own), its process tree on Windows. Killing the shell alone would leave a timed-out child running."""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
        except OSError:
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:  # the group has gone already
            pass
    process.kill()


def run_command(argv: list[str], cfg: Config, env: dict[str, str]) -> tuple[int | None, str]:
    """(exit status, its stdout and stderr interleaved) of one command run from the repository root with no
    stdin. The status is None when the command ran past TIMEOUT_SECONDS; it has been killed by then, with
    every process it started."""
    process = subprocess.Popen(argv, cwd=cfg.repo_root, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)  # POSIX: its own process group; ignored on Windows
    try:
        output, _ = process.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.communicate(timeout=KILL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            pass  # a process that left the group still holds the pipe; the output is not shown anyway
        return None, ""
    except BaseException:  # an interrupt: the command, in its own session, did not get it
        _kill_tree(process)
        raise
    return process.returncode, output.decode("utf-8", errors="replace")


def _tail(output: str) -> list[str]:
    lines = [line.rstrip() for line in output.rstrip().splitlines()]
    shown = lines[-OUTPUT_LINES:]
    cut = len(lines) - len(shown)
    return ([f"... {cut} earlier line(s) not shown"] if cut else []) + shown


def _select(cfg: Config, ids: list[str]) -> list[Check]:
    """The checks of the named findings, in the order named, or of every finding, in ID order. A finding
    counts when it parses and its ID is its own, as for kblam check; a named one that does not is an error."""
    findings = load_view(cfg).findings
    counts = Counter(f.file_id for f in findings)
    readable = {f.file_id: f for f in findings if f.ok and counts[f.file_id] == 1}
    for finding_id in ids:
        if not ID_RE.match(finding_id):
            raise RecheckError(f"{finding_id!r} is not a finding ID like F-0137")
        if finding_id not in readable:
            raise RecheckError(f"{finding_id} is not a readable finding in {cfg.findings_dir}/; "
                               f"run kblam validate")
    if not ids and len(readable) < len(findings):
        print(f"kblam recheck: {len(findings) - len(readable)} finding file(s) in {cfg.findings_dir}/ do not "
              f"parse or share an ID, so their check: commands were not run; run kblam validate")
    checks = []
    for finding in [readable[i] for i in dict.fromkeys(ids)] if ids else readable.values():
        command = _command(finding)
        if command is not None:
            checks.append(Check(finding.file_id, command))
        elif ids:
            print(f"kblam recheck: {finding.file_id} has no check: command")
    return checks


def recheck(cfg: Config, ids: list[str]) -> int:
    """`kblam recheck [F-…]`: print one line per check and return the exit status: 1 when a check failed,
    timed out or was skipped, else 0 (also when there is no check to run)."""
    checks = _select(cfg, ids)
    if not checks:
        print("kblam recheck: " + ("nothing to run" if ids else
                                   f"no finding in {cfg.findings_dir}/ has a check: command; nothing to run"))
        return EXIT_OK

    approved = set(approvals(cfg))
    terminal = sys.stdin is not None and sys.stdin.isatty()
    unapproved = [c for c in checks if c.digest not in approved]
    if unapproved and terminal:
        approved |= _ask(cfg, unapproved)
    argv, env = [], {}
    if any(c.digest in approved for c in checks):
        shell = _hook_shell()
        if shell is None:
            raise RecheckError("neither bash nor pwsh is on PATH, and check: commands run through one of them")
        argv, env = shell[1], check_environment(cfg)

    outcome = Counter()
    for check in checks:
        if check.digest not in approved:
            print(f"skipped {check.finding_id} (not approved)", flush=True)
            outcome["skipped"] += 1
            continue
        try:
            status, output = run_command([*argv, check.command], cfg, env)
        except (OSError, ValueError) as exc:  # the OS would not start it: a NUL byte, an over-long command
            print(f"FAIL {check.finding_id} (not run: {exc})", flush=True)
            outcome["failed"] += 1
            continue
        if status == 0:
            print(f"pass {check.finding_id}", flush=True)
            outcome["passed"] += 1
        elif status is None:
            print(f"TIMEOUT {check.finding_id}", flush=True)
            outcome["timed out"] += 1
        else:
            lines = [f"FAIL {check.finding_id} (exit {status})", *(f"  {line}" for line in _tail(output))]
            print("\n".join(lines), flush=True)
            outcome["failed"] += 1

    summary = [("passed", "passed"), ("failed", "failed"),
               ("timed out", f"timed out (limit {TIMEOUT_SECONDS:g} s)"), ("skipped", "skipped")]
    print("kblam recheck: " + ", ".join(f"{outcome[key]} {text}" for key, text in summary if outcome[key]))
    if outcome["skipped"] and not terminal:
        print("kblam recheck: a person approves each check: command on this machine by running kblam "
              "recheck at an interactive terminal, and this is not one, so the commands not approved yet were "
              "skipped. The commands come from committed findings, which any contributor can write. An agent "
              "asks the user to run kblam recheck.")
    return EXIT_FAILED if outcome["failed"] or outcome["timed out"] or outcome["skipped"] else EXIT_OK
