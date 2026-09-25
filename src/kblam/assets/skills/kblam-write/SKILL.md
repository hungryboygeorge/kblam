---
name: kblam-write
description: Add, change or correct a finding in {{kb_root}}/ (the kblam knowledge base), and handle any kblam refusal - a denied write under {{kb_root}}/ or .kblam/ or to kblam.toml, a kblam put rejection (exit 1, 3 or 4), a Stop hook block, a failing kblam validate or pre-commit, a review or unchecked item, or a suspect dependency.
---

# Writing findings with kblam

`{{kb_root}}/` holds only current facts, one claim per file. `kblam put` is the only way in: a
Write, Edit or shell write under `{{kb_root}}/` is denied, and one that slips through is caught when
you stop. Each refusal names the finding and the rule that fired, and what to do.

`.kblam/` holds kblam's own state (the review items, the verdict cache, the lock), and only kblam
writes it: a Write, Edit, shell write or removal there is denied. The one exception is
`.kblam/staging/`, where your staged findings are yours to edit.

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
   `{{kb_root}}/` is unchanged and the staged file stays: fix it and put it again.

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
check: "uv run python evidence/2026-09-22-sensor-type-ratio/derived/ratio.py"   # optional
verified: 2026-09-22
---

**Claim.** The first paragraph is the claim: prose, at most 250 words by default.

Supporting detail after it.

<!-- verbatim: evidence/2026-09-22-sensor-type-ratio/log.txt:12-13 -->
> text copied exactly from lines 12-13 of that file
```

- The file is at most 300 lines by default (`kblam.toml` sets both limits).
- A verbatim tag goes on the line directly before a fenced block or blockquote; `:@0x1F0` cites a
  byte offset. The excerpt must occur exactly in that range: copy it, never retype it.
- Two findings that give the same quantity name must give the same value and unit.

## Refusals

`kblam put` exits 1 for a K rule or a request kblam will not carry out, 3 when another kblam
write held `.kblam/lock` too long (retry), and 4 for a Jev or quantity reject. Exit 2 means no
usable `kblam.toml` or bad arguments.

| Rule | Trigger | Fix |
|---|---|---|
| K1 | frontmatter schema, ID, topic, label or scope | the field it names |
| K2 | evidence missing or not found; `depends_on` names no finding | cite paths that exist |
| K3 | a `depends_on` fingerprint is stale or missing | re-read the target; in a staged file set it to `null` |
| K4 | revision-history language | state the current fact directly |
| K5 | another finding's ID next to such language | `kblam edit` that finding instead |
| K6 | file or claim too long, or no prose claim first | cut detail or split independent claims |
| K7 | `INDEX.md` differs from the generated one | `kblam index` |
| K8 | a file under `{{kb_root}}/` that is not a finding | remove it |
| K9 | the claim nearly duplicates another finding's | `kblam edit` that finding instead |
| K10 | a verbatim excerpt is not in its cited range | copy the text exactly, or fix the range |
| K11 | a `reported` finding quotes no retired document; `history/` cited as evidence; a finding depends on a `reported` one | see "Reported findings" |

Jev and quantity rejects (exit 4) report every verdict that fired, review ones included:
`same_fact` (the existing finding already states this), `cannot_both_be_true` (probable conflict),
`revision` (this reads as a correction), `quantity_conflict` (same quantity, different value).

"F-NNNN changed since your edit" means another write replaced it: move your staged copy out of
`.kblam/staging/`, `kblam edit F-NNNN` again and reapply your change.

## Reported findings

Label a claim `reported` when a retired document (under `history/`) states it and nothing in the
tree reproduces it: say so in the claim ("A20 of the retired calibration document reports ...;
no script in the tree reproduces it"), and quote the source lines with a verbatim excerpt from
`history/`. List the captures it was measured on in `evidence`, with the history file. A claim a
later document or finding contradicts is not reported: it is left out. `history/` is not evidence
for any other label, and no other finding may `depends_on` a reported one. To promote one,
reproduce it in an `evidence/` package, then `kblam edit` it: new label, the package's output as
its excerpt, the history excerpt removed.

## A reject means: edit the existing finding

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
fact, so each rejecting verdict is also recorded as a rejected item (`R-…`) in
`.kblam/review.jsonl` and printed with its ID — a rejected item is not in `{{kb_root}}/`, so it
does not fail `kblam validate`: send the ID to the coordinator (or the librarian if one is
deployed) and carry on. `kblam resolve R-… --distinct "<reason>"` closes it and clears the pair
when the two findings do state distinct facts, so putting the unchanged staged file then goes
through.

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

Changing either finding's claim, scope, quantities or evidence closes the item (the put re-checks
the pair and raises a new item if a verdict still fires). The coordinator, or the librarian if one
is deployed, decides each item, never the author whose write raised it. The one exception: the
librarian may close a `low_confidence` item its own write raised, with a written reason. An author
sends the item IDs to the librarian and carries on.

- A real restatement (`same_fact`, `restates_and_extends`) is merged: `kblam edit` the existing
  finding so it carries the new detail, and the new finding stops stating that fact.
- `kblam resolve R-XXXXXXXX --distinct "<reason>"` only when Jev misread two findings that state
  distinct facts. The reason names what differs (component, operation, condition, model or
  quantity) for a later reader.

## Unchecked items

If Jev could not be reached, the write is accepted but unchecked, and `kblam validate` fails.
Run `kblam check --pending` once Jev is reachable.

## Stop hook block

`{{kb_root}}/` was changed outside `kblam put` and fails `kblam validate`. Fix each failure through
kblam (`kblam edit`, `kblam put`, `kblam index`, `kblam ack`). A legitimate out-of-band change
such as a `git pull` is accepted with `kblam validate --record` once the tree is clean.
