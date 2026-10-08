"""The joins between finding v2 and review records: fingerprints, committed roots and state refusal."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from kblam import cli, decisions, hook, k13, receipts, registry, review_stage, rules, treehash
from kblam.review_index import generate_review_index
from kblam.sources import SourceReader, State, sha256_hex
from kblam.finding import fingerprint, yaml_rt
from kblam.view import load_view

from conftest import dump_record, record_data, record_text
from test_approval import git, gkb  # noqa: F401 (gkb is a fixture)

CLAIM = "The media tray reports its type through two contact pins read at load time."
# The step every stale or unavailable reference message ends with (SPEC §5.2.3 Evaluation).
def retire_pinned(path: str) -> str:
    """The step a "the pinned version is not present" message ends with, naming the file the record reads
    (SPEC §5.2.3 Evaluation)."""
    return (f"; restore the pinned bytes of {path}, or retire the record and file a new one (kblam review "
            f"decide source-challenge-0001 --status stale --by NAME --reason TEXT --expect D)")


@pytest.mark.parametrize("args", [
    ["challenge", "new", "missing.txt", "--lines", "1-1", "--by", "author"],
    ["task", "new", "F-0001", "--kind", "replication", "--by", "author", "--proponent", "author"],
    ["use", "review", "source-challenge-0001", "F-0001", "1", "--by", "author", "--proponent", "author"],
    ["review", "index"],
    ["put", ".kblam/review-staging/source-challenge-0001.yaml"],
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
    receipt = json.loads((kb.cfg.review_receipts_dir / "claim-task-0001.json").read_text(encoding="utf-8"))
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
    gkb.write(".kblam/review-ids", '["source-challenge-9999"]\n')
    git(gkb, "add", "-f", ".kblam/review-ids")
    view = load_view(gkb.cfg)
    assert any("source-challenge-9999" in issue.message for issue in rules.validate(view))
    capsys.readouterr()

    assert hook._stop(gkb.cfg, "Stop", {"stop_hook_active": False}) == 0

    out = capsys.readouterr().out
    assert "git tracks" in out
    assert "source-challenge-9999" not in out
    assert not rules.validate(view, trust_state=False)


def test_untrusted_state_validation_never_reads_registry_marker_or_receipts(kb, monkeypatch):
    kb.write("research-review/challenges/source-challenge-0001.yaml", record_text("source-challenge"))
    kb.write("research-review/tasks/claim-task-0001.yaml", record_text("claim-task"))
    kb.write("research-review/uses/checked-use-0001.yaml", record_text("checked-use"))
    physical_reads = _state_reads(kb.cfg, monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("validation read untrusted .kblam/ state")

    monkeypatch.setattr(registry, "read_ids", forbidden)
    monkeypatch.setattr(treehash, "read_recorded", forbidden)
    monkeypatch.setattr(receipts, "read_allocation", forbidden)

    issues = rules.validate(load_view(kb.cfg), trust_state=False)

    assert any(issue.code == "K13" for issue in issues)  # on-disk record checks still run
    assert physical_reads == []


def _state_reads(cfg, monkeypatch):
    """Count actual file opens, including read_bytes/read_text, below the resolved state root."""
    reads = []
    original = Path.open

    def counted(path, mode="r", *args, **kwargs):
        if path.resolve().is_relative_to(cfg.state_dir.resolve()) and ("r" in mode or "+" in mode):
            reads.append(path.resolve())
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted)
    return reads


def _state_reference_scene(kb, location, *, confirmed=False):
    """Valid references with real bytes; only the chosen reference points into machine state."""
    data = b"quoted assertion\n"
    state_path = ".kblam/foreign-evidence.txt"
    ordinary_path = "evidence/assertion.txt"
    kb.write(state_path, data)
    kb.write(ordinary_path, data)
    kb.add("F-0001", "tray", CLAIM)
    ref = {"path": ordinary_path, "sha256": sha256_hex(data), "repo": None,
           "commit": None, "blob": None, "snapshot": None}
    challenge = record_data("source-challenge")
    challenge["source"] = {**ref, "assertion": {"lines": [1, 1], "text": "quoted assertion",
                                               "sha256": sha256_hex(b"quoted assertion"),
                                               "occurrence": 1}}
    challenge["basis"] = [{**ref, "locator": "line 1", "role": "counterevidence",
                           "provenance": "observed"}]
    if location == "source":
        challenge["source"]["path"] = state_path
    elif location == "basis":
        challenge["basis"][0]["path"] = state_path
    elif location == "snapshot":
        challenge["source"].update(path="evidence/missing-assertion.txt", snapshot=state_path)
    elif location == "decision":
        finding = load_view(kb.cfg).findings[0]
        task = record_data("claim-task", status="inconclusive",
                           claim_fingerprint=fingerprint(finding, kb.cfg.scope_separator),
                           base_file_sha256=sha256_hex(finding.raw))
        task["decisions"] = [{"date": "2026-09-28", "by": "reviewer-b", "status": "inconclusive",
                              "reason": "The capture is inconclusive.",
                              "bind": decisions.subject_digest("claim-task", task),
                              "evidence": [{**ref, "path": state_path, "locator": "line 1",
                                            "provenance": "observed"}]}]
        kb.write("research-review/tasks/claim-task-0001.yaml", dump_record(task))
    if confirmed:
        challenge["status"] = "confirmed"
        challenge["source"]["snapshot"] = state_path
        challenge["decisions"] = [{"date": "2026-09-28", "by": "reviewer-b", "status": "confirmed",
                                   "reason": "The assertion is contradicted by the capture.",
                                   "bind": decisions.subject_digest("source-challenge", challenge), "evidence": []}]
    kb.write("research-review/challenges/source-challenge-0001.yaml", dump_record(challenge))
    view = load_view(kb.cfg)
    kb.write("research-review/INDEX.md", generate_review_index(view))
    return load_view(kb.cfg)


@pytest.mark.parametrize("location", ["source", "basis", "decision", "snapshot"])
def test_untrusted_validation_never_opens_state_references(kb, monkeypatch, location):
    view = _state_reference_scene(kb, location)
    # Trusted mode reads these paths normally, but machine state cannot count as primary support.
    assert rules.validate(view) == []
    challenge = next(rec for rec in view.records if rec.kind == "source-challenge")
    if location == "basis":
        assert not k13._is_primary(view, challenge.data["basis"][0])
    if location in ("source", "snapshot"):
        trusted = k13.challenge_info(view, SourceReader(kb.cfg, view), challenge)
        assert trusted.resolved.state is (State.CURRENT if location == "source" else State.PINNED)
        assert trusted.resolved.error is None
    reads = _state_reads(kb.cfg, monkeypatch)

    issues = rules.validate(view, trust_state=False)

    assert reads == []
    availability = [issue for issue in issues
                    if "cannot be read here" in issue.message or "the pinned version" in issue.message]
    assert len(availability) == 1
    issue = availability[0]
    if location == "decision":
        assert issue.code == "K15"
        # The evidence's bytes are under .kblam/, which this check does not read, so no restore can put
        # them back from this clone: the line names only the step that can work here (K15's recovery).
        assert issue.message == (
            "the evidence '.kblam/foreign-evidence.txt' of claim-task-0001's effective decision cannot be read "
            "here: this check reads no file under .kblam/, so no restore puts it back. Run kblam review "
            "rebind claim-task-0001 --by NAME --reason TEXT --expect D --evidence PROVENANCE:PATH:LOCATOR")
    else:
        assert issue.code == "K13"
        prefix = "basis[0]: " if location == "basis" else ""
        # The refused path is the reference's own (source, basis) or its snapshot's: either way a path
        # under .kblam/, which this check reads nothing from while machine state is not trusted. The
        # restore the usual line names cannot work here, so only the retire step is named.
        expected = (prefix + "the pinned version .kblam/foreign-evidence.txt cannot be read here: this "
                    "check reads no file under .kblam/, so no restore puts it back. Retire the record and "
                    "file a new one (kblam review decide source-challenge-0001 --status stale --by NAME --reason TEXT "
                    "--expect D)")
        assert issue.message == expected


def test_untrusted_use_recovery_keeps_its_projected_reader_and_receipts_untrusted(kb, monkeypatch):
    _state_reference_scene(kb, "source")
    use = record_data("checked-use")  # broken bindings reach the proposed recovery's structural checks
    use["decisions"] = [{"date": "2026-09-28", "by": "reviewer-b", "status": "open",
                         "reason": "Review the use.", "bind": decisions.subject_digest("checked-use", use),
                         "evidence": [{"path": ".kblam/foreign-evidence.txt",
                                       "sha256": sha256_hex(b"quoted assertion\n"), "repo": None,
                                       "commit": None, "blob": None, "snapshot": None,
                                       "locator": "line 1", "provenance": "observed"}]}]
    kb.write("research-review/uses/checked-use-0001.yaml", dump_record(use))
    kb.write(".kblam/review-receipts/checked-use-0001.json", '{"creator": "foreign"}\n')
    view = load_view(kb.cfg)
    kb.write("research-review/INDEX.md", generate_review_index(view))
    view = load_view(kb.cfg)
    reads = _state_reads(kb.cfg, monkeypatch)

    issues = rules.validate(view, trust_state=False)

    assert reads == []
    assert any(issue.owner == "checked-use-0001" and "restore source-challenge-0001's source" in issue.message for issue in issues)
    assert not any("allocated as" in issue.message for issue in issues)


def test_tracked_state_stop_never_opens_a_challenge_source_in_state(gkb, capsys, monkeypatch):
    """D49 for the line the Stop hook gives while git tracks `.kblam/`: the source under `.kblam/` is not
    read at all, so the line names no restore of it (that cannot work here) and names only the retire
    step, which runs once the state the block's own text names is dealt with."""
    view = _state_reference_scene(gkb, "source", confirmed=True)
    assert rules.validate(view) == []
    git(gkb, "add", "-f", ".kblam/foreign-evidence.txt")
    cfg = gkb.cfg
    gkb.write(".kblam/stop-block", "old digest\n")
    reads = _state_reads(cfg, monkeypatch)
    capsys.readouterr()

    assert hook._stop(cfg, "Stop", {"stop_hook_active": False}) == 0

    out = capsys.readouterr().out
    assert "git tracks" in out
    assert ("the pinned version .kblam/foreign-evidence.txt cannot be read here: this check reads no file "
            "under .kblam/, so no restore puts it back. Retire the record and file a new one (kblam review "
            "decide source-challenge-0001 --status stale --by NAME --reason TEXT --expect D)") in out
    assert "restore the pinned bytes" not in out
    assert reads == [cfg.state_dir.resolve() / "stop-block"]  # hook-owned marker remains allowed


@pytest.mark.parametrize("candidate", [b"candidate source\n", None], ids=["overlay", "absent"])
def test_k10_uses_candidate_review_root_bytes_not_the_working_copy(kb, candidate):
    path = "research-review/challenges/source-challenge-0001.yaml"
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
