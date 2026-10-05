"""M6.11 group 9: root integrity and additive upgrades (Acceptance 7).

Every CLI call has its own whole-KB and source-repository write window. Init's pre-commit
hook is included as well: m.tree deliberately omits Git administrative files.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tomllib
from dataclasses import asdict

import pytest

import m611_helpers as m
from conftest import KB, SourceRepo, TRACE_PATH, TRACE_TEXT, ZERO64, record_data
from kblam import init, records, treehash
from kblam.finding import yaml_rt
from kblam.hook import SKILL_POINTER
from kblam.review import ReviewItem, current_fingerprints
from kblam.review_index import generate_review_index
from kblam.view import load_view

frozen_today = m.frozen_today

REVIEW_INDEX = "research-review/INDEX.md"
TREE_HASH = ".kblam/tree.hash"
REGISTRY = ".kblam/review-ids"
SC_PATH = "research-review/challenges/SC-0001.yaml"
SC_STAGE = ".kblam/review-staging/SC-0001.yaml"
SC_RECEIPT = ".kblam/review-receipts/SC-0001.json"
ROOT_MESSAGE = ("the review root changed from research-review to research-notes in kblam.toml; "
                "schema 1 fixes it at init")
MISSING_MESSAGE = ("K13 research-notes/challenges/SC-0001.yaml: SC-0001 is missing from "
                   "research-notes/; records are never deleted or renamed; restore it from git\n")
INIT_PATHS = {"kblam.toml", "findings/INDEX.md", REVIEW_INDEX, ".gitattributes", ".gitignore",
              ".claude/rules/kblam-findings.md", ".claude/skills/kblam-write/SKILL.md",
              ".claude/settings.json", "CLAUDE.md", ".git/hooks/pre-commit", TREE_HASH,
              ".kblam/config-approved"}


def tree(kb):
    snapshot = m.tree(kb)
    hook = kb.root / ".git/hooks/pre-commit"
    if hook.is_file():
        snapshot[".git/hooks/pre-commit"] = hook.read_bytes()
    return snapshot


@contextlib.contextmanager
def changes(kb, source_repo, expected):
    before, source = tree(kb), source_repo.snapshot()
    try:
        yield
    finally:
        assert source_repo.snapshot() == source, "source repository changed"
        actual = m.changed(before, tree(kb))
        assert actual == expected, f"unexpected KB changes: {actual ^ expected}"


def call(kb, source_repo, argv, *, changed=(), code=0, out="", err=""):
    with changes(kb, source_repo, set(changed)):
        if argv[0] == "init":
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = m.main(list(argv))  # init rejects --root; the fixture sets cwd
            run = m.Run(status, stdout.getvalue(), stderr.getvalue())
        else:
            run = m.kblam(kb, *argv)
        assert run == m.Run(code, out, err)
    return run


def validate(kb, source_repo, *, code=0, diagnostics="", findings=0):
    summary = (f"kblam validate: OK ({findings} findings)\n" if code == 0 else
               f"kblam validate: {code} error(s) in findings/\n")
    call(kb, source_repo, ["validate"], code=int(code != 0), out=diagnostics + summary)


def new_challenge(kb, source_repo, rec_id="SC-0001"):
    staged = f".kblam/review-staging/{rec_id}.yaml"
    receipt = f".kblam/review-receipts/{rec_id}.json"
    path = kb.root / staged
    call(kb, source_repo, ["challenge", "new", m.TRACE, "--lines", "3-3", "--by", "reviewer-a"],
         changed={staged, receipt}, out=f"{path}\n")
    data = yaml_rt().load(path.read_text(encoding="utf-8"))
    fields = record_data("SC")
    for key in ("proposition", "scope", "classification", "basis", "usable", "limits"):
        data[key] = fields[key]
    data["basis"][0]["sha256"] = None
    path.write_bytes(records.dump(data))  # author fills the staged copy outside the CLI window
    return path


def put_challenge(kb, source_repo, path, rec_id="SC-0001", *, missing_hash=False):
    target = f"research-review/challenges/{rec_id}.yaml"
    paths = {target, REVIEW_INDEX, REGISTRY, path.relative_to(kb.root).as_posix()}
    if not missing_hash:
        paths.add(TREE_HASH)
    call(kb, source_repo, ["put", path], changed=paths,
         out=f"kblam put: {rec_id} -> {target}\n",
         err=(f"kblam put {rec_id}: no .kblam/tree.hash, and the review root holds records; tree.hash not "
              "advanced. Run kblam validate --record once the tree validates.\n" if missing_hash else ""))


def set_root(kb):
    text = (kb.root / "kblam.toml").read_text(encoding="utf-8")
    if "review" in tomllib.loads(text):
        text = text.replace('root = "research-review"', 'root = "research-notes"')
    else:
        text += '\n[review]\nroot = "research-notes"\n'
    kb.write("kblam.toml", text)


@pytest.fixture
def fresh_repo(tmp_path, monkeypatch):
    """A Git repository with only a nested, committed read-only source, before init."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    source = SourceRepo(root).init()
    source.commit(TRACE_PATH, TRACE_TEXT, "trace")
    monkeypatch.chdir(root)
    # Hook execution and PATH discovery have their own integration tests. Only this environmental
    # check is stubbed; init still dispatches through the CLI and writes all the real assets.
    monkeypatch.setattr(init.Init, "check_hooks", lambda self, installed, findings_dir: None)
    return KB(root), source


def init_output(kb, *, first=False, hash_action="unchanged", hash_note="", config="unchanged",
                review_action="unchanged", review_root="research-review", root_changed=False):
    items = [
        ("created" if first else config, "kblam.toml",
         "edit [kb] scopes for this project; after an edit, kblam approve-config before you commit it" if first else
         "init never overwrites it" if config == "kept" else ""),
        ("created" if first else "unchanged", "findings/INDEX.md",
         "kblam index" if first else "exists; not regenerated"),
        ("created" if first else review_action, f"{review_root}/INDEX.md",
         "kblam review index" if first or review_action == "created" else "exists; not regenerated"),
        ("created" if first else "updated" if root_changed else "unchanged", ".gitattributes",
         "added findings/** -text and kblam.resolutions.jsonl merge=union and research-review/** -text" if first else
         f"added {review_root}/** -text" if root_changed else "has the kblam lines"),
        ("created" if first else "unchanged", ".gitignore",
         "added .kblam/" if first else "has the kblam line"),
        ("created" if first else "updated" if root_changed else "unchanged", ".claude/rules/kblam-findings.md",
         "rewritten to the installed version" if root_changed else ""),
        ("created" if first else "updated" if root_changed else "unchanged", ".claude/skills/kblam-write/SKILL.md",
         "rewritten to the installed version" if root_changed else ""),
        ("created" if first else "unchanged", ".claude/settings.json",
         "kblam hook entries" if first else "kblam hook entries are current"),
        ("created" if first else "unchanged", "CLAUDE.md",
         "added the kblam line" if first else "has the kblam line"),
        ("created" if first else "unchanged", ".git/hooks/pre-commit", "git pre-commit hook" if first else ""),
        ("created" if first else hash_action, TREE_HASH, hash_note),
    ]
    return (f"kblam init: {kb.root.resolve()}\n" +
            "".join(f"  {action:<9} {path}" + (f" ({note})" if note else "") + "\n"
                    for action, path, note in items) +
            "kblam init: done. Review the files above and commit them.\n")


def initialize(kb, source_repo):
    call(kb, source_repo, ["init"], changed=INIT_PATHS, out=init_output(kb, first=True))


# These are writes to either root, the registry or tree.hash. Staging-only new/edit commands and
# Jev state commands are not mutations of those domains (writes.py's shared frame).
ROOT_MUTATIONS = [
    ("finding-put", ["put", "FINDING_STAGE"]),
    ("record-put", ["put", "SC_STAGE"]),
    ("ack", ["ack", "F-0001", "F-0001"]),
    ("index", ["index"]),
    ("review-index", ["review", "index"]),
    ("decide", ["review", "decide", "SC-0001", "--status", "confirmed", "--by", "reviewer-b",
                "--reason", "reviewed the record", "--expect", ZERO64]),
    ("rebind", ["review", "rebind", "CT-0001", "--by", "reviewer-b", "--reason", "rechecked",
                "--expect", ZERO64]),
    ("pin", ["challenge", "pin", "SC-0001", "--expect", ZERO64]),
    ("record", ["validate", "--record"]),
    ("forget-missing", ["validate", "--record", "--forget-missing"]),
    ("init", ["init"]),
    ("update", ["init", "--update"]),
]


@pytest.mark.parametrize("name, argv", ROOT_MUTATIONS, ids=[p[0] for p in ROOT_MUTATIONS])
def test_format_2_root_change_refuses_every_mutation(kb, source_repo, monkeypatch, name, argv):
    """Acceptance 7: start with open SC-0001, a clean pinned nested source, a registered ID and
    format-2 tree.hash. challenge new --by reviewer-a exits 0 and prints its staged path; changes
    .kblam/review-staging/SC-0001.yaml and .kblam/review-receipts/SC-0001.json. Its put exits 0,
    prints SC-0001's canonical destination and changes research-review/challenges/SC-0001.yaml,
    research-review/INDEX.md, .kblam/review-ids, .kblam/tree.hash and removes the staged copy.
    After a hand edit of the configured root, each parameterized mutation (decision/rebind actor
    reviewer-b; other commands have no --by) exits 1 with the exact root-change refusal.
    finding put changes only .kblam/pairs.sqlite; both validate --record variants change only
    .kblam/pairs.sqlite and .kblam/review.jsonl; every other mutation changes none (D47/D50:
    Jev check work precedes the lock). Neither root, the registry nor tree.hash changes.
    init prints only its header and kept config before refusing. validate afterwards exits 1
    with K13 root-change and missing SC-0001 diagnostics; changes none. Source unchanged per call.
    """
    path = new_challenge(kb, source_repo)
    put_challenge(kb, source_repo, path)
    # Both puts get a syntactically usable staging path; the root guard must run before their reads.
    kb.write(SC_STAGE, (kb.root / SC_PATH).read_bytes())
    finding_stage = kb.write(".kblam/staging/F-0001-ratio.md", m.finding_text("F-0001", m.CLAIM))
    set_root(kb)
    argv = [str(path) if arg == "SC_STAGE" else str(finding_stage) if arg == "FINDING_STAGE" else arg
            for arg in argv]
    if argv[0] == "init":
        subprocess.run(["git", "init", "-q", str(kb.root)], check=True, capture_output=True)
        monkeypatch.chdir(kb.root)
        expected_out = (f"kblam init: {kb.root.resolve()}\n"
                        "  kept      kblam.toml (init never overwrites it)\n")
        command = "init"
    else:
        expected_out = ""
        command = " ".join(argv[:2]) if argv[0] in {"review", "challenge"} else argv[0]
    pointer = f" {SKILL_POINTER}" if argv[0] == "put" else ""
    permitted = ({".kblam/pairs.sqlite"} if name == "finding-put" else
                 {".kblam/pairs.sqlite", ".kblam/review.jsonl"}
                 if name in {"record", "forget-missing"} else set())
    call(kb, source_repo, argv, changed=permitted, code=1, out=expected_out,
         err=f"kblam {command}: {ROOT_MESSAGE}{pointer}\n")
    validate(kb, source_repo, code=2, diagnostics=f"K13 kblam.toml: {ROOT_MESSAGE}\n" + MISSING_MESSAGE)


@pytest.mark.parametrize("hash_format", ["format-1", "missing"])
def test_legacy_or_missing_hash_reports_ids_missing_from_changed_root(kb, source_repo, hash_format):
    """Acceptance 7: open registered SC-0001 on a clean pinned source is installed through
    challenge new --by reviewer-a (exit 0/path; .kblam/review-staging/SC-0001.yaml and
    .kblam/review-receipts/SC-0001.json) and put (exit 0/destination;
    research-review/challenges/SC-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash and staged removal). Then format-1 or absent tree.hash stores no root and
    kblam.toml is hand-edited to research-notes. validate (no actor) exits 1, reports only K13
    SC-0001 missing from research-notes, and changes none; repeating it has the same outcome.
    Source unchanged per call; the old record and registry remain, no recategorisation occurs.
    """
    path = new_challenge(kb, source_repo)
    put_challenge(kb, source_repo, path)
    if hash_format == "format-1":
        kb.write(TREE_HASH, treehash.tree_digest(load_view(kb.cfg)) + "\n")
    else:
        (kb.root / TREE_HASH).unlink()
    set_root(kb)
    validate(kb, source_repo, code=1, diagnostics=MISSING_MESSAGE)
    validate(kb, source_repo, code=1, diagnostics=MISSING_MESSAGE)
    assert (kb.root / SC_PATH).is_file()
    assert m.registry(kb) == ["SC-0001"]


@pytest.mark.parametrize("hash_format", ["format-2", "format-1", "missing"])
def test_root_without_records_is_accepted_by_validate_record(kb, source_repo, hash_format):
    """Acceptance 7: no records or registered IDs, no findings, and a clean nested source.
    After kblam.toml's root changes to research-notes with format-2, format-1 or no tree.hash,
    validate (no actor) exits 0/OK (0 findings), changes none. validate --record (no actor)
    exits 0/OK with recorded .kblam/tree.hash for this tree; changes exactly .kblam/tree.hash,
    .kblam/pairs.sqlite and .kblam/review.jsonl (empty Jev check state). With a missing marker,
    the automatic findings-only baseline instead omits review.jsonl and announces the accepted
    repository findings. The format-2 line records research-notes. Final validate exits 0/OK and changes none.
    No registry or record is created; source unchanged per call. No unvalidated change is accepted.
    """
    if hash_format == "format-1":
        kb.write(TREE_HASH, treehash.tree_digest(load_view(kb.cfg)) + "\n")
    elif hash_format == "missing":
        (kb.root / TREE_HASH).unlink()
    set_root(kb)
    validate(kb, source_repo)
    changed = {TREE_HASH, ".kblam/pairs.sqlite", ".kblam/review.jsonl"}
    out = "kblam validate: OK (0 findings); recorded .kblam/tree.hash for this tree\n"
    if hash_format == "missing":
        changed.remove(".kblam/review.jsonl")
        out += ("kblam validate: there was no .kblam/tree.hash (a new clone, or .kblam/ was deleted), so "
                "Jev was not asked: 0 finding(s) accepted from the repository as checked at their current "
                "fingerprints. kblam audit checks them with Jev\n")
    call(kb, source_repo, ["validate", "--record"], changed=changed, out=out)
    assert treehash.read_recorded(kb.cfg) == (2, "research-notes", treehash.tree_digest_v2(load_view(kb.cfg)))
    assert m.registry(kb) is None
    validate(kb, source_repo)


def test_root_without_records_is_accepted_by_init_update(fresh_repo):
    """Acceptance 7: fresh Git KB with no findings, records or registered IDs and a clean nested
    source. init (no actor) exits 0/created, changes kblam.toml, findings/INDEX.md,
    research-review/INDEX.md, .gitattributes, .gitignore, .claude/rules/kblam-findings.md,
    .claude/skills/kblam-write/SKILL.md, .claude/settings.json, CLAUDE.md,
    .git/hooks/pre-commit and .kblam/tree.hash. After a hand edit of kblam.toml to
    research-notes, validate exits 0/OK (0 findings), changes none. init --update exits 0,
    reports the new root recorded, new review index, attributes and refreshed root-specific
    assets; changes exactly research-notes/INDEX.md, .gitattributes,
    .claude/rules/kblam-findings.md, .claude/skills/kblam-write/SKILL.md and
    .kblam/tree.hash. Old research-review/INDEX.md is preserved.
    Final validate exits 0/OK, changes none. Repeating init --update exits 0/all unchanged
    except kept config, changes none; validate is again clean/read-only. No actors where
    commands have no --by; source unchanged per call. Neither root holds a record.
    """
    kb, source_repo = fresh_repo
    initialize(kb, source_repo)
    old_index = (kb.root / REVIEW_INDEX).read_bytes()
    set_root(kb)
    validate(kb, source_repo)
    call(kb, source_repo, ["init", "--update"], changed={
        "research-notes/INDEX.md", ".gitattributes", ".claude/rules/kblam-findings.md",
        ".claude/skills/kblam-write/SKILL.md", TREE_HASH,
    }, out=init_output(kb, config="kept", review_action="created", review_root="research-notes",
                       root_changed=True, hash_action="updated",
                       hash_note="review root research-review -> research-notes recorded"))
    assert (kb.root / REVIEW_INDEX).read_bytes() == old_index
    assert treehash.read_recorded(kb.cfg) == (2, "research-notes", treehash.tree_digest_v2(load_view(kb.cfg)))
    assert m.registry(kb) is None
    validate(kb, source_repo)
    call(kb, source_repo, ["init", "--update"],
         out=init_output(kb, config="kept", review_root="research-notes"))
    validate(kb, source_repo)


def test_fresh_init_writes_both_indexes_and_update_is_idempotent(fresh_repo):
    """Acceptance 7: fresh outer Git repo, no KB config, findings, records or registry, clean
    committed nested source. init (no actor) exits 0, reports created config, both indexes,
    attributes, ignore, rule, skill, settings, guidance, pre-commit and format-2 tree.hash; changes
    exactly kblam.toml, findings/INDEX.md, research-review/INDEX.md, .gitattributes, .gitignore,
    .claude/rules/kblam-findings.md, .claude/skills/kblam-write/SKILL.md, .claude/settings.json,
    CLAUDE.md, .git/hooks/pre-commit and .kblam/tree.hash. Both subsequent init --update calls
    exit 0, report unchanged for every item, change none. validate after init and after each
    update exits 0/OK (0 findings), changes none. The source is unchanged per call. Hook/PATH
    environmental checking alone is stubbed; every installed asset and both indexes are real.
    """
    kb, source_repo = fresh_repo
    initialize(kb, source_repo)
    assert (kb.root / "findings/INDEX.md").is_file()
    assert (kb.root / REVIEW_INDEX).read_bytes() == generate_review_index(load_view(kb.cfg))
    assert (kb.root / ".gitattributes").read_bytes() == (
        b"findings/** -text\nkblam.resolutions.jsonl merge=union\nresearch-review/** -text\n")
    assert treehash.read_recorded(kb.cfg) == (2, "research-review", treehash.tree_digest_v2(load_view(kb.cfg)))
    assert m.registry(kb) is None
    validate(kb, source_repo)
    for _ in range(2):
        call(kb, source_repo, ["init", "--update"], out=init_output(kb))
        validate(kb, source_repo)


@pytest.mark.parametrize("hand_edited", [False, True], ids=["generated", "hand-edited"])
def test_update_preserves_a_populated_review_index(fresh_repo, hand_edited):
    """Acceptance 7: start fresh with a clean nested source; init exits 0/created and changes
    kblam.toml, findings/INDEX.md, research-review/INDEX.md, .gitattributes, .gitignore,
    .claude/rules/kblam-findings.md, .claude/skills/kblam-write/SKILL.md, .claude/settings.json,
    CLAUDE.md, .git/hooks/pre-commit, .kblam/tree.hash. challenge new --by reviewer-a exits
    0/path and changes .kblam/review-staging/SC-0001.yaml, .kblam/review-receipts/SC-0001.json;
    put exits 0/destination and changes research-review/challenges/SC-0001.yaml,
    research-review/INDEX.md, .kblam/review-ids, .kblam/tree.hash and removes the staged copy.
    With the populated index generated or hand-edited, two init --update calls (no actor) exit
    0, say INDEX.md exists; not regenerated, change none. A hand edit also keeps tree.hash
    with the changed-outside-kblam note. validate after each update changes none: exit 0/OK
    for the generated index, exit 1/K13 INDEX.md differs for the hand edit. Source unchanged.
    """
    kb, source_repo = fresh_repo
    initialize(kb, source_repo)
    put_challenge(kb, source_repo, new_challenge(kb, source_repo))
    if hand_edited:
        kb.write(REVIEW_INDEX, "# A populated handwritten review index\n")
    populated = (kb.root / REVIEW_INDEX).read_bytes()
    for _ in range(2):
        call(kb, source_repo, ["init", "--update"], out=init_output(
            kb, hash_action="kept" if hand_edited else "unchanged",
            hash_note="findings/ or research-review/ changed outside kblam; run kblam validate --record"
            if hand_edited else ""))
        assert (kb.root / REVIEW_INDEX).read_bytes() == populated
        validate(kb, source_repo, code=int(hand_edited), diagnostics=(
            "K13 research-review/INDEX.md: INDEX.md differs from the generated review index; it is "
            "never edited by hand. Run kblam review index to regenerate it\n" if hand_edited else ""))


def legacy_hash(kb, digest=None):
    data = (digest or treehash.tree_digest(load_view(kb.cfg))).encode("ascii") + b"\n"
    kb.write(TREE_HASH, data)
    return data


def old_jev_state(kb):
    items = [ReviewItem("R-1a2b3c4d", "review", "open", "F-0001", "0badf00d0000", "F-0002", "0badf00d0000",
                        "same_fact", "new", 0.9, 0.9, None, "the two state one fact", "2026-09-28T00:00:00Z"),
             ReviewItem("U-5e6f7a8b", "unchecked", "open", "F-0001", "0badf00d0000", message="Jev was away",
                        created="2026-09-28T00:00:00Z")]
    kb.write(".kblam/review.jsonl", "".join(json.dumps(asdict(item), sort_keys=True) + "\n" for item in items))
    kb.write(".kblam/pairs.sqlite", b"pair cache bytes")
    kb.write(".kblam/calls.jsonl", b'{"status": "cache_hit"}\n')


def test_matching_clean_format_1_migrates_without_rewriting_findings_or_jev_state(fresh_repo):
    """Acceptance 7: init from a fresh repo/clean source exits 0/created, changing kblam.toml,
    findings/INDEX.md, research-review/INDEX.md, .gitattributes, .gitignore,
    .claude/rules/kblam-findings.md, .claude/skills/kblam-write/SKILL.md, .claude/settings.json,
    CLAUDE.md, .git/hooks/pre-commit, .kblam/tree.hash. Start the upgrade with no records, one
    valid finding (including a K10 quote), unchanged source, stale open R-/U- items and Jev caches,
    no review root/index yet, and a matching format-1 digest. validate (no actor) exits 0/OK
    (1 findings), changes none. init --update exits 0, says created research-review/INDEX.md
    and updated .kblam/tree.hash (migrated to format 2), changes exactly research-review/INDEX.md
    and .kblam/tree.hash. A repeated init --update exits 0/all unchanged, changes none. validate
    after both calls exits 0/OK (1 findings), changes none. Finding text/citations, items and
    Jev cache/log bytes remain exact; no recategorisation, no source writes, no state loss.
    """
    kb, source_repo = fresh_repo
    initialize(kb, source_repo)
    kb.write("evidence/2026-09-22-ratio/README.md", "manifest\n")
    finding = m.quoting_finding(kb, "F-0001", source_repo, "2-2", scope="[any]")
    old_jev_state(kb)
    (kb.root / REVIEW_INDEX).unlink()
    (kb.root / "research-review").rmdir()  # a pre-feature KB has no review root yet
    legacy_hash(kb)
    preserved = {rel: (kb.root / rel).read_bytes() for rel in (
        finding.relative_to(kb.root).as_posix(), ".kblam/review.jsonl", ".kblam/pairs.sqlite",
        ".kblam/calls.jsonl")}
    validate(kb, source_repo, findings=1)
    call(kb, source_repo, ["init", "--update"], changed={REVIEW_INDEX, TREE_HASH},
         out=init_output(kb, review_action="created", hash_action="updated", hash_note="migrated to format 2"))
    assert treehash.read_recorded(kb.cfg) == (2, "research-review", treehash.tree_digest_v2(load_view(kb.cfg)))
    validate(kb, source_repo, findings=1)
    call(kb, source_repo, ["init", "--update"], out=init_output(kb))
    validate(kb, source_repo, findings=1)
    assert {rel: (kb.root / rel).read_bytes() for rel in preserved} == preserved


@pytest.mark.parametrize("blocker", ["unmatched", "invalid", "open-item"])
def test_format_1_is_kept_until_tree_matches_and_full_validation_is_clean(fresh_repo, blocker):
    """Acceptance 7: fresh init (no actor), exit 0/created, changes kblam.toml,
    findings/INDEX.md, research-review/INDEX.md, .gitattributes, .gitignore,
    .claude/rules/kblam-findings.md, .claude/skills/kblam-write/SKILL.md, .claude/settings.json,
    CLAUDE.md, .git/hooks/pre-commit and .kblam/tree.hash. Upgrade starts with no records,
    clean committed nested source, and format-1 tree.hash. Its blocker is either an unmatched
    digest on an otherwise clean tree, a matching digest over a K8 stray, or a matching clean
    findings tree with an open current R- item. init --update twice (no actor) exits 0, says
    kept .kblam/tree.hash (run kblam validate --record), changes none. validate after each call
    changes none: unmatched exits 0/OK (0 findings); invalid exits 1/K8 stray; open-item exits
    1/review R-1a2b3c4d and 0 errors, 1 open item. The old hash/source/state are unchanged.
    """
    kb, source_repo = fresh_repo
    initialize(kb, source_repo)
    if blocker == "invalid":
        kb.write("findings/notes.md", "scratch\n")
    elif blocker == "open-item":
        kb.write("evidence/2026-09-22-ratio/README.md", "manifest\n")
        kb.add("F-0001", "ratio", m.CLAIM, scope="[any]")
        fp = current_fingerprints(load_view(kb.cfg))["F-0001"]
        item = ReviewItem("R-1a2b3c4d", "review", "open", "F-0001", fp, verdict="same_fact",
                          winner="new", p=0.9, confidence=0.9, message="Independent review is pending",
                          created="2026-09-28T00:00:00Z")
        kb.write(".kblam/review.jsonl", json.dumps(asdict(item), sort_keys=True) + "\n")
    legacy = legacy_hash(kb, ZERO64 if blocker == "unmatched" else None)
    for _ in range(2):
        call(kb, source_repo, ["init", "--update"],
             out=init_output(kb, hash_action="kept", hash_note="run kblam validate --record"))
        assert (kb.root / TREE_HASH).read_bytes() == legacy
        if blocker == "unmatched":
            validate(kb, source_repo)
        elif blocker == "invalid":
            validate(kb, source_repo, code=1, diagnostics=(
                "K8 findings/notes.md: findings/ holds only findings (F-NNNN-<slug>.md in a topic folder) "
                "and the generated INDEX.md. Record each fact here as a finding via kblam new and kblam "
                "put, then delete this file\n"))
        else:
            call(kb, source_repo, ["validate"], code=1, out=(
                'review R-1a2b3c4d same_fact F-0001 (p 0.90, confidence 0.90): Independent review is '
                'pending. Edit a finding, or if they are distinct: kblam resolve R-1a2b3c4d --distinct '
                '"<reason>"\n'
                "kblam validate: 0 error(s) in findings/, 1 open item(s) in .kblam/review.jsonl\n"))


def test_validate_record_explicitly_migrates_a_clean_legacy_hash(kb, source_repo):
    """Acceptance 7: start with no findings, clean source and open registered SC-0001 installed
    through challenge new --by reviewer-a (exit 0/path; .kblam/review-staging/SC-0001.yaml,
    .kblam/review-receipts/SC-0001.json) and put (exit 0/destination;
    research-review/challenges/SC-0001.yaml, research-review/INDEX.md, .kblam/review-ids,
    .kblam/tree.hash and staged removal). Then set a format-1 digest not matching the tree.
    validate --record (no actor) exits 0/OK, recorded .kblam/tree.hash; changes
    .kblam/tree.hash to format 2 plus .kblam/pairs.sqlite and .kblam/review.jsonl (empty Jev
    check state) after validation. Final validate exits 0/OK (0 findings),
    changes none. It is an explicit clean-tree acceptance, not a silent upgrade; source unchanged.
    """
    put_challenge(kb, source_repo, new_challenge(kb, source_repo))
    legacy_hash(kb, ZERO64)
    call(kb, source_repo, ["validate", "--record"],
         changed={TREE_HASH, ".kblam/pairs.sqlite", ".kblam/review.jsonl"},
         out="kblam validate: OK (0 findings); recorded .kblam/tree.hash for this tree\n")
    assert treehash.read_recorded(kb.cfg) == (2, "research-review", treehash.tree_digest_v2(load_view(kb.cfg)))
    validate(kb, source_repo)


def test_missing_hash_with_records_is_not_bootstrapped_by_writes(fresh_repo):
    """Acceptance 7: fresh init (no actor), exit 0/created, changes kblam.toml,
    findings/INDEX.md, research-review/INDEX.md, .gitattributes, .gitignore,
    .claude/rules/kblam-findings.md, .claude/skills/kblam-write/SKILL.md, .claude/settings.json,
    CLAUDE.md, .git/hooks/pre-commit, .kblam/tree.hash. challenge new --by reviewer-a exits
    0/path, changes .kblam/review-staging/SC-0001.yaml, .kblam/review-receipts/SC-0001.json;
    put exits 0/destination, changes research-review/challenges/SC-0001.yaml,
    research-review/INDEX.md, .kblam/review-ids, .kblam/tree.hash and staged removal. With
    open SC-0001/current source and tree.hash removed, init --update exits 0/kept missing
    tree.hash with records; changes none. index and review index exit 0/wrote index and warn
    no .kblam/tree.hash, review root holds records; neither changes bytes. challenge new
    --by reviewer-a exits 0/path, changes .kblam/review-staging/SC-0002.yaml,
    .kblam/review-receipts/SC-0002.json; put exits 0/destination and warns missing hash,
    changes research-review/challenges/SC-0002.yaml, research-review/INDEX.md,
    .kblam/review-ids and staged removal, but not tree.hash. validate after init --update,
    each index command, SC-0002's put and validate --record exits 0/OK (0 findings), changes
    none. validate --record exits 0/OK recorded,
    changes .kblam/tree.hash, .kblam/pairs.sqlite and .kblam/review.jsonl (empty Jev check
    state); final validate is clean/read-only. Actors omitted where
    commands have no --by. Source unchanged per call; only explicit validation accepts the tree.
    """
    kb, source_repo = fresh_repo
    initialize(kb, source_repo)
    put_challenge(kb, source_repo, new_challenge(kb, source_repo))
    (kb.root / TREE_HASH).unlink()
    call(kb, source_repo, ["init", "--update"], out=init_output(
        kb, hash_action="kept",
        hash_note="missing, and the review root holds records; run kblam validate --record"))
    validate(kb, source_repo)
    for argv, command, out in (
        (["index"], "index", "kblam index: wrote findings/INDEX.md (0 findings)\n"),
        (["review", "index"], "review index", "kblam review index: wrote research-review/INDEX.md\n"),
    ):
        call(kb, source_repo, argv, out=out, err=(
            f"kblam {command}: no .kblam/tree.hash, and the review root holds records; tree.hash not "
            "advanced. Run kblam validate --record once the tree validates.\n"))
        assert not (kb.root / TREE_HASH).exists()
        validate(kb, source_repo)
    path = new_challenge(kb, source_repo, "SC-0002")
    put_challenge(kb, source_repo, path, "SC-0002", missing_hash=True)
    assert not (kb.root / TREE_HASH).exists()
    validate(kb, source_repo)
    call(kb, source_repo, ["validate", "--record"],
         changed={TREE_HASH, ".kblam/pairs.sqlite", ".kblam/review.jsonl"},
         out="kblam validate: OK (0 findings); recorded .kblam/tree.hash for this tree\n")
    assert treehash.read_recorded(kb.cfg) == (2, "research-review", treehash.tree_digest_v2(load_view(kb.cfg)))
    validate(kb, source_repo)
