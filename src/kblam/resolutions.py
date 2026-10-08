"""`kblam.resolutions.jsonl`: the committed resolutions of items Jev misread (SPEC §6.4 "Resolutions", §3).

`kblam resolve` appends one JSON object per line and never rewrites a line: the sides, as [finding ID,
state hash] pairs (sorted; one side for kind `not_revision`), the kind (`distinct` for an item on a
pair, `not_revision` for a `revision` item), the reason and the date (UTC). The file sits at the
repository root and is committed, and git merges it with the union driver, so a resolution reaches
every clone. Its sides are state hashes (§6.5), so a resolution survives an edit Jev does not see
(evidence, quantities, label, title) and lapses when the claim or the scope of a side changes. No
resolution covers a `quantity_conflict` (§6.3).

A line that is not such an object is an error naming the file and the line, never skipped: skipping it
would raise again every item it resolved, and hide that the file was damaged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from kblam.config import RESOLUTIONS_NAME, Config, ConfigError
from kblam.finding import ID_RE

DISTINCT = "distinct"            # an item on a pair: the two findings state distinct facts
NOT_REVISION = "not_revision"    # a `revision` item: the finding states a fact directly
KINDS = (DISTINCT, NOT_REVISION)
KEYS = ("date", "kind", "reason", "sides")
STATE_HASH_RE = re.compile(r"[0-9a-f]{12}")   # jev_prompts.state_hash
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ResolutionError(ConfigError):
    """kblam.resolutions.jsonl cannot be read. Like a broken kblam.toml it is committed project state
    that no check runs without, so every command that reads it stops and names the line (exit 2)."""


@dataclass(frozen=True)
class Resolution:
    sides: tuple[tuple[str, str], ...]   # (finding ID, state hash); one side for NOT_REVISION
    kind: str
    reason: str
    date: str                            # YYYY-MM-DD, UTC

    def line(self) -> str:
        record = {"sides": [list(side) for side in self.sides], "kind": self.kind, "reason": self.reason,
                  "date": self.date}
        return json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"


def resolution(kind: str, sides, reason: str) -> Resolution:
    """A resolution of `kind` recorded today (UTC) on (finding ID, state hash) `sides`."""
    return Resolution(tuple(sorted(tuple(side) for side in sides)), kind, reason,
                      datetime.now(timezone.utc).strftime("%Y-%m-%d"))


def key(kind: str, sides) -> tuple[str, frozenset]:
    """What a check looks a resolution up by: its kind and its sides, unordered."""
    return kind, frozenset(tuple(side) for side in sides)


def by_key(resolutions: list[Resolution]) -> dict[tuple[str, frozenset], Resolution]:
    """`resolutions` by `key`; of two on the same sides, the later line wins."""
    return {key(r.kind, r.sides): r for r in resolutions}


def _damaged(where: str, problem: str) -> ResolutionError:
    return ResolutionError(
        f"{where}: {problem}. Only kblam resolve writes {RESOLUTIONS_NAME}, one resolution per line, and it "
        f"never rewrites a line, so this one was damaged by a merge or a hand edit. kblam never repairs a "
        f"line and the file is not an agent's to edit, so leave it as it is and tell the user to repair "
        f"{where}; kblam will not check findings against a resolution log it cannot read")


def _problem(record) -> str | None:
    """Why `record` is not a resolution as kblam resolve writes it, or None."""
    if not isinstance(record, dict) or set(record) != set(KEYS):
        return f"not a JSON object with exactly the keys {', '.join(KEYS)}"
    kind, sides, reason, day = record["kind"], record["sides"], record["reason"], record["date"]
    if kind not in KINDS:
        return f"kind must be {' or '.join(KINDS)}, got {kind!r}"
    count = 1 if kind == NOT_REVISION else 2
    if not (isinstance(sides, list) and len(sides) == count
            and all(isinstance(side, list) and len(side) == 2 and all(isinstance(v, str) for v in side)
                    and ID_RE.fullmatch(side[0]) and STATE_HASH_RE.fullmatch(side[1]) for side in sides)):
        return (f"sides must be {count} [finding ID, state hash] pair(s) for kind {kind}, such as "
                f"[\"F-0137\", \"6d79e4e0e409\"], got {sides!r}")
    if not isinstance(reason, str) or not reason.strip():
        return "reason must be a non-empty string"
    if not isinstance(day, str) or not DATE_RE.fullmatch(day):
        return f"date must be YYYY-MM-DD, got {day!r}"
    try:
        date.fromisoformat(day)
    except ValueError:
        return f"date {day!r} is not a calendar date"
    return None


def _parse(data: bytes) -> list[Resolution]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        number = data[:exc.start].count(b"\n") + 1
        raise _damaged(f"{RESOLUTIONS_NAME}:{number}", "not UTF-8 text") from None
    resolutions = []
    # Only "\n" ends a line: a reason may hold characters str.splitlines() would split at (U+2028).
    for number, line in enumerate(text.split("\n"), 1):
        line = line.removesuffix("\r")  # a checkout with CRLF line endings
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            problem = f"not JSON ({exc})"
        else:
            problem = _problem(record)
        if problem:
            raise _damaged(f"{RESOLUTIONS_NAME}:{number}", problem)
        resolutions.append(Resolution(tuple(tuple(side) for side in record["sides"]), record["kind"],
                                      record["reason"], record["date"]))
    return resolutions


def _read(cfg: Config) -> bytes:
    try:
        return cfg.resolutions_path.read_bytes()
    except FileNotFoundError:
        return b""
    except OSError as exc:
        raise ResolutionError(f"cannot read {RESOLUTIONS_NAME}: {exc.strerror}; leave it as it is and tell "
                              f"the user") from None


def load(cfg: Config) -> list[Resolution]:
    """Every resolution in the file, in file order; [] while there is no file. A line that is not a
    resolution raises ResolutionError naming it."""
    return _parse(_read(cfg))


def append(cfg: Config, new: Resolution) -> None:
    """Add `new` as the file's last line. Call under the lock (kblam resolve holds it). The earlier lines
    are checked, so a damaged file is reported rather than extended, and copied byte for byte; the file is
    then replaced whole with store.atomic_write, not opened for appending, so no reader and no crash sees
    half a line, on Windows as elsewhere."""
    from kblam.store import atomic_write  # avoid an import cycle: store imports review, which imports this

    data = _read(cfg)
    _parse(data)
    if data and not data.endswith(b"\n"):
        data += b"\n"
    atomic_write(cfg.resolutions_path, data + new.line().encode("utf-8"))
