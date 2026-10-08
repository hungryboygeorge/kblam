"""M6.11 `kblam rm` and `kblam renumber` vs review records, and record IDs allocated above git history
(SPEC §7 "`rm` and `renumber` vs review records", §5.2.5). Every message variant is asserted in full, with
its exit status and findings/, the review root, the registry and tree.hash unchanged; and every command a
message names is run in the state the message names and succeeds (D49)."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import m611_helpers as m
from conftest import finding_text, record_text
from kblam import resolutions, review_stage
from kblam.finding import fingerprint, parse_finding
from kblam.records import KINDS

from test_bootstrap_records import (QUESTION, bootstraps_as_named, no_jev_check,  # noqa: F401 (a fixture)
                                    unbootstrapped, unrecorded)
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
    """(fingerprint v2, full-file sha256) of the finding at `path`, as a claim task or checked use
    binds it."""
    raw = (kb.root / path).read_bytes()
    return fingerprint(parse_finding(path, raw), "/"), hashlib.sha256(raw).hexdigest()


def install(kb, kind: str, rec_id: str, **fields) -> None:
    """Write a record into the review root by hand and accept the tree (fixture setup)."""
    kb.write(f"{kb.cfg.review_dir}/{KINDS[kind]}/{rec_id}.yaml", record_text(kind, rec_id, **fields))
    m.accept_tree(kb)


def ct(kb, rec_id: str, path: str, finding_id: str = "F-0012", **fields) -> None:
    """A claim task naming `finding_id`, bound to the file at `path` (a path that is not a finding: a
    stale binding)."""
    fp, sha = binding(kb, path) if (kb.root / path).is_file() else ("0badf00d0000", "0" * 64)
    install(kb, "claim-task", rec_id, finding=finding_id, claim_fingerprint=fp, base_file_sha256=sha, **fields)


def cu(kb, rec_id: str, path: str, finding_id: str = "F-0012", **fields) -> None:
    fp, sha = binding(kb, path)
    install(kb, "checked-use", rec_id, finding=finding_id, finding_fingerprint=fp, finding_file_sha256=sha, **fields)


def sc(kb, rec_id: str, *finding_ids: str, **fields) -> None:
    install(kb, "source-challenge", rec_id, linked_findings=list(finding_ids), **fields)


def merge_pair(kb, *, quantity: bool = True) -> None:
    """F-0012, the finding review records link, and F-0020, the target, which gives a quantity F-0012
    lacks unless `quantity` is False."""
    kb.add("F-0012", "sensor", CLAIM_A)
    kb.add("F-0020", "motor", CLAIM_B, topic="motor",
           extra='quantities:\n  - {name: warm-up time, value: 90, unit: "s"}\n' if quantity else "")


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


def merge_into(removed: str, target: str, one: str, lead: str) -> str:
    """The steps that move everything `removed` states into `target`, as a refusal prints them: the
    quantity route first, the removal before the put, and the whole merge in one staged copy of `target`
    (`one`). `lead` names the direction."""
    return (f"{lead}: {one}. If {removed} gives a quantity {target} lacks, kblam rm {removed} "
            f"--merged-into {target} is refused for it: add only that quantity to that copy, leave "
            f"{target}'s claim as it is installed, and kblam put it; that put leaves nothing staged, so "
            f"kblam edit {target} stages the next copy to work in. Add what {removed} states that {target} "
            f"does not yet (its detail and quantities) to the copy you are working in, run kblam rm "
            f"{removed} --merged-into {target}, then kblam put that copy (a put of {target} that states "
            f"{removed}'s fact is refused while {removed} is installed)")


def merge_text(records: str, one: str, stale: str = "") -> str:
    """The merge-the-other-way refusal for a linked finding: who links it, the one staged copy to work in,
    and the sequence that copy runs. The quantity route comes first, so the author checks it before
    choosing a route, and the removal comes before the put in every state."""
    return (f"F-0012 cannot be removed: {records} it, and kblam never removes a finding a review record "
            f"links. findings/ is unchanged. "
            + merge_into("F-0020", "F-0012", one, "Merge the other way, in one staged copy of F-0012")
            + stale + ".")


def adjudicator_route(sent: str, path: str, decides: str, note: str = "") -> str:
    """The adjudicator wording and the retire step: who settles a dead end, what an agent that is not the
    adjudicator sends it, and the commands that retire each record linking `path`; `note` is the
    independence note of a record that is not open. Where the coordinator authored one of the records and
    no librarian is deployed, no agent is left who may decide them, so the sentence ends by telling the
    user."""
    return (f"Settling this is the adjudicator's: the librarian when one is deployed, otherwise the "
            f"coordinator, and never the author of the records involved. Send {sent} to the coordinator or "
            f"librarian, who decide them, and carry on; where the coordinator authored one of them and no "
            f"librarian is deployed, tell the user. The adjudicator retires each record that links "
            f"{path} and is not retired: {decides}." + note)


def refile_text(target: str, phrase: str) -> str:
    """The re-file step: a retired record whose question still applies is filed again against the finding
    that remains (`target` in the commands, `phrase` in the sentence)."""
    return (f" For each retired record whose question still applies to {phrase}, file a new record against "
            f"{phrase}: kblam challenge new SOURCE-PATH --lines A-B --by NAME, whose free linked_findings "
            f"entry then names {target}; kblam task new {target} --kind KIND --by NAME --proponent NAME; "
            f"and, for a use, kblam use review source-challenge-NNNN {target} ORDINAL --by NAME "
            f"--proponent NAME, which "
            f"stages one only for a confirmed challenge's affected excerpt of {target}. Fill the staged "
            f"record and put it (kblam put STAGED-PATH); a use covers its excerpt only once it is approved "
            f"(K14), so an agent who is not its proponent runs kblam review decide checked-use-NNNN "
            f"--status "
            f"approved --by NAME --reason TEXT --expect D on it")


def retire_commands(kb, reason: str, *rec_ids: str) -> str:
    """The retire commands a refusal prints for `rec_ids`, each with its current subject digest (the same
    digest `kblam review decide --expect` takes) and the reason the adjudicator records."""
    return "; ".join(f'kblam review decide {rec_id} --status stale --by NAME --reason "{reason}" '
                     f"--expect {m.expect(kb, rec_id)}" for rec_id in rec_ids)


# The merge a dead end runs: the direction F-0012 into F-0020, in one staged copy of F-0020.
REASON_MERGE = "F-0012 merged into F-0020"
TARGET_FRESH = "run kblam edit F-0020, which stages one at .kblam/staging/F-0020-motor.md"
MERGE_TARGET = merge_into("F-0012", "F-0020", TARGET_FRESH,
                          "Then merge F-0012 into F-0020, in one staged copy of F-0020")
# The sentence naming the records the edit of F-0020, the target, makes stale.
STALE_TARGET = (". The edit makes checked-use-0001 stale until a reviewer rechecks and rebinds it; kblam put "
                "prints the kblam review rebind command for it")


def dead_end_text(kb, records: str, records_for: str, sent: str, rec_ids: tuple[str, ...],
                  stale: str = "", note: str = "") -> str:
    """The dead end where a record also links the target: nothing can be removed, so the refusal hands it
    to the adjudicator, who retires the records linking F-0012 and merges F-0012 into F-0020."""
    return (f"F-0012 cannot be removed: {records} it, and F-0020 cannot be removed in its place: "
            f"{records_for} it; kblam never removes a finding a review record links. findings/ is unchanged. "
            + adjudicator_route(sent, "F-0012", retire_commands(kb, REASON_MERGE, *rec_ids), note)
            + " " + MERGE_TARGET + stale + "." + refile_text("F-0020", "F-0020") + ".")

# What the refusal names as the one staged copy to work in, per staged-copy state.
EDIT_FRESH = "run kblam edit F-0012, which stages one at .kblam/staging/F-0012-sensor.md"
ONE_STAGED = "your staged copy is .kblam/staging/F-0012-sensor.md"
SEVERAL_STAGED = ("keep one of your staged copies .kblam/staging/F-0012-other.md, "
                  ".kblam/staging/F-0012-sensor.md and delete the others, and work in that copy")
# The last sentence, naming the records the put of the edited F-0012 lists as made stale.
STALE_ONE = (". The edit makes claim-task-0003 stale until a reviewer rechecks and rebinds it; kblam put prints "
             "the kblam review rebind command for it")
STALE_EACH = (". The edit makes claim-task-0003, checked-use-0002 stale until a reviewer rechecks and rebinds each; kblam "
              "put prints the kblam review rebind command for each")


def test_rm_refuses_a_linked_finding_and_says_to_merge_the_other_way(kb):
    merge_pair(kb)
    ct(kb, "claim-task-0003", MINE)
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message(
        "rm", merge_text("review record claim-task-0003 links", EDIT_FRESH, STALE_ONE))


def test_rm_names_every_record_and_each_one_the_edit_makes_stale(kb):
    merge_pair(kb)
    ct(kb, "claim-task-0003", MINE)
    cu(kb, "checked-use-0002", MINE)
    sc(kb, "source-challenge-0004", "F-0012")
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message(
        "rm", merge_text("review records source-challenge-0004, claim-task-0003, checked-use-0002 link", EDIT_FRESH, STALE_EACH))


@pytest.mark.parametrize("setup, records", [
    (lambda kb: sc(kb, "source-challenge-0004", "F-0012"), "review record source-challenge-0004 links"),
    (lambda kb: cu(kb, "checked-use-0002", MINE, status="withdrawn"), "review record checked-use-0002 links"),
    (lambda kb: ct(kb, "claim-task-0003", "findings/nowhere.md"), "review record claim-task-0003 links"),  # a stale binding
])
def test_rm_leaves_the_stale_sentence_out_when_the_edit_makes_no_record_stale(kb, setup, records):
    """Only source challenges link, only a withdrawn record, or a binding that no longer matches the
    file: the put of the edited finding lists nothing as made stale, so the refusal says nothing about
    it. A retired record does not link at all (R3e), so it cannot reach this refusal."""
    merge_pair(kb)
    setup(kb)
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message(
        "rm", merge_text(records, EDIT_FRESH))


def test_rm_with_a_staged_copy_says_to_extend_and_put_that_copy(kb):
    merge_pair(kb)
    ct(kb, "claim-task-0003", MINE)
    m.ok(m.kblam(kb, "edit", "F-0012"))
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message(
        "rm", merge_text("review record claim-task-0003 links", ONE_STAGED, STALE_ONE))


def test_rm_with_several_staged_copies_names_each(kb):
    merge_pair(kb)
    sc(kb, "source-challenge-0004", "F-0012")
    staged = m.ok(m.kblam(kb, "edit", "F-0012")).out.strip()
    kb.write(".kblam/staging/F-0012-other.md", (kb.root / staged).read_bytes())
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message(
        "rm", merge_text("review record source-challenge-0004 links", SEVERAL_STAGED))


def test_rm_dead_end_is_handed_to_the_adjudicator_in_full(kb):
    """F-0012 and F-0020 are both linked: the whole text, byte for byte, with the retire command carrying
    the record's current subject digest in full and the merge named in the direction F-0012 into F-0020."""
    merge_pair(kb)
    ct(kb, "claim-task-0003", MINE)
    cu(kb, "checked-use-0001", "findings/motor/F-0020-motor.md", finding_id="F-0020")
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", dead_end_text(
        kb, "review record claim-task-0003 links", "review record checked-use-0001 links",
        "F-0012, F-0020, claim-task-0003 and checked-use-0001", ("claim-task-0003",), STALE_TARGET))


def test_rm_dead_end_names_the_records_in_kind_order_not_string_order(kb):
    """The dead end names one record of each kind and the numbers are picked so that their string order is
    the reverse of the kind order (checked-use < claim-task < source-challenge by name): the text is in
    kind order, source challenge first."""
    merge_pair(kb)
    sc(kb, "source-challenge-0009", "F-0012")
    ct(kb, "claim-task-0002", MINE)
    cu(kb, "checked-use-0001", "findings/motor/F-0020-motor.md", finding_id="F-0020")
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", dead_end_text(
        kb, "review records source-challenge-0009, claim-task-0002 link", "review record checked-use-0001 links",
        "F-0012, F-0020, source-challenge-0009, claim-task-0002 and checked-use-0001",
        ("source-challenge-0009", "claim-task-0002"), STALE_TARGET))


def test_rm_dead_end_names_each_record_once_and_its_independence(kb):
    """A record that is not open needs a `--by` the independence rule does not forbid, so the refusal names
    each role and its value: one role for a challenge, and a task's two (its creator and its proponent) with
    "neither … nor …". A record linking both findings is listed once."""
    merge_pair(kb)
    ct(kb, "claim-task-0003", MINE, status="confirmed")
    sc(kb, "source-challenge-0004", "F-0012", "F-0020")
    install(kb, "source-challenge", "source-challenge-0002", linked_findings=["F-0012"], status="rejected", creator="reviewer-a")
    cu(kb, "checked-use-0001", "findings/motor/F-0020-motor.md", finding_id="F-0020", status="withdrawn",
       proponent="researcher-a")
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", dead_end_text(
        kb, "review records source-challenge-0002, source-challenge-0004, claim-task-0003 link", "review records source-challenge-0004, checked-use-0001 link",
        "F-0012, F-0020, source-challenge-0002, source-challenge-0004, claim-task-0003 and checked-use-0001", ("source-challenge-0002", "source-challenge-0004", "claim-task-0003"),
        note=" source-challenge-0002 is rejected, so its --by must not be its creator (source-challenge-0002's creator is reviewer-a)."
             " claim-task-0003 is confirmed, so its --by must be neither its creator (claim-task-0003's creator is "
             "reviewer-a) nor its proponent (claim-task-0003's proponent is researcher-a)."))



# --- rm: the commands the refusal names (D49) ----------------------------------------------------


# The one staged copy each state works in, as the refusal names it, and the copies staged before it.
ACQUIRE = {"none": EDIT_FRESH, "one": ONE_STAGED, "several": SEVERAL_STAGED}
KEPT = ".kblam/staging/F-0012-other.md"


def stage(kb, variant: str) -> None:
    """Stage the copies the refusal will name: `kblam edit F-0012` for one, and a second copy of it for
    several."""
    first = m.ok(m.kblam(kb, "edit", "F-0012"), "edit").out.strip()
    if variant == "several":
        kb.write(KEPT, (kb.root / first).read_bytes())


def work_in(kb, variant: str) -> str:
    """The one staged copy to work in, obtained as the refusal names it: the copy `kblam edit F-0012`
    prints when none is staged, the staged copy itself for one, and the kept copy (the other deleted) for
    several. Returns its repository-relative path."""
    if variant == "none":
        return m.ok(m.kblam(kb, "edit", "F-0012"), "edit").out.strip()
    if variant == "several":
        (kb.root / ".kblam/staging/F-0012-sensor.md").unlink()      # keep one, delete the others
        return KEPT
    return ".kblam/staging/F-0012-sensor.md"


def add_quantity(path) -> None:
    """Add only F-0020's quantity to a staged copy of F-0012, leaving its claim as it is installed: the put
    the refusal names first, which needs the quantity installed before the removal."""
    path.write_bytes(path.read_text(encoding="utf-8").replace(
        "verified:", 'quantities:\n  - {name: warm-up time, value: 90, unit: "s"}\nverified:', 1).encode("utf-8"))


def add_detail(path, detail: str = "The warm-up drift of F-0020 settles within 90 seconds.") -> None:
    """Add what F-0020 states that F-0012 does not yet to a staged copy of F-0012: the put the refusal
    names after the removal."""
    path.write_bytes((path.read_text(encoding="utf-8") + f"\n{detail}\n").encode("utf-8"))


def stale_line(task: str) -> str:
    """The put's sentence naming a claim task the put made stale, as the rebind it prints covers it."""
    return (f"kblam put: {task} is now stale (this put changed F-0012, which it is bound to); a reviewer "
            f"rechecks it and runs kblam review rebind {task} --by NAME --reason TEXT --expect D. kblam "
            f"validate fails until then")


@pytest.mark.parametrize("staged", ["none", "one", "several"])
@pytest.mark.parametrize("quantity", [False, True], ids=["no-quantity", "quantity"])
def test_the_merge_the_other_way_runs_as_the_refusal_names_it(kb, staged, quantity):
    """D49: for each staged-copy state and each route, the commands the refusal names run in its order, as
    an agent would — the copy `kblam edit` prints, never one re-staged by hand — the rebind the last put
    prints succeeds, validate passes and nothing is left open."""
    merge_pair(kb, quantity=quantity)
    task = m.task(kb, "F-0012", by="reviewer-a", proponent="researcher-a")
    if staged != "none":
        stage(kb, staged)                       # the copies the refusal names
    linked = refused(kb, "rm", "F-0012", "--merged-into", "F-0020")
    assert f"in one staged copy of F-0012: {ACQUIRE[staged]}" in linked
    if quantity:
        assert "which F-0012 does not give" in refused(kb, "rm", "F-0020", "--merged-into", "F-0012")

    path = kb.root / work_in(kb, staged)
    if quantity:
        add_quantity(path)                      # only that quantity, F-0012's claim as it is installed
        assert stale_line(task) in m.put_ok(kb, path).out
        path = kb.root / m.ok(m.kblam(kb, "edit", "F-0012"), "edit").out.strip()   # kblam edit F-0012
    add_detail(path)                            # what F-0020 states that F-0012 does not yet
    removed = m.ok(m.kblam(kb, "rm", "F-0020", "--merged-into", "F-0012"), "rm")
    assert "kblam rm: removed F-0020 (findings/motor/F-0020-motor.md), merged into F-0012" in removed.out
    put = m.put_ok(kb, path)                    # the put the refusal names last, which prints the rebind
    assert f"kblam review rebind {task} --by NAME --reason TEXT --expect D" in put.out
    assert not (kb.findings / "motor" / "F-0020-motor.md").exists()

    m.ok(m.rebind(kb, task, by="reviewer-b"), "review rebind")
    assert m.validate(kb).code == 0
    assert m.kblam(kb, "items").out == "kblam items: no open review, rejected or unchecked items\n"


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
    ct(kb, "claim-task-0003", MINE)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record claim-task-0003 links, so it keeps its ID. Renumber the other "
        f"finding with that ID instead: kblam renumber {THEIRS}."))

    before = state(kb)
    m.ok(m.kblam(kb, "renumber", THEIRS), "renumber")      # the command, as the refusal names it
    after = state(kb)
    assert after[MINE] == before[MINE]
    assert all(after[p] == b for p, b in before.items() if p.startswith(f"{kb.cfg.review_dir}/"))
    assert (kb.findings / "motor" / "F-0013-motor.md").is_file()


def test_a_withdrawn_record_still_links(kb):
    same_id(kb)
    cu(kb, "checked-use-0001", THEIRS, status="withdrawn")
    assert refused(kb, "renumber", kb.root / THEIRS) == message("renumber", (
        f"{THEIRS} holds F-0012, which review record checked-use-0001 links, so it keeps its ID. Renumber the other "
        f"finding with that ID instead: kblam renumber {MINE}."))


def bare_note(*sc_ids: str) -> str:
    """The note a renumber dead end prints for the source challenge records it retires: a challenge's
    linked_findings entry is
    a bare ID, so retiring one frees every file with that ID."""
    words = ", ".join(sc_ids[:-1]) + (" and " if len(sc_ids) > 1 else "") + sc_ids[-1]
    return (f" {words} {'lists' if len(sc_ids) == 1 else 'list'} F-0012 in linked_findings as a bare ID, so "
            f"it links every file with the ID: retiring it frees all of them, and each record is listed once.")


@pytest.mark.parametrize("setup, records, sent, retire, note", [
    (lambda kb: sc(kb, "source-challenge-0004", "F-0012"), "review record source-challenge-0004", "both paths and source-challenge-0004",
     ("source-challenge-0004",), bare_note("source-challenge-0004")),                                              # a bare ID
    (lambda kb: ct(kb, "claim-task-0003", "findings/nowhere.md"), "review record claim-task-0003", "both paths and claim-task-0003",
     ("claim-task-0003",), ""),                                                             # matches neither file
    (lambda kb: (ct(kb, "claim-task-0003", MINE), cu(kb, "checked-use-0001", THEIRS)), "review records claim-task-0003, checked-use-0001",
     "both paths, claim-task-0003 and checked-use-0001", ("claim-task-0003",), ""),                # records link the two files
])
def test_renumber_refuses_when_every_file_with_the_id_is_linked(kb, setup, records, sent, retire, note):
    same_id(kb)
    setup(kb)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", renumber_dead_end_text(
        kb, "both findings", f"{MINE}, {THEIRS}", records, sent, MINE, "two", retire, note=note))


def test_renumber_with_three_files_names_each_one_it_can_renumber(kb):
    same_id(kb, third=True)
    ct(kb, "claim-task-0003", MINE)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record claim-task-0003 links, so it keeps its ID. Renumber the other "
        f"findings with that ID that kblam can renumber instead: kblam renumber {THEIRS}; kblam renumber "
        f"{THIRD}."))
    cu(kb, "checked-use-0001", THIRD)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record claim-task-0003 links, so it keeps its ID. Renumber the other "
        f"finding with that ID that kblam can renumber instead: kblam renumber {THEIRS}."))


def test_renumber_with_three_files_all_linked(kb):
    same_id(kb, third=True)
    sc(kb, "source-challenge-0004", "F-0012")
    assert refused(kb, "renumber", kb.root / THEIRS) == message("renumber", (
        f"all 3 findings with ID F-0012 ({MINE}, {THEIRS}, {THIRD}) are linked by review record source-challenge-0004, and "
        f"kblam renumbers no finding a review record links. K1 fails kblam validate and every commit until "
        f"this is settled. "
        + adjudicator_route("the 3 paths and source-challenge-0004", THEIRS,
                            retire_commands(kb, took_new_id(THEIRS, "three"), "source-challenge-0004"), note=bare_note("source-challenge-0004"))
        + f" Then kblam renumber {THEIRS}, which prints the new ID."
        + refile_text("NEW-ID", "the ID kblam renumber prints") + "."))


UNREADABLE = "---\nid: F-0012\ntitle: [unclosed\n---\n\n**Claim.** The motor warm-up drift settles.\n"


def damage_unreadable(kb) -> str:
    kb.write(THEIRS, UNREADABLE)
    line, problem = parse_finding(THEIRS, UNREADABLE.encode()).parse_errors[0]
    return (f"{THEIRS} cannot be read as a finding (" + (f"line {line}: " if line else "") + f"{problem}), so "
            f"kblam cannot rewrite its id; leave it as it is and tell the user to repair "
            + (f"line {line}" if line else "this file"))


def damage_id_line(kb) -> str:
    kb.write(THEIRS, finding_text("F-0012", CLAIM_B, topic="motor").replace("id: F-0012", "id: !!str F-0012"))
    return (f"{THEIRS}: could not set id to F-0013 without changing anything else; leave it as it is and tell "
            f"the user to write this file's id line as id: F-0012")


def damage_no_id(kb) -> str:
    kb.write(THEIRS, finding_text("F-0012", CLAIM_B, topic="motor").replace("id: F-0012\n", ""))
    return (f"{THEIRS} has no id key, so kblam cannot rewrite it; leave it as it is and tell the user to add "
            f"its id line (id: F-0012)")


def damage_resolutions(kb) -> str:
    kb.write("kblam.resolutions.jsonl", "not json\n")
    with pytest.raises(resolutions.ResolutionError) as raised:
        resolutions.load(kb.cfg)
    return str(raised.value)


def repair(kb) -> None:
    kb.write(THEIRS, finding_text("F-0012", CLAIM_B, topic="motor"))
    (kb.root / "kblam.resolutions.jsonl").unlink(missing_ok=True)


def own_problem_clause(path: str, problem: str) -> str:
    """The clause a renumber dead end prints when the selected file itself would refuse once the records
    that link it are retired: its own problem, and the step that makes the route runnable. Every problem
    kblam writes here carries its own step, so the clause adds none: one that tells the user what to
    repair is followed by "Once it is repaired", and one that names kblam's own route by "Then"."""
    if "leave it as it is and tell the user" in problem:
        tail = f"Once it is repaired, run kblam renumber {path} again."
    else:
        tail = f"Then run kblam renumber {path} again."
    return f" Then kblam renumber {path} refuses for this file itself: {problem}. {tail}"


def repair_log_text(path: str) -> str:
    """The step out of the dead end a damaged kblam.resolutions.jsonl leaves: the log blocks every file's
    renumber, so no record is retired for nothing, and repairing it frees every file it alone blocked."""
    return f"Once it is repaired, run kblam renumber {path}."


def case_c_text(kb, reason: str, own: str = "", retire: tuple[str, ...] = ("claim-task-0003",),
                sent: str = f"{MINE} and claim-task-0003", log_to: str | None = None) -> str:
    """Case C: every other file with the ID has a problem of its own, so the refusal names it and hands the
    dead end to the adjudicator, who retires the records linking the selected file and renumbers it. `own`
    is the clause printed when the selected file itself has a problem too. `log_to` is the file to repair
    instead, for the damaged kblam.resolutions.jsonl: it blocks every file's renumber, so the refusal
    names no adjudicator route at all."""
    head = (f"{MINE} holds F-0012, which review record claim-task-0003 links, so it keeps its ID; the other finding "
            f"with that ID, {THEIRS}, cannot be renumbered yet: {reason}. ")
    if log_to is not None:
        return head + repair_log_text(log_to)
    return (head
            + adjudicator_route(sent, MINE, retire_commands(kb, took_new_id(MINE, "two"), *retire))
            + (own if own else f" Then kblam renumber {MINE}, which prints the new ID."
               + refile_text("NEW-ID", "the ID kblam renumber prints") + "."))


@pytest.mark.parametrize("damage, shared", [
    (damage_unreadable, False), (damage_id_line, False), (damage_no_id, False), (damage_resolutions, True)])
def test_renumber_says_why_the_unlinked_file_cannot_be_renumbered_yet(kb, at_root, damage, shared):
    """Case C, for each reason: the other file's problem is named, the route through the adjudicator is
    given, and the file the user must repair is named. `shared` marks a damaged kblam.resolutions.jsonl,
    which blocks the selected file's renumber too: retiring anything would be for nothing, since repairing
    the log is what lets the other file renumber, so the refusal gives no adjudicator route and ends with
    the repair and the renumber to run again."""
    same_id(kb)
    ct(kb, "claim-task-0003", MINE)
    reason = damage(kb)
    m.accept_tree(kb)
    assert refused(kb, "renumber", kb.root / MINE) == message(
        "renumber", case_c_text(kb, reason, log_to=THEIRS if shared else None))

    repair(kb)
    m.accept_tree(kb)
    m.ok(m.kblam(kb, "renumber", THEIRS), "renumber")
    assert (kb.findings / "motor" / "F-0013-motor.md").is_file()


def rekey_problem(dependent: str, rec_id: str, fp: str) -> str:
    """The reason renumbering the other file gives for a depends_on entry it cannot re-key: the entry's
    value sits on a line of its own, and the step is the agent's route through kblam (the hooks deny a
    hand edit of an installed finding)."""
    return (f"{dependent}: could not change depends_on F-0012 to F-0031 without changing anything else; "
            f"kblam edit {rec_id} stages a copy, write the entry there on its key's own line as "
            f"F-0012: {fp}, and kblam put that copy, which lets the renumber re-key it")


def test_renumber_says_when_a_dependent_of_the_unlinked_file_cannot_be_rekeyed(kb, at_root):
    """Case C, fifth reason: a depends_on entry meaning the other file that kblam cannot re-key without
    changing anything else (its value on a line of its own). D49: the entry is made re-keyable through
    kblam — the route the reason names — and the printed renumber then re-keys it as it prints."""
    same_id(kb)
    ct(kb, "claim-task-0003", MINE)
    theirs_fp = binding(kb, THEIRS)[0]
    dependent = "findings/pump/F-0030-pump.md"
    block = f"depends_on:\n  F-0012:\n    '{theirs_fp}'\n"
    kb.add("F-0030", "pump", CLAIM_C, topic="pump", extra=block)
    renamed = "findings/motor/F-0031-motor.md"
    text = finding_text("F-0012", CLAIM_B, topic="motor").replace("id: F-0012", "id: F-0031")
    new_fp = fingerprint(parse_finding(renamed, text.encode()), "/")
    m.accept_tree(kb)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", case_c_text(
        kb, rekey_problem(dependent, "F-0030", theirs_fp)))

    # The step as printed: stage the dependent, put the entry on the key's line, put the copy; the
    # renumber then re-keys it without any retirement.
    m.ok(m.kblam(kb, "edit", "F-0030"), "edit")
    staged = kb.root / ".kblam/staging/F-0030-pump.md"
    kb.write(".kblam/staging/F-0030-pump.md",
             staged.read_text().replace(block, f"depends_on:\n  F-0012: '{theirs_fp}'\n"))
    m.ok(m.kblam(kb, "put", ".kblam/staging/F-0030-pump.md"), "put")
    m.ok(m.kblam(kb, "renumber", THEIRS), "renumber")
    assert f"F-0031: {new_fp}".encode() in (kb.root / dependent).read_bytes()


def test_renumber_with_three_files_names_each_unlinked_file_and_its_reason(kb):
    same_id(kb, third=True)
    ct(kb, "claim-task-0003", MINE)
    unreadable = damage_unreadable(kb)
    no_id = f"{THIRD} has no id key, so kblam cannot rewrite it; leave it as it is and tell the user to add " \
            f"its id line (id: F-0012)"
    kb.write(THIRD, finding_text("F-0012", CLAIM_C, topic="tray").replace("id: F-0012\n", ""))
    m.accept_tree(kb)
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record claim-task-0003 links, so it keeps its ID; the other findings with "
        f"that ID that no review record links cannot be renumbered yet: {THEIRS}: {unreadable}; {THIRD}: "
        f"{no_id}. "
        + adjudicator_route(f"{MINE} and claim-task-0003", MINE,
                            retire_commands(kb, took_new_id(MINE, "three"), "claim-task-0003"))
        + f" Then kblam renumber {MINE}, which prints the new ID."
        + refile_text("NEW-ID", "the ID kblam renumber prints") + "."))


def peer_state(kb, peer: str) -> None:
    """The other file: renumberable, linked by a record, or unlinked but failing renumber's dry run."""
    if peer == "linked":
        ct(kb, "claim-task-0003", THEIRS)
    elif peer == "broken":
        damage_no_id(kb)
    m.accept_tree(kb)


@pytest.mark.parametrize("peer", ["ready", "linked", "broken"])
def test_the_selected_files_own_refusal_offers_the_other_file_only_when_it_can_be_renumbered(kb, at_root, peer):
    same_id(kb)
    kb.write(MINE, UNREADABLE)
    peer_state(kb, peer)
    line, problem = parse_finding(MINE, UNREADABLE.encode()).parse_errors[0]
    # With no alternative to offer, the refusal leaves the file and names what the user must repair: no
    # agent may edit a file under findings/ (SPEC §8 item 1).
    offer = f"renumber {THEIRS} instead (kblam renumber {THEIRS}), or " if peer == "ready" else ""
    where = f"line {line}" if line else "this file"
    assert refused(kb, "renumber", kb.root / MINE) == message("renumber", (
        f"{MINE} cannot be read as a finding (" + (f"line {line}: " if line else "") + f"{problem}), so kblam "
        f"cannot rewrite its id; {offer}leave it as it is and tell the user to repair {where}."))
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
    kind = rec_id.rsplit("-", 1)[0]
    kb.write(f"{kb.cfg.review_dir}/{KINDS[kind]}/{rec_id}.yaml", record_text(kind, rec_id))
    commit_all(kb, message)


@needs_git
def test_a_record_id_in_history_only_is_never_issued_again(kb, tmp_path, monkeypatch):
    git(kb, "init", "-q")
    commit_all(kb, "the KB")
    first = git(kb, "rev-parse", "--abbrev-ref", "HEAD")
    commit_record(kb, "source-challenge-0003", "a challenge")
    git(kb, "rm", "-q", f"{kb.cfg.review_dir}/challenges/source-challenge-0003.yaml")
    git(kb, "commit", "-q", "-m", "remove it")                         # on the current branch, then removed
    git(kb, "checkout", "-q", "-b", "side")
    commit_record(kb, "claim-task-0004", "a task on a side branch")            # another local branch
    git(kb, "checkout", "-q", "-b", "fetched")
    commit_record(kb, "checked-use-0006", "a use only a remote-tracking ref holds")
    git(kb, "update-ref", "refs/remotes/origin/fetched", "HEAD")
    git(kb, "checkout", "-q", "-b", "tagged", first)
    commit_record(kb, "source-challenge-0009", "a challenge only a tag holds")
    git(kb, "tag", "kept")
    git(kb, "checkout", "-q", first)
    git(kb, "branch", "-q", "-D", "fetched", "tagged")
    assert not (kb.root / kb.cfg.review_dir).exists()

    assert [review_stage.allocate_record_id(kb.cfg, kind) for kind in ("source-challenge", "claim-task", "checked-use")] == \
        ["source-challenge-0010", "claim-task-0005", "checked-use-0007"]
    empty = tmp_path / "no-git-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))                             # without git, history adds nothing
    assert [review_stage.allocate_record_id(kb.cfg, kind) for kind in ("source-challenge", "claim-task", "checked-use")] == \
        ["source-challenge-0001", "claim-task-0001", "checked-use-0001"]


@needs_git
def test_challenge_new_allocates_above_a_removed_record(kb, source_repo):
    git(kb, "init", "-q")
    (kb.root / ".gitignore").write_text(".kblam/\nresources/\n", encoding="utf-8")
    commit_record(kb, "source-challenge-0002", "a challenge")
    git(kb, "rm", "-q", f"{kb.cfg.review_dir}/challenges/source-challenge-0002.yaml")
    git(kb, "commit", "-q", "-m", "remove it")
    assert m.stage_challenge(kb, source_repo, "3-3", by="reviewer-a").id == "source-challenge-0003"


@needs_git
def test_claim_task_new_allocates_above_a_removed_record(kb):
    """A claim task's number is issued above the claim-task IDs the review root's history holds: a
    `claim-task-0007.yaml` that was committed and then removed keeps its number, and the other kinds'
    numbers are their own (numbering is per kind)."""
    git(kb, "init", "-q")
    commit_record(kb, "claim-task-0007", "a task")
    git(kb, "rm", "-q", f"{kb.cfg.review_dir}/tasks/claim-task-0007.yaml")
    git(kb, "commit", "-q", "-m", "remove it")
    assert review_stage.allocate_record_id(kb.cfg, "claim-task") == "claim-task-0008"
    assert review_stage.allocate_record_id(kb.cfg, "checked-use") == "checked-use-0001"


@pytest.mark.parametrize("peer", ["ready", "linked", "broken"])
@pytest.mark.parametrize("damage, refusal, offer, advice", [
    ("id: !!str F-0012", f"{MINE}: could not set id to F-0013 without changing anything else; {{}}",
     f"renumber {THEIRS} instead (kblam renumber {THEIRS}), or leave it as it is and tell the user to write "
     f"this file's id line as id: F-0012",
     "leave it as it is and tell the user to write this file's id line as id: F-0012"),
    ("", f"{MINE} has no id key, so kblam cannot rewrite it; {{}}",
     f"renumber {THEIRS} instead (kblam renumber {THEIRS})",
     "leave it as it is and tell the user to add its id line (id: F-0012)"),
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


# --- a clone: rm and renumber record no tree.hash while records are present -----------------------


def test_rm_on_a_clone_with_records_records_nothing_and_says_what_to_do(kb, no_jev_check):
    """rm writes no registry, so with records present it does not bootstrap; the removal goes in. D49:
    kblam validate passes, and kblam validate --record bootstraps."""
    merge_pair(kb)
    task = m.task(kb, "F-0020", by="reviewer-a", proponent="researcher-a")
    shutil.rmtree(kb.root / ".kblam")                       # a clone: no tree.hash, registry or Jev state

    run = m.kblam(kb, "rm", "F-0012", "--merged-into", "F-0020")

    assert run.code == 0 and run.err == unrecorded("rm"), run.out + run.err
    assert run.out.startswith("kblam rm: removed F-0012 (findings/calibration/F-0012-sensor.md), merged into "
                              "F-0020\n"), run.out
    assert unbootstrapped(kb) and m.registry(kb) is None
    bootstraps_as_named(kb, f"{task} open replication of F-0020: {QUESTION}", 1)
    assert m.registry(kb) == [task]


def test_renumber_on_a_clone_with_records_records_nothing_and_says_what_to_do(kb, at_root, no_jev_check):
    """renumber writes no registry either. D49 as for rm."""
    same_id(kb)
    ct(kb, "claim-task-0003", MINE)
    shutil.rmtree(kb.root / ".kblam")

    run = m.kblam(kb, "renumber", THEIRS)

    assert run.code == 0 and run.err == unrecorded("renumber"), run.out + run.err
    assert run.out.startswith(f"kblam renumber: F-0012 -> F-0013: {THEIRS} is now findings/motor/F-0013-motor.md "
                              f"(fingerprint "), run.out
    assert unbootstrapped(kb) and m.registry(kb) is None
    bootstraps_as_named(kb, f"claim-task-0003 open replication of F-0012: {QUESTION}", 2)
    assert m.registry(kb) == ["claim-task-0003"]


# --- R3e: retired records stop blocking, and the adjudicator settles the dead ends -----------------
#
# "Retire, then act" (user, 2026-10-07): a retired (`stale`) record no longer refuses an rm or a
# renumber, and where two findings that records link are a dead end, the adjudicator retires the records
# linking the finding that goes and runs the command. Every step below runs as printed (D49).

ADJUDICATOR = "librarian"
ASSET_ROOT = Path(__file__).resolve().parents[1] / "src" / "kblam" / "assets"
DECIDE_RE = re.compile(r'kblam review decide \S+ --status stale --by NAME --reason "[^"]*" --expect [0-9a-f]{64}')


def took_new_id(path: str, count: str) -> str:
    """The reason a retirement records before a renumber: the file the record links is taking a new ID."""
    return f"{path} took a new ID: {count} findings shared F-0012"


def retire_as_printed(kb, text: str) -> list[str]:
    """Run every retire command a refusal prints, as the adjudicator whose own name replaces `--by NAME`:
    D49's run of the step the text names. Returns the record IDs it retired, in the order printed."""
    commands = DECIDE_RE.findall(text)
    assert commands, f"the refusal printed no retire command:\n{text}"
    for command in commands:
        argv = [ADJUDICATOR if token == "NAME" else token for token in shlex.split(command)[1:]]
        m.ok(m.kblam(kb, *argv), "review decide")
    return [shlex.split(command)[3] for command in commands]


def renumber_as_printed(kb, text: str, path: str) -> str:
    """Run the `kblam renumber <path>` a refusal prints; the new ID the command printed."""
    assert f"kblam renumber {path}" in text, f"the refusal did not print a renumber of {path}:\n{text}"
    run = m.ok(m.kblam(kb, "renumber", path), "renumber")
    match = re.search(r"F-\d+ -> (F-\d+):", run.out)
    assert match, run.out
    return match.group(1)


def rebind_as_printed(kb, out: str, rec_id: str) -> None:
    """Run the rebind command a put prints for the record it made stale, with the adjudicator's name for
    `--by`, a reason for TEXT and the record's current subject digest for D."""
    line = next(line for line in out.splitlines() if "runs kblam review rebind " in line)
    command = line.split("runs ", 1)[1].split(". kblam validate", 1)[0]
    values = {"NAME": ADJUDICATOR, "TEXT": "re-read the merged finding", "D": m.expect(kb, rec_id)}
    argv = [values.get(token, token) for token in shlex.split(command)[1:]]
    m.ok(m.kblam(kb, *argv), "review rebind")


def commits_with_the_hook(kb) -> subprocess.CompletedProcess:
    """`git commit` as an agent runs it, with the pre-commit hook installed at .git/hooks/pre-commit: the
    hook refuses the commit while kblam validate fails (SPEC §8 item 4)."""
    from kblam import approval

    if shutil.which("sh") is None:
        pytest.skip("the POSIX pre-commit hook requires sh")
    git(kb, "init", "-q")
    hook = kb.root / ".git" / "hooks" / "pre-commit"
    hook.write_bytes((ASSET_ROOT / "pre-commit").read_bytes())
    os.chmod(hook, 0o755)
    if not (kb.root / ".gitignore").exists():
        kb.write(".gitignore", ".kblam/\n")
    approval.record_approval(kb.cfg, (kb.root / "kblam.toml").read_bytes())
    git(kb, "add", "-A")
    env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    return subprocess.run(["git", "-c", "user.name=kblam test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", "commit", "-q", "-m", "the KB"],
                          cwd=kb.root, env=env, capture_output=True, text=True)


def recorded_and_committed(kb) -> None:
    """The KB validates and a commit passes the installed pre-commit hook. The warnings allowed are the
    fixture's untracked evidence folder and K13's dangling link on a retired record whose finding was
    removed (a warning at that status, never an error): nothing else, and no error at all."""
    git(kb, "init", "-q")
    if not (kb.root / ".gitignore").exists():
        kb.write(".gitignore", ".kblam/\n")
    run = m.ok(m.validate(kb, "--record"), "validate --record")
    warnings = [line for line in run.out.splitlines() if "warning" in line]
    assert all("is not tracked by git" in line or "K13 warning" in line for line in warnings), run.out
    done = commits_with_the_hook(kb)
    assert done.returncode == 0, done.stdout + done.stderr


def retire(kb, *rec_ids: str, by: str = "reviewer-b") -> None:
    """Retire installed records the way the adjudicator does: `kblam review decide --status stale`, with
    the reason and the current subject digest (D49: a stored `status: stale` with no decision is not a
    retirement, and K13 refuses it)."""
    for rec_id in rec_ids:
        m.ok(m.kblam(kb, "review", "decide", rec_id, "--status", "stale", "--by", by,
                     "--reason", "settled before the merge or renumber", "--expect", m.expect(kb, rec_id)),
             "review decide")


def test_rm_succeeds_when_only_retired_records_link(kb):
    """A retired record leaves the finding's identity free: rm is an ordinary rm, so the retirement the
    adjudicator recorded is what unblocked it."""
    merge_pair(kb)
    ct(kb, "claim-task-0003", MINE)
    sc(kb, "source-challenge-0004", "F-0012")
    cu(kb, "checked-use-0001", "findings/motor/F-0020-motor.md", finding_id="F-0020")
    retire(kb, "claim-task-0003", "source-challenge-0004", "checked-use-0001")
    removed = m.ok(m.kblam(kb, "rm", "F-0012", "--merged-into", "F-0020"), "rm")
    assert removed.out.startswith(f"kblam rm: removed F-0012 ({MINE}), merged into F-0020\n"), removed.out
    assert not (kb.root / MINE).exists()
    assert m.validate(kb, "--record").code == 0


def test_renumber_succeeds_when_only_retired_records_link(kb, at_root):
    """The same for renumber: a retired record no longer keeps a file's identity."""
    same_id(kb)
    sc(kb, "source-challenge-0004", "F-0012")
    ct(kb, "claim-task-0003", MINE)
    retire(kb, "source-challenge-0004", "claim-task-0003")
    run = m.ok(m.kblam(kb, "renumber", MINE), "renumber")
    assert run.out.startswith(f"kblam renumber: F-0012 -> F-0013: {MINE} is now "
                              f"findings/calibration/F-0013-sensor.md (fingerprint "), run.out
    assert m.validate(kb, "--record").code == 0


def test_a_rejected_record_still_blocks_and_a_stale_one_leaves_the_stale_sentence_out(kb):
    """Only `stale` is retired: a rejected challenge still links, so rm refuses; and a retired task's
    binding no
    longer puts it in the list of records the edit makes stale."""
    merge_pair(kb)
    cu(kb, "checked-use-0002", MINE, status="withdrawn")
    install(kb, "source-challenge", "source-challenge-0004", linked_findings=["F-0012"], status="rejected", creator="reviewer-a")
    ct(kb, "claim-task-0003", MINE, status="stale")
    assert refused(kb, "rm", "F-0012", "--merged-into", "F-0020") == message("rm", merge_text(
        "review records source-challenge-0004, checked-use-0002 link", EDIT_FRESH))


def test_the_rm_dead_end_runs_as_the_refusal_names_it(kb, at_root):
    """D49: the retire commands the refusal prints run as printed, then the merge it names (the copy
    `kblam edit` stages, what F-0012 states added to it, the rm, the put), the rebind that put prints,
    validate --record, and a commit through the installed pre-commit hook."""
    merge_pair(kb)
    ct(kb, "claim-task-0003", MINE)
    ct(kb, "claim-task-0002", "findings/motor/F-0020-motor.md", finding_id="F-0020")
    text = refused(kb, "rm", "F-0012", "--merged-into", "F-0020")

    assert retire_as_printed(kb, text) == ["claim-task-0003"]
    path = kb.root / m.ok(m.kblam(kb, "edit", "F-0020"), "edit").out.strip()      # the copy it names
    add_detail(path, CLAIM_A)                       # what F-0012 states that F-0020 does not yet
    removed = m.ok(m.kblam(kb, "rm", "F-0012", "--merged-into", "F-0020"), "rm")
    assert removed.out.startswith(f"kblam rm: removed F-0012 ({MINE}), merged into F-0020\n"), removed.out
    put = m.put_ok(kb, path)
    assert "The edit makes claim-task-0002 stale" in text          # the sentence the refusal printed
    rebind_as_printed(kb, put.out, "claim-task-0002")
    recorded_and_committed(kb)


def test_the_rm_dead_end_independence_note_runs_with_another_name(kb):
    """The refusal names the role `--by` must not be for a record that is not open; the adjudicator uses
    another name, and the decide the note describes succeeds."""
    merge_pair(kb)
    cu(kb, "checked-use-0001", "findings/motor/F-0020-motor.md", finding_id="F-0020")
    install(kb, "source-challenge", "source-challenge-0002", linked_findings=["F-0012"], status="rejected", creator=ADJUDICATOR)
    text = refused(kb, "rm", "F-0012", "--merged-into", "F-0020")
    assert (f"source-challenge-0002 is rejected, so its --by must not be its creator (source-challenge-0002's creator is "
            f"{ADJUDICATOR}).") in text
    same = m.kblam(kb, "review", "decide", "source-challenge-0002", "--status", "stale", "--by", ADJUDICATOR,
                   "--reason", REASON_MERGE, "--expect", m.expect(kb, "source-challenge-0002"))
    assert same.code == 1 and "creator" in same.err
    m.ok(m.kblam(kb, "review", "decide", "source-challenge-0002", "--status", "stale", "--by", "reviewer-b",
                 "--reason", REASON_MERGE, "--expect", m.expect(kb, "source-challenge-0002")), "review decide")


# --- R3e: renumber's dead ends ---------------------------------------------------------------------


def renumber_dead_end_text(kb, both: str, files: str, records: str, sent: str, selected: str,
                           count: str, retire: tuple[str, ...], note: str = "", own: str = "") -> str:
    """The dead end where every file with the ID is linked: what was refused, the adjudicator wording,
    the retire commands for the records linking the selected file, and either the renumber and the
    re-file step or, when the selected file itself has a problem (`own`), that problem and the step that
    makes the route runnable."""
    return (f"{both} with ID F-0012 ({files}) are linked by {records}, and kblam renumbers no finding a "
            f"review record links. K1 fails kblam validate and every commit until this is settled. "
            + adjudicator_route(sent, selected, retire_commands(kb, took_new_id(selected, count), *retire))
            + note
            + (own if own else f" Then kblam renumber {selected}, which prints the new ID."
               + refile_text("NEW-ID", "the ID kblam renumber prints") + "."))


def test_renumber_dead_end_names_the_selected_files_own_problem(kb, at_root):
    """Case B with the selected file unreadable: it is named for what it is, not printed a renumber that
    would refuse, and the file is the user's to repair (no agent may edit findings/)."""
    same_id(kb)
    ct(kb, "claim-task-0003", MINE)                      # links the selected file
    cu(kb, "checked-use-0001", THEIRS)                    # links the other one, so every file is linked
    kb.write(MINE, UNREADABLE)
    m.accept_tree(kb)
    line, problem = parse_finding(MINE, UNREADABLE.encode()).parse_errors[0]
    own = own_problem_clause(MINE, (
        f"{MINE} cannot be read as a finding (" + (f"line {line}: " if line else "") + f"{problem}), so kblam "
        f"cannot rewrite its id; leave it as it is and tell the user to repair "
        + (f"line {line}" if line else "this file")))
    assert refused(kb, "renumber", MINE) == message("renumber", renumber_dead_end_text(
        kb, "both findings", f"{MINE}, {THEIRS}", "review records claim-task-0003, checked-use-0001",
        "both paths, claim-task-0003 and checked-use-0001", MINE, "two", ("claim-task-0003",), own=own))


def test_renumber_dead_end_names_an_unreadable_log_as_the_selected_files_own_problem(
        kb, at_root, monkeypatch):
    """D49 for N3: where kblam cannot even read kblam.resolutions.jsonl, the selected file's own clause
    ends by telling the user to repair it rather than printing a renumber that would refuse, and the
    printed route runs once the log is back."""
    same_id(kb)
    ct(kb, "claim-task-0003", MINE)                      # links the selected file
    cu(kb, "checked-use-0001", THEIRS)                    # links the other one, so every file is linked
    m.accept_tree(kb)
    log = kb.cfg.resolutions_path
    real = Path.read_bytes

    def unreadable(self):
        if self == log:
            raise PermissionError(errno.EACCES, "Permission denied")
        return real(self)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", unreadable)
        own = own_problem_clause(MINE, "cannot read kblam.resolutions.jsonl: Permission denied; leave it "
                                       "as it is and tell the user")
        text = refused(kb, "renumber", MINE)
    assert text == message("renumber", renumber_dead_end_text(
        kb, "both findings", f"{MINE}, {THEIRS}", "review records claim-task-0003, checked-use-0001",
        "both paths, claim-task-0003 and checked-use-0001", MINE, "two", ("claim-task-0003",), own=own))
    assert "Once it is repaired, run kblam renumber" in text

    assert retire_as_printed(kb, text) == ["claim-task-0003"]
    assert renumber_as_printed(kb, text, MINE) == "F-0013"


def test_renumber_dead_end_names_the_rekey_route_for_the_selected_file(kb, at_root):
    """Case B with a dependent whose `depends_on` entry kblam cannot re-key: the selected file's own
    problem names kblam's route (stage the dependent, put the entry on its key's own line, put the copy),
    so the clause ends "Then run kblam renumber <path> again" and names no repair by the user. D49: the
    printed edit and put make the entry re-keyable, the adjudicator retires the record that links the
    selected file, and the printed renumber then re-keys the entry as it prints."""
    same_id(kb)
    ct(kb, "claim-task-0003", MINE)                      # links the selected file
    cu(kb, "checked-use-0001", THEIRS)                    # links the other one, so every file is linked
    mine_fp = binding(kb, MINE)[0]
    dependent = "findings/pump/F-0030-pump.md"
    block = f"depends_on:\n  F-0012:\n    '{mine_fp}'\n"
    kb.add("F-0030", "pump", CLAIM_C, topic="pump", extra=block)
    m.accept_tree(kb)
    own = own_problem_clause(MINE, rekey_problem(dependent, "F-0030", mine_fp))
    text = refused(kb, "renumber", MINE)
    assert text == message("renumber", renumber_dead_end_text(
        kb, "both findings", f"{MINE}, {THEIRS}", "review records claim-task-0003, checked-use-0001",
        "both paths, claim-task-0003 and checked-use-0001", MINE, "two", ("claim-task-0003",), own=own))
    assert "Leave the files as they are" not in text and "Once it is repaired" not in text

    # The step as printed: stage the dependent, put the entry on its key's own line, and put the copy.
    m.ok(m.kblam(kb, "edit", "F-0030"), "edit")
    staged = kb.root / ".kblam/staging/F-0030-pump.md"
    kb.write(".kblam/staging/F-0030-pump.md",
             staged.read_text().replace(block, f"depends_on:\n  F-0012: '{mine_fp}'\n"))
    m.ok(m.kblam(kb, "put", ".kblam/staging/F-0030-pump.md"), "put")

    assert retire_as_printed(kb, text) == ["claim-task-0003"]
    assert renumber_as_printed(kb, text, MINE) == "F-0031"
    new_fp = binding(kb, "findings/calibration/F-0031-sensor.md")[0]
    assert f"F-0031: {new_fp}".encode() in (kb.root / dependent).read_bytes()


@needs_git
def test_the_renumber_dead_end_routes_a_status_kblam_cannot_decide_through_validate(kb, at_root):
    """D49 for a record whose status is outside its kind's vocabulary: no decision takes such a record
    anywhere, so its retire step is kblam validate's own line for the record rather than a decide command,
    and running what that line says — the git restore that puts the committed record back — leaves the
    ordinary adjudicator route, with the decide for the restored record."""
    same_id(kb)
    ct(kb, "claim-task-0003", MINE)
    cu(kb, "checked-use-0001", THEIRS)
    m.accept_tree(kb)
    git(kb, "init", "-q")
    commit_all(kb, "the KB as kblam left it")
    path = f"{kb.cfg.review_dir}/tasks/claim-task-0003.yaml"
    sound = (kb.root / path).read_text(encoding="utf-8")   # read as text: git may restore CRLF line ends
    fp, sha = binding(kb, MINE)
    kb.write(path, record_text("claim-task", "claim-task-0003", finding="F-0012", claim_fingerprint=fp,
                               base_file_sha256=sha, status="bogus"))          # the hand edit
    m.accept_tree(kb)

    text = refused(kb, "renumber", MINE)
    assert (f"claim-task-0003's status is not one kblam can decide from; run kblam validate and do what its line "
            f"for {path} says, then run kblam renumber {MINE} again") in text
    assert DECIDE_RE.search(text) is None                  # no decision takes that status anywhere

    line = next(line for line in m.validate(kb).out.splitlines() if line.startswith(f"K13 {path}:"))
    restore = re.search(r"\((git restore --source=HEAD --staged --worktree [^)]+)\)", line)
    assert restore is not None, line
    assert restore.group(1) == f"git restore --source=HEAD --staged --worktree {path}"

    git(kb, *restore.group(1).split()[1:])                 # the step that line names, run
    assert (kb.root / path).read_text(encoding="utf-8") == sound

    again = refused(kb, "renumber", MINE)                  # the renumber the step says to run again
    assert "not one kblam can decide from" not in again
    assert retire_as_printed(kb, again) == ["claim-task-0003"]     # the ordinary adjudicator route
    assert renumber_as_printed(kb, again, MINE) == "F-0013"


def test_renumber_dead_end_is_handed_to_the_adjudicator_in_full(kb, at_root):
    """Case B with two files and one source challenge: the whole text, byte for byte, including the
    bare-ID note."""
    same_id(kb)
    sc(kb, "source-challenge-0004", "F-0012")
    assert refused(kb, "renumber", MINE) == message("renumber", renumber_dead_end_text(
        kb, "both findings", f"{MINE}, {THEIRS}", "review record source-challenge-0004", "both paths and source-challenge-0004", MINE,
        "two", ("source-challenge-0004",), note=bare_note("source-challenge-0004")))


def test_renumber_dead_end_with_three_files_list_each_record_once(kb, at_root):
    """Case B with three files and a claim task linking only the selected one: the retire list names each
    record
    once, and the record list in the head names every record that links any file."""
    same_id(kb, third=True)
    ct(kb, "claim-task-0003", MINE)
    sc(kb, "source-challenge-0004", "F-0012")
    assert refused(kb, "renumber", MINE) == message("renumber", renumber_dead_end_text(
        kb, "all 3 findings", f"{MINE}, {THEIRS}, {THIRD}", "review records source-challenge-0004, claim-task-0003",
        "the 3 paths, source-challenge-0004 and claim-task-0003", MINE, "three", ("source-challenge-0004", "claim-task-0003"),
        note=bare_note("source-challenge-0004")))


def test_the_renumber_dead_end_runs_as_the_refusal_names_it(kb, at_root):
    """D49: retire the records the refusal names, renumber the selected file as printed, file a new record
    against the new ID it printed, and the KB validates and commits."""
    same_id(kb)
    sc(kb, "source-challenge-0004", "F-0012")
    text = refused(kb, "renumber", MINE)

    assert retire_as_printed(kb, text) == ["source-challenge-0004"]
    assert renumber_as_printed(kb, text, MINE) == "F-0013"
    assert (kb.findings / "calibration" / "F-0013-sensor.md").is_file()
    # the re-file step: the retired record's question again, against the new ID the renumber printed
    m.ok(m.kblam(kb, "task", "new", "F-0013", "--kind", "replication", "--by", "reviewer-a",
                 "--proponent", "researcher-a"), "task new")
    recorded_and_committed(kb)


def test_renumber_dead_end_names_the_unlinked_files_problem_and_its_route(kb, at_root):
    """Case C: the other file's problem is named as before, and the route replaces the step that sent an
    agent to a person to fix it."""
    same_id(kb)
    ct(kb, "claim-task-0003", MINE)
    reason = damage_no_id(kb)
    m.accept_tree(kb)
    assert refused(kb, "renumber", MINE) == message("renumber", (
        f"{MINE} holds F-0012, which review record claim-task-0003 links, so it keeps its ID; the other finding "
        f"with that ID, {THEIRS}, cannot be renumbered yet: {reason}. "
        + adjudicator_route(f"{MINE} and claim-task-0003", MINE,
                            retire_commands(kb, took_new_id(MINE, "two"), "claim-task-0003"))
        + f" Then kblam renumber {MINE}, which prints the new ID."
        + refile_text("NEW-ID", "the ID kblam renumber prints") + "."))


@pytest.mark.parametrize("damage", [damage_unreadable, damage_id_line, damage_no_id, damage_resolutions])
def test_the_renumber_dead_end_with_a_damaged_other_file_runs_as_printed(kb, at_root, damage):
    """D49: the route runs whatever the damage is, and every file the user must repair is left to them.
    For the three damages to the other file, the route runs straight away (the retire step first) and the
    other file is left as it is. A damaged kblam.resolutions.jsonl blocks every file's renumber, so no
    record is retired for nothing: the text gives no retire command, the user repairs the log (the step
    the text names), and the renumber of the other file then succeeds."""
    same_id(kb)
    ct(kb, "claim-task-0003", MINE)
    damage(kb)
    m.accept_tree(kb)
    text = refused(kb, "renumber", MINE)

    if damage is damage_resolutions:
        assert not DECIDE_RE.search(text) and "Once it is repaired" in text
        repair(kb)                                           # the user repairs it, as the text says
        m.accept_tree(kb)
    else:
        assert retire_as_printed(kb, text) == ["claim-task-0003"]
        assert "leave it as it is and tell the user" in text     # the damaged file is left, per the text
    # The renumber the text names, run again: the selected file keeps its ID when the log is the damage,
    # since that route retires nothing.
    which = THEIRS if damage is damage_resolutions else MINE
    assert renumber_as_printed(kb, text, which) == "F-0013"
    if damage is not damage_resolutions:
        repair(kb)                                           # the user repairs the other file
        m.ok(m.kblam(kb, "index"), "index")
        m.accept_tree(kb)
    m.ok(m.kblam(kb, "task", "new", "F-0013", "--kind", "replication", "--by", "reviewer-a",
                 "--proponent", "researcher-a"), "task new")
    recorded_and_committed(kb)


def test_the_selected_files_own_damage_leaves_the_file_and_tells_the_user(kb, at_root):
    """Case C's reasons for the selected file itself, with no other file an agent could renumber: the
    refusal says to leave the file as it is and what the user must write, since no agent may edit a file
    under findings/."""
    same_id(kb)
    ct(kb, "claim-task-0003", THEIRS)                     # the other file is linked, so there is no alternative
    kb.write(MINE, finding_text("F-0012", CLAIM_A).replace("id: F-0012\n", ""))
    m.accept_tree(kb)
    assert refused(kb, "renumber", MINE) == message("renumber", (
        f"{MINE} has no id key, so kblam cannot rewrite it; leave it as it is and tell the user to add its "
        f"id line (id: F-0012)."))

    kb.write(MINE, finding_text("F-0012", CLAIM_A).replace("id: F-0012", "id: !!str F-0012"))
    m.accept_tree(kb)
    assert refused(kb, "renumber", MINE) == message("renumber", (
        f"{MINE}: could not set id to F-0013 without changing anything else; leave it as it is and tell the "
        f"user to write this file's id line as id: F-0012."))

