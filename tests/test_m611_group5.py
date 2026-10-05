"""SPEC §12 M6.11 acceptance tests, test group 5: basis and confirmation (A3, A5).

"*Basis and confirmation* (A3, A5): the `contradicted`, `unsupported` and `wrong_model` role
requirements; confirming without primary provenance, on a finding or a history document only, or with a
provisional source (refused); a basis entry on the source itself after another commit is checked out
(still available); another basis file changed (a K13 error for a confirmed challenge, a warning for an
open one); a missing original capture recorded as `unsupported` with its limits."

Every test drives the real CLI in process (m611_helpers.kblam) and states, in its docstring, the start
state, the command and actor, the exit status, the diagnostics, the files changed, the validation
result afterwards and the acceptance criterion it demonstrates. Every CLI invocation — setup calls,
refusals and read-only calls alike — runs inside `changes`: the whole KB tree (m611_helpers.tree) and
the source repository (SourceRepo.snapshot) are compared before and after, as an exact set of changed
paths (`set()` for a refusal or a read-only call) and as equal source state. Every Run is asserted in
full: exit code, whole stdout, empty stderr. The test's own fixture edits (writing a finding or a
history file, changing a basis file, committing to the source) sit outside those windows. Sources and
basis files live outside the review root, so nothing here calls `accept_tree`, which re-records
tree.hash and is not this group's subject. Offline and deterministic: `frozen_today` pins `created`
and a decision's `date` at 2026-09-28, and the fixtures enable no Jev verdict.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import m611_helpers as m
from conftest import dump_record
from kblam.finding import yaml_rt

# The same alias a group file declares: autouse, so every test in this module runs on the pinned date.
frozen_today = m.frozen_today

REVIEW = "research-review"
SC = "SC-0001"                     # every test in this group installs exactly one challenge
CHALLENGE = f"{REVIEW}/challenges/{SC}.yaml"
INDEX = f"{REVIEW}/INDEX.md"
STAGED = ".kblam/review-staging/SC-0001.yaml"
RECEIPT = ".kblam/review-receipts/SC-0001.json"
REGISTRY = ".kblam/review-ids"
TREE_HASH = ".kblam/tree.hash"

OK = "kblam validate: OK (0 findings)"
OK_ONE = "kblam validate: OK (1 findings)"
FAILED = "kblam validate: 1 error(s) in findings/"
# The exact paths each of the group's commands may change: the staging command writes the staged record
# and its allocation receipt; the put installs the record, the review index, the registry and tree.hash
# and removes the staged file; a decision rewrites the record, the index and tree.hash.
NOTHING: set[str] = set()
STAGING_WRITES = {STAGED, RECEIPT}
PUT_WRITES = {STAGED, CHALLENGE, INDEX, REGISTRY, TREE_HASH}
DECIDE_WRITES = {CHALLENGE, INDEX, TREE_HASH}

PROPOSITION = "The printed byte equality follows from the printed byte values"
USABLE = "The printed byte values may be cited as a report, not as verified wire bytes."
LIMITS = "Do not infer the actual capture bytes or the host's routing from this row."
SOURCE_ENTRY = m.TRACE                                       # the basis entry on the source itself
OTHER_ENTRY = m.EVIDENCE_PATH                                # a basis file the review root does not hold
HISTORY_ENTRY = "history/2026-09-01-trace-reread.md"         # a file under the history_dirs folder


# --- bracketing every command ---------------------------------------------------------------------


@contextlib.contextmanager
def changes(kb, source_repo, expected=NOTHING):
    """Bracket one CLI invocation: the whole KB tree and the source repository just before it and just
    after it. `expected` is the exact set of KB paths the call may change: empty for a refusal or a
    read-only command. A stray write, in the KB or in the source repository, fails here."""
    before, source_before = m.tree(kb), source_repo.snapshot()
    yield
    assert m.changed(before, m.tree(kb)) == set(expected), "the KB tree changed otherwise"
    assert source_repo.snapshot() == source_before, "the source repository changed"


def cli(kb, source_repo, expected, *argv: object) -> m.Run:
    """One `kblam` invocation inside `changes`; the Run, for the caller to assert in full."""
    with changes(kb, source_repo, expected):
        return m.kblam(kb, *argv)


def exactly(run: m.Run, out: str, what: str, *, code: int = 0) -> None:
    """A Run asserted in full: this exit code, this whole stdout, nothing on stderr."""
    assert run.code == code, f"{what} exited {run.code}:\n{run.out}{run.err}"
    assert run.err == "", f"{what} wrote to stderr:\n{run.err}"
    assert run.out == out, f"{what} printed:\n{run.out}"


def basis_of(path, *, role: str = "internal-inconsistency", provenance: str = "observed",
             locator: str = "row 102: printed byte values") -> dict:
    """One staged basis entry (SPEC §5.2.2): a file reference plus locator, role and provenance. `sha256`
    and the pin are null, so `put` hashes the file and pins it by the §5.2.2 rule (a basis entry whose
    canonical key is the source's gets `source.sha256` and no pin)."""
    return {"path": path, "sha256": None, "repo": None, "commit": None, "blob": None, "snapshot": None,
            "locator": locator, "role": role, "provenance": provenance}


def record_text(kb, rec_id: str = SC) -> str:
    """An installed record's bytes as text (a library read, not a command)."""
    return m.record_path(kb, rec_id).read_text(encoding="utf-8")


def installed(kb, rec_id: str = SC) -> dict:
    """The installed record as parsed data (a library read, not a command)."""
    return yaml_rt().load(record_text(kb, rec_id).encode("utf-8"))


def key_line(kb, rec_id: str, key: str) -> int:
    """The 1-based line of a record's top-level key: the line K13 reports an issue about that key on."""
    lines = record_text(kb, rec_id).splitlines()
    return next(i for i, line in enumerate(lines, 1) if line.startswith(f"{key}:"))


# --- the group's commands: one bracketed call each -------------------------------------------------


def stage(kb, source_repo, *, by="reviewer-a", lines="3-3", classification="contradicted", basis,
          proposition=PROPOSITION, usable=USABLE, limits=LIMITS) -> Path:
    """`challenge new --lines A-B --by NAME` (bracketed: the staged record and its allocation receipt),
    then the author's filling of the free fields — a fixture edit, outside the command windows. The
    staged record's path."""
    run = cli(kb, source_repo, STAGING_WRITES,
              "challenge", "new", m.TRACE, "--lines", lines, "--by", by)
    staged = kb.root / STAGED
    exactly(run, f"{staged}\n", "challenge new")
    data = yaml_rt().load(staged.read_bytes().decode("utf-8"))
    data.update({"proposition": proposition, "scope": ["MX-100 capture transcription"],
                 "classification": classification, "basis": basis, "usable": usable, "limits": limits})
    staged.write_bytes(dump_record(data).encode("utf-8"))
    return staged


def install(kb, source_repo, staged: Path) -> None:
    """`kblam put <staged SC->` (bracketed: the record, the review index, the registry and tree.hash; the
    staged file is gone)."""
    run = cli(kb, source_repo, PUT_WRITES, "put", str(staged))
    exactly(run, f"kblam put: SC-0001 -> {CHALLENGE}\n", "put")
    assert not staged.exists()


def confirm(kb, source_repo, rec_id: str, *, by="reviewer-b", reason="read the source and pinned it"):
    """`kblam review decide <ID> --status confirmed --by NAME --reason TEXT --expect D` (bracketed: the
    record, the index, tree.hash). With nothing left to report it prints its one line."""
    run = cli(kb, source_repo, DECIDE_WRITES, "review", "decide", rec_id, "--status", "confirmed",
              "--by", by, "--reason", reason, "--expect", m.expect(kb, rec_id))
    exactly(run, f"kblam review decide: {rec_id} is now confirmed (subject digest "
                 f"{m.expect(kb, rec_id)[:12]})\n", "review decide")


def refuse_confirmation(kb, source_repo, rec_id: str, *, by="reviewer-b",
                        reason="read the source and pinned it") -> m.Run:
    """A `review decide --status confirmed` that must be refused, bracketed: it changes nothing. The Run,
    whose whole output and stderr the caller asserts."""
    run = cli(kb, source_repo, NOTHING, "review", "decide", rec_id, "--status", "confirmed",
              "--by", by, "--reason", reason, "--expect", m.expect(kb, rec_id))
    assert run.code == 1, run.out + run.err
    assert run.err == "", run.err
    return run


def refusal(kb, run: m.Run, message: str, *, key: str = "basis", rec_id=SC) -> None:
    """The whole output of a refused `review decide` (SPEC §5.2.4 "Where each rule blocks"), asserted
    byte for byte: exit 1, the one K13 issue on the record — on the line of the record's `key` — the
    refusal line, and nothing on stderr."""
    assert run.code == 1, run.out + run.err
    assert run.err == "", run.err
    assert run.out == (
        f"K13 {CHALLENGE}:{key_line(kb, rec_id, key)}: {message}\n"
        f"kblam review decide: refused {rec_id} (1 error(s)); {REVIEW}/ is unchanged. Fix what is listed "
        f"above and run it again.\n"), run.out


def read(kb, source_repo, *argv: object) -> m.Run:
    """A read-only command (`validate`, `challenge show`, `review list`), bracketed: it changes nothing
    in the KB and nothing in the source repository."""
    return cli(kb, source_repo, NOTHING, *argv)


# --- the role each classification requires --------------------------------------------------------


def test_a_contradicted_challenge_needs_a_counterevidence_role(kb, source_repo):
    """Start: no records and no findings; the trace is committed and clean, so `challenge new` pins the
    source it stages, and F-0001 is absent, so nothing is affected. Commands, each bracketed: `challenge
    new --lines 3-3 --by reviewer-a` (writes the staged record and its allocation receipt), the author's
    filling (a fixture edit: classification `contradicted`, one basis entry of role `model-mismatch`),
    `kblam put` (writes the record, the review index, the registry and tree.hash; the staged file is
    gone), `kblam validate` (a read), `kblam review decide SC-0001 --status confirmed --by reviewer-b
    --reason ... --expect D` (refused, exit 1) and `kblam validate` again (a read). Files changed: the
    staged record and its receipt, then the record, the review index, the registry and tree.hash, and
    nothing else — both reads and the refusal change nothing. Diagnostics: the K13 issue "SC-0001 is classified contradicted but no basis
    entry has role counterevidence or internal-inconsistency" on SC-0001's `basis:` line, then "kblam
    review decide: refused SC-0001 (1 error(s)); research-review/ is unchanged. Fix what is listed above
    and run it again.". The source repository is untouched throughout. `validate` exits 0 after the
    refusal (only a confirmation needs the role) and the record stays open. Acceptance 5: a closing
    decision needs the support its classification names."""
    staged = stage(kb, source_repo, by="reviewer-a", classification="contradicted",
                   basis=[basis_of(SOURCE_ENTRY, role="model-mismatch")])
    install(kb, source_repo, staged)
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")

    run = refuse_confirmation(kb, source_repo, SC)

    refusal(kb, run, "SC-0001 is classified contradicted but no basis entry has role counterevidence or "
                     "internal-inconsistency")
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    assert installed(kb)["status"] == "open"


def test_a_contradicted_challenge_with_counterevidence_confirms(kb, source_repo):
    """Start: no records and no findings; the trace is committed and clean. Commands, each bracketed:
    `challenge new --lines 3-3 --by reviewer-a` (staged record and receipt), the author's filling
    (classification `contradicted`, one basis entry on the source with role `counterevidence` and the
    primary provenance `observed`), `kblam put` (writes the record, the review index, the registry and
    tree.hash; the staged file is gone), `kblam review decide SC-0001 --status confirmed --by reviewer-b
    --reason ... --expect D` (writes the record, the review index and tree.hash), `kblam validate` and
    `kblam review list` (reads). Files changed: the staged record and its receipt, then the record, the
    review index, the registry and tree.hash, then the record, the review index and tree.hash; the two
    reads change nothing, in the KB or the source repository. Exit 0 throughout, no diagnostic, and
    `review list` prints its one line for a confirmed, current challenge. The source repository is untouched.
    `validate` exits 0 afterwards and the record holds the role it was confirmed on. Acceptance 5: the
    counterevidence a `contradicted` classification names is what a confirmation needs."""
    staged = stage(kb, source_repo, by="reviewer-a", classification="contradicted",
                   basis=[basis_of(SOURCE_ENTRY, role="counterevidence")])
    install(kb, source_repo, staged)

    confirm(kb, source_repo, SC, reason="read the source and pinned it")

    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    exactly(read(kb, source_repo, "review", "list"),
            f"SC-0001 challenge confirmed {m.expect(kb, SC)[:12]} {m.TRACE}:3-3 current\n",
            "review list")
    assert installed(kb)["basis"][0]["role"] == "counterevidence"
    assert installed(kb)["basis"][0]["provenance"] == "observed"


def test_a_wrong_model_challenge_needs_a_model_mismatch_role(kb, source_repo):
    """Start: no records and no findings; the trace is committed and clean. Commands, each bracketed:
    `challenge new --lines 3-3 --by reviewer-a` (staged record and receipt), the author's filling
    (a fixture edit: classification `wrong_model`, one basis entry of role `internal-inconsistency`),
    `kblam put` (writes the record, the review index, the registry and tree.hash; the staged file is
    gone), `kblam validate` (a read), `kblam review decide SC-0001 --status confirmed --by reviewer-b
    --reason ... --expect D` (refused, exit 1) and `kblam validate` again (a read). Files changed: the
    staged record and its receipt, then the record, the review index, the registry and tree.hash, and
    nothing else — both reads and the refusal change nothing. Diagnostics: the K13 issue "SC-0001 is
    classified wrong_model but no basis entry has role model-mismatch" on SC-0001's `basis:` line, then
    the refusal line. The source repository is untouched. `validate` exits 0 afterwards and
    the record stays open. Acceptance 5: a `wrong_model` judgment needs model-mismatch support, which
    internal inconsistency is not."""
    staged = stage(kb, source_repo, by="reviewer-a", classification="wrong_model",
                   basis=[basis_of(SOURCE_ENTRY, role="internal-inconsistency")])
    install(kb, source_repo, staged)
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")

    run = refuse_confirmation(kb, source_repo, SC)

    refusal(kb, run, "SC-0001 is classified wrong_model but no basis entry has role model-mismatch")
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    assert installed(kb)["status"] == "open"


def test_a_wrong_model_challenge_with_a_model_mismatch_confirms(kb, source_repo):
    """Start: no records and no findings; the trace is committed and clean. Commands, each bracketed:
    `challenge new --lines 3-3 --by reviewer-a` (staged record and receipt), the author's filling
    (a fixture edit: classification `wrong_model`, one basis entry on the source whose role is
    `model-mismatch`), `kblam put` (writes the record, the review index, the registry and tree.hash; the
    staged file is gone), `kblam review decide SC-0001 --status confirmed --by reviewer-b --reason ...
    --expect D` (writes the record, the review index and tree.hash), `kblam validate` (a read). Files
    changed: the staged record and its receipt, then the record, the review index, the registry and
    tree.hash, then the record, the review index and tree.hash; the read changes nothing, in the KB or
    the source repository. Exit 0 throughout, no diagnostic. `validate` exits 0 afterwards and the
    record holds the role it was confirmed on. Acceptance 5: the
    role the classification names is what makes the confirmation hold."""
    staged = stage(kb, source_repo, by="reviewer-a", classification="wrong_model",
                   basis=[basis_of(SOURCE_ENTRY, role="model-mismatch")])
    install(kb, source_repo, staged)

    confirm(kb, source_repo, SC)

    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    assert installed(kb)["classification"] == "wrong_model"
    assert installed(kb)["basis"][0]["role"] == "model-mismatch"


def test_an_unsupported_challenge_needs_no_counterevidence_role(kb, source_repo):
    """Start: no records and no findings; the trace is committed and clean. Commands, each bracketed:
    `challenge new --lines 3-3 --by reviewer-a` (staged record and receipt), the author's filling
    (a fixture edit: classification `unsupported`, one basis entry on the source whose role is
    `missing-support`), `kblam put` (writes the record, the review index, the registry and tree.hash; the
    staged file is gone), `kblam review decide SC-0001 --status confirmed --by reviewer-b --reason ...
    --expect D` (writes the record, the review index and tree.hash), `kblam validate` (a read). Files
    changed: the staged record and its receipt, then the record, the review index, the registry and
    tree.hash, then the record, the review index and tree.hash; the read changes nothing, in the KB or
    the source repository. Exit 0 throughout, with no diagnostic at all. `unsupported` asks for neither
    counterevidence nor a model mismatch, so a challenge recording absent support confirms without
    inventing either. Acceptance 5."""
    staged = stage(kb, source_repo, by="reviewer-a", classification="unsupported",
                   basis=[basis_of(SOURCE_ENTRY, role="missing-support")])
    install(kb, source_repo, staged)

    confirm(kb, source_repo, SC,
            reason="the original capture is missing; nothing contradicts the transcription")

    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    assert installed(kb)["classification"] == "unsupported"
    assert installed(kb)["basis"][0]["role"] == "missing-support"


# --- confirmation: primary support and a pinned source --------------------------------------------


def test_confirming_without_primary_provenance_is_refused(kb, source_repo):
    """Start: no records and no findings; the trace is committed and clean. Commands, each bracketed:
    `challenge new --lines 3-3 --by reviewer-a` (staged record and receipt), the author's filling (one
    basis entry on the source whose provenance is `inferred`, which is not in `[review]
    primary_provenance`), `kblam put` (writes the record, the review index, the registry and tree.hash;
    the staged file is gone), `kblam validate` (a read), `kblam review decide SC-0001 --status confirmed
    --by reviewer-b --reason ... --expect D` (refused, exit 1) and `kblam validate` again (a read). Files
    changed: the staged record and its receipt, then the record, the review index, the registry and
    tree.hash, and nothing else — both reads and the refusal change nothing. Diagnostics: the K13 issue
    "SC-0001 is confirmed with no primary support; a confirmation needs a basis entry whose provenance
    is one of observed, decoded and whose resolved path is outside findings/, research-review/ and the
    history folders" on SC-0001's `basis:` line, then the refusal line. The source repository is untouched. `validate` exits 0 afterwards and the record stays open.
    Acceptance 5: a decision needs support kblam can hash as primary."""
    staged = stage(kb, source_repo, by="reviewer-a",
                   basis=[basis_of(SOURCE_ENTRY, provenance="inferred")])
    install(kb, source_repo, staged)
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")

    run = refuse_confirmation(kb, source_repo, SC)

    message = ("SC-0001 is confirmed with no primary support; a confirmation needs a basis entry whose "
               f"provenance is one of observed, decoded and whose resolved path is outside findings/, "
               f"{REVIEW}/ and the history folders")
    refusal(kb, run, message)
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    assert installed(kb)["status"] == "open"


def test_confirming_on_a_finding_alone_is_refused(kb, source_repo):
    """Start: finding F-0001 installed through `kb.add` (a fixture edit), no records; the trace is
    committed and clean. Commands, each bracketed: `challenge new --lines 3-3 --by reviewer-a` (staged
    record and receipt), the author's filling (a fixture edit: whose only basis entry is that finding
    file, role `internal-inconsistency` and the primary provenance `observed`), `kblam put` (writes the
    record, the review index, the registry and tree.hash; the staged file is gone), `kblam validate`, a
    read over 1 finding, `kblam review decide SC-0001 --status confirmed --by reviewer-b --reason ...
    --expect D`, refused, exit 1, and `kblam validate` again. Files changed: F-0001, findings/INDEX.md
    and .kblam/tree.hash (the fixture edit: `kb.add` reindexes and records the tree), then the staged
    record and its receipt, then the record, the review index, the registry and tree.hash, and nothing
    else — both reads and the refusal change nothing.
    Diagnostics: the "no primary support"
    K13 issue (a second KB paraphrase is not primary support) on SC-0001's `basis:` line, then the
    refusal line. The source repository is untouched and F-0001 is unchanged. `validate` exits 0
    afterwards with F-0001 untouched. Acceptance 5: primary support cannot be the KB's own
    restatement."""
    finding = kb.add("F-0001", "ratio", m.CLAIM)
    finding_path = finding.relative_to(kb.root).as_posix()
    staged = stage(kb, source_repo, by="reviewer-a", basis=[basis_of(finding_path)])
    install(kb, source_repo, staged)
    exactly(read(kb, source_repo, "validate"), f"{OK_ONE}\n", "validate")

    run = refuse_confirmation(kb, source_repo, SC)

    refusal(kb, run, "SC-0001 is confirmed with no primary support; a confirmation needs a basis entry "
                     f"whose provenance is one of observed, decoded and whose resolved path is outside "
                     f"findings/, {REVIEW}/ and the history folders")
    exactly(read(kb, source_repo, "validate"), f"{OK_ONE}\n", "validate")
    assert installed(kb)["status"] == "open"


def test_confirming_on_a_history_document_alone_is_refused(kb, source_repo):
    """Start: no records or findings, and one file under the `history_dirs` folder (the KB's own older
    writing) written as a fixture edit; the trace is committed and clean. Commands, each bracketed:
    `challenge new --lines 3-3 --by reviewer-a` (staged record and receipt), the author's filling (whose
    only basis entry is that history document, role `internal-inconsistency` and the primary provenance
    `observed`), `kblam put` (writes the record, the review index, the registry and tree.hash; the
    staged file is gone), `kblam validate` (a read), `kblam review decide SC-0001 --status confirmed --by
    reviewer-b --reason ... --expect D` (refused, exit 1) and `kblam validate` again (a read). Files
    changed: the history document (the fixture edit), then the staged record and its receipt, then the
    record, the review index, the registry and tree.hash, and nothing else — both reads and the refusal
    change nothing. Diagnostics: the "no primary support" K13 issue, which names the history folders, on
    SC-0001's
    `basis:` line, then the refusal line. The source repository is untouched. `validate` exits 0
    afterwards and the record stays open. Acceptance 5: history is the KB's own older writing, not
    primary support."""
    kb.write(HISTORY_ENTRY, "A paraphrase of row 102 written last week.\n")
    staged = stage(kb, source_repo, by="reviewer-a", basis=[basis_of(HISTORY_ENTRY)])
    install(kb, source_repo, staged)
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")

    run = refuse_confirmation(kb, source_repo, SC)

    refusal(kb, run, "SC-0001 is confirmed with no primary support; a confirmation needs a basis entry "
                     f"whose provenance is one of observed, decoded and whose resolved path is outside "
                     f"findings/, {REVIEW}/ and the history folders")
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    assert installed(kb)["status"] == "open"


def test_confirming_with_a_provisional_source_is_refused(kb, source_repo):
    """Start: the trace has an uncommitted change (a fixture edit), so the blob at the worktree's HEAD is
    not the working file and the staging command cannot pin the source (§5.2.2); `challenge new --lines
    3-3` captures the unchanged line 3. Commands, each bracketed: `challenge new --lines 3-3 --by
    reviewer-a` (writes the staged record and its allocation receipt, the source reference left
    provisional), the author's filling (a fixture edit), `kblam put` (writes the record, the review
    index, the registry and tree.hash; the staged file is gone), `kblam validate` (a read), `kblam
    review decide SC-0001 --status confirmed --by reviewer-b --reason ... --expect D` (refused, exit 1)
    and `kblam validate` again (a read). Files changed: the staged record and its receipt, then the
    record, the review index, the registry and tree.hash, and nothing else — both reads and the refusal
    change nothing. Diagnostics: the K13 issue on SC-0001's `source:` line explains that confirmation
    needs a pinned source, only an open challenge is pinned, and a confirmed record must be restored or
    retired and replaced through staging, filling and put (with a snapshot pin if needed), then the
    refusal line. The source repository is untouched. `validate` exits 0 afterwards (a provisional source is no
    issue while the challenge is open) and the record stays open. Acceptance 5: a confirmation needs
    bytes kblam can read back, not just today's working file."""
    source_repo.write(m.TRACE_PATH, m.TRACE_TEXT + "Row 104: bytes 0x42 0x43\n")
    staged = stage(kb, source_repo, by="reviewer-a", basis=[basis_of(SOURCE_ENTRY)])
    install(kb, source_repo, staged)
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")

    run = refuse_confirmation(kb, source_repo, SC)

    message = (
        "SC-0001 is confirmed with a provisional source; a confirmation needs a pinned source, and only "
        "an open challenge is pinned (a confirmed challenge's source is fixed from the decision that "
        "closes it). Restore it from git, or retire it (kblam review decide SC-0001 --status stale --by "
        "NAME --reason TEXT --expect D) and write a new challenge (kblam challenge new "
        f"{m.TRACE} --lines A-B --by NAME, fill the staged record and kblam put it, which pins the source "
        "when its worktree's HEAD holds those bytes; where it does not, pin the installed challenge with "
        "kblam challenge pin SC-NNNN --expect D --snapshot PATH)")
    refusal(kb, run, message, key="source")
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    assert installed(kb)["status"] == "open"


# --- the basis entry on the source itself, and another basis file ----------------------------------


def test_a_basis_entry_on_the_source_itself_survives_another_commit(kb, source_repo):
    """Start: no findings; the trace is committed and clean. Commands, each bracketed: `challenge new
    --lines 3-3 --by reviewer-a` (writes the staged record and its allocation receipt, the source pinned
    to HEAD's blob), the author's filling (a fixture edit: one basis entry on the source itself, without
    a pin of its own and with `sha256` left for `put`), `kblam put` (writes the record, the review index,
    the registry and tree.hash; the staged file is gone), `kblam review decide SC-0001 --status
    confirmed --by reviewer-b --reason ... --expect D` (writes the record, the review index and
    tree.hash), `kblam validate` (a read). Fixture edit: a second version of the trace is committed, so
    another commit is checked out and the working bytes are no longer the challenged version. Commands
    after it, each bracketed: `kblam validate`, `kblam challenge show SC-0001` (reads). Files changed:
    the staged record and its receipt, then the record, the review index, the registry and tree.hash,
    then the record, the review index and tree.hash, and nothing else — the two reads change nothing in
    the KB or the source repository (acceptance 1). Exit 0 throughout, no diagnostic. `challenge show`
    prints the whole block below: the source is `pinned` at the first commit's blob
    and the basis entry reads at that pin, so it stays available. Acceptance 5: a decision goes stale
    when an input kblam hashed changes — and a basis entry on the source reads the bytes the source was
    pinned to, so checking out another commit does not make it stale."""
    staged = stage(kb, source_repo, by="reviewer-a", basis=[basis_of(SOURCE_ENTRY)])
    install(kb, source_repo, staged)
    confirm(kb, source_repo, SC)
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    pinned_blob = source_repo.blob(m.TRACE_PATH)

    source_repo.commit(m.TRACE_PATH, m.TRACE_TEXT.replace("Row 103", "Row 103 (reread)"), "reread")
    assert source_repo.blob(m.TRACE_PATH) != pinned_blob        # another commit is now checked out

    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    exactly(read(kb, source_repo, "challenge", "show", SC), "\n".join([
        "SC-0001 confirmed",
        f"subject digest: {m.expect(kb, SC)}",
        f"source: {m.TRACE}",
        f"version: {pinned_blob}",
        "state: pinned",
        "assertion: lines 3-3",
        f"  {m.LINE3}",
        f"proposition: {PROPOSITION}",
        "scope: MX-100 capture transcription",
        "classification: contradicted",
        "basis:",
        f"  {SOURCE_ENTRY} pinned: row 102: printed byte values (internal-inconsistency, observed)",
        f"usable: {USABLE}",
        f"limits: {LIMITS}",
        "linked findings: none",
        "decisions:",
        f"  2026-09-28 reviewer-b confirmed: read the source and pinned it "
        f"bind {m.expect(kb, SC)[:12]}",
    ]) + "\n", "challenge show")


def test_another_basis_file_changed_is_a_warning_while_the_challenge_is_open(kb, source_repo):
    """Start: no findings; the trace is committed and clean. Commands, each bracketed: `challenge new
    --lines 3-3 --by reviewer-a` (writes the staged record and its allocation receipt), the author's
    filling (a fixture edit) with two basis entries — the source itself and
    evidence/2026-09-22-ratio/README.md, which no worktree owns and so stays provisional — `kblam put`
    (writes the record, the review index, the registry and tree.hash; the staged file is gone),
    `kblam validate` (a read). Fixture edit: the README's bytes change. Commands after it, each
    bracketed: `kblam validate`, then `kblam challenge show SC-0001` (reads). Files changed: the staged
    record and its receipt, then the record, the review index, the registry and tree.hash, then the
    README (the fixture edit), and nothing else — the three reads change nothing, in the KB or the
    source repository. The validate exits 0 and prints the one K13 warning "basis[1]: the source changed since SC-0001 was written" on the
    record's `basis:` line, then OK; it changes nothing, the source repository is untouched, and
    `challenge show` prints the whole block below with the entry marked stale. Acceptance 5: an input a
    decision would hash has changed, so the challenge is not confirmable as it stands — but while it is
    open the changed basis file is a warning, not a failure."""
    staged = stage(kb, source_repo, by="reviewer-a",
                   basis=[basis_of(SOURCE_ENTRY), basis_of(OTHER_ENTRY, role="counterevidence")])
    install(kb, source_repo, staged)
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")

    (kb.root / OTHER_ENTRY).write_bytes(b"manifest, revised after the reread\n")

    exactly(read(kb, source_repo, "validate"), "\n".join([
        f"K13 warning {CHALLENGE}:{key_line(kb, SC, 'basis')}: basis[1]: the source changed since "
        f"SC-0001 was written",
        OK,
    ]) + "\n", "validate")
    exactly(read(kb, source_repo, "challenge", "show", SC), "\n".join([
        "SC-0001 open",
        f"subject digest: {m.expect(kb, SC)}",
        f"source: {m.TRACE}",
        f"version: {source_repo.blob(m.TRACE_PATH)}",
        "state: current",
        "assertion: lines 3-3",
        f"  {m.LINE3}",
        f"proposition: {PROPOSITION}",
        "scope: MX-100 capture transcription",
        "classification: contradicted",
        "basis:",
        f"  {SOURCE_ENTRY} current: row 102: printed byte values (internal-inconsistency, observed)",
        f"  {OTHER_ENTRY} stale (the working file has other bytes): row 102: printed byte values "
        f"(counterevidence, observed)",
        f"usable: {USABLE}",
        f"limits: {LIMITS}",
        "linked findings: none",
        "decisions:",
        "  none",
    ]) + "\n", "challenge show")


def test_another_basis_file_changed_is_an_error_once_the_challenge_is_confirmed(kb, source_repo):
    """Start: no findings; the trace is committed and clean. Commands, each bracketed: `challenge new
    --lines 3-3 --by reviewer-a` (writes the staged record and its allocation receipt), the author's
    filling (a fixture edit) with the same two basis entries, `kblam put` (writes the record, the review
    index, the registry and tree.hash; the staged file is gone), `kblam review decide SC-0001 --status
    confirmed --by reviewer-b --reason ... --expect D` (writes the record, the review index and
    tree.hash), `kblam validate` (a read). Fixture edit: the README's bytes change after the
    confirmation. Command after it, bracketed, run twice: `kblam validate` (reads). Files changed: the
    staged record and its receipt, then the record, the review index, the registry and tree.hash, then
    the record, the review index and tree.hash, then the README (the fixture edit), and nothing else —
    the three reads change nothing in the KB or the source repository. The initial validate exits 0;
    both post-edit validates exit 1 and print the one K13 error
    "basis[1]: the source changed since SC-0001 was written" on the record's `basis:` line, then "kblam
    validate: 1 error(s) in findings/". It changes nothing, prints the same bytes both times, and leaves
    the source repository untouched. Acceptance 5: the confirmation rests on a file whose bytes changed,
    so it no longer holds, and validate says so rather than rewriting the reference."""
    staged = stage(kb, source_repo, by="reviewer-a",
                   basis=[basis_of(SOURCE_ENTRY), basis_of(OTHER_ENTRY, role="counterevidence")])
    install(kb, source_repo, staged)
    confirm(kb, source_repo, SC)
    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")

    (kb.root / OTHER_ENTRY).write_bytes(b"manifest, revised after the confirmation\n")

    issue = "\n".join([
        f"K13 {CHALLENGE}:{key_line(kb, SC, 'basis')}: basis[1]: the source changed since SC-0001 "
        f"was written",
        FAILED,
    ]) + "\n"
    exactly(read(kb, source_repo, "validate"), issue, "validate", code=1)
    exactly(read(kb, source_repo, "validate"), issue, "validate", code=1)   # the same tree, same bytes


# --- a missing original capture -------------------------------------------------------------------


def test_a_missing_original_capture_is_recorded_as_unsupported_with_its_limits(kb, source_repo):
    """Start: no records and no findings; the trace is committed and clean, and the original capture it
    transcribes does not exist in the KB at all. Commands, each bracketed: `challenge new --lines 3-3
    --by reviewer-a` (writes the staged record and its allocation receipt, the source pinned); the
    author's filling (a fixture edit) with classification `unsupported`, a proposition saying the
    original capture is missing, one basis entry on the source with role `missing-support`, a usable
    remainder and limits that say what may not be inferred; `kblam put` (writes the record, the review
    index, the registry and tree.hash; the staged file is gone); `kblam review decide SC-0001 --status
    confirmed --by reviewer-b --reason ... --expect D` (writes the record, the review index and
    tree.hash); `kblam validate` and `kblam challenge show SC-0001` (reads). Files changed: the staged
    record and its receipt, then the record, the review index, the registry and tree.hash, then the
    record, the review index and tree.hash, and nothing else — the two reads change nothing, in the KB
    or the source repository. Exit 0 throughout, no diagnostic. `validate` exits 0, the review index
    shows the classification,
    and `challenge show` prints the whole block below, usable remainder and limits included. Acceptance
    3: source bytes and inference are not conflated — the record states what survives and what may not
    be inferred from it."""
    staged = stage(kb, source_repo, by="reviewer-a", classification="unsupported",
                   proposition="The MX-100 original capture is missing; only this transcription survives",
                   basis=[basis_of(SOURCE_ENTRY, role="missing-support",
                                   locator="row 102: the transcribed values")],
                   usable="The transcribed byte values may be cited as a transcription, not as wire "
                          "bytes.",
                   limits="Do not infer the original capture's bytes, its timing or the host's routing "
                          "from this row.")
    install(kb, source_repo, staged)

    confirm(kb, source_repo, SC, reason="reread the transcription; nothing else survives")

    exactly(read(kb, source_repo, "validate"), f"{OK}\n", "validate")
    exactly(read(kb, source_repo, "challenge", "show", SC), "\n".join([
        "SC-0001 confirmed",
        f"subject digest: {m.expect(kb, SC)}",
        f"source: {m.TRACE}",
        f"version: {source_repo.blob(m.TRACE_PATH)}",
        "state: current",
        "assertion: lines 3-3",
        f"  {m.LINE3}",
        "proposition: The MX-100 original capture is missing; only this transcription survives",
        "scope: MX-100 capture transcription",
        "classification: unsupported",
        "basis:",
        f"  {SOURCE_ENTRY} current: row 102: the transcribed values (missing-support, observed)",
        "usable: The transcribed byte values may be cited as a transcription, not as wire bytes.",
        "limits: Do not infer the original capture's bytes, its timing or the host's routing from this "
        "row.",
        "linked findings: none",
        "decisions:",
        f"  2026-09-28 reviewer-b confirmed: reread the transcription; nothing else survives "
        f"bind {m.expect(kb, SC)[:12]}",
    ]) + "\n", "challenge show")
    assert "| SC-0001 | 3-3 | unsupported | confirmed |" in (kb.root / INDEX).read_text("utf-8")
