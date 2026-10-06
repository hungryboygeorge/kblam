"""SPEC §12 M6.11 group 7: task workflows through the real CLI (Acceptance 4 and 5)."""

from __future__ import annotations

import hashlib

import pytest

import m611_helpers as m
from kblam import records
from kblam.finding import fingerprint, yaml_rt
from kblam.view import load_view

frozen_today = m.frozen_today

CT = "CT-0001"
CT_PATH = "research-review/tasks/CT-0001.yaml"
SC_PATH = "research-review/challenges/SC-0001.yaml"
STAGED_CT = ".kblam/review-staging/CT-0001.yaml"
STAGED_SC = ".kblam/review-staging/SC-0001.yaml"
FINDING = "findings/calibration/F-0001-ratio.md"
QUESTION = "Does an independent measurement establish the claim?"
REBIND = "kblam review rebind CT-0001 --by NAME --reason TEXT --expect D"
REBIND_EVIDENCE = f"{REBIND} --evidence PROVENANCE:PATH:LOCATOR"
PENDING = f"{CT} open replication of F-0001: {QUESTION}\n"
PRIMARY_REASON = "Three controlled captures satisfy outcomes.supports."
INCONCLUSIVE_REASON = "Three captures remain too noisy; the stop condition precludes further work."


def _run(kb, source_repo, paths, *argv, code=0, out="", err=""):
    """One command, one complete KB/source window, and its complete Run contract."""
    before, source_before = m.tree(kb), source_repo.snapshot()
    run = m.kblam(kb, *argv)
    assert m.changed(before, m.tree(kb)) == set(paths)
    assert source_repo.snapshot() == source_before
    assert run == m.Run(code, out, err)
    return run


def _read(kb, rec_id=CT):
    path = m.record_path(kb, rec_id)
    return records.parse_record(path.relative_to(kb.root).as_posix(), path.read_bytes())


def _validate(kb, source_repo, *, pending=False, issues="", code=0):
    summary = ("kblam validate: 1 error(s) in research-review/\n" if code else
               "kblam validate: OK (1 findings)" + ("; 1 pending task(s)" if pending else "") + "\n")
    return _run(kb, source_repo, set(), "validate", code=code,
                out=issues + (PENDING if pending else "") + summary)


def _finding(kb):
    kb.add("F-0001", "ratio", m.CLAIM)
    m.accept_tree(kb)


def _task(kb, source_repo, *, kind="replication", registered=(CT,)):
    path = kb.root / STAGED_CT
    _run(kb, source_repo, {STAGED_CT, ".kblam/review-receipts/CT-0001.json"},
         "task", "new", "F-0001", "--kind", kind, "--by", "researcher-a",
         "--proponent", "author-a", out=f"{path}\n")
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    assert data["status"] == "open" and data["decisions"] == []
    assert data["creator"] == "researcher-a" and data["proponent"] == "author-a"
    data.update({"question": QUESTION, "method": "Repeat the capture with the documented settings.",
                 "outcomes": {"supports": "The ratio is within 0.1%.",
                              "refutes": "The ratio differs by more.",
                              "inconclusive": "The capture is too noisy to tell."},
                 "controls": ["same firmware version"], "stop": "Stop after three captures.",
                 "expected_evidence": ["an evidence/ capture package that does not exist yet"]})
    path.write_bytes(records.dump(data))
    _run(kb, source_repo, {STAGED_CT, CT_PATH, "research-review/INDEX.md",
                          ".kblam/review-ids", ".kblam/tree.hash"},
         "put", path, out=f"kblam put: {CT} -> {CT_PATH}\n")
    installed = _read(kb).data
    assert installed == records.parse_record(CT_PATH, records.dump(data)).data
    finding = load_view(kb.cfg).findings[0]
    assert installed["claim_fingerprint"] == fingerprint(finding, kb.cfg.scope_separator)
    assert installed["base_file_sha256"] == hashlib.sha256(finding.raw).hexdigest()
    assert m.registry(kb) == list(registered)


def _decide(kb, source_repo, status, *, evidence=(), reason=PRIMARY_REASON,
            code=0, out=None, err="", paths=None):
    digest = m.expect(kb, CT)
    args = ["review", "decide", CT, "--status", status, "--by", "reviewer-b",
            "--reason", reason, "--expect", digest]
    for entry in evidence:
        args += ["--evidence", entry]
    if out is None:
        out = f"kblam review decide: {CT} is now {status} (subject digest {digest[:12]})\n"
    if paths is None:
        paths = {CT_PATH, "research-review/INDEX.md", ".kblam/tree.hash"} if code == 0 else set()
    _run(kb, source_repo, paths, *args, code=code, out=out, err=err)


def _confirm(kb, source_repo):
    _decide(kb, source_repo, "confirmed", evidence=(m.PRIMARY_EVIDENCE,))
    data = _read(kb).data
    assert data["status"] == "confirmed"
    assert data["decisions"] == [{"date": m.TODAY.isoformat(), "by": "reviewer-b", "status": "confirmed",
                                  "reason": PRIMARY_REASON, "bind": m.expect(kb, CT),
                                  "evidence": [{"path": m.EVIDENCE_PATH,
                                                "sha256": hashlib.sha256(b"manifest\n").hexdigest(),
                                                "repo": None, "commit": None, "blob": None,
                                                "snapshot": None, "locator": "row 0",
                                                "provenance": "observed"}]}]
    _validate(kb, source_repo)


def _no_primary(kb, command):
    line = _read(kb).key_line("decisions")
    return (f"K15 {CT_PATH}:{line}: CT-0001 is confirmed and its effective decision has no evidence "
            "entry whose provenance is in [review] primary_provenance and whose path is outside "
            "findings/, the review root and every history_dirs folder; cite one with "
            f"{REBIND_EVIDENCE}\n"
            f"kblam review {command}: refused CT-0001 (1 error(s)); research-review/ is unchanged. "
            "Fix what is listed above and run it again.\n")


@pytest.mark.parametrize("kind", ("replication", "confirmation"))
def test_open_task_is_pending_and_can_be_edited_and_shown(kb, source_repo, kind):
    """Start: no records, valid F-0001, committed LF source; no decision evidence.
    task new by researcher-a/proponent author-a exits 0, prints CT-0001's staging path,
    changes .kblam/review-staging/CT-0001.yaml and .kblam/review-receipts/CT-0001.json.
    put (allocation actor researcher-a) exits 0, prints CT-0001 -> installed path, changes
    .kblam/review-staging/CT-0001.yaml, research-review/tasks/CT-0001.yaml,
    research-review/INDEX.md, .kblam/review-ids and .kblam/tree.hash.
    task edit (no --by) exits 0, prints staging path, changes .kblam/review-staging/CT-0001.yaml
    and .kblam/review-receipts/CT-0001.edit-base.json. The edited put exits 0 and changes
    that staging path, .kblam/review-receipts/CT-0001.edit-base.json,
    research-review/tasks/CT-0001.yaml, research-review/INDEX.md and .kblam/tree.hash. task show (no --by) exits 0, prints the full current binding and
    edited question, changes none. Both validate calls (no --by) exit 0, print CT-0001
    open <kind> of F-0001 and OK with one pending task, change none; no K13/K15 diagnostics.
    Source unchanged around every command. Acceptance 4: visible, nonblocking planned work.
    """
    _finding(kb)
    _task(kb, source_repo, kind=kind)
    pending = f"{CT} open {kind} of F-0001: {QUESTION}\n"
    _run(kb, source_repo, set(), "validate",
         out=pending + "kblam validate: OK (1 findings); 1 pending task(s)\n")
    staged = kb.root / STAGED_CT
    _run(kb, source_repo, {STAGED_CT, ".kblam/review-receipts/CT-0001.edit-base.json"},
         "task", "edit", CT, out=f"{staged}\n")
    data = yaml_rt().load(staged.read_bytes().decode("utf-8"))
    data["question"] = "Do three independent captures establish the claim?"
    staged.write_bytes(records.dump(data))
    _run(kb, source_repo, {STAGED_CT, CT_PATH, "research-review/INDEX.md", ".kblam/tree.hash",
                          ".kblam/review-receipts/CT-0001.edit-base.json"},
         "put", staged, out=f"kblam put: {CT} -> {CT_PATH}\n")
    assert _read(kb).data == records.parse_record(CT_PATH, records.dump(data)).data
    _run(kb, source_repo, set(), "task", "show", CT,
         out=f"{CT} open\nsubject digest: {m.expect(kb, CT)}\nkind: {kind}\nfinding: F-0001\n"
             "proponent: author-a\n"
             f"binding: fingerprint {data['claim_fingerprint']}, file sha256 {data['base_file_sha256']}\n"
             "binding: current\nquestion: Do three independent captures establish the claim?\n"
             "method: Repeat the capture with the documented settings.\n"
             "outcomes.supports: The ratio is within 0.1%.\n"
             "outcomes.refutes: The ratio differs by more.\n"
             "outcomes.inconclusive: The capture is too noisy to tell.\n"
             "controls: same firmware version\nstop: Stop after three captures.\n"
             "expected evidence: an evidence/ capture package that does not exist yet\n"
             "decisions:\n  none\n")
    _run(kb, source_repo, set(), "validate",
         out=f"{CT} open {kind} of F-0001: {data['question']}\n"
             "kblam validate: OK (1 findings); 1 pending task(s)\n")


def test_confirmed_task_has_primary_evidence_and_ignores_uncited_evidence(kb, source_repo):
    """Start: no records, valid F-0001 citing the evidence folder, committed LF source.
    task new by researcher-a/proponent author-a exits 0, prints staging path, changes
    .kblam/review-staging/CT-0001.yaml and .kblam/review-receipts/CT-0001.json; put exits 0,
    prints CT-0001 -> installed path, changes .kblam/review-staging/CT-0001.yaml,
    research-review/tasks/CT-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash. validate exits 0, prints pending and OK, changes none.
    review decide confirmed by reviewer-b with observed README.md evidence exits 0,
    prints CT-0001 is now confirmed, changes research-review/tasks/CT-0001.yaml,
    research-review/INDEX.md and .kblam/tree.hash. Both subsequent validate calls exit 0,
    print OK without pending or K15, change none, even after fixture editing uncited log.txt.
    No stderr; source unchanged for each call. Acceptance 4 and 5: primary evidence closes
    independently; a finding's cited evidence not hashed by the decision is not checked.
    """
    _finding(kb)
    _task(kb, source_repo)
    _validate(kb, source_repo, pending=True)
    _confirm(kb, source_repo)
    kb.write("evidence/2026-09-22-ratio/log.txt", "The uncited capture was replaced.\n")
    _validate(kb, source_repo)


@pytest.mark.parametrize("evidence_kind", ("challenge", "finding"))
def test_confirming_with_only_a_kb_record_as_evidence_is_refused(kb, source_repo, evidence_kind):
    """Start: no records, valid F-0001, committed LF source. For the challenge case only:
    challenge new by reviewer-a exits 0, prints staging path, changes
    .kblam/review-staging/SC-0001.yaml and .kblam/review-receipts/SC-0001.json; challenge put
    exits 0, prints SC-0001 -> installed path, changes .kblam/review-staging/SC-0001.yaml,
    research-review/challenges/SC-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash. SC-0001 remains open with pinned source and primary basis.
    task new by researcher-a/proponent author-a exits 0, prints staging path, changes
    .kblam/review-staging/CT-0001.yaml and .kblam/review-receipts/CT-0001.json; task put
    exits 0, prints CT-0001 -> installed path, changes .kblam/review-staging/CT-0001.yaml,
    research-review/tasks/CT-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash. review decide confirmed by reviewer-b citing only observed SC-0001
    or F-0001 exits 1, prints K15 no evidence entry outside findings/review/history and
    review decide refused CT-0001 (1 error(s)), changes none. validate exits 0 with pending
    CT-0001 and OK; changes none. No stderr, source unchanged each call. Acceptance 5:
    a challenge or paraphrase cannot replace primary evidence; refused decisions stay open.
    """
    _finding(kb)
    if evidence_kind == "challenge":
        staged = kb.root / STAGED_SC
        _run(kb, source_repo, {STAGED_SC, ".kblam/review-receipts/SC-0001.json"},
             "challenge", "new", m.TRACE, "--lines", "3-3", "--by", "reviewer-a",
             out=f"{staged}\n")
        data = yaml_rt().load(staged.read_bytes().decode("utf-8"))
        data.update({"proposition": "The equality follows from the printed byte values.",
                     "scope": ["MX-100 capture transcription"], "classification": "contradicted",
                     "basis": [{"path": m.TRACE, "sha256": None, "repo": None, "commit": None,
                                "blob": None, "snapshot": None, "locator": "row 102",
                                "role": "internal-inconsistency", "provenance": "observed"}],
                     "usable": "The printed byte values remain a report.",
                     "limits": "Do not infer actual wire bytes."})
        staged.write_bytes(records.dump(data))
        _run(kb, source_repo, {STAGED_SC, SC_PATH, "research-review/INDEX.md",
                              ".kblam/review-ids", ".kblam/tree.hash"},
             "put", staged, out=f"kblam put: SC-0001 -> {SC_PATH}\n")
        assert _read(kb, "SC-0001").status == "open"
    _task(kb, source_repo, registered=(CT, "SC-0001") if evidence_kind == "challenge" else (CT,))
    path = SC_PATH if evidence_kind == "challenge" else FINDING
    _decide(kb, source_repo, "confirmed", evidence=(f"observed:{path}:the KB interpretation",),
            code=1, out=_no_primary(kb, "decide"))
    assert _read(kb).status == "open" and _read(kb).data["decisions"] == []
    _validate(kb, source_repo, pending=True)


def test_inconclusive_task_closes_without_promoting_the_claim(kb, source_repo):
    """Start: no records, valid F-0001, committed LF source.
    task new by researcher-a/proponent author-a exits 0, prints staging path, changes
    .kblam/review-staging/CT-0001.yaml and .kblam/review-receipts/CT-0001.json; put exits 0,
    prints CT-0001 -> installed path, changes .kblam/review-staging/CT-0001.yaml,
    research-review/tasks/CT-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash. validate exits 0, prints pending and OK, changes none.
    review decide inconclusive by reviewer-b with stopping reason and no evidence exits 0,
    prints CT-0001 is now inconclusive, changes research-review/tasks/CT-0001.yaml,
    research-review/INDEX.md and .kblam/tree.hash (not the finding). Subsequent validate
    exits 0, prints OK with no pending or K15, changes none. No stderr; source unchanged
    each call. Acceptance 4 and 5: independent inconclusive closure is not confirmation.
    """
    _finding(kb)
    _task(kb, source_repo)
    _validate(kb, source_repo, pending=True)
    finding_before = (kb.root / FINDING).read_bytes()
    _decide(kb, source_repo, "inconclusive", reason=INCONCLUSIVE_REASON)
    data = _read(kb).data
    assert data["status"] == "inconclusive"
    assert data["decisions"] == [{"date": m.TODAY.isoformat(), "by": "reviewer-b", "status": "inconclusive",
                                  "reason": INCONCLUSIVE_REASON, "evidence": [],
                                  "bind": m.expect(kb, CT)}]
    assert (kb.root / FINDING).read_bytes() == finding_before
    _validate(kb, source_repo)


def test_changed_effective_evidence_reports_k15_and_can_be_rebound(kb, source_repo):
    """Start: no records, valid F-0001, committed LF source and provisional manifest evidence.
    task new by researcher-a/proponent author-a exits 0, prints staging path, changes
    .kblam/review-staging/CT-0001.yaml and .kblam/review-receipts/CT-0001.json; put exits 0,
    prints CT-0001 -> installed path, changes .kblam/review-staging/CT-0001.yaml,
    research-review/tasks/CT-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash. review decide confirmed by reviewer-b with observed README.md exits 0,
    prints CT-0001 is now confirmed, changes research-review/tasks/CT-0001.yaml,
    research-review/INDEX.md and .kblam/tree.hash; validate exits 0, OK, changes none.
    After fixture changing README.md, validate exits 1 with K15 effective evidence is stale:
    working file has other bytes, and 1 error(s); changes none. review rebind by reviewer-b
    citing updated primary evidence exits 0, prints CT-0001 rebound, now confirmed,
    changes research-review/tasks/CT-0001.yaml and .kblam/tree.hash only. Final validate
    exits 0, OK with no K15 or pending, changes none. No stderr; source unchanged each call.
    Acceptance 5: hashed decision inputs cannot change silently, and rechecking repairs them.
    """
    _finding(kb)
    _task(kb, source_repo)
    _confirm(kb, source_repo)
    old = _read(kb).data["decisions"][0]
    kb.write(m.EVIDENCE_PATH, "Three repeated captures: ratio within 0.1%.\n")
    line = _read(kb).key_line("decisions")
    _validate(kb, source_repo, code=1,
              issues=f"K15 {CT_PATH}:{line}: the evidence {m.EVIDENCE_PATH!r} of CT-0001's effective "
                     "decision is stale: the working file has other bytes; restore those bytes, "
                     f"then run {REBIND_EVIDENCE}\n")
    digest = m.expect(kb, CT)
    _run(kb, source_repo, {CT_PATH, ".kblam/tree.hash"}, "review", "rebind", CT,
         "--by", "reviewer-b", "--reason", "Rechecked the replacement capture against the controls.",
         "--expect", digest, "--evidence", m.PRIMARY_EVIDENCE,
         out=f"kblam review rebind: {CT} rebound, now confirmed (subject digest {digest[:12]})\n")
    data = _read(kb).data
    assert data["status"] == "confirmed" and len(data["decisions"]) == 2
    assert data["decisions"][0] == old
    assert data["decisions"][1]["evidence"][0]["sha256"] == hashlib.sha256(
        (kb.root / m.EVIDENCE_PATH).read_bytes()).hexdigest()
    assert data["decisions"][1]["bind"] == digest
    _validate(kb, source_repo)


def test_rebinding_confirmed_task_requires_evidence_again(kb, source_repo):
    """Start: no records, valid F-0001, committed LF source.
    task new by researcher-a/proponent author-a exits 0, prints staging path, changes
    .kblam/review-staging/CT-0001.yaml and .kblam/review-receipts/CT-0001.json; put exits 0,
    prints CT-0001 -> installed path, changes .kblam/review-staging/CT-0001.yaml,
    research-review/tasks/CT-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash. review decide confirmed by reviewer-b with observed README.md exits 0,
    prints CT-0001 is now confirmed, changes research-review/tasks/CT-0001.yaml,
    research-review/INDEX.md and .kblam/tree.hash; first validate exits 0, OK, changes none.
    After fixture changing only F-0001's body, validate exits 1, prints K15 file now hashes
    differently (whole-file binding) and 1 error(s), changes none. review rebind by reviewer-b
    without --evidence exits 1, prints K15 no primary evidence and refused CT-0001, changes
    none. validate again exits 1 with the same K15 binding error, changes none.
    review rebind by reviewer-b with observed README.md exits 0, prints rebound now confirmed,
    changes research-review/tasks/CT-0001.yaml and .kblam/tree.hash; final validate exits 0,
    OK, changes none. No stderr; source unchanged each call. Acceptance 5: old evidence
    cannot silently be reused, even when independent rechecking repairs only body bytes.
    """
    _finding(kb)
    _task(kb, source_repo)
    _confirm(kb, source_repo)
    old_data = _read(kb).data
    kb.add("F-0001", "ratio", m.CLAIM, body="Additional context, not a different claim.")
    m.accept_tree(kb)
    now = hashlib.sha256((kb.root / FINDING).read_bytes()).hexdigest()
    line = _read(kb).key_line("base_file_sha256")
    issue = (f"K15 {CT_PATH}:{line}: F-0001's file now hashes to {now}, not the "
             f"{old_data['base_file_sha256']} CT-0001 was bound to (the binding covers the whole file, "
             f"not only the fingerprint); reread it, then run {REBIND_EVIDENCE}\n")
    _validate(kb, source_repo, code=1, issues=issue)
    digest = m.expect(kb, CT)
    _run(kb, source_repo, set(), "review", "rebind", CT, "--by", "reviewer-b", "--reason",
         "Rechecked the added context.", "--expect", digest, code=1, out=_no_primary(kb, "rebind"))
    assert _read(kb).data == old_data
    _validate(kb, source_repo, code=1, issues=issue)
    # Compute only the subject digest, not the CLI output, from the expected new binding.
    from kblam.decisions import subject_digest

    rebound = dict(old_data)
    rebound["base_file_sha256"] = now
    new_digest = subject_digest("CT", rebound)
    _run(kb, source_repo, {CT_PATH, ".kblam/tree.hash"}, "review", "rebind", CT,
         "--by", "reviewer-b", "--reason", "Rechecked the added context with the primary capture.",
         "--expect", digest, "--evidence", m.PRIMARY_EVIDENCE,
         out=f"kblam review rebind: {CT} rebound, now confirmed (subject digest {new_digest[:12]})\n")
    data = _read(kb).data
    assert data["base_file_sha256"] == now
    assert data["claim_fingerprint"] == old_data["claim_fingerprint"]
    assert data["status"] == "confirmed" and data["decisions"][:-1] == old_data["decisions"]
    assert data["decisions"][-1]["bind"] == new_digest
    assert data["decisions"][-1]["evidence"] == old_data["decisions"][-1]["evidence"]
    _validate(kb, source_repo)


def test_retired_task_has_no_binding_or_evidence_availability_checks(kb, source_repo):
    """Start: no records, valid F-0001, committed LF source.
    task new by researcher-a/proponent author-a exits 0, prints staging path, changes
    .kblam/review-staging/CT-0001.yaml and .kblam/review-receipts/CT-0001.json; put exits 0,
    prints CT-0001 -> installed path, changes .kblam/review-staging/CT-0001.yaml,
    research-review/tasks/CT-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash. review decide confirmed by reviewer-b with observed README.md exits 0,
    prints CT-0001 is now confirmed, changes research-review/tasks/CT-0001.yaml,
    research-review/INDEX.md and .kblam/tree.hash; validate exits 0, OK, changes none.
    review decide stale by reviewer-b exits 0, prints CT-0001 is now stale, changes
    research-review/tasks/CT-0001.yaml, research-review/INDEX.md and .kblam/tree.hash.
    After fixture changing F-0001's claim/body and README.md, validate exits 0, OK without
    K15 or pending, changes none. review rebind by reviewer-b exits 1, stderr says CT-0001
    is stale; a retired record stays as audit data; changes none. Final validate exits 0,
    OK, changes none. Source unchanged each call. Acceptance 4 and 5: retirement is explicit,
    audit data remains, and old bindings do not block a valid finding.
    """
    _finding(kb)
    _task(kb, source_repo)
    _confirm(kb, source_repo)
    prior = _read(kb).data
    _decide(kb, source_repo, "stale", reason="Retired after completing the review.")
    retired = _read(kb).data
    assert retired["status"] == "stale" and retired["decisions"][:-1] == prior["decisions"]
    assert retired["decisions"][-1] == {"date": m.TODAY.isoformat(), "by": "reviewer-b", "status": "stale",
                                       "reason": "Retired after completing the review.",
                                       "evidence": [], "bind": m.expect(kb, CT)}
    kb.add("F-0001", "ratio", "The curve types differ by 5% on line 0.", body="New capture context.")
    kb.write(m.EVIDENCE_PATH, "A replacement capture manifest.\n")
    m.accept_tree(kb)
    finding = load_view(kb.cfg).findings[0]
    assert fingerprint(finding, kb.cfg.scope_separator) != retired["claim_fingerprint"]
    assert hashlib.sha256(finding.raw).hexdigest() != retired["base_file_sha256"]
    _validate(kb, source_repo)
    _run(kb, source_repo, set(), "review", "rebind", CT, "--by", "reviewer-b", "--reason",
         "Try to recheck retired work.", "--expect", m.expect(kb, CT), code=1,
         err="kblam review rebind: CT-0001 is stale; a retired record stays as audit data and is never "
             "rebound. Draft a new task with kblam task new\n")
    assert _read(kb).data == retired
    _validate(kb, source_repo)
