"""SPEC §12 M6.10 test group 12, source safety (A1), end to end through the CLI.

Every test drives `kblam.cli.main` in process, as the console entry point runs it, and asserts the exit
status, the exact diagnostics, which files changed and the validation result afterwards. Two trees are
watched around every command:

- the nested Git source repository (`SourceRepo.snapshot()`: working bytes, HEAD, symbolic HEAD, index,
  refs and packed-refs, so staging is covered too). Equality before and after is what "the source
  repository is unchanged" means (Acceptance 1);
- the whole KB (`_kb_snapshot()`: every file under the KB root except the nested source repository and
  its Git state, symlinks recorded by their target and never followed). A refusal or a read-only
  command must leave it identical; a write must change exactly the paths the test names.

Offline and deterministic: `_today` is pinned and no fixture enables a Jev verdict.

The canonical key of SPEC §5.1.2 is what every spelling of one path must give: `\\`, `./`, a doubled
`/`, case on Windows and a symlink alias all key to the one source, so K13 (Acceptance 2) applies to
each spelling. A path that traverses, is drive-relative, is a UNC or alternate-data-stream path, or
resolves out of the repository or into a protected root, is refused instead — by the command that
takes it, by K12 in a record, or by K10 in a verbatim tag.
"""

from __future__ import annotations

import datetime
import hashlib
import os
from pathlib import Path

import pytest

from conftest import SOURCE_REPO, TRACE_PATH, TRACE_TEXT, dump_record, record_data
from kblam import records, review_stage, review_write
from kblam.cli import main
from kblam.decisions import subject_digest
from kblam.finding import fingerprint, yaml_rt
from kblam.hook import SKILL_POINTER
from kblam.review_index import generate_review_index
from kblam.sources import sha256_hex
from kblam.treehash import write_tree_hash_v2
from kblam.view import load_view

TODAY = datetime.date(2026, 9, 28)
REVIEW = "research-review"
STATE = ".kblam"
STAGING = f"{STATE}/review-staging"
RECEIPTS = f"{STATE}/review-receipts"
CHALLENGES = f"{REVIEW}/challenges"
TASKS = f"{REVIEW}/tasks"
USES = f"{REVIEW}/uses"
INDEX = f"{REVIEW}/INDEX.md"
TREE_HASH = f"{STATE}/tree.hash"
REGISTRY = f"{STATE}/review-ids"
FINDING_PATH = "findings/calibration/F-0001-ratio.md"
TRACE = f"{SOURCE_REPO}/{TRACE_PATH}"
LINE3 = "Row 102: bytes 0x3A 0x3B; the two bytes are equal."       # TRACE_TEXT line 3
CLAIM = "The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0)."
DECIDER = "reviewer-b"


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """`created` and a decision's `date` are today's: pin them, so the bytes kblam writes are fixed."""
    monkeypatch.setattr(review_stage, "_today", lambda: TODAY)
    monkeypatch.setattr(review_write, "_today", lambda: TODAY)
    return TODAY


# --- helpers (modelled on tests/test_cli_review.py) ------------------------------------------------


def _run(kb, *argv: str) -> int:
    """`kblam --root <kb> <argv>` in process, as the console entry point runs it."""
    return main(["--root", str(kb.root), *argv])


def _kb_snapshot(kb) -> dict:
    """Every file of the KB, as {repo-relative path: bytes}, plus {path: "@symlink -> <target>"} for a
    link. Links are never followed, so one into or out of the repository can neither loop nor escape.
    The nested source repository and every `.git` are left out: SourceRepo.snapshot covers those."""
    root, source = kb.root, (kb.root / SOURCE_REPO).resolve()
    found: dict[str, object] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        keep = []
        for name in dirnames:
            path = here / name
            if path.is_symlink():
                found[path.relative_to(root).as_posix()] = f"@symlink -> {os.readlink(path)}"
            elif name != ".git" and path.resolve() != source:
                keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            path = here / name
            found[path.relative_to(root).as_posix()] = (f"@symlink -> {os.readlink(path)}"
                                                        if path.is_symlink() else path.read_bytes())
    return found


def _run_kb(source_repo, kb, *argv: str) -> tuple[int, dict]:
    """Run a feature command with both trees watched: the source repository must come out unchanged
    (Acceptance 1), and the KB paths whose bytes it changed are returned as {path: new bytes, or None
    for a file it removed}."""
    source_before = source_repo.snapshot()
    before = _kb_snapshot(kb)
    code = _run(kb, *argv)
    assert source_repo.snapshot() == source_before, \
        f"the source repository changed under: kblam {' '.join(argv)}"
    after = _kb_snapshot(kb)
    changed = {path: after.get(path) for path in before if before[path] != after.get(path)}
    changed.update({path: after[path] for path in after if path not in before})
    return code, changed


def _untouched(source_repo, kb, *argv: str) -> int:
    """A refusal or a read-only command: its exit status, with both trees asserted unchanged."""
    code, changed = _run_kb(source_repo, kb, *argv)
    assert changed == {}, f"kblam {' '.join(argv)} changed {sorted(changed)}"
    return code


def _written(source_repo, kb, *argv: str) -> tuple[int, dict]:
    """A command that writes: its exit status and the KB paths it changed, for the caller to name
    exactly."""
    return _run_kb(source_repo, kb, *argv)


def _staged(source_repo, kb, capsys, *argv: str) -> tuple[Path, dict]:
    """A staging command's success (exit 0, the staged path printed): that path and what it changed."""
    code, changed = _written(source_repo, kb, *argv)
    assert code == 0
    return Path(capsys.readouterr().out.strip()), changed


def _validate_clean(source_repo, kb, capsys, expected: list[str]) -> None:
    """`kblam validate` afterwards: exit 0, its exact output, and no KB change of its own."""
    assert _untouched(source_repo, kb, "validate") == 0
    assert capsys.readouterr().out.splitlines() == expected


def _sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _link(target: Path, link: Path, *, directory: bool = False) -> Path:
    """A symlink at `link`; a skip where the OS refuses to make one (Windows without the privilege)."""
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    return link


def _sc(repo, *, status: str = "confirmed", path: str | None = None, pin: bool = True,
        rel: str = TRACE_PATH, text: str = LINE3, lines: tuple[int, int] = (3, 3),
        rec_id: str = "SC-0001") -> dict:
    """A challenge on the source's working bytes as they are now, pinned at HEAD unless `pin` is false.
    `path` overrides the source path as the record spells it. A status other than open gets its one
    decision, bound to the record as it then stands."""
    data = record_data("SC", rec_id, status=status)
    digest = _sha((repo.root / rel).read_bytes())
    data["source"].update(path=path or repo.kb_path(rel), sha256=digest)
    if pin:
        data["source"].update(repo=SOURCE_REPO, commit=repo.head(), blob=repo.blob(rel))
    data["source"]["assertion"] = {"lines": list(lines), "text": text, "sha256": _sha(text),
                                   "occurrence": 1}
    data["basis"][0].update(path=repo.kb_path(rel), sha256=digest)
    if status != "open":
        data["decisions"] = [{"date": TODAY, "by": DECIDER, "status": status, "evidence": [],
                              "reason": "read the source at its pin", "bind": None}]
        data["decisions"][0]["bind"] = subject_digest("SC", data)
    return data


def _ct(kb, *, rec_id: str = "CT-0001") -> dict:
    """An open task bound to F-0001's current fingerprint and file bytes, as `task new` writes it."""
    finding = next(f for f in load_view(kb.cfg).findings if f.file_id == "F-0001")
    return record_data("CT", rec_id, status="open", finding="F-0001",
                       claim_fingerprint=fingerprint(finding),
                       base_file_sha256=sha256_hex(finding.raw))


def _install(kb, kind: str, data: dict) -> None:
    """Place a record in the review root directly (fixture setup only)."""
    kb.write(f"{REVIEW}/{records.KINDS[kind]}/{data['id']}.yaml", dump_record(data))


def _finding(kb, tag: str, excerpt: str = LINE3, *, fid: str = "F-0001") -> None:
    """F-0001 with one verbatim tag and its blockquote, as its author writes it."""
    block = "\n".join("> " + line for line in excerpt.split("\n"))
    kb.add(fid, "ratio", CLAIM, body=f"Detail follows.\n\n<!-- verbatim: {tag} -->\n{block}")


def _finding_path(kb, fid: str = "F-0001") -> Path:
    return next(iter(sorted((kb.root / "findings").rglob(f"{fid}-*.md"))))


def _tag_line(kb, tag: str) -> int:
    """The file line of a verbatim tag: the line K10, K13 and `challenge uses` report the excerpt at."""
    lines = _finding_path(kb).read_text(encoding="utf-8").splitlines()
    return next(i for i, line in enumerate(lines, 1)
                if line.strip() == f"<!-- verbatim: {tag} -->")


def _key_line(kb, rel: str, key: str) -> int:
    """The line of a top-level key in a record file, as records.key_line reports it."""
    lines = (kb.root / rel).read_text(encoding="utf-8").splitlines()
    return next(i for i, line in enumerate(lines, 1) if line.startswith(f"{key}:"))


def _accept(kb, capsys) -> None:
    """Fixture setup: write the review index from the records present, then accept the whole tree, as
    `kblam review index` and `kblam validate --record` would. The fixture's own out-of-band writes are
    not the command under test, so their diagnostics are dropped."""
    view = load_view(kb.cfg)
    if view.records:
        kb.write(INDEX, generate_review_index(view))
    write_tree_hash_v2(kb.cfg, load_view(kb.cfg))
    capsys.readouterr()


def _digest(kb, rec_id: str) -> str:
    """The installed record's subject digest, as show, list and --expect take it."""
    rec = next(r for r in load_view(kb.cfg).records if r.id == rec_id)
    return subject_digest(rec.kind, rec.data)


def _record(kb, rec_id: str):
    return next(r for r in load_view(kb.cfg).records if r.id == rec_id)


def _fill(path: Path, **fields) -> None:
    """Fill a staged record's blank fields, as its author does, and write it back with LF endings."""
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    data.update(fields)
    path.write_bytes(records.dump(data))


def _rewrite(path: Path, dotted: str, value) -> None:
    """Hand-edit a staged record's nested field as an editor would, past the lock (SPEC §5.1.6);
    `dotted` is "key.subkey" or "key[i].subkey"."""
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    head, _, tail = dotted.partition(".")
    name, _, index = head.partition("[")
    field = data[name][int(index[:-1])] if index else data[name]
    field[tail] = value
    path.write_bytes(records.dump(data))


def _sc_fields(source_repo) -> dict:
    """The blank SC fields the author fills before the first put (SPEC §5.1.5)."""
    return {"proposition": "The printed byte equality follows from the printed byte values",
            "scope": ["MX-100 capture transcription"],
            "classification": "contradicted",
            "basis": [{"path": source_repo.kb_path(), "sha256": None, "repo": None, "commit": None,
                       "blob": None, "snapshot": None, "locator": "row 102: printed byte values",
                       "role": "internal-inconsistency", "provenance": "observed"}],
            "usable": "The printed byte values may be cited as a report.",
            "limits": "Do not infer the capture bytes from this row."}


def _cu_fields() -> dict:
    """The blank CU fields the author fills before the first put."""
    return {"disposition": "unaffected_raw_bytes",
            "reason": "The excerpt is cited only for the printed byte values."}


def _ct_fields() -> dict:
    """The blank CT fields the author fills before the first put."""
    return {"question": "Does an independent measurement establish the claim?",
            "method": "Repeat the capture with the documented settings.",
            "outcomes": {"supports": "The ratio is within 0.1%.", "refutes": "The ratio differs by more.",
                         "inconclusive": "The capture is too noisy to tell."},
            "controls": ["same firmware version"],
            "stop": "Stop after three captures.",
            "expected_evidence": ["an evidence/ capture package"]}


def _k13_same(repo, *, source: str = TRACE, lines: str = "3-3", ordinal: int = 1) -> str:
    """The K13 same-bytes diagnostic (SPEC §5.1.4) for excerpt `ordinal` of F-0001 against SC-0001."""
    version = repo.blob(TRACE_PATH)[:12]
    return (f"SC-0001 challenges this quoted assertion at {source}@{version}:{lines}; edit the finding "
            f"or have this use reviewed (kblam use review SC-0001 F-0001 {ordinal} --by NAME --proponent "
            f"NAME). K10 is checked separately.")


# --- traversal, drive-relative, UNC and stream paths ----------------------------------------------

# Every refused spelling and the reason paths.syntax_problem gives it (SPEC §5.1.2 Paths).
BAD_PATHS = [
    ("/etc/passwd", "absolute path"),
    ("../outside.md", "'..' segment"),
    ("resources/mx-docs/../../outside.md", "'..' segment"),
    ("C:/x.md", "drive letter"),
    ("C:x.md", "drive-relative path"),
    ("\\\\host\\share\\x.md", "UNC path"),
    ("//host/share/x.md", "UNC path"),
    (f"{TRACE}:stream", "':' (an alternate data stream)"),
    ("notes:x.md", "':' (an alternate data stream)"),
]


@pytest.mark.parametrize("spelled, reason", BAD_PATHS)
def test_challenge_new_refuses_a_traversal_drive_relative_unc_or_stream_path(kb, source_repo, capsys,
                                                                             spelled, reason):
    """Start: no records and no staging; the nested source repository at HEAD. Command: kblam challenge
    new <spelled> --lines 3-3 --by reviewer-a. Exit 1; stderr "<spelled> is not a repository-relative
    path: <reason>". Files changed: none — neither tree moves (.kblam/review-staging/ is not even
    created). validate afterwards exits 0 with "kblam validate: OK (0 findings)". Acceptance 1."""
    assert _untouched(source_repo, kb, "challenge", "new", spelled, "--lines", "3-3",
                      "--by", "reviewer-a") == 1
    assert capsys.readouterr().err == \
        (f"kblam challenge new: {spelled!r} is not a repository-relative path: {reason}\n")
    assert not (kb.root / STAGING).exists()
    assert not (kb.root / RECEIPTS).exists()
    _validate_clean(source_repo, kb, capsys, ["kblam validate: OK (0 findings)"])


def test_challenge_new_refuses_a_symlink_that_escapes_the_repository(kb, source_repo, capsys, tmp_path):
    """Start: escape.md is a symlink to a file beside the KB root, up/ a directory symlink to the KB
    root's parent. Command: kblam challenge new <spelled> --lines 1-1 --by reviewer-a, for both. Exit 1
    each; stderr "cannot be challenged: resolves outside the repository". Files changed: none in either
    tree; validate afterwards exits 0, clean. Acceptance 1: a source is never read through a link out
    of the repository."""
    outside = tmp_path / "outside.md"
    outside.write_text("outside the KB\n", encoding="utf-8")
    _link(outside, kb.root / "escape.md")
    _link(Path(".."), kb.root / "up", directory=True)

    for spelled in ("escape.md", "up/outside.md"):
        assert _untouched(source_repo, kb, "challenge", "new", spelled, "--lines", "1-1",
                          "--by", "reviewer-a") == 1
        assert capsys.readouterr().err == \
            (f"kblam challenge new: {spelled!r} cannot be challenged: resolves outside the "
             f"repository\n")
    assert not (kb.root / STAGING).exists()
    _validate_clean(source_repo, kb, capsys, ["kblam validate: OK (0 findings)"])


@pytest.mark.parametrize("alias, target, spelled, root", [
    ("findings-link", "findings", "findings-link/F-0001-ratio.md", "findings/"),
    ("state-link", ".kblam", "state-link/review-ids", ".kblam/"),
])
def test_challenge_new_refuses_a_symlink_into_a_protected_root(kb, source_repo, capsys, alias, target,
                                                              spelled, root):
    """Start: F-0001 exists; <alias> is a symlink to <target>. Command: kblam challenge new <spelled>
    --lines 1-1 --by reviewer-a. Exit 1; stderr "<spelled> lies under <root>, which is never a source;
    challenge the document the claim came from". Files changed: none in either tree; validate
    afterwards exits 0 (F-0001 alone is clean). Acceptance 1. §5.1.2: the exclusion tests the resolved
    target, so a symlink in does not escape it."""
    _finding(kb, f"{TRACE}:3")
    _accept(kb, capsys)                                       # F-0001 is fixture setup, not the command
    (kb.root / STATE).mkdir(exist_ok=True)
    _link(kb.root / target, kb.root / alias, directory=True)

    assert _untouched(source_repo, kb, "challenge", "new", spelled, "--lines", "1-1",
                      "--by", "reviewer-a") == 1
    assert capsys.readouterr().err == \
        (f"kblam challenge new: {spelled} lies under {root}, which is never a source; challenge the "
         f"document the claim came from\n")
    assert not (kb.root / STAGING).exists()
    _validate_clean(source_repo, kb, capsys, ["kblam validate: OK (1 findings)"])


# --- the same paths in a record: K12, and put's preconditions --------------------------------------


@pytest.mark.parametrize("spelled, reason", [
    ("../../outside.md", "'..' segment"),
    ("C:x.md", "drive-relative path"),
    ("notes:x.md", "':' (an alternate data stream)"),
])
def test_a_record_path_spelling_is_a_k12_error(kb, source_repo, capsys, spelled, reason):
    """Start: SC-0001 confirmed, its source path hand-edited to <spelled> (an editor ignores the lock,
    §5.1.6). Command: kblam validate. Exit 1; "K12 research-review/challenges/SC-0001.yaml:<line>:
    source.path: <reason>", then the error summary. Files changed: none in either tree. Acceptance 1:
    a record path that is not repo-relative is refused, never read."""
    _install(kb, "SC", _sc(source_repo, path=spelled))
    _accept(kb, capsys)

    assert _untouched(source_repo, kb, "validate") == 1
    assert capsys.readouterr().out.splitlines() == [
        f"K12 {CHALLENGES}/SC-0001.yaml:{_key_line(kb, f'{CHALLENGES}/SC-0001.yaml', 'source')}: "
        f"source.path: {reason}",
        "kblam validate: 1 error(s) in findings/",
    ]


def test_a_record_path_through_an_escaping_symlink_is_a_k12_error(kb, source_repo, capsys, tmp_path):
    """Start: escape.md is a symlink out of the repository; SC-0001 confirmed with it as its source
    path (no syntax problem — the escape is only visible once it is resolved). Command: kblam validate.
    Exit 1; "K12 ...SC-0001.yaml:<line>: source: path 'escape.md': resolves outside the repository".
    Files changed: none in either tree. Acceptance 1: the resolver refuses the escape rather than
    reading it."""
    outside = tmp_path / "outside.md"
    outside.write_text("outside the KB\n", encoding="utf-8")
    _link(outside, kb.root / "escape.md")
    _install(kb, "SC", _sc(source_repo, path="escape.md"))
    _accept(kb, capsys)

    assert _untouched(source_repo, kb, "validate") == 1
    assert capsys.readouterr().out.splitlines() == [
        f"K12 {CHALLENGES}/SC-0001.yaml:{_key_line(kb, f'{CHALLENGES}/SC-0001.yaml', 'source')}: "
        f"source: path 'escape.md': resolves outside the repository",
        "kblam validate: 1 error(s) in findings/",
    ]


def test_put_refuses_a_staged_record_whose_basis_path_traverses(kb, source_repo, capsys):
    """Start: SC-0001 is staged and complete except that a basis entry's path (a free field, so the
    allocation receipt does not bind it) was hand-edited to "../../outside.md" after `challenge new`.
    Command: kblam put <staged>. Exit 1; the K12 lines for basis[0] at the path the record would be
    installed at, then "kblam put: rejected SC-0001 (...); research-review/ is unchanged...". Files
    changed: none in either tree — no record, no registry, the staged file stays as the author filled
    it — so validate afterwards exits 0, with no record installed. Acceptance 1."""
    staged, changed = _staged(source_repo, kb, capsys, "challenge", "new", TRACE, "--lines", "3-3",
                              "--by", "reviewer-a")
    assert set(changed) == {f"{STAGING}/SC-0001.yaml", f"{RECEIPTS}/SC-0001.json"}
    _fill(staged, **_sc_fields(source_repo))
    _rewrite(staged, "basis[0].path", "../../outside.md")
    key_line = _key_line(kb, f"{STAGING}/SC-0001.yaml", "basis")   # the same line in either copy
    where = {f"{STAGING}/SC-0001.yaml:{key_line}", f"{CHALLENGES}/SC-0001.yaml:{key_line}"}

    assert _untouched(source_repo, kb, "put", str(staged)) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] in {f"K12 {place}: basis[0].path: '..' segment" for place in where}
    assert lines[1] in {f"K12 {place}: basis[0].sha256: required" for place in where}
    assert lines[2] == \
        (f"kblam put: rejected SC-0001 (2 error(s)); {REVIEW}/ is unchanged. Fix the staged file and "
         f"put it again. {SKILL_POINTER}")
    assert staged.is_file() and not (kb.root / CHALLENGES).exists()
    assert not (kb.root / REGISTRY).exists()
    _validate_clean(source_repo, kb, capsys, ["kblam validate: OK (0 findings)"])


def test_a_verbatim_tag_with_an_unsafe_path_is_a_k10_error(kb, source_repo, capsys):
    """Start: no records; F-0001 quotes line 3 through a tag whose path leaves the repository. Command:
    kblam validate. Exit 1; K10 reports the tag and its reason (K13 never sees the excerpt). Files
    changed: none in either tree. Acceptance 1: an excerpt cannot reach a source outside the
    repository."""
    tag = "../../outside.md:3"
    _finding(kb, tag)
    _accept(kb, capsys)

    assert _untouched(source_repo, kb, "validate") == 1
    assert capsys.readouterr().out.splitlines() == [
        f"K10 {FINDING_PATH}:{_tag_line(kb, tag)}: verbatim source ../../outside.md points outside "
        f"the repository root",
        "kblam validate: 1 error(s) in findings/",
    ]


# --- one canonical key for every spelling ----------------------------------------------------------

# Spellings of the one source path that must key to the one source (SPEC §5.1.2 Paths).
SPELLINGS = [
    TRACE,
    f"./{TRACE}",
    TRACE.replace("/", "\\"),
    TRACE.replace("/", "//"),
]


@pytest.mark.parametrize("spelled", SPELLINGS)
def test_k13_applies_to_every_spelling_of_the_quoted_source(kb, source_repo, capsys, spelled):
    """Start: SC-0001 confirmed on TRACE lines 3-3; F-0001 quotes that line through <spelled>, which the
    tag keeps as written. Command: kblam validate. Exit 1 with the K13 same-bytes error naming SC-0001,
    the source as the challenge writes it and the pinned version. Files changed: none in either tree.
    Acceptance 2 through the canonical key: every spelling reaches the one challenge."""
    _install(kb, "SC", _sc(source_repo))
    tag = f"{spelled}:3"
    _finding(kb, tag)
    _accept(kb, capsys)

    assert _untouched(source_repo, kb, "validate") == 1
    assert capsys.readouterr().out.splitlines() == [
        f"K13 {FINDING_PATH}:{_tag_line(kb, tag)}: {_k13_same(source_repo)}",
        "kblam validate: 1 error(s) in findings/",
    ]


def test_a_symlink_alias_gives_k13_one_canonical_key(kb, source_repo, capsys, tmp_path):
    """Start: SC-0001 confirmed on TRACE; trace-alias.md is a symlink to it in the repository root;
    F-0001 quotes line 3 through the alias. Command: kblam validate, then kblam challenge uses SC-0001.
    Exit 1 with the K13 same-bytes error (the diagnostic names the source as the challenge writes it,
    not as the finding spells it); challenge uses names the excerpt and the command to fix it, exit 0.
    Files changed: none in either tree for both commands. Acceptance 2 through the canonical key."""
    _install(kb, "SC", _sc(source_repo))
    _link(Path(TRACE), kb.root / "trace-alias.md")
    tag = "trace-alias.md:3"
    _finding(kb, tag)
    _accept(kb, capsys)
    line = _tag_line(kb, tag)

    assert _untouched(source_repo, kb, "validate") == 1
    assert capsys.readouterr().out.splitlines() == [
        f"K13 {FINDING_PATH}:{line}: {_k13_same(source_repo)}",
        "kblam validate: 1 error(s) in findings/",
    ]
    assert _untouched(source_repo, kb, "challenge", "uses", "SC-0001") == 0
    assert capsys.readouterr().out == \
        (f"F-0001 {FINDING_PATH}:{line} excerpt 1 same: error; "
         f"kblam use review SC-0001 F-0001 1 --by NAME --proponent NAME\n")


def test_a_case_differing_spelling_is_one_key_on_windows_only(kb, source_repo, capsys):
    """Start: SC-0001 confirmed on TRACE lines 3-3; F-0001 quotes line 3 through the upper-cased path.
    Command: kblam validate. The canonical key is case-folded with os.path.normcase on Windows only
    (§5.1.2), so on Windows the tag keys to the same source and K13 fires with the same-bytes error;
    on a case-sensitive filesystem the two paths are different keys, K13 relates nothing and K10
    reports the missing source. Exit 1 either way (no silent pass); files changed: none in either tree.
    A case-insensitive volume off Windows (macOS, usually) names the real file under the upper-cased
    spelling, so the test skips there rather than assert a rule that platform does not have.
    Acceptance 2 on Windows."""
    _install(kb, "SC", _sc(source_repo))
    tag = f"{TRACE.upper()}:3"
    _finding(kb, tag)
    _accept(kb, capsys)
    line = _tag_line(kb, tag)

    code, changed = _run_kb(source_repo, kb, "validate")
    if os.name != "nt" and (kb.root / TRACE.upper()).is_file():
        pytest.skip("case-insensitive filesystem: case folding is a Windows rule (SPEC 275-276)")
    assert changed == {} and code == 1
    expected = (f"K13 {FINDING_PATH}:{line}: {_k13_same(source_repo)}" if os.name == "nt" else
                f"K10 {FINDING_PATH}:{line}: verbatim source {TRACE.upper()} does not exist; cite an "
                f"existing file relative to the repository root")
    assert capsys.readouterr().out.splitlines() == [expected,
                                                    "kblam validate: 1 error(s) in findings/"]


# --- the other commands that take a path -----------------------------------------------------------


@pytest.mark.parametrize("spelled, message", [
    ("../outside.md", "--snapshot '../outside.md': '..' segment"),
    ("C:x.md", "--snapshot 'C:x.md': drive-relative path"),
    ("escape.md", "--snapshot 'escape.md': resolves outside the repository"),
    ("findings/F-0001-ratio.md",
     "--snapshot 'findings/F-0001-ratio.md': findings/ holds the findings; a snapshot is a "
     "project-owned copy outside every source repository and outside the roots kblam owns"),
])
def test_challenge_pin_refuses_a_snapshot_path_outside_the_repository(kb, source_repo, capsys, tmp_path,
                                                                     spelled, message):
    """Start: SC-0001 is open and provisional — it was installed without a pin, so `challenge pin` is
    the command that could set one; the source's working bytes are the HEAD blob's. F-0001 exists, and
    for the escape.md case escape.md is a symlink out of the repository. Command: kblam challenge pin
    SC-0001 --expect D --snapshot <spelled> (pin takes no --by). Exit 1; stderr "kblam challenge pin:
    <message>". Files changed: none in either tree (the record keeps its blank pin); validate afterwards
    exits 0, clean. Acceptance 1."""
    _finding(kb, f"{TRACE}:3")
    if spelled == "escape.md":
        outside = tmp_path / "outside.md"
        outside.write_text("outside the KB\n", encoding="utf-8")
        _link(outside, kb.root / "escape.md")
    _install(kb, "SC", _sc(source_repo, status="open", pin=False))
    _accept(kb, capsys)

    assert _untouched(source_repo, kb, "challenge", "pin", "SC-0001", "--expect", _digest(kb, "SC-0001"),
                      "--snapshot", spelled) == 1
    assert capsys.readouterr().err == f"kblam challenge pin: {message}\n"
    source = _record(kb, "SC-0001").data["source"]
    assert all(source[key] is None for key in (*records.PIN_KEYS, "snapshot"))
    _validate_clean(source_repo, kb, capsys, ["kblam validate: OK (1 findings)"])


def test_review_decide_refuses_an_evidence_path_that_escapes_or_streams(kb, source_repo, capsys):
    """Start: SC-0001 open and complete. Command: kblam review decide SC-0001 --status confirmed --by
    reviewer-b --reason ... --expect D --evidence observed:../outside.md:row 0. Exit 1; stderr
    "--evidence path '../outside.md': '..' segment". Files changed: none in either tree (no decision is
    appended; the record is still open), and validate afterwards exits 0 with SC-0001 open.
    Acceptance 1."""
    _install(kb, "SC", _sc(source_repo, status="open"))
    _accept(kb, capsys)

    assert _untouched(source_repo, kb, "review", "decide", "SC-0001", "--status", "confirmed",
                      "--by", DECIDER, "--reason", "read the source at its pin",
                      "--expect", _digest(kb, "SC-0001"),
                      "--evidence", "observed:../outside.md:row 0") == 1
    assert capsys.readouterr().err == "kblam review decide: --evidence path '../outside.md': '..' segment\n"
    assert _record(kb, "SC-0001").status == "open"
    _validate_clean(source_repo, kb, capsys, ["kblam validate: OK (0 findings)"])


# --- pin, edit and the task commands, run to success ------------------------------------------------


def test_challenge_pin_pins_an_open_challenge_to_head(kb, source_repo, capsys):
    """Start: SC-0001 open and provisional (installed without a pin; the source's working bytes are the
    HEAD blob's, so the worktree can pin them). Command: kblam challenge pin SC-0001 --expect D. Exit 0;
    "kblam challenge pin: SC-0001 pinned (subject digest <12 hex>)". Files changed: the record, whose
    source now carries the Git pin, plus .kblam/review-ids and .kblam/tree.hash — the fixture
    hand-installed the record, so this first kblam write bootstraps the registry and the tree digest.
    validate afterwards exits 0 and the source is unchanged. Acceptance 1."""
    _install(kb, "SC", _sc(source_repo, status="open", pin=False))
    _accept(kb, capsys)

    code, changed = _written(source_repo, kb, "challenge", "pin", "SC-0001",
                             "--expect", _digest(kb, "SC-0001"))
    assert code == 0
    assert capsys.readouterr().out == \
        (f"kblam challenge pin: SC-0001 pinned (subject digest {_digest(kb, 'SC-0001')[:12]})\n")
    assert set(changed) == {f"{CHALLENGES}/SC-0001.yaml", REGISTRY, TREE_HASH}
    source = _record(kb, "SC-0001").data["source"]
    assert (source["repo"], source["commit"], source["blob"]) == \
        (SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    assert source["snapshot"] is None
    _validate_clean(source_repo, kb, capsys, ["kblam validate: OK (0 findings)"])


def test_challenge_pin_records_a_project_owned_snapshot(kb, source_repo, capsys):
    """Start: SC-0001 open and provisional; snapshots/trace-copy.md is a project-owned copy of the
    source's bytes, outside every source repository and every root kblam owns. Command: kblam challenge
    pin SC-0001 --expect D --snapshot snapshots/trace-copy.md. Exit 0; the same pinned line as pinning
    to HEAD. Files changed: the record, whose source now names the snapshot and keeps repo/commit/blob
    null, plus .kblam/review-ids and .kblam/tree.hash, bootstrapped by this first kblam write; the copy
    itself is kblam's to read, never to write. validate afterwards exits 0; the source repository is
    unchanged. Acceptance 1."""
    kb.write("snapshots/trace-copy.md", TRACE_TEXT)
    _install(kb, "SC", _sc(source_repo, status="open", pin=False))
    _accept(kb, capsys)

    code, changed = _written(source_repo, kb, "challenge", "pin", "SC-0001",
                             "--expect", _digest(kb, "SC-0001"),
                             "--snapshot", "snapshots/trace-copy.md")
    assert code == 0
    assert capsys.readouterr().out == \
        (f"kblam challenge pin: SC-0001 pinned (subject digest {_digest(kb, 'SC-0001')[:12]})\n")
    assert set(changed) == {f"{CHALLENGES}/SC-0001.yaml", REGISTRY, TREE_HASH}
    source = _record(kb, "SC-0001").data["source"]
    assert source["snapshot"] == "snapshots/trace-copy.md"
    assert all(source[key] is None for key in records.PIN_KEYS)
    _validate_clean(source_repo, kb, capsys, ["kblam validate: OK (0 findings)"])


def test_challenge_edit_stages_a_copy_of_an_open_challenge(kb, source_repo, capsys):
    """Start: SC-0001 open and installed, its tree accepted. Command: kblam challenge edit SC-0001.
    Exit 0; stdout the staged path, whose bytes equal the installed record's. Files changed: the staged
    copy and its edit-base receipt, nothing else. validate afterwards exits 0; the source repository is
    unchanged. Acceptance 1."""
    _install(kb, "SC", _sc(source_repo, status="open"))
    _accept(kb, capsys)

    staged, changed = _staged(source_repo, kb, capsys, "challenge", "edit", "SC-0001")
    assert staged == kb.root / STAGING / "SC-0001.yaml"
    assert set(changed) == {f"{STAGING}/SC-0001.yaml", f"{RECEIPTS}/SC-0001.edit-base.json"}
    assert staged.read_bytes() == (kb.root / CHALLENGES / "SC-0001.yaml").read_bytes()
    _validate_clean(source_repo, kb, capsys, ["kblam validate: OK (0 findings)"])


# --- every feature command leaves the source repository unchanged ----------------------------------


def test_a_whole_review_workflow_leaves_the_source_repository_unchanged(kb, source_repo, capsys):
    """Start: F-0001 quotes line 3 of the trace; no records. Every §5.1.5 command that reads or relates
    to the source is run in turn through the CLI, each on its own actor: challenge new (by reviewer-a)
    → put → challenge show → review decide --status confirmed (reviewer-b) → challenge uses → use review
    (reviewer-a) → put → review decide --status approved (reviewer-b) → review rebind (reviewer-b) →
    task new (reviewer-a) → put → task show → task edit → review list → review index → validate →
    challenge pin (refused: the challenge is confirmed) → challenge edit (refused: the challenge is
    confirmed). Each exit status is asserted, each command's exact set of changed KB paths is asserted,
    and the source repository's working bytes, HEAD, index, refs and packed-refs are compared before and
    after every one of them (Acceptance 1). Afterwards validate exits 0 with the open task pending."""
    _finding(kb, f"{TRACE}:3")
    _accept(kb, capsys)

    staged, changed = _staged(source_repo, kb, capsys, "challenge", "new", TRACE, "--lines", "3-3",
                              "--by", "reviewer-a")
    assert staged == kb.root / STAGING / "SC-0001.yaml"
    assert set(changed) == {f"{STAGING}/SC-0001.yaml", f"{RECEIPTS}/SC-0001.json"}
    _fill(staged, **_sc_fields(source_repo))

    code, changed = _written(source_repo, kb, "put", str(staged))
    assert code == 0
    assert capsys.readouterr().out == f"kblam put: SC-0001 -> {CHALLENGES}/SC-0001.yaml\n"
    assert set(changed) == {f"{CHALLENGES}/SC-0001.yaml", INDEX, REGISTRY, TREE_HASH,
                            f"{STAGING}/SC-0001.yaml"}

    assert _untouched(source_repo, kb, "challenge", "show", "SC-0001") == 0
    assert capsys.readouterr().out.startswith("SC-0001 open\nsubject digest: ")

    code, changed = _written(source_repo, kb, "review", "decide", "SC-0001", "--status", "confirmed",
                             "--by", DECIDER, "--reason", "read the source at its pin",
                             "--expect", _digest(kb, "SC-0001"))
    assert code == 0
    text = capsys.readouterr().out
    assert text.splitlines()[0] == \
        f"kblam review decide: SC-0001 is now confirmed (subject digest {_digest(kb, 'SC-0001')[:12]})"
    assert text.splitlines()[-1] == \
        ("kblam review decide: done, but kblam validate still fails (1 error(s) listed above, owned by "
         "other findings or records)")
    assert set(changed) == {f"{CHALLENGES}/SC-0001.yaml", INDEX, TREE_HASH}

    assert _untouched(source_repo, kb, "challenge", "uses", "SC-0001") == 0
    line = _tag_line(kb, f"{TRACE}:3")
    assert capsys.readouterr().out == \
        (f"F-0001 {FINDING_PATH}:{line} excerpt 1 same: error; "
         f"kblam use review SC-0001 F-0001 1 --by NAME --proponent NAME\n")

    use_staged, changed = _staged(source_repo, kb, capsys, "use", "review", "SC-0001", "F-0001", "1",
                                  "--by", "reviewer-a", "--proponent", "researcher-a")
    assert set(changed) == {f"{STAGING}/CU-0001.yaml", f"{RECEIPTS}/CU-0001.json"}
    _fill(use_staged, **_cu_fields())

    code, changed = _written(source_repo, kb, "put", str(use_staged))
    assert code == 0
    assert capsys.readouterr().out.splitlines()[0] == f"kblam put: CU-0001 -> {USES}/CU-0001.yaml"
    assert set(changed) == {f"{USES}/CU-0001.yaml", INDEX, REGISTRY, TREE_HASH,
                            f"{STAGING}/CU-0001.yaml"}

    code, changed = _written(source_repo, kb, "review", "decide", "CU-0001", "--status", "approved",
                             "--by", DECIDER, "--reason", "the excerpt is used only for the printed bytes",
                             "--expect", _digest(kb, "CU-0001"))
    assert code == 0
    assert capsys.readouterr().out == \
        (f"kblam review decide: CU-0001 is now approved (subject digest {_digest(kb, 'CU-0001')[:12]})\n")
    assert set(changed) == {f"{USES}/CU-0001.yaml", INDEX, TREE_HASH}

    code, changed = _written(source_repo, kb, "review", "rebind", "CU-0001", "--by", DECIDER,
                             "--reason", "rechecked the excerpt in this revision",
                             "--expect", _digest(kb, "CU-0001"))
    assert code == 0
    assert capsys.readouterr().out.endswith(
        f"kblam review rebind: CU-0001 rebound, now approved "
        f"(subject digest {_digest(kb, 'CU-0001')[:12]})\n")
    assert set(changed) == {f"{USES}/CU-0001.yaml", TREE_HASH}      # the status is unchanged

    task_staged, changed = _staged(source_repo, kb, capsys, "task", "new", "F-0001",
                                   "--kind", "replication", "--by", "reviewer-a",
                                   "--proponent", "researcher-a")
    assert set(changed) == {f"{STAGING}/CT-0001.yaml", f"{RECEIPTS}/CT-0001.json"}
    _fill(task_staged, **_ct_fields())

    code, changed = _written(source_repo, kb, "put", str(task_staged))
    assert code == 0
    assert capsys.readouterr().out == f"kblam put: CT-0001 -> {TASKS}/CT-0001.yaml\n"
    assert set(changed) == {f"{TASKS}/CT-0001.yaml", INDEX, REGISTRY, TREE_HASH,
                            f"{STAGING}/CT-0001.yaml"}

    assert _untouched(source_repo, kb, "task", "show", "CT-0001") == 0
    binding = _record(kb, "CT-0001").data
    assert capsys.readouterr().out.splitlines() == [
        "CT-0001 open",
        f"subject digest: {_digest(kb, 'CT-0001')}",
        "kind: replication",
        "finding: F-0001",
        "proponent: researcher-a",
        f"binding: fingerprint {binding['claim_fingerprint']}, file sha256 "
        f"{binding['base_file_sha256']}",
        "binding: current",
        "question: Does an independent measurement establish the claim?",
        "method: Repeat the capture with the documented settings.",
        "outcomes.supports: The ratio is within 0.1%.",
        "outcomes.refutes: The ratio differs by more.",
        "outcomes.inconclusive: The capture is too noisy to tell.",
        "controls: same firmware version",
        "stop: Stop after three captures.",
        "expected evidence: an evidence/ capture package",
        "decisions:",
        "  none",
    ]

    edited, changed = _staged(source_repo, kb, capsys, "task", "edit", "CT-0001")
    assert edited == kb.root / STAGING / "CT-0001.yaml"
    assert set(changed) == {f"{STAGING}/CT-0001.yaml", f"{RECEIPTS}/CT-0001.edit-base.json"}

    assert _untouched(source_repo, kb, "review", "list") == 0
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == \
        ["SC-0001", "CT-0001", "CU-0001"]

    code, changed = _written(source_repo, kb, "review", "index")
    assert code == 0
    assert capsys.readouterr().out.startswith("kblam review index: wrote ")
    assert set(changed) == set()      # the bytes the preceding puts wrote are already current

    _validate_clean(source_repo, kb, capsys, [
        "CT-0001 open replication of F-0001: Does an independent measurement establish the claim?",
        "kblam validate: OK (1 findings); 1 pending task(s)",
    ])

    # the two refusals: each still leaves both trees alone
    assert _untouched(source_repo, kb, "challenge", "pin", "SC-0001",
                      "--expect", _digest(kb, "SC-0001")) == 1
    assert capsys.readouterr().err == \
        ("kblam challenge pin: SC-0001 is confirmed; only an open challenge can be pinned (its source is "
         "fixed from the decision that closes it)\n")
    assert _untouched(source_repo, kb, "challenge", "edit", "SC-0001") == 1
    assert capsys.readouterr().err == \
        "kblam challenge edit: SC-0001 is confirmed; only an open challenge can be edited\n"
