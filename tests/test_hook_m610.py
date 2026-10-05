"""M6.10 in `kblam hook` (SPEC §8 items 2 and 3): the adjudicator gate on kblam resolve and kblam rm, the
denial of a shell removal of a finding, the protection of kblam.resolutions.jsonl, and the Stop hook on a
tree with no tree.hash (a new clone), which runs the deterministic rules and never asks Jev. Jev is the
fake transport from test_check; nothing here touches the network."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from kblam import hook
from kblam.finding import FILENAME_RE, fingerprint
from kblam.jev import CACHE_NAME, PairCache
from kblam.store import put
from kblam.treehash import read_recorded
from kblam.view import load_view

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, finding_text
from test_check import E1, N, jkb, stage  # noqa: F401 (jkb is a fixture)
from test_hook import POINTER, blocked, call, denied, hkb, no_project_dir, stop, tool  # noqa: F401 (fixtures)


def config_with(kb, kb_extra: str) -> None:
    """The fixture kblam.toml with extra [kb] lines."""
    kb.write("kblam.toml", KBLAM_TOML + kb_extra + NO_EMBEDDINGS + PROMPT_TOML)


def shell(kb, command: str, agent_type: str | None = None, name: str = "Bash") -> dict:
    payload = tool(kb, name, command=command)
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


def allowed(kb, monkeypatch, capsys, payload) -> bool:
    return call("PreToolUse", payload, monkeypatch, capsys) == (0, None, "")


# --- the adjudicator gate (§8 item 2) -------------------------------------------------------------


@pytest.fixture
def akb(kb):
    """The fixture KB with a librarian as the adjudicator."""
    config_with(kb, 'adjudicators = ["librarian"]\n')
    return kb


RESOLVE = "kblam resolve R-12345678 --distinct 'one is the MX-100 figure'"
RM = "kblam rm F-0001 --merged-into F-0002"


@pytest.mark.parametrize("command", [RESOLVE, RM])
def test_gate_lets_a_listed_agent_type_run_resolve_and_rm(akb, monkeypatch, capsys, command):
    assert allowed(akb, monkeypatch, capsys, shell(akb, command, "librarian"))
    assert allowed(akb, monkeypatch, capsys, shell(akb, command, "librarian", name="PowerShell"))


@pytest.mark.parametrize("command, which", [(RESOLVE, "kblam resolve"), (RM, "kblam rm")])
def test_gate_denies_resolve_and_rm_to_another_agent_type(akb, monkeypatch, capsys, command, which):
    for agent_type in ("researcher", "general-purpose"):
        reason = denied(call("PreToolUse", shell(akb, command, agent_type), monkeypatch, capsys)[1])
        assert reason == (f"kblam: this command runs {which}, the adjudicator's command, and agent type "
                          f"\"{agent_type}\" is not in [kb] adjudicators in kblam.toml (which lists librarian), so "
                          f"it is denied. Send the item or finding IDs to the coordinator or librarian, who decides "
                          f"them, and carry on. {POINTER}")


def test_gate_lets_the_main_session_through(akb, monkeypatch, capsys):
    """A plain main session has no agent_type; an empty one, or one that is not a string, is treated the
    same (§8 item 2)."""
    for command in (RESOLVE, RM):
        assert allowed(akb, monkeypatch, capsys, shell(akb, command))
        assert allowed(akb, monkeypatch, capsys, shell(akb, command, ""))
        assert allowed(akb, monkeypatch, capsys, {**shell(akb, command), "agent_type": None})


def test_gate_is_off_without_the_adjudicators_key(kb, monkeypatch, capsys):
    for command in (RESOLVE, RM):
        assert allowed(kb, monkeypatch, capsys, shell(kb, command, "researcher"))


def test_an_empty_adjudicators_list_leaves_the_commands_to_the_main_session(kb, monkeypatch, capsys):
    config_with(kb, "adjudicators = []\n")
    reason = denied(call("PreToolUse", shell(kb, RM, "librarian"), monkeypatch, capsys)[1])
    assert "(which is empty, so only the main session adjudicates)" in reason and reason.endswith(POINTER)
    assert allowed(kb, monkeypatch, capsys, shell(kb, RM))


@pytest.mark.parametrize("command", [
    "kblam --root /srv/repo resolve R-12345678 --distinct x",
    "kblam --root=/srv/repo rm F-0001 --merged-into F-0002",
    "FOO=1 sudo kblam resolve R-12345678 --distinct x",
    "env KBLAM_X=1 nohup kblam rm F-0001 --merged-into F-0002",
    "cd /srv/repo && /home/me/.local/bin/kblam rm F-0001 --merged-into F-0002",
    ".venv/bin/kblam resolve R-12345678 --distinct x",
    "kblam validate; kblam.exe resolve R-12345678 --distinct x",
    "git log | head -1 || kblam rm F-0001 --merged-into F-0002 2>/dev/null",
])
def test_gate_finds_kblam_after_options_wrappers_and_paths(akb, monkeypatch, capsys, command):
    reason = denied(call("PreToolUse", shell(akb, command, "researcher"), monkeypatch, capsys)[1])
    assert "the adjudicator's command" in reason and reason.endswith(POINTER)


@pytest.mark.parametrize("command", [
    "kblam resolve R-12345678 --distinct 'one is the MX-100 figure'",
    "& C:\\Users\\me\\.local\\bin\\kblam.exe rm F-0001 --merged-into F-0002",
    "kblam.exe --root C:\\repo resolve R-12345678 --distinct x",
    "Set-Location C:\\repo; KBLAM rm F-0001 --merged-into F-0002",
])
def test_gate_matches_the_powershell_forms(akb, monkeypatch, capsys, command):
    reason = denied(call("PreToolUse", shell(akb, command, "researcher", name="PowerShell"), monkeypatch, capsys)[1])
    assert "the adjudicator's command" in reason and reason.endswith(POINTER)


@pytest.mark.parametrize("command", [
    "kblam put .kblam/staging/F-0003-x.md",
    "kblam validate --record",
    "kblam --root rm check F-0001",          # a root folder named rm; the subcommand is check
    "echo kblam resolve R-12345678",
    "grep -rn 'kblam rm' notes.md",
    "kblam-helper resolve R-12345678",
    "python -c 'print(1)' > kblam-rm.log",
])
def test_gate_passes_other_commands(akb, monkeypatch, capsys, command):
    assert allowed(akb, monkeypatch, capsys, shell(akb, command, "researcher"))


def test_the_gate_and_the_removal_check_stay_on_the_fast_path(akb):
    """PreToolUse runs on every shell command: the new checks import neither the CLI nor the Jev client."""
    akb.add("F-0001", "motor", E1)
    probe = ("import json, sys; from kblam.entry import main; code = main(['hook', 'PreToolUse']); "
             "print(json.dumps([code, 'kblam.cli' in sys.modules, 'kblam.jev' in sys.modules, "
             "'kblam.finding' in sys.modules]))")
    payload = json.dumps(shell(akb, f"{RM} && rm -rf findings/calibration", "researcher"))
    done = subprocess.run([sys.executable, "-c", probe], input=payload, capture_output=True, text=True,
                          check=True, env={k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
    lines = done.stdout.splitlines()
    assert "the adjudicator's command" in lines[0] and "removes findings (findings/calibration)" in lines[0]
    assert json.loads(lines[1]) == [0, False, False, False]


def test_gate_and_a_write_under_findings_get_one_deny(akb, monkeypatch, capsys):
    reason = denied(call("PreToolUse", shell(akb, RESOLVE + " > findings/log.txt", "researcher"),
                         monkeypatch, capsys)[1])
    assert reason.startswith("kblam: this command writes under findings/ (findings/log.txt), so it is denied.")
    assert "kblam: this command runs kblam resolve" in reason
    assert reason.count(POINTER) == 1 and reason.endswith(POINTER)


# --- removing a finding (§8 item 2) ---------------------------------------------------------------


@pytest.fixture
def rkb(kb):
    """A finding on disk, a topic folder without findings, and an empty folder."""
    kb.add("F-0001", "motor", E1)
    kb.write("findings/scratch/notes.md", "scratch\n")
    (kb.findings / "empty").mkdir()
    return kb


REMOVAL_TAIL = ("Removing a finding is the adjudicator's decision: once a merge has moved everything a finding "
                "states into another, the adjudicator removes it with kblam rm <id> --merged-into <target>. Any "
                f"other agent sends the finding IDs to the coordinator or librarian. {POINTER}")


@pytest.mark.parametrize("tool_name, command, target", [
    ("Bash", "rm findings/calibration/F-0001-motor.md", "findings/calibration/F-0001-motor.md"),
    ("Bash", "rm -f -- findings/calibration/F-0042-not-yet-there.md", "findings/calibration/F-0042-not-yet-there.md"),
    ("Bash", "mv findings/calibration/F-0001-motor.md /tmp/", "findings/calibration/F-0001-motor.md"),
    ("Bash", "rm -rf findings/calibration", "findings/calibration"),
    ("Bash", "rm -r ./findings/", "./findings/"),
    ("Bash", "sudo rmdir findings/calibration", "findings/calibration"),
    ("PowerShell", "Remove-Item findings\\calibration\\F-0001-motor.md", "findings\\calibration\\F-0001-motor.md"),
    ("PowerShell", "Remove-Item -Recurse -Force findings/calibration", "findings/calibration"),
    ("PowerShell", "Move-Item findings/calibration/F-0001-motor.md backup/", "findings/calibration/F-0001-motor.md"),
    ("PowerShell", "del findings", "findings"),
])
def test_shell_removal_of_a_finding_is_denied(rkb, monkeypatch, capsys, tool_name, command, target):
    reason = denied(call("PreToolUse", shell(rkb, command, name=tool_name), monkeypatch, capsys)[1])
    assert reason == f"kblam: this command removes findings ({target}), so it is denied. {REMOVAL_TAIL}"


@pytest.mark.parametrize("name", [
    "F-0001-motor.md", "F-10000-a-b-c.md", "F-001-motor.md", "F-0001-.md", "F-0001-a--b.md", "F-0001-motor.md.bak",
    "F-0001_motor.md", "notes.md", "INDEX.md",
] + ([] if sys.platform == "win32" else ["F-0001-Motor.md", "f-0001-motor.md"]))  # Windows folds the case
def test_the_hook_knows_a_finding_name_as_kblam_does(name):
    """hook.py may not import kblam.finding (too slow for PreToolUse), so it keeps its own copy of the
    filename pattern; the two must agree."""
    assert bool(hook.FINDING_NAME_RE.match(name)) == bool(FILENAME_RE.match(name))


def test_removal_of_a_finding_by_absolute_path_is_denied(rkb, monkeypatch, capsys):
    target = str(rkb.findings / "calibration" / "F-0001-motor.md")
    reason = denied(call("PreToolUse", shell(rkb, f"rm '{target}'"), monkeypatch, capsys)[1])
    assert reason == f"kblam: this command removes findings ({target}), so it is denied. {REMOVAL_TAIL}"


@pytest.mark.parametrize("tool_name, command", [
    ("Bash", "rm findings/calibration/notes.md"),              # a stray file (K8)
    ("Bash", "rm findings/F-0002-loose.md"),                   # a finding name outside a topic folder (K8)
    ("Bash", "rm findings/calibration/sub/F-0003-deep.md"),    # the same, one level too deep (K8)
    ("Bash", "rm findings/INDEX.md"),
    ("Bash", "rm -rf findings/scratch"),                       # a folder that holds no finding
    ("Bash", "rmdir findings/empty"),
    ("Bash", "mv findings/scratch/notes.md /tmp/"),
    ("PowerShell", "Remove-Item -Recurse findings\\scratch"),
    ("PowerShell", "Remove-Item findings/calibration/notes.md"),
])
def test_removing_other_files_under_the_kb_root_passes(rkb, monkeypatch, capsys, tool_name, command):
    assert allowed(rkb, monkeypatch, capsys, shell(rkb, command, name=tool_name))


def test_removing_a_kb_root_that_holds_no_finding_passes(kb, monkeypatch, capsys):
    """The KB root and a topic folder are checked on disk: removing them removes a finding only when one is
    there (here the root holds INDEX.md alone)."""
    assert allowed(kb, monkeypatch, capsys, shell(kb, "rm -rf findings"))
    kb.add("F-0001", "motor", E1)
    assert "removes findings (findings)" in denied(call("PreToolUse", shell(kb, "rm -rf findings"),
                                                        monkeypatch, capsys)[1])


def test_moving_a_finding_by_hand_gets_one_deny_with_both_reasons(rkb, monkeypatch, capsys):
    command = "mv findings/calibration/F-0001-motor.md findings/motor/F-0001-motor.md"
    reason = denied(call("PreToolUse", shell(rkb, command), monkeypatch, capsys)[1])
    assert reason.startswith("kblam: this command writes under findings/ (findings/motor/F-0001-motor.md), so it "
                             "is denied. findings/ is written only by kblam put")
    assert ("kblam: this command removes findings (findings/calibration/F-0001-motor.md), so it is denied. "
            + REMOVAL_TAIL) in reason
    assert reason.count(POINTER) == 1


def test_removing_a_finding_and_kblam_state_gets_both_sentences(rkb, monkeypatch, capsys):
    command = "rm findings/calibration/F-0001-motor.md .kblam/review.jsonl"
    reason = denied(call("PreToolUse", shell(rkb, command), monkeypatch, capsys)[1])
    assert reason.startswith("kblam: this command removes findings (findings/calibration/F-0001-motor.md)")
    assert "kblam: removing .kblam/review.jsonl under .kblam/ denied." in reason and reason.count(POINTER) == 1


# --- kblam.resolutions.jsonl (§8) -----------------------------------------------------------------


RESOLUTIONS_TAIL = ("kblam.resolutions.jsonl records the adjudicator's resolutions, and only kblam resolve writes "
                    "it: the adjudicator closes an item Jev misread with kblam resolve <item-id> --distinct "
                    "\"<reason>\", and any other agent sends the item ID to the coordinator or librarian. "
                    f"{POINTER}")


@pytest.mark.parametrize("name, key", [("Write", "file_path"), ("Edit", "file_path"),
                                       ("NotebookEdit", "notebook_path")])
def test_file_tool_write_of_the_resolutions_is_denied(kb, monkeypatch, capsys, name, key):
    for path in (str(kb.root / "kblam.resolutions.jsonl"), "kblam.resolutions.jsonl",
                 "evidence/../kblam.resolutions.jsonl"):
        reason = denied(call("PreToolUse", tool(kb, name, **{key: path}), monkeypatch, capsys)[1])
        assert reason == f"kblam: {name} of {path} denied. {RESOLUTIONS_TAIL}"


@pytest.mark.parametrize("tool_name, command, what", [
    ("Bash", "echo '{}' >> kblam.resolutions.jsonl", "writing kblam.resolutions.jsonl"),
    ("Bash", "sed -i '1d' kblam.resolutions.jsonl", "writing kblam.resolutions.jsonl"),
    ("Bash", "cp /tmp/resolutions.jsonl kblam.resolutions.jsonl", "writing kblam.resolutions.jsonl"),
    ("Bash", "rm kblam.resolutions.jsonl", "removing kblam.resolutions.jsonl"),
    ("Bash", "mv kblam.resolutions.jsonl /tmp/", "removing kblam.resolutions.jsonl"),
    ("PowerShell", "Add-Content -Path kblam.resolutions.jsonl -Value x", "writing kblam.resolutions.jsonl"),
    ("PowerShell", "Remove-Item .\\kblam.resolutions.jsonl", "removing .\\kblam.resolutions.jsonl"),
])
def test_shell_write_or_removal_of_the_resolutions_is_denied(kb, monkeypatch, capsys, tool_name, command, what):
    reason = denied(call("PreToolUse", shell(kb, command, name=tool_name), monkeypatch, capsys)[1])
    assert reason == f"kblam: {what} denied. {RESOLUTIONS_TAIL}"


@pytest.mark.parametrize("tool_name, command", [
    ("Bash", "cat kblam.resolutions.jsonl"),
    ("Bash", "cp kblam.resolutions.jsonl /tmp/"),
    ("Bash", "echo x > docs/kblam.resolutions.jsonl"),
    ("PowerShell", "Get-Content kblam.resolutions.jsonl | Select-String distinct"),
])
def test_reading_the_resolutions_or_writing_another_file_passes(kb, monkeypatch, capsys, tool_name, command):
    assert allowed(kb, monkeypatch, capsys, shell(kb, command, name=tool_name))
    other = tool(kb, "Write", file_path=str(kb.root / "docs" / "kblam.resolutions.jsonl"))
    assert allowed(kb, monkeypatch, capsys, other)


# --- Stop and SubagentStop with no tree.hash (§8 item 3, "A new clone") ------------------------------


def checked(kb, finding_id: str) -> bool:
    finding = next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)
    return PairCache(kb.cfg.state_dir / CACHE_NAME).was_checked(finding_id, fingerprint(finding, "/"))


@pytest.fixture
def no_jev_check(monkeypatch):
    """Fails the test if the Stop hook runs the Jev check."""
    def refuse(*args, **kwargs):
        pytest.fail("the Stop hook ran the Jev check on a tree with no tree.hash")

    monkeypatch.setattr("kblam.review.check_findings", refuse)


def stop_input(kb, event: str) -> dict:
    """A Stop, or a real subagent's SubagentStop."""
    if event == "Stop":
        return stop(kb)
    return {**stop(kb, event="SubagentStop"), "agent_id": "a1", "agent_type": "Explore"}


@pytest.mark.parametrize("event", ["Stop", "SubagentStop"])
def test_stop_without_tree_hash_runs_only_the_rules_and_is_silent_when_clean(hkb, monkeypatch, capsys,
                                                                             no_jev_check, event):
    hkb.add("F-0001", "motor", E1)
    hkb.add("F-0002", "drift", N)                 # never checked; Jev would call it a restatement
    hkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    (hkb.root / ".kblam" / "tree.hash").unlink()  # as in a new clone
    assert call(event, stop_input(hkb, event), monkeypatch, capsys) == (0, None, "")
    assert hkb.fake.requests == []
    assert read_recorded(hkb.cfg) is None        # nothing is recorded: only validate --record accepts the tree
    assert not checked(hkb, "F-0001") and not checked(hkb, "F-0002")
    assert not (hkb.root / ".kblam" / "stop-block").exists()


def test_stop_without_tree_hash_blocks_on_rules_and_open_items(hkb, monkeypatch, capsys, no_jev_check):
    hkb.add("F-0001", "motor", E1)
    hkb.fake.relations[(E1, N)] = ("restates_and_extends", 0.60, 0.70)
    assert put(hkb.cfg, stage(hkb, "F-0002", "drift", N), client_factory=hkb.factory).review  # an open item
    (hkb.findings / "calibration" / "notes.md").write_text("scratch\n", encoding="utf-8")  # K8
    (hkb.root / ".kblam" / "tree.hash").unlink()
    asked = len(hkb.fake.requests)
    reason = blocked(call("Stop", stop(hkb), monkeypatch, capsys)[1])
    assert "findings/ was changed outside kblam put" in reason and "K8 findings/calibration/notes.md" in reason
    assert "review R-" in reason and "restates_and_extends F-0002 vs F-0001" in reason
    assert reason.endswith(POINTER) and len(hkb.fake.requests) == asked

    # the loop guard as with a tree.hash: continuing after that block with nothing changed is let through
    code, answer, _ = call("Stop", stop(hkb, active=True), monkeypatch, capsys)
    assert code == 0 and "unchanged since the last block" in answer["systemMessage"]
    assert read_recorded(hkb.cfg) is None


def test_stop_without_tree_hash_or_kb_root_stays_silent(kb, monkeypatch, capsys, no_jev_check):
    (kb.root / ".kblam" / "tree.hash").unlink()
    for path in sorted(kb.findings.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    kb.findings.rmdir()
    assert call("Stop", stop(kb), monkeypatch, capsys) == (0, None, "")


def test_a_finding_written_by_hand_on_a_new_clone_is_still_blocked(hkb, monkeypatch, capsys, no_jev_check):
    """No tree.hash does not mean anything goes: a finding written outside put fails K7 (INDEX.md)."""
    hkb.add("F-0001", "motor", E1)
    (hkb.root / ".kblam" / "tree.hash").unlink()
    hkb.write("findings/calibration/F-0002-drift.md", finding_text("F-0002", N))
    reason = blocked(call("Stop", stop(hkb), monkeypatch, capsys)[1])
    assert "K7 findings/INDEX.md: INDEX.md differs" in reason and reason.endswith(POINTER)
    assert hkb.fake.requests == []
