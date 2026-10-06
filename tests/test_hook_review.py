"""M6.11: the hooks guard the review root too (SPEC §8 items 1-3, §12 M6.11 test group 11).

The review root (`research-review/` by default) is guarded like `findings/`: every write under it is
denied, and a removal only for the root itself, a kind folder (`challenges`, `tasks`, `uses`), a
record file (`SC-*.yaml`, `CT-*.yaml`, `CU-*.yaml`) or a glob that could name one of those, so a
stray file there can still be deleted (that is how a K13 stray-file error is fixed). Under `.kblam/`,
`.kblam/review-staging/` is exempt as `.kblam/staging/` is, and `.kblam/review-receipts/` is not. The
Stop hook's format-2 digest covers findings/ and the review root. Offline and deterministic: the
hook is driven exactly as tests/test_hook.py drives it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, record_text
from test_check import E1, jkb  # noqa: F401 (jkb is a fixture, and hkb is built on it)
from test_hook import POINTER, STATE_TAIL, blocked, call, denied, hkb, stop, tool  # noqa: F401 (hkb too)

REVIEW = "research-review"
CUSTOM = "review/records"
REVIEW_TAIL = ("Review records are written only by kblam: stage one with kblam challenge new, kblam task "
               "new or kblam use review (or kblam challenge/task edit), edit it under .kblam/review-staging/, "
               f"then kblam put it. {POINTER}")


@pytest.fixture(autouse=True)
def no_project_dir(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


@pytest.fixture
def custom(kb):
    """The same KB with a non-default `[review] root` (SPEC §9)."""
    kb.write("kblam.toml", KBLAM_TOML + f'[review]\nroot = "{CUSTOM}"\n' + NO_EMBEDDINGS + PROMPT_TOML)
    return kb


@pytest.fixture
def review_kb(request):
    """test_hook's `hkb`: the KB with the fake Jev also behind the Stop hook's check. It is reached by
    name, because ruff reads an imported fixture used as a test parameter as a redefinition (F811)."""
    return request.getfixturevalue("hkb")


# --- file tools: a write under the review root is denied ------------------------------------------


@pytest.mark.parametrize("name, key, rel", [
    ("Write", "file_path", "research-review/challenges/SC-0001.yaml"),
    ("Edit", "file_path", "research-review/INDEX.md"),
    ("Write", "file_path", "research-review/tasks/CT-0001.yaml"),
    ("NotebookEdit", "notebook_path", "research-review/uses/CU-0001.yaml"),
    ("Write", "file_path", "research-review/"),
    ("Write", "file_path", "evidence/../research-review/new.md"),
])
def test_a_write_under_the_review_root_is_denied(kb, monkeypatch, capsys, name, key, rel):
    for path in (str(kb.root / rel), rel):  # absolute, and relative to the hook's cwd
        code, answer, _ = call("PreToolUse", tool(kb, name, **{key: path}), monkeypatch, capsys)
        assert code == 0
        assert denied(answer) == f"kblam: {name} of {path} under {REVIEW}/ denied. {REVIEW_TAIL}"


@pytest.mark.parametrize("rel", [
    "research-review-notes.md",                # the root's name as a prefix, not as a folder
    "research-review-backup/SC-0001.yaml",
    "evidence/research-review.md",
    "src/tool.py",
    ".kblam/review-staging/SC-0001.yaml",      # the author's staged copy: not kblam's state
    ".kblam/staging/F-0001-motor.md",
])
def test_writes_outside_the_review_root_are_unaffected(kb, monkeypatch, capsys, rel):
    for path in (str(kb.root / rel), rel):
        assert call("PreToolUse", tool(kb, "Write", file_path=path), monkeypatch, capsys) == (0, None, "")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths are case-insensitive")
def test_a_review_write_is_denied_whatever_the_case(kb, monkeypatch, capsys):
    target = str(kb.root / "RESEARCH-REVIEW" / "INDEX.md").upper()
    assert denied(call("PreToolUse", tool(kb, "Write", file_path=target), monkeypatch, capsys)[1])


# --- Bash: writes ---------------------------------------------------------------------------------


@pytest.mark.parametrize("command", [
    "echo x > research-review/challenges/SC-0001.yaml",
    "printf '%s' x 2>/dev/null >>research-review/INDEX.md",
    "cat <<'EOF' > research-review/notes.md\nit's here\nEOF",
    "sed -i 's/1.0017/1.0018/' research-review/challenges/SC-0001.yaml",
    "cp /tmp/SC-0003.yaml research-review/challenges/",
    "mv .kblam/review-staging/SC-0001.yaml research-review/challenges/SC-0001.yaml",
    "git status && ls | tee -a notes.txt research-review/notes.md",
    "FOO=1 cp -t research-review/challenges a.yaml b.yaml",
])
def test_a_bash_write_under_the_review_root_is_denied(kb, monkeypatch, capsys, command):
    reason = denied(call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys)[1])
    assert f" under {REVIEW}/ denied. " in reason and reason.endswith(REVIEW_TAIL)


def test_the_bash_review_deny_names_the_written_targets(kb, monkeypatch, capsys):
    command = "echo x > research-review/challenges/notes.md && tee research-review/INDEX.md"
    reason = denied(call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys)[1])
    assert reason == (f"kblam: writing research-review/challenges/notes.md, research-review/INDEX.md "
                      f"under {REVIEW}/ denied. {REVIEW_TAIL}")


def test_the_bash_review_deny_names_writes_and_removals(kb, monkeypatch, capsys):
    command = "echo x > research-review/INDEX.md && rm research-review/challenges/SC-0001.yaml"
    reason = denied(call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys)[1])
    assert reason == (f"kblam: writing research-review/INDEX.md and removing "
                      f"research-review/challenges/SC-0001.yaml under {REVIEW}/ denied. {REVIEW_TAIL}")


# --- Bash: removals -------------------------------------------------------------------------------


@pytest.mark.parametrize("command", [
    "rm research-review/challenges/SC-0001.yaml",           # a record file
    "rm -f research-review/tasks/CT-0002.yaml",
    "rmdir research-review/uses",                           # a kind folder
    "rm -rf research-review",                               # the root itself
    "rm research-review/",
    "sudo rm -- research-review/challenges/SC-0001.yaml",
    "rm research-review/challenges/SC-*.yaml",              # a glob that could name a record
    "rm research-review/challenges/*.yaml",
    "rm -rf research-review/*",
    "rm research-review/*.yaml",
    "rm -r research-review/t?sks",                          # a glob over a kind folder
    "rm -r research-review/ch*",
    "mv research-review/challenges/SC-0001.yaml /tmp/",     # the source of an mv
    "mv -t /tmp research-review/uses/CU-0001.yaml",
])
def test_a_removal_of_a_record_kind_folder_or_the_root_is_denied(kb, monkeypatch, capsys, command):
    reason = denied(call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys)[1])
    assert f" under {REVIEW}/ denied. " in reason and reason.endswith(REVIEW_TAIL)


@pytest.mark.parametrize("command", [
    "rm research-review/challenges/C?-0010.yaml",           # globs that name no literal SC-/CT-/CU-
    "rm research-review/challenges/*0010*",
    "rm research-review/challenges/*-0010.yaml",
    "rm research-review/challenges/S*",
    "rm research-review/challenges/*",
])
def test_a_removal_glob_that_could_name_a_record_is_denied(kb, monkeypatch, capsys, command):
    reason = denied(call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys)[1])
    assert f" under {REVIEW}/ denied. " in reason and reason.endswith(REVIEW_TAIL)


@pytest.mark.parametrize("command", [
    "rm research-review/challenges/*.txt",
    "rm research-review/challenges/*.md",
    "rm research-review/challenges/notes-*",
    "rm research-review/challenges/*.bak",
    "rm research-review/challenges/INDEX.*",
    "rm research-review/challenges/U*",                     # no record name begins with `U` or `T`
    "rm research-review/challenges/T*",                     # (only `CU-` and `CT-` do)
])
def test_a_removal_glob_that_cannot_name_a_record_passes(kb, monkeypatch, capsys, command):
    assert call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys) == (0, None, "")


@pytest.mark.parametrize("tool_name, command", [
    ("Bash", "rm research-review/challenges/[S]C-0010.yaml"),          # a bracket is one glob token:
    ("Bash", "rm research-review/challenges/[SC]C-0010.yaml"),         # its text is not a literal tail
    ("Bash", "rm research-review/challenges/*00[1-9]0.yaml"),
    ("PowerShell", "Remove-Item research-review/challenges/[S]C-0010.yaml"),
    ("PowerShell", "Remove-Item research-review/challenges/[SC]C-0010.yaml"),
    ("PowerShell", "Remove-Item research-review/challenges/*00[1-9]0.yaml"),
])
def test_a_bracket_expression_that_could_name_a_record_is_denied(kb, monkeypatch, capsys, tool_name, command):
    reason = denied(call("PreToolUse", tool(kb, tool_name, command=command), monkeypatch, capsys)[1])
    assert f" under {REVIEW}/ denied. " in reason and reason.endswith(REVIEW_TAIL)


def test_an_unclosed_bracket_is_a_literal_character(kb, monkeypatch, capsys):
    """fnmatch reads a `[` with no `]` as a literal, so this names no record (and `[` is not a token)."""
    command = "rm research-review/challenges/notes[.txt"
    assert call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys) == (0, None, "")


def test_the_bash_removal_deny_reads_as_under_kblam_it_does(kb, monkeypatch, capsys):
    command = "rm -rf research-review/tasks"
    reason = denied(call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys)[1])
    assert reason == f"kblam: removing research-review/tasks under {REVIEW}/ denied. {REVIEW_TAIL}"


@pytest.mark.parametrize("command", [
    "rm research-review/INDEX.md",                          # the generated index is regenerable
    "rm research-review/notes.md",
    "rm research-review/challenges/notes.md",               # a K13 stray: how it is fixed
    "rm research-review/challenges/*.txt",
    "rm -rf research-review/scratch/",
    "rm research-review/challenges/SC-0001.txt",
    "rm -rf .",                                             # an ancestor, as the .kblam/ rule allows
    "mv research-review/INDEX.md /tmp/",                    # the source of an mv, when it is no record
    "rm findings/calibration/notes.md",
    "cat research-review/INDEX.md",
    "grep -rn SC-0001 research-review/",
])
def test_removing_a_stray_file_under_the_review_root_passes(kb, monkeypatch, capsys, command):
    assert call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys) == (0, None, "")


def test_an_absolute_target_is_compared_as_a_path(kb, monkeypatch, capsys):
    target = str(kb.root).replace("\\", "/") + f"/{REVIEW}/challenges/SC-0001.yaml"
    reason = denied(call("PreToolUse", tool(kb, "Bash", command=f"rm {target}"), monkeypatch, capsys)[1])
    assert reason == f"kblam: removing {target} under {REVIEW}/ denied. {REVIEW_TAIL}"


# --- PowerShell: writes and removals ---------------------------------------------------------------


@pytest.mark.parametrize("command", [
    "'x' | Set-Content research-review/challenges/SC-0001.yaml",
    "Set-Content -Value x -Path research-review\\INDEX.md",
    "Add-Content -LiteralPath:research-review/INDEX.md -Value y",
    "Get-Date | Out-File -Append research-review/notes.txt",
    "New-Item -ItemType File -Path research-review -Name notes.md",
    "Copy-Item .kblam/review-staging/SC-0001.yaml research-review/challenges/",
    "Move-Item -Dest research-review/challenges/SC-0001.yaml -Path .kblam/review-staging/SC-0001.yaml",
    "Write-Output x *> research-review/log.txt",
])
def test_a_powershell_write_under_the_review_root_is_denied(kb, monkeypatch, capsys, command):
    reason = denied(call("PreToolUse", tool(kb, "PowerShell", command=command), monkeypatch, capsys)[1])
    assert f" under {REVIEW}/ denied. " in reason and reason.endswith(REVIEW_TAIL)


@pytest.mark.parametrize("command", [
    "Remove-Item research-review/challenges/SC-0001.yaml",
    "del research-review\\tasks\\CT-0001.yaml",
    "ri -LiteralPath research-review/uses",
    "rm -Recurse -Force research-review",
    "Remove-Item notes.txt, research-review/challenges/SC-0001.yaml",
    "Move-Item research-review/challenges/CT-0001.yaml backup/",
    "mi -Path research-review/challenges/SC-0001.yaml -Destination backup/",
])
def test_a_powershell_removal_of_a_record_kind_folder_or_the_root_is_denied(kb, monkeypatch, capsys, command):
    reason = denied(call("PreToolUse", tool(kb, "PowerShell", command=command), monkeypatch, capsys)[1])
    assert f" under {REVIEW}/ denied. " in reason and reason.endswith(REVIEW_TAIL)


def test_the_powershell_mv_deny_names_the_removed_source(kb, monkeypatch, capsys):
    command = "mv research-review/challenges/SC-0001.yaml backup/"
    reason = denied(call("PreToolUse", tool(kb, "PowerShell", command=command), monkeypatch, capsys)[1])

    assert reason == (f"kblam: removing research-review/challenges/SC-0001.yaml under {REVIEW}/ denied. "
                      f"{REVIEW_TAIL}")


@pytest.mark.parametrize("command", [
    "Remove-Item research-review/INDEX.md",
    "Remove-Item research-review/challenges/notes.md",
    "Remove-Item research-review/challenges/*.txt",
    "Get-Content research-review/INDEX.md",
    "Copy-Item research-review/INDEX.md backup/",
    "Remove-Item findings/calibration/notes.md",
])
def test_powershell_removals_of_stray_files_pass(kb, monkeypatch, capsys, command):
    assert call("PreToolUse", tool(kb, "PowerShell", command=command), monkeypatch, capsys) == (0, None, "")


# --- .kblam/review-staging/ and .kblam/review-receipts/ --------------------------------------------


@pytest.mark.parametrize("tool_name, command", [
    ("Bash", "echo x > .kblam/review-staging/SC-0001.yaml"),
    ("Bash", "rm .kblam/review-staging/SC-0001.yaml"),
    ("Bash", "mv .kblam/review-staging/SC-0001.yaml .kblam/review-staging/SC-0002.yaml"),
    ("Bash", "cat .kblam/review-receipts/SC-0001.json"),
    ("PowerShell", "Set-Content -Path .kblam/review-staging/SC-0001.yaml -Value x"),
    ("PowerShell", "Remove-Item .kblam/review-staging/SC-0001.yaml"),
    ("PowerShell", "Get-Content .kblam/review-receipts/SC-0001.json"),
])
def test_the_review_staging_folder_is_exempt_from_the_state_rule(kb, monkeypatch, capsys, tool_name, command):
    assert call("PreToolUse", tool(kb, tool_name, command=command), monkeypatch, capsys) == (0, None, "")


@pytest.mark.parametrize("name, key, rel", [
    ("Write", "file_path", ".kblam/review-staging/SC-0001.yaml"),
    ("Edit", "file_path", ".kblam/review-staging/SC-0001.yaml"),
    ("NotebookEdit", "notebook_path", ".kblam/review-staging/x.ipynb"),
])
def test_a_file_tool_may_edit_the_review_staging_folder(kb, monkeypatch, capsys, name, key, rel):
    assert call("PreToolUse", tool(kb, name, **{key: str(kb.root / rel)}), monkeypatch, capsys) == (0, None, "")


@pytest.mark.parametrize("name, key, rel", [
    ("Write", "file_path", ".kblam/review-receipts/SC-0001.json"),
    ("Edit", "file_path", ".kblam/review-receipts/SC-0001.json"),
])
def test_a_write_into_the_review_receipts_is_denied(kb, monkeypatch, capsys, name, key, rel):
    for path in (str(kb.root / rel), rel):
        reason = denied(call("PreToolUse", tool(kb, name, **{key: path}), monkeypatch, capsys)[1])
        assert reason == f"kblam: {name} of {path} under .kblam/ denied. {STATE_TAIL}"


@pytest.mark.parametrize("tool_name, command", [
    ("Bash", "echo x > .kblam/review-receipts/SC-0001.json"),
    ("Bash", "rm .kblam/review-receipts/SC-0001.json"),
    ("Bash", "mv .kblam/review-receipts/SC-0001.json /tmp/"),
    ("PowerShell", "Set-Content -Path .kblam/review-receipts/SC-0001.json -Value x"),
    ("PowerShell", "Remove-Item .kblam/review-receipts/SC-0001.json"),
])
def test_a_write_or_removal_in_the_review_receipts_is_denied(kb, monkeypatch, capsys, tool_name, command):
    reason = denied(call("PreToolUse", tool(kb, tool_name, command=command), monkeypatch, capsys)[1])
    assert " under .kblam/ denied. " in reason and reason.endswith(STATE_TAIL)


def test_a_command_hitting_several_roots_names_them_in_order(kb, monkeypatch, capsys):
    command = "echo x > findings/notes.md && rm -rf research-review && rm .kblam/tree.hash"
    reason = denied(call("PreToolUse", tool(kb, "Bash", command=command), monkeypatch, capsys)[1])

    assert reason.index("writes under findings/") < reason.index(f"under {REVIEW}/ denied")
    assert reason.index(f"under {REVIEW}/ denied") < reason.index("under .kblam/ denied")


# --- a configured root -----------------------------------------------------------------------------


def test_the_configured_root_is_guarded_under_its_own_name(custom, monkeypatch, capsys):
    path = f"{CUSTOM}/challenges/SC-0001.yaml"
    reason = denied(call("PreToolUse", tool(custom, "Write", file_path=path), monkeypatch, capsys)[1])
    assert reason == f"kblam: Write of {path} under {CUSTOM}/ denied. {REVIEW_TAIL}"


def test_the_default_root_is_not_guarded_when_another_is_configured(custom, monkeypatch, capsys):
    assert call("PreToolUse", tool(custom, "Write", file_path="research-review/INDEX.md"),
                monkeypatch, capsys) == (0, None, "")


def test_removals_follow_the_configured_root(custom, monkeypatch, capsys):
    denied_reason = denied(call("PreToolUse", tool(custom, "Bash", command=f"rm -rf {CUSTOM}/tasks"),
                                monkeypatch, capsys)[1])
    assert denied_reason == (f"kblam: removing {CUSTOM}/tasks under {CUSTOM}/ denied. {REVIEW_TAIL}")
    assert call("PreToolUse", tool(custom, "Bash", command=f"rm {CUSTOM}/notes.md"),
                monkeypatch, capsys) == (0, None, "")
    assert call("PreToolUse", tool(custom, "Bash", command="rm research-review/challenges/SC-0001.yaml"),
                monkeypatch, capsys) == (0, None, "")


# --- the fast path ---------------------------------------------------------------------------------


def test_pre_tool_use_on_a_review_path_imports_only_the_hook_code(kb):
    """SPEC §8: a PreToolUse call imports only the hook code, and validating a review path needs none
    of the validator, the Jev client or the record modules."""
    banned = ("kblam.check", "kblam.cli", "kblam.jev", "kblam.k13", "kblam.k14", "kblam.k15", "kblam.records",
              "kblam.review", "kblam.review_stage", "kblam.review_write", "kblam.rules", "kblam.store",
              "kblam.treehash", "kblam.view")
    probe = ("import json, sys; from kblam.entry import main; code = main(['hook', 'PreToolUse']); "
             f"print(json.dumps([code, sorted(m for m in {banned!r} if m in sys.modules)]))")
    payload = json.dumps(tool(kb, "Write", file_path=str(kb.root / REVIEW / "challenges" / "SC-0001.yaml")))
    done = subprocess.run([sys.executable, "-c", probe], input=payload, capture_output=True, text=True,
                          check=True, env={k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
    lines = done.stdout.strip().splitlines()

    assert json.loads(lines[-1]) == [0, []]
    assert json.loads(lines[0])["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- Stop over the review root ---------------------------------------------------------------------


STOP_FIX = ("Fix each failure through kblam: a finding with kblam edit <id>, a change to the staged copy and "
            "kblam put; a record as its failure line says, with the kblam command it names or a staged copy and "
            f"kblam put. Never write under findings/ or {REVIEW}/ directly. Once the tree is clean, kblam "
            "validate --record accepts the change.")


def test_stop_blocks_on_a_record_changed_out_of_band(review_kb, monkeypatch, capsys):
    """The format-2 digest covers the review root (SPEC §5.2.6), so a hand-edited record reaches K13. The
    digest cannot tell which root changed, so with the review root folder present the reason names both,
    and its fix sentence covers records too (SPEC §8 item 3)."""
    review_kb.add("F-0001", "motor", E1)
    assert call("Stop", stop(review_kb), monkeypatch, capsys) == (0, None, "")  # the tree as kblam left it
    review_kb.write(f"{REVIEW}/challenges/SC-0001.yaml", record_text("SC", "SC-0009"))  # a hand edit

    code, answer, _ = call("Stop", stop(review_kb), monkeypatch, capsys)
    first, *failures, fix, pointer = blocked(answer).split("\n")

    assert code == 0
    assert first == (f"kblam: findings/ or {REVIEW}/ was changed outside kblam, and the knowledge base fails "
                     f"kblam validate:")
    assert failures == [f"K13 {REVIEW}/INDEX.md: INDEX.md is missing; run kblam review index",
                        f"K13 {REVIEW}/challenges/SC-0001.yaml:2: id: 'SC-0009' does not match the file name's "
                        f"ID (SC-0001)"]
    assert (fix, pointer) == (STOP_FIX, POINTER)


def test_stop_loop_guard_names_both_roots(review_kb, monkeypatch, capsys):
    """Continuing because of a block, with neither root changed since: the stop is let through with a note
    that names both roots and says what to do when the failures cannot be fixed through kblam."""
    review_kb.add("F-0001", "motor", E1)
    review_kb.write(f"{REVIEW}/challenges/SC-0001.yaml", record_text("SC", "SC-0009"))
    _first, *failures, _fix, _pointer = blocked(call("Stop", stop(review_kb), monkeypatch, capsys)[1]).split("\n")

    code, answer, _ = call("Stop", stop(review_kb, active=True), monkeypatch, capsys)

    assert code == 0 and "decision" not in answer
    assert answer["systemMessage"] == (
        f"kblam hook Stop: the knowledge base still fails kblam validate ({len(failures)} failure(s)), and "
        f"neither findings/ nor {REVIEW}/ changed since the last block, so the stop is not blocked again. If "
        f"you cannot fix the failures through kblam, leave findings/ and {REVIEW}/ as they are and tell the "
        f"user; allowed")


def test_stop_names_only_findings_while_there_is_no_review_root(review_kb, monkeypatch, capsys):
    """With no review root folder, only findings/ can have changed, and the block reason and the
    loop-guard note name only it."""
    review_kb.add("F-0001", "motor", E1)
    assert not (review_kb.root / REVIEW).exists()
    (review_kb.findings / "calibration" / "notes.md").write_text("scratch\n", encoding="utf-8")

    first, *failures, fix, pointer = blocked(call("Stop", stop(review_kb), monkeypatch, capsys)[1]).split("\n")
    assert first == "kblam: findings/ was changed outside kblam put, and the knowledge base fails kblam validate:"
    assert failures and all(line.startswith("K8 findings/calibration/notes.md") for line in failures)
    assert (fix, pointer) == ("Fix each failure through kblam (kblam edit <id>, change the staged copy, kblam put "
                              "it); never write under findings/ directly. Once the tree is clean, kblam validate "
                              "--record accepts the change.", POINTER)

    code, answer, _ = call("Stop", stop(review_kb, active=True), monkeypatch, capsys)
    assert code == 0 and answer["systemMessage"] == (
        f"kblam hook Stop: findings/ still fails kblam validate ({len(failures)} failure(s)) and findings/ is "
        f"unchanged since the last block, so the stop is not blocked again; allowed")


def test_stop_is_silent_while_a_format_2_tree_hash_matches_the_review_root(review_kb, monkeypatch, capsys):
    """A matching tree.hash stays on the fast path: the hash is kblam's record of the tree, and a
    later out-of-band change is what Stop validates (SPEC §5.2.4 decides this deliberately)."""
    review_kb.add("F-0001", "motor", E1)
    review_kb.write(f"{REVIEW}/challenges/SC-0001.yaml", record_text("SC", "SC-0009"))
    review_kb.reindex()                              # the tree as kblam would have left it

    assert call("Stop", stop(review_kb), monkeypatch, capsys) == (0, None, "")
    assert review_kb.fake.requests == []
