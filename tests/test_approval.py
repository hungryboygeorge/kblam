"""SPEC §8 item 4: a commit that changes kblam.toml needs a person's approval (`kblam approve-config`),
which `kblam validate --commit`, the git pre-commit hook's command, checks. Real git repositories under
tmp_path, isolated from system and global git config; nothing here touches the network."""

from __future__ import annotations

import subprocess
import sys

import pytest

from kblam import approval, cli

from conftest import KBLAM_TOML

SCOPES = 'scopes = ["MX-200", "MX-100", "any"]'
MORE_SCOPES = 'scopes = ["MX-200", "MX-100", "MX-300", "any"]'


class Terminal:
    """A stdin that is an interactive terminal: what a person at a shell has and an agent's shell does not."""

    def isatty(self) -> bool:
        return True


def git(kb, *args: str, **config: str) -> subprocess.CompletedProcess:
    settings = {"user.name": "kblam test", "user.email": "test@example.invalid", "commit.gpgsign": "false",
                **{key.replace("_", "."): value for key, value in config.items()}}
    options = [part for key, value in settings.items() for part in ("-c", f"{key}={value}")]
    return subprocess.run(["git", *options, *args], cwd=kb.root, capture_output=True, check=True)


def commit(kb, message: str = "change") -> None:
    git(kb, "add", "-A")
    git(kb, "commit", "-q", "--no-verify", "-m", message)


@pytest.fixture
def gkb(kb, monkeypatch):
    """The fixture KB as a git repository with everything committed (.kblam/ ignored, as init sets up)."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(kb.root.parent / "gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(kb.root.parent))
    kb.write(".gitignore", ".kblam/\n")
    git(kb, "init", "-q")
    commit(kb, "initial")
    return kb


def run(kb, capsys, *args: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = cli.main(["--root", str(kb.root), *args])
    out, err = capsys.readouterr()
    return code, out, err


def edit_config(kb, old: str = SCOPES, new: str = MORE_SCOPES) -> None:
    path = kb.root / "kblam.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def approve(kb, capsys, monkeypatch, answer: str = "y") -> tuple[int, str, str]:
    """`kblam approve-config` by a person at a terminal who answers `answer`."""
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    return run(kb, capsys, "approve-config")


def test_a_commit_that_leaves_kblam_toml_alone_passes(gkb, capsys):
    gkb.write("notes/plan.md", "a file outside the knowledge base\n")
    git(gkb, "add", "-A")
    code, out, _ = run(gkb, capsys, "validate", "--commit")
    assert code == 0 and "kblam validate: OK" in out


def test_a_changed_kblam_toml_is_refused_until_a_person_approves_it(gkb, capsys, monkeypatch):
    edit_config(gkb)
    git(gkb, "add", "kblam.toml")
    code, out, _ = run(gkb, capsys, "validate", "--commit")
    assert code == 1
    assert "this commit changes kblam.toml" in out and "kblam approve-config at a terminal" in out
    assert "kblam.toml needs approval before this commit" in out
    assert run(gkb, capsys, "validate")[0] == 0  # plain validate does not look at commits

    code, out, err = approve(gkb, capsys, monkeypatch)
    assert code == 0 and "approved" in out
    assert f"-{SCOPES}\n+{MORE_SCOPES}\n" in out and "kblam.toml (last commit)" in out
    assert run(gkb, capsys, "validate", "--commit")[0] == 0


def test_an_agent_shell_cannot_approve(gkb, capsys, monkeypatch):
    """No interactive terminal (an agent's Bash or PowerShell tool, a script, a pipe): nothing is approved."""
    edit_config(gkb)
    git(gkb, "add", "kblam.toml")
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("a non-interactive run must not ask"))
    code, out, err = run(gkb, capsys, "approve-config")
    assert code == 1 and "interactive terminal" in err and "asks the user" in err
    assert not approval.approvals(gkb.cfg)
    assert run(gkb, capsys, "validate", "--commit")[0] == 1


def test_answering_no_approves_nothing(gkb, capsys, monkeypatch):
    edit_config(gkb)
    code, out, _ = approve(gkb, capsys, monkeypatch, answer="n")
    assert code == 1 and "not approved" in out and not approval.approvals(gkb.cfg)


def test_an_approval_covers_only_that_version(gkb, capsys, monkeypatch):
    edit_config(gkb)
    assert approve(gkb, capsys, monkeypatch)[0] == 0
    edit_config(gkb, MORE_SCOPES, 'scopes = ["any"]')  # changed again after the approval
    git(gkb, "add", "kblam.toml")
    assert run(gkb, capsys, "validate", "--commit")[0] == 1
    edit_config(gkb, 'scopes = ["any"]', MORE_SCOPES)  # back to the approved version
    git(gkb, "add", "kblam.toml")
    assert run(gkb, capsys, "validate", "--commit")[0] == 0


def test_a_crlf_working_copy_matches_the_blob_git_stores(gkb, capsys, monkeypatch):
    """With core.autocrlf the person approves a CRLF file and git stages it with LF: the same version."""
    edit_config(gkb)
    path = gkb.root / "kblam.toml"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert approve(gkb, capsys, monkeypatch)[0] == 0
    git(gkb, "add", "kblam.toml", core_autocrlf="true")
    assert b"\r\n" not in git(gkb, "cat-file", "blob", ":kblam.toml").stdout
    assert run(gkb, capsys, "validate", "--commit")[0] == 0


def test_removing_kblam_toml_from_the_commit_is_refused(gkb, capsys):
    git(gkb, "rm", "-q", "--cached", "kblam.toml")
    code, out, _ = run(gkb, capsys, "validate", "--commit")
    assert code == 1 and "this commit removes kblam.toml" in out


def test_approve_config_with_nothing_to_approve(gkb, capsys, monkeypatch):
    code, out, _ = approve(gkb, capsys, monkeypatch)
    assert code == 0 and "needs no approval" in out and not approval.approvals(gkb.cfg)


def test_a_config_that_does_not_load_cannot_be_approved(gkb, capsys, monkeypatch):
    edit_config(gkb, "[jev]\n", '[jev]\nkey_env = "GITHUB_TOKEN"\n')
    code, _out, err = approve(gkb, capsys, monkeypatch)
    assert code == 2 and "[jev] key_env must be 'OPENROUTER_API_KEY' here" in err
    assert not approval.approvals(gkb.cfg)


def test_validate_commit_outside_git_says_so(kb, capsys, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(kb.root.parent))
    code, _out, err = run(kb, capsys, "validate", "--commit")
    assert code == 1 and "is not in a git work tree" in err


def test_the_digest_ignores_line_endings_only():
    assert approval.config_digest(b"a = 1\r\nb = 2\r\n") == approval.config_digest(b"a = 1\nb = 2\n")
    assert approval.config_digest(b"a = 1\n") != approval.config_digest(b"a = 2\n")
    assert KBLAM_TOML  # the fixture config the other tests edit
