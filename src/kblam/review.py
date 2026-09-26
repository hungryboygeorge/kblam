"""`.kblam/review.jsonl` and the commands around it: check, check --pending, audit, resolve (SPEC §6.4,
§6.5, §7; M5).

One JSON object per line, one line per item. A `review` item is a verdict that fired in review mode
(or a reject verdict found on a finding already in the tree); a `rejected` item is a Jev reject
verdict that refused a `put` (§6.4) — its finding is staged, so it is not in the tree and never
fails `validate`; an `unchecked` item is a finding whose check could not get an answer to every
Jev question. An open review or unchecked item makes `kblam validate` fail. Items close
automatically when a side's fingerprint changes (the put of the edited finding re-checks the
pair), by `kblam resolve --distinct`, or, for unchecked items, when a check of that finding gets
every answer. A rejected item closes when a put of its finding succeeds or its staged fingerprint
changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from kblam.check import CONFLICT, REVISION, SAME_FACT, Checker, CheckResult, Verdict
from kblam.config import Config
from kblam.finding import ID_RE, fingerprint, id_number
from kblam.jev import CACHE_NAME, PairCache
from kblam.lock import kb_lock
from kblam.view import KBView, load_view

REVIEW_NAME = "review.jsonl"
# §6.4: the Jev verdicts that refuse a write. `quantity_conflict` is code, not Jev, and records nothing.
JEV_REJECTS = (SAME_FACT, CONFLICT, REVISION)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ReviewError(Exception):
    """A review-workflow request that cannot be carried out; the message says what to do instead."""


@dataclass
class ReviewItem:
    id: str
    kind: str                       # review | rejected | unchecked
    status: str                     # open | closed
    new_id: str
    new_fp: str
    existing_id: str | None = None  # review and rejected items on a pair
    existing_fp: str | None = None
    verdict: str | None = None      # review and rejected items
    winner: str | None = None
    p: float | None = None
    confidence: float | None = None
    noul: float | None = None
    message: str = ""
    created: str = ""
    closed: str | None = None
    close_reason: str | None = None
    # M6.10: each side's state hash (SPEC §6.5), so a resolution can be recorded for a side that is
    # not in the tree (a rejected item's staged finding); and, for a rejected item closed by a put,
    # the fingerprint that went in (`kblam items --reworded`, SPEC §7). None in items written earlier.
    new_state: str | None = None
    existing_state: str | None = None
    closed_fp: str | None = None

    @property
    def open(self) -> bool:
        return self.status == "open"

    def close(self, reason: str) -> None:
        self.status, self.closed, self.close_reason = "closed", _now(), reason

    def describe(self) -> str:
        if self.kind == "unchecked":
            return (f"unchecked {self.id} {self.new_id}: {self.message}; run kblam check --pending once Jev "
                    f"is reachable")
        who = self.new_id + (f" vs {self.existing_id}" if self.existing_id else "")
        scores = ", ".join(f"{k} {v:.2f}" for k, v in (("p", self.p), ("confidence", self.confidence),
                                                       ("noul", self.noul)) if v is not None)
        shown = f"{self.verdict} {who}" + (f" ({scores})" if scores else "")
        if self.kind == "rejected":
            return (f"rejected {self.id} {shown}: {self.message}. The put was refused: send {self.id} to the "
                    f"librarian, which resolves it only when the two findings state distinct facts "
                    f"(kblam resolve {self.id} --distinct \"<reason>\")")
        return (f"review {self.id} {shown}: {self.message}. Edit a finding, or if they are distinct: "
                f"kblam resolve {self.id} --distinct \"<reason>\"")


def _short_hash(parts: list) -> str:
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()[:8]


def _item_id(v: Verdict) -> str:
    """§6.4: the verdict and both (ID, fingerprint) sides, so raising the same item again does not
    duplicate it. A rejected item hashes the same parts as the review item for that pair."""
    return "R-" + _short_hash([v.verdict, v.existing_id, v.existing_fp, v.new_id, v.new_fp])


def review_item(v: Verdict) -> ReviewItem:
    return ReviewItem(_item_id(v), "review", "open", v.new_id, v.new_fp, v.existing_id, v.existing_fp, v.verdict,
                      v.winner, v.p, v.confidence, v.noul, v.message, _now())


def rejected_item(v: Verdict) -> ReviewItem:
    """A Jev reject verdict that refused a `put` (§6.4). The finding is staged, so this item is
    recorded and printed but never fails `validate`; only a put of the finding closes it."""
    return ReviewItem(_item_id(v), "rejected", "open", v.new_id, v.new_fp, v.existing_id, v.existing_fp, v.verdict,
                      v.winner, v.p, v.confidence, v.noul, v.message, _now())


def unchecked_item(result: CheckResult) -> ReviewItem:
    item_id = "U-" + _short_hash([result.finding_id, result.fingerprint])
    reasons = sorted({line.split(": ", 1)[-1] for line in result.unavailable})
    message = (f"Jev could not answer {len(result.unavailable)} question(s) for this finding "
               f"({'; '.join(reasons)})")
    return ReviewItem(item_id, "unchecked", "open", result.finding_id, result.fingerprint, message=message,
                      created=_now())


# --- the file ---------------------------------------------------------------------------------


def _path(cfg: Config):
    return cfg.state_dir / REVIEW_NAME


def load_items(cfg: Config) -> list[ReviewItem]:
    path = _path(cfg)
    if not path.is_file():
        return []
    items = []
    fields = set(ReviewItem.__dataclass_fields__)
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or not fields >= set(record):
                raise ValueError("not a review item")
            items.append(ReviewItem(**record))
        except (ValueError, TypeError) as exc:
            raise ReviewError(f".kblam/{REVIEW_NAME}:{number} cannot be read ({exc}); it is written only by "
                              f"kblam, so restore it or delete the line and run kblam check") from None
    return items


def save_items(cfg: Config, items: list[ReviewItem]) -> None:
    from kblam.store import atomic_write  # avoid an import cycle

    text = "".join(json.dumps(asdict(i), sort_keys=True, ensure_ascii=False) + "\n" for i in items)
    atomic_write(_path(cfg), text.encode("utf-8"))


def current_fingerprints(view: KBView) -> dict[str, str]:
    """Fingerprint of each finding whose ID is used once and which parses."""
    counts: dict[str, int] = {}
    for f in view.findings:
        counts[f.file_id] = counts.get(f.file_id, 0) + 1
    return {f.file_id: fingerprint(f) for f in view.findings if f.ok and counts[f.file_id] == 1}


def reconcile(items: list[ReviewItem], fps: dict[str, str]) -> None:
    """Close open items whose findings no longer have the fingerprints the item was raised at. A
    rejected item is left alone: its finding is staged, so it is never in `fps`, and put closes it."""
    for item in items:
        if not item.open or item.kind == "rejected":
            continue
        for side, fp in ((item.new_id, item.new_fp), (item.existing_id, item.existing_fp)):
            if side is not None and fps.get(side) != fp:
                item.close(f"{side} changed" if side in fps else f"{side} is no longer in the KB")
                break


def open_items(cfg: Config, view: KBView) -> list[ReviewItem]:
    """Open review and unchecked items against the tree in `view` (read-only: auto-closing is not
    written back). Rejected items are not against the tree and never fail `validate`."""
    items = load_items(cfg)
    reconcile(items, current_fingerprints(view))
    return [i for i in items if i.open and i.kind != "rejected"]


@dataclass
class Recorded:
    result: CheckResult
    opened: list[ReviewItem]           # review items open for this result's verdicts
    unchecked: ReviewItem | None


def record(cfg: Config, view: KBView, results: list[CheckResult], *, reject_as_review: bool) -> list[Recorded]:
    """Write what checks found into review.jsonl; call under the lock, with `view` the tree as it now is.

    Review-mode verdicts become review items; reject verdicts too when `reject_as_review` (the finding
    is already in the tree). A result missing any answer opens an unchecked item; a complete one
    closes the finding's unchecked items and is remembered in pairs.sqlite for `kblam check`.
    """
    cache = PairCache(cfg.state_dir / CACHE_NAME)
    items = {i.id: i for i in load_items(cfg)}
    out = []
    for result in results:
        opened = []
        for verdict in result.verdicts:
            if verdict.mode == "reject" and not reject_as_review:
                continue
            item = review_item(verdict)
            if item.id in items and items[item.id].open and items[item.id].kind == item.kind:
                item = items[item.id]
            items[item.id] = item
            opened.append(item)
        unchecked = None
        if result.unavailable:
            unchecked = unchecked_item(result)
            if unchecked.id in items and items[unchecked.id].open and items[unchecked.id].kind == "unchecked":
                items[unchecked.id].message = unchecked.message  # the latest failure's reason
                unchecked = items[unchecked.id]
            items[unchecked.id] = unchecked
        else:
            for item in items.values():
                if item.open and item.kind == "unchecked" and item.new_id == result.finding_id:
                    item.close("checked")
            if result.jev_enabled:
                cache.mark_checked(result.finding_id, result.fingerprint)
        out.append(Recorded(result, opened, unchecked))
    reconcile(list(items.values()), current_fingerprints(view))
    save_items(cfg, list(items.values()))
    return out


def record_rejected(cfg: Config, check: CheckResult) -> list[ReviewItem]:
    """`put` was refused by the Jev check (§6.4): record each rejecting verdict as a rejected item and
    close the finding's open rejected items raised at another fingerprint of the staged file. An item
    already open on the same verdict and pair is left as it stands. A `quantity_conflict` records
    nothing. Call under the lock; `findings/` is untouched."""
    items = {i.id: i for i in load_items(cfg)}
    reopened, closed = [], []
    for verdict in check.verdicts:
        if verdict.mode != "reject" or verdict.verdict not in JEV_REJECTS:
            continue
        item = rejected_item(verdict)
        if item.id in items and items[item.id].open:
            item = items[item.id]  # the same verdict on the same pair at the same fingerprints
        else:
            items[item.id] = item
            reopened.append(item)
    for item in items.values():
        if (item.open and item.kind == "rejected" and item.new_id == check.finding_id
                and item.new_fp != check.fingerprint):
            item.close("the staged finding changed")
            closed.append(item)
    if reopened or closed:
        save_items(cfg, list(items.values()))
    return reopened


def close_rejected(cfg: Config, finding_id: str) -> list[ReviewItem]:
    """A successful `put` of `finding_id` closes its open rejected items (§6.4)."""
    items = load_items(cfg)
    closed = []
    for item in items:
        if item.open and item.kind == "rejected" and item.new_id == finding_id:
            item.close(f"{finding_id} was put")
            closed.append(item)
    if closed:
        save_items(cfg, items)
    return closed


# --- commands ---------------------------------------------------------------------------------


def _findings_by_id(view: KBView) -> dict:
    fps = current_fingerprints(view)
    return {f.file_id: f for f in view.findings if f.file_id in fps}


def check_findings(cfg: Config, ids: list[str] | None, *, command: str = "check",
                   client_factory=None) -> list[Recorded]:
    """`kblam check [F-…]`: check the named findings, or every finding with no complete check at its
    current fingerprint. Nothing under findings/ is written."""
    view = load_view(cfg)
    by_id = _findings_by_id(view)
    if ids:
        for finding_id in ids:
            if not ID_RE.match(finding_id):
                raise ReviewError(f"{finding_id!r} is not a finding ID like F-0137")
            if finding_id not in by_id:
                raise ReviewError(f"{finding_id} is not a readable finding in {cfg.findings_dir}/; run kblam validate")
        targets = [by_id[i] for i in dict.fromkeys(ids)]
    with Checker(cfg, client_factory) as checker:
        if not ids:
            targets = [f for f in by_id.values() if not checker.cache.was_checked(f.file_id, fingerprint(f))]
        results = [checker.check(view, f, command) for f in targets]  # Jev is asked outside the lock
    with kb_lock(cfg, command):
        return record(cfg, load_view(cfg), results, reject_as_review=True)


def check_pending(cfg: Config, *, client_factory=None) -> list[Recorded]:
    """`kblam check --pending`: re-run the check of each finding with an open unchecked item."""
    view = load_view(cfg)
    by_id = _findings_by_id(view)
    pending = [i.new_id for i in open_items(cfg, view) if i.kind == "unchecked"]
    targets = [by_id[i] for i in dict.fromkeys(pending) if i in by_id]
    with Checker(cfg, client_factory) as checker:
        results = [checker.check(view, f, "check --pending") for f in targets]
    with kb_lock(cfg, "check --pending"):
        return record(cfg, load_view(cfg), results, reject_as_review=True)


def audit(cfg: Config, *, client_factory=None) -> list[Recorded]:
    """`kblam audit`: ask the candidate pairs with no cache entry for the current fingerprints, model
    and prompt id, and record what fires."""
    view = load_view(cfg)
    with Checker(cfg, client_factory) as checker:
        results = checker.audit(view)
    with kb_lock(cfg, "audit"):
        return record(cfg, load_view(cfg), results, reject_as_review=True)


def resolve(cfg: Config, item_id: str, reason: str) -> list[ReviewItem]:
    """`kblam resolve <id> --distinct "<reason>"`: close a review or rejected item and store the reason
    in the pair cache, so the pair at these fingerprints is not raised again (a rejected item's staged
    finding then puts through). Returns every item it closed (all open review and rejected items on
    the same pair at the same fingerprints)."""
    reason = " ".join(reason.split())
    if not reason:
        raise ReviewError("--distinct needs a reason: say why the two findings state different facts")
    with kb_lock(cfg, f"resolve {item_id}"):
        items = load_items(cfg)
        reconcile(items, current_fingerprints(load_view(cfg)))
        item = next((i for i in items if i.id == item_id), None)
        if item is None:
            raise ReviewError(f"no item {item_id} in .kblam/{REVIEW_NAME}; kblam validate lists the open ones")
        if item.kind == "unchecked":
            raise ReviewError(f"{item_id} is an unchecked item; it closes when kblam check --pending gets an "
                              f"answer to every Jev question")
        if not item.open:
            raise ReviewError(f"{item_id} is already closed ({item.close_reason})")
        new = (item.new_id, item.new_fp)
        other = (item.existing_id, item.existing_fp) if item.existing_id else None
        PairCache(cfg.state_dir / CACHE_NAME).mark_distinct(new, other, reason)
        sides = {new, other}
        closed = []
        for i in items:
            if i.open and i.kind in ("review", "rejected") and {
                    (i.new_id, i.new_fp),
                    (i.existing_id, i.existing_fp) if i.existing_id else None} == sides:
                i.close(f"distinct: {reason}")
                closed.append(i)
        save_items(cfg, items)
        return sorted(closed, key=lambda i: (i.id != item_id, id_number(i.new_id)))
