"""Git pins (SPEC §5.1.2 Git pins): owning_worktree, oid_length, read_blob, verify_pin, auto_pin.

Every test runs against repositories the fixtures build under tmp_path, with system and global Git
config ignored: deterministic and offline. The gitpin calls under test must never change the source
repository, which source_repo.snapshot() shows.
"""

from __future__ import annotations

import hashlib
import subprocess

import pytest

from kblam import gitpin
from kblam.gitpin import GitPin

from conftest import SOURCE_REPO, TRACE_PATH, TRACE_TEXT, SourceRepo

TRACE_BYTES = TRACE_TEXT.encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- the working parts --------------------------------------------------------------------------


def test_owning_worktree_oid_length_and_read_blob(kb, source_repo):
    raw = source_repo.kb_path()
    toplevel = gitpin.owning_worktree(kb.root / raw)
    assert toplevel == source_repo.root.resolve()
    assert gitpin.oid_length(toplevel) == 40
    assert gitpin.read_blob(toplevel, source_repo.blob(TRACE_PATH)) == TRACE_BYTES
    assert gitpin.read_blob(toplevel, "0" * 40) is None


def test_no_worktree_here(kb, source_repo):
    """The KB root itself is not a repository, and a path whose directories do not exist has none."""
    assert gitpin.owning_worktree(kb.root / "evidence") is None
    assert gitpin.owning_worktree(kb.root / "gone" / "deeper" / "notes.md") is None


def test_auto_pin_on_a_clean_checkout(kb, source_repo):
    cfg = kb.cfg
    raw = source_repo.kb_path()
    pin = gitpin.auto_pin(cfg, raw, digest(TRACE_BYTES))
    assert pin == GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    check = gitpin.verify_pin(cfg, raw, digest(TRACE_BYTES), pin)
    assert check.status == "ok"
    assert check.message == ""
    assert check.data == TRACE_BYTES


def test_verify_pin_holds_when_the_working_file_is_gone(kb, source_repo):
    """A pin is about the commit, so a reference stays pinned after the working file is deleted."""
    cfg = kb.cfg
    raw = source_repo.kb_path()
    (kb.root / raw).unlink()
    pin = GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    check = gitpin.verify_pin(cfg, raw, digest(TRACE_BYTES), pin)
    assert check.status == "ok"
    assert check.data == TRACE_BYTES


def test_no_auto_pin_with_a_dirty_file(kb, source_repo):
    cfg = kb.cfg
    raw = source_repo.kb_path()
    source_repo.write(TRACE_PATH, TRACE_TEXT + "Row 104: written after the commit.\n")
    dirty = (kb.root / raw).read_bytes()
    assert dirty != TRACE_BYTES
    assert gitpin.auto_pin(cfg, raw, digest(dirty)) is None


def test_no_auto_pin_for_a_crlf_checkout_of_an_lf_blob(kb, source_repo):
    cfg = kb.cfg
    raw = source_repo.kb_path()
    source_repo.git("config", "core.autocrlf", "true")
    (kb.root / raw).unlink()
    source_repo.git("checkout", "--", TRACE_PATH)
    working = (kb.root / raw).read_bytes()
    assert b"\r\n" in working and working != TRACE_BYTES

    assert gitpin.auto_pin(cfg, raw, digest(working)) is None
    # The blob is the LF text, unchanged: the CRLF working bytes are what makes the file provisional.
    pin = gitpin.auto_pin(cfg, raw, digest(TRACE_BYTES))
    assert pin == GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    assert gitpin.verify_pin(cfg, raw, digest(working), pin).status == "invalid"


def test_no_auto_pin_for_a_file_outside_head(kb, source_repo):
    cfg = kb.cfg
    raw = source_repo.kb_path("notes/untracked.md")
    source_repo.write("notes/untracked.md", "not committed\n")
    assert gitpin.auto_pin(cfg, raw, digest(b"not committed\n")) is None


def test_no_auto_pin_without_head(kb):
    """A repository with no commit has no HEAD to pin to."""
    repo = SourceRepo(kb.root, "resources/mx-notes").init()
    repo.write(TRACE_PATH, TRACE_TEXT)
    done = subprocess.run(["git", "-C", str(repo.root), "rev-parse", "--verify", "HEAD"],
                          capture_output=True)
    assert done.returncode != 0
    assert gitpin.auto_pin(kb.cfg, f"resources/mx-notes/{TRACE_PATH}", digest(TRACE_BYTES)) is None


def test_pin_in_the_repository_itself_is_dot(kb):
    """repo "." is this repository's own worktree (SPEC §5.1.2 File references)."""
    cfg = kb.cfg
    repo = SourceRepo(kb.root, ".").init()
    repo.commit("evidence/note.md", "committed here\n", "note")
    raw = "evidence/note.md"
    sha = digest(b"committed here\n")
    pin = gitpin.auto_pin(cfg, raw, sha)
    assert pin == GitPin(".", repo.head(), repo.blob(raw))
    check = gitpin.verify_pin(cfg, raw, sha, pin)
    assert check.status == "ok"
    assert check.data == b"committed here\n"


# --- pins verify_pin refuses --------------------------------------------------------------------


def test_verify_pin_rejects_a_blob_that_is_not_at_the_path(kb, source_repo):
    cfg = kb.cfg
    other, other_text = "notes/other.md", "other notes\n"
    source_repo.commit(other, other_text, "other")
    raw = source_repo.kb_path(other)
    wrong = GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    check = gitpin.verify_pin(cfg, raw, digest(other_text.encode()), wrong)
    assert check.status == "invalid"
    assert "at" in check.message
    # The committed blob at its own path is fine: the path, not the blob, was wrong.
    right = GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(other))
    assert gitpin.verify_pin(cfg, raw, digest(other_text.encode()), right).status == "ok"


def test_verify_pin_rejects_a_blob_that_does_not_exist(kb, source_repo):
    """A forged blob ID of the right length is not in the tree at the path (it need not exist)."""
    forged = GitPin(SOURCE_REPO, source_repo.head(), "dead" * 10)
    check = gitpin.verify_pin(kb.cfg, source_repo.kb_path(), digest(TRACE_BYTES), forged)
    assert check.status == "invalid"


@pytest.mark.parametrize("oid", ["0" * 39, "0" * 41, "0" * 64, "A" * 40, "z" * 40, "", "HEAD"])
def test_verify_pin_rejects_a_bad_object_id(kb, source_repo, oid):
    """SHA-1 here: a SHA-256-length ID, a shorter one, upper case, non-hex and a name are all not IDs."""
    cfg = kb.cfg
    blob = source_repo.blob(TRACE_PATH)
    for pin in (GitPin(SOURCE_REPO, oid, blob), GitPin(SOURCE_REPO, source_repo.head(), oid)):
        check = gitpin.verify_pin(cfg, source_repo.kb_path(), digest(TRACE_BYTES), pin)
        assert check.status == "invalid"
        assert "lowercase hex" in check.message


def test_verify_pin_rejects_a_non_commit(kb, source_repo):
    cfg = kb.cfg
    raw = source_repo.kb_path()
    sha = digest(TRACE_BYTES)
    blob = source_repo.blob(TRACE_PATH)
    check = gitpin.verify_pin(cfg, raw, sha, GitPin(SOURCE_REPO, blob, blob))
    assert check.status == "invalid"
    assert "not a commit" in check.message
    tree = source_repo.git("rev-parse", "HEAD^{tree}")
    assert gitpin.verify_pin(cfg, raw, sha, GitPin(SOURCE_REPO, tree, blob)).status == "invalid"


def test_verify_pin_rejects_a_blob_with_other_bytes(kb, source_repo):
    cfg = kb.cfg
    pin = GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    check = gitpin.verify_pin(cfg, source_repo.kb_path(), digest(b"not the blob\n"), pin)
    assert check.status == "invalid"
    assert "hash" in check.message


def test_verify_pin_rejects_a_refused_path(kb, source_repo):
    cfg = kb.cfg
    pin = GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    for raw in ("../outside.md", "/etc/passwd", "C:/notes.md", "notes/a.md:hidden", ""):
        check = gitpin.verify_pin(cfg, raw, digest(TRACE_BYTES), pin)
        assert check.status == "invalid", raw


def test_verify_pin_rejects_a_refused_repo(kb, source_repo):
    cfg = kb.cfg
    blob = source_repo.blob(TRACE_PATH)
    for repo in ("../mx-docs", "/resources/mx-docs", "resources/mx-docs:ads"):
        check = gitpin.verify_pin(cfg, source_repo.kb_path(), digest(TRACE_BYTES),
                                  GitPin(repo, source_repo.head(), blob))
        assert check.status == "invalid", repo


def test_verify_pin_rejects_another_worktree_as_the_repo(kb, source_repo):
    """The recorded repo is a worktree, but not the one that owns the file."""
    cfg = kb.cfg
    other = SourceRepo(kb.root, "resources/mx-specs").init()
    other.commit("pinout.md", "pin 1: VCC\n", "pinout")
    pin = GitPin("resources/mx-specs", source_repo.head(), source_repo.blob(TRACE_PATH))
    check = gitpin.verify_pin(cfg, source_repo.kb_path(), digest(TRACE_BYTES), pin)
    assert check.status == "invalid"
    assert "worktree" in check.message


# --- pins that cannot be checked here are absent, not wrong -------------------------------------


def test_verify_pin_absent_when_the_repository_is_missing(kb, source_repo):
    cfg = kb.cfg
    pin = GitPin("resources/elsewhere", source_repo.head(), source_repo.blob(TRACE_PATH))
    check = gitpin.verify_pin(cfg, source_repo.kb_path(), digest(TRACE_BYTES), pin)
    assert check.status == "absent"
    assert "resources/elsewhere" in check.message


def test_verify_pin_absent_when_the_directory_is_not_a_worktree(kb, source_repo):
    cfg = kb.cfg
    (kb.root / "resources" / "mx-specs").mkdir(parents=True)
    pin = GitPin("resources/mx-specs", source_repo.head(), source_repo.blob(TRACE_PATH))
    check = gitpin.verify_pin(cfg, source_repo.kb_path(), digest(TRACE_BYTES), pin)
    assert check.status == "absent"
    assert "worktree" in check.message


def test_verify_pin_absent_when_the_commit_is_missing(kb, source_repo):
    cfg = kb.cfg
    pin = GitPin(SOURCE_REPO, "0" * 40, source_repo.blob(TRACE_PATH))
    check = gitpin.verify_pin(cfg, source_repo.kb_path(), digest(TRACE_BYTES), pin)
    assert check.status == "absent"
    assert "0" * 40 in check.message


def test_verify_pin_absent_when_the_blob_cannot_be_read(kb, source_repo):
    """read is the caller's cache (sources.SourceReader.blob); None there means the object is gone."""
    cfg = kb.cfg
    pin = GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    check = gitpin.verify_pin(cfg, source_repo.kb_path(), digest(TRACE_BYTES), pin,
                              read=lambda toplevel, oid: None)
    assert check.status == "absent"
    assert check.data is None


def test_verify_pin_is_not_confused_by_a_hash_that_is_not_a_hash(kb, source_repo):
    cfg = kb.cfg
    pin = GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    check = gitpin.verify_pin(cfg, source_repo.kb_path(), "0" * 64, pin)
    assert check.status == "invalid"


# --- repositories that are worktrees in their own right -----------------------------------------


def test_submodule_reports_its_own_toplevel(kb, source_repo):
    cfg = kb.cfg
    specs = SourceRepo(kb.root, "resources/mx-specs").init()
    specs.commit("MX-100/pinout.md", "pin 1: VCC\n", "pinout")
    vendor = "vendor/mx-specs"
    done = subprocess.run(
        ["git", "-C", str(source_repo.root), "-c", "protocol.file.allow=always",
         "submodule", "add", specs.root.as_posix(), vendor],
        capture_output=True, text=True, check=False)
    if done.returncode:
        pytest.skip(f"this git will not add a file-protocol submodule: {done.stderr.strip()}")
    source_repo.git("commit", "-q", "-m", "vendor the pinout")

    sub = source_repo.root / vendor
    raw = f"{SOURCE_REPO}/{vendor}/MX-100/pinout.md"
    assert (kb.root / raw).read_bytes() == b"pin 1: VCC\n"    # the submodule's checkout
    assert gitpin.owning_worktree(kb.root / raw) == sub.resolve()
    sha = digest(b"pin 1: VCC\n")
    pin = gitpin.auto_pin(cfg, raw, sha)
    # The submodule's own HEAD, not the parent's: the submodule is the file's worktree.
    assert pin == GitPin(f"{SOURCE_REPO}/{vendor}", specs.head(), specs.blob("MX-100/pinout.md"))
    assert gitpin.verify_pin(cfg, raw, sha, pin).status == "ok"
    # The parent repository is a worktree, but not this file's: invalid, not absent.
    parent_pin = GitPin(SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    assert gitpin.verify_pin(cfg, raw, sha, parent_pin).status == "invalid"


def test_linked_worktree_reports_its_own_toplevel(kb, source_repo):
    cfg = kb.cfg
    linked = kb.root / "resources" / "mx-docs-alt"
    done = subprocess.run(["git", "-C", str(source_repo.root), "worktree", "add", "-q", "-b", "alt",
                           str(linked)], capture_output=True, text=True, check=False)
    if done.returncode:
        pytest.skip(f"this git will not add a linked worktree: {done.stderr.strip()}")
    raw = "resources/mx-docs-alt/" + TRACE_PATH
    assert gitpin.owning_worktree(kb.root / raw) == linked.resolve()
    pin = gitpin.auto_pin(cfg, raw, digest(TRACE_BYTES))
    assert pin == GitPin("resources/mx-docs-alt", source_repo.head(), source_repo.blob(TRACE_PATH))
    assert gitpin.verify_pin(cfg, raw, digest(TRACE_BYTES), pin).status == "ok"


# --- a SHA-256 repository -----------------------------------------------------------------------


@pytest.fixture
def sha256_repo(kb, monkeypatch):
    """A source repository using SHA-256 object IDs; skipped where this git cannot make one."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(kb.root.parent / "no-global-gitconfig"))
    repo = SourceRepo(kb.root, "resources/sha256-docs")
    try:
        repo.init("--object-format=sha256")
    except subprocess.CalledProcessError as exc:
        reason = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr)
        pytest.skip(f"this git cannot create a SHA-256 repository: {reason.strip()}")
    if repo.git("rev-parse", "--show-object-format", check=False) != "sha256":
        pytest.skip("this git ignored --object-format=sha256")
    repo.commit(TRACE_PATH, TRACE_TEXT, "trace")
    return repo


def test_sha256_repository_uses_64_hex_ids(kb, sha256_repo):
    cfg = kb.cfg
    raw = sha256_repo.kb_path()
    sha = digest(TRACE_BYTES)
    assert gitpin.oid_length(sha256_repo.root) == 64
    pin = gitpin.auto_pin(cfg, raw, sha)
    assert pin is not None
    assert pin.repo == "resources/sha256-docs"
    assert len(pin.commit) == 64 and len(pin.blob) == 64
    assert gitpin.verify_pin(cfg, raw, sha, pin).status == "ok"
    # A SHA-1-length ID is the wrong length in a SHA-256 repository.
    short = GitPin(pin.repo, pin.commit[:40], pin.blob)
    check = gitpin.verify_pin(cfg, raw, sha, short)
    assert check.status == "invalid"
    assert "64 lowercase hex" in check.message


# --- read-only ----------------------------------------------------------------------------------


def test_the_source_repository_is_unchanged(kb, source_repo):
    cfg = kb.cfg
    raw = source_repo.kb_path()
    sha = digest(TRACE_BYTES)
    before = source_repo.snapshot()
    toplevel = gitpin.owning_worktree(kb.root / raw)
    pin = gitpin.auto_pin(cfg, raw, sha)
    assert pin is not None
    assert gitpin.verify_pin(cfg, raw, sha, pin).status == "ok"
    assert gitpin.oid_length(toplevel) == 40
    assert gitpin.read_blob(toplevel, pin.blob) == TRACE_BYTES
    assert gitpin.owning_worktree(kb.root / raw) == toplevel
    assert source_repo.snapshot() == before
