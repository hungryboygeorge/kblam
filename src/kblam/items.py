"""`kblam items [--reworded] [--stats]`: the work queue in `.kblam/review.jsonl` (SPEC §6.4, §7, §10.7; M6.10).

- The plain listing is every open review, rejected and unchecked item, reconciled against the tree as
  `kblam validate` reconciles them, rejected items included: they never fail validate (their finding is
  staged, not in the tree), but an adjudicator still decides each one (§8.1).
- `--reworded` lists each rejected item whose finding later went in at another fingerprint while the
  other side of the pair stayed as it was. Each is either the correction the reject asked for or
  rewording to get past the check, which the skill forbids; the adjudicator reads both findings to tell
  which. `put` records the fingerprint that went in (`closed_fp`) when it closes a rejected item.
- `--stats` counts each verdict's closed items, closed with `kblam resolve --distinct` (a false alarm) or
  otherwise (an edit, a merge or a removal), and applies §10.7: once 20 of the items a verdict is judged
  on have closed, recalibrating it is due when the distinct share exceeds its mode's bar.
"""

from __future__ import annotations

from dataclasses import dataclass

from kblam import review
from kblam.check import CONFLICT, LOW_CONFIDENCE, RESTATES, REVISION, SAME_FACT, parse_policy
from kblam.config import Config
from kblam.jev import jev_settings
from kblam.review import ReviewItem
from kblam.view import KBView, load_view

# §6.4's Jev verdicts, in its table's order. quantity_conflict is code, not Jev, and is not calibrated.
VERDICTS = (SAME_FACT, CONFLICT, REVISION, RESTATES, LOW_CONFIDENCE)
MIN_CLOSED = 20                             # §10.7: closed items of a verdict before its share decides anything
BAR_PERCENT = {"reject": 10, "review": 50}  # §10.7: the share of false alarms each mode's §10.4 bar allows
DISTINCT = "distinct:"                      # the close reason `kblam resolve --distinct` gives an item


def _items(cfg: Config, view: KBView | None) -> list[ReviewItem]:
    """Every item in review.jsonl, open ones reconciled against the tree (read-only: nothing is written back)."""
    items = review.load_items(cfg)
    review.reconcile(items, review.current_fingerprints(view if view is not None else load_view(cfg)))
    return items


def open_items(cfg: Config, view: KBView | None = None) -> list[ReviewItem]:
    """Open review, rejected and unchecked items, reconciled as `review.open_items` does, rejected ones kept."""
    return [item for item in _items(cfg, view) if item.open]


def reworded(cfg: Config, view: KBView | None = None) -> list[ReviewItem]:
    """Rejected items that a put closed at another fingerprint than the one rejected, whose other side, if
    any, is in the KB now at the fingerprint the item recorded (§6.4, §7)."""
    fps = review.current_fingerprints(view if view is not None else load_view(cfg))
    return [item for item in review.load_items(cfg)
            if item.kind == "rejected" and not item.open and item.closed_fp and item.closed_fp != item.new_fp
            and (item.existing_id is None or fps.get(item.existing_id) == item.existing_fp)]


@dataclass(frozen=True)
class VerdictStats:
    """One verdict's closed items (§10.7)."""
    verdict: str
    mode: str | None           # the mode [jev.thresholds] gives it; None when the verdict is disabled
    review_closed: int
    review_distinct: int
    rejected_closed: int
    rejected_distinct: int

    @property
    def counted(self) -> tuple[str, int, int] | None:
        """(kind, closed, closed as distinct) of the items §10.7 judges the verdict on: its rejected items in
        reject mode, its review items in review mode. None when the verdict is disabled."""
        if self.mode == "reject":
            return "rejected", self.rejected_closed, self.rejected_distinct
        if self.mode == "review":
            return "review", self.review_closed, self.review_distinct
        return None

    @property
    def due(self) -> bool:
        """Recalibration is due: at least MIN_CLOSED items counted, and a distinct share above the bar."""
        if self.counted is None:
            return False
        _kind, closed, distinct = self.counted
        return closed >= MIN_CLOSED and distinct * 100 > BAR_PERCENT[self.mode] * closed


def stats(cfg: Config, view: KBView | None = None) -> list[VerdictStats]:
    """The closed items of each verdict in VERDICTS, with its mode from `[jev.thresholds]`. `low_confidence`
    only ever raises review items (§6.4), so it is judged as a review verdict while it is enabled."""
    policy = parse_policy(jev_settings(cfg).thresholds)
    modes = {verdict: threshold.mode for verdict, threshold in policy.verdicts.items()}
    if policy.low_confidence_review is not None:
        modes[LOW_CONFIDENCE] = "review"
    closed = [item for item in _items(cfg, view) if not item.open]
    rows = []
    for verdict in VERDICTS:
        counts = []
        for kind in ("review", "rejected"):
            mine = [item for item in closed if item.kind == kind and item.verdict == verdict]
            counts += [len(mine), sum(1 for item in mine if (item.close_reason or "").startswith(DISTINCT))]
        rows.append(VerdictStats(verdict, modes.get(verdict), *counts))
    return rows


# --- output -------------------------------------------------------------------------------------


def listing_lines(items: list[ReviewItem]) -> list[str]:
    if not items:
        return ["kblam items: no open review, rejected or unchecked items"]
    counts = [(kind, sum(1 for item in items if item.kind == kind)) for kind in ("review", "rejected", "unchecked")]
    return [item.describe() for item in items] + [
        f"kblam items: {len(items)} open item(s) ({', '.join(f'{n} {kind}' for kind, n in counts if n)}); open "
        f"review and unchecked items fail kblam validate, rejected ones do not"]


def reworded_lines(items: list[ReviewItem]) -> list[str]:
    if not items:
        return ["kblam items --reworded: no rejected item's finding went in at another fingerprint while the other "
                "side of the pair stayed as it was"]
    lines = [f"kblam items --reworded: {len(items)} rejected item(s) whose finding later went in at another "
             f"fingerprint while the other side of the pair stayed as it was. Each is either the correction the "
             f"reject asked for or rewording to pass the check; read both findings to tell which:"]
    for item in items:
        pair = f"{item.new_id} vs {item.existing_id}" if item.existing_id else item.new_id
        other = f"; {item.existing_id} is unchanged at {item.existing_fp}" if item.existing_id else ""
        lines.append(f"  {item.id} {item.verdict} {pair}: rejected at {item.new_fp}, went in at {item.closed_fp} "
                     f"({item.closed}){other}")
    lines.append("kblam items --reworded: a correction needs nothing more. Rewording to pass leaves one fact in "
                 "two findings, or two findings in conflict: merge the finding into the one it restates (kblam edit "
                 "that finding, then kblam rm <id> --merged-into <that finding>), or rewrite whichever is wrong; "
                 "for a revision item, rewrite the original finding")
    return lines


def _percent(part: int, whole: int) -> str:
    """One decimal, dropped when it is 0: 3 of 20 is 15%, 5 of 48 is 10.4% (not a rounded 10% beside a 10% bar)."""
    return f"{100 * part / whole:.1f}".removesuffix(".0") + "%"


def stats_lines(rows: list[VerdictStats]) -> list[str]:
    lines = ["kblam items --stats: closed items per verdict; distinct means closed with kblam resolve --distinct, "
             "a false alarm (SPEC §10.7)"]
    for row in rows:
        counts = (f"rejected items {row.rejected_closed} closed ({row.rejected_distinct} distinct, "
                  f"{row.rejected_closed - row.rejected_distinct} otherwise), review items {row.review_closed} "
                  f"closed ({row.review_distinct} distinct, {row.review_closed - row.review_distinct} otherwise)")
        if row.counted is None:
            result = "disabled in [jev.thresholds], so no bar applies"
        else:
            kind, closed, distinct = row.counted
            if closed < MIN_CLOSED:
                result = f"keeps its mode: {closed} of the {MIN_CLOSED} closed {kind} items needed to judge it"
            else:
                share = f"{distinct} of {closed} closed {kind} items distinct ({_percent(distinct, closed)})"
                bar = f"the {BAR_PERCENT[row.mode]}% a {row.mode} verdict allows"
                result = (f"recalibration due: {share}, above {bar}" if row.due
                          else f"keeps its mode: {share}, within {bar}")
        lines.append(f"  {row.verdict} ({row.mode or 'disabled'}): {counts}; {result}")
    due = [row.verdict for row in rows if row.due]
    lines.append("kblam items --stats: " + (
        f"recalibrate {', '.join(due)} (SPEC §10.3-§10.5, on pairs that include long claims); until then "
        f"{'it keeps its' if len(due) == 1 else 'each keeps its'} mode" if due
        else "no verdict is due for recalibration"))
    return lines
