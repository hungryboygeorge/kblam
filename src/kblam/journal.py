"""`.kblam/journal.json`: marks a multi-file write in progress (SPEC §5.2.6 Interrupted writes).

A write that changes more than one file among the two roots and the registry calls begin() first,
then writes each file (records and findings, then indexes, then the registry), then tree.hash, then
end(). A command that takes the lock calls recover() first.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from kblam import records, registry
from kblam.config import Config

TREE_HASH_NAME = "tree.hash"
KEYS = ("command", "paths", "tree_hash")


def _shown(cfg: Config) -> str:
    from kblam.store import display_path  # avoid an import cycle

    return display_path(cfg, cfg.journal_path)


def _write(path: Path, data: bytes) -> None:
    from kblam.store import atomic_write  # avoid an import cycle

    atomic_write(path, data)


def _tree_hash_path(cfg: Config) -> Path:
    return cfg.state_dir / TREE_HASH_NAME


def _read(cfg: Config) -> dict:
    """The journal as data. An unreadable one raises ValueError naming the file."""
    try:
        data = json.loads(cfg.journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{_shown(cfg)} cannot be read ({exc}); it is written only by kblam, during a "
                         f"multi-file write") from exc
    if (not isinstance(data, dict) or set(data) != set(KEYS)
            or not isinstance(data["command"], str)
            or not isinstance(data["paths"], list) or not all(isinstance(p, str) for p in data["paths"])
            or not (data["tree_hash"] is None or isinstance(data["tree_hash"], str))):
        raise ValueError(f"{_shown(cfg)} is not a journal kblam wrote; it is written only by kblam, "
                         f"during a multi-file write")
    return data


def begin(cfg: Config, command: str, paths: list[str]) -> None:
    """Write the journal (store.atomic_write): {"command": command, "paths": repo-relative paths it will
    change, "tree_hash": the text of .kblam/tree.hash as found, or null if there is none}."""
    tree_hash_path = _tree_hash_path(cfg)
    recorded = tree_hash_path.read_text(encoding="utf-8") if tree_hash_path.is_file() else None
    record = {"command": command, "paths": list(paths), "tree_hash": recorded}
    _write(cfg.journal_path, (json.dumps(record, indent=2) + "\n").encode("utf-8"))


def end(cfg: Config) -> None:
    """Delete the journal (missing is fine)."""
    cfg.journal_path.unlink(missing_ok=True)


def _record_ids(cfg: Config, paths: list[str]) -> set[str]:
    """The IDs of the journal-listed paths that are record files of the review root and exist on disk."""
    found = set()
    for path in paths:
        head, _, name = path.rpartition("/")
        match = records.FILENAME_RE.match(name)
        if match is None or head != f"{cfg.review_dir}/{records.KINDS[match.group(2)]}":
            continue
        if (cfg.repo_root / path).is_file():
            found.add(match.group(1))
    return found


def recover(cfg: Config, regenerate: Callable[[], None]) -> str | None:
    """Recover from an interrupted write, or return None if there is no journal.

    Calls regenerate() (the caller's regeneration of both indexes from the files present); registers
    (registry) every journal-listed record path that exists; restores .kblam/tree.hash to the recorded
    text (deleting the file if none was recorded); deletes the journal; returns "the interrupted
    <command> may be partial: run kblam validate, fix what it reports, then kblam validate --record".
    If regenerate or any step raises, the journal stays and the exception propagates. Never invents
    file contents.
    """
    if not cfg.journal_path.is_file():
        return None
    record = _read(cfg)
    regenerate()
    found = _record_ids(cfg, record["paths"])
    registered = registry.read_ids(cfg)
    if found and (registered is None or found - registered):
        registry.write_ids(cfg, (registered or set()) | found)
    tree_hash_path = _tree_hash_path(cfg)
    if record["tree_hash"] is None:
        tree_hash_path.unlink(missing_ok=True)
    else:
        _write(tree_hash_path, record["tree_hash"].encode("utf-8"))
    cfg.journal_path.unlink()
    return (f"the interrupted {record['command']} may be partial: run kblam validate, fix what it "
            f"reports, then kblam validate --record")
