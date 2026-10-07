"""K15, claim task bindings (SPEC §5.2.4 K15, §5.2.3 Claim task)."""

from __future__ import annotations

from kblam import paths
from kblam.config import STATE_DIR
from kblam.decisions import decision_issues
from kblam.finding import ID_RE as FINDING_ID_RE
from kblam.finding import Finding, fingerprint
from kblam.k13 import identity_recovery
from kblam.records import (EFFECTIVE, FINGERPRINT_RE, HEX64_RE, RETIRED, STATUSES, Record, file_ref,
                           schema_issues)
from kblam.rules import Issue
from kblam.sources import sha256_hex

# `confirmed` and `not_reproduced` need primary evidence; `inconclusive` does not (SPEC §5.2.3).
PRIMARY_EVIDENCE = ("confirmed", "not_reproduced")

# The command that fixes a binding, with the placeholders of the §5.2.5 command table.
REBIND = "kblam review rebind {rid} --by NAME --reason TEXT --expect D"


def task_binding_problems(view, rec: Record) -> list[str]:
    """Why a CT's binding no longer holds, [] when it does: the finding exists, and its K3 fingerprint and
    file sha256 equal claim_fingerprint and base_file_sha256. Each message names the finding and ends
    with the command that fixes it (kblam review rebind <CT> ...)."""
    return [message for _key, message in _binding_problems(view, rec)]


def k15(view, reader) -> list[Issue]:
    """Every K15 issue, owner = the task's ID, level by the SPEC §5.2.4 Severity table."""
    issues: list[Issue] = []
    for rec in view.records:
        if rec.kind != "CT" or not isinstance(rec.data, dict):
            continue                    # not a task, or a file K13 reports as not parsing
        status = rec.status
        if status not in STATUSES["CT"] or status == RETIRED:
            continue                    # K13 reports the vocabulary; a retired task gets no binding check
        issues += [Issue(rec.path, rec.key_line(key), "K15", message, "error", rec.id or "")
                   for key, message in _binding_problems(view, rec, trust_state=reader.trust_state)
                   if key is not None]
        if status in EFFECTIVE["CT"]:
            issues += _evidence_issues(view, reader, rec)
    return issues


def k15_pending(view, reader) -> list[str]:
    """One line per open, well-formed task whose binding matches, in ID order:
    "CT-0001 open replication of F-0014: <question>" (SPEC §5.2.4 K15). Pending lines fail nothing."""
    lines = []
    tasks = [rec for rec in view.records
             if rec.kind == "CT" and isinstance(rec.id, str) and rec.status == "open"]
    for rec in sorted(tasks, key=_order):
        if schema_issues(rec, staged=False) or decision_issues(rec):
            continue                    # K13 reports a record that is not well formed
        if task_binding_problems(view, rec):
            continue                    # the binding no longer holds: the task is stale, not pending
        data = rec.data
        lines.append(f"{rec.id} open {data['kind']} of {data['finding']}: {data['question']}")
    return lines


# --- the binding and the closing evidence -------------------------------------------------------


def _binding_problems(view, rec: Record, *, trust_state: bool = True) -> list[tuple[str | None, str]]:
    """(the top-level key to report the problem on, why the binding no longer holds).

    A key of None is the missing finding: K13 reports it as a dangling link, so `k15` does not emit a
    second issue for it, and the commands that list a task's problems still get the message.
    """
    if rec.kind != "CT" or rec.id is None or not isinstance(rec.data, dict):
        return []
    data = rec.data
    finding_id = data.get("finding")
    if not (isinstance(finding_id, str) and FINDING_ID_RE.match(finding_id)):
        return []                       # schema_issues reports a `finding` that is not a finding ID
    command = REBIND.format(rid=rec.id)
    if rec.status in PRIMARY_EVIDENCE:   # rebind keeps the status, so the primary evidence is cited again
        command += " --evidence PROVENANCE:PATH:LOCATOR"
    recovery = identity_recovery(view, rec, trust_state=trust_state) or f"run {command}"
    found = _findings(view, finding_id)
    if not found:
        return [(None, f"{finding_id} names no finding in the KB; restore it, then {recovery}")]
    if len(found) > 1:
        return [(None, f"{finding_id} is the ID of {len(found)} findings, so {rec.id}'s binding cannot be "
                       f"checked until the IDs are unique; K1 reports the duplicate IDs")]
    finding = found[0]

    problems: list[tuple[str | None, str]] = []
    bound, now = data.get("claim_fingerprint"), fingerprint(finding, view.cfg.scope_separator)
    if isinstance(bound, str) and FINGERPRINT_RE.fullmatch(bound) and bound != now:
        problems.append(("claim_fingerprint", f"{finding_id}'s fingerprint is now {now}, not the {bound} "
                                              f"{rec.id} was bound to; reread it, then {recovery}"))
    bound, now = data.get("base_file_sha256"), sha256_hex(finding.raw)
    if isinstance(bound, str) and HEX64_RE.fullmatch(bound) and bound != now:
        problems.append(("base_file_sha256", f"{finding_id}'s file now hashes to {now}, not the {bound} "
                                             f"{rec.id} was bound to (the binding covers the whole file, "
                                             f"not only the fingerprint); reread it, then {recovery}"))
    return problems


def _findings(view, finding_id: str) -> list[Finding]:
    """Every parsed finding with this ID. K1 reports a file that does not parse and K13 the dangling link,
    so a binding that names no single parsed finding is not K15's to report."""
    return [f for f in view.findings if f.file_id == finding_id and isinstance(f.meta, dict)]


def _evidence_issues(view, reader, rec: Record) -> list[Issue]:
    """An effective task's effective decision (SPEC §5.2.3 "A closed task is current"): every evidence
    entry is available, and `confirmed` and `not_reproduced` have primary evidence. A reference that is
    structurally wrong is K13's (sources.Resolved.error)."""
    decision = _effective_decision(rec)
    entries = decision.get("evidence") if decision is not None else None
    if not isinstance(entries, list):
        return []                       # no decision, or schema_issues reports the entry's shape
    line = rec.key_line("decisions")
    issues: list[Issue] = []
    primary = False
    identity = identity_recovery(view, rec, trust_state=reader.trust_state)
    command = f"{REBIND.format(rid=rec.id)} --evidence PROVENANCE:PATH:LOCATOR"
    recovery = identity or f"run {command}"
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        resolved = reader.resolve(file_ref(entry))
        if resolved.error is not None:
            continue                    # K13 reports a reference that is structurally wrong
        path = entry.get("path")
        if not resolved.state.available:
            refused = reader.refused_for_trust(file_ref(entry))
            if refused is not None:
                # A validation that does not trust machine state reads nothing under .kblam/, so the
                # bytes cannot be restored from this clone whatever they hold (SPEC §5.2.6 Committed
                # state): the line names the step that can work here instead.
                message = (f"the evidence {path!r} of {rec.id}'s effective decision cannot be read here: "
                           f"this check reads no file under {STATE_DIR}/, so no restore puts it back. "
                           f"{recovery[:1].upper()}{recovery[1:]}")
            else:
                message = (f"the evidence {path!r} of {rec.id}'s effective decision is "
                           f"{resolved.state.value}: {resolved.message}; restore those bytes, then "
                           f"{recovery}")
            issues.append(Issue(rec.path, line, "K15", message, "error", rec.id or ""))
        primary = primary or _primary(view, entry, path)
    if rec.status in PRIMARY_EVIDENCE and not primary:
        recovery = identity or f"cite one with {command}"
        issues.append(Issue(rec.path, line, "K15",
                            f"{rec.id} is {rec.status} and its effective decision has no evidence entry "
                            f"whose provenance is in [review] primary_provenance and whose path is outside "
                            f"findings/, the review root and every history_dirs folder; {recovery}",
                            "error", rec.id or ""))
    return issues


def _effective_decision(rec: Record) -> dict | None:
    """The decision that set the record's status: the last entry of `decisions`, when it is one."""
    entries = rec.data.get("decisions") if isinstance(rec.data, dict) else None
    if not isinstance(entries, list) or not entries:
        return None
    last = entries[-1]
    return last if isinstance(last, dict) else None


def _primary(view, entry: dict, path) -> bool:
    """A primary evidence entry (SPEC §5.2.3): a provenance in [review] primary_provenance, and a path
    whose resolved target is outside findings/, the review root, .kblam/ and every history_dirs folder.
    A challenge, or a paraphrase of one, is not primary evidence."""
    if entry.get("provenance") not in view.cfg.primary_provenance or not isinstance(path, str):
        return False
    try:
        target = paths.resolve(view.cfg, path)
    except paths.PathRefused:
        return False
    return paths.protected(view.cfg, target) is None


def _order(rec: Record) -> tuple[int, str]:
    """Numeric ID order, as view.findings uses for findings: CT-0009 before CT-00010."""
    number = rec.id[3:] if isinstance(rec.id, str) else ""
    return (int(number) if number.isdigit() else 0, rec.id or "")
