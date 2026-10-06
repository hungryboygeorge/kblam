"""M6.11 `kblam rm` and `kblam renumber` vs review records, and record IDs allocated above git history
(SPEC §7 "`rm` and `renumber` vs review records", §5.2.5). Every message variant is asserted in full, with
its exit status and findings/, the review root, the registry and tree.hash unchanged; and every command a
message names is run in the state the message names and succeeds (D49)."""

from __future__ import annotations

import hashlib
import shlex

import pytest

import m611_helpers as m
from conftest import finding_text, record_text
from kblam import resolutions, review_stage
from kblam.finding import fingerprint, parse_finding
from kblam.records import KINDS

from test_rm_renumber import commit_all, git, needs_git, no_outer_git  # noqa: F401 (no_outer_git: fixture)

POINTER = "Load the kblam-write skill for how to fix this."
CLAIM_A = "The two sensor curve types agree to about 0.1%, so they are not two analog gains."
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."
CLAIM_C = "The media tray reports its type through two contact pins read at load time."
MINE = "findings/calibration/F-0012-sensor.md"
THEIRS = "findings/motor/F-0012-motor.md"
THIRD = "findings/tray/F-0012-tray.md"


# --- fixtures: findings and hand-installed records -----------------------------------------------


def binding(kb, path: str) -> tuple[str, str]:
    """(fingerprint v2, full-file sha256) of the finding at `path`, as a CT or CU binds it."""
    raw = (kb.root / path).read_bytes()
    return fingerprint(parse_finding(path, raw), "/"), hashlib.sha256(raw).hexdigest()


def install(kb, kind: str, rec_id: str, **fields) -> None:
    """Write a record into the review root by hand and accept the tree (fixture setup)."""
    kb.write(f"{kb.cfg.review_dir}/{KINDS[kind]}/{rec_id}.yaml", record_text(kind, rec_id, **fields))
    m.accept_tree(kb)


def ct(kb, rec_id: str, path: str, finding_id: str = "F-0012", **fields) -> None:
    """A CT naming `finding_id`, bound to the file at `path` (a path that is not a finding: a stale binding)."""
    fp, sha = binding(kb, path) if (kb.root / path).is_file() else ("0badf00d0000", "0" * 64)
    install(kb, "CT", rec_id, finding=finding_id, claim_fingerprint=fp, base_file_sha256=sha, **fields)


def cu(kb, rec_id: str, path: str, finding_id: str = "F-0012", **fields) -> None:
    fp, sha = binding(kb, path)
    install(kb, "CU", rec_id, finding=finding_id, finding_fingerprint=fp, finding_file_sha256=sha, **fields)


def sc(kb, rec_id: str, *finding_ids: str) -> None:
    install(kb, "SC", rec_id, linked_findings=list(finding_ids))


def merge_pair(kb) -> None:
    """F-0012, the finding review records link, and F-0020, the target, which gives a quantity."""
    kb.add("F-0012", "sensor", CLAIM_A)
    kb.add("F-0020", "motor", CLAIM_B, topic="motor",
           extra='quantities:\n  - {name: warm-up time, value: 90, unit: "s"}\n')


def same_id(kb, *, third: bool = False) -> None:
    """F-0012 in two (or three) topic folders, as after merging the work of two clones."""
    kb.add("F-0012", "sensor", CLAIM_A)
    kb.add("F-0012", "motor", CLAIM_B, topic="motor")
    if third:
        kb.add("F-0012", "tray", CLAIM_C, topic="tray")


def state(kb) -> dict:
    """findings/, the review root, the registry and tree.hash: what a refusal leaves unchanged."""
    out = {p: b for p, b in m.tree(kb).items() if p.startswith((f"{kb.cfg.findings_dir}/", f"{kb.cfg.review_dir}/"))}
    for name in (".kblam/review-ids", ".kblam/tree.hash"):
        path = kb.root / name
        out[name] = path.read_bytes() if path.is_file() else None
    return out


def refused(kb, *argv) -> str:
    """Run a command that must be refused (exit 1) with nothing it guards changed; its stderr."""
    before = state(kb)
    run = m.kblam(kb, *argv)
    assert run.code == 1, run.out + run.err
    assert run.out == ""
    assert state(kb) == before
    return run.err


@pytest.fixture
def at_root(kb, monkeypatch):
    """Run from the repository root, so a command a message prints runs as printed (its paths are
    repository-relative)."""
    monkeypatch.chdir(kb.root)


def message(command: str, text: str) -> str:
    """A refusal's whole stderr: the SPEC text, then the skill pointer (SPEC §8 item 6)."""
    return f"kblam {command}: {text} {POINTER}\n"


# --- rm: the refusal texts -----------------------------------------------------------------------


MERGE = ("F-0012 cannot be removed: {records} it, and kblam never removes a finding a review record links. "
         "findings/ is unchanged. Merge the other way: make F-0012 also state what F-0020 states that F-0012 "
         "does not yet (its detail and quantities; kblam edit F-0012), then kblam rm F-0020 --merged-into "
         "F-0012.")


def test_rm_refuses_a_linked_finding_and_says_to_merge_the_other_way(kb):
    merge_pair(kb)
    ct(kb, "CT-0003", MINE)
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", MERGE.format(
        records="review record CT-0003 links")[:-1] + ". The edit makes CT-0003 stale until a reviewer rechecks "
        "and rebinds it; kblam put prints the kblam review rebind command for it.")


def test_rm_names_every_record_and_each_one_the_edit_makes_stale(kb):
    merge_pair(kb)
    ct(kb, "CT-0003", MINE)
    cu(kb, "CU-0002", MINE)
    sc(kb, "SC-0004", "F-0012")
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", MERGE.format(
        records="review records SC-0004, CT-0003, CU-0002 link")[:-1] + ". The edit makes CT-0003, CU-0002 "
        "stale until a reviewer rechecks and rebinds each; kblam put prints the kblam review rebind command for "
        "each.")


@pytest.mark.parametrize("setup, records", [
    (lambda kb: sc(kb, "SC-0004", "F-0012"), "review record SC-0004 links"),
    (lambda kb: (ct(kb, "CT-0003", MINE, status="stale"), cu(kb, "CU-0002", MINE, status="withdrawn")),
     "review records CT-0003, CU-0002 link"),
    (lambda kb: ct(kb, "CT-0003", "findings/nowhere.md"), "review record CT-0003 links"),  # a stale binding
])
def test_rm_leaves_the_stale_sentence_out_when_the_edit_makes_no_record_stale(kb, setup, records):
    """Only SC links, only retired or withdrawn records, or a binding that no longer matches the file: the
    put of the edited finding lists nothing as made stale, so the refusal says nothing about it."""
    merge_pair(kb)
    setup(kb)
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", MERGE.format(records=records))


def test_rm_with_a_staged_copy_says_to_extend_and_put_that_copy(kb):
    merge_pair(kb)
    ct(kb, "CT-0003", MINE)
    m.ok(m.kblam(kb, "edit", "F-0012"))
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", (
        "F-0012 cannot be removed: review record CT-0003 links it, and kblam never removes a finding a review "
        "record links. findings/ is unchanged. Merge the other way: make your staged copy "
        ".kblam/staging/F-0012-sensor.md also state what F-0020 states that F-0012 does not yet (its detail "
        "and quantities) and put it, then kblam rm F-0020 --merged-into F-0012. The edit makes CT-0003 stale "
        "until a reviewer rechecks and rebinds it; kblam put prints the kblam review rebind command for it."))


def test_rm_with_several_staged_copies_names_each(kb):
    merge_pair(kb)
    sc(kb, "SC-0004", "F-0012")
    staged = m.ok(m.kblam(kb, "edit", "F-0012")).out.strip()
    kb.write(".kblam/staging/F-0012-other.md", (kb.root / staged).read_bytes())
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", (
        "F-0012 cannot be removed: review record SC-0004 links it, and kblam never removes a finding a review "
        "record links. findings/ is unchanged. Merge the other way: make one of your staged copies "
        ".kblam/staging/F-0012-other.md, .kblam/staging/F-0012-sensor.md also state what F-0020 states that "
        "F-0012 does not yet (its detail and quantities) and put it, then kblam rm F-0020 --merged-into "
        "F-0012."))


def test_rm_refuses_when_a_record_also_links_the_target(kb):
    merge_pair(kb)
    ct(kb, "CT-0003", MINE)
    cu(kb, "CU-0001", "findings/motor/F-0020-motor.md", finding_id="F-0020")
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", (
        "F-0012 cannot be removed: review record CT-0003 links it, and F-0020 cannot be removed in its place: "
        "review record CU-0001 links it; kblam never removes a finding a review record links. findings/ is "
        "unchanged. Leave both as they are and tell the user F-0012, F-0020, CT-0003 and CU-0001."))


def test_rm_names_a_record_that_links_both_findings_once(kb):
    merge_pair(kb)
    ct(kb, "CT-0003", MINE)
    sc(kb, "SC-0004", "F-0012", "F-0020")
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", (
        "F-0012 cannot be removed: review records SC-0004, CT-0003 link it, and F-0020 cannot be removed in its "
        "place: review record SC-0004 links it; kblam never removes a finding a review record links. findings/ "
        "is unchanged. Leave both as they are and tell the user F-0012, F-0020, SC-0004 and CT-0003."))


# --- rm: the commands the refusal names (D49) ----------------------------------------------------


def extend(path, detail: str = "The warm-up drift of F-0020 settles within 90 seconds.") -> None:
    """Add F-0020's quantity and detail to a staged copy of F-0012, as the refusal says to."""
    text = path.read_text(encoding="utf-8").replace(
        "verified:", 'quantities:\n  - {name: warm-up time, value: 90, unit: "s"}\nverified:', 1)
    path.write_bytes((text + f"\n{detail}\n").encode("utf-8"))


def test_the_merge_the_other_way_succeeds_as_the_refusal_names_it(kb):
    merge_pair(kb)
    task = m.task(kb, "F-0012", by="reviewer-a", proponent="researcher-a")
    cu(kb, "CU-0002", MINE)
    linked = refused(kb, "rm", "F-0012", "--merged-into", "F-0020")
    assert f"The edit makes {task}, CU-0002 stale" in linked

    staged = m.kblam(kb, "edit", "F-0012")              # "kblam edit F-0012" succeeds: no copy is staged
    m.ok(staged, "edit")
    path = kb.root / staged.out.strip()
    extend(path)
    put = m.put_ok(kb, path)
    for rec_id in (task, "CU-0002"):                    # the put prints the rebind command for each record
        assert (f"kblam put: {rec_id} is now stale (this put changed F-0012, which it is bound to); a reviewer "
                f"rechecks it and runs kblam review rebind {rec_id} --by NAME --reason TEXT --expect D. kblam "
                f"validate fails until then") in put.out
    removed = m.ok(m.kblam(kb, "rm", "F-0020", "--merged-into", "F-0012"), "rm")
    assert "kblam rm: removed F-0020 (findings/motor/F-0020-motor.md), merged into F-0012" in removed.out


def test_the_merge_the_other_way_succeeds_from_the_staged_copy(kb):
    merge_pair(kb)
    task = m.task(kb, "F-0012", by="reviewer-a", proponent="researcher-a")
    path = kb.root / m.ok(m.kblam(kb, "edit", "F-0012")).out.strip()
    linked = refused(kb, "rm", "F-0012", "--merged-into", "F-0020")
    assert "make your staged copy .kblam/staging/F-0012-sensor.md also state" in linked

    extend(path)
    assert f"kblam put: {task} is now stale" in m.put_ok(kb, path).out
    m.ok(m.kblam(kb, "rm", "F-0020", "--merged-into", "F-0012"), "rm")
    assert not (kb.findings / "motor" / "F-0020-motor.md").exists()


def test_rm_cannot_make_a_record_stale(kb):
    """rm refuses a finding any record links and one another finding depends on, so the only file it
    changes besides the removed one is INDEX.md, which no record binds."""
    merge_pair(kb)
    m.task(kb, "F-0020", by="reviewer-a", proponent="researcher-a")
    kb.add("F-0013", "other", CLAIM_C, topic="tray")
    before = state(kb)
    removed = m.ok(m.kblam(kb, "rm", "F-0012", "--merged-into", "F-0020"), "rm")
    assert "stale" not in removed.out + removed.err
    after = state(kb)
    assert m.changed(before, after) == {MINE, "findings/INDEX.md", ".kblam/tree.hash"}
    assert m.validate(kb).code == 0


# --- renumber: the link definition and the refusal texts -----------------------------------------


def test_a_binding_links_only_the_file_it_matches(kb, at_root):
    """Case A: the other file can be renumbered, and doing so leaves the linked file and the record as
    they were."""
    same_id(kb)
    ct(kb, "CT-0003", MINE)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record CT-0003 links, so it keeps its ID. Renumber the other "
        f"finding with that ID instead: kblam renumber {THEIRS}."))

    before = state(kb)
    m.ok(m.kblam(kb, "renumber", THEIRS), "renumber")      # the command, as the refusal names it
    after = state(kb)
    assert after[MINE] == before[MINE]
    assert all(after[p] == b for p, b in before.items() if p.startswith(f"{kb.cfg.review_dir}/"))
    assert (kb.findings / "motor" / "F-0013-motor.md").is_file()


def test_a_withdrawn_record_still_links(kb):
    same_id(kb)
    cu(kb, "CU-0001", THEIRS, status="withdrawn")
    assert refused(kb, "renumber", kb.root / THEIRS) == message("renumber", (
        f"{THEIRS} holds F-0012, which review record CU-0001 links, so it keeps its ID. Renumber the other "
        f"finding with that ID instead: kblam renumber {MINE}."))


@pytest.mark.parametrize("setup, records, listed", [
    (lambda kb: sc(kb, "SC-0004", "F-0012"), "review record SC-0004", "SC-0004"),          # a bare ID
    (lambda kb: ct(kb, "CT-0003", "findings/nowhere.md"), "review record CT-0003", "CT-0003"),  # matches neither
    (lambda kb: (ct(kb, "CT-0003", MINE), cu(kb, "CU-0001", THEIRS)), "review records CT-0003, CU-0001",
     "CT-0003, CU-0001"),
])
def test_renumber_refuses_when_every_file_with_the_id_is_linked(kb, setup, records, listed):
    same_id(kb)
    setup(kb)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"both findings with ID F-0012 ({MINE}, {THEIRS}) are linked by {records}, and kblam renumbers no "
        f"finding a review record links. K1 fails kblam validate and every commit until a person settles "
        f"this: tell the user both paths and {listed}."))


def test_renumber_with_three_files_names_each_one_it_can_renumber(kb):
    same_id(kb, third=True)
    ct(kb, "CT-0003", MINE)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record CT-0003 links, so it keeps its ID. Renumber the other "
        f"findings with that ID that kblam can renumber instead: kblam renumber {THEIRS}; kblam renumber "
        f"{THIRD}."))
    cu(kb, "CU-0001", THIRD)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record CT-0003 links, so it keeps its ID. Renumber the other "
        f"finding with that ID that kblam can renumber instead: kblam renumber {THEIRS}."))


def test_renumber_with_three_files_all_linked(kb):
    same_id(kb, third=True)
    sc(kb, "SC-0004", "F-0012")
    assert refused(kb, "renumber", kb.root / THEIRS) == message("renumber", (
        f"all 3 findings with ID F-0012 ({MINE}, {THEIRS}, {THIRD}) are linked by review record SC-0004, and "
        f"kblam renumbers no finding a review record links. K1 fails kblam validate and every commit until a "
        f"person settles this: tell the user the 3 paths and SC-0004."))


UNREADABLE = "---\nid: F-0012\ntitle: [unclosed\n---\n\n**Claim.** The motor warm-up drift settles.\n"


def damage_unreadable(kb) -> str:
    kb.write(THEIRS, UNREADABLE)
    line, problem = parse_finding(THEIRS, UNREADABLE.encode()).parse_errors[0]
    return (f"{THEIRS} cannot be read as a finding (" + (f"line {line}: " if line else "") + f"{problem}), so "
            f"kblam cannot rewrite its id; ask a person to fix this file")


def damage_id_line(kb) -> str:
    kb.write(THEIRS, finding_text("F-0012", CLAIM_B, topic="motor").replace("id: F-0012", "id: !!str F-0012"))
    return (f"{THEIRS}: could not set id to F-0013 without changing anything else; ask a person to fix this "
            f"file's id line")


def damage_no_id(kb) -> str:
    kb.write(THEIRS, finding_text("F-0012", CLAIM_B, topic="motor").replace("id: F-0012\n", ""))
    return f"{THEIRS} has no id key, so kblam cannot rewrite it; ask a person to add its id line (id: F-0012)"


def damage_resolutions(kb) -> str:
    kb.write("kblam.resolutions.jsonl", "not json\n")
    with pytest.raises(resolutions.ResolutionError) as raised:
        resolutions.load(kb.cfg)
    return str(raised.value)


def repair(kb) -> None:
    kb.write(THEIRS, finding_text("F-0012", CLAIM_B, topic="motor"))
    (kb.root / "kblam.resolutions.jsonl").unlink(missing_ok=True)


@pytest.mark.parametrize("damage", [damage_unreadable, damage_id_line, damage_no_id, damage_resolutions])
def test_renumber_says_why_the_unlinked_file_cannot_be_renumbered_yet(kb, at_root, damage):
    """Case C, for each reason; a person fixes it, and then the command the message names succeeds."""
    same_id(kb)
    ct(kb, "CT-0003", MINE)
    reason = damage(kb)
    m.accept_tree(kb)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record CT-0003 links, so it keeps its ID; the other finding with "
        f"that ID, {THEIRS}, cannot be renumbered yet: {reason}. Ask a person to fix that, then run kblam "
        f"renumber {THEIRS}."))

    repair(kb)
    m.accept_tree(kb)
    m.ok(m.kblam(kb, "renumber", THEIRS), "renumber")
    assert (kb.findings / "motor" / "F-0013-motor.md").is_file()


def test_renumber_says_when_a_dependent_of_the_unlinked_file_cannot_be_rekeyed(kb, at_root):
    """Case C, fifth reason: a depends_on entry meaning the other file that kblam cannot re-key without
    changing anything else (its value on a line of its own); once a person rewrites the entry, the command the message names succeeds."""
    same_id(kb)
    ct(kb, "CT-0003", MINE)
    theirs_fp = binding(kb, THEIRS)[0]
    dependent = "findings/pump/F-0030-pump.md"
    kb.add("F-0030", "pump", CLAIM_C, topic="pump", extra=f"depends_on:\n  F-0012:\n    '{theirs_fp}'\n")
    renamed = "findings/motor/F-0031-motor.md"
    text = finding_text("F-0012", CLAIM_B, topic="motor").replace("id: F-0012", "id: F-0031")
    new_fp = fingerprint(parse_finding(renamed, text.encode()), "/")
    m.accept_tree(kb)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record CT-0003 links, so it keeps its ID; the other finding with "
        f"that ID, {THEIRS}, cannot be renumbered yet: {dependent}: could not change depends_on F-0012 to "
        f"F-0031 without changing anything else; change that entry by hand to F-0031: {new_fp}. Ask a person "
        f"to fix that, then run kblam renumber {THEIRS}."))

    kb.add("F-0030", "pump", CLAIM_C, topic="pump", extra=f"depends_on:\n  F-0012: '{theirs_fp}'\n")
    m.accept_tree(kb)
    m.ok(m.kblam(kb, "renumber", THEIRS), "renumber")
    assert f"F-0031: {new_fp}".encode() in (kb.root / dependent).read_bytes()


def test_renumber_with_three_files_names_each_unlinked_file_and_its_reason(kb):
    same_id(kb, third=True)
    ct(kb, "CT-0003", MINE)
    unreadable = damage_unreadable(kb)
    kb.write(THIRD, finding_text("F-0012", CLAIM_C, topic="tray").replace("id: F-0012\n", ""))
    m.accept_tree(kb)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record CT-0003 links, so it keeps its ID; the other findings with "
        f"that ID that no review record links cannot be renumbered yet: {THEIRS}: {unreadable}; {THIRD}: "
        f"{THIRD} has no id key, so kblam cannot rewrite it; ask a person to add its id line (id: F-0012). Ask a "
        f"person to fix that, then run kblam renumber "
        f"{THEIRS}; kblam renumber {THIRD}."))


def peer_state(kb, peer: str) -> None:
    """The other file: renumberable, linked by a record, or unlinked but failing renumber's dry run."""
    if peer == "linked":
        ct(kb, "CT-0003", THEIRS)
    elif peer == "broken":
        damage_no_id(kb)
    m.accept_tree(kb)


@pytest.mark.parametrize("peer", ["ready", "linked", "broken"])
def test_the_selected_files_own_refusal_offers_the_other_file_only_when_it_can_be_renumbered(kb, at_root, peer):
    same_id(kb)
    kb.write(MINE, UNREADABLE)
    peer_state(kb, peer)
    line, problem = parse_finding(MINE, UNREADABLE.encode()).parse_errors[0]
    offer = f"renumber {THEIRS} instead (kblam renumber {THEIRS}), or " if peer == "ready" else ""
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} cannot be read as a finding (" + (f"line {line}: " if line else "") + f"{problem}), so kblam "
        f"cannot rewrite its id; {offer}ask a person to fix this file."))
    if peer == "ready":
        m.ok(m.kblam(kb, "renumber", THEIRS), "renumber")      # the offered command, as printed


# --- renumber: the records a re-keyed dependent makes stale --------------------------------------


def test_renumber_lists_the_records_its_rekeying_makes_stale_and_the_rebind_runs_as_printed(kb, at_root):
    kb.add("F-0005", "sensor", CLAIM_A)
    mine_fp = binding(kb, "findings/calibration/F-0005-sensor.md")[0]
    kb.add("F-0006", "uses", CLAIM_C, topic="tray", extra=f"depends_on:\n  F-0005: '{mine_fp}'\n")
    task = m.task(kb, "F-0006", by="reviewer-a", proponent="researcher-a")
    kb.add("F-0005", "motor", CLAIM_B, topic="motor")
    m.accept_tree(kb)

    run = m.ok(m.kblam(kb, "renumber", "findings/calibration/F-0005-sensor.md"), "renumber")
    stale = (f"kblam renumber: {task} is now stale (this renumber changed F-0006, which it is bound to); a "
             f"reviewer rechecks it and runs kblam review rebind {task} --by NAME --reason TEXT --expect D. kblam "
             f"validate fails until then")
    assert stale in run.out.splitlines()
    assert m.validate(kb).code == 1

    command = stale.split("runs ", 1)[1].split(". kblam validate", 1)[0]
    values = {"NAME": "reviewer-b", "TEXT": "re-read F-0006; only its depends_on key changed",
              "D": m.expect(kb, task)}
    argv = [values.get(token, token) for token in shlex.split(command)]
    assert argv[:3] == ["kblam", "review", "rebind"]
    m.ok(m.kblam(kb, *argv[1:]), "rebind")
    assert m.validate(kb).code == 0


# --- record IDs above git history ----------------------------------------------------------------


def commit_record(kb, rec_id: str, message: str) -> None:
    kind = rec_id[:2]
    kb.write(f"{kb.cfg.review_dir}/{KINDS[kind]}/{rec_id}.yaml", record_text(kind, rec_id))
    commit_all(kb, message)


@needs_git
def test_a_record_id_in_history_only_is_never_issued_again(kb, tmp_path, monkeypatch):
    git(kb, "init", "-q")
    commit_all(kb, "the KB")
    first = git(kb, "rev-parse", "--abbrev-ref", "HEAD")
    commit_record(kb, "SC-0003", "a challenge")
    git(kb, "rm", "-q", f"{kb.cfg.review_dir}/challenges/SC-0003.yaml")
    git(kb, "commit", "-q", "-m", "remove it")                         # on the current branch, then removed
    git(kb, "checkout", "-q", "-b", "side")
    commit_record(kb, "CT-0004", "a task on a side branch")            # another local branch
    git(kb, "checkout", "-q", "-b", "fetched")
    commit_record(kb, "CU-0006", "a use only a remote-tracking ref holds")
    git(kb, "update-ref", "refs/remotes/origin/fetched", "HEAD")
    git(kb, "checkout", "-q", "-b", "tagged", first)
    commit_record(kb, "SC-0009", "a challenge only a tag holds")
    git(kb, "tag", "kept")
    git(kb, "checkout", "-q", first)
    git(kb, "branch", "-q", "-D", "fetched", "tagged")
    assert not (kb.root / kb.cfg.review_dir).exists()

    assert [review_stage.allocate_record_id(kb.cfg, kind) for kind in ("SC", "CT", "CU")] == \
        ["SC-0010", "CT-0005", "CU-0007"]
    empty = tmp_path / "no-git-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))                             # without git, history adds nothing
    assert [review_stage.allocate_record_id(kb.cfg, kind) for kind in ("SC", "CT", "CU")] == \
        ["SC-0001", "CT-0001", "CU-0001"]


@needs_git
def test_challenge_new_allocates_above_a_removed_record(kb, source_repo):
    git(kb, "init", "-q")
    (kb.root / ".gitignore").write_text(".kblam/\nresources/\n", encoding="utf-8")
    commit_record(kb, "SC-0002", "a challenge")
    git(kb, "rm", "-q", f"{kb.cfg.review_dir}/challenges/SC-0002.yaml")
    git(kb, "commit", "-q", "-m", "remove it")
    assert m.stage_challenge(kb, source_repo, "3-3", by="reviewer-a").id == "SC-0003"


@pytest.mark.parametrize("peer", ["ready", "linked", "broken"])
@pytest.mark.parametrize("damage, refusal, offer, advice", [
    ("id: !!str F-0012", f"{MINE}: could not set id to F-0013 without changing anything else; {{}}",
     f"renumber {THEIRS} instead (kblam renumber {THEIRS}), or ask a person to fix this file's id line",
     "ask a person to fix this file's id line"),
    ("", f"{MINE} has no id key, so kblam cannot rewrite it; {{}}",
     f"renumber {THEIRS} instead (kblam renumber {THEIRS})", "ask a person to add its id line (id: F-0012)"),
])
def test_the_selected_files_id_line_refusals_offer_the_other_file_only_when_it_can_be_renumbered(
        kb, at_root, damage, refusal, offer, advice, peer):
    same_id(kb)
    kb.write(MINE, finding_text("F-0012", CLAIM_A).replace("id: F-0012\n", f"{damage}\n" if damage else ""))
    peer_state(kb, peer)
    assert refused(kb, "renumber", kb.root / MINE) == message(
        "renumber", refusal.format(offer if peer == "ready" else advice) + ".")
    if peer == "ready":
        m.ok(m.kblam(kb, "renumber", THEIRS), "renumber")      # the offered command, as printed
