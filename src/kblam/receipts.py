"""Allocation and edit-base receipts of review records, `.kblam/review-receipts/` (SPEC §5.1.5).

kblam's state, not the author's: the allocation receipt `<ID>.json` is never rewritten; the edit-base
receipt `<ID>.edit-base.json` holds the sha256 of the installed record's bytes when it was copied.
"""

from __future__ import annotations

import json

from kblam.config import Config


def write_allocation(cfg: Config, rec_id: str, payload: dict) -> None:
    """Create `<ID>.json` (JSON, indent 2, sorted keys, final newline). Raises FileExistsError if it
    exists: an allocation receipt is never rewritten."""
    path = cfg.review_receipts_dir / f"{rec_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as handle:
        handle.write((json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def read_allocation(cfg: Config, rec_id: str) -> dict | None:
    """The allocation receipt, or None if absent or unreadable."""
    return _read(cfg.review_receipts_dir / f"{rec_id}.json")


def write_edit_base(cfg: Config, rec_id: str, sha256: str) -> None:
    from kblam.store import atomic_write  # avoid an import cycle

    data = {"id": rec_id, "sha256": sha256}
    atomic_write(cfg.review_receipts_dir / f"{rec_id}.edit-base.json",
                 (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def read_edit_base(cfg: Config, rec_id: str) -> str | None:
    """The recorded sha256, or None if there is no readable edit-base receipt."""
    data = _read(cfg.review_receipts_dir / f"{rec_id}.edit-base.json")
    value = data.get("sha256") if data else None
    return value if isinstance(value, str) else None


def remove_edit_base(cfg: Config, rec_id: str) -> None:
    (cfg.review_receipts_dir / f"{rec_id}.edit-base.json").unlink(missing_ok=True)


def _read(path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
