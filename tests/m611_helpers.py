"""Helpers for the SPEC §12 M6.11 acceptance tests (the twelve test groups): stage, fill, put and
decide review records through the real CLI, so a group file reads as a scenario and asserts on output.

Use `import m611_helpers` (tests/ is on sys.path; a module without the `test_` prefix is not collected)
with conftest's `kb` and `source_repo` fixtures. `kb` is a KB holding one evidence folder
(evidence/2026-09-22-ratio/) and no findings; `source_repo` is a nested Git repository at
resources/mx-docs whose notes/full-scan-trace.md holds TRACE_TEXT (four lines, LF), committed.

    import m611_helpers as m

    frozen_today = m.frozen_today            # module level: pins created and decision dates

    def test_something(kb, source_repo):
        m.quoting_finding(kb, "F-0001", source_repo, "3-3")        # F-0001 quotes trace line 3
        sc = m.confirmed_challenge(kb, source_repo, "3-3", by="reviewer-a", decider="reviewer-b")
        m.approved_use(kb, sc, "F-0001", 1, proponent="researcher-a", reviewer="reviewer-b")
        assert m.validate(kb).code == 0

The use in that example is not decoration: an installed excerpt inside a confirmed challenge's
assertion is a K14 error until a current use covers it, so the challenge alone leaves validate at
exit 1. `test_the_documented_example` runs the sequence exactly as written.

Driving the CLI
---------------
- `kblam(kb, *argv) -> Run` runs `kblam.cli.main(["--root", <kb root>, *argv])` in process, capturing
  stdout and stderr (no capsys needed) and turning SystemExit into `Run.code`. `Run` holds `code`,
  `out` and `err`.
- `validate(kb, *extra)` is `kblam(kb, "validate", *extra)`; extra is `--record`, `--forget-missing`.
- `ok(run, what="")` asserts exit 0, printing the output when it did not hold, and returns the run.

Records
-------
All three record-staging helpers assert exit 0, full stdout of exactly the staged path plus a newline,
empty stderr, and a staged file whose ID matches the printed SC-/CT-/CU- ID (four digits or more).

- `stage_challenge(kb, source_repo, lines, *, by, path=None, **fields) -> Staged`: `challenge new`,
  then the author's filling: a proposition, a scope, `contradicted`, one basis entry on the source
  itself with a primary provenance, a usable remainder and limits. `fields` are the SC's free fields
  (proposition, scope, classification, basis, usable, limits, linked_findings) and replace those
  defaults: `""` or `[]` leaves a field blank for a put that must be refused, and `DROP` (conftest)
  as a value drops the key. `classification="wrong_model"` makes the default basis role
  `model-mismatch`. `path` is the source, relative to the KB root (default: the fixture trace).
- `stage_task(kb, finding_id, *, kind="replication", by, proponent, **fields) -> Staged`: `task new`
  plus a question, a method, the three outcomes, controls, a stop condition and expected evidence.
  `fields` are the CT's free fields.
- `stage_use(kb, sc_id, finding_id, ordinal=1, *, by, proponent, **fields) -> Staged`: `use review`
  plus `unaffected_raw_bytes` and a reason; `fields` are `disposition` and `reason`.
- `put(kb, staged_or_path) -> Run`, `put_ok(kb, staged_or_path) -> Run` (put, asserting exit 0).
- `challenge(...) -> str`, `task(...) -> str`, `use(...) -> str` stage, put and assert 0, returning
  the ID. `open_task(...) -> str` is `task(...)`, left open: validate then prints a pending line.
- `confirmed_challenge(kb, source_repo, lines, *, by, decider, **fields) -> str` is `challenge(...)`
  plus the pin a confirmation needs (`challenge pin`, when `challenge new` left the source
  provisional) and `review decide --status confirmed` by `decider`, who must differ from `by`
  (independence, §5.2.2).
- `approved_use(kb, sc_id, finding_id, ordinal=1, *, proponent, reviewer) -> str` drafts the use as
  `reviewer`, puts it and approves it by `reviewer`, who must differ from `proponent`.
- `confirmed_task(kb, ct_id, *, by, evidence=PRIMARY_EVIDENCE, reason=…) -> Run` decides `confirmed`;
  pass `evidence=()` to check the refusal of a confirmation with no primary evidence.
- `decide(kb, rec_id, status, *, by, reason=…, evidence=()) -> Run` and
  `rebind(kb, rec_id, *, by, reason=…, evidence=(), reopen=False) -> Run` run the decision commands
  with `--expect` taken here, so a test never computes the digest itself. An evidence item is either
  `"PROVENANCE:PATH:LOCATOR"` or a `(provenance, path, locator)` tuple.
- `pin_challenge(kb, rec_id, *, snapshot=None) -> Run` runs `challenge pin --expect <digest>`.
- The decision and pin commands return the run: assert `run.code` and `run.out` or `run.err`, which
  is where the diagnostic under test is.

Findings and sources
--------------------
- `quoting_finding(kb, finding_id, source_repo, lines, *, slug="ratio", claim=CLAIM, path=None,
  body="") -> Path` places a finding through `kb.add` (fixture setup, not a put) whose one verbatim
  excerpt quotes exactly lines A-B of the source, so K10 verifies it and K14 sees it inside a
  challenge on those lines. `topic`, `title`, `label`, `scope` and `evidence` go through to
  conftest's `finding_text`.
- `verbatim(tag, excerpt) -> str` is a `<!-- verbatim: tag -->` blockquote block, for a finding body
  built by hand: `kb.add("F-0001", "ratio", CLAIM, body=verbatim(f"{TRACE}:2", "Row 101: …"))`.
- `stage_finding(kb, finding_id, text, *, slug=None) -> Path` stages a finding for `kblam put`:
  `kblam edit <finding_id>` for an installed finding, or `kblam new <topic> <title>` for a new one
  (topic and title from `text`'s frontmatter; the staged file is named for the ID `text` carries,
  under `slug` when given and otherwise under the slug `kblam new` derived). The staged file is then
  written with `text`. This is the route for a test that needs the put itself: its refusal, exit code
  and message.
- `tree(kb, *, exclude=()) -> dict[str, bytes]` is every file under the KB root as
  {repo-relative POSIX path: bytes}, leaving out nested source repositories and every `.git`;
  `changed(before, after) -> set[str]` is the paths added, removed or holding other bytes. Together
  they are the "files changed" assertion; `exclude=["research-review/"]` watches one root alone.
- `accept_tree(kb) -> None` is fixture setup for a hand-written tree, and nothing else: it writes the
  review root's INDEX.md from the records present (the empty index when there are none, as
  `kblam review index` writes it) and records .kblam/tree.hash for the tree as it is now, as
  `kblam validate --record` would. Call it after installing records or findings out of band, and
  before a command that validates, which would otherwise report K7 or K13. Never call it in a test
  whose subject is K7, K13 or tree.hash behaviour: it re-records tree.hash unconditionally, so it
  would accept the very divergence under test.
- `source_repo.snapshot()` (conftest) is "the source is unchanged": working bytes, HEAD, index and
  refs, equal before and after.

Reading a KB
------------
- `expect(kb, rec_id) -> str`: the record's subject digest, read through the library
  (view.load_view + decisions.subject_digest), which is the value `challenge show`, `task show` and
  `review list` print and `--expect` takes.
- `record_path(kb, rec_id) -> Path` is where the record is installed; `registry(kb) -> list[str] |
  None` is .kblam/review-ids, or None when the file does not exist.

Order, and the traps
--------------------
- A put of a finding that quotes a confirmed challenge's assertion is refused (K14, §5.2.4). Build
  findings before the challenge is confirmed (`quoting_finding`, or `kb.add`), or edit a finding
  that already had the excerpt.
- A confirmation needs a pinned, available source. `confirmed_challenge` pins a provisional one for
  you, but a working file with uncommitted changes cannot be pinned, so a test of that refusal
  stages and puts by hand: `stage_challenge`, `put`, `decide`.
- Records installed by hand (`kb.add`, or a record file written directly) leave the review INDEX.md
  and tree.hash stale: call `accept_tree` before the next validating command.
- Everything runs in process and offline: the fixture KBs enable no Jev verdict, so no request
  leaves the machine. `frozen_today` pins `created` and a decision's `date` at TODAY (2026-09-28),
  which exact byte assertions need.
- Constants: `TRACE` (the source path as records write it), `TRACE_PATH`, `TRACE_TEXT`, `SOURCE_REPO`,
  `LINE2`, `LINE3`, `CLAIM`, `EVIDENCE_PATH`, `PRIMARY_EVIDENCE`, `TODAY`. `finding_text` is
  conftest's, re-exported here.
"""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import DROP, SOURCE_REPO, TRACE_PATH, TRACE_TEXT, finding_text
from kblam import records, review_stage, review_write
from kblam.cli import main
from kblam.decisions import subject_digest
from kblam.finding import yaml_rt
from kblam.review_index import generate_review_index
from kblam.treehash import write_tree_hash_v2
from kblam.view import load_view

__all__ = [
    "CLAIM", "DROP", "EVIDENCE_PATH", "LINE2", "LINE3", "PRIMARY_EVIDENCE", "Run", "SOURCE_REPO",
    "Staged", "TODAY", "TRACE", "TRACE_PATH", "TRACE_TEXT", "accept_tree", "approved_use", "challenge",
    "changed", "confirmed_challenge", "confirmed_task", "decide", "expect", "finding_text",
    "frozen_today", "kblam", "ok", "open_task", "pin_challenge", "put", "put_ok", "quoting_finding",
    "rebind", "record_path", "registry", "stage_challenge", "stage_finding", "stage_task", "stage_use",
    "task", "tree", "use", "validate", "verbatim",
]

TODAY = datetime.date(2026, 9, 28)
TRACE = f"{SOURCE_REPO}/{TRACE_PATH}"                     # the fixture source, as records write it
LINE2 = "Row 101: bytes 0x3A 0x3B"                        # TRACE_TEXT line 2
LINE3 = "Row 102: bytes 0x3A 0x3B; the two bytes are equal."   # TRACE_TEXT line 3
CLAIM = "The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0)."
EVIDENCE_PATH = "evidence/2026-09-22-ratio/README.md"     # a primary file the kb fixture holds
PRIMARY_EVIDENCE = f"observed:{EVIDENCE_PATH}:row 0"


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """Pin `created` and a decision's `date` at TODAY. Declare it at module level
    (`frozen_today = m611_helpers.frozen_today`) in any group file that compares record bytes or
    show output; it is autouse, so every test in that module gets it."""
    monkeypatch.setattr(review_stage, "_today", lambda: TODAY)
    monkeypatch.setattr(review_write, "_today", lambda: TODAY)
    return TODAY


# --- driving the CLI ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """A CLI run: its exit code, stdout and stderr."""

    code: int
    out: str
    err: str


def kblam(kb, *argv: object) -> Run:
    """`kblam --root <kb> <argv>` in process, with stdout and stderr captured. A usage error's
    SystemExit becomes `Run.code` (2), so a test asserts on the code instead of catching it."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(["--root", str(kb.root), *[str(arg) for arg in argv]])
        except SystemExit as exc:
            code = 0 if exc.code is None else exc.code if isinstance(exc.code, int) else 1
    return Run(code, out.getvalue(), err.getvalue())


def validate(kb, *extra: object) -> Run:
    """`kblam validate`, with everything validate takes on top (`--record`, `--forget-missing`)."""
    return kblam(kb, "validate", *extra)


def ok(run: Run, what: str = "kblam") -> Run:
    """Assert a run exited 0, showing its output when it did not; the run itself, for chaining."""
    assert run.code == 0, f"{what} exited {run.code}:\n{run.out}{run.err}"
    return run


# --- reading a KB ---------------------------------------------------------------------------------


def expect(kb, rec_id: str) -> str:
    """The record's subject digest, as `challenge show`, `task show` and `review list` print it and as
    `--expect` takes it. Read through the library (view.load_view + decisions.subject_digest)."""
    view = load_view(kb.cfg)
    rec = next((r for r in view.records if r.id == rec_id and isinstance(r.data, dict)), None)
    assert rec is not None, f"{rec_id} is not a record in {kb.cfg.review_dir}/"
    return subject_digest(rec.kind, rec.data)


def record_path(kb, rec_id: str) -> Path:
    """Where the record is installed: <review root>/challenges|tasks|uses/<ID>.yaml."""
    return kb.root / kb.cfg.review_dir / records.KINDS[rec_id[:2]] / f"{rec_id}.yaml"


def registry(kb) -> list[str] | None:
    """The record-ID registry (.kblam/review-ids) as a list, or None when the file does not exist."""
    path = kb.root / ".kblam/review-ids"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def tree(kb, *, exclude: Iterable[str] = ()) -> dict[str, bytes]:
    """Every file under the KB root as {repo-relative POSIX path: bytes}. Nested source repositories
    and anything under a `.git` are left out, as is any path equal to or under an `exclude` prefix."""
    repos = [p.parent.relative_to(kb.root).as_posix() for p in kb.root.rglob(".git") if p.parent != kb.root]
    skipped = tuple(str(prefix).rstrip("/") for prefix in exclude)
    out = {}
    for path in sorted(kb.root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(kb.root)
        if ".git" in rel.parts:
            continue
        name = rel.as_posix()
        if any(name == repo or name.startswith(f"{repo}/") for repo in repos):
            continue
        if any(name == prefix or name.startswith(f"{prefix}/") for prefix in skipped):
            continue
        out[name] = path.read_bytes()
    return out


def changed(before: Mapping[str, bytes], after: Mapping[str, bytes]) -> set[str]:
    """The paths added, removed or holding other bytes between two tree() snapshots."""
    return {name for name in set(before) | set(after) if before.get(name) != after.get(name)}


def accept_tree(kb) -> None:
    """Fixture setup, and nothing else: write the review root's INDEX.md from the records present
    (the empty index when there are none, as `kblam review index` writes it) and record
    .kblam/tree.hash for the tree as it is now, as `kblam validate --record` would. Call it after
    installing records or findings out of band, and before a validating command. It re-records
    tree.hash unconditionally, so a test whose subject is K7, K13 or tree.hash must not call it."""
    view = load_view(kb.cfg)
    kb.write(f"{kb.cfg.review_dir}/INDEX.md", generate_review_index(view))
    write_tree_hash_v2(kb.cfg, load_view(kb.cfg))


# --- staging, filling and putting records ---------------------------------------------------------


@dataclass(frozen=True)
class Staged:
    """A staged record: its ID and the staged file kblam wrote, which its author may edit."""

    id: str
    path: Path


def _span(lines: object) -> tuple[int, int]:
    """(A, B) from "A-B", (A, B) or one line number."""
    if isinstance(lines, str):
        first, _, second = lines.partition("-")
        return int(first), int(second or first)
    if isinstance(lines, int):
        return lines, lines
    a, b = lines            # a 2-sequence, as --lines A-B parses to
    return int(a), int(b)


def _lines_arg(lines: object) -> str:
    a, b = _span(lines)
    return f"{a}-{b}"


def _evidence_args(evidence) -> list[str]:
    """--evidence arguments from "PROVENANCE:PATH:LOCATOR" strings or (provenance, path, locator)."""
    if isinstance(evidence, str):
        evidence = (evidence,)
    out: list[str] = []
    for item in evidence:
        out += ["--evidence", item if isinstance(item, str) else ":".join(item)]
    return out


def _fill(path: Path, defaults: Mapping, fields: Mapping) -> dict:
    """Write the staged record back with `defaults` filled in and `fields` over them (DROP removes a
    key), as its author would, with LF endings."""
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    for key, value in {**defaults, **fields}.items():
        if value is DROP:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_bytes(records.dump(data))
    return dict(data)


def _sc_defaults(kb, path: str, classification: str) -> dict:
    """The blank SC fields filled the way a reviewer would: a claim, a scope, a classification and one
    basis entry on the source itself (put hashes it and gives it the source's pin, §5.2.5)."""
    role = "model-mismatch" if classification == "wrong_model" else "internal-inconsistency"
    return {
        "proposition": "The printed byte equality follows from the printed byte values",
        "scope": ["MX-100 capture transcription"],
        "classification": classification,
        "basis": [{"path": path, "sha256": None, "repo": None, "commit": None, "blob": None,
                   "snapshot": None, "locator": "row 102: printed byte values", "role": role,
                   "provenance": kb.cfg.primary_provenance[0]}],
        "usable": "The printed byte values may be cited as a report.",
        "limits": "Do not infer the capture bytes from this row.",
    }


def _ct_defaults() -> dict:
    """The blank CT fields filled the way a researcher would."""
    return {
        "question": "Does an independent measurement establish the claim?",
        "method": "Repeat the capture with the documented settings.",
        "outcomes": {"supports": "The ratio is within 0.1%.", "refutes": "The ratio differs by more.",
                     "inconclusive": "The capture is too noisy to tell."},
        "controls": ["same firmware version"],
        "stop": "Stop after three captures.",
        "expected_evidence": ["an evidence/ capture package"],
    }


def _cu_defaults() -> dict:
    """The blank CU fields filled the way a reviewer would."""
    return {"disposition": "unaffected_raw_bytes",
            "reason": "The excerpt is cited only for the printed byte values."}


def _staged_path(kb, run: Run, kind: str, what: str) -> Path:
    """Assert exit 0, exactly the staged path plus newline, empty stderr and a matching file ID.
    Check the ID's shape rather than duplicating allocation over records, registry and receipts."""
    output = f"{what} exited {run.code}:\nstdout: {run.out!r}\nstderr: {run.err!r}"
    prefix = re.escape(str(kb.cfg.review_staging_dir / kind))
    match = re.fullmatch(rf"{prefix}-([0-9]{{4,}})\.yaml\n", run.out)
    assert run.code == 0 and run.err == "" and match is not None, output
    rec_id = f"{kind}-{match[1]}"
    staged = kb.cfg.review_staging_dir / f"{rec_id}.yaml"
    assert staged.is_file(), f"{output}\nstaged file is missing: {staged}"
    data = yaml_rt().load(staged.read_bytes().decode("utf-8"))
    assert isinstance(data, dict) and data.get("id") == rec_id, \
        f"{output}\nstaged record ID does not match {rec_id}"
    return staged


def stage_challenge(kb, source_repo, lines: object, *, by: str, path: str | None = None,
                    **fields) -> Staged:
    """`challenge new` plus the author's filling: an SC ready for `put`. Assert exit 0, exactly the
    staged SC path plus newline, empty stderr and a file with the same ID. `lines` is "A-B", (A, B) or
    one line number; `path` is the source (default: the fixture trace); `fields` are the SC's free
    fields and replace the defaults (DROP drops a key, "" leaves it blank)."""
    path = path or source_repo.kb_path()
    run = kblam(kb, "challenge", "new", path, "--lines", _lines_arg(lines), "--by", by)
    staged = _staged_path(kb, run, "SC", "challenge new")
    data = _fill(staged, _sc_defaults(kb, path, fields.get("classification", "contradicted")), fields)
    return Staged(data["id"], staged)


def stage_task(kb, finding_id: str, *, kind: str = "replication", by: str, proponent: str,
               **fields) -> Staged:
    """`task new` plus the researcher's filling: a CT ready for `put`. Assert exit 0, exactly the
    staged CT path plus newline, empty stderr and a file with the same ID."""
    run = kblam(kb, "task", "new", finding_id, "--kind", kind, "--by", by,
                "--proponent", proponent)
    staged = _staged_path(kb, run, "CT", "task new")
    data = _fill(staged, _ct_defaults(), fields)
    return Staged(data["id"], staged)


def stage_use(kb, sc_id: str, finding_id: str, ordinal: int = 1, *, by: str, proponent: str,
              **fields) -> Staged:
    """`use review` plus the reviewer's filling: a CU ready for `put`. Assert exit 0, exactly the
    staged CU path plus newline, empty stderr and a file with the same ID."""
    run = kblam(kb, "use", "review", sc_id, finding_id, ordinal, "--by", by,
                "--proponent", proponent)
    staged = _staged_path(kb, run, "CU", "use review")
    data = _fill(staged, _cu_defaults(), fields)
    return Staged(data["id"], staged)


def put(kb, staged) -> Run:
    """`kblam put <file>` for a staged record or finding: a Staged object or a path."""
    return kblam(kb, "put", str(staged.path if isinstance(staged, Staged) else staged))


def put_ok(kb, staged) -> Run:
    """`put`, asserting it was accepted."""
    return ok(put(kb, staged), "put")


def challenge(kb, source_repo, lines: object, *, by: str, **fields) -> str:
    """stage_challenge + a put that must be accepted; the installed challenge's ID."""
    staged = stage_challenge(kb, source_repo, lines, by=by, **fields)
    put_ok(kb, staged)
    return staged.id


def task(kb, finding_id: str, *, kind: str = "replication", by: str, proponent: str,
         **fields) -> str:
    """stage_task + a put that must be accepted; the installed task's ID."""
    staged = stage_task(kb, finding_id, kind=kind, by=by, proponent=proponent, **fields)
    put_ok(kb, staged)
    return staged.id


def open_task(kb, finding_id: str, *, kind: str = "replication", by: str, proponent: str,
              **fields) -> str:
    """task(...), left open: validate then prints its pending line and still exits 0."""
    return task(kb, finding_id, kind=kind, by=by, proponent=proponent, **fields)


def use(kb, sc_id: str, finding_id: str, ordinal: int = 1, *, by: str, proponent: str,
        **fields) -> str:
    """stage_use + a put that must be accepted; the installed use's ID (still open)."""
    staged = stage_use(kb, sc_id, finding_id, ordinal, by=by, proponent=proponent, **fields)
    put_ok(kb, staged)
    return staged.id


def approved_use(kb, sc_id: str, finding_id: str, ordinal: int = 1, *, proponent: str,
                 reviewer: str) -> str:
    """use(...) drafted and approved by `reviewer`, who must differ from `proponent` (independence)."""
    rec_id = use(kb, sc_id, finding_id, ordinal, by=reviewer, proponent=proponent)
    ok(decide(kb, rec_id, "approved", by=reviewer), "review decide")
    return rec_id


def _pinned(kb, rec_id: str) -> bool:
    view = load_view(kb.cfg)
    rec = next(r for r in view.records if r.id == rec_id)
    source = rec.data["source"]
    return source["repo"] is not None or source["snapshot"] is not None


def confirmed_challenge(kb, source_repo, lines: object, *, by: str, decider: str,
                        **fields) -> str:
    """challenge(...) plus the pin a confirmation needs (challenge pin, when the source is still
    provisional) and `review decide --status confirmed` by `decider`, who must differ from `by`."""
    rec_id = challenge(kb, source_repo, lines, by=by, **fields)
    if not _pinned(kb, rec_id):
        ok(pin_challenge(kb, rec_id), "challenge pin")
    ok(decide(kb, rec_id, "confirmed", by=decider), "review decide")
    return rec_id


# --- decisions ------------------------------------------------------------------------------------


def decide(kb, rec_id: str, status: str, *, by: str, reason: str = "reviewed the record",
           evidence=()) -> Run:
    """`review decide <rec_id> --status <status> --by <by> --reason <reason> --expect <digest>
    [--evidence …]`, with `--expect` taken here. The caller asserts the code and output."""
    return kblam(kb, "review", "decide", rec_id, "--status", status, "--by", by,
                 "--reason", reason, "--expect", expect(kb, rec_id), *_evidence_args(evidence))


def rebind(kb, rec_id: str, *, by: str, reason: str = "rechecked the record", evidence=(),
           reopen: bool = False) -> Run:
    """`review rebind <rec_id> --by <by> --reason <reason> --expect <digest> [--evidence …]
    [--reopen]`, with `--expect` taken here."""
    argv = ["review", "rebind", rec_id, "--by", by, "--reason", reason, "--expect", expect(kb, rec_id)]
    if reopen:
        argv.append("--reopen")
    return kblam(kb, *argv, *_evidence_args(evidence))


def pin_challenge(kb, rec_id: str, *, snapshot: str | None = None) -> Run:
    """`challenge pin <rec_id> --expect <digest> [--snapshot PATH]`."""
    argv = ["challenge", "pin", rec_id, "--expect", expect(kb, rec_id)]
    if snapshot is not None:
        argv += ["--snapshot", snapshot]
    return kblam(kb, *argv)


def confirmed_task(kb, ct_id: str, *, by: str, evidence=PRIMARY_EVIDENCE,
                   reason: str = "the evidence establishes the claim") -> Run:
    """`review decide <ct_id> --status confirmed` with primary evidence; `evidence=()` checks the
    refusal of a confirmation that cites none."""
    return decide(kb, ct_id, "confirmed", by=by, reason=reason, evidence=evidence)


# --- findings and sources -------------------------------------------------------------------------


def verbatim(tag: str, excerpt: str) -> str:
    """A verbatim tag and its blockquote, as a finding body: `verbatim(f"{TRACE}:2", LINE2)`."""
    block = "\n".join(f"> {line}" for line in excerpt.split("\n"))
    return f"Detail follows.\n\n<!-- verbatim: {tag} -->\n{block}"


def _source_lines(source_repo, path: str, a: int, b: int) -> str:
    """Lines A-B of the source's working bytes, LF-normalised and without a final newline: the text
    K10 compares against."""
    raw = (source_repo.kb_root / path).read_bytes()
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(text.split("\n")[a - 1:b])


def quoting_finding(kb, finding_id: str, source_repo, lines: object, *, slug: str = "ratio",
                    claim: str = CLAIM, path: str | None = None, body: str = "", **kw) -> Path:
    """Place a finding through `kb.add` whose one verbatim excerpt quotes exactly lines A-B of the
    source, so K10 verifies it and K14 relates it to a challenge on those lines. `body` is extra prose
    before the quote; anything else in `**kw` goes to conftest's finding_text."""
    path = path or source_repo.kb_path()
    a, b = _span(lines)
    block = verbatim(f"{path}:{a}-{b}", _source_lines(source_repo, path, a, b))
    return kb.add(finding_id, slug, claim, body=f"{body}\n\n{block}" if body else block, **kw)


def _frontmatter(text: str) -> dict:
    """The leading YAML frontmatter of a finding, as plain data."""
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "the finding text has no frontmatter"
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    return dict(yaml_rt().load("\n".join(lines[1:end])))


def _renamed(path: Path, name: str) -> Path:
    """`path` under the new name, with the old file moved aside when the name changes."""
    if path.name == name:
        return path
    target = path.with_name(name)
    path.rename(target)
    return target


def stage_finding(kb, finding_id: str | None, text: str, *, slug: str | None = None) -> Path:
    """Stage a finding for `kblam put`, the way the CLI requires: `kblam edit <finding_id>` for an
    installed finding, or `kblam new <topic> <title>` for a new one. The staged file is named for the
    ID `text` carries, under `slug` when given and otherwise under the slug `kblam new` derived, and
    is then written with `text`; the caller `put`s it."""
    meta = _frontmatter(text)
    if finding_id is None:
        run = ok(kblam(kb, "new", meta["topic"], meta["title"]), "new")
        path = Path(run.out.strip())
        _prefix, _number, allocated = path.name.split("-", 2)     # F-NNNN-<slug>.md
        path = _renamed(path, f"{meta['id']}-{slug or allocated.removesuffix('.md')}.md")
    else:
        run = ok(kblam(kb, "edit", finding_id), "edit")
        path = Path(run.out.strip())
        if slug is not None:
            path = _renamed(path, f"{finding_id}-{slug}.md")
    path.write_bytes(text.encode("utf-8"))
    return path
