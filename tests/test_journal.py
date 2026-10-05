"""The interrupted-write journal, `.kblam/journal.json` (SPEC §5.1.6 Interrupted writes)."""

from __future__ import annotations

import json

import pytest

from kblam.journal import begin, end, recover
from kblam.registry import read_ids, write_ids

from conftest import record_text

CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
COMMAND = "put F-0001"
MESSAGE = ("the interrupted put F-0001 may be partial: run kblam validate, fix what it reports, "
           "then kblam validate --record")
PARTIAL = b"partial write\n"  # what the interrupted write left in .kblam/tree.hash


def journal_text(command: str, paths: list[str], tree_hash: str | None) -> str:
    return json.dumps({"command": command, "paths": paths, "tree_hash": tree_hash}, indent=2) + "\n"


def tree_hash_path(kb):
    return kb.root / ".kblam" / "tree.hash"


def test_begin_records_the_command_and_paths_and_no_tree_hash(kb):
    tree_hash_path(kb).unlink()
    paths = ["findings/calibration/F-0001-sensor.md", ".kblam/review-ids"]
    begin(kb.cfg, COMMAND, paths)
    assert kb.cfg.journal_path.read_text(encoding="utf-8") == journal_text(COMMAND, paths, None)


def test_begin_records_the_tree_hash_text_as_found(kb):
    kb.add("F-0002", "sensor", CLAIM_A)
    recorded = tree_hash_path(kb).read_text(encoding="utf-8")
    assert recorded.endswith("\n")  # the file's text, not the stripped digest
    begin(kb.cfg, COMMAND, [])
    assert kb.cfg.journal_path.read_text(encoding="utf-8") == journal_text(COMMAND, [], recorded)


def test_end_deletes_the_journal_and_missing_is_fine(kb):
    begin(kb.cfg, COMMAND, [])
    end(kb.cfg)
    assert not kb.cfg.journal_path.exists()
    end(kb.cfg)


def test_recover_without_a_journal_does_nothing(kb):
    recorded = tree_hash_path(kb).read_bytes()
    calls = []
    assert recover(kb.cfg, lambda: calls.append("regenerate")) is None
    assert calls == []
    assert not kb.cfg.review_ids_path.exists()
    assert tree_hash_path(kb).read_bytes() == recorded


def test_recover_regenerates_first_registers_the_records_that_exist_and_restores_tree_hash(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write("research-review/INDEX.md", "# review index\n")
    kb.write("research-review/challenges/notes.md", "not a record\n")
    kb.write("research-review/challenges/SC-0001.yaml", record_text("SC"))
    recorded = tree_hash_path(kb).read_text(encoding="utf-8")
    paths = ["research-review/challenges/SC-0001.yaml",    # a record, on disk
             "research-review/tasks/CT-0001.yaml",         # a record path the write never reached
             "research-review/INDEX.md",                   # the review root's index
             "research-review/challenges/notes.md",        # a review file that is not a record
             "findings/calibration/F-0001-sensor.md"]      # a findings file
    begin(kb.cfg, COMMAND, paths)
    tree_hash_path(kb).write_bytes(PARTIAL)
    calls = []

    assert recover(kb.cfg, lambda: calls.append("regenerate")) == MESSAGE

    assert calls == ["regenerate"]
    assert kb.cfg.review_ids_path.read_bytes() == b'["SC-0001"]\n'
    assert not kb.cfg.journal_path.exists()
    assert tree_hash_path(kb).read_text(encoding="utf-8") == recorded


def test_recover_unions_the_found_ids_with_a_registry_that_exists(kb):
    write_ids(kb.cfg, {"CT-0002"})
    kb.write("research-review/uses/CU-0003.yaml", record_text("CU"))
    begin(kb.cfg, COMMAND, ["research-review/uses/CU-0003.yaml"])
    assert recover(kb.cfg, lambda: None) == MESSAGE
    assert read_ids(kb.cfg) == {"CT-0002", "CU-0003"}
    assert kb.cfg.review_ids_path.read_bytes() == b'["CT-0002", "CU-0003"]\n'


def test_recover_creates_no_registry_when_no_listed_record_exists(kb):
    kb.write("research-review/INDEX.md", "# review index\n")
    begin(kb.cfg, COMMAND, ["research-review/tasks/CT-0001.yaml", "research-review/INDEX.md"])
    assert recover(kb.cfg, lambda: None) == MESSAGE
    assert not kb.cfg.review_ids_path.exists()


def test_recover_deletes_tree_hash_when_none_was_recorded(kb):
    tree_hash_path(kb).unlink()
    begin(kb.cfg, COMMAND, [])
    tree_hash_path(kb).write_bytes(PARTIAL)
    assert recover(kb.cfg, lambda: None) == MESSAGE
    assert not tree_hash_path(kb).exists()
    assert not kb.cfg.journal_path.exists()


def test_recover_restores_the_recorded_tree_hash_byte_for_byte(kb):
    recorded = tree_hash_path(kb).read_bytes()
    begin(kb.cfg, COMMAND, [])
    tree_hash_path(kb).write_bytes(PARTIAL)

    def regenerate():
        assert tree_hash_path(kb).read_bytes() == PARTIAL  # the restore comes after the indexes

    assert recover(kb.cfg, regenerate) == MESSAGE
    assert tree_hash_path(kb).read_bytes() == recorded


def test_a_failing_regenerate_leaves_the_journal_and_tree_hash(kb):
    tree_hash_path(kb).unlink()
    begin(kb.cfg, COMMAND, [])
    tree_hash_path(kb).write_bytes(PARTIAL)
    journal = kb.cfg.journal_path.read_bytes()

    def regenerate():
        raise OSError("the index write failed")

    with pytest.raises(OSError, match="the index write failed"):
        recover(kb.cfg, regenerate)
    assert kb.cfg.journal_path.read_bytes() == journal
    assert tree_hash_path(kb).read_bytes() == PARTIAL
    assert not kb.cfg.review_ids_path.exists()


def test_an_unreadable_journal_raises_and_stays(kb):
    kb.write(".kblam/journal.json", "{not json\n")
    calls = []
    with pytest.raises(ValueError, match="journal.json"):
        recover(kb.cfg, lambda: calls.append("regenerate"))
    assert calls == []
    assert kb.cfg.journal_path.read_bytes() == b"{not json\n"


@pytest.mark.parametrize("data", [
    {"command": 1, "paths": [], "tree_hash": None},
    {"command": "put F-0001", "paths": "findings/", "tree_hash": None},
    {"command": "put F-0001", "paths": [1], "tree_hash": None},
    {"command": "put F-0001", "paths": [], "tree_hash": 3},
    {"command": "put F-0001", "paths": []},
])
def test_a_journal_of_the_wrong_shape_raises_and_stays(kb, data):
    text = json.dumps(data)
    kb.write(".kblam/journal.json", text)
    with pytest.raises(ValueError, match="journal.json"):
        recover(kb.cfg, lambda: None)
    assert kb.cfg.journal_path.read_text(encoding="utf-8") == text


def test_recover_does_not_touch_a_findings_file_or_its_own_registry_twice(kb):
    """Nothing but the registry and tree.hash is written: an unrelated change in the roots is not accepted."""
    kb.add("F-0001", "sensor", CLAIM_A)
    finding = kb.root / "findings" / "calibration" / "F-0001-sensor.md"
    before = {p: p.read_bytes() for p in (finding, kb.root / "findings" / "INDEX.md")}
    begin(kb.cfg, COMMAND, ["findings/calibration/F-0001-sensor.md"])
    recover(kb.cfg, lambda: None)
    assert {p: p.read_bytes() for p in before} == before
