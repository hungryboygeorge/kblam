"""Writes: ID allocation, staging (`new`, `edit`), `put`, `ack` and `index` (SPEC §7, §5.2.4, §5.2.6).

Every locked command goes through `writes.locked`: `new` and `edit` stage only, so they take the lock
without refusing a changed review root; `put`, `ack` and `index` are mutating, so they refuse one and
recover an interrupted write first. `put` blocks on K1-K11 as before, on K14 only for an affected
excerpt the installed finding did not already have affected, and never on K13 or K15 (SPEC §5.2.4
"Where each rule blocks"); the tasks and uses it makes stale, the K14 errors it keeps and the errors it
does not refuse come back in `PutResult` for the CLI to report. Each write goes through `writes.apply`,
which journals a change of more than one file and records the format-2 `tree.hash`.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from kblam import k14, k15, matching, review, writes
from kblam.check import Checker, CheckResult
from kblam.config import Config
from kblam.finding import (
    FILENAME_RE,
    ID_RE,
    Finding,
    fingerprint,
    format_id,
    id_number,
    parse_finding,
    plain_data,
    yaml_rt,
)
from kblam.index import generate_index
from kblam.rules import TOPIC_RE, Issue, dependencies, errors, validate
from kblam.sources import SourceReader, sha256_hex
from kblam.treehash import clean_before_v2
from kblam.view import KBView, load_view


DEP_ENTRY_RE = re.compile(  # `key: value` of one depends_on entry, block or flow style
    r"""(?:"[^"]*"|'[^']*'|[^\s:{},]+)[ \t]*:(?P<gap>[ \t]*)"""
    r"""(?P<value>"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s,{}\[\]#]+)?"""
)


class StoreError(Exception):
    """A request that cannot be carried out; the message says what to do instead."""


@dataclass
class PutResult:
    issues: list[Issue] = field(default_factory=list)            # the errors that refuse the put
    warnings: list[Issue] = field(default_factory=list)          # printed; never refuse (SPEC §5)
    view: KBView | None = None
    finding_id: str | None = None
    target: str | None = None
    removed: list[str] = field(default_factory=list)
    stamped: list[tuple[str, str]] = field(default_factory=list)  # (target ID, fingerprint written)
    suspect: list[str] = field(default_factory=list)              # dependents this put made suspect
    recorded: bool = False                                        # tree.hash advanced (SPEC §8 rule)
    check: CheckResult | None = None                              # the Jev check (SPEC §6), once validation passed
    review: list[review.ReviewItem] = field(default_factory=list) # review items open for this write
    unchecked: review.ReviewItem | None = None                    # set when Jev could not answer everything
    rejected_items: list[review.ReviewItem] = field(default_factory=list)  # recorded by a refused put (§6.4)
    stale: list[str] = field(default_factory=list)                # CT/CU IDs this put makes stale (§5.2.4)
    kept: list[Issue] = field(default_factory=list)               # K14 errors the put leaves in place
    remaining: list[Issue] = field(default_factory=list)          # candidate errors that neither refuse nor
                                                                  # are kept K14 errors

    @property
    def rejected(self) -> bool:
        """A reject verdict fired (SPEC §6.4); findings/ is unchanged."""
        return bool(self.check and self.check.rejected)

    @property
    def ok(self) -> bool:
        """`issues` holds the errors only, so warnings never count here (SPEC §5)."""
        return not self.issues and not self.rejected


@dataclass
class AckResult:
    path: str
    fingerprint: str
    changed: bool
    recorded: bool = False  # tree.hash advanced (the tree.hash rule, SPEC §8)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def display_path(cfg: Config, path: Path) -> str:
    try:
        return path.resolve().relative_to(cfg.repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _ids_in(directory: Path) -> list[tuple[int, Path]]:
    found = []
    if directory.is_dir():
        for path in directory.rglob("F-*.md"):
            match = FILENAME_RE.match(path.name)
            if match:
                found.append((id_number(match.group(1)), path))
    return found


def allocate_id(cfg: Config) -> str:
    """Next ID after every ID used in findings/ or waiting in staging."""
    numbers = [n for n, _ in _ids_in(cfg.findings_path) + _ids_in(cfg.staging_dir)]
    return format_id(max(numbers, default=0) + 1)


def slugify(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    if len(slug) > 60:
        slug = slug[:60].rsplit("-", 1)[0]
    return slug or "finding"


def _yaml_scalar(value: str) -> str:
    yaml = yaml_rt()
    yaml.width = 4096
    buffer = io.StringIO()
    yaml.dump({"v": value}, buffer)
    return buffer.getvalue().strip()[len("v: "):]


def skeleton(cfg: Config, finding_id: str, topic: str, title: str) -> str:
    return (
        "---\n"
        f"id: {finding_id}\n"
        f"title: {_yaml_scalar(title)}\n"
        f"topic: {topic}\n"
        f"label:        # one of: {', '.join(cfg.labels)}\n"
        f"scope: []     # one or more of: {', '.join(cfg.scopes)}\n"
        "evidence: []  # repo-relative paths that exist\n"
        f"verified: {date.today().isoformat()}\n"
        "# optional keys: depends_on: {F-0102: null} (put stamps the fingerprint), anchors: [\"0x1A2B3C\"],\n"
        "#   quantities: [{name: ..., value: ..., unit: ...}], check: \"<command>\"\n"
        "---\n"
        "\n"
        "**Claim.**\n"
    )


def new_finding(cfg: Config, topic: str, title: str) -> Path:
    if not TOPIC_RE.match(topic):
        raise StoreError(f"topic {topic!r} must be a folder name of lowercase letters, digits, '-' or '_'")
    title = title.strip()
    if not title or "\n" in title:
        raise StoreError("title must be a non-empty one-line string")
    with writes.locked(cfg, f"new {topic}", mutating=False):
        finding_id = allocate_id(cfg)
        path = cfg.staging_dir / f"{finding_id}-{slugify(title)}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "xb") as handle:
            handle.write(skeleton(cfg, finding_id, topic, title).encode("utf-8"))
    return path


def edit_base_path(cfg: Config, finding_id: str) -> Path:
    """Where `kblam edit` records the bytes it copied, for put's edit-base guard (SPEC §12 M2)."""
    return cfg.staging_dir / f"{finding_id}.edit-base.json"


def edit_finding(cfg: Config, finding_id: str) -> Path:
    if not ID_RE.match(finding_id):
        raise StoreError(f"{finding_id!r} is not a finding ID like F-0137")
    with writes.locked(cfg, f"edit {finding_id}", mutating=False):
        staged = [p for n, p in _ids_in(cfg.staging_dir) if format_id(n) == finding_id]
        if staged:
            raise StoreError(
                f"{finding_id} is already staged at {display_path(cfg, staged[0])}; edit that file and "
                f"kblam put it, or delete it to start again"
            )
        matches = [f for f in load_view(cfg).findings if f.file_id == finding_id]
        if not matches:
            raise StoreError(f"{finding_id} is not in {cfg.findings_dir}/; use kblam new for a new finding")
        if len(matches) > 1:
            raise StoreError(f"{finding_id} has more than one file in {cfg.findings_dir}/; run kblam validate")
        base = matches[0]
        target = cfg.staging_dir / base.name
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "xb") as handle:
            handle.write(base.raw)
        record = {"id": finding_id, "path": base.path, "sha256": hashlib.sha256(base.raw).hexdigest()}
        atomic_write(edit_base_path(cfg, finding_id), (json.dumps(record, indent=2) + "\n").encode("utf-8"))
    return target


def _check_edit_base(cfg: Config, finding_id: str, current: KBView, replaced: list[str], shown: str) -> None:
    """A put over an existing ID must come from kblam edit of the file as it is now."""
    base = edit_base_path(cfg, finding_id)
    if not base.is_file():
        raise StoreError(
            f"{finding_id} already exists in {cfg.findings_dir}/, and {shown} was not staged by kblam edit. "
            f"To rewrite {finding_id}: kblam edit {finding_id}, change the staged copy, and put that. A new "
            f"finding needs a new ID: kblam new"
        )
    try:
        record = json.loads(base.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreError(f"{display_path(cfg, base)} is unreadable ({exc}); delete it and {shown}, then run "
                         f"kblam edit {finding_id} again") from exc
    if (not isinstance(record, dict) or replaced != [record.get("path")]
            or hashlib.sha256(current.files[replaced[0]]).hexdigest() != record.get("sha256")):
        raise StoreError(
            f"{finding_id} changed since your edit (another put or kblam ack rewrote it); run kblam edit "
            f"{finding_id} again and reapply your change. Move {shown} out of .kblam/staging/ first: edit "
            f"refuses while a copy is staged"
        )


def regenerate_index(cfg: Config) -> KBView:
    """`kblam index`: rewrite INDEX.md from frontmatter and record tree.hash (format 2, SPEC §5.2.6)."""
    with writes.locked(cfg, "index", mutating=True):
        view = load_view(cfg)
        clean = clean_before_v2(cfg, view, bool(view.records))
        generated = generate_index(view)
        changes = [(view.index_path, generated)] if view.files.get(view.index_path) != generated else []
        writes.apply(cfg, "index", changes,
                     registry_ids=writes.registry_after(cfg, _present_record_ids(view), set()),
                     clean_before=clean)
        return load_view(cfg)


def _staged_topic_error(cfg: Config, source: Path, raw: bytes) -> str | None:
    finding = parse_finding(f"{cfg.findings_dir}/{source.name}", raw)
    shown = display_path(cfg, source)
    if finding.parse_errors:
        line, message = finding.parse_errors[0]
        return f"K1 {shown}" + (f":{line}" if line else "") + f": {message}"
    topic = finding.meta.get("topic") if isinstance(finding.meta, dict) else None
    if not isinstance(topic, str) or not TOPIC_RE.match(topic):
        line = finding.key_line("topic") or 1
        return (f"K1 {shown}:{line}: topic {topic!r} must be set to a folder name of lowercase letters, "
                f"digits, '-' or '_'; kblam put files the finding under {cfg.findings_dir}/<topic>/")
    return None


def stamp_dependency(finding: Finding, target_id: str, value: str, shown: str) -> bytes:
    """The finding's bytes with depends_on[target_id] set to `value`; no other byte changes.

    `shown` is the file's path as messages should name it.
    """
    depends = finding.meta["depends_on"]
    line, col = depends.lc.key(target_id)
    text = finding.raw.decode("utf-8")
    lines = text.split("\n")
    file_line = line + 1  # frontmatter line 0 is file line 1, after the opening ---
    entry = DEP_ENTRY_RE.match(lines[file_line], col)
    if entry is None:
        raise StoreError(f"{shown}: cannot find the value of depends_on {target_id}; write the entry as "
                         f"{target_id}: null")
    scalar = _yaml_scalar(value)
    if entry.group("value") is not None:
        start, end = entry.span("value")
    else:
        start = end = entry.end("gap")
        if not entry.group("gap"):
            scalar = " " + scalar
    lines[file_line] = lines[file_line][:start] + scalar + lines[file_line][end:]
    stamped = "\n".join(lines).encode("utf-8")

    check = parse_finding(finding.path, stamped)
    expected = plain_data(finding.meta)
    expected["depends_on"][target_id] = value
    if not check.ok or plain_data(check.meta) != expected or check.body_lines != finding.body_lines:
        raise StoreError(f"{shown}: could not set depends_on {target_id} without changing anything else; "
                         f"write the entry on its own line as {target_id}: null")
    return stamped


def _stamp_nulls(staged: Finding, others: list[Finding], shown: str) -> tuple[Finding, list[tuple[str, str]]]:
    """Stamp each null depends_on value whose target is a readable finding among `others`."""
    by_id: dict[str, list[Finding]] = {}
    for f in others:
        by_id.setdefault(f.file_id, []).append(f)
    stamped = []
    depends = staged.meta.get("depends_on")
    keys = [k for k, v in depends.items() if v is None] if isinstance(depends, dict) else []
    for key in keys:
        targets = by_id.get(key, [])
        if len(targets) != 1 or not targets[0].ok:
            continue  # K1/K2 report it
        value = fingerprint(targets[0])
        staged = parse_finding(staged.path, stamp_dependency(staged, key, value, shown))
        stamped.append((key, value))
    return staged, stamped


def put(cfg: Config, source: Path, *, client_factory=None) -> PutResult:
    """Validate the KB as it would be with `source` in place, run the Jev check, and move it in only
    if both pass.

    Null depends_on fingerprints are stamped first. K3 on other findings does not block: a
    rewrite reports the dependents it left suspect, and validate fails until they are acked.
    Replacing an existing ID needs the record kblam edit wrote, matching the file as it is now.
    A refused write records a rejected item per rejecting verdict (§6.4).
    K1-K11 refuse as before; K13 and K15 never do, and K14 refuses only for an excerpt the installed
    finding did not already have affected (§5.2.4).
    Jev is asked before the lock is taken; under the lock the candidates are recomputed against the
    current tree and only pairs it gained meanwhile are asked (SPEC §12 M5). `client_factory`
    builds the JevClient (tests pass one with a fake transport).
    """
    source = Path(source)
    with Checker(cfg, client_factory) as checker:
        _prefetch(cfg, source, checker)
        with writes.locked(cfg, f"put {source.name}", mutating=True):
            return _put(cfg, source, checker)


def _prefetch(cfg: Config, source: Path, checker: Checker) -> None:
    """Ask Jev what the check of `source` will need, if it would pass validation as the tree is now."""
    if not checker.policy.enabled:
        return
    try:
        result, _current = _prepare(cfg, source)
    except (StoreError, OSError):
        return  # _put reports it under the lock
    if not result.issues:
        checker.prefetch(result.view, _written(result))


def _written(result: PutResult) -> Finding:
    return next(f for f in result.view.findings if f.path == result.target)


def _prepare(cfg: Config, source: Path) -> tuple[PutResult, KBView]:
    """The tree as it would be after the put, validated: (result with view and issues, current tree)."""
    source = source.resolve()
    if not source.is_file():
        raise StoreError(f"{source} is not a file")
    if source.is_relative_to(cfg.findings_path.resolve()):
        raise StoreError(
            f"{display_path(cfg, source)} is already under {cfg.findings_dir}/; put takes a staged copy. "
            f"Run kblam edit <id> and put the staged file"
        )
    match = FILENAME_RE.match(source.name)
    if not match:
        raise StoreError(f"{source.name} must be named F-NNNN-<slug>.md (slug: lowercase letters, digits, '-')")
    finding_id = match.group(1)
    raw = source.read_bytes()
    problem = _staged_topic_error(cfg, source, raw)
    if problem:
        raise StoreError(problem)
    topic = parse_finding(source.name, raw).meta["topic"]
    target = f"{cfg.findings_dir}/{topic}/{source.name}"

    current = load_view(cfg)
    replaced = [f.path for f in current.findings if f.file_id == finding_id]
    if replaced:
        _check_edit_base(cfg, finding_id, current, replaced, display_path(cfg, source))
    others = [f for f in current.findings if f.file_id != finding_id]
    staged, stamped = _stamp_nulls(parse_finding(target, raw), others, display_path(cfg, source))
    files = {p: b for p, b in current.files.items() if p not in replaced}
    files[target] = staged.raw
    # K13-K15 read the review records off the view, so the candidate carries the current ones over.
    view = KBView(cfg=cfg, files=files, display={target: display_path(cfg, source)},
                  review_files=dict(current.review_files), review_symlinks=set(current.review_symlinks))
    view.files[view.index_path] = generate_index(view)

    result = PutResult(view=view, finding_id=finding_id, target=target,
                       removed=[p for p in replaced if p != target], stamped=stamped)
    issues = validate(view, focus=frozenset({finding_id}))
    found = errors(issues)
    # K13 and K15 never refuse a finding put; K3 refuses only this finding's own dependents (its owner
    # is the dependent, not the path K3 is displayed at); K14 refuses below (SPEC §5.2.4).
    result.issues = [i for i in found
                     if i.code not in REVIEW_CODES and (i.code != "K3" or i.owner == finding_id)]
    readers = SourceReader(cfg, current), SourceReader(cfg, view)
    result.issues += _newly_affected(result, current, readers, found)
    result.remaining = [i for i in found if i not in result.issues and i not in result.kept]
    result.stale = _made_stale(result, current, readers)
    result.warnings = [i for i in issues if not i.is_error]
    return result, current


REVIEW_CODES = ("K13", "K14", "K15")  # the rules that refuse through their own scope, not the K1-K11 sweep
RETIRED_STATUSES = ("stale", "withdrawn")  # a put lists the tasks and uses it makes stale, not those already retired

NEW_EXCERPT_MESSAGE = (
    "{challenges} challenges this quoted assertion, and the installed finding does not already have it "
    "affected: a new excerpt cannot quote a confirmed challenge's assertion. Edit the finding to quote the "
    "usable bytes outside its span, or have this use reviewed ({review}).")


def _newly_affected(result: PutResult, current: KBView, readers: tuple[SourceReader, SourceReader],
                    found: list[Issue]) -> list[Issue]:
    """The K14 errors that refuse this put (SPEC §5.2.4 "Where each rule blocks"): the excerpts whose
    (challenge ID, canonical source key, tag_sha256) the installed finding did not already have affected,
    reported at the tag line of the excerpt as K14 reports them. An excerpt is affected whether or not a
    use covers it (SPEC §5.2.4), and K14 reports no error for one a current use covers, so the refusal is
    synthesised from the same wording. The finding's other K14 errors - the excerpts it already had
    affected - go to result.kept; K14 never refuses for those."""
    finding_id, view = result.finding_id, result.view
    reader_installed, reader_candidate = readers
    installed = k14.affected_triples(current, reader_installed, finding_id)
    affected = k14.affected_triples(view, reader_candidate, finding_id)
    errors_here = [i for i in found if i.code == "K14" and i.owner == finding_id]
    by_tag: dict[tuple[str, str], list[str]] = {}
    for challenge, key, tag in affected - installed:
        by_tag.setdefault((key, tag), []).append(challenge)
    if not by_tag:
        result.kept = errors_here
        return []
    written = _written(result)
    refused: list[Issue] = []
    refused_lines: set[int] = set()
    for match in matching.finding_matches(view, reader_candidate, written):
        challenges = by_tag.get((match.key, match.tag_sha256))
        if challenges is None:
            continue
        line = written.body_start_line + match.start
        refused_lines.add(line)
        at_line = [i for i in errors_here if i.line == line]
        refused += at_line or [_new_excerpt_issue(written, finding_id, line, challenge, match.ordinal)
                               for challenge in sorted(challenges)]
    result.kept = [i for i in errors_here if i.line not in refused_lines]
    return refused


def _new_excerpt_issue(written: Finding, finding_id: str, line: int, challenge: str,
                       ordinal: int) -> Issue:
    """K14's refusal for a newly affected excerpt K14 reports no error for (a current use covers it, and a
    use binds an installed finding, so the put that would install the excerpt is refused, SPEC §5.2.4).
    One issue per challenge, so the command each names is the one that would address that challenge."""
    review = k14.USE_REVIEW.format(challenge=challenge, finding=finding_id, ordinal=ordinal)
    return Issue(written.path, line, "K14", NEW_EXCERPT_MESSAGE.format(challenges=challenge, review=review),
                 "error", finding_id)


def _made_stale(result: PutResult, current: KBView, readers: tuple[SourceReader, SourceReader]) -> list[str]:
    """The IDs of the CT and CU records this put makes stale (SPEC §5.2.4 "Where each rule blocks"),
    in ID order: those bound to this finding whose binding held on the current view and no longer holds
    on the candidate. A task or use already stale or withdrawn is not listed."""
    reader_installed, reader_candidate = readers
    stale = []
    for rec in current.records:
        if rec.kind not in ("CT", "CU") or not isinstance(rec.id, str) or rec.status in RETIRED_STATUSES:
            continue
        data = rec.data if isinstance(rec.data, dict) else {}
        if data.get("finding") != result.finding_id:
            continue
        if rec.kind == "CT":
            # task_binding_problems returns the reasons the binding is broken, so [] means it holds
            broke = not k15.task_binding_problems(current, rec) and k15.task_binding_problems(result.view, rec)
        else:
            broke = _finding_binding(current, data) and not _finding_binding(result.view, data)
        if broke:
            stale.append(rec.id)
    return sorted(stale, key=_record_order)


def _finding_binding(view: KBView, data: dict) -> bool:
    """Whether a CU's finding binding holds on this view: the finding exists once, and its K3
    fingerprint and file sha256 are the ones the use bound (SPEC §5.2.3 "A use is current")."""
    found = [f for f in view.findings if f.file_id == data.get("finding") and isinstance(f.meta, dict)]
    if len(found) != 1:
        return False
    return (data.get("finding_fingerprint") == fingerprint(found[0])
            and data.get("finding_file_sha256") == sha256_hex(found[0].raw))


def _record_order(rec_id: str) -> tuple[int, str]:
    """Numeric ID order, as k15 and view.findings use it: CT-0009 before CT-00010."""
    number = rec_id[3:]
    return (int(number) if number.isdigit() else 0, rec_id)


def _present_record_ids(view: KBView) -> set[str]:
    """The IDs of the records in the review root, as they are now (writes.registry_after's `present`)."""
    return {rec.id for rec in view.records if isinstance(rec.id, str)}


def _put(cfg: Config, source: Path, checker: Checker) -> PutResult:
    result, current = _prepare(cfg, source)
    if result.issues:
        return result
    view, finding_id, target = result.view, result.finding_id, result.target
    written = _written(result)
    result.check = checker.check(view, written, "put")
    if result.rejected:
        result.rejected_items = review.record_rejected(cfg, result.check)
        return result

    previous = next((f for f in current.findings if f.file_id == finding_id and f.ok), None)
    if previous is None or fingerprint(previous) != fingerprint(written):
        result.suspect = [d.dependent for d in dependencies(view)
                          if d.target == finding_id and d.state == "suspect"]

    clean = clean_before_v2(cfg, current, bool(current.records))
    # INDEX.md is written only when the put changes it (a body-only edit does not), so the journal lists
    # the files this put really writes, as regenerate_index does (SPEC §5.2.6 "Interrupted writes").
    index = ([(view.index_path, view.files[view.index_path])]
             if current.files.get(view.index_path) != view.files[view.index_path] else [])
    changes = [(target, view.files[target])] + [(old, None) for old in result.removed] + index
    result.recorded = writes.apply(cfg, f"put {source.name}", changes,
                                   registry_ids=writes.registry_after(cfg, _present_record_ids(current), set()),
                                   clean_before=clean)
    edit_base_path(cfg, finding_id).unlink(missing_ok=True)
    source = source.resolve()
    if source.is_relative_to(cfg.staging_dir.resolve()):
        source.unlink()
    recorded, = review.record(cfg, view, [result.check], reject_as_review=False)
    review.close_rejected(cfg, finding_id)
    result.review, result.unchecked = recorded.opened, recorded.unchecked
    return result


def ack(cfg: Config, dependent_id: str, target_id: str) -> AckResult:
    """`kblam ack`: record the target's current fingerprint in the dependent, in place under findings/.

    Only that value changes, so INDEX.md (built from title, label and scope) stays current. The
    dependent's bytes do change, so an edit of it begun before the ack is refused at put.
    """
    for value in (dependent_id, target_id):
        if not ID_RE.match(value):
            raise StoreError(f"{value!r} is not a finding ID like F-0137")
    with writes.locked(cfg, f"ack {dependent_id} {target_id}", mutating=True):
        return _ack(cfg, dependent_id, target_id)


def _ack(cfg: Config, dependent_id: str, target_id: str) -> AckResult:
    view = load_view(cfg)
    matches = [f for f in view.findings if f.file_id == dependent_id]
    if not matches:
        raise StoreError(f"{dependent_id} is not in {cfg.findings_dir}/")
    if len(matches) > 1 or not matches[0].ok:
        raise StoreError(f"{dependent_id} cannot be read as a single finding; run kblam validate")
    dependent = matches[0]
    link = next((d for d in dependencies(view) if d.dependent == dependent_id and d.target == target_id), None)
    if link is None:
        raise StoreError(f"{dependent_id} does not list {target_id} in depends_on, so there is nothing to ack. "
                         f"To add the dependency: kblam edit {dependent_id}, add {target_id}: null under "
                         f"depends_on, and put it")
    if link.state == "missing":
        raise StoreError(f"{target_id} is not a finding in the KB; remove it from {dependent_id}'s depends_on")
    if dependent_id == target_id or link.current is None:
        raise StoreError(f"cannot ack {dependent_id} -> {target_id}; run kblam validate and fix the errors it "
                         f"reports for either finding")
    if link.state == "current":
        return AckResult(dependent.path, link.current, changed=False)

    data = stamp_dependency(dependent, target_id, link.current, dependent.path)
    clean = clean_before_v2(cfg, view, bool(view.records))
    recorded = writes.apply(cfg, f"ack {dependent_id} {target_id}", [(dependent.path, data)],
                            registry_ids=writes.registry_after(cfg, _present_record_ids(view), set()),
                            clean_before=clean)
    return AckResult(dependent.path, link.current, changed=True, recorded=recorded)
