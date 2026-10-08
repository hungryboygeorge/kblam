"""Subject digests, decisions and status changes (SPEC §5.2.2 Subject digest, Decisions, Status changes)."""

from __future__ import annotations

import hashlib
import json

from kblam.records import RETIRED, STATUSES, Record
from kblam.rules import Issue

SUBJECT_FIELDS = {
    "source-challenge": ("id", "source", "proposition", "scope", "classification", "basis", "usable",
                         "limits"),
    "claim-task": ("id", "kind", "finding", "claim_fingerprint", "base_file_sha256", "question", "method",
                   "outcomes", "controls", "stop", "expected_evidence", "proponent"),
    "checked-use": ("id", "challenge", "challenge_bind", "finding", "finding_fingerprint",
                    "finding_file_sha256", "citation", "disposition", "reason", "proponent"),
}

KIND_WORDS = {"source-challenge": "challenge", "claim-task": "task", "checked-use": "use"}

# The §5.2.2 table (SPEC lines 371-384) as data: kind -> from -> the statuses that kind's own rows
# allow. The two rows that hold for every kind -- any status except `open` (and a source challenge's
# `confirmed`) to `open`, and `open` to `stale` -- name no kind, so `_allowed` adds them.
TRANSITIONS = {
    "source-challenge": {"open": ("confirmed", "rejected"),
                         "confirmed": ("stale",),
                         "rejected": ("stale",)},
    "claim-task": {"open": ("confirmed", "not_reproduced", "inconclusive"),
                   "confirmed": ("stale",),
                   "not_reproduced": ("stale",),
                   "inconclusive": ("stale",)},
    "checked-use": {"open": ("approved", "withdrawn"),
                    "approved": ("stale",),
                    "withdrawn": ("stale",)},
}

REBINDS = ("claim-task", "checked-use")   # `rebind` keeps a task's and a use's status; a challenge is never re-decided

REOPEN_REFUSED = ("a confirmed challenge is never reopened; retire it with --status stale and file a "
                  "new challenge")

# The fields `decide` and `rebind` compare `--by` against, in the order a message names them (SPEC
# §5.2.2 "Independent means": a source challenge's creator; a claim task's creator and proponent; a
# checked use's proponent, whose creator may approve it).
SELF_ROLES = {"source-challenge": ("creator",), "claim-task": ("creator", "proponent"), "checked-use": ("proponent",)}


def canonical_json(obj) -> bytes:
    """json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False), UTF-8 encoded."""
    # default=str: a parsed record can hold a date or bytes where a field expects text, and digests are
    # computed for records the schema already rejects. No subject field of any kind holds a date, so
    # this never applies to a well-formed record.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode("utf-8")


def subject_digest(kind: str, data: dict) -> str:
    """sha256 hex of canonical_json({field: data.get(field) for field in SUBJECT_FIELDS[kind]})."""
    fields = {field: data.get(field) for field in SUBJECT_FIELDS[kind]}
    return hashlib.sha256(canonical_json(fields)).hexdigest()


def _allowed(kind: str, old: str) -> tuple[str, ...]:
    """The statuses a decision may set from `old`: the kind's own row, then the two rows for every kind."""
    if old not in STATUSES.get(kind, ()):
        return ()                                    # not a status of this kind: no row of the table applies
    allowed = list(TRANSITIONS[kind].get(old, ()))
    if old == "open":
        allowed.append(RETIRED)                      # any kind: open -> stale, anyone
    elif not (kind == "source-challenge" and old == "confirmed"):  # open, and a challenge's confirmed
        allowed.append("open")
    return tuple(allowed)


def transition_problem(kind: str, old: str, new: str) -> str | None:
    """None if a decision may take a `kind` record from `old` to `new`, else why not.

    The §5.2.2 table, plus `rebind` keeping the status (old == new, for a claim task and a checked
    use, old not stale). A confirmed source challenge never goes back to open ("a confirmed challenge
    is never reopened; retire it with --status stale and file a new challenge").
    """
    word = KIND_WORDS.get(kind, kind)
    allowed = _allowed(kind, old)
    if not allowed:
        # Not a status of this kind, so no row of the table applies; schema_issues reports the vocabulary.
        return f"a {word} cannot go from {old} to {new}: {old} is not a status a decision sets"
    if new == old:
        if kind in REBINDS and old != RETIRED:
            return None                              # rebind keeps the status
        if kind == "source-challenge":
            return (f"a {word} cannot be decided to its own status {old}: a challenge has no rebind, and "
                    f"a changed assertion or judgement is a new challenge")
        return (f"a {word} in status {old} cannot be re-decided to {old}: {old} is retired, and a retired "
                f"record stays as audit data")
    if new in allowed:
        return None
    if kind == "source-challenge" and old == "confirmed" and new == "open":
        return REOPEN_REFUSED
    return f"a {word} cannot go from {old} to {new}; from {old} a decision can set {' or '.join(allowed)}"


def needs_independence(kind: str, old: str, new: str) -> bool:
    """Setting `open`, and retiring an `open` record, need no independence; every other decision does."""
    return new != "open" and not (old == "open" and new == RETIRED)


def independence_problem(kind: str, data: dict, by: str) -> str | None:
    """None if `by` is independent for this record (SPEC §5.2.2): for a source challenge, by != creator;
    for a claim task, by differs from creator and proponent; for a checked use, by != proponent (the
    creator may approve). Else a message naming the role, e.g. "reviewer-b is source-challenge-0001's
    creator; a closing decision needs someone else"."""
    rid = data.get("id")
    rid = rid if isinstance(rid, str) and rid else "the record"
    for role in SELF_ROLES.get(kind, ()):
        if data.get(role) == by:
            return f"{by} is {rid}'s {role}; a closing decision needs someone else"
    return None


def decision_issues(rec: Record) -> list[Issue]:
    """K13 errors on a parsed record's decisions (skips what schema_issues reports as malformed):
    status differs from the last decision's (or from `open` with none); when status is not open, the
    last decision's bind differs from subject_digest; a decision sequence that breaks transition_problem
    (replayed from `open`); a stored decision that needed independence and lacks it. code K13, level
    error, owner rec.id, path rec.path."""
    data = rec.data
    kind = rec.kind
    if not isinstance(data, dict) or kind not in SUBJECT_FIELDS:
        return []                                    # schema_issues reports a record that did not parse
    decisions_line = rec.key_line("decisions")
    status_line = rec.key_line("status")
    issues: list[Issue] = []

    def add(line: int, message: str) -> None:
        issues.append(Issue(rec.path, line, "K13", message, owner=rec.id or ""))

    entries = data.get("decisions")
    if not isinstance(entries, list):
        entries = []                                 # schema_issues reports a `decisions` that is not a list
    previous = "open"                                # open until a decision sets the status (SPEC §5.2.2)
    last_status: str | None = None
    last_bind: str | None = None
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            last_status = last_bind = None           # an entry that is not a mapping: schema_issues' business
            continue
        status = entry.get("status")
        by = entry.get("by")
        bind = entry.get("bind")
        last_status = status if isinstance(status, str) else None
        last_bind = bind if isinstance(bind, str) else None
        if not isinstance(status, str) or status not in STATUSES[kind]:
            continue                                 # schema_issues reports the vocabulary; nothing to replay
        problem = transition_problem(kind, previous, status)
        if problem:
            add(decisions_line, f"decisions[{i}]: {problem}")
        if isinstance(by, str) and needs_independence(kind, previous, status):
            problem = independence_problem(kind, data, by)
            if problem:
                add(decisions_line, f"decisions[{i}]: {problem}")
        previous = status

    status = rec.status
    if status is None or status not in STATUSES[kind]:
        return issues                                # schema_issues reports a missing or unknown status
    if not entries:
        if status != "open":
            add(status_line, f"status is {status} with no decisions; a record with no decisions is open, "
                             f"so set status: open or append the decision that made it {status}")
        return issues
    if last_status is not None and last_status in STATUSES[kind] and status != last_status:
        add(status_line, f"status is {status} but the last decision set {last_status}; a record's status "
                         f"is its last decision's status")
    if status != "open" and last_bind is not None:
        digest = subject_digest(kind, data)
        if last_bind != digest:
            add(decisions_line, f"the last decision's bind is {last_bind} but the record's subject digest "
                                f"is {digest}; a decided record cannot be edited, so the record was "
                                f"changed by hand")
    return issues
