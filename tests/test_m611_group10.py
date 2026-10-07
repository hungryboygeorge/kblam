"""SPEC §12 M6.11 group 10: concurrency and interrupted writes (Acceptance 6)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import m611_helpers as m
from conftest import KB, SourceRepo
from kblam import lock, records, store, writes
from kblam.finding import yaml_rt
from kblam.index import generate_index
from kblam.review_index import generate_review_index
from kblam.view import load_view

frozen_today = m.frozen_today

REVIEW = "research-review/INDEX.md"
IDS = ".kblam/review-ids"
HASH = ".kblam/tree.hash"
JOURNAL = ".kblam/journal.json"
SC = "research-review/challenges/SC-0001.yaml"
CT = "research-review/tasks/CT-0001.yaml"
STAGED = ".kblam/review-staging/SC-0001.yaml"
QUESTION = "Does an independent measurement establish the claim?"


@contextlib.contextmanager
def changes(kb, source_repo, expected):
    before, source = m.tree(kb), source_repo.snapshot()
    try:
        yield
    finally:
        assert m.changed(before, m.tree(kb)) == expected
        assert source_repo.snapshot() == source


def run(kb, source_repo, argv, expected, *, code=0, out="", err=""):
    with changes(kb, source_repo, expected):
        result = m.kblam(kb, *argv)
    assert result == m.Run(code, out, err)
    return result


def data(path):
    return yaml_rt().load(path.read_bytes().decode("utf-8"))


def fill(path, **fields):
    value = data(path)
    value.update(fields)
    path.write_bytes(records.dump(value))


def stage_challenge(kb, source_repo, number=1):
    rec_id = f"SC-{number:04d}"
    staged = kb.root / f".kblam/review-staging/{rec_id}.yaml"
    run(kb, source_repo,
        ["challenge", "new", m.TRACE, "--lines", "3-3", "--by", "reviewer-a"],
        {staged.relative_to(kb.root).as_posix(), f".kblam/review-receipts/{rec_id}.json"},
        out=f"{staged}\n")
    fill(staged,
         proposition="The printed byte equality follows from the printed byte values",
         scope=["MX-100 capture transcription"], classification="contradicted",
         basis=[{"path": m.TRACE, "sha256": None, "repo": None, "commit": None,
                 "blob": None, "snapshot": None, "locator": "row 102: printed byte values",
                 "role": "internal-inconsistency", "provenance": "observed"}],
         usable="The printed byte values may be cited as a report.",
         limits="Do not infer the capture bytes from this row.")
    return staged


def put_challenge(kb, source_repo, number=1):
    staged = stage_challenge(kb, source_repo, number)
    rec_id = f"SC-{number:04d}"
    installed = f"research-review/challenges/{rec_id}.yaml"
    run(kb, source_repo, ["put", staged],
        {installed, REVIEW, IDS, HASH, staged.relative_to(kb.root).as_posix()},
        out=f"kblam put: {rec_id} -> {installed}\n")
    return staged


def validate(kb, source_repo, *, tasks=(), record=False):
    """`kblam validate`, or with record=True `validate --record`: with no tree.hash that is the
    bootstrap, which asks Jev nothing (no review.jsonl) and says so."""
    pending = "".join(f"{rec_id} open replication of F-0001: {question}\n"
                      for rec_id, question in tasks)
    count = 1 if tasks else 0
    summary = f"kblam validate: OK ({count} findings)"
    if tasks:
        summary += f"; {len(tasks)} pending task(s)"
    baseline = record and not (kb.root / HASH).exists()
    if record:
        summary += "; recorded .kblam/tree.hash for this tree"
    if baseline:
        summary += ("\nkblam validate: there was no .kblam/tree.hash (a new clone, or .kblam/ was deleted), so "
                    f"Jev was not asked: {count} finding(s) accepted from the repository as checked at their "
                    "current fingerprints. kblam audit checks them with Jev")
    run(kb, source_repo, ["validate", *(["--record"] if record else [])],
        {HASH, ".kblam/pairs.sqlite"} if baseline else
        {HASH, ".kblam/pairs.sqlite", ".kblam/review.jsonl"} if record else set(),
        out=pending + summary + "\n")


def edit_record(kb, source_repo, rec_id, **fields):
    word = "challenge" if rec_id.startswith("SC-") else "task"
    staged = kb.root / f".kblam/review-staging/{rec_id}.yaml"
    receipt = f".kblam/review-receipts/{rec_id}.edit-base.json"
    run(kb, source_repo, [word, "edit", rec_id],
        {staged.relative_to(kb.root).as_posix(), receipt}, out=f"{staged}\n")
    fill(staged, **fields)
    installed = m.record_path(kb, rec_id).relative_to(kb.root).as_posix()
    changed = {installed, HASH, receipt, staged.relative_to(kb.root).as_posix()}
    if rec_id.startswith("CT-"):
        changed.add(REVIEW)
    run(kb, source_repo, ["put", staged], changed,
        out=f"kblam put: {rec_id} -> {installed}\n")


def show_challenge(kb, source_repo):
    digest = m.expect(kb, "SC-0001")
    source = data(m.record_path(kb, "SC-0001"))["source"]
    version = source["blob"] if source["repo"] else "provisional"
    run(kb, source_repo, ["challenge", "show", "SC-0001"], set(), out=(
        f"SC-0001 open\nsubject digest: {digest}\nsource: {m.TRACE}\n"
        f"version: {version}\nstate: current\nassertion: lines 3-3\n  {m.LINE3}\n"
        "proposition: The printed byte equality follows from the printed byte values\n"
        "scope: MX-100 capture transcription\nclassification: contradicted\nbasis:\n"
        f"  {m.TRACE} current: row 102: printed byte values (internal-inconsistency, observed)\n"
        "usable: The printed byte values may be cited as a report.\n"
        "limits: Do not infer the capture bytes from this row.\nlinked findings: none\ndecisions:\n  none\n"))
    return digest


@pytest.mark.parametrize("command", ["decide", "pin"])
def test_changed_challenge_refuses_inspected_digest_then_fresh_digest_succeeds(kb, source_repo, command):
    """Start without records, clean source (dirty at allocation for pin); SC-0001 put open.

    Acceptance 6. challenge new --by reviewer-a exits 0, path only; changes
    .kblam/review-staging/SC-0001.yaml and .kblam/review-receipts/SC-0001.json.
    First put exits 0, SC-0001 -> canonical path; changes that staged path, the canonical
    research-review/challenges/SC-0001.yaml, research-review/INDEX.md, .kblam/review-ids
    and .kblam/tree.hash. challenge show exits 0, full subject/source report; changes none.
    challenge edit exits 0, staged path; changes {.kblam/review-staging/SC-0001.yaml,
    .kblam/review-receipts/SC-0001.edit-base.json}. Its put exits 0; changes
    {research-review/challenges/SC-0001.yaml, .kblam/review-staging/SC-0001.yaml,
    .kblam/review-receipts/SC-0001.edit-base.json, .kblam/tree.hash}.
    review decide --by reviewer-b or challenge pin (no actor flag) with old --expect
    exits 1, 'changed since you inspected it; show it again'; changes none.
    Retrying with current --expect exits 0, 'is now confirmed' or 'pinned'; decide
    changes {research-review/challenges/SC-0001.yaml, research-review/INDEX.md,
    .kblam/tree.hash}; pin changes {research-review/challenges/SC-0001.yaml,
    .kblam/tree.hash}. validate exits 0, OK (0 findings); changes none.
    Every command leaves source bytes, HEAD, index and refs unchanged; fixture commit
    before pin is outside command windows. No K13/K14/K15 diagnostic remains.
    """
    if command == "pin":
        source_repo.write(m.TRACE_PATH, m.TRACE_TEXT + "A new final line.\n")
    put_challenge(kb, source_repo)
    inspected = show_challenge(kb, source_repo)
    edit_record(kb, source_repo, "SC-0001", proposition="The byte equality needs independent checking")
    if command == "pin":
        source_repo.commit(m.TRACE_PATH, m.TRACE_TEXT + "A new final line.\n")
    argv = (["review", "decide", "SC-0001", "--status", "confirmed", "--by", "reviewer-b",
             "--reason", "Checked the printed values"] if command == "decide"
            else ["challenge", "pin", "SC-0001"])
    prefix = "review decide" if command == "decide" else "challenge pin"
    run(kb, source_repo, [*argv, "--expect", inspected], set(), code=1,
        err=f"kblam {prefix}: SC-0001 changed since you inspected it; show it again: "
            "kblam challenge show SC-0001\n")
    expected = {SC, HASH, REVIEW} if command == "decide" else {SC, HASH}
    with changes(kb, source_repo, expected):
        result = m.kblam(kb, *argv, "--expect", m.expect(kb, "SC-0001"))
    digest = m.expect(kb, "SC-0001")[:12]
    text = "is now confirmed" if command == "decide" else "pinned"
    assert result == m.Run(0, f"kblam {prefix}: SC-0001 {text} (subject digest {digest})\n", "")
    installed = data(m.record_path(kb, "SC-0001"))
    assert installed["status"] == ("confirmed" if command == "decide" else "open")
    assert len(installed["decisions"]) == (1 if command == "decide" else 0)
    assert installed["source"]["commit"] == source_repo.head()
    validate(kb, source_repo)


def test_changed_task_refuses_rebind_digest_then_current_rebind_succeeds(kb, source_repo):
    """Start F-0001 installed, clean committed source, no records; CT-0001 allocated/put open.

    Acceptance 6. task new --by researcher-a --proponent researcher-a exits 0, path;
    changes .kblam/review-staging/CT-0001.yaml and .kblam/review-receipts/CT-0001.json.
    Put exits 0, CT-0001 -> canonical; changes staged path, research-review/tasks/CT-0001.yaml,
    research-review/INDEX.md, .kblam/review-ids and .kblam/tree.hash. task show exits 0,
    current binding and digest; changes none. task edit exits 0, path; changes staged
    .kblam/review-staging/CT-0001.yaml and .kblam/review-receipts/CT-0001.edit-base.json.
    Edit put exits 0; changes {research-review/tasks/CT-0001.yaml,
    .kblam/review-staging/CT-0001.yaml, .kblam/review-receipts/CT-0001.edit-base.json,
    research-review/INDEX.md, .kblam/tree.hash}.
    review rebind --by reviewer-b with inspected --expect exits 1, 'changed since you
    inspected it; show it again'; changes none. Fresh rebind exits 0, 'rebound, now open';
    changes {research-review/tasks/CT-0001.yaml, .kblam/tree.hash}. validate exits 0, revised pending
    question and OK (1 findings); changes none. Source unchanged for every command.
    """
    kb.add("F-0001", "ratio", m.CLAIM)
    staged = kb.root / ".kblam/review-staging/CT-0001.yaml"
    run(kb, source_repo,
        ["task", "new", "F-0001", "--kind", "replication", "--by", "researcher-a",
         "--proponent", "researcher-a"],
        {".kblam/review-staging/CT-0001.yaml", ".kblam/review-receipts/CT-0001.json"},
        out=f"{staged}\n")
    fill(staged, question=QUESTION, method="Repeat the capture with the documented settings.",
         outcomes={"supports": "The ratio is within 0.1%.", "refutes": "The ratio differs by more.",
                   "inconclusive": "The capture is too noisy to tell."},
         controls=["same firmware version"], stop="Stop after three captures.",
         expected_evidence=["an evidence/ capture package"])
    run(kb, source_repo, ["put", staged],
        {CT, REVIEW, IDS, HASH, ".kblam/review-staging/CT-0001.yaml"},
        out=f"kblam put: CT-0001 -> {CT}\n")
    inspected = m.expect(kb, "CT-0001")
    record = data(m.record_path(kb, "CT-0001"))
    run(kb, source_repo, ["task", "show", "CT-0001"], set(), out=(
        f"CT-0001 open\nsubject digest: {inspected}\nkind: replication\nfinding: F-0001\n"
        f"proponent: researcher-a\nbinding: fingerprint {record['claim_fingerprint']}, "
        f"file sha256 {record['base_file_sha256']}\nbinding: current\nquestion: {QUESTION}\n"
        "method: Repeat the capture with the documented settings.\n"
        "outcomes.supports: The ratio is within 0.1%.\noutcomes.refutes: The ratio differs by more.\n"
        "outcomes.inconclusive: The capture is too noisy to tell.\ncontrols: same firmware version\n"
        "stop: Stop after three captures.\nexpected evidence: an evidence/ capture package\n"
        "decisions:\n  none\n"))
    question = "Does the revised method establish the claim?"
    edit_record(kb, source_repo, "CT-0001", question=question)
    argv = ["review", "rebind", "CT-0001", "--by", "reviewer-b", "--reason", "Reread the method"]
    run(kb, source_repo, [*argv, "--expect", inspected], set(), code=1,
        err="kblam review rebind: CT-0001 changed since you inspected it; show it again: "
            "kblam task show CT-0001\n")
    digest = m.expect(kb, "CT-0001")
    run(kb, source_repo, [*argv, "--expect", digest], {CT, HASH},
        out=f"kblam review rebind: CT-0001 rebound, now open (subject digest {digest[:12]})\n")
    decision = data(m.record_path(kb, "CT-0001"))["decisions"][-1]
    assert decision["by"] == "reviewer-b" and decision["bind"] == digest
    validate(kb, source_repo, tasks=[("CT-0001", question)])


def interrupted_put(kb, source_repo, monkeypatch, checkpoint):
    staged = stage_challenge(kb, source_repo)
    original = (kb.root / HASH).read_bytes() if (kb.root / HASH).exists() else None
    order = [SC, REVIEW, IDS, HASH]
    seen = []
    real = store.atomic_write

    def fail_after(path, raw):
        real(path, raw)
        rel = path.relative_to(kb.root).as_posix()
        if rel != JOURNAL:
            seen.append(rel)
        if rel == checkpoint:
            raise OSError(f"injected after {checkpoint}")

    with monkeypatch.context() as patch:
        patch.setattr(store, "atomic_write", fail_after)
        expected = {JOURNAL, *order[:order.index(checkpoint) + 1]}
        # main does not translate unexpected OSError to an exit code. Assert the
        # exception and both output streams, as well as the partial write.
        with changes(kb, source_repo, expected):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with pytest.raises(OSError) as caught:
                    m.main(["--root", str(kb.root), "put", str(staged)])
            assert str(caught.value) == f"injected after {checkpoint}"
            assert out.getvalue() == err.getvalue() == ""
    assert seen == order[:order.index(checkpoint) + 1]
    assert staged.is_file()
    assert json.loads((kb.root / JOURNAL).read_bytes()) == {
        "command": "put SC-0001", "paths": [SC, REVIEW, IDS],
        "tree_hash": original.decode("utf-8") if original is not None else None}
    return original


def recovery_new(kb, source_repo, expected):
    staged = kb.root / ".kblam/review-staging/SC-0002.yaml"
    run(kb, source_repo,
        ["challenge", "new", m.TRACE, "--lines", "2-2", "--by", "reviewer-c"],
        {*expected, ".kblam/review-staging/SC-0002.yaml", ".kblam/review-receipts/SC-0002.json"},
        out=f"{staged}\n",
        err="kblam challenge new: the interrupted put SC-0001 may be partial: run kblam validate, "
            "fix what it reports, then kblam validate --record\n")
    assert not (kb.root / JOURNAL).exists()
    assert m.registry(kb) == ["SC-0001"]
    view = load_view(kb.cfg)
    assert (kb.root / "findings/INDEX.md").read_bytes() == generate_index(view)
    assert (kb.root / REVIEW).read_bytes() == generate_review_index(view)


@pytest.mark.parametrize("checkpoint", [SC, REVIEW, IDS, HASH])
@pytest.mark.parametrize("recorded", [True, False], ids=["recorded-hash", "missing-hash"])
def test_each_partial_record_write_recovers_without_accepting_tree(kb, source_repo, monkeypatch,
                                                                  checkpoint, recorded):
    """Start no records, clean source, tree.hash present or absent; allocate SC-0001 open.

    Acceptance 6. challenge new --by reviewer-a exits 0, staged path; changes
    .kblam/review-staging/SC-0001.yaml and .kblam/review-receipts/SC-0001.json.
    Put has injected OSError (no CLI exit code), no stdout/stderr; changes .kblam/journal.json
    plus each written prefix of [research-review/challenges/SC-0001.yaml,
    research-review/INDEX.md, .kblam/review-ids, .kblam/tree.hash], including the named
    checkpoint. The staged draft and allocation receipt stay. Next challenge new
    --by reviewer-c exits 0, SC-0002 path; stderr 'the interrupted put SC-0001 may be
    partial: run kblam validate, fix what it reports, then kblam validate --record'.
    That call changes {.kblam/review-staging/SC-0002.yaml,
    .kblam/review-receipts/SC-0002.json, .kblam/journal.json} (journal removed), plus
    research-review/INDEX.md iff interrupted after record, .kblam/review-ids iff
    interrupted before registry, .kblam/tree.hash iff interrupted after tree.hash
    (restore previous bytes, or remove when absent). findings/INDEX.md is regenerated identically.
    Validate exits 0, OK (0 findings); changes none. validate --record exits 0,
    'recorded .kblam/tree.hash for this tree'; changes tree.hash, .kblam/pairs.sqlite and .kblam/review.jsonl
    (with tree.hash missing, it is the bootstrap: it asks Jev nothing, says so, and changes tree.hash and
    .kblam/pairs.sqlite). Recovery registers
    only the existing SC-0001, invents no record, preserves its open status and all
    draft bytes, and leaves the source unchanged around every CLI invocation.
    """
    if not recorded:
        (kb.root / HASH).unlink()
    original = interrupted_put(kb, source_repo, monkeypatch, checkpoint)
    draft = (kb.root / STAGED).read_bytes()
    expected = {JOURNAL}
    if checkpoint == SC:
        expected.add(REVIEW)
    if checkpoint in (SC, REVIEW):
        expected.add(IDS)
    if checkpoint == HASH:
        expected.add(HASH)
    recovery_new(kb, source_repo, expected)
    assert ((kb.root / HASH).read_bytes() if (kb.root / HASH).exists() else None) == original
    assert (kb.root / STAGED).read_bytes() == draft
    assert data(m.record_path(kb, "SC-0001"))["status"] == "open"
    assert not m.record_path(kb, "SC-0002").exists()
    validate(kb, source_repo)
    validate(kb, source_repo, record=True)


@pytest.mark.parametrize("failed_index", ["findings/INDEX.md", REVIEW])
def test_failed_recovery_keeps_journal_and_retry_restores_both_indexes(kb, source_repo, monkeypatch,
                                                                     failed_index):
    """Start no records and clean source; interrupt SC-0001 put after tree.hash.

    Acceptance 6. challenge new --by reviewer-a exits 0, path; changes
    {.kblam/review-staging/SC-0001.yaml, .kblam/review-receipts/SC-0001.json}.
    Put raises exact injected OSError, no exit code/output; changes
    {research-review/challenges/SC-0001.yaml, research-review/INDEX.md,
    .kblam/review-ids, .kblam/tree.hash, .kblam/journal.json}.
    Fixture corrupts both indexes outside windows. challenge new --by reviewer-c with
    recovery index failure exits 1, 'cannot recover the interrupted write recorded in
    .kblam/journal.json: injected recovery index failure; the journal is kept'; no stdout.
    Changes none if findings index fails; only findings/INDEX.md if review index fails.
    Journal and advanced hash remain byte-identical; no SC-0002 allocated. Retry exits 0,
    SC-0002 staged path, exact partial-operation stderr; changes
    {.kblam/review-staging/SC-0002.yaml, .kblam/review-receipts/SC-0002.json,
    .kblam/journal.json, .kblam/tree.hash, research-review/INDEX.md} (journal removed,
    hash restored), and findings/INDEX.md iff it previously failed. Registry unchanged.
    validate exits 0, OK
    (0 findings), changes none; validate --record exits 0 and changes tree.hash, .kblam/pairs.sqlite and .kblam/review.jsonl.
    Each call preserves source bytes, HEAD, index and refs.
    """
    original = interrupted_put(kb, source_repo, monkeypatch, HASH)
    kb.write("findings/INDEX.md", "broken findings index\n")
    kb.write(REVIEW, "broken review index\n")
    journal = (kb.root / JOURNAL).read_bytes()
    advanced = (kb.root / HASH).read_bytes()
    real = store.atomic_write

    def fail(path, raw):
        if path.relative_to(kb.root).as_posix() == failed_index:
            raise OSError("injected recovery index failure")
        real(path, raw)

    with monkeypatch.context() as patch:
        patch.setattr(store, "atomic_write", fail)
        run(kb, source_repo,
            ["challenge", "new", m.TRACE, "--lines", "2-2", "--by", "reviewer-c"],
            {"findings/INDEX.md"} if failed_index == REVIEW else set(), code=1,
            err="kblam challenge new: cannot recover the interrupted write recorded in "
                ".kblam/journal.json: injected recovery index failure; the journal is kept\n")
    assert (kb.root / JOURNAL).read_bytes() == journal
    assert (kb.root / HASH).read_bytes() == advanced
    assert not (kb.root / ".kblam/review-staging/SC-0002.yaml").exists()
    expected = {JOURNAL, HASH, REVIEW}
    if failed_index == "findings/INDEX.md":
        expected.add("findings/INDEX.md")
    recovery_new(kb, source_repo, expected)
    assert (kb.root / HASH).read_bytes() == original
    validate(kb, source_repo)
    validate(kb, source_repo, record=True)


def test_record_put_leaves_hash_stale_over_out_of_band_change(kb, source_repo):
    """Start SC-0001 open on clean source; fixture changes its proposition out of band.

    Acceptance 6. Each challenge new --by reviewer-a (SC-0001 then SC-0002) exits 0,
    path; SC-0001 new changes {.kblam/review-staging/SC-0001.yaml,
    .kblam/review-receipts/SC-0001.json}; SC-0002 new changes
    {.kblam/review-staging/SC-0002.yaml, .kblam/review-receipts/SC-0002.json}.
    First put exits 0, canonical SC-0001 path; changes
    {research-review/challenges/SC-0001.yaml, .kblam/review-staging/SC-0001.yaml,
    research-review/INDEX.md, .kblam/review-ids, .kblam/tree.hash}.
    Second put exits 0, canonical SC-0002 path; changes
    {research-review/challenges/SC-0002.yaml, .kblam/review-staging/SC-0002.yaml,
    research-review/INDEX.md, .kblam/review-ids}. stderr names findings/ or
    research-review/ changed outside kblam and 'tree.hash not advanced'. No journal
    remains and the unvalidated change survives. validate exits 0, OK (0 findings),
    changes none; it does not accept the digest. validate --record exits 0, recorded
    tree.hash; changes tree.hash, .kblam/pairs.sqlite and .kblam/review.jsonl. Source unchanged for every call.
    """
    put_challenge(kb, source_repo)
    recorded = (kb.root / HASH).read_bytes()
    fill(m.record_path(kb, "SC-0001"), proposition="An out-of-band revised proposition")
    staged = stage_challenge(kb, source_repo, 2)
    sc2 = "research-review/challenges/SC-0002.yaml"
    run(kb, source_repo, ["put", staged],
        {sc2, REVIEW, IDS, ".kblam/review-staging/SC-0002.yaml"},
        out=f"kblam put: SC-0002 -> {sc2}\n",
        err="kblam put SC-0002: findings/ or research-review/ was changed outside kblam since kblam "
            "last wrote it; tree.hash not advanced. Run kblam validate --record once the change is "
            "validated.\n")
    assert (kb.root / HASH).read_bytes() == recorded
    assert data(m.record_path(kb, "SC-0001"))["proposition"] == "An out-of-band revised proposition"
    assert not (kb.root / JOURNAL).exists()
    validate(kb, source_repo)
    assert (kb.root / HASH).read_bytes() == recorded
    validate(kb, source_repo, record=True)


def wait_for(path, processes=()):
    deadline = time.monotonic() + 30
    while not path.exists():
        assert time.monotonic() < deadline, f"timed out waiting for {path.name}"
        assert all(p.poll() is None for p in processes), "worker exited before reaching gate"
        time.sleep(0.01)


def race_worker(root, gates, name, staged, expected):
    """Real child-process CLI, gated after actual lock acquisition, never replacing the lock."""
    kb, source = KB(Path(root)), SourceRepo(Path(root))
    gates = Path(gates)
    real = writes.kb_lock
    real_create = lock._create

    def observed_create(path, content):
        acquired = real_create(path, content)
        if path.name == "lock" and not acquired:
            (gates / f"u23j_{name}.blocked").touch()
        return acquired

    lock._create = observed_create

    @contextlib.contextmanager
    def observed_lock(cfg, command):
        (gates / f"u23j_{name}.attempt").touch()
        with real(cfg, command):
            before = m.tree(kb, exclude=[".kblam/lock"])
            original_source = source.snapshot()
            (gates / f"u23j_{name}.acquired").write_text(str(os.getpid()), encoding="utf-8")
            wait_for(gates / f"u23j_{name}.release")
            try:
                yield
            finally:
                assert m.changed(before, m.tree(kb, exclude=[".kblam/lock"])) == set(expected)
                assert source.snapshot() == original_source

    writes.kb_lock = observed_lock
    result = m.kblam(kb, "put", staged)
    (gates / f"u23j_{name}.result").write_text(json.dumps(result.__dict__), encoding="utf-8")


def race(kb, source_repo, tmp_path, left, right, left_changes, right_changes):
    gates = tmp_path / "u23j_gates"
    gates.mkdir()
    bootstrap = tmp_path / "u23j_worker.py"
    bootstrap.write_text(
        "import sys, json\n"
        f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "from test_m611_group10 import race_worker\n"
        "race_worker(*sys.argv[1:5], json.loads(sys.argv[5]))\n",
        encoding="utf-8", newline="\n")
    processes = []
    before, original_source = m.tree(kb), source_repo.snapshot()

    def start(name, staged, expected):
        process = subprocess.Popen(
            [sys.executable, str(bootstrap), str(kb.root), str(gates), name, str(staged),
             json.dumps(sorted(expected))], cwd=str(Path(__file__).parent),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(process)
        return process

    try:
        first = start("left", left, left_changes)
        wait_for(gates / "u23j_left.acquired", [first])
        assert json.loads((kb.root / ".kblam/lock").read_bytes())["pid"] == int(
            (gates / "u23j_left.acquired").read_text(encoding="utf-8"))
        second = start("right", right, right_changes)
        wait_for(gates / "u23j_right.blocked", processes)
        assert second.poll() is None
        assert not (gates / "u23j_right.acquired").exists()
        assert m.changed(before, m.tree(kb)) == {".kblam/lock"}
        assert source_repo.snapshot() == original_source
        (gates / "u23j_left.release").touch()
        wait_for(gates / "u23j_left.result", [first])
        wait_for(gates / "u23j_right.acquired", [second])
        assert json.loads((kb.root / ".kblam/lock").read_bytes())["pid"] == int(
            (gates / "u23j_right.acquired").read_text(encoding="utf-8"))
        after_left = m.tree(kb, exclude=[".kblam/lock"])
        assert m.changed(before, after_left) == left_changes
        assert source_repo.snapshot() == original_source
        (gates / "u23j_right.release").touch()
        outputs = [p.communicate(timeout=60) for p in processes]
        assert [p.returncode for p in processes] == [0, 0], outputs
        assert outputs == [("", ""), ("", "")]
        final = m.tree(kb)
        assert m.changed(after_left, final) == right_changes
        assert m.changed(before, final) == left_changes | right_changes
        assert source_repo.snapshot() == original_source
        assert not (kb.root / ".kblam/lock").exists()
        results = [m.Run(**json.loads((gates / f"u23j_{name}.result").read_text(encoding="utf-8")))
                   for name in ("left", "right")]
        return results
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=60)


def test_two_process_record_puts_serialize_without_losing_registry(kb, source_repo, tmp_path):
    """Start no records, clean source; stage separate open SC-0001 and SC-0002.

    Acceptance 6. Each challenge new --by reviewer-a exits 0, path; SC-0001 new changes
    {.kblam/review-staging/SC-0001.yaml, .kblam/review-receipts/SC-0001.json}; SC-0002
    new changes {.kblam/review-staging/SC-0002.yaml, .kblam/review-receipts/SC-0002.json}.
    Two real processes invoke put while the first holds the real lock; second cannot
    acquire until first releases. Each put exits 0, canonical record path, stderr empty.
    SC-0001 put changes {research-review/challenges/SC-0001.yaml,
    .kblam/review-staging/SC-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash}; SC-0002 put changes {research-review/challenges/SC-0002.yaml,
    .kblam/review-staging/SC-0002.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash}. Each staged file is removed. Exclusive mutation windows
    exclude only the transient lock, whose pid/ownership and final removal are asserted
    separately; parent snapshots verify each completion before allowing the next write.
    The pre-start-to-final snapshot changes exactly the union of the two put sets.
    Registry contains both IDs and index contains both records; no journal remains.
    validate exits 0, OK (0 findings), changes none. Source unchanged per worker window
    and per parent completion window, including HEAD, index and refs.
    """
    left = stage_challenge(kb, source_repo)
    right = stage_challenge(kb, source_repo, 2)
    sc2 = "research-review/challenges/SC-0002.yaml"
    results = race(kb, source_repo, tmp_path, left, right,
                   {SC, REVIEW, IDS, HASH, STAGED},
                   {sc2, REVIEW, IDS, HASH, ".kblam/review-staging/SC-0002.yaml"})
    assert results == [m.Run(0, f"kblam put: SC-0001 -> {SC}\n", ""),
                       m.Run(0, f"kblam put: SC-0002 -> {sc2}\n", "")]
    assert m.registry(kb) == ["SC-0001", "SC-0002"]
    assert "SC-0001" in (kb.root / REVIEW).read_text(encoding="utf-8")
    assert "SC-0002" in (kb.root / REVIEW).read_text(encoding="utf-8")
    assert not (kb.root / JOURNAL).exists()
    validate(kb, source_repo)


def test_two_process_edits_refuse_second_stale_edit_base(kb, source_repo, tmp_path):
    """Start SC-0001 open, clean committed source; two staged copies share its edit base.

    Acceptance 6. challenge new --by reviewer-a exits 0, path; changes
    {.kblam/review-staging/SC-0001.yaml, .kblam/review-receipts/SC-0001.json}.
    First put exits 0; changes {research-review/challenges/SC-0001.yaml,
    .kblam/review-staging/SC-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash}. challenge edit exits 0, path; changes
    {.kblam/review-staging/SC-0001.yaml, .kblam/review-receipts/SC-0001.edit-base.json}.
    Fixture copies its bytes
    into .kblam/review-staging/left/SC-0001.yaml and right/SC-0001.yaml outside windows.
    Concurrent process puts: left exits 0, canonical path, stderr empty; changes
    {research-review/challenges/SC-0001.yaml, .kblam/tree.hash,
    .kblam/review-staging/left/SC-0001.yaml, .kblam/review-receipts/SC-0001.edit-base.json}
    (staged copy and receipt removed). Right exits 1, 'changed since your edit; run kblam challenge edit SC-0001
    again', stdout empty; changes none and retains its draft. Per-writer windows exclude
    only the transient lock; its ownership/removal and each parent completion are checked.
    The pre-start-to-final snapshot changes exactly the left put's set (right writes nothing).
    validate exits 0, OK (0 findings); changes none. Source unchanged in all windows.
    """
    put_challenge(kb, source_repo)
    staged = kb.root / STAGED
    receipt = ".kblam/review-receipts/SC-0001.edit-base.json"
    run(kb, source_repo, ["challenge", "edit", "SC-0001"], {STAGED, receipt}, out=f"{staged}\n")
    left = kb.write(".kblam/review-staging/left/SC-0001.yaml", staged.read_bytes())
    right = kb.write(".kblam/review-staging/right/SC-0001.yaml", staged.read_bytes())
    staged.unlink()
    fill(left, proposition="The left reviewer narrowed the proposition")
    fill(right, proposition="The right reviewer independently narrowed the proposition")
    right_bytes = right.read_bytes()
    results = race(kb, source_repo, tmp_path, left, right,
                   {SC, HASH, receipt, ".kblam/review-staging/left/SC-0001.yaml"}, set())
    assert results == [m.Run(0, f"kblam put: SC-0001 -> {SC}\n", ""),
                       m.Run(1, "", "kblam put: SC-0001 changed since your edit; run kblam challenge "
                                    "edit SC-0001 again. Load the kblam-write skill for how to fix this.\n")]
    assert right.read_bytes() == right_bytes
    assert data(m.record_path(kb, "SC-0001"))["proposition"] == "The left reviewer narrowed the proposition"
    assert not (kb.root / JOURNAL).exists()
    validate(kb, source_repo)
