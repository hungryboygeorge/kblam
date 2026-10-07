"""The format-2 tree.hash rule: advance only across kblam's writes; with no records, bootstrap a
missing marker only for a clean findings tree, accepting its findings. Format 1 is never advanced."""

from __future__ import annotations

from kblam.cli import main
from kblam.finding import fingerprint
from kblam.jev import CACHE_NAME, PairCache
from kblam.store import ack, put, regenerate_index
from kblam.treehash import read_recorded, tree_digest_v2
from kblam.view import load_view

from conftest import finding_text, record_text

CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."
CLAIM_C = "The media tray reports its type through two contact pins read at load time."
CLAIM_D = "The fan controller holds its duty cycle at 40% until the case reaches 50 degrees."
WARNING = ("findings/ or research-review/ was changed outside kblam since kblam last wrote it; "
           "tree.hash not advanced. Run kblam validate --record once the change is validated.")
MISSING = ("there is no .kblam/tree.hash (a new clone, or .kblam/ was deleted), and the tree as it was before "
           "this write fails kblam validate, so kblam did not record tree.hash for it; the write itself is "
           "done. Run kblam validate, fix anything it lists, then run kblam validate --record.")
OLD_FORMAT = (".kblam/tree.hash is in the old format; tree.hash not advanced. "
              "Run kblam validate --record once the tree validates.")


def digest(cfg) -> str:
    return tree_digest_v2(load_view(cfg))


def shell_append(path, text: str) -> None:
    """A write into findings/ that does not go through kblam."""
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def test_index_after_shell_write_leaves_tree_hash_stale(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    before = read_recorded(kb.cfg)
    kb.write("findings/motor/F-0002-motor.md", finding_text("F-0002", CLAIM_B, topic="motor"))
    capsys.readouterr()
    regenerate_index(kb.cfg)
    assert "F-0002" in (kb.findings / "INDEX.md").read_text(encoding="utf-8")  # the index write still happens
    assert read_recorded(kb.cfg) == before != (2, kb.cfg.review_dir, digest(kb.cfg))
    assert f"kblam index: {WARNING}" in capsys.readouterr().err


def test_ack_after_shell_write_leaves_tree_hash_stale(kb, capsys):
    target = kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: deadbeef0000\n")
    before = read_recorded(kb.cfg)
    shell_append(target, "\nDetail added by a shell write.\n")
    capsys.readouterr()
    result = ack(kb.cfg, "F-0002", "F-0001")
    assert result.changed and not result.recorded
    assert read_recorded(kb.cfg) == before != (2, kb.cfg.review_dir, digest(kb.cfg))
    assert f"kblam ack F-0002 F-0001: {WARNING}" in capsys.readouterr().err


def test_put_after_shell_write_leaves_tree_hash_stale(kb, capsys):
    target = kb.add("F-0001", "sensor", CLAIM_A)
    before = read_recorded(kb.cfg)
    shell_append(target, "\nDetail added by a shell write.\n")
    capsys.readouterr()
    result = put(kb.cfg, kb.write(".kblam/staging/F-0002-motor.md", finding_text("F-0002", CLAIM_B, topic="motor")))
    assert result.ok and not result.recorded
    assert (kb.findings / "motor" / "F-0002-motor.md").is_file()
    assert read_recorded(kb.cfg) == before != (2, kb.cfg.review_dir, digest(kb.cfg))
    assert f"kblam put F-0002-motor.md: {WARNING}" in capsys.readouterr().err


def test_clean_put_advances_tree_hash(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    capsys.readouterr()  # fixture setup writes findings/ directly, so its reindex warns
    result = put(kb.cfg, kb.write(".kblam/staging/F-0002-motor.md", finding_text("F-0002", CLAIM_B, topic="motor")))
    assert result.ok and result.recorded
    assert read_recorded(kb.cfg) == (2, kb.cfg.review_dir, digest(kb.cfg))
    assert capsys.readouterr().err == ""


def checked(kb, finding_id: str) -> bool:
    """Whether the pair cache marks the finding as checked at its current fingerprint (SPEC §6.5)."""
    finding = next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)
    return PairCache(kb.cfg.state_dir / CACHE_NAME).was_checked(finding_id, fingerprint(finding, "/"))


def test_a_format_1_tree_hash_is_never_advanced(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    capsys.readouterr()
    kb.write(".kblam/tree.hash", "a" * 64 + "\n")
    result = put(kb.cfg, kb.write(".kblam/staging/F-0002-motor.md", finding_text("F-0002", CLAIM_B, topic="motor")))
    assert result.ok and not result.recorded
    assert (kb.cfg.state_dir / "tree.hash").read_bytes() == b"a" * 64 + b"\n"
    assert f"kblam put F-0002-motor.md: {OLD_FORMAT}" in capsys.readouterr().err


def test_index_and_ack_warn_about_a_format_1_tree_hash_and_leave_it(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: deadbeef\n")
    kb.write(".kblam/tree.hash", "a" * 64 + "\n")     # format 1, as an older kblam left it
    capsys.readouterr()
    regenerate_index(kb.cfg)
    assert f"kblam index: {OLD_FORMAT}" in capsys.readouterr().err
    ack(kb.cfg, "F-0002", "F-0001")
    assert f"kblam ack F-0002 F-0001: {OLD_FORMAT}" in capsys.readouterr().err
    assert (kb.cfg.state_dir / "tree.hash").read_bytes() == b"a" * 64 + b"\n"


def test_index_names_tree_hash_in_stdout_only_when_it_advanced(kb, capsys):
    """`kblam index` reports the tree.hash it advanced (SPEC §5.2.6); a format-1 file stays, warned on stderr."""
    kb.add("F-0001", "sensor", CLAIM_A)
    root = ["--root", str(kb.root)]
    capsys.readouterr()

    assert main(root + ["index"]) == 0
    assert " and .kblam/tree.hash" in capsys.readouterr().out

    kb.write(".kblam/tree.hash", "a" * 64 + "\n")     # format 1, as an older kblam left it
    assert main(root + ["index"]) == 0
    out, err = capsys.readouterr()
    assert ".kblam/tree.hash" not in out
    assert f"kblam index: {OLD_FORMAT}" in err
    assert (kb.cfg.state_dir / "tree.hash").read_bytes() == b"a" * 64 + b"\n"


def test_bootstrap_without_findings_records_the_tree(kb, capsys):
    """A new KB (kblam init's first index) has no tree.hash and no findings: kblam's own write records it."""
    (kb.root / ".kblam" / "tree.hash").unlink()
    regenerate_index(kb.cfg)
    assert read_recorded(kb.cfg) == (2, kb.cfg.review_dir, digest(kb.cfg))
    assert capsys.readouterr().err == ""


def test_bootstrap_records_a_tree_that_passes_the_rules_and_accepts_its_findings(kb, capsys):
    """No tree.hash but findings that pass the deterministic rules (a new clone): the write records the
    tree and marks the findings it found as accepted from the repository (SPEC §8, the tree.hash rule)."""
    kb.add("F-0001", "sensor", CLAIM_A)
    (kb.root / ".kblam" / "tree.hash").unlink()
    capsys.readouterr()
    result = put(kb.cfg, kb.write(".kblam/staging/F-0002-motor.md", finding_text("F-0002", CLAIM_B, topic="motor")))
    assert result.ok and result.recorded
    assert read_recorded(kb.cfg) == (2, kb.cfg.review_dir, digest(kb.cfg))
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
    assert read_recorded(kb.cfg) is None
    assert f"kblam index: {MISSING}" in capsys.readouterr().err
    assert not checked(kb, "F-0001") and not checked(kb, "F-0002")

    kb.write("findings/tray/F-0003-tray.md", finding_text("F-0003", CLAIM_C, topic="tray"))  # K7 again
    result = put(kb.cfg, kb.write(".kblam/staging/F-0004-fan.md", finding_text("F-0004", CLAIM_D, topic="fan")))
    assert result.ok and not result.recorded and read_recorded(kb.cfg) is None
    assert (kb.findings / "fan" / "F-0004-fan.md").is_file()   # the write itself is done
    assert f"kblam put F-0004-fan.md: {MISSING}" in capsys.readouterr().err
    assert not checked(kb, "F-0001")

    assert main(["--root", str(kb.root), "validate", "--record"]) == 0  # the baseline, without Jev
    assert read_recorded(kb.cfg) == (2, kb.cfg.review_dir, digest(kb.cfg))
    assert all(checked(kb, i) for i in ("F-0001", "F-0002", "F-0003", "F-0004"))


def test_a_missing_tree_hash_with_a_failing_record_is_not_bootstrapped(kb, capsys):
    """Records take part in the bootstrap's validation: this one fails K13 (its source and the review
    index are missing), so the index write leaves tree.hash missing and says why."""
    kb.write("research-review/challenges/SC-0001.yaml", record_text("SC"))
    (kb.root / ".kblam" / "tree.hash").unlink()
    kb.write("findings/calibration/F-0001-sensor.md", finding_text("F-0001", CLAIM_A))
    capsys.readouterr()
    regenerate_index(kb.cfg)
    assert read_recorded(kb.cfg) is None
    assert capsys.readouterr().err == f"kblam index: {MISSING}\n"


def test_validate_record_advances_only_on_a_clean_result(kb, capsys):
    target = kb.add("F-0001", "sensor", CLAIM_A)
    before = read_recorded(kb.cfg)
    root = ["--root", str(kb.root)]
    shell_append(target, "\nDetail pulled in by git.\n")

    assert main(root + ["validate"]) == 0
    assert read_recorded(kb.cfg) == before  # plain validate never writes

    assert main(root + ["validate", "--record"]) == 0
    assert "recorded .kblam/tree.hash for this tree" in capsys.readouterr().out
    recorded = read_recorded(kb.cfg)
    assert recorded == (2, kb.cfg.review_dir, digest(kb.cfg)) != before

    kb.write("findings/summary.md", "# stray\n")
    assert main(root + ["validate", "--record"]) == 1
    assert "tree.hash not recorded" in capsys.readouterr().out
    assert read_recorded(kb.cfg) == recorded
