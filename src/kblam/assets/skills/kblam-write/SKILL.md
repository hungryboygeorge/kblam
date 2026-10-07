---
name: kblam-write
description: Add, change or correct a finding in {{kb_root}}/ (the kblam knowledge base), or challenge a source, task a claim or review a use under {{review_root}}/, and handle any kblam refusal - a denied write under {{kb_root}}/, {{review_root}}/ or .kblam/ or to kblam.toml, a kblam put rejection (exit 1, 3 or 4), a Stop hook block, a failing kblam validate or pre-commit, a review or unchecked item, a suspect dependency, a check that kblam recheck reports as failed or not approved, or a K13, K14 or K15 refusal.
---

# Writing findings with kblam

`{{kb_root}}/` holds only current facts, one claim per file. `kblam put` is the only way in: a
Write, Edit or shell write under `{{kb_root}}/` is denied, and one that slips through is caught when
you stop. Each refusal names the finding and the rule that fired, and what to do.

`.kblam/` holds kblam's own state (the review items, the verdict cache, the lock and the receipts of
the review records), and only kblam writes it: a Write, Edit, shell write or removal there is denied.
Two folders are yours to edit: `.kblam/staging/`, for staged findings, and `.kblam/review-staging/`,
for staged records. `kblam.resolutions.jsonl` is kblam's too: only `kblam resolve` writes it.

`kblam.toml` sets the rules and where kblam sends its API key, so only a person changes it: writing
or removing it is denied too, and a commit that changes it is refused until a person approves it with
`kblam approve-config` at a terminal. If a finding needs something it does not allow, such as a new
scope, ask the user to add it. Never try to approve a change yourself.

## The flow

1. `kblam new <topic> "<title>"` stages a skeleton for a new finding and prints its path
   (`.kblam/staging/F-NNNN-<slug>.md`). To change an existing finding: `kblam edit F-NNNN`.
2. Edit the staged file.
3. `kblam put <staged file>`. It validates the knowledge base as it would be after the move, runs
   the Jev check, moves the file in and regenerates `{{kb_root}}/INDEX.md`. On any refusal
   `{{kb_root}}/` is unchanged and the staged file stays: fix it and put it again. Errors in your
   finding, or ones the move causes in others, refuse the put; errors that were already in other
   findings are printed as warnings and do not.

A wrong finding is corrected by `kblam edit`ing it so it states only what is true now. Never write
a second finding that corrects it, and never leave "previously", "was wrong" or similar notes:
history is in git and `evidence/`. A negative result is a current fact: "X does not do Y;
evidence: ...".

## Format

```markdown
---
id: F-0137                     # matches the filename; never changes
title: The two sensor curve types are not two analog gains
topic: calibration             # the folder under {{kb_root}}/
label: observed                # observed | decoded | inferred | unknown | reported
scope: [MX-200]                # values from kblam.toml [kb] scopes
evidence:                      # at least one repo-relative path that exists
  - evidence/2026-09-22-sensor-type-ratio/
depends_on: {F-0102: null}     # optional; put stamps F-0102's fingerprint (you assert you read it)
anchors: ["0x1A2B3C"]          # optional; quoted strings
quantities:                    # optional; code compares these, not Jev
  - {name: type1/type0 curve ratio, value: 1.0017, unit: ratio}
check: "uv run python evidence/2026-09-22-sensor-type-ratio/derived/ratio.py"   # optional; see Checks
verified: 2026-09-22
---

**Claim.** The first paragraph is the claim: prose, at most 250 words by default.

Supporting detail after it.

<!-- verbatim: evidence/2026-09-22-sensor-type-ratio/log.txt:12-13 -->
> text copied exactly from lines 12-13 of that file
```

- A finding may cite a file in a source repository under `resources/` (for example
  `resources/mx-docs/`); that folder, or an enclosing folder such as `resources`, must then be in
  `[kb] evidence_roots`. A review record that references the source does not change that, and only
  a person changes `kblam.toml`, so ask the user to add it.
- The file is at most 300 lines by default (`kblam.toml` sets both limits).
- A verbatim tag goes on the line directly before a fenced block or blockquote; `:@0x1F0` cites a
  byte offset. The excerpt must occur exactly in that range: copy it, never retype it. To quote a
  binary file, end the tag with ` hex` (`<!-- verbatim: path:@0x1F0 hex -->`) and give the bytes as
  hex pairs in a fenced block, copied from a hex dump.
- When `kblam.toml` sets `verbatim_blockquotes` (a new project does), every blockquote needs a
  verbatim tag. A quotation you cannot tag is paraphrase, and belongs in prose.
- Two findings that give the same quantity name must give the same value and unit.

## Refusals

`kblam put` exits 1 for a K rule or a request kblam will not carry out, 3 when another kblam
write held `.kblam/lock` too long (retry), and 4 for a Jev or quantity reject. Exit 2 means no
usable `kblam.toml` or bad arguments. `kblam recheck` exits 1 when a check failed, could not run
or is not approved (see "Checks").

| Rule | Trigger | Fix |
|---|---|---|
| K1 | frontmatter schema, ID, topic, label or scope; an old-format `depends_on` stamp (8 digits) | the field it names; for an old stamp see "Old formats" |
| K2 | evidence missing, not found or outside the evidence folders; `depends_on` names no finding | cite paths that exist, under an evidence folder; for a file in a source repository under `resources/`, ask the user to add its folder, or `resources`, to `[kb] evidence_roots` |
| K3 | a `depends_on` fingerprint is stale or missing | re-read the target; in a staged file set it to `null` |
| K4 | revision-history language | state the current fact directly |
| K5 | another finding's ID next to such language | `kblam edit` that finding instead |
| K6 | file or claim too long, or no prose claim first | cut detail or split independent claims |
| K7 | `INDEX.md` differs from the generated one | `kblam index` |
| K8 | a file under `{{kb_root}}/` that is not a finding | remove it |
| K9 | the claim nearly duplicates another finding's | `kblam edit` that finding instead |
| K10 | a verbatim excerpt is not in its cited range | copy the text exactly, or fix the range |
| K11 | a `reported` finding quotes no retired document; `history/` cited as evidence; a finding depends on a `reported` one | see "Reported findings" |
| K12 | a blockquote without a verbatim tag | tag it and copy the text exactly, or make it prose |

Jev and quantity rejects (exit 4) report every verdict that fired, review ones included:
`same_fact` (the existing finding already states this), `cannot_both_be_true` (probable conflict),
`revision` (this reads as a correction), `quantity_conflict` (same quantity, different value).

"F-NNNN changed since your edit" means another write replaced it: move your staged copy out of
`.kblam/staging/`, `kblam edit F-NNNN` again and reapply your change.

## Source challenges and claim tasks

`{{review_root}}/` holds three kinds of record: **source challenges** (`SC-…`, under `challenges/`),
**claim tasks** (`CT-…`, under `tasks/`) and **reviewed uses** (`CU-…`, under `uses/`). A challenge
says a quoted passage of a source is contradicted, unsupported or from the wrong model; a task asks
for work that would confirm or refute a finding's claim; a use records that an excerpt quotes a
confirmed challenge's assertion but draws only on bytes the challenge leaves alone. They are written
like findings: draft one with the kblam command below, edit it under `.kblam/review-staging/`, then
`kblam put` it. A write under `{{review_root}}/` or `.kblam/review-receipts/` is denied, and so is
removing a record file, a kind folder or the review root itself, because the records and the receipts
are kblam's to write; removing a stray file there is yours to do, and it is how a K13 stray-file error
is fixed.

**Challenging a source.** `kblam challenge new <source-path> --lines A-B --by NAME` captures lines A-B
exactly as the assertion and stages the record, already pinned when the source's committed bytes are
the working ones. Write what the source is claimed to get wrong (`proposition`, `scope`,
`classification`), at least one `basis` entry with its `locator`, `role` and `provenance`, and what
remains `usable` and its `limits`, then put it. Before the first put the assertion may be narrowed
inside the captured lines, never widened; after it the assertion never changes, and a source that
changed needs a new challenge, with the old one retired.

An independent reviewer reads the record, fixes the source version and decides - in that order,
because pinning changes the subject digest that `--expect D` takes:

```
kblam challenge show SC-0001
kblam challenge pin SC-0001 --expect D
kblam challenge show SC-0001
kblam review decide SC-0001 --status confirmed --by NAME --reason TEXT --expect D --evidence PROVENANCE:PATH:LOCATOR
```

Each `show` prints the digest for the next command's `--expect D`, or at least its first 12 hex
digits; a command refuses with "SC-0001 changed since you inspected it; show it again" when the record
changed since. Pin only what is still provisional: a challenge on a source whose committed bytes are
the working ones is pinned already and `pin` refuses it ("a pin is never replaced"), while a
successful pin changes the digest, so `show` again before deciding. A dirty working file is pinned by
committing the exact bytes it holds; a CRLF checkout is not, because the commit holds the LF bytes
while the working bytes differ, so a project with a normalizing checkout (`core.autocrlf`) pins it
with `--snapshot PATH`, a project-owned copy of those bytes. "only an open challenge can be pinned".

**What a confirmation needs.** The source must be available and pinned, every `basis` entry must be
available, and at least one must be primary support: a provenance in `[review] primary_provenance`
whose path is outside the knowledge base, the review root and the history folders. A paraphrase in the
knowledge base is not primary support, and `--evidence` on `decide` does not stand in for one:
confirming without it is refused with "with no primary support; a confirmation needs a basis entry
whose provenance is one of observed, decoded". A `contradicted` challenge also needs a basis entry
with role `counterevidence` or `internal-inconsistency`, and a `wrong_model` one a `model-mismatch`
entry ("no basis entry has role counterevidence or internal-inconsistency", "no basis entry has role
model-mismatch"). The challenge is still open while that is fixed: `kblam challenge edit SC-0001`,
amend the basis, put it, `show` it again and decide. A challenge is confirmed or rejected by someone
other than its creator; a confirmed challenge is never reopened, only retired (`--status stale`), and
that retirement needs someone other than its creator too.

**After a confirmation.** `kblam challenge uses SC-0001` lists every finding excerpt the challenge
affects, each with the command that fixes it. Edit the finding: narrow the claim to what the usable
remainder supports, or remove the excerpt. Where the finding draws only on the raw bytes, anyone may
draft a use: `kblam use review SC-0001 F-0012 2 --by NAME --proponent NAME` (the proponent is the
finding's author), then fill its `disposition` and `reason` and put it. `unaffected_raw_bytes` names
the subset of bytes the excerpt is still used for; `rewritten_claim` says the finding's claim no
longer relies on the challenged interpretation. Both need the `reason`, and a use never turns a false
interpretation into a fact. A reviewer other than the proponent approves it with
`kblam review decide CU-0001 --status approved --by NAME --reason TEXT --expect D`.

Approving is refused unless the use is current once approved: its challenge confirmed with an
available source, its finding's fingerprint and bytes the bound ones, and the excerpt at its ordinal
still a verified K10 match. Editing that finding makes the use stale - the excerpt is a K14 error
again and the use's binding a K13 warning - and approving it again is refused ("a decision that keeps
the status belongs to kblam review rebind"). A reviewer rechecks the excerpt in the new revision and
rebinds it with `kblam review rebind CU-0001 --by NAME --reason TEXT --expect D`. A rebind keeps the
excerpt at the cited ordinal, or the only excerpt that still carries its `tag_sha256`; when none or
several do - "no longer has the excerpt it cites" - stage a new use with `kblam use review`.

**Tasking a claim.** `kblam task new F-0014 --kind replication --by NAME --proponent NAME` stages a
task bound to the finding's current fingerprint and file bytes. Write the `question`, `method`, the
three `outcomes` (`supports`, `refutes`, `inconclusive`), `controls`, `stop` and `expected_evidence`,
then put it. Do the work into an `evidence/` package, and a reviewer who is neither the creator nor
the proponent decides with that evidence:

```
kblam review decide CT-0001 --status confirmed --by NAME --reason TEXT --expect D --evidence PROVENANCE:PATH:LOCATOR
```

`confirmed` (`outcomes.supports`) and `not_reproduced` (`outcomes.refutes`) need a primary evidence
entry; `inconclusive` does not, and says only why no further work would decide the question. A pending
task - open, well formed, its binding current - is listed by `kblam validate` and fails nothing.

**Editing a record.** `kblam challenge edit SC-0001` and `kblam task edit CT-0001` stage a copy of an
open record; the free fields above are yours to change and every other field must be put as installed
("SC-0001 changed since your edit; run kblam challenge edit SC-0001 again" means another write
replaced it). A use has no edit command: a changed use needs a new `kblam use review`, or
`kblam review rebind` when only its bindings moved. `kblam review list` shows each record's status and
whether its binding is still current; `kblam review list --open` shows the open ones.

**K13 to K15 refusals.** K13 is record integrity, K14 is an affected use, K15 is a task binding. A
record put that refuses one writes nothing. A finding put is refused only when it adds an affected
excerpt; an edit that keeps an affected excerpt the installed finding already had goes through, and
the excerpt stays a K14 error until a use is reviewed or rebound, or the excerpt is removed.

| Rule | Trigger | Fix |
|---|---|---|
| K13 | a file under `{{review_root}}/` that is not a record or the generated `INDEX.md` ("`{{review_root}}/` holds only SC-, CT- and CU- records in their kind's folder"), or a record or `INDEX.md` that is a symlink | remove the stray file, or replace the link with the file itself |
| K13 | "INDEX.md is missing; run kblam review index", or "INDEX.md differs from the generated review index", because a hand edit or a new record left it behind | `kblam review index` |
| K13 | a schema error: an unknown or missing key, a wrong type, a bad path or hash, a blank required field, an assertion that no longer matches its source | the diagnostic names the field or the file: fix the staged record and put it again |
| K13 | an ID claimed by two files, or a record whose `id` differs from its file name | restore the right file from git; records are never renamed |
| K13 | a registered record missing from `{{review_root}}/`: `records are never deleted or renamed` | restore it from git |
| K13 | a decided record was changed by hand: the last decision's `bind` no longer matches the record's subject digest | restore the record from git; a decided record cannot be edited |
| K13 | `the source changed since kblam challenge new; run it again`: the staged record's source no longer holds the bytes `challenge new` captured | start that record again against the bytes you now mean to challenge (`kblam challenge new`) |
| K13 | a source, basis or decision-evidence reference of an installed record is stale or unavailable: "the source changed since SC-0001 was written", "the pinned version is not present" | a warning while the record is open and an error once it is effective: restore the original or pinned bytes, or retire the record and file a new one |
| K13 | a decision the transition table refuses, or one that is not independent: `a closing decision needs someone else` | `--by` must differ from a challenge's `creator`, a task's `creator` and `proponent`, or a use's `proponent` |
| K14 | `SC-0001 challenges this quoted assertion at` ...: the excerpt quotes an assertion a confirmed challenge covers | edit the finding, or have its use reviewed (`kblam use review SC-0001 F-0012 2 --by NAME --proponent NAME`) |
| K14 | the excerpt quotes the assertion text of another version: `was judged on` one version `and this excerpt quotes its assertion text from another version of that file` | challenge that version too (`kblam challenge new`), or have this use reviewed |
| K14 | a warning: a line tag's cited range overlaps the assertion's lines, or the finding lists or names the challenged source | read what the finding takes from it; a warning never fails `kblam validate` |
| K15 | an `open` task's binding no longer matches: the finding's fingerprint or file bytes changed since `kblam task new` bound the task to it | reread the finding, then `kblam review rebind CT-0001 --by NAME --reason TEXT --expect D`, with `--reopen` when the task should be left open again |
| K15 | a `confirmed` or `not_reproduced` task's binding no longer matches, or its effective evidence is gone, or it was never primary | rebind, citing the primary evidence again whatever the old decision cited: `kblam review rebind CT-0001 --by NAME --reason TEXT --expect D --evidence PROVENANCE:PATH:LOCATOR` (`--reopen` too, to leave the task open). A `stale` task is never rebound: draft a new one |
| K15 | an `inconclusive` task's binding no longer matches, or an evidence entry of its decision is unavailable | rebind, and recheck what it cited: `kblam review rebind CT-0001 --by NAME --reason TEXT --expect D` (restore those bytes, or cite the replacement with `--evidence PROVENANCE:PATH:LOCATOR`). `inconclusive` needs no primary evidence entry, but it is an effective status all the same: the rebind needs someone independent of the task, unlike an `open` task, which its own creator or proponent may rebind |

Only errors fail `kblam validate`: a warning (exit 0) is not a failure, and a pending task is not a
failure either. `kblam validate --record --forget-missing` is different from all of the above: it is
"reserved for the coordinator or the user". It forgets a registered ID whose record is gone instead of
restoring it, and an author never runs it to make a missing-record error go away.

## Reported findings

Label a claim `reported` when a retired document (under `history/`) states it and nothing in the
tree reproduces it: say so in the claim ("A20 of the retired calibration document reports ...;
no script in the tree reproduces it"), and quote the source lines with a verbatim excerpt from
`history/`. List the captures it was measured on in `evidence`, with the history file. A claim a
later document or finding contradicts is not reported: it is left out. `history/` is not evidence
for any other label, and no other finding may `depends_on` a reported one. To promote one,
reproduce it in an `evidence/` package, then `kblam edit` it: new label, the package's output as
its excerpt, the history excerpt removed.

## A verdict that names an existing finding: edit that finding

`same_fact`, `restates_and_extends`, `cannot_both_be_true`, `quantity_conflict` and K9 name an
existing finding. Run `kblam edit` on **that** finding so it states the current fact, including
your new detail or correction, and put it; drop the new finding if nothing is left for it. If your
new finding is the wrong one, correct it. If two quantities measure different things, rename one.
A `revision` reject means the same: rewrite the original finding instead of writing a correction.

Do not reword the new finding until it gets past K9 or Jev. The reject means the fact is already
in the knowledge base. A reworded copy would put one fact in two findings that later drift apart,
or leave two findings that contradict each other, and the next reader would find whichever one
their search hits first. That is the failure kblam exists to stop; the check can be fooled by
rewording, so passing it that way fixes nothing.

A reject is not always right: Jev sometimes reads two findings that state distinct facts as one
fact, or a direct statement as a correction, so each rejecting verdict is also recorded as a
rejected item (`R-…`) in `.kblam/review.jsonl` and printed with its ID. A rejected item is not in
`{{kb_root}}/`, so it does not fail `kblam validate`: send the ID to the coordinator (or the
librarian if one is deployed) and carry on. When Jev did misread it, they close it with
`kblam resolve R-… --distinct "<reason>"`, and putting the unchanged staged file then goes through.

## After a rewrite: suspect dependents

`put` lists the findings that depend on the one you changed ("F-x is now suspect"), and
`kblam validate` fails until each is resolved. `kblam deps F-x` shows them. Re-read the rewritten
finding, then either `kblam ack F-x F-target` (F-x still holds as written) or `kblam edit F-x`.
Never ack without re-reading.

## Review items

The write is accepted, but `kblam validate` (and so pre-commit and the Stop hook) fails while the
item is open:

- `restates_and_extends`: the new finding restates an existing one and adds detail; move the
  detail into the existing finding (`kblam edit`).
- `low_confidence`: Jev could not place the pair; read both findings.
- Reject verdicts that `kblam check`, `kblam audit` or the Stop hook find on findings already in
  `{{kb_root}}/`.

Changing either finding's claim, label, scope, quantities or evidence closes the item (the put
re-checks the pair and raises a new item if a verdict still fires). `kblam items` lists the open
items. The coordinator, or the librarian if one is deployed, decides each item, never the author
whose write raised it. The one exception: the librarian may close a `low_confidence` item its own
write raised, with a written reason. An author sends the item IDs to the librarian and carries on.

- A real restatement (`same_fact`, `restates_and_extends`) is merged: the finding that stays, A,
  takes the other finding B's detail, and B is removed with `kblam rm B --merged-into A`. Remove B
  before the put, not after: `kblam edit A`, add B's detail to the staged copy,
  `kblam rm B --merged-into A`, then `kblam put <staged file>`; the removal leaves A's bytes
  alone, so the staged copy still puts. A put of A that states B's fact is refused while B is
  installed, by K9 ("claim duplicates B") or by a `same_fact` reject (exit 4) whose text says to
  edit B: two findings that state one fact cannot both be in `{{kb_root}}/`, and here B is the one
  being merged away, so remove it instead of editing it. When B gives a quantity A lacks,
  `kblam rm B` is refused for it: `kblam edit A`, add the quantity with its value and unit and leave
  the claim as it is, put it, then `kblam rm B --merged-into A`, then `kblam edit A` again, add the
  rest of B's detail and put it. Deleting a finding file any other way is denied.
- `kblam rm` and `kblam renumber` refuse a finding that a review record links (records never
  follow a finding): do what the refusal says (merge the other way, removing the other finding
  before the put, or renumber the other file), and when it says to tell the user, stop and tell
  them.
- `kblam resolve R-XXXXXXXX --distinct "<reason>"` only when Jev misread the item: two findings
  that state distinct facts, or, for a `revision` item, a finding that states a fact directly
  rather than correcting an earlier claim. The reason names what differs (component, operation,
  condition, model or quantity), or why it is not a correction, for a later reader. A
  `quantity_conflict` is never resolved: rename the quantity if the two measure different things,
  otherwise fix a value.
- `kblam items --reworded` lists each rejected item whose finding later went in changed while the
  other finding stayed as it was: the correction the reject asked for, or rewording to get past it.
  Read which.

`kblam resolve` and `kblam rm` are the adjudicator's commands: when `kblam.toml` sets
`[kb] adjudicators`, they are denied to every agent type not listed there.

## Unchecked items

If Jev could not be reached, the write is accepted but unchecked, and `kblam validate` fails.
Run `kblam check --pending` once Jev is reachable.

## Checks

`check:` names a command whose re-run reproduces the claim's key number and exits 0 when it does.
`kblam recheck` runs it from the repository root without a shell: write a program and its
arguments, with `/` in paths (`\` is an escape character, as in a POSIX shell), and put a pipeline
or a redirection in a script under `evidence/`.

kblam runs a check only once a person has approved that exact command, and the files it names, on
their machine, because anyone who can push can put a command in a finding. A new or changed
command, or one whose script changed, is reported as not approved and is not run: ask the user to
run `kblam recheck F-NNNN` at a terminal, where it is shown to them first. Never run the command
yourself to get around that, and never try to approve it.

A failed check means the key number did not reproduce, or the command broke. Read its output
(`.kblam/recheck/F-NNNN.log`), then `kblam edit` the finding so it states what the evidence shows
now, or so its `check:` runs what reproduces it. A check that ran past the time limit needs either
a fix for whatever hangs or, from the user, a higher `recheck_timeout_seconds` in `kblam.toml`.

## Stop hook block

`{{kb_root}}/` or `{{review_root}}/` was changed outside kblam and fails `kblam validate`. Fix each
failure through kblam (`kblam edit`, `kblam put`, `kblam index`, `kblam ack`; a record through the
command its diagnostic names). A legitimate out-of-band change such as a `git pull` is accepted with
`kblam validate --record` once the tree is clean.

A block, or a refusal from any kblam command, that says git tracks files under `.kblam/` is not
yours to fix: `.kblam/` is each machine's own state, and a commit that holds it replaces every
clone's. Tell the user what it names; untracking the files and, when they came with a pull,
resetting `.kblam/` are their decision.

## Old formats

A refusal, or a Stop hook note, that says `.kblam/` holds state from before fingerprint v2, and a
K1 error about an old-format (8-digit) `depends_on` stamp, mean the knowledge base or this machine
predates kblam's current formats. Run `kblam upgrade`: it asks Jev nothing, and prints what it did.
It re-stamps each old stamp whose target is unchanged; one it leaves means the target changed
since: re-read the target, then `kblam ack`. Tell the user what it printed: the re-stamped findings
are committed once, each other machine runs `kblam upgrade` for its own state, and any prompt ids
it prints are theirs to record in `kblam.toml`.
