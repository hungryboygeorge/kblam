"""The console entry point: exit codes, root discovery and agent-facing output."""

from __future__ import annotations

import re

from kblam.cli import main

from conftest import DEFAULT_PROMPT_ID
from test_store import CLAIM_A, fill


def test_cli_new_put_validate_from_subdirectory(kb, monkeypatch, capsys):
    subdir = kb.root / "evidence"
    monkeypatch.chdir(subdir)  # root is found by walking up to kblam.toml

    assert main(["new", "calibration", "sensor curve types"]) == 0
    staged = kb.root / capsys.readouterr().out.strip()
    fill(staged, CLAIM_A)

    assert main(["put", str(staged)]) == 0
    assert "F-0001 -> findings/calibration/F-0001-sensor-curve-types.md" in capsys.readouterr().out
    assert main(["validate"]) == 0
    assert "OK (1 findings)" in capsys.readouterr().out


def test_cli_put_rejection_exits_nonzero(kb, capsys):
    root = ["--root", str(kb.root)]
    assert main(root + ["new", "calibration", "Superseded types"]) == 0
    staged = capsys.readouterr().out.strip()
    fill(kb.root / staged, CLAIM_A)
    assert main(root + ["put", staged]) == 1
    out = capsys.readouterr().out
    assert 'K4 .kblam/staging/F-0001-superseded-types.md:3: revision-history language "supersed"' in out
    assert "findings/ is unchanged" in out


def test_cli_validate_reports_hand_edit(kb, capsys):
    (kb.findings / "INDEX.md").write_text("edited\n", encoding="utf-8")
    assert main(["--root", str(kb.root), "validate"]) == 1
    assert "K7 findings/INDEX.md: INDEX.md differs" in capsys.readouterr().out
    assert main(["--root", str(kb.root), "index"]) == 0
    assert main(["--root", str(kb.root), "validate"]) == 0


def test_cli_without_config_explains(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["validate"]) == 2
    assert "no kblam.toml found" in capsys.readouterr().err


def test_cli_prompt_id_prints_the_configs_prompt_id(kb, capsys):
    """`kblam prompt-id`: the id of this project's wording, what [jev.thresholds] must record."""
    assert main(["--root", str(kb.root), "prompt-id"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"kblam prompt-id: {DEFAULT_PROMPT_ID}"

    toml = (kb.root / "kblam.toml").read_text(encoding="utf-8")
    changed = toml.replace("The claims are about different subjects.", "The claims are about other subjects.")
    assert changed != toml
    kb.write("kblam.toml", changed)
    assert main(["--root", str(kb.root), "prompt-id"]) == 0
    out = capsys.readouterr().out
    assert re.match(r"kblam prompt-id: [0-9a-f]{12}\n", out) and DEFAULT_PROMPT_ID not in out
