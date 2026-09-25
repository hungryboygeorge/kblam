"""M6: `kblam hook` (PreToolUse deny, Stop/SubagentStop block), the skill pointer on every
write-stopping message, and the installable files under src/kblam/assets/. Jev is the fake transport
from test_check; nothing here touches the network."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from kblam import check, cli, entry
from kblam.lock import kb_lock
from kblam.store import edit_finding
from kblam.treehash import read_tree_hash

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, finding_text
from test_check import E1, N, jkb, quantity, stage  # noqa: F401 (jkb is a fixture)

ASSETS = Path(__file__).resolve().parents[1] / "src" / "kblam" / "assets"
POINTER = "Load the kblam-write skill for how to fix this."


@pytest.fixture(autouse=True)
def no_project_dir(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


@pytest.fixture
def hkb(jkb, monkeypatch):
    """jkb, with the fake Jev also behind the Stop hook's check (it builds its client via kblam.check)."""
    monkeypatch.setattr(check, "JevClient", jkb.factory)
    return jkb


def call(event: str, payload, monkeypatch, capsys, *, argv_root: Path | None = None):
    """Run `kblam hook <event>` through the console entry point; (exit code, stdout JSON or None, stderr)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    capsys.readouterr()  # drop fixture setup output
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    argv = (["--root", str(argv_root)] if argv_root else []) + ["hook", event]
    code = entry.main(argv)
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def tool(kb, name: str, **tool_input) -> dict:
    return {"session_id": "s", "hook_event_name": "PreToolUse", "cwd": str(kb.root), "tool_name": name,
            "tool_input": tool_input}


def stop(kb, active: bool = False, event: str = "Stop") -> dict:
    return {"session_id": "s", "hook_event_name": event, "cwd": str(kb.root), "stop_hook_active": active}


def denied(answer) -> str:
    out = answer["hookSpecificOutput"]
    assert (out["hookEventName"], out["permissionDecision"]) == ("PreToolUse", "deny")
    return out["permissionDecisionReason"]


def blocked(answer) -> str:
    assert answer["decision"] == "block"
    return answer["reason"]


# --- PreToolUse ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name, key, rel", [
    ("Write", "file_path", "src/tool.py"),
    ("Edit", "file_path", "evidence/2026-09-22-ratio/README.md"),
    ("Write", "file_path", ".kblam/staging/F-0001-motor.md"),
    ("Write", "file_path", "findings-notes.md"),
    ("NotebookEdit", "notebook_path", "analysis/plot.ipynb"),
])
def test_writes_outside_findings_are_unaffected(kb, monkeypatch, capsys, name, key, rel):
    assert call("PreToolUse", tool(kb, name, **{key: str(kb.root / rel)}), monkeypatch, capsys) == (0, None, "")


@pytest.mark.parametrize("name, key, target", [
    ("Write", "file_path", "findings/calibration/F-0001-motor.md"),
    ("Edit", "file_path", "findings/INDEX.md"),
    ("Write", "file_path", "findings/new-topic/notes.md"),
    ("NotebookEdit", "notebook_path", "findings/calibration/x.ipynb"),
    ("Write", "file_path", "evidence/../findings/calibration/F-0002-x.md"),
])
def test_direct_write_into_findings_is_denied(kb, monkeypatch, capsys, name, key, target):
    for path in (str(kb.root / target), target):  # absolute, and relative to the hook's cwd
        code, answer, _ = call("PreToolUse", tool(kb, name, **{key: path}), monkeypatch, capsys)
        reason = denied(answer)
        assert code == 0 and "findings/ is written only by kblam put" in reason
        assert reason.endswith(POINTER)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths are case-insensitive")
def test_direct_write_denied_whatever_the_case(kb, monkeypatch, capsys):
    target = str(kb.root / "FINDINGS" / "calibration" / "F-0001-motor.md").upper()
    assert denied(call("PreToolUse", tool(kb, "Write", file_path=target), monkeypatch, capsys)[1])


@pytest.mark.parametrize("command", [
    "echo x > findings/calibration/F-0001-motor.md",
    "printf '%s' x 2>/dev/null >>findings/INDEX.md",
    "cat <<'EOF' > findings/calibration/F-0003-x.md\nit's here\nEOF",
    "sed -i 's/90/95/' findings/calibration/F-0001-motor.md",
    "cp /tmp/F-0003-x.md findings/calibration/",
    "mv .kblam/staging/F-0003-x.md findings/calibration/F-0003-x.md",
    "git status && ls | tee -a notes.txt findings/notes.md",
    "FOO=1 cp -t findings/calibration a.md b.md",
])
def test_shell_write_into_findings_is_denied(kb, monkeypatch, capsys, command):
    reason = denied(call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys)[1])
    assert "this command writes under findings/" in reason and reason.endswith(POINTER)


@pytest.mark.parametrize("command", [
    "kblam put .kblam/staging/F-0003-x.md",
    "grep -rn motor findings/ > /tmp/hits.txt",
    "cp findings/calibration/F-0001-motor.md backup/",
    "sed -n 1,5p findings/calibration/F-0001-motor.md",
    "python tools/run.py 2>&1 | tee run.log",
    "cat findings/calibration/F-0001-motor.md",
    "echo 'unbalanced",
])
def test_shell_commands_that_only_read_findings_pass(kb, monkeypatch, capsys, command):
    assert call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys) == (0, None, "")


@pytest.mark.parametrize("command", [
    "'x' | Set-Content findings/calibration/F-0001-motor.md",
    "Set-Content -Value x -Path findings\\calibration\\F-0001-motor.md",
    "Add-Content -LiteralPath:findings/INDEX.md -Value y",
    "Get-Date | Out-File -Append findings/notes.txt",
    "New-Item -ItemType File -Path findings/calibration -Name F-0003-x.md",
    "Copy-Item .kblam/staging/F-0003-x.md findings/calibration/",
    "Move-Item -Dest findings/calibration/F-0003-x.md -Path .kblam/staging/F-0003-x.md",
    "Write-Output x *> findings/log.txt",
])
def test_powershell_write_into_findings_is_denied(kb, monkeypatch, capsys, command):
    reason = denied(call("PreToolUse", tool(kb, "PowerShell", command=command), monkeypatch, capsys)[1])
    assert "this command writes under findings/" in reason and reason.endswith(POINTER)


@pytest.mark.parametrize("command", [
    "Get-Content findings/calibration/F-0001-motor.md | Select-String motor",
    "rg motor findings/ > hits.txt",
    "Copy-Item findings/calibration/F-0001-motor.md backup/",
    "Set-Content -Path notes.txt -Value (Get-Content findings/INDEX.md)",
    "kblam put .kblam/staging/F-0003-x.md",
])
def test_powershell_commands_that_only_read_findings_pass(kb, monkeypatch, capsys, command):
    assert call("PreToolUse", tool(kb, "PowerShell", command=command), monkeypatch, capsys) == (0, None, "")


STATE_TAIL = (".kblam/ holds kblam's own state and only kblam writes it; stage findings under .kblam/staging/ "
              f"(kblam new, kblam edit). {POINTER}")


@pytest.mark.parametrize("name, key, rel", [
    ("Write", "file_path", ".kblam/review.jsonl"),
    ("Edit", "file_path", ".kblam/tree.hash"),
    ("Write", "file_path", ".kblam/lock"),
    ("Write", "file_path", ".kblam/staging/../pairs.sqlite"),
    ("NotebookEdit", "notebook_path", ".kblam/x.ipynb"),
])
def test_file_tool_write_into_kblam_state_is_denied(kb, monkeypatch, capsys, name, key, rel):
    for path in (str(kb.root / rel), rel):
        reason = denied(call("PreToolUse", tool(kb, name, **{key: path}), monkeypatch, capsys)[1])
        assert reason == f"kblam: {name} of {path} under .kblam/ denied. {STATE_TAIL}"


def test_editing_a_staged_file_is_allowed(kb, monkeypatch, capsys):
    staged = kb.write(".kblam/staging/F-0001-motor.md", finding_text("F-0001", E1))
    edit = tool(kb, "Edit", file_path=str(staged), old_string="90 seconds", new_string="95 seconds")
    assert call("PreToolUse", edit, monkeypatch, capsys) == (0, None, "")
    assert call("PreToolUse", tool(kb, "Write", file_path=str(staged), content="x"), monkeypatch, capsys) == \
           (0, None, "")


@pytest.mark.parametrize("tool_name, command", [
    ("Bash", "echo x > .kblam/tree.hash"),
    ("Bash", "cp /tmp/review.jsonl .kblam/review.jsonl"),
    ("Bash", "rm .kblam/review.jsonl"),
    ("Bash", "rm -rf .kblam"),
    ("Bash", "rmdir .kblam/cache"),
    ("Bash", "mv .kblam/review.jsonl /tmp/"),
    ("Bash", "mv -t /tmp .kblam/review.jsonl"),
    ("Bash", "sudo rm -- .kblam/lock"),
    ("PowerShell", "Set-Content -Path .kblam/tree.hash -Value x"),
    ("PowerShell", "Remove-Item .kblam/review.jsonl"),
    ("PowerShell", "rm -Recurse -Force .kblam"),
    ("PowerShell", "del .kblam\\tree.hash"),
    ("PowerShell", "ri -LiteralPath .kblam/lock"),
    ("PowerShell", "Remove-Item notes.txt, .kblam/review.jsonl"),
    ("PowerShell", "Move-Item .kblam/review.jsonl backup/"),
    ("PowerShell", "mi -Path .kblam/review.jsonl -Destination backup/"),
])
def test_shell_write_or_removal_in_kblam_state_is_denied(kb, monkeypatch, capsys, tool_name, command):
    reason = denied(call("PreToolUse", tool(kb, tool_name, command=command), monkeypatch, capsys)[1])
    assert " under .kblam/ denied. " in reason and reason.endswith(STATE_TAIL)


def test_shell_removal_deny_names_the_targets(kb, monkeypatch, capsys):
    command = "echo x > .kblam/tree.hash && rm .kblam/review.jsonl .kblam/staging/F-0003-x.md"
    reason = denied(call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys)[1])
    assert reason == f"kblam: writing .kblam/tree.hash and removing .kblam/review.jsonl under .kblam/ denied. {STATE_TAIL}"


@pytest.mark.parametrize("tool_name, command", [
    ("Bash", "rm findings/calibration/notes.md"),
    ("Bash", "rm .kblam/staging/F-0003-x.md"),
    ("Bash", "mv .kblam/staging/F-0003-x.md .kblam/staging/F-0003-y.md"),
    ("Bash", "echo x > .kblam/staging/F-0003-x.md"),
    ("Bash", "cat .kblam/review.jsonl"),
    ("Bash", "cp .kblam/review.jsonl /tmp/"),
    ("PowerShell", "Remove-Item findings/calibration/notes.md"),
    ("PowerShell", "Remove-Item .kblam/staging/F-0003-x.md"),
    ("PowerShell", "Get-Content .kblam/review.jsonl"),
    ("PowerShell", "Copy-Item .kblam/review.jsonl backup/"),
])
def test_staging_writes_findings_removal_and_state_reads_pass(kb, monkeypatch, capsys, tool_name, command):
    assert call("PreToolUse", tool(kb, tool_name, command=command), monkeypatch, capsys) == (0, None, "")


def test_pre_tool_use_is_dispatched_without_the_cli(kb):
    """The fast path: `kblam hook` does not import the CLI or the Jev client."""
    probe = ("import json, sys; from kblam.entry import main; code = main(['hook', 'PreToolUse']); "
             "print(json.dumps([code, 'kblam.cli' in sys.modules, 'kblam.jev' in sys.modules]))")
    payload = json.dumps(tool(kb, "Write", file_path=str(kb.root / "src" / "a.py")))
    done = subprocess.run([sys.executable, "-c", probe], input=payload, capture_output=True, text=True,
                          check=True, env={k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
    assert json.loads(done.stdout) == [0, False, False]


# --- Stop and SubagentStop ----------------------------------------------------------------------


@pytest.mark.parametrize("event", ["Stop", "SubagentStop"])
def test_stop_with_unchanged_tree_is_silent(hkb, monkeypatch, capsys, event):
    hkb.add("F-0001", "motor", E1)
    assert call(event, stop(hkb, event=event), monkeypatch, capsys) == (0, None, "")
    assert hkb.fake.requests == []


def test_subagent_stop_of_an_internal_agent_is_silent(hkb, monkeypatch, capsys):
    hkb.add("F-0001", "motor", E1)
    (hkb.findings / "calibration" / "notes.md").write_text("scratch\n", encoding="utf-8")
    internal = {**stop(hkb, event="SubagentStop"), "agent_type": ""}
    assert call("SubagentStop", internal, monkeypatch, capsys) == (0, None, "")
    real = {**stop(hkb, event="SubagentStop"), "agent_id": "a1", "agent_type": "Explore"}
    assert "K8" in blocked(call("SubagentStop", real, monkeypatch, capsys)[1])


def test_shell_write_the_bash_parser_misses_is_caught_at_stop(hkb, monkeypatch, capsys):
    hkb.add("F-0001", "motor", E1)
    command = "python -c \"open('findings/calibration/F-0002-drift.md', 'w').write(TEXT)\""
    assert call("PreToolUse", tool(hkb, "Bash", command=command), monkeypatch, capsys) == (0, None, "")
    hkb.write("findings/calibration/F-0002-drift.md", finding_text("F-0002", N))  # what the command did
    hkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)

    code, answer, _ = call("Stop", stop(hkb), monkeypatch, capsys)
    reason = blocked(answer)
    assert code == 0 and "findings/ was changed outside kblam put" in reason
    assert "K7 findings/INDEX.md: INDEX.md differs" in reason
    assert "review R-" in reason and "same_fact F-0002 vs F-0001" in reason  # Jev check ran on the change
    assert reason.endswith(POINTER)


def test_stop_after_a_clean_out_of_band_change_is_silent_and_leaves_tree_hash(hkb, monkeypatch, capsys):
    hkb.add("F-0001", "motor", E1)
    recorded = read_tree_hash(hkb.cfg)
    hkb.write("findings/calibration/F-0001-motor.md",
              finding_text("F-0001", E1, body="More detail, written by a shell command."))
    assert call("Stop", stop(hkb), monkeypatch, capsys) == (0, None, "")
    assert read_tree_hash(hkb.cfg) == recorded  # only validate --record accepts the change


def test_stop_loop_guard(hkb, monkeypatch, capsys):
    hkb.add("F-0001", "motor", E1)
    (hkb.findings / "calibration" / "notes.md").write_text("scratch\n", encoding="utf-8")
    assert "K8" in blocked(call("Stop", stop(hkb), monkeypatch, capsys)[1])

    # continuing because of that block, nothing changed: let the agent stop, with a note
    code, answer, _ = call("Stop", stop(hkb, active=True), monkeypatch, capsys)
    assert code == 0 and "decision" not in answer
    assert "unchanged since the last block" in answer["systemMessage"]

    # continuing, but findings/ changed and still fails: block again
    (hkb.findings / "calibration" / "more.md").write_text("scratch\n", encoding="utf-8")
    assert "more.md" in blocked(call("Stop", stop(hkb, active=True), monkeypatch, capsys)[1])

    # a later, unrelated stop at the same tree is blocked again
    assert blocked(call("Stop", stop(hkb, active=False), monkeypatch, capsys)[1])


def test_stop_without_a_knowledge_base_is_silent(tmp_path, monkeypatch, capsys):
    (tmp_path / "kblam.toml").write_text("[kb]\n", encoding="utf-8")
    payload = {"hook_event_name": "Stop", "cwd": str(tmp_path), "stop_hook_active": False}
    assert call("Stop", payload, monkeypatch, capsys) == (0, None, "")


# --- failing open -------------------------------------------------------------------------------


@pytest.mark.parametrize("payload, note", [
    ("not json", "hook input is not JSON"),
    ("[1, 2]", "hook input is not a JSON object"),
    ({"tool_name": "Write"}, "no tool_input object for Write"),
    ({"tool_name": "Write", "tool_input": {"file_path": 7}}, "Write has no file_path string"),
    ({"tool_name": "Bash", "tool_input": {}}, "Bash has no command string"),
])
def test_malformed_hook_input_fails_open(kb, monkeypatch, capsys, payload, note):
    if isinstance(payload, dict):
        payload = {**payload, "cwd": str(kb.root)}
    code, answer, _ = call("PreToolUse", payload, monkeypatch, capsys)
    assert code == 0 and set(answer) == {"systemMessage"} and note in answer["systemMessage"]


@pytest.mark.parametrize("event", ["PreToolUse", "Stop", "SubagentStop"])
def test_hooks_are_silent_without_kblam_toml(tmp_path, monkeypatch, capsys, event):
    """No kblam.toml in cwd or any parent: no knowledge base, so no output at all (SPEC §8)."""
    (tmp_path / "findings").mkdir()
    payload = {"cwd": str(tmp_path), "hook_event_name": event, "stop_hook_active": False, "tool_name": "Write",
               "tool_input": {"file_path": str(tmp_path / "findings" / "x.md")}}
    assert call(event, payload, monkeypatch, capsys) == (0, None, "")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert call(event, payload, monkeypatch, capsys) == (0, None, "")


@pytest.mark.parametrize("content, note", [
    (b"[kb\n", "kblam.toml"),                         # not TOML
    (b"[kb]\nroot = 7\n", "[kb] root must be str"),    # invalid value
    (b"[kb]\nroot = \"\xff\"\n", "not UTF-8 text"),     # unreadable
])
def test_bad_kblam_toml_fails_open_with_a_note(kb, monkeypatch, capsys, content, note):
    kb.write("kblam.toml", content)
    code, answer, _ = call("PreToolUse", tool(kb, "Write", file_path="findings/x.md"), monkeypatch, capsys)
    assert code == 0 and set(answer) == {"systemMessage"} and note in answer["systemMessage"]
    assert answer["systemMessage"].endswith("; allowed")


def test_unknown_event_fails_open(kb, monkeypatch, capsys):
    code, answer, _ = call("PostToolUse", tool(kb, "Write", file_path="findings/x.md"), monkeypatch, capsys)
    assert code == 0 and "unknown event" in answer["systemMessage"]


def test_unexpected_error_fails_open(hkb, monkeypatch, capsys):
    hkb.add("F-0001", "motor", E1)
    (hkb.findings / "calibration" / "notes.md").write_text("scratch\n", encoding="utf-8")

    def broken(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("kblam.review.check_findings", broken)
    code, answer, _ = call("Stop", stop(hkb), monkeypatch, capsys)
    assert code == 0 and "unexpected RuntimeError: disk on fire" in answer["systemMessage"]


def test_root_option_and_cli_dispatch(kb, monkeypatch, capsys):
    payload = tool(kb, "Write", file_path=str(kb.root / "findings" / "x.md"))
    payload["cwd"] = str(kb.root.parent)  # outside the repository; --root names it
    assert denied(call("PreToolUse", payload, monkeypatch, capsys, argv_root=kb.root)[1])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["--root", str(kb.root), "hook", "PreToolUse"]) == 0
    assert denied(json.loads(capsys.readouterr().out))


# --- put refusals name the skill ----------------------------------------------------------------


def run(kb, *args) -> int:
    return cli.main(["--root", str(kb.root), *args])


def test_put_k_rule_reject_names_the_skill(kb, capsys):
    staged = stage(kb, "F-0001", "motor", "The motor was previously believed to warm up in 60 seconds.")
    assert run(kb, "put", str(staged)) == 1
    assert capsys.readouterr().out.rstrip().endswith(POINTER)


def test_put_jev_reject_names_the_skill(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", N))) == 4
    assert capsys.readouterr().out.rstrip().endswith(POINTER)


def test_put_quantity_reject_names_the_skill(kb, capsys):
    kb.add("F-0001", "motor", E1, extra=quantity("curve ratio", 1.0017))
    assert run(kb, "put", str(stage(kb, "F-0002", "drift", N, extra=quantity("curve ratio", 1.5)))) == 4
    assert capsys.readouterr().out.rstrip().endswith(POINTER)


def test_put_refused_request_and_lock_timeout_name_the_skill(kb, capsys):
    kb.add("F-0001", "motor", E1)
    hand_named = stage(kb, "F-0001", "motor", E1 + " Hand-named copy.")
    assert run(kb, "put", str(hand_named)) == 1
    err = capsys.readouterr().err.rstrip()
    assert "was not staged by kblam edit" in err and err.endswith(POINTER)
    hand_named.unlink()
    kb.write("kblam.toml", KBLAM_TOML + "lock_wait_seconds = 0\n" + NO_EMBEDDINGS + PROMPT_TOML)
    staged = edit_finding(kb.cfg, "F-0001")
    with kb_lock(kb.cfg, "test holder"):
        assert run(kb, "put", str(staged)) == 3
    assert capsys.readouterr().err.rstrip().endswith(POINTER)


# --- installable files --------------------------------------------------------------------------


def test_hooks_json_calls_kblam_hook_for_each_event():
    settings = json.loads((ASSETS / "hooks.json").read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    assert set(hooks) == {"PreToolUse", "Stop", "SubagentStop"}
    matchers = [group.get("matcher") for group in hooks["PreToolUse"]]
    assert matchers == ["Write|Edit|NotebookEdit", "Bash|PowerShell"]
    assert [group.get("matcher") for event in ("Stop", "SubagentStop") for group in hooks[event]] == [None, None]
    for event, groups in hooks.items():
        for group in groups:
            [handler] = group["hooks"]
            assert set(handler) == {"type", "command", "timeout"}  # shell form: no args
            assert handler["type"] == "command"
            assert handler["command"] == (
                f"kblam hook {event} || echo '{{\"systemMessage\": \"kblam hook {event} did not run "
                "(is kblam installed?); {{kb_root}}/ is unguarded\"}'")  # init substitutes the KB root
            assert handler["timeout"] == (30 if event == "PreToolUse" else 300)


def test_pre_commit_takes_kblam_from_path():
    pre_commit = (ASSETS / "pre-commit").read_bytes()
    assert pre_commit.startswith(b"#!/bin/sh\n") and b"\r" not in pre_commit
    assert b'\nkblam --root "$root" validate\n' in pre_commit and b".venv" not in pre_commit
    assert POINTER.encode() in pre_commit


@pytest.mark.parametrize("rel", ["rules/kblam-findings.md", "skills/kblam-write/SKILL.md"])
def test_guidance_names_only_real_commands(rel):
    text = (ASSETS / rel).read_text(encoding="utf-8")
    commands = set(cli.build_parser()._subparsers._group_actions[0].choices)
    used = set(re.findall(r"`kblam ([a-z][a-z-]*)", text))
    assert used and used <= commands, used - commands
