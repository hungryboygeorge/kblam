"""M6.5: `kblam init [--update]` in fresh git repositories under tmp_path, the kblam.toml template, and
the package assets. The hook check runs the real hook commands through a shell in two tests; the rest
replace it. Nothing here touches the network (a fresh KB's Stop hook matches tree.hash and asks nobody)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kblam import cli, init
from kblam.check import parse_policy
from kblam.config import ConfigError, load_config
from kblam.jev import DEFAULT_JEV, jev_settings

from conftest import DEFAULT_PROMPT_ID

HOOK_EVENTS = ["PreToolUse", "PreToolUse", "Stop", "SubagentStop"]
FOREIGN = {"type": "command", "command": "echo mine", "timeout": 5}


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A fresh `git init` repository as the current directory, isolated from system and global git config.
    The hook check runs `kblam` from PATH: this checkout's, not one installed elsewhere, even when pytest
    runs from the venv's python without the venv activated."""
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def no_hook_check(monkeypatch):
    monkeypatch.setattr(init.Init, "check_hooks", lambda self, installed, findings_dir: None)


def kblam_init(capsys, *args) -> tuple[int, str, str]:
    capsys.readouterr()
    code = cli.main(["init", *args])
    out, err = capsys.readouterr()
    return code, out, err


def actions(out: str) -> dict[str, str]:
    """The file lines of init's output: {path: action}."""
    lines = [line.split(None, 2) for line in out.splitlines() if line.startswith("  ")]
    return {parts[1]: parts[0] for parts in lines if len(parts) >= 2 and parts[0] in
            ("created", "updated", "unchanged", "kept", "refused")}


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every file init may write (the work tree, .kblam/ and the git pre-commit hook), with its mtime."""
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and (rel.parts[0] != ".git" or rel.as_posix() == ".git/hooks/pre-commit"):
            files[rel.as_posix()] = (path.read_bytes(), path.stat().st_mtime_ns)
    return files


def installed_hooks(kb_root: str = "findings") -> dict:
    return init._hook_entries(kb_root)["hooks"]


def settings(root: Path) -> dict:
    return json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))


# The lines init appends to .gitattributes (SPEC §7.1): the KB root byte-exact, the resolutions merged by union.
ATTRIBUTES = "{root}/** -text\nkblam.resolutions.jsonl merge=union\nresearch-review/** -text\n"
# Every file init writes, in its report order, except that the pre-commit hook is kept last: the tests
# below read WRITTEN[:-1] as "everything but the pre-commit hook".
WRITTEN = ["kblam.toml", "findings/INDEX.md", "research-review/INDEX.md", ".gitattributes", ".gitignore",
           ".claude/rules/kblam-findings.md", ".claude/skills/kblam-write/SKILL.md", ".claude/settings.json",
           "CLAUDE.md", ".kblam/tree.hash", ".git/hooks/pre-commit"]


# --- a fresh repository ---------------------------------------------------------------------------


def test_init_in_a_fresh_repo_produces_a_kb_that_validates(repo, capsys):
    code, out, err = kblam_init(capsys)
    assert (code, err) == (0, ""), out + err
    assert actions(out) == {rel: "created" for rel in WRITTEN}
    for rel in WRITTEN:
        assert b"\r" not in (repo / rel).read_bytes(), rel  # LF endings
    assert (repo / "kblam.toml").read_bytes() == (init.ASSETS / "kblam.toml").read_bytes()
    assert (repo / ".gitattributes").read_text(encoding="utf-8") == ATTRIBUTES.format(root="findings")
    assert "(added findings/** -text and kblam.resolutions.jsonl merge=union and research-review/** -text)" in out
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".kblam/\n"
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == init.GUIDANCE_LINE.format(root="findings") + "\n"
    assert settings(repo) == {"hooks": installed_hooks()}
    assert (repo / ".git/hooks/pre-commit").read_bytes() == (init.ASSETS / "pre-commit").read_bytes()
    for rel in ("rules/kblam-findings.md", "skills/kblam-write/SKILL.md"):
        # both root tokens are rendered: the review root's default is the one a fresh repo gets
        assert (repo / ".claude" / rel).read_bytes() == init._asset(rel, "findings", "research-review")
        assert b"{{kb_root}}" not in (repo / ".claude" / rel).read_bytes()
    assert "{{kb_root}}" not in json.dumps(settings(repo)) and "; findings/ is unguarded" in json.dumps(settings(repo))

    # the hook check ran every entry through a shell, and each answered with silence
    assert "hook check through " in out
    answers = [line for line in out.splitlines() if line.startswith(("  PreToolUse", "  Stop", "  SubagentStop"))]
    assert [line.split()[0].rstrip(":") for line in answers] == HOOK_EVENTS
    assert all(line.endswith("answered (no output)") for line in answers), answers

    capsys.readouterr()
    assert cli.main(["validate"]) == 0
    assert "kblam validate: OK (0 findings)" in capsys.readouterr().out


def test_init_approves_its_own_template_for_the_first_commit(repo, capsys, no_hook_check, monkeypatch):
    """The kblam.toml init writes is kblam's template, so committing it needs no approval; an edit to it
    does (SPEC §8 item 4), and approve-config shows the edit against the template, not the whole file."""
    assert kblam_init(capsys)[0] == 0
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    capsys.readouterr()
    assert cli.main(["validate", "--commit"]) == 0

    path = repo / "kblam.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('scopes = ["any"]', 'scopes = ["any", "MX-200"]'),
                    encoding="utf-8")
    subprocess.run(["git", "add", "kblam.toml"], cwd=repo, check=True)
    capsys.readouterr()
    assert cli.main(["validate", "--commit"]) == 1
    assert "this commit changes kblam.toml" in capsys.readouterr().out

    class Terminal:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["approve-config"]) == 0
    out = capsys.readouterr().out
    assert "--- kblam.toml (the template kblam init writes)" in out
    changed = [line for line in out.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    assert changed == ['-scopes = ["any"]               # where a finding applies; edit for this project',
                       '+scopes = ["any", "MX-200"]               # where a finding applies; edit for this project']
    assert cli.main(["validate", "--commit"]) == 0


def test_second_init_changes_nothing(repo, capsys):
    assert kblam_init(capsys)[0] == 0
    before = snapshot(repo)
    code, out, _ = kblam_init(capsys)
    assert code == 0
    assert set(actions(out).values()) == {"unchanged"} and set(actions(out)) == set(WRITTEN)
    assert snapshot(repo) == before


def test_init_appends_to_existing_files(repo, capsys, no_hook_check):
    (repo / ".gitignore").write_bytes(b"build/")                   # no final newline
    (repo / ".gitattributes").write_bytes(b"* text=auto\n")
    (repo / "CLAUDE.md").write_bytes(b"# Project\n\nSome guidance.\n")
    assert kblam_init(capsys)[0] == 0
    assert (repo / ".gitignore").read_bytes() == b"build/\n.kblam/\n"
    assert (repo / ".gitattributes").read_bytes() == b"* text=auto\n" + ATTRIBUTES.format(root="findings").encode()
    line = init.GUIDANCE_LINE.format(root="findings")
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == f"# Project\n\nSome guidance.\n\n{line}\n"


def test_gitattributes_gains_only_the_missing_line(repo, capsys, no_hook_check):
    """A repository with both protected roots needs only the resolutions line; a second init finds
    all three (SPEC §7.1)."""
    (repo / ".gitattributes").write_bytes(b"findings/** -text\nresearch-review/** -text\n")
    code, out, _ = kblam_init(capsys)
    assert code == 0 and actions(out)[".gitattributes"] == "updated"
    assert "(added kblam.resolutions.jsonl merge=union)" in out
    assert (repo / ".gitattributes").read_text(encoding="utf-8") == (
        "findings/** -text\nresearch-review/** -text\nkblam.resolutions.jsonl merge=union\n")
    code, out, _ = kblam_init(capsys)
    assert actions(out)[".gitattributes"] == "unchanged" and "has the kblam lines" in out


def test_init_keeps_an_extended_guidance_line(repo, capsys, no_hook_check):
    line = init.GUIDANCE_LINE.format(root="findings")
    text = f"# Project\n\n{line} Send review items to the librarian.\n"
    (repo / "CLAUDE.md").write_bytes(text.encode("utf-8"))
    assert kblam_init(capsys)[0] == 0
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == text


def test_gitattributes_line_follows_the_configured_root(repo, capsys, no_hook_check):
    template = (init.ASSETS / "kblam.toml").read_text(encoding="utf-8")
    (repo / "kblam.toml").write_text(template.replace('root = "findings"', 'root = "kb/facts"'), encoding="utf-8")
    code, out, _ = kblam_init(capsys)
    assert code == 0 and actions(out)["kblam.toml"] == "kept"
    assert (repo / ".gitattributes").read_text(encoding="utf-8") == ATTRIBUTES.format(root="kb/facts")
    assert (repo / "kb" / "facts" / "INDEX.md").is_file()
    assert "`kb/facts/`" in (repo / "CLAUDE.md").read_text(encoding="utf-8")


def test_assets_name_the_configured_root_and_a_second_init_is_unchanged(repo, capsys, no_hook_check):
    template = (init.ASSETS / "kblam.toml").read_text(encoding="utf-8")
    (repo / "kblam.toml").write_text(template.replace('root = "findings"', 'root = "kb"'), encoding="utf-8")
    assert kblam_init(capsys)[0] == 0
    rule = (repo / ".claude/rules/kblam-findings.md").read_text(encoding="utf-8")
    assert rule.startswith('---\npaths:\n  - "kb/**"\n---\n') and "`kb/INDEX.md`" in rule
    skill = (repo / ".claude/skills/kblam-write/SKILL.md").read_text(encoding="utf-8")
    assert "{{kb_root}}" not in rule + skill and "findings/" not in rule + skill and "`kb/`" in skill
    commands = [h["command"] for groups in settings(repo)["hooks"].values() for g in groups for h in g["hooks"]]
    assert all(c.endswith("; kb/ is unguarded\"}'") for c in commands) and len(commands) == 4

    before = snapshot(repo)
    code, out, _ = kblam_init(capsys)
    assert code == 0 and actions(out)[".claude/rules/kblam-findings.md"] == "unchanged"
    assert actions(out)[".claude/skills/kblam-write/SKILL.md"] == "unchanged"
    assert actions(out)[".claude/settings.json"] == "unchanged"
    assert snapshot(repo) == before


# --- settings.json --------------------------------------------------------------------------------


def test_existing_settings_keys_and_hooks_survive_the_merge(repo, capsys, no_hook_check):
    existing = {
        "permissions": {"allow": ["Bash(git status)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [FOREIGN]}],
            "Notification": [{"hooks": [FOREIGN]}],
        },
        "model": "opus",
    }
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps(existing), encoding="utf-8")
    code, out, _ = kblam_init(capsys)
    assert code == 0 and actions(out)[".claude/settings.json"] == "updated"
    merged = settings(repo)
    kblam = installed_hooks()
    assert merged["permissions"] == existing["permissions"] and merged["model"] == "opus"
    assert merged["hooks"]["Notification"] == existing["hooks"]["Notification"]
    assert merged["hooks"]["PreToolUse"] == existing["hooks"]["PreToolUse"] + kblam["PreToolUse"]
    assert merged["hooks"]["Stop"] == kblam["Stop"] and merged["hooks"]["SubagentStop"] == kblam["SubagentStop"]

    before = snapshot(repo)
    code, out, _ = kblam_init(capsys)
    assert code == 0 and actions(out)[".claude/settings.json"] == "unchanged"
    assert snapshot(repo) == before  # recognised, not duplicated


def test_stale_kblam_hook_entries_are_replaced_only_by_update(repo, capsys, no_hook_check):
    stale = {"type": "command", "command": "kblam hook Stop", "timeout": 30}
    existing = {"hooks": {"Stop": [{"hooks": [FOREIGN, stale]}],
                          "PreToolUse": [{"matcher": "Write", "hooks": [{**stale, "command": "kblam hook PreToolUse"}]}]}}
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

    code, out, _ = kblam_init(capsys)
    assert code == 0 and actions(out)[".claude/settings.json"] == "kept"
    assert settings(repo) == existing

    code, out, _ = kblam_init(capsys, "--update")
    assert code == 0 and actions(out)[".claude/settings.json"] == "updated"
    hooks, kblam = settings(repo)["hooks"], installed_hooks()
    assert hooks["Stop"] == [{"hooks": [FOREIGN]}] + kblam["Stop"]  # the foreign handler stays in its group
    assert hooks["PreToolUse"] == kblam["PreToolUse"]                 # the emptied group is dropped
    assert hooks["SubagentStop"] == kblam["SubagentStop"]


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '{"hooks": {"Stop": {"command": "x"}}}'])
def test_unusable_settings_is_an_error_and_nothing_is_written(repo, capsys, no_hook_check, text):
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(text, encoding="utf-8")
    before = snapshot(repo)
    code, out, err = kblam_init(capsys)
    assert code == 1 and "settings.json" in err and "Nothing was written" in err
    assert snapshot(repo) == before


# --- pre-commit, --update, errors -----------------------------------------------------------------


def test_foreign_pre_commit_is_not_overwritten(repo, capsys, no_hook_check):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(b"#!/bin/sh\nexec make lint\n")
    for args in ((), ("--update",)):
        code, out, _ = kblam_init(capsys, *args)
        assert code == 1 and actions(out)[".git/hooks/pre-commit"] == "refused"
        assert "a different pre-commit hook is already there" in out
        assert hook.read_bytes() == b"#!/bin/sh\nexec make lint\n"
    for rel in WRITTEN[:-1]:  # everything else was written
        assert (repo / rel).is_file(), rel


def test_pre_commit_honours_core_hooks_path(repo, capsys, no_hook_check):
    subprocess.run(["git", "config", "core.hooksPath", "githooks"], check=True)
    assert kblam_init(capsys)[0] == 0
    assert (repo / "githooks" / "pre-commit").read_bytes() == (init.ASSETS / "pre-commit").read_bytes()
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()


def test_update_rewrites_a_changed_skill_but_never_kblam_toml(repo, capsys, no_hook_check):
    assert kblam_init(capsys)[0] == 0
    skill, config, hook = (repo / ".claude/skills/kblam-write/SKILL.md", repo / "kblam.toml",
                           repo / ".git/hooks/pre-commit")
    skill.write_text("# local edit\n", encoding="utf-8")
    edited = config.read_text(encoding="utf-8").replace('scopes = ["any"]', 'scopes = ["any", "MX-100"]')
    config.write_text(edited, encoding="utf-8")
    hook.write_bytes(hook.read_bytes().replace(b"validate --commit\n", b"validate  # older kblam\n"))

    code, out, _ = kblam_init(capsys)
    assert code == 0
    assert actions(out)[".claude/skills/kblam-write/SKILL.md"] == "kept"
    assert actions(out)[".git/hooks/pre-commit"] == "kept"
    assert skill.read_text(encoding="utf-8") == "# local edit\n"

    code, out, _ = kblam_init(capsys, "--update")
    assert code == 0
    assert actions(out)[".claude/skills/kblam-write/SKILL.md"] == "updated"
    assert actions(out)[".git/hooks/pre-commit"] == "updated"
    assert actions(out)["kblam.toml"] == "kept"
    assert skill.read_bytes() == init._asset("skills/kblam-write/SKILL.md", "findings", "research-review")
    assert hook.read_bytes() == (init.ASSETS / "pre-commit").read_bytes()
    assert config.read_text(encoding="utf-8") == edited


def test_init_outside_a_git_repo_is_a_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    code, _, err = kblam_init(capsys)
    assert code == 2 and "not inside a git repository" in err
    assert list(plain.iterdir()) == []


def test_init_refuses_root_option(repo, capsys):
    capsys.readouterr()
    assert cli.main(["--root", str(repo), "init"]) == 2
    assert "--root is not accepted" in capsys.readouterr().err
    assert not (repo / "kblam.toml").exists()


def test_invalid_kblam_toml_stops_init_before_writing(repo, capsys, no_hook_check):
    (repo / "kblam.toml").write_text("[kb]\nroot = 7\n", encoding="utf-8")
    before = snapshot(repo)
    code, _, err = kblam_init(capsys)
    assert code == 2 and "[kb] root must be str" in err
    assert snapshot(repo) == before


def test_an_unreadable_registry_is_refused_without_a_traceback(repo, capsys, no_hook_check):
    """The findings index step's writes.locked (mutating) runs inside init's try, so store's refusal (SPEC
    §5.2.6) is reported, not raised, and it comes before that step writes the index."""
    assert kblam_init(capsys)[0] == 0
    (repo / "findings" / "INDEX.md").unlink()                     # so init writes the index again
    (repo / ".kblam").mkdir(exist_ok=True)
    (repo / ".kblam" / "review-ids").write_bytes(b"{bad\n")

    code, _, err = kblam_init(capsys)

    assert code == 1 and "Traceback" not in err
    assert "kblam init: .kblam/review-ids cannot be read" in err
    assert not (repo / "findings" / "INDEX.md").exists()          # nothing was written


@pytest.mark.parametrize("root, ok", [
    ("findings", True), ("kb/facts", True), ("kb/v1.2_x-y", True), ("kb/", True),
    ("kb's", False), ('kb"x', False), ("kb facts", False), ("kb$HOME", False), ("fünde", False),
])
def test_kb_root_characters(tmp_path, root, ok):
    (tmp_path / "kblam.toml").write_text(f"[kb]\nroot = '''{root}'''\n", encoding="utf-8")
    if ok:
        assert load_config(root=tmp_path).findings_dir == root.rstrip("/")
    else:
        with pytest.raises(ConfigError, match=r"\[kb\] root must be /-separated segments"):
            load_config(root=tmp_path)


def test_hook_check_reports_a_hook_that_did_not_run(repo, capsys, monkeypatch):
    """With kblam missing from the shell's PATH the `||` branch answers, and init says so and exits 1."""
    real = init._run_hook

    def missing(argv, handler, payload, root, env):
        return real(argv, {**handler, "command": handler["command"].replace("kblam hook", "kblam-absent hook")},
                    payload, root, env)

    monkeypatch.setattr(init, "_run_hook", missing)
    code, out, _ = kblam_init(capsys)
    assert code == 1
    assert out.count("did not run (is kblam installed?)") == 4


# --- the kblam.toml template ----------------------------------------------------------------------


def test_template_is_a_valid_config_with_the_spec_values(tmp_path):
    (tmp_path / "kblam.toml").write_bytes((init.ASSETS / "kblam.toml").read_bytes())
    cfg = load_config(root=tmp_path)
    assert cfg.findings_dir == "findings" and cfg.evidence_roots == ("evidence",) and cfg.scopes == ("any",)
    assert (cfg.max_claim_words, cfg.max_lines) == (250, 300)
    settings = jev_settings(cfg)
    assert settings.topic_bonus == DEFAULT_JEV["topic_bonus"] == 0.2
    assert settings.key_file == DEFAULT_JEV["key_file"] == "~/kblam/jev!.txt"
    assert settings.key_env == "OPENROUTER_API_KEY"
    assert settings.embedding_model == DEFAULT_JEV["embedding_model"] == "embeddinggemma:300m"
    assert settings.ollama_url == DEFAULT_JEV["ollama_url"] == "http://127.0.0.1:11434"
    assert settings.embedding_query_prefix == "task: search result | query: {text}"
    assert settings.embedding_document_prefix == "title: none | text: {text}"
    policy = parse_policy(settings.thresholds)
    assert policy.served_model == "typesafe/jev-1.13-20260917" and policy.low_confidence_review == 0.3
    assert (policy.relation_prompt_id, policy.revision_prompt_id) == (  # the template's thresholds record
        settings.relation_prompt_id, settings.revision_prompt_id)       # its own prompt, one id per question
    assert settings.prompt_id == DEFAULT_PROMPT_ID


def test_init_reports_a_project_prompt_that_differs_and_keeps_it(repo, capsys, no_hook_check):
    """A project may edit [jev.prompt]; init says its wording is not the installed one and leaves it
    alone (SPEC §6.2, §9). Nothing else about kblam.toml is reported twice."""
    template = (init.ASSETS / "kblam.toml").read_text(encoding="utf-8")
    own = template.replace("The claims are about different subjects.", "The claims are about other subjects.")
    assert own != template
    (repo / "kblam.toml").write_text(own, encoding="utf-8")
    code, out, _ = kblam_init(capsys)
    assert code == 0 and actions(out)["[jev.prompt]"] == "kept"
    assert "differs from the installed default wording" in out
    assert (repo / "kblam.toml").read_text(encoding="utf-8") == own  # never rewritten, --update included
    code, out, _ = kblam_init(capsys, "--update")
    assert code == 0 and actions(out)["[jev.prompt]"] == "kept"
    assert (repo / "kblam.toml").read_text(encoding="utf-8") == own


def test_init_reports_no_prompt_line_when_the_wording_is_the_installed_one(repo, capsys):
    code, out, _ = kblam_init(capsys)
    assert code == 0 and "[jev.prompt]" not in out
    assert actions(out)["kblam.toml"] == "created"
