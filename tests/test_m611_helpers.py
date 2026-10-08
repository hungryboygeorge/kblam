"""The M6.11 acceptance-test helpers themselves (m611_helpers): one whole workflow through them, the
"files changed" and "subject digest" assertions they exist for, and staging a finding through the CLI.
Offline and deterministic: the frozen date keeps the record bytes fixed, and the source repository is
conftest's nested Git fixture."""

from __future__ import annotations

import pytest

import m611_helpers as m

# The same alias a group file declares: autouse, so every test here runs on the pinned date.
frozen_today = m.frozen_today


def test_the_documented_example(kb, source_repo):
    """The module docstring's example, run exactly as it is written there."""
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")         # F-0001 quotes trace line 3
    sc = m.confirmed_challenge(kb, source_repo, "3-3", by="reviewer-a", decider="reviewer-b")
    m.approved_use(kb, sc, "F-0001", 1, proponent="researcher-a", reviewer="reviewer-b")
    assert m.validate(kb).code == 0


def test_the_challenge_alone_leaves_validate_failing(kb, source_repo):
    """Why the example's use is not decoration: without it the quoted excerpt is a K14 error."""
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")
    m.confirmed_challenge(kb, source_repo, "3-3", by="reviewer-a", decider="reviewer-b")
    failing = m.validate(kb)
    assert failing.code == 1
    assert "K14 findings/calibration/F-0001-ratio.md:15: source-challenge-0001 challenges this quoted assertion at " \
           f"{m.TRACE}@" in failing.out


def test_the_whole_round_trip_through_the_helpers(kb, source_repo):
    """A confirmed challenge, an approved use of the finding that quotes the challenged lines, and a
    confirmed task with primary evidence: validate is clean, three records are current, and no feature
    command touched the source repository."""
    before = source_repo.snapshot()

    m.quoting_finding(kb, "F-0001", source_repo, "3-3")         # F-0001 quotes trace line 3
    m.accept_tree(kb)

    sc = m.confirmed_challenge(kb, source_repo, "3-3", by="reviewer-a", decider="reviewer-b")
    assert sc == "source-challenge-0001"
    cu = m.approved_use(kb, sc, "F-0001", 1, proponent="researcher-a", reviewer="reviewer-b")
    assert cu == "checked-use-0001"
    ct = m.open_task(kb, "F-0001", kind="replication", by="reviewer-a", proponent="researcher-a")
    assert ct == "claim-task-0001"
    m.ok(m.confirmed_task(kb, ct, by="reviewer-b"), "review decide")

    assert m.ok(m.validate(kb), "validate").out == "kblam validate: OK (1 findings)\n"

    listing = m.ok(m.kblam(kb, "review", "list"), "review list")
    assert listing.out.splitlines() == [
        f"source-challenge-0001 challenge confirmed {m.expect(kb, sc)[:12]} {m.TRACE}:3-3 current",
        f"claim-task-0001 task confirmed {m.expect(kb, ct)[:12]} replication of F-0001 current",
        f"checked-use-0001 use approved {m.expect(kb, cu)[:12]} source-challenge-0001 in F-0001 excerpt 1 current",
    ]
    assert source_repo.snapshot() == before
    # frozen_today reached the modules review_stage and review_write write the dates from
    assert "date: 2026-09-28" in m.record_path(kb, sc).read_text(encoding="utf-8")


def _prepare_record_stage(kb, source_repo, kind):
    """Prepare the subject before patching the runner; return the staging helper call to test."""
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")
    if kind == "source-challenge":
        return lambda: m.stage_challenge(kb, source_repo, "3-3", by="reviewer-a")
    if kind == "claim-task":
        return lambda: m.stage_task(kb, "F-0001", by="reviewer-a", proponent="researcher-a")
    sc = m.confirmed_challenge(kb, source_repo, "3-3", by="reviewer-a", decider="reviewer-b")
    return lambda: m.stage_use(kb, sc, "F-0001", by="reviewer-b", proponent="researcher-a")


@pytest.mark.parametrize("kind", ["source-challenge", "claim-task", "checked-use"])
@pytest.mark.parametrize("fault", [
    "stderr", "extra-line", "other-path", "other-kind", "short-id", "missing-newline", "whitespace",
])
def test_record_staging_rejects_unexpected_streams(kb, source_repo, monkeypatch, kind, fault):
    """An otherwise successful command cannot hide stderr or any difference in its full stdout."""
    stage = _prepare_record_stage(kb, source_repo, kind)
    runner = m.kblam
    runs = []

    def doctored(kb, *argv):
        run = runner(kb, *argv)
        assert run.code == 0 and run.err == ""
        expected = f"{kb.cfg.review_staging_dir / f'{kind}-0001.yaml'}\n"
        assert run.out == expected
        out, err = run.out, run.err
        if fault == "stderr":
            err = "unexpected recovery diagnostic\n"
        elif fault == "extra-line":
            out += "unexpected extra output\n"
        elif fault == "other-path":
            out = f"{kb.root / f'{kind}-0001.yaml'}\n"
        elif fault == "other-kind":
            out = out.replace(f"{kind}-0001", "XX-0001")
        elif fault == "short-id":
            out = out.replace("-0001.yaml", "-001.yaml")
        elif fault == "missing-newline":
            out = out.removesuffix("\n")
        else:
            out = f" {out}"
        doctored_run = m.Run(run.code, out, err)
        runs.append(doctored_run)
        return doctored_run

    monkeypatch.setattr(m, "kblam", doctored)
    with pytest.raises(AssertionError) as exc:
        stage()
    assert len(runs) == 1
    assert f"stdout: {runs[0].out!r}" in str(exc.value)
    assert f"stderr: {runs[0].err!r}" in str(exc.value)


@pytest.mark.parametrize("kind", ["source-challenge", "claim-task", "checked-use"])
@pytest.mark.parametrize("fault", ["missing-file", "other-id"])
def test_record_staging_checks_the_printed_file(kb, source_repo, monkeypatch, kind, fault):
    """A well-shaped stdout line still has to name a file containing that same record ID."""
    stage = _prepare_record_stage(kb, source_repo, kind)
    runner = m.kblam
    runs = []

    def doctored(kb, *argv):
        run = runner(kb, *argv)
        assert run.code == 0 and run.err == ""
        staged = kb.cfg.review_staging_dir / f"{kind}-0001.yaml"
        assert run.out == f"{staged}\n"
        if fault == "missing-file":
            staged.unlink()
        else:
            staged.write_bytes(staged.read_bytes().replace(
                f"id: {kind}-0001\n".encode(), f"id: {kind}-9999\n".encode()))
        runs.append(run)
        return m.Run(run.code, run.out, run.err)

    monkeypatch.setattr(m, "kblam", doctored)
    with pytest.raises(AssertionError) as exc:
        stage()
    assert len(runs) == 1
    assert f"stdout: {runs[0].out!r}" in str(exc.value)
    assert f"stderr: {runs[0].err!r}" in str(exc.value)
    assert ("staged file is missing" if fault == "missing-file" else
            "staged record ID does not match") in str(exc.value)


def test_changed_reports_exactly_the_files_a_put_wrote(kb, source_repo):
    """The record, the review index, the registry and tree.hash; the staged file is gone."""
    staged = m.stage_challenge(kb, source_repo, "2-2", by="reviewer-a")
    m.accept_tree(kb)
    before = m.tree(kb)

    m.put_ok(kb, staged)

    assert m.changed(before, m.tree(kb)) == {
        ".kblam/review-ids",
        ".kblam/review-staging/source-challenge-0001.yaml",
        ".kblam/tree.hash",
        "research-review/INDEX.md",
        "research-review/challenges/source-challenge-0001.yaml",
    }
    assert m.record_path(kb, staged.id).is_file() and not staged.path.exists()


def test_expect_is_the_digest_show_prints_and_decide_takes(kb, source_repo):
    sc = m.challenge(kb, source_repo, "3-3", by="reviewer-a")
    m.accept_tree(kb)

    shown = m.ok(m.kblam(kb, "challenge", "show", sc), "challenge show")
    assert shown.out.splitlines()[1] == f"subject digest: {m.expect(kb, sc)}"

    stale = m.kblam(kb, "review", "decide", sc, "--status", "rejected", "--by", "reviewer-b",
                    "--reason", "no", "--expect", "a" * 12)
    assert stale.code == 1
    assert stale.err == (f"kblam review decide: {sc} changed since you inspected it; show it again: "
                         f"kblam challenge show {sc}\n")

    accepted = m.ok(m.decide(kb, sc, "confirmed", by="reviewer-b"), "review decide")
    assert accepted.out.startswith(f"kblam review decide: {sc} is now confirmed")


def test_stage_finding_puts_a_new_finding_through_the_cli(kb):
    """`kblam new` allocates an ID; the staged file ends up named for the ID the text carries, which is
    the one `kblam put` reads off the file name."""
    text = m.finding_text("F-0007", m.CLAIM)
    staged = m.stage_finding(kb, None, text)
    assert staged.name.startswith("F-0007-")

    m.put_ok(kb, staged)

    installed = kb.findings / "calibration" / staged.name
    assert installed.read_bytes() == text.encode("utf-8")
    assert not staged.exists()
    assert m.ok(m.validate(kb), "validate").out == "kblam validate: OK (1 findings)\n"


def test_stage_finding_honours_an_explicit_slug(kb):
    """Both routes name the staged file for the ID and the slug given, not for the one kblam derived."""
    text = m.finding_text("F-0007", m.CLAIM)
    staged = m.stage_finding(kb, None, text, slug="curve-ratio")
    assert staged.name == "F-0007-curve-ratio.md"
    m.put_ok(kb, staged)
    assert (kb.findings / "calibration" / "F-0007-curve-ratio.md").is_file()

    edited = m.finding_text("F-0007", m.CLAIM + " A second measurement agrees.")
    second = m.stage_finding(kb, "F-0007", edited, slug="curve-ratio-v2")
    assert second.name == "F-0007-curve-ratio-v2.md"
    m.put_ok(kb, second)

    assert not (kb.findings / "calibration" / "F-0007-curve-ratio.md").exists()
    assert (kb.findings / "calibration" / "F-0007-curve-ratio-v2.md").read_text(encoding="utf-8") == edited
    assert m.ok(m.validate(kb), "validate").code == 0


def test_stage_finding_edits_an_installed_finding_for_the_put(kb):
    """The edit route: the staged copy carries the edited text, and the put installs it in place."""
    original = m.finding_text("F-0001", m.CLAIM)
    m.put_ok(kb, m.stage_finding(kb, None, original))

    edited = m.finding_text("F-0001", m.CLAIM + " A second measurement agrees.")
    staged = m.stage_finding(kb, "F-0001", edited)
    assert staged.name == "F-0001-title-of-f-0001.md"
    assert staged.read_bytes() == edited.encode("utf-8")
    m.put_ok(kb, staged)

    assert (kb.findings / "calibration" / staged.name).read_text(encoding="utf-8") == edited
    assert m.ok(m.validate(kb), "validate").code == 0
