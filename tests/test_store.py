"""new / edit / put / index workflows."""

from __future__ import annotations

import hashlib

import pytest

from kblam.store import StoreError, edit_finding, new_finding, put
from kblam.treehash import current_digest, read_tree_hash

from conftest import finding_text

CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."


def fill(path, claim, *, scope="[MX-200]", evidence="[evidence/2026-09-22-ratio/]", body=""):
    """Fill a `kblam new` skeleton the way an agent would."""
    text = path.read_text(encoding="utf-8")
    text = text.replace("label:  ", "label: observed  ", 1)
    text = text.replace("scope: []", f"scope: {scope}", 1)
    text = text.replace("evidence: []", f"evidence: {evidence}", 1)
    text = text.replace("**Claim.**\n", f"**Claim.** {claim}\n" + (f"\n{body}\n" if body else ""))
    path.write_text(text, encoding="utf-8", newline="\n")


def test_new_fill_put_validate(kb):
    staged = new_finding(kb.cfg, "calibration", "sensor curve types are not analog gains")
    assert staged.parent == kb.cfg.staging_dir
    assert staged.name == "F-0001-sensor-curve-types-are-not-analog-gains.md"

    unfilled = put(kb.cfg, staged)
    assert not unfilled.ok  # an untouched skeleton is rejected
    assert {i.code for i in unfilled.issues} >= {"K1", "K6"}

    fill(staged, CLAIM_A)
    result = put(kb.cfg, staged)
    assert result.ok, [i.format(result.view) for i in result.issues]
    target = kb.findings / "calibration" / staged.name
    assert target.is_file()
    assert not staged.exists()
    assert kb.issues() == []
    index = (kb.findings / "INDEX.md").read_text(encoding="utf-8")
    assert "[F-0001](calibration/F-0001-sensor-curve-types-are-not-analog-gains.md)" in index
    assert "sensor curve types are not analog gains" in index
    assert read_tree_hash(kb.cfg) == current_digest(kb.cfg)


def test_new_never_reuses_a_staged_id(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    first = new_finding(kb.cfg, "motor", "Motor warm-up")
    second = new_finding(kb.cfg, "motor", "Motor warm-up")
    assert first.name.startswith("F-0002-")
    assert second.name.startswith("F-0003-")


def test_new_rejects_bad_topic(kb):
    with pytest.raises(StoreError, match="topic"):
        new_finding(kb.cfg, "../escape", "Title")


def test_edit_put_rewrites_in_place_with_slug_change(kb):
    old = kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    staged = edit_finding(kb.cfg, "F-0001")
    assert staged.read_bytes() == old.read_bytes()

    renamed = staged.with_name("F-0001-sensor-curve-ratio.md")
    text = staged.read_text(encoding="utf-8").replace("about 0.1%", "within 0.2%")
    renamed.write_text(text, encoding="utf-8", newline="\n")
    staged.unlink()

    result = put(kb.cfg, renamed)
    assert result.ok, [i.format(result.view) for i in result.issues]
    assert not old.exists()
    new_path = kb.findings / "calibration" / "F-0001-sensor-curve-ratio.md"
    assert "within 0.2%" in new_path.read_text(encoding="utf-8")
    assert sorted(p.name for p in kb.findings.rglob("F-*.md")) == [
        "F-0001-sensor-curve-ratio.md", "F-0002-motor.md"]
    assert result.removed == ["findings/calibration/F-0001-sensor.md"]
    assert kb.issues() == []


def test_edit_put_topic_change_moves_file_and_drops_empty_folder(kb):
    old = kb.add("F-0001", "sensor", CLAIM_A)
    staged = edit_finding(kb.cfg, "F-0001")
    staged.write_text(staged.read_text(encoding="utf-8").replace("topic: calibration", "topic: sensor"),
                      encoding="utf-8", newline="\n")
    assert put(kb.cfg, staged).ok
    assert not old.exists() and not old.parent.exists()
    assert (kb.findings / "sensor" / "F-0001-sensor.md").is_file()
    assert kb.issues() == []


def test_edit_refuses_second_staging_and_unknown_id(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    edit_finding(kb.cfg, "F-0001")
    with pytest.raises(StoreError, match="already staged"):
        edit_finding(kb.cfg, "F-0001")
    with pytest.raises(StoreError, match="not in findings/"):
        edit_finding(kb.cfg, "F-0042")


def test_put_with_k4_term_leaves_findings_byte_identical(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    before = kb.snapshot()
    hash_before = read_tree_hash(kb.cfg)
    staged = new_finding(kb.cfg, "motor", "Motor warm-up")
    fill(staged, "The motor warm-up drift was previously believed to settle within 30 seconds.")
    result = put(kb.cfg, staged)
    assert not result.ok
    assert [i.code for i in result.issues] == ["K4"]
    formatted = result.issues[0].format(result.view)
    assert formatted.startswith(f"K4 .kblam/staging/{staged.name}:")
    assert kb.snapshot() == before
    assert read_tree_hash(kb.cfg) == hash_before
    assert staged.exists()


def test_put_rejects_near_duplicate_and_names_existing_finding(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    staged = new_finding(kb.cfg, "calibration", "Curve types again")
    fill(staged, CLAIM_A.replace("about", "roughly"))
    before = kb.snapshot()
    result = put(kb.cfg, staged)
    assert [i.code for i in result.issues] == ["K9"]
    assert "edit F-0001 instead" in result.issues[0].message
    assert kb.snapshot() == before


def test_put_warns_about_errors_already_in_the_kb(kb):
    """SPEC §7 put: an error that was there before the put is a warning, not a refusal."""
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write("findings/summary.md", "# stray\n")
    staged = new_finding(kb.cfg, "motor", "Motor warm-up")
    fill(staged, CLAIM_B)
    result = put(kb.cfg, staged)
    assert result.ok and result.issues == []
    assert [(i.code, i.path) for i in result.warnings] == [("K8", "findings/summary.md")]


def test_put_regenerates_hand_edited_index(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    (kb.findings / "INDEX.md").write_text("hand edited\n", encoding="utf-8")
    staged = new_finding(kb.cfg, "motor", "Motor warm-up")
    fill(staged, CLAIM_B)
    assert put(kb.cfg, staged).ok
    assert kb.issues() == []


def test_put_refuses_files_already_in_findings(kb):
    path = kb.add("F-0001", "sensor", CLAIM_A)
    with pytest.raises(StoreError, match="already under findings/"):
        put(kb.cfg, path)


def test_put_rejects_unusable_topic_before_touching_anything(kb):
    staged = kb.write(".kblam/staging/F-0001-x.md", finding_text("F-0001", CLAIM_A, topic="../up"))
    with pytest.raises(StoreError, match="topic"):
        put(kb.cfg, staged)


def test_tree_hash_covers_paths_and_bytes(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    digest = current_digest(kb.cfg)
    assert digest == read_tree_hash(kb.cfg)
    assert len(digest) == len(hashlib.sha256().hexdigest())
    kb.write("findings/calibration/notes.txt", "x")
    assert current_digest(kb.cfg) != digest
