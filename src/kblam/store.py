"""Writes: ID allocation, staging (`new`, `edit`), `put`, `ack`, `index`, `rm` and `renumber` (SPEC §7)."""

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

from kblam import review
from kblam.check import Checker, CheckResult, _differ, _format_value, _quantities
from kblam.config import Config
from kblam.jev import jev_settings
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
from kblam.rules import TOPIC_RE, Issue, dependencies, validate
from kblam.treehash import as_kblam_left_it, record_after_write, tree_digest
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
    warnings: list[Issue] = field(default_factory=list)             # errors already in the tree: not blocking
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

    @property
    def rejected(self) -> bool:
        """A reject verdict fired (SPEC §6.4); findings/ is unchanged."""
        return bool(self.check and self.check.rejected)

    @property
    def ok(self) -> bool:
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


def _history_ids(cfg: Config) -> list[int]:
    """The number of every finding file in the git history of the KB root, on every ref (`--all`: local
    branches, remote-tracking ones and tags). Empty when git cannot answer (SPEC §7 new)."""
    try:
        out = _git(cfg, "log", "--all", "--no-renames", "--name-only", "--format=", "-z", "--", cfg.findings_dir)
    except GitUnavailable:
        return []
    numbers = []
    for name in out.decode("utf-8", "replace").replace("\n", "\0").split("\0"):
        match = FILENAME_RE.match(name.rsplit("/", 1)[-1])
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
    with kb_lock(cfg, f"new {topic}"):
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
    with kb_lock(cfg, f"edit {finding_id}"):
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
    """`kblam index`: rewrite INDEX.md from frontmatter and record tree.hash."""
    with kb_lock(cfg, "index"):
        view = load_view(cfg)
        clean = as_kblam_left_it(cfg, tree_digest(view))
        generated = generate_index(view)
        index_file = cfg.repo_root / view.index_path
        if not index_file.is_file() or index_file.read_bytes() != generated:
            atomic_write(index_file, generated)
        record_after_write(cfg, clean, "index")
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
    Jev is asked before the lock is taken; under the lock the candidates are recomputed against the
    current tree and only pairs it gained meanwhile are asked (SPEC §12 M5). `client_factory`
    builds the JevClient (tests pass one with a fake transport).
    """
    source = Path(source)
    with Checker(cfg, client_factory) as checker:
        _prefetch(cfg, source, checker)
        with kb_lock(cfg, f"put {source.name}"):
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
    view = KBView(cfg=cfg, files=files, display={target: display_path(cfg, source)})
    view.files[view.index_path] = generate_index(view)

    result = PutResult(view=view, finding_id=finding_id, target=target,
                       removed=[p for p in replaced if p != target], stamped=stamped)
    focus = frozenset({finding_id})
    result.issues, result.warnings = _blocking(validate(view, focus=focus), current, target, focus)
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

    clean = as_kblam_left_it(cfg, tree_digest(current))
    root = cfg.repo_root
    atomic_write(root / target, view.files[target])
    for old in result.removed:
        old_path = root / old
        old_path.unlink()
        parent = old_path.parent
        if parent != cfg.findings_path and not any(parent.iterdir()):
            parent.rmdir()
    atomic_write(root / view.index_path, view.files[view.index_path])
    result.recorded = record_after_write(cfg, clean, "put")
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
    with kb_lock(cfg, f"ack {dependent_id} {target_id}"):
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
    clean = as_kblam_left_it(cfg, tree_digest(view))
    atomic_write(cfg.repo_root / dependent.path, data)
    result.recorded = record_after_write(cfg, clean, "ack")
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
    or while one of its quantities is missing from <target> with the same value and unit. Under the lock it
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
    clean = as_kblam_left_it(cfg, tree_digest(view))
    path = cfg.repo_root / finding.path
    path.unlink()
    if path.parent != cfg.findings_path and not any(path.parent.iterdir()):
        path.parent.rmdir()
        result.folder = PurePosixPath(finding.path).parent.as_posix() + "/"
    _write_index(cfg, load_view(cfg))
    result.recorded = record_after_write(cfg, clean, "rm")
    result.closed = review.close_items_on(cfg, finding_id, f"{finding_id} was removed (merged into {target_id})",
                                          items)
    return result


def _set_id(finding: Finding, new_id: str, shown: str) -> bytes:
    """The finding's bytes with its `id` value set to `new_id`; no other byte changes."""
    problem = (f"{shown}: could not set id to {new_id} without changing anything else; renumber the other file "
               f"with that ID instead, or ask a person to fix this file's id line")
    try:
        position = finding.meta.lc.key("id")
    except (AttributeError, KeyError):
        raise StoreError(f"{shown} has no id key, so kblam cannot rewrite it; renumber the other file with its "
                         f"ID instead") from None
    data = _rewrite_entry(finding, position, new_id)
    expected = plain_data(finding.meta)
    expected["id"] = new_id
    if data is None or not _changes_only(finding, data, expected):
        raise StoreError(problem)
    return data


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
    whose fingerprint does not settle which file they mean) are listed for a person to check. It regenerates
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
    if not finding.ok:
        line, message = finding.parse_errors[0]
        raise StoreError(f"{finding.path} cannot be read as a finding (" + (f"line {line}: " if line else "")
                         + f"{message}), so kblam cannot rewrite its id; renumber {kept[0].path} instead, or ask a "
                         f"person to fix this file")
    new_id = allocate_id(cfg)
    new_path = f"{PurePosixPath(finding.path).parent.as_posix()}/{new_id}-{finding.slug}.md"
    data = _set_id(finding, new_id, finding.path)
    new_fp = fingerprint(parse_finding(new_path, data), cfg.scope_separator)

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

    clean = as_kblam_left_it(cfg, tree_digest(view))
    atomic_write(cfg.repo_root / new_path, data)
    (cfg.repo_root / finding.path).unlink()
    for dependent, rewritten in rewrites:
        atomic_write(cfg.repo_root / dependent.path, rewritten)
    _write_index(cfg, load_view(cfg))
    return RenumberResult(
        old_id, new_id, finding.path, new_path, new_fp,
        kept=[f.path for f in kept],
        rekeyed=[(d.file_id, d.path) for d, _ in rewrites],
        mentions=[f"{p}:{line}: {what}" for p, line, what in sorted(mentions)],
        index_path=view.index_path,
        recorded=record_after_write(cfg, clean, "renumber"),
    )
