"""Installing and changing review records (SPEC §5.2.4 "Where each rule blocks", §5.2.5, §5.2.6):
`kblam put` of an SC-/CT-/CU- file, `review decide`, `review rebind`, `challenge pin` and
`review index`. Library functions only; cli.py dispatches `kblam put` on the file name (records.
FILENAME_RE to put_record, anything else to store.put) and maps results to exit codes.

Every function here follows one frame:
1. Argument checks that need no tree (ID syntax, a §5.2.2 name for --by, a non-empty --reason,
   --expect syntax, --evidence syntax) raise store.StoreError before the lock.
2. Under writes.locked(cfg, "<command>", mutating=True): load_view(cfg) and one sources.SourceReader;
   the command's preconditions (§5.2.5), each refused with store.StoreError and its SPEC message.
3. The record's new bytes (records.dump of the updated round-trip mapping, so comments survive), then
   the candidate view: the current view with those bytes at `<review root>/<kind folder>/<ID>.yaml` and
   the review index regenerated (review_index.generate_review_index of the candidate).
4. rules.validate(candidate). The errors whose owner is this record's ID refuse the write: return a
   WriteResult with them in `issues` and write nothing. Errors owned by other records or findings do not
   refuse; they are the obligations the command leaves (`remaining`).
5. writes.apply(cfg, command, [(record path, bytes), (review index path, bytes)],
   registry_ids=writes.registry_after(cfg, present IDs, {ID}),
   clean_before=treehash.clean_before_v2(cfg, view, bool(view.records))).
   The review index is included only when its bytes change. put_record then removes the staged file
   (when it lies under .kblam/review-staging/) and the edit-base receipt (receipts.remove_edit_base).

A decision's `date` is today (_today(), which tests monkeypatch), and its `bind` is the subject digest
after the command's other changes (decisions.subject_digest of the new data).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from datetime import date
from functools import partial
from pathlib import Path

from kblam import (decisions, gitpin, k13, k14, k15, matching, paths, receipts, records,
                   review_index, rules, treehash, writes)
from kblam.config import Config
from kblam.finding import fingerprint, normalise_newlines, plain_data
from kblam.gitdir import tracked_file
from kblam.k13 import _canonical_kind                      # K13's one rule for a record's canonical path
from kblam.records import Record
from kblam.rules import Issue
from kblam.sources import SourceReader, sha256_hex
from kblam.store import StoreError, display_path
from kblam.view import KBView, load_view

EXPECT_MIN = 12   # --expect takes the subject digest or a prefix of at least 12 hex digits

HEX_RE = re.compile(r"[0-9a-f]+")
# The messages SPEC §5.2.3 and §5.2.5 quote, byte for byte.
MESSAGE_SOURCE_CHANGED = "the source changed since kblam challenge new; run it again"
MESSAGE_TASK_BOUND = "{finding} changed since kblam task new bound {rec_id} to it; reread it and run " \
                     "kblam task new again"
MESSAGE_NO_REBIND = ("a challenge has no rebind; a changed assertion or judgement is a new challenge")


def _today() -> date:
    return date.today()


@dataclass
class WriteResult:
    rec_id: str
    path: str = ""                                            # the installed record's repo-relative path
    digest: str = ""                                          # the subject digest after the write
    status: str = ""                                          # the status after the write
    issues: list[Issue] = field(default_factory=list)         # errors owned by the record: the write refused
    warnings: list[Issue] = field(default_factory=list)       # every warning of the candidate validation
    remaining: list[Issue] = field(default_factory=list)      # errors owned by others: validate still fails
    newly_affected: list[str] = field(default_factory=list)   # decide confirmed: finding IDs whose excerpts
                                                              # the challenge newly affects (K14)
    recorded: bool = False                                    # tree.hash advanced

    @property
    def ok(self) -> bool:
        return not self.issues


# --- argument parsing ---------------------------------------------------------------------------


def match_expect(rec_id: str, digest: str, expect: str) -> None:
    """--expect (SPEC §5.2.5): `expect` must be lowercase hex of at least EXPECT_MIN digits (else a
    StoreError saying so) and a prefix of `digest`; otherwise StoreError("<ID> changed since you inspected
    it; show it again"), with "show" naming the command: kblam challenge show / task show for SC / CT,
    kblam review list for CU."""
    shown = _show_command(rec_id)
    if not (isinstance(expect, str) and len(expect) >= EXPECT_MIN and HEX_RE.fullmatch(expect)):
        raise StoreError(f"--expect takes the subject digest {shown} printed, or a prefix of at least "
                         f"{EXPECT_MIN} lowercase hex digits, not {expect!r}")
    if not digest.startswith(expect):
        raise StoreError(f"{rec_id} changed since you inspected it; show it again: {shown}")


def parse_evidence(cfg: Config, spec: str) -> dict:
    """One --evidence PROVENANCE:PATH:LOCATOR, split at its first two colons (the locator may contain
    colons), as a decision evidence entry in records.REF_KEYS order plus `locator` and `provenance`.

    Refuses (StoreError): fewer than two colons; an empty part; a provenance not in cfg.provenance
    (name the allowed values); a path paths.syntax_problem or paths.resolve refuses; a directory; a
    missing file. The entry: the path as a record writes it (`\\` to `/`, a leading `./` dropped), sha256 of the working file's raw bytes, repo/commit/blob from
    gitpin.auto_pin(cfg, path, sha256) or all null, snapshot null, locator, provenance. Whether the
    entry is primary is K15's and K13's to judge on the candidate, not this function's."""
    parts = spec.split(":", 2)
    if len(parts) < 3:
        raise StoreError(f"--evidence takes PROVENANCE:PATH:LOCATOR, not {spec!r}; the path and the "
                         f"locator may not contain a colon")
    provenance, path, locator = parts
    for name, value in (("provenance", provenance), ("path", path), ("locator", locator)):
        if not value.strip():
            raise StoreError(f"--evidence {spec!r} has an empty {name}; it takes "
                             f"PROVENANCE:PATH:LOCATOR")
    if provenance not in cfg.provenance:
        raise StoreError(f"--evidence provenance {provenance!r} is not one of {', '.join(cfg.provenance)} "
                         f"([review] provenance)")
    problem = paths.syntax_problem(path)
    if problem:
        raise StoreError(f"--evidence path {path!r}: {problem}")
    path = _written_path(path)                            # the path as the entry writes it (§5.2.2)
    try:
        target = paths.resolve(cfg, path)
    except paths.PathRefused as exc:
        raise StoreError(f"--evidence path {path!r}: {exc}") from exc
    if target.is_dir():
        raise StoreError(f"--evidence path {path!r} is a directory, not a file")
    if not target.is_file():
        raise StoreError(f"--evidence path {path!r} does not exist; cite a file that exists, relative to "
                         f"the repository root")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise StoreError(f"--evidence path {path!r} cannot be read ({exc.strerror or exc})") from exc
    digest = sha256_hex(data)
    pin = gitpin.auto_pin(cfg, path, digest)
    entry = {"path": path, "sha256": digest,
             "repo": pin.repo if pin is not None else None,
             "commit": pin.commit if pin is not None else None,
             "blob": pin.blob if pin is not None else None,
             "snapshot": None}
    entry["locator"] = locator
    entry["provenance"] = provenance
    return entry


# --- put ----------------------------------------------------------------------------------------


def put_record(cfg: Config, staged: Path) -> WriteResult:
    """`kblam put <staged SC-/CT-/CU- file>` (SPEC §5.2.5 "What put accepts").

    The staged name must match records.FILENAME_RE; it parses (records.parse_record) or is refused
    with the parse error. The ID comes from the name, and the data's `id` must equal it.

    First put (no installed record): the allocation receipt (receipts.read_allocation) must exist; the
    staged `id`, `created`, `creator`, `proponent` and bindings must equal the receipt's (for an SC: the
    source reference's six keys; for a CT: kind, finding, claim_fingerprint, base_file_sha256; for a
    CU: challenge, challenge_bind, finding, finding_fingerprint, finding_file_sha256, citation);
    `status` must be open and `decisions` []. Then by kind:
    - SC: the source must still be available with the receipt's sha256, else "the source changed since
      kblam challenge new; run it again". The assertion may be narrowed: `lines` within the receipt's
      captured lines and `text` a non-empty substring of the captured text. assertion.sha256 and
      occurrence, when null, are computed (matching.assertion_match on the source's LF text within
      lines) and written; a non-null wrong value is refused, as is an assertion that does not match
      exactly once within its lines. Each basis entry with null sha256 gets the working file's sha256
      and, when all three pin keys are null, gitpin.auto_pin's pin; a basis entry whose canonical key
      is the source's gets source.sha256 and no pin (§5.2.3 Basis); a given sha256 or pin is verified
      (sources resolve), never replaced.
    - CT: if the finding's fingerprint or file sha256 now differs from the receipt: "<F> changed since
      kblam task new bound <CT> to it; reread it and run kblam task new again".
    - CU: nothing beyond the receipt (a broken binding is a K13 warning; K14 blocks the finding).
    Put over an installed record: an edit-base receipt must exist and equal the sha256 of the installed
    bytes, else "<ID> changed since your edit; run kblam <challenge|task> edit <ID> again"; the
    installed status must be open; every field except the kind's free fields (FREE_FIELDS) must equal
    the installed record's. For an SC, the basis is filled and verified as on a first put (SPEC
    §5.2.5: a staged basis entry may leave sha256 and the pin null), except that an entry equal to an
    installed one is not read again.
    On either put, a basis entry that is new or changed and has a given sha256 must resolve available
    (sources resolve, an on-source entry read at the source's pin), or the put is refused.
    In both cases records.schema_issues(staged=False) errors refuse (blank required fields), returned
    in WriteResult.issues, before the candidate validation. The written bytes are records.dump of the
    staged round-trip mapping with the computed values filled in. A refusal's issues are the record's
    own: it is reported at the staged file the author is editing, not at the canonical path it does not
    occupy yet (_at_staged).
    """
    staged = Path(staged)
    match = records.FILENAME_RE.match(staged.name)
    if match is None:
        raise StoreError(f"{staged.name} must be named SC-NNNN.yaml, CT-NNNN.yaml or CU-NNNN.yaml")
    rec_id, kind = match.group(1), match.group(2)
    if not staged.is_file():
        raise StoreError(f"{display_path(cfg, staged)} is not a file")
    shown = display_path(cfg, staged)
    rec = records.parse_record(f"{cfg.review_dir}/{records.KINDS[kind]}/{staged.name}", staged.read_bytes())
    if rec.error is not None or not isinstance(rec.data, dict):
        raise StoreError(f"{shown}: {rec.error or 'not a YAML mapping'}; fix it and put it again")
    if rec.data.get("id") != rec_id:
        raise StoreError(f"{shown}: id is {rec.data.get('id')!r}, but the file name's ID is {rec_id}; the "
                         f"ID never changes")
    path = _record_path(cfg, kind, rec_id)

    with writes.locked(cfg, f"put {rec_id}", mutating=True):
        view = load_view(cfg)
        reader = SourceReader(cfg, view)
        installed = _installed(view, rec_id)
        if installed is None:
            _first_put(cfg, rec, rec_id, kind, view, reader)
        else:
            _over_put(cfg, rec, rec_id, kind, installed, reader)
        new_bytes = records.dump(rec.meta)
        candidate = _candidate(cfg, view, path, new_bytes)
        result = _land(cfg, f"put {rec_id}", kind, rec_id, view, candidate, path, new_bytes)
        if not result.ok:               # a refusal is the author's to fix in the staged file it read
            result = _at_staged(result, rec, records.parse_record(path, new_bytes), path, shown)
        if result.ok:
            _remove_staged(cfg, staged)
            receipts.remove_edit_base(cfg, rec_id)
    return result


def _at_staged(result: WriteResult, staged: Record, written: Record, path: str,
               shown: str) -> WriteResult:
    """A refused put's own issues, moved to the staged file the author is looking at: the record is not
    at its canonical path yet, so the path it is reported at is the one `kblam challenge new` printed
    (the finding put does the same with `view.display`).

    The issues are the candidate validation's, so their lines are the lines of the bytes kblam would
    write; put fills the computed fields (an assertion's sha256 and occurrence, a basis entry's hash and
    pin) into the round-trip mapping, which can move the lines below them. Each top-level key is mapped
    by name instead, so a line the written record starts a key on is reported at the line the staged file
    starts that key on; a line no key starts is left as the written record has it. `staged` is the record
    parsed from the staged bytes (put's fills included: they change no top-level key's line), `written`
    the record parsed from the bytes about to be written.
    """
    lines = {}
    keys = set(staged.data or ()) | set(records.KEYS.get(written.kind or "", ()))
    for key in keys:
        was, now = staged.key_line(key), written.key_line(key)
        if was and now:
            lines[now] = was

    def moved(issue: Issue) -> Issue:
        if issue.path != path:
            return issue                # an issue about another file keeps that file's path (§5.2.4)
        return replace(issue, path=shown, line=lines.get(issue.line, issue.line))

    result.issues = [moved(issue) for issue in result.issues]
    result.warnings = [moved(issue) for issue in result.warnings]
    return result


FREE_FIELDS = {
    "SC": ("proposition", "scope", "classification", "basis", "usable", "limits", "linked_findings"),
    "CT": ("question", "method", "outcomes", "controls", "stop", "expected_evidence"),
    "CU": ("disposition", "reason"),
}


def _first_put(cfg: Config, rec: Record, rec_id: str, kind: str, view: KBView,
               reader: SourceReader) -> None:
    """The §5.2.5 checks a first put adds: the allocation receipt, and the kind's computed fields."""
    receipt = receipts.read_allocation(cfg, rec_id)
    if receipt is None:
        raise StoreError(
            f"{rec_id} has no allocation receipt in .kblam/review-receipts/; a first put needs the one "
            f"kblam challenge new, kblam task new or kblam use review wrote. Draft a new "
            f"{decisions.KIND_WORDS[kind]} and put that")
    data = rec.data
    _match_receipt(rec_id, kind, data, receipt)
    if data.get("status") != "open" or data.get("decisions") != []:
        raise StoreError(
            f"{rec_id} is a first put, so its status must be open and its decisions empty. Its status and "
            f"decisions change only through kblam review decide: put it open, then decide")
    if kind == "SC":
        _fill_challenge(cfg, rec, rec_id, reader, receipt)
    elif kind == "CT":
        _check_task_fresh(cfg, rec, rec_id, view, receipt)


def _over_put(cfg: Config, rec: Record, rec_id: str, kind: str, installed: Record,
              reader: SourceReader) -> None:
    """The §5.2.5 checks a put over an installed record adds: a current edit base, an open record, every
    field but the kind's free ones equal to the installed record's, and, for a challenge, the basis
    entries a first put would fill in and verify."""
    base = receipts.read_edit_base(cfg, rec_id)
    if base is None or base != sha256_hex(installed.raw):
        raise StoreError(f"{rec_id} changed since your edit; {_edit_hint(rec_id, kind)}")
    if not isinstance(installed.data, dict):
        raise StoreError(f"the installed {rec_id} does not parse; run kblam validate and fix it, then "
                         f"{_edit_hint(rec_id, kind)}")
    if installed.status != "open":
        raise StoreError(f"{rec_id} is {installed.status}; only an open {decisions.KIND_WORDS[kind]} can "
                         f"be edited")
    for key in records.KEYS[kind]:
        if key in FREE_FIELDS[kind]:
            continue
        if rec.data.get(key) != installed.data.get(key):
            if key in ("status", "decisions"):
                raise StoreError(f"{rec_id}: {key} is not a free field and changes only through kblam "
                                 f"review decide or kblam review rebind; put it back as installed")
            raise StoreError(
                f"{rec_id}: {key} is not a free field, so it must be put as installed. Only "
                f"{', '.join(FREE_FIELDS[kind])} change through an edit; {_edit_hint(rec_id, kind)}")
    if kind == "SC":
        source = rec.meta.get("source") if isinstance(rec.meta.get("source"), dict) else None
        if source is not None:
            _fill_basis(cfg, rec_id, rec.meta, source, reader, installed.data.get("basis") or [])


def _edit_hint(rec_id: str, kind: str) -> str:
    """How an author changes a record that is already installed (SPEC §5.2.5): an edit command for a
    challenge or a task, and a new record for a use, which has no edit command. Written to follow a
    semicolon or "then" in the messages above."""
    if kind == "CU":
        return ("a use has no edit command, so a changed use needs a new kblam use review SC-… F-… "
                "<excerpt-ordinal> --by NAME --proponent NAME")
    return f"run kblam {decisions.KIND_WORDS[kind]} edit {rec_id} again"


def _match_receipt(rec_id: str, kind: str, data: dict, receipt: dict) -> None:
    """The staged record against its allocation receipt: id, created, creator, proponent and bindings."""
    for key in ("id", "created", "creator"):
        _same_as_receipt(rec_id, key, data.get(key), receipt.get(key))
    if kind in ("CT", "CU"):
        _same_as_receipt(rec_id, "proponent", data.get("proponent"), receipt.get("proponent"))
    if kind == "SC":
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        recorded = receipt.get("source") if isinstance(receipt.get("source"), dict) else {}
        for key in records.REF_KEYS:
            _same_as_receipt(rec_id, f"source.{key}", source.get(key), recorded.get(key))
    elif kind == "CT":
        for key in ("kind", "finding", "claim_fingerprint", "base_file_sha256"):
            _same_as_receipt(rec_id, key, data.get(key), receipt.get(key))
    else:
        for key in ("challenge", "challenge_bind", "finding", "finding_fingerprint",
                    "finding_file_sha256"):
            _same_as_receipt(rec_id, key, data.get(key), receipt.get(key))
        citation = data.get("citation") if isinstance(data.get("citation"), dict) else {}
        recorded = receipt.get("citation") if isinstance(receipt.get("citation"), dict) else {}
        for key in records.CITATION_KEYS:
            _same_as_receipt(rec_id, f"citation.{key}", citation.get(key), recorded.get(key))


def _same_as_receipt(rec_id: str, key: str, value, expected) -> None:
    """One field the allocation receipt fixes (SPEC §5.2.5): a receipt is never rewritten."""
    if value != expected:
        raise StoreError(
            f"{rec_id}: {key} is {value!r}, but its allocation receipt has {expected!r}. The ID, the "
            f"created date, the creator, the proponent and the bindings are set at allocation: restore "
            f"{key} and put it again, or start again with a new record")


def _fill_challenge(cfg: Config, rec: Record, rec_id: str, reader: SourceReader, receipt: dict) -> None:
    """A first put of an SC: the source is still there, the assertion may be narrowed, and the computed
    assertion values and the basis' null hashes and pins are filled in (SPEC §5.2.3, §5.2.5). The
    computed values go into the round-trip mapping, which is what records.dump writes."""
    meta = rec.meta
    source = meta.get("source") if isinstance(meta.get("source"), dict) else None
    if source is None:
        return                                          # schema_issues reports a source that is missing
    resolved = reader.resolve(records.file_ref(source))
    if not resolved.state.available or resolved.data is None:
        raise StoreError(MESSAGE_SOURCE_CHANGED)
    text = _source_text(resolved.data)
    if text is None:
        raise StoreError("the source is binary (a NUL byte, or not UTF-8), and schema 1 cannot challenge "
                         "a binary source; kblam challenge new refuses it too")
    assertion = source.get("assertion") if isinstance(source.get("assertion"), dict) else None
    if assertion is not None:
        _narrowing(rec_id, assertion, receipt.get("captured"))
        located = _locate(rec_id, text, assertion)
        _fill_assertion(rec_id, assertion, located)
    _fill_basis(cfg, rec_id, meta, source, reader)


def _source_text(data: bytes) -> str | None:
    """The source's LF-normalised text, or None for a binary source (as matching and K13 read it)."""
    if b"\0" in data:
        return None
    try:
        return normalise_newlines(data.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def _narrowing(rec_id: str, assertion: dict, captured) -> None:
    """The assertion may be narrowed before the first put, never widened (SPEC §5.2.3 Capture)."""
    captured = captured if isinstance(captured, dict) else {}
    lines, text = assertion.get("lines"), assertion.get("text")
    recorded_lines, recorded_text = captured.get("lines"), captured.get("text")
    if (isinstance(lines, list) and len(lines) == 2 and isinstance(recorded_lines, list)
            and len(recorded_lines) == 2 and isinstance(lines[0], int) and isinstance(lines[1], int)
            and not _in_range(lines, recorded_lines)):
        raise StoreError(
            f"{rec_id}: the assertion's lines {lines[0]}-{lines[1]} are outside the captured lines "
            f"{recorded_lines[0]}-{recorded_lines[1]}. Before the first put the assertion may be narrowed, "
            f"not widened (kblam challenge edit {rec_id})")
    if isinstance(text, str) and text.strip() and isinstance(recorded_text, str):
        if text not in recorded_text:
            raise StoreError(
                f"{rec_id}: the assertion's text is not part of the lines kblam challenge new captured. "
                f"Before the first put the assertion may be narrowed to a substring of the captured text, "
                f"not replaced (kblam challenge edit {rec_id})")


def _in_range(lines: list, captured: list) -> bool:
    """`lines` = (A, B) lies within `captured` = (A', B')."""
    return captured[0] <= lines[0] <= lines[1] <= captured[1]


def _locate(rec_id: str, text: str, assertion: dict):
    """matching.assertion_match of the assertion in the source's text, or None when the assertion's shape
    is one schema_issues reports (a refusal raises StoreError)."""
    lines, needle = assertion.get("lines"), assertion.get("text")
    if not (isinstance(needle, str) and needle.strip() and isinstance(lines, list) and len(lines) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) for n in lines)
            and 1 <= lines[0] <= lines[1]):
        return None
    located = matching.assertion_match(text, needle, (lines[0], lines[1]))
    if isinstance(located, str):
        raise StoreError(f"{rec_id}: {located}; widen or narrow the assertion's lines and put it again")
    return located


def _fill_assertion(rec_id: str, assertion: dict, located) -> None:
    """The assertion's sha256 and occurrence: computed when null, verified when given (SPEC §5.2.3)."""
    if located is None:
        return
    occurrence, _span = located
    expected = sha256_hex(assertion["text"].encode("utf-8"))
    given = assertion.get("sha256")
    if given is None:
        assertion["sha256"] = expected
    elif given != expected:
        raise StoreError(f"{rec_id}: assertion.sha256 is {given}, but the assertion's text hashes to "
                         f"{expected}; set it to null and put it again, and put writes it")
    given_occurrence = assertion.get("occurrence")
    if given_occurrence is None:
        assertion["occurrence"] = occurrence
    elif given_occurrence != occurrence:
        raise StoreError(f"{rec_id}: assertion.occurrence is {given_occurrence}, but the assertion matches "
                         f"at occurrence {occurrence} in the source; set it to null and put it again, and "
                         f"put writes it")


def _fill_basis(cfg: Config, rec_id: str, meta, source: dict, reader: SourceReader,
                installed_basis=()) -> None:
    """Every basis entry with a null sha256 gets the working file's sha256, and the pin auto_pin gives,
    unless the entry is on the source itself, which gets source.sha256 and no pin (SPEC §5.2.3 Basis).

    A new or changed entry that gives a sha256 must resolve to those bytes (SPEC §5.2.5: a given value is
    verified, never replaced). `installed_basis` is the installed record's basis, as plain data: an entry
    put over an installed record exactly as it stands was verified when it was installed, so it is not
    read again, and a basis that only carries over stays valid when another source is unavailable."""
    entries = meta.get("basis")
    if not isinstance(entries, list):
        return
    known = [entry for entry in installed_basis if isinstance(entry, dict)]
    source_key = _key(cfg, source.get("path"))
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        path = entry["path"]
        on_source = source_key is not None and _key(cfg, path) == source_key
        if isinstance(entry.get("sha256"), str) and plain_data(entry) not in known:
            _verify_basis(cfg, rec_id, index, entry, reader, source)
        if entry.get("sha256") is None:
            if on_source and isinstance(source.get("sha256"), str):
                entry["sha256"] = source["sha256"]
            else:
                working = reader.working(path)
                if working is None:
                    continue                            # a null stays null: schema_issues refuses it
                entry["sha256"] = sha256_hex(working)
        if all(entry.get(key) is None for key in records.PIN_KEYS) and not on_source:
            if isinstance(entry.get("sha256"), str):
                pin = gitpin.auto_pin(cfg, path, entry["sha256"])
                if pin is not None:
                    entry["repo"], entry["commit"], entry["blob"] = pin.repo, pin.commit, pin.blob


def _verify_basis(cfg: Config, rec_id: str, index: int, entry: dict, reader: SourceReader,
                  source: dict) -> None:
    """A basis entry that gives a sha256 resolves to those bytes (SPEC §5.2.5), the way the source's
    reference is resolved: an entry on the source itself is read at the source's pin."""
    resolved = reader.resolve(records.file_ref(entry), pinned_source=records.file_ref(source))
    if resolved.state.available:
        return
    raise StoreError(f"{rec_id}: basis[{index}] {entry.get('path')} does not resolve to the recorded bytes "
                     f"({resolved.message}); correct the sha256, restore those bytes, or set sha256 to "
                     f"null and put writes the working file's")


def _check_task_fresh(cfg: Config, rec: Record, rec_id: str, view: KBView, receipt: dict) -> None:
    """A first put of a CT whose finding changed since task new is refused (SPEC §5.2.5)."""
    finding_id = rec.data.get("finding")
    found = [f for f in view.findings if f.file_id == finding_id]
    if len(found) != 1 or not found[0].ok:
        raise StoreError(f"{finding_id} is not one readable finding in {cfg.findings_dir}/; run kblam "
                         f"validate and fix it, then run kblam task new again")
    finding = found[0]
    if (fingerprint(finding, cfg.scope_separator) != receipt.get("claim_fingerprint")
            or sha256_hex(finding.raw) != receipt.get("base_file_sha256")):
        raise StoreError(MESSAGE_TASK_BOUND.format(finding=finding_id, rec_id=rec_id))


def _remove_staged(cfg: Config, staged: Path) -> None:
    """The staged file, when it is kblam's staging copy (an author's file elsewhere is left alone)."""
    try:
        inside = staged.resolve().is_relative_to(cfg.review_staging_dir.resolve())
    except OSError:
        inside = False
    if inside:
        staged.unlink(missing_ok=True)


# --- decide, rebind, pin ------------------------------------------------------------------------


def decide(cfg: Config, rec_id: str, status: str, by: str, reason: str, expect: str,
           evidence: list[str]) -> WriteResult:
    """`kblam review decide <ID> --status S --by NAME --reason TEXT --expect D [--evidence ...]...`.

    Preconditions: the record is installed and parses; match_expect on its current subject digest;
    a status equal to the current one is refused (keeping the status is rebind's);
    decisions.transition_problem(kind, old status, status) is None; when decisions.needs_independence,
    decisions.independence_problem is None. Appends {date, by, status, reason, evidence: [parse_evidence
    of each], bind} and sets `status`. The kind's closing requirements are then the candidate
    validation's errors owned by the record (K13 SC confirmation, K15 primary evidence). Approving a CU
    is refused unless k13.use_current holds for it on the candidate (list use_binding_problems).
    For `--status confirmed` of an SC, newly_affected is the finding IDs having a triple with this
    challenge's ID in k14.affected_triples on the candidate but not on the current view.
    """
    _check_record_id(rec_id)
    _check_name(by)
    _check_reason(reason)
    entries = [parse_evidence(cfg, spec) for spec in evidence]

    with writes.locked(cfg, f"review decide {rec_id}", mutating=True):
        view = load_view(cfg)
        rec = _existing(view, cfg, rec_id)
        kind = rec.kind
        match_expect(rec_id, decisions.subject_digest(kind, rec.data), expect)
        old = rec.status
        if status == old and kind != "SC" and old != records.RETIRED:
            raise StoreError(f"{rec_id} is already {status}; a decision that keeps the status belongs to "
                             f"kblam review rebind {rec_id} --by NAME --reason TEXT --expect D")
        problem = _transition(kind, old, status)
        if problem:
            raise StoreError(problem)
        if decisions.needs_independence(kind, old, status):
            problem = decisions.independence_problem(kind, rec.data, by)
            if problem:
                raise StoreError(problem)
        new_data = _decide_data(rec, rec.data, status, by, reason, entries)
        path = _record_path(cfg, kind, rec_id)
        new_bytes = _record_bytes(rec, new_data)
        candidate = _candidate(cfg, view, path, new_bytes)
        if status == "approved":
            _check_approved_cu(cfg, candidate, records.parse_record(path, new_bytes), rec_id)
        result = _land(cfg, f"review decide {rec_id}", kind, rec_id, view, candidate, path, new_bytes)
        if result.ok and kind == "SC" and status == "confirmed":
            result.newly_affected = _newly_affected(cfg, view, candidate, rec)
    return result


def _decide_data(rec: Record, base: dict, status: str, by: str, reason: str,
                 entries: list[dict]) -> dict:
    """`base` (the record's data, with a rebind's new bindings already in it) and the decision appended,
    `bind` still null."""
    data = dict(base)
    data["status"] = status
    data["decisions"] = [*(base.get("decisions") or []),
                         {"date": _today(), "by": by, "status": status, "reason": reason,
                          "evidence": entries, "bind": None}]
    return data


def _record_bytes(rec: Record, new_data: dict) -> bytes:
    """records.dump of the round-trip mapping with new_data's changes written into it, so comments survive
    everywhere else. The decision's `bind` is the subject digest after the command's other changes
    (SPEC §5.2.5), so it is computed before the bytes are."""
    decision = new_data["decisions"][-1]
    decision["bind"] = decisions.subject_digest(rec.kind, new_data)
    meta = rec.meta
    for key, value in new_data.items():
        if key == "decisions" or value == rec.data.get(key):
            continue
        meta[key] = value
    if len(new_data["decisions"]) != len(rec.data.get("decisions") or []):
        meta.setdefault("decisions", []).append(decision)
    return records.dump(meta)


def _check_approved_cu(cfg: Config, candidate: KBView, written: Record, rec_id: str) -> None:
    """Approving a use is refused unless it is current once approved (SPEC §5.2.3 "A use is current")."""
    reader = SourceReader(cfg, candidate)
    if k13.use_current(candidate, reader, written):
        return
    problems = k13.use_binding_problems(candidate, reader, written)
    detail = " ".join(problems) if problems else f"{rec_id} is not approved"
    raise StoreError(f"approving {rec_id} needs it to be current: {detail}")


def _newly_affected(cfg: Config, view: KBView, candidate: KBView, rec: Record) -> list[str]:
    """The findings the confirmation newly makes fail K14 (SPEC §5.2.4, §5.2.5): the IDs having an
    affected triple with this challenge's ID in the candidate but not in the current view."""
    before = SourceReader(cfg, view)
    after = SourceReader(cfg, candidate)
    rec_id = rec.id or ""
    found: list[str] = []
    for finding in candidate.findings:
        if not finding.file_id:
            continue
        now = {triple for triple in k14.affected_triples(candidate, after, finding.file_id)
               if triple[0] == rec_id}
        was = {triple for triple in k14.affected_triples(view, before, finding.file_id)
               if triple[0] == rec_id}
        if now - was:
            found.append(finding.file_id)
    return sorted(set(found), key=_finding_order)


def _finding_order(finding_id: str):
    """Finding IDs by number, then text, so F-0002 comes before F-0010."""
    number = finding_id[2:]
    return (int(number) if number.isdigit() else 0, finding_id)


def _rebind_hint(rec_id: str, status: str | None, reopen: bool = False) -> str:
    """The rebind command a diagnostic suggests: the complete one (SPEC §5.2.5), since --by alone is a
    usage error. `status` is the record's status as installed and `reopen` whether that command was the
    reopening one. A rebind that does not reopen keeps the record's status and passes that status's
    closing checks again, so a task kept confirmed or not_reproduced cites its primary evidence once
    more; a reopening rebind sets the record open, which closes nothing, so it carries --reopen and
    needs no evidence."""
    command = k15.REBIND.format(rid=rec_id)
    if reopen and status != "open":
        return command + " --reopen"
    return command + (" --evidence PROVENANCE:PATH:LOCATOR" if status in k15.PRIMARY_EVIDENCE else "")


def rebind(cfg: Config, rec_id: str, by: str, reason: str, expect: str, evidence: list[str], *,
           reopen: bool = False) -> WriteResult:
    """`kblam review rebind <ID> --by NAME --reason TEXT --expect D [--evidence ...]... [--reopen]`.

    Only a CT or CU whose status is not stale (an SC is refused: "a challenge has no rebind; a changed
    assertion or judgement is a new challenge"). match_expect on the digest before the rebind. The
    finding must be installed once and parse. New bindings: CT claim_fingerprint and base_file_sha256
    from the installed finding; CU finding_fingerprint and finding_file_sha256, challenge_bind = the
    challenge's current subject digest (refused unless the challenge is confirmed with an available
    source), and the citation: the excerpt at `ordinal` if its tag_sha256 still equals citation's,
    else the only excerpt with that tag_sha256 (ordinal, path, range updated); none or several: refuse
    and say to stage a new use (kblam use review ...). Then a decision with status open (reopen) or the
    current status, checked as decide checks it (transition, independence; for a CU kept approved,
    current once approved; for a closed CT, its primary evidence given again with --evidence and judged
    by K15 on the candidate).
    """
    _check_record_id(rec_id)
    _check_name(by)
    _check_reason(reason)
    entries = [parse_evidence(cfg, spec) for spec in evidence]

    with writes.locked(cfg, f"review rebind {rec_id}", mutating=True):
        view = load_view(cfg)
        reader = SourceReader(cfg, view)
        rec = _existing(view, cfg, rec_id)
        kind = rec.kind
        if kind == "SC":
            raise StoreError(MESSAGE_NO_REBIND)
        if kind not in decisions.REBINDS:
            raise StoreError(f"{rec_id} is not a task or a use; only they rebind ({decisions.KIND_WORDS})")
        if rec.status == records.RETIRED:
            raise StoreError(f"{rec_id} is stale; a retired record stays as audit data and is never "
                             f"rebound. Draft a new {decisions.KIND_WORDS[kind]} with kblam "
                             f"{decisions.KIND_WORDS[kind]} new")
        match_expect(rec_id, decisions.subject_digest(kind, rec.data), expect)
        rebound = dict(rec.data)
        status = "open" if reopen else rec.status
        if kind == "CT":
            _rebind_task(cfg, view, rebound, rec_id, rec.status, reopen)
        else:
            _rebind_use(cfg, view, reader, rebound, rec_id, rec.status, reopen)
        problem = _transition(kind, rec.status, status)
        if problem:
            raise StoreError(problem)
        if decisions.needs_independence(kind, rec.status, status):
            problem = decisions.independence_problem(kind, rebound, by)
            if problem:
                raise StoreError(problem)
        new_data = _decide_data(rec, rebound, status, by, reason, entries)
        path = _record_path(cfg, kind, rec_id)
        new_bytes = _record_bytes(rec, new_data)
        candidate = _candidate(cfg, view, path, new_bytes)
        if status == "approved":
            _check_approved_cu(cfg, candidate, records.parse_record(path, new_bytes), rec_id)
        result = _land(cfg, f"review rebind {rec_id}", kind, rec_id, view, candidate, path, new_bytes)
    return result


def _rebind_task(cfg: Config, view: KBView, new_data: dict, rec_id: str, status: str | None,
                 reopen: bool) -> None:
    """A task's new binding: the installed finding's fingerprint and file sha256 (SPEC §5.2.5)."""
    finding = _one_finding(cfg, view, new_data.get("finding"), rec_id, status, reopen)
    new_data["claim_fingerprint"] = fingerprint(finding, cfg.scope_separator)
    new_data["base_file_sha256"] = sha256_hex(finding.raw)


def _rebind_use(cfg: Config, view: KBView, reader: SourceReader, new_data: dict, rec_id: str,
                status: str | None, reopen: bool) -> None:
    """A use's new bindings: the confirmed challenge's current subject digest, the installed finding's
    fingerprint and file sha256, and the excerpt that still carries the cited tag_sha256."""
    challenge_id = new_data.get("challenge")
    challenge = _installed(view, challenge_id) if isinstance(challenge_id, str) else None
    if challenge is None or challenge.kind != "SC":
        raise StoreError(f"the challenge {challenge_id} is not a record in {cfg.review_dir}/; restore it "
                         f"from git, then run {_rebind_hint(rec_id, status, reopen)}")
    info = k13.challenge_info(view, reader, challenge)
    if not info.confirmed:
        raise StoreError(f"the challenge {challenge_id} is {challenge.status}, not confirmed; a use binds a "
                         f"confirmed challenge with an available source")
    if not info.available or info.digest is None:
        raise StoreError(f"the challenge {challenge_id}'s source is not available; restore the pinned "
                         f"version or the working file, then run {_rebind_hint(rec_id, status, reopen)}, "
                         f"or stage a new use (kblam use review SC-… F-… <excerpt-ordinal> --by NAME "
                         f"--proponent NAME)")
    finding = _one_finding(cfg, view, new_data.get("finding"), rec_id, status, reopen)
    new_data["challenge_bind"] = info.digest
    new_data["finding_fingerprint"] = fingerprint(finding, cfg.scope_separator)
    new_data["finding_file_sha256"] = sha256_hex(finding.raw)
    new_data["citation"] = _rebind_citation(cfg, view, reader, finding, new_data, rec_id)


def _rebind_citation(cfg: Config, view: KBView, reader: SourceReader, finding, new_data: dict,
                     rec_id: str) -> dict:
    """The citation a rebind keeps: the excerpt at the cited ordinal if it still carries the cited
    tag_sha256, else the only excerpt that does (SPEC §5.2.5)."""
    citation = new_data.get("citation") if isinstance(new_data.get("citation"), dict) else {}
    matches = matching.finding_matches(view, reader, finding)
    ordinal, tag = citation.get("ordinal"), citation.get("tag_sha256")
    at = next((m for m in matches if m.ordinal == ordinal and m.tag_sha256 == tag), None)
    if at is None:
        same = [m for m in matches if m.tag_sha256 == tag]
        if len(same) != 1:
            raise StoreError(
                f"{rec_id}: {finding.file_id} no longer has the excerpt it cites "
                f"({'no excerpt' if not same else f'{len(same)} excerpts'} carry its tag_sha256); rebind "
                f"keeps the excerpt, so stage a new use: kblam use review {new_data.get('challenge')} "
                f"{finding.file_id} <excerpt-ordinal> --by NAME --proponent NAME")
        at = same[0]
    return {"ordinal": at.ordinal, "path": at.path, "range": list(at.range), "tag_sha256": at.tag_sha256}


def _one_finding(cfg: Config, view: KBView, finding_id, rec_id: str, status: str | None,
                 reopen: bool):
    """The single installed, parsing finding a task or use binds, or a StoreError."""
    found = [f for f in view.findings if f.file_id == finding_id]
    if len(found) != 1 or not found[0].ok:
        raise StoreError(f"{finding_id} is not one readable finding in {cfg.findings_dir}/; run kblam "
                         f"validate and fix it, then run {_rebind_hint(rec_id, status, reopen)}")
    return found[0]


def challenge_pin(cfg: Config, rec_id: str, expect: str, snapshot: str | None = None) -> WriteResult:
    """`kblam challenge pin SC-… --expect D [--snapshot PATH]`: only for an open challenge whose source is
    available; match_expect first. Without --snapshot: gitpin.auto_pin(cfg, source.path,
    source.sha256) sets repo, commit and blob, and None is refused (say the HEAD blob does not hold
    exactly these bytes, and that --snapshot pins a copy). With --snapshot: the path must pass
    paths.syntax_problem and resolve to a file whose raw bytes hash to source.sha256, outside every
    source repository (its owning worktree, if any, is the repository root), not under a protected root
    (paths.protected), and not the source itself; it is written to source.snapshot. An already pinned source (Git pin or snapshot) is refused. Only the pin fields
    change; no decision is appended."""
    _check_record_id(rec_id)
    if not records.SC_ID_RE.fullmatch(rec_id):
        raise StoreError(f"{rec_id} is not a challenge ID like SC-0001; only a challenge is pinned")
    if snapshot is not None:
        problem = paths.syntax_problem(snapshot)
        if problem:
            raise StoreError(f"--snapshot {snapshot!r}: {problem}")

    with writes.locked(cfg, f"challenge pin {rec_id}", mutating=True):
        view = load_view(cfg)
        reader = SourceReader(cfg, view)
        rec = _existing(view, cfg, rec_id)
        if rec.kind != "SC":
            raise StoreError(f"{rec_id} is not a challenge; only a challenge is pinned")
        match_expect(rec_id, decisions.subject_digest(rec.kind, rec.data), expect)
        if rec.status != "open":
            raise StoreError(f"{rec_id} is {rec.status}; only an open challenge can be pinned (its source "
                             f"is fixed from the decision that closes it)")
        source = rec.data.get("source") if isinstance(rec.data.get("source"), dict) else None
        if source is None:
            raise StoreError(f"{rec_id} has no source to pin; run kblam validate and fix it")
        if source.get("snapshot") is not None or not all(source.get(key) is None
                                                         for key in records.PIN_KEYS):
            raise StoreError(f"{rec_id}'s source is already pinned; a pin is never replaced. To pin "
                             f"another version, write a new challenge")
        if not reader.resolve(records.file_ref(source)).state.available:
            raise StoreError(MESSAGE_SOURCE_CHANGED)
        source_path, digest = source.get("path"), source.get("sha256")
        if snapshot is None:
            pin = gitpin.auto_pin(cfg, source_path, digest) if isinstance(source_path, str) else None
            if pin is None:
                raise StoreError(
                    f"the blob at {source_path} in its worktree's HEAD does not hold exactly these bytes, "
                    f"so {rec_id}'s source stays provisional. Commit them and pin it again, or pin a copy "
                    f"with --snapshot PATH")
            values = {"repo": pin.repo, "commit": pin.commit, "blob": pin.blob}
        else:
            values = {"snapshot": _written_path(snapshot)}
            _check_snapshot(cfg, rec_id, values["snapshot"], digest, _key(cfg, source_path))
        _set_pin(rec, values)
        new_bytes = records.dump(rec.meta)
        path = _record_path(cfg, "SC", rec_id)
        candidate = _candidate(cfg, view, path, new_bytes)
        result = _land(cfg, f"challenge pin {rec_id}", "SC", rec_id, view, candidate, path, new_bytes)
    return result


def _check_snapshot(cfg: Config, rec_id: str, snapshot: str, digest, source_key: str | None) -> None:
    """--snapshot: a project-owned copy of the source's bytes (SPEC §5.2.2 "Git pins", §5.2.5): a file
    outside every source repository, outside the roots kblam owns, and outside the source itself, whose
    raw bytes hash to source.sha256."""
    try:
        target = paths.resolve(cfg, snapshot)
    except paths.PathRefused as exc:
        raise StoreError(f"--snapshot {snapshot!r}: {exc}") from exc
    if source_key is not None and _key(cfg, snapshot) == source_key:
        raise StoreError(f"--snapshot {snapshot!r} is {rec_id}'s source; a snapshot is a copy of the "
                         f"bytes, not the source itself")
    root = paths.protected(cfg, target)
    if root is not None:
        raise StoreError(f"--snapshot {snapshot!r}: {_owned_root(cfg, root)}; a snapshot is a "
                         f"project-owned copy outside every source repository and outside the roots kblam "
                         f"owns")
    owner = gitpin.owning_worktree(target)
    if owner is not None and os.path.normcase(str(owner)) != os.path.normcase(str(cfg.repo_root.resolve())):
        raise StoreError(f"--snapshot {snapshot!r} is inside the Git worktree {str(owner)}; a snapshot is "
                         f"a project-owned copy outside every source repository")
    if not target.is_file():
        raise StoreError(f"--snapshot {snapshot!r} does not exist; pin a copy of the bytes that hash to "
                         f"{digest}")
    if sha256_hex(target.read_bytes()) != digest:
        raise StoreError(f"--snapshot {snapshot!r} does not hash to {rec_id}'s source.sha256 ({digest}); "
                         f"pin a copy of exactly those bytes")


def _owned_root(cfg: Config, root: str) -> str:
    """What the root paths.protected named is called in a diagnostic."""
    return {"findings": f"{cfg.findings_dir}/ holds the findings",
            "review": f"the review root {cfg.review_dir}/ holds the records",
            "state": ".kblam/ is kblam's state",
            "history": "a history folder holds the KB's older versions"}[root]


def _set_pin(rec: Record, values: dict) -> None:
    """Set only the pin fields on the round-trip mapping, so comments elsewhere survive."""
    source = rec.meta["source"]
    for key, value in values.items():
        source[key] = value


def _written_path(raw: str) -> str:
    """A path as a record writes it (SPEC §5.2.2): `/` separators, no leading `./`."""
    path = raw.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


# --- the review index ---------------------------------------------------------------------------


def regenerate_review_index(cfg: Config) -> bool:
    """`kblam review index`: under writes.locked(cfg, "review index", mutating=True), write
    `<review root>/INDEX.md` from the records present when its bytes differ, through writes.apply
    (registry_ids=writes.registry_after(cfg, present, set())), and return whether tree.hash advanced."""
    with writes.locked(cfg, "review index", mutating=True):
        view = load_view(cfg)
        path = view.review_index_path
        generated = review_index.generate_review_index(view)
        changes = [(path, generated)] if view.review_files.get(path) != generated else []
        return writes.apply(cfg, "review index", changes,
                            registry_ids=writes.registry_after(cfg, _present(view), set()),
                            clean_before=treehash.clean_before_v2(cfg, view, bool(view.records)))


# --- the frame's shared steps --------------------------------------------------------------------


def _land(cfg: Config, command: str, kind: str, rec_id: str, view: KBView, candidate: KBView,
          path: str, new_bytes: bytes) -> WriteResult:
    """Step 4 and 5 of the frame: validate the candidate, refuse on the record's own errors, else write
    the record, the review index (when it changed), the registry and tree.hash."""
    all_issues = rules.validate(candidate)
    written = records.parse_record(path, new_bytes)
    mine = sorted({issue for issue in rules.errors(all_issues) if issue.owner == rec_id}
                  | set(records.schema_issues(written, staged=False, tracked=partial(tracked_file, cfg))),
                  key=_order)
    result = WriteResult(rec_id=rec_id, path=path,
                         digest=decisions.subject_digest(kind, written.data),
                         status=written.status or "",
                         issues=mine,
                         warnings=[issue for issue in all_issues if not issue.is_error])
    if mine:
        return result
    result.remaining = [issue for issue in rules.errors(all_issues) if issue.owner != rec_id]
    index_path = candidate.review_index_path
    generated = candidate.review_files[index_path]
    changes = [(path, new_bytes)]
    if view.review_files.get(index_path) != generated:
        changes.append((index_path, generated))
    result.recorded = writes.apply(
        cfg, command, changes,
        registry_ids=writes.registry_after(cfg, _present(candidate), {rec_id}),
        clean_before=treehash.clean_before_v2(cfg, view, bool(view.records)))
    return result


def _candidate(cfg: Config, view: KBView, path: str, new_bytes: bytes) -> KBView:
    """The view as it would be after the write: the record at its canonical path, the review index
    regenerated, and every other file, symlink and finding as it is."""
    files = dict(view.review_files)
    files[path] = new_bytes
    symlinks = set(view.review_symlinks)
    symlinks.discard(path)
    candidate = KBView(cfg=cfg, files=view.files, display=dict(view.display),
                       review_files=files, review_symlinks=symlinks)
    candidate.review_files[candidate.review_index_path] = review_index.generate_review_index(candidate)
    return candidate


def _present(view: KBView) -> set[str]:
    """The IDs of the records present at their canonical paths, as K13 counts them for the registry."""
    return k13.present_ids(view)


def _installed(view: KBView, rec_id) -> Record | None:
    """The installed record with this ID, at its canonical path when more than one file claims it."""
    if not isinstance(rec_id, str):
        return None
    matches = [rec for rec in view.records if rec.id == rec_id]
    if not matches:
        return None
    canonical = [rec for rec in matches if _canonical_kind(view.cfg, rec.path) is not None]
    return canonical[0] if canonical else matches[0]


def _existing(view: KBView, cfg: Config, rec_id: str) -> Record:
    """The installed, parsing record a decision command acts on, or a StoreError."""
    rec = _installed(view, rec_id)
    if rec is None:
        raise StoreError(f"{rec_id} is not a record in {cfg.review_dir}/; draft it with kblam "
                         f"challenge new, kblam task new or kblam use review, then kblam put it")
    if rec.error is not None or not isinstance(rec.data, dict):
        raise StoreError(f"{rec_id} did not parse ({rec.error or 'not a YAML mapping'}); restore it from "
                         f"git, or run kblam validate to see what it reports")
    return rec


def _record_path(cfg: Config, kind: str, rec_id: str) -> str:
    """A record's canonical path, relative to the repository root."""
    return f"{cfg.review_dir}/{records.KINDS[kind]}/{rec_id}.yaml"


def _key(cfg: Config, raw) -> str | None:
    """paths.canonical_key of a record's value, or None when the value is no usable path."""
    if not isinstance(raw, str):
        return None
    try:
        return paths.canonical_key(cfg, raw)
    except paths.PathRefused:
        return None


def _show_command(rec_id: str) -> str:
    """"show" for --expect's message: the command that prints the record's subject digest."""
    word = decisions.KIND_WORDS.get(rec_id.split("-")[0], "")
    return f"kblam {word} show {rec_id}" if word in ("challenge", "task") else "kblam review list"


def _order(issue: Issue):
    """The order rules.validate sorts in, so the two lists read alike."""
    return (issue.path, issue.line, int(issue.code[1:]) if issue.code[1:].isdigit() else 0, issue.message)


def _transition(kind: str, old: str, new: str) -> str | None:
    """decisions.transition_problem, with a status outside the kind's vocabulary refused first."""
    if new not in records.STATUSES.get(kind, ()):
        raise StoreError(f"--status {new!r} is not a status of a {decisions.KIND_WORDS[kind]}; use one of: "
                         f"{', '.join(records.STATUSES[kind])}")
    return decisions.transition_problem(kind, old, new)


def _check_record_id(rec_id: str) -> None:
    if not (isinstance(rec_id, str) and records.ID_RE.fullmatch(rec_id)):
        raise StoreError(f"{rec_id!r} is not a record ID like SC-0001, CT-0001 or CU-0001")


def _check_name(by: str) -> None:
    if not (isinstance(by, str) and records.NAME_RE.fullmatch(by)):
        raise StoreError(f"--by {by!r} is not a name: a name starts with a letter or digit and holds only "
                         f"letters, digits, '.', '_', '@' and '-'")


def _check_reason(reason: str) -> None:
    if not (isinstance(reason, str) and reason.strip()):
        raise StoreError("--reason must be a non-empty string: a decision is audit data, so it states why")
