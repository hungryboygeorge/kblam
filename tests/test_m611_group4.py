"""SPEC §12 M6.11 acceptance tests, test group 4: put blocking (A2, A4).

Every CLI invocation in this module is bracketed by `cli()`: the whole KB (`m611_helpers.tree`) and
the nested source repository (`source_repo.snapshot()`) are snapshotted before the call and compared
after it, so a write the command did not declare — a stray file, a source byte, a file left behind —
fails the test instead of folding into the next command's baseline. The setup commands run through
the same bracket, and so does every read-only call.

Each test states its start state, the command and actor, the exit status, the diagnostics (code and
key text), the files changed and the validation result afterwards, and names the acceptance
criterion it demonstrates.

A refused record put and a read-only command change nothing. A refused finding put may initialize the
Jev pair cache under `.kblam/` (kblam's own cache, outside both roots) when its Checker is
constructed; `findings/`, the review root, the registry and `tree.hash` stay unchanged.
Offline and deterministic: `frozen_today` pins `created` and a decision's `date`, and the fixture KB
enables no Jev verdict, so no request leaves the machine.
"""

from __future__ import annotations

import contextlib
import hashlib
from pathlib import Path

import m611_helpers as m
from kblam import records
from kblam.finding import yaml_rt
from kblam.hook import SKILL_POINTER

frozen_today = m.frozen_today            # `created` and a decision's `date` are TODAY: the bytes are fixed

REVIEW = "research-review"
REVIEW_STAGING = ".kblam/review-staging"     # the authors' staging for records
RECEIPTS = ".kblam/review-receipts"
FINDINGS_STAGING = ".kblam/staging"          # `kblam new` / `kblam edit` stage findings here
REGISTRY = ".kblam/review-ids"
TREE_HASH = ".kblam/tree.hash"
PAIR_CACHE = ".kblam/pairs.sqlite"
FINDING_PATH = "findings/calibration/F-0001-ratio.md"     # F-0001 as `quoting_finding` installs it
SENSOR_PATH = "findings/calibration/F-0001-sensor.md"     # F-0001 as `kb.add(..., "sensor", ...)` does
# kblam's caches and journals under .kblam/, written by any put that runs the Jev check (the pair
# cache, the check and review journals). They are not one of the two roots the §5.2.6 rules cover.
JEV_STATE = {PAIR_CACHE, ".kblam/checks.jsonl", ".kblam/review.jsonl"}
JEV_NOTE = ("kblam put: [jev.thresholds] enables no Jev verdict, so Jev was not asked "
            "(quantities were compared)")


# --- driving the CLI, one bracketed call at a time ------------------------------------------------


@contextlib.contextmanager
def cli(kb, source_repo, expected, *argv):
    """One CLI invocation, bracketed.

    The KB tree and the source repository are snapshotted before the call and compared when the
    `with` body exits, so a write injected by the command — or immediately after it — fails the
    exit. `expected` is the exact set of KB paths the command may add, remove or rewrite; the source
    repository must come back byte-identical, HEAD, index and refs included (Acceptance 1)."""
    command = " ".join(str(arg) for arg in argv)
    before = m.tree(kb)
    source = source_repo.snapshot()
    run = m.kblam(kb, *argv)
    yield run
    after = m.tree(kb)
    changed = m.changed(before, after)
    assert changed == set(expected), (
        f"`kblam {command}` changed {sorted(changed)}, expected {sorted(set(expected))}")
    assert source_repo.snapshot() == source, f"`kblam {command}` changed the source repository"


def call(kb, source_repo, changed, *argv, code=0, out=None, err=""):
    """`cli()`, with the whole Run asserted: exit code, exact stdout and exact stderr."""
    with cli(kb, source_repo, changed, *argv) as run:
        assert run.code == code, f"exit {run.code}, expected {code}:\n{run.out}{run.err}"
        if out is not None:
            assert run.out == out, f"stdout was {run.out!r}"
        assert run.err == err, f"stderr was {run.err!r}"
    return run


def text(*lines: str) -> str:
    """Output lines as kblam prints them: one trailing newline each."""
    return "".join(f"{line}\n" for line in lines)


# --- fixture setup helpers (outside every bracket: they are not CLI calls) ------------------------


def rel(kb, path) -> str:
    """`path` as `m.tree` names it: relative to the KB root, POSIX."""
    return Path(path).relative_to(kb.root).as_posix()


def line_with(path: Path, needle: str) -> int:
    """The 1-based line of `path` whose text holds `needle`: where kblam reports an issue at a line."""
    lines = path.read_text(encoding="utf-8").split("\n")
    return next(i for i, text_ in enumerate(lines, start=1) if needle in text_)


def excerpt_line(path: Path) -> int:
    """The line kblam reports F-0001's excerpt of the trace's lines 3-3 at: the `<!-- verbatim: … -->`
    tag that opens it, which is where the excerpt match begins (§5.2.4 K14 and its neighbours). `path`
    is the staged or the installed finding."""
    return line_with(path, f"<!-- verbatim: {m.TRACE}:3-3 -->")


def staged_key_line(staged: Path, key: str) -> int:
    """The line a record issue for a staged record is reported at: its top-level `key`. A refused put
    names the staged file's own display path, not the path it would be installed to (SPEC §5.2.2)."""
    return records.parse_record(f"{REVIEW_STAGING}/{staged.name}", staged.read_bytes()).key_line(key)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reading(kb, relative: str) -> str:
    """The text of a file of the KB, as the fixture left it."""
    return (kb.root / relative).read_text(encoding="utf-8")


def sha_of(kb, relative: str) -> str:
    return sha256_hex((kb.root / relative).read_bytes())


def edited(finding_text: str, extra: str = "Another detail, added after the excerpt.") -> str:
    """A body-only edit of a finding: one prose paragraph appended, so the frontmatter, the claim and
    every verbatim excerpt stay byte-identical."""
    return f"{finding_text}\n{extra}\n"


def fill(path: Path, **fields) -> dict:
    """Fill a staged record's blank fields, as its author does, and write it back with LF endings."""
    data = yaml_rt().load(path.read_bytes().decode("utf-8"))
    data.update(fields)
    path.write_bytes(records.dump(data))
    return data


def sc_fields() -> dict:
    """The blank source-challenge fields the author fills before the first put (SPEC §5.2.5): a proposition, a
    scope, `contradicted`, one basis entry on the source itself, a usable remainder and limits."""
    return {"proposition": "The printed byte equality follows from the printed byte values",
            "scope": ["MX-100 capture transcription"],
            "classification": "contradicted",
            "basis": [{"path": m.TRACE, "sha256": None, "repo": None, "commit": None, "blob": None,
                       "snapshot": None, "locator": "row 102: printed byte values",
                       "role": "internal-inconsistency", "provenance": "observed"}],
            "usable": "The printed byte values may be cited as a report.",
            "limits": "Do not infer the capture bytes from this row."}


def ct_fields() -> dict:
    """The blank claim-task fields the author fills before the first put."""
    return {"question": "Does an independent measurement establish the claim?",
            "method": "Repeat the capture with the documented settings.",
            "outcomes": {"supports": "The ratio is within 0.1%.",
                         "refutes": "The ratio differs by more.",
                         "inconclusive": "The capture is too noisy to tell."},
            "controls": ["same firmware version"],
            "stop": "Stop after three captures.",
            "expected_evidence": ["an evidence/ capture package"]}


def cu_fields() -> dict:
    """The blank checked-use fields the reviewer fills before the first put."""
    return {"disposition": "unaffected_raw_bytes",
            "reason": "The excerpt is cited only for the printed byte values."}


# --- one bracketed call per command ---------------------------------------------------------------


def new_finding(kb, source_repo, name: str, *, topic: str = "calibration",
                title: str = "Sensor curve types") -> Path:
    """`kblam new <topic> <title>`, which stages a finding under the slug it derives and prints it."""
    staged = kb.root / FINDINGS_STAGING / name
    call(kb, source_repo, {rel(kb, staged)}, "new", topic, title, out=f"{staged}\n")
    return staged


def edit_finding(kb, source_repo, finding_id: str, slug: str) -> Path:
    """`kblam edit <finding-id>`: stage a copy of the installed finding, with the edit-base receipt
    whose bytes the put checks the installed finding against."""
    staged = kb.root / FINDINGS_STAGING / f"{finding_id}-{slug}.md"
    call(kb, source_repo, {rel(kb, staged), f"{FINDINGS_STAGING}/{finding_id}.edit-base.json"},
         "edit", finding_id, out=f"{staged}\n")
    return staged


def challenge_new(kb, source_repo, *, by: str, rec_id: str = "source-challenge-0001",
                  lines: str = "3-3") -> Path:
    """`kblam challenge new <trace> --lines A-B --by NAME`: stage the source challenge, write its
    receipt."""
    staged = kb.root / REVIEW_STAGING / f"{rec_id}.yaml"
    call(kb, source_repo, {rel(kb, staged), f"{RECEIPTS}/{rec_id}.json"},
         "challenge", "new", m.TRACE, "--lines", lines, "--by", by, out=f"{staged}\n")
    return staged


def task_new(kb, source_repo, finding_id: str, *, by: str, proponent: str,
             rec_id: str = "claim-task-0001") -> Path:
    """`kblam task new <finding> --kind replication --by NAME --proponent NAME`: stage the claim task."""
    staged = kb.root / REVIEW_STAGING / f"{rec_id}.yaml"
    call(kb, source_repo, {rel(kb, staged), f"{RECEIPTS}/{rec_id}.json"},
         "task", "new", finding_id, "--kind", "replication", "--by", by, "--proponent", proponent,
         out=f"{staged}\n")
    return staged


def use_review(kb, source_repo, sc_id: str, finding_id: str, *, by: str, proponent: str,
               rec_id: str = "checked-use-0001", ordinal: int = 1) -> Path:
    """`kblam use review <challenge> <finding> <ordinal> --by NAME --proponent NAME`: stage the checked
    use."""
    staged = kb.root / REVIEW_STAGING / f"{rec_id}.yaml"
    call(kb, source_repo, {rel(kb, staged), f"{RECEIPTS}/{rec_id}.json"},
         "use", "review", sc_id, finding_id, str(ordinal), "--by", by, "--proponent", proponent,
         out=f"{staged}\n")
    return staged


def put_record(kb, source_repo, staged: Path, rec_id: str, *, out: str, err: str = "") -> None:
    """The first put of a staged record: the record is installed, the review index regenerated, the
    ID registered, `tree.hash` recorded and the staged file consumed (§5.2.6)."""
    folder = records.KINDS[rec_id.rsplit("-", 1)[0]]
    call(kb, source_repo,
         {f"{REVIEW}/{folder}/{rec_id}.yaml", f"{REVIEW}/INDEX.md", REGISTRY, TREE_HASH,
          rel(kb, staged)},
         "put", str(staged), out=out, err=err)


def decide(kb, source_repo, rec_id: str, status: str, *, by: str, changed, out: str,
           reason: str = "reviewed the record", code: int = 0, err: str = "") -> m.Run:
    """`kblam review decide <ID> --status S --by NAME --reason TEXT --expect D`, with `--expect`
    read from the installed record here."""
    return call(kb, source_repo, changed,
                "review", "decide", rec_id, "--status", status, "--by", by, "--reason", reason,
                "--expect", m.expect(kb, rec_id), code=code, out=out, err=err)


def validate(kb, source_repo, *, code: int, out: str, err: str = "") -> m.Run:
    """`kblam validate`, read-only: it never changes a file of the KB or of the source."""
    return call(kb, source_repo, set(), "validate", code=code, out=out, err=err)


# --- the diagnostics under test -------------------------------------------------------------------


def same_bytes_message(source_repo, ordinal: int = 1) -> str:
    """The K14 "same bytes" diagnostic (SPEC §5.2.4) for F-0001's excerpt `ordinal` of the trace's
    lines 3-3, which source-challenge-0001 challenges at its pinned blob."""
    version = source_repo.blob(m.TRACE_PATH)[:12]
    return (f"source-challenge-0001 challenges this quoted assertion at {m.TRACE}@{version}:3-3; edit the finding or "
            f"have this use reviewed (kblam use review source-challenge-0001 F-0001 {ordinal} --by NAME --proponent "
            f"NAME). K10 is checked separately.")


def same_bytes_open_use(source_repo, ordinal: int = 1, rec_id: str = "checked-use-0001") -> str:
    """The same-bytes diagnostic where `rec_id` is already installed open for researcher-a: the step it
    names is the decision that approves that use, not a second `kblam use review`, which would settle
    nothing the open one does not (k14.USE_DECIDE)."""
    version = source_repo.blob(m.TRACE_PATH)[:12]
    return (f"source-challenge-0001 challenges this quoted assertion at {m.TRACE}@{version}:3-3; edit the finding or "
            f"have this use reviewed (kblam review decide {rec_id} --status approved --by NAME --reason "
            f"TEXT --expect D; its --by must not be its proponent ({rec_id}'s proponent is "
            f"researcher-a)). K10 is checked separately.")


def stale_line(rec_id: str) -> str:
    """The line a finding put prints for a record it made stale (SPEC §5.2.4, §5.2.5)."""
    return (f"kblam put: {rec_id} is now stale (this put changed F-0001, which it is bound to); a "
            f"reviewer rechecks it and runs kblam review rebind {rec_id} --by NAME --reason TEXT "
            f"--expect D. kblam validate fails until then")


def stale_use_warning(now: str, bound: str) -> str:
    """The K13 warning for a use whose finding's bytes changed under it (SPEC §5.2.3 "A use is
    current"), as a put and validate print it."""
    return (f"K13 warning {REVIEW}/uses/checked-use-0001.yaml: F-0001's file bytes changed since this use was "
            f"bound ({now} is not {bound}) (run kblam review rebind checked-use-0001 --by NAME --reason TEXT "
            f"--expect D)")


def k15_binding_line(kb, path: str, bound: str) -> str:
    """The K15 error a task bound to the finding's bytes `bound` raises once those bytes change
    (SPEC §5.2.3, §5.2.4): reported on the record's `base_file_sha256` line."""
    record = m.record_path(kb, "claim-task-0001")
    return (f"K15 {rel(kb, record)}:{line_with(record, bound)}: F-0001's file now hashes to "
            f"{sha_of(kb, path)}, not the {bound} claim-task-0001 was bound to (the binding covers the whole "
            f"file, not only the fingerprint); reread it, then run kblam review rebind claim-task-0001 --by NAME "
            f"--reason TEXT --expect D")


def k4_line(kb, path: Path) -> str:
    """K4's revision-history error for a finding whose claim says a type was superseded (SPEC §5)."""
    return (f"K4 {rel(kb, path)}:{line_with(path, 'superseded')}: revision-history language "
            f"\"supersed\". State only the current fact, directly; earlier versions live in git history, "
            f"not in the finding. A negative result is written positively: \"X does not do Y; "
            f"evidence: ...\"")


# --- start states ---------------------------------------------------------------------------------


def confirmed_challenge(kb, source_repo, *, affects: str | None = None) -> str:
    """source-challenge-0001 on the trace's lines 3-3, put and confirmed by reviewer-b (its creator is reviewer-a).
    `affects` is the installed finding whose excerpt the confirmation newly makes fail K14, or None.
    Returns the challenge's ID."""
    staged = challenge_new(kb, source_repo, by="reviewer-a")
    fill(staged, **sc_fields())
    put_record(kb, source_repo, staged, "source-challenge-0001",
               out=text(f"kblam put: source-challenge-0001 -> {REVIEW}/challenges/source-challenge-0001.yaml"))
    lines = [f"kblam review decide: source-challenge-0001 is now confirmed (subject digest "
             f"{m.expect(kb, 'source-challenge-0001')[:12]})"]
    if affects is not None:
        lines += [
            f"kblam review decide: source-challenge-0001 now affects {affects}; run kblam challenge uses source-challenge-0001 "
            f"for each excerpt and the command that fixes it",
            f"K14 {FINDING_PATH}:{excerpt_line(kb.root / FINDING_PATH)}: "
            f"{same_bytes_message(source_repo)}",
            "kblam review decide: done, but kblam validate still fails (1 error(s) listed above, "
            "owned by other findings or records)",
        ]
    decide(kb, source_repo, "source-challenge-0001", "confirmed", by="reviewer-b", out=text(*lines),
           changed={f"{REVIEW}/challenges/source-challenge-0001.yaml", f"{REVIEW}/INDEX.md", TREE_HASH})
    return "source-challenge-0001"


def approved_use(kb, source_repo, sc_id: str = "source-challenge-0001", finding_id: str = "F-0001") -> str:
    """checked-use-0001, proposed by researcher-a and approved by reviewer-b, which covers F-0001's excerpt of
    the challenged lines: the put leaves the K14 error standing (the use is still open) and the
    approval resolves it. Returns the use's ID."""
    staged = use_review(kb, source_repo, sc_id, finding_id, by="reviewer-b",
                        proponent="researcher-a")
    fill(staged, **cu_fields())
    put_record(kb, source_repo, staged, "checked-use-0001", out=text(
        f"kblam put: checked-use-0001 -> {REVIEW}/uses/checked-use-0001.yaml",
        f"K14 {FINDING_PATH}:{excerpt_line(kb.root / FINDING_PATH)}: "
        f"{same_bytes_open_use(source_repo)}",
        "kblam put: done, but kblam validate still fails (1 error(s) listed above, owned by other "
        "findings or records)"))
    decide(kb, source_repo, "checked-use-0001", "approved", by="reviewer-b",
           changed={f"{REVIEW}/uses/checked-use-0001.yaml", f"{REVIEW}/INDEX.md", TREE_HASH},
           out=text(f"kblam review decide: checked-use-0001 is now approved (subject digest "
                    f"{m.expect(kb, 'checked-use-0001')[:12]})"))
    return "checked-use-0001"


def finding_with_an_approved_use(kb, source_repo) -> str:
    """The second scenario's start state: F-0001 quotes the trace's lines 3-3 (installed through
    `kb.add`, before a challenge on them exists), source-challenge-0001 is confirmed on those lines by reviewer-b
    and checked-use-0001 covers the excerpt, so validate exits 0. Returns the challenge's ID."""
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")
    sc = confirmed_challenge(kb, source_repo, affects="F-0001")
    approved_use(kb, source_repo, sc, "F-0001")
    validate(kb, source_repo, code=0, out="kblam validate: OK (1 findings)\n")
    return sc


def finding_with_a_task(kb, source_repo) -> str:
    """The third scenario's start state: F-0001 (a plain finding, no excerpt) and the open, current
    task claim-task-0001 bound to it, creator and proponent researcher-a. validate exits 0."""
    kb.add("F-0001", "sensor", m.CLAIM)
    staged = task_new(kb, source_repo, "F-0001", by="researcher-a", proponent="researcher-a")
    fill(staged, **ct_fields())
    put_record(kb, source_repo, staged, "claim-task-0001",
               out=text(f"kblam put: claim-task-0001 -> {REVIEW}/tasks/claim-task-0001.yaml"))
    return "claim-task-0001"


# --- a new finding that quotes a confirmed assertion (refused, exit 1, findings/ unchanged) -------


def test_a_new_finding_quoting_a_confirmed_assertion_is_refused(kb, source_repo):
    """(a) Start: no findings; source-challenge-0001 confirmed on the trace's lines 3-3, pinned at the HEAD blob,
    validate clean. Command: `kblam put <staged new F-0001>` quoting those lines verbatim (`put` takes
    no --by). Exit 1: a new excerpt cannot quote a confirmed challenge's assertion in schema 1, even
    for its raw bytes, because the use that would cover it binds an installed finding. Diagnostics: the
    K14 same-bytes error at the staged file, and "kblam put: rejected F-0001 (1 error(s)); findings/ is
    unchanged…". Files changed: the Jev pair cache alone — findings/, the review root, the registry and
    tree.hash are untouched — and the staged file stays. validate afterwards: exit 0. Acceptance 2."""
    confirmed_challenge(kb, source_repo)
    validate(kb, source_repo, code=0, out="kblam validate: OK (0 findings)\n")
    staged = new_finding(kb, source_repo, "F-0001-sensor-curve-types.md")
    staged.write_bytes(m.finding_text("F-0001", m.CLAIM, title="Sensor curve types",
                                      body=m.verbatim(f"{m.TRACE}:3-3", m.LINE3)).encode("utf-8"))
    before_excerpt = excerpt_line(staged)

    call(kb, source_repo, {PAIR_CACHE}, "put", str(staged), code=1, out=text(
        f"K14 {rel(kb, staged)}:{before_excerpt}: {same_bytes_message(source_repo)}",
        f"kblam put: rejected F-0001 (1 error(s)); findings/ is unchanged. Fix the staged file and put "
        f"it again. {SKILL_POINTER}"))

    assert staged.is_file() and not (kb.root / FINDING_PATH).exists()
    validate(kb, source_repo, code=0, out="kblam validate: OK (0 findings)\n")


# --- an installed finding with an approved use, edited in the body only ---------------------------


def test_a_body_only_edit_is_accepted_and_lists_the_use_it_makes_stale(kb, source_repo):
    """(b) Start: F-0001 quotes the trace's lines 3-3, source-challenge-0001 confirmed on them and checked-use-0001 approved,
    so the excerpt is covered and validate is clean. Command: `kblam put <staged edit of F-0001>`, one
    paragraph appended after the excerpt (frontmatter, claim and excerpt unchanged). Exit 0: the kept
    excerpt is one the installed finding already had affected, so K14 does not refuse the edit, and K15
    never refuses a finding put. Diagnostics: the K13 warning that the use's bound finding bytes
    changed, the put line, "checked-use-0001 is now stale…", the K14 error the put keeps — the use no longer
    covers the excerpt — and "done, but kblam validate still fails (1 error(s)…)". Files changed: the
    finding, tree.hash, the staged file and the edit-base receipt removed, and kblam's own caches.
    validate afterwards: exit 1, the K14 error, that K13 warning and 1 error in findings/. Acceptance
    2."""
    finding_with_an_approved_use(kb, source_repo)
    bound = sha_of(kb, FINDING_PATH)
    staged = edit_finding(kb, source_repo, "F-0001", "ratio")
    staged.write_bytes(edited(reading(kb, FINDING_PATH)).encode("utf-8"))
    staged_line = excerpt_line(staged)

    with cli(kb, source_repo,
             {FINDING_PATH, TREE_HASH, rel(kb, staged),
              f"{FINDINGS_STAGING}/F-0001.edit-base.json"} | JEV_STATE,
             "put", str(staged)) as run:
        assert run.code == 0
        now = sha_of(kb, FINDING_PATH)
        assert run.out == text(
            stale_use_warning(now, bound),
            f"kblam put: F-0001 -> {FINDING_PATH}",
            stale_line("checked-use-0001"),
            f"K14 {rel(kb, staged)}:{staged_line}: {same_bytes_message(source_repo)}",
            "kblam put: done, but kblam validate still fails (1 error(s) listed above that this put "
            "did not refuse)")
        assert run.err == text(JEV_NOTE)

    assert not staged.exists()
    validate(kb, source_repo, code=1, out=text(
        f"K14 {FINDING_PATH}:{excerpt_line(kb.root / FINDING_PATH)}: {same_bytes_message(source_repo)}",
        stale_use_warning(now, bound),
        "kblam validate: 1 error(s) in findings/"))


def test_a_reviewer_rebinds_the_use_the_edit_made_stale_and_validate_is_clean(kb, source_repo):
    """(b) Start: as the body-only edit left it — checked-use-0001 (proponent researcher-a) bound to F-0001's
    earlier bytes, so K14 fails and validate exits 1. Command: `kblam review rebind checked-use-0001 --by
    reviewer-b --reason … --expect D`, a reviewer other than the proponent, who recomputes the use's
    binding from the installed finding and from the confirmed challenge's current subject digest. Exit
    0. Diagnostics: "kblam review rebind: checked-use-0001 rebound, now approved (subject digest …)". Files
    changed: the use and tree.hash (the review index is unchanged: the use's row keeps the same ID,
    challenge, finding, excerpt, disposition and status). validate afterwards: exit 0. Acceptance 2,
    Acceptance 5."""
    finding_with_an_approved_use(kb, source_repo)
    bound = sha_of(kb, FINDING_PATH)
    staged = edit_finding(kb, source_repo, "F-0001", "ratio")
    staged.write_bytes(edited(reading(kb, FINDING_PATH)).encode("utf-8"))
    staged_line = excerpt_line(staged)

    with cli(kb, source_repo,
             {FINDING_PATH, TREE_HASH, rel(kb, staged),
              f"{FINDINGS_STAGING}/F-0001.edit-base.json"} | JEV_STATE,
             "put", str(staged)) as run:
        assert run.code == 0
        now = sha_of(kb, FINDING_PATH)
        assert run.out == text(
            stale_use_warning(now, bound),
            f"kblam put: F-0001 -> {FINDING_PATH}",
            stale_line("checked-use-0001"),
            f"K14 {rel(kb, staged)}:{staged_line}: {same_bytes_message(source_repo)}",
            "kblam put: done, but kblam validate still fails (1 error(s) listed above that this put "
            "did not refuse)")
        assert run.err == text(JEV_NOTE)
    validate(kb, source_repo, code=1, out=text(
        f"K14 {FINDING_PATH}:{excerpt_line(kb.root / FINDING_PATH)}: {same_bytes_message(source_repo)}",
        stale_use_warning(now, bound),
        "kblam validate: 1 error(s) in findings/"))

    with cli(kb, source_repo, {f"{REVIEW}/uses/checked-use-0001.yaml", TREE_HASH},
             "review", "rebind", "checked-use-0001", "--by", "reviewer-b",
             "--reason", "rechecked the excerpt in the new revision",
             "--expect", m.expect(kb, "checked-use-0001")) as run:
        assert run.code == 0
        assert run.out == text(f"kblam review rebind: checked-use-0001 rebound, now approved (subject digest "
                               f"{m.expect(kb, 'checked-use-0001')[:12]})")
        assert run.err == ""

    validate(kb, source_repo, code=0, out="kblam validate: OK (1 findings)\n")


# --- a finding put with an open task and with a stale one (never refused by K15) ------------------


def test_a_finding_put_with_an_open_task_lists_the_task_it_makes_stale(kb, source_repo):
    """(c) Start: F-0001 and the open, current task claim-task-0001 bound to its bytes, so validate prints the
    pending line and exits 0. Command: `kblam put <staged edit of F-0001>`, one paragraph appended.
    Exit 0: a finding put blocks on K1-K11 and K14, never on K15, so the task's broken binding does not
    refuse it. Diagnostics: the put line, "claim-task-0001 is now stale…" with the rebind command for an open
    task (no --evidence), the K15 binding error it did not refuse, and "done, but kblam validate still
    fails (1 error(s)…)". Files changed: the finding, tree.hash, the staged file and the edit-base
    receipt removed, and kblam's own caches. validate afterwards: exit 1, no pending line, 1 error in
    findings/. Acceptance 4."""
    finding_with_a_task(kb, source_repo)
    validate(kb, source_repo, code=0, out=text(
        "claim-task-0001 open replication of F-0001: Does an independent measurement establish the claim?",
        "kblam validate: OK (1 findings); 1 pending task(s)"))
    bound = sha_of(kb, SENSOR_PATH)
    staged = edit_finding(kb, source_repo, "F-0001", "sensor")
    staged.write_bytes(edited(reading(kb, SENSOR_PATH)).encode("utf-8"))

    with cli(kb, source_repo,
             {SENSOR_PATH, TREE_HASH, rel(kb, staged),
              f"{FINDINGS_STAGING}/F-0001.edit-base.json"} | JEV_STATE,
             "put", str(staged)) as run:
        assert run.code == 0
        assert run.out == text(
            f"kblam put: F-0001 -> {SENSOR_PATH}",
            stale_line("claim-task-0001"),
            k15_binding_line(kb, SENSOR_PATH, bound),
            "kblam put: done, but kblam validate still fails (1 error(s) listed above that this put "
            "did not refuse)")
        assert run.err == text(JEV_NOTE)

    validate(kb, source_repo, code=1, out=text(
        k15_binding_line(kb, SENSOR_PATH, bound),
        "kblam validate: 1 error(s) in research-review/"))


def test_a_finding_put_with_an_already_stale_task_is_accepted(kb, source_repo):
    """(c) Start: F-0001 and claim-task-0001 bound to it, then F-0001 rewritten out of band, so claim-task-0001 is
    already stale and validate exits 1 with the K15 error. Command: `kblam put <staged edit of
    F-0001>`, a second paragraph appended. Exit 0: K15 never refuses a finding put, and a binding that
    was already broken is not listed again as newly stale. Diagnostics: the put line, the K15 error it
    did not refuse, and "done, but kblam validate still fails (1 error(s)…)". Files changed: the
    finding, tree.hash, the staged file and the edit-base receipt removed, and kblam's own caches.
    validate afterwards: exit 1, 1 error in research-review/. Acceptance 4."""
    finding_with_a_task(kb, source_repo)
    bound = sha_of(kb, SENSOR_PATH)
    kb.add("F-0001", "sensor", m.CLAIM, body="A first detail.")
    validate(kb, source_repo, code=1, out=text(
        k15_binding_line(kb, SENSOR_PATH, bound),
        "kblam validate: 1 error(s) in research-review/"))
    staged = edit_finding(kb, source_repo, "F-0001", "sensor")
    staged.write_bytes(edited(reading(kb, SENSOR_PATH), "A second one.").encode("utf-8"))

    with cli(kb, source_repo,
             {SENSOR_PATH, TREE_HASH, rel(kb, staged),
              f"{FINDINGS_STAGING}/F-0001.edit-base.json"} | JEV_STATE,
             "put", str(staged)) as run:
        assert run.code == 0
        assert run.out == text(
            f"kblam put: F-0001 -> {SENSOR_PATH}",
            k15_binding_line(kb, SENSOR_PATH, bound),
            "kblam put: done, but kblam validate still fails (1 error(s) listed above that this put "
            "did not refuse)")
        assert run.err == text(JEV_NOTE)

    validate(kb, source_repo, code=1, out=text(
        k15_binding_line(kb, SENSOR_PATH, bound),
        "kblam validate: 1 error(s) in research-review/"))


def test_a_finding_put_with_a_retired_task_is_accepted_and_raises_nothing(kb, source_repo):
    """(c) Start: F-0001 and claim-task-0001 bound to it, then claim-task-0001 retired by reviewer-b (`kblam review
    decide claim-task-0001 --status stale`: `stale` is every kind's retired status, and a retired task gets no
    binding check), so validate exits 0. Command: `kblam put <staged edit of F-0001>`, one paragraph
    appended. Exit 0: K15 never refuses a finding put, and a retired task leaves no obligation at all.
    Diagnostics: the put line alone — no stale line, no "validate still fails" line. Files changed: the
    finding, tree.hash, the staged file and the edit-base receipt removed, and kblam's own caches.
    validate afterwards: exit 0. Acceptance 4."""
    finding_with_a_task(kb, source_repo)
    decide(kb, source_repo, "claim-task-0001", "stale", by="reviewer-b", reason="the question is settled",
           changed={f"{REVIEW}/tasks/claim-task-0001.yaml", f"{REVIEW}/INDEX.md", TREE_HASH},
           out=text(f"kblam review decide: claim-task-0001 is now stale (subject digest "
                    f"{m.expect(kb, 'claim-task-0001')[:12]})"))
    validate(kb, source_repo, code=0, out="kblam validate: OK (1 findings)\n")
    staged = edit_finding(kb, source_repo, "F-0001", "sensor")
    staged.write_bytes(edited(reading(kb, SENSOR_PATH)).encode("utf-8"))

    call(kb, source_repo,
         {SENSOR_PATH, TREE_HASH, rel(kb, staged), f"{FINDINGS_STAGING}/F-0001.edit-base.json"}
         | JEV_STATE,
         "put", str(staged), err=text(JEV_NOTE),
         out=f"kblam put: F-0001 -> {SENSOR_PATH}\n")

    validate(kb, source_repo, code=0, out="kblam validate: OK (1 findings)\n")


# --- a record put while an unrelated finding fails, and while the record itself fails -------------


def test_a_record_put_is_accepted_while_an_unrelated_finding_fails(kb, source_repo):
    """(d) Start: F-0002 states a fact in revision-history language, so K4 fails it and validate exits
    1; the registry does not exist and the review root is empty. Command: `kblam put <staged new
    source-challenge-0001>`, filled and ready. Exit 0: a record put blocks on the errors owned by that record, and
    another file's error never refuses it. Diagnostics: the put line, F-0002's K4 error and "done, but
    kblam validate still fails (1 error(s) listed above, owned by other findings or records)". Files
    changed: the record, <review root>/INDEX.md, .kblam/review-ids, tree.hash and the staged file's
    removal. validate afterwards: exit 1, 1 error in findings/. Acceptance 2."""
    other = kb.add("F-0002", "history", "The first curve type was superseded by the second.")
    validate(kb, source_repo, code=1, out=text(
        k4_line(kb, other), "kblam validate: 1 error(s) in findings/"))
    staged = challenge_new(kb, source_repo, by="reviewer-a")
    fill(staged, **sc_fields())

    put_record(kb, source_repo, staged, "source-challenge-0001", out=text(
        f"kblam put: source-challenge-0001 -> {REVIEW}/challenges/source-challenge-0001.yaml",
        k4_line(kb, other),
        "kblam put: done, but kblam validate still fails (1 error(s) listed above, owned by other "
        "findings or records)"))

    assert m.registry(kb) == ["source-challenge-0001"]
    assert not staged.exists()
    validate(kb, source_repo, code=1, out=text(
        k4_line(kb, other), "kblam validate: 1 error(s) in findings/"))


def test_a_record_put_whose_own_record_fails_is_refused(kb, source_repo):
    """(d) Start: no records; a staged source-challenge-0001 whose `proposition` is blank, which §5.2.2 requires (a
    blank value is allowed only in a staged record). Command: `kblam put <staged source-challenge-0001>`. Exit 1: the
    error is owned by source-challenge-0001, which the put acts on. Diagnostics: the K13 error at the record's
    proposition line, at the staged file's own display path, and "kblam put: rejected source-challenge-0001 (1
    error(s)); research-review/ is unchanged…". Files changed: none at all — no record, no index, no
    registry, no tree.hash, not even the Jev pair cache a finding put writes — and the staged file
    stays. validate afterwards: exit 0. Acceptance 2."""
    staged = challenge_new(kb, source_repo, by="reviewer-a")
    fill(staged, **{**sc_fields(), "proposition": ""})

    call(kb, source_repo, set(), "put", str(staged), code=1, out=text(
        f"K13 {rel(kb, staged)}:{staged_key_line(staged, 'proposition')}: proposition: "
        f"required",
        f"kblam put: rejected source-challenge-0001 (1 error(s)); {REVIEW}/ is unchanged. Fix the staged file and "
        f"put it again. {SKILL_POINTER}"))

    assert staged.is_file() and not (kb.root / REVIEW / "challenges").exists()
    assert m.registry(kb) is None
    validate(kb, source_repo, code=0, out="kblam validate: OK (0 findings)\n")
