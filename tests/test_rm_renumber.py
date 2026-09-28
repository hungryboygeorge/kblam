"""M6.10 `kblam rm`, `kblam renumber` and ID allocation above git history (SPEC §7 rm, renumber and `new`).
The git tests build a repository under tmp_path and are skipped when git is not installed."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from kblam.cli import main
from kblam.finding import fingerprint, parse_finding
from kblam.lock import kb_lock
from kblam.review import ReviewItem, load_items, save_items
from kblam.store import StoreError, _yaml_scalar, allocate_id, edit_finding, new_finding
from kblam.treehash import current_digest, read_tree_hash

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, finding_text

POINTER = "Load the kblam-write skill for how to fix this."
CLAIM_A = "The two sensor curve types agree to about 0.1%, so they are not two analog gains."
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."
CLAIM_C = "The media tray reports its type through two contact pins read at load time."
CLAIM_D = "The pump primes in ten seconds when the inlet valve is open."

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def no_outer_git(monkeypatch):
    """A GIT_DIR or GIT_INDEX_FILE from a surrounding git hook must not redirect the scratch repositories."""
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)


def git(kb, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=kblam test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=kb.root, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def commit_all(kb, message: str) -> str:
    """Commit everything but .kblam/, which is ignored as kblam init sets it up (SPEC §7.1): kblam acts on no
    state that git tracks (§8.3)."""
    if not (kb.root / ".gitignore").exists():
        kb.write(".gitignore", ".kblam/\n")
    git(kb, "add", "-A")
    git(kb, "commit", "-q", "-m", message)
    return git(kb, "rev-parse", "HEAD")


def run(kb, *args) -> int:
    return main(["--root", str(kb.root), *args])


def quantity(name: str, value, unit: str = "ratio") -> str:
    return f"quantities:\n  - {{name: {name}, value: {value}, unit: \"{unit}\"}}\n"


def refused(kb, capsys, *args, exit_code: int = 1) -> str:
    """Run a command that must be refused with findings/ unchanged; return its message."""
    before = kb.snapshot()
    capsys.readouterr()  # the fixture's own notes
    assert run(kb, *args) == exit_code
    err = capsys.readouterr().err.strip()
    assert err.endswith(POINTER)
    assert kb.snapshot() == before
    return err


# --- rm: refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize("args, message", [
    (["F-12", "--merged-into", "F-0001"], "kblam rm: 'F-12' is not a finding ID like F-0137"),
    (["F-0002", "--merged-into", "0001"], "kblam rm: '0001' is not a finding ID like F-0137"),
    (["F-0002", "--merged-into", "F-0002"], "kblam rm: --merged-into names the finding that now states what "
                                            "F-0002 stated, so it cannot be F-0002 itself"),
    (["F-0009", "--merged-into", "F-0001"], "kblam rm: F-0009 is not in findings/, so there is nothing to remove"),
    (["F-0002", "--merged-into", "F-0009"], "kblam rm: F-0009 is not in findings/; --merged-into names the finding "
                                            "that now states what F-0002 stated"),
])
def test_rm_refuses_a_malformed_or_unknown_id_and_merging_into_itself(kb, capsys, args, message):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    assert refused(kb, capsys, "rm", *args).startswith(message)


def test_rm_refuses_while_other_findings_depend_on_it(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A, extra="depends_on:\n  F-0002: null\n")
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    kb.add("F-0003", "tray", CLAIM_C, topic="tray", extra="depends_on:\n  F-0002: null\n")
    kb.add("F-0004", "pump", CLAIM_D, topic="pump", extra="depends_on: {F-0001: null, F-0002: null}\n")
    err = refused(kb, capsys, "rm", "F-0002", "--merged-into", "F-0001")
    assert err == (
        "kblam rm: F-0002 cannot be removed: F-0003 and F-0004 list F-0002 in depends_on; edit each to depend on "
        "F-0001 first (kblam edit <id>, change F-0002 to F-0001: null under depends_on, and put it); F-0001 itself "
        "lists F-0002 in depends_on; remove that entry first (kblam edit F-0001). findings/ is unchanged. "
        + POINTER)


@pytest.mark.parametrize("target_extra", [
    "",                                                  # the target gives no such quantity
    quantity("curve ratio", 1.0017, "%"),                # another unit
    quantity("curve ratio", 1.0018),                     # another value, at tolerance 0
    quantity("gain ratio", 1.0017),                      # another name
])
def test_rm_refuses_a_quantity_the_target_does_not_give(kb, capsys, target_extra):
    kb.add("F-0001", "sensor", CLAIM_A, extra=target_extra)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=quantity("curve ratio", 1.0017))
    err = refused(kb, capsys, "rm", "F-0002", "--merged-into", "F-0001")
    assert ("F-0002 gives curve ratio = 1.0017 ratio, which F-0001 does not give with the same name, value and "
            "unit; move it into F-0001 first (kblam edit F-0001), or keep F-0002") in err


@pytest.mark.parametrize("target_extra, tolerance", [
    (quantity("Curve   RATIO", 1.0017, "  ratio "), ""),  # names compare as §6.3 does, units after collapsing
    (quantity("curve ratio", 1.0018), "quantity_rel_tolerance = 0.001\n"),
])
def test_rm_accepts_a_quantity_the_target_gives(kb, capsys, target_extra, tolerance):
    kb.write("kblam.toml", KBLAM_TOML + NO_EMBEDDINGS + tolerance + PROMPT_TOML)
    kb.add("F-0001", "sensor", CLAIM_A, extra=target_extra)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=quantity("curve ratio", 1.0017))
    assert run(kb, "rm", "F-0002", "--merged-into", "F-0001") == 0
    assert not (kb.findings / "motor" / "F-0002-motor.md").exists()


def test_rm_refuses_a_damaged_review_file_before_writing_anything(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    kb.write(".kblam/review.jsonl", "not json\n")
    err = refused(kb, capsys, "rm", "F-0002", "--merged-into", "F-0001")
    assert err.startswith("kblam rm: .kblam/review.jsonl:1 cannot be read")


def test_rm_refuses_a_duplicated_id_and_a_lock_timeout(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    kb.add("F-0002", "copy", CLAIM_C, topic="tray")
    err = refused(kb, capsys, "rm", "F-0002", "--merged-into", "F-0001")
    assert "F-0002 has more than one file in findings/" in err and "kblam renumber <path>" in err

    kb.write("kblam.toml", KBLAM_TOML + "lock_wait_seconds = 0\n" + NO_EMBEDDINGS + PROMPT_TOML)
    with kb_lock(kb.cfg, "test holder"):
        err = refused(kb, capsys, "rm", "F-0001", "--merged-into", "F-0002", exit_code=3)
    assert "timed out" in err


# --- rm: the removal ----------------------------------------------------------------------------


def item(item_id: str, kind: str, new_id: str, existing_id: str | None = None, status: str = "open",
         verdict: str | None = "same_fact", close_reason: str | None = None) -> ReviewItem:
    return ReviewItem(item_id, kind, status, new_id, "0000000a", existing_id, "0000000b" if existing_id else None,
                      None if kind == "unchecked" else verdict, message="m", created="t",
                      closed="t" if status == "closed" else None, close_reason=close_reason)


def test_rm_removes_the_file_and_its_folder_regenerates_the_index_and_closes_its_items(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A, extra=quantity("curve ratio", 1.0017))
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=quantity("curve ratio", 1.0017))
    kb.add("F-0003", "tray", CLAIM_C, topic="tray")
    save_items(kb.cfg, [
        item("R-0000000a", "review", "F-0003", "F-0002"),
        item("R-0000000b", "rejected", "F-0002", "F-0001"),
        item("U-0000000c", "unchecked", "F-0002"),
        item("R-0000000d", "review", "F-0003", "F-0001"),
        item("R-0000000e", "review", "F-0002", "F-0001", status="closed", close_reason="distinct: other phase"),
    ])
    capsys.readouterr()

    assert run(kb, "rm", "F-0002", "--merged-into", "F-0001") == 0
    out = capsys.readouterr().out.splitlines()
    reason = "F-0002 was removed (merged into F-0001)"
    assert out == [
        "kblam rm: removed F-0002 (findings/motor/F-0002-motor.md), merged into F-0001",
        "kblam rm: removed findings/motor/, which the removal left empty",
        "kblam rm: regenerated findings/INDEX.md and .kblam/tree.hash",
        f"kblam rm: closed review item R-0000000a (same_fact F-0003 vs F-0002): {reason}",
        f"kblam rm: closed rejected item R-0000000b (same_fact F-0002 vs F-0001): {reason}",
        f"kblam rm: closed unchecked item U-0000000c (unchecked F-0002): {reason}",
        "kblam rm: commit this with the reason for the removal in the message, e.g.: Remove F-0002 (Title of "
        "F-0002), merged into F-0001: <what made it redundant>",
    ]
    assert not (kb.findings / "motor").exists()
    assert kb.issues() == []  # INDEX.md regenerated (K7), nothing else disturbed
    assert "F-0002" not in (kb.findings / "INDEX.md").read_text(encoding="utf-8")
    assert read_tree_hash(kb.cfg) == current_digest(kb.cfg)
    by_id = {i.id: i for i in load_items(kb.cfg)}
    assert {i.id for i in by_id.values() if i.open} == {"R-0000000d"}
    assert {i.id for i in by_id.values() if i.close_reason == reason} == {"R-0000000a", "R-0000000b", "U-0000000c"}
    assert by_id["R-0000000e"].close_reason == "distinct: other phase"


def test_rm_keeps_a_topic_folder_with_other_findings_and_notes_a_staged_copy(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B)
    staged = edit_finding(kb.cfg, "F-0002")
    assert run(kb, "rm", "F-0002", "--merged-into", "F-0001") == 0
    out = capsys.readouterr().out
    assert (kb.findings / "calibration").is_dir() and "left empty" not in out
    assert (f"kblam rm: .kblam/staging/{staged.name} is a staged copy of F-0002; putting it would add F-0002 "
            f"again, so delete it unless that is what you want") in out


def test_rm_leaves_tree_hash_stale_after_a_change_outside_kblam(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    kb.write("findings/calibration/notes.txt", "a shell write kblam did not make\n")
    stale = read_tree_hash(kb.cfg)
    assert run(kb, "rm", "F-0002", "--merged-into", "F-0001") == 0
    captured = capsys.readouterr()
    assert "kblam rm: regenerated findings/INDEX.md\n" in captured.out
    assert "kblam rm: findings/ was changed outside kblam since kblam last wrote it; tree.hash not advanced" \
           in captured.err
    assert read_tree_hash(kb.cfg) == stale != current_digest(kb.cfg)
    assert kb.codes() == ["K8"]


# --- new: ID allocation -------------------------------------------------------------------------


@needs_git
def test_new_allocates_above_an_id_that_was_committed_then_removed(kb, capsys):
    git(kb, "init", "-q")
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    kb.add("F-0003", "tray", CLAIM_C, topic="tray")
    commit_all(kb, "three findings")
    assert run(kb, "rm", "F-0003", "--merged-into", "F-0001") == 0
    commit_all(kb, "Remove F-0003, merged into F-0001")
    assert new_finding(kb.cfg, "motor", "Motor drift").name == "F-0004-motor-drift.md"


@needs_git
def test_new_allocates_above_ids_on_other_branches_and_remote_tracking_refs(kb):
    git(kb, "init", "-q")
    kb.add("F-0001", "sensor", CLAIM_A)
    commit_all(kb, "one finding")
    first = git(kb, "rev-parse", "--abbrev-ref", "HEAD")
    git(kb, "checkout", "-q", "-b", "side")
    kb.add("F-0007", "motor", CLAIM_B, topic="motor")
    commit_all(kb, "a finding on a side branch")
    git(kb, "checkout", "-q", "-b", "fetched")
    kb.add("F-0012", "tray", CLAIM_C, topic="tray")
    commit_all(kb, "a finding only a remote-tracking ref holds")
    git(kb, "update-ref", "refs/remotes/origin/fetched", "HEAD")
    git(kb, "checkout", "-q", first)  # only F-0001 is in the tree
    git(kb, "branch", "-q", "-D", "fetched")
    kb.reindex()
    assert sorted(p.name for p in kb.findings.rglob("F-*.md")) == ["F-0001-sensor.md"]
    assert allocate_id(kb.cfg) == "F-0013"


@needs_git
def test_allocation_falls_back_silently_when_git_cannot_answer(kb, tmp_path, monkeypatch, capsys):
    git(kb, "init", "-q")
    kb.add("F-0005", "sensor", CLAIM_A)
    commit_all(kb, "one finding")
    (kb.findings / "calibration" / "F-0005-sensor.md").unlink()
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    capsys.readouterr()
    assert allocate_id(kb.cfg) == "F-0006"  # with git: above the committed F-0005

    empty = tmp_path / "no-git-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))  # git is not found
    assert allocate_id(kb.cfg) == "F-0003"
    (kb.root / ".git").rename(kb.root / "not-git")  # not a repository
    monkeypatch.undo()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(kb.root.parent))
    assert allocate_id(kb.cfg) == "F-0003"
    assert capsys.readouterr().err == ""


def test_new_refuses_a_topic_outside_the_configured_topics(kb, capsys):
    kb.write("kblam.toml", KBLAM_TOML + 'topics = ["calibration", "motor"]\n' + NO_EMBEDDINGS + PROMPT_TOML)
    with pytest.raises(StoreError, match=r"topic 'optics' is not in the topics kblam.toml allows "
                                         r"\(\[kb\] topics\); use one of: calibration, motor"):
        new_finding(kb.cfg, "optics", "Lens flare")
    assert run(kb, "new", "optics", "Lens flare") == 1
    assert "use one of: calibration, motor" in capsys.readouterr().err
    assert new_finding(kb.cfg, "motor", "Motor drift").name == "F-0001-motor-drift.md"
    assert not (kb.cfg.staging_dir / "F-0001-lens-flare.md").exists()


# --- renumber -----------------------------------------------------------------------------------


def fp_of(path) -> str:
    return fingerprint(parse_finding(path.name, path.read_bytes()))


def two_clones(kb):
    """F-0005 twice, as after merging the work of two clones, and findings that name F-0005."""
    mine = kb.add("F-0005", "sensor", CLAIM_A, title="Sensor curve types")
    theirs = kb.add("F-0005", "motor", CLAIM_B, topic="motor", title="Motor warm-up")
    return mine, theirs


def test_renumber_gives_a_new_id_and_rekeys_the_dependents_that_mean_that_file(kb, capsys):
    mine, theirs = two_clones(kb)
    mine_fp, theirs_fp = fp_of(mine), fp_of(theirs)
    lamp_fp = fp_of(kb.add("F-0003", "other", "The lamp flickers at 50 Hz.", topic="pump", title="Lamp"))
    # fingerprints are quoted, as kblam writes them: YAML would read some (e.g. 054e8097) as numbers
    means_mine = kb.add("F-0006", "uses-mine", CLAIM_C, topic="tray", title="Tray",
                        extra=f"depends_on:\n  F-0003: '{lamp_fp}'   # kept\n  F-0005: '{mine_fp}'   # read it\n")
    flow = kb.add("F-0004", "flow", "The heater settles in 3 minutes.", topic="pump", title="Heater",
                  extra=f"depends_on: {{F-0005: '{mine_fp}', F-0003: '{lamp_fp}'}}\n")
    means_theirs = kb.add("F-0007", "uses-theirs", CLAIM_D, topic="pump", title="Pump",
                          extra=f"depends_on: {{F-0005: '{theirs_fp}'}}\n")
    kb.add("F-0008", "stale", "The valve closes in 2 ms.", topic="pump", title="Valve",
           extra="depends_on:\n  F-0005: deadbeef\n")
    kb.add("F-0009", "prose", "The fan runs at 1200 rpm.", topic="pump", title="Fan",
           body="See F-0005 for the warm-up.")
    before = {p: p.read_bytes() for p in (mine, theirs, means_mine, means_theirs, flow)}
    capsys.readouterr()

    assert run(kb, "renumber", str(mine)) == 0
    out = capsys.readouterr().out.splitlines()
    renamed = kb.findings / "calibration" / "F-0010-sensor.md"
    new_fp = fp_of(renamed)
    assert out == [
        f"kblam renumber: F-0005 -> F-0010: findings/calibration/F-0005-sensor.md is now "
        f"findings/calibration/F-0010-sensor.md (fingerprint {new_fp}); F-0005 stays with "
        f"findings/motor/F-0005-motor.md",
        f"kblam renumber: F-0004 depends_on F-0005 is now F-0010: {new_fp} (findings/pump/F-0004-flow.md), "
        f"since its fingerprint showed it meant findings/calibration/F-0005-sensor.md",
        f"kblam renumber: F-0006 depends_on F-0005 is now F-0010: {new_fp} (findings/tray/F-0006-uses-mine.md), "
        f"since its fingerprint showed it meant findings/calibration/F-0005-sensor.md",
        "kblam renumber: regenerated findings/INDEX.md and .kblam/tree.hash",
        "kblam renumber: 2 other mention(s) of F-0005 may mean either finding; a person checks each and points it "
        "at F-0010 where it meant the renumbered one:",
        "  findings/pump/F-0008-stale.md:10: depends_on F-0005: deadbeef matches neither file",
        "  findings/pump/F-0009-prose.md:13: the body names F-0005",
    ]
    assert not mine.exists()
    assert renamed.read_bytes() == before[mine].replace(b"id: F-0005", b"id: F-0010")
    for dependent in (means_mine, flow):  # only that entry changed, in block and in flow style
        assert dependent.read_bytes() == before[dependent].replace(f"F-0005: '{mine_fp}'".encode(),
                                                                   f"F-0010: {_yaml_scalar(new_fp)}".encode())
    assert theirs.read_bytes() == before[theirs] and means_theirs.read_bytes() == before[means_theirs]
    assert [(i.code, i.path) for i in kb.issues()] == [("K3", "findings/pump/F-0008-stale.md")]
    assert read_tree_hash(kb.cfg) == current_digest(kb.cfg)


def test_renumber_leaves_an_entry_that_matches_both_files_for_a_person(kb, capsys):
    mine = kb.add("F-0005", "sensor", CLAIM_A, title="Sensor")
    theirs = kb.add("F-0005", "same", CLAIM_A, topic="motor", title="Sensor again")  # the same fingerprint
    assert fp_of(mine) == fp_of(theirs)
    dependent = kb.add("F-0006", "uses", CLAIM_C, topic="tray", title="Tray",
                       extra=f"depends_on: {{F-0005: '{fp_of(mine)}'}}\n")
    before = dependent.read_bytes()
    assert run(kb, "renumber", str(theirs)) == 0
    out = capsys.readouterr().out
    assert f"  findings/tray/F-0006-uses.md:9: depends_on F-0005: {fp_of(mine)} is the fingerprint of both files" \
           in out
    assert dependent.read_bytes() == before
    assert (kb.findings / "motor" / "F-0007-same.md").is_file()


def test_renumber_refuses_a_path_that_is_not_a_shared_id(kb, capsys):
    unique = kb.add("F-0001", "sensor", CLAIM_A)
    err = refused(kb, capsys, "renumber", str(unique))
    assert err.startswith("kblam renumber: no other file in findings/ uses F-0001, so there is nothing to renumber")
    staged = kb.write(".kblam/staging/F-0001-sensor.md", finding_text("F-0001", CLAIM_A))
    for path in (staged, kb.findings / "calibration" / "F-0009-missing.md", kb.findings / "INDEX.md"):
        err = refused(kb, capsys, "renumber", str(path))
        assert "is not a finding in a topic folder of findings/" in err


@needs_git
def test_renumber_allocates_above_git_history(kb, capsys):
    git(kb, "init", "-q")
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0020", "motor", CLAIM_B, topic="motor")
    commit_all(kb, "two findings")
    (kb.findings / "motor" / "F-0020-motor.md").unlink()
    kb.add("F-0001", "copy", CLAIM_C, topic="tray")
    assert run(kb, "renumber", str(kb.findings / "tray" / "F-0001-copy.md")) == 0
    assert (kb.findings / "tray" / "F-0021-copy.md").is_file()
