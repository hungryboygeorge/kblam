"""The shared frame of every locked command and every write to the two roots (SPEC §5.1.6).

`locked` takes `.kblam/lock`, recovers an interrupted write, and (for a mutating command) refuses a
changed review root. `apply` writes a planned change in the SPEC's order: the journal when more than
one file among the two roots and the registry changes, then records and findings, then indexes, then
the registry, then tree.hash, then the journal's removal. Finding writes (store.py), record writes
(review_write.py) and staging (review_stage.py) all go through here, so the order lives in one place.

A mutating command is one that writes `findings/`, the review root, the registry or tree.hash. Staging
commands (`new`, `edit`, `challenge new`/`edit`, `task new`/`edit`, `use review`) are not: they write
only `.kblam/staging/`, `.kblam/review-staging/` and receipts, so they recover a journal but never
refuse a root change.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kblam import journal, registry, treehash
from kblam.config import Config
from kblam.lock import kb_lock


def regenerate_indexes(cfg: Config) -> None:
    """Rewrite both indexes from the files present (journal recovery). The review index is written only
    when the review root exists or holds records, so a KB with no records gains no review index."""
    from kblam.index import generate_index
    from kblam.review_index import generate_review_index
    from kblam.store import atomic_write
    from kblam.view import load_view

    view = load_view(cfg)
    atomic_write(cfg.repo_root / view.index_path, generate_index(view))
    if cfg.review_path.is_dir() or view.records:
        atomic_write(cfg.repo_root / view.review_index_path, generate_review_index(view))


def mutation_refusal(cfg: Config) -> str | None:
    """Why a mutating command must refuse before reading anything else, or None: the review root in
    kblam.toml differs from the one a format-2 tree.hash records (treehash.root_problem, SPEC §5.1.6),
    or the registry cannot be read (its ValueError message)."""
    try:
        registered = registry.read_ids(cfg)
    except ValueError as exc:
        return str(exc)
    return treehash.root_problem(cfg, registered)


@contextmanager
def locked(cfg: Config, command: str, *, mutating: bool) -> Iterator[None]:
    """Hold `.kblam/lock` for the command's read-validate-write span.

    Just after the lock is taken, a journal left by an interrupted write is recovered
    (journal.recover with regenerate_indexes) and its message is printed to stderr as
    "kblam <command>: <message>". A failed recovery leaves the journal and raises StoreError with the
    reason. Then, for a mutating command, mutation_refusal raises StoreError. Raises lock.LockError on a
    lock timeout, as kb_lock does.
    """
    from kblam.store import StoreError

    with kb_lock(cfg, command):
        try:
            message = journal.recover(cfg, lambda: regenerate_indexes(cfg))
        except (OSError, ValueError) as exc:
            raise StoreError(f"cannot recover the interrupted write recorded in .kblam/journal.json: {exc}; "
                             f"the journal is kept") from exc
        if message:
            print(f"kblam {command}: {message}", file=sys.stderr)
        if mutating:
            problem = mutation_refusal(cfg)
            if problem:
                raise StoreError(problem)
        yield


def registry_after(cfg: Config, present: set[str], register: set[str]) -> set[str] | None:
    """The registry a write should leave, or None when it should not be written: the current registry
    (or, when there is none, the IDs of the records present, per SPEC §5.1.6 "at its first write")
    plus `register`. None when that equals the registry on disk, or when there is no registry, no record
    is present and nothing is registered (a KB that has never had a record gets no registry file)."""
    current = registry.read_ids(cfg)
    if current is None:
        wanted = set(present) | set(register)
        return wanted or None
    wanted = current | set(register)
    return None if wanted == current else wanted


def apply(cfg: Config, command: str, changes: list[tuple[str, bytes | None]], *,
          registry_ids: set[str] | None, clean_before: bool) -> bool:
    """Write a planned change and return whether tree.hash advanced (treehash.record_after_write_v2).

    `changes` are (repo-relative POSIX path, bytes or None to delete), records and findings first, then
    indexes, in the order they are written; `registry_ids` is registry_after's result. When more than
    one file among the changes and the registry is written, journal.begin(cfg, command, paths) comes
    first (paths = the changed paths, then the registry's path) and journal.end last. Each file goes
    through store.atomic_write; a deleted file under the findings root also removes its topic folder
    when that is left empty. `clean_before` is treehash.clean_before_v2 of the tree as read under the
    lock, before the write. An exception part-way leaves the journal for the next locked command.
    """
    from kblam.store import atomic_write, display_path

    registry_path = display_path(cfg, cfg.review_ids_path)
    paths = [path for path, _data in changes] + ([registry_path] if registry_ids is not None else [])
    journaled = len(paths) > 1
    if journaled:
        journal.begin(cfg, command, paths)
    for path, data in changes:
        target = cfg.repo_root / path
        if data is not None:
            atomic_write(target, data)
            continue
        target.unlink(missing_ok=True)
        parent = target.parent
        if (parent != cfg.findings_path and _under(parent, cfg.findings_path)
                and parent.is_dir() and not any(parent.iterdir())):
            parent.rmdir()
    if registry_ids is not None:
        registry.write_ids(cfg, registry_ids)
    recorded = treehash.record_after_write_v2(cfg, clean_before, command)
    if journaled:
        journal.end(cfg)
    return recorded


def _under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True
