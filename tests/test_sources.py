"""File references and the one source reader per validation (SPEC §5.2.2 File references, §5.2.6).

Every test runs against repositories the fixtures build under tmp_path, with system and global Git
config ignored: deterministic and offline. `reads` is asserted throughout: each identity is read
once, a miss counts one read too, and a cache hit adds none.
"""

from __future__ import annotations

import hashlib

import pytest

from kblam import gitpin, paths
from kblam.gitpin import GitPin
from kblam.sources import FileRef, SourceReader, State
from kblam.view import KBView, load_view

from conftest import SOURCE_REPO, SOURCE_TEXT, TRACE_PATH, TRACE_TEXT, ZERO64

TRACE_BYTES = TRACE_TEXT.encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ref(path, sha256: str | None = ZERO64, **fields) -> FileRef:
    """A FileRef as records.file_ref builds one: path, sha256 then the four pin fields."""
    pin = {"repo": None, "commit": None, "blob": None, "snapshot": None, **fields}
    return FileRef(path, sha256, pin["repo"], pin["commit"], pin["blob"], pin["snapshot"])


def trace_pin(source_repo) -> tuple[str, GitPin]:
    """The sha256 and the Git pin a command records for the committed trace (gitpin.auto_pin's result)."""
    return digest(TRACE_BYTES), GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))


def toplevel_of(kb, raw: str):
    """The owning worktree of a repo-relative path: the (blob, ...) cache identity's middle field."""
    return gitpin.owning_worktree(paths.resolve(kb.cfg, raw))


def key_of(kb, raw: str) -> str:
    """The canonical key of a path: what a resolved reference's key and the `working` identity hold
    (case-folded on Windows, so tests never spell it out)."""
    return paths.canonical_key(kb.cfg, raw)


def reader_for(kb) -> SourceReader:
    """A reader over the view of the KB as it is on disk, as a validation builds one."""
    return SourceReader(kb.cfg, load_view(kb.cfg))


# --- the four states ------------------------------------------------------------------------------


def test_a_current_source_is_the_working_file(kb):
    raw = "evidence/2026-09-22-ratio/log.txt"
    reader = reader_for(kb)
    resolved = reader.resolve(ref(raw, digest(SOURCE_TEXT.encode())))
    assert resolved.state is State.CURRENT
    assert resolved.data == SOURCE_TEXT.encode()
    assert resolved.message == ""
    assert resolved.key == raw
    assert resolved.error is None
    assert dict(reader.reads) == {("working", raw): 1}


def test_a_git_pin_survives_a_changed_working_file(kb, source_repo):
    """SPEC §5.2.2: the verified blob is read with git cat-file, never by a checkout."""
    sha, pin = trace_pin(source_repo)
    raw = source_repo.kb_path()
    source_repo.write(TRACE_PATH, TRACE_TEXT + "Row 104: added after the commit.\n")
    reader = reader_for(kb)
    resolved = reader.resolve(ref(raw, sha, repo=pin.repo, commit=pin.commit, blob=pin.blob))
    assert resolved.state is State.PINNED
    assert resolved.data == TRACE_BYTES
    assert resolved.message == ""
    assert resolved.error is None
    assert dict(reader.reads) == {("working", raw): 1,
                                  ("blob", str(toplevel_of(kb, raw)), pin.blob): 1}


def test_a_snapshot_pins_bytes_the_working_file_does_not_hold(kb):
    raw = "evidence/2026-09-22-ratio/log.txt"
    snapshot = "evidence/2026-09-22-ratio/snapshots/log-v2.txt"
    data = b"capture log, revision 2\n"
    kb.write(snapshot, data)
    reader = reader_for(kb)
    resolved = reader.resolve(ref(raw, digest(data), snapshot=snapshot))
    assert resolved.state is State.PINNED
    assert resolved.data == data
    assert resolved.message == ""
    assert resolved.key == raw
    assert dict(reader.reads) == {("working", raw): 1, ("snapshot", snapshot, digest(data)): 1}


def test_a_snapshot_with_other_bytes_is_no_copy(kb):
    """SPEC §5.2.2: a snapshot is checked by content hash, so another revision is not the copy."""
    raw = "evidence/2026-09-22-ratio/log.txt"
    kb.write("evidence/2026-09-22-ratio/snapshots/log-v2.txt", b"revision 3\n")
    resolved = reader_for(kb).resolve(ref(raw, digest(b"revision 2\n"),
                                          snapshot="evidence/2026-09-22-ratio/snapshots/log-v2.txt"))
    assert resolved.state is State.UNAVAILABLE
    assert resolved.data is None
    assert "the snapshot is missing or has other bytes" in resolved.message


def test_a_provisional_file_with_other_bytes_is_stale(kb):
    raw = "evidence/2026-09-22-ratio/log.txt"
    reader = reader_for(kb)
    resolved = reader.resolve(ref(raw, digest(b"what the record says the file held\n")))
    assert resolved.state is State.STALE
    assert resolved.data is None
    assert resolved.message == "the working file has other bytes"
    assert resolved.error is None
    assert dict(reader.reads) == {("working", raw): 1}


def test_a_provisional_file_that_is_missing_is_unavailable(kb):
    raw = "evidence/2026-09-22-ratio/gone.txt"
    resolved = reader_for(kb).resolve(ref(raw, ZERO64))
    assert resolved.state is State.UNAVAILABLE
    assert resolved.data is None
    assert resolved.message == "the working file is missing"
    assert resolved.key == raw
    assert resolved.error is None


def test_a_pin_that_cannot_be_checked_here_is_unavailable(kb, source_repo):
    """A well-formed commit ID that the repository does not hold: absent, not structurally wrong."""
    sha, pin = trace_pin(source_repo)
    raw = source_repo.kb_path()
    source_repo.write(TRACE_PATH, "the working file has moved on\n")
    gone = "0" * gitpin.oid_length(source_repo.root)
    reader = reader_for(kb)
    resolved = reader.resolve(ref(raw, sha, repo=SOURCE_REPO, commit=gone, blob=pin.blob))
    assert resolved.state is State.UNAVAILABLE
    assert resolved.data is None
    assert resolved.error is None
    assert gone in resolved.message


def test_a_blob_the_reader_cannot_read_is_unavailable(kb, source_repo, monkeypatch):
    """The reader's own blob cache answers verify_pin's read: None there means the object is gone."""
    sha, pin = trace_pin(source_repo)
    raw = source_repo.kb_path()
    source_repo.write(TRACE_PATH, "the working file has moved on\n")
    reader = reader_for(kb)
    monkeypatch.setattr(reader, "blob", lambda toplevel, oid: None)
    resolved = reader.resolve(ref(raw, sha, repo=pin.repo, commit=pin.commit, blob=pin.blob))
    assert resolved.state is State.UNAVAILABLE
    assert resolved.data is None
    assert resolved.error is None
    assert pin.blob in resolved.message


def test_the_snapshot_is_used_when_the_pin_cannot_be_checked(kb, source_repo):
    sha, pin = trace_pin(source_repo)
    raw = source_repo.kb_path()
    source_repo.write(TRACE_PATH, "the working file has moved on\n")
    snapshot = "evidence/2026-09-22-ratio/snapshots/trace.md"
    kb.write(snapshot, TRACE_BYTES)
    resolved = reader_for(kb).resolve(ref(raw, sha, repo=SOURCE_REPO, commit="0" * 40,
                                          blob=pin.blob, snapshot=snapshot))
    assert resolved.state is State.PINNED
    assert resolved.data == TRACE_BYTES


def test_an_invalid_pin_is_an_error_and_the_working_file_still_wins(kb, source_repo):
    """The pin names a blob the commit's tree does not hold: a structural error at the CURRENT state."""
    sha, pin = trace_pin(source_repo)
    raw = source_repo.kb_path()
    forged = "dead" * 10
    reader = reader_for(kb)
    resolved = reader.resolve(ref(raw, sha, repo=SOURCE_REPO, commit=pin.commit, blob=forged))
    assert resolved.state is State.CURRENT
    assert resolved.data == TRACE_BYTES
    assert resolved.message == ""
    assert resolved.error is not None and forged in resolved.error


# --- paths a reference may not use ---------------------------------------------------------------


def test_a_refused_path_is_unavailable_and_reads_nothing(kb):
    reader = reader_for(kb)
    for raw in ("../outside.md", "/etc/passwd", "C:/notes.md", "notes/a.md:hidden", ""):
        resolved = reader.resolve(ref(raw, ZERO64))
        assert resolved.state is State.UNAVAILABLE, raw
        assert resolved.key is None, raw
        assert resolved.error is not None, raw
        assert resolved.data is None, raw
    assert not reader.reads


def test_a_directory_is_refused(kb):
    raw = "evidence/2026-09-22-ratio"
    reader = reader_for(kb)
    resolved = reader.resolve(ref(raw, ZERO64))
    assert resolved.state is State.UNAVAILABLE
    assert resolved.key == raw
    assert resolved.error == f"{raw!r} is a directory, not a file"
    assert resolved.data is None


def test_non_string_fields_do_not_raise(kb, source_repo):
    """records.schema_issues reports bad types (SPEC §5.2.2), so resolve() only has to stay standing."""
    sha, pin = trace_pin(source_repo)
    raw = source_repo.kb_path()
    reader = reader_for(kb)
    for path in (None, 7, ["notes.md"]):
        resolved = reader.resolve(ref(path, ZERO64))
        assert (resolved.state, resolved.key, resolved.error) == (State.UNAVAILABLE, None, None), path
    assert not reader.reads
    # A sha256 that is not a string has nothing to compare bytes against.
    assert reader.resolve(ref(raw, 42)).state is State.UNAVAILABLE
    assert not reader.reads
    # A pin field of the wrong type is verify_pin's error, and the working file still decides the state.
    junk = reader.resolve(ref(raw, sha, repo=SOURCE_REPO, commit=pin.commit, blob=5))
    assert junk.state is State.CURRENT
    assert junk.error is not None
    # A snapshot path that is not a path is no snapshot, so the reference stays provisional.
    assert reader.resolve(ref(raw, sha, snapshot=9)).state is State.CURRENT


# --- one entry per identity ----------------------------------------------------------------------


def test_two_versions_of_one_path_are_distinct_entries(kb, source_repo):
    """SPEC §5.2.6: a blob is keyed by (worktree toplevel, object ID), so two revisions never share."""
    raw = source_repo.kb_path()
    first, first_blob = source_repo.head(), source_repo.blob(TRACE_PATH)
    second_text = TRACE_TEXT + "Row 104: bytes 0x40 0x41\n"
    second = source_repo.commit(TRACE_PATH, second_text, "row 104")
    second_blob = source_repo.blob(TRACE_PATH)
    reader = reader_for(kb)
    older = reader.resolve(ref(raw, digest(TRACE_BYTES), repo=SOURCE_REPO, commit=first, blob=first_blob))
    newer = reader.resolve(ref(raw, digest(second_text.encode()), repo=SOURCE_REPO,
                               commit=second, blob=second_blob))
    assert older.state is State.PINNED and older.data == TRACE_BYTES
    assert newer.state is State.CURRENT and newer.data == second_text.encode()
    top = str(toplevel_of(kb, raw))
    assert dict(reader.reads) == {("working", raw): 1, ("blob", top, first_blob): 1,
                                  ("blob", top, second_blob): 1}


def test_a_second_resolve_adds_no_reads(kb, source_repo):
    sha, pin = trace_pin(source_repo)
    raw = source_repo.kb_path()
    source_repo.write(TRACE_PATH, TRACE_TEXT + "Row 104: added after the commit.\n")
    reader = reader_for(kb)
    first = reader.resolve(ref(raw, sha, repo=pin.repo, commit=pin.commit, blob=pin.blob))
    counts = dict(reader.reads)
    assert reader.resolve(ref(raw, sha, repo=pin.repo, commit=pin.commit, blob=pin.blob)) == first
    assert dict(reader.reads) == counts


# --- a basis entry on the source itself ----------------------------------------------------------


def test_a_basis_entry_on_the_source_is_read_at_the_sources_pin(kb, source_repo):
    """SPEC §5.2.3 Basis: an entry with the source's key and no pin of its own follows the source's pin,
    so checking out another commit does not make it stale."""
    sha, pin = trace_pin(source_repo)
    raw = source_repo.kb_path()
    source_repo.write(TRACE_PATH, TRACE_TEXT + "Row 104: added after the commit.\n")
    reader = reader_for(kb)
    source = ref(raw, sha, repo=pin.repo, commit=pin.commit, blob=pin.blob)
    basis = ref(raw, sha)
    resolved = reader.resolve(basis, pinned_source=source)
    assert (resolved.state, resolved.data, resolved.message, resolved.error) == \
        (State.PINNED, TRACE_BYTES, "", None)
    assert resolved.key == raw
    counts = dict(reader.reads)
    assert counts == {("working", raw): 1, ("blob", str(toplevel_of(kb, raw)), pin.blob): 1}
    # The source itself reads the same two identities: one read each for the whole validation.
    assert reader.resolve(source) == resolved
    assert dict(reader.reads) == counts


def test_a_basis_sha256_that_differs_from_the_sources_is_an_error(kb, source_repo):
    sha, pin = trace_pin(source_repo)
    raw = source_repo.kb_path()
    source = ref(raw, sha, repo=pin.repo, commit=pin.commit, blob=pin.blob)
    basis = ref(raw, digest(b"what the basis entry claims the source held\n"))
    resolved = reader_for(kb).resolve(basis, pinned_source=source)
    assert resolved.state is State.UNAVAILABLE
    assert resolved.data is None
    assert resolved.key == raw
    assert resolved.error is not None
    assert resolved.error.startswith(f"sha256 {basis.sha256} differs from the source's {sha}")


def test_another_path_is_not_given_the_sources_pin(kb, source_repo):
    """Only an entry on the source itself follows the source's pin; another path resolves on its own."""
    sha, pin = trace_pin(source_repo)
    text = "other notes\n"
    source_repo.commit("notes/other.md", text, "other")
    kb.write("evidence/2026-09-22-ratio/other.md", "edited here, after the commit\n")
    source = ref(source_repo.kb_path(), sha, repo=pin.repo, commit=pin.commit, blob=pin.blob)
    resolved = reader_for(kb).resolve(ref("evidence/2026-09-22-ratio/other.md", digest(text.encode())),
                                      pinned_source=source)
    assert resolved.state is State.STALE
    assert resolved.data is None
    assert resolved.error is None


# --- the view is what a validation sees ----------------------------------------------------------


def test_a_findings_path_comes_from_the_view(kb):
    """SPEC §5.2.6: findings/ is never a source, so its bytes are the view's, not the disk's."""
    path = "findings/calibration/F-0001-ratio.md"
    kb.add("F-0001", "ratio", "The two curves agree to 0.1%.")
    on_disk = (kb.root / path).read_bytes()
    seen = b"edited in the view, not on disk\n"
    view = load_view(kb.cfg)
    view.files[path] = seen
    reader = SourceReader(kb.cfg, view)
    resolved = reader.resolve(ref(path, digest(seen)))
    assert resolved.state is State.CURRENT
    assert resolved.data == seen and resolved.data != on_disk
    assert dict(reader.reads) == {("working", key_of(kb, path)): 1}


def test_a_review_root_path_comes_from_the_review_files(kb):
    """A decision's evidence entry may name a review-root file; the view holds those bytes too."""
    path = f"{kb.cfg.review_dir}/evidence/2026-09-28-capture/run.log"
    data = b"one capture\n"
    view = KBView(cfg=kb.cfg, files={}, review_files={path: data})
    reader = SourceReader(kb.cfg, view)
    resolved = reader.resolve(ref(path, digest(data)))
    assert resolved.state is State.CURRENT
    assert resolved.data == data
    assert dict(reader.reads) == {("working", key_of(kb, path)): 1}


def test_a_path_absent_from_the_view_is_missing(kb):
    """The file is on disk, but no validation sees it: the view decides, and an absent path is missing."""
    path = "findings/calibration/F-0001-ratio.md"
    kb.add("F-0001", "ratio", "The two curves agree to 0.1%.")
    reader = SourceReader(kb.cfg, KBView(cfg=kb.cfg, files={}))
    resolved = reader.resolve(ref(path, digest((kb.root / path).read_bytes())))
    assert resolved.state is State.UNAVAILABLE
    assert resolved.message == "the working file is missing"
    assert resolved.data is None


def test_a_symlink_into_findings_is_served_from_the_view(kb):
    """The exclusion tests the resolved target (SPEC §5.2.2 Paths), so a symlink does not escape it."""
    path = "findings/calibration/F-0001-ratio.md"
    link = "evidence/2026-09-22-ratio/lnk.md"
    kb.add("F-0001", "ratio", "The two curves agree to 0.1%.")
    try:
        (kb.root / link).symlink_to(kb.root / path)
    except OSError:
        pytest.skip("this platform will not create a symlink here")
    seen = b"the view's bytes\n"
    view = load_view(kb.cfg)
    view.files[path] = seen
    reader = SourceReader(kb.cfg, view)
    resolved = reader.resolve(ref(link, digest(seen)))
    assert resolved.state is State.CURRENT
    assert resolved.data == seen
    # Both paths resolve to one target, so they are one canonical key and one read.
    assert dict(reader.reads) == {("working", key_of(kb, path)): 1}


def test_working_reads_once_and_caches_a_miss(kb):
    raw = "evidence/2026-09-22-ratio/log.txt"
    gone = "evidence/2026-09-22-ratio/gone.txt"
    directory = "evidence/2026-09-22-ratio"
    reader = reader_for(kb)
    assert reader.working(raw) == SOURCE_TEXT.encode()
    assert reader.working(raw) == SOURCE_TEXT.encode()
    assert reader.working(gone) is None and reader.working(gone) is None
    assert reader.working(directory) is None
    assert reader.working("../outside.md") is None       # refused paths are not read at all
    assert dict(reader.reads) == {("working", raw): 1, ("working", gone): 1, ("working", directory): 1}
