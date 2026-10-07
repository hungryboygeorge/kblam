"""Staging and reading review records (SPEC §5.2.5): `challenge new/edit/show/uses`, `task
new/edit/show`, `use review` and `review list`. Library functions only; cli.py maps them to commands.

Staging writes only `.kblam/review-staging/<ID>.yaml` and receipts under `.kblam/review-receipts/`
(receipts.py); it never touches the review root, the registry or tree.hash. Everything that installs
or changes an installed record (put, decide, rebind, `challenge pin`, `review index`) is
review_write.py's.

Conventions for every function here:
- A refusal raises store.StoreError with a message that names what kblam parsed and the command that
  fixes it (§5.2.5 Diagnostics); cli maps it to exit 1. A bad argument (a malformed ID, a name that is
  not a §5.2.2 name, `--lines` not `A-B` with 1 <= A <= B) is also a StoreError.
- Staging functions run under writes.locked(cfg, "<command>", mutating=False): a journal is recovered,
  a root change is not refused. Read-only functions (show, uses, list) take no lock.
- Records are serialised with records.dump; field order is records.KEYS[kind]; `created` is today
  (_today(), which tests monkeypatch) as a datetime.date; `schema` is records.SCHEMA; `status` is
  "open"; `decisions` is [].
- The staged file is `cfg.review_staging_dir / f"{rec_id}.yaml"`, created with open(..., "xb").
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from kblam import (decisions, gitpin, k13, k14, k15, matching, paths, receipts, records, registry,
                   writes)
from kblam.config import Config
from kblam.finding import ID_RE as FINDING_ID_RE
from kblam.finding import fingerprint, normalise_newlines
from kblam.sources import SourceReader, sha256_hex
from kblam.store import StoreError, display_path, history_names
from kblam.view import load_view


def _today() -> date:
    return date.today()


def allocate_record_id(cfg: Config, prefix: str) -> str:
    """The next ID of kind `prefix` ("SC", "CT" or "CU"), four digits or more: one above the highest
    number of that prefix among the record files in the review root (any file whose name matches
    records.FILENAME_RE, in any folder under the root), the staged files in `.kblam/review-staging/`,
    the registry (registry.read_ids; a ValueError becomes StoreError), the allocation receipts in
    `.kblam/review-receipts/` (an abandoned draft keeps its receipt, and receipts are never rewritten),
    and the record files in the git history of the review root on any ref (store.history_names; without
    git it adds nothing). Numbering is per prefix: SC-0001 and CT-0001 coexist. Call it under the lock."""
    if prefix not in records.KINDS:
        raise StoreError(f"{prefix!r} is not a record kind (SC, CT or CU)")
    numbers = []
    for text in _allocated_ids(cfg):
        match = records.ID_RE.fullmatch(text)
        if match is not None and match.group(1) == prefix:
            numbers.append(int(text[3:]))
    return f"{prefix}-{max(numbers, default=0) + 1:04d}"


def _allocated_ids(cfg: Config):
    """Every ID that already holds a number: the record files of the review root, the staged files, the
    registry's entries, the receipts' file names (an abandoned draft keeps its number) and the record
    files the review root's git history holds (a record that was committed and removed keeps its)."""
    for directory in (cfg.review_path, cfg.review_staging_dir):
        for path in _files(directory):
            match = records.FILENAME_RE.match(path.name)
            if match:
                yield match.group(1)
    for name in history_names(cfg, cfg.review_dir):
        match = records.FILENAME_RE.match(name)
        if match:
            yield match.group(1)
    for path in _files(cfg.review_receipts_dir):
        yield path.name.split(".", 1)[0]
    try:
        registered = registry.read_ids(cfg)
    except ValueError as exc:
        raise StoreError(str(exc)) from exc
    yield from sorted(registered or set())


def _files(directory: Path) -> list[Path]:
    """Every file at any depth under `directory`, sorted; [] when it does not exist."""
    return sorted(path for path in directory.rglob("*") if path.is_file()) if directory.is_dir() else []


# --- challenges ---------------------------------------------------------------------------------


def challenge_new(cfg: Config, source_path: str, lines: tuple[int, int], by: str) -> Path:
    """`kblam challenge new <source-path> --lines A-B --by NAME`: stage SC-NNNN.yaml and return its path.

    Refuses: a path paths.syntax_problem refuses; a path that does not resolve inside the repository;
    a resolved target under a protected root (paths.protected: findings/, the review root, .kblam/,
    history_dirs: "never a source"); a directory; a missing file; a binary source (a NUL byte, or not
    UTF-8: "a binary source cannot be challenged in schema 1"); lines outside the source's LF text
    (matching.line_starts; same wording as matching's out-of-range message); an assertion that
    matching.assertion_match finds more than once within the lines (ambiguous).

    Writes the record: `source` = {path as given with `\\` turned to `/` and a leading `./` dropped,
    sha256 of the working file's raw bytes, repo/commit/blob from gitpin.auto_pin(cfg, path, sha256)
    or all null, snapshot null, assertion: {lines: [A, B], text: lines A-B of the LF text joined
    without a final newline, sha256: sha256 of that text's UTF-8, occurrence: from assertion_match}};
    `proposition` "", `scope` [], `classification` "", `basis` [], `usable` "", `limits` "",
    `linked_findings` []. Then the allocation receipt (receipts.write_allocation):
    {"id", "created" (ISO string), "creator", "source": the six reference keys as written,
     "captured": {"lines": [A, B], "text": the captured text}}.
    review_write.put_record checks a first put against exactly this receipt.
    """
    first, last = _line_range(lines)
    _check_name(by, "--by")
    with writes.locked(cfg, "challenge new", mutating=False):
        raw = _read_source(cfg, source_path)
        text = _challengeable_text(raw)
        captured, occurrence = _capture(text, (first, last))
        digest = sha256_hex(raw)
        pin = gitpin.auto_pin(cfg, source_path, digest)
        rec_id = allocate_record_id(cfg, "SC")
        today = _today()
        source = {
            "path": _as_written(source_path),
            "sha256": digest,
            "repo": pin.repo if pin is not None else None,
            "commit": pin.commit if pin is not None else None,
            "blob": pin.blob if pin is not None else None,
            "snapshot": None,
            "assertion": {"lines": [first, last], "text": captured,
                          "sha256": sha256_hex(captured.encode("utf-8")), "occurrence": occurrence},
        }
        data = {"schema": records.SCHEMA, "id": rec_id, "created": today, "creator": by,
                "status": "open", "source": source, "proposition": "", "scope": [],
                "classification": "", "basis": [], "usable": "", "limits": "",
                "linked_findings": [], "decisions": []}
        staged = _stage(cfg, "SC", rec_id, data)
        receipts.write_allocation(cfg, rec_id, {
            "id": rec_id, "created": today.isoformat(), "creator": by,
            "source": {key: source[key] for key in records.REF_KEYS},
            "captured": {"lines": [first, last], "text": captured}})
    return staged


def challenge_edit(cfg: Config, rec_id: str) -> Path:
    """`kblam challenge edit SC-…`: stage a byte-for-byte copy of the installed SC and write its edit-base
    receipt (receipts.write_edit_base with the sha256 of the installed bytes). Refuses: not an SC ID; no
    installed record (`<review root>/challenges/<ID>.yaml`); a record that does not parse; an `id` that
    differs from the file name's ID (nothing is staged: kblam validate names the step that fixes the
    installed file); a status other than open ("SC-0001 is <status>; only an open challenge can be
    edited"); a staged copy already present (name it, and say to edit that copy and put it, or delete it
    to start again)."""
    with writes.locked(cfg, f"challenge edit {rec_id}", mutating=False):
        return _edit(cfg, "SC", rec_id)


def challenge_show(cfg: Config, rec_id: str) -> str:
    """`kblam challenge show SC-…`: the text to print (final newline included), read from the installed
    record with one sources.SourceReader over load_view(cfg). Lines, in this order: the ID and status;
    "subject digest: <64 hex>" (decisions.subject_digest); the source path, its version (the pin's blob,
    or the snapshot path, or "provisional") and its state (sources.State value, plus the resolver's
    message when not available); the assertion lines and text; the proposition, scope and
    classification; each basis entry (path, state, locator, role, provenance); usable; limits; linked
    findings; each decision (date, by, status, reason, evidence paths, bind prefix of 12 hex). A
    malformed record is refused (StoreError naming the file and saying to run kblam validate)."""
    _check_id(rec_id, "SC")
    rec = _installed(cfg, "SC", rec_id)
    view = load_view(cfg)
    reader = SourceReader(cfg, view)
    info = k13.challenge_info(view, reader, rec)
    data = rec.data
    source = _mapping(data.get("source"))
    assertion = _mapping(source.get("assertion"))

    out = [f"{rec.id} {rec.status}",
           f"subject digest: {info.digest or ''}",
           f"source: {_text(source.get('path'))}",
           f"version: {_version_text(source)}",
           f"state: {_state_text(info.resolved)}",
           f"assertion: lines {_range_text(assertion.get('lines'))}"]
    text = assertion.get("text")
    if isinstance(text, str) and text:
        out += [f"  {line}" for line in text.split("\n")]
    _field(out, "proposition", data.get("proposition"))
    out.append(f"scope: {_joined(data.get('scope'))}")
    _field(out, "classification", data.get("classification"))
    out.append("basis:")
    out += _basis_lines(reader, data.get("basis"), source)
    _field(out, "usable", data.get("usable"))
    _field(out, "limits", data.get("limits"))
    out.append(f"linked findings: {_joined(data.get('linked_findings'))}")
    out.append("decisions:")
    out += _decision_lines(data.get("decisions"))
    return "\n".join(out) + "\n"


def challenge_uses(cfg: Config, rec_id: str) -> str:
    """`kblam challenge uses SC-…`: one line per k14.challenge_relations entry (the text to print, final
    newline included): the finding ID and path:line, the excerpt ordinal (for an excerpt), the relation
    and level in words (error, warning, or "covered by CU-0003"), and the command. When the challenge
    is not confirmed, one line saying so ("SC-0001 is open; only a confirmed challenge affects
    findings"). Refuses an unknown or malformed record."""
    _check_id(rec_id, "SC")
    rec = _installed(cfg, "SC", rec_id)
    if rec.status != "confirmed":
        return f"{rec_id} is {rec.status}; only a confirmed challenge affects findings\n"
    view = load_view(cfg)
    reader = SourceReader(cfg, view)
    relations = k14.challenge_relations(view, reader, rec_id)
    lines = [_relation_line(relation) for relation in relations]
    if not lines:
        lines = [f"no finding excerpt or reference relates to {rec_id}"]
    return "\n".join(lines) + "\n"


# --- tasks --------------------------------------------------------------------------------------


def task_new(cfg: Config, finding_id: str, kind: str, by: str, proponent: str) -> Path:
    """`kblam task new F-… --kind replication|confirmation --by NAME --proponent NAME`: stage CT-NNNN.yaml
    bound to the finding and return its path.

    Refuses: a malformed finding ID; kind not in records.TASK_KINDS; a finding absent from findings/,
    present more than once, or not parsing (finding.ok false: "run kblam validate"). Writes `kind`,
    `finding`, `claim_fingerprint` (finding.fingerprint), `base_file_sha256` (sha256 of the finding
    file's raw bytes), blank `question` "", `method` "", `outcomes` {supports: "", refutes: "",
    inconclusive: ""}, `controls` [], `stop` "", `expected_evidence` []. Allocation receipt:
    {"id", "created", "creator", "proponent", "kind", "finding", "claim_fingerprint",
     "base_file_sha256"}.
    """
    _check_finding_id(finding_id)
    if kind not in records.TASK_KINDS:
        raise StoreError(f"--kind {kind!r} is not one of {', '.join(records.TASK_KINDS)}")
    _check_name(by, "--by")
    _check_name(proponent, "--proponent")
    with writes.locked(cfg, "task new", mutating=False):
        view = load_view(cfg)
        finding = _finding_once(cfg, view, finding_id)
        rec_id = allocate_record_id(cfg, "CT")
        today = _today()
        printed = fingerprint(finding, cfg.scope_separator)
        digest = sha256_hex(finding.raw)
        data = {"schema": records.SCHEMA, "id": rec_id, "created": today, "creator": by,
                "proponent": proponent, "status": "open", "kind": kind, "finding": finding_id,
                "claim_fingerprint": printed, "base_file_sha256": digest, "question": "", "method": "",
                "outcomes": {key: "" for key in records.OUTCOME_KEYS}, "controls": [], "stop": "",
                "expected_evidence": [], "decisions": []}
        staged = _stage(cfg, "CT", rec_id, data)
        receipts.write_allocation(cfg, rec_id, {
            "id": rec_id, "created": today.isoformat(), "creator": by, "proponent": proponent,
            "kind": kind, "finding": finding_id, "claim_fingerprint": printed,
            "base_file_sha256": digest})
    return staged


def task_edit(cfg: Config, rec_id: str) -> Path:
    """`kblam task edit CT-…`: as challenge_edit, for an open task under `<review root>/tasks/`."""
    with writes.locked(cfg, f"task edit {rec_id}", mutating=False):
        return _edit(cfg, "CT", rec_id)


def task_show(cfg: Config, rec_id: str) -> str:
    """`kblam task show CT-…`: ID, status, "subject digest: <64 hex>", kind, finding, proponent, the
    binding and whether it still holds (k15.task_binding_problems: each problem printed), question,
    method, outcomes, controls, stop, expected evidence, and the decisions as for challenge_show."""
    _check_id(rec_id, "CT")
    rec = _installed(cfg, "CT", rec_id)
    view = load_view(cfg)
    data = rec.data

    out = [f"{rec.id} {rec.status}",
           f"subject digest: {decisions.subject_digest(rec.kind, data)}",
           f"kind: {_text(data.get('kind'))}",
           f"finding: {_text(data.get('finding'))}",
           f"proponent: {_text(data.get('proponent'))}"]
    out.append(f"binding: fingerprint {_text(data.get('claim_fingerprint'))}, file sha256 "
               f"{_text(data.get('base_file_sha256'))}")
    problems = k15.task_binding_problems(view, rec)
    if problems:
        out += [f"binding: {problem}" for problem in problems]
    else:
        out.append("binding: current")
    _field(out, "question", data.get("question"))
    _field(out, "method", data.get("method"))
    outcomes = _mapping(data.get("outcomes"))
    for key in records.OUTCOME_KEYS:
        _field(out, f"outcomes.{key}", outcomes.get(key))
    out.append(f"controls: {_joined(data.get('controls'))}")
    _field(out, "stop", data.get("stop"))
    out.append(f"expected evidence: {_joined(data.get('expected_evidence'))}")
    out.append("decisions:")
    out += _decision_lines(data.get("decisions"))
    return "\n".join(out) + "\n"


# --- uses ---------------------------------------------------------------------------------------


def use_review(cfg: Config, challenge_id: str, finding_id: str, ordinal: int, by: str,
               proponent: str) -> Path:
    """`kblam use review SC-… F-… <excerpt-ordinal> --by NAME --proponent NAME`: stage CU-NNNN.yaml and
    return its path.

    Preconditions, each refused with its own message: the challenge is installed, parses and is
    confirmed; its source is available (k13.challenge_info(...).available); the finding is installed
    once and parses; an excerpt exists at `ordinal` (matching.finding_matches, 1-based); it is a
    verified text match (ExcerptMatch.verified; a binary-exempt excerpt never qualifies); and the
    challenge affects it: (challenge ID, match.key, match.tag_sha256) is in
    k14.affected_triples(view, reader, finding_id). One SourceReader for all of it.

    Writes `challenge`, `challenge_bind` (the challenge's subject digest), `finding`,
    `finding_fingerprint`, `finding_file_sha256`, `citation` {ordinal, path: match.path, range:
    list(match.range), tag_sha256: match.tag_sha256}, blank `disposition` "" and `reason` "".
    Allocation receipt: {"id", "created", "creator", "proponent", "challenge", "challenge_bind",
    "finding", "finding_fingerprint", "finding_file_sha256", "citation"}.
    """
    _check_id(challenge_id, "SC")
    _check_finding_id(finding_id)
    if not (isinstance(ordinal, int) and not isinstance(ordinal, bool) and ordinal >= 1):
        raise StoreError(f"excerpt ordinal {ordinal!r} must be an integer >= 1")
    _check_name(by, "--by")
    _check_name(proponent, "--proponent")
    with writes.locked(cfg, "use review", mutating=False):
        view = load_view(cfg)
        reader = SourceReader(cfg, view)
        rec = _installed(cfg, "SC", challenge_id)
        info = k13.challenge_info(view, reader, rec)
        if not info.confirmed:
            raise StoreError(
                f"{challenge_id} is {rec.status}; only a confirmed challenge can be used (kblam review "
                f"decide {challenge_id} --status confirmed --by NAME --reason TEXT --expect D)")
        if not info.available:
            reason = info.resolved.message if info.resolved is not None else "the record has no source"
            path = _text(_mapping(rec.data.get("source")).get("path"))
            raise StoreError(
                f"{challenge_id}'s source is not available ({reason}); restore the version "
                f"{challenge_id} was judged on, or retire it (kblam review decide {challenge_id} "
                f"--status stale --by NAME --reason TEXT --expect D) and write a new challenge "
                f"(kblam challenge new {path} --lines A-B --by NAME)")
        finding = _finding_once(cfg, view, finding_id)
        matches = matching.finding_matches(view, reader, finding)
        match = next((item for item in matches if item.ordinal == ordinal), None)
        if match is None:
            raise StoreError(f"{finding_id} has no excerpt {ordinal}; it has {len(matches)} verbatim "
                             f"tags (kblam use review {challenge_id} {finding_id} <ordinal> --by NAME "
                             f"--proponent NAME)")
        if match.is_hex:
            raise StoreError(f"excerpt {ordinal} of {finding_id} is a hex byte rendering, which never "
                             f"qualifies as a use")
        if match.binary:
            raise StoreError(f"excerpt {ordinal} of {finding_id} is binary-exempt, and a binary-exempt "
                             f"excerpt never qualifies as a use")
        if not match.verified:
            raise StoreError(f"excerpt {ordinal} of {finding_id} is not a verified text match "
                             f"({match.problem}); fix the excerpt or its citation, then run kblam use "
                             f"review again")
        if (challenge_id, match.key, match.tag_sha256) not in k14.affected_triples(view, reader, finding_id):
            raise StoreError(f"{challenge_id} does not affect excerpt {ordinal} of {finding_id} "
                             f"({match.path}:{_range_text(match.range)}); kblam challenge uses "
                             f"{challenge_id} lists what it affects")
        rec_id = allocate_record_id(cfg, "CU")
        today = _today()
        citation = {"ordinal": match.ordinal, "path": match.path, "range": list(match.range),
                    "tag_sha256": match.tag_sha256}
        data = {"schema": records.SCHEMA, "id": rec_id, "created": today, "creator": by,
                "proponent": proponent, "status": "open", "challenge": challenge_id,
                "challenge_bind": info.digest, "finding": finding_id,
                "finding_fingerprint": fingerprint(finding, cfg.scope_separator),
                "finding_file_sha256": sha256_hex(finding.raw), "citation": citation,
                "disposition": "", "reason": "", "decisions": []}
        staged = _stage(cfg, "CU", rec_id, data)
        receipts.write_allocation(cfg, rec_id, {
            "id": rec_id, "created": today.isoformat(), "creator": by, "proponent": proponent,
            "challenge": challenge_id, "challenge_bind": info.digest, "finding": finding_id,
            "finding_fingerprint": data["finding_fingerprint"],
            "finding_file_sha256": data["finding_file_sha256"], "citation": citation})
    return staged


# --- listing ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ListedRecord:
    id: str
    kind: str          # "SC" | "CT" | "CU"
    status: str
    digest: str        # the subject digest, 64 hex
    subject: str       # SC: "<source path>:<A>-<B>"; CT: "<kind> of <finding>"; CU: "<challenge> in
                       # <finding> excerpt <ordinal>"
    current: bool      # SC: source available; CT: task_binding_problems is []; CU: use_binding_problems
                       # is [] (the status is shown separately)


def review_list(cfg: Config, *, only_open: bool = False) -> list[ListedRecord]:
    """`kblam review list [--open]`: every installed, parsed record with a valid ID, in (kind order SC,
    CT, CU; then ID number) order; with only_open, those whose status is open. A record that does not
    parse is skipped (K13 reports it)."""
    view = load_view(cfg)
    reader = SourceReader(cfg, view)
    listed = []
    for rec in view.records:
        if rec.kind not in records.KINDS or not isinstance(rec.data, dict):
            continue
        if rec.id is None or records.ID_RE.fullmatch(rec.id) is None:
            continue
        status = rec.status or ""
        if only_open and status != "open":
            continue
        listed.append(ListedRecord(id=rec.id, kind=rec.kind, status=status,
                                   digest=decisions.subject_digest(rec.kind, rec.data),
                                   subject=_subject(rec), current=_current(view, reader, rec)))
    listed.sort(key=lambda item: (_KIND_ORDER[item.kind], _id_number(item.id), item.id))
    return listed


def format_listed(item: ListedRecord) -> str:
    """One `review list` line: "<ID> <kind word> <status> <digest[:12]> <subject> <current|stale>",
    where the kind word is decisions.KIND_WORDS[kind]."""
    state = "current" if item.current else "stale"
    return (f"{item.id} {decisions.KIND_WORDS[item.kind]} {item.status} {item.digest[:12]} "
            f"{item.subject} {state}")


_KIND_ORDER = {"SC": 0, "CT": 1, "CU": 2}


def _id_number(rec_id: str) -> int:
    number = rec_id[3:]
    return int(number) if number.isdigit() else 0


def _subject(rec) -> str:
    """The subject a `review list` line shows (SPEC §5.2.5): what the record is about, from its own
    fields, as best they can be read."""
    data = rec.data
    if rec.kind == "SC":
        source = _mapping(data.get("source"))
        assertion = _mapping(source.get("assertion"))
        return f"{_text(source.get('path'))}:{_range_text(assertion.get('lines'))}"
    if rec.kind == "CT":
        return f"{_text(data.get('kind'))} of {_text(data.get('finding'))}"
    citation = _mapping(data.get("citation"))
    return (f"{_text(data.get('challenge'))} in {_text(data.get('finding'))} excerpt "
            f"{_number_text(citation.get('ordinal'))}")


def _current(view, reader, rec) -> bool:
    """Whether the record's subject still holds as it was bound: an SC's source is available, a CT's and
    a CU's bindings all hold (the status is shown separately)."""
    if rec.kind == "SC":
        return k13.challenge_info(view, reader, rec).available
    if rec.kind == "CT":
        return not k15.task_binding_problems(view, rec)
    return not k13.use_binding_problems(view, reader, rec)


# --- shared staging, reading and refusal helpers -------------------------------------------------


def _stage(cfg: Config, kind: str, rec_id: str, data: dict) -> Path:
    """Write the staged record as records.dump of `data`, in records.KEYS order: the file is new, so
    open(..., "xb") refuses to overwrite a copy an author is editing."""
    path = cfg.review_staging_dir / f"{rec_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as handle:
        handle.write(records.dump({key: data[key] for key in records.KEYS[kind]}))
    return path


def _edit(cfg: Config, kind: str, rec_id: str) -> Path:
    """`challenge edit` and `task edit`: stage a byte-for-byte copy of an open installed record.

    An installed record whose `id` differs from its file name's ID is refused before anything is staged:
    the copy would carry the same bad `id`, `put` would refuse it (the ID is not a free field), and with
    the copy deleted the author would only stage it again, so the refusal names the check that fixes the
    installed file instead (kblam validate, and the step its line for the file gives)."""
    word = decisions.KIND_WORDS[kind]
    _check_id(rec_id, kind)
    folder = records.KINDS[kind]
    shown = f"{cfg.review_dir}/{folder}/{rec_id}.yaml"
    installed = cfg.review_path / folder / f"{rec_id}.yaml"
    if not installed.is_file():
        raise StoreError(f"{rec_id} is not installed at {shown}; kblam {word} new writes a new one, and "
                         f"kblam review list shows what is there")
    raw = installed.read_bytes()
    rec = records.parse_record(shown, raw)
    if rec.data is None:
        raise StoreError(f"{shown} did not parse ({rec.error}); run kblam validate")
    if rec.data.get("id") != rec_id:
        raise StoreError(
            f"the installed record {shown} has id {rec.data.get('id')!r}, but its file name's ID is "
            f"{rec_id}, so kblam will not stage a copy of it. Run kblam validate and do what its line for "
            f"{shown} says, then run kblam {word} edit {rec_id} again")
    staged = cfg.review_staging_dir / f"{rec_id}.yaml"
    if staged.exists():
        raise StoreError(f"{rec_id} is already staged at {display_path(cfg, staged)}; edit that copy and "
                         f"put it, or delete it to start again")
    if rec.status != "open":
        raise StoreError(f"{rec_id} is {rec.status}; only an open {word} can be edited")
    staged.parent.mkdir(parents=True, exist_ok=True)
    with open(staged, "xb") as handle:
        handle.write(raw)
    receipts.write_edit_base(cfg, rec_id, sha256_hex(raw))
    return staged


def _installed(cfg: Config, kind: str, rec_id: str):
    """The parsed record installed at `<review root>/<kind folder>/<ID>.yaml`, or a StoreError naming the
    file and the command that writes one."""
    shown = f"{cfg.review_dir}/{records.KINDS[kind]}/{rec_id}.yaml"
    path = cfg.review_path / records.KINDS[kind] / f"{rec_id}.yaml"
    if not path.is_file():
        raise StoreError(f"{rec_id} is not installed at {shown}; {_new_command(kind)}")
    rec = records.parse_record(shown, path.read_bytes())
    if rec.data is None:
        raise StoreError(f"{shown} did not parse ({rec.error}); run kblam validate")
    return rec


def _new_command(kind: str) -> str:
    """The §5.2.5 command that allocates a record of `kind`."""
    return {
        "SC": "kblam challenge new <source-path> --lines A-B --by NAME",
        "CT": "kblam task new <finding> --kind replication|confirmation --by NAME --proponent NAME",
        "CU": "kblam use review <challenge> <finding> <ordinal> --by NAME --proponent NAME",
    }[kind]


def _finding_once(cfg: Config, view, finding_id: str):
    """The one parsing finding with this file ID (task new and use review both bind one), or a
    StoreError: absent, present more than once, or not parsing."""
    found = [finding for finding in view.findings if finding.file_id == finding_id]
    if not found:
        raise StoreError(f"{finding_id} is not in {cfg.findings_dir}/; write it with kblam new and "
                         f"kblam put")
    if len(found) > 1:
        raise StoreError(f"{finding_id} is the ID of {len(found)} findings in {cfg.findings_dir}/; run "
                         f"kblam validate")
    if not found[0].ok:
        raise StoreError(f"{finding_id} does not parse; run kblam validate")
    return found[0]


def _read_source(cfg: Config, source_path: str) -> bytes:
    """The working bytes of a challengeable source, or a StoreError: bad syntax, a path outside the
    repository, a protected root, a directory, or a missing file."""
    if not isinstance(source_path, str):
        raise StoreError(f"source path {source_path!r} is not a path")
    problem = paths.syntax_problem(source_path)
    if problem is not None:
        raise StoreError(f"{source_path!r} is not a repository-relative path: {problem}")
    try:
        target = paths.resolve(cfg, source_path)
    except paths.PathRefused as exc:
        raise StoreError(f"{source_path!r} cannot be challenged: {exc}") from exc
    where = paths.protected(cfg, target)
    if where is not None:
        raise StoreError(f"{source_path} lies under {_protected_name(cfg, where, target)}, which is never "
                         f"a source; challenge the document the claim came from")
    if target.is_dir():
        raise StoreError(f"{source_path} is a directory, not a file; challenge the file that holds the "
                         f"assertion")
    if not target.is_file():
        raise StoreError(f"{source_path} does not exist; give a path relative to the repository root")
    return target.read_bytes()


def _protected_name(cfg: Config, where: str, target: Path) -> str:
    """What a refusal names as the protected root `target` lies under: paths.protected gives the class of
    root, and a history path is named as the folder kblam.toml configures, not as the literal word
    "history"."""
    if where == "history":
        for directory in cfg.history_dirs:
            folder = directory.strip("/")
            if folder and _under(target, (cfg.repo_root / folder).resolve(strict=False)):
                return f"{folder}/"
    return {"findings": f"{cfg.findings_dir}/", "review": f"{cfg.review_dir}/", "state": ".kblam/"}.get(
        where, where)


def _under(target: Path, root: Path) -> bool:
    """`target` is `root` or lies under it, case-insensitively on Windows, as paths.paths compares."""
    target_text, root_text = os.path.normcase(str(target)), os.path.normcase(str(root))
    return target_text == root_text or target_text.startswith(root_text.rstrip(os.sep) + os.sep)


def _challengeable_text(raw: bytes) -> str:
    """The source's LF-normalised text, or a StoreError for a binary source: a NUL byte, or not UTF-8."""
    if b"\0" in raw:
        raise StoreError("a binary source cannot be challenged in schema 1")
    try:
        return normalise_newlines(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise StoreError("a binary source cannot be challenged in schema 1") from None


def _capture(text: str, lines: tuple[int, int]) -> tuple[str, int]:
    """(the assertion text, its occurrence) for lines A-B of `text`: the lines joined without a final
    newline, and matching.assertion_match's occurrence among all matches in the whole source. A refused
    location raises StoreError with its own message: lines outside the source (matching's own wording),
    a range that holds no text at all, or an ambiguous match."""
    first, last = lines
    if first < 1 or last < first or last > len(matching.line_starts(text)):
        located = matching.assertion_match(text, "", (first, last))
        raise StoreError(located)       # matching owns the out-of-range wording
    captured = _lines_text(text, first, last)
    if not captured.strip():
        raise StoreError(f"lines {first}-{last} hold no text to challenge")
    located = matching.assertion_match(text, captured, (first, last))
    if isinstance(located, str):
        raise StoreError(located)
    occurrence, _span = located
    return captured, occurrence


def _lines_text(text: str, first: int, last: int) -> str:
    """Lines `first`-`last` of `text` joined with newlines and without a final newline, as
    matching.line_starts and matching.assertion_match bound them; "" when the range is outside it (the
    refusal comes from assertion_match's own wording)."""
    starts = matching.line_starts(text)
    if first < 1 or last < first or last > len(starts):
        return ""
    begin = starts[first - 1]
    end = starts[last] - 1 if last < len(starts) else len(text) - (1 if text.endswith("\n") else 0)
    return text[begin:end]


def _line_range(lines) -> tuple[int, int]:
    """`--lines A-B` as (A, B), 1 <= A <= B; anything else is a StoreError."""
    if (isinstance(lines, (tuple, list)) and len(lines) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) for n in lines)
            and 1 <= lines[0] <= lines[1]):
        return lines[0], lines[1]
    raise StoreError(f"--lines {lines!r} must be A-B with 1 <= A <= B")


def _as_written(source_path: str) -> str:
    """The path as a record keeps it: `\\` turned to `/` and a leading `./` dropped (SPEC §5.2.2)."""
    text = source_path.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _check_id(rec_id, kind: str) -> str:
    if not (isinstance(rec_id, str) and records.ID_RE.fullmatch(rec_id)
            and rec_id.startswith(f"{kind}-")):
        raise StoreError(f"{rec_id!r} is not a {decisions.KIND_WORDS[kind]} ID like {kind}-0001")
    return rec_id


def _check_finding_id(finding_id) -> str:
    if not (isinstance(finding_id, str) and FINDING_ID_RE.fullmatch(finding_id)):
        raise StoreError(f"{finding_id!r} is not a finding ID like F-0137")
    return finding_id


def _check_name(value, what: str) -> str:
    if not (isinstance(value, str) and records.NAME_RE.fullmatch(value)):
        raise StoreError(f"{what} {value!r} is not a name: letters, digits, '.', '_', '@' and '-', "
                         f"starting with a letter or a digit")
    return value


# --- text helpers -------------------------------------------------------------------------------


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _number_text(value) -> str:
    """An integer as text (a citation's `ordinal`); "" when it is missing or of another type."""
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else ""


def _joined(value) -> str:
    """A list of strings as one line, or "none" when it is empty or not a list."""
    if not isinstance(value, list) or not value:
        return "none"
    return ", ".join(str(item) for item in value)


def _range_text(value) -> str:
    """A range as "63-65" or "0x40"'s offset form [N] -> "N"; "" when it is not a list of integers."""
    if (isinstance(value, (list, tuple)) and value
            and all(isinstance(n, int) and not isinstance(n, bool) for n in value)):
        return "-".join(str(n) for n in value)
    return ""


def _field(out: list[str], label: str, value) -> None:
    """`label: value`, with a multi-line value continued indented under the label; a blank value is left
    as an empty `label:` line (only a malformed record has one)."""
    text = _text(value)
    first, _, rest = text.partition("\n")
    out.append(f"{label}: {first}")
    if rest:
        out += [f"  {line}" for line in rest.split("\n")]


def _version_text(source: dict) -> str:
    """A challenge's source version: the pin's blob ID, the snapshot path, or "provisional"."""
    ref = records.file_ref(source)
    if ref.pin is not None:
        return ref.pin.blob
    if isinstance(ref.snapshot, str) and ref.snapshot:
        return ref.snapshot
    return "provisional"


def _state_text(resolved) -> str:
    """A resolved reference's state, with the resolver's message when it is not available."""
    if resolved is None:
        return "unavailable (the record has no source to resolve)"
    text = resolved.state.value
    if not resolved.state.available and resolved.message:
        text += f" ({resolved.message})"
    return text


def _basis_lines(reader, basis, source: dict) -> list[str]:
    """One line per basis entry: its path, state, locator, role and provenance. A basis entry on the
    source's canonical key and without a pin of its own is read at the source's pin (SPEC §5.2.3)."""
    if not isinstance(basis, list) or not basis:
        return ["  none"]
    pinned_source = records.file_ref(source) if isinstance(source.get("path"), str) else None
    out = []
    for entry in basis:
        if not isinstance(entry, dict):
            out.append(f"  {entry!r}")
            continue
        resolved = reader.resolve(records.file_ref(entry), pinned_source=pinned_source)
        out.append(f"  {_text(entry.get('path'))} {_state_text(resolved)}: "
                   f"{_text(entry.get('locator'))} ({_text(entry.get('role'))}, "
                   f"{_text(entry.get('provenance'))})")
    return out


def _decision_lines(entries) -> list[str]:
    """One line per decision: date, by, status, reason, the evidence paths and the bind's 12-hex
    prefix."""
    if not isinstance(entries, list) or not entries:
        return ["  none"]
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            out.append(f"  {entry!r}")
            continue
        line = (f"  {_text(entry.get('date'))} {_text(entry.get('by'))} "
                f"{_text(entry.get('status'))}: {_text(entry.get('reason'))}")
        evidence = entry.get("evidence")
        if isinstance(evidence, list) and evidence:
            paths_ = ", ".join(_text(item.get("path")) for item in evidence if isinstance(item, dict))
            line += f" [evidence: {paths_}]"
        bind = entry.get("bind")
        if isinstance(bind, str) and bind:
            line += f" bind {bind[:12]}"
        out.append(line)
    return out


def _relation_line(relation: k14.Relation) -> str:
    """One `challenge uses` line: the finding and where it is, the excerpt ordinal, the relation and the
    level in words, and the command that addresses it."""
    where = f"{relation.path}:{relation.line}"
    ordinal = f" excerpt {relation.ordinal}" if relation.ordinal is not None else ""
    level = f"covered by {relation.use}" if relation.use else relation.level
    line = f"{relation.finding} {where}{ordinal} {relation.relation}: {level}"
    return f"{line}; {relation.command}" if relation.command else line
