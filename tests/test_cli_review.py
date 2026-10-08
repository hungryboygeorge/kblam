"""The §5.2.5 commands at the console entry point (SPEC §5.2.5, §5.2.6, §7): subcommands, output and exit
codes. Records come from the nested Git fixture repository; staging runs through the CLI, so these tests
cover the whole path from `challenge new` to `validate`. Offline and deterministic: `_today` is pinned and
the fixtures enable no Jev verdict, so no request leaves the machine."""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path

import pytest

from conftest import TRACE_PATH, record_text
from kblam import records, review_stage, review_write
from kblam.cli import main
from kblam.decisions import subject_digest
from kblam.finding import yaml_rt
from kblam.hook import SKILL_POINTER
from kblam.review_index import generate_review_index
from kblam.treehash import write_tree_hash_v2
from kblam.view import load_view

from test_concurrency import hold_lock, set_lock_config
from test_k14 import CLAIM, LINE3, REVIEW, TRACE, add_finding, put as install_record, quoted, sc, use
from test_review_stage import ct
from test_store import CLAIM_A, fill as fill_finding

TODAY = datetime.date(2026, 9, 28)
STAGING = ".kblam/review-staging"
RECEIPTS = ".kblam/review-receipts"
CHALLENGES = f"{REVIEW}/challenges"
LINE2 = "Row 101: bytes 0x3A 0x3B"            # TRACE_TEXT line 2
RANGE_WARNING = (f"the cited range {TRACE}:3-3 overlaps lines 3-3 of source-challenge-0001's assertion without "
                 f"quoting it; check that the excerpt does not rely on the challenged text "
                 f"(kblam challenge uses source-challenge-0001 lists what source-challenge-0001 affects)")


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """`created` and a decision's `date` are today's: pin them, so the bytes kblam writes are fixed."""
    monkeypatch.setattr(review_stage, "_today", lambda: TODAY)
    monkeypatch.setattr(review_write, "_today", lambda: TODAY)
    return TODAY


# --- driving the CLI and building the fixtures ---------------------------------------------------


def run(kb, *argv: str) -> int:
    """`kblam --root <kb> <argv>` in process, as the console entry point runs it."""
    return main(["--root", str(kb.root), *argv])


def stage(kb, capsys, *argv: str) -> Path:
    """A staging command's success: it exits 0 and prints the staged path."""
    assert run(kb, *argv) == 0
    return Path(capsys.readouterr().out.strip())


def accept_tree(kb, capsys) -> None:
    """Fixture setup: write the review index from the records present, then accept the whole tree, as
    `kblam review index` and `kblam validate --record` would. Findings written through `kb.add` have
    already regenerated findings/INDEX.md. The fixture's own out-of-band writes are not the command under
    test, so their diagnostics are dropped."""
    view = load_view(kb.cfg)
    if view.records:
        kb.write(f"{REVIEW}/INDEX.md", generate_review_index(view))
    write_tree_hash_v2(kb.cfg, load_view(kb.cfg))
    capsys.readouterr()


def digest_of(kb, rec_id: str) -> str:
    """The installed record's subject digest, as show and list print it."""
    rec = next(r for r in load_view(kb.cfg).records if r.id == rec_id)
    return subject_digest(rec.kind, rec.data)


def fill(path: Path, **fields) -> None:
    """Fill a staged record's blank fields, as its author does, and write it back with LF endings."""
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    data.update(fields)
    path.write_bytes(records.dump(data))


def sc_fields(source_repo) -> dict:
    """The blank source-challenge fields the author fills before the first put (SPEC §5.2.5)."""
    return {"proposition": "The printed byte equality follows from the printed byte values",
            "scope": ["MX-100 capture transcription"],
            "classification": "contradicted",
            "basis": [{"path": source_repo.kb_path(), "sha256": None, "repo": None, "commit": None,
                       "blob": None, "snapshot": None, "locator": "row 102: printed byte values",
                       "role": "internal-inconsistency", "provenance": "observed"}],
            "usable": "The printed byte values may be cited as a report.",
            "limits": "Do not infer the capture bytes from this row."}


def ct_fields() -> dict:
    """The blank claim-task fields the author fills before the first put."""
    return {"question": "Does an independent measurement establish the claim?",
            "method": "Repeat the capture with the documented settings.",
            "outcomes": {"supports": "The ratio is within 0.1%.", "refutes": "The ratio differs by more.",
                         "inconclusive": "The capture is too noisy to tell."},
            "controls": ["same firmware version"],
            "stop": "Stop after three captures.",
            "expected_evidence": ["an evidence/ capture package"]}


def cu_fields() -> dict:
    """The blank checked-use fields the author fills before the first put."""
    return {"disposition": "unaffected_raw_bytes",
            "reason": "The excerpt is cited only for the printed byte values."}


def registry_ids(kb):
    """The registry's contents, or None when the file does not exist."""
    path = kb.root / ".kblam/review-ids"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def staged_sc(kb, capsys, source_repo, *, lines: str = "3-3") -> Path:
    """`challenge new` plus the author's Filling: a source challenge ready for `put`."""
    path = stage(kb, capsys, "challenge", "new", TRACE, "--lines", lines, "--by", "reviewer-a")
    fill(path, **sc_fields(source_repo))
    return path


# --- challenge new, edit, show, uses, pin ---------------------------------------------------------


def test_challenge_new_stages_and_prints_the_path(kb, source_repo, capsys):
    assert run(kb, "challenge", "new", source_repo.kb_path(), "--lines", "2-2",
               "--by", "reviewer-a") == 0
    assert capsys.readouterr().out == f"{kb.root / STAGING / 'source-challenge-0001.yaml'}\n"
    assert (kb.root / STAGING / "source-challenge-0001.yaml").is_file()


def test_challenge_new_refuses_lines_outside_the_source(kb, source_repo, capsys):
    """A StoreError, not a usage error: whether the range fits is review_stage's to refuse."""
    assert run(kb, "challenge", "new", TRACE, "--lines", "3-9", "--by", "reviewer-a") == 1
    assert capsys.readouterr().err == \
        "kblam challenge new: lines 3-9 are outside the source (it has 4 lines)\n"
    assert not (kb.root / STAGING / "source-challenge-0001.yaml").exists()


def test_challenge_edit_stages_a_copy_with_its_edit_base_receipt(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo, status="open"))
    accept_tree(kb, capsys)
    assert run(kb, "challenge", "edit", "source-challenge-0001") == 0
    staged = Path(capsys.readouterr().out.strip())
    assert staged == kb.root / STAGING / "source-challenge-0001.yaml"
    assert staged.read_bytes() == (kb.root / CHALLENGES / "source-challenge-0001.yaml").read_bytes()
    assert (kb.root / RECEIPTS / "source-challenge-0001.edit-base.json").is_file()


def test_challenge_show_prints_the_subject_digest_and_the_source(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo))
    accept_tree(kb, capsys)
    assert run(kb, "challenge", "show", "source-challenge-0001") == 0
    text = capsys.readouterr().out
    assert text.splitlines()[:6] == [
        "source-challenge-0001 confirmed",
        f"subject digest: {digest_of(kb, 'source-challenge-0001')}",
        f"source: {TRACE}",
        f"version: {source_repo.blob(TRACE_PATH)}",
        "state: current",
        "assertion: lines 3-3",
    ]
    assert text.endswith("\n") and not text.endswith("\n\n")   # exactly one final newline


def test_challenge_uses_names_the_excerpt_and_the_command(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", LINE3)
    accept_tree(kb, capsys)
    assert run(kb, "challenge", "uses", "source-challenge-0001") == 0
    assert capsys.readouterr().out == \
        ("F-0001 findings/calibration/F-0001-ratio.md:15 excerpt 1 same: error; "
         "kblam use review source-challenge-0001 F-0001 1 --by NAME --proponent NAME\n")


def test_challenge_pin_pins_a_provisional_source(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo, status="open", pin=False))  # committed, but not pinned
    accept_tree(kb, capsys)
    assert run(kb, "challenge", "pin", "source-challenge-0001", "--expect", digest_of(kb, "source-challenge-0001")) == 0
    assert capsys.readouterr().out == \
        f"kblam challenge pin: source-challenge-0001 pinned (subject digest {digest_of(kb, 'source-challenge-0001')[:12]})\n"


def test_challenge_pin_refuses_a_pin_that_is_already_there(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo, status="open"))
    accept_tree(kb, capsys)
    assert run(kb, "challenge", "pin", "source-challenge-0001", "--expect", digest_of(kb, "source-challenge-0001")) == 1
    assert capsys.readouterr().err == \
        ("kblam challenge pin: source-challenge-0001's source is already pinned; a pin is never replaced. To pin "
         "another version, write a new challenge\n")


# --- task new, edit, show; use review -------------------------------------------------------------


def test_task_new_stages_a_task_bound_to_the_finding(kb, source_repo, capsys):
    add_finding(kb, "3", LINE3)
    accept_tree(kb, capsys)
    path = stage(kb, capsys, "task", "new", "F-0001", "--kind", "replication", "--by", "reviewer-a",
                 "--proponent", "researcher-a")
    assert path == kb.root / STAGING / "claim-task-0001.yaml"
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    assert (data["kind"], data["finding"], data["status"], data["question"]) == \
        ("replication", "F-0001", "open", "")


def test_task_new_refuses_a_finding_that_is_not_there(kb, capsys):
    assert run(kb, "task", "new", "F-0400", "--kind", "confirmation", "--by", "reviewer-a",
               "--proponent", "researcher-a") == 1
    assert capsys.readouterr().err == \
        "kblam task new: F-0400 is not in findings/; write it with kblam new and kblam put\n"


def test_task_edit_stages_a_copy_and_task_show_prints_the_binding(kb, source_repo, capsys):
    add_finding(kb, "3", LINE3)
    install_record(kb, "claim-task", ct(kb))                          # open, bound to F-0001 as it is now
    accept_tree(kb, capsys)
    assert run(kb, "task", "edit", "claim-task-0001") == 0
    assert Path(capsys.readouterr().out.strip()) == kb.root / STAGING / "claim-task-0001.yaml"
    assert run(kb, "task", "show", "claim-task-0001") == 0
    text = capsys.readouterr().out
    assert text.splitlines()[:5] == ["claim-task-0001 open", f"subject digest: {digest_of(kb, 'claim-task-0001')}",
                                     "kind: replication", "finding: F-0001",
                                     "proponent: researcher-a"]
    assert "\nbinding: current\n" in text


def test_use_review_refuses_a_challenge_that_is_not_confirmed(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo, status="open"))
    add_finding(kb, "3", LINE3)
    accept_tree(kb, capsys)
    assert run(kb, "use", "review", "source-challenge-0001", "F-0001", "1", "--by", "reviewer-a",
               "--proponent", "researcher-a") == 1
    assert capsys.readouterr().err == \
        ("kblam use review: source-challenge-0001 is open; only a confirmed challenge can be used (kblam review decide "
         "source-challenge-0001 --status confirmed --by NAME --reason TEXT --expect D)\n")


# --- review index and review list -----------------------------------------------------------------


def test_review_index_writes_the_index_and_the_tree_hash(kb, source_repo, capsys):
    add_finding(kb, "3", LINE3)
    install_record(kb, "claim-task", ct(kb))
    accept_tree(kb, capsys)
    assert run(kb, "review", "index") == 0
    assert capsys.readouterr().out == \
        f"kblam review index: wrote {REVIEW}/INDEX.md and .kblam/tree.hash\n"
    assert (kb.root / REVIEW / "INDEX.md").read_text(encoding="utf-8").startswith(
        "# Review index (generated by `kblam review index`; do not edit by hand)\n")


def test_review_list_prints_one_line_per_record_and_honours_open(kb, source_repo, capsys):
    add_finding(kb, "3", LINE3)
    install_record(kb, "source-challenge", sc(source_repo))
    install_record(kb, "claim-task", ct(kb))
    accept_tree(kb, capsys)
    assert run(kb, "review", "list") == 0
    assert capsys.readouterr().out.splitlines() == [
        f"source-challenge-0001 challenge confirmed {digest_of(kb, 'source-challenge-0001')[:12]} {TRACE}:3-3 current",
        f"claim-task-0001 task open {digest_of(kb, 'claim-task-0001')[:12]} replication of F-0001 current",
    ]
    assert run(kb, "review", "list", "--open") == 0
    assert capsys.readouterr().out == \
        f"claim-task-0001 task open {digest_of(kb, 'claim-task-0001')[:12]} replication of F-0001 current\n"


def test_review_list_prints_nothing_without_records(kb, capsys):
    assert run(kb, "review", "list") == 0
    assert capsys.readouterr().out == ""


# --- decisions: decide and rebind -----------------------------------------------------------------


def test_review_decide_refuses_a_decision_by_the_creator(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo, status="open"))   # its creator is reviewer-a
    accept_tree(kb, capsys)
    assert run(kb, "review", "decide", "source-challenge-0001", "--status", "confirmed", "--by", "reviewer-a",
               "--reason", "read it myself", "--expect", digest_of(kb, "source-challenge-0001")) == 1
    assert capsys.readouterr().err == \
        "kblam review decide: reviewer-a is source-challenge-0001's creator; a closing decision needs someone else\n"


def test_review_decide_refused_by_the_records_own_error_lists_it(kb, source_repo, capsys):
    """A decision blocks on the errors owned by the record it acts on (SPEC §5.2.4): the confirmation
    needs primary support, so the write is refused and nothing changes."""
    data = sc(source_repo, status="open")
    data["basis"][0]["provenance"] = "inferred"               # not in [review] primary_provenance
    install_record(kb, "source-challenge", data)
    accept_tree(kb, capsys)
    assert run(kb, "review", "decide", "source-challenge-0001", "--status", "confirmed", "--by", "reviewer-b",
               "--reason", "read the source", "--expect", digest_of(kb, "source-challenge-0001")) == 1
    text = capsys.readouterr().out
    assert text.splitlines() == [
        f"K13 {CHALLENGES}/source-challenge-0001.yaml:24: source-challenge-0001 is confirmed with no primary support; a confirmation "
        f"needs a basis entry whose provenance is one of observed, decoded and whose resolved path is "
        f"outside findings/, {REVIEW}/ and the history folders",
        f"kblam review decide: refused source-challenge-0001 (1 error(s)); {REVIEW}/ is unchanged. Fix what is listed "
        f"above and run it again.",
    ]
    assert next(r for r in load_view(kb.cfg).records if r.id == "source-challenge-0001").status == "open"


def test_review_decide_refuses_a_stale_expect(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo))
    accept_tree(kb, capsys)
    assert run(kb, "review", "decide", "source-challenge-0001", "--status", "confirmed", "--by", "reviewer-b",
               "--reason", "read it", "--expect", "a" * 12) == 1
    assert capsys.readouterr().err == \
        ("kblam review decide: source-challenge-0001 changed since you inspected it; show it again: "
         "kblam challenge show source-challenge-0001\n")


def test_review_rebind_refuses_a_challenge(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo))
    accept_tree(kb, capsys)
    assert run(kb, "review", "rebind", "source-challenge-0001", "--by", "reviewer-b", "--reason", "rechecked it",
               "--expect", digest_of(kb, "source-challenge-0001")) == 1
    assert capsys.readouterr().err == \
        ("kblam review rebind: a challenge has no rebind; a changed assertion or judgement is a new "
         "challenge\n")


def test_review_rebind_rebinds_a_stale_use(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", LINE3)
    install_record(kb, "checked-use", use(kb))                         # approved, current: validate is clean
    accept_tree(kb, capsys)
    assert run(kb, "validate") == 0

    kb.add("F-0001", "ratio", CLAIM, body=quoted(f"{TRACE}:3", LINE3) + "\n\nAnother detail.\n")
    accept_tree(kb, capsys)
    assert run(kb, "validate") == 1                           # the use no longer holds: K14 fires again
    assert run(kb, "review", "rebind", "checked-use-0001", "--by", "reviewer-b", "--reason",
               "rechecked the excerpt in the new revision",
               "--expect", digest_of(kb, "checked-use-0001")) == 0
    text = capsys.readouterr().out
    assert "K13 warning research-review/uses/checked-use-0001.yaml: F-0001's file bytes changed since this use " \
           "was bound" in text
    assert text.splitlines()[-1] == \
        (f"kblam review rebind: checked-use-0001 rebound, now approved "
         f"(subject digest {digest_of(kb, 'checked-use-0001')[:12]})")
    assert run(kb, "validate") == 0


# --- put: dispatch, refusals and the obligations it lists -----------------------------------------


def test_put_of_a_staged_finding_is_still_a_finding_put(kb, capsys):
    """An F- file does not match records.FILENAME_RE, so put dispatches to store.put."""
    assert run(kb, "new", "calibration", "sensor curve types") == 0
    staged = Path(capsys.readouterr().out.strip())
    fill_finding(staged, CLAIM_A)
    assert run(kb, "put", str(staged)) == 0
    assert capsys.readouterr().out.splitlines()[0] == \
        "kblam put: F-0001 -> findings/calibration/F-0001-sensor-curve-types.md"


def test_put_of_a_staged_record_installs_it(kb, source_repo, capsys):
    staged = staged_sc(kb, capsys, source_repo)
    assert run(kb, "put", str(staged)) == 0
    assert capsys.readouterr().out == "kblam put: source-challenge-0001 -> research-review/challenges/source-challenge-0001.yaml\n"
    assert (kb.root / CHALLENGES / "source-challenge-0001.yaml").is_file() and not staged.exists()


def test_a_refused_record_put_writes_nothing(kb, capsys):
    """The blank task fails schema_issues, which is an error owned by claim-task-0001: the write is refused."""
    add_finding(kb, "3", LINE3)
    accept_tree(kb, capsys)
    staged = stage(kb, capsys, "task", "new", "F-0001", "--kind", "replication", "--by", "reviewer-a",
                   "--proponent", "researcher-a")
    assert run(kb, "put", str(staged)) == 1
    text = capsys.readouterr().out
    first = text.splitlines()[0]
    where = f"K15 {STAGING}/claim-task-0001.yaml:"           # the staged file, not the canonical path (D33)
    assert first.startswith(where)
    assert staged.read_text(encoding="utf-8").split("\n")[int(first[len(where):].split(":")[0]) - 1] \
        .startswith("question:")
    assert text.splitlines()[-1] == \
        (f"kblam put: rejected claim-task-0001 (8 error(s)); {REVIEW}/ is unchanged. Fix the staged file and put "
         f"it again. {SKILL_POINTER}")
    assert not (kb.root / REVIEW / "tasks").exists() and staged.is_file()


def test_a_record_put_without_its_receipt_is_refused_with_the_pointer(kb, capsys):
    """A first put needs the allocation receipt the staging commands write: the pointer suffix is put's."""
    staged = kb.write(f"{STAGING}/source-challenge-0001.yaml", record_text("source-challenge"))
    assert run(kb, "put", str(staged)) == 1
    assert capsys.readouterr().err == \
        (f"kblam put: source-challenge-0001 has no allocation receipt in {RECEIPTS}/; a first put needs the one kblam "
         f"challenge new, kblam task new or kblam use review wrote. Draft a new challenge and put that. "
         f"{SKILL_POINTER}\n")
    assert staged.is_file() and not (kb.root / CHALLENGES).exists()


def test_a_record_put_lists_errors_owned_by_other_files(kb, source_repo, capsys):
    """K1-K11 never refuse a record put: the other file's error is listed and validate still fails."""
    kb.add("F-0002", "history", "The first curve type was superseded by the second.")
    staged = staged_sc(kb, capsys, source_repo)
    assert run(kb, "put", str(staged)) == 0
    text = capsys.readouterr().out
    assert "kblam put: source-challenge-0001 -> research-review/challenges/source-challenge-0001.yaml" in text
    assert "K4 findings/calibration/F-0002-history.md:" in text
    assert text.endswith("kblam put: done, but kblam validate still fails (1 error(s) listed above, owned "
                         "by other findings or records)\n")


def test_editing_a_finding_lists_the_use_it_makes_stale(kb, source_repo, capsys):
    """SPEC §12 M6.11 group 4: a body-only edit keeps the affected excerpt, so the put succeeds, lists the
    use it makes stale and the K14 error it keeps, and validate fails until the use is rebound."""
    install_record(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", LINE3)
    install_record(kb, "checked-use", use(kb))
    accept_tree(kb, capsys)
    assert run(kb, "validate") == 0
    capsys.readouterr()                                       # validate's own output is asserted below

    assert run(kb, "edit", "F-0001") == 0
    staged = Path(capsys.readouterr().out.strip())
    staged.write_bytes(staged.read_bytes() + b"\nAnother detail.\n")
    assert run(kb, "put", str(staged)) == 0
    text = capsys.readouterr().out
    assert ("kblam put: checked-use-0001 is now stale (this put changed F-0001, which it is bound to); a reviewer "
            "rechecks it and runs kblam review rebind checked-use-0001 --by NAME --reason TEXT --expect D. kblam "
            "validate fails until then") in text
    # a put reports its own file at the staged path it read (`view.display`, SPEC §5.2.5)
    assert "K14 .kblam/staging/F-0001-ratio.md:15: source-challenge-0001 challenges this quoted assertion at " \
           f"{TRACE}@" in text
    assert text.endswith("kblam put: done, but kblam validate still fails (1 error(s) listed above that "
                         "this put did not refuse)\n")
    assert run(kb, "validate") == 1


def test_a_stale_closed_task_is_told_to_rebind_with_evidence(kb, source_repo, capsys):
    """SPEC §5.2.5: a rebind keeps the record's status and passes that status's closing checks again, so a
    confirmed task's rebind must cite its primary evidence once more. The line the put prints is runnable
    as printed."""
    EVIDENCE = "observed:evidence/2026-09-22-ratio/README.md:row 0"
    add_finding(kb, "3", LINE3)
    install_record(kb, "claim-task", ct(kb))                          # open, bound to F-0001 as it is now
    accept_tree(kb, capsys)
    assert run(kb, "review", "decide", "claim-task-0001", "--status", "confirmed", "--by", "reviewer-b",
               "--reason", "the capture package repeats the measurement",
               "--expect", digest_of(kb, "claim-task-0001"), "--evidence", EVIDENCE) == 0
    assert run(kb, "validate") == 0
    capsys.readouterr()

    assert run(kb, "edit", "F-0001") == 0
    staged = Path(capsys.readouterr().out.strip())
    staged.write_bytes(staged.read_bytes() + b"\nAnother detail.\n")
    assert run(kb, "put", str(staged)) == 0
    assert ("kblam put: claim-task-0001 is now stale (this put changed F-0001, which it is bound to); a reviewer "
            "rechecks it and runs kblam review rebind claim-task-0001 --by NAME --reason TEXT --expect D "
            "--evidence PROVENANCE:PATH:LOCATOR. kblam validate fails until then") in capsys.readouterr().out

    assert run(kb, "review", "rebind", "claim-task-0001", "--by", "reviewer-b", "--reason",
               "re-read the finding; the binding holds again", "--expect", digest_of(kb, "claim-task-0001"),
               "--evidence", EVIDENCE) == 0
    assert capsys.readouterr().out.splitlines()[-1] == \
        (f"kblam review rebind: claim-task-0001 rebound, now confirmed "
         f"(subject digest {digest_of(kb, 'claim-task-0001')[:12]})")


def test_a_stale_open_task_is_told_to_rebind_without_evidence(kb, source_repo, capsys):
    """An open task's rebind re-checks nothing, so its line carries no --evidence."""
    add_finding(kb, "3", LINE3)
    install_record(kb, "claim-task", ct(kb))
    accept_tree(kb, capsys)

    assert run(kb, "edit", "F-0001") == 0
    staged = Path(capsys.readouterr().out.strip())
    staged.write_bytes(staged.read_bytes() + b"\nAnother detail.\n")
    assert run(kb, "put", str(staged)) == 0
    assert ("kblam put: claim-task-0001 is now stale (this put changed F-0001, which it is bound to); a reviewer "
            "rechecks it and runs kblam review rebind claim-task-0001 --by NAME --reason TEXT --expect D. kblam "
            "validate fails until then") in capsys.readouterr().out


# --- validate: pending tasks, warnings, the registry and --forget-missing --------------------------


def test_validate_prints_pending_tasks_without_failing(kb, source_repo, capsys):
    add_finding(kb, "3", LINE3)
    install_record(kb, "claim-task", ct(kb))
    accept_tree(kb, capsys)
    assert run(kb, "validate") == 0
    assert capsys.readouterr().out.splitlines() == [
        "claim-task-0001 open replication of F-0001: Does an independent measurement establish the claim?",
        "kblam validate: OK (1 findings); 1 pending task(s)",
    ]
    assert run(kb, "validate", "--record") == 0
    text = capsys.readouterr().out
    assert text.splitlines()[-1] == \
        "kblam validate: OK (1 findings); 1 pending task(s); recorded .kblam/tree.hash for this tree"


def test_a_range_only_k14_warning_passes_validate(kb, source_repo, capsys):
    """SPEC §12 M6.11 group 3: an excerpt whose cited range overlaps the assertion's lines without
    quoting it is a warning, and validate exits 0."""
    install_record(kb, "source-challenge", sc(source_repo))
    add_finding(kb, "3", "Row 102: bytes 0x3A 0x3B")
    accept_tree(kb, capsys)
    assert run(kb, "validate") == 0
    text = capsys.readouterr().out
    assert re.fullmatch(r"K14 warning findings/calibration/F-0001-ratio\.md:\d+: " + re.escape(RANGE_WARNING),
                        text.splitlines()[0])
    assert text.endswith("kblam validate: OK (1 findings)\n")


def test_validate_record_creates_the_registry_after_a_clone(kb, source_repo, capsys):
    install_record(kb, "source-challenge", sc(source_repo))
    accept_tree(kb, capsys)
    assert registry_ids(kb) is None
    assert run(kb, "validate", "--record") == 0
    assert capsys.readouterr().out.splitlines()[-1] == \
        "kblam validate: OK (0 findings); recorded .kblam/tree.hash for this tree"
    assert registry_ids(kb) == ["source-challenge-0001"]


def test_validate_record_leaves_a_kb_without_records_without_a_registry(kb, capsys):
    assert run(kb, "validate", "--record") == 0
    assert capsys.readouterr().out == \
        "kblam validate: OK (0 findings); recorded .kblam/tree.hash for this tree\n"
    assert registry_ids(kb) is None


def test_forget_missing_drops_the_gone_ids_and_record_alone_does_not(kb, source_repo, capsys):
    """SPEC §12 M6.11 group 8: `review index` and a plain `validate --record` never forget; only
    --forget-missing does, printing each ID, and the drop stands."""
    install_record(kb, "source-challenge", sc(source_repo, rec_id="source-challenge-0001"))
    install_record(kb, "source-challenge", sc(source_repo, rec_id="source-challenge-0002", lines=(2, 2), text=LINE2))
    accept_tree(kb, capsys)
    assert run(kb, "review", "index") == 0                    # a write creates the registry
    assert capsys.readouterr().out == \
        f"kblam review index: wrote {REVIEW}/INDEX.md and .kblam/tree.hash\n"
    assert registry_ids(kb) == ["source-challenge-0001", "source-challenge-0002"]

    (kb.root / CHALLENGES / "source-challenge-0002.yaml").unlink()          # the record is gone (a clone without it)
    assert run(kb, "review", "index") == 0
    assert capsys.readouterr().out == f"kblam review index: wrote {REVIEW}/INDEX.md\n"
    assert registry_ids(kb) == ["source-challenge-0001", "source-challenge-0002"]         # the index never forgets

    assert run(kb, "validate", "--record") == 1
    text = capsys.readouterr().out
    assert text.splitlines() == [
        f"K13 {CHALLENGES}/source-challenge-0002.yaml: source-challenge-0002 is missing from {REVIEW}/; records are never deleted or "
        f"renamed; git's last commit does not hold a file at {CHALLENGES}/source-challenge-0002.yaml, so leave it as it "
        f"is and tell the user",
        "kblam validate: 1 error(s) in research-review/; tree.hash not recorded",
    ]
    assert registry_ids(kb) == ["source-challenge-0001", "source-challenge-0002"]

    assert run(kb, "validate", "--record", "--forget-missing") == 0
    assert capsys.readouterr().out.splitlines() == [
        f"kblam validate --record: forgot source-challenge-0002 (no record in {REVIEW}/)",
        "kblam validate: OK (0 findings); recorded .kblam/tree.hash for this tree",
    ]
    assert registry_ids(kb) == ["source-challenge-0001"]


def test_forgetting_stands_when_the_validation_after_it_fails(kb, source_repo, capsys):
    """The drop is the flag's explicit purpose, so it is written even when the validation fails."""
    install_record(kb, "source-challenge", sc(source_repo, rec_id="source-challenge-0001"))
    accept_tree(kb, capsys)
    assert run(kb, "review", "index") == 0
    assert registry_ids(kb) == ["source-challenge-0001"]
    capsys.readouterr()                                       # the index's own output is not asserted here
    (kb.root / CHALLENGES / "source-challenge-0001.yaml").unlink()
    assert run(kb, "review", "index") == 0                    # the index drops the gone record
    kb.add("F-0002", "history", "The first curve type was superseded by the second.")   # K4: still an error
    assert registry_ids(kb) == ["source-challenge-0001"]                    # the index never forgets (SPEC group 8)

    assert run(kb, "validate", "--record", "--forget-missing") == 1
    lines = capsys.readouterr().out.splitlines()
    assert f"kblam validate --record: forgot source-challenge-0001 (no record in {REVIEW}/)" in lines
    assert any(line.startswith("K4 findings/calibration/F-0002-history.md:") for line in lines)
    assert lines[-1] == "kblam validate: 1 error(s) in findings/; tree.hash not recorded"
    assert registry_ids(kb) == []


def test_the_error_count_names_each_root_that_holds_an_error(kb, source_repo, capsys):
    """validate's count names findings/ for a finding's error, the review root for a record's, and both
    when both hold one."""
    install_record(kb, "source-challenge", sc(source_repo, rec_id="source-challenge-0001"))
    accept_tree(kb, capsys)
    assert run(kb, "review", "index") == 0                    # a write creates the registry
    capsys.readouterr()
    (kb.root / CHALLENGES / "source-challenge-0001.yaml").unlink()          # K13: a registered record is missing
    assert run(kb, "validate") == 1
    assert capsys.readouterr().out.splitlines()[-1] == f"kblam validate: 2 error(s) in {REVIEW}/"
    kb.add("F-0002", "history", "The first curve type was superseded by the second.")   # K4: a finding's
    assert run(kb, "validate") == 1
    assert capsys.readouterr().out.splitlines()[-1] == f"kblam validate: 3 error(s) in findings/ and {REVIEW}/"


def test_forget_missing_without_record_is_a_usage_error(kb, capsys):
    assert run(kb, "validate", "--forget-missing") == 2
    assert capsys.readouterr().err == "kblam validate: --forget-missing needs --record\n"
    assert registry_ids(kb) is None


def test_forget_missing_without_record_reads_no_config(tmp_path, capsys):
    """The flag combination is a usage error before load_config: it holds with no kblam.toml either."""
    assert main(["--root", str(tmp_path), "validate", "--forget-missing"]) == 2
    assert capsys.readouterr().err == "kblam validate: --forget-missing needs --record\n"


# --- usage errors and the lock --------------------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["challenge", "new", TRACE, "--lines", "3", "--by", "reviewer-a"],
    ["challenge", "new", TRACE, "--lines", "0-2", "--by", "reviewer-a"],
    ["challenge", "new", TRACE, "--lines", "4-3", "--by", "reviewer-a"],
    ["challenge", "new", TRACE, "--lines", "３-３", "--by", "reviewer-a"],   # not decimal digits
    ["task", "new", "F-0001", "--kind", "repeat", "--by", "reviewer-a", "--proponent", "researcher-a"],
    ["use", "review", "source-challenge-0001", "F-0001", "first", "--by", "reviewer-a", "--proponent", "researcher-a"],
    ["use", "edit", "checked-use-0001"],                               # there is no use edit
    ["review", "move", "checked-use-0001"],                            # there is no review move
])
def test_bad_arguments_are_usage_errors(kb, argv, capsys):
    with pytest.raises(SystemExit) as caught:
        run(kb, *argv)
    assert caught.value.code == 2
    assert capsys.readouterr().err.startswith("usage: kblam")


def test_a_locked_kb_reports_a_timeout(kb, capsys):
    set_lock_config(kb, lock_wait_seconds=0)
    hold_lock(kb, pid=os.getpid())
    assert run(kb, "review", "index") == 3
    assert capsys.readouterr().err.startswith(
        "kblam review index: timed out after 0s waiting for .kblam/lock")


# --- the whole workflow through the CLI -----------------------------------------------------------


def test_the_full_cli_round_trip(kb, source_repo, capsys):
    """challenge new → put → decide confirmed → use review → put → decide approved → task new → put →
    decide confirmed: every §5.2.5 command, then a clean validate and three current records."""
    add_finding(kb, "3", LINE3)                               # F-0001 quotes line 3 of the trace
    accept_tree(kb, capsys)

    staged = staged_sc(kb, capsys, source_repo)
    assert run(kb, "put", str(staged)) == 0
    assert capsys.readouterr().out == "kblam put: source-challenge-0001 -> research-review/challenges/source-challenge-0001.yaml\n"

    assert run(kb, "challenge", "uses", "source-challenge-0001") == 0        # open: nothing relates to it yet
    assert capsys.readouterr().out == "source-challenge-0001 is open; only a confirmed challenge affects findings\n"

    assert run(kb, "review", "decide", "source-challenge-0001", "--status", "confirmed", "--by", "reviewer-b",
               "--reason", "read the source and pinned it",
               "--expect", digest_of(kb, "source-challenge-0001")) == 0
    text = capsys.readouterr().out
    assert f"kblam review decide: source-challenge-0001 is now confirmed " \
           f"(subject digest {digest_of(kb, 'source-challenge-0001')[:12]})\n" in text
    assert "kblam review decide: source-challenge-0001 now affects F-0001; run kblam challenge uses source-challenge-0001 for each " \
           "excerpt and the command that fixes it\n" in text
    assert text.endswith("kblam review decide: done, but kblam validate still fails (1 error(s) listed "
                         "above, owned by other findings or records)\n")

    assert run(kb, "challenge", "uses", "source-challenge-0001") == 0
    assert capsys.readouterr().out == \
        ("F-0001 findings/calibration/F-0001-ratio.md:15 excerpt 1 same: error; "
         "kblam use review source-challenge-0001 F-0001 1 --by NAME --proponent NAME\n")

    use_staged = stage(kb, capsys, "use", "review", "source-challenge-0001", "F-0001", "1", "--by", "reviewer-a",
                       "--proponent", "researcher-a")
    assert use_staged == kb.root / STAGING / "checked-use-0001.yaml"
    fill(use_staged, **cu_fields())
    assert run(kb, "put", str(use_staged)) == 0                # an open use does not cover the excerpt
    assert capsys.readouterr().out.endswith(
        "kblam put: done, but kblam validate still fails (1 error(s) listed above, owned by other "
        "findings or records)\n")

    assert run(kb, "review", "decide", "checked-use-0001", "--status", "approved", "--by", "reviewer-b",
               "--reason", "the excerpt really is used only for the printed bytes",
               "--expect", digest_of(kb, "checked-use-0001")) == 0
    assert capsys.readouterr().out == \
        (f"kblam review decide: checked-use-0001 is now approved "
         f"(subject digest {digest_of(kb, 'checked-use-0001')[:12]})\n")

    task_staged = stage(kb, capsys, "task", "new", "F-0001", "--kind", "replication",
                        "--by", "reviewer-a", "--proponent", "researcher-a")
    fill(task_staged, **ct_fields())
    assert run(kb, "put", str(task_staged)) == 0
    assert capsys.readouterr().out == "kblam put: claim-task-0001 -> research-review/tasks/claim-task-0001.yaml\n"

    assert run(kb, "validate") == 0                            # the open task is pending, not a failure
    assert capsys.readouterr().out.splitlines() == [
        "claim-task-0001 open replication of F-0001: Does an independent measurement establish the claim?",
        "kblam validate: OK (1 findings); 1 pending task(s)",
    ]

    assert run(kb, "review", "decide", "claim-task-0001", "--status", "confirmed", "--by", "reviewer-b",
               "--reason", "the capture package repeats the measurement",
               "--expect", digest_of(kb, "claim-task-0001"),
               "--evidence", "observed:evidence/2026-09-22-ratio/README.md:row 0") == 0
    assert capsys.readouterr().out == \
        (f"kblam review decide: claim-task-0001 is now confirmed "
         f"(subject digest {digest_of(kb, 'claim-task-0001')[:12]})\n")
    assert run(kb, "validate") == 0
    assert capsys.readouterr().out == "kblam validate: OK (1 findings)\n"

    assert run(kb, "review", "list") == 0
    assert capsys.readouterr().out.splitlines() == [
        f"source-challenge-0001 challenge confirmed {digest_of(kb, 'source-challenge-0001')[:12]} {TRACE}:3-3 current",
        f"claim-task-0001 task confirmed {digest_of(kb, 'claim-task-0001')[:12]} replication of F-0001 current",
        f"checked-use-0001 use approved {digest_of(kb, 'checked-use-0001')[:12]} source-challenge-0001 in F-0001 excerpt 1 current",
    ]
