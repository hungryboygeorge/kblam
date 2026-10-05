"""M6.11 U1: canonical path keys and refusals (SPEC §5.2.2 Paths)."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML
from kblam.paths import PathRefused, canonical_key, protected, resolve, syntax_problem

LOG = "evidence/2026-09-22-ratio/log.txt"  # written by the kb fixture

# Every refusal form, with the message the path is refused with.
REFUSALS = [
    ("", "empty path"),
    ("/etc/passwd", "absolute path"),
    ("\\x", "absolute path"),
    ("C:/x", "drive letter"),
    ("c:\\x", "drive letter"),
    ("C:x", "drive-relative path"),
    ("\\\\host\\share", "UNC path"),
    ("//host/share", "UNC path"),
    ("evidence/2026-09-22-ratio/log.txt:zone", "':' (an alternate data stream)"),
    ("notes:x", "':' (an alternate data stream)"),
    ("..", "'..' segment"),
    ("../x", "'..' segment"),
    ("evidence/../../x", "'..' segment"),
    ("evidence\\..\\x", "'..' segment"),
]

# Spellings that are relative paths, whether or not their target exists.
ACCEPTED = ["a", "a/b", "./a", ".\\a", "a\\b", "a//b", "a/./b", "a/", ".", "..a", "a..b"]


def _link(target: Path, link: Path, *, directory: bool = False) -> Path:
    """A symlink at `link`; a skip where the OS refuses to make one (Windows without the privilege)."""
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    return link


@pytest.mark.parametrize("raw, message", REFUSALS)
def test_syntax_problem_refuses(raw, message):
    assert syntax_problem(raw) == message


@pytest.mark.parametrize("raw", ACCEPTED)
def test_syntax_problem_accepts_relative_spellings(raw):
    assert syntax_problem(raw) is None


@pytest.mark.parametrize("raw, message", REFUSALS)
def test_resolve_and_canonical_key_refuse(kb, raw, message):
    for call in (resolve, canonical_key):
        with pytest.raises(PathRefused) as excinfo:
            call(kb.cfg, raw)
        assert str(excinfo.value) == message


def test_one_key_for_every_spelling(kb):
    cfg = kb.cfg
    assert canonical_key(cfg, LOG) == LOG
    for raw in ("./" + LOG, LOG.replace("/", "\\"), "./" + LOG.replace("/", "\\"),
                LOG.replace("/", "/./"), LOG.replace("/", "//"), LOG + "/"):
        assert canonical_key(cfg, raw) == LOG


def test_resolve_is_absolute_and_need_not_exist(kb):
    cfg = kb.cfg
    assert resolve(cfg, LOG) == (kb.root / LOG).resolve()
    assert resolve(cfg, "notes/not-written-yet.txt") == (kb.root / "notes/not-written-yet.txt").resolve()
    assert resolve(cfg, "./" + LOG) == resolve(cfg, LOG)


def test_repository_root_itself_is_refused(kb):
    cfg = kb.cfg
    assert resolve(cfg, ".") == cfg.repo_root.resolve()  # a target, but not a key
    for raw in (".", "./"):
        with pytest.raises(PathRefused) as excinfo:
            canonical_key(cfg, raw)
        assert str(excinfo.value) == "the repository root itself"


def test_symlink_alias_gives_one_key(kb):
    cfg = kb.cfg
    _link(Path(LOG), kb.root / "log-link.txt")
    assert canonical_key(cfg, "log-link.txt") == LOG


def test_escaping_symlink_refused(kb, tmp_path):
    cfg = kb.cfg
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")
    _link(tmp_path / "outside.txt", kb.root / "escape.txt")
    _link(Path(".."), kb.root / "up", directory=True)
    for raw in ("escape.txt", "escape.txt/", "up/outside.txt"):
        with pytest.raises(PathRefused) as excinfo:
            canonical_key(cfg, raw)
        assert str(excinfo.value) == "resolves outside the repository"


def test_escaping_directory_symlink_refused(kb, tmp_path):
    cfg = kb.cfg
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("x\n", encoding="utf-8")
    _link(outside, kb.root / "escape", directory=True)
    with pytest.raises(PathRefused) as excinfo:
        resolve(cfg, "escape/x.txt")
    assert str(excinfo.value) == "resolves outside the repository"


@pytest.mark.parametrize("raw, expected", [
    ("findings", "findings"),
    ("findings/F-0001-x.md", "findings"),
    ("research-review", "review"),
    ("research-review/challenges/SC-0001.yaml", "review"),
    (".kblam", "state"),
    (".kblam/review-ids", "state"),
    ("history/2025-01-old.md", "history"),
    ("evidence/2026-09-22-ratio/log.txt", None),
    ("README.md", None),
])
def test_protected_roots(kb, raw, expected):
    assert protected(kb.cfg, resolve(kb.cfg, raw)) == expected


def test_protected_follows_a_symlink_into_findings(kb):
    cfg = kb.cfg
    findings = kb.root / "findings"
    findings.mkdir(parents=True, exist_ok=True)
    alias = _link(findings, kb.root / "alias", directory=True)
    assert canonical_key(cfg, "alias/task.md") == canonical_key(cfg, "findings/task.md")  # one key
    assert protected(cfg, resolve(cfg, "alias/task.md")) == "findings"
    assert protected(cfg, alias / "task.md") == "findings"  # resolved here too, so no escape


def test_protected_returns_the_first_matching_root(kb):
    kb.write("kblam.toml", KBLAM_TOML + 'history_dirs = ["findings"]\n' + NO_EMBEDDINGS + PROMPT_TOML)
    assert protected(kb.cfg, resolve(kb.cfg, "findings/F-0001-x.md")) == "findings"


def test_history_dirs_come_from_config(kb):
    kb.write("kblam.toml", KBLAM_TOML + 'history_dirs = ["archive/old"]\n' + NO_EMBEDDINGS + PROMPT_TOML)
    cfg = kb.cfg
    assert cfg.history_dirs == ("archive/old",)
    assert protected(cfg, resolve(cfg, "archive/old/2025-01.md")) == "history"
    assert protected(cfg, resolve(cfg, "history/2025-01.md")) is None


def test_key_case_folded_on_windows_only(kb):
    folded = canonical_key(kb.cfg, LOG.upper())
    assert folded == (LOG if os.name == "nt" else LOG.upper())


def test_protected_case_insensitive_on_windows_only(kb):
    cfg = kb.cfg
    target = resolve(cfg, "FINDINGS/F-0001-x.md")
    assert protected(cfg, target) == ("findings" if os.name == "nt" else None)


def test_repo_root_spelt_through_a_symlink(kb, tmp_path):
    real = kb.root.resolve()
    link = _link(real, tmp_path / "repo-link", directory=True)
    cfg = dataclasses.replace(kb.cfg, repo_root=link)  # the root itself, as a symlink spelling
    assert canonical_key(cfg, LOG) == LOG
    assert resolve(cfg, LOG) == real / LOG
    assert protected(cfg, resolve(cfg, "findings/F-0001-x.md")) == "findings"
