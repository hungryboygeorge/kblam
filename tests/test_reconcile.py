"""The joins between finding v2 and review records: fingerprints, committed roots and state refusal."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from kblam import cli, hook, receipts, registry, review_stage, rules, treehash
from kblam.finding import fingerprint, yaml_rt
from kblam.view import load_view

from conftest import record_text
from test_approval import git, gkb  # noqa: F401 (gkb is a fixture)

CLAIM = "The media tray reports its type through two contact pins read at load time."


@pytest.mark.parametrize("args", [
    ["challenge", "new", "missing.txt", "--lines", "1-1", "--by", "author"],
    ["task", "new", "F-0001", "--kind", "replication", "--by", "author", "--proponent", "author"],
    ["use", "review", "SC-0001", "F-0001", "1", "--by", "author", "--proponent", "author"],
    ["review", "index"],
    ["put", ".kblam/review-staging/SC-0001.yaml"],
], ids=["challenge", "task", "use", "review", "record-put"])
def test_record_command_families_refuse_tracked_state_before_reading_or_writing_it(gkb, capsys, args):
    gkb.write(".kblam/review-receipts/foreign.json", "not trusted\n")
    git(gkb, "add", "-f", ".kblam/review-receipts/foreign.json")
    before = {p.relative_to(gkb.cfg.state_dir).as_posix(): p.read_bytes()
              for p in gkb.cfg.state_dir.rglob("*") if p.is_file()}
    capsys.readouterr()

    assert cli.main(["--root", str(gkb.root), *args]) == 1

    assert "under .kblam/, which holds this machine's own state" in capsys.readouterr().err
    after = {p.relative_to(gkb.cfg.state_dir).as_posix(): p.read_bytes()
             for p in gkb.cfg.state_dir.rglob("*") if p.is_file()}
    assert after == before


def test_task_and_allocation_receipt_use_v2_with_the_configured_scope_separator(kb):
    config = kb.root / "kblam.toml"
    kb.write("kblam.toml", config.read_text(encoding="utf-8").replace(
        "[jev]", 'scope_separator = ":"\n[jev]', 1))
    kb.add("F-0001", "tray", CLAIM, scope="[MX-100:MX-200]")
    finding = load_view(kb.cfg).findings[0]

    staged = review_stage.task_new(kb.cfg, "F-0001", "replication", "reviewer-a", "author-a")

    data = yaml_rt().load(staged.read_text(encoding="utf-8"))
    receipt = json.loads((kb.cfg.review_receipts_dir / "CT-0001.json").read_text(encoding="utf-8"))
    expected = fingerprint(finding, kb.cfg.scope_separator)
    assert len(expected) == 12 and expected != fingerprint(finding, "/")
    assert data["claim_fingerprint"] == receipt["claim_fingerprint"] == expected


def test_validate_commit_refuses_review_files_not_held_in_the_commit(gkb, capsys):
    gkb.write("research-review/INDEX.md", "not a generated index\n")
    capsys.readouterr()

    assert cli.main(["--root", str(gkb.root), "validate", "--commit"]) == 1

    out = capsys.readouterr().out
    assert "K13" in out
    assert "this commit does not hold the files under research-review/ as they are on disk" in out
    assert "research-review/INDEX.md (untracked)" in out


def test_stop_does_not_validate_a_forged_registry_while_git_tracks_state(gkb, capsys):
    gkb.write(".kblam/review-ids", '["SC-9999"]\n')
    git(gkb, "add", "-f", ".kblam/review-ids")
    view = load_view(gkb.cfg)
    assert any("SC-9999" in issue.message for issue in rules.validate(view))
    capsys.readouterr()

    assert hook._stop(gkb.cfg, "Stop", {"stop_hook_active": False}) == 0

    out = capsys.readouterr().out
    assert "git tracks" in out
    assert "SC-9999" not in out
    assert not rules.validate(view, trust_state=False)


def test_untrusted_state_validation_never_reads_registry_marker_or_receipts(kb, monkeypatch):
    kb.write("research-review/challenges/SC-0001.yaml", record_text("SC"))
    kb.write("research-review/tasks/CT-0001.yaml", record_text("CT"))

    def forbidden(*args, **kwargs):
        pytest.fail("validation read untrusted .kblam/ state")

    monkeypatch.setattr(registry, "read_ids", forbidden)
    monkeypatch.setattr(treehash, "read_recorded", forbidden)
    monkeypatch.setattr(receipts, "read_allocation", forbidden)

    issues = rules.validate(load_view(kb.cfg), trust_state=False)

    assert any(issue.code == "K13" for issue in issues)  # on-disk record checks still run


@pytest.mark.parametrize("candidate", [b"candidate source\n", None], ids=["overlay", "absent"])
def test_k10_uses_candidate_review_root_bytes_not_the_working_copy(kb, candidate):
    path = "research-review/challenges/SC-0001.yaml"
    kb.write(path, "working source\n")
    kb.add("F-0001", "tray", CLAIM,
           body=f"<!-- verbatim: {path}:1 -->\n> candidate source")
    before = load_view(kb.cfg)
    assert [issue for issue in rules.k10_verbatim(before)]
    after = replace(before, review_files={} if candidate is None else {path: candidate}, memo={})

    issues = rules.k10_verbatim(after)

    if candidate is None:
        assert len(issues) == 1 and "does not exist" in issues[0].message
    else:
        assert issues == []
