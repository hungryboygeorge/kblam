"""`kblam upgrade` and state recorded before fingerprint v2 (SPEC §5.1, §6.4, §6.5, §7 upgrade, §9
"Upgrading"): K1 reports an old-format stamp; commands that would read old machine state as changes refuse
until the upgrade, and the Stop hook only notes it; the upgrade re-stamps current stamps (staged copies
too), follows the tree.hash rule, moves items, checked marks and cached answers to v2 fingerprints and
state hashes, and prints the per-question prompt ids. The "old kblam" state here is written the way
the kblam before M6.10 wrote it. Nothing touches the network."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing

import pytest

from kblam import review
from kblam.finding import fingerprint, fingerprint_v1, parse_finding
from kblam.jev import CACHE_NAME, PairCache, Side, jev_settings
from kblam.lock import kb_lock
from kblam.review import ReviewItem, load_items, save_items
from kblam.store import edit_finding, put
from kblam.treehash import read_tree_hash, tree_digest
from kblam.view import load_view

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, finding_text
from test_check import E1, E2, N, jkb, run  # noqa: F401 (jkb is a fixture)
from test_hook import call, stop

CLAIM_A = "The sensor's two curve types agree to about 0.1%, so they are not two analog gains."
CLAIM_B = "The pump motor reaches steady output after 90 seconds of warm-up at 4000 rpm."
CLAIM_C = "The media tray reports its type through two contact pins read at load time."
EARLIER = "An earlier version of this claim, which a later put rewrote."


def found(kb, finding_id: str):
    return next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)


def v1_of(kb, finding_id: str) -> str:
    return fingerprint_v1(found(kb, finding_id))


def v2_of(kb, finding_id: str) -> str:
    return fingerprint(found(kb, finding_id), "/")


def v1_of_text(finding_id: str, claim: str, **kw) -> str:
    """The v1 fingerprint of a version of `finding_id` that is not in the KB (it was rewritten since)."""
    return fingerprint_v1(parse_finding(f"findings/calibration/{finding_id}-x.md",
                                        finding_text(finding_id, claim, **kw).encode("utf-8")))


def cache_path(kb):
    return kb.cfg.state_dir / CACHE_NAME


def old_marks(kb, *finding_ids: str) -> None:
    """Checked marks as the kblam before fingerprint v2 recorded them."""
    PairCache(cache_path(kb))
    with closing(sqlite3.connect(cache_path(kb))) as conn, conn:
        for finding_id in finding_ids:
            conn.execute("INSERT INTO checked VALUES (?, ?, ?)", (finding_id, v1_of(kb, finding_id),
                                                                  "2026-09-20T10:00:00Z"))


def old_item(item_id: str, kind: str, new: tuple[str, str], existing: tuple[str, str] | None = None,
             verdict: str | None = "restates_and_extends") -> ReviewItem:
    return ReviewItem(item_id, kind, "open", new[0], new[1], *(existing or (None, None)),
                      verdict=None if kind == "unchecked" else verdict, message="raised by the old kblam",
                      created="2026-09-20T10:00:00Z")


def two_findings(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor")


# --- the knowledge base: depends_on stamps ------------------------------------------------------------


def test_k1_reports_an_old_stamp_naming_upgrade_and_k3_does_not_repeat_it(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=f"depends_on:\n  F-0001: {v1_of(kb, 'F-0001')}\n")
    issues = kb.issues()
    assert [(i.code, i.path.rsplit("/", 1)[-1]) for i in issues] == [("K1", "F-0002-motor.md")]
    assert "old-format fingerprint (8 digits" in issues[0].message and "run kblam upgrade" in issues[0].message
    assert "kblam ack F-0002 F-0001" in issues[0].message


def test_upgrade_restamps_current_stamps_byte_for_byte_and_leaves_stale_ones(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    current, stale = v1_of(kb, "F-0001"), v1_of_text("F-0001", EARLIER)
    two = kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=f"depends_on:\n  F-0001: {current}   # read\n")
    three = kb.add("F-0003", "tray", CLAIM_C, topic="tray", extra=f"depends_on: {{F-0001: {stale}}}\n")
    before_two, before_three = two.read_bytes(), three.read_bytes()
    capsys.readouterr()

    assert run(kb, "upgrade") == 0
    out = capsys.readouterr().out
    new = v2_of(kb, "F-0001")
    assert two.read_bytes() == before_two.replace(current.encode(), new.encode())  # nothing else changed
    assert three.read_bytes() == before_three
    assert f"kblam upgrade: re-stamped 1 depends_on value(s) in findings/ with v2 fingerprints:\n" \
           f"  findings/motor/F-0002-motor.md: F-0001 {current} -> {new}\n" in out
    assert f"1 depends_on value(s) stay in the old format, because their target changed since" in out
    assert f"  findings/tray/F-0003-tray.md: F-0001 {stale}\n" in out
    assert "kblam upgrade: recorded .kblam/tree.hash for the tree" in out
    assert read_tree_hash(kb.cfg) == tree_digest(load_view(kb.cfg))
    assert "commit the re-stamped findings, so every clone has them" in out
    assert [(i.code, i.path.rsplit("/", 1)[-1]) for i in kb.issues()] == [("K1", "F-0003-tray.md")]

    assert run(kb, "upgrade") == 0  # again: nothing more to do, and the stale stamp is still named
    out = capsys.readouterr().out
    assert "re-stamped" not in out and f"F-0001 {stale}" in out and "nothing to upgrade" not in out


def test_upgrade_leaves_tree_hash_stale_after_a_change_outside_kblam(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=f"depends_on:\n  F-0001: {v1_of(kb, 'F-0001')}\n")
    recorded = read_tree_hash(kb.cfg)
    kb.write("findings/motor/notes.txt", "written by hand\n")  # findings/ changed outside kblam
    capsys.readouterr()
    assert run(kb, "upgrade") == 0
    assert "left .kblam/tree.hash as it was" in capsys.readouterr().out
    assert read_tree_hash(kb.cfg) == recorded


def test_upgrade_restamps_a_staged_copy_and_its_edit_still_puts(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=f"depends_on:\n  F-0001: {v1_of(kb, 'F-0001')}\n")
    staged = edit_finding(kb.cfg, "F-0002")
    capsys.readouterr()
    assert run(kb, "upgrade") == 0
    out = capsys.readouterr().out
    new = v2_of(kb, "F-0001")
    assert f"F-0001: {new}" in staged.read_text(encoding="utf-8")
    assert "re-stamped 1 depends_on value(s) in staged findings" in out
    assert "updated the edit record of F-0002, whose staged copy still puts over the re-stamped file" in out
    staged.write_text(staged.read_text(encoding="utf-8").replace("Title of F-0002", "Warm-up of the pump motor"),
                      encoding="utf-8", newline="\n")
    result = put(kb.cfg, staged)
    assert result.ok, [i.format(result.view) for i in result.issues]


# --- this machine's state ------------------------------------------------------------------------------


def test_commands_refuse_old_state_until_upgrade_and_nothing_is_closed_meanwhile(kb, capsys, monkeypatch):
    two_findings(kb)
    items = [old_item("R-old00001", "review", ("F-0002", v1_of(kb, "F-0002")), ("F-0001", v1_of(kb, "F-0001"))),
             old_item("U-old00002", "unchecked", ("F-0001", v1_of(kb, "F-0001")))]
    save_items(kb.cfg, items)
    old_marks(kb, "F-0001")
    before = (kb.cfg.state_dir / "review.jsonl").read_bytes()
    capsys.readouterr()
    for args in (["validate"], ["validate", "--record"], ["check"], ["audit"], ["items"],
                 ["resolve", "R-old00001", "--distinct", "why"], ["rm", "F-0002", "--merged-into", "F-0001"]):
        assert run(kb, *args) == 1, args
        err = capsys.readouterr().err
        assert "state recorded before fingerprint v2 (2 open item(s) in .kblam/review.jsonl; 1 checked mark(s)" in err
        assert "run kblam upgrade" in err and err.rstrip().endswith("Load the kblam-write skill for how to fix this.")
    assert (kb.cfg.state_dir / "review.jsonl").read_bytes() == before
    assert run(kb, "deps", "F-0001") == 0 and run(kb, "index") == 0  # commands that read no such state run

    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    code, answer, _ = call("Stop", stop(kb), monkeypatch, capsys)
    assert code == 0 and "decision" not in answer and "run kblam upgrade" in answer["systemMessage"]
    assert (kb.cfg.state_dir / "review.jsonl").read_bytes() == before


def test_upgrade_moves_open_items_to_v2_under_the_ids_a_check_gives_and_closes_changed_ones(kb, capsys):
    two_findings(kb)
    kb.add("F-0003", "tray", CLAIM_C, topic="tray")
    items = [old_item("R-old00001", "review", ("F-0002", v1_of(kb, "F-0002")), ("F-0001", v1_of(kb, "F-0001"))),
             old_item("U-old00002", "unchecked", ("F-0001", v1_of(kb, "F-0001"))),
             old_item("R-old00003", "review", ("F-0003", v1_of_text("F-0003", EARLIER, topic="tray")),
                      ("F-0001", v1_of(kb, "F-0001"))),  # F-0003 was rewritten since
             old_item("R-old00004", "review", ("F-0002", v1_of(kb, "F-0002")), ("F-0003", v1_of(kb, "F-0003")))]
    items[3].close("distinct: already decided")  # closed items stay as they were
    save_items(kb.cfg, items)
    capsys.readouterr()
    assert run(kb, "upgrade") == 0
    out = capsys.readouterr().out
    after = {i.id: i for i in load_items(kb.cfg)}
    moved = [i for i in after.values() if i.open]
    assert sorted((i.kind, i.new_id, i.new_fp, i.existing_fp) for i in moved) == sorted([
        ("review", "F-0002", v2_of(kb, "F-0002"), v2_of(kb, "F-0001")),
        ("unchecked", "F-0001", v2_of(kb, "F-0001"), None)])
    assert all(i.id == review.item_id_of(i) for i in moved)
    assert "moved 2 open item(s) to v2 fingerprints" in out and "R-old00001 is now R-" in out
    assert "closed 1 item(s) whose finding changed since it was raised: R-old00003" in out
    assert after["R-old00003"].close_reason == "F-0003 changed" and after["R-old00004"].new_fp == v1_of(kb, "F-0002")
    assert run(kb, "validate") == 1  # still open, and listed
    listed = capsys.readouterr().out
    assert all(i.id in listed for i in moved)


def test_after_upgrade_checked_marks_and_cached_answers_ask_jev_nothing_again(jkb, capsys):
    """The state of an old kblam is simulated from a real check: its marks and answers re-keyed as the
    kblam before M6.10 keyed them (v1 fingerprints, the combined prompt id)."""
    jkb.add("F-0001", "motor", E1)
    jkb.add("F-0002", "drift", N)
    jkb.add("F-0003", "tray", E2, topic="tray")
    assert run(jkb, "check") == 0
    assert run(jkb, "audit") == 0
    asked = len(jkb.fake.requests)
    assert asked > 0
    view = load_view(jkb.cfg)
    by_state = {Side.of(f, "/").state_hash: f for f in view.findings}
    settings = jev_settings(jkb.cfg)
    with closing(sqlite3.connect(cache_path(jkb))) as conn, conn:
        for finding in view.findings:
            conn.execute("UPDATE checked SET fp = ? WHERE finding_id = ?", (fingerprint_v1(finding), finding.file_id))
        rows = conn.execute("SELECT rowid, existing_fp, new_fp FROM answers").fetchall()
        for rowid, existing, new in rows:
            conn.execute("UPDATE answers SET prompt_id = ?, existing_fp = ?, new_fp = ? WHERE rowid = ?",
                         (settings.prompt_id, fingerprint_v1(by_state[existing]) if existing else "",
                          fingerprint_v1(by_state[new]), rowid))
    capsys.readouterr()
    assert run(jkb, "check") == 1 and "3 checked mark(s)" in capsys.readouterr().err

    assert run(jkb, "upgrade") == 0
    out = capsys.readouterr().out
    assert "carried 3 checked mark(s) over to v2 fingerprints" in out
    assert f"re-keyed {len(rows)} cached Jev answer(s) by state hash" in out
    assert (f"relation_prompt_id = \"{settings.relation_prompt_id}\" and revision_prompt_id = "
            f"\"{settings.revision_prompt_id}\"") in out  # the fixture's thresholds record prompt_id
    assert run(jkb, "check") == 0
    assert "every finding has been checked at its current fingerprint" in capsys.readouterr().out
    assert run(jkb, "audit") == 0
    assert len(jkb.fake.requests) == asked  # nothing asked again


def test_upgrade_never_edits_kblam_toml_and_says_so_when_nothing_is_old(jkb, capsys):
    before = (jkb.root / "kblam.toml").read_bytes()
    capsys.readouterr()
    assert run(jkb, "upgrade") == 0
    out = capsys.readouterr().out
    assert "records prompt_id, the id of the whole prompt" in out and "run kblam approve-config" in out
    assert (jkb.root / "kblam.toml").read_bytes() == before


def test_with_nothing_old_upgrade_says_so(kb, capsys):
    two_findings(kb)
    capsys.readouterr()
    assert run(kb, "upgrade") == 0
    assert capsys.readouterr().out == ("kblam upgrade: nothing to upgrade; the knowledge base and this machine's "
                                       "state are already in the M6.10 formats\n")


def test_upgrade_waits_for_the_lock_and_times_out_with_exit_3(kb, capsys):
    kb.write("kblam.toml", KBLAM_TOML + "lock_wait_seconds = 0\n" + NO_EMBEDDINGS + PROMPT_TOML)
    with kb_lock(kb.cfg, "test holder"):
        assert run(kb, "upgrade") == 3


@pytest.mark.parametrize("damage, code, message", [
    ("review", 1, "review.jsonl"),
    ("resolutions", 2, "kblam.resolutions.jsonl"),
    ("cache", 1, "was written by an older kblam"),  # answers keyed by the integer prompt_version (§9)
])
def test_an_upgrade_that_refuses_has_written_nothing(kb, capsys, damage, code, message):
    kb.add("F-0001", "sensor", CLAIM_A)
    two = kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=f"depends_on:\n  F-0001: {v1_of(kb, 'F-0001')}\n")
    if damage == "review":
        kb.write(".kblam/review.jsonl", "not an item\n")
    elif damage == "resolutions":
        kb.write("kblam.resolutions.jsonl", "not a resolution\n")
    else:
        with closing(sqlite3.connect(cache_path(kb))) as conn, conn:
            conn.execute("CREATE TABLE answers (expected_model TEXT, prompt_version INTEGER, kind TEXT, "
                         "existing_fp TEXT, new_fp TEXT, answer TEXT)")
    before, recorded = two.read_bytes(), read_tree_hash(kb.cfg)
    capsys.readouterr()
    assert run(kb, "upgrade") == code
    assert message in capsys.readouterr().err
    assert two.read_bytes() == before and read_tree_hash(kb.cfg) == recorded  # the stamp is still v1


def test_another_machine_upgrades_its_own_state_after_pulling_an_upgraded_kb(kb, tmp_path, capsys):
    """Machine A upgrades the knowledge base and commits it; machine B, whose .kblam/ was written by the old
    kblam, then only has its own state to migrate."""
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=f"depends_on:\n  F-0001: {v1_of(kb, 'F-0001')}\n")
    b_items = [old_item("R-old00001", "review", ("F-0002", v1_of(kb, "F-0002")), ("F-0001", v1_of(kb, "F-0001")))]
    assert run(kb, "upgrade") == 0  # machine A
    machine_b = tmp_path / "machine-b"
    shutil.copytree(kb.root, machine_b, ignore=shutil.ignore_patterns(".kblam"))  # the pulled tree
    b = type(kb)(machine_b)
    save_items(b.cfg, b_items)
    old_marks(b, "F-0001")  # fingerprint_v1 of F-0001 is the same on both machines
    capsys.readouterr()
    assert run(b, "upgrade") == 0
    out = capsys.readouterr().out
    assert "re-stamped" not in out
    assert "moved 1 open item(s) to v2 fingerprints" in out and "carried 1 checked mark(s) over" in out
    assert run(b, "upgrade") == 0 and "nothing to upgrade" in capsys.readouterr().out


# --- the places that must still recognise an old stamp until the upgrade ----------------------------------


def test_items_reworded_compares_fingerprints_only_in_one_format(kb, capsys):
    two_findings(kb)
    across = old_item("R-old00005", "rejected", ("F-0002", v1_of_text("F-0002", EARLIER, topic="motor")),
                      ("F-0001", v1_of(kb, "F-0001")), verdict="same_fact")
    across.close("F-0002 was put")
    across.closed_fp = v2_of(kb, "F-0002")  # rejected before fingerprint v2, put after it: not comparable
    before = old_item("R-old00006", "rejected", ("F-0002", v1_of_text("F-0002", EARLIER, topic="motor")),
                      ("F-0001", v1_of(kb, "F-0001")), verdict="same_fact")
    before.close("F-0002 was put")
    before.closed_fp = v1_of(kb, "F-0002")  # both before: comparable, and F-0001 is unchanged under v1
    save_items(kb.cfg, [across, before])
    capsys.readouterr()
    assert run(kb, "items", "--reworded") == 0
    out = capsys.readouterr().out
    assert "R-old00006" in out and "R-old00005" not in out


def test_renumber_follows_an_old_stamp_to_the_file_it_meant(kb, capsys):
    mine = kb.add("F-0005", "sensor", CLAIM_A, title="Sensor curve types")
    kb.add("F-0005", "motor", CLAIM_B, topic="motor", title="Motor warm-up")
    old = fingerprint_v1(parse_finding(mine.name, mine.read_bytes()))
    dependent = kb.add("F-0006", "uses-mine", CLAIM_C, topic="tray", extra=f"depends_on:\n  F-0005: '{old}'\n")
    capsys.readouterr()
    assert run(kb, "renumber", str(mine)) == 0
    renamed, = (kb.findings / "calibration").glob("F-*-sensor.md")
    new = fingerprint(parse_finding(renamed.name, renamed.read_bytes()), "/")
    text = dependent.read_text(encoding="utf-8")
    assert f"{renamed.name[:6]}: {new}" in text and old not in text and "F-0005" not in text
