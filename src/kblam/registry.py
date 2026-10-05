"""The record-ID registry, `.kblam/review-ids` (SPEC §5.2.6): every review record ID kblam has written or
accepted, as a sorted JSON list. A registered ID with no record is a K13 error."""

from __future__ import annotations

import json

from kblam.config import Config


def _shown(cfg: Config) -> str:
    from kblam.store import display_path  # avoid an import cycle

    return display_path(cfg, cfg.review_ids_path)


def read_ids(cfg: Config) -> set[str] | None:
    """The registered IDs, or None if the registry does not exist. An unreadable file raises ValueError."""
    path = cfg.review_ids_path
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{_shown(cfg)} cannot be read ({exc}); kblam writes it, so restore it from a "
                         f"backup, or delete it and run kblam validate --record") from exc
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError(f"{_shown(cfg)} is not a JSON list of record IDs; kblam writes it, so restore it "
                         f"from a backup, or delete it and run kblam validate --record")
    return set(data)


def write_ids(cfg: Config, ids: set[str]) -> None:
    """Write the registry: a JSON list of the IDs in sorted order and a final newline, via
    store.atomic_write."""
    from kblam.store import atomic_write  # avoid an import cycle

    atomic_write(cfg.review_ids_path, (json.dumps(sorted(ids)) + "\n").encode("utf-8"))


def ensure(cfg: Config, present: set[str]) -> set[str]:
    """The registry, created from `present` (the IDs of the records in the review root) if absent."""
    registered = read_ids(cfg)
    if registered is None:
        write_ids(cfg, present)
        return set(present)
    return registered


def missing(cfg: Config, present: set[str]) -> list[str]:
    """Registered IDs not in `present`, sorted; [] when there is no registry."""
    return sorted((read_ids(cfg) or set()) - present)
