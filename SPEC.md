# kblam — specification (draft 1, 2026-09-23)

kblam is a small knowledge-base system for research findings maintained by several LLM agents. It
keeps the knowledge base (KB) limited to **current facts**. It enforces that mechanically, and uses
the Jev model (TypeSafe AI, called through OpenRouter) to detect a new or edited finding that
contradicts or duplicates an existing one.

Developed against a pilot project's findings corpus (a hardware reverse-engineering research
effort). The tool itself must stay project-neutral; project specifics live in
a config file in the consuming repository.

Status of this document: design settled with the user except where §13 lists an open decision.
Details of the Jev API are cited to `desk-jevdocs-answers.md` entry IDs (Q1–Q9) in this directory
and must be checked against that file, which is the working evidence for them. Citations of the
form `typesafe/<page>.md` (and the other `vendor/<page>.md` names) refer to public documentation
pages, listed with their URLs in `REFERENCES.md`.

---

## 1. The problem

Agents share findings in large markdown files (the pilot corpus: ~34k lines across ~40 root
`.md` files, 1,000–3,500 lines each). Written instructions (CLAUDE.md, a 34 KB research-desk agent
definition) have not stopped five failure modes, all observed in that corpus:

1. **A falsified claim stays where it was, and the correction is appended elsewhere.** In one
   calibration findings document: section C10 (L830) still asserts that the curve "types" are
   gain 1 / gain 2; its index row (L80) still lists that claim at "high" confidence; the handoff
   table (L1293) still offers it; the falsification is a separate section C16 at L1340. Agents
   grep, land on L80 or L830, and never see L1340.
2. **New documents or sections supersede old ones** instead of editing them. "Superseded" markers are
   missed by grep-excerpt readers and forgotten at long context lengths.
3. **Hand-maintained indexes go stale.** Observed live during this design work: two separate
   research-desk agents recomputed their own index line numbers, reported them as verified, and
   were still wrong (by 3 lines, then by 2).
4. **Failed experiments are kept whole** with a correction appended, instead of being reduced to
   the current fact they established.
5. **The same fact is copied into several files** (desk answer files → findings documents, or two
   topic documents over the same subject, one keeping an N-series and the other a C-series) and the
   copies drift.

Also observed: an agent told explicitly to rewrite in place added a "X was removed from this list"
note, then reported "no annotations or revision markers left behind". **Agent self-reports of
compliance are not evidence. Only a check on the file is.**

Research behind this design (copied into `research/`):
- `desk-kbtools-answers.md`: requirements-traceability tools, validators, agent-memory systems.
  Only Doorstop has the needed change-propagation mechanism (per-link fingerprints / suspect links,
  A3); nothing covers failure modes 1, 4 or 5.
- `desk-llmwiki-answers.md`: Karpathy's LLM-wiki pattern and its derivatives. The main
  implementations deliberately keep falsified claims and annotate them (the opposite of this
  design). No system blocks a bad KB write; every deterministic checker exits 0. "Retain but make it
  unretrievable" has no working implementation (A12).
- `desk-reddit-answers.md`: practitioner reports. The complaint is common; Anthropic states that
  there is "no … duplicate-rule detection, or cross-file conflict detection yet"
  (anthropics/claude-code#85477, verified). The convergent practitioner design is an overwritten
  current-state file plus a separate append-only history, enforced by a hook. No measured results
  exist for any intervention.

## 2. Principles (decided with the user)

P1. **The KB contains only current facts.** When a claim is found wrong, the original finding is
    **edited in place** so it states only what is true now. There is no withdrawn/superseded status,
    no `withdrawn_by` or retraction pointer, no archive section, no "this was wrong" note, and no new
    finding that corrects an old one.
P2. **A negative result is a current fact**, written positively: "X does not do Y; evidence: …".
    That is how dead-end knowledge survives without keeping the mistaken claim.
P3. **History lives outside the KB.** Earlier versions of a finding live in git history (not in
    the working tree, so no grep or glob reaches them; they're reached only deliberately via
    `git log -p`). Raw experiment records live in immutable `evidence/` manifests. Manifests record
    observations and procedure; interpretation belongs in findings, because an interpretation
    written into an immutable file can never be corrected.
P4. **Enforcement is mechanical.** Every rule that matters is checked by code on the files, at a
    point the agent cannot skip. Prose instructions describe the system; they do not enforce it.
P5. **Nothing agents read is maintained by hand.** Indexes and catalogs are generated.
P6. **One fact, one place.** A finding that restates another is rejected; the agent edits the
    existing one.

## 3. Knowledge-base layout (in the consuming repository)

```
<repo>/
├── kblam.toml                 # project config (§9)
├── findings/
│   ├── INDEX.md               # GENERATED by `kblam index`; never hand-edited
│   └── <topic>/               # topic folders, e.g. calibration/, protocol/, banding/
│       └── F-0137-pressure-curve-types.md
├── evidence/                  # existing immutable experiment folders (unchanged)
└── .kblam/                    # gitignored machine state
    ├── pairs.sqlite           # Jev verdict cache (§6.5)
    ├── embeddings.sqlite      # embedding vector cache (§6.1)
    ├── calls.jsonl            # one line per Jev request: model id, tokens, cost, latency
    ├── checks.jsonl           # one line per check: candidates with reasons, verdicts (IDs and fingerprints, no finding text)
    ├── review.jsonl           # review, unchecked and rejected items (§6.4, §6.5); an open review or unchecked item fails `validate`, a rejected one does not
    ├── staging/               # findings being written or rewritten (`new`, `edit`), awaiting `put`
    ├── stop-block             # the tree digest at the Stop hook's last block (loop guard, §8 item 3)
    └── tree.hash              # sha256 over sorted relative paths + bytes of findings/, advanced only across kblam's own writes (§8)
```

Topic folders are organised by subject, never by work package or agent (naming a document after
the work package that produced it is what creates parallel truths).

## 4. Finding format

One claim per file. Filename `F-NNNN-<slug>.md`; the ID never changes, but the slug may.

```markdown
---
id: F-0137
title: The two pressure-sensor curve types are not two analog gains
topic: calibration
label: observed            # observed | decoded | inferred | unknown | reported
scope: [MX-100]            # project-defined vocabulary (kblam.toml); code-owned, never judged by Jev
evidence:
  - evidence/2026-09-22-pressure-curve-ratio/      # must exist
  - bench-runs/run-20260922-01/pressure-trace.csv
depends_on:
  F-0102: 3fa9c1d2         # fingerprint of F-0102 when this finding was last checked against it
anchors: ["0x1A2B3C", "REG 0x2F"]   # optional, quoted strings; links findings that name the same thing (§6.1)
quantities:                # optional; values that code compares, not Jev (§6.3)
  - {name: type1/type0 curve ratio, value: 1.0017, unit: ratio}
check: "uv run python evidence/2026-09-22-pressure-curve-ratio/derived/ratio.py"  # optional, re-runnable
verified: 2026-09-22
---

**Claim.** The pressure sensor's curve table lists two types; their outputs agree to about 0.1%
(median ratio 1.0017 over the run), so they are not two analog gains; a 1.8× gain difference would
show as a ratio near 1.8.

<supporting detail, verbatim excerpts with file:line or offset attribution>
```

Rules:
- The **first paragraph is the claim** (bounded length, §5 K6). Jev comparisons use the claim
  paragraph plus the scope, not the whole body.
- `depends_on` stores fingerprints, not bare IDs (Doorstop's stamp/cleared mechanism, research A3).
- `anchors` entries must be YAML strings: an unquoted `0x1A2B3C` parses as an integer, so K1
  rejects non-string anchors and asks for quotes.
- Paths in `evidence` and in verbatim tags (K10) are relative to the repository root, which is the
  directory containing `kblam.toml` (found by walking up from the current directory, or `--root`).
- `check` is the optional "warrant" idea (desk-reddit): a command whose re-run reproduces the
  finding's key number. `kblam recheck` runs these and reports any finding whose check fails.
- `reported` (user, 2026-09-25) labels a claim that a retired source document states and that
  nothing in the tree reproduces. The current fact it records is that report: the claim says which
  document states it and that it is not reproduced, and the body quotes the document's lines from
  `history/` (§11 step 3) in a verbatim excerpt. It is not a withdrawn or superseded status (P1): a
  claim a later document or finding contradicts is not migrated at all. Retired documents are
  evidence for no other label, and no finding may depend on a reported one, so an unreproduced
  figure cannot become the support of a fact. A reported finding is promoted by reproducing it in an
  `evidence/` package and editing it: new label, the package's output as its excerpt. K11 enforces
  the mechanics; the label name and the history folders are configurable (§9).

## 5. Deterministic validator (`kblam validate`)

Runs without the network. Exit status is non-zero on any error. Rule codes:

| Code | Rule |
|---|---|
| K1 | Frontmatter parses and matches the schema; `id` is unique and matches the filename; `topic` matches the folder; `label` and `scope` are in the configured vocabularies; `depends_on` values are string fingerprints (or null before M2's `ack`); `anchors` are strings. |
| K2 | `evidence` is present with at least one entry, and every path exists inside the repository root. Every `depends_on` ID exists. |
| K3 | **Suspect dependency:** a `depends_on` fingerprint differs from the target's current fingerprint (the target was rewritten). Resolved only by re-reading the target and running `kblam ack F-x F-y` (which records the new fingerprint), or by editing the dependent. Fingerprint = first 8 hex digits of sha256 over a canonical JSON encoding of the target's id, claim paragraph (whitespace runs collapsed, so reflowing is not a change), scope, quantities and evidence list. A `null` fingerprint in `findings/` is a K3 error (unstamped). A finding may not depend on itself (K2). A cycle between two or more findings is allowed and kept when each claim states content from the other: `depends_on` is outside the fingerprint, so an `ack` on one edge never makes another edge suspect. |
| K4 | **Revision-history language** in any finding (configurable pattern list, §9: phrases a corpus analysis found used only in unwanted senses, e.g. `was wrong`, `supersed`, `withdrawn`, `refuted`, `is falsified`, `previously believed`, `no longer true`). Matching is case-insensitive and anchored at a word start (so the stems `falsif`, `supersed`, `retract` match their inflections). Verbatim excerpts that pass K10 are exempt (they quote sources, so domain terms such as "shading correction" must not trip it). Heuristic; Jev's revision question (§6.2) catches paraphrases. |
| K5 | A finding mentions another finding's ID within N words (`history_id_window`, default 10) of a K4 term (catches "F-0102 is wrong"). Same K10 excerpt exemption as K4. |
| K6 | File length ≤ configured maximum (default 300 lines); claim paragraph ≤ configured words (default 250; the user found 80 far too small, 2026-09-23). |
| K7 | `INDEX.md` is byte-identical to what `kblam index` would generate. |
| K8 | No file of any kind under `findings/` other than findings in topic folders and `INDEX.md` (stops "summary", "handoff" and notes files from appearing in the KB). |
| K9 | Exact or near-exact duplicate claim paragraphs (normalised text; token-set Jaccard similarity ≥ `duplicate_similarity`, default 0.9). A cheap first pass before Jev. |
| K10 | Every quoted excerpt marked as verbatim occurs exactly in its cited source. Syntax: a fenced block or blockquote directly preceded by `<!-- verbatim: <repo-relative path>:<line>[-<line>] -->` (or `:@0x<offset>` for a byte offset). The excerpt, with blockquote `> ` markers stripped and line endings normalised, must occur within the cited range. Catches paraphrased "quotes". Binary sources (containing a NUL byte or not valid UTF-8) are exempt; their excerpts are checked by `check:` commands. |
| K11 | **Reported claims** (§4). A finding labelled `reported_label` has at least one verbatim tag whose source is under a `history_dirs` folder (K10 checks the excerpt itself). A finding with any other label lists no path under a `history_dirs` folder in `evidence` (prose and verbatim excerpts may still cite one), and has no `depends_on` entry naming a reported finding. When `reported_label` is `""` or not in `labels`, only the evidence check applies. |

## 6. Jev contradiction and duplicate check (`kblam check`)

### 6.1 Candidate pairs (code, not Jev)
Candidate selection must not depend on any one project's writing habits (user, 2026-09-23: the
earlier design's auto-extracted hex and backtick anchors suited the pilot corpus only). For a new
or changed finding N there are two kinds of candidate:
- **Dependencies**, always included: a `depends_on` edge in either direction.
- **Scored** findings: every other finding, scored by similarity to N's title and claim
  paragraph, over the whole KB (every topic). The score comes from a local embedding model when
  one is available, otherwise from BM25 (user, 2026-09-24). Each check uses one mechanism for
  every pair.
  - **Links.** A finding is *linked* to N when they share a declared `anchors` entry (hex anchors
    compare by value, `0x001a2b3c` = `0x1A2B3C`, others case-insensitively) or an evidence path
    (equal, or one contains the other). A linked finding's score gets a bonus of `link_bonus`
    (§9, default 0.15) times N's top raw score, once however many links it has, under either
    mechanism (user, 2026-09-24). A linked finding is eligible even when its raw score is 0.
  - **Embedding** (preferred): cosine similarity between ollama embeddings of N's title and claim
    paragraph and each finding's. `embedding_model` and `ollama_url` are set in §9; the defaults are
    `embeddinggemma:300m` at `http://127.0.0.1:11434`, the best mechanism in the recall
    measurement (§13). `OLLAMA_HOST` is not read. N is embedded with the model's query prefix and
    the other findings with its document prefix (§9, `embedding_query_prefix` and
    `embedding_document_prefix`, which default to embeddinggemma's documented forms). No topic
    bonus applies, and every finding is eligible whatever its score: recall was measured that way,
    and catching paraphrases that share no token is the reason to use embeddings at all.
  - **BM25** (fallback): k1 = 1.2, b = 0.75, with N's title and claim paragraph as the query
    against each finding's title and claim paragraph. Tokens are lowercased runs of letters,
    digits, `_`, `.`, `-` and `/` with trailing punctuation stripped, so `0x1A2B3C`, `MX-100` and
    `foo_bar()` stay whole; a small fixed English stop-word list is dropped; there is no stemming.
    Pure Python, no dependency, deterministic. A finding in N's topic gets a bonus of
    `topic_bonus` (§9, default 0.2) times N's top score, so same-topic findings rank ahead of
    equally similar ones elsewhere. A finding scoring 0 (no shared token) is never a candidate.
  - **Choosing.** `embedding_model = ""` selects BM25, silently. Otherwise each command
    (`put`, `check`, `audit`, the Stop hook) first asks ollama for its model list
    (`GET /api/tags`, 2 s timeout).
    - BM25 is used for the rest of the command in three cases:
      - that request fails;
      - `embedding_model` is not in the list;
      - an embed request fails.
    - Falling back is not an error and does not make a write unchecked. The command prints one line
      to stderr naming the reason (`similarity: BM25 (ollama not reachable at <url>)`).
    - A candidate set is never ranked by a mix of the two methods. If `put`'s under-lock embed
      fails after its pre-lock pass used embeddings, the under-lock ranking is BM25 over every
      finding.
  - **Vectors are cached** in `.kblam/embeddings.sqlite`, keyed by model name, the model digest
    from `/api/tags`, role (query or document) and the sha256 of the prefixed text. Only uncached
    texts are sent, in requests of at most 64 inputs (larger ones fail inside ollama). Re-pulling a
    model changes its digest, so its old vectors are never reused. Like the Jev pairs, `put` embeds
    before taking the lock and, under the lock, embeds only findings the tree gained meanwhile. A
    re-run on the same machine and model yields the same candidates; another machine may not, and
    one without ollama gets BM25.

Budget: `max_candidates` (§9, default 30) pairs whose scopes overlap. Dependencies come first, by
score; the rest of the budget goes to the scored findings in order of score with bonuses, ties by
ID. Links are a bonus, not a priority, because a common evidence file crowds similarity out of the
budget. Across the first 174 checks of the pilot corpus, 29 had 15 or more linked candidates and 4
had 25 or more; one interface specification took 577 slots. A replay of those checks, in an
evaluation on the pilot corpus that is not published, found:
- at no bonus from 0 up does any `restates_and_extends` or `cannot_both_be_true` pair fall out of
  the budget;
- a rejected conflict that entered only through a link ranks 24th of 66 by cosine alone, and 5th at
  0.15;
- no pair that linked-first displaced draws a verdict under the current policy.

Pairs with disjoint scopes don't count against the budget. The quantity comparison (§6.3)
is code, so it covers every finding whose scope overlaps N's, candidate or not; a finding it
conflicts with is added to the check with only the quantity verdict. Each check's candidate set, with the reasons each
matched, is logged to `.kblam/checks.jsonl`.

A finding is never its own candidate (an `edit` of F-0088 is not compared with the F-0088 it
replaces).

Scope is decided in code first: if `scope` sets are disjoint (e.g. MX-100 vs MX-200), the pair is
recorded as `different_scope` and Jev is not asked. A scope value containing `/` stands for each of
its parts (`MX-100/MX-200` = {MX-100, MX-200}), and `any` overlaps every scope. Jev's documented weaknesses include literal
reading and indirection (typesafe/model-jaggedness_jev-1.13.md), so model-gating must
not be left to it.

### 6.2 Questions per pair
State = `{"existing": <claim paragraph + scope of E>, "new": <claim paragraph + scope of N>}`.
Name the parts in the instructions with backtick paths (per TypeSafe guidance; exact rules in
desk-jevdocs Q5).

**Where the wording lives.** The question text is a project's, not code's: each kblam.toml carries it
in `[jev.prompt.relation]` and `[jev.prompt.revision]` (§9), and it is sent verbatim. Code owns what
the API contract fixes — the relation option keys, the question `type`s (`choice`, `noul`), the state
shapes above and `SHAPE_VERSION` — and validates the config: the relation's criteria keys are exactly
the option keys, the revision's exactly `true`/`false`, every text field a non-empty string and every
`examples` a list of strings. Anything else is an error naming the key.

The prompt's identity is `prompt_id`, the first 12 hex digits of the sha256 of the two question dicts
as sent (with `SHAPE_VERSION`) over canonical JSON: parsed values, so reformatting the TOML does not
change it and any edit to a character of the text does. It is what `[jev.thresholds]` records (§6.4)
and what keys the pair cache (§6.5). `kblam prompt-id` prints the current one.

- **relation** (Choice): which describes `new` relative to `existing`?
  - `same_fact`: everything `new` states is already stated by `existing` (same fact, possibly in
    different words, or a subset of it)
  - `restates_and_extends`: `new` restates the fact in `existing` and adds detail to it (user,
    2026-09-22: under P6 the detail belongs in the existing finding)
  - `cannot_both_be_true`: they make claims about the same subject that cannot both hold
  - `compatible_same_subject`: same subject, both can be true
  - `unrelated`: different subjects
- **revision** (Noul, asked once per changed finding, not per pair): "Does `new` describe a
  correction or revision of an earlier claim, rather than stating a fact directly?"

Wording must avoid negation and keep criteria aligned with instructions (jaggedness items 1 and 7).
The shipped default is the wording the calibration run (§10.6) measured, with one change: its scope
example is generic (`products, versions or components`), where the measured wording named the pilot
project's own scope example.

**Option wording (desk-jevdocs Q5, Q9 §9.8, quoting jev-dsl's README):** "An option that argues for
itself steers the answer; an option that merely describes its condition does not." Write each
option as a condition the state could satisfy, never as a case for picking it. "A choice's
distribution is uncertainty about which single alternative fits, not evidence that several apply":
`probabilities["cannot_both_be_true"] = 0.4` never means "40% conflicting".

**Request shape (desk-jevdocs Q6):** one request per pair. TypeSafe's fan-out advice multiplies
*questions* against one shared state, not states; both published pairwise precedents send one
request per pair (entity alignment: "One request goes out per pair", 450 pairs, 6-worker pool); and
putting ~30 unrelated candidates in one state is the documented "large state full of irrelevant
detail" failure mode. The `revision` Noul concerns `new` alone, so it is its own request with state
= `new`. Budgets: 64k tokens per request, 32k for state plus the longest question (models.md).
Run pairs in parallel with a small worker pool, within the 1,200 requests/minute limit.

**Reading the answers (desk-jevdocs Q3; jaggedness page):**
- A Noul returns only `noul` (0–1) and no `confidence`. `revision` gets its own threshold on
  `noul`. Never reuse a Noul threshold for a Choice, or the reverse.
- A Choice always names a winner, even when no option fits; low `confidence` is the only sign of
  that. Gate every Choice verdict on confidence as well as on the winning probability, or
  `unrelated` absorbs ambiguous pairs. The citation-check cookbook's "neither way" verdict came back
  at confidence 0.27 and 0.56 while its clear verdicts were ≥ 0.93, so expect a real share of pairs to
  land in review.
- Answers are not perfectly repeatable: TypeSafe's consistency cookbook reports about 90.8%
  plurality-label agreement over 15 runs, and a Noul near 0.5 spread 0.43–0.53. Calibration (§10)
  measured a relation-winner flip rate of 3/95 and 2/84 and no Noul change above 0.1, so pairs are
  asked once and the cached answer stands.

### 6.3 Numbers stay in code
Jev "struggles with tasks that require numeric precision" and cannot compare hex values reliably
(jaggedness). For every candidate pair whose scopes overlap, when N and E both carry a quantity
with the same name (compared case-insensitively with whitespace runs collapsed), code compares them
and reports `quantity_conflict` itself if the units differ or the values differ by more than
`quantity_rel_tolerance` (§9, default 0: one named quantity has one current value, P6). A
`quantity_conflict` rejects the write: "F-0102 gives <name> = <value>; rewrite F-0102 in place,
correct this finding, or rename the quantity if they measure different things". Jev is never asked
whether two numbers agree; Jev is still asked about the pair's claims.

### 6.4 Decision policy
Thresholds and modes come from the calibration run (§10.6) and live in `[jev.thresholds]` in
`kblam.toml` (§9), together with the served model ID and the prompt id (`prompt_id`, §6.2) they were
measured on.

A relation verdict v **fires** when Jev's winner is v, p(v) ≥ T_v and confidence ≥ C_v (the rule
the calibration measured). The revision verdict fires when noul ≥ T_rev.

| Verdict | Fires at (jev-1.13-20260917, the default wording) | Mode | Message |
|---|---|---|---|
| `same_fact` | p ≥ 0.69, confidence ≥ 0.61 | **reject** | "F-0088 already states this; edit F-0088 instead" |
| `cannot_both_be_true` | p ≥ 0.58, confidence ≥ 0.46 | **reject** | "probable conflict with F-0102 (p=…): rewrite F-0102 in place so it states the current fact, or correct this finding" |
| `revision` | noul ≥ 0.75 | **reject** | "this reads as a correction; rewrite the original finding instead" |
| `quantity_conflict` (§6.3) | code | **reject** | §6.3 |
| `restates_and_extends` | p ≥ 0.48, confidence ≥ 0.34 | **review** | "this restates F-0088 and adds detail; move the detail into F-0088 (kblam edit F-0088)" |
| `low_confidence` | no verdict fires on the pair and confidence < 0.3 | **review** | "Jev could not place this against F-0102 (winner …, confidence …); read both" |
| otherwise | | accept | |

- **Reject:** `put` refuses and `findings/` is unchanged. It reports every verdict that fired,
  review ones included, so the author fixes everything in one pass.
- **Rejected pairs** (user decision delegated to the coordinator, 2026-09-24). Jev can reject a
  pair of distinct facts, most often "the interface supports X" against "this program does not
  use X". Rewording to get past it is forbidden (the skill), so a rejected put records each
  `same_fact`, `cannot_both_be_true` or `revision` verdict that rejected it as a **rejected item**
  in `.kblam/review.jsonl`. Its ID is `R-` + the same hash as a review item's, over the staged
  finding's ID and fingerprint and the other side's. `put` prints each item's ID with the reject
  message. `quantity_conflict` is code, not Jev, and records nothing.
  - A rejected item does not fail `validate`: its finding is not in the tree. It closes when a put
    of that finding ID succeeds, or when the staged finding's fingerprint no longer matches (the
    next put of a changed staged file re-checks the pair).
  - `kblam resolve R-x --distinct "<reason>"` accepts it like a review item. It stores the reason
    in the pair cache at those fingerprints and closes the item. A put of the unchanged staged
    finding then reports that pair as "resolved as distinct, not raised" and goes through.
  - The author never resolves its own rejected item: it sends the ID to the adjudicator (§8.1),
    which resolves it only when the two findings state distinct facts.
- **Review:** the write is accepted, and a review item (both IDs and fingerprints, the verdict, p,
  confidence) is appended to `.kblam/review.jsonl` and printed. `kblam validate` fails while any
  item is open. An item closes automatically when either finding's fingerprint changes: the `put`
  of the edited finding re-checks the pair and raises a new item if a verdict still fires.
  `kblam resolve <review-id> --distinct "<reason>"` closes it, and every other open review item on
  the same pair at the same fingerprints, and stores the reason in the pair cache, so that pair at
  those fingerprints is not raised again. A review item's ID (`R-` + 8 hex digits) is a hash of the
  verdict and both (ID, fingerprint) sides, so raising the same item again doesn't duplicate it.
- **Unchecked** items (`U-` + a hash of ID and fingerprint) are per finding: some Jev question for
  it got no answer (§6.5). `resolve` refuses them. They close when `kblam check --pending` gets an
  answer to every question for that finding, or when its fingerprint changes.
- `kblam check` and `kblam audit` run on findings already in the tree, so a reject verdict they
  find is recorded as a review item.
- There is no separate review band below the reject verdicts: at each, the best calibration
  point with precision ≥ 0.5 was the reject point itself (§10.6).
- The `low_confidence` row comes from a post-hoc look at the calibration pairs (evidence
  `derived/low-confidence.md`), not a pre-registered held-out result. Below confidence 0.49,
  12 of 179 pairs fell in the band and 7 were contradictions, duplicates or extensions that the
  verdicts missed. Below 0.3, 2 pairs fell in it, and both were such positives.
  - The user set the cut at 0.49 on 2026-09-22.
  - The calibration-topic migration measured it on real claims of up to 250 words: 517 of 4077
    asked pairs (12.7%) fell below 0.49. Of the 425 review items raised, 359 were closed as
    distinct and none was closed by a merge; the rest closed as a side effect of edits.
  - The cut is 0.3 since 2026-09-24 (coordinator, on the user's delegation of this question). On
    the migration's pairs, 15 of the 517 verdicts fall below 0.3.
  - The cut is set by `low_confidence_review` in `[jev.thresholds]`; absent means off.
- **Numeric paraphrase false reject (known):** "removes about a quarter of the difference" vs
  "leaves about three quarters of it" fired `cannot_both_be_true` at p 0.94 in both runs (TOP-008).
  The user decided (2026-09-22) that contradictions block the write anyway; the author rewrites
  one finding's wording (or merges the two, since they state one fact).
- **Model or prompt mismatch:** if the served model ID of any answer used (fresh or cached) or the
  prompt id of the wording in `[jev.prompt]` (§6.2) differs from the one recorded in
  `[jev.thresholds]`, no Jev verdict rejects: every one that fires becomes a review item, and kblam
  warns, naming both ids, that re-calibration (§10) is needed. `quantity_conflict` still rejects,
  because it doesn't involve Jev.
- **Only enabled verdicts are asked.** A verdict with no entry in `[jev.thresholds]` is disabled.
  The relation question is asked only if a relation verdict or `low_confidence_review` is set;
  the revision question only if `revision` is. A KB with no `[jev.thresholds]` sends nothing to
  Jev and runs only §6.3 (the CLI says so on stderr).
- **Direction.** The finding being written or checked is always `new`, so editing an older finding
  asks the reverse of the direction calibration measured (calibration's extending claim was always
  `new`). In the M5 smoke, editing F-0001 raised "F-0001 restates F-0004". This can only raise a
  review item, whose message may then point the wrong way.

### 6.5 Cache, cost and failure handling
- Cache key = (model ID, prompt id, fingerprint(E), fingerprint(N)). A pair is re-asked only when
  one side changes, the prompt changes (any edit to `[jev.prompt]` changes its id), or the model
  changes. A cache written by an older kblam, whose rows were keyed by an integer prompt version, is
  reported rather than dropped: the message names the file and leaves deleting or migrating it to the
  user. `kblam audit` asks each candidate pair that has no cached answer in either direction (lower
  ID as `existing`) and the revision question for each finding without one, so after a baseline it
  only asks what changed. **Cost** is $0.042 per million input tokens; output
  tokens are reported but not charged (both observed in the M3 smoke, 2026-09-22). Measured with
  the draft wording on one-sentence claims: a relation pair is 771 input tokens ($0.000032), a
  revision question 390 ($0.000016); most of the relation count is the fixed question text. So one
  write with 30 candidates ≈ 24k tokens ≈ $0.001, and a one-time baseline over ~4,500 candidate
  pairs ≈ 3.5M tokens ≈ $0.15 (longer claims raise both); after that, about $1 a month. Log actual `usage.cost` from each
  response to `calls.jsonl`; `kblam cost` summarises it.
- Pin the model to the dated snapshot the thresholds were measured on (e.g. `typesafe/jev-1.13` →
  response `model: typesafe/jev-1.13-20260917`); record the served model ID in every cache row.
  A different served ID invalidates the cache and needs re-calibration (desk-jevdocs Q7).
- **Network or API failure is never a silent pass.** The write is accepted but marked unchecked in
  `.kblam/review.jsonl`, and the next `kblam validate` fails until `kblam check --pending` succeeds.
  A question that failed before `put` took the lock is not retried under it, so an outage never
  holds the lock through retries.
- API key: read from the `OPENROUTER_API_KEY` environment variable, falling back to a key file
  (the default is `~/kblam/jev!.txt`; another variable or file is set per machine, §9). Never log,
  print or commit it. (Note: `!` in the filename triggers history expansion in interactive bash;
  quote the path or rename the file.)

## 7. Commands

| Command | Purpose |
|---|---|
| `kblam new <topic> "<title>"` | allocate the next ID (across `findings/` and staging), write a skeleton to `.kblam/staging/F-NNNN-<slug>.md`, print the path |
| `kblam edit <id>` | copy an existing finding to staging for rewriting in place (P1) and print the path |
| `kblam put <file>` | **the only way into `findings/`**: validate the KB as it would be after the move + Jev check + move into place + regenerate the index and `tree.hash`. A staged file with an existing ID replaces that finding in place (the old file is removed if the slug or topic changed). Any error leaves `findings/` unchanged. |
| `kblam validate` | all deterministic rules (§5) plus open review and unchecked items (open rejected items do not count); no network; non-zero exit on failure |
| `kblam check [F-…]` | §6 check of the named findings, or of every finding with no complete check at its current fingerprint; writes nothing under `findings/`; what fires becomes review items |
| `kblam check --pending` | re-run the check of each finding with an open unchecked item |
| `kblam audit` | ask every candidate pair and revision question with no cached answer (§6.5); what fires becomes review items |
| `kblam validate --record` | check (as `kblam check` with no IDs) the findings not yet checked at their current fingerprint, then, on a clean result, write `tree.hash` for the current tree: the explicit way to accept a legitimate out-of-band change such as `git pull` or `git checkout` |
| `kblam index` | regenerate `findings/INDEX.md` from frontmatter and write `tree.hash`. Deterministic: a fixed header line, topics in sorted order, one table row per finding (ID link, title, label, scope) in ID order, no timestamps. |
| `kblam ack <dependent> <target>` | record the target's current fingerprint in the dependent's `depends_on` after re-reading the target (K3). Edits only that value (round-trip YAML), then rewrites `tree.hash`. `--all <target>` is deliberately absent: each dependent is re-read and acked on its own. |
| `kblam deps <id>` | list the finding's dependents and dependencies, with suspect ones marked |
| `kblam resolve <review-id> --distinct "<reason>"` | close a review or rejected item whose two findings state distinct facts (§6.4) |
| `kblam recheck` | run the `check:` commands |
| `kblam cost` | spend summary from `calls.jsonl` |
| `kblam prompt-id` | print the id of this project's Jev prompt (its `[jev.prompt]` tables): what `[jev.thresholds]` records and calibration is tied to (§6.2, §9) |
| `kblam hook <event>` | entry point for Claude Code hooks (§8) |
| `kblam init [--update]` | set up the current git repository for kblam (§12 M6.5); `--update` rewrites the files kblam owns to the installed version |
| `kblam migrate …` | helpers for splitting an existing document into findings (§11) |

Exit status: 0 success; 1 refused (validation errors, open review or unchecked items, a request
kblam will not carry out, Jev unavailable; `check` and `audit` also exit 1 when the run left an item
open); 2 no usable `kblam.toml` or bad arguments; 3 timed out waiting for `.kblam/lock`; 4 `put`
rejected by the Jev check or a quantity conflict (`findings/` unchanged).

## 8. Enforcement points (Claude Code and git)

Research agents write code and scratch files freely; nothing below touches paths outside `findings/`
and `.kblam/`.

**`.kblam/` is kblam's state** (tree.hash, review.jsonl, the verdict cache, the lock). A hand write
there could silence the Stop hook or close a review item, so items 1 and 2 treat a path under
`.kblam/` like one under `findings/`, with two differences: `.kblam/staging/` is exempt (staged
findings are the author's to edit), and removal is denied too (`rm`, `rmdir`, `Remove-Item`/`rm`/
`del`/`ri` targets, and the *source* of `mv`/`Move-Item`), since deleting `review.jsonl` would close
every item. The deny reason reads "kblam: <what> under .kblam/ denied. .kblam/ holds kblam's own
state and only kblam writes it; stage findings under .kblam/staging/ (kblam new, kblam edit)." plus
the skill pointer. A stale lock is broken by kblam itself (§12 M2).

**`kblam.toml` is the project's.** It sets the rules kblam enforces and, within the limits of §9,
where requests go, so an agent that could change it could weaken every check or redirect the key.
Items 1 and 2 deny writing it and removing it (the repository's own `kblam.toml`, not a file of that
name elsewhere), in the same way as `.kblam/`, with the reason "kblam: <what> denied. kblam.toml sets
the rules kblam enforces and where kblam sends the Jev API key, so only a person changes it; ask the
user to make the change you need." plus the skill pointer. A person edits it by hand (user,
2026-09-25).

**Hook input and output.** `kblam hook <event>` reads Claude Code's hook JSON on stdin and always
exits 0; the decision travels only in the JSON on stdout (research/desk-hooks-answers.md H4–H6).
- Allow is silence: no output, so Claude Code's normal permission flow still applies. The hook
  never answers "allow".
- Deny (PreToolUse): `{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "deny", "permissionDecisionReason": "<text>"}}`.
- Block (Stop, SubagentStop): `{"decision": "block", "reason": "<text>"}`.
- **No `kblam.toml`: silent.** The hooks may be configured where no knowledge base exists (a
  parent directory, a copied settings file); there they print nothing and exit 0.
- **Fail open.** A hook never stops an agent's unrelated work. It allows the action and prints
  `{"systemMessage": "kblam hook <event>: <why>; allowed"}`, which the user sees and the model
  does not, when `kblam.toml` is unreadable or invalid, the input is not a JSON object, `tool_input` is missing
  or not an object, the path or command is not a string, the event is unknown, or anything raises
  (`unexpected <Type>: <message>`).
- **Root.** `--root` if given, else `$CLAUDE_PROJECT_DIR`, else the input's `cwd`, else the
  process's working directory; from there kblam walks up to `kblam.toml` as usual (§9).
- **Paths.** A target path is resolved against the input's `cwd`, after expanding `~` and `$VAR`
  and, on Windows, turning Git Bash's `/c/...` into `c:/...`; in a PowerShell command `\` is a
  separator on every platform, as it is in PowerShell. It is normalised lexically, and a second
  form has its symlinks resolved (`realpath`, which accepts paths that do not exist), since the
  root kblam finds is a resolved path and an agent may name the repository through a symlinked
  directory. Windows paths compare case-insensitively. It is under `findings/` if either form is
  the findings directory or inside it (either form of that directory), and under `.kblam/` (§8)
  if a form is inside `.kblam/` and outside `.kblam/staging/`.
- A PreToolUse call imports only the hook code (the console script dispatches `kblam hook` before
  loading the rest of the CLI), so it takes about 0.1 s.

1. **PreToolUse on Write|Edit|NotebookEdit** (the file-writing tools Claude Code documents;
   the path is `tool_input.file_path`, or `notebook_path` for NotebookEdit; there is no MultiEdit
   tool, research/desk-hooks-answers.md H12): if the target path is under `findings/`, deny it.
   Any other path passes instantly. The reason reads: "kblam: <Tool> of <path> denied. findings/ is
   written only by kblam put: stage the finding with kblam new <topic> "<title>" or kblam edit
   <id>, edit the staged copy under .kblam/staging/, then kblam put it." plus the skill pointer
   (item 6).
2. **PreToolUse on Bash and PowerShell:** best-effort deny for commands that visibly write under
   `findings/`. The command is split into words and operators (Python `shlex`); a command it cannot
   split (unbalanced quotes) is allowed silently. The targets it finds:
   - **Bash:** redirection targets (`>`, `>>`, `&>`, `>|`, `N>`; `>&N` and `>&-` are not files);
     the destination of `cp`/`mv` (the last operand, or `-t DIR`/`--target-directory=`); `tee`
     operands; the files of `sed -i`/`--in-place` (the first operand is the script unless `-e` or
     `-f` is given). Leading `VAR=value` words and the wrappers `sudo`, `command`, `builtin`,
     `env`, `nohup`, `time` and `exec` are skipped. Here-document bodies are removed before parsing.
   - **PowerShell** (backslash is a path separator, not an escape): redirection as in Bash, plus
     `*>`; the path of `Set-Content`, `Add-Content`/`ac` and `Out-File` (`-Path`, `-LiteralPath`,
     Out-File's `-FilePath`, or the first positional argument); `New-Item`/`ni` (`-Path`, joined
     with `-Name`, or the first positional); the destination of `Copy-Item`/`copy`/`cp`/`cpi` and
     `Move-Item`/`move`/`mv`/`mi` (`-Destination` or the second positional). Parameter names match
     case-insensitively by unique prefix, and `-Param:value` is accepted.
   - A command that only reads `findings/` (`grep`, `cat`, `Get-Content`) is never denied. Neither
     is `rm`, nor the *source* of an `mv`: removing a stray file is how a K8 failure is fixed, and
     kblam has no delete command. (Under `.kblam/`, removal is denied; see the paragraph above.)
   The reason reads "kblam: this command writes under findings/ (<targets>), so it is denied." with
   item 1's tail. Both shell tools are matched, because with `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` (set
   on the pilot project's machines) agents may write through either (H9). This is only a first
   line; it can't parse everything (a `cd findings` before a relative write, a script that writes
   files, a backslash path in Bash), and the Stop hook (item 3) catches what it misses.
**tree.hash rule.** `tree.hash` means "findings/ as kblam last wrote it". `put`, `ack` and `index`
compare the tree's digest with `tree.hash` *before* their write (under the lock). If they match,
or no `tree.hash` exists yet (bootstrap), they record the new digest after the write. If they
differ, something changed `findings/` outside kblam: they still do their own write, but leave
`tree.hash` stale and warn, so the Stop hook still validates the out-of-band change. Otherwise a
shell write followed by `kblam index` or `kblam ack` would silence the Stop hook without anything
being validated. Only `kblam validate --record` accepts an out-of-band change.

3. **Stop and SubagentStop:** hash `findings/`; if the hash equals `.kblam/tree.hash`, exit 0
   silently (the gist-thread practitioner removed a Stop hook because it fired on every response;
   the hash check makes it cost nothing). With no `tree.hash` and no `findings/` there is no
   knowledge base yet, and the hook is silent. If the hash differs, the tree changed outside
   `kblam put`:
   - It checks every finding not yet checked at its current fingerprint (as `validate --record`
     does, so what fires becomes review items), then runs `validate` and lists the open items.
   - A clean result is silent and leaves `tree.hash` stale: only `kblam validate --record` accepts
     an out-of-band change, so later stops validate again.
   - Otherwise it blocks, quoting at most 30 failure lines plus "... and N more; run kblam validate
     for all of them". The reason says `findings/` was changed outside `kblam put` and fails
     `kblam validate`, lists the failures, says to fix each through kblam (`kblam edit`, change the
     staged copy, `kblam put`) and never to write under `findings/` directly, that `kblam validate
     --record` accepts the change once the tree is clean, and ends with the skill pointer.
   - **Loop guard.** Each block writes the tree digest to `.kblam/stop-block`. A stop is let
     through, with a systemMessage note, only when the input's `stop_hook_active` is true (the
     agent is continuing because of a block) *and* the tree is unchanged since the last block. An
     agent that cannot fix the tree is released instead of looping; one that changed `findings/`
     and still fails is blocked again; a later ordinary stop is blocked again.
   SubagentStop also fires for Claude Code's internal agents (prompt suggestions, `/btw`), which
   have an empty `agent_type` and can't fix `findings/`. The hook exits 0 silently for them (H15).
   An input with no `agent_type` key at all is treated as a real subagent. So is a subagent whose
   `agent_type` is the session's own agent name (H3); the hook cannot tell it apart.
4. **git pre-commit** (a POSIX `sh` script that `kblam init` installs): `kblam --root <repo>
   validate`, with `kblam` from `PATH`. Catches anything written outside Claude Code. If `kblam`
   is not installed the command fails with 127 and the commit is refused. Any non-zero exit prints
   "kblam pre-commit: commit refused (kblam validate exit N)." and the skill pointer.
5. **Librarian agent** (optional; proposed by the user 2026-09-23; see §8.1).
6. **Agent guidance** (§8.2): a path-scoped rule for reading findings and a skill for writing them.
   Every message that stops a write names the skill with the line "Load the kblam-write skill for
   how to fix this.": items 1–4, and every `put` refusal. For `put` that means the final line of a
   K-rule reject (exit 1) and of a Jev or quantity reject (exit 4), and every error `put` stops on
   except a config error (exit 2): the edit-base guard and bad-file refusals (exit 1) and a lock
   timeout (exit 3).

Items 1–4 and every rule in §5–§6 hold with or without a librarian: `kblam put` is the only way
into `findings/` whoever calls it. Several writers are the normal case, so `put` is safe under
concurrency by itself (§12 M2: edit-base guard and lock).

### 8.1 Librarian agent

**Optional.** A long-lived agent that, when deployed, is the only writer of `findings/`. Other
agents read `findings/` directly; they send the librarian observations to record, and it turns them
into findings through `kblam`. Without it, research agents run `kblam new`/`edit`/`put` themselves
via Bash, and the coordinator owns what the librarian would otherwise own: running
`kblam validate` at the end of each work package and clearing suspect dependencies (K3) and Jev
review items.

**Adjudicating librarian (deployed before M7; user, 2026-09-23).** Until the MCP server exists,
a deployment can run a librarian whose role is adjudication. Research agents still write through
`kblam put` themselves. The agent definition belongs to the deployment and is not shipped by kblam.
The deployment also picks the model.
- **It owns review items, rejected items and suspect dependencies.** An author whose put raised a
  review item, was refused with a rejected item (§6.4), or made dependents suspect sends the
  librarian the item or finding IDs and carries on working. A rejected item is closed only when
  Jev misread a pair of distinct facts; otherwise the author edits as the reject says.
- **For each review item** it reads both findings and their cited evidence, then takes one of
  three actions:
  - **Merge.** A real `same_fact` or `restates_and_extends` restatement is merged: it
    `kblam edit`s the existing finding to carry the new detail and drops the new finding's copy
    of the fact.
  - **Close.** It runs `kblam resolve --distinct` only when Jev misread a pair of distinct facts,
    and writes the reason for a later reader.
  - **Correct.** A finding that its own cited evidence contradicts is corrected with
    `kblam edit`.
- **For each suspect dependency** it re-reads the target, then either `kblam ack`s the dependent or
  edits it.
- **It judges form and evidence, not physical truth.** When two findings conflict and the cited
  evidence does not settle which is right, it leaves the item open and escalates to the
  coordinator.
- **Items its own writes raise go to the coordinator, with one exception** (user, 2026-09-24). It
  may close a `low_confidence` item that its own merge or correction raised, with
  `kblam resolve --distinct` and a written reason, when the pair states distinct facts. Every other
  verdict raised by its own write (`same_fact`, `restates_and_extends`, `cannot_both_be_true`)
  goes to the coordinator.
- **It may spawn the deployment's read-only research workers** to check cited evidence and look
  up sources (the deployment defines this worker type and where it looks sources up). A worker
  writes its report to a file whose path the librarian gives it, because an in-process teammate
  receives no subagent report.
- **Tools:**
  - Bash, used only for `kblam`;
  - Read, Grep and Glob;
  - Edit, for its staged files only;
  - the Skill tool, SendMessage, and Agent for the research workers.
  
  This boundary is kept by instruction and by the §8 hooks, not by construction. K1–K11 and Jev
  gate its writes as they gate anyone's.

What no gate can supply without a librarian is an owner for the old finding: an agent
mid-experiment whose put is rejected as a duplicate must rewrite the existing finding, and may
instead reword to slip past K9; the Jev check (§6) is the defence there.

What it adds over research agents writing findings themselves:
- **Someone owns the old finding.** When kblam rejects a write as a duplicate or a conflict, the
  right move is to rewrite the *existing* finding. A research agent in the middle of an experiment
  has every incentive to append instead, and no context on the old finding. The librarian's whole job
  is that edit.
- **Research agents stay lean.** They don't need the KB rules, the finding format, or the review
  workflow in their prompts (the current 34 KB desk definition is part of the failure). Their
  contract is "send the librarian: the claim, scope, verbatim excerpt(s) with file:line, evidence paths".
- **Removes the second copy.** Desk answers files and the "graduation" step (desk answers becoming
  findings — a source of failure mode 5) are replaced by messages to the librarian, which writes
  the finding once.
- **A hard tool boundary.** The librarian is given kblam as an MCP server (`put`, `check`, `validate`,
  `resolve`, `ack`, `search`) and **no Bash, Write or Edit**. It cannot write `findings/` except
  through kblam, so the validator and Jev gate every librarian write by construction. Other agents'
  direct writes to `findings/` are denied by the §8 hooks.
- **Cheap replacement.** Its knowledge is the KB itself, so a compacted or replaced librarian loses
  nothing that matters.

Risks and their mitigations:
- **Paraphrase drift** (the librarian rewords evidence). Mitigation: validator rule K10, where every
  quoted excerpt in a finding must occur verbatim in the cited evidence file (Astro-Han
  `check_evidence.py` idea, desk-llmwiki A2). The librarian writes the claim; quotes are copied, not
  retyped.
- **It judges form, not truth.** It cannot decide whether a physical claim is right. When a new
  observation conflicts with a finding and the evidence doesn't settle it, it escalates to the
  coordinator; it does not pick a winner.
- **Bottleneck.** Senders don't wait: they send and continue working. Volume is low (findings, not
  code).
- **Model choice** is the user's call. Nothing observed in this design work says one model tier
  follows the rules better: every agent involved ran on the same model, so the two "reported
  compliance, file shows otherwise" cases say nothing about models, only that self-reports are
  not evidence.
- **It is still an LLM.** Enforcement never relies on its discipline; K1–K11 and Jev check its
  writes like anyone else's.

A hook can tell a subagent's call from the main thread's. Hook input carries `agent_id` (an opaque
per-subagent ID, present only inside a subagent) and `agent_type` (the agent *type*, e.g. a custom
agent name; not a teammate's name). No field carries the name a coordinator addresses a teammate
by (research/desk-hooks-answers.md H15). A per-agent write rule (for example, only the librarian
writes) can therefore key on `agent_type`.

### 8.2 Agent guidance: read rule and write skill

Split by when an agent needs it (user, 2026-09-23). Agents read findings constantly and write them
rarely. A skill loads only when the task seems to match its description, so reading guidance as
a skill would miss the moments it is for; writing guidance loaded into every reader would undo the
"research agents stay lean" aim (§8.1). The source files ship inside the kblam package
(`src/kblam/assets/`), and `kblam init` writes them into the consuming repo's `.claude/` (§12 M6.5).

- **Reading: `.claude/rules/kblam-findings.md`**, a Claude Code project rule with
  `paths: ["findings/**"]` in its frontmatter, so it loads when an agent works with a finding and
  costs nothing otherwise. Content, kept short:
  - `findings/` holds only current facts; history is in git and `evidence/`, never in a finding.
  - Start from `findings/INDEX.md`.
  - The first paragraph is the claim. What `label` means for how far to trust it: `observed`
    (measured or seen directly), `decoded` (read out of code, firmware or a data format),
    `inferred` (concluded from other facts, not seen directly; trust it less), `unknown` (records
    what is not known), `reported` (a retired document under `history/` states it and nothing in
    the tree reproduces it: do not build on it; reproduce it first). And that `scope` limits where it applies (an MX-100 finding says nothing
    about the MX-200).
  - A suspect dependency (`kblam deps F-x`) means the finding it depends on was rewritten since
    this one was checked against it: re-read the target before relying on the dependent.
  - Cite findings by ID; don't cite `.kblam/staging/` files or desk answer files.
  - To add or change a finding, load the `kblam-write` skill.
  
  The consuming repo's CLAUDE.md also carries one always-loaded line, because a path-scoped rule
  loads when a matching file is **Read**, not on a Grep hit and not on Edit (documented,
  research/desk-hooks-answers.md H10), so an agent that only greps `findings/` never gets it: "Findings live in `findings/`, hold current facts only, and are
  written only via `kblam put` (load the `kblam-write` skill); reading guidance loads when you open
  one."
- **Writing: `.claude/skills/kblam-write/SKILL.md`**, a skill whose description names adding,
  changing or correcting a finding and handling any kblam refusal. Content:
  - The finding format (§4): frontmatter fields, the claim paragraph and its length limit, verbatim
    excerpt tags (K10), `quantities` and `anchors` (quoted strings).
  - The flow: `kblam new` or `kblam edit`, edit the staged file, `kblam put`. Never write under
    `findings/` directly (it is denied).
  - Each refusal in author terms: what triggered it and what to do. K rules by code (exit 1); Jev
    and quantity rejects (exit 4); lock timeout (exit 3, retry).
  - **Rejects mean edit the existing finding.** `same_fact`, `restates_and_extends` and
    `cannot_both_be_true` name an existing finding: `kblam edit` that one so it states the current
    fact, rather than rewording the new one until it passes. Rewording to get past K9 or Jev is the
    failure this tool exists to stop (§8.1), and the skill says so and why. A `revision` reject
    means the same: rewrite the original finding instead of writing a correction.
  - After a rewrite: which dependents became suspect, and `kblam ack` only after re-reading the
    target.
  - **Review items:**
    - what each verdict means;
    - who decides: the coordinator, or the librarian if one is deployed (§8.1), never the author
      whose write raised the item. The one exception is the librarian closing a `low_confidence`
      item its own write raised (§8.1). An author sends the IDs to the librarian and carries on;
    - the merge rule: a real `same_fact` or `restates_and_extends` restatement is merged into the
      existing finding;
    - `resolve --distinct` is only for a pair Jev misread as related. The reason names what differs,
      for a later reader.
  - Unchecked items: `kblam check --pending` once Jev is reachable.
  - That writes and removals under `.kblam/` are denied, except in `.kblam/staging/` (§8).
- **Pointers from the tool.** The §8 deny hooks, the Stop hook's block message and every `put`
  refusal end with "Load the kblam-write skill for how to fix this." so an agent reaches the skill at
  the moment it needs it, whatever its description matching does.

## 9. Configuration (`kblam.toml`)

```toml
[kb]
root = "findings"
evidence_roots = ["evidence", "bench-runs", "device-dumps"]
labels = ["observed", "decoded", "inferred", "unknown", "reported"]
scopes = ["MX-100", "MX-200", "MX-100/MX-200", "host-software", "any"]
reported_label = "reported"   # K11; "" turns off the reported-label checks
history_dirs = ["history"]    # K11: retired documents (§11 step 3); evidence for no other label
max_lines = 300
max_claim_words = 250
history_terms = ["was wrong", "were wrong", "wrong about", "supersed", "withdrawn", "retract",
                 "refuted", "is falsified", "was falsified", "falsified by", "now known",
                 "previous revision", "previously believed", "previously reported",
                 "previously said", "i previously", "not previously", "no longer true",
                 "no longer holds", "no longer valid", "no longer safe", "used to argue",
                 "turned out", "once quoted", "do not quote", "must not be quoted",
                 "do not revive", "removed from this list"]
# From an evaluation on the pilot corpus, not published (37,483 finding lines, every
# occurrence classified): each term is used only in an unwanted sense there. Bare "previously",
# "no longer", "used to", "falsif", "revised" and "correction:" have legitimate uses ("the
# previously read buffers", "data not used to fit it") and are narrowed or dropped. Project
# conventions ("[rewrite fix]", "HISTORICAL —") belong in the project's own list.
duplicate_similarity = 0.9     # K9
history_id_window = 10         # K5, words
lock_wait_seconds = 30         # how long new/edit/put/ack/index wait for .kblam/lock
lock_stale_seconds = 300       # a lock older than this (or whose holder is dead) is broken

[jev]
endpoint = "https://openrouter.ai/api/v1/systemone"
model = "typesafe/jev-1.13"
expected_served_model = "typesafe/jev-1.13-20260917"
max_candidates = 30
topic_bonus = 0.2              # §6.1: same-topic similarity bonus, as a fraction of the top score (BM25 only)
link_bonus = 0.15              # §6.1: shared anchor or evidence bonus, as a fraction of the top score
embedding_model = "embeddinggemma:300m"   # §6.1; "" means BM25 always
ollama_url = "http://127.0.0.1:11434"
embedding_query_prefix = "task: search result | query: {text}"
embedding_document_prefix = "title: none | text: {text}"
quantity_rel_tolerance = 0.0   # §6.3
workers = 6                    # parallel pair requests

[jev.thresholds]      # calibrated on the default prompt (§10.6); no entry = verdict disabled
served_model = "typesafe/jev-1.13-20260917"
prompt_id = "4dda2f781f12"     # §6.2: the shipped default's id (the criteria below are abbreviated)
same_fact            = { p = 0.69, confidence = 0.61, mode = "reject" }
cannot_both_be_true  = { p = 0.58, confidence = 0.46, mode = "reject" }
restates_and_extends = { p = 0.48, confidence = 0.34, mode = "review" }
revision             = { noul = 0.75, mode = "reject" }
low_confidence_review = 0.3    # §6.4; absent = off

# The Jev questions, sent as written (§6.2). The option keys, the question types and the state shapes
# are code's, and the criteria keys must be exactly theirs; everything else here is text.
[jev.prompt.relation]
instructions = "`existing` and `new` each hold one finding: a `claim` and the `scope` (products, versions or components) the claim applies to. Which option describes the claim in `new` relative to the claim in `existing`?"
  [jev.prompt.relation.criteria.same_fact]
  what = "Everything the claim in `new` states is already stated by the claim in `existing`: ..."
  not_for = "A claim in `new` that adds, changes or conflicts with a detail ..."
  examples = ["'The document feeder holds up to 50 sheets' and 'Up to 50 sheets fit in the document feeder'"]
  # ... one such subtable per relation option, in this order: same_fact, restates_and_extends,
  # cannot_both_be_true, compatible_same_subject, unrelated ...
[jev.prompt.revision]
instructions = "Does the `claim` in `new` describe a correction or revision of an earlier claim, rather than stating a fact directly?"
  [jev.prompt.revision.criteria]
  true = "The claim says that an earlier claim was wrong, changed or replaced, and gives the corrected version."
  false = "The claim states a fact directly, without referring to an earlier claim."
```

The block above is an example configuration for a hypothetical project (the prompt's criteria are
abbreviated). `kblam.toml` is committed, so it holds nothing machine-specific.
The API key is read from the environment variable named by `key_env` if it is set, otherwise from
`key_file`, a path relative to the user's home directory when it starts with `~` (user,
2026-09-23: a key file, not an environment variable). The defaults are `OPENROUTER_API_KEY` and
`~/kblam/jev!.txt`. Each machine keeps the key at that path; the key never enters the repository.

**Per-machine settings (user, 2026-09-25).** `kblam.toml` is committed, so anyone who can change the
repository, and any agent working in it, could otherwise choose which secret kblam sends as the API
key and where it and the findings' text go; the Stop hook would send them with nobody running a
command. So four `[jev]` keys are limited in `kblam.toml`: `key_env` and `key_file` may only hold
their defaults, `endpoint` must be an `https://openrouter.ai/` URL, and `ollama_url` must name this
machine (`127.0.0.1`, `localhost` or `[::1]`). Anything else there is a config error naming the key
and where to set it instead: `~/kblam/config.toml`, an optional per-machine file holding only a
`[jev]` table with any of those four keys as strings. Its values override `kblam.toml`'s and have
none of those limits, since only the machine's user writes it.

`kblam init` writes `kblam.toml` with the `[jev]` section, the
default prompt and thresholds as shown (they belong to the model, not the project) and generic `[kb]`
vocabularies (`evidence_roots = ["evidence"]`, `scopes = ["any"]`) for the project to edit. A project
may edit the prompt text — `kblam prompt-id` then prints a new id, the thresholds no longer match
(§6.4: nothing is rejected until they are recalibrated), and `kblam init` reports the prompt as
differing from the installed one without touching it.

`prompt_version` (an integer, in `[jev]` and in `[jev.thresholds]`) is gone: a config that still
carries it, or lacks `[jev.prompt]`, is an error naming the move, never a fallback. `.kblam/pairs.sqlite`
from before the move is the same kind of error: kblam names the file and asks the user to delete or
migrate it, because the answers in it are keyed by the old integer.

## 10. Calibration experiment (must run before any reject mode is enabled)

This follows the pilot project's evidence rules: one evidence package for the run
(`evidence/<date>-jev-contradiction-eval/`) with README (objective, decision, outcomes, stop
condition), `raw/` (the labelled pairs and the raw API responses) and `derived/`.

10.1 **Decision it changes:** for each verdict (contradiction, duplicate, restate-and-extend, revision), whether Jev
     rejects writes, only raises review items, or is not used.

10.2 **Labelled set, built from the pilot corpus** (~150–250 pairs):
- *Contradictions*: every place the corpus corrects itself: sections marked FALSIFIED, WITHDRAWN,
  SUPERSEDED or "corrected" (~40 markers across a handful of state, handoff and findings
  documents, including the desk answer files). Pair the original claim with the correcting
  claim. Anchor example: one calibration document's C10 against its C16.
- *Duplicates*: facts restated between `desk-*-answers.md` and the findings documents, and between
  the two calibration findings documents that keep parallel numbered series.
- *Compatible, same subject*: adjacent entries about the same component that don't conflict.
- *Unrelated*: random pairs across topics.
- *Revision language*: correction paragraphs (positives) vs direct statements (negatives).
- Each claim is rewritten into the §4 claim-paragraph form **before** labelling, and the correction
  wording is stripped from the correcting claim (otherwise Jev detects the word "FALSIFIED", not the
  conflict). Every rewritten claim keeps its verbatim source excerpt(s) with file:line, checked
  mechanically (as K10), and a term scan confirms no correction wording survives in any claim Jev
  sees.
- **Labelling (user, 2026-09-22):** a builder agent extracts and rewrites the pairs and records the
  corpus's own signal (e.g. "B corrects A") separately. Two labellers on different models (one
  candidate model was refused by the provider) then label every pair independently and blind to each other and to that signal,
  including a flag for rewrites that changed meaning or leaked a cue. Agreement is reported.
  Each disagreement goes back to both labellers with the other's label and reasoning; a labeller
  that concedes resolves it (one labeller noticing what the other missed is not a conflict). Only
  pairs where both hold their positions go to the user, who decides them.
- Split 50/50 into calibration and held-out test sets, stratified by class.

10.3 **Procedure:** run every pair (one request per pair, §6.2) twice and report the flip rate;
     choose thresholds on the calibration half; report precision, recall and a confusion matrix on
     the held-out half. Record the served model ID, tokens and cost. Report the resolution of the
     returned values before choosing thresholds: in the M3 smoke both answers came back at exactly
     two decimals (confidence 1.0, probabilities 1.0/0.0, noul 0.99), so thresholds finer than the
     actual resolution are meaningless.

10.4 **Pre-registered cut-offs** (held-out set, per verdict):
- **Reject mode** if precision ≥ 0.90 at recall ≥ 0.70.
- **Review-only** if precision ≥ 0.50 at recall ≥ 0.60.
- **Disabled** otherwise; the deterministic rules (K4, K5, K9, §6.3) still apply.

10.5 **Stop condition:** one full run, plus at most one round of prompt-wording revision on the
     calibration half. If a verdict doesn't reach review-only after that revision, stop; more prompt
     tuning on ~100 pairs would overfit.

10.6 **Result (2026-09-22, typesafe/jev-1.13-20260917, the v2 wording):** the labelled set and
     the raw responses are in the pilot project's evidence package (README, `derived/metrics.md`,
     `derived/metrics-topup.md`). 136 corpus pairs plus 84 constructed top-up pairs (duplicates and
     extensions were too rare in the corpus), each run twice; about $0.017 in total. Held-out
     precision / recall: `cannot_both_be_true` 1.00 / 0.83, `revision` 1.00 / 0.91, `same_fact`
     1.00 / 0.71 (all reject), `restates_and_extends` 0.76 / 0.86 (review; 0.43 precision on corpus
     pairs alone). The thresholds are in §6.4 and §9. Limits: small samples; the contradiction
     threshold sits on the weakest calibration positive (PAIR-109 falls just below it on run 2);
     `same_fact` positives are almost all constructed.
     **Default wording (2026-09-25):** the shipped default differs from the v2 wording only in the
     relation instructions' scope parenthetical. It was recalibrated with the same rule on the same
     179 relation pairs, two fresh passes, $0.015: held-out `cannot_both_be_true` 1.00 / 0.83,
     `same_fact` 1.00 / 0.71 (both unchanged), `restates_and_extends` 0.79 / 0.86. The revision
     question is unchanged and keeps its threshold. §6.4 and §9 give the resulting thresholds, which
     the template records against the default prompt's `prompt_id`. The selection rule pins each cut
     on the weakest calibration positive, so a re-run moves cuts by about ±0.02; the held-out
     precision / recall is the stable result. Thresholds and wording move together, since `prompt_id`
     (§6.2) is the id of the text they were measured on: a project that edits `[jev.prompt]` gets a
     new id, and its thresholds, recorded against another, reject nothing (§6.4) until they are
     recalibrated.

## 11. Migration of an existing corpus

0. Run `kblam init` in the repository (§12 M6.5): `kblam.toml`, the §8.2 rule and skill, the §8
   hooks, the pre-commit hook and the CLAUDE.md line are then in place before any finding is written.
1. Start with one topic; a calibration topic is the natural first, since two documents already
   conflict on one entry and cover the same subject.
2. An agent extracts candidate claims into staged findings. For each falsified-and-corrected pair it
   writes only the current fact (P1/P2). Every staged finding goes in through `kblam put`, so the
   validator and Jev check the migration itself. A claim the source states that nothing in the tree
   reproduces, and that no later document or finding contradicts, goes in as `reported` (§4, K11),
   quoting the source at its `history/` path, so it is not lost when the source retires.
3. The source documents move to a `history/` folder that is excluded from agents' default search
   (`.ignore`/`.rgignore`; confirm that the Grep tool honours it), or are deleted, since git keeps them.
4. Before further topics migrate, an independent agent that wrote none of the findings reviews the
   result against the cited evidence. Its corrections go in through `kblam put` like any other.
5. Replace the documentation-discipline section of the repository's CLAUDE.md with the §8.2
   one-line pointer that `init` appended: findings live in `findings/`, they are written only
   via `kblam put`, `kblam validate` is the authority, and history is in git and `evidence/`.
   Check the §13 loading questions with real agents here. Remove the rules the validator now
   enforces from CLAUDE.md and from the research-desk definition (its answers-file INDEX-with-line-
   numbers scheme is failure mode 3). Narrow CLAUDE.md's manifest requirements to observations
   (§13).

## 12. Implementation milestones

M1. Finding schema, `validate` (K1, K2, K4–K10), `index`, `new`, `edit`, `put` (without the Jev
    step, which M5 adds), `tree.hash`, with tests on fixture KBs.
M2. Fingerprints, `ack`, `deps`, K3. Put semantics:
    - A staged finding may list `depends_on: {F-0102: null}`; `put` stamps each null with the
      target's current fingerprint (the author asserts they read it) and reports what it stamped.
      A non-null fingerprint that is stale blocks the put (K3 on the incoming finding).
    - K3 on *other* findings does not block a put. A put that changes a finding's fingerprint
      reports the dependents it made suspect; `validate` (and so pre-commit and the Stop hook) fails
      until each is acked or edited. This keeps one rewrite from freezing every unrelated write.
    - **Edit-base guard.** `kblam edit` records the sha256 of the finding's file bytes at copy time
      (under `.kblam/staging/`). A `put` whose ID already exists in `findings/` requires that record
      and refuses if the current file no longer matches it ("F-x changed since your edit; run
      `kblam edit F-x` again and reapply your change"). A hand-named file therefore cannot
      overwrite an unrelated finding, and two agents editing one finding cannot lose an update.
    - **Lock.** `new`, `edit`, `put`, `ack` and `index` hold an exclusive `.kblam/lock` for their
      read-validate-write span, so concurrent puts cannot both validate against the same old tree
      and concurrent `new` calls cannot allocate the same ID. Waiting is bounded; a stale lock
      (holder dead, or older than a configured age) is broken with a message.
M3. Jev client (endpoint, key loading, retries, cost log, cache) plus a smoke test on one synthetic pair.
M4. Calibration experiment (§10); thresholds written into config, with the `prompt_id` of the wording
    they were measured on (§6.2).
M5. `check`, `audit`, review workflow, and the §6.4 decision policy.
    - Candidate selection (§6.1) and quantity comparison (§6.3) are code, logged per check.
    - **Jev in `put`.** `put` asks Jev *before* taking the lock (so slow network calls don't hold
      other writers), then under the lock recomputes the candidates against the current tree and
      decides. Pairs already asked are cache hits; only pairs the tree gained meanwhile are asked
      under the lock. A reject leaves `findings/` unchanged and exits 4 (§7).
    - A pair or revision question that could not be asked (JevUnavailable) makes the write
      unchecked (§6.5), never accepted as passed.
    - `kblam check [F-…]` runs the same decision for existing findings without writing; reject
      verdicts found there become review items (the finding is already in the tree). `check
      --pending` retries unchecked items. `audit` checks every candidate pair with no cache entry
      for the current fingerprints, model and prompt id.
    - `validate` fails while `.kblam/review.jsonl` holds an open review or unchecked item.
      `validate --record` runs `check` on the changed findings first (§7).
    - Tests use a fake Jev client (no network); one opt-in live test is skipped without a key.
M6. Hooks (§8) and the pre-commit hook; test that code writes outside `findings/` are unaffected and
    that a shell write into `findings/` is caught at Stop.
    The consuming repo gets `.gitattributes` with `findings/** -text`: K7 and `tree.hash` are
    byte-exact, and `core.autocrlf=true` would otherwise check files out with CRLF.
    Agent guidance (§8.2): the `kblam-findings` rule and the `kblam-write` skill, written from this
    spec and checked against the CLI's actual messages and exit codes; every write-stopping message
    (deny hooks, Stop block, `put` refusals) names the skill, with a test that each one does.
    The console script dispatches `kblam hook` without importing the rest of the CLI (§8).
M6.5. Packaging: one install per machine, one command per project (user, 2026-09-23). No Claude
    Code plugin: plugins can't ship rules, and each machine would still need an install step
    (research/desk-hooks-answers.md P1, P3).
    - **Per machine:** `uv tool install git+<kblam repo URL>` puts `kblam` on `PATH`, and the
      OpenRouter key is saved at `~/kblam/jev!.txt` (§9). Nothing else. (Until a published release, `uv tool install <path to a kblam checkout>`.)
    - **Per project:** `kblam init`, run in a git repository, writes everything the project needs.
      The user commits it, and a clone on another machine carries it along. `init` never commits,
      never touches anything outside the repository root, and prints each file it wrote, changed or
      left alone:
      - `kblam.toml` from the §9 template, only if absent (never overwritten, even by `--update`);
      - `<kb root>/INDEX.md` via `kblam index`, only if the KB root has none;
      - `.gitattributes`: the line `<kb root>/** -text` (from the configured root), appended if absent;
      - `.gitignore`: the line `.kblam/`, appended if absent;
      - `.claude/rules/kblam-findings.md` and `.claude/skills/kblam-write/SKILL.md`;
      - `.claude/settings.json`: kblam's hook entries merged in (below). Other keys and other hooks
        are kept; kblam's entries are recognised by a command starting with `kblam hook` and are
        replaced, never duplicated. An unparseable settings file is an error, and nothing is
        written;
      - `CLAUDE.md`: the §8.2 line appended if absent (the file is created if missing); a line that
        starts with it counts as present, since a project may extend the sentence;
      - the git pre-commit hook at `git rev-parse --git-path hooks/pre-commit` (so
        `core.hooksPath` is honoured), made executable. A different pre-commit already there is
        not overwritten: `init` says so, and exits 1 after writing everything else.
      With `--update`, the rule, the skill, the hook entries and a kblam pre-commit hook are
      rewritten to the installed version; otherwise an existing one of those files that differs
      from the installed version is reported, not overwritten.
      Finally `init` checks that `kblam` resolves on `PATH` and runs each hook entry's command
      once, through the shell, with a synthetic input (a Write outside the KB root, and a Stop),
      reporting whether each answered.
    - **The source files** ship inside the package as `src/kblam/assets/` (the rule, the skill,
      the hook entries and the pre-commit script), so an installed kblam has them.
      `.gitattributes` is generated from the configured root. `init` writes files with LF endings.
      The rule (its `paths` filter and text), the skill and the hook entries write the KB root as
      the token `{{kb_root}}`; `init` replaces it with the configured `[kb] root` before writing or
      comparing, so a KB folder with another name gets a rule that loads and text that names it.
    - **Hook entries.** Four handler groups: PreToolUse with matcher `Write|Edit|NotebookEdit`,
      PreToolUse with matcher `Bash|PowerShell`, and Stop and SubagentStop with no matcher. Every
      handler is shell form, calling `kblam` by name:
      `{"type": "command", "command": "kblam hook <Event> || echo '{\"systemMessage\": \"kblam
      hook <Event> did not run (is kblam installed?); findings/ is unguarded\"}'", "timeout": N}`.
      Hook commands run in Git Bash on this machine (H9); the command is also valid PowerShell 7.
      A command that isn't found exits 127, which Claude Code ignores silently (H7); the `||`
      branch turns that into a message the user sees. (`kblam hook` itself always exits 0, §8.)
      `timeout` is in seconds (H8): 30 for PreToolUse, 300 for Stop and SubagentStop, whose check
      can ask Jev and wait for the lock. A timed-out hook fails open. The git pre-commit hook
      (§8 item 4) is the backstop in every case.
    - **As built:**
      - *Order and output.* First line `kblam init: <repo root>`, then one line per item in this
        order: kblam.toml, INDEX.md, .gitattributes, .gitignore, rule, skill, settings.json,
        CLAUDE.md, pre-commit. Each reads `<action> <path> (<note>)`, where action is `created`,
        `updated`, `unchanged`, `kept` (exists and differs; not overwritten) or `refused`. Then the
        `kblam` found on `PATH`, the hook check, and "done. Review the files above and commit them."
        or "finished with the problems reported above (exit 1)."
      - *Before anything is written* init resolves the repository, parses and shape-checks
        settings.json, resolves the pre-commit path, and loads an existing kblam.toml.
      - *Exit codes.* 0 done, including `kept` items. 1: a foreign pre-commit hook, a pre-commit path
        outside the repository (an absolute `core.hooksPath`, a worktree, a submodule: nothing is
        written there, and the message names the asset to install by hand), settings.json that is
        not JSON or not a mergeable hooks table (nothing written), `kblam` not on `PATH`, neither
        bash nor pwsh found, a hook that did not answer, or a write error. 2: not in a git
        repository, git missing, `--root` given, or an existing kblam.toml unreadable or invalid
        (nothing written). 3: lock timeout from the index step.
      - *settings.json.* Read as UTF-8 (a BOM is accepted); a missing file is created with only the
        hooks. kblam's handlers are those whose command starts with `kblam hook`; they compare as a
        set of (event, matcher, handler). Merging removes kblam handlers from every group, drops a
        group that leaves empty, keeps every other handler, and appends kblam's groups to their
        events. A rewrite is reformatted as 2-space JSON.
      - *Appended lines* count as present when a line of the file, stripped, equals them exactly
        (so `/.kblam/` in .gitignore gets a second `.kblam/` line). A missing final newline is added
        first, and CLAUDE.md's line gets a blank line before it.
      - *kblam.toml* comes from a fifth asset, `src/kblam/assets/kblam.toml`: the §9 values with
        the generic `[kb]` vocabularies. An existing one is reported `unchanged` if it equals the
        template, else `kept`. INDEX.md is created by `kblam index` only if absent, which also
        writes `tree.hash`.
      - *Pre-commit.* A file containing `# kblam pre-commit hook` is an older kblam version (`kept`,
        or rewritten with `--update`); anything else is foreign and never overwritten. It is written
        with mode 0755; other files keep their existing mode or get 0644.
      - *Hook check.* bash is the first `bash` on `PATH`, skipping on Windows the WSL launchers
        under `%SystemRoot%` and `WindowsApps`; otherwise `pwsh -NoProfile -Command`. It runs in the
        repository root with `CLAUDE_PROJECT_DIR` set and the handler's timeout. Both PreToolUse
        groups get a Write of `<repo>/kblam-init-hook-check.txt`, and Stop and SubagentStop get
        `stop_hook_active: false` with no `agent_type`. A hook answered if it exited 0 with no output
        or with JSON other than the did-not-run message. On a KB whose `tree.hash` is stale, the Stop
        run does the real check and may ask Jev.
      - *Key file.* `~` expands to HOME, or USERPROFILE on Windows, here and in the per-machine
        `~/kblam/config.toml` (§9). Any other relative `key_file` is relative to the repository
        root. The missing-key message names both the variable and the file.
      - *Hooks and config.* Only the upward search for kblam.toml finding none is silent. An
        explicit `--root` without kblam.toml, an unreadable file, invalid TOML or an invalid value
        gets the systemMessage note. Malformed hook input and unknown events are checked before
        the config and always get the note.
    - Tests: `init` in a fresh git repo under `tmp_path` produces a KB that validates; a second
      `init` changes nothing; existing settings keys and hooks survive the merge; a foreign
      pre-commit hook is not overwritten; `--update` rewrites a changed skill but never
      `kblam.toml`; the hooks are silent with no `kblam.toml`.
M6.6. Generality fixes (user, 2026-09-23), before M8:
    - Candidate selection as in §6.1: linked findings plus BM25 similarity over the whole KB,
      replacing auto-extracted anchors and the ID tie-break. `topic_bonus` in §9. The check log
      (`checks.jsonl`) records each candidate's reason (linked: which link; similar: its score).
    - K6 defaults 250 words and 300 lines (§5, §9, the template, the skill).
    - K4's term list replaced by the phrases that an evaluation on the pilot corpus
      (not published) found used only in unwanted senses; a test per dropped term
      shows its legitimate use now passes (e.g. "the dark frame is used to subtract the offset").
    - `.kblam/` protection in the hooks (§8).
    - `{{kb_root}}` substitution in the assets (M6.5).
    - **As built:**
      - *Tokens.* The regex `[\w./-]+` on the lowercased text (`\w` is Unicode); trailing `.`, `-`
        and `/` stripped, leading ones kept (`/usr/lib`, `.5`); apostrophes split words; empty
        tokens dropped. Stop words: Lucene's English set (a an and are as at be but by for if in
        into is it no not of on or such that the their then there these they this to was will
        with).
      - *Documents.* Title plus claim paragraph (the `**Claim.**` marker removed), for every
        readable finding in the KB root; staged files are not in it. Document length is the token
        count after stop words.
      - *Score.* IDF = ln(1 + (N − n + 0.5)/(n + 0.5)), always positive, so a score of 0 means no
        shared token. Each distinct query token counts once; sums run in sorted-token order, so
        equal documents score identically. N's own current version is in the statistics during
        check, audit and Stop, but not during a put of a new ID; the version being replaced by an
        edit is in the statistics but never a candidate.
      - *Bonus.* `topic_bonus` (≥ 0) times N's top raw score over all other findings (every scope,
        linked ones included), added only to same-topic findings with a raw score above 0. Topics
        compare as exact strings.
      - *Order.* M6.8 gives the current order. A finding with several links appears once, with all
        of them.
      - *Log.* `checks.jsonl` records each candidate's `reasons` as strings: `depends_on`,
        `anchor <normalised anchor>`, `evidence <N's evidence path>`, `similar <score>` or
        `similar <total> (bm25 <raw> + topic <bonus>)`. `over_budget` and `different_scope` are ID
        lists; `different_scope` holds every linked or nonzero-score finding with a disjoint scope.
      - *`.kblam/` in the hooks.* `.kblam` itself counts (so `rm -rf .kblam` is denied), and so
        does `.kblam/staging` itself for the exemption. Bash removals: every operand of `rm` and
        `rmdir`, and the `mv` sources (every operand but the last, or all of them with `-t` /
        `--target-directory=`). PowerShell removals: `Remove-Item`, `rm`, `del`, `ri`, `erase`,
        `rd`, `rmdir` (every positional argument plus `-Path`/`-LiteralPath`, split on commas), and
        the `Move-Item` source. Not caught: `unlink`, `find -delete`, `git rm`/`git clean`,
        `truncate`, a `cd` before a relative path, and globs. A command that hits both roots gets
        one deny with both sentences ("writing …" and/or "removing …") and one skill pointer.
      - *`{{kb_root}}`* is replaced as text in the rule and the skill, and inside the JSON string
        values of the hook entries after parsing.
      - *`[kb] root`* must be `/`-separated segments of ASCII letters, digits, `.`, `_` and `-`
        (after normalising a trailing `/` or leading `./`); anything else is a config error, since
        a quote would break the hooks' shell quoting or the rule's YAML.
      - *K4 and K5* report once per matching term, so "was wrong about" is reported under both
        "was wrong" and "wrong about".
M6.7. Embedding candidates (user, 2026-09-24): §6.1's embedding mechanism with BM25 fallback, the
    `.kblam/embeddings.sqlite` vector cache, and the §9 keys, in both `kblam.toml` files.
    - Standard library only: `urllib` against the configured URL, and a pure-Python dot product.
      No numpy and no `ollama` package.
    - The `checks.jsonl` record gains `similarity`: `embed <model>`, `bm25 (<reason>)` after a
      fallback, or `bm25` when `embedding_model` is `""`. An
      embedding candidate's reason reads `similar <cosine> (embed)`.
    - **Tests** use a fake ollama (a local HTTP server or an injected client), never a real
      server. They cover:
      - ranking by cosine, with ties by ID;
      - fallback on a refused connection, on a missing model, and on an embed request that fails
        partway through, each with its stderr line;
      - `embedding_model = ""` forcing BM25;
      - a cache hit sending no request, and a digest change sending one;
      - the 64-input chunking;
      - BM25 behaviour unchanged.
M6.8. Link bonus (user, 2026-09-24): §6.1's links become a score bonus; `depends_on` stays first.
    - `link_bonus` (≥ 0) in §9 and in both `kblam.toml` files. N's top raw score is the one
      `topic_bonus` uses: the maximum over all other findings, every scope, before any bonus.
    - A dependency ranks before every other candidate. Among dependencies, and among the rest,
      order is by raw score plus topic bonus plus link bonus, then by ID.
    - A linked finding is a candidate even at a raw score of 0 (BM25 with no shared token), so a
      finding linked to N can still be compared with it.
    - `checks.jsonl` reasons keep the links (`depends_on`, `anchor …`, `evidence …`) and add the
      score for every non-dependency candidate: `similar <total> (embed <cosine> + link <bonus>)`
      or `similar <total> (bm25 <raw> + topic <bonus> + link <bonus>)`, with zero terms omitted.
    - **Tests** cover:
      - a dependency ranking first despite a lower score;
      - a linked finding overtaking an unlinked one within the bonus, and losing to one beyond it;
      - one bonus for several links;
      - a zero-score linked finding under BM25 being a candidate;
      - `link_bonus = 0` giving pure score order;
      - the new reason strings.
M6.9. Reported claims (user, 2026-09-25): the `reported` label (§4) and K11 (§5).
    - `[kb] reported_label` (default `"reported"`) and `history_dirs` (default `["history"]`) in §9;
      `"reported"` joins the default `labels` and both `kblam.toml` files. A history path matches
      by whole leading segments, with or without a leading `./`.
    - The read rule (§8.2), the write skill's format and refusal table, and a "Reported findings"
      section in the skill say what the label means and how a reported finding is promoted.
    - **Tests** cover: a reported finding quoting history passing every rule; one with no history
      excerpt; history in another label's `evidence`; history in another label's prose and
      excerpts passing; a dependency on a reported finding, from another label and from a reported
      one; the label checks off when the label is not in the vocabulary; configurable folders.
M7. **Deferred (user, 2026-09-24).** kblam MCP server and a librarian with no Bash/Write/Edit
    tools. The MCP `put` goes through the same edit-base guard and lock as the CLI (M2). Nothing
    before or in M8 depends on it: the librarian deployed now (§8.1) uses the CLI.
M8. `kblam init` in the pilot project, the calibration-topic migration (§11), done by the librarian
    if deployed, otherwise by a migration agent using the CLI, and the CLAUDE.md rewrite. Then the
    §13 loading questions are checked with real agents.

## 13. Open decisions and unknowns

- **Rule and skill loading (§8.2), unknown:** whether project rules and skills reach subagents and
  teammates the same way they reach the main session (the docs are silent either way, H10). M8
  checks it with a real teammate. (A path-scoped rule loads on Read, not on Grep; that is
  documented, and the CLAUDE.md one-liner covers it.) Observed in M8 (2026-09-23): an agent whose
  definition has an explicit `tools:` list without `Skill` (the `implementer` type) cannot load
  the skill. A teammate of that type found the CLAUDE.md line, then asked how to load the
  skill without the Skill tool. It was told to read `.claude/skills/kblam-write/SKILL.md` as a
  file. A teammate of the same type running a different model found and read that file without
  being told.
  - Since 2026-09-24 (user), the user-level `implementer` and `research-desk` definitions list
    `Skill`.
  - Whether a teammate of those types loads the skill through that tool is still to be checked
    with a real teammate, in §11 step 5.
- **Shell-tool routing, not measured and not needed:** with `CLAUDE_CODE_USE_POWERSHELL_TOOL=1`
  the docs say a Bash-only matcher never fires, yet the Bash tool is still available in sessions
  here (H9). §8 matches both shell tools, so no answer would change anything.
- **Jev thresholds and longer claims:** the §10 calibration used claims rewritten to at most 80
  words; the default is now 250 (K6). The migration's 166 checks (4077 asked pairs, claims up to
  250 words, `.kblam/checks.jsonl` and `review.jsonl` in the pilot project, 2026-09-24) gave these
  results:
  - `low_confidence` at 12.7% of pairs, against 6.7% in calibration, with no merge among the
    items it raised. The cut is now 0.3 (§6.4).
  - `restates_and_extends` fired 49 times; 18 of the 34 items it raised were closed as distinct.
  - `cannot_both_be_true` rejected 17 puts and `revision` 4. Before `af316b5`, a reject left no record, so its
    precision on long claims is unknown. Rejected items (§6.4) now record it: each `R-…` closed
    distinct is a false reject.
  - The reject thresholds stay. A recalibration on long claims follows if the rejected items show
    false rejects above the calibration's rate.
- **Candidate recall of §6.1, measured on the M4 labelled pairs (an evaluation over the pilot
  corpus, not published, 2026-09-23; questions S1–S10 in `research/desk-sim-answers.md`).** Each
  positive pair was ranked in both directions against a pool of 461 claims.
  - BM25 as built found the partner within 30 candidates for 96.7% of all pair-directions, and
    for 96.7% of the 122 corpus directions. Its 8 misses are two pairs that share no token
    (PAIR-024, TOP-014) and two paraphrases.
  - The best local embedding (ollama `embeddinggemma:300m`) reached 99.2% overall and 98.4% on
    corpus. RRF fusion of the two did no better than the embedding alone.
  - Stemming changed nothing that matters. The scope gate lost no positive pair.
  - Embeddings let more `compatible_same_subject` negatives into a budget of 30: 72% against
    BM25's 55%.
  - Linked-first ordering, tested with the source document standing in for `evidence`, lowered
    BM25's corpus recall@5 from 86% to 50%, and at @30 it traded misses rather than removing
    them. The labelled claims carry no real links, so this does not measure linking on a real
    KB.

  Decided (user, 2026-09-24): use the embedding when ollama serves the model, and BM25 otherwise
  (§6.1, M6.7). Dependencies keep priority, and a shared anchor or evidence path is a score bonus
  (§6.1, M6.8). In the real KB, linked-first filled most of the budget in a tail of checks.

- **Deferred (user, 2026-09-24):** the MCP server (M7). Idea recorded for then:
  rejection tickets. A rejected `put` returns `needs_rewrite` with a ticket stored under `.kblam/`,
  and the Stop/SubagentStop hook blocks the agent from finishing while it holds an open ticket.
  This works the same for the CLI and MCP. A ticket would be keyed on the hook input's `agent_id`
  (§8.1, H15). SubagentStop also fires for Claude Code's internal agents, with an empty or
  session-level `agent_type`, so a ticket check must ignore those.

- **Implementation language: Python + official `typesafe_sdk` (decided by the user, 2026-09-22).**
  §6.4's decision policy copies jev-dsl's design of explicit, named threshold policies, enforced by
  tests instead of types. The facts behind the decision (desk-jevdocs Q2, Q9; claims marked ✓ were
  independently verified against the jev-dsl repo):
  - *Python + official `typesafe_sdk`* (PyPI `typesafe-sdk` 0.7.1, Python ≥ 3.10): talks to
    OpenRouter by changing the base URL; retries built in. Windows support is undocumented (desk-jevdocs Q2), but 0.7.1 was observed on 2026-09-22 running on this Windows machine and reaching OpenRouter's `/api/v1/systemone` (M3 smoke), and it carried the M4 calibration's requests without a transport failure.
  - *Haskell + `inanna-malick/jev-dsl`* (MIT, early alpha): a
    **library that builds and decodes packets, not a client** ✓ (cabal: "No network: any transport
    carries the JSON"). The bundled transport scripts are hard-coded to TypeSafe's own endpoint, so
    OpenRouter needs a replacement transport. Whether its decoder accepts OpenRouter's extra
    response members (`id`, `provider`, `usage.cost`) is untested. Needs GHC 9.12+ ✓
    (tested-with 9.12.3, base ≥ 4.21). **No documented Windows build** ✓ (the flake lists only
    Linux and macOS systems), so WSL2 + Nix, or an untested GHCup route. It provides typed
    threshold *policies* (mass, margin, confidence; the policy is part of the type, so a lenient
    verdict can't reach code that demands a strict one) and compile-time exhaustiveness over the
    relation options. It has no cache, cost log, parallel runner or calibration; its own policy
    floors are "plausible, not calibrated". It cannot send structured (object/array) question or
    option text, or Noul `criteria.true/false`. Pin a commit, not a version.
  - Either way kblam builds the cache, cost log, parallel runner, validator, hooks and calibration
    itself; jev-dsl changes only the request/decision layer.
- Exact API schema, limits, errors and SDK behaviour: desk-jevdocs Q1–Q2.
- Confidence semantics and threshold method: Q3. Pairwise precedents (entity alignment, citation
  check): Q4. Wording rules: Q5. Fan-out vs one pair per request: Q6. Determinism and pinning: Q7.
  Data handling: Q8. The user has approved sending finding text to TypeSafe via OpenRouter.
- **The Grep tool honours `.ignore` (§11 step 3), observed 2026-09-24.** In the pilot project,
  with `history/` in `.ignore`, a directory-wide Grep for `offset` found nothing under `history/`.
  The same Grep given one retired document's path found 8 lines, and `grep -c` also counts 8.
- **Evidence manifests in the pilot project record observations only (P3; coordinator, on the
  user's delegation, 2026-09-24).**
  - A manifest keeps everything CLAUDE.md asks for now except interpretation: the objective and
    decision, the outcomes, controls and stop condition, the device state, the commands and
    hashes, the artifact inventory and the reproduction steps.
  - Its results are what was observed, and each names the finding IDs that interpret it.
  - `inferred` and `decoded` conclusions go in findings, where they can be corrected. In a
    manifest they would outlive their correction.
  - The CLAUDE.md edit is part of §11 step 5. Existing manifests are not rewritten.
