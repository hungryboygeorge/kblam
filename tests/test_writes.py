"""The shared write frame (SPEC §5.1.6): `writes.locked` and `writes.apply`.

`locked` is `.kblam/lock` plus journal recovery plus (for a mutating command) the root and registry
refusal; `apply` writes a planned change in the SPEC's order and journals a change of more than one
file. Everything here runs against the plain `kb` fixture: no source repository and no Jev.
"""

from __future__ import annotations

import json

import pytest

from kblam import journal, registry, store, writes
from kblam.index import generate_index
from kblam.review_index import generate_review_index
from kblam.store import StoreError, ack, atomic_write, new_finding, regenerate_index
from kblam.treehash import clean_before_v2, format_line, read_recorded, tree_digest_v2
from kblam.view import load_view

from conftest import ZERO64, finding_text, record_text

REVIEW = "research-review"
SC = f"{REVIEW}/challenges/SC-0001.yaml"
CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."
CLAIM_C = "The media tray reports its type through two contact pins read at load time."
NOT_A_RECORD = "0" * 64 + "\n"      # what a partial write leaves in .kblam/tree.hash


def stage(kb, finding_id: str, slug: str = "motor", *, claim: str = CLAIM_B, topic: str = "motor"):
    """A staged finding, as a hand-written `.kblam/staging/` file."""
    return kb.write(f".kblam/staging/{finding_id}-{slug}.md",
                    finding_text(finding_id, claim, topic=topic))


def clean_now(kb) -> bool:
    """treehash.clean_before_v2 of the tree as it is now, as a write under the lock computes it."""
    return clean_before_v2(kb.cfg, load_view(kb.cfg), bool(load_view(kb.cfg).records))


# --- locked -------------------------------------------------------------------------------------


def test_a_locked_command_recovers_an_interrupted_write(kb, capsys):
    """SPEC §5.1.6 Interrupted writes: the next command that takes the lock recovers first."""
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write(SC, record_text("SC"))
    recorded = (kb.cfg.state_dir / "tree.hash").read_bytes()
    journal.begin(kb.cfg, "put F-0002-motor.md", [SC, f"{REVIEW}/INDEX.md"])
    kb.write(".kblam/tree.hash", NOT_A_RECORD)             # the interrupted write left this
    kb.write(f"{REVIEW}/INDEX.md", "stale\n")
    (kb.findings / "INDEX.md").unlink()
    capsys.readouterr()

    new_finding(kb.cfg, "motor", "Motor warm-up")          # any locked command, mutating or not

    err = capsys.readouterr().err
    assert ("kblam new motor: the interrupted put F-0002-motor.md may be partial: run kblam validate, fix "
            "what it reports, then kblam validate --record") in err
    assert not kb.cfg.journal_path.exists()
    assert (kb.cfg.state_dir / "tree.hash").read_bytes() == recorded
    assert registry.read_ids(kb.cfg) == {"SC-0001"}        # the journal-listed record is registered
    view = load_view(kb.cfg)
    assert (kb.findings / "INDEX.md").read_bytes() == generate_index(view)
    assert (kb.cfg.review_path / "INDEX.md").read_bytes() == generate_review_index(view)


def test_a_recovery_removes_a_tree_hash_the_journal_did_not_find(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    (kb.cfg.state_dir / "tree.hash").unlink()
    journal.begin(kb.cfg, "index", ["findings/INDEX.md", f"{REVIEW}/INDEX.md"])
    kb.write(".kblam/tree.hash", NOT_A_RECORD)
    capsys.readouterr()

    new_finding(kb.cfg, "motor", "Motor warm-up")

    assert not (kb.cfg.state_dir / "tree.hash").exists()
    assert not kb.cfg.journal_path.exists()
    assert "the interrupted index may be partial" in capsys.readouterr().err


def test_a_failed_recovery_keeps_the_journal_and_refuses_the_command(kb, monkeypatch):
    journal.begin(kb.cfg, "put F-0002-motor.md", ["findings/motor/F-0002-motor.md", f"{REVIEW}/INDEX.md"])

    def fail(_cfg):
        raise OSError("the index write failed")

    monkeypatch.setattr(writes, "regenerate_indexes", fail)
    with pytest.raises(StoreError, match="cannot recover the interrupted write recorded in "
                                         r"\.kblam/journal\.json: the index write failed; the journal is kept"):
        new_finding(kb.cfg, "motor", "Motor warm-up")
    assert kb.cfg.journal_path.is_file()
    assert not (kb.cfg.staging_dir / "F-0001-motor-warm-up.md").exists()  # nothing else ran


def test_a_changed_review_root_refuses_a_mutating_command_only(kb):
    """SPEC §5.1.6: every mutating command refuses a changed root; staging commands do not."""
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write(SC, record_text("SC"))
    kb.write(".kblam/tree.hash", format_line("research-notes", ZERO64))
    with pytest.raises(StoreError, match="the review root changed from research-notes to research-review "
                                         "in kblam.toml; schema 1 fixes it at init"):
        regenerate_index(kb.cfg)
    assert new_finding(kb.cfg, "motor", "Motor warm-up").is_file()


def test_an_unreadable_registry_refuses_a_mutating_command(kb):
    kb.write(".kblam/review-ids", "{not json\n")
    assert new_finding(kb.cfg, "motor", "Motor warm-up").is_file()
    with pytest.raises(StoreError, match=r"\.kblam/review-ids cannot be read"):
        regenerate_index(kb.cfg)


# --- registry_after -----------------------------------------------------------------------------


@pytest.mark.parametrize("first", ["index", "ack", "put"])
def test_the_first_write_after_a_clone_recreates_the_registry(kb, first):
    """SPEC §5.1.6: kblam creates the registry from the records present at its first write after a clone."""
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: deadbeef\n")
    kb.write(SC, record_text("SC"))
    kb.reindex()                                          # from here the registry exists with the record
    assert registry.read_ids(kb.cfg) == {"SC-0001"}
    (kb.cfg.state_dir / "review-ids").unlink()            # as a fresh clone of the same tree has it

    if first == "index":
        regenerate_index(kb.cfg)
    elif first == "ack":
        assert ack(kb.cfg, "F-0002", "F-0001").changed
    else:
        assert store.put(kb.cfg, stage(kb, "F-0003", "tray", claim=CLAIM_C, topic="tray")).ok

    assert registry.read_ids(kb.cfg) == {"SC-0001"}


def test_a_kb_with_no_records_gets_no_registry(kb):
    """SPEC §5.1.6: the registry appears only once a record is present, so a plain KB never gains one."""
    kb.add("F-0001", "sensor", CLAIM_A)
    assert registry.read_ids(kb.cfg) is None

    assert store.put(kb.cfg, stage(kb, "F-0002")).ok
    regenerate_index(kb.cfg)

    assert registry.read_ids(kb.cfg) is None
    assert not kb.cfg.review_ids_path.exists()


def test_registry_after_is_none_until_a_record_is_present(kb):
    assert writes.registry_after(kb.cfg, set(), set()) is None              # no registry, no record
    assert writes.registry_after(kb.cfg, {"SC-0001"}, set()) == {"SC-0001"}  # created from them
    registry.write_ids(kb.cfg, {"SC-0001"})
    assert writes.registry_after(kb.cfg, {"SC-0001"}, set()) is None        # unchanged: not rewritten
    assert writes.registry_after(kb.cfg, {"SC-0001"}, {"CT-0002"}) == {"SC-0001", "CT-0002"}


# --- apply --------------------------------------------------------------------------------------


def test_apply_advances_tree_hash_and_writes_in_the_specs_order(kb, monkeypatch):
    """SPEC §5.1.6: the journal first, then records and findings, then indexes, then the registry."""
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write(SC, record_text("SC"))
    kb.reindex()                                          # the fixture accepts the tree, as --record would
    seen = []
    real_write = atomic_write

    def recording(path, data):
        seen.append((path.name, kb.cfg.journal_path.is_file()))
        real_write(path, data)

    monkeypatch.setattr(store, "atomic_write", recording)
    recorded = writes.apply(kb.cfg, "put F-0002-motor.md",
                            [("findings/motor/F-0002-motor.md", b"---\nid: F-0002\n---\n"),
                             ("findings/INDEX.md", b"# Findings\n")],
                            registry_ids={"SC-0001"}, clean_before=clean_now(kb))
    assert recorded is True
    assert seen == [("journal.json", False), ("F-0002-motor.md", True), ("INDEX.md", True),
                    ("review-ids", True), ("tree.hash", True)]
    assert not kb.cfg.journal_path.exists()
    assert registry.read_ids(kb.cfg) == {"SC-0001"}
    assert read_recorded(kb.cfg) == (2, kb.cfg.review_dir, tree_digest_v2(load_view(kb.cfg)))


def test_apply_journals_only_a_change_of_more_than_one_file(kb, monkeypatch):
    calls = []
    real_begin = journal.begin
    monkeypatch.setattr(journal, "begin",
                        lambda cfg, command, paths: calls.append(list(paths)) or real_begin(cfg, command, paths))
    one = [("findings/INDEX.md", b"one\n")]
    two = [("findings/INDEX.md", b"two\n"), ("findings/notes.md", b"n\n")]

    writes.apply(kb.cfg, "index", one, registry_ids=None, clean_before=clean_now(kb))
    assert calls == []
    writes.apply(kb.cfg, "index", two, registry_ids=None, clean_before=clean_now(kb))
    assert calls == [["findings/INDEX.md", "findings/notes.md"]]
    writes.apply(kb.cfg, "index", one, registry_ids={"SC-0001"}, clean_before=clean_now(kb))
    assert calls[-1] == ["findings/INDEX.md", ".kblam/review-ids"]  # the registry counts as a file


def test_apply_removes_an_emptied_topic_folder_and_keeps_a_full_one(kb):
    kb.add("F-0001", "sensor", CLAIM_A, topic="sensor")
    kb.add("F-0002", "motor", CLAIM_B, topic="toy")
    kb.write("findings/toy/notes.md", "kept\n")
    writes.apply(kb.cfg, "put F-0001-motor.md", [("findings/sensor/F-0001-sensor.md", None)],
                 registry_ids=None, clean_before=clean_now(kb))
    assert not (kb.findings / "sensor").exists() and not (kb.findings / "sensor" / "F-0001-sensor.md").exists()
    writes.apply(kb.cfg, "put F-0002-motor.md", [("findings/toy/F-0002-motor.md", None)],
                 registry_ids=None, clean_before=clean_now(kb))
    assert (kb.findings / "toy").is_dir()  # notes.md is left, so the folder stays


def test_an_exception_part_way_leaves_the_journal_and_a_partial_write(kb, monkeypatch):
    kb.add("F-0001", "sensor", CLAIM_A)
    real_write = atomic_write
    writes_seen = []

    def failing(path, data):
        writes_seen.append(path)
        if len(writes_seen) == 3:                  # 1 the journal, 2 the finding, 3 the index
            raise OSError("the second write failed")
        real_write(path, data)

    monkeypatch.setattr(store, "atomic_write", failing)
    before = read_recorded(kb.cfg)
    with pytest.raises(OSError, match="the second write failed"):
        writes.apply(kb.cfg, "put F-0002-motor.md",
                     [("findings/motor/F-0002-motor.md", b"---\nid: F-0002\n---\n"),
                      ("findings/INDEX.md", b"# Findings\n")],
                     registry_ids=None, clean_before=clean_now(kb))
    record = json.loads(kb.cfg.journal_path.read_text(encoding="utf-8"))
    assert record["command"] == "put F-0002-motor.md"
    assert record["paths"] == ["findings/motor/F-0002-motor.md", "findings/INDEX.md"]
    assert (kb.findings / "motor" / "F-0002-motor.md").is_file()   # the first write landed
    assert (kb.findings / "INDEX.md").read_bytes() != b"# Findings\n"
    assert read_recorded(kb.cfg) == before                         # tree.hash did not advance
