"""`kblam init [--update]` and the review root: the review INDEX.md, the record-ID registry and the
format-2 tree.hash (SPEC §11 step 6, §12 M6.5 "As built" order, §5.2.6 "Record-ID registry",
"tree.hash, format 2" and "Upgrade", §12 M6.11 test group 9).

Init runs in a fresh `git init` repository under tmp_path (the fixture is test_init's). Nothing here
reaches Jev or the network: every validation is `rules.validate`, which asks no model. The hook check is
replaced, so no shell runs init's hook commands.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from kblam import records, review, rules, treehash
from kblam.review import ReviewItem
from kblam.review_index import generate_review_index
from kblam.view import load_view

from conftest import KB, ZERO64, dump_record, record_data
import test_init
from test_init import actions, kblam_init, snapshot

# The repository fixture and the hook-check stub test_init defines, reused here by name: a fixture reaches
# a test module through its namespace, so these two names are what pytest looks up for the tests below.
repo = test_init.repo
no_hook_check = test_init.no_hook_check

REVIEW = "research-review"
REVIEW_INDEX = f"{REVIEW}/INDEX.md"
TREE_HASH = ".kblam/tree.hash"
REGISTRY = ".kblam/review-ids"
CLAIM = "The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0)."
ROOT_MESSAGE = ("the review root changed from research-review to research-notes in kblam.toml; "
                "schema 1 fixes it at init")
# The item lines of init's output in the order SPEC §12 M6.5 "As built" lists them.
SPEC_ORDER = ["kblam.toml", "findings/INDEX.md", REVIEW_INDEX, ".gitattributes", ".gitignore",
              ".claude/rules/kblam-findings.md", ".claude/skills/kblam-write/SKILL.md",
              ".claude/settings.json", "CLAUDE.md", ".git/hooks/pre-commit", TREE_HASH]
ACTIONS = ("created", "updated", "unchanged", "kept", "refused")


# --- helpers ------------------------------------------------------------------------------------


def item_lines(out: str) -> list[list[str]]:
    """(action, path, note) of every item line init printed, in the order it printed them."""
    parts = [line.split(None, 2) for line in out.splitlines() if line.startswith("  ")]
    return [p + [""] * (3 - len(p)) for p in parts if len(p) >= 2 and p[0] in ACTIONS]


def order(out: str) -> list[str]:
    """The paths of those lines, in report order."""
    return [parts[1] for parts in item_lines(out)]


def note(out: str, path: str) -> list[str]:
    """The note of the item line for `path`, without the parentheses init prints it in."""
    notes = [parts[2].strip() for parts in item_lines(out) if parts[1] == path]
    return [text[1:-1] if text.startswith("(") and text.endswith(")") else text for text in notes]


def ensure_kb(repo: Path) -> KB:
    """The repository as `kblam init` leaves it, plus an evidence package and one finding, so the tree
    validates cleanly (K2 needs the cited evidence to exist) with both indexes and tree.hash current."""
    evidence = repo / "evidence" / "2026-09-22-ratio"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "README.md").write_text("manifest\n", encoding="utf-8", newline="\n")
    kb = KB(repo)
    # The init template's only scope is "any", so the finding has to use it to validate cleanly.
    kb.add("F-0001", "sensor", CLAIM, scope="[any]")   # writes the finding, the index and tree.hash
    return kb


def add_record(repo: Path, kind: str, rec_id: str | None = None) -> Path:
    """A record at its canonical path, as a reviewer's put would leave it."""
    rec_id = rec_id or f"{kind}-0001"
    path = repo / REVIEW / records.KINDS[kind] / f"{rec_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_record(record_data(kind, rec_id)), encoding="utf-8", newline="\n")
    return path


def go_legacy(repo: Path, digest: str | None = None) -> bytes:
    """Replace tree.hash with a format-1 line, as a KB from before schema 1 has it."""
    data = (digest or treehash.tree_digest(load_view(KB(repo).cfg))).encode("ascii") + b"\n"
    (repo / TREE_HASH).write_bytes(data)
    return data


def add_review_item(repo: Path, item: ReviewItem) -> None:
    """One item in `.kblam/review.jsonl`, as `kblam check` would leave it."""
    (repo / ".kblam").mkdir(exist_ok=True)
    (repo / ".kblam" / "review.jsonl").write_text(json.dumps(asdict(item), sort_keys=True) + "\n",
                                                  encoding="utf-8", newline="\n")


def add_review_items(repo: Path) -> None:
    """A review and an unchecked item, plus the Jev caches: state kblam never rewrites."""
    items = [ReviewItem("R-1a2b3c4d", "review", "open", "F-0001", "0badf00d0000", "F-0002", "0badf00d0000",
                        "same_fact", "new", 0.9, 0.9, None, "the two state one fact", "2026-09-28T00:00:00Z"),
             ReviewItem("U-5e6f7a8b", "unchecked", "open", "F-0001", "0badf00d0000", message="Jev was away",
                        created="2026-09-28T00:00:00Z")]
    text = "".join(json.dumps(asdict(item), sort_keys=True) + "\n" for item in items)
    (repo / ".kblam").mkdir(exist_ok=True)
    (repo / ".kblam" / "review.jsonl").write_text(text, encoding="utf-8", newline="\n")
    (repo / ".kblam" / "pairs.sqlite").write_bytes(b"pair cache bytes")
    (repo / ".kblam" / "calls.jsonl").write_bytes(b'{"status": "cache_hit"}\n')


def set_review_root(repo: Path, root: str) -> None:
    """Point [review] root at another folder in kblam.toml, as a project editing it does."""
    with (repo / "kblam.toml").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f'\n[review]\nroot = "{root}"\n')


# --- a fresh repository -------------------------------------------------------------------------


def test_a_fresh_repo_gets_both_indexes_a_registry_free_tree_hash(repo, capsys, no_hook_check):
    """Both indexes, the format-2 tree.hash, no registry (a KB with no records gets none: D28 b), and the
    item lines in SPEC §12 M6.5 "As built" order."""
    code, out, err = kblam_init(capsys)

    assert (code, err) == (0, ""), out + err
    assert order(out) == SPEC_ORDER
    assert actions(out)[REVIEW_INDEX] == "created" and actions(out)[TREE_HASH] == "created"
    assert actions(out)[".gitattributes"] == "created"
    assert note(out, REVIEW_INDEX) == ["kblam review index"]
    assert note(out, ".gitattributes") == [
        "added findings/** -text and kblam.resolutions.jsonl merge=union and research-review/** -text"]
    assert len([line for line in out.splitlines() if ".gitattributes" in line]) == 1
    assert (repo / ".gitattributes").read_text(encoding="utf-8") == \
        "findings/** -text\nkblam.resolutions.jsonl merge=union\nresearch-review/** -text\n"

    kb = KB(repo)
    assert (repo / REVIEW_INDEX).read_bytes() == generate_review_index(load_view(kb.cfg))
    assert treehash.read_recorded(kb.cfg) == (2, REVIEW, treehash.tree_digest_v2(load_view(kb.cfg)))
    assert not (repo / REGISTRY).exists()
    assert rules.errors(rules.validate(load_view(kb.cfg))) == []      # K13 is clean over the new index


def test_a_second_update_changes_nothing(repo, capsys, no_hook_check):
    """Idempotent: a second run reports `unchanged` for every item and writes no byte (SPEC §11 step 6)."""
    assert kblam_init(capsys)[0] == 0
    before = snapshot(repo)

    code, out, _ = kblam_init(capsys, "--update")

    assert code == 0
    assert set(actions(out).values()) == {"unchanged"}
    assert order(out) == SPEC_ORDER
    assert snapshot(repo) == before
    assert treehash.read_recorded(KB(repo).cfg)[0] == 2


def test_a_populated_review_index_is_never_replaced(repo, capsys, no_hook_check):
    """A review index that is there, whatever it holds, is reported and left alone -- the freshly
    generated one for the records present, and a hand-edited one (SPEC §11 step 6; §12 group 9)."""
    assert kblam_init(capsys)[0] == 0
    add_record(repo, "SC")
    index = repo / REVIEW_INDEX
    populated = generate_review_index(load_view(KB(repo).cfg))
    assert populated != index.read_bytes()      # the index init wrote knows nothing of the new record
    index.write_bytes(populated)

    code, out, _ = kblam_init(capsys, "--update")
    assert code == 0 and actions(out)[REVIEW_INDEX] == "unchanged"
    assert index.read_bytes() == populated

    index.write_bytes(b"# hand written, do not touch\n")
    code, out, _ = kblam_init(capsys, "--update")
    assert code == 0 and actions(out)[REVIEW_INDEX] == "unchanged"
    assert note(out, REVIEW_INDEX) == ["exists; not regenerated"]
    assert index.read_bytes() == b"# hand written, do not touch\n"


# --- the registry -------------------------------------------------------------------------------


def test_the_registry_is_created_from_the_records_present(repo, capsys, no_hook_check):
    """SPEC §5.2.6 "Record-ID registry": created from the records present, with no report line of its own."""
    assert kblam_init(capsys)[0] == 0
    add_record(repo, "SC")
    add_record(repo, "CT")

    code, out, _ = kblam_init(capsys, "--update")

    assert code == 0
    assert (repo / REGISTRY).read_bytes() == (json.dumps(["CT-0001", "SC-0001"]) + "\n").encode("utf-8")
    assert REGISTRY not in out                                   # the As built order has no registry line


def test_a_registry_already_there_is_left_as_it_is(repo, capsys, no_hook_check):
    """A registry is written only when there is none: the one a clone carries survives `init --update`."""
    assert kblam_init(capsys)[0] == 0
    (repo / REGISTRY).write_bytes(b'["SC-0001"]\n')
    add_record(repo, "CT")

    assert kblam_init(capsys, "--update")[0] == 0

    assert (repo / REGISTRY).read_bytes() == b'["SC-0001"]\n'


# --- tree.hash ----------------------------------------------------------------------------------


def test_an_interrupted_write_is_recovered_before_tree_hash_is_read(repo, capsys, no_hook_check):
    """SPEC §5.2.6 "Interrupted writes": recovery restores tree.hash to the journal's value, and init
    reads the file after it, so it reports `kept` instead of recording over the restored value."""
    assert kblam_init(capsys)[0] == 0
    ensure_kb(repo)
    restored = treehash.format_line(REVIEW, ZERO64)                        # the journal's tree.hash
    before = {rel: data for rel, (data, _mtime) in snapshot(repo).items() if rel != TREE_HASH}
    (repo / ".kblam" / "journal.json").write_text(
        json.dumps({"command": "put", "paths": ["findings/INDEX.md", REVIEW_INDEX], "tree_hash": restored}),
        encoding="utf-8", newline="\n")

    code, out, err = kblam_init(capsys, "--update")

    assert code == 0, out + err
    assert "the interrupted put may be partial" in err
    assert actions(out)[TREE_HASH] == "kept"
    assert note(out, TREE_HASH) == \
        [f"findings/ or {REVIEW}/ changed outside kblam; run kblam validate --record"]
    after = {rel: data for rel, (data, _mtime) in snapshot(repo).items()}
    assert after.pop(TREE_HASH) == restored.encode("utf-8")     # the value recovery restored, kept
    assert after == before                                      # and no byte elsewhere changed


def test_a_matched_format_2_tree_hash_records_inits_own_writes(repo, capsys, no_hook_check):
    """A review INDEX.md init creates is recorded on init's own tree.hash line, and a second run then
    reports `unchanged` for every item (SPEC §12 M6.5 "As built")."""
    assert kblam_init(capsys)[0] == 0
    kb = ensure_kb(repo)
    (repo / REVIEW_INDEX).unlink()
    (repo / TREE_HASH).write_bytes(
        treehash.format_line(REVIEW, treehash.tree_digest_v2(load_view(kb.cfg))).encode("utf-8"))

    code, out, err = kblam_init(capsys, "--update")

    assert (code, err) == (0, "")
    assert actions(out)[REVIEW_INDEX] == "created"
    assert actions(out)[TREE_HASH] == "updated"
    assert note(out, TREE_HASH) == ["init's own writes recorded"]
    assert treehash.read_recorded(kb.cfg) == (2, REVIEW, treehash.tree_digest_v2(load_view(kb.cfg)))

    code, out, _ = kblam_init(capsys, "--update")
    assert code == 0
    assert set(actions(out).values()) == {"unchanged"}


def test_a_format_1_tree_hash_migrates_when_the_findings_tree_matches(repo, capsys, no_hook_check):
    """SPEC §5.2.6 "Upgrade": format 2 only from a matching findings tree and a clean validation, and Jev
    state, `R-`/`U-` items and finding text are untouched."""
    assert kblam_init(capsys)[0] == 0
    kb = ensure_kb(repo)
    add_review_items(repo)
    moved = ["findings/calibration/F-0001-sensor.md", ".kblam/review.jsonl", ".kblam/pairs.sqlite",
             ".kblam/calls.jsonl"]
    before = {rel: (repo / rel).read_bytes() for rel in moved}
    go_legacy(repo)

    code, out, err = kblam_init(capsys, "--update")

    assert (code, err) == (0, ""), out + err
    assert actions(out)[TREE_HASH] == "updated"
    assert note(out, TREE_HASH) == ["migrated to format 2"]
    text = (repo / TREE_HASH).read_text(encoding="utf-8")
    assert text.startswith(f"kblam-tree-v2 {REVIEW} ") and len(text.split()[2]) == 64
    assert treehash.read_recorded(kb.cfg) == (2, REVIEW, treehash.tree_digest_v2(load_view(kb.cfg)))
    for rel in moved:
        assert (repo / rel).read_bytes() == before[rel], rel


def test_a_created_findings_index_says_nothing_about_tree_hash(repo, capsys, no_hook_check):
    """Init writes the findings INDEX.md itself, so the migration is the only tree.hash line and nothing
    warns about an index write of init's own (D32 f)."""
    assert kblam_init(capsys)[0] == 0
    kb = ensure_kb(repo)
    (repo / "findings" / "INDEX.md").unlink()
    go_legacy(repo)                    # format 1, matching the findings tree: the index is not in it

    code, out, err = kblam_init(capsys, "--update")

    assert (code, err) == (0, ""), out + err
    assert actions(out)["findings/INDEX.md"] == "created"
    assert actions(out)[TREE_HASH] == "updated"
    assert note(out, TREE_HASH) == ["migrated to format 2"]
    assert (repo / "findings" / "INDEX.md").is_file()
    assert treehash.read_recorded(kb.cfg)[0] == 2


def test_a_format_1_tree_hash_is_kept_after_an_out_of_band_change(repo, capsys, no_hook_check):
    """A format-1 digest the findings tree no longer matches is left for `kblam validate --record`, and
    `kept` does not change the exit status (SPEC §5.2.6 "Upgrade")."""
    assert kblam_init(capsys)[0] == 0
    ensure_kb(repo)
    legacy = go_legacy(repo, ZERO64)
    before = snapshot(repo)

    code, out, err = kblam_init(capsys, "--update")

    assert (code, err) == (0, ""), out + err
    assert actions(out)[TREE_HASH] == "kept"
    assert note(out, TREE_HASH) == ["run kblam validate --record"]
    assert (repo / TREE_HASH).read_bytes() == legacy
    assert snapshot(repo) == before
    assert treehash.read_recorded(KB(repo).cfg)[0] == 1


def test_a_format_1_tree_hash_is_kept_when_validation_fails(repo, capsys, no_hook_check):
    """A matching digest is not enough: a tree that does not validate stays in format 1."""
    assert kblam_init(capsys)[0] == 0
    kb = ensure_kb(repo)
    (repo / "findings" / "notes.md").write_text("scratch\n", encoding="utf-8", newline="\n")   # K8
    kb.reindex()                                  # the index is current, so K8 is the only error
    assert [i.code for i in rules.errors(rules.validate(load_view(kb.cfg)))] == ["K8"]
    legacy = go_legacy(repo)                      # the digest covers the stray file: the tree matches

    code, out, _ = kblam_init(capsys, "--update")

    assert code == 0
    assert actions(out)[TREE_HASH] == "kept" and note(out, TREE_HASH) == ["run kblam validate --record"]
    assert (repo / TREE_HASH).read_bytes() == legacy


def test_an_open_review_item_blocks_the_format_1_migration(repo, capsys, no_hook_check):
    """SPEC §5.2.6 "Upgrade": the matching digest is not enough, an open item keeps the file as it is.
    The item's fingerprints are F-0001's current ones, so reconcile does not close it."""
    assert kblam_init(capsys)[0] == 0
    kb = ensure_kb(repo)
    fingerprints = review.current_fingerprints(load_view(kb.cfg))
    add_review_item(repo, ReviewItem("R-1a2b3c4d", "review", "open", "F-0001", fingerprints["F-0001"],
                                     verdict="same_fact", winner="new", p=0.9, confidence=0.9,
                                     message="F-0001 and F-0002 state one fact",
                                     created="2026-09-28T00:00:00Z"))
    view = load_view(kb.cfg)
    legacy = go_legacy(repo)                    # format 1, the digest of the findings tree as it is
    assert legacy.decode("ascii").strip() == treehash.tree_digest(view)
    assert rules.errors(rules.validate(view)) == []      # only the open item blocks the migration
    assert len(review.open_items(kb.cfg, view)) == 1

    code, out, err = kblam_init(capsys, "--update")

    assert (code, err) == (0, "")
    assert actions(out)[TREE_HASH] == "kept"
    assert note(out, TREE_HASH) == ["run kblam validate --record"]
    assert (repo / TREE_HASH).read_bytes() == legacy


def test_a_missing_tree_hash_with_records_present_is_not_bootstrapped(repo, capsys, no_hook_check):
    """SPEC §8, §5.2.6: a missing tree.hash is bootstrapped only while the review root holds no records."""
    assert kblam_init(capsys)[0] == 0
    ensure_kb(repo)
    add_record(repo, "SC")
    (repo / TREE_HASH).unlink()

    code, out, err = kblam_init(capsys, "--update")

    assert (code, err) == (0, ""), out + err
    assert actions(out)[TREE_HASH] == "kept"
    assert note(out, TREE_HASH) == \
        ["missing, and the review root holds records; run kblam validate --record"]
    assert not (repo / TREE_HASH).exists()


def test_a_root_change_without_records_is_recorded(repo, capsys, no_hook_check):
    """SPEC §5.2.6: while neither root holds a record and the registry is empty, init records the new
    root, and the old root's index is left as it was."""
    assert kblam_init(capsys)[0] == 0
    kb = ensure_kb(repo)
    old_index = (repo / REVIEW_INDEX).read_bytes()
    set_review_root(repo, "research-notes")

    code, out, _ = kblam_init(capsys, "--update")

    assert code == 0
    assert actions(out)["research-notes/INDEX.md"] == "created"
    assert actions(out)[TREE_HASH] == "updated"
    assert note(out, TREE_HASH) == [f"review root {REVIEW} -> research-notes recorded"]
    assert treehash.read_recorded(kb.cfg) == (2, "research-notes",
                                              treehash.tree_digest_v2(load_view(kb.cfg)))
    assert (repo / REVIEW_INDEX).read_bytes() == old_index


def test_a_root_change_with_records_refuses_and_writes_nothing(repo, capsys, no_hook_check):
    """SPEC §5.2.6: the root is fixed at init while a record exists; the refusal changes nothing."""
    assert kblam_init(capsys)[0] == 0
    kb = ensure_kb(repo)
    record = add_record(repo, "SC")
    set_review_root(repo, "research-notes")
    before = snapshot(repo)

    code, out, err = kblam_init(capsys, "--update")

    assert code == 1 and "Traceback" not in err
    assert ROOT_MESSAGE in err
    assert snapshot(repo) == before
    assert not (repo / "research-notes").exists() and record.is_file()
    assert treehash.read_recorded(kb.cfg)[1] == REVIEW


def test_a_record_added_out_of_band_makes_the_format_2_tree_hash_stale(repo, capsys, no_hook_check):
    """A tree changed outside kblam is not recorded: the line names what changed and what to run."""
    assert kblam_init(capsys)[0] == 0
    kb = ensure_kb(repo)
    before = (repo / TREE_HASH).read_bytes()
    assert before == treehash.format_line(REVIEW, treehash.tree_digest_v2(load_view(kb.cfg))).encode("utf-8")
    add_record(repo, "SC")                          # out of band: no put recorded it

    code, out, _ = kblam_init(capsys, "--update")

    assert code == 0
    assert actions(out)[TREE_HASH] == "kept"
    assert note(out, TREE_HASH) == \
        [f"findings/ or {REVIEW}/ changed outside kblam; run kblam validate --record"]
    assert (repo / TREE_HASH).read_bytes() == before
