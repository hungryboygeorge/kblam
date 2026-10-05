"""The tree.hash rule (SPEC §8, §5.1.6): kblam advances tree.hash only across its own writes, and a
format-1 file is never advanced."""

from __future__ import annotations

from kblam.cli import main
from kblam.store import ack, put, regenerate_index
from kblam.treehash import read_recorded, tree_digest_v2
from kblam.view import load_view

from conftest import finding_text, record_text

CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."
WARNING = ("findings/ or research-review/ was changed outside kblam since kblam last wrote it; "
           "tree.hash not advanced. Run kblam validate --record once the change is validated.")
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
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: deadbeef\n")
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
    """`kblam index` reports the tree.hash it advanced (SPEC §5.1.6); a format-1 file stays, warned on stderr."""
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


def test_bootstrap_without_tree_hash_records_it(kb, capsys):
    (kb.root / ".kblam" / "tree.hash").unlink()
    kb.write("findings/calibration/F-0001-sensor.md", finding_text("F-0001", CLAIM_A))
    regenerate_index(kb.cfg)
    assert read_recorded(kb.cfg) == (2, kb.cfg.review_dir, digest(kb.cfg))
    assert capsys.readouterr().err == ""


def test_a_missing_tree_hash_with_records_present_is_not_bootstrapped(kb, capsys):
    kb.write("research-review/challenges/SC-0001.yaml", record_text("SC"))
    (kb.root / ".kblam" / "tree.hash").unlink()
    kb.write("findings/calibration/F-0001-sensor.md", finding_text("F-0001", CLAIM_A))
    regenerate_index(kb.cfg)
    assert read_recorded(kb.cfg) is None
    assert "kblam index: no .kblam/tree.hash, and the review root holds records; tree.hash not advanced." \
        in capsys.readouterr().err


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
