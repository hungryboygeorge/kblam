"""`kblam put` of a finding against the review records (SPEC §5.1.4 "Where each rule blocks",
§5.1.6; §12 M6.10 test groups 4, 9/10).

A finding put blocks on K1-K11 as before, on K13 only for an affected excerpt the installed finding
did not already have affected, and never on K12 or K14. It reports the tasks and uses it makes stale,
the K13 errors it keeps and the errors that did not refuse. Confirmed challenges and uses are written
into the review root as record files (review_write is another unit's) and read back by load_view.
Offline: the Jev thresholds of the fixtures enable no verdict.
"""

from __future__ import annotations

import json

import pytest

from kblam import matching, registry, store
from kblam.decisions import subject_digest
from kblam.finding import fingerprint
from kblam.review_index import generate_review_index
from kblam.sources import SourceReader, sha256_hex
from kblam.store import StoreError, ack, atomic_write, edit_finding, regenerate_index
from kblam.treehash import format_line, read_recorded, tree_digest_v2
from kblam.view import KBView, load_view

from conftest import ZERO64, finding_text, record_data, record_text
from test_k13 import (CLAIM, LINE3, TRACE, TRACE_TEXT, add_finding, quoted, same_bytes_message, sc, use)
from test_k13 import deciding
from test_k13 import put as put_record

REVIEW = "research-review"
SC = f"{REVIEW}/challenges/SC-0001.yaml"
CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."
CLAIM_C = "The media tray reports its type through two contact pins read at load time."


# --- helpers ------------------------------------------------------------------------------------


def write_review_index(kb) -> None:
    """The review index kblam would write for the records present (K12 checks it byte for byte)."""
    kb.write(f"{REVIEW}/INDEX.md", generate_review_index(load_view(kb.cfg)))


def ct(kb, finding_id: str, *, rec_id: str = "CT-0001", status: str = "open") -> dict:
    """A task bound to the finding as it is now: its K3 fingerprint and its file sha256."""
    finding = next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)
    data = record_data("CT", rec_id, status=status, finding=finding_id,
                       claim_fingerprint=fingerprint(finding), base_file_sha256=sha256_hex(finding.raw))
    return deciding("CT", data)


def stage(kb, finding_id: str, slug: str, claim: str, *, topic: str = "calibration", **kw):
    """A staged finding file, as `kblam new` plus an author's edits leave it."""
    return kb.write(f".kblam/staging/{finding_id}-{slug}.md",
                    finding_text(finding_id, claim, topic=topic, **kw))


def replace_in(path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8", newline="\n")


def append_excerpt(path, tag: str, excerpt: str) -> None:
    """Add a second verbatim tag and block to a staged finding."""
    text = path.read_text(encoding="utf-8").rstrip("\n")
    path.write_text(f"{text}\n\n{quoted(tag, excerpt)}\n", encoding="utf-8", newline="\n")


def codes(issues) -> list[str]:
    return [i.code for i in issues]


# --- where each rule blocks ---------------------------------------------------------------------


def test_a_k12_error_and_a_k14_error_elsewhere_do_not_refuse_a_put(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    put_record(kb, "CT", record_data("CT", "CT-0001"))       # open, bound to F-0001: the sha is wrong
    kb.write(f"{REVIEW}/challenges/notes.md", "not a record\n")   # a K12 stray file
    write_review_index(kb)
    staged = stage(kb, "F-0002", "drift", CLAIM_B, topic="motor")

    result = store.put(kb.cfg, staged)

    assert result.ok, [i.format(result.view) for i in result.issues]
    assert result.issues == [] and result.kept == []
    assert (kb.findings / "motor" / staged.name).is_file()
    assert sorted({i.code for i in result.remaining}) == ["K12", "K14"]
    assert any(i.code == "K12" and "notes.md" in i.path for i in result.remaining)
    assert any(i.code == "K14" and i.owner == "CT-0001" for i in result.remaining)


def test_a_put_that_makes_a_task_and_a_use_stale_succeeds_and_lists_them(kb, source_repo):
    put_record(kb, "SC", sc(source_repo))
    add_finding(kb, "3", LINE3)                     # F-0001 quotes the confirmed assertion
    put_record(kb, "CT", ct(kb, "F-0001"))          # bound to F-0001 as it is now
    put_record(kb, "CU", use(kb))                   # approved and current: it covers the excerpt
    write_review_index(kb)
    assert kb.issues() == []                         # the approved use covers the excerpt

    staged = edit_finding(kb.cfg, "F-0001")
    replace_in(staged, "about 0.1%", "within 0.2%")
    result = store.put(kb.cfg, staged)

    assert result.ok, [i.format(result.view) for i in result.issues]
    assert result.stale == ["CT-0001", "CU-0001"]    # in ID order: a task and a use
    assert "within 0.2%" in (kb.findings / "calibration" / "F-0001-ratio.md").read_text(encoding="utf-8")
    assert {"K13", "K14"} <= set(kb.codes())         # the obligations the put leaves


def test_a_new_finding_quoting_a_confirmed_assertion_is_refused(kb, source_repo):
    put_record(kb, "SC", sc(source_repo))
    write_review_index(kb)
    staged = stage(kb, "F-0001", "ratio", CLAIM, body=quoted(f"{TRACE}:3", LINE3))

    result = store.put(kb.cfg, staged)

    assert codes(result.issues) == ["K13"]
    assert result.issues[0].message == same_bytes_message(source_repo)
    assert result.kept == [] and result.remaining == []
    assert staged.is_file() and not (kb.findings / "calibration" / staged.name).exists()


def test_an_edit_that_keeps_an_affected_excerpt_succeeds_with_it_in_kept(kb, source_repo):
    add_finding(kb, "3", LINE3)                     # installed before the challenge: it already has it
    put_record(kb, "SC", sc(source_repo))
    write_review_index(kb)

    staged = edit_finding(kb.cfg, "F-0001")
    replace_in(staged, "about 0.1%", "within 0.2%")
    result = store.put(kb.cfg, staged)

    assert result.ok, [i.format(result.view) for i in result.issues]
    assert result.issues == [] and result.stale == []
    assert codes(result.kept) == ["K13"]
    assert result.kept[0].message == same_bytes_message(source_repo)
    assert result.remaining == []                    # a kept K13 error is listed, not left unclassified
    assert not staged.exists()
    assert codes(kb.issues()) == ["K13"]            # the excerpt stays an error until a use is reviewed


def test_a_new_excerpt_a_current_use_covers_is_refused_anyway(kb, source_repo):
    """An excerpt is affected whether or not a use covers it (SPEC §5.1.4), so a put cannot install one;
    K13 reports no error for a covered excerpt, so the refusal is synthesised at the same line."""
    challenge = sc(source_repo)
    put_record(kb, "SC", challenge)
    staged = stage(kb, "F-0001", "ratio", CLAIM, body=quoted(f"{TRACE}:3", LINE3))

    base = load_view(kb.cfg)
    sc_record = next(r for r in base.records if r.id == "SC-0001")
    files = dict(base.files)
    files[f"findings/calibration/{staged.name}"] = staged.read_bytes()
    candidate = KBView(cfg=kb.cfg, files=files, review_files=dict(base.review_files))
    finding = next(f for f in candidate.findings if f.file_id == "F-0001")
    [match] = matching.finding_matches(candidate, SourceReader(kb.cfg, candidate), finding)
    put_record(kb, "CU", deciding("CU", record_data(
        "CU", "CU-0001", status="approved", challenge="SC-0001",
        challenge_bind=subject_digest("SC", sc_record.data), finding="F-0001",
        finding_fingerprint=fingerprint(finding), finding_file_sha256=sha256_hex(finding.raw),
        citation={"ordinal": match.ordinal, "path": match.path, "range": list(match.range),
                  "tag_sha256": match.tag_sha256})))
    write_review_index(kb)
    # the approved use covers the excerpt on this tree (F-0001 itself is only staged, so K12 reports the
    # use's dangling finding link; nothing here is a K13 error, which is what the put has to synthesise)
    assert "K13" not in codes(kb.issues())

    result = store.put(kb.cfg, staged)

    assert codes(result.issues) == ["K13"]
    assert result.issues[0].owner == "F-0001"
    assert result.issues[0].line == finding.body_start_line + match.start
    assert "SC-0001" in result.issues[0].message
    assert "a new excerpt cannot quote a confirmed challenge's assertion" in result.issues[0].message
    assert result.kept == []
    assert staged.is_file() and not (kb.findings / "calibration" / staged.name).exists()


def test_two_challenges_on_one_tag_are_refused_one_issue_each(kb, source_repo):
    """Each issue names one challenge and the command that would address it: a message naming two has no
    runnable command behind it (SPEC §5.1.4 "Where each rule blocks"; decisions log D29)."""
    first, second = sc(source_repo), sc(source_repo, rec_id="SC-0002")
    put_record(kb, "SC", first)
    put_record(kb, "SC", second)
    staged = stage(kb, "F-0001", "ratio", CLAIM, body=quoted(f"{TRACE}:3", LINE3))

    # Both challenges' excerpts are covered by an approved use, so K13 reports no error for them and the
    # put has to synthesise the refusal from the affected triples (as the test above does for one).
    base = load_view(kb.cfg)
    files = dict(base.files)
    files[f"findings/calibration/{staged.name}"] = staged.read_bytes()
    candidate = KBView(cfg=kb.cfg, files=files, review_files=dict(base.review_files))
    finding = next(f for f in candidate.findings if f.file_id == "F-0001")
    [match] = matching.finding_matches(candidate, SourceReader(kb.cfg, candidate), finding)
    for rec_id, challenge in (("CU-0001", "SC-0001"), ("CU-0002", "SC-0002")):
        record = next(r for r in base.records if r.id == challenge)
        put_record(kb, "CU", deciding("CU", record_data(
            "CU", rec_id, status="approved", challenge=challenge,
            challenge_bind=subject_digest("SC", record.data), finding="F-0001",
            finding_fingerprint=fingerprint(finding), finding_file_sha256=sha256_hex(finding.raw),
            citation={"ordinal": match.ordinal, "path": match.path, "range": list(match.range),
                      "tag_sha256": match.tag_sha256})))
    write_review_index(kb)
    assert "K13" not in codes(kb.issues())           # both excerpts are covered on this tree

    result = store.put(kb.cfg, staged)

    assert codes(result.issues) == ["K13", "K13"]
    assert [i.owner for i in result.issues] == ["F-0001", "F-0001"]
    assert [i.line for i in result.issues] == [finding.body_start_line + match.start] * 2
    for issue, challenge in zip(result.issues, ("SC-0001", "SC-0002")):
        assert sum(name in issue.message for name in ("SC-0001", "SC-0002")) == 1
        assert f"kblam use review {challenge} F-0001 {match.ordinal} --by NAME --proponent NAME" \
            in issue.message
    assert staged.is_file() and not (kb.findings / "calibration" / staged.name).exists()


def test_an_edit_that_adds_a_second_affected_excerpt_is_refused(kb, source_repo):
    add_finding(kb, "3", LINE3)
    put_record(kb, "SC", sc(source_repo))
    write_review_index(kb)
    offset = TRACE_TEXT.index("the two bytes")

    staged = edit_finding(kb.cfg, "F-0001")
    append_excerpt(staged, f"{TRACE}:@0x{offset:X}", "the two bytes")
    result = store.put(kb.cfg, staged)

    assert codes(result.issues) == ["K13"]          # the newly affected excerpt refuses the edit
    assert codes(result.kept) == ["K13"]            # the excerpt the installed finding already had
    assert result.issues[0].line != result.kept[0].line
    assert staged.is_file()
    assert (kb.findings / "calibration" / "F-0001-ratio.md").is_file()   # findings/ is unchanged


def test_k3_on_another_finding_does_not_refuse_a_put(kb):
    """Blocking uses the issue's owner, never the path it is displayed at (SPEC §5.1.4)."""
    kb.add("F-0001", "sensor", CLAIM_A)
    dependent = stage(kb, "F-0002", "motor", CLAIM_B, topic="motor",
                      extra="depends_on:\n  F-0001: null\n")
    assert store.put(kb.cfg, dependent).ok
    rewrite = edit_finding(kb.cfg, "F-0001")
    replace_in(rewrite, "about 0.1%", "within 0.2%")
    assert store.put(kb.cfg, rewrite).ok            # F-0002 is suspect from here on
    assert kb.codes() == ["K3"]

    result = store.put(kb.cfg, stage(kb, "F-0003", "tray", CLAIM_C, topic="tray"))

    assert result.ok and result.issues == []
    assert codes(result.remaining) == ["K3"]        # owned by F-0002, so it does not refuse F-0003


# --- tree.hash and the journal ------------------------------------------------------------------


def test_a_root_change_refuses_put_ack_and_index(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write(SC, record_text("SC"))                 # a record must exist for the root to matter
    kb.write(".kblam/tree.hash", format_line("research-notes", ZERO64))
    staged = stage(kb, "F-0002", "motor", CLAIM_B, topic="motor")
    before = kb.snapshot()
    message = ("the review root changed from research-notes to research-review in kblam.toml; "
               "schema 1 fixes it at init")

    for call in (lambda: store.put(kb.cfg, staged),
                 lambda: ack(kb.cfg, "F-0002", "F-0001"),
                 lambda: regenerate_index(kb.cfg)):
        with pytest.raises(StoreError) as caught:
            call()
        assert str(caught.value) == message
    assert kb.snapshot() == before and staged.is_file()


def test_a_missing_tree_hash_with_records_present_leaves_a_put_unrecorded(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write(SC, record_text("SC"))
    (kb.cfg.state_dir / "tree.hash").unlink()
    staged = stage(kb, "F-0002", "motor", CLAIM_B, topic="motor")

    result = store.put(kb.cfg, staged)

    assert result.ok and not result.recorded
    assert read_recorded(kb.cfg) is None
    assert ("kblam put F-0002-motor.md: no .kblam/tree.hash, and the review root holds records; "
            "tree.hash not advanced. Run kblam validate --record once the tree validates.") \
        in capsys.readouterr().err


def test_a_missing_tree_hash_without_records_is_bootstrapped_as_format_2(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    (kb.cfg.state_dir / "tree.hash").unlink()
    staged = stage(kb, "F-0002", "motor", CLAIM_B, topic="motor")
    capsys.readouterr()

    result = store.put(kb.cfg, staged)

    assert result.recorded
    assert read_recorded(kb.cfg) == (2, kb.cfg.review_dir, tree_digest_v2(load_view(kb.cfg)))
    assert capsys.readouterr().err == ""


def test_a_multi_file_put_journals_and_then_removes_the_journal(kb, monkeypatch):
    """SPEC §5.1.6 Interrupted writes: the journal is written before the first change and deleted last."""
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write(SC, record_text("SC"))
    kb.reindex()                                    # the fixture accepts the tree, as --record would
    seen = []
    real_write = atomic_write

    def recording(path, data):
        journal = kb.cfg.journal_path
        seen.append((path.name, journal.is_file(),
                     json.loads(journal.read_text(encoding="utf-8")) if journal.is_file() else None))
        real_write(path, data)

    monkeypatch.setattr(store, "atomic_write", recording)
    staged = stage(kb, "F-0002", "motor", CLAIM_B, topic="motor")

    result = store.put(kb.cfg, staged)

    assert result.ok and result.recorded
    assert not kb.cfg.journal_path.exists()
    assert seen[0] == ("journal.json", False, None)
    assert [name for name, present, _record in seen if present] == \
        ["F-0002-motor.md", "INDEX.md", "tree.hash"]
    assert seen[1][2]["command"] == "put F-0002-motor.md"
    assert seen[1][2]["paths"] == ["findings/motor/F-0002-motor.md", "findings/INDEX.md"]
    assert registry.read_ids(kb.cfg) == {"SC-0001"}


def test_a_body_only_edit_journals_nothing(kb, monkeypatch):
    """INDEX.md is written only when it changes, so a one-file put writes no journal (SPEC §5.1.6)."""
    kb.add("F-0001", "sensor", CLAIM_A)
    journals = []
    real_write = atomic_write

    def recording(path, data):
        if path.name == "journal.json":
            journals.append(json.loads(data.decode("utf-8"))["paths"])  # the bytes it is given
        real_write(path, data)

    monkeypatch.setattr(store, "atomic_write", recording)
    staged = edit_finding(kb.cfg, "F-0001")
    replace_in(staged, "about 0.1%", "within 0.2%")

    assert store.put(kb.cfg, staged).ok
    assert journals == []
    assert "within 0.2%" in (kb.findings / "calibration" / "F-0001-sensor.md").read_text(encoding="utf-8")
