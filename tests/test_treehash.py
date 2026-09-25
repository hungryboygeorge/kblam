"""The tree.hash rule (SPEC §8): kblam advances tree.hash only across its own writes."""

from __future__ import annotations

from kblam.cli import main
from kblam.store import ack, put, regenerate_index
from kblam.treehash import current_digest, read_tree_hash

from conftest import finding_text

CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."
WARNING = ("findings/ was changed outside kblam since kblam last wrote it; tree.hash not advanced. "
           "Run kblam validate --record once the change is validated.")


def shell_append(path, text: str) -> None:
    """A write into findings/ that does not go through kblam."""
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def test_index_after_shell_write_leaves_tree_hash_stale(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    before = read_tree_hash(kb.cfg)
    kb.write("findings/motor/F-0002-motor.md", finding_text("F-0002", CLAIM_B, topic="motor"))
    capsys.readouterr()
    regenerate_index(kb.cfg)
    assert "F-0002" in (kb.findings / "INDEX.md").read_text(encoding="utf-8")  # the index write still happens
    assert read_tree_hash(kb.cfg) == before != current_digest(kb.cfg)
    assert f"kblam index: {WARNING}" in capsys.readouterr().err


def test_ack_after_shell_write_leaves_tree_hash_stale(kb, capsys):
    target = kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: deadbeef\n")
    before = read_tree_hash(kb.cfg)
    shell_append(target, "\nDetail added by a shell write.\n")
    capsys.readouterr()
    result = ack(kb.cfg, "F-0002", "F-0001")
    assert result.changed and not result.recorded
    assert read_tree_hash(kb.cfg) == before != current_digest(kb.cfg)
    assert f"kblam ack: {WARNING}" in capsys.readouterr().err


def test_put_after_shell_write_leaves_tree_hash_stale(kb, capsys):
    target = kb.add("F-0001", "sensor", CLAIM_A)
    before = read_tree_hash(kb.cfg)
    shell_append(target, "\nDetail added by a shell write.\n")
    capsys.readouterr()
    result = put(kb.cfg, kb.write(".kblam/staging/F-0002-motor.md", finding_text("F-0002", CLAIM_B, topic="motor")))
    assert result.ok and not result.recorded
    assert (kb.findings / "motor" / "F-0002-motor.md").is_file()
    assert read_tree_hash(kb.cfg) == before != current_digest(kb.cfg)
    assert f"kblam put: {WARNING}" in capsys.readouterr().err


def test_clean_put_advances_tree_hash(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    capsys.readouterr()  # fixture setup writes findings/ directly, so its reindex warns
    result = put(kb.cfg, kb.write(".kblam/staging/F-0002-motor.md", finding_text("F-0002", CLAIM_B, topic="motor")))
    assert result.ok and result.recorded
    assert read_tree_hash(kb.cfg) == current_digest(kb.cfg)
    assert capsys.readouterr().err == ""


def test_bootstrap_without_tree_hash_records_it(kb, capsys):
    (kb.root / ".kblam" / "tree.hash").unlink()
    kb.write("findings/calibration/F-0001-sensor.md", finding_text("F-0001", CLAIM_A))
    regenerate_index(kb.cfg)
    assert read_tree_hash(kb.cfg) == current_digest(kb.cfg)
    assert capsys.readouterr().err == ""


def test_validate_record_advances_only_on_a_clean_result(kb, capsys):
    target = kb.add("F-0001", "sensor", CLAIM_A)
    before = read_tree_hash(kb.cfg)
    root = ["--root", str(kb.root)]
    shell_append(target, "\nDetail pulled in by git.\n")

    assert main(root + ["validate"]) == 0
    assert read_tree_hash(kb.cfg) == before  # plain validate never writes

    assert main(root + ["validate", "--record"]) == 0
    assert "recorded .kblam/tree.hash for this tree" in capsys.readouterr().out
    recorded = read_tree_hash(kb.cfg)
    assert recorded == current_digest(kb.cfg) != before

    kb.write("findings/summary.md", "# stray\n")
    assert main(root + ["validate", "--record"]) == 1
    assert "tree.hash not recorded" in capsys.readouterr().out
    assert read_tree_hash(kb.cfg) == recorded
