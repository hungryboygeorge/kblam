"""K13, review record integrity (SPEC §5.2.4 K13 and the Severity table; §5.2.3 Evaluation, Basis,
Confirmation). Also the shared challenge and use state that K14, the staging commands and the decision
commands use."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import date
from functools import partial
from pathlib import PurePosixPath

from kblam import decisions, matching, paths, receipts, records, registry, sources, treehash
from kblam.config import STATE_DIR
from kblam.finding import fingerprint, normalise_newlines
from kblam.gitdir import committed_record
from kblam.records import Record
from kblam.review_index import generate_review_index
from kblam.rules import Issue
from kblam.sources import FileRef, Resolved, State

FOLDERS = {folder: prefix for prefix, folder in records.KINDS.items()}   # "challenges" -> "source-challenge"
REBIND = "kblam review rebind {rid} --by NAME --reason TEXT --expect D"
CONFIRM = "kblam review decide {rid} --status confirmed --by NAME --reason TEXT --expect D"
RETIRE = "kblam review decide {rid} --status stale --by NAME --reason TEXT --expect D"
NEW_USE = "kblam use review {challenge} {finding} <excerpt-ordinal> --by NAME --proponent NAME"
WRITE_GATE_FIX = ("fix the review-root problem kblam validate reports first (it blocks every write), "
                  "then run kblam validate again")


@dataclass(frozen=True)
class ChallengeInfo:
    rec: Record
    key: str | None                     # canonical key of source.path; None when refused or malformed
    resolved: Resolved | None           # the source reference, resolved; None when malformed
    span: tuple[int, int] | None        # the assertion's span in the resolved bytes' LF text, when the
                                        # source is available and the assertion matches (sha256,
                                        # occurrence, unique within lines)
    digest: str | None                  # subject digest; None when data did not parse

    @property
    def confirmed(self) -> bool:
        return self.rec.status == "confirmed"

    @property
    def available(self) -> bool:
        return self.resolved is not None and self.resolved.state.available


def challenge_info(view, reader, rec: Record) -> ChallengeInfo:
    """Resolve a challenge's source and locate its assertion (matching.assertion_match on the resolved
    bytes)."""
    data = rec.data if isinstance(rec.data, dict) else None
    if data is None:
        return ChallengeInfo(rec=rec, key=None, resolved=None, span=None, digest=None)
    digest = (decisions.subject_digest(rec.kind, data)
              if rec.kind in decisions.SUBJECT_FIELDS else None)
    source = data.get("source")
    if not isinstance(source, dict):
        return ChallengeInfo(rec=rec, key=None, resolved=None, span=None, digest=digest)
    resolved = reader.resolve(records.file_ref(source))
    span = None
    if resolved.state.available:
        located = _probe(resolved, source.get("assertion"))
        span = located[1] if isinstance(located, tuple) else None
    return ChallengeInfo(rec=rec, key=resolved.key, resolved=resolved, span=span, digest=digest)


def use_binding_problems(view, reader, rec: Record) -> list[str]:
    """Why a checked use's bindings no longer hold, [] when they all do (SPEC §5.2.3 "A use is current",
    less the status): its challenge exists, is confirmed with an available source and its subject digest
    equals challenge_bind; the finding exists and its fingerprint and file sha256 equal the binding; the
    excerpt at citation.ordinal exists, matches citation (path, range, tag_sha256) and is a verified text
    match (matching.ExcerptMatch.verified; a binary-exempt excerpt never qualifies). Each message names
    what changed and ends with the same prerequisite-aware recovery for the use: `rebind` only when all
    its prerequisites hold, otherwise fix the first prerequisite and validate again (§5.2.5)."""
    data = rec.data if isinstance(rec.data, dict) else None
    rid = rec.id or "this use"
    if rec.kind != "checked-use" or data is None:
        # The record's own file is what a repair means, so the step is the one an installed record's damage
        # names (records.restore_step): the hooks deny an agent any write to it, and git's last commit holds
        # the bytes only where it holds a copy kblam reads as the record the file name gives.
        step = (records.restore_step(rec.path, partial(committed_record, view.cfg))
                if records.in_kind_folder(rec) else "leave it as it is and tell the user")
        return [f"{rid} did not parse as a use, so its bindings cannot hold; {step}"]
    problems: list[str] = []
    problems += _challenge_binding(view, reader, data)
    problems += _finding_binding(view, reader, data)
    if not problems:
        return []                       # no advice to give, so none of the recovery work is worth it
    recovery = _use_recovery(view, reader, rec)
    return [f"{problem} ({recovery})" for problem in problems]


def _rebind(rid: str) -> str:
    """The command that recomputes a use's bindings from the installed records."""
    return REBIND.format(rid=rid)


def use_current(view, reader, rec: Record) -> bool:
    """A current use: status approved and use_binding_problems is []. Only a current use resolves K14."""
    return rec.status == "approved" and not use_binding_problems(view, reader, rec)


def k13(view, reader) -> list[Issue]:
    """Every K13 issue of the view, each with owner = the record's ID ("" for a file that is no record,
    the index and the root) and level from the SPEC §5.2.4 Severity table by the record's status."""
    cfg = view.cfg
    present = present_ids(view)
    claims = _id_claims(view)

    registry_unreadable = False
    registered = None
    issues = []
    if reader.trust_state:
        try:
            registered = registry.read_ids(cfg)
        except ValueError as exc:
            registry_unreadable = True
            reader._k13_write_refusal = str(exc)      # this failed read already supplies the write gate
            issues = [Issue(".kblam/review-ids", 0, "K13", str(exc), "error", "")]
    issues += _layout_issues(view, reader, registered, registry_unreadable)
    issues += _duplicate_issues(view, claims)
    issues += _registry_issues(view, present, registered)
    root = treehash.root_problem(cfg, registered) if reader.trust_state else None
    if root is not None:
        issues.append(Issue("kblam.toml", 0, "K13", root, "error", ""))
    for rec in view.records:
        issues += _record_issues(view, reader, rec)
    return sorted(set(issues), key=lambda i: (i.path, i.line, int(i.code[1:]), i.message))


# --- layout: records, symlinks, the index and the root ------------------------------------------


def _layout_issues(view, reader, registered: set[str] | None,
                   registry_unreadable: bool) -> list[Issue]:
    """Every file under the review root is a record at its canonical path or the generated INDEX.md
    (SPEC §5.2.4 K13), and neither is a symlink. Symlinked paths are reported as such and not again as
    strays: one path, one reason."""
    cfg = view.cfg
    issues = []
    for path in sorted(view.review_files):
        if path == view.review_index_path or path in view.review_symlinks:
            continue
        if _canonical_kind(cfg, path) is None:
            issues.append(Issue(path, 0, "K13",
                                _stray_message(view, reader, path, registered, registry_unreadable),
                                "error", ""))
    for path in sorted(view.review_symlinks):
        issues.append(Issue(
            path, 0, "K13",
            f"a review record and the generated INDEX.md may not be symlinks ({cfg.review_dir}/ holds "
            f"plain files); replace this link with the file itself", "error", ""))
    issues += _index_issues(view, reader)
    return issues


def present_ids(view) -> set[str]:
    """The IDs of the records present at their canonical paths (§5.2.6): what K13 compares the registry
    with, and what the registry is created from (a record write, `validate --record`, `init --update`)."""
    return {rec.id for rec in view.records
            if rec.id is not None and _canonical_kind(view.cfg, rec.path) is not None}


def _canonical_kind(cfg, path: str) -> str | None:
    """The kind a review path names at its canonical location, or None when the path is not
    `<review root>/<kind folder>/<ID>.yaml` with the ID's kind matching the folder (§5.2.2)."""
    try:
        rel = PurePosixPath(path).relative_to(cfg.review_dir)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) != 2 or parts[0] not in FOLDERS:
        return None
    match = records.FILENAME_RE.match(parts[1])
    if match is None or match.group(2) != FOLDERS[parts[0]]:
        return None
    return match.group(2)


def _stray_message(view, reader, path: str, registered: set[str] | None,
                   registry_unreadable: bool) -> str:
    """As K8's: a file that names a record but sits in the wrong place, or a file that is no record.

    A record-named file kblam knows (a canonical copy is present, or the registry holds the ID) is
    restored from git: `kblam put` needs an allocation receipt, and neither moving nor deleting a
    record file is possible (SPEC §5.2.5, §8 items 1-4). A record-named file kblam does not know is
    restaged with the command that writes the receipt; only a file that is no record at all is
    deleted, which the hook allows."""
    cfg = view.cfg
    name = PurePosixPath(path).name
    match = records.FILENAME_RE.match(name)
    if match is None:
        diagnosis = (f"{cfg.review_dir}/ holds only source-challenge, claim-task and checked-use "
                     f"records in their kind's folder and the generated INDEX.md.")
        if registry_unreadable:
            return f"{diagnosis} {WRITE_GATE_FIX}; delete this file"
        return (f"{diagnosis} Record each challenge, task or use with kblam challenge new, kblam task new "
                f"or kblam use review, then delete this file")
    rec_id, kind = match.group(1), match.group(2)
    canonical = f"{cfg.review_dir}/{records.KINDS[kind]}/{name}"
    if canonical in view.review_files or (registered is not None and rec_id in registered):
        return (f"a {decisions.KIND_WORDS[kind]} must sit directly in its kind's folder ({canonical}); "
                f"this ID is a record kblam knows, so restore {cfg.review_dir}/ from git (the record's "
                f"place is its canonical path)")
    diagnosis = (f"a {decisions.KIND_WORDS[kind]} must sit directly in its kind's folder ({canonical}); "
                 f"this ID is not an installed record or a registered ID, so ")
    if _write_refusal(view, reader) is not None:
        return diagnosis + WRITE_GATE_FIX
    return (diagnosis + "restage it with kblam challenge new, kblam task new or kblam use review, each "
            "of which writes the receipt kblam put needs")


def _index_issues(view, reader) -> list[Issue]:
    """The review index (SPEC §5.2.4 K13, §5.2.5), as K7: missing, or not byte-identical to what
    `kblam review index` generates. A review root that does not exist has no index to keep."""
    path = view.review_index_path
    if path in view.review_symlinks:
        return []                                   # the symlink check already reports this path
    if not view.review_files and not view.cfg.review_path.is_dir():
        return []
    if path not in view.review_files:
        message = "INDEX.md is missing; run kblam review index"
        if _write_refusal(view, reader) is not None:
            message = f"INDEX.md is missing; {WRITE_GATE_FIX}"
        return [Issue(path, 0, "K13", message, "error", "")]
    if view.review_files[path] != generate_review_index(view):
        recovery = (WRITE_GATE_FIX if _write_refusal(view, reader) is not None
                    else "Run kblam review index to regenerate it")
        return [Issue(
            path, 0, "K13",
            f"INDEX.md differs from the generated review index; it is never edited by hand. {recovery}",
            "error", "")]
    return []


def _claimed_ids(rec: Record) -> set[str]:
    """The IDs a record file claims: its filename's, and its data's `id` when that is an ID."""
    claimed = {rec.id} if rec.id is not None else set()
    value = rec.data.get("id") if isinstance(rec.data, dict) else None
    if isinstance(value, str) and records.ID_RE.match(value):
        claimed.add(value)
    return claimed


def _id_claims(view) -> dict[str, list[Record]]:
    """The records claiming each ID, shared by validation and the use's write-blocking guard."""
    claims: dict[str, list[Record]] = {}
    for rec in view.records:
        for claimed in _claimed_ids(rec):
            holders = claims.setdefault(claimed, [])
            if rec not in holders:
                holders.append(rec)
    return claims


def _duplicate_issues(view, claims: dict[str, list[Record]]) -> list[Issue]:
    """IDs are unique and each ID matches its file (SPEC §5.2.4 K13): a structure error, every status.
    The step is the one `records.restore_step` gives for the file that causes the duplicate, or, where
    no restore puts that file right, the step that names the files and says to leave them alone."""
    committed = partial(committed_record, view.cfg)
    issues = []
    for rec_id in sorted(claims):
        holders = claims[rec_id]
        if len(holders) < 2:
            continue
        where = ", ".join(rec.path for rec in holders)
        step = _duplicate_step(holders, committed)
        message = (f"the ID {rec_id} is claimed by more than one record file ({where}); each ID names one "
                   f"record, and records are never renamed. {step[:1].upper()}{step[1:]}")
        for rec in holders:
            issues.append(Issue(rec.path, rec.key_line("id"), "K13", message, "error", rec.id or ""))
    return issues


def _duplicate_step(holders: list[Record], committed) -> str:
    """What fixes a duplicate ID: the file whose own bytes no longer read as the record its file name
    gives is the one kblam will not accept, so it gets the step records.restore_step gives (the restore,
    where git's last commit holds a copy kblam reads as that record, in the file's kind folder). Every
    other duplicate is a hand copy, or a file git's last commit already holds as it stands: putting those
    bytes back changes nothing, kblam never renames a record, and the hooks deny removing a record file,
    so no command an agent may run puts it right: the step then names the files it concerns and says to
    leave them as they are and tell the user."""
    strays = [rec for rec in holders if not _reads_as_its_name(rec)]
    if len(strays) == 1 and records.in_kind_folder(strays[0]):
        return records.restore_step(strays[0].path, committed)
    listed = ", ".join(rec.path for rec in holders)
    return (f"no command an agent may run puts {listed} right; leave them as they are and tell the user")


def _reads_as_its_name(rec: Record) -> bool:
    """Whether the record's own bytes read as the record its file name gives: data that parsed and an
    `id` that is the file name's ID. A file that does not is the one a duplicate line's restore is for:
    its `id` line, or the file itself, is the hand damage the ID error already reports."""
    value = rec.data.get("id") if isinstance(rec.data, dict) else None
    return rec.id is not None and value == rec.id


def _registry_issues(view, present: set[str], registered: set[str] | None) -> list[Issue]:
    """Every registered ID is present (SPEC §5.2.6): the message names the root the record is missing
    from, and the path is the canonical path the record should have."""
    issues = []
    for rec_id in sorted((registered or set()) - present):
        folder = records.KINDS.get(rec_id.rsplit("-", 1)[0], "")
        path = f"{view.cfg.review_dir}/{(folder + '/') if folder else ''}{rec_id}.yaml"
        issues.append(Issue(
            path, 0, "K13",
            f"{rec_id} is missing from {view.cfg.review_dir}/; records are never deleted or renamed; "
            + records.restore_step(path, partial(committed_record, view.cfg)),
            "error", rec_id))
    return issues


# --- per-record checks --------------------------------------------------------------------------


def _record_issues(view, reader, rec: Record) -> list[Issue]:
    """The structure of one record file, then the checks its kind and status add. A file that is no
    record, and a record whose data did not parse or whose status is malformed, get structure errors
    only (SPEC §5.2.4 Severity table)."""
    schema = records.schema_issues(rec, staged=False, committed=partial(committed_record, view.cfg))
    issues = [_owned(issue, rec) for issue in schema]
    issues += [_owned(issue, rec) for issue in decisions.decision_issues(rec)]
    issues += _identity_issues(view, rec, schema, trust_state=reader.trust_state)
    if rec.kind not in records.KINDS or not isinstance(rec.data, dict):
        return issues
    status = rec.status
    if status not in records.STATUSES[rec.kind]:
        return issues
    if rec.kind == "source-challenge":
        issues += _challenge_issues(view, reader, rec, status)
    elif rec.kind == "claim-task":
        issues += _task_issues(view, reader, rec, status)
    else:
        issues += _use_issues(view, reader, rec, status)
    return issues


def _identity_issues(view, rec: Record, schema: list[Issue], *, trust_state: bool = True) -> list[Issue]:
    """Valid identity fields against the allocation receipt; absent or unreadable receipts prove nothing."""
    if not trust_state or _canonical_kind(view.cfg, rec.path) is None or not isinstance(rec.data, dict):
        return []
    receipt = receipts.read_allocation(view.cfg, rec.id)
    if receipt is None:
        return []
    fields = ("created", "creator", "proponent") if rec.kind in ("claim-task", "checked-use") else ("created", "creator")
    issues = []
    for field in fields:
        original = receipt.get(field)
        if (field not in rec.data or not isinstance(original, str)
                or any(issue.line == rec.key_line(field) for issue in schema)):
            continue                                # the field's structure error is its one reason
        now = rec.data[field]
        if isinstance(now, date):
            now = now.isoformat()
        if not isinstance(now, str):
            continue                                # no valid identity value to compare
        if now != original:
            issues.append(_error(rec, field,
                                 f"{field} is {now!r}, but it was allocated as {original!r} (id, created, "
                                 f"creator and proponent never change after allocation)"))
    return issues


def identity_recovery(view, rec: Record, *, trust_state: bool = True) -> str | None:
    """Restore a record before suggesting a write that its allocation identity would refuse."""
    if not _identity_issues(view, rec, records.schema_issues(rec, staged=False), trust_state=trust_state):
        return None
    return (f"restore {rec.id} from git (its identity changed after allocation), then run "
            f"kblam validate again")


def _owned(issue: Issue, rec: Record) -> Issue:
    """An issue from records.py or decisions.py, with its owner defaulted to the record's ID."""
    return issue if issue.owner else replace(issue, owner=rec.id or "")


def _error(rec: Record, key: str, message: str) -> Issue:
    """A structure error of one record: error at every status, at the top-level key's line."""
    return Issue(rec.path, rec.key_line(key), "K13", message, "error", rec.id or "")


def _availability_level(kind: str, status: str) -> str | None:
    """The Severity table's "a source, basis or decision-evidence reference stale or unavailable" row:
    warning while open, error when effective, nothing when closed or retired."""
    if status == "open":
        return "warning"
    return "error" if status in records.EFFECTIVE[kind] else None


def _binding_level(kind: str, status: str) -> str | None:
    """The "checked use binding broken" row: a warning while open and while effective, nothing else.
    K14 reports the excerpt the use no longer covers as an error, which is what blocks."""
    if status == "open" or status in records.EFFECTIVE[kind]:
        return "warning"
    return None


def _dangling_level(kind: str, status: str) -> str:
    """The "Dangling link" row: error while open and when effective, a warning when closed or retired."""
    return "error" if _availability_level(kind, status) is not None else "warning"


def _reference_issues(rec: Record, key: str, label: str, ref: FileRef, resolved: Resolved,
                      status: str, *, availability: bool = True, reader=None) -> list[Issue]:
    """One resolved reference (a challenge's source, a basis entry, a decision's evidence entry): a
    structural problem is an error at every status, and a state the record cannot use is the
    availability row. An unusable reference only reports the structural problem. `availability=False`
    leaves the state row out (K15 owns a task's evidence availability). `reader` is the validation's
    source reader, for the paths it refuses to read for trust (SourceReader.refused_for_trust, named in
    `_state_message`)."""
    if resolved.error is not None:
        if isinstance(ref.path, str) and paths.syntax_problem(ref.path) is not None:
            return []                   # records.schema_issues reports a path's syntax itself
        return [_error(rec, key, f"{label}: {resolved.error}")]
    if not availability:
        return []
    level = _availability_level(rec.kind, status)
    if level is None or resolved.state.available:
        return []
    sentence = _state_message(rec, ref, resolved,
                              untrusted=reader.refused_for_trust(ref) if reader is not None else None)
    return [Issue(rec.path, rec.key_line(key), "K13", sentence if label == "source" else f"{label}: {sentence}",
                  level, rec.id or "")]


def _state_message(rec: Record, ref: FileRef, resolved: Resolved, *, untrusted: str | None = None) -> str:
    """What a stale or unavailable reference says (SPEC §5.2.3 Evaluation): never "the source now says".
    Each one names the step the kblam-write skill gives, and the file it puts back: restore the bytes the
    record was written against at the reference's own path (a reference is available again once that file
    holds them, whether the record pins them by a commit or by a snapshot), or retire the record
    (_retire_step) and file a new one. A refused path is not a str, so that one names no file. Where
    validation refuses the reference for trust (`untrusted` names the path it will not read), the restore
    cannot work here whatever the file holds, so only the retire step is named."""
    rid = rec.id or "this record"
    if resolved.state is State.STALE:
        return (f"the source changed since {rid} was written; restore {ref.path} to the bytes {rid} was "
                f"written against, or {_retire_step(rec)}")
    if resolved.message == sources.MESSAGE_MISSING and isinstance(ref.path, str):
        return f"the working file {ref.path} is missing; restore it, or {_retire_step(rec)}"
    where = f" of {ref.path}" if isinstance(ref.path, str) else ""
    if untrusted is not None:
        retire = _retire_step(rec)
        return (f"the pinned version {untrusted} cannot be read here: this check reads no file under "
                f"{STATE_DIR}/, so no restore puts it back. {retire[:1].upper()}{retire[1:]}")
    return f"the pinned version is not present; restore the pinned bytes{where}, or {_retire_step(rec)}"


def _retire_step(rec: Record) -> str:
    """The way out of a reference that can no longer be restored: retire the record, which is the only
    thing that clears the reference (a stale record is never reopened), and file a new one."""
    return f"retire the record and file a new one ({RETIRE.format(rid=rec.id or 'source-challenge-NNNN')})"


def _probe(resolved: Resolved, assertion) -> tuple[int, tuple[int, int]] | str | None:
    """matching.assertion_match on the resolved bytes, or None when there is nothing to probe (bytes the
    record cannot read, or an assertion schema_issues reports). A str is the refusal.

    The text is the bytes decoded as UTF-8 with line endings normalised to LF. A binary source (a NUL
    byte, or not valid UTF-8) cannot be challenged in schema 1 (§5.2.3 Assertion)."""
    if not isinstance(assertion, dict) or resolved.data is None:
        return None
    text, lines = assertion.get("text"), assertion.get("lines")
    if not isinstance(text, str) or not text.strip():
        return None                                 # schema_issues reports a missing or empty text
    if not (isinstance(lines, list) and len(lines) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) for n in lines)
            and 1 <= lines[0] <= lines[1]):
        return None                                 # schema_issues reports a bad line range
    try:
        decoded = resolved.data.decode("utf-8")
    except UnicodeDecodeError:
        return ("the source is binary (it is not valid UTF-8), and schema 1 cannot challenge a binary "
                "source")
    if "\x00" in decoded:
        return ("the source is binary (it holds a NUL byte), and schema 1 cannot challenge a binary "
                "source")
    return matching.assertion_match(normalise_newlines(decoded), text, (lines[0], lines[1]))


# --- source challenges ---------------------------------------------------------------------------


def _challenge_issues(view, reader, rec: Record, status: str) -> list[Issue]:
    data = rec.data
    source = data.get("source") if isinstance(data.get("source"), dict) else None
    info = challenge_info(view, reader, rec)
    issues = []
    if info.resolved is not None and source is not None:
        ref = records.file_ref(source)
        issues += _reference_issues(rec, "source", "source", ref, info.resolved, status, reader=reader)
        located = _probe(info.resolved, source.get("assertion"))
        if isinstance(located, str):
            issues.append(_error(rec, "source", f"assertion: {located}"))
        elif located is not None:
            issues += _assertion_issues(rec, source.get("assertion"), located)
    issues += _basis_issues(view, reader, rec, status, source)
    issues += _decision_evidence_issues(view, reader, rec, status)
    if status in records.EFFECTIVE[rec.kind]:
        issues += _confirmation_issues(view, reader, rec, info, source)
    issues += _finding_links(view, rec, "linked_findings", data.get("linked_findings"), plural=True)
    return issues


def _assertion_issues(rec: Record, assertion, located: tuple[int, tuple[int, int]]) -> list[Issue]:
    """The assertion's sha256 and occurrence against the match in an available source (SPEC §5.2.3
    Assertion). A value schema_issues already rejects is not reported twice."""
    occurrence, _span = located
    issues = []
    recorded = assertion.get("sha256")
    if isinstance(recorded, str) and records.HEX64_RE.fullmatch(recorded):
        expected = hashlib.sha256(assertion["text"].encode("utf-8")).hexdigest()
        if recorded != expected:
            issues.append(_error(rec, "source",
                                 f"assertion.sha256 is {recorded}, but the assertion's text hashes to "
                                 f"{expected}"))
    lines = assertion["lines"]
    recorded_occurrence = assertion.get("occurrence")
    if (isinstance(recorded_occurrence, int) and not isinstance(recorded_occurrence, bool)
            and recorded_occurrence != occurrence):
        issues.append(_error(rec, "source",
                             f"assertion.occurrence is {recorded_occurrence}, but the match within lines "
                             f"{lines[0]}-{lines[1]} is occurrence {occurrence} among all its matches"))
    return issues


def _basis_issues(view, reader, rec: Record, status: str, source: dict | None) -> list[Issue]:
    """Every basis entry, resolved on its own or (on the source's canonical key, without a pin of its
    own) at the source's pin (SPEC §5.2.3 Basis)."""
    entries = rec.data.get("basis")
    if not isinstance(entries, list):
        return []                                   # schema_issues reports a basis that is not a list
    pinned_source = None
    if source is not None and isinstance(source.get("path"), str):
        pinned_source = records.file_ref(source)
    issues = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue                                 # schema_issues reports an entry that is not a mapping
        ref = records.file_ref(entry)
        resolved = reader.resolve(ref, pinned_source=pinned_source)
        issues += _reference_issues(rec, "basis", f"basis[{i}]", ref, resolved, status, reader=reader)
    return issues


def _confirmation_issues(view, reader, rec: Record, info: ChallengeInfo,
                         source: dict | None) -> list[Issue]:
    """What a confirmation adds (SPEC §5.2.3 Confirmation). A source or basis entry the record cannot
    read is reported by the availability row, which is an error at this status. A confirmed challenge
    is never pinned or reopened (§5.2.2 "Status changes", §5.2.5 `challenge pin`), so the provisional
    source message names the sequence that does work: retire the record and write a new challenge."""
    cfg = view.cfg
    rid = rec.id or "this challenge"
    if info.resolved is None or not info.resolved.state.available:
        return []
    issues = []
    if source is None or not records.file_ref(source).pinned:
        path = source.get("path") if isinstance(source, dict) else None
        where = path if isinstance(path, str) else "SOURCE-PATH"
        recovery = (f"Restore it from git, or retire it ({RETIRE.format(rid=rid)}) and write a new "
                    f"challenge (kblam challenge new {where} --lines A-B --by NAME, fill the staged "
                    f"record and kblam put it, which pins the source when its worktree's HEAD holds "
                    f"those bytes; where it does not, pin the installed challenge with kblam challenge "
                    f"pin source-challenge-NNNN --expect D --snapshot PATH)")
        identity = identity_recovery(view, rec, trust_state=reader.trust_state)
        if identity is not None:
            recovery = "R" + identity[1:]
        elif _write_refusal(view, reader) is not None:
            recovery = f"Restore it from git, or {WRITE_GATE_FIX}"
        issues.append(_error(rec, "source",
                             f"{rid} is {rec.status} with a provisional source; a confirmation needs a "
                             f"pinned source, and only an open challenge is pinned (a confirmed "
                             f"challenge's source is fixed from the decision that closes it). {recovery}"))
    basis = [entry for entry in rec.data.get("basis", []) if isinstance(entry, dict)]
    if not any(_is_primary(view, entry) for entry in basis):
        issues.append(_error(rec, "basis",
                             f"{rid} is {rec.status} with no primary support; a confirmation needs a basis "
                             f"entry whose provenance is one of {', '.join(cfg.primary_provenance)} and "
                             f"whose resolved path is outside {cfg.findings_dir}/, {cfg.review_dir}/ and "
                             f"the history folders"))
    roles = {entry.get("role") for entry in basis}
    classification = rec.data.get("classification")
    if classification == "contradicted" and not roles & {"counterevidence", "internal-inconsistency"}:
        issues.append(_error(rec, "basis",
                             f"{rid} is classified contradicted but no basis entry has role "
                             f"counterevidence or internal-inconsistency"))
    elif classification == "wrong_model" and "model-mismatch" not in roles:
        issues.append(_error(rec, "basis",
                             f"{rid} is classified wrong_model but no basis entry has role model-mismatch"))
    return issues


def _is_primary(view, entry: dict) -> bool:
    """A basis entry that is primary support (SPEC §5.2.3 Confirmation): its provenance is in
    `[review] primary_provenance` and its resolved path is outside every protected root."""
    if entry.get("provenance") not in view.cfg.primary_provenance:
        return False
    path = entry.get("path")
    if not isinstance(path, str):
        return False
    try:
        target = paths.resolve(view.cfg, path)
    except paths.PathRefused:
        return False
    return paths.protected(view.cfg, target) is None


# --- claim tasks and checked uses ----------------------------------------------------------------


def _task_issues(view, reader, rec: Record, status: str) -> list[Issue]:
    """A task's dangling finding link and the structure of its decisions' evidence. Its binding mismatch
    and its evidence availability are K15's (SPEC §5.2.4 K15)."""
    issues = _finding_links(view, rec, "finding", rec.data.get("finding"), plural=False)
    issues += _decision_evidence_issues(view, reader, rec, status, availability=False)
    return issues


def _use_issues(view, reader, rec: Record, status: str) -> list[Issue]:
    """A use's dangling links, its citation, and its bindings (SPEC §5.2.4 K13, checked use). A broken
    binding is a warning while the use is open or approved: K14 reports the excerpt it no longer
    covers."""
    issues = _finding_links(view, rec, "finding", rec.data.get("finding"), plural=False)
    challenge_id = rec.data.get("challenge")
    if isinstance(challenge_id, str) and _record(view, challenge_id, "source-challenge") is None:
        issues.append(Issue(rec.path, rec.key_line("challenge"), "K13",
                            f"the challenge {challenge_id} names no record in {view.cfg.review_dir}/",
                            _dangling_level(rec.kind, status), rec.id or ""))
    level = _binding_level(rec.kind, status)
    if level is not None:
        for problem in use_binding_problems(view, reader, rec):
            issues.append(Issue(rec.path, 0, "K13", problem, level, rec.id or ""))
    issues += _decision_evidence_issues(view, reader, rec, status)
    return issues


def _decision_evidence_issues(view, reader, rec: Record, status: str, *,
                              availability: bool = True) -> list[Issue]:
    """Every decision's evidence entries, resolved through the reader (SPEC §5.2.4 K13, decisions):
    a structural problem is an error at every status, while the stale-or-unavailable row belongs to the
    effective decision (the last entry of `decisions`, SPEC §5.2.4 K15). A task reports structure only:
    K15 owns a task's evidence availability."""
    decisions = rec.data.get("decisions", [])
    issues = []
    for i, decision in enumerate(decisions):
        if not isinstance(decision, dict) or not isinstance(decision.get("evidence"), list):
            continue
        effective = availability and i == len(decisions) - 1
        for j, entry in enumerate(decision["evidence"]):
            if not isinstance(entry, dict):
                continue
            ref = records.file_ref(entry)
            resolved = reader.resolve(ref)
            issues += _reference_issues(rec, "decisions", f"decisions[{i}].evidence[{j}]", ref, resolved,
                                        status, availability=effective, reader=reader)
    return issues


def _finding_links(view, rec: Record, key: str, value, *, plural: bool) -> list[Issue]:
    """A `linked_findings` entry or a `finding` that names no finding in the KB (SPEC §5.2.4 K13,
    Dangling link): the level follows the record's status."""
    values = value if plural and isinstance(value, list) else [value]
    known = {finding.file_id for finding in view.findings}
    level = _dangling_level(rec.kind, rec.status)
    issues = []
    for i, item in enumerate(values):
        if not isinstance(item, str) or item in known:
            continue
        name = f"{key}[{i}]" if plural else key
        issues.append(Issue(rec.path, rec.key_line(key), "K13",
                            f"{name} names {item}, which is no finding in {view.cfg.findings_dir}/",
                            level, rec.id or ""))
    return issues


# --- the bindings of a use ----------------------------------------------------------------------


def _record(view, rec_id, kind: str) -> Record | None:
    """The record with this ID and kind, or None. A view holds one record per path only."""
    if not isinstance(rec_id, str):
        return None
    for rec in view.records:
        if rec.id == rec_id and rec.kind == kind:
            return rec
    return None


def _finding(view, finding_id):
    """The parsing finding with this file ID, or None."""
    if not isinstance(finding_id, str):
        return None
    for finding in view.findings:
        if finding.file_id == finding_id:
            return finding
    return None


def _schema_unsupported(rec: Record) -> bool:
    """Whether records cannot check this record's fields at all (SPEC §5.2.2 Values): only an integer
    equal to records.SCHEMA is supported, and records reports an unsupported version alone, so no field
    check vouches for any field's type."""
    data = rec.data if isinstance(rec.data, dict) else {}
    schema = data.get("schema")
    return "schema" in data and not (isinstance(schema, int) and not isinstance(schema, bool)
                                     and schema == records.SCHEMA)


def _citation_malformed(rec: Record, issues: list[Issue]) -> bool:
    """Whether this use's citation is no excerpt reference (SPEC §5.2.3 checked use citation, §5.2.2
    Values): not a mapping, missing a required key, or flagged by records' own checks (a wrong type or
    range, an unknown key), every one of them reported at the `citation` key's line. The mapping guard
    stands before any read of the citation: it holds whatever the schema is, and a supported schema is
    what vouches for the keys' types."""
    data = rec.data if isinstance(rec.data, dict) else {}
    citation = data.get("citation")
    if not isinstance(citation, dict) or any(key not in citation for key in records.CITATION_KEYS):
        return True
    line = rec.key_line("citation")
    return any(issue.line == line for issue in issues)


def _use_blocking_issues(view, reader, rec: Record, *, status: str | None = None) -> list[Issue]:
    """The checked use errors a recovery's write cannot cure (SPEC §5.2.4, Where each rule blocks).

    Schema errors block before fields are trusted. Otherwise project the valid decision the command
    appends: rebind keeps the status; retirement sets stale. The new last decision cures endpoint status
    and bind errors, but the shared validators still see historical transitions, independence, evidence
    structure and duplicate IDs. The writer's candidate factory installs it at the canonical path,
    leaving any misplaced copy in the view: the write itself can create a duplicate. Evidence
    availability is not structure: the command's empty evidence makes the old effective decision
    historical. Broken bindings are warnings, and dangling links are
    restored before rebind or become warnings at stale. No binding/recovery checks recurse here.
    """
    from kblam.review_write import _candidate         # review_write imports k13; use it only at runtime

    schema = records.schema_issues(rec, staged=False)
    if not isinstance(rec.data, dict) or schema:
        return schema
    issues = []
    data = dict(rec.data)
    data["status"] = status if status is not None else rec.status
    by = "kblam-recovery" if data["proponent"] != "kblam-recovery" else "kblam-recovery-2"
    data["decisions"] = [*data["decisions"],
                         {"date": "2000-01-01", "by": by, "status": data["status"],
                          "reason": "check recovery", "evidence": [],
                          "bind": decisions.subject_digest("checked-use", data)}]
    path = f"{view.cfg.review_dir}/{records.KINDS['checked-use']}/{rec.id}.yaml"
    raw = records.dump(data)
    projected = _candidate(view.cfg, view, path, raw)
    candidate = records.parse_record(path, raw)
    issues += [issue for issue in _duplicate_issues(projected, _id_claims(projected))
               if issue.owner == rec.id]
    issues += decisions.decision_issues(candidate)
    issues += _identity_issues(projected, candidate, [], trust_state=reader.trust_state)
    projected_reader = sources.SourceReader(view.cfg, projected, trust_state=reader.trust_state)
    projected_reader.pin_queries = reader.pin_queries   # the same validation: git's answers hold for both
    issues += _decision_evidence_issues(projected, projected_reader, candidate, data["status"],
                                      availability=False)
    return issues


def _write_refusal(view, reader) -> str | None:
    """The writer's global gate, cached on the one reader shared by a validation run."""
    from kblam.writes import mutation_refusal

    if not reader.trust_state:
        return None
    if not hasattr(reader, "_k13_write_refusal"):
        reader._k13_write_refusal = mutation_refusal(view.cfg)
    return reader._k13_write_refusal


def _use_recovery(view, reader, rec: Record) -> str:
    """The first unmet prerequisite, shared by every binding problem of this use (§5.2.5). Structure
    that survives the proposed write overrides its advice. After repairing a prerequisite, validate
    again rather than assume the remaining ones hold."""
    rid = rec.id or "this use"
    if _schema_unsupported(rec):
        return (f"restore {rid} from git (its schema version is not supported), then run kblam "
                f"validate again")
    issues = records.schema_issues(rec, staged=False)
    if _citation_malformed(rec, issues):
        return (f"restore {rid} from git (its citation is not a valid excerpt reference), then run "
                f"kblam validate again")
    recovery, status = ("", rec.status) if issues else _use_binding_recovery(view, reader, rec)
    if _use_blocking_issues(view, reader, rec, status=status):
        return (f"restore {rid} from git (its structure is invalid; kblam validate names the problem), "
                f"then run kblam validate again")
    if _write_refusal(view, reader) is not None:
        return WRITE_GATE_FIX
    return recovery


def _use_binding_recovery(view, reader, rec: Record) -> tuple[str, str | None]:
    """The prerequisite row and its checked use decision's target status; other writes leave its status
    alone."""
    from kblam.review_write import _one_finding       # the writer's exact-one/readable prerequisite
    from kblam.store import StoreError

    data = rec.data
    rid = rec.id or "this use"
    challenge_id = data.get("challenge")
    challenge = _record(view, challenge_id, "source-challenge")
    if challenge is None or records.schema_issues(challenge, staged=False):
        return f"restore {challenge_id} from git, then run kblam validate again", rec.status
    if challenge.status in ("rejected", "stale"):
        return (f"{challenge_id} is {challenge.status}, so the judgement this use bound is gone; "
                f"retire this use ({RETIRE.format(rid=rid)})"), "stale"
    info = challenge_info(view, reader, challenge)
    if challenge.status == "open":
        identity = identity_recovery(view, challenge, trust_state=reader.trust_state)
        if identity is not None:
            return identity, rec.status
        if not info.available:
            return f"restore {challenge_id}'s source, then run kblam validate again", rec.status
        source = challenge.data.get("source")
        if not records.file_ref(source).pinned:
            return (f"pin {challenge_id} (kblam challenge pin {challenge_id} --expect D, adding "
                    f"--snapshot PATH where its worktree's HEAD doesn't hold the source's bytes), "
                    f"confirm it ({CONFIRM.format(rid=challenge_id)}), then run kblam validate again"), rec.status
        return (f"confirm {challenge_id} ({CONFIRM.format(rid=challenge_id)}), then run kblam "
                f"validate again"), rec.status
    if info.confirmed and not info.available:
        return f"restore the version {challenge_id} was judged on, then run kblam validate again", rec.status
    finding_id = data.get("finding")
    try:
        finding = _one_finding(view.cfg, view, finding_id, rid, rec.status, reopen=False)
    except StoreError:
        found = [item for item in view.findings if item.file_id == finding_id]
        if not found:
            return f"restore {finding_id} from git, then run kblam validate again", rec.status
        if len(found) > 1:
            return (f"fix {finding_id} so exactly one file in {view.cfg.findings_dir}/ holds it "
                    f"(kblam validate names them), then run kblam validate again"), rec.status
        return f"fix {finding_id} (kblam validate names the problem), then run kblam validate again", rec.status
    citation = data.get("citation")
    kept = None
    ordinal = citation.get("ordinal")
    if isinstance(ordinal, int) and not isinstance(ordinal, bool) and ordinal >= 1:
        matches = matching.finding_matches(view, reader, finding)
        kept = _rebind_excerpt(matches, ordinal, citation.get("tag_sha256"))
    if kept is None:
        new_use = NEW_USE.format(challenge=challenge_id, finding=finding_id)
        return (f"retire this use ({RETIRE.format(rid=rid)}); if the finding still quotes the assertion, "
                f"make sure kblam validate verifies that excerpt as a text match (fix the excerpt or "
                f"its citation), then stage a new use ({new_use})"), "stale"
    if rec.status == "approved" and (kept.binary or kept.is_hex or not kept.verified):
        return (f"restore the source text excerpt {kept.ordinal} quotes (or fix the excerpt so kblam "
                f"validate verifies it), then run kblam validate again; or retire this use "
                f"({RETIRE.format(rid=rid)})"), "stale"
    return f"run {_rebind(rid)}", rec.status


def _challenge_binding(view, reader, data: dict) -> list[str]:
    """Whether the use's challenge still holds: it exists, is confirmed, its source is available, and its
    subject digest equals challenge_bind (SPEC §5.2.3 "A use is current")."""
    challenge_id = data.get("challenge")
    rec = _record(view, challenge_id, "source-challenge")
    if rec is None:
        return [f"the challenge {challenge_id} is not a record in {view.cfg.review_dir}; a use binds one "
                f"confirmed challenge"]
    if not isinstance(rec.data, dict):
        return [f"the challenge {challenge_id} did not parse, so the use's binding cannot hold"]
    problems = []
    info = challenge_info(view, reader, rec)
    if not info.confirmed:
        problems.append(f"the challenge {challenge_id} is {rec.status}, not confirmed")
    if info.resolved is None or not info.resolved.state.available:
        problems.append(f"the challenge {challenge_id}'s source is not available")
    bound = data.get("challenge_bind")
    if info.digest is not None and isinstance(bound, str) and info.digest != bound:
        problems.append(f"the challenge {challenge_id} changed since this use was bound: its subject "
                        f"digest is {info.digest}, not {bound}")
    return problems


def _finding_binding(view, reader, data: dict) -> list[str]:
    """Whether the use's finding still holds: it exists, and its K3 fingerprint, file bytes and the
    excerpt at citation.ordinal are what the use bound (SPEC §5.2.3 "A use is current")."""
    finding_id = data.get("finding")
    finding = _finding(view, finding_id)
    if finding is None:
        return [f"the finding {finding_id} is not in {view.cfg.findings_dir}/"]
    if not finding.ok:
        return [f"{finding_id}'s file does not parse, so the use's binding cannot hold"]
    problems = []
    print_digest = fingerprint(finding, view.cfg.scope_separator)
    if print_digest != data.get("finding_fingerprint"):
        problems.append(f"{finding_id} changed since this use was bound: its fingerprint is "
                        f"{print_digest}, not {data.get('finding_fingerprint')}")
    digest = sources.sha256_hex(finding.raw)
    if digest != data.get("finding_file_sha256"):
        problems.append(f"{finding_id}'s file bytes changed since this use was bound ({digest} is not "
                        f"{data.get('finding_file_sha256')})")
    problems += _citation_problems(view, reader, finding, data)
    return problems


def _rebind_excerpt(matches, ordinal, tag):
    """The excerpt rebind keeps (§5.2.5): at the cited ordinal while it carries tag_sha256, else the
    only excerpt carrying it. None or several leave rebind refused."""
    at = next((m for m in matches if m.ordinal == ordinal and m.tag_sha256 == tag), None)
    if at is not None:
        return at
    same = [m for m in matches if m.tag_sha256 == tag]
    return same[0] if len(same) == 1 else None


def _citation_problems(view, reader, finding, data: dict) -> list[str]:
    """The excerpt at citation.ordinal as the use binds it: it exists, its path, range and tag_sha256 are
    the cited ones, and it is a verified text match (SPEC §5.2.3 checked use citation)."""
    citation = data.get("citation")
    if not isinstance(citation, dict):
        return ["the citation does not describe an excerpt"]
    ordinal = citation.get("ordinal")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        return ["the citation names no excerpt ordinal"]
    matches = matching.finding_matches(view, reader, finding)
    match = next((m for m in matches if m.ordinal == ordinal), None)
    if match is None:
        return [f"the finding has no excerpt {ordinal} (it has {len(matches)})"]
    problems = []
    if match.path != citation.get("path"):
        problems.append(f"excerpt {ordinal} quotes {match.path}, not the cited {citation.get('path')}")
    cited_range = citation.get("range")
    if not isinstance(cited_range, list) or tuple(cited_range) != tuple(match.range):
        problems.append(f"excerpt {ordinal} covers {_range_text(match.range)}, not the cited "
                        f"{_range_text(cited_range)}")
    if match.tag_sha256 != citation.get("tag_sha256"):
        problems.append(f"excerpt {ordinal} has tag_sha256 {match.tag_sha256}, not the cited "
                        f"{citation.get('tag_sha256')}")
    if match.is_hex:
        problems.append(f"excerpt {ordinal} is a hex byte rendering, which never qualifies as a use")
    elif match.binary:
        problems.append(f"excerpt {ordinal} is binary-exempt, which never qualifies as a use")
    elif not match.verified:
        problems.append(f"excerpt {ordinal} is not a verified text match ({match.problem})")
    return problems


def _range_text(value) -> str:
    """A citation range as a message writes it ("63-65", "0x40"), or the value itself when it is not a
    range of integers."""
    if (isinstance(value, (list, tuple)) and value
            and all(isinstance(n, int) and not isinstance(n, bool) for n in value)):
        return "-".join(str(n) for n in value)
    return repr(value)
