"""SPEC §12 M6.11 group 11: exit status, Stop and review-path enforcement."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

import m611_helpers as m
from conftest import record_data
from kblam import approval, records
from kblam.finding import yaml_rt
from test_check import E1, N, jkb  # noqa: F401 (fixture)

frozen_today = m.frozen_today

U23K_REVIEW = "research-review"
U23K_FINDING = "findings/calibration/F-0001-ratio.md"
U23K_SC = "research-review/challenges/source-challenge-0001.yaml"
U23K_CT = "research-review/tasks/claim-task-0001.yaml"
U23K_INDEX = "research-review/INDEX.md"
U23K_QUESTION = "Does an independent measurement establish the claim?"
U23K_POINTER = "Load the kblam-write skill for how to fix this."
U23K_REVIEW_TAIL = (
    "Review records are written only by kblam: stage one with kblam challenge new, kblam task new "
    "or kblam use review (or kblam challenge/task edit), edit it under .kblam/review-staging/, "
    f"then kblam put it. {U23K_POINTER}"
)
U23K_STATE_TAIL = (
    ".kblam/ holds kblam's own state and only kblam writes it; stage findings under .kblam/staging/ "
    f"(kblam new, kblam edit). {U23K_POINTER}"
)


@contextmanager
def u23k_changes(kb, source_repo, expected):
    before, source = m.tree(kb), source_repo.snapshot()
    try:
        yield
    finally:
        assert m.changed(before, m.tree(kb)) == set(expected)
        assert source_repo.snapshot() == source


def u23k_run(kb, source_repo, argv, *, paths=(), code=0, out="", err=""):
    with u23k_changes(kb, source_repo, paths):
        run = m.kblam(kb, *argv)
        assert run == m.Run(code, out, err)
    return run


def u23k_fill(path, fields):
    data = yaml_rt().load(path.read_text(encoding="utf-8"))
    data.update(fields)
    path.write_bytes(records.dump(data))


def u23k_challenge(kb, source_repo, *, narrow=False):
    staged = kb.root / ".kblam/review-staging/source-challenge-0001.yaml"
    u23k_run(kb, source_repo,
             ("challenge", "new", m.TRACE, "--lines", "3-3", "--by", "reviewer-a"),
             paths={".kblam/review-staging/source-challenge-0001.yaml", ".kblam/review-receipts/source-challenge-0001.json"},
             out=f"{staged}\n")
    defaults = record_data("source-challenge")
    fields = {key: defaults[key] for key in
              ("proposition", "scope", "classification", "basis", "usable", "limits")}
    fields["basis"][0]["sha256"] = None
    u23k_fill(staged, fields)
    if narrow:
        data = yaml_rt().load(staged.read_text(encoding="utf-8"))
        data["source"]["assertion"].update(text="the two bytes are equal", sha256=None, occurrence=None)
        staged.write_bytes(records.dump(data))
    u23k_run(kb, source_repo, ("put", staged),
             paths={U23K_SC, U23K_INDEX, ".kblam/review-ids", ".kblam/tree.hash",
                    ".kblam/review-staging/source-challenge-0001.yaml"},
             out=f"kblam put: source-challenge-0001 -> {U23K_SC}\n")


def u23k_confirm(kb, source_repo, *, issues="", affected=False):
    digest = m.expect(kb, "source-challenge-0001")
    text = ("" if affected else issues)
    text += f"kblam review decide: source-challenge-0001 is now confirmed (subject digest {digest[:12]})\n"
    if affected:
        text += ("kblam review decide: source-challenge-0001 now affects F-0001; run kblam challenge uses source-challenge-0001 "
                 "for each excerpt and the command that fixes it\n")
    if affected:
        text += issues
    if affected:
        text += ("kblam review decide: done, but kblam validate still fails (1 error(s) listed above, "
                 "owned by other findings or records)\n")
    u23k_run(kb, source_repo,
             ("review", "decide", "source-challenge-0001", "--status", "confirmed", "--by", "reviewer-b",
              "--reason", "read the printed byte values independently", "--expect", digest),
             paths={U23K_SC, U23K_INDEX, ".kblam/tree.hash"}, out=text)


def u23k_task(kb, source_repo):
    staged = kb.root / ".kblam/review-staging/claim-task-0001.yaml"
    u23k_run(kb, source_repo,
             ("task", "new", "F-0001", "--kind", "replication", "--by", "researcher-a",
              "--proponent", "researcher-a"),
             paths={".kblam/review-staging/claim-task-0001.yaml", ".kblam/review-receipts/claim-task-0001.json"},
             out=f"{staged}\n")
    defaults = record_data("claim-task")
    u23k_fill(staged, {key: defaults[key] for key in
                      ("question", "method", "outcomes", "controls", "stop", "expected_evidence")})
    u23k_run(kb, source_repo, ("put", staged),
             paths={U23K_CT, U23K_INDEX, ".kblam/review-ids", ".kblam/tree.hash",
                    ".kblam/review-staging/claim-task-0001.yaml"},
             out=f"kblam put: claim-task-0001 -> {U23K_CT}\n")


def u23k_hook(kb, source_repo, monkeypatch, event, payload, *, out="", paths=()):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(kb.root), **payload})))
    return u23k_run(kb, source_repo, ("hook", event), paths=paths, out=out)


def u23k_warning(path=U23K_FINDING):
    return (f"K14 warning {path}:15: the cited range {m.TRACE}:3-3 overlaps lines 3-3 of "
            "source-challenge-0001's assertion without quoting it; check that the excerpt does not rely on the "
            "challenged text (kblam challenge uses source-challenge-0001 lists what source-challenge-0001 affects)\n")


def u23k_k14(source_repo):
    return (f"K14 {U23K_FINDING}:15: source-challenge-0001 challenges this quoted assertion at "
            f"{m.TRACE}@{source_repo.blob(m.TRACE_PATH)[:12]}:3-3; edit the finding or have this use "
            "reviewed (kblam use review source-challenge-0001 F-0001 1 --by NAME --proponent NAME). "
            "K10 is checked separately.\n")


def test_warnings_only_validate_finding_put_and_stop_exit_zero(kb, source_repo, monkeypatch):
    """Acceptance 4, 6: F-0001 quotes raw row-102 bytes, not the challenged equality.

    Start: no records, committed source. challenge new --by reviewer-a (0, staged path):
    {.kblam/review-staging/source-challenge-0001.yaml, .kblam/review-receipts/source-challenge-0001.json}; put SC (0,
    installed path): {research-review/challenges/source-challenge-0001.yaml, research-review/INDEX.md,
    .kblam/review-ids, .kblam/tree.hash, .kblam/review-staging/source-challenge-0001.yaml}; decide confirmed
    --by reviewer-b (0, confirmed + K14 range warning): {research-review/challenges/source-challenge-0001.yaml,
    research-review/INDEX.md, .kblam/tree.hash}. validate (0, K14 warning and OK): none.
    edit F-0001 (no --by, 0, staged path): {.kblam/staging/F-0001-ratio.md,
    .kblam/staging/F-0001.edit-base.json}; finding put (no --by, 0, installed path + K14 warning + disabled Jev):
    {findings/calibration/F-0001-ratio.md, .kblam/staging/F-0001-ratio.md, .kblam/tree.hash,
    .kblam/staging/F-0001.edit-base.json, .kblam/pairs.sqlite, .kblam/review.jsonl,
    .kblam/checks.jsonl}. validate afterwards (0,
    same K14 warning and OK): none. An out-of-band body edit forces Stop's validating path;
    hook Stop (no --by, 0, silent): {.kblam/checks.jsonl}; validate afterwards
    (0, warning and OK): none.
    Every CLI call preserves the source's working bytes, HEAD, index and refs.
    """
    kb.add("F-0001", "ratio", m.CLAIM,
           body=m.verbatim(f"{m.TRACE}:3-3", "Row 102: bytes 0x3A 0x3B"))
    m.accept_tree(kb)
    u23k_challenge(kb, source_repo, narrow=True)
    warning = u23k_warning()
    u23k_confirm(kb, source_repo, issues=warning)
    expected = warning + "kblam validate: OK (1 findings)\n"
    u23k_run(kb, source_repo, ("validate",), out=expected)
    staged = kb.root / ".kblam/staging/F-0001-ratio.md"
    u23k_run(kb, source_repo, ("edit", "F-0001"),
             paths={".kblam/staging/F-0001-ratio.md", ".kblam/staging/F-0001.edit-base.json"}, out=f"{staged}\n")
    staged.write_bytes(staged.read_bytes() + b"\nMore detail about the raw printed bytes.\n")
    u23k_run(kb, source_repo, ("put", staged),
             paths={U23K_FINDING, ".kblam/staging/F-0001-ratio.md", ".kblam/tree.hash",
                    ".kblam/staging/F-0001.edit-base.json", ".kblam/pairs.sqlite",
                    ".kblam/review.jsonl", ".kblam/checks.jsonl"},
             out=(u23k_warning(".kblam/staging/F-0001-ratio.md")
                  + f"kblam put: F-0001 -> {U23K_FINDING}\n"),
             err="kblam put: [jev.thresholds] enables no Jev verdict, so Jev was not asked "
                 "(quantities were compared)\n")
    u23k_run(kb, source_repo, ("validate",), out=expected)
    finding = kb.root / U23K_FINDING
    finding.write_bytes(finding.read_bytes() + b"\nAnother raw-byte detail.\n")
    stale_hash = (kb.root / ".kblam/tree.hash").read_bytes()
    u23k_hook(kb, source_repo, monkeypatch, "Stop", {"stop_hook_active": False},
               paths={".kblam/checks.jsonl"})
    assert (kb.root / ".kblam/tree.hash").read_bytes() == stale_hash
    u23k_run(kb, source_repo, ("validate",), out=expected)


def test_pending_only_validate_record_and_finding_put_and_stop_exit_zero(kb, source_repo, monkeypatch):
    """Acceptance 4, 6: a well-formed open claim-task-0001 binds F-0001, source committed.

    task new --by researcher-a --proponent researcher-a (0, path):
    {.kblam/review-staging/claim-task-0001.yaml, .kblam/review-receipts/claim-task-0001.json}; put CT (0,
    installed path): {research-review/tasks/claim-task-0001.yaml, research-review/INDEX.md,
    .kblam/review-ids, .kblam/tree.hash, .kblam/review-staging/claim-task-0001.yaml}.
    validate (no --by, 0, claim-task-0001 open replication and OK; 1 pending task): none.
    new calibration 'Independent tray contact' (no --by, 0, staged path):
    {.kblam/staging/F-0002-independent-tray-contact.md}; put finding (no --by, 0,
    installed path + disabled Jev): {findings/calibration/F-0002-independent-tray-contact.md,
    findings/INDEX.md, .kblam/staging/F-0002-independent-tray-contact.md, .kblam/tree.hash,
    .kblam/pairs.sqlite, .kblam/review.jsonl, .kblam/checks.jsonl}; validate afterwards
    (0, pending CT and OK with 2 findings): none. An out-of-band body-only edit of F-0002
    forces hook Stop to validate (no --by, 0, silent): {.kblam/checks.jsonl}, checking
    the previously unchecked F-0001; validate afterwards (0, pending CT and OK): none. All calls preserve the committed source and Git state.
    """
    kb.add("F-0001", "ratio", m.CLAIM)
    m.accept_tree(kb)
    u23k_task(kb, source_repo)
    pending = f"claim-task-0001 open replication of F-0001: {U23K_QUESTION}\n"
    u23k_run(kb, source_repo, ("validate",),
             out=pending + "kblam validate: OK (1 findings); 1 pending task(s)\n")
    staged = kb.root / ".kblam/staging/F-0002-independent-tray-contact.md"
    u23k_run(kb, source_repo, ("new", "calibration", "Independent tray contact"),
             paths={staged.relative_to(kb.root).as_posix()}, out=f"{staged}\n")
    staged.write_bytes(m.finding_text("F-0002", "The tray contact closes when paper is loaded.",
                                    title="Independent tray contact").encode())
    installed = "findings/calibration/F-0002-independent-tray-contact.md"
    u23k_run(kb, source_repo, ("put", staged),
             paths={installed, "findings/INDEX.md", staged.relative_to(kb.root).as_posix(),
                    ".kblam/tree.hash", ".kblam/pairs.sqlite", ".kblam/review.jsonl",
                    ".kblam/checks.jsonl"},
             out=f"kblam put: F-0002 -> {installed}\n",
             err="kblam put: [jev.thresholds] enables no Jev verdict, so Jev was not asked "
                 "(quantities were compared)\n")
    expected = pending + "kblam validate: OK (2 findings); 1 pending task(s)\n"
    u23k_run(kb, source_repo, ("validate",), out=expected)
    finding = kb.root / installed
    finding.write_bytes(finding.read_bytes() + b"\nMore detail about the contact.\n")
    stale_hash = (kb.root / ".kblam/tree.hash").read_bytes()
    u23k_hook(kb, source_repo, monkeypatch, "Stop", {"stop_hook_active": False},
               paths={".kblam/checks.jsonl"})
    assert (kb.root / ".kblam/tree.hash").read_bytes() == stale_hash
    u23k_run(kb, source_repo, ("validate",), out=expected)


def test_matching_tree_hash_after_confirmation_leaves_stop_silent_while_validate_fails(
        kb, source_repo, monkeypatch):
    """Acceptance 4, 6: installed F-0001 quotes line 3 of the committed source, no use.

    challenge new --by reviewer-a (0, path): {.kblam/review-staging/source-challenge-0001.yaml,
    .kblam/review-receipts/source-challenge-0001.json}; put open SC (0, installed path):
    {research-review/challenges/source-challenge-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash, .kblam/review-staging/source-challenge-0001.yaml}; decide confirmed --by reviewer-b
    (0, confirmed, now affects F-0001, K14 quoted assertion, validate still fails):
    {research-review/challenges/source-challenge-0001.yaml, research-review/INDEX.md, .kblam/tree.hash}.
    hook Stop and SubagentStop (no --by, 0, silent): each none, since kblam recorded the tree.
    validate afterwards (no --by, 1, K14 challenges this quoted assertion and 1 error): none.
    Every call preserves the source's bytes, HEAD, index and refs.
    """
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")
    m.accept_tree(kb)
    u23k_challenge(kb, source_repo)
    issue = u23k_k14(source_repo)
    u23k_confirm(kb, source_repo, issues=issue, affected=True)
    for event in ("Stop", "SubagentStop"):
        u23k_hook(kb, source_repo, monkeypatch, event, {"stop_hook_active": False})
    u23k_run(kb, source_repo, ("validate",), code=1,
             out=issue + "kblam validate: 1 error(s) in findings/\n")


def u23k_pre_commit(kb):
    asset = Path(__file__).resolve().parents[1] / "src/kblam/assets/pre-commit"
    env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    return subprocess.run(["sh", str(asset)], cwd=kb.root, env=env, capture_output=True,
                          text=True, timeout=30, check=False)


def test_source_change_outside_both_roots_fails_validate_and_pre_commit_but_not_stop(
        kb, source_repo, monkeypatch):
    """Acceptance 4, 6: confirmed source-challenge-0001 at pinned line 3, F-0001 quotes unaffected line 2.

    challenge new --by reviewer-a (0, path): {.kblam/review-staging/source-challenge-0001.yaml,
    .kblam/review-receipts/source-challenge-0001.json}; put open SC (0, installed path):
    {research-review/challenges/source-challenge-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash, .kblam/review-staging/source-challenge-0001.yaml}; decide confirmed --by reviewer-b
    (0, confirmed): {research-review/challenges/source-challenge-0001.yaml, research-review/INDEX.md,
    .kblam/tree.hash}; validate before source change (no --by, 0, OK): none;
    real pre-commit before source change (no --by, 0, the untracked-evidence warning for
    evidence/ only, since the trace lies in the nested source repository, and OK, empty stderr): none.
    Fixture changes source line 2 outside both roots; hook Stop (no --by, 0, silent): none.
    validate (no --by, 1, K10 excerpt does not occur and 1 error): none; real pre-commit
    script (no --by, 1, same K10 plus warnings; stderr commit refused, validate exit 1 and skill pointer):
    none; validate afterwards (1, identical K10 error): none. All commands preserve the source
    as it stood immediately before them, including the deliberately dirty bytes and Git state.
    """
    if shutil.which("sh") is None:
        pytest.skip("the POSIX pre-commit script requires sh")
    subprocess.run(["git", "init", "-q", str(kb.root)], check=True, capture_output=True)
    m.quoting_finding(kb, "F-0001", source_repo, "2-2")
    m.accept_tree(kb)
    u23k_challenge(kb, source_repo)
    u23k_confirm(kb, source_repo)
    subprocess.run(["git", "add", "kblam.toml", "findings", "research-review"], cwd=kb.root,
                   check=True, capture_output=True)
    approval.record_approval(kb.cfg, (kb.root / "kblam.toml").read_bytes())
    warnings = (
        "kblam validate: warning: evidence/2026-09-22-ratio is not tracked by git, so F-0001, which "
        "cites it as evidence, passes K2 here and fails on every clone. Commit it "
        "(git add evidence/2026-09-22-ratio), or cite a file that is committed\n")
    # Only that one: the verbatim source lies in the nested source repository under an evidence root,
    # which holds it, so it gets no untracked warning (the pre-commit output below is compared whole).
    u23k_run(kb, source_repo, ("validate",), out="kblam validate: OK (1 findings)\n")
    with u23k_changes(kb, source_repo, set()):
        done = u23k_pre_commit(kb)
        assert (done.returncode, done.stdout, done.stderr) == (
            0, warnings + "kblam validate: OK (1 findings)\n", "")
    source_repo.write(m.TRACE_PATH, m.TRACE_TEXT.replace(m.LINE2, "Row 101: bytes 0xAA 0xBB"))
    u23k_hook(kb, source_repo, monkeypatch, "Stop", {"stop_hook_active": False})
    expected = (f"K10 {U23K_FINDING}:15: the excerpt does not occur verbatim in {m.TRACE}:2-2; "
                "copy the text exactly from the source (no paraphrase, no reflowing)\n"
                "kblam validate: 1 error(s) in findings/\n")
    u23k_run(kb, source_repo, ("validate",), code=1, out=expected)
    with u23k_changes(kb, source_repo, set()):
        done = u23k_pre_commit(kb)
        assert (done.returncode, done.stdout, done.stderr) == (
            1, expected.replace("kblam validate: 1 error(s)", warnings + "kblam validate: 1 error(s)"),
            f"kblam pre-commit: commit refused (kblam validate exit 1). {U23K_POINTER}\n")
    u23k_run(kb, source_repo, ("validate",), code=1, out=expected)


def test_review_index_and_validate_are_byte_identical_across_runs(kb, source_repo):
    """Acceptance 4, 6: open source-challenge-0001 with committed source and open claim-task-0001 bound to F-0001.

    challenge new --by reviewer-a (0, path): {.kblam/review-staging/source-challenge-0001.yaml,
    .kblam/review-receipts/source-challenge-0001.json}; put SC (0, installed path):
    {research-review/challenges/source-challenge-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash, .kblam/review-staging/source-challenge-0001.yaml}; task new --by researcher-a
    --proponent researcher-a (0, path): {.kblam/review-staging/claim-task-0001.yaml,
    .kblam/review-receipts/claim-task-0001.json}; put CT (0, installed path):
    {research-review/tasks/claim-task-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash, .kblam/review-staging/claim-task-0001.yaml}. Three review index calls (no --by,
    0, generated path): each none, including tree.hash; index bytes equal a fixed literal.
    Three validate calls afterwards (no --by, 0, pending CT and OK): each none, identical UTF-8
    stdout and empty stderr. All calls preserve committed source bytes and Git state.
    """
    kb.add("F-0001", "ratio", m.CLAIM)
    m.accept_tree(kb)
    u23k_challenge(kb, source_repo)
    u23k_task(kb, source_repo)
    expected_index = (
        "# Review index (generated by `kblam review index`; do not edit by hand)\n\n"
        "## Challenges\n\n"
        f"### {m.TRACE}\n\n"
        "| ID | Lines | Classification | Status | Findings |\n"
        "|---|---|---|---|---|\n"
        "| source-challenge-0001 | 3-3 | contradicted | open |  |\n\n"
        "## Tasks\n\n"
        "| ID | Finding | Kind | Status | Question |\n"
        "|---|---|---|---|---|\n"
        f"| claim-task-0001 | F-0001 | replication | open | {U23K_QUESTION} |\n\n"
        "## Uses\n\nNone.\n"
    ).encode()
    expected = (f"claim-task-0001 open replication of F-0001: {U23K_QUESTION}\n"
                "kblam validate: OK (1 findings); 1 pending task(s)\n")
    outputs = []
    for _ in range(3):
        u23k_run(kb, source_repo, ("review", "index"),
                 out=f"kblam review index: wrote {U23K_INDEX} and .kblam/tree.hash\n")
        assert (kb.root / U23K_INDEX).read_bytes() == expected_index
        outputs.append(u23k_run(kb, source_repo, ("validate",), out=expected).out.encode())
    assert outputs == [expected.encode()] * 3


@pytest.fixture
def u23k_jev_kb(request):
    return request.getfixturevalue("jkb")


def test_finding_jev_rejection_keeps_exit_four_and_roots_unchanged(u23k_jev_kb, source_repo):
    """Acceptance 4, 6: no review records; F-0001 states E1; committed source; fake Jev same_fact.

    new calibration 'Warm up drift' (no --by, 0, staged path):
    {.kblam/staging/F-0002-warm-up-drift.md}; put finding (no --by, 4, rejected R-443913e9
    same_fact F-0002 vs F-0001 with p .93/confidence .91, findings unchanged, skill pointer):
    {.kblam/pairs.sqlite, .kblam/review.jsonl, .kblam/checks.jsonl, .kblam/calls.jsonl}.
    Neither root, registry,
    staged finding nor tree.hash changes. validate afterwards (no --by, 0, OK with 1 finding):
    none; rejected items do not fail validate. Every command preserves source bytes and Git state.
    """
    kb = u23k_jev_kb
    kb.add("F-0001", "motor", E1)
    m.accept_tree(kb)
    kb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    staged = kb.root / ".kblam/staging/F-0002-warm-up-drift.md"
    u23k_run(kb, source_repo, ("new", "calibration", "Warm up drift"),
             paths={staged.relative_to(kb.root).as_posix()}, out=f"{staged}\n")
    staged.write_bytes(m.finding_text("F-0002", N, title="Warm up drift").encode())
    u23k_run(kb, source_repo, ("put", staged), code=4,
             paths={".kblam/pairs.sqlite", ".kblam/review.jsonl", ".kblam/checks.jsonl",
                    ".kblam/calls.jsonl"},
             out=('rejected R-443913e9 same_fact F-0002 vs F-0001 (p 0.93, confidence 0.91): '
                  'F-0001 already states this; edit F-0001 instead (kblam edit F-0001). The put was '
                  'refused: send R-443913e9 to the librarian, which resolves it only when the two '
                  'findings state distinct facts (kblam resolve R-443913e9 --distinct "<reason>")\n'
                  'kblam put: rejected F-0002 by the Jev check (1 reject verdict(s)); findings/ is '
                  f'unchanged. Fix every verdict above and put it again. {U23K_POINTER}\n'))
    u23k_run(kb, source_repo, ("validate",), out="kblam validate: OK (1 findings)\n")


@pytest.mark.parametrize("tool,key", [("Write", "file_path"), ("Edit", "file_path"),
                                     ("NotebookEdit", "notebook_path")])
@pytest.mark.parametrize("root,target,tail", [
    (U23K_REVIEW, "research-review/challenges/source-challenge-0001.yaml", U23K_REVIEW_TAIL),
    (".kblam", ".kblam/review-receipts/source-challenge-0001.json", U23K_STATE_TAIL),
])
def test_file_hooks_deny_review_and_receipt_writes(kb, source_repo, monkeypatch, tool, key,
                                                root, target, tail):
    """Acceptance 6: empty review root, no records, committed source and valid KB.

    hook PreToolUse (no --by) for Write, Edit or NotebookEdit to a review record or receipt:
    exit 0; exact deny JSON names the tool, target and protected root, gives staging instructions
    and skill pointer; changed files: none per call. validate afterwards (0, OK 0 findings): none.
    Source working bytes, HEAD, index and refs unchanged around both calls.
    """
    reason = f"kblam: {tool} of {target} under {root}/ denied. {tail}"
    answer = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": reason}}
    u23k_hook(kb, source_repo, monkeypatch, "PreToolUse",
               {"tool_name": tool, "tool_input": {key: target}}, out=json.dumps(answer) + "\n")
    u23k_run(kb, source_repo, ("validate",), out="kblam validate: OK (0 findings)\n")


@pytest.mark.parametrize("tool,command,root,what,tail", [
    ("Bash", "echo x > research-review/INDEX.md", U23K_REVIEW,
     "writing research-review/INDEX.md", U23K_REVIEW_TAIL),
    ("PowerShell", "Set-Content research-review/INDEX.md x", U23K_REVIEW,
     "writing research-review/INDEX.md", U23K_REVIEW_TAIL),
    ("Bash", "rm research-review/challenges/source-challenge-0001.yaml", U23K_REVIEW,
     "removing research-review/challenges/source-challenge-0001.yaml", U23K_REVIEW_TAIL),
    ("PowerShell", "Remove-Item research-review/tasks/claim-task-0001.yaml", U23K_REVIEW,
     "removing research-review/tasks/claim-task-0001.yaml", U23K_REVIEW_TAIL),
    ("Bash", "rmdir research-review/uses", U23K_REVIEW,
     "removing research-review/uses", U23K_REVIEW_TAIL),
    ("PowerShell", "Remove-Item -Recurse research-review", U23K_REVIEW,
     "removing research-review", U23K_REVIEW_TAIL),
    ("Bash", "echo x > .kblam/review-receipts/source-challenge-0001.json", ".kblam",
     "writing .kblam/review-receipts/source-challenge-0001.json", U23K_STATE_TAIL),
    ("PowerShell", "Set-Content .kblam/review-receipts/source-challenge-0001.json x", ".kblam",
     "writing .kblam/review-receipts/source-challenge-0001.json", U23K_STATE_TAIL),
    ("Bash", "rm .kblam/review-receipts/source-challenge-0001.json", ".kblam",
     "removing .kblam/review-receipts/source-challenge-0001.json", U23K_STATE_TAIL),
    ("PowerShell", "Remove-Item .kblam/review-receipts/source-challenge-0001.json", ".kblam",
     "removing .kblam/review-receipts/source-challenge-0001.json", U23K_STATE_TAIL),
])
def test_shell_hooks_deny_review_and_receipt_writes_and_removals(
        kb, source_repo, monkeypatch, tool, command, root, what, tail):
    """Acceptance 6: no records, committed source, clean KB; the shell command is not executed.

    hook PreToolUse (no --by, Bash or PowerShell writes/removals): 0, exact deny JSON names
    written/removed targets under research-review/ or .kblam/, staging guidance and skill pointer;
    files changed: none per call. validate afterwards (0, OK with 0 findings): none.
    Record files, kind folders and review root removal are guarded; source and Git state unchanged.
    """
    reason = f"kblam: {what} under {root}/ denied. {tail}"
    answer = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": reason}}
    u23k_hook(kb, source_repo, monkeypatch, "PreToolUse",
               {"tool_name": tool, "tool_input": {"command": command}}, out=json.dumps(answer) + "\n")
    u23k_run(kb, source_repo, ("validate",), out="kblam validate: OK (0 findings)\n")


@pytest.mark.parametrize("tool,tool_input", [
    ("Write", {"file_path": ".kblam/review-staging/source-challenge-0001.yaml"}),
    ("Edit", {"file_path": ".kblam/review-staging/claim-task-0001.yaml"}),
    ("NotebookEdit", {"notebook_path": ".kblam/review-staging/u23k_notes.ipynb"}),
    ("Bash", {"command": "echo x > .kblam/review-staging/source-challenge-0001.yaml"}),
    ("PowerShell", {"command": "Set-Content .kblam/review-staging/claim-task-0001.yaml x"}),
    ("Bash", {"command": "rm .kblam/review-staging/source-challenge-0001.yaml"}),
    ("PowerShell", {"command": "Remove-Item .kblam/review-staging/claim-task-0001.yaml"}),
])
def test_hooks_allow_author_writes_and_removals_under_review_staging(
        kb, source_repo, monkeypatch, tool, tool_input):
    """Acceptance 6: clean KB, no records, committed source; only the requested hook is run.

    hook PreToolUse (no --by) for file tools or shell writes/removals under review-staging:
    exit 0, silent (normal permissions still apply); changed files: none per call.
    validate afterwards (no --by, 0, OK 0 findings): none. Source and Git state unchanged.
    """
    u23k_hook(kb, source_repo, monkeypatch, "PreToolUse",
               {"tool_name": tool, "tool_input": tool_input})
    u23k_run(kb, source_repo, ("validate",), out="kblam validate: OK (0 findings)\n")
