"""The tree.hash rule (SPEC §8): kblam advances tree.hash only across its own writes, and a bootstrap
(no tree.hash) records only a tree that passes the deterministic rules, accepting its findings."""

from __future__ import annotations

from kblam.cli import main
from kblam.finding import fingerprint
from kblam.jev import CACHE_NAME, PairCache
from kblam.store import ack, put, regenerate_index
from kblam.treehash import current_digest, read_tree_hash
from kblam.view import load_view

from conftest import finding_text

CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."
CLAIM_C = "The media tray reports its type through two contact pins read at load time."
CLAIM_D = "The fan controller holds its duty cycle at 40% until the case reaches 50 degrees."
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


def checked(kb, finding_id: str) -> bool:
    """Whether the pair cache marks the finding as checked at its current fingerprint (SPEC §6.5)."""
    finding = next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)
    return PairCache(kb.cfg.state_dir / CACHE_NAME).was_checked(finding_id, fingerprint(finding))


def test_bootstrap_without_findings_records_the_tree(kb, capsys):
    """A new KB (kblam init's first index) has no tree.hash and no findings: kblam's own write records it."""
    (kb.root / ".kblam" / "tree.hash").unlink()
    regenerate_index(kb.cfg)
    assert read_tree_hash(kb.cfg) == current_digest(kb.cfg)
    assert capsys.readouterr().err == ""


def test_bootstrap_records_a_tree_that_passes_the_rules_and_accepts_its_findings(kb, capsys):
    """No tree.hash but findings that pass the deterministic rules (a new clone): the write records the
    tree and marks the findings it found as accepted from the repository (SPEC §8, the tree.hash rule)."""
    kb.add("F-0001", "sensor", CLAIM_A)
    (kb.root / ".kblam" / "tree.hash").unlink()
    capsys.readouterr()
    result = put(kb.cfg, kb.write(".kblam/staging/F-0002-motor.md", finding_text("F-0002", CLAIM_B, topic="motor")))
    assert result.ok and result.recorded
    assert read_tree_hash(kb.cfg) == current_digest(kb.cfg)
    assert checked(kb, "F-0001")      # accepted from the repository without asking Jev
    assert not checked(kb, "F-0002")  # the put's own check (no Jev verdict is enabled here) marks nothing
    assert capsys.readouterr().err == ""


def test_bootstrap_refuses_a_tree_that_fails_the_rules(kb, capsys):
    """No tree.hash and a tree that fails the rules before the write (here K7: a finding written without
    its index): tree.hash stays absent and nothing is marked, so the Stop hook still validates the tree
    until kblam validate --record accepts it."""
    kb.add("F-0001", "sensor", CLAIM_A)
    (kb.root / ".kblam" / "tree.hash").unlink()
    kb.write("findings/motor/F-0002-motor.md", finding_text("F-0002", CLAIM_B, topic="motor"))
    capsys.readouterr()
    regenerate_index(kb.cfg)
    assert "F-0002" in (kb.findings / "INDEX.md").read_text(encoding="utf-8")  # the index write still happens
    assert read_tree_hash(kb.cfg) is None
    assert f"kblam index: {WARNING}" in capsys.readouterr().err
    assert not checked(kb, "F-0001") and not checked(kb, "F-0002")

    kb.write("findings/tray/F-0003-tray.md", finding_text("F-0003", CLAIM_C, topic="tray"))  # K7 again
    result = put(kb.cfg, kb.write(".kblam/staging/F-0004-fan.md", finding_text("F-0004", CLAIM_D, topic="fan")))
    assert result.ok and not result.recorded and read_tree_hash(kb.cfg) is None
    assert f"kblam put: {WARNING}" in capsys.readouterr().err
    assert not checked(kb, "F-0001")

    assert main(["--root", str(kb.root), "validate", "--record"]) == 0  # the baseline, without Jev
    assert read_tree_hash(kb.cfg) == current_digest(kb.cfg)
    assert all(checked(kb, i) for i in ("F-0001", "F-0002", "F-0003", "F-0004"))


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
