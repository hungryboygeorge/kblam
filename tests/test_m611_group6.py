"""SPEC §12 M6.11 test group 6, *Independence and identity* (Acceptance 5).

Every test drives the real CLI through `m611_helpers.kblam` (`kblam.cli.main` in process), one call at a
time: each single command — setup commands included — runs inside its own `changes(...)` bracket, which
asserts that the whole KB tree and the source repository change by exactly the paths that command may
change (`set()` for a refusal or a read-only command), and every `Run` is asserted in full (exit status,
the whole stdout, the whole stderr). The test's own fixture edits (placing a finding, filling a staged
record, editing an installed record by hand) sit outside those windows.

Each test states its start state, the command and actor, the exit status, the diagnostics, the files
changed, the validation result afterwards and the acceptance criterion it demonstrates (A5).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

import m611_helpers as m
from kblam import records
from kblam.finding import yaml_rt

frozen_today = m.frozen_today                     # created dates and decision dates at TODAY

REVIEW = "research-review"
INDEX = f"{REVIEW}/INDEX.md"
REGISTRY = ".kblam/review-ids"
TREE_HASH = ".kblam/tree.hash"
STAGING = ".kblam/review-staging/{}.yaml"
RECEIPT = ".kblam/review-receipts/{}.json"
EDIT_BASE = ".kblam/review-receipts/{}.edit-base.json"
PATH = {"source-challenge": f"{REVIEW}/challenges/source-challenge-0001.yaml", "claim-task": f"{REVIEW}/tasks/claim-task-0001.yaml",
        "checked-use": f"{REVIEW}/uses/checked-use-0001.yaml"}
FINDING = "findings/calibration/F-0001-ratio.md"

CREATOR = "reviewer-a"                            # the source challenge's and the task's creator
PROPONENT = "researcher-a"                        # the task's and the use's proponent
INDEPENDENT = "reviewer-b"                        # a third name, independent of both
REASON = "reviewed the record"


# --- bracketing one CLI call ----------------------------------------------------------------------


@contextmanager
def changes(kb, source_repo, expected: set[str]):
    """Bracket exactly one CLI call. When the block ends the whole-KB tree has changed by exactly
    `expected` (KB-relative POSIX paths added, removed or rewritten; `set()` for a refusal or a read-only
    command) and the source repository is byte-identical, so a stray write anywhere under the KB root or
    under a source repository fails here."""
    before, source = m.tree(kb), source_repo.snapshot()
    yield
    changed = m.changed(before, m.tree(kb))
    assert changed == expected, f"changed {sorted(changed)}, expected {sorted(expected)}"
    assert source_repo.snapshot() == source, "the command wrote in the source repository"


def check(run, *, code: int, out: str = "", err: str = "") -> m.Run:
    """A Run in full: the exit status, the whole stdout and the whole stderr."""
    assert (run.code, run.out, run.err) == (code, out, err)
    return run


def staged(rec_id: str) -> str:
    """The staged record's path, relative to the KB root."""
    return STAGING.format(rec_id)


def full(kb, rel: str) -> str:
    """A KB-relative path as the staging commands print it (absolute under the fixture root)."""
    return str(kb.root / rel)


def edit(path: Path, **fields) -> None:
    """Fill or change a record file as its author or an editor would (a fixture edit: outside every
    bracket)."""
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    data.update(fields)
    path.write_bytes(records.dump(data))


def drop(path: Path, key: str) -> None:
    """Remove a top-level key from a record file (a fixture edit: outside every bracket)."""
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    data.pop(key)
    path.write_bytes(records.dump(data))


def key_line(kb, rec_id: str, key: str) -> int:
    """The 1-based line of a top-level key in the installed record, which the diagnostics report."""
    rel = f"{REVIEW}/{records.KINDS[rec_id.rsplit('-', 1)[0]]}/{rec_id}.yaml"
    return records.parse_record(rel, m.record_path(kb, rec_id).read_bytes()).key_line(key)


def k14_line(kb, source_repo) -> str:
    """The K14 error source-challenge-0001 leaves over F-0001's excerpt, exactly as a command prints it (SPEC §5.2.4):
    the confirmed challenge's assertion is quoted by an installed finding with no current use."""
    text = (kb.root / FINDING).read_text(encoding="utf-8")
    tag = f"<!-- verbatim: {m.TRACE}:3-3 -->"
    line = next(i for i, row in enumerate(text.splitlines(), 1) if row.strip() == tag)
    version = source_repo.blob(m.TRACE_PATH)[:12]
    return (f"K14 {FINDING}:{line}: source-challenge-0001 challenges this quoted assertion at {m.TRACE}@{version}:3-3; "
            f"edit the finding or have this use reviewed (kblam use review source-challenge-0001 F-0001 1 --by NAME "
            f"--proponent NAME). K10 is checked separately.")


def k14_line_open_use(kb, source_repo) -> str:
    """The same K14 error where checked-use-0001 is already installed open for PROPONENT: the step it names is the
    decision that approves that use, not a second `kblam use review`, which would settle nothing the open
    one does not (SPEC §5.2.4)."""
    text = (kb.root / FINDING).read_text(encoding="utf-8")
    tag = f"<!-- verbatim: {m.TRACE}:3-3 -->"
    line = next(i for i, row in enumerate(text.splitlines(), 1) if row.strip() == tag)
    version = source_repo.blob(m.TRACE_PATH)[:12]
    return (f"K14 {FINDING}:{line}: source-challenge-0001 challenges this quoted assertion at {m.TRACE}@{version}:3-3; "
            f"edit the finding or have this use reviewed (kblam review decide checked-use-0001 --status approved "
            f"--by NAME --reason TEXT --expect D; its --by must not be its proponent (checked-use-0001's proponent "
            f"is {PROPONENT})). K10 is checked separately.")


def k14_after_confirmation(kb, source_repo) -> str:
    """What `review decide --status confirmed` prints after its own line when it leaves the K14 error: a
    decision that confirms a challenge lists the findings it newly makes fail K14 (SPEC §5.2.4)."""
    return ("kblam review decide: source-challenge-0001 now affects F-0001; run kblam challenge uses source-challenge-0001 for each "
            "excerpt and the command that fixes it\n" + k14_line(kb, source_repo) + "\n"
            "kblam review decide: done, but kblam validate still fails (1 error(s) listed above, owned by "
            "other findings or records)\n")


def failed(n: int, root: str = "research-review/") -> str:
    """Validate's failure summary line, for n errors in `root` (a record's, unless a finding's is named)."""
    return f"kblam validate: {n} error(s) in {root}"


def bind_message(stale: str, now: str) -> str:
    """The K13 message a hand edit of a decided record gets (SPEC §5.2.2 Decisions)."""
    return (f"the last decision's bind is {stale} but the record's subject digest is {now}; a decided "
            f"record cannot be edited, so the record was changed by hand")


def identity_output(path: str, line: int, field: str, current: str, original: str) -> str:
    """The whole stdout the ruling expects for a hand-changed identity field (SPEC 269, 1937): the K13
    message, then validate's summary. The two values are quoted as kblam's other identity diagnostics
    quote a parsed value, so a date renders as its `YYYY-MM-DD` text."""
    return (f"K13 {path}:{line}: {field} is {current!r}, but it was allocated as {original!r} (id, "
            f"created, creator and proponent never change after allocation)\n{failed(1)}\n")


# --- the filled records and the fixture states ----------------------------------------------------


def reference(path: str) -> dict:
    """A blank file reference: `put` hashes the working file and pins it (SPEC §5.2.5)."""
    return {"path": path, "sha256": None, "repo": None, "commit": None, "blob": None, "snapshot": None}


def challenge_fields(kb, source_repo) -> dict:
    """The filled source-challenge fields: one basis entry on the source itself, with a primary provenance."""
    return {"proposition": "The printed byte equality follows from the printed byte values",
            "scope": ["MX-100 capture transcription"],
            "classification": "contradicted",
            "basis": [{**reference(source_repo.kb_path()), "locator": "row 102: printed byte values",
                       "role": "internal-inconsistency",
                       "provenance": kb.cfg.primary_provenance[0]}],
            "usable": "The printed byte values may be cited as a report.",
            "limits": "Do not infer the capture bytes from this row."}


def task_fields() -> dict:
    """The filled claim-task fields."""
    return {"question": "Does an independent measurement establish the claim?",
            "method": "Repeat the capture with the documented settings.",
            "outcomes": {"supports": "The ratio is within 0.1%.", "refutes": "The ratio differs by more.",
                         "inconclusive": "The capture is too noisy to tell."},
            "controls": ["same firmware version"],
            "stop": "Stop after three captures.",
            "expected_evidence": ["an evidence/ capture package"]}


def use_fields() -> dict:
    """The filled checked-use fields."""
    return {"disposition": "unaffected_raw_bytes",
            "reason": "The excerpt is cited only for the printed byte values."}


def new_challenge(kb, source_repo, *, by: str = CREATOR) -> Path:
    """`kblam challenge new <trace> --lines 3-3 --by NAME`: stages source-challenge-0001 and its allocation receipt and
    prints the staged path; nothing else changes."""
    with changes(kb, source_repo, {staged("source-challenge-0001"), RECEIPT.format("source-challenge-0001")}):
        run = m.kblam(kb, "challenge", "new", source_repo.kb_path(), "--lines", "3-3", "--by", by)
    check(run, code=0, out=f"{full(kb, staged('source-challenge-0001'))}\n")
    return kb.root / staged("source-challenge-0001")


def install_challenge(kb, source_repo) -> str:
    """source-challenge-0001 installed open by CREATOR on trace line 3: `challenge new` (staged record + allocation
    receipt), the author's filling (fixture edit), `put` (record, review index, registry, tree.hash; the
    staged file removed). Each command is bracketed on its own."""
    path = new_challenge(kb, source_repo)
    edit(path, **challenge_fields(kb, source_repo))
    with changes(kb, source_repo, {PATH["source-challenge"], INDEX, REGISTRY, TREE_HASH, staged("source-challenge-0001")}):
        run = m.kblam(kb, "put", str(path))
    check(run, code=0, out=f"kblam put: source-challenge-0001 -> {PATH['source-challenge']}\n")
    return "source-challenge-0001"


def install_task(kb, source_repo) -> str:
    """F-0001 (placed by hand: fixture setup) and claim-task-0001 installed open by CREATOR for PROPONENT:
    `task new`, the author's filling, `put`, each bracketed on its own."""
    kb.add("F-0001", "ratio", m.CLAIM)
    with changes(kb, source_repo, {staged("claim-task-0001"), RECEIPT.format("claim-task-0001")}):
        run = m.kblam(kb, "task", "new", "F-0001", "--kind", "replication", "--by", CREATOR,
                      "--proponent", PROPONENT)
    check(run, code=0, out=f"{full(kb, staged('claim-task-0001'))}\n")
    path = kb.root / staged("claim-task-0001")
    edit(path, **task_fields())
    with changes(kb, source_repo, {PATH["claim-task"], INDEX, REGISTRY, TREE_HASH, staged("claim-task-0001")}):
        run = m.kblam(kb, "put", str(path))
    check(run, code=0, out=f"kblam put: claim-task-0001 -> {PATH['claim-task']}\n")
    return "claim-task-0001"


def decide(kb, source_repo, rec_id: str, status: str, *, by: str, evidence=(), extra: str = ""):
    """`kblam review decide <ID> --status S --by NAME --reason TEXT --expect D [--evidence …]`: bracketed,
    with its whole output asserted -- the decision's own line, then `extra`, the lines it leaves behind."""
    digest = m.expect(kb, rec_id)
    argv = ["review", "decide", rec_id, "--status", status, "--by", by, "--reason", REASON,
            "--expect", digest]
    for item in evidence:
        argv += ["--evidence", item]
    with changes(kb, source_repo, {PATH[rec_id.rsplit("-", 1)[0]], INDEX, TREE_HASH}):
        run = m.kblam(kb, *argv)
    check(run, code=0,
          out=f"kblam review decide: {rec_id} is now {status} (subject digest {digest[:12]})\n" + extra)
    return run


def decide_refused(kb, source_repo, rec_id: str, status: str, *, by: str, err: str, evidence=()) -> m.Run:
    """A `review decide` that must be refused: exit 1, the exact stderr, stdout empty, nothing changed."""
    argv = ["review", "decide", rec_id, "--status", status, "--by", by, "--reason", REASON,
            "--expect", m.expect(kb, rec_id)]
    for item in evidence:
        argv += ["--evidence", item]
    with changes(kb, source_repo, set()):
        run = m.kblam(kb, *argv)
    return check(run, code=1, err=err)


def validate(kb, source_repo, *, code: int, out: str) -> m.Run:
    """`kblam validate`: bracketed (read-only), with the whole stdout asserted."""
    with changes(kb, source_repo, set()):
        run = m.kblam(kb, "validate")
    return check(run, code=code, out=out)


def install_drafted_use(kb, source_repo) -> tuple[str, str]:
    """F-0001 quoting trace line 3, source-challenge-0001 confirmed on it (which leaves the K14 error over F-0001) and
    checked-use-0001 drafted open by INDEPENDENT for proponent PROPONENT. Returns ("source-challenge-0001", "checked-use-0001")."""
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")           # fixture setup, outside every window
    install_challenge(kb, source_repo)
    decide(kb, source_repo, "source-challenge-0001", "confirmed", by=INDEPENDENT,
           extra=k14_after_confirmation(kb, source_repo))
    with changes(kb, source_repo, {staged("checked-use-0001"), RECEIPT.format("checked-use-0001")}):
        run = m.kblam(kb, "use", "review", "source-challenge-0001", "F-0001", "1", "--by", INDEPENDENT,
                      "--proponent", PROPONENT)
    check(run, code=0, out=f"{full(kb, staged('checked-use-0001'))}\n")
    edit(kb.root / staged("checked-use-0001"), **use_fields())
    with changes(kb, source_repo, {PATH["checked-use"], INDEX, REGISTRY, TREE_HASH, staged("checked-use-0001")}):
        run = m.kblam(kb, "put", str(kb.root / staged("checked-use-0001")))
    check(run, code=0, out=f"kblam put: checked-use-0001 -> {PATH['checked-use']}\n{k14_line_open_use(kb, source_repo)}\n"
                           "kblam put: done, but kblam validate still fails (1 error(s) listed above, "
                           "owned by other findings or records)\n")
    return "source-challenge-0001", "checked-use-0001"


def decided(kb, source_repo, kind: str) -> str:
    """A record of `kind` whose last decision was taken by the independent INDEPENDENT, validation clean:
    source-challenge-0001 confirmed, claim-task-0001 confirmed, checked-use-0001 approved."""
    if kind == "source-challenge":
        install_challenge(kb, source_repo)
        decide(kb, source_repo, "source-challenge-0001", "confirmed", by=INDEPENDENT)
        return "source-challenge-0001"
    if kind == "claim-task":
        install_task(kb, source_repo)
        decide(kb, source_repo, "claim-task-0001", "confirmed", by=INDEPENDENT, evidence=(m.PRIMARY_EVIDENCE,))
        return "claim-task-0001"
    use = install_drafted_use(kb, source_repo)[1]
    decide(kb, source_repo, use, "approved", by=INDEPENDENT)
    return use


def clean(kind: str) -> str:
    """The validate line of the fixture state `decided` leaves clean."""
    return f"kblam validate: OK ({1 if kind != 'source-challenge' else 0} findings)\n"


# --- a self-decision is refused, for each kind ----------------------------------------------------


def test_a_challenge_creator_cannot_confirm_its_own_challenge(kb, source_repo):
    """Start: source-challenge-0001 open, installed by reviewer-a on trace line 3; no findings. Command: `kblam review
    decide source-challenge-0001 --status confirmed --by reviewer-a` (its creator), with the --expect digest show
    prints. Exit 1, stderr "kblam review decide: reviewer-a is source-challenge-0001's creator; a closing decision needs
    someone else", stdout empty. Files: none. Validation afterwards: exit 0, "kblam validate: OK (0
    findings)". A5."""
    install_challenge(kb, source_repo)
    decide_refused(kb, source_repo, "source-challenge-0001", "confirmed", by=CREATOR,
                   err="kblam review decide: reviewer-a is source-challenge-0001's creator; a closing decision needs "
                       "someone else\n")
    validate(kb, source_repo, code=0, out="kblam validate: OK (0 findings)\n")


def test_a_task_creator_cannot_close_its_own_task(kb, source_repo):
    """Start: claim-task-0001 open, installed by reviewer-a for proponent researcher-a. Command: `kblam review
    decide claim-task-0001 --status confirmed --by reviewer-a` (its creator), citing the evidence document. Exit
    1, stderr "kblam review decide: reviewer-a is claim-task-0001's creator; a closing decision needs someone
    else", stdout empty. Files: none. Validation afterwards: exit 0, the task still open and printed
    pending. A5."""
    install_task(kb, source_repo)
    decide_refused(kb, source_repo, "claim-task-0001", "confirmed", by=CREATOR, evidence=(m.PRIMARY_EVIDENCE,),
                   err="kblam review decide: reviewer-a is claim-task-0001's creator; a closing decision needs "
                       "someone else\n")
    validate(kb, source_repo, code=0,
             out="claim-task-0001 open replication of F-0001: Does an independent measurement establish the "
                 "claim?\n" + "kblam validate: OK (1 findings); 1 pending task(s)\n")


def test_a_task_proponent_cannot_close_its_own_task(kb, source_repo):
    """Start: claim-task-0001 open, installed by reviewer-a for proponent researcher-a. Command: `kblam review
    decide claim-task-0001 --status confirmed --by researcher-a` (its proponent, not its creator), citing the
    evidence document. Exit 1, stderr "kblam review decide: researcher-a is claim-task-0001's proponent; a closing
    decision needs someone else", stdout empty. Files: none. Validation afterwards: exit 0, the task still
    pending. A5."""
    install_task(kb, source_repo)
    decide_refused(kb, source_repo, "claim-task-0001", "confirmed", by=PROPONENT, evidence=(m.PRIMARY_EVIDENCE,),
                   err="kblam review decide: researcher-a is claim-task-0001's proponent; a closing decision needs "
                       "someone else\n")
    validate(kb, source_repo, code=0,
             out="claim-task-0001 open replication of F-0001: Does an independent measurement establish the "
                 "claim?\n" + "kblam validate: OK (1 findings); 1 pending task(s)\n")


def test_a_use_proponent_cannot_approve_its_own_use(kb, source_repo):
    """Start: source-challenge-0001 confirmed, F-0001 quotes its assertion (a K14 error), checked-use-0001 open: drafted by
    reviewer-b, proponent researcher-a. Command: `kblam review decide checked-use-0001 --status approved --by
    researcher-a` (its proponent). Exit 1, stderr "kblam review decide: researcher-a is checked-use-0001's
    proponent; a closing decision needs someone else", stdout empty. Files: none. Validation afterwards:
    exit 1, the K14 error over F-0001 (a merely drafted use covers nothing). A5."""
    install_drafted_use(kb, source_repo)
    decide_refused(kb, source_repo, "checked-use-0001", "approved", by=PROPONENT,
                   err="kblam review decide: researcher-a is checked-use-0001's proponent; a closing decision needs "
                       "someone else\n")
    validate(kb, source_repo, code=1,
             out=f"{k14_line_open_use(kb, source_repo)}\n{failed(1, 'findings/')}\n")


def test_a_uses_creator_may_approve_it(kb, source_repo):
    """Start: source-challenge-0001 confirmed, F-0001 quotes its assertion (a K14 error), checked-use-0001 open, drafted by
    reviewer-b (its creator) for proponent researcher-a. Command: `kblam review decide checked-use-0001 --status
    approved --by reviewer-b`. Exit 0, "kblam review decide: checked-use-0001 is now approved (subject digest …)".
    Files: the use record, the review index (its Status cell) and tree.hash. Validation afterwards: exit
    0, the current use resolves the K14 overlap. A5: a use's creator may approve it, its proponent may
    not."""
    install_drafted_use(kb, source_repo)
    decide(kb, source_repo, "checked-use-0001", "approved", by=INDEPENDENT)
    validate(kb, source_repo, code=0, out="kblam validate: OK (1 findings)\n")


# --- a stored self-decision is a K13 error, for each kind -----------------------------------------


@pytest.mark.parametrize(("kind", "actor", "role"), [
    ("source-challenge", CREATOR, "creator"), ("claim-task", CREATOR, "creator"), ("claim-task", PROPONENT, "proponent"),
    ("checked-use", PROPONENT, "proponent"),
])
def test_a_stored_self_decision_is_a_k13_error(kb, source_repo, kind, actor, role):
    """Start: a decided record of `kind` (source-challenge-0001 confirmed; claim-task-0001 confirmed; checked-use-0001 approved), its
    validation clean, decided by the independent reviewer-b. Hand edit (fixture setup, outside every
    window): the record's decision names the record's own creator or proponent instead. Command: `kblam
    validate`. Exit 1, "K13 <record>:<decisions line>: decisions[0]: <name> is <ID>'s <role>; a closing
    decision needs someone else", and no other error. Files: none (validate is read-only). A5: a stored
    decision that needed independence and lacks it is a K13 error."""
    rec_id = decided(kb, source_repo, kind)
    validate(kb, source_repo, code=0, out=clean(kind))
    data = yaml_rt().load(m.record_path(kb, rec_id).read_bytes().decode("utf-8"))
    data["decisions"][0]["by"] = actor
    m.record_path(kb, rec_id).write_bytes(records.dump(data))
    validate(kb, source_repo, code=1,
             out=f"K13 {PATH[kind]}:{key_line(kb, rec_id, 'decisions')}: decisions[0]: {actor} is "
                 f"{rec_id}'s {role}; a closing decision needs someone else\n{failed(1)}\n")


# --- a confirmed challenge is never reopened ------------------------------------------------------


def test_a_confirmed_challenge_is_never_reopened(kb, source_repo):
    """Start: source-challenge-0001 confirmed by the independent reviewer-b. Command: `kblam review decide source-challenge-0001
    --status open --by reviewer-b`. Exit 1, stderr "kblam review decide: a confirmed challenge is never
    reopened; retire it with --status stale and file a new challenge". Files: none. Validation
    afterwards: exit 0. The retire route a confirmed challenge does have is then run: `--status stale`
    exits 0, changes the record, the review index and tree.hash, and validation stays clean. A5."""
    install_challenge(kb, source_repo)
    decide(kb, source_repo, "source-challenge-0001", "confirmed", by=INDEPENDENT)
    decide_refused(kb, source_repo, "source-challenge-0001", "open", by=INDEPENDENT,
                   err="kblam review decide: a confirmed challenge is never reopened; retire it with "
                       "--status stale and file a new challenge\n")
    validate(kb, source_repo, code=0, out="kblam validate: OK (0 findings)\n")
    decide(kb, source_repo, "source-challenge-0001", "stale", by=INDEPENDENT)
    validate(kb, source_repo, code=0, out="kblam validate: OK (0 findings)\n")


# --- identity at put ------------------------------------------------------------------------------


def test_a_put_that_keeps_the_identity_fields_installs_the_record(kb, source_repo):
    """Start: source-challenge-0001 staged and filled by reviewer-a with its allocation receipt, no records installed.
    Command: `kblam put .kblam/review-staging/source-challenge-0001.yaml`, the staged identity fields exactly as the
    receipt wrote them. Exit 0, "kblam put: source-challenge-0001 -> research-review/challenges/source-challenge-0001.yaml", stderr
    empty. Files: the record, the review index, the registry (created from the records present) and
    tree.hash; the staged file is removed. Validation afterwards: exit 0. A5: a record is installed with
    the identity it was allocated, and exactly those files are written."""
    path = new_challenge(kb, source_repo)
    edit(path, **challenge_fields(kb, source_repo))
    with changes(kb, source_repo, {PATH["source-challenge"], INDEX, REGISTRY, TREE_HASH, staged("source-challenge-0001")}):
        run = m.kblam(kb, "put", str(path))
    check(run, code=0, out=f"kblam put: source-challenge-0001 -> {PATH['source-challenge']}\n")
    validate(kb, source_repo, code=0, out="kblam validate: OK (0 findings)\n")


def test_a_staged_id_that_differs_from_the_file_name_is_refused(kb, source_repo):
    """Start: source-challenge-0001 staged and filled by reviewer-a with its allocation receipt, no records installed.
    Hand edit (fixture setup): the staged `id` becomes source-challenge-0009. Command: `kblam put
    .kblam/review-staging/source-challenge-0001.yaml`. Exit 1, stderr "kblam put: .kblam/review-staging/source-challenge-0001.yaml:
    id is 'source-challenge-0009', but the file name's ID is source-challenge-0001; the ID never changes. Set id back to source-challenge-0001 in
    .kblam/review-staging/source-challenge-0001.yaml and put it again. Load the kblam-write skill for how to fix this.",
    stdout empty. Files: none (the staged file stays where it is). Validation afterwards: exit 0. A5."""
    path = new_challenge(kb, source_repo)
    edit(path, **{**challenge_fields(kb, source_repo), "id": "source-challenge-0009"})
    with changes(kb, source_repo, set()):
        run = m.kblam(kb, "put", str(path))
    check(run, code=1,
          err="kblam put: .kblam/review-staging/source-challenge-0001.yaml: id is 'source-challenge-0009', but the file name's ID is "
              "source-challenge-0001; the ID never changes. Set id back to source-challenge-0001 in .kblam/review-staging/source-challenge-0001.yaml "
              "and put it again. Load the kblam-write skill for how to fix this.\n")
    assert not m.record_path(kb, "source-challenge-0001").exists()
    validate(kb, source_repo, code=0, out="kblam validate: OK (0 findings)\n")


@pytest.mark.parametrize(("field", "value", "shown", "receipt"), [
    ("created", "2026-09-27", "'2026-09-27'", "'2026-09-28'"),
    ("creator", INDEPENDENT, "'reviewer-b'", "'reviewer-a'"),
])
def test_a_first_put_refuses_a_changed_created_or_creator(kb, source_repo, field, value, shown, receipt):
    """Start: source-challenge-0001 staged by reviewer-a with the allocation receipt kblam wrote. Hand edit (fixture
    setup): the staged `created` (a day earlier) or `creator` (reviewer-b) no longer matches the receipt.
    Command: `kblam put .kblam/review-staging/source-challenge-0001.yaml`. Exit 1, stderr "kblam put: source-challenge-0001: <field>
    is <the staged value>, but its allocation receipt has <the receipt's>. The ID, the created date, the
    creator, the proponent and the bindings are set at allocation: restore <field> and put it again, or
    start again with a new record. Load the kblam-write skill for how to fix this.", stdout empty. Files:
    none. Validation afterwards: exit 0. A5: the identity fields are fixed at allocation."""
    path = new_challenge(kb, source_repo)
    edit(path, **{**challenge_fields(kb, source_repo), field: value})
    with changes(kb, source_repo, set()):
        run = m.kblam(kb, "put", str(path))
    check(run, code=1,
          err=f"kblam put: source-challenge-0001: {field} is {shown}, but its allocation receipt has {receipt}. The ID, "
              f"the created date, the creator, the proponent and the bindings are set at allocation: "
              f"restore {field} and put it again, or start again with a new record. Load the kblam-write "
              f"skill for how to fix this.\n")
    validate(kb, source_repo, code=0, out="kblam validate: OK (0 findings)\n")


def test_a_first_put_refuses_a_changed_proponent(kb, source_repo):
    """Start: F-0001 placed by hand, claim-task-0001 staged for proponent researcher-a with its allocation
    receipt. Hand edit (fixture setup): the staged `proponent` becomes reviewer-c. Command: `kblam put
    .kblam/review-staging/claim-task-0001.yaml`. Exit 1, stderr "kblam put: claim-task-0001: proponent is 'reviewer-c',
    but its allocation receipt has 'researcher-a'. The ID, the created date, the creator, the proponent
    and the bindings are set at allocation: restore proponent and put it again, or start again with a new
    record. Load the kblam-write skill for how to fix this.", stdout empty. Files: none. Validation
    afterwards: exit 0. A5: who stands behind the claim is fixed when the task is bound."""
    kb.add("F-0001", "ratio", m.CLAIM)                                # fixture setup
    with changes(kb, source_repo, {staged("claim-task-0001"), RECEIPT.format("claim-task-0001")}):
        run = m.kblam(kb, "task", "new", "F-0001", "--kind", "replication", "--by", CREATOR,
                      "--proponent", PROPONENT)
    check(run, code=0, out=f"{full(kb, staged('claim-task-0001'))}\n")
    edit(kb.root / staged("claim-task-0001"), **{**task_fields(), "proponent": "reviewer-c"})
    with changes(kb, source_repo, set()):
        run = m.kblam(kb, "put", str(kb.root / staged("claim-task-0001")))
    check(run, code=1,
          err="kblam put: claim-task-0001: proponent is 'reviewer-c', but its allocation receipt has "
              "'researcher-a'. The ID, the created date, the creator, the proponent and the bindings are "
              "set at allocation: restore proponent and put it again, or start again with a new record. "
              "Load the kblam-write skill for how to fix this.\n")
    validate(kb, source_repo, code=0, out="kblam validate: OK (1 findings)\n")


def test_a_task_edit_refuses_a_changed_proponent(kb, source_repo):
    """Start: claim-task-0001 installed open by reviewer-a for proponent researcher-a. Command: `kblam task edit
    claim-task-0001` (exit 0, its stdout the staged copy's path; it writes the copy and its edit-base receipt),
    then the copy's `proponent` is set to reviewer-c (fixture edit) and `kblam put` runs on it. Exit 1,
    stderr "kblam put: claim-task-0001: proponent is not a free field, so it must be put as installed. Only
    question, method, outcomes, controls, stop, expected_evidence change through an edit; run kblam task
    edit claim-task-0001 again. Load the kblam-write skill for how to fix this.", stdout empty. Files: none (the
    edit-base copy stays staged). Validation afterwards: exit 0. A5: an edit changes the free fields
    only."""
    install_task(kb, source_repo)
    with changes(kb, source_repo, {staged("claim-task-0001"), EDIT_BASE.format("claim-task-0001")}):
        run = m.kblam(kb, "task", "edit", "claim-task-0001")
    check(run, code=0, out=f"{full(kb, staged('claim-task-0001'))}\n")
    edit(kb.root / staged("claim-task-0001"), proponent="reviewer-c")
    with changes(kb, source_repo, set()):
        run = m.kblam(kb, "put", str(kb.root / staged("claim-task-0001")))
    check(run, code=1,
          err="kblam put: claim-task-0001: proponent is not a free field, so it must be put as installed. Only "
              "question, method, outcomes, controls, stop, expected_evidence change through an edit; run "
              "kblam task edit claim-task-0001 again. Load the kblam-write skill for how to fix this.\n")
    validate(kb, source_repo, code=0,
             out="claim-task-0001 open replication of F-0001: Does an independent measurement establish the "
                 "claim?\n" + "kblam validate: OK (1 findings); 1 pending task(s)\n")


def test_a_missing_proponent_is_refused_at_put(kb, source_repo):
    """Start: F-0001 placed by hand, claim-task-0001 staged for proponent researcher-a with its allocation
    receipt. Hand edit (fixture setup): the staged `proponent` key is removed. Command: `kblam put
    .kblam/review-staging/claim-task-0001.yaml`. Exit 1, stderr "kblam put: claim-task-0001: proponent is None, but its
    allocation receipt has 'researcher-a'. The ID, the created date, the creator, the proponent and the
    bindings are set at allocation: restore proponent and put it again, or start again with a new record.
    Load the kblam-write skill for how to fix this.", stdout empty (a first put matches the receipt
    before it reads the field table, so a record carrying no proponent at all is refused there). Files:
    none. Validation afterwards: exit 0. A5."""
    kb.add("F-0001", "ratio", m.CLAIM)                                # fixture setup
    with changes(kb, source_repo, {staged("claim-task-0001"), RECEIPT.format("claim-task-0001")}):
        run = m.kblam(kb, "task", "new", "F-0001", "--kind", "replication", "--by", CREATOR,
                      "--proponent", PROPONENT)
    check(run, code=0, out=f"{full(kb, staged('claim-task-0001'))}\n")
    drop(kb.root / staged("claim-task-0001"), "proponent")
    with changes(kb, source_repo, set()):
        run = m.kblam(kb, "put", str(kb.root / staged("claim-task-0001")))
    check(run, code=1,
          err="kblam put: claim-task-0001: proponent is None, but its allocation receipt has 'researcher-a'. The "
              "ID, the created date, the creator, the proponent and the bindings are set at allocation: "
              "restore proponent and put it again, or start again with a new record. Load the kblam-write "
              "skill for how to fix this.\n")
    validate(kb, source_repo, code=0, out="kblam validate: OK (1 findings)\n")


def test_a_proponent_outside_the_name_pattern_is_refused(kb, source_repo):
    """Start: F-0001 placed by hand, no records. Command: `kblam task new F-0001 --kind replication --by
    reviewer-a --proponent 'not a name'`. Exit 1, stderr "kblam task new: --proponent 'not a name' is not
    a name: letters, digits, '.', '_', '@' and '-', starting with a letter or a digit", stdout empty.
    Files: none (nothing is staged and no receipt is written). Validation afterwards: exit 0. A5
    (§5.2.2 Values)."""
    kb.add("F-0001", "ratio", m.CLAIM)                                # fixture setup
    with changes(kb, source_repo, set()):
        run = m.kblam(kb, "task", "new", "F-0001", "--kind", "replication", "--by", CREATOR,
                      "--proponent", "not a name")
    check(run, code=1,
          err="kblam task new: --proponent 'not a name' is not a name: letters, digits, '.', '_', '@' and "
              "'-', starting with a letter or a digit\n")
    validate(kb, source_repo, code=0, out="kblam validate: OK (1 findings)\n")


# --- identity by hand is a K13 error --------------------------------------------------------------


def test_a_hand_changed_id_is_a_k13_error(kb, source_repo):
    """Start: source-challenge-0001 installed open by reviewer-a, in a KB that is no git repository. Hand edit (fixture
    setup): the record's `id` becomes source-challenge-0009 while its file name does not. Command: `kblam validate`.
    Exit 1, "K13 research-review/challenges/source-challenge-0001.yaml:2: id: 'source-challenge-0009' does not match the file name's
    ID (source-challenge-0001); git's last commit does not hold a file at research-review/challenges/source-challenge-0001.yaml, and
    records are never renamed, so leave it as it is and tell the user" and no other error. Files: none
    (validate is read-only). A5: a record's ID matches its file (§5.2.4 K13)."""
    install_challenge(kb, source_repo)
    edit(m.record_path(kb, "source-challenge-0001"), id="source-challenge-0009")
    validate(kb, source_repo, code=1,
             out=f"K13 {PATH['source-challenge']}:{key_line(kb, 'source-challenge-0001', 'id')}: id: 'source-challenge-0009' does not match the file "
                 f"name's ID (source-challenge-0001); git's last commit does not hold a file at {PATH['source-challenge']}, and "
                 f"records are never renamed, so leave it as it is and tell the user\n{failed(1)}\n")


def test_a_hand_changed_proponent_on_a_decided_task_is_a_k13_error(kb, source_repo):
    """Start: claim-task-0001 confirmed by the independent reviewer-b, its validation clean. Hand edit (fixture
    setup): the record's `proponent` becomes researcher-c. Command: `kblam validate`. Exit 1, "K13
    research-review/tasks/claim-task-0001.yaml:<decisions line>: the last decision's bind is <the digest at the
    decision> but the record's subject digest is <the digest now>; a decided record cannot be edited, so
    the record was changed by hand", plus the K13 identity error at the proponent line. Files: none.
    A5: a proponent is fixed at allocation and part of a task's subject, so a hand change breaks both
    identity and the decision's bind."""
    install_task(kb, source_repo)
    decide(kb, source_repo, "claim-task-0001", "confirmed", by=INDEPENDENT, evidence=(m.PRIMARY_EVIDENCE,))
    validate(kb, source_repo, code=0, out=clean("claim-task"))
    stale = m.expect(kb, "claim-task-0001")
    edit(m.record_path(kb, "claim-task-0001"), proponent="researcher-c")
    now = m.expect(kb, "claim-task-0001")
    identity = identity_output(PATH["claim-task"], key_line(kb, "claim-task-0001", "proponent"), "proponent",
                               "researcher-c", PROPONENT).splitlines()[0]
    validate(kb, source_repo, code=1,
             out=f"{identity}\nK13 {PATH['claim-task']}:{key_line(kb, 'claim-task-0001', 'decisions')}: "
                 f"{bind_message(stale, now)}\n{failed(2)}\n")


@pytest.mark.parametrize(("field", "value", "original"), [
    pytest.param("created", "2026-09-27", "2026-09-28", id="created"),
    pytest.param("creator", "reviewer-c", CREATOR, id="creator"),
])
def test_a_hand_changed_created_or_creator_is_a_k13_error(kb, source_repo, field, value, original):
    """Start: claim-task-0001 confirmed by the independent reviewer-b, its validation clean. Hand edit (fixture
    setup): the record's `created` (a day earlier) or `creator` (reviewer-c) changes, against SPEC §5.2.2
    line 269 (id, created, creator and proponent never change after allocation). Command: `kblam
    validate`. The lead's ruling (SPEC 269, 1937) fixes the expected diagnostic as "K13
    research-review/tasks/claim-task-0001.yaml:<the field's line>: <field> is '<the value now>', but it was
    allocated as '<the value at allocation>' (id, created, creator and proponent never change after
    allocation)", exit 1, stderr empty. Neither field is in a task's
    subject digest (SPEC 327-335), so the last decision's bind still equals the record's subject digest --
    asserted here, which is why the expected diagnostic is an identity error and not a bind mismatch.
    Files: none. A5."""
    install_task(kb, source_repo)
    decide(kb, source_repo, "claim-task-0001", "confirmed", by=INDEPENDENT, evidence=(m.PRIMARY_EVIDENCE,))
    validate(kb, source_repo, code=0, out=clean("claim-task"))
    stale = m.expect(kb, "claim-task-0001")
    edit(m.record_path(kb, "claim-task-0001"), **{field: value})
    now = m.expect(kb, "claim-task-0001")
    record = yaml_rt().load(m.record_path(kb, "claim-task-0001").read_bytes().decode("utf-8"))
    assert now == stale and record["decisions"][-1]["bind"] == now, "the subject digest, and the bind, " \
                                                                   "must not move"
    with changes(kb, source_repo, set()):
        run = m.kblam(kb, "validate")
    check(run, code=1, out=identity_output(PATH["claim-task"], key_line(kb, "claim-task-0001", field), field, value,
                                           original))


def test_a_hand_installed_record_without_a_proponent_is_an_error(kb, source_repo):
    """Start: claim-task-0001 installed open on F-0001, its `proponent` key removed by hand (fixture setup).
    Command: `kblam validate`. Exit 1, stderr empty, "K13 research-review/tasks/claim-task-0001.yaml: missing key
    'proponent'" and no other error, then the summary: proponent is a common-format field (§5.2.2), so
    its schema errors are K13's. Files: none (validate is read-only). A5: a task or a use needs a
    proponent (§5.2.3 field tables)."""
    install_task(kb, source_repo)
    drop(m.record_path(kb, "claim-task-0001"), "proponent")
    validate(kb, source_repo, code=1, out=f"K13 {PATH['claim-task']}: missing key 'proponent'\n{failed(1)}\n")


# --- a hand edit of a decided record --------------------------------------------------------------


@pytest.mark.parametrize(("kind", "field"), [
    ("source-challenge", "proposition"), ("claim-task", "method"), ("checked-use", "reason"),
])
def test_a_hand_edit_of_a_decided_record_is_a_k13_bind_mismatch(kb, source_repo, kind, field):
    """Start: a decided, effective record of `kind` whose validation is clean (source-challenge-0001 confirmed; claim-task-0001
    confirmed; checked-use-0001 approved). Hand edit (fixture setup): one free field changes -- a field no review
    index cell shows, so the index stays byte-identical. Command: `kblam validate`. Exit 1, "K13
    <record>:<decisions line>: the last decision's bind is <the digest at the decision> but the record's
    subject digest is <the digest now>; a decided record cannot be edited, so the record was changed by
    hand", and no other error. Files: none. A5: a decision goes stale when any input kblam hashed for it
    changes."""
    rec_id = decided(kb, source_repo, kind)
    validate(kb, source_repo, code=0, out=clean(kind))
    stale = m.expect(kb, rec_id)
    edit(m.record_path(kb, rec_id), **{field: "A narrower revision, written by hand"})
    now = m.expect(kb, rec_id)
    validate(kb, source_repo, code=1,
             out=f"K13 {PATH[kind]}:{key_line(kb, rec_id, 'decisions')}: {bind_message(stale, now)}\n"
                 f"{failed(1)}\n")
