"""tree.hash format 2: the digest of both roots, its line, and the rule helpers (SPEC §5.2.6)."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from kblam.review_index import generate_review_index
from kblam.rules import validate
from kblam.treehash import (
    V2_TAG,
    clean_before_v2,
    format_line,
    parse_line,
    read_recorded,
    record_after_write_v2,
    root_problem,
    tree_digest_v2,
    write_tree_hash_v2,
)
from kblam.view import KBView, load_view

from conftest import finding_text, record_text

CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."

HEX = "a" * 64          # a digest kblam would never compute, so a match is never accidental

FINDING = b"---\nid: F-0001\n---\n\n**Claim.** A claim.\n"
FINDINGS_INDEX = b"# Findings\n\n| ID | Title |\n"
RECORD = b"schema: 1\nid: source-challenge-0001\n"
REVIEW_INDEX = b"# Review\n"


def hand_digest(cfg, entries: list[tuple[bytes, bytes]]) -> str:
    """The format-2 digest over literal bytes, as SPEC §5.2.6 states it: the header, then each
    domain-separated name, NUL, the byte length in decimal ASCII, NUL and the bytes."""
    h = hashlib.sha256()
    h.update(b"kblam-tree-v2\0" + cfg.findings_dir.encode("utf-8") + b"\0"
             + cfg.review_dir.encode("utf-8") + b"\0")
    for name, data in entries:
        h.update(name + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
    return h.hexdigest()


def both_roots_view(cfg) -> KBView:
    """Two findings files, the findings index, one record and the review index, in sorted name order."""
    return KBView(
        cfg=cfg,
        files={f"{cfg.findings_dir}/INDEX.md": FINDINGS_INDEX,
               f"{cfg.findings_dir}/calibration/F-0001-sensor.md": FINDING},
        review_files={f"{cfg.review_dir}/INDEX.md": REVIEW_INDEX,
                      f"{cfg.review_dir}/challenges/source-challenge-0001.yaml": RECORD},
    )


def root_message(cfg, old: str) -> str:
    return (f"the review root changed from {old} to {cfg.review_dir} in kblam.toml; "
            f"schema 1 fixes it at init")


# --- the digest ---------------------------------------------------------------------------------

def test_tree_digest_v2_is_the_hand_built_sha256(kb):
    cfg = kb.cfg
    assert tree_digest_v2(both_roots_view(cfg)) == hand_digest(cfg, [
        (b"f/INDEX.md", FINDINGS_INDEX),
        (b"f/calibration/F-0001-sensor.md", FINDING),
        (b"r/INDEX.md", REVIEW_INDEX),
        (b"r/challenges/source-challenge-0001.yaml", RECORD),
    ])
    # An empty tree is the header alone: the roots are in the digest even with no file.
    assert tree_digest_v2(KBView(cfg=cfg, files={})) == hand_digest(cfg, [])


def test_tree_digest_v2_binds_the_review_root_name(kb):
    cfg = kb.cfg
    other = replace(cfg, review_dir="research-notes")
    first, second = tree_digest_v2(both_roots_view(cfg)), tree_digest_v2(both_roots_view(other))
    assert first != second
    assert second == hand_digest(other, [
        (b"f/INDEX.md", FINDINGS_INDEX),
        (b"f/calibration/F-0001-sensor.md", FINDING),
        (b"r/INDEX.md", REVIEW_INDEX),
        (b"r/challenges/source-challenge-0001.yaml", RECORD),
    ])


def test_tree_digest_v2_counts_a_file_in_each_root_separately(kb):
    cfg = kb.cfg
    both = KBView(cfg=cfg,
                  files={f"{cfg.findings_dir}/challenges/source-challenge-0001.yaml": RECORD},
                  review_files={f"{cfg.review_dir}/challenges/source-challenge-0001.yaml": RECORD})
    only_findings = KBView(cfg=cfg, files={f"{cfg.findings_dir}/challenges/source-challenge-0001.yaml": RECORD})
    assert tree_digest_v2(both) == hand_digest(cfg, [(b"f/challenges/source-challenge-0001.yaml", RECORD),
                                                     (b"r/challenges/source-challenge-0001.yaml", RECORD)])
    assert tree_digest_v2(both) != tree_digest_v2(only_findings)


# --- the line -----------------------------------------------------------------------------------

def test_format_line_and_parse_line_round_trip(kb):
    line = format_line("research-notes", HEX)
    assert line == f"{V2_TAG} research-notes {HEX}\n"
    assert parse_line(line) == (2, "research-notes", HEX)


@pytest.mark.parametrize("text, expected", [
    (HEX, (1, None, HEX)),
    (HEX + "\n", (1, None, HEX)),
    (f"\t  {HEX}\n\n", (1, None, HEX)),
    (f"{V2_TAG} research-review {HEX}\n", (2, "research-review", HEX)),
    (f"{V2_TAG} findings-notes {HEX}", (2, "findings-notes", HEX)),
    (f"  {V2_TAG} research-notes {HEX} \n", (2, "research-notes", HEX)),
])
def test_parse_line_reads_both_formats(text, expected):
    assert parse_line(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "\n",
    HEX[:-1],                                   # 63
    HEX + "a",                                  # 65
    HEX.upper(),                                # a bare hex line is lowercase
    f"{V2_TAG} research-review {HEX.upper()}",
    f"{V2_TAG} research-review",                # no digest
    f"{V2_TAG} {HEX}",                          # no root
    f"{V2_TAG}  research-review {HEX}",         # two spaces: the root is empty
    f"{V2_TAG} research review {HEX}",          # a space in the root
    f"{V2_TAG} research-review {HEX} extra",
    f"{V2_TAG.upper()} research-review {HEX}",
    f"kblam-tree-v1 research-review {HEX}",
    "not a tree hash at all",
])
def test_parse_line_refuses_anything_else(text):
    with pytest.raises(ValueError):
        parse_line(text)


def test_read_recorded_is_none_without_a_tree_hash(kb):
    (kb.root / ".kblam" / "tree.hash").unlink()
    assert read_recorded(kb.cfg) is None


def test_read_recorded_reads_a_bare_hex_as_format_1(kb):
    kb.write(".kblam/tree.hash", f"  {HEX}\n")
    assert read_recorded(kb.cfg) == (1, None, HEX)


def test_read_recorded_reads_a_format_2_line(kb):
    kb.write(".kblam/tree.hash", format_line("research-notes", HEX))
    assert read_recorded(kb.cfg) == (2, "research-notes", HEX)


def test_read_recorded_treats_unparseable_text_as_format_1(kb):
    kb.write(".kblam/tree.hash", "  not a digest at all \n")
    assert read_recorded(kb.cfg) == (1, None, "not a digest at all")


def test_write_tree_hash_v2_writes_the_line_of_the_tree(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    cfg, view = kb.cfg, load_view(kb.cfg)
    write_tree_hash_v2(cfg, view)
    assert (kb.root / ".kblam" / "tree.hash").read_bytes() == \
        format_line(cfg.review_dir, tree_digest_v2(view)).encode("utf-8")
    assert read_recorded(cfg) == (2, cfg.review_dir, tree_digest_v2(view))


# --- root_problem -------------------------------------------------------------------------------

def test_root_problem_is_none_without_a_tree_hash(kb):
    (kb.root / ".kblam" / "tree.hash").unlink()
    assert root_problem(kb.cfg, None) is None


def test_root_problem_is_none_for_a_format_1_tree_hash(kb):
    kb.write(".kblam/tree.hash", HEX + "\n")
    assert root_problem(kb.cfg, {"source-challenge-0001"}) is None


def test_root_problem_is_none_for_the_configured_root(kb):
    kb.write(".kblam/tree.hash", format_line(kb.cfg.review_dir, HEX))
    assert root_problem(kb.cfg, {"source-challenge-0001"}) is None


def test_root_problem_reports_a_root_change_when_a_record_moved_with_it(kb):
    cfg = kb.cfg
    kb.write(".kblam/tree.hash", format_line("research-notes", HEX))
    kb.write(f"{cfg.review_dir}/challenges/source-challenge-0001.yaml", RECORD)
    assert root_problem(cfg, None) == root_message(cfg, "research-notes")


def test_root_problem_reports_a_root_change_when_the_old_root_holds_a_record(kb):
    cfg = kb.cfg
    kb.write(".kblam/tree.hash", format_line("research-notes", HEX))
    kb.write("research-notes/tasks/claim-task-0001.yaml", RECORD)
    assert root_problem(cfg, None) == root_message(cfg, "research-notes")


def test_root_problem_reports_a_root_change_when_the_registry_is_not_empty(kb):
    cfg = kb.cfg
    kb.write(".kblam/tree.hash", format_line("research-notes", HEX))
    assert root_problem(cfg, {"source-challenge-0001"}) == root_message(cfg, "research-notes")
    assert root_problem(cfg, set()) is None
    assert root_problem(cfg, None) is None


def test_root_problem_is_none_when_neither_root_holds_a_record(kb):
    cfg = kb.cfg
    kb.write(".kblam/tree.hash", format_line("research-notes", HEX))
    kb.write("research-notes/challenges/source-challenge-0001.txt", b"not a record name\n")
    assert root_problem(cfg, None) is None


@pytest.mark.parametrize("root", ["../elsewhere", "/elsewhere", "C:/elsewhere", "elsewhere\\challenges"])
def test_root_problem_never_looks_outside_the_repository(kb, root):
    cfg = kb.cfg
    outside = kb.root.parent / "elsewhere"
    outside.joinpath("challenges").mkdir(parents=True, exist_ok=True)
    outside.joinpath("challenges", "source-challenge-0001.yaml").write_bytes(RECORD)
    kb.write(".kblam/tree.hash", format_line(root, HEX))
    assert root_problem(cfg, None) is None


# --- clean_before_v2 ----------------------------------------------------------------------------

def test_clean_before_v2_bootstraps_a_clean_tree_unless_the_write_creates_no_registry(kb):
    """With no tree.hash, a tree that validates bootstraps whether or not records exist, since the write
    creates the registry from them; rm, renumber and upgrade write no registry, so with records they
    do not bootstrap."""
    cfg, view = kb.cfg, load_view(kb.cfg)
    (kb.root / ".kblam" / "tree.hash").unlink()
    assert clean_before_v2(cfg, view, has_records=False) is True
    assert clean_before_v2(cfg, view, has_records=True) is True
    assert clean_before_v2(cfg, view, has_records=False, creates_registry=False) is True
    assert clean_before_v2(cfg, view, has_records=True, creates_registry=False) is False


def test_clean_before_v2_does_not_bootstrap_a_tree_that_fails_the_rules(kb):
    kb.write("research-review/challenges/source-challenge-0001.yaml", RECORD)     # K13: not a valid record
    (kb.root / ".kblam" / "tree.hash").unlink()
    cfg, view = kb.cfg, load_view(kb.cfg)
    assert clean_before_v2(cfg, view, has_records=True) is False
    assert read_recorded(cfg) is None


def test_clean_before_v2_bootstraps_a_tree_whose_only_issues_are_warnings(kb):
    """The bootstrap's validation ignores warnings, as `kblam validate --record` does: a record whose
    source file is gone reports K13 warnings only while it is open, so the first write bootstraps; a
    findings-only tree reports no warnings at all (K1-K12), so it bootstraps too."""
    cfg = kb.cfg
    kb.add("F-0001", "sensor", CLAIM_A)                    # no records: no warnings to ignore
    (kb.root / ".kblam" / "tree.hash").unlink()
    view = load_view(cfg)
    assert validate(view) == []
    assert clean_before_v2(cfg, view, has_records=False) is True

    kb.write(f"{cfg.review_dir}/challenges/source-challenge-0001.yaml", record_text("source-challenge"))
    kb.write(f"{cfg.review_dir}/INDEX.md", generate_review_index(load_view(cfg)))
    view = load_view(cfg)
    levels = [issue.level for issue in validate(view)]
    assert levels and "error" not in levels
    assert clean_before_v2(cfg, view, has_records=True) is True
    assert record_after_write_v2(cfg, True, "put") is True
    assert read_recorded(cfg) == (2, cfg.review_dir, tree_digest_v2(load_view(cfg)))


def test_clean_before_v2_never_matches_a_format_1_line(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    cfg, view = kb.cfg, load_view(kb.cfg)
    kb.write(".kblam/tree.hash", HEX + "\n")        # a bare hex line: format 1, as an older kblam left it
    assert read_recorded(cfg) == (1, None, HEX)
    assert clean_before_v2(cfg, view, has_records=False) is False
    assert clean_before_v2(cfg, view, has_records=True) is False


def test_clean_before_v2_never_matches_unparseable_text(kb):
    kb.write(".kblam/tree.hash", "not a digest at all\n")
    assert clean_before_v2(kb.cfg, load_view(kb.cfg), has_records=False) is False


def test_clean_before_v2_matches_the_format_2_line_of_this_tree(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    cfg, view = kb.cfg, load_view(kb.cfg)
    write_tree_hash_v2(cfg, view)
    assert clean_before_v2(cfg, view, has_records=True) is True
    kb.write("findings/calibration/F-0002-motor.md", finding_text("F-0002", CLAIM_B))
    assert clean_before_v2(cfg, load_view(cfg), has_records=True) is False


def test_clean_before_v2_refuses_a_line_for_another_root(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    cfg, view = kb.cfg, load_view(kb.cfg)
    other = replace(cfg, review_dir="research-notes")
    write_tree_hash_v2(other, KBView(cfg=other, files=view.files))  # the same tree, another root
    assert clean_before_v2(cfg, view, has_records=True) is False


# --- record_after_write_v2 ----------------------------------------------------------------------

def test_record_after_write_v2_writes_the_line_when_clean(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    capsys.readouterr()  # fixture setup writes findings/ directly, so its reindex warns
    cfg, view = kb.cfg, load_view(kb.cfg)
    assert record_after_write_v2(cfg, clean_before=True, command="index") is True
    assert (kb.root / ".kblam" / "tree.hash").read_bytes() == \
        format_line(cfg.review_dir, tree_digest_v2(view)).encode("utf-8")
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_record_after_write_v2_bootstraps_a_missing_file(kb, capsys):
    cfg, view = kb.cfg, load_view(kb.cfg)
    (kb.root / ".kblam" / "tree.hash").unlink()
    assert clean_before_v2(cfg, view, has_records=False) is True
    assert record_after_write_v2(cfg, clean_before=True, command="index") is True
    assert read_recorded(cfg) == (2, cfg.review_dir, tree_digest_v2(view))
    assert capsys.readouterr().err == ""


def test_record_after_write_v2_warns_about_a_format_1_file_and_leaves_it(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    capsys.readouterr()  # fixture setup writes findings/ directly, so its reindex warns
    cfg = kb.cfg
    kb.write(".kblam/tree.hash", HEX + "\n")        # format 1, as an older kblam left it
    path = kb.root / ".kblam" / "tree.hash"
    before = path.read_bytes()
    assert record_after_write_v2(cfg, clean_before=False, command="put") is False
    assert path.read_bytes() == before
    assert capsys.readouterr().err == (
        "kblam put: .kblam/tree.hash is in the old format; tree.hash not advanced. "
        "Run kblam validate --record once the tree validates.\n")


MISSING_FAILED = ("there is no .kblam/tree.hash (a new clone, or .kblam/ was deleted), and the tree as it was "
                  "before this write fails kblam validate, so kblam did not record tree.hash for it; the write "
                  "itself is done. Run kblam validate, fix anything it lists, then run kblam validate "
                  "--record.\n")


@pytest.mark.parametrize("record", [True, False], ids=["records", "no-records"])
def test_record_after_write_v2_warns_when_the_bootstrap_failed(kb, capsys, record):
    if record:
        kb.write("research-review/challenges/source-challenge-0001.yaml", RECORD)
    cfg = kb.cfg
    path = kb.root / ".kblam" / "tree.hash"
    path.unlink()
    capsys.readouterr()
    assert record_after_write_v2(cfg, clean_before=False, command="put") is False
    assert not path.exists()
    assert capsys.readouterr().err == f"kblam put: {MISSING_FAILED}"


def test_record_after_write_v2_warns_when_a_registry_free_write_meets_records(kb, capsys):
    """rm, renumber and upgrade write no registry, so with records present they never bootstrap; the
    warning says so and names the command that does."""
    kb.write("research-review/challenges/source-challenge-0001.yaml", RECORD)
    cfg = kb.cfg
    path = kb.root / ".kblam" / "tree.hash"
    path.unlink()
    capsys.readouterr()
    assert record_after_write_v2(cfg, clean_before=False, command="rm", creates_registry=False) is False
    assert not path.exists()
    assert capsys.readouterr().err == (
        "kblam rm: there is no .kblam/tree.hash (a new clone, or .kblam/ was deleted), and kblam rm does not "
        "record one while research-review/ holds review records; tree.hash not advanced. Run kblam validate, "
        "fix anything it lists, then run kblam validate --record.\n")
    (kb.root / "research-review/challenges/source-challenge-0001.yaml").unlink()    # no records: the ordinary text
    assert record_after_write_v2(cfg, clean_before=False, command="rm", creates_registry=False) is False
    assert capsys.readouterr().err == f"kblam rm: {MISSING_FAILED}"


def test_record_after_write_v2_warns_about_an_out_of_band_change(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    capsys.readouterr()  # fixture setup writes findings/ directly, so its reindex warns
    cfg = kb.cfg
    write_tree_hash_v2(cfg, load_view(cfg))
    path = kb.root / ".kblam" / "tree.hash"
    before = path.read_bytes()
    kb.write("findings/calibration/F-0002-motor.md", finding_text("F-0002", CLAIM_B))  # outside kblam
    assert clean_before_v2(cfg, load_view(cfg), has_records=True) is False
    assert record_after_write_v2(cfg, clean_before=False, command="index") is False
    assert path.read_bytes() == before
    assert capsys.readouterr().err == (
        f"kblam index: {cfg.findings_dir}/ or {cfg.review_dir}/ was changed outside kblam since kblam "
        f"last wrote it; tree.hash not advanced. Run kblam validate --record once the change is "
        f"validated.\n")


def test_a_format_2_line_is_accepted_after_the_next_clean_write(kb, capsys):
    """The upgrade path: the old bare hex warns once, then a bootstrap writes format 2."""
    kb.add("F-0001", "sensor", CLAIM_A)
    cfg = kb.cfg
    kb.write(".kblam/tree.hash", HEX + "\n")        # format 1, as an older kblam left it
    assert record_after_write_v2(cfg, clean_before=False, command="index") is False
    assert ".kblam/tree.hash is in the old format" in capsys.readouterr().err
    path = kb.root / ".kblam" / "tree.hash"
    path.unlink()                                    # validate --record on a clean tree
    assert record_after_write_v2(cfg, clean_before_v2(cfg, load_view(cfg), has_records=False),
                                 command="validate") is True
    assert read_recorded(cfg)[0] == 2
    assert clean_before_v2(cfg, load_view(cfg), has_records=True) is True
