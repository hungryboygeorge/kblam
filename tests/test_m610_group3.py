"""SPEC §12 M6.10 test group 3, K13 (acceptance criterion A2): affected uses, end to end through the
real CLI on the fixture KB and its nested Git source repository. Each test states its start state,
the command and actor, the exit status, the diagnostics, the files changed and the validation that
follows.

Every CLI invocation is bracketed on its own by `command`: a whole-KB `m610_helpers.tree` snapshot
and a `SourceRepo.snapshot()` are taken just before the call and compared just after, so a call that
writes anything but the paths it is expected to write - in the KB or in the source repository -
fails the test. The setup helpers are expanded into their steps (`challenge new`, the author's
filling, `put`, `review decide`, `use review`), and every Run is asserted in full: exit code, whole
stdout and stderr. The fixture's own writes (a finding by hand, an edit or a commit in the source
repository) sit outside the windows.

Findings are placed out of band (`kb.add`), because the put that would install a finding quoting a
confirmed challenge's assertion is itself refused (§5.1.4, test group 4); a state in which such a
finding exists is built before the confirmation. Offline and deterministic: the frozen date pins
`created` and a decision's `date`, and the fixtures enable no Jev verdict.
"""

from __future__ import annotations

from pathlib import Path

import m610_helpers as m
from kblam import records
from kblam.finding import yaml_rt

frozen_today = m.frozen_today

STAGING = ".kblam/review-staging"
RECEIPTS = ".kblam/review-receipts"
SC_RECORD = "research-review/challenges/SC-0001.yaml"
FINDING = "findings/calibration/F-0001-ratio.md"       # where kb.add files F-0001 (CLAIM, slug "ratio")
TAG_LINE = 15                                          # the file line of a body whose tag is its 3rd line
ASSERTION = "the two bytes are equal"                  # the claim inside TRACE_TEXT line 3
NEW_TEXT = "# a heading added above\n" + m.TRACE_TEXT   # another version: every line moves by one


# --- driving one command at a time ----------------------------------------------------------------


def rel(kb, path: Path) -> str:
    """A path as `m610_helpers.tree` names it."""
    return path.relative_to(kb.root).as_posix()


def command(kb, source_repo, expected: set[str], *argv: object) -> m.Run:
    """One CLI invocation in its own window: the KB changes by exactly `expected` paths and the
    source repository's working bytes, HEAD, index and refs are unchanged. The caller asserts the
    Run in full."""
    before_tree, before_source = m.tree(kb), source_repo.snapshot()
    run = m.kblam(kb, *argv)
    assert m.changed(before_tree, m.tree(kb)) == expected, f"after: kblam {' '.join(map(str, argv))}"
    assert source_repo.snapshot() == before_source, f"the source changed: kblam {' '.join(map(str, argv))}"
    return run


def ok_run(run: m.Run, *lines: str) -> m.Run:
    """The run exited 0, printed exactly `lines` on stdout and nothing on stderr."""
    assert (run.code, run.out.splitlines(), run.err) == (0, list(lines), "")
    return run


def put_line(rec_id: str, folder: str) -> str:
    """What `kblam put` prints for an accepted record."""
    return f"kblam put: {rec_id} -> research-review/{folder}/{rec_id}.yaml"


def done_but(what: str, errors: int) -> str:
    """The tail a record write prints when errors owned by others remain (SPEC §5.1.4)."""
    return (f"kblam {what}: done, but kblam validate still fails ({errors} error(s) listed above, "
            f"owned by other findings or records)")


def diagnostic(code: str, message: str, *, line: int = TAG_LINE, level: str = "error") -> str:
    """A rule's diagnostic on F-0001 as validate prints it: `<code>[ warning] <path>:<line>: <msg>`."""
    return f"{code}{' warning' if level == 'warning' else ''} {FINDING}:{line}: {message}"


def k13_line(message: str, *, line: int = TAG_LINE, level: str = "error") -> str:
    """The K13 diagnostic on F-0001 at `line`."""
    return diagnostic("K13", message, line=line, level=level)


def same_bytes(path: str, version: str, *, lines: str = "3-3", ordinal: int = 1) -> str:
    """The §5.1.4 same-bytes error, with the pin's 12-hex version prefix (k13.MESSAGE_SAME_BYTES)."""
    return (f"SC-0001 challenges this quoted assertion at {path}@{version}:{lines}; edit the finding or "
            f"have this use reviewed (kblam use review SC-0001 F-0001 {ordinal} --by NAME --proponent "
            f"NAME). K10 is checked separately.")


def trace_version(source_repo) -> str:
    """The trace's pinned version, as its diagnostics name it: the HEAD blob's first 12 hex digits."""
    return source_repo.blob(m.TRACE_PATH)[:12]


# --- the fixture's own writes (outside every window) ----------------------------------------------


def sc_fields(kb, path: str) -> dict:
    """The blank SC fields the author fills before the first put (SPEC §5.1.5): a proposition, a
    scope, a classification and one basis entry on the source itself, which `put` hashes and pins."""
    return {
        "proposition": "The printed byte equality follows from the printed byte values",
        "scope": ["MX-100 capture transcription"],
        "classification": "contradicted",
        "basis": [{"path": path, "sha256": None, "repo": None, "commit": None, "blob": None,
                   "snapshot": None, "locator": "row 102: printed byte values",
                   "role": "internal-inconsistency", "provenance": kb.cfg.primary_provenance[0]}],
        "usable": "The printed byte values may be cited as a report.",
        "limits": "Do not infer the capture bytes from this row.",
    }


def cu_fields() -> dict:
    """The blank CU fields the reviewer fills before the put."""
    return {"disposition": "unaffected_raw_bytes",
            "reason": "The excerpt is cited only for the printed byte values."}


def fill(staged: Path, **fields: object) -> None:
    """Write the author's filling into a staged record, as kblam writes records (LF endings)."""
    data = yaml_rt().load(staged.read_bytes().decode("utf-8"))
    data.update(fields)
    staged.write_bytes(records.dump(data))


def quoted_trace(kb, finding_id: str, excerpt: str, *, lines: str = "3-3", **kw) -> Path:
    """Install a finding whose one verbatim excerpt is `excerpt`, tagged to trace lines `lines`."""
    return kb.add(finding_id, "ratio", m.CLAIM,
                  body=m.verbatim(f"{m.TRACE}:{lines}", excerpt), **kw)


def pinned(kb, rec_id: str) -> bool:
    """Whether an installed challenge's source carries a Git pin or a snapshot."""
    source = yaml_rt().load(m.record_path(kb, rec_id).read_bytes().decode("utf-8"))["source"]
    return source["repo"] is not None or source["snapshot"] is not None


# --- the commands, each bracketed ----------------------------------------------------------------


def new_challenge(kb, source_repo, path: str, *, lines: str = "3-3", text: str | None = None) -> Path:
    """`challenge new` in its window, then the author's filling of the staged file (fixture setup,
    outside the windows). `text` narrows the captured line as §5.1.3 allows before the first put;
    `put` then computes the assertion's sha256 and occurrence."""
    staged = kb.root / STAGING / "SC-0001.yaml"
    run = command(kb, source_repo, {rel(kb, staged), f"{RECEIPTS}/SC-0001.json"},
                  "challenge", "new", path, "--lines", lines, "--by", "reviewer-a")
    ok_run(run, str(staged))
    fill(staged, **sc_fields(kb, path))
    if text is not None:
        data = yaml_rt().load(staged.read_bytes().decode("utf-8"))
        data["source"]["assertion"].update({"text": text, "sha256": None, "occurrence": None})
        staged.write_bytes(records.dump(data))
    return staged


def install(kb, source_repo, staged: Path, record: str) -> m.Run:
    """`kblam put` of a staged record in its window: the record, the review index, the registry and
    tree.hash are written, and the staged file is removed."""
    return command(kb, source_repo,
                   {".kblam/review-ids", ".kblam/tree.hash", "research-review/INDEX.md", record,
                    rel(kb, staged)},
                   "put", str(staged))


def new_use(kb, source_repo, sc_id: str, ordinal: int, *, rec_id: str) -> Path:
    """`use review` in its window, then the drafted use's filling (fixture setup)."""
    staged = kb.root / STAGING / f"{rec_id}.yaml"
    run = command(kb, source_repo, {rel(kb, staged), f"{RECEIPTS}/{rec_id}.json"},
                  "use", "review", sc_id, "F-0001", str(ordinal),
                  "--by", "reviewer-b", "--proponent", "researcher-a")
    ok_run(run, str(staged))
    fill(staged, **cu_fields())
    return staged


def decide(kb, source_repo, rec_id: str, status: str, *argv: object) -> m.Run:
    """`kblam review decide <rec_id> --status <status> --expect <digest>` in its window."""
    record = f"research-review/{records.KINDS[rec_id[:2]]}/{rec_id}.yaml"
    return command(kb, source_repo, {".kblam/tree.hash", "research-review/INDEX.md", record},
                   "review", "decide", rec_id, "--status", status, "--expect", m.expect(kb, rec_id),
                   *argv)


# --- same bytes -----------------------------------------------------------------------------------


def test_a_finding_quoting_the_assertion_of_a_confirmed_challenge_is_one_k13_error(kb, source_repo):
    """Start: F-0001 quotes trace line 3 verbatim; SC-0001 (creator reviewer-a, one
    primary-provenance basis entry on the source) is confirmed by reviewer-b on lines 3-3 and pinned
    at HEAD. F-0001 was installed before the confirmation, as a finding put would refuse it.

    Commands: `challenge new` (exit 0, the staged path), `put` (exit 0, the record line), `review
    decide --status confirmed --by reviewer-b` (exit 0, the digest line, the finding it newly
    affects and the K13 error it leaves) and `kblam validate`, no actor, exit 1 with exactly the
    §5.1.4 same-bytes message at the excerpt's tag line. Files changed: the staging file and its
    receipt, then the record, the index, the registry and tree.hash, then the record, the index and
    tree.hash again; `validate` changes nothing, and the source repository is unchanged throughout.
    Acceptance 2: an excerpt affected by a confirmed challenge is surfaced."""
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")

    staged = new_challenge(kb, source_repo, m.TRACE)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))

    error = k13_line(same_bytes(m.TRACE, trace_version(source_repo)))
    confirmed = decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                       "--reason", "read the source and pinned it")
    ok_run(confirmed,
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})",
           "kblam review decide: SC-0001 now affects F-0001; run kblam challenge uses SC-0001 for "
           "each excerpt and the command that fixes it",
           error,
           done_but("review decide", 1))

    run = command(kb, source_repo, set(), "validate")
    assert (run.code, run.out.splitlines(), run.err) == (
        1, [error, "kblam validate: 1 error(s) in findings/"], "")


def test_a_current_approved_use_covers_the_excerpt_and_the_validation_is_clean(kb, source_repo):
    """Start: as the test above, with `validate` failing one K13 error: F-0001 quotes the assertion
    of the confirmed, pinned SC-0001.

    Commands, each exit 0 and each in its own window: `challenge new`, `put`, `review decide
    --status confirmed --by reviewer-b`; `use review SC-0001 F-0001 1 --by reviewer-b --proponent
    researcher-a` (prints the staged CU-0001.yaml); `put` of the use (the record line, the K13 error
    it leaves and its tail); `review decide --status approved --by reviewer-b`, who is not the
    proponent; `validate`, exit 0 with no K13 line; `challenge uses SC-0001` and `review list`, both
    read-only. Files changed: the staged files and receipts, then the record, index, registry and
    tree.hash. The source repository is unchanged throughout. Acceptance 2: a current use covers the
    excerpt."""
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")

    staged = new_challenge(kb, source_repo, m.TRACE)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))
    error = k13_line(same_bytes(m.TRACE, trace_version(source_repo)))
    confirmed = decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                       "--reason", "read the source and pinned it")
    ok_run(confirmed,
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})",
           "kblam review decide: SC-0001 now affects F-0001; run kblam challenge uses SC-0001 for "
           "each excerpt and the command that fixes it",
           error,
           done_but("review decide", 1))

    use = new_use(kb, source_repo, "SC-0001", 1, rec_id="CU-0001")
    ok_run(install(kb, source_repo, use, "research-review/uses/CU-0001.yaml"),
           put_line("CU-0001", "uses"), error, done_but("put", 1))

    approved = decide(kb, source_repo, "CU-0001", "approved", "--by", "reviewer-b",
                      "--reason", "the excerpt really is used only for the printed bytes")
    ok_run(approved, f"kblam review decide: CU-0001 is now approved (subject digest "
                     f"{m.expect(kb, 'CU-0001')[:12]})")

    ok_run(command(kb, source_repo, set(), "validate"), "kblam validate: OK (1 findings)")
    ok_run(command(kb, source_repo, set(), "challenge", "uses", "SC-0001"),
           f"F-0001 {FINDING}:{TAG_LINE} excerpt 1 same: covered by CU-0001")
    ok_run(command(kb, source_repo, set(), "review", "list"),
           f"SC-0001 challenge confirmed {m.expect(kb, 'SC-0001')[:12]} {m.TRACE}:3-3 current",
           f"CU-0001 use approved {m.expect(kb, 'CU-0001')[:12]} SC-0001 in F-0001 excerpt 1 current")


def test_only_the_second_of_two_matches_in_the_cited_range_intersects_the_assertion(kb, source_repo):
    """Start: F-0001 quotes "bytes" under a line-3 tag. The word occurs twice on line 3 - at
    "bytes 0x3A", before the assertion, and at "the two bytes are equal", inside it - so the excerpt
    has two matches, both within the cited range. SC-0001's author narrowed the captured assertion
    to "the two bytes are equal" before the first put, and reviewer-b confirmed it on lines 3-3,
    pinned at HEAD.

    Commands: `challenge new`, `put`, `review decide --status confirmed --by reviewer-b`, and
    `kblam validate`, no actor, exit 1 with one K13 error, the same-bytes message: the second match
    intersects, so the excerpt is affected even though the first does not, and the
    overlap-without-quoting warning is not what it gets. Files changed: as the first test; the
    validation changes nothing and the source repository is unchanged. Acceptance 2: an affected
    excerpt is surfaced at the version it quotes."""
    quoted_trace(kb, "F-0001", "bytes")

    staged = new_challenge(kb, source_repo, m.TRACE, text=ASSERTION)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))
    error = k13_line(same_bytes(m.TRACE, trace_version(source_repo)))
    ok_run(decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                  "--reason", "read the source and pinned it"),
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})",
           "kblam review decide: SC-0001 now affects F-0001; run kblam challenge uses SC-0001 for "
           "each excerpt and the command that fixes it",
           error, done_but("review decide", 1))

    run = command(kb, source_repo, set(), "validate")
    assert (run.code, run.out.splitlines(), run.err) == (
        1, [error, "kblam validate: 1 error(s) in findings/"], "")


def test_a_cited_range_that_overlaps_without_quoting_is_a_warning_that_passes_validate(kb,
                                                                                        source_repo):
    """Start: F-0001 quotes line 3's first half, "Row 102: bytes 0x3A 0x3B", which contains none of
    SC-0001's assertion text and is contained in none of it. SC-0001's author narrowed the captured
    assertion to "the two bytes are equal" before the first put, so the assertion's span starts
    after the excerpt; its cited range 3-3 still overlaps the assertion's lines. SC-0001 is
    confirmed by reviewer-b on lines 3-3, pinned at HEAD.

    Commands: `challenge new`, `put`, `review decide --status confirmed --by reviewer-b` - which
    leaves no K13 error, a warning never failing anything - and `kblam validate`, no actor, exit 0
    with one K13 warning naming the cited range and the assertion's lines, then the OK summary.
    Files changed: as the first test; the validation changes nothing and the source repository is
    unchanged. Acceptance 2: a candidate overlap is surfaced without blocking."""
    quoted_trace(kb, "F-0001", "Row 102: bytes 0x3A 0x3B")

    staged = new_challenge(kb, source_repo, m.TRACE, text=ASSERTION)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))
    warning = k13_line(f"the cited range {m.TRACE}:3-3 overlaps lines 3-3 of SC-0001's assertion "
                       f"without quoting it; check that the excerpt does not rely on the challenged "
                       f"text (kblam challenge uses SC-0001 lists what SC-0001 affects)", level="warning")
    ok_run(decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                  "--reason", "read the source and pinned it"),
           warning, f"kblam review decide: SC-0001 is now confirmed (subject digest "
                    f"{m.expect(kb, 'SC-0001')[:12]})")

    ok_run(command(kb, source_repo, set(), "validate"), warning, "kblam validate: OK (1 findings)")


# --- different bytes ------------------------------------------------------------------------------


def test_another_version_quoting_the_assertion_is_version_unproved(kb, source_repo):
    """Start: SC-0001 is confirmed by reviewer-b on trace lines 3-3, pinned at the HEAD blob; a
    heading is then added above the working file (a fixture write), so it is another version and no
    longer the challenged one. F-0001 then quotes the moved line 4, which is still the assertion's
    text.

    Commands: `challenge new`, `put`, `review decide --status confirmed --by reviewer-b` (no finding
    exists yet, so it leaves nothing) and `kblam validate`, no actor, exit 1 with the "version
    unproved" diagnostic - not the confirmed-challenge message - naming the pinned version, the
    lines to challenge and the use to have reviewed. Files changed: as the first test; the
    validation changes nothing and the source repository is unchanged by every command (the fixture
    write sits outside the windows). Acceptance 2: an excerpt of another version that quotes the
    assertion is surfaced as version unproved."""
    staged = new_challenge(kb, source_repo, m.TRACE)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))
    ok_run(decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                  "--reason", "read the source and pinned it"),
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})")

    source_repo.write(m.TRACE_PATH, NEW_TEXT)                  # another version of the source
    m.quoting_finding(kb, "F-0001", source_repo, "4-4")

    unproved = k13_line(
        f"SC-0001 was judged on {m.TRACE}@{trace_version(source_repo)} only, and this excerpt quotes "
        f"its assertion text from another version of that file. This does not show that the version is "
        f"wrong: challenge it (kblam challenge new {m.TRACE} --lines 4-4 --by NAME) or have this use "
        f"reviewed (kblam use review SC-0001 F-0001 1 --by NAME --proponent NAME).")
    run = command(kb, source_repo, set(), "validate")
    assert (run.code, run.out.splitlines(), run.err) == (
        1, [unproved, "kblam validate: 1 error(s) in findings/"], "")
    assert "challenges this quoted assertion" not in run.out


def test_another_version_that_does_not_quote_the_assertion_reports_nothing(kb, source_repo):
    """Start: as the test above (SC-0001 confirmed and pinned at the HEAD blob, the working file
    another version), but F-0001 quotes the moved line 3, "Row 101: bytes 0x3A 0x3B", which holds
    none of the assertion's text and none of which the assertion text holds.

    Commands: `challenge new`, `put`, `review decide --status confirmed --by reviewer-b`, and
    `kblam validate`, no actor, exit 0 with no K13 line at all: kblam does not guess at text shared
    between versions. Files changed: the record, index, registry and tree.hash on the writes; the
    validation changes nothing and the source repository is unchanged. Acceptance 2: only an excerpt
    quoting the assertion is surfaced across versions."""
    staged = new_challenge(kb, source_repo, m.TRACE)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))
    ok_run(decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                  "--reason", "read the source and pinned it"),
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})")

    source_repo.write(m.TRACE_PATH, NEW_TEXT)                  # another version of the source
    m.quoting_finding(kb, "F-0001", source_repo, "3-3")

    ok_run(command(kb, source_repo, set(), "validate"), "kblam validate: OK (1 findings)")


# --- references -----------------------------------------------------------------------------------


def test_an_evidence_entry_and_prose_that_name_the_challenged_source_are_warnings(kb, source_repo):
    """Start: SC-0001 is confirmed by reviewer-b on lines 3-3, pinned at HEAD. F-0001 then lists the
    challenged source in `evidence` (its frontmatter line 7) and names the same path in prose on the
    body's first line (13); it quotes nothing.

    Commands: `challenge new`, `put`, `review decide --status confirmed --by reviewer-b`, then
    `kblam validate`, no actor, exit 0 with one warning for the evidence entry and one for the prose
    text, both naming SC-0001 and the command that lists it, then the OK summary: a path-only
    reference is never classified as safe, and a warning never fails. `challenge uses SC-0001`, also
    read-only, prints the same two relations. Files changed: as the first test; the read-only
    commands change nothing and the source repository is unchanged. Acceptance 2: a reference that
    only names the challenged source is surfaced as a warning."""
    staged = new_challenge(kb, source_repo, m.TRACE)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))
    ok_run(decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                  "--reason", "read the source and pinned it"),
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})")

    kb.add("F-0001", "ratio", m.CLAIM, evidence=f"[{m.TRACE}]",
           body=f"See {m.TRACE} for the trace.")

    tail = "(kblam challenge uses SC-0001 lists what SC-0001 affects)"
    ok_run(command(kb, source_repo, set(), "validate"),
           k13_line(f"evidence lists {m.TRACE}, which SC-0001 challenges; a listed source is not "
                    f"shown to be safe, so check what this finding takes from it against SC-0001's "
                    f"assertion and limits {tail}", line=7, level="warning"),
           k13_line(f"the text names {m.TRACE}, which SC-0001 challenges; a named source is not "
                    f"shown to be safe, so check that the claim does not rest on SC-0001's "
                    f"assertion {tail}", line=13, level="warning"),
           "kblam validate: OK (1 findings)")
    ok_run(command(kb, source_repo, set(), "challenge", "uses", "SC-0001"),
           f"F-0001 {FINDING}:7 evidence: warning; kblam edit F-0001",
           f"F-0001 {FINDING}:13 prose: warning; kblam edit F-0001")


# --- the right span -------------------------------------------------------------------------------


def crlf_source(source_repo) -> tuple[str, bytes, int, int]:
    """Commit a CRLF, multibyte source: (its KB path, its bytes, the byte offset of the assertion
    text's start, the byte offset of its line's start)."""
    data = "Aé\r\nRow 102: bytes 0x3A 0x3B; the two bytes are equal.\r\n".encode("utf-8")
    source_repo.commit("notes/crlf.md", data)
    path = source_repo.kb_path("notes/crlf.md")
    return path, data, data.index(b"the two bytes"), data.index(b"Row 102")


def test_an_offset_tag_in_a_crlf_multibyte_source_maps_inside_the_assertion_span(kb, source_repo):
    """Start: notes/crlf.md holds a multibyte (é) and CRLF source; SC-0001's author narrowed its
    captured line 2 to the assertion "the two bytes are equal", and reviewer-b confirmed it on lines
    2-2, pinned at HEAD. F-0001's offset tag then points at the raw byte offset of "the two bytes".

    Commands: `challenge new` on the new file, `put`, `review decide --status confirmed --by
    reviewer-b` (no finding yet), and `kblam validate`, no actor, exit 1 with one K13 error: the
    raw-byte match is mapped into the LF-normalised text and lands inside the assertion's span,
    despite the two bytes of CRLF and the multibyte character before it. Files changed: as the first
    test; the validation changes nothing and the source repository is unchanged (the fixture commit
    of the new file sits outside the windows). Acceptance 2: an offset tag is matched in the raw
    bytes and mapped to the right span."""
    path, data, inside, _outside = crlf_source(source_repo)
    staged = new_challenge(kb, source_repo, path, lines="2-2", text=ASSERTION)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))
    ok_run(decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                  "--reason", "read the source and pinned it"),
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})")

    kb.add("F-0001", "ratio", m.CLAIM, body=m.verbatim(f"{path}:@0x{inside:X}", "the two bytes"))

    error = k13_line(same_bytes(path, source_repo.blob("notes/crlf.md")[:12], lines="2-2"))
    run = command(kb, source_repo, set(), "validate")
    assert (run.code, run.out.splitlines(), run.err) == (
        1, [error, "kblam validate: 1 error(s) in findings/"], "")


def test_an_offset_tag_before_the_assertion_span_in_the_same_source_reports_nothing(kb, source_repo):
    """Start: as the test above, with SC-0001's author having narrowed the captured assertion to "the
    two bytes are equal", but F-0001's offset tag points at the start of line 2 and its excerpt stops
    before the assertion text.

    Commands: `challenge new`, `put`, `review decide --status confirmed --by reviewer-b`, and
    `kblam validate`, no actor, exit 0 with no K13 line: the mapped span lies before the assertion's
    span, and an offset tag never gets the cited-range warning. Files changed: as the first test; the
    validation changes nothing and the source repository is unchanged. Acceptance 2: the same source
    yields a diagnostic only where the span really intersects."""
    path, _data, _inside, outside = crlf_source(source_repo)
    staged = new_challenge(kb, source_repo, path, lines="2-2", text=ASSERTION)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))
    ok_run(decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                  "--reason", "read the source and pinned it"),
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})")

    kb.add("F-0001", "ratio", m.CLAIM,
           body=m.verbatim(f"{path}:@0x{outside:X}", "Row 102: bytes 0x3A 0x3B;"))

    ok_run(command(kb, source_repo, set(), "validate"), "kblam validate: OK (1 findings)")


# --- what a use can and cannot cover ---------------------------------------------------------------


def test_use_review_refuses_a_binary_exempt_excerpt(kb, source_repo):
    """Start: SC-0001 is confirmed by reviewer-b on lines 3-3, pinned at HEAD. The trace's working
    file is then replaced by binary bytes (a fixture write; the pinned challenge stays confirmed and
    available), so F-0001's excerpt on it is binary-exempt - K10 does not check it and K13 does not
    report it.

    Commands: `challenge new`, `put`, `review decide --status confirmed --by reviewer-b`, then `use
    review SC-0001 F-0001 1 --by reviewer-b --proponent researcher-a`, exit 1 with the binary-exempt
    refusal: a binary-exempt excerpt never qualifies as a use. Files changed: the three setup
    commands as the first test, and the refusal changes nothing - no CU- record is staged, no
    receipt written. `validate` afterwards exits 0 with no K10 or K13 line, and the source repository
    is unchanged by every command. Acceptance 2: a binary-exempt excerpt can never be covered."""
    staged = new_challenge(kb, source_repo, m.TRACE)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))
    ok_run(decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                  "--reason", "read the source and pinned it"),
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})")

    source_repo.write(m.TRACE_PATH, b"\x00\x01binary")
    kb.add("F-0001", "ratio", m.CLAIM, body=m.verbatim(f"{m.TRACE}:@0x0", "anything at all"))

    run = command(kb, source_repo, set(), "use", "review", "SC-0001", "F-0001", "1",
                  "--by", "reviewer-b", "--proponent", "researcher-a")
    assert (run.code, run.out, run.err) == (
        1, "", "kblam use review: excerpt 1 of F-0001 is binary-exempt, and a binary-exempt excerpt "
              "never qualifies as a use\n")
    assert not (kb.root / STAGING / "CU-0001.yaml").exists()

    ok_run(command(kb, source_repo, set(), "validate"), "kblam validate: OK (1 findings)")


def test_two_identical_tag_and_block_copies_are_separate_uses_by_ordinal(kb, source_repo):
    """Start: F-0001's body holds the same tag and blockquote twice, both quoting trace line 3, so it
    has two excerpts with the same tag_sha256; SC-0001 is confirmed by reviewer-b on lines 3-3,
    pinned at HEAD.

    Commands, each in its own window and each exit 0: `challenge new`, `put`, `review decide --status
    confirmed --by reviewer-b` (which lists both K13 errors it leaves); `validate` exits 1 with one
    error per copy, naming excerpt 1 and excerpt 2; `use review ... 1`, `put` (which lists both
    errors) and `review decide --status approved --by reviewer-b` (which lists the one left) cover
    the first copy only; `validate` then exits 1 with the single error for excerpt 2, whose recovery
    command names ordinal 2. Repeating the three commands for ordinal 2 exits 0 each and leaves
    `validate` clean, with two current uses in `review list`. Files changed: the staging file and its
    receipt, then the record, index, registry and tree.hash, per round trip. The source repository is
    unchanged throughout. Acceptance 2: identical copies are separate uses, told apart by ordinal."""
    block = m.verbatim(f"{m.TRACE}:3-3", m.LINE3)
    kb.add("F-0001", "ratio", m.CLAIM, body=f"{block}\n\n{block}")

    second_copy = TAG_LINE + 5        # the copy's tag, five body lines below the first (m.verbatim)
    version = trace_version(source_repo)
    first_error = k13_line(same_bytes(m.TRACE, version))
    second_error = k13_line(same_bytes(m.TRACE, version, ordinal=2), line=second_copy)

    staged = new_challenge(kb, source_repo, m.TRACE)
    ok_run(install(kb, source_repo, staged, SC_RECORD), put_line("SC-0001", "challenges"))
    ok_run(decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                  "--reason", "read the source and pinned it"),
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})",
           "kblam review decide: SC-0001 now affects F-0001; run kblam challenge uses SC-0001 for "
           "each excerpt and the command that fixes it",
           first_error, second_error, done_but("review decide", 2))

    run = command(kb, source_repo, set(), "validate")
    assert (run.code, run.out.splitlines(), run.err) == (
        1, [first_error, second_error, "kblam validate: 2 error(s) in findings/"], "")

    use1 = new_use(kb, source_repo, "SC-0001", 1, rec_id="CU-0001")
    ok_run(install(kb, source_repo, use1, "research-review/uses/CU-0001.yaml"),
           put_line("CU-0001", "uses"), first_error, second_error, done_but("put", 2))
    ok_run(decide(kb, source_repo, "CU-0001", "approved", "--by", "reviewer-b",
                  "--reason", "the excerpt really is used only for the printed bytes"),
           f"kblam review decide: CU-0001 is now approved (subject digest "
           f"{m.expect(kb, 'CU-0001')[:12]})",
           second_error, done_but("review decide", 1))

    run = command(kb, source_repo, set(), "validate")
    assert (run.code, run.out.splitlines(), run.err) == (
        1, [second_error, "kblam validate: 1 error(s) in findings/"], "")

    use2 = new_use(kb, source_repo, "SC-0001", 2, rec_id="CU-0002")
    ok_run(install(kb, source_repo, use2, "research-review/uses/CU-0002.yaml"),
           put_line("CU-0002", "uses"), second_error, done_but("put", 1))
    ok_run(decide(kb, source_repo, "CU-0002", "approved", "--by", "reviewer-b",
                  "--reason", "the second copy is used for the printed bytes too"),
           f"kblam review decide: CU-0002 is now approved (subject digest "
           f"{m.expect(kb, 'CU-0002')[:12]})")

    ok_run(command(kb, source_repo, set(), "validate"), "kblam validate: OK (1 findings)")
    ok_run(command(kb, source_repo, set(), "review", "list"),
           f"SC-0001 challenge confirmed {m.expect(kb, 'SC-0001')[:12]} {m.TRACE}:3-3 current",
           f"CU-0001 use approved {m.expect(kb, 'CU-0001')[:12]} SC-0001 in F-0001 excerpt 1 current",
           f"CU-0002 use approved {m.expect(kb, 'CU-0002')[:12]} SC-0001 in F-0001 excerpt 2 current")


# --- what K13 leaves to others --------------------------------------------------------------------


def test_an_excerpt_failing_k10_is_reported_by_k10_alone(kb, source_repo):
    """Start: F-0001 quotes "Row 102: bytes 0x3A 0x3C; the two bytes are equal." under a line-3 tag.
    The source prints 0x3B, so the excerpt occurs nowhere and K10 fails it; it contains the
    assertion's text, so K13 would relate it if K10 had passed it. SC-0001 is confirmed by
    reviewer-b on lines 3-3, pinned at HEAD.

    Commands: `challenge new`, `put` (which lists the K10 error owned by F-0001), `review decide
    --status confirmed --by reviewer-b` (which lists it again), and `kblam validate`, no actor, exit
    1 with the K10 failure alone - no K13 line names the excerpt. Files changed: as the first test;
    the validation changes nothing and the source repository is unchanged. Acceptance 2: K13 never
    waives K10, and an unchecked excerpt is K10's to report."""
    quoted_trace(kb, "F-0001", "Row 102: bytes 0x3A 0x3C; the two bytes are equal.")

    staged = new_challenge(kb, source_repo, m.TRACE)
    k10 = diagnostic("K10", f"the excerpt does not occur verbatim in {m.TRACE}:3-3; copy the text "
                            f"exactly from the source (no paraphrase, no reflowing)")
    ok_run(install(kb, source_repo, staged, SC_RECORD),
           put_line("SC-0001", "challenges"), k10, done_but("put", 1))
    ok_run(decide(kb, source_repo, "SC-0001", "confirmed", "--by", "reviewer-b",
                  "--reason", "read the source and pinned it"),
           f"kblam review decide: SC-0001 is now confirmed (subject digest "
           f"{m.expect(kb, 'SC-0001')[:12]})",
           k10, done_but("review decide", 1))

    run = command(kb, source_repo, set(), "validate")
    assert (run.code, run.out.splitlines(), run.err) == (
        1, [k10, "kblam validate: 1 error(s) in findings/"], "")
    assert not [line for line in run.out.splitlines() if line.startswith("K13 ")]
