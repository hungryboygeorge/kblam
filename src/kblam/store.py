"""Writes: ID allocation, staging (`new`, `edit`), `put`, `ack`, `index`, `rm` and `renumber` (SPEC §7).

Finding puts use the introduced-only K1-K12 validation and K14's newly affected excerpt scope; K13
and K15 remain nonblocking (SPEC §5.2.4). Put, ack and index use writes.locked and writes.apply for
review-root checks and journal recovery. Rm and renumber retain their separate write mechanics.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

from kblam import k14, k15, matching, records, resolutions, review, writes
from kblam.check import Checker, CheckResult, _differ, _format_value, _quantities
from kblam.config import Config
from kblam.jev import Side, jev_settings
from kblam.lock import kb_lock
from kblam.finding import (
    FILENAME_RE,
    ID_IN_TEXT_RE,
    ID_RE,
    Finding,
    fingerprint,
    fingerprint_as,
    format_id,
    id_number,
    parse_finding,
    plain_data,
    yaml_rt,
)
from kblam.index import generate_index
from kblam.rules import TOPIC_RE, Issue, dependencies, errors, validate
from kblam.sources import SourceReader, sha256_hex
from kblam.treehash import clean_before_v2, record_after_write_v2
from kblam.view import KBView, load_view


DEP_ENTRY_RE = re.compile(  # `key: value` of one depends_on entry (or of `id`), block or flow style
    r"""(?P<key>"[^"]*"|'[^']*'|[^\s:{},]+)[ \t]*:(?P<gap>[ \t]*)"""
    r"""(?P<value>"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s,{}\[\]#]+)?"""
)
GIT_TIMEOUT = 60  # seconds for one git command; a slower one counts as git being unable to answer
COMMIT_LINE_RE = re.compile(rb"([0-9a-f]{40,64}) ([0-9a-f]{4,64})")  # `--format=%H %h`
OBJECT_ID_RE = re.compile(rb"[0-9a-f]{40,64}")


class StoreError(Exception):
    """A request that cannot be carried out; the message says what to do instead."""


class GitUnavailable(Exception):
    """git could not answer: it is not installed, the root is not in a repository, or the command failed."""


@dataclass
class PutResult:
    issues: list[Issue] = field(default_factory=list)               # errors that block the put (SPEC §7)
    warnings: list[Issue] = field(default_factory=list)             # existing errors and nonblocking warnings
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
    # SPEC §7 ack (M6.10): the target's claim now, and as it stood at the fingerprint the dependent recorded (the
    # version in git history that has it), so the re-reading has something to read. Set when a value changed.
    claim_now: str | None = None
    claim_then: str | None = None
    then_commit: str | None = None     # short hash of the commit holding that version
    history_note: str | None = None    # why claim_then is missing


@dataclass
class RemoveResult:
    """What `kblam rm` did (SPEC §7)."""
    finding_id: str
    target_id: str
    path: str                                   # the removed file
    title: str
    folder: str | None = None                   # the topic folder the removal left empty, removed with it
    index_path: str = ""
    recorded: bool = False                      # tree.hash advanced (the tree.hash rule, SPEC §8)
    closed: list[review.ReviewItem] = field(default_factory=list)
    staged: list[str] = field(default_factory=list)  # staged copies of the removed finding (kblam edit)


@dataclass
class RenumberResult:
    """What `kblam renumber` did (SPEC §7)."""
    old_id: str
    new_id: str
    old_path: str
    new_path: str
    fingerprint: str                            # the renumbered finding's new fingerprint
    kept: list[str] = field(default_factory=list)   # the files that keep the old ID
    rekeyed: list[tuple[str, str]] = field(default_factory=list)  # (dependent ID, its file)
    mentions: list[str] = field(default_factory=list)  # other mentions of the old ID, for a person to check
    index_path: str = ""
    recorded: bool = False                      # tree.hash advanced (the tree.hash rule, SPEC §8)
    resolutions: int = 0                        # resolutions copied under the new ID (SPEC §6.4)
    stale: list[tuple[str, str]] = field(default_factory=list)  # (CT/CU ID, re-keyed finding it was bound to)


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


def _git(cfg: Config, *args: str, stdin: bytes | None = None) -> bytes:
    """stdout of `git <args>`, run in the repository root; GitUnavailable when git cannot answer."""
    try:
        done = subprocess.run(["git", *args], cwd=cfg.repo_root, input=stdin, capture_output=True,
                              timeout=GIT_TIMEOUT)
    except OSError as exc:
        raise GitUnavailable(f"cannot run git ({exc.strerror or exc})") from None
    except subprocess.TimeoutExpired:
        raise GitUnavailable(f"git {args[0]} took longer than {GIT_TIMEOUT}s") from None
    if done.returncode != 0:
        lines = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitUnavailable(lines[0] if lines else f"git {args[0]} exited with status {done.returncode}")
    return done.stdout


def history_names(cfg: Config, root: str) -> list[str]:
    """The file name of every file in the git history of `root` (repository-relative), on every ref
    (`--all`: local branches, remote-tracking ones and tags). Empty when git cannot answer (SPEC §7 new,
    §5.2.5)."""
    try:
        out = _git(cfg, "log", "--all", "--no-renames", "--name-only", "--format=", "-z", "--", root)
    except GitUnavailable:
        return []
    return [name.rsplit("/", 1)[-1] for name in out.decode("utf-8", "replace").replace("\n", "\0").split("\0")
            if name]


def _history_ids(cfg: Config) -> list[int]:
    """The number of every finding file in the git history of the KB root (SPEC §7 new)."""
    numbers = []
    for name in history_names(cfg, cfg.findings_dir):
        match = FILENAME_RE.match(name)
        if match:
            numbers.append(id_number(match.group(1)))
    return numbers


def allocate_id(cfg: Config) -> str:
    """Next ID after every ID used in findings/, waiting in staging, or in the git history of the KB root on
    any local ref (SPEC §7 new): an ID that was ever committed, one `kblam rm` removed included, is never
    issued again. Without git, or outside a repository, the history adds nothing."""
    numbers = [n for n, _ in _ids_in(cfg.findings_path) + _ids_in(cfg.staging_dir)] + _history_ids(cfg)
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
    if cfg.topics and topic not in cfg.topics:  # K1's topic vocabulary (SPEC §5), when kblam.toml sets one
        raise StoreError(f"topic {topic!r} is not in the topics kblam.toml allows ([kb] topics); use one of: "
                         f"{', '.join(cfg.topics)}. Topics name subjects: if none fits, ask the user to add one")
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


def _rewrite_entry(finding: Finding, position: tuple[int, int], value: str, key: str | None = None) -> bytes | None:
    """The finding's bytes with the frontmatter entry whose key ruamel places at `position` (line, column) set
    to `value`, and its key replaced by `key` when one is given; None when that line does not hold the entry
    as `key: value`. Only those bytes change; the caller re-parses the result to make sure (_changes_only)."""
    line, col = position
    lines = finding.raw.decode("utf-8").split("\n")
    file_line = line + 1  # frontmatter line 0 is file line 1, after the opening ---
    entry = DEP_ENTRY_RE.match(lines[file_line], col)
    if entry is None:
        return None
    scalar = _yaml_scalar(value)
    if entry.group("value") is not None:
        start, end = entry.span("value")
    else:
        start = end = entry.end("gap")
        if not entry.group("gap"):
            scalar = " " + scalar
    row = lines[file_line][:start] + scalar + lines[file_line][end:]
    if key is not None:  # the key precedes the value, so its span is still valid
        key_start, key_end = entry.span("key")
        row = row[:key_start] + key + row[key_end:]
    lines[file_line] = row
    return "\n".join(lines).encode("utf-8")


def _changes_only(finding: Finding, data: bytes, expected: dict) -> bool:
    """`data` parses to the frontmatter `expected` and the same body as `finding`."""
    check = parse_finding(finding.path, data)
    return check.ok and plain_data(check.meta) == expected and check.body_lines == finding.body_lines


def stamp_dependency(finding: Finding, target_id: str, value: str, shown: str) -> bytes:
    """The finding's bytes with depends_on[target_id] set to `value`; no other byte changes.

    `shown` is the file's path as messages should name it.
    """
    stamped = _rewrite_entry(finding, finding.meta["depends_on"].lc.key(target_id), value)
    if stamped is None:
        raise StoreError(f"{shown}: cannot find the value of depends_on {target_id}; write the entry as "
                         f"{target_id}: null")
    expected = plain_data(finding.meta)
    expected["depends_on"][target_id] = value
    if not _changes_only(finding, stamped, expected):
        raise StoreError(f"{shown}: could not set depends_on {target_id} without changing anything else; "
                         f"write the entry on its own line as {target_id}: null")
    return stamped


def rekey_dependency(finding: Finding, old_id: str, new_id: str, value: str, shown: str) -> bytes:
    """The finding's bytes with the depends_on entry `old_id` renamed `new_id` and set to `value`; no other byte
    changes (kblam renumber, SPEC §7). `shown` is the file's path as messages should name it."""
    problem = (f"{shown}: could not change depends_on {old_id} to {new_id} without changing anything else; "
               f"change that entry by hand to {new_id}: {value}")
    data = _rewrite_entry(finding, finding.meta["depends_on"].lc.key(old_id), value, key=new_id)
    expected = plain_data(finding.meta)
    expected["depends_on"] = {(new_id if k == old_id else k): (value if k == old_id else v)
                              for k, v in expected["depends_on"].items()}
    if data is None or not _changes_only(finding, data, expected):
        raise StoreError(problem)
    return data


def _stamp_nulls(staged: Finding, others: list[Finding], shown: str,
                 separator: str) -> tuple[Finding, list[tuple[str, str]]]:
    """Stamp each null depends_on value whose target is a readable finding among `others`, with the
    target's fingerprint under the KB's scope `separator` (SPEC §5.1)."""
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
        value = fingerprint(targets[0], separator)
        staged = parse_finding(staged.path, stamp_dependency(staged, key, value, shown))
        stamped.append((key, value))
    return staged, stamped


def put(cfg: Config, source: Path, *, client_factory=None) -> PutResult:
    """Validate the KB as it would be with `source` in place, run the Jev check, and move it in only
    if both pass.

    Null depends_on fingerprints are stamped first. Only errors in the incoming finding and errors the
    move introduces in other findings block (SPEC §7, Validation); errors already in the tree are
    returned as warnings. K3 on other findings does not block: a rewrite reports the dependents it
    left suspect, and validate fails until they are acked.
    Replacing an existing ID needs the record kblam edit wrote, matching the file as it is now.
    A refused write records a rejected item per rejecting verdict (§6.4).
    K1-K12 refuse as before; K13 and K15 never do, and K14 refuses only for an excerpt the installed
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
    staged, stamped = _stamp_nulls(parse_finding(target, raw), others, display_path(cfg, source),
                                   cfg.scope_separator)
    files = {p: b for p, b in current.files.items() if p not in replaced}
    files[target] = staged.raw
    # K13-K15 read the review records off the view, so the candidate carries the current ones over.
    view = KBView(cfg=cfg, files=files, display={target: display_path(cfg, source)},
                  review_files=dict(current.review_files), review_symlinks=set(current.review_symlinks))
    view.files[view.index_path] = generate_index(view)

    result = PutResult(view=view, finding_id=finding_id, target=target,
                       removed=[p for p in replaced if p != target], stamped=stamped)
    focus = frozenset({finding_id})
    issues = validate(view, focus=focus)
    found = errors(issues)
    # GitHub §7 put scopes K1-K12; local §5.2.5 "What put accepts" keeps K13/K15 nonblocking.
    ordinary = [i for i in found if i.code not in REVIEW_CODES]
    result.issues, result.warnings = _blocking(ordinary, current, target, focus)
    readers = SourceReader(cfg, current), SourceReader(cfg, view)
    result.issues += _newly_affected(result, current, readers, found)
    result.remaining = [i for i in found if i not in result.issues and i not in result.kept
                        and i not in result.warnings]
    result.stale = _made_stale(current, view, finding_id)
    result.warnings += [i for i in issues if not i.is_error]
    return result, current


def _blocking(after: list[Issue], before: KBView, target: str,
              focus: frozenset[str]) -> tuple[list[Issue], list[Issue]]:
    """Split the issues of the tree after a put into (blocking, warnings) (SPEC §7 put, Validation).

    An error in the incoming finding blocks, and so does an error the move introduces elsewhere: one
    present after the move and absent before it, compared by (path, code, message). The files of other
    findings do not change, so their line numbers do not shift. An error that was already there is a
    warning, so one broken finding does not block every writer and two can be fixed one put at a time.
    K3 on another finding never blocks: the put reports the dependents it made suspect (§5 K3)."""
    if all(issue.path == target for issue in after):
        return after, []
    already = {(i.path, i.code, i.message) for i in validate(before, focus=focus)}
    blocking, warnings = [], []
    for issue in after:
        if issue.path == target:
            blocking.append(issue)
        elif (issue.path, issue.code, issue.message) in already:
            warnings.append(issue)
        elif issue.code != "K3":
            blocking.append(issue)
    return blocking, warnings


REVIEW_CODES = ("K13", "K14", "K15")  # the rules that refuse through their own scope, not the K1-K12 sweep
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


def _made_stale(current: KBView, after: KBView, finding_id: str) -> list[str]:
    """The IDs of the CT and CU records a write that turns `current` into `after` makes stale by changing
    `finding_id` (SPEC §5.2.4 "Where each rule blocks"; a put, and the dependents renumber re-keys), in ID
    order: those bound to that finding whose binding held on the current view and no longer holds after.
    A task or use already stale or withdrawn is not listed."""
    stale = []
    for rec in current.records:
        if rec.kind not in ("CT", "CU") or not isinstance(rec.id, str) or rec.status in RETIRED_STATUSES:
            continue
        data = rec.data if isinstance(rec.data, dict) else {}
        if data.get("finding") != finding_id:
            continue
        if rec.kind == "CT":
            # task_binding_problems returns the reasons the binding is broken, so [] means it holds
            broke = not k15.task_binding_problems(current, rec) and k15.task_binding_problems(after, rec)
        else:
            broke = _finding_binding(current, data) and not _finding_binding(after, data)
        if broke:
            stale.append(rec.id)
    return sorted(stale, key=_record_order)


def _finding_binding(view: KBView, data: dict) -> bool:
    """Whether a CU's finding binding holds on this view: the finding exists once, and its K3
    fingerprint and file sha256 are the ones the use bound (SPEC §5.2.3 "A use is current")."""
    found = [f for f in view.findings if f.file_id == data.get("finding") and isinstance(f.meta, dict)]
    if len(found) != 1:
        return False
    return (data.get("finding_fingerprint") == fingerprint(found[0], view.cfg.scope_separator)
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
    separator = cfg.scope_separator
    if previous is None or fingerprint(previous, separator) != fingerprint(written, separator):
        # an old-format stamp (SPEC §5.1) needs the same re-reading as a suspect one
        result.suspect = [d.dependent for d in dependencies(view)
                          if d.target == finding_id and d.state in ("suspect", "old")]

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
    review.close_rejected(cfg, finding_id, fingerprint(written, separator))
    result.review, result.unchecked = recorded.opened, recorded.unchecked
    return result


def ack(cfg: Config, dependent_id: str, target_id: str) -> AckResult:
    """`kblam ack`: record the target's current fingerprint in the dependent, in place under findings/.

    Only that value changes, so INDEX.md (built from title, label and scope) stays current. The
    dependent's bytes do change, so an edit of it begun before the ack is refused at put. Before
    writing it looks in git history for the target as the dependent recorded it (SPEC §7 ack): the
    result carries that claim beside the current one, or a note saying why there is none. git never
    makes the ack fail.
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

    target = next(f for f in view.findings if f.file_id == target_id)  # one readable file: link.current is set
    result = AckResult(dependent.path, link.current, changed=True, claim_now=target.claim)
    if link.recorded is None:
        result.history_note = (f"{dependent_id} recorded no fingerprint for {target_id} (null), so there is no "
                               f"earlier version of {target_id} to show; re-read {target_id} in full")
    else:
        try:
            found = recorded_version(cfg, target, link.recorded)
        except GitUnavailable as exc:
            result.history_note = (f"could not look in git history for {target_id} as {dependent_id} recorded it "
                                   f"({exc}); re-read {target_id} in full")
        else:
            if found is None:
                result.history_note = (f"no version of {target_id} in git history has the fingerprint "
                                       f"{dependent_id} recorded ({link.recorded}), so kblam cannot show what "
                                       f"changed; re-read {target_id} in full")
            else:
                result.then_commit, result.claim_then = found

    data = stamp_dependency(dependent, target_id, link.current, dependent.path)
    clean = clean_before_v2(cfg, view, bool(view.records))
    result.recorded = writes.apply(cfg, f"ack {dependent_id} {target_id}", [(dependent.path, data)],
                                   registry_ids=writes.registry_after(cfg, _present_record_ids(view), set()),
                                   clean_before=clean)
    return result


def recorded_version(cfg: Config, target: Finding, recorded: str) -> tuple[str, str] | None:
    """(short commit hash, claim) of the newest version of `target` in the history of HEAD whose fingerprint is
    `recorded`: the target as a dependent last checked it (SPEC §7 ack). None when no version has it, as when
    it was never committed. GitUnavailable when git cannot answer.

    Every path the ID had under the KB root counts, so a version from before a topic or slug change is
    found however much the file changed with it."""
    spec = f":(glob){cfg.findings_dir}/**/{target.file_id}-*.md"
    out = _git(cfg, "log", "--no-renames", "--format=%H %h", "--name-only", "-z", "--", spec)
    versions: list[tuple[bytes, str, bytes]] = []  # (commit, short commit, path), newest first
    commit = None
    for token in out.split(b"\0"):
        # With -z each path is its own token and a header follows a newline; a header's token can also carry
        # lines git prints before it (log.showSignature), so the last line is the one that counts.
        token = token.strip(b"\n").rsplit(b"\n", 1)[-1]
        header = COMMIT_LINE_RE.fullmatch(token)
        if header:
            commit = header.group(1), header.group(2).decode("ascii")
        elif commit and FILENAME_RE.match(token.rsplit(b"/", 1)[-1].decode("utf-8", "replace")):
            versions.append((commit[0], commit[1], token))
    blobs = _blobs(cfg, [full + b":" + path for full, _short, path in versions])
    for (_full, short, path), raw in zip(versions, blobs):
        if raw is None:
            continue  # the path was removed in that commit
        then = parse_finding(path.decode("utf-8", "replace"), raw)
        if (then.ok and then.file_id == target.file_id
                and fingerprint_as(recorded, then, cfg.scope_separator) == recorded):
            return short, then.claim
    return None


def _blobs(cfg: Config, specs: list[bytes]) -> list[bytes | None]:
    """The blob each `<commit>:<path>` names, or None where there is none, from one `git cat-file --batch`."""
    if not specs:
        return []
    out = _git(cfg, "cat-file", "--batch", stdin=b"".join(spec + b"\n" for spec in specs))
    blobs: list[bytes | None] = []
    pos = 0
    while len(blobs) < len(specs):
        end = out.find(b"\n", pos)
        if end < 0:
            break
        parts = out[pos:end].split(b" ")
        pos = end + 1
        if len(parts) == 3 and OBJECT_ID_RE.fullmatch(parts[0]) and parts[2].isdigit():
            size = int(parts[2])
            blobs.append(out[pos:pos + size] if parts[1] == b"blob" else None)
            pos += size + 1
        else:
            blobs.append(None)  # "<spec> missing"
    return blobs + [None] * (len(specs) - len(blobs))


# --- rm and renumber (SPEC §7, M6.10) -----------------------------------------------------------------


def _write_index(cfg: Config, view: KBView) -> None:
    generated = generate_index(view)
    index_file = cfg.repo_root / view.index_path
    if not index_file.is_file() or index_file.read_bytes() != generated:
        atomic_write(index_file, generated)


def _one_finding(cfg: Config, view: KBView, finding_id: str, missing: str) -> Finding:
    """The one readable file of `finding_id`; `missing` is the refusal when there is none."""
    files = [f for f in view.findings if f.file_id == finding_id]
    if not files:
        raise StoreError(missing)
    if len(files) > 1:
        raise StoreError(f"{finding_id} has more than one file in {cfg.findings_dir}/ "
                         f"({', '.join(f.path for f in files)}); give one of them a new ID with kblam renumber "
                         f"<path> first")
    if not files[0].ok:
        line, message = files[0].parse_errors[0]
        raise StoreError(f"{files[0].path} cannot be read as a finding ("
                         + (f"line {line}: " if line else "") + f"{message}); fix it with kblam edit "
                         f"{finding_id} and kblam put first")
    return files[0]


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _removal_problems(cfg: Config, view: KBView, finding: Finding, target: Finding) -> list[str]:
    """Why `finding` cannot be removed as merged into `target` (SPEC §7 rm): a finding that depends on it, or
    a quantity of it that `target` does not give with the same value and unit. Names compare as §6.3
    compares them (case-insensitively, whitespace runs collapsed), units exactly after collapsing
    whitespace, and values within `quantity_rel_tolerance`."""
    finding_id, target_id = finding.file_id, target.file_id
    problems = []
    dependents = sorted({f.file_id for f in view.findings if f.path != finding.path and isinstance(f.meta, dict)
                         and isinstance(f.meta.get("depends_on"), dict) and finding_id in f.meta["depends_on"]},
                        key=id_number)
    others = [d for d in dependents if d != target_id]
    if others:
        problems.append(f"{_and(others)} {'lists' if len(others) == 1 else 'list'} {finding_id} in depends_on; "
                        f"edit each to depend on {target_id} first (kblam edit <id>, change {finding_id} to "
                        f"{target_id}: null under depends_on, and put it)")
    if target_id in dependents:
        problems.append(f"{target_id} itself lists {finding_id} in depends_on; remove that entry first "
                        f"(kblam edit {target_id})")

    written = finding.meta.get("quantities")
    mine = _quantities(finding)
    if written is not None and (not isinstance(written, list) or len(mine) != len(written)):
        problems.append(f"{finding_id} has a quantity kblam cannot read (kblam validate reports it under K1), so "
                        f"kblam cannot tell whether {target_id} gives it")
    elif mine:
        tolerance = jev_settings(cfg).quantity_rel_tolerance
        theirs = _quantities(target)
        lost = [(name, value, unit) for key, name, value, unit in mine
                if not any(key == k and unit == u and not _differ(value, v, tolerance) for k, _n, v, u in theirs)]
        if lost:
            listed = _and([f"{name} = {_format_value(value, unit)}" for name, value, unit in lost])
            problems.append(f"{finding_id} gives {listed}, which {target_id} does not give with the same name, "
                            f"value and unit; move {'it' if len(lost) == 1 else 'them'} into {target_id} first "
                            f"(kblam edit {target_id}), or keep {finding_id}")
    return problems


def remove_finding(cfg: Config, finding_id: str, target_id: str) -> RemoveResult:
    """`kblam rm <id> --merged-into <target>` (SPEC §7, §8.1): remove a finding after a merge moved everything
    it stated into <target>.

    Refused while another finding depends on it, while either ID is not a single readable finding in the KB,
    while one of its quantities is missing from <target> with the same value and unit, or while a review
    record links it (in any status; the refusal says to merge the other way, _linked_removal). Under the lock it
    removes the file and a topic folder that leaves empty, regenerates INDEX.md, applies the tree.hash rule
    (§8) and closes the finding's open review, rejected and unchecked items. The reason for the removal goes
    in the commit message. Who may run it is the hooks' concern (the adjudicator gate, §8 item 2).
    """
    for value in (finding_id, target_id):
        if not ID_RE.match(value):
            raise StoreError(f"{value!r} is not a finding ID like F-0137")
    if finding_id == target_id:
        raise StoreError(f"--merged-into names the finding that now states what {finding_id} stated, so it cannot "
                         f"be {finding_id} itself")
    with kb_lock(cfg, f"rm {finding_id} --merged-into {target_id}"):
        return _remove(cfg, finding_id, target_id)


def _remove(cfg: Config, finding_id: str, target_id: str) -> RemoveResult:
    view = load_view(cfg)
    finding = _one_finding(cfg, view, finding_id, f"{finding_id} is not in {cfg.findings_dir}/, so there is "
                                                  f"nothing to remove")
    target = _one_finding(cfg, view, target_id, f"{target_id} is not in {cfg.findings_dir}/; --merged-into names "
                                                f"the finding that now states what {finding_id} stated")
    linked = _links(view, finding_id)[finding.path]
    if linked:
        raise StoreError(_linked_removal(cfg, view, finding, target, linked))
    problems = _removal_problems(cfg, view, finding, target)
    if problems:
        raise StoreError(f"{finding_id} cannot be removed: " + "; ".join(problems)
                         + f". {cfg.findings_dir}/ is unchanged")
    items = review.load_items(cfg)  # read before anything is written: a damaged review.jsonl refuses the rm

    title = finding.meta.get("title")
    result = RemoveResult(finding_id, target_id, finding.path, title if isinstance(title, str) else "",
                          index_path=view.index_path,
                          staged=[display_path(cfg, p) for n, p in _ids_in(cfg.staging_dir)
                                  if format_id(n) == finding_id])
    clean = clean_before_v2(cfg, view, bool(view.records))
    path = cfg.repo_root / finding.path
    path.unlink()
    if path.parent != cfg.findings_path and not any(path.parent.iterdir()):
        path.parent.rmdir()
        result.folder = PurePosixPath(finding.path).parent.as_posix() + "/"
    _write_index(cfg, load_view(cfg))
    result.recorded = record_after_write_v2(cfg, clean, "rm")
    result.closed = review.close_items_on(cfg, finding_id, f"{finding_id} was removed (merged into {target_id})",
                                          items)
    return result


def _set_id(finding: Finding, new_id: str, shown: str, other) -> bytes:
    """The finding's bytes with its `id` value set to `new_id`; no other byte changes. `other()` is the
    path of another file with that ID that kblam can renumber, or None: only then does a refusal offer
    renumbering it instead (SPEC §7)."""
    try:
        position = finding.meta.lc.key("id")
    except (AttributeError, KeyError):
        alternative = other()
        raise StoreError(f"{shown} has no id key, so kblam cannot rewrite it; "
                         + (_instead(alternative) if alternative
                            else f"ask a person to add its id line (id: {finding.file_id})")) from None
    data = _rewrite_entry(finding, position, new_id)
    expected = plain_data(finding.meta)
    expected["id"] = new_id
    if data is None or not _changes_only(finding, data, expected):
        alternative = other()
        raise StoreError(f"{shown}: could not set id to {new_id} without changing anything else; "
                         + (f"{_instead(alternative)}, or " if alternative else "")
                         + "ask a person to fix this file's id line")
    return data


def _instead(path: str) -> str:
    """A renumber refusal's alternative: the other file with the ID that kblam can renumber (SPEC §7)."""
    return f"renumber {path} instead (kblam renumber {path})"


def _names(text: str, finding_id: str) -> bool:
    return any(m.group(0) == finding_id for m in ID_IN_TEXT_RE.finditer(text))


def renumber(cfg: Config, path: Path) -> RenumberResult:
    """`kblam renumber <path>` (SPEC §7): give a new ID to one of the findings that share an ID, which K1
    reports after the work of two clones is merged.

    Under the lock it allocates an ID as `new` does, rewrites the file's `id` value and renames it
    F-<new>-<slug>.md in its topic folder. Each other finding's depends_on entry whose recorded fingerprint
    shows it means this file (it equals this file's fingerprint before the change, and no file keeping the
    old ID has that fingerprint) is re-keyed to the new ID with this file's new fingerprint, verified by
    re-parsing as `stamp_dependency` is. The other mentions of the old ID (titles, bodies, depends_on entries
    whose fingerprint does not settle which file they mean) are listed for a person to check. A resolution
    (§6.4) whose side is the old ID at this file's state hash meant this file, so a copy under the new ID is
    appended to kblam.resolutions.jsonl, and the verdicts it settled are not raised again. It regenerates
    INDEX.md and applies the tree.hash rule (§8).
    """
    source = Path(path)
    with kb_lock(cfg, f"renumber {source.name}"):
        return _renumber(cfg, source)


def _renumber(cfg: Config, source: Path) -> RenumberResult:
    view = load_view(cfg)
    wanted = source.resolve()
    finding = next((f for f in view.findings if (cfg.repo_root / f.path).resolve() == wanted), None)
    if finding is None:
        raise StoreError(f"{display_path(cfg, source)} is not a finding in a topic folder of {cfg.findings_dir}/; "
                         f"kblam renumber takes the path of one of the findings that share an ID, as K1 names them "
                         f"in kblam validate")
    old_id = finding.file_id
    kept = [f for f in view.findings if f.file_id == old_id and f is not finding]
    if not kept:
        raise StoreError(f"no other file in {cfg.findings_dir}/ uses {old_id}, so there is nothing to renumber: "
                         f"an ID never changes, and kblam renumber only settles an ID two findings share (K1)")
    links = _links(view, old_id)
    new_id = allocate_id(cfg)
    peers: list[tuple[Finding, str | None]] | None = None  # None until a message needs them

    def unlinked_peers() -> list[tuple[Finding, str | None]]:
        """Each other file with the ID that no review record links, with the reason kblam cannot renumber
        it (None when it can: the first case's test, SPEC §7). Worked out once, when a message needs it."""
        nonlocal peers
        if peers is None:
            peers = [(k, _renumber_problem(cfg, view, k, new_id)) for k in kept if not links[k.path]]
        return peers

    def other() -> str | None:
        return next((k.path for k, problem in unlinked_peers() if problem is None), None)

    if links[finding.path]:
        raise StoreError(_linked_renumber(view, finding, links, unlinked_peers()))
    plan = _renumber_plan(cfg, view, finding, kept, new_id, other)

    clean = clean_before_v2(cfg, view, bool(view.records))
    atomic_write(cfg.repo_root / plan.new_path, plan.data)
    (cfg.repo_root / finding.path).unlink()
    for dependent, rewritten in plan.rewrites:
        atomic_write(cfg.repo_root / dependent.path, rewritten)
    after = load_view(cfg)
    _write_index(cfg, after)
    for line in plan.carried:
        resolutions.append(cfg, line)
    return RenumberResult(
        old_id, new_id, finding.path, plan.new_path, plan.new_fp,
        kept=[f.path for f in kept],
        rekeyed=[(d.file_id, d.path) for d, _ in plan.rewrites],
        mentions=[f"{p}:{line}: {what}" for p, line, what in sorted(plan.mentions)],
        index_path=view.index_path,
        recorded=record_after_write_v2(cfg, clean, "renumber"),
        resolutions=len(plan.carried),
        # A re-keyed dependent's bytes change, so a CT or CU bound to it goes stale (SPEC §7 renumber).
        stale=[(rec_id, d.file_id) for d, _ in plan.rewrites for rec_id in _made_stale(view, after, d.file_id)],
    )


@dataclass
class _RenumberPlan:
    """What renumbering one file writes (SPEC §7 renumber), worked out before anything is written."""
    new_path: str
    data: bytes                                  # the file's bytes under the new ID
    new_fp: str
    carried: list[resolutions.Resolution]
    rewrites: list[tuple[Finding, bytes]]        # (dependent, its bytes with the entry re-keyed)
    mentions: list[tuple[str, int, str]]         # (path, line, what), for a person to check


def _renumber_plan(cfg: Config, view: KBView, finding: Finding, kept: list[Finding], new_id: str,
                   other) -> _RenumberPlan:
    """Renumber's preconditions for `finding`, and what it would write. A StoreError (or ResolutionError,
    for a damaged kblam.resolutions.jsonl) is the refusal; `other()` names another file with the ID that
    kblam can renumber, which only then the refusal offers instead (SPEC §7)."""
    old_id = finding.file_id
    if not finding.ok:
        line, message = finding.parse_errors[0]
        alternative = other()
        raise StoreError(f"{finding.path} cannot be read as a finding (" + (f"line {line}: " if line else "")
                         + f"{message}), so kblam cannot rewrite its id; "
                         + (f"{_instead(alternative)}, or " if alternative else "")
                         + "ask a person to fix this file")
    new_path = f"{PurePosixPath(finding.path).parent.as_posix()}/{new_id}-{finding.slug}.md"
    data = _set_id(finding, new_id, finding.path, other)
    new_fp = fingerprint(parse_finding(new_path, data), cfg.scope_separator)
    carried = _carried_resolutions(cfg, finding, old_id, new_id)  # read first: a damaged file refuses

    rewrites: list[tuple[Finding, bytes]] = []
    mentions: list[tuple[str, int, str]] = []
    for f in view.findings:
        shown = new_path if f is finding else f.path
        meta = f.meta if isinstance(f.meta, dict) else {}
        depends = meta.get("depends_on")
        if isinstance(depends, dict) and old_id in depends:
            recorded = depends[old_id]
            line = depends.lc.key(old_id)[0] + 2
            # A stamp from before fingerprint v2 is compared in its own format (fingerprint_as).
            stamp = isinstance(recorded, str)
            means_this = stamp and recorded == fingerprint_as(recorded, finding, cfg.scope_separator)
            means_kept = stamp and recorded in {fingerprint_as(recorded, k, cfg.scope_separator) for k in kept}
            if means_this and not means_kept and f is not finding:
                rewrites.append((f, rekey_dependency(f, old_id, new_id, new_fp, f.path)))
            elif not (means_kept and not means_this):
                why = ("matches neither file" if not means_this
                       else "is this finding's own fingerprint" if f is finding
                       else "is the fingerprint of both files")
                shown_value = "null" if recorded is None else recorded
                mentions.append((shown, line, f"depends_on {old_id}: {shown_value} {why}"))
        title = meta.get("title")
        if isinstance(title, str) and _names(title, old_id):
            mentions.append((shown, f.key_line("title") or 1, f"the title names {old_id}"))
        for number, text in enumerate(f.body_lines):
            if _names(text, old_id):
                mentions.append((shown, f.body_start_line + number, f"the body names {old_id}"))
    return _RenumberPlan(new_path, data, new_fp, carried, rewrites, mentions)


def _renumber_problem(cfg: Config, view: KBView, finding: Finding, new_id: str) -> str | None:
    """The refusal renumbering `finding` would give, without its `kblam renumber:` prefix and without any
    "renumber the other file instead" alternative; None when renumbering it would pass every precondition."""
    kept = [f for f in view.findings if f.file_id == finding.file_id and f is not finding]
    try:
        _renumber_plan(cfg, view, finding, kept, new_id, lambda: None)
    except (StoreError, resolutions.ResolutionError) as exc:
        return str(exc)
    return None


# --- rm and renumber vs review records (SPEC §7 "`rm` and `renumber` vs review records") ----------------

BINDINGS = {"CT": ("claim_fingerprint", "base_file_sha256"),     # a record's finding binding:
            "CU": ("finding_fingerprint", "finding_file_sha256")}  # (fingerprint v2, full-file sha256)


def _kind_order(rec_id: str) -> tuple[int, int, str]:
    """Records by kind (SC, CT, CU), then in numeric ID order."""
    return (tuple(records.KINDS).index(rec_id[:2]), *_record_order(rec_id))


def _bound(cfg: Config, rec, finding: Finding) -> bool:
    """Whether a CT's or CU's finding binding identifies this file: fingerprint v2 and file sha256 match."""
    fingerprint_key, sha_key = BINDINGS[rec.kind]
    return (rec.data.get(fingerprint_key) == fingerprint(finding, cfg.scope_separator)
            and rec.data.get(sha_key) == sha256_hex(finding.raw))


def _links(view: KBView, finding_id: str) -> dict[str, list]:
    """The review records that link each file with `finding_id`: {path: [records.Record]}, in `_kind_order`.
    A CT or CU naming the ID links the file its binding identifies, or, when its binding identifies none,
    every file with the ID. An SC's `linked_findings` entry is a bare ID and links every file with it. Every
    status counts: retiring a record does not free a finding's identity. Installed records only."""
    files = [f for f in view.findings if f.file_id == finding_id]
    links: dict[str, list] = {f.path: [] for f in files}
    named = [rec for rec in view.records if isinstance(rec.id, str) and isinstance(rec.data, dict)]
    for rec in sorted(named, key=lambda rec: _kind_order(rec.id)):
        if rec.kind == "SC":
            listed = rec.data.get("linked_findings")
            linked = files if isinstance(listed, list) and finding_id in listed else []
        elif rec.kind in BINDINGS and rec.data.get("finding") == finding_id:
            linked = [f for f in files if _bound(view.cfg, rec, f)] or files
        else:
            linked = []
        for f in linked:
            links[f.path].append(rec)
    return links


def _review_records(ids: list[str], verb: str = "") -> str:
    """"review record CT-0003 links" for one and "review records CT-0003, CU-0001 link" for several; with
    no verb, "review record CT-0003" and "review records CT-0003, CU-0001"."""
    words = ("review record " if len(ids) == 1 else "review records ") + ", ".join(ids)
    return words + (f" {verb}s" if len(ids) == 1 else f" {verb}") if verb else words


def _linked_removal(cfg: Config, view: KBView, finding: Finding, target: Finding, linked: list) -> str:
    """rm's refusal of a finding review records link (SPEC §7): merge the other way, unless a record also
    links the target. The staged copies and the records the edit makes stale are named as SPEC §7 says."""
    finding_id, target_id = finding.file_id, target.file_id
    mine = [rec.id for rec in linked]
    theirs = [rec.id for rec in _links(view, target_id)[target.path]]
    head = f"{finding_id} cannot be removed: {_review_records(mine, 'link')} it"
    if theirs:
        named = sorted(set(mine) | set(theirs), key=_kind_order)
        return (f"{head}, and {target_id} cannot be removed in its place: {_review_records(theirs, 'link')} it; "
                f"kblam never removes a finding a review record links. {cfg.findings_dir}/ is unchanged. Leave "
                f"both as they are and tell the user {_and([finding_id, target_id, *named])}")
    staged = sorted(display_path(cfg, p) for n, p in _ids_in(cfg.staging_dir) if format_id(n) == finding_id)
    lacks = f"also state what {target_id} states that {finding_id} does not yet (its detail and quantities"
    if not staged:
        how = f"make {finding_id} {lacks}; kblam edit {finding_id})"
    elif len(staged) == 1:
        how = f"make your staged copy {staged[0]} {lacks}) and put it"
    else:
        how = f"make one of your staged copies {', '.join(staged)} {lacks}) and put it"
    text = (f"{head}, and kblam never removes a finding a review record links. {cfg.findings_dir}/ is unchanged. "
            f"Merge the other way: {how}, then kblam rm {target_id} --merged-into {finding_id}")
    # the records the put of the edited finding lists as made stale (_made_stale): bound to this file, not retired
    stale = [rec.id for rec in linked
             if rec.kind in BINDINGS and rec.status not in RETIRED_STATUSES and _bound(cfg, rec, finding)]
    if stale:
        each = "it" if len(stale) == 1 else "each"
        text += (f". The edit makes {', '.join(stale)} stale until a reviewer rechecks and rebinds {each}; kblam "
                 f"put prints the kblam review rebind command for {each}")
    return text


def _linked_renumber(view: KBView, finding: Finding, links: dict[str, list],
                     peers: list[tuple[Finding, str | None]]) -> str:
    """renumber's refusal of a file review records link (SPEC §7): renumber another file with the ID that
    kblam can renumber, or, when there is none, why not. `peers` are the other files no record links, each
    with the reason kblam cannot renumber it (None when it can)."""
    old_id = finding.file_id
    files = [f for f in view.findings if f.file_id == old_id]
    mine = [rec.id for rec in links[finding.path]]
    head = f"{finding.path} holds {old_id}, which {_review_records(mine, 'link')}, so it keeps its ID"
    ready = [f.path for f, problem in peers if problem is None]
    if ready:
        if len(files) == 2:
            which = "the other finding with that ID"
        else:
            which = f"the other finding{'s' if len(ready) > 1 else ''} with that ID that kblam can renumber"
        return f"{head}. Renumber {which} instead: " + "; ".join(f"kblam renumber {path}" for path in ready)
    if not peers:
        ids = sorted({rec.id for f in files for rec in links[f.path]}, key=_kind_order)
        both, paths = (("both findings", "both paths") if len(files) == 2
                       else (f"all {len(files)} findings", f"the {len(files)} paths"))
        return (f"{both} with ID {old_id} ({', '.join(f.path for f in files)}) are linked by {_review_records(ids)}, "
                f"and kblam renumbers no finding a review record links. K1 fails kblam validate and every commit "
                f"until a person settles this: tell the user {paths} and {', '.join(ids)}")
    if len(files) == 2:
        (peer, problem), = peers
        why = f"the other finding with that ID, {peer.path}, cannot be renumbered yet: {problem}"
    else:
        why = (f"the other finding{'s' if len(peers) > 1 else ''} with that ID that no review record links cannot "
               f"be renumbered yet: " + "; ".join(f"{f.path}: {problem}" for f, problem in peers))
    return (f"{head}; {why}. Ask a person to fix that, then run "
            + "; ".join(f"kblam renumber {f.path}" for f, _ in peers))


def _carried_resolutions(cfg: Config, finding: Finding, old_id: str, new_id: str) -> list[resolutions.Resolution]:
    """The resolutions to append for a renumbered finding (SPEC §7 renumber): a copy under `new_id`, with its
    reason and date, of each resolution with the side (`old_id`, this finding's state hash), unless the file
    has it already. The state hash tells the two files that shared the ID apart, as a fingerprint does for
    depends_on; should both state the same claim and scope, the resolution holds for both."""
    state = Side.of(finding, cfg.scope_separator).state_hash
    recorded = resolutions.load(cfg)
    have = resolutions.by_key(recorded)
    carried = []
    for line in recorded:
        if (old_id, state) not in line.sides:
            continue
        sides = tuple(sorted((new_id, s) if (i, s) == (old_id, state) else (i, s) for i, s in line.sides))
        key = resolutions.key(line.kind, sides)
        if key not in have:
            have[key] = resolutions.Resolution(sides, line.kind, line.reason, line.date)
            carried.append(have[key])
    return carried
