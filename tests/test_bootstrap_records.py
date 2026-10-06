"""SPEC §8 item 3 and §5.2.6 "Upgrade": a new clone, or a KB whose .kblam/ was deleted, holds review records
and no tree.hash. When the full deterministic validation, K13-K15 included, is clean, `kblam validate
--record`, the first write (a put of a finding or of a record) and `kblam init --update` record the format-2
tree.hash, create the registry from the records present, accept every finding from the repository and ask
Jev nothing. A failed bootstrap leaves tree.hash missing and says what to do; `kblam validate --record` then
creates no registry either. Jev is the fake transport from test_check, and nothing touches the network."""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess

import pytest

import m611_helpers as m
from kblam import init
from kblam.finding import fingerprint
from kblam.jev import CACHE_NAME, PairCache
from kblam.treehash import read_recorded, tree_digest_v2
from kblam.view import load_view

from test_check import E2, jkb  # noqa: F401 (jkb is a fixture)

REVIEW = "research-review"
CLAIM_B = "The pump motor reaches steady output after 90 seconds of warm-up at 4000 rpm."
QUESTION = "Does an independent measurement establish the claim?"
PENDING = f"CT-0001 open replication of F-0002: {QUESTION}"
BASELINE = ("kblam validate: there was no .kblam/tree.hash (a new clone, or .kblam/ was deleted), so Jev was not "
            "asked: 2 finding(s) accepted from the repository as checked at their current fingerprints. kblam "
            "audit checks them with Jev")
MISSING = ("there is no .kblam/tree.hash (a new clone, or .kblam/ was deleted), and the tree as it was before this "
           "write fails kblam validate, so kblam did not record it; tree.hash not advanced. Run kblam validate, "
           "fix anything it lists, then run kblam validate --record.")


@pytest.fixture
def no_jev_check(monkeypatch):
    """The Jev check of the whole KB (`kblam check`, `validate --record` with a tree.hash) must not run."""
    def refuse(*args, **kwargs):
        pytest.fail("the Jev check of the knowledge base ran on a bootstrap")

    monkeypatch.setattr("kblam.review.check_findings", refuse)


def reviewed(kb, source_repo, *, use: bool = True) -> None:
    """F-0001 quotes the trace's line 3, which SC-0001 (confirmed) challenges, and CU-0001 (approved)
    covers that excerpt; F-0002 is a plain finding with CT-0001, an open task, on it. Without `use`, the
    excerpt is left uncovered: a K14 error."""
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")
    sc = m.confirmed_challenge(kb, source_repo, "3-3", by="reviewer-a", decider="reviewer-b")
    if use:
        m.approved_use(kb, sc, "F-0001", proponent="researcher-a", reviewer="reviewer-b")
    m.open_task(kb, "F-0002", by="researcher-a", proponent="researcher-a")


def clone(kb) -> None:
    """The state a fresh clone leaves: no .kblam/ at all (no tree.hash, registry or Jev state)."""
    shutil.rmtree(kb.root / ".kblam")
    kb.fake.requests.clear()


def recorded(kb) -> bool:
    """Whether tree.hash is the format-2 line of the tree as it is now."""
    return read_recorded(kb.cfg) == (2, REVIEW, tree_digest_v2(load_view(kb.cfg)))


def accepted(kb, *finding_ids: str) -> bool:
    """Whether each finding is marked checked at its current fingerprint."""
    view = load_view(kb.cfg)
    cache = PairCache(kb.cfg.state_dir / CACHE_NAME)
    return all(cache.was_checked(f.file_id, fingerprint(f, kb.cfg.scope_separator))
               for f in view.findings if f.file_id in finding_ids)


def present(kb) -> list[str]:
    """The IDs of the records in the review root, sorted."""
    return sorted(r.id for r in load_view(kb.cfg).records)


def bootstrapped(kb, *, registry: list[str]) -> None:
    """tree.hash is format 2 for the tree as it is, the registry is exactly `registry` (the records
    present), and both findings are accepted from the repository."""
    assert recorded(kb)
    assert m.registry(kb) == registry == present(kb)
    assert accepted(kb, "F-0001", "F-0002")


# --- clean: every path bootstraps, asking Jev nothing ---------------------------------------------


def test_validate_record_bootstraps_a_clone_with_records(jkb, source_repo, no_jev_check):
    reviewed(jkb, source_repo)
    clone(jkb)

    run = m.validate(jkb, "--record")

    assert run == m.Run(0, f"{PENDING}\nkblam validate: OK (2 findings); 1 pending task(s); recorded "
                           f".kblam/tree.hash for this tree\n{BASELINE}\n", "")
    bootstrapped(jkb, registry=["CT-0001", "CU-0001", "SC-0001"])
    assert jkb.fake.requests == []
    assert m.validate(jkb) == m.Run(0, f"{PENDING}\nkblam validate: OK (2 findings); 1 pending task(s)\n", "")


def test_the_put_of_a_finding_bootstraps_a_clone_with_records(jkb, source_repo, no_jev_check):
    """The put checks its own finding with Jev, as every put does; the bootstrap asks about no other."""
    reviewed(jkb, source_repo)
    clone(jkb)
    staged = m.stage_finding(jkb, None, m.finding_text("F-0003", E2, topic="tray"), slug="tray")

    run = m.put(jkb, staged)

    assert run.code == 0 and run.err == "", run.out + run.err
    assert run.out.startswith("kblam put: F-0003 -> findings/tray/F-0003-tray.md\n"), run.out
    bootstrapped(jkb, registry=["CT-0001", "CU-0001", "SC-0001"])
    assert accepted(jkb, "F-0003")                                   # checked by its own put
    assert jkb.fake.requests and all(b["state"]["new"]["claim"] == E2 for b in jkb.fake.requests)


def test_the_put_of_a_record_bootstraps_a_clone_with_records(jkb, source_repo, no_jev_check):
    reviewed(jkb, source_repo)
    clone(jkb)
    staged = m.stage_task(jkb, "F-0001", by="researcher-a", proponent="researcher-a")

    run = m.put(jkb, staged)

    assert run == m.Run(0, f"kblam put: {staged.id} -> {REVIEW}/tasks/{staged.id}.yaml\n", "")
    bootstrapped(jkb, registry=["CT-0001", "CT-0002", "CU-0001", "SC-0001"])
    assert staged.id == "CT-0002"
    assert jkb.fake.requests == []


@pytest.fixture
def git_kb(jkb, monkeypatch):
    """jkb as a git repository, run from its root as `kblam init` must be, with the hook check stubbed
    (it has its own tests) and system and global git config ignored."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(jkb.root.parent / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(jkb.root.parent))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    subprocess.run(["git", "init", "-q", str(jkb.root)], check=True, capture_output=True)
    monkeypatch.chdir(jkb.root)
    monkeypatch.setattr(init.Init, "check_hooks", lambda self, installed, findings_dir: None)
    return jkb


def init_update() -> m.Run:
    """`kblam init --update` (init takes no --root: it runs where it is started)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = m.main(["init", "--update"])
    return m.Run(code, out.getvalue(), err.getvalue())


def test_init_update_bootstraps_a_clone_with_records(git_kb, source_repo, no_jev_check):
    """init's review-index step creates the registry from the records present, and its tree.hash step
    then bootstraps."""
    reviewed(git_kb, source_repo)
    clone(git_kb)

    run = init_update()

    assert run.code == 0 and run.err == "", run.out + run.err
    assert run.out.endswith("  created   .kblam/tree.hash\nkblam init: done. Review the files above and commit "
                            "them.\n"), run.out
    bootstrapped(git_kb, registry=["CT-0001", "CU-0001", "SC-0001"])
    assert git_kb.fake.requests == []
