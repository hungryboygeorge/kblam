"""`kblam upgrade`: move a knowledge base and this machine's state to the formats M6.10 introduces
(SPEC §7, §5.1, §6.4, §6.5, §9 "Upgrading").

Fingerprint v2 (§5.1) changes every finding's fingerprint, and M6.10 keys cached answers and resolutions by
state hash. What was recorded before is migrated here, under the lock:

- in the knowledge base, once (committed): each `depends_on` stamp that is current under v1 gets its target's
  v2 fingerprint; a stale one stays as it is, and K1 keeps reporting it until someone re-reads and acks;
- on each machine (`.kblam/`): the same for staged findings, and for the edit records of findings the
  re-stamp rewrote; open review, rejected and unchecked items on current v1 fingerprints move to v2 (and to
  the IDs a check would now give them), and the others close as their findings changed; checked marks move
  to v2; resolutions recorded in pairs.sqlite move into kblam.resolutions.jsonl, keyed by state hash; cached
  answers under the current wording move to per-question prompt ids and state hashes.

It prints the per-question prompt ids for a person to record in `[jev.thresholds]` and never edits
kblam.toml. Until a machine's state is upgraded, `old_state` names what is old, and the commands that would
read it as changes refuse (every open item would close, every finding would be checked again); the Stop hook
notes it instead. Nothing here asks Jev.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kblam import registry, resolutions, review
from kblam.config import Config
from kblam.finding import FILENAME_RE, Finding, fingerprint, fingerprint_v1, is_v1_fingerprint, parse_finding
from kblam.jev import CACHE_NAME, JevSettings, PairCache, Side, jev_settings, question_prompt_id
from kblam.jev_prompts import RELATION_KEY, REVISION_KEY
from kblam.lock import kb_lock
from kblam.store import atomic_write, display_path, edit_base_path, stamp_dependency
from kblam.treehash import clean_before_v2, read_recorded, record_after_write_v2, tree_digest
from kblam.view import load_view

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Why the tree.hash rule left tree.hash as it was, for `kblam upgrade` to say (UpgradeResult.kept_because).
KEPT_NONE_RECORDS = "no tree.hash, records"         # none, and upgrade writes no registry for the records
KEPT_NONE_INVALID = "no tree.hash, invalid"         # none, and the tree before the upgrade failed validation
KEPT_OLD_RECORDS = "format 1, records"              # a matching format-1 marker cannot vouch for records
KEPT_OLD_REGISTRY = "format 1, registry"            # ... nor for the IDs a registry lists
KEPT_OLD_UNREADABLE = "format 1, unreadable"        # ... nor for a registry that cannot be read
KEPT_CHANGED_V1 = "format 1, changed"               # findings/ changed since the format-1 marker
KEPT_CHANGED = "changed"                            # the tree changed since the format-2 marker


@dataclass(frozen=True)
class Restamp:
    path: str     # the file, as it can be shown
    target: str   # the depends_on key
    old: str      # the v1 stamp
    new: str | None = None  # its v2 replacement; None when the stamp stays (its target changed since)


@dataclass
class UpgradeResult:
    """What `kblam upgrade` did (SPEC §7)."""
    restamped: list[Restamp] = field(default_factory=list)      # in findings/
    stale: list[Restamp] = field(default_factory=list)          # v1 stamps left as they are, in findings/
    staged: list[Restamp] = field(default_factory=list)         # re-stamped in .kblam/staging/
    edit_records: list[str] = field(default_factory=list)       # IDs whose edit record followed the re-stamp
    recorded: bool | None = None       # tree.hash advanced; None when nothing in findings/ was written
    kept_because: str | None = None    # why not, when recorded is False: one of the KEPT_* reasons
    items_rekeyed: list[tuple[str, str]] = field(default_factory=list)  # (old ID, new ID)
    items_closed: list[str] = field(default_factory=list)       # open items whose finding changed since
    marks_moved: int = 0
    marks_dropped: int = 0
    resolutions_moved: int = 0
    resolutions_present: int = 0       # already in kblam.resolutions.jsonl
    resolutions_lapsed: int = 0        # a side changed since, so it no longer applied
    answers_moved: int = 0
    answers_dropped: int = 0
    prompt_ids: tuple[str, str] | None = None  # (relation, revision): to record in place of the combined prompt_id
    prompt_id_differs: tuple[str, str] | None = None  # (recorded, current): prompt_id is another wording's id

    @property
    def changed(self) -> bool:
        return bool(self.restamped or self.staged or self.edit_records or self.items_rekeyed or self.items_closed
                    or self.marks_moved or self.marks_dropped or self.resolutions_moved or self.resolutions_present
                    or self.resolutions_lapsed or self.answers_moved or self.answers_dropped)


# --- what is old -----------------------------------------------------------------------------------


def _read_only(path: Path):
    """A connection that can neither write nor create the file."""
    return closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True))


def old_state(cfg: Config) -> list[str]:
    """What this machine's .kblam/ holds from before fingerprint v2 that a command would misread: open
    review and unchecked items on v1 fingerprints, v1 checked marks, and resolutions still in pairs.sqlite.
    [] when there is none. Creates nothing; a pairs.sqlite that cannot be read is left to the command,
    which reports it. Rejected items and cached answers are not counted: a check never reads the first
    as a change, and the second only costs questions asked again."""
    found = []
    items = [i for i in review.load_items(cfg) if i.open and i.kind != "rejected"
             and (is_v1_fingerprint(i.new_fp) or is_v1_fingerprint(i.existing_fp))]
    if items:
        found.append(f"{len(items)} open item(s) in .kblam/review.jsonl")
    cache = cfg.state_dir / CACHE_NAME
    if cache.is_file():
        try:
            with _read_only(cache) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                marks = (conn.execute("SELECT COUNT(*) FROM checked WHERE length(fp) = 8").fetchone()[0]
                         if "checked" in tables else 0)
                pairs = (conn.execute("SELECT COUNT(*) FROM distinct_pairs").fetchone()[0]
                         if "distinct_pairs" in tables else 0)
        except sqlite3.Error:
            return found
        if marks:
            found.append(f"{marks} checked mark(s) in .kblam/{CACHE_NAME}")
        if pairs:
            found.append(f"{pairs} resolution(s) in .kblam/{CACHE_NAME}")
    return found


def old_state_problem(stale: list[str]) -> str:
    """Why a command will not read old-format state, and what fixes it."""
    return (f".kblam/ on this machine holds state recorded before fingerprint v2 ({'; '.join(stale)}). Read "
            f"now, every open item would close as if its finding had changed and every finding would be checked "
            f"with Jev again, so it is migrated first: run kblam upgrade (SPEC §7)")


# --- the upgrade -------------------------------------------------------------------------------------


def upgrade(cfg: Config) -> UpgradeResult:
    """`kblam upgrade` (SPEC §7): the steps in this module's docstring, under the lock. A [jev] table that does
    not load is a ConfigError before anything is written."""
    settings = jev_settings(cfg)
    with kb_lock(cfg, "upgrade"):
        return _upgrade(cfg, settings)


def _upgrade(cfg: Config, settings: JevSettings) -> UpgradeResult:
    # What can refuse is read before anything is written, so a refused upgrade leaves everything as it was: a
    # damaged review.jsonl or kblam.resolutions.jsonl, or a cache keyed by the integer prompt_version of old.
    items = review.load_items(cfg)
    have = resolutions.by_key(resolutions.load(cfg))
    cache = cfg.state_dir / CACHE_NAME
    if cache.is_file():
        PairCache(cache)  # CacheSchemaError for that cache (SPEC §9 "Upgrading")
    result = UpgradeResult()
    view = load_view(cfg)
    separator = cfg.scope_separator
    counts: dict[str, int] = {}
    for f in view.findings:
        counts[f.file_id] = counts.get(f.file_id, 0) + 1
    readable = {f.file_id: f for f in view.findings if f.ok and counts[f.file_id] == 1}

    # 1. depends_on stamps in the knowledge base, then the edit records of the files rewritten
    writes = []
    for finding in view.findings:
        data, done, stale = _restamp(finding, readable, separator, finding.path)
        result.stale += stale
        if done:
            writes.append((finding, data))
            result.restamped += done
    if writes:
        clean = clean_before_v2(cfg, view, bool(view.records), creates_registry=False)
        recorded = read_recorded(cfg)
        if recorded is None:
            kept = KEPT_NONE_RECORDS if view.records else KEPT_NONE_INVALID
        elif recorded[0] == 1:
            kept = KEPT_CHANGED_V1 if recorded[2] != tree_digest(view) else KEPT_OLD_RECORDS
        else:
            kept = KEPT_CHANGED
        # Only upgrade may bridge a matching format-1 marker to format 2, and only before records
        # exist: that old marker cannot vouch for the review root or its registered IDs.
        if (recorded is not None and recorded[0] == 1 and recorded[2] == tree_digest(view)
                and not view.records):
            try:
                registered = registry.read_ids(cfg)
            except ValueError:
                # A damaged registry cannot authorize the bridge; leave the old marker and warn as
                # for a nonempty registry, while the rest of the upgrade continues.
                clean, kept = False, KEPT_OLD_UNREADABLE
            else:
                clean, kept = not registered, KEPT_OLD_REGISTRY
        for finding, data in writes:
            atomic_write(cfg.repo_root / finding.path, data)
            if _follow_edit_record(cfg, finding, data):
                result.edit_records.append(finding.file_id)
        result.recorded = record_after_write_v2(cfg, clean, "upgrade", creates_registry=False)
        result.kept_because = None if result.recorded else kept

    # 2. staged findings
    staged: dict[str, Finding] = {}
    for path in sorted(cfg.staging_dir.glob("F-*.md")) if cfg.staging_dir.is_dir() else ():
        if not FILENAME_RE.match(path.name):
            continue
        shown = display_path(cfg, path)
        finding = parse_finding(shown, path.read_bytes())
        data, done, _stale = _restamp(finding, readable, separator, shown)
        if done:
            atomic_write(path, data)
            result.staged += done
            finding = parse_finding(shown, data)
        if finding.ok:
            staged[finding.file_id] = finding

    # 3. review items
    _upgrade_items(cfg, view, readable, staged, items, result)

    # 4-6. pairs.sqlite: checked marks, resolutions, cached answers
    if cache.is_file():
        with closing(sqlite3.connect(cache, timeout=30)) as conn, conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "checked" in tables:
                _upgrade_marks(conn, readable, separator, result)
            if "distinct_pairs" in tables:
                _move_resolutions(cfg, conn, readable, separator, have, result)
            if "answers" in tables:
                _rekey_answers(conn, settings, readable, separator, result)

    # 7. the prompt ids, for a person to record, while the combined prompt_id vouches for this wording: one that
    # does not means the thresholds were calibrated on other wording, and recording the current ids would apply
    # them to wording nobody calibrated (SPEC §6.4)
    thresholds = cfg.jev.get("thresholds") if isinstance(cfg.jev, dict) else None
    if isinstance(thresholds, dict) and "prompt_id" in thresholds:
        if thresholds["prompt_id"] == settings.prompt_id:
            result.prompt_ids = (settings.relation_prompt_id, settings.revision_prompt_id)
        else:
            result.prompt_id_differs = (str(thresholds["prompt_id"]), settings.prompt_id)
    return result


def _restamp(finding: Finding, readable: dict[str, Finding], separator: str,
             shown: str) -> tuple[bytes, list[Restamp], list[Restamp]]:
    """(the file's bytes after, the stamps replaced, the stale ones) for each v1 `depends_on` value of
    `finding`: one that is its target's current v1 fingerprint gets the target's v2 fingerprint, each written
    as `ack` writes one, byte for byte (store.stamp_dependency); a stale one stays."""
    depends = finding.meta.get("depends_on") if finding.ok and isinstance(finding.meta, dict) else None
    if not isinstance(depends, dict):
        return finding.raw, [], []
    done, stale = [], []
    for key, value in list(depends.items()):
        if not isinstance(key, str) or not is_v1_fingerprint(value):
            continue
        target = readable.get(key)
        if target is None or fingerprint_v1(target) != value:
            stale.append(Restamp(shown, key, str(value)))
            continue
        new = fingerprint(target, separator)
        finding = parse_finding(finding.path, stamp_dependency(finding, key, new, shown))
        done.append(Restamp(shown, key, str(value), new))
    return finding.raw, done, stale


def _follow_edit_record(cfg: Config, before: Finding, data: bytes) -> bool:
    """When `kblam edit` recorded exactly the bytes the re-stamp replaced, record the new ones: the change was
    only a stamp's format, and the staged copy was re-stamped the same way, so put's edit-base guard should
    not refuse the edit (SPEC §7)."""
    path = edit_base_path(cfg, before.file_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if (not isinstance(record, dict) or record.get("path") != before.path
            or record.get("sha256") != hashlib.sha256(before.raw).hexdigest()):
        return False
    record["sha256"] = hashlib.sha256(data).hexdigest()
    atomic_write(path, (json.dumps(record, indent=2) + "\n").encode("utf-8"))
    return True


def _upgrade_items(cfg: Config, view, readable: dict[str, Finding], staged: dict[str, Finding],
                   items: list[review.ReviewItem], result: UpgradeResult) -> None:
    """An open item whose every v1 side is its finding's current v1 version (a rejected item's new side is its
    staged file) moves to v2 fingerprints and takes the ID a check would give it now. The other open review
    and unchecked items close as their finding changed since they were raised, as they would have under v1
    (review.reconcile); a rejected one stays open until a put of its finding closes it (SPEC §6.4).
    `items` is every item in review.jsonl."""
    separator = cfg.scope_separator
    touched = False
    for item in items:
        if not item.open:
            continue
        moves = {}
        for id_field, fp_field in (("new_id", "new_fp"), ("existing_id", "existing_fp")):
            finding_id, fp = getattr(item, id_field), getattr(item, fp_field)
            if finding_id is None or not is_v1_fingerprint(fp):
                continue
            in_staging = item.kind == "rejected" and id_field == "new_id"  # a rejected finding is staged
            source = (staged if in_staging else readable).get(finding_id)
            moves[fp_field] = (fingerprint(source, separator)
                               if source is not None and fingerprint_v1(source) == fp else None)
        if not moves or None in moves.values():
            continue
        for fp_field, fp in moves.items():
            setattr(item, fp_field, fp)
        new_id = review.item_id_of(item)
        if new_id != item.id:
            result.items_rekeyed.append((item.id, new_id))
            item.id = new_id
        touched = True
    was_open = {item.id for item in items if item.open}
    review.reconcile(items, review.current_fingerprints(view))
    result.items_closed = [item.id for item in items if item.id in was_open and not item.open]
    if touched or result.items_closed:
        review.save_items(cfg, items)


def _upgrade_marks(conn, readable: dict[str, Finding], separator: str, result: UpgradeResult) -> None:
    """A v1 checked mark on a finding's current version becomes a v2 mark; the others were for versions no
    longer in the KB."""
    rows = conn.execute("SELECT finding_id, fp, created FROM checked WHERE length(fp) = 8").fetchall()
    for finding_id, fp, created in rows:
        finding = readable.get(finding_id)
        if finding is not None and fingerprint_v1(finding) == fp:
            conn.execute("INSERT OR REPLACE INTO checked VALUES (?, ?, ?)",
                         (finding_id, fingerprint(finding, separator), created))
            result.marks_moved += 1
        else:
            result.marks_dropped += 1
    conn.execute("DELETE FROM checked WHERE length(fp) = 8")


def _move_resolutions(cfg: Config, conn, readable: dict[str, Finding], separator: str, have: dict,
                      result: UpgradeResult) -> None:
    """Each row of `distinct_pairs` whose sides are their findings' current v1 versions becomes a line of
    kblam.resolutions.jsonl (SPEC §6.4): `distinct` for a pair, `not_revision` for a row with one side, keyed
    by state hash, with its reason and date. A row whose side changed since had already lapsed. Every row is
    then deleted, so none moves twice. `have` is the file's resolutions, by resolutions.key."""
    for a_id, a_fp, b_id, b_fp, reason, created in conn.execute(
            "SELECT a_id, a_fp, b_id, b_fp, reason, created FROM distinct_pairs").fetchall():
        sides = [(i, fp) for i, fp in ((a_id, a_fp), (b_id, b_fp)) if i]  # a revision row's other side is empty
        if not sides or any(i not in readable or fingerprint_v1(readable[i]) != fp for i, fp in sides):
            result.resolutions_lapsed += 1
            continue
        kind = resolutions.NOT_REVISION if len(sides) == 1 else resolutions.DISTINCT
        states = [(i, Side.of(readable[i], separator).state_hash) for i, _ in sides]
        key = resolutions.key(kind, states)
        if key in have:
            result.resolutions_present += 1
            continue
        day = created[:10] if isinstance(created, str) and DATE_RE.fullmatch(created[:10]) else _today()
        text = " ".join(str(reason).split()) or "recorded before M6.10 without a reason"
        line = resolutions.Resolution(tuple(sorted(states)), kind, text, day)
        resolutions.append(cfg, line)
        have[key] = line
        result.resolutions_moved += 1
    conn.execute("DELETE FROM distinct_pairs")


def _rekey_answers(conn, settings: JevSettings, readable: dict[str, Finding], separator: str,
                   result: UpgradeResult) -> None:
    """Cached answers written before M6.10 are keyed by the combined prompt id and v1 fingerprints. One under
    the current wording whose sides are current findings moves to its question's own prompt id and the sides'
    state hashes (SPEC §6.5); an answer already there under the new key stays. The rest answered a wording or
    a version no longer in use, and are dropped."""
    by_v1 = {fingerprint_v1(f): f for f in readable.values()}
    rows = conn.execute("SELECT expected_model, prompt_id, kind, existing_fp, new_fp, served_model, answer, call, "
                        "created FROM answers WHERE length(new_fp) = 8").fetchall()
    for model, prompt_id, kind, existing_fp, new_fp, served, answer, call, created in rows:
        new = by_v1.get(new_fp)
        existing = by_v1.get(existing_fp) if kind == RELATION_KEY else None
        usable = (prompt_id == settings.prompt_id and new is not None
                  and (existing is not None if kind == RELATION_KEY else kind == REVISION_KEY and not existing_fp))
        if not usable:
            result.answers_dropped += 1
            continue
        key = (model, question_prompt_id(settings, kind), kind,
               Side.of(existing, separator).state_hash if existing is not None else "",
               Side.of(new, separator).state_hash)
        conn.execute("INSERT OR IGNORE INTO answers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (*key, served, answer, call, created))
        result.answers_moved += 1
    conn.execute("DELETE FROM answers WHERE length(new_fp) = 8")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
