# kblam — specification (draft 2, 2026-09-25)

kblam is a small knowledge-base system for research findings maintained by several LLM agents. It
keeps the knowledge base (KB) limited to **current facts**. It enforces that mechanically, and uses
the Jev model (TypeSafe AI, called through OpenRouter) to detect a new or edited finding that
contradicts or duplicates an existing one.

Developed against a pilot project's findings corpus (a hardware reverse-engineering research
effort). The tool itself must stay project-neutral; project specifics live in
a config file in the consuming repository.

## About this document

**Status.** The design is settled with the user except where §13 lists an open question. Draft 2
resolves every finding of a review of draft 1, on the user's instruction to fix them all
(2026-09-25); Appendix A records the decisions that review needed and the reason for each. Behaviour
that is specified here but not built yet is marked "(not yet built)" where it is described, and
M6.10 (§12) lists all of it. Everything else describes kblam as built, and each milestone's
"Status" line says what is implemented.

**Conventions.** §3–§12 say what kblam does. A paragraph headed "As built" records an
implementation detail that tests rely on (CONTRIBUTING.md). A decision the user made or delegated
carries an attribution such as "(user, 2026-09-24)", and CONTRIBUTING.md asks that such a decision
not be reversed without asking. "`findings/`" stands for the KB root, the folder `[kb] root` names
(§9). Appendix A holds the dated history of decisions, Appendix B the measurements behind the
design, and Appendix C what belongs to the pilot project's deployment rather than to kblam.

**Sources.** Research notes are cited by short name and entry, for example "desk-hooks H15" for
entry H15 of `research/desk-hooks-answers.md`:

- desk-kbtools (`research/desk-kbtools-answers.md`): requirements-traceability tools, validators
  and agent-memory systems.
- desk-llmwiki (`research/desk-llmwiki-answers.md`): Karpathy's LLM-wiki pattern and its
  derivatives.
- desk-reddit (`research/desk-reddit-answers.md`): practitioner reports.
- desk-hooks (`research/desk-hooks-answers.md`): Claude Code hooks, rules, skills and plugins
  (entries H1–H15 and P1–P8).
- desk-sim (`research/desk-sim-answers.md`): candidate recall of BM25 and local embeddings
  (entries S1–S10).
- desk-jev-wording (`research/desk-jev-wording-answers.md`): the Jev question wording against
  TypeSafe's guidance, and the composition of the labelled set (entries A1–A5).
- desk-jevdocs (`desk-jevdocs-answers.md`, at the repository root): the Jev API, its SDK and
  TypeSafe's guidance. Q1–Q2: the API schema, limits, errors and SDK behaviour; Q3: confidence
  semantics and threshold method; Q4: pairwise precedents (entity alignment, citation check); Q5:
  wording rules; Q6: fan-out vs one pair per request; Q7: determinism and pinning; Q8: data
  handling; Q9: jev-dsl. Details of the Jev API are cited to these entries and must be checked
  against that file, which is the working evidence for them.

Documentation pages are cited as `typesafe/<page>.md` and `openrouter/<page>.md`, the names
REFERENCES.md lists with their URLs; `typesafe/model-jaggedness_jev-1.13.md`, TypeSafe's page on
where Jev 1.13 is weak, is also called the jaggedness page. The pilot project's own evidence (its
evidence packages, evaluation files and logs) is not in this repository: the spec names it by role,
and §13 lists the figures that still need a published source.

**Terms and roles.**

| Term | Meaning |
|---|---|
| finding | One claim in one file under the KB root (§4). |
| claim paragraph | The first paragraph of a finding's body; what Jev compares (§4). |
| fingerprint | A hash of what a finding asserts; `depends_on` records it (§5.1). |
| state hash | A hash of a finding exactly as Jev sees it; keys cached answers and resolutions (§6.5; not yet built). |
| staged file | A finding being written or rewritten under `.kblam/staging/`, waiting for `kblam put` (§7). |
| item | An entry in `.kblam/review.jsonl`: a *review* item (a verdict someone must decide), a *rejected* item (a Jev verdict that refused a `put`) or an *unchecked* item (a finding Jev could not fully check) (§6.4). |
| resolution | What `kblam resolve` records when it closes an item as Jev's misreading (§6.4). |
| author | The agent whose write raised an item or made a dependency suspect. |
| adjudicator | Whoever decides items and suspect dependencies: the librarian when one is deployed, otherwise the coordinator; never the author (§8.1). |
| coordinator | The agent that assigns the other agents' work and addresses them as teammates (in the pilot, the lead agent of an agent team). |
| librarian | An optional long-lived agent (§8.1): today an adjudicator; after M7 also the only writer of the KB. |
| research agent | An agent doing a project's research (experiments, code, reading); it writes findings through kblam unless a writing librarian does. |
| main session, subagent, teammate | Claude Code terms: the session a person started; an agent another agent spawned inside its session; a separate agent session a coordinator addresses by name. Hook input tells them apart only as desk-hooks H3 and H15 describe (§8.1). |
| research desk, desk answers file | In the pilot, an agent role that researches questions and records its answers in `desk-*-answers.md` files; the notes in `research/` are such files. |
| work package, graduation | In the pilot, a unit of assigned work; and the step that copied desk answers into findings documents, a source of failure mode 5. |
| evidence package | A folder of immutable experiment records under an evidence root: a manifest (README) plus raw and derived files (P3, §10). |
| retired document | A source document moved to a history folder when its topic migrated (§11); only `reported` findings quote it (§4). |
| M3 smoke, M5 smoke | The live smoke tests run while building milestones M3 and M5 (§12). |

---

## 1. The problem

Agents share findings in large markdown files (the pilot corpus: ~34k lines across ~40 root
`.md` files of up to about 3,500 lines). Written instructions (CLAUDE.md, a 34 KB research-desk
agent definition) have not stopped five failure modes, all observed in that corpus:

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

Research behind this design (the sources are listed under "About this document"):

- desk-kbtools: only Doorstop has the needed change-propagation mechanism (per-link fingerprints /
  suspect links, desk-kbtools A3); nothing covers failure modes 1, 4 or 5.
- desk-llmwiki: the main implementations of the LLM-wiki pattern deliberately keep falsified claims
  and annotate them (the opposite of this design). No system blocks a bad KB write; every
  deterministic checker exits 0. "Retain but make it unretrievable" has no working implementation
  (desk-llmwiki A12).
- desk-reddit: the complaint is common; Anthropic states that there is "no … duplicate-rule
  detection, or cross-file conflict detection yet" (anthropics/claude-code#85477, verified). The
  convergent practitioner design is an overwritten current-state file plus a separate append-only
  history, enforced by a hook. No measured results exist for any intervention.

**How the design answers each failure mode.**

| Failure mode | Mechanisms |
|---|---|
| 1. A falsified claim stays; the correction is appended elsewhere | P1 (edit in place); K4 and K5 (revision language); the `revision` and `cannot_both_be_true` verdicts refuse a correcting finding (§6.4); the adjudicator owns the old finding (§8.1) |
| 2. New documents or sections supersede old ones | One claim per file (§4); K8 (nothing but findings in the KB); K9 and `same_fact` refuse a restatement; the Stop and pre-commit hooks catch writes that bypass `put` (§8) |
| 3. Hand-maintained indexes go stale | P5; `INDEX.md` is generated and checked byte for byte (K7) |
| 4. Failed experiments are kept whole, with a correction appended | P2 and P3 (the negative result is the current fact; raw records stay in evidence packages); K6 bounds a finding's length; K4, K5 and `revision` catch the appended correction. No rule recognises a whole experiment written up as one finding: only the adjudicator's reading does (§8.1) |
| 5. The same fact is copied into several files | P6; K9, `same_fact`, `restates_and_extends` and `quantity_conflict` (§6); `kblam rm` after a merge (§7); the librarian replaces desk answers files (§8.1) |
| Self-reports are not evidence | Every rule is a check on the files (P4), run by `put`, `validate`, the Stop hook and the pre-commit hook (§8) |

## 2. Principles (decided with the user)

- **P1. The KB contains only current facts.** When a claim is found wrong, the original finding is
  **edited in place** so it states only what is true now. There is no withdrawn/superseded status,
  no `withdrawn_by` or retraction pointer, no archive section, no "this was wrong" note, and no new
  finding that corrects an old one.
- **P2. A negative result is a current fact**, written positively: "X does not do Y; evidence: …".
  That is how dead-end knowledge survives without keeping the mistaken claim.
- **P3. History lives outside the KB.** Earlier versions of a finding live in git history (not in
  the working tree, so no grep or glob reaches them; they're reached only deliberately via
  `git log -p`). Raw experiment records live in immutable `evidence/` manifests. Manifests record
  observations and procedure; interpretation belongs in findings, because an interpretation
  written into an immutable file can never be corrected.
- **P4. Enforcement is mechanical.** Every rule that matters is checked by code on the files, at a
  point the agent cannot skip. Prose instructions describe the system; they do not enforce it.
- **P5. Nothing agents read is maintained by hand.** Indexes and catalogs are generated.
- **P6. One fact, one place.** A finding that restates another is rejected; the agent edits the
  existing one.

## 3. Knowledge-base layout (in the consuming repository)

```
<repo>/
├── kblam.toml                 # project config (§9); only a person changes it (§8)
├── kblam.resolutions.jsonl    # resolutions of misread items, committed (§6.4; not yet built)
├── findings/                  # the KB root ([kb] root)
│   ├── INDEX.md               # GENERATED by `kblam index`; never hand-edited
│   └── <topic>/               # topic folders, e.g. calibration/, protocol/, banding/
│       └── F-0137-pressure-curve-types.md
├── evidence/                  # immutable evidence packages ([kb] evidence_roots, §4)
├── history/                   # retired source documents ([kb] history_dirs, §11)
├── .claude/                   # the §8.2 rule and skill and the §8 hook entries (kblam init)
├── CLAUDE.md                  # carries the §8.2 pointer line
└── .kblam/                    # gitignored machine state, never committed (§8)
    ├── pairs.sqlite           # Jev answers, resolutions and "checked" marks (§6.4, §6.5)
    ├── embeddings.sqlite      # embedding vector cache (§6.1)
    ├── calls.jsonl            # one line per Jev request: model id, tokens, cost, latency
    ├── checks.jsonl           # one line per check: candidates with reasons, verdicts (IDs and fingerprints, no finding text)
    ├── recheck.jsonl          # one line per check: command a recheck considered: IDs, digests, outcome (no command text, §7)
    ├── recheck/               # F-NNNN.log: the output of that finding's last recheck (§7)
    ├── review.jsonl           # review, unchecked and rejected items (§6.4, §6.5); an open review or unchecked item fails `validate`, a rejected one does not
    ├── staging/               # findings being written or rewritten (`new`, `edit`), awaiting `put`, and `edit`'s edit-base records (§7)
    ├── lock, lock.break       # the writers' lock and its break guard (§7)
    ├── config-approved        # digests of the kblam.toml versions a person approved on this machine (§8 item 4)
    ├── stop-block             # the tree digest at the Stop hook's last block (loop guard, §8 item 3)
    └── tree.hash              # digest of findings/, advanced only across kblam's own writes (§8)
```

`kblam init` also adds lines to `.gitattributes` and `.gitignore` (§7.1), and §11 adds history
folders to `.ignore`. Each machine keeps the Jev API key, by default in `~/kblam/jev!.txt`, and may
keep a per-machine `~/kblam/config.toml` (§9); neither is in the repository. Each clone keeps the
`check:` commands a person approved in its git directory, `.git/kblam/recheck-approved.jsonl`
(shared by its linked work trees), where no commit can write (§7, `kblam recheck`).

Topic folders are organised by subject, never by work package or agent (naming a document after
the work package that produced it is what creates parallel truths). `[kb] topics` makes that a
checked vocabulary (K1; not yet built).

**As built.** `tree.hash` holds the sha256, in hex, over every file under the KB root (`INDEX.md`
included) in order of its path relative to the KB root: for each file its UTF-8 path, a NUL byte,
its length in decimal, a NUL byte, then its bytes.

## 4. Finding format

One claim per file. Filename `F-NNNN-<slug>.md`. The ID is `F-` and at least four digits (`F-0137`;
`F-10000` follows `F-9999`), and it never changes. The slug is lowercase ASCII letters and digits in
runs joined by single hyphens, and it may change; `kblam new` derives it from the title (§7).

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
verified: 2026-09-22       # when the author last checked the claim against its evidence
---

**Claim.** The pressure sensor's curve table lists two types; their outputs agree to about 0.1%
(median ratio 1.0017 over the run), so they are not two analog gains; a 1.8× gain difference would
show as a ratio near 1.8.

<supporting detail, verbatim excerpts with file:line or offset attribution>
```

Rules:
- **Keys.** Required: `id`, `title` (one non-empty line), `topic`, `label`, `scope` (a non-empty
  list), `evidence` (K2) and `verified`. Optional: `depends_on`, `anchors`, `quantities`, `check`.
  Any other key is a K1 error.
- **The claim.** The first paragraph is the claim: the first run of non-blank lines after the
  frontmatter, joined with single spaces. A leading `**Claim.**` (or `**Claim:**`) marker is
  conventional and is not part of the claim. It must be prose, not a heading, blockquote, table,
  comment or code block, and its length is bounded (K6). Jev comparisons use the claim paragraph
  plus the scope, not the whole body.
- **`verified`** is a date (YYYY-MM-DD): when the author last checked the claim against its
  evidence. `kblam new` sets it to the day it writes the skeleton, `kblam edit` copies it
  unchanged, and the author updates it after re-checking. It is the author's record; kblam checks
  only its form (K1).
- `depends_on` stores fingerprints, not bare IDs (Doorstop's stamp/cleared mechanism,
  desk-kbtools A3; §5.1).
- `anchors` entries must be YAML strings: an unquoted `0x1A2B3C` parses as an integer, so K1
  rejects non-string anchors and asks for quotes.
- **`quantities`** entries are `{name, value, unit}`: `name` a non-empty string, `value` a number
  (an integer or a decimal, never a quoted string; a hex value is written in decimal, or declared as
  an anchor), `unit` an optional string. §6.3 compares them.
- Paths in `evidence` and in verbatim tags (K10) are relative to the repository root, which is the
  directory containing `kblam.toml` (found by walking up from the current directory, or `--root`).
  Evidence lies under an `[kb] evidence_roots` folder (K2; not yet enforced).
- `check` is the optional "warrant" idea (desk-reddit): a command whose re-run reproduces the
  finding's key number. The command itself compares what it computes with the finding and exits
  non-zero on a mismatch. It is a program and its arguments, split as a POSIX shell splits them but
  run without a shell, from the repository root (user, 2026-09-26), so a pipeline goes in a script
  under `evidence/`. `kblam recheck` runs these commands, each only once a person has approved it
  on that machine, and reports any that fail (§7).
- **Scope** values come from `[kb] scopes`, and two conventions apply to them (§6.1): a value
  containing `/` stands for each of its parts, and `any` overlaps every scope. Both symbols are
  configurable (`scope_separator`, `scope_wildcard`, §9; not yet built: today they are fixed).
- `reported` (user, 2026-09-25) labels a claim that a retired source document states and that
  nothing in the tree reproduces. The current fact it records is that report: the claim says which
  document states it and that it is not reproduced, and the body quotes the document's lines from
  a history folder (`history/`, §11 step 2) in a verbatim excerpt. It is not a withdrawn or
  superseded status (P1): a claim a later document or finding contradicts is not migrated at all.
  Retired documents are evidence for no other label, and no finding may depend on a reported one,
  so an unreproduced figure cannot become the support of a fact. A reported finding is promoted by
  reproducing it in an `evidence/` package and editing it: new label, the package's output as its
  excerpt. K11 enforces the mechanics; the label name and the history folders are configurable
  (§9).

## 5. Deterministic validator (`kblam validate`)

Runs without the network. Exit status is non-zero on any error. Rule codes:

| Code | Rule |
|---|---|
| K1 | Frontmatter parses and matches the schema (§4); `id` is unique and matches the filename; `topic` is a folder name (lowercase letters, digits, `-`, `_`) that matches the folder and, when `[kb] topics` is non-empty, is in it (not yet built); `label` and `scope` are in the configured vocabularies; `verified` is a date; `depends_on` maps finding IDs to string fingerprints or null; `anchors` are strings; each quantity is well formed (§4); `check` is a non-empty string. |
| K2 | `evidence` has at least one entry, and every path is relative, resolves inside the repository root and exists. Every path also lies under an `[kb] evidence_roots` folder, or, for a reported finding, under a `history_dirs` folder (not yet built: as built, any path inside the repository passes). Every `depends_on` ID exists, and a finding does not depend on itself. |
| K3 | **Suspect dependency:** a `depends_on` fingerprint differs from the target's current fingerprint (§5.1), because the target was rewritten, or is null. Resolved only by re-reading the target and running `kblam ack F-x F-y` (which records the new fingerprint, §7), or by editing the dependent. A null in a staged file is stamped by `put`; a null in the KB root is an error (unstamped). A cycle between two or more findings is allowed: `depends_on` is outside the fingerprint, so an `ack` on one edge never makes another edge suspect. |
| K4 | **Revision-history language** in a finding's title or body (`history_terms`, §9: phrases that an evaluation on the pilot corpus found used only in unwanted senses, e.g. `was wrong`, `supersed`, `withdrawn`, `refuted`, `is falsified`, `previously believed`, `no longer true`). Matching is case-insensitive and anchored at a word start, so the stems `supersed` and `retract` match their inflections. Verbatim excerpts that pass K10 are exempt, because they quote sources (a quoted datasheet line such as "this document supersedes revision C" must not trip it); an excerpt of a binary source is not checked by K10 and so not exempt. One issue per term per line. Heuristic; Jev's revision question (§6.2) catches paraphrases. |
| K5 | A finding names another finding's ID within `history_id_window` words (default 10) of a term from `history_id_terms` (§9): words that are ordinary on their own but, next to another finding's ID, describe that finding as wrong or replaced (catches "F-0102 is wrong", "replaces F-0102"). Same K10 excerpt exemption as K4. One issue per (ID, term). Not yet built: as built, K5 uses the K4 terms, so it fires only where K4 does and adds only a message naming the other finding; that stays the behaviour while `history_id_terms` is absent. |
| K6 | File length ≤ `max_lines` (default 300); the claim paragraph exists, is prose (§4), and has ≤ `max_claim_words` words (default 250; §13 notes that calibration used claims of at most 80). |
| K7 | `INDEX.md` is byte-identical to what `kblam index` would generate. |
| K8 | No file of any kind under `findings/` other than `INDEX.md` and findings directly inside a topic folder, one level below the KB root (stops "summary", "handoff" and notes files from appearing in the KB). A finding file anywhere else under the KB root is reported with a pointer to `put`, which files it under its topic. |
| K9 | Near-duplicate claim paragraphs: token-set Jaccard similarity ≥ `duplicate_similarity` (default 0.9). Tokens are taken from the claim lowercased, with `*`, `_` and backticks removed, as runs of letters and digits joined by `.`, `-`, `/` or `:`. K9 ignores scope: two near-identical claims about different products state one fact that holds for both, which belongs in one finding with both scopes (P6). The issue is reported on the finding being written, else on the newer one. A cheap first pass before Jev. |
| K10 | Every excerpt marked as verbatim occurs exactly in its cited source. Syntax: a fenced block or blockquote on the line directly after `<!-- verbatim: <repo-relative path>:<line>[-<line>] -->` or `<!-- verbatim: <path>:@0x<offset> -->`. With lines, the excerpt, with blockquote `> ` markers stripped and line endings normalised, must occur within the cited lines; with an offset, its bytes (with LF or CRLF line endings) must start at that byte offset. A binary source (one containing a NUL byte or not valid UTF-8) is not checked; its excerpts are checked by `check:` commands. Catches paraphrased "quotes". Not yet built: a tag ending in ` hex` (`<!-- verbatim: path:@0x1F0 hex -->`) quotes bytes instead, as a fenced block of hex byte pairs (whitespace ignored) that must equal the source's bytes at that offset, so an excerpt of a binary source is checked too. |
| K11 | **Reported claims** (§4). A finding labelled `reported_label` has at least one verbatim tag whose source is under a `history_dirs` folder (K10 checks the excerpt itself). A finding with any other label lists no path under a `history_dirs` folder in `evidence` (prose and verbatim excerpts may still cite one), and has no `depends_on` entry naming a reported finding. A history path matches by whole leading path segments, with or without a leading `./`. When `reported_label` is `""` or not in `labels`, only the evidence check applies. |
| K12 | With `[kb] verbatim_blockquotes = true`, every blockquote in a finding's body is a verbatim excerpt: it carries a K10 tag. A quotation that cannot be tagged is paraphrase, and belongs in prose. Off while the key is absent, so a KB set up earlier keeps validating; `kblam init` writes it on for a new project. Not yet built. |

`kblam validate` also fails while `.kblam/review.jsonl` holds an open review or unchecked item
(§6.4). `put` runs the rules, not that check (§7).

### 5.1 Fingerprint

A finding's fingerprint identifies what it asserts. `depends_on` records it (K3), review items
carry it (§6.4), and a finding counts as checked at a fingerprint (§6.5, §8).

**As built (fingerprint v1).** The first 8 hex digits of the sha256 of the canonical JSON (keys
sorted, no spaces, UTF-8) of `{id, claim, scope, quantities, evidence}`: the claim with whitespace
runs collapsed, so reflowing it is not a change, and each list in the order the file gives it, so
reordering one is. The title, `label`, `depends_on`, `anchors` and the body after the claim
paragraph are not covered.

**Fingerprint v2 (not yet built, M6.10).** The first 12 hex digits of the same hash over
`{id, claim, label, scope, quantities, evidence}`, with every list in a canonical order: scope values
split at the scope separator, de-duplicated and sorted; evidence paths with `\` turned to `/`, a
leading `./` and a trailing `/` removed, then sorted; quantities sorted by normalised name, value and
unit. Adding the label makes a demoted (or promoted) finding's dependents suspect, so they are
re-read; canonical order means that reordering a list is not an edit. The longer value tells the two
formats apart: K1 reports an 8-digit stamp as an old-format stamp and names `kblam upgrade`, which
re-stamps every `depends_on` value that is current under v1 with the target's v2 fingerprint and
leaves stale ones stale (§7). Every machine must then run a kblam with v2.

## 6. Jev contradiction and duplicate check (`kblam check`)

### 6.1 Candidate pairs (code, not Jev)

Candidate selection must not depend on any one project's writing habits (user, 2026-09-23: the
earlier design's auto-extracted hex and backtick anchors suited the pilot corpus only). For a new
or changed finding N there are two kinds of candidate:

- **Dependencies**: a `depends_on` edge in either direction. They rank ahead of every other
  candidate (M6.8).
- **Scored** findings: every other finding, scored by similarity to N's title and claim
  paragraph, over the whole KB (every topic). The score comes from a local embedding model when
  one is available, otherwise from BM25 (user, 2026-09-24). Each check uses one mechanism for
  every pair.
  - **Links.** A finding is *linked* to N when they share a declared `anchors` entry (hex anchors
    compare by value, `0x001a2b3c` = `0x1A2B3C`, others case-insensitively with whitespace runs
    collapsed) or an evidence path (equal, or one contains the other by whole path segments). A
    linked finding's score gets a bonus of `link_bonus` (§9, default 0.15) times N's top raw
    score, once however many links it has, under either mechanism (user, 2026-09-24). A linked
    finding is eligible even when its raw score is 0.
  - **Embedding** (preferred): cosine similarity between ollama embeddings of N's title and claim
    paragraph and each finding's. `embedding_model` and `ollama_url` are set in §9; the defaults are
    `embeddinggemma:300m` at `http://127.0.0.1:11434`, the best mechanism in the recall
    measurement (Appendix B.2). `OLLAMA_HOST` is not read. N is embedded with the model's query
    prefix and the other findings with its document prefix (§9, `embedding_query_prefix` and
    `embedding_document_prefix`, which default to embeddinggemma's documented forms). No topic
    bonus applies, and every finding is eligible whatever its score: recall was measured that way,
    and catching paraphrases that share no token is the reason to use embeddings at all.
  - **BM25** (fallback): k1 = 1.2, b = 0.75, with N's title and claim paragraph as the query
    against each finding's title and claim paragraph. Tokens are lowercased runs of letters,
    digits, `_`, `.`, `-` and `/` with trailing punctuation stripped, so `0x1A2B3C`, `MX-100` and
    `foo_bar()` stay whole; a small fixed English stop-word list is dropped; there is no stemming.
    Pure Python, no dependency, deterministic. A finding in N's topic gets a bonus of
    `topic_bonus` (§9, default 0.2) times N's top score, so same-topic findings rank ahead of
    equally similar ones elsewhere. A finding scoring 0 (no shared token) is never a candidate
    unless it is linked.
  - **Choosing.** `embedding_model = ""` selects BM25, silently. Otherwise each command
    (`put`, `check`, `audit`, `validate --record`, the Stop hook) first asks ollama for its model
    list (`GET /api/tags`, 2 s timeout).
    - BM25 is used for the rest of the command in three cases:
      - that request fails;
      - `embedding_model` is not in the list;
      - an embed request fails.
    - Falling back is not an error and does not make a write unchecked. The command prints one line
      to stderr naming the reason (`similarity: BM25 (ollama not reachable at <url>)`).
    - A candidate set is never ranked by a mix of the two methods. If `put`'s under-lock embed
      fails after its pre-lock pass used embeddings, the under-lock ranking is BM25 over every
      finding, and any pair it selects that was not asked before the lock is asked under the lock
      (§7).
    - Candidates therefore depend on the machine: one without ollama, or without the model, ranks
      by BM25, so the same `put` can meet different candidates, and a different verdict, on two
      machines. A project that needs the same candidates everywhere sets `embedding_model = ""`.
  - **Vectors are cached** in `.kblam/embeddings.sqlite`, keyed by model name, the model digest
    from `/api/tags`, role (query or document) and the sha256 of the prefixed text. Only uncached
    texts are sent, in requests of at most 64 inputs (larger ones fail inside ollama). Re-pulling a
    model changes its digest, so its old vectors are never reused. Like the Jev pairs, `put` embeds
    before taking the lock and, under the lock, embeds only findings the tree gained meanwhile. A
    re-run on the same machine and model yields the same candidates.

**Scope gating.** Scope is decided in code first: if the `scope` sets of N and E are disjoint
(e.g. MX-100 vs MX-200), the pair is recorded as `different_scope` and Jev is not asked, whatever
kind of candidate E is. A scope value containing `/` stands for each of its parts
(`MX-100/MX-200` = {MX-100, MX-200}), and `any` overlaps every scope (§4). Jev's documented
weaknesses include literal reading and indirection (typesafe/model-jaggedness_jev-1.13.md), so
scope gating must not be left to it.

**Budget and order.** The candidates are at most `max_candidates` (§9, default 30) findings whose
scopes overlap N's, in rank order: dependencies first, then the scored findings; within each group
by raw score plus topic bonus plus link bonus, and by ID among equal totals. A finding ranked past
the budget, a dependency included, is not asked and is logged as over budget. Pairs with disjoint
scopes don't count against the budget. Links are a bonus, not a priority, because a common evidence
file crowds similarity out of the budget; Appendix B.1 gives the measurement behind the default
bonus.

**Quantities.** The quantity comparison (§6.3) is code, so it covers every finding whose scope
overlaps N's, candidate or not; a finding it conflicts with is added to the check with only the
quantity verdict.

A finding is never its own candidate (an `edit` of F-0088 is not compared with the F-0088 it
replaces). Each check's candidate set, with the reasons each matched, is logged to
`.kblam/checks.jsonl`.

**As built (M6.6–M6.8).**
- *Tokens.* The regex `[\w./-]+` on the lowercased text (`\w` is Unicode); trailing `.`, `-` and
  `/` stripped, leading ones kept (`/usr/lib`, `.5`); apostrophes split words; empty tokens
  dropped. Stop words: Lucene's English set (a an and are as at be but by for if in into is it no
  not of on or such that the their then there these they this to was will with).
- *Documents.* Title plus claim paragraph (the `**Claim.**` marker removed), for every readable
  finding in the KB root; staged files are not in it. Document length is the token count after
  stop words.
- *Score.* IDF = ln(1 + (N − n + 0.5)/(n + 0.5)), always positive, so a score of 0 means no shared
  token. Each distinct query token counts once; sums run in sorted-token order, so equal documents
  score identically. N's own current version is in the statistics during check, audit and Stop,
  but not during a put of a new ID; the version being replaced by an edit is in the statistics but
  never a candidate.
- *Bonuses.* N's top raw score is the maximum over all other findings, every scope, linked ones
  included, before any bonus. `topic_bonus` (≥ 0) times it is added only to same-topic findings
  with a raw score above 0, topics compared as exact strings; `link_bonus` (≥ 0) times it is added
  once to a linked finding. `link_bonus = 0` gives pure score order.
- *Embedding requests.* Standard library only: `urllib` against the configured URL and a
  standard-library dot product of unit vectors. No numpy and no `ollama` package.
- *Log.* Each `checks.jsonl` record has `similarity`: `embed <model>`, `bm25 (<reason>)` after a
  fallback, or `bm25` when `embedding_model` is `""`. Each candidate's `reasons` are strings: its
  links (`depends_on`, `anchor <normalised anchor>`, `evidence <N's evidence path>`), a finding
  with several links appearing once with all of them; then, for every candidate but a dependency,
  its score: `similar <raw>` (with ` (embed)` under embeddings) when no bonus applies, otherwise
  `similar <total> (embed <cosine> + link <bonus>)` or
  `similar <total> (bm25 <raw> + topic <bonus> + link <bonus>)`, zero terms omitted. `over_budget`
  and `different_scope` are ID lists; `different_scope` holds every finding with a disjoint scope
  that would otherwise be eligible (under BM25, the linked or nonzero-score ones).

### 6.2 Questions per pair

The state of a relation question is
`{"existing": {"claim": <E's claim>, "scope": <E's scope>}, "new": {"claim": <N's claim>, "scope": <N's scope>}}`,
each claim paragraph with whitespace runs collapsed and each scope as the frontmatter lists it. The
revision question's state is `{"new": {"claim": …, "scope": …}}` alone. Name the parts in the
instructions with backtick paths (per TypeSafe guidance; exact rules in desk-jevdocs Q5).

**Where the wording lives.** The question text is a project's, not code's: each kblam.toml carries it
in `[jev.prompt.relation]` and `[jev.prompt.revision]` (§9), and it is sent verbatim. Code owns what
the API contract fixes — the relation option keys, the question `type`s (`choice`, `noul`), the state
shapes above and `SHAPE_VERSION` — and validates the config: the relation question has exactly the
keys `instructions` and `criteria`, its criteria are exactly the option keys, each with exactly
`what`, `not_for` and `examples`; the revision question has `instructions` and `criteria` with
exactly `true` and `false`; every text field is a non-empty string and every `examples` a list of
strings. Anything else is an error naming the key. `src/kblam/assets/kblam.toml` holds the full
text of the shipped default; §9 shows it abbreviated.

**Prompt identity.** `prompt_id` is the first 12 hex digits of the sha256 of the canonical JSON of
`{"shape": SHAPE_VERSION, "relation": <relation question>, "revision": <revision question>}`, the
questions as sent: parsed values, so reformatting the TOML does not change it and any edit to a
character of the text does. It is what `[jev.thresholds]` records (§6.4) and what keys the pair
cache (§6.5). `kblam prompt-id` prints the current one.

Not yet built (M6.10): one id per question. `relation_prompt_id` and `revision_prompt_id` are the
same hash over `{"shape": SHAPE_VERSION, "relation": <relation question>}` and
`{"shape": SHAPE_VERSION, "revision": <revision question>}`. `[jev.thresholds]` records both,
each question's answers are cached under its own id, and a mismatch demotes only that question's
verdicts (§6.4), so editing one question's wording leaves the other's calibration and cache
intact. For the shipped default they are `6d79e4e0e409` and `d9a34c823fe3` (the combined id is
`4dda2f781f12`). A `[jev.thresholds]` that still records `prompt_id` is accepted when it equals the
current combined id, which vouches for both questions, and `kblam prompt-id` prints all three.

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

**Wording guidance** for whoever edits `[jev.prompt]` (kblam does not check it): wording must avoid
negation and keep criteria aligned with instructions (the jaggedness page, items 1 and 7). That
applies to the free text of `instructions` and each option's `what`. `not_for` is the field
TypeSafe's criteria schema gives for the boundary with neighbouring options (desk-jev-wording A1),
so it names what an option excludes by design, and the revision question's `false` criterion
describes the Noul's negative case. The shipped default is the wording the calibration first
measured (the v2 wording, §10.6), with one change: its scope example is generic
(`products, versions or components`), where the measured wording named the pilot project's own
scope example. It was recalibrated with that change (§10.6).

**Option wording (desk-jevdocs Q5, and Q9's part 9.8, quoting jev-dsl's README):** "An option that
argues for itself steers the answer; an option that merely describes its condition does not." Write
each option as a condition the state could satisfy, never as a case for picking it. "A choice's
distribution is uncertainty about which single alternative fits, not evidence that several apply":
`probabilities["cannot_both_be_true"] = 0.4` never means "40% conflicting".

**Request shape (desk-jevdocs Q6):** one request per pair. TypeSafe's fan-out advice multiplies
*questions* against one shared state, not states; both published pairwise precedents send one
request per pair (entity alignment: "One request goes out per pair", 450 pairs, 6-worker pool); and
putting ~30 unrelated candidates in one state is the documented "large state full of irrelevant
detail" failure mode. The `revision` Noul concerns `new` alone, so it is its own request with state
= `new`. Budgets: 64k tokens per request, 32k for state plus the longest question
(typesafe/models.md). Run pairs in parallel with a small worker pool (`workers`, §9), within the
1,200 requests/minute limit.

**Reading the answers (desk-jevdocs Q3; the jaggedness page):**
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
(the jaggedness page). For every finding E whose scope overlaps N's, candidate or not (§6.1), when
N and E both carry a quantity with the same name (compared case-insensitively with whitespace runs
collapsed), code compares them and reports `quantity_conflict` itself if the units differ (compared
exactly, after collapsing whitespace) or the values differ by more than `quantity_rel_tolerance`
(§9, default 0): they conflict unless |a − b| ≤ tolerance × max(|a|, |b|), and at 0 any difference
conflicts (one named quantity has one current value, P6). A `quantity_conflict` rejects the write:
"F-0102 gives <name> = <value> and this finding gives <value>; rewrite F-0102 in place, correct
this finding, or rename the quantity if they measure different things". Jev is never asked whether
two numbers agree; Jev is still asked about the pair's claims.

Two findings that measure different things under one name rename a quantity; a resolution does not
cover a quantity conflict. Not yet built (M6.10): `kblam resolve` refuses an item that holds a
`quantity_conflict`, and no resolution suppresses one. As built, a `--distinct` resolution of a
pair suppresses every verdict on it at those fingerprints, a quantity conflict included; since the
fingerprint covers the quantities, that lasts only until either side's quantities change.

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
- **Rejected items** (user decision delegated to the coordinator, 2026-09-24). Jev can reject a
  pair of distinct facts, most often "the interface supports X" against "this program does not
  use X", and can read a direct statement as a correction. Rewording to get past a reject is
  forbidden (the skill), so a rejected put records each `same_fact`, `cannot_both_be_true` or
  `revision` verdict that rejected it as a **rejected item** in `.kblam/review.jsonl`, and prints
  each item's ID with the reject message. `quantity_conflict` is code, not Jev, and records
  nothing.
  - A rejected item does not fail `validate`: its finding is not in the tree. It closes when a put
    of that finding ID succeeds, or when the staged finding's fingerprint no longer matches (the
    next put of a changed staged file re-checks the pair).
  - The author never resolves its own rejected item: it sends the ID to the adjudicator (§8.1) and
    carries on. The adjudicator resolves it (below) only when Jev misread it; a put of the
    unchanged staged finding then reports that verdict as "resolved as distinct, not raised" and
    goes through.
  - Not yet built (M6.10): the shell hook denies `kblam resolve` to an agent that is not an
    adjudicator (§8 item 2), and `kblam items --reworded` lists each rejected item whose finding
    later went in at a different fingerprint while the other side of the pair stayed as it was.
    Each is either the correction the reject asked for or rewording to pass, and the adjudicator
    reads which (§7).
- **Review:** the write is accepted, and a review item (both IDs and fingerprints, the verdict, p,
  confidence) is recorded in `.kblam/review.jsonl` and printed. `kblam validate` fails while any
  item is open. An item closes automatically when either finding's fingerprint changes: the `put`
  of the edited finding re-checks the pair and raises a new item if a verdict still fires. The
  adjudicator closes one by editing or merging findings (§8.1), with `kblam rm` when a merge
  leaves a finding nothing to state (§7; not yet built), or by resolving it.
- **Item IDs.** A review or rejected item's ID is `R-` and the first 8 hex digits of the sha256 of
  the JSON list [verdict, existing ID, existing fingerprint, new ID, new fingerprint], with nulls
  for the missing side of a `revision` item, so raising the same item again doesn't duplicate it.
  An unchecked item's ID is `U-` and the same hash of [finding ID, fingerprint].
- **Resolutions.** `kblam resolve <item> --distinct "<reason>"` closes a review or rejected item
  that Jev misread, closes every other open review or rejected item on the same sides, and records
  a resolution, so those sides are not raised again. For an item on a pair it records that the two
  findings state distinct facts, and the reason names what differs (component, operation,
  condition, model or quantity) for a later reader. For a `revision` item it records that the
  finding states a fact directly rather than correcting an earlier claim, and the reason says why.
  `resolve` refuses an unchecked item.
  - As built, a resolution is a row of `.kblam/pairs.sqlite`: the unordered pair of (ID,
    fingerprint) sides, with an empty second side for a `revision` item, and the reason. It holds at
    any model and prompt, but only on the machine that recorded it and only until either side's
    fingerprint changes. The messages of a `revision` item still speak of "two findings" that
    "state distinct facts"; M6.10 corrects them.
  - Not yet built (M6.10): `resolve` appends each resolution to `kblam.resolutions.jsonl` at the
    repository root, a committed, append-only file (one JSON object per line: the sides as (ID,
    state hash) pairs, the kind `distinct` or `not_revision`, the reason and the date). Git merges
    it with the `merge=union` driver, and only kblam writes it (the §8 hooks deny other writes).
    Its sides are state hashes (§6.5), so a resolution reaches every clone and survives edits Jev
    does not see (evidence, quantities, label), and it lapses when the claim or scope changes.
    `kblam upgrade` moves existing resolutions into it. `resolve` refuses an item that holds a
    `quantity_conflict` (§6.3).
- **Unchecked** items are per finding: some Jev question for it got no answer (§6.5). They close
  when `kblam check --pending` gets an answer to every question for that finding, or when its
  fingerprint changes.
- `kblam check` and `kblam audit` run on findings already in the tree, so a reject verdict they
  find is recorded as a review item.
- `.kblam/review.jsonl` holds one JSON object per item, open or closed; a closed item keeps the
  time and the reason it closed. kblam rewrites the file whole, under the lock (§7).
- There is no separate review band below the reject verdicts: at each, the best calibration
  point with precision ≥ 0.5 was the reject point itself (§10.6).
- **`low_confidence`** comes from a post-hoc look at the calibration pairs, not from a
  pre-registered held-out result (Appendix B.3). Its cut is `low_confidence_review` in
  `[jev.thresholds]`, and absent means off. The cut is 0.3 since 2026-09-24 (coordinator, on the
  user's delegation of this question), after the first cut, 0.49 (user, 2026-09-22), raised 425
  review items in the calibration-topic migration and none of them led to a merge. Field
  monitoring (§10.7) decides whether it stays.
- **Numeric paraphrase false reject (known):** "removes about a quarter of the difference" vs
  "leaves about three quarters of it" fired `cannot_both_be_true` at p 0.94 in both runs (TOP-008).
  The user decided (2026-09-22) that contradictions block the write anyway; the author rewrites
  one finding's wording (or merges the two, since they state one fact).
- **Model or prompt mismatch:** if the served model ID of any answer a check used (fresh or cached)
  or the prompt id of the wording in `[jev.prompt]` (§6.2) differs from the one recorded in
  `[jev.thresholds]`, no Jev verdict of that check rejects: every one that fires becomes a review
  item, and kblam warns, naming both ids, that re-calibration (§10) is needed. `quantity_conflict`
  still rejects, because it doesn't involve Jev. With one prompt id per question (§6.2; not yet
  built), a prompt mismatch demotes only the verdicts of the question whose wording changed.
- **Only enabled verdicts are asked.** A verdict with no entry in `[jev.thresholds]` is disabled.
  The relation question is asked only if a relation verdict or `low_confidence_review` is set;
  the revision question only if `revision` is. A KB with no `[jev.thresholds]` sends nothing to
  Jev and runs only §6.3 (the CLI says so on stderr).
- **Direction.** The finding being written or checked is always `new`, so editing an older finding
  asks the reverse of the direction calibration measured (calibration's extending claim was always
  `new`). In the M5 smoke, editing F-0001 raised "F-0001 restates F-0004": when the finding being
  edited is the one that already holds the extra detail, `restates_and_extends` can fire the other
  way round, as a review item whose message points the wrong way. `put` and `check` look up
  answers in their own direction only; `audit` asks a pair that has an answer in neither direction,
  with the higher ID as `new` (§6.5), so a pair `audit` answered may be asked again, reversed, by a
  later `check`.

### 6.5 Cache, cost and failure handling

- **Cache key.** As built, answers in `.kblam/pairs.sqlite` are keyed by
  (`[jev] expected_served_model`, prompt id, kind, fingerprint(E) or "" for a revision question,
  fingerprint(N)), and a row is used only when the served model it recorded equals
  `expected_served_model`. A pair is re-asked only when one side changes, the prompt changes (any
  edit to `[jev.prompt]` changes its id), or the expected model changes.
  - Not yet built (M6.10): the key uses each side's **state hash**, the first 12 hex digits of the
    sha256 of the canonical JSON of that side's state exactly as sent (`{"claim": …, "scope": …}`,
    §6.2), and the question's own prompt id. An edit Jev does not see (evidence, quantities, label,
    title) then re-asks nothing. A finding still counts as checked at its fingerprint, not its state
    hash, because an evidence change can change its links and so its candidates (§6.1).
- **Checked marks.** `pairs.sqlite` also records each (finding, fingerprint) whose check got an
  answer to every question. `check` with no IDs, `validate --record` and the Stop hook check the
  findings that have no such mark (§7, §8).
- A cache written by an older kblam, keyed by an integer prompt version, is reported rather than
  dropped (§9, "Upgrading").
- `kblam audit` asks each candidate pair that has no cached answer in either direction (lower ID
  as `existing`) and the revision question for each finding without one, so after a baseline it
  only asks what changed.
- **Cost** is $0.042 per million input tokens; output tokens are reported but not charged (both
  observed in the M3 smoke, 2026-09-22). Measured with the draft wording on one-sentence claims: a
  relation pair is 771 input tokens ($0.000032), a revision question 390 ($0.000016); most of the
  relation count is the fixed question text. So one write with 30 candidates ≈ 24k tokens ≈
  $0.001, and a one-time baseline over ~4,500 candidate pairs ≈ 3.5M tokens ≈ $0.15 (longer claims
  raise both); after that, about $1 a month (the cost of about a thousand such writes). Log actual
  `usage.cost` from each response to `calls.jsonl`; `kblam cost` summarises it.
- **Model drift.** kblam requests `[jev] model`, `typesafe/jev-1.13`: a version, not the moving
  `jev-latest` alias. Each response names the dated snapshot that served it
  (`typesafe/jev-1.13-20260917`). TypeSafe advises pinning the version thresholds were tuned on
  (desk-jevdocs Q7), but whether OpenRouter accepts a dated snapshot in the request is not
  documented, so kblam detects a new snapshot rather than preventing one. Two settings name the
  expected snapshot. `[jev] expected_served_model` decides which answers the cache stores and
  reuses: an answer from another snapshot serves the check that asked for it, is never cached, and
  `kblam cost` counts it. `[jev.thresholds] served_model` decides whether the thresholds apply
  (§6.4). Both name the snapshot the thresholds were measured on, and both change together after a
  recalibration (§10). Every cache row records the snapshot that served it.
- **Network or API failure is never a silent pass.** A Jev question that gets no answer (the request
  failed after the client's retries, or no API key could be read) leaves the finding unchecked:
  `put` still moves it in and exits 0, printing the unchecked item (§6.4), and the next
  `kblam validate` fails until `kblam check --pending` gets every answer. A question that failed
  before `put` took the lock is not retried under it, so an outage never holds the lock through
  retries.
- **API key:** read as §9 describes. Never log, print or commit it.

## 7. Commands

Every command but `init` takes `--root <dir>` to name the repository root (§4).

| Command | Purpose |
|---|---|
| `kblam new <topic> "<title>"` | allocate the next ID (below), write a skeleton to `.kblam/staging/F-NNNN-<slug>.md`, print the path |
| `kblam edit <id>` | copy an existing finding to staging for rewriting in place (P1), record its edit base (below) and print the path; refused while a copy of that ID is staged |
| `kblam put <file>` | **the only way into `findings/`**: validate the KB as it would be after the move + Jev check + move into place + regenerate the index and `tree.hash` (below). A staged file with an existing ID replaces that finding in place (the old file is removed if the slug or topic changed). Any refusal leaves `findings/` unchanged. |
| `kblam validate` | all deterministic rules (§5) plus open review and unchecked items (open rejected items do not count); no network; non-zero exit on failure |
| `kblam validate --record` | check (as `kblam check` with no IDs, so this form may ask Jev) the findings not yet checked at their current fingerprint, then, on a clean result, write `tree.hash` for the current tree: the explicit way to accept a legitimate out-of-band change such as `git pull` or `git checkout`. With no `tree.hash` yet, as on a fresh clone, it asks Jev nothing: it validates, records the tree, and marks every finding as accepted from the repository (§8 item 3; not yet built) |
| `kblam validate --commit` | as `kblam validate`, for the commit being made, with the checks of §8 item 4; what the pre-commit hook runs |
| `kblam approve-config` | show how `kblam.toml` differs from the last commit (from the template `kblam init` writes when no commit holds it), check it loads, and on an interactive terminal ask a person to approve it for commits on this machine; with no terminal, approve nothing and exit 1 |
| `kblam check [F-…]` | §6 check of the named findings, or of every finding with no complete check at its current fingerprint; writes nothing under `findings/`; what fires becomes review items |
| `kblam check --pending` | re-run the check of each finding with an open unchecked item |
| `kblam audit` | ask every candidate pair and revision question with no cached answer (§6.5); what fires becomes review items |
| `kblam index` | regenerate `findings/INDEX.md` from frontmatter and write `tree.hash` (by the tree.hash rule, §8). Deterministic: a fixed header line, topics in sorted order, one table row per finding (ID link, title, label, scope) in ID order, no timestamps. |
| `kblam ack <dependent> <target>` | record the target's current fingerprint in the dependent's `depends_on` after re-reading the target (K3). Edits only that value (round-trip YAML), then rewrites `tree.hash` by the tree.hash rule. `--all <target>` is deliberately absent: each dependent is re-read and acked on its own. Not yet built (M6.10): `ack` first prints the target's claim as it stood at the recorded fingerprint (the version in git history that has it) beside its current claim, so the re-reading has something to read. |
| `kblam deps <id>` | list the finding's dependents and dependencies, with suspect ones marked |
| `kblam resolve <item-id> --distinct "<reason>"` | close a review or rejected item that Jev misread, and record the resolution (§6.4) |
| `kblam rm <id> --merged-into <target>` | (not yet built) remove a finding after a merge moved everything it stated into `<target>` (§8.1). Refused while another finding depends on it (edit each dependent to depend on `<target>` first), while `<target>` is not in the KB, or while one of its quantities is missing from `<target>` with the same value and unit. Under the lock it removes the file and a topic folder left empty, regenerates `INDEX.md`, applies the tree.hash rule and closes the finding's open items; the reason for the removal goes in the commit message. An adjudicator's command (§8 item 2). |
| `kblam renumber <path>` | (not yet built) give a new ID to one of two findings that share an ID, which K1 reports after the work of two clones is merged: rewrite that file's `id` and filename, re-key each `depends_on` entry whose recorded fingerprint shows it means that finding, and list the other mentions of the old ID for a person to check |
| `kblam items [--reworded] [--stats]` | (not yet built) list the open review, rejected and unchecked items. `--reworded` lists each rejected item whose finding later went in at a different fingerprint while the other side of the pair stayed as it was: a correction or rewording to pass, for the adjudicator to tell apart (§6.4); `put` records the fingerprint that went in when it closes a rejected item. `--stats` counts, per verdict, the items closed as distinct and those closed otherwise (§10.7) |
| `kblam recheck [F-…]` | run the `check:` commands (§4) of the named findings, in the order given, or of every finding, in ID order. At an interactive terminal it first shows a person each command that is new, changed, or whose named files changed since its approval, and asks; without one it runs only the approved commands and reports the others as not approved ("`kblam recheck`" below) |
| `kblam recheck --list` | print each `check:` command with its approval state on this machine; run nothing |
| `kblam upgrade` | (not yet built) migrate a KB and this machine's state to the formats M6.10 introduces, under the lock: re-stamp `depends_on` with v2 fingerprints (§5.1), move resolutions from `.kblam/pairs.sqlite` into `kblam.resolutions.jsonl` (§6.4), re-key the cached answers of current findings by state hash (§6.5), and print the per-question prompt ids for a person to record in `[jev.thresholds]` (it never edits `kblam.toml`). It applies the tree.hash rule to its writes |
| `kblam calibrate <pairs.jsonl>` | (not yet built) run the §10 procedure on a labelled set: every pair twice, the flip rate and the answers' resolution, thresholds chosen on the calibration half by the §10.3 rule, held-out precision and recall with counts and exact 95% intervals, and a proposed `[jev.thresholds]` for a person to copy (it never edits `kblam.toml`) |
| `kblam cost` | spend summary from `calls.jsonl` |
| `kblam prompt-id` | print the id of this project's Jev prompt (its `[jev.prompt]` tables): what `[jev.thresholds]` records and calibration is tied to (§6.2, §9); with M6.10, the per-question ids too |
| `kblam jev-smoke` | ask Jev one synthetic relation pair and one revision question and report the answers, the served model and the cost: a live check of the key, the endpoint and the model. Exit 1 unless the pair comes back `same_fact` and the noul is at least 0.5 |
| `kblam hook <event>` | entry point for Claude Code hooks (§8) |
| `kblam init [--update]` | set up the current git repository for kblam (§7.1); `--update` rewrites the files kblam owns to the installed version |
| `kblam migrate …` | planned, not specified and not built: helpers for splitting an existing document into findings (§11 step 3, §13) |

Exit status: 0 success, including a `put` whose Jev questions went unanswered (the finding is in,
with an open unchecked item); 1 refused (validation errors, open review or unchecked items, a
request kblam will not carry out, Jev unavailable to `jev-smoke`, files under `.kblam/` that git
tracks (§8); `check` and `audit` also exit 1 when the run left an item open, which an unavailable
Jev does, and `recheck` when a check failed, could not run or was not approved, or a finding could
not be read); 2 no usable `kblam.toml` or bad
arguments; 3 timed out waiting for `.kblam/lock`; 4 `put` rejected by the Jev check or a quantity
conflict (`findings/` unchanged).

**`new`.** The next ID is one above the highest ID in the KB root and in staging. Not yet built
(M6.10): it is also above every ID in the git history of the KB root on any local ref,
remote-tracking ones included, so an ID that was ever committed, including one `kblam rm`
removed, is never issued again. Two clones that allocate before they exchange commits can still
pick the same ID; K1 reports the duplicate after the merge, and `kblam renumber` settles it. The
slug is the title folded to ASCII and lowercased, each run of other characters turned into one
`-`, trimmed of `-` at both ends and cut at a `-` to at most 60 characters (`finding` if nothing is
left). The skeleton holds every required key, today's date in `verified`, and the `**Claim.**`
marker.

**`put`** (M2, M5).
- It takes a finding file from anywhere outside the KB root, normally a staged one, and refuses
  (exit 1, nothing written) when the path is not a file, when it is under the KB root, when its name
  is not `F-NNNN-<slug>.md`, when its frontmatter does not parse or its `topic` is not a folder
  name, and when its ID is already in the KB root and the edit-base guard (below) fails. These are
  the "bad-file refusals" of §8 item 6.
- **Stamping.** A staged finding may list `depends_on: {F-0102: null}`; `put` stamps each null with
  the target's current fingerprint (the author asserts they read it) and reports what it stamped.
  A non-null fingerprint that is stale blocks the put (K3 on the incoming finding).
- **Validation.** `put` runs every rule of §5 on the tree as it would be after the move. K3 on
  *other* findings does not block it: a put that changes a finding's fingerprint reports the
  dependents it made suspect, and `validate` (and so pre-commit and the Stop hook) fails until each
  is acked or edited. This keeps one rewrite from freezing every unrelated write. For the same
  reason, open review and unchecked items never block a put, so the merge that closes an item can
  always go in. As built, any other error anywhere in the tree blocks the put, so one broken finding
  blocks every writer, and two broken findings cannot be fixed one put at a time, since each put
  still sees the other's error. Not yet built (M6.10): only errors in the incoming finding, and
  errors the move introduces in other findings (present after the move and absent before it), block
  a put; errors that were already there are printed as warnings.
- **The Jev check** (M5) runs once validation passes. `put` asks Jev *before* taking the lock (so
  slow network calls don't hold other writers), then under the lock recomputes the candidates
  against the current tree and decides. Pairs already asked are answered from memory or the cache:
  normally only pairs the tree gained meanwhile are asked under the lock, and after an embedding
  failure under the lock, every pair of the BM25 ranking that was not asked before it (§6.1). A
  reject leaves `findings/` unchanged and exits 4; a question with no answer makes the write
  unchecked (§6.5).
- **The move.** `put` writes the finding to `<KB root>/<topic>/<name>`, removes the old file when
  the slug or topic changed (and a topic folder that leaves empty), regenerates `INDEX.md`, applies
  the tree.hash rule (§8), deletes the edit-base record and, when the file was under
  `.kblam/staging/`, the staged file, records review and unchecked items, and closes the finding's
  open rejected items.

**Edit-base guard** (M2). `kblam edit` records the sha256 of the finding's file bytes at copy time,
in `.kblam/staging/<id>.edit-base.json`. A `put` whose ID already exists in `findings/` requires
that record and refuses if the current file no longer matches it ("F-x changed since your edit;
run `kblam edit F-x` again and reapply your change"). A hand-named file therefore cannot overwrite
an unrelated finding, and two agents editing one finding cannot lose an update. An `ack` changes
the dependent's bytes, so an edit of it begun before the ack is refused at put the same way.

**Lock** (M2). `new`, `edit`, `put`, `ack`, `index`, `resolve`, `validate --record` and the
recording step of `check` and `audit` hold an exclusive `.kblam/lock` for their
read-validate-write span, so concurrent puts cannot both validate against the same old tree,
concurrent `new` calls cannot allocate the same ID, and concurrent writers of
`.kblam/review.jsonl`, which kblam rewrites whole, cannot lose an update. Waiting is bounded
(`lock_wait_seconds`; then exit 3). The lock file records the holder's pid, command and start time.
As built, a lock is broken, with a message, when its holder's process is not running or when it is
older than `lock_stale_seconds`, even if its holder is still at work; `.kblam/lock.break` ensures
that only one waiter breaks it. Not yet built (M6.10): the holder refreshes the lock file's
modification time at least every `lock_stale_seconds`/3, and a lock is broken only when its
holder's process is not running or its last refresh is older than `lock_stale_seconds` (which also
covers a pid reused by another process), so a live holder is never broken.

**`kblam recheck`** (user, 2026-09-26). A `check:` string is written by an agent, reaches every
clone through `git pull` from anyone who can push, and is read by neither the K rules nor Jev, and
an agent may be allowed to run `kblam` without asking (a `Bash(kblam:*)` permission, say). If
`recheck` ran whatever `check:` says, a command someone put into a finding would run without anyone
having seen it. So a check runs only in `kblam recheck`, never from a hook, `validate`, `put`,
`check` or `audit`, and only once a person has approved that exact command on the machine. The
threat is a command a third party puts into the knowledge base, not an agent on this machine set on
running its own code (§8.3).
- *Command form.* The string is split into arguments by POSIX shell rules on every platform and run
  without a shell, so the person approves exactly the argv that runs. `;`, `&&`, `|`, `$(…)`,
  backticks, redirection, globs, `~` and `$VAR` have no effect, `#` is an ordinary character, and
  `\` escapes the next character (so paths are written with `/`). A bare program name is looked up
  only in PATH's absolute entries, never in the current directory; a name with a directory part is
  relative to the repository root. On Windows a name without a PATHEXT extension gets each in turn,
  and a batch file (`.bat`, `.cmd`) is refused, because Windows runs it through cmd.exe, which
  re-parses its arguments.
- *Approval.* An approval covers the finding ID, the sha256 of the `check:` string exactly as
  written, and the sha256 of every regular file inside the repository that the command names: an
  argument, the value of an `--option=value` argument, or the program when it is given as a path.
  So a changed command or a changed script needs a person again, and the reason given names what
  changed. Code the command reaches without naming it (a module its script imports, the project
  that `uv run` syncs) is not pinned. Approvals are JSON lines (ID, digests, time) in the
  repository's git directory, `.git/kblam/recheck-approved.jsonl`, shared by its linked work trees:
  a pull writes tracked files over ignored ones, so approvals under `.kblam/` could come from any
  commit, while git refuses every path with a `.git` component. The §8 hooks deny agents writes
  there, and outside a git work tree `recheck` runs nothing. Old approvals are kept, so a command
  changed back needs none. A line that cannot be read, or a link in the file's place, refuses the
  run.
- *Asking.* A person is asked only when stdin and stdout are both an interactive terminal. For each
  command that is new, changed, or whose named files changed, `recheck` shows the finding ID and the
  reason; the string, escaped when it holds anything other than printable ASCII, so that no control
  character, lookalike letter or direction mark can hide what it says; the argv as JSON; the program
  found; the pinned files; and the directory, the variable it runs without and the timeout. It asks
  `[y/N]` for each before running any, and records each `y`. With no terminal it asks nothing: the
  approved commands run, and every other one is reported as not approved, with its reason and a
  message that a person runs `kblam recheck <id>` at a terminal and that an agent asks the user to,
  never running the command itself. As with `approve-config`, a wrapper that supplies a terminal,
  such as `script`, gets past this.
- *Running.* Just before a check runs, its named files are hashed again; a change since its approval
  (an earlier check in the same run may have made it) means it is not run. It runs from the
  repository root with stdin closed, in a process group of its own (a new session on POSIX), with
  kblam's environment minus the variable the Jev API key is read from (`key_env`, §9). A check
  passes when it exits 0. It fails on any other exit or a signal, and on running past `[kb]
  recheck_timeout_seconds` (§9, default 600), when it and every process it started are killed
  (`killpg`, or `taskkill /T` by full path on Windows). A string that cannot be split, or a program
  that is not found, is reported without asking and counts as a failure, as does a program that
  cannot be started.
- *Output.* A line as each approved check starts, and one with its result. For a failure, the last
  20 lines of output follow, with control characters escaped, then what to do. Last comes a summary
  line, which ends with the skill pointer when the exit status is 1. The exit status is 0 when every
  selected check passed, or when no finding has a check, and 1 otherwise, including when a finding
  cannot be read or its `check:` is not a string; with no IDs given, such a finding is reported and
  the rest still run. An ID that is malformed, not in the KB, unreadable, or without a `check:`
  refuses the whole run before anything runs. `--list` runs nothing: it prints each command with its
  state (approved; not approved, and why; or cannot run, and why) and exits 0.
- *Logs.* `.kblam/recheck.jsonl` gets one line for each check a run considered: time, ID,
  fingerprint, command sha256, the pinned files with their digests, outcome (`passed`, `failed`,
  `timed_out`, `not_started`, `not_approved` or `declined`), exit code, seconds, and whether a
  terminal was present. It never holds the command text or its output. `.kblam/recheck/F-NNNN.log`
  holds the combined stdout and stderr of that finding's last run (empty when it could not start).
  It is written to a new file that then replaces it, so a link at that name is replaced, never
  written through, and a link at `.kblam/recheck.jsonl` or `.kblam/recheck/` refuses the run.

### 7.1 `kblam init`

Packaging: one install per machine, one command per project (user, 2026-09-23). No Claude Code
plugin: plugins can't ship rules, and each machine would still need an install step
(desk-hooks P1, P3).

- **Per machine:** `uv tool install git+<kblam repo URL>` puts `kblam` on `PATH`, and the
  OpenRouter key is saved at `~/kblam/jev!.txt` (§9). Nothing else. (Until a published release,
  `uv tool install <path to a kblam checkout>`.)
- **Per project:** `kblam init`, run in a git repository, writes everything the project needs.
  The user commits it, and a clone on another machine carries it along. `init` never commits,
  never touches anything outside the repository root, and prints each file it wrote, changed or
  left alone:
  - `kblam.toml` from the §9 template, only if absent (never overwritten, even by `--update`);
  - `<kb root>/INDEX.md` via `kblam index`, only if the KB root has none;
  - `.gitattributes`: the line `<kb root>/** -text` (from the configured root), appended if absent,
    because K7 and `tree.hash` are byte-exact and `core.autocrlf=true` would otherwise check files
    out with CRLF; and (not yet built) `kblam.resolutions.jsonl merge=union` (§6.4);
  - `.gitignore`: the line `.kblam/`, appended if absent;
  - `.claude/rules/kblam-findings.md` and `.claude/skills/kblam-write/SKILL.md`;
  - `.claude/settings.json`: kblam's hook entries merged in (below). Other keys and other hooks
    are kept; kblam's entries are recognised by a command starting with `kblam hook` and are
    replaced, never duplicated. An unparseable settings file is an error, and nothing is
    written;
  - `CLAUDE.md`: the §8.2 line appended if absent (the file is created if missing); a line that
    starts with it counts as present, since a project may extend the sentence;
  - the git pre-commit hook at `git rev-parse --git-path hooks/pre-commit` (so `core.hooksPath` is
    honoured), made executable. A different pre-commit already there is not overwritten: `init`
    says so, and exits 1 after writing everything else.

  With `--update`, the rule, the skill, the hook entries and a kblam pre-commit hook are rewritten
  to the installed version; otherwise an existing one of those files that differs from the
  installed version is reported, not overwritten. Finally `init` checks that `kblam` resolves on
  `PATH` and runs each hook entry's command once, through the shell, with a synthetic input (a
  Write outside the KB root, and a Stop), reporting whether each answered.
- **The source files** ship inside the package as `src/kblam/assets/` (the rule, the skill, the
  hook entries, the pre-commit script and the `kblam.toml` template), so an installed kblam has
  them. `.gitattributes` is generated from the configured root. `init` writes files with LF
  endings. The rule (its `paths` filter and text), the skill and the hook entries write the KB root
  as the token `{{kb_root}}`; `init` replaces it with the configured `[kb] root` before writing or
  comparing, so a KB folder with another name gets a rule that loads and text that names it.
- **Hook entries.** Four handler groups: PreToolUse with matcher `Write|Edit|NotebookEdit`,
  PreToolUse with matcher `Bash|PowerShell`, and Stop and SubagentStop with no matcher. Every
  handler is shell form, calling `kblam` by name:
  `{"type": "command", "command": "kblam hook <Event> || echo '{\"systemMessage\": \"kblam hook <Event> did not run (is kblam installed?); findings/ is unguarded\"}'", "timeout": N}`.
  On Windows, hook commands run in Git Bash (desk-hooks H9); the command is also valid
  PowerShell 7. A command that isn't found exits 127, which Claude Code ignores silently
  (desk-hooks H7); the `||` branch turns that into a message the user sees. (`kblam hook` itself
  always exits 0, §8.) `timeout` is in seconds (desk-hooks H8): 30 for PreToolUse, 300 for Stop and
  SubagentStop, whose check can ask Jev and wait for the lock. A timed-out hook fails open. The git
  pre-commit hook (§8 item 4) is the backstop in every case.

**As built.**
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
- *kblam.toml* comes from `src/kblam/assets/kblam.toml`: the §9 values with the generic `[kb]`
  vocabularies. An existing one is reported `unchanged` if it equals the template, else `kept`.
  INDEX.md is created by `kblam index` only if absent, which also writes `tree.hash`.
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
- *`{{kb_root}}`* is replaced as text in the rule and the skill, and inside the JSON string
  values of the hook entries after parsing.

## 8. Enforcement points (Claude Code and git)

Research agents write code and scratch files freely; nothing below touches paths outside `findings/`,
`.kblam/`, `kblam.toml`, `kblam.resolutions.jsonl` and `.git/kblam/` (§7, `kblam recheck`), except
the pre-commit hook's evidence checks (item 4).

**`.kblam/` is kblam's state** (tree.hash, review.jsonl, the verdict cache, the lock). A hand write
there could silence the Stop hook or close a review item, so items 1 and 2 treat a path under
`.kblam/` like one under `findings/`, with two differences: `.kblam/staging/` is exempt (staged
findings are the author's to edit), and removal is denied too (`rm`, `rmdir`, the PowerShell
removal commands, and the *source* of `mv`/`Move-Item`), since deleting `review.jsonl` would close
every item. The deny reason reads "kblam: <what> under .kblam/ denied. .kblam/ holds kblam's own
state and only kblam writes it; stage findings under .kblam/staging/ (kblam new, kblam edit)." plus
the skill pointer. A stale lock is broken by kblam itself (§7). Not yet built (M6.10): the committed
`kblam.resolutions.jsonl` (§6.4) is kblam's state too, and items 1 and 2 protect it the same way.
`.git/kblam/`, where `kblam recheck` keeps what a person approved (§7), is protected as well: items
1 and 2 deny writes and removals there, with the reason that only `kblam recheck` writes it, after
showing each command to a person at a terminal.

**Committed state (user, 2026-09-26).** `.kblam/` is never committed. A pull writes tracked files
over ignored ones, so a commit holding files there would replace every clone's `tree.hash`, review
items and cached answers with its own. While git tracks anything under `.kblam/` (the index lists
it, so a staged file counts, and so does a link at `.kblam` itself), every command but `init` and
`hook` refuses with exit 1, naming the files and the fix: `git rm -r --cached .kblam` and a commit,
and, when the files came with a pull, deleting `.kblam/`, then `kblam validate --record` (which
accepts the committed findings as on a new clone, item 3) and `kblam audit`. The pre-commit hook's
`validate --commit` refuses such a commit the same way. The Stop hook trusts none of that state
(item 3).

**As built.** `.kblam` itself counts (so `rm -rf .kblam` is denied), and so does `.kblam/staging`
itself for the exemption. Bash removals: every operand of `rm` and `rmdir`, and the `mv` sources
(every operand but the last, or all of them with `-t` / `--target-directory=`). PowerShell
removals: `Remove-Item`, `rm`, `del`, `ri`, `erase`, `rd`, `rmdir` (every positional argument plus
`-Path`/`-LiteralPath`, split on commas), and the `Move-Item` source. Not caught: `unlink`,
`find -delete`, `git rm`/`git clean`, `truncate`, a `cd` before a relative path, and globs. A
command that hits both roots gets one deny with both sentences ("writing …" and/or "removing …")
and one skill pointer.

**`kblam.toml` is the project's.** It sets the rules kblam enforces and, within the limits of §9,
where requests go, so an agent that could change it could weaken every check or redirect the key.
Items 1 and 2 deny writing it and removing it (the repository's own `kblam.toml`, not a file of that
name elsewhere), in the same way as `.kblam/`, with the reason "kblam: <what> denied. kblam.toml sets
the rules kblam enforces and where kblam sends the Jev API key, so only a person changes it; ask the
user to make the change you need." plus the skill pointer. A person edits it by hand and approves
the change before committing it (item 4) (user, 2026-09-25). §8.3 says whom each of these
protections guards against.

**The tree.hash rule.** `tree.hash` means "findings/ as kblam last wrote it". `put`, `ack` and
`index` compare the tree's digest with `tree.hash` *before* their write (under the lock). If they
match, or no `tree.hash` exists yet (bootstrap), they record the new digest after the write. If they
differ, something changed `findings/` outside kblam: they still do their own write, but leave
`tree.hash` stale and warn, so the Stop hook still validates the out-of-band change. Otherwise a
shell write followed by `kblam index` or `kblam ack` would silence the Stop hook without anything
being validated. Only `kblam validate --record` accepts an out-of-band change. Not yet built
(M6.10): a bootstrap records the tree only when the tree before the write passes the deterministic
rules, and then also marks every finding in it as accepted from the repository (item 3, "A new
clone").

**Hook input and output.** `kblam hook <event>` reads Claude Code's hook JSON on stdin and always
exits 0; the decision travels only in the JSON on stdout (desk-hooks H4–H6).
- Allow is silence: no output, so Claude Code's normal permission flow still applies. The hook
  never answers "allow".
- Deny (PreToolUse): `{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "deny", "permissionDecisionReason": "<text>"}}`.
- Block (Stop, SubagentStop): `{"decision": "block", "reason": "<text>"}`.
- **No `kblam.toml`: silent.** The hooks may be configured where no knowledge base exists (a
  parent directory, a copied settings file); there they print nothing and exit 0. Only the upward
  search finding no `kblam.toml` is silent: an explicit `--root` without one, an unreadable file,
  invalid TOML or an invalid value gets the note below.
- **Fail open.** A hook never stops an agent's unrelated work. It allows the action and prints
  `{"systemMessage": "kblam hook <event>: <why>; allowed"}`, which the user sees and the model
  does not, when `kblam.toml` is unreadable or invalid, the input is not a JSON object, `tool_input`
  is missing or not an object, the path or command is not a string, the event is unknown, or
  anything raises (`unexpected <Type>: <message>`). Malformed input and an unknown event are
  checked before the config and always get the note.
- **Root.** `--root` if given, else `$CLAUDE_PROJECT_DIR`, else the input's `cwd`, else the
  process's working directory; from there kblam walks up to `kblam.toml` as usual (§4).
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
   tool, desk-hooks H12): if the target path is under `findings/`, deny it.
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
   - A command that only reads `findings/` (`grep`, `cat`, `Get-Content`) is never denied. As
     built, neither is `rm`, nor the *source* of an `mv`: removing a stray file is how a K8 failure
     is fixed. Not yet built (M6.10): removing a finding file (`F-NNNN-<slug>.md` in a topic
     folder) is denied with a pointer to `kblam rm` (§7), since removing a finding is an
     adjudicator's decision; removing any other file under the KB root stays allowed. (Under
     `.kblam/`, removal is denied; see above.)
   - **Adjudicator gate** (not yet built, M6.10). When `[kb] adjudicators` is set (§9), a command
     that runs `kblam resolve` or `kblam rm` is denied if the hook input carries an `agent_type`
     that is not in the list. A plain main session has no `agent_type` and passes; every subagent,
     and every session started with an agent definition (a teammate, for one), carries one
     (desk-hooks H15). An empty list therefore leaves these commands to the main session, and a
     project with a librarian lists the librarian's agent type. The gate keys on a type, not a
     name, so every agent of a listed type passes, and a subagent whose type equals its session's
     own agent name cannot be told apart from that session (desk-hooks H3). A person running kblam
     in a terminal is not gated. With `adjudicators` absent there is no gate, as built.

   The reason reads "kblam: this command writes under findings/ (<targets>), so it is denied." with
   item 1's tail. Both shell tools are matched, because with `CLAUDE_CODE_USE_POWERSHELL_TOOL=1`
   (set on the pilot project's machines) agents may write through either (desk-hooks H9). This is
   only a first line; it can't parse everything (a `cd findings` before a relative write, a script
   that writes files, a backslash path in Bash), and the Stop hook (item 3) catches what it misses.

3. **Stop and SubagentStop:** hash `findings/`; if the hash equals `.kblam/tree.hash`, exit 0
   silently, so while the tree is as kblam left it the hook costs one hash (a practitioner in the
   comment thread of Karpathy's LLM-wiki gist removed a Stop hook because it fired on every
   response, desk-llmwiki A6). With no `tree.hash` and no `findings/` there is no knowledge base
   yet, and the hook is silent. If the hash differs, the tree changed outside `kblam put`:
   - It checks every finding not yet checked at its current fingerprint (as `validate --record`
     does, so what fires becomes review items), then runs `validate` and lists the open items.
   - A clean result is silent and leaves `tree.hash` stale: only `kblam validate --record` accepts
     an out-of-band change. Until someone runs it, every stop validates again (its check finds
     nothing new to ask) and every `put`, `ack` and `index` warns that `findings/` changed outside
     kblam, so the change stays visible until someone accepts it on purpose.
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
   - **A new clone.** A populated `findings/` with no `tree.hash`, as in a fresh clone or after
     `.kblam/` was deleted, counts as changed, so as built the hook checks every finding with Jev.
     A KB of the pilot's size cannot finish that within the hook's 300 s timeout (at six workers,
     the 4,077 pairs of the pilot's migration would each need an answer in under 0.44 s), so the hook
     times out, lets the stop through, and starts over at the next stop until the cache fills. Not
     yet built (M6.10): in that case the hook runs only the deterministic rules, which the
     committing machines' pre-commit hooks already ran, and blocks on their failures as above;
     `kblam validate --record` records the tree without asking Jev and marks every finding as
     accepted from the repository, so later checks cover what changes after the clone, and
     `kblam audit` checks the rest when someone wants it.
   - **Committed state.** While git tracks files under `.kblam/` (above), the hook ignores
     `tree.hash` and the open items, runs only the deterministic rules, as on a new clone, and
     blocks with the committed-state message and any failures. The loop guard applies as above.

   SubagentStop also fires for Claude Code's internal agents (prompt suggestions, `/btw`), which
   have an empty `agent_type` and can't fix `findings/`. The hook exits 0 silently for them
   (desk-hooks H15). An input with no `agent_type` key at all is treated as a real subagent. So is
   a subagent whose `agent_type` is the session's own agent name (desk-hooks H3); the hook cannot
   tell it apart.

4. **git pre-commit** (a POSIX `sh` script that `kblam init` installs): `kblam --root <repo>
   validate --commit`, with `kblam` from `PATH`. Catches anything written outside Claude Code. If
   `kblam` is not installed the command fails with 127 and the commit is refused. Any non-zero exit
   prints "kblam pre-commit: commit refused (kblam validate exit N)." and the skill pointer.

   **Configuration changes (user, 2026-09-25).** The deny hooks cannot see every write to
   `kblam.toml` (a `git checkout` of an older version, a script), so `--commit` also compares the
   `kblam.toml` the commit will hold (the index, read with `git cat-file`) with the last commit's. A
   difference is refused unless a person approved exactly that version on this machine with
   `kblam approve-config`; a commit that removes it is refused outright. Approvals are sha256 digests
   of the file with CRLF normalised to LF (so a `core.autocrlf` working copy matches the stored
   blob), one a line in `.kblam/config-approved`, which the deny hooks protect. `approve-config`
   asks only on an interactive terminal, so an agent's non-interactive shell cannot approve; a
   wrapper that supplies a terminal, or `git commit --no-verify`, still gets past it, so like the
   other hooks this stops an agent taking a shortcut, not one set on evading it. `kblam init` records
   an approval of the template it writes, so the first commit of an unedited `kblam.toml` needs
   none.

   **What is validated.** As built, apart from `kblam.toml`, `--commit` validates the working tree,
   not the commit, and fails on any open review or unchecked item. So it can pass a commit that
   leaves out part of a put (a new finding staged without its regenerated `INDEX.md`), it can
   refuse a valid commit because of an unrelated unstaged change, and one open item, or a Jev
   outage, blocks every commit on that machine, code-only commits included. Not yet built
   (M6.10):
   - It refuses a commit while any file under the KB root has unstaged changes or is untracked, so
     the tree it validates is the tree being committed.
   - The open-item condition applies only to a commit that changes a file under the KB root or
     `kblam.resolutions.jsonl`; other commits are not blocked by open items. The K rules apply to
     every commit.
   - It refuses a commit that modifies or deletes a file under an `evidence_roots` or
     `history_dirs` folder that the last commit holds (adding files is allowed), because evidence
     is immutable (P3) and reported findings quote the retired documents (K11). A person who must
     change committed evidence commits with `--no-verify`, knowing that the commit is then not
     validated.
   - It warns about an `evidence` path or verbatim source that git does not track: that finding
     passes K2 and K10 here and fails them on every clone.
   - It refuses a commit whose `kblam.resolutions.jsonl` does not parse.

5. **Librarian agent** (optional; proposed by the user 2026-09-23; see §8.1).

6. **Agent guidance** (§8.2): a path-scoped rule for reading findings and a skill for writing them.
   Every message that stops a write names the skill with the line "Load the kblam-write skill for
   how to fix this.": items 1–4, and every `put` refusal. For `put` that means the final line of a
   K-rule reject (exit 1) and of a Jev or quantity reject (exit 4), and every error `put` stops on
   except a config error (exit 2): the edit-base guard and bad-file refusals (exit 1, §7) and a lock
   timeout (exit 3).

Items 1–4 and every rule in §5–§6 hold with or without a librarian: `kblam put` is the only way
into `findings/` whoever calls it. Several writers are the normal case, so `put` is safe under
concurrency by itself (§7: edit-base guard and lock).

### 8.1 Librarian agent

**Optional.** A long-lived agent in one of two roles. As an **adjudicator**, deployable now, it
decides review items, rejected items and suspect dependencies, while research agents still write
through `kblam put` themselves. As a **writer** (M7, deferred) it is also the only writer of
`findings/`: other agents read `findings/` directly and send it observations to record, and it turns
them into findings through `kblam`. Without a librarian, research agents run `kblam new`/`edit`/`put`
themselves via Bash, and the coordinator owns what the librarian would otherwise own: running
`kblam validate` at the end of each work package and clearing suspect dependencies (K3) and Jev
review items. The role is kblam's. kblam's repository carries a general definition of the adjudicating
librarian, `agents/librarian.md` (user, 2026-09-26), for a deployment to copy into its
`.claude/agents/` and adapt; its model, and any research workers it spawns, are the deployment's
(Appendix C describes the pilot's).

#### Adjudicating librarian (deployable now; user, 2026-09-23)

Until the MCP server exists, a deployment can run a librarian whose role is adjudication. Research
agents still write through `kblam put` themselves. The general definition, `agents/librarian.md`,
names no model, so the deployment picks one (a `model:` line; without one the librarian runs on the
session's model). The coordinator, the read-only research worker type it may spawn and any ranking of
sources come from the project's CLAUDE.md. When the project sets `[kb] adjudicators` (§9), the
librarian's agent type is listed there.

- **It owns review items, rejected items and suspect dependencies.** An author whose put raised a
  review item, was refused with a rejected item (§6.4), or made dependents suspect sends the
  librarian the item or finding IDs and carries on working. A rejected item is closed only when
  Jev misread it; otherwise the author edits as the reject says.
- **For each review item** it reads both findings and their cited evidence, then takes one of
  three actions:
  - **Merge.** A real `same_fact` or `restates_and_extends` restatement is merged: it
    `kblam edit`s the existing finding to carry the new detail and drops the new finding's copy
    of the fact. A finding the merge leaves with nothing of its own to state is removed with
    `kblam rm <id> --merged-into <existing>` (§7).
  - **Close.** It runs `kblam resolve --distinct` only when Jev misread the item: a pair of
    distinct facts, or a direct statement read as a correction. It writes the reason for a later
    reader.
  - **Correct.** A finding that its own cited evidence contradicts is corrected with
    `kblam edit`.
- **For each suspect dependency** it re-reads the target, then either `kblam ack`s the dependent or
  edits it.
- **It judges form and evidence, not physical truth.** When two findings conflict and the cited
  evidence does not settle which is right, it leaves the item open and escalates to the
  coordinator; it does not pick a winner.
- **Items its own writes raise go to the coordinator, with one exception** (user, 2026-09-24). It
  may close a `low_confidence` item that its own merge or correction raised, with
  `kblam resolve --distinct` and a written reason, when the pair states distinct facts. Every other
  verdict raised by its own write (`same_fact`, `restates_and_extends`, `cannot_both_be_true`,
  `revision`) goes to the coordinator.
- Its tools and any research workers it uses are the deployment's (Appendix C). Its boundary is kept
  by instruction and by the §8 hooks, not by construction; the adjudicator gate (§8 item 2) makes
  `resolve` and `rm` its own. K1–K12 and Jev gate its writes as they gate anyone's.

What no gate can supply without a librarian is an owner for the old finding: an agent
mid-experiment whose put is rejected as a duplicate must rewrite the existing finding, and may
instead reword to slip past K9; the Jev check (§6) is the defence there, and `kblam items --reworded`
(§7) shows the adjudicator where it happened.

#### Writing librarian (M7, deferred)

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
- **A hard tool boundary.** The writing librarian is given kblam as an MCP server (`put`, `check`,
  `validate`, `resolve`, `ack`, `search`) and **no Bash, Write or Edit**. It cannot write
  `findings/` except through kblam, so the validator and Jev gate every librarian write by
  construction. Other agents' direct writes to `findings/` are denied by the §8 hooks.
- **Cheap replacement.** Its knowledge is the KB itself, so a compacted or replaced librarian loses
  nothing that matters.

Risks and their mitigations (both roles):
- **Paraphrase drift** (the librarian rewords evidence). Mitigation: K10 checks every excerpt tagged
  as verbatim against its cited file, and K12 (not yet built) requires every blockquote to be
  tagged, so no quotation escapes the check (the idea of `scripts/check_evidence.py` in
  Astro-Han/karpathy-llm-wiki, the largest derivative of the LLM-wiki pattern, desk-llmwiki A2). The
  librarian writes the claim; quotes are copied, not retyped.
- **It judges form, not truth** (above): it escalates what the evidence doesn't settle.
- **Bottleneck.** Senders don't wait: they send and continue working. Volume is low (findings, not
  code).
- **Model choice** is the user's call. Nothing observed in this design work says one model tier
  follows the rules better: every agent involved ran on the same model, so the two "reported
  compliance, file shows otherwise" cases say nothing about models, only that self-reports are
  not evidence.
- **It is still an LLM.** Enforcement never relies on its discipline; K1–K12 and Jev check its
  writes like anyone else's.

**Identifying the caller.** A hook can tell a subagent's call from the main thread's. Hook input
carries `agent_id` (an opaque per-subagent ID, present only inside a subagent) and `agent_type` (the
agent *type*, e.g. a custom agent name, present inside a subagent and in a session started with an
agent definition; not a teammate's name). No field carries the name a coordinator addresses a
teammate by (desk-hooks H15). A per-agent rule can therefore key on `agent_type`; the adjudicator
gate (§8 item 2) does.

### 8.2 Agent guidance: read rule and write skill

Split by when an agent needs it (user, 2026-09-23). Agents read findings constantly and write them
rarely. A skill loads only when the task seems to match its description, so reading guidance as
a skill would miss the moments it is for; writing guidance loaded into every reader would undo the
"research agents stay lean" aim (§8.1). The source files ship inside the kblam package
(`src/kblam/assets/`), and `kblam init` writes them into the consuming repo's `.claude/` (§7.1).

- **Reading: `.claude/rules/kblam-findings.md`**, a Claude Code project rule with
  `paths: ["findings/**"]` in its frontmatter, so it loads when an agent works with a finding and
  costs nothing otherwise. Content, kept short:
  - `findings/` holds only current facts; history is in git and `evidence/`, never in a finding.
  - Start from `findings/INDEX.md`.
  - The first paragraph is the claim. What `label` means for how far to trust it: `observed`
    (measured or seen directly), `decoded` (read out of code, firmware or a data format),
    `inferred` (concluded from other facts, not seen directly; trust it less), `unknown` (records
    what is not known), `reported` (a retired document under `history/` states it and nothing in
    the tree reproduces it: do not build on it; reproduce it first). And that `scope` limits where
    it applies (an MX-100 finding says nothing about the MX-200). The text describes the default
    labels and history folder; a project that changes `[kb] labels` or `history_dirs` says so in its
    own CLAUDE.md, since `init --update` rewrites the rule.
  - A suspect dependency (`kblam deps F-x`) means the finding it depends on was rewritten since
    this one was checked against it: re-read the target before relying on the dependent.
  - Cite findings by ID; don't cite `.kblam/staging/` files or desk answer files.
  - A finding's `check:` command runs only through `kblam recheck`, which runs one only once a
    person has approved it; never run it directly, since anyone who can push can write one (§7).
  - To add or change a finding, load the `kblam-write` skill.

  The consuming repo's CLAUDE.md also carries one always-loaded line, because a path-scoped rule
  loads when a matching file is **Read**, not on a Grep hit and not on Edit (documented,
  desk-hooks H10), so an agent that only greps `findings/` never gets it: "Findings live in
  `findings/`, hold current facts only, and are written only via `kblam put` (load the
  `kblam-write` skill); reading guidance loads when you open one."
- **Writing: `.claude/skills/kblam-write/SKILL.md`**, a skill whose description names adding,
  changing or correcting a finding and handling any kblam refusal. Content:
  - The finding format (§4): frontmatter fields, the claim paragraph and its length limit, verbatim
    excerpt tags (K10), `quantities` and `anchors` (quoted strings).
  - The flow: `kblam new` or `kblam edit`, edit the staged file, `kblam put`. Never write under
    `findings/` directly (it is denied).
  - Each refusal in author terms: what triggered it and what to do. K rules by code (exit 1); Jev
    and quantity rejects (exit 4); lock timeout (exit 3, retry).
  - **A verdict that names an existing finding means: edit that finding.** The rejects `same_fact`
    and `cannot_both_be_true` (and `quantity_conflict` and K9), and the review verdict
    `restates_and_extends`, name an existing finding: `kblam edit` that one so it states the current
    fact, rather than rewording the new one until it passes. Rewording to get past K9 or Jev is the
    failure this tool exists to stop (§8.1), and the skill says so and why. A `revision` reject
    means the same: rewrite the original finding instead of writing a correction. (The skill as
    built files all of these under the heading "A reject means"; M6.10 corrects the heading.)
  - After a rewrite: which dependents became suspect, and `kblam ack` only after re-reading the
    target.
  - **Review items:**
    - what each verdict means;
    - who decides: the coordinator, or the librarian if one is deployed (§8.1), never the author
      whose write raised the item. The one exception is the librarian closing a `low_confidence`
      item its own write raised (§8.1). An author sends the IDs to the librarian and carries on;
    - the merge rule: a real `same_fact` or `restates_and_extends` restatement is merged into the
      existing finding;
    - `resolve --distinct` is only for an item Jev misread: two findings that state distinct facts,
      or, for `revision`, a direct statement read as a correction. The reason names what differs,
      for a later reader.
  - Unchecked items: `kblam check --pending` once Jev is reachable.
  - `check:` commands (§7, `kblam recheck`): how to write one (a program and its arguments, no
    shell, run from the repository root, exit 0 when the number reproduces); that `kblam recheck`
    runs one only once a person has approved it, so an author asks the user to approve a new or
    changed one at a terminal and never runs or approves it any other way; and what a failed check
    means.
  - That writes and removals under `.kblam/` are denied, except in `.kblam/staging/`, and so are
    those under `.git/kblam/` (§8).
- **Pointers from the tool.** The §8 deny hooks, the Stop hook's block message and every `put`
  refusal end with "Load the kblam-write skill for how to fix this." so an agent reaches the skill at
  the moment it needs it, whatever its description matching does.

### 8.3 Threat model and data flow

**Whom the protections guard against.** kblam's hooks and checks stop an agent taking a shortcut
(a direct write, a reworded duplicate, a correction appended instead of an edit) and catch honest
mistakes. They do not stop an agent set on evading them: a script can write files the shell hook
cannot parse, `git commit --no-verify` skips the pre-commit hook, and a wrapper can supply a
terminal (§8 item 4). Each protected asset has a stated adversary:
- **The rules** (the thresholds, vocabularies and term lists in `kblam.toml`): agents. The deny
  hooks and a person's approval at commit keep an agent from weakening a check (§8).
- **The API key and the findings' text**: other contributors, and an agent whose write to
  `kblam.toml` the hooks miss. `kblam.toml` is committed, so a change to it would otherwise decide
  which secret kblam sends and where, and the Stop hook would send it with nobody running a
  command. Hence the limits on `endpoint`, `key_env`, `key_file` and `ollama_url` in `kblam.toml`
  (§9). They keep kblam's own requests from being redirected; they cannot stop an agent with a
  shell from reading the key file itself, or from writing `~/kblam/config.toml`, which is outside
  the repository and outside the hooks' reach.
- **kblam's state** (`.kblam/`, and `kblam.resolutions.jsonl`): hand edits that would close items
  or silence the Stop hook (§8). For `.kblam/`, also other contributors: a pull writes tracked files
  over ignored ones, so a commit holding files there would replace every clone's own, and kblam acts
  on none of that state while git tracks any of it (§8, "Committed state").
- **The user's environment**: `check:` commands, which any contributor can commit in a finding.
  `kblam recheck` runs only commands a person approved on that machine, keeps those approvals in
  the git directory, where no commit can write, and runs each without the key's variable (§7). An
  agent may be allowed to run `kblam` without asking, so the approval is what keeps a committed
  command from running unseen.

**What leaves the machine.** With any verdict enabled in `[jev.thresholds]`, as in the template,
kblam sends each checked finding's claim paragraph and scope to TypeSafe through OpenRouter
(`[jev] endpoint`), with the API key; no other part of a finding is sent. The pilot's user approved
this (Appendix C). A project that must keep its text local removes `[jev.thresholds]`, and kblam
then sends nothing to Jev (§6.4). Titles and claims also go to ollama, which `kblam.toml` may place
only on the local machine (§9). The logs hold IDs, fingerprints and verdicts, never finding text or
the key.

## 9. Configuration (`kblam.toml`)

```toml
[kb]
root = "findings"
evidence_roots = ["evidence", "bench-runs", "device-dumps"]   # K2: where evidence may lie (M6.10)
labels = ["observed", "decoded", "inferred", "unknown", "reported"]
scopes = ["MX-100", "MX-200", "MX-100/MX-200", "host-software", "any"]
reported_label = "reported"   # K11; "" turns off the reported-label checks
history_dirs = ["history"]    # K11: retired documents (§11 step 2); evidence for no other label
max_lines = 300
max_claim_words = 250
history_terms = ["was wrong", "were wrong", "wrong about", "supersed", "withdrawn", "retract",
                 "refuted", "is falsified", "was falsified", "falsified by", "now known",
                 "previous revision", "previously believed", "previously reported",
                 "previously said", "i previously", "not previously", "no longer true",
                 "no longer holds", "no longer valid", "no longer safe", "used to argue",
                 "turned out", "once quoted", "do not quote", "must not be quoted",
                 "do not revive", "removed from this list"]
# Each term is used only in an unwanted sense in the pilot corpus (Appendix B.5). Bare
# "previously", "no longer", "used to", "falsif", "revised" and "correction:" have legitimate uses
# ("the previously read buffers", "data not used to fit it") and are narrowed or dropped. Project
# conventions ("[rewrite fix]", "HISTORICAL —") belong in the project's own list.
duplicate_similarity = 0.9     # K9
history_id_window = 10         # K5, words
lock_wait_seconds = 30         # how long the lock's holders (§7) wait for .kblam/lock
lock_stale_seconds = 300       # a lock older than this (or whose holder is dead) is broken (§7)

[jev]
endpoint = "https://openrouter.ai/api/v1/systemone"
# key_env = "OPENROUTER_API_KEY"   # kblam.toml may hold only these two defaults (below)
# key_file = "~/kblam/jev!.txt"
model = "typesafe/jev-1.13"
expected_served_model = "typesafe/jev-1.13-20260917"   # §6.5: the snapshot whose answers are cached
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
served_model = "typesafe/jev-1.13-20260917"   # §6.4: the snapshot the thresholds were measured on
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

The block above is an example configuration for a hypothetical project. The prompt's criteria are
abbreviated: `src/kblam/assets/kblam.toml` holds the full text of the default, and `4dda2f781f12`
is the id of that text. `kblam.toml` is committed, so it holds nothing machine-specific. kblam
rejects a key it does not know, in `[kb]` as in `[jev.thresholds]`.

**`[kb] root`** is a relative path inside the repository made of `/`-separated segments of ASCII
letters, digits, `.`, `_` and `-` (a trailing `/` or a leading `./` is normalised away); anything
else is a config error, since `init` writes the root into shell-quoted hook commands and into the
rule's YAML (as built, M6.6).

**Why the check's settings sit in `[jev]`.** `[jev]` configures the whole §6 check, not only the
calls to Jev: candidate selection (`max_candidates`, the bonuses and the embedding settings, §6.1)
and the quantity comparison (`quantity_rel_tolerance`, §6.3) are code, and they run even when
`[jev.thresholds]` enables no verdict. Those keys stay in `[jev]` so that every existing
`kblam.toml` keeps working (Appendix A).

**API key.** The key is read from the environment variable named by `key_env` if it is set,
otherwise from `key_file`, a path relative to the user's home directory when it starts with `~`
(user, 2026-09-23: a key file, not an environment variable). `~` expands to HOME, or USERPROFILE on
Windows, here and in the per-machine `~/kblam/config.toml`; any other relative `key_file` is
relative to the repository root. The file holds only the key, on one line. The defaults are
`OPENROUTER_API_KEY` and `~/kblam/jev!.txt`. Each machine keeps the key at that path; the key never
enters the repository, and the missing-key message names both the variable and the file. The `!` in
the default name starts history expansion in interactive bash, so a person creating the file there
single-quotes that part of the path (`~/'kblam/jev!.txt'`); kblam reads the path itself, not through
a shell. The default stays as it is, since machines already keep their keys there (Appendix A).

**Per-machine settings (user, 2026-09-25).** `kblam.toml` is committed, so anyone who can change the
repository, and any agent working in it, could otherwise choose which secret kblam sends as the API
key and where it and the findings' text go; the Stop hook would send them with nobody running a
command. So four `[jev]` keys are limited in `kblam.toml`: `key_env` and `key_file` may only hold
their defaults, `endpoint` must be an `https://openrouter.ai/` URL, and `ollama_url` must name this
machine (`127.0.0.1`, `localhost` or `[::1]`). Anything else there is a config error naming the key
and where to set it instead: `~/kblam/config.toml`, an optional per-machine file holding only a
`[jev]` table with any of those four keys as strings. Its values override `kblam.toml`'s and have
none of those limits, since only the machine's user writes it. §8.3 says what these limits do and do
not protect.

`kblam init` writes `kblam.toml` with the `[jev]` section, the default prompt and thresholds as shown
(they belong to the model and the wording, not the project; §10 says what that assumes) and generic
`[kb]` vocabularies (`evidence_roots = ["evidence"]`, `scopes = ["any"]`) for the project to edit. A
project may edit the prompt text — `kblam prompt-id` then prints a new id, the thresholds no longer
match (§6.4: nothing is rejected until they are recalibrated), and `kblam init` reports the prompt as
differing from the installed one without touching it.

**Keys M6.10 adds (not yet built).** Until M6.10 is built kblam rejects these as unknown keys, so no
`kblam.toml` may carry them yet. `kblam init` will write them as shown; a `kblam.toml` without them
keeps the behaviour of today.

```toml
[kb]
topics = []                    # K1: the allowed topics; empty = any
adjudicators = []              # §8 item 2: agent types that may run kblam resolve and kblam rm; absent = no gate
history_id_terms = ["wrong", "incorrect", "mistaken", "erroneous", "corrects", "corrected",
                    "correction", "replaces", "replaced", "instead of", "contradicts",
                    "contradicted", "outdated", "obsolete", "invalid", "revises", "revised"]
                               # K5; absent = K5 uses history_terms
verbatim_blockquotes = true    # K12; absent = off
scope_separator = "/"          # §4, §6.1; "" = a scope value never splits
scope_wildcard = "any"         # §4, §6.1; "" = no scope overlaps every other
recheck_timeout_seconds = 600  # §7 kblam recheck: a check: command running longer is killed and fails; absent = 600

[jev.thresholds]
relation_prompt_id = "6d79e4e0e409"   # §6.2; with revision_prompt_id, replaces prompt_id
revision_prompt_id = "d9a34c823fe3"
```

The K5 list above has not been tested on any corpus. Like the K4 list, it should be checked for
legitimate uses on a real one before a project turns it on (§13).

**Upgrading.** `prompt_version` (an integer, in `[jev]` and in `[jev.thresholds]`) is gone: a config
that still carries it, or lacks `[jev.prompt]`, is an error naming the move, never a fallback. A
`.kblam/pairs.sqlite` from before the move is the same kind of error: kblam names the file and asks
the user to delete or migrate it, because the answers in it are keyed by the old integer. Deleting it
means the missing pairs are asked again, and loses the resolutions and checked marks it holds. The
formats M6.10 introduces (not yet built) are adopted with `kblam upgrade`, once per KB and once on
each machine (§7); a `[jev.thresholds]` that still records `prompt_id` is accepted while it equals
the current combined id (§6.2).

## 10. Calibration experiment (must run before reject mode is enabled for a model and wording)

Thresholds hold for the served model and the question wording they were measured on (§6.4). A
project that keeps the default wording and model uses the pilot's calibration (§10.6), which the
`kblam.toml` template records; a project that changes either recalibrates before anything is
rejected again. That the pilot's thresholds suit another project's corpus is an assumption, not a
measurement: §10.7 watches for it failing, and §13 lists it as a risk.

This follows the pilot project's evidence rules: one evidence package for the run
(`evidence/<date>-jev-contradiction-eval/`) with README (objective, decision, outcomes, stop
condition), `raw/` (the labelled pairs and the raw API responses) and `derived/`.

### 10.1 Decision it changes

For each verdict (contradiction, duplicate, restate-and-extend, revision): whether Jev rejects
writes, only raises review items, or is not used.

### 10.2 Labelled set, built from the pilot corpus (~150–250 pairs)

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
  candidate model was refused by the provider) then label every pair independently and blind to
  each other and to that signal, including a flag for rewrites that changed meaning or leaked a
  cue. Agreement is reported. Each disagreement goes back to both labellers with the other's label
  and reasoning; a labeller that concedes resolves it (one labeller noticing what the other missed
  is not a conflict). Only pairs where both hold their positions go to the user, who decides them.
- Split 50/50 into calibration and held-out test sets, stratified by class.
- The set the run used (desk-jev-wording A5): 95 relation pairs and 41 revision items from the
  corpus, and 84 constructed relation pairs added because duplicates and extensions were rare in
  it; the 179 relation pairs split 91 for calibration and 88 held out.

### 10.3 Procedure

Run every pair (one request per pair, §6.2) twice and report the flip rate; choose thresholds on the
calibration half only; report precision, recall and a confusion matrix on the held-out half, each
precision and recall with its counts and an exact (Clopper–Pearson) 95% interval. Record the served
model ID, tokens and cost. Report the resolution of the returned values before choosing thresholds:
in the M3 smoke both answers came back at exactly two decimals (confidence 1.0, probabilities
1.0/0.0, noul 0.99), so thresholds finer than the actual resolution are meaningless.

**Selection rule.** Each verdict's cuts sit on the weakest calibration positive at the chosen
operating point, so a re-run moves them by about ±0.02 (§10.6), and §10.4 decides the mode.
The exact search that chose the operating point is recorded in the pilot's evidence package,
not yet here (§13); `kblam calibrate` (§7; not yet built) must reproduce it.

### 10.4 Pre-registered cut-offs (held-out set, per verdict)

- **Reject mode** if precision ≥ 0.90 at recall ≥ 0.70.
- **Review-only** if precision ≥ 0.50 at recall ≥ 0.60.
- **Disabled** otherwise; the deterministic rules (K4, K5, K9, §6.3) still apply.

### 10.5 Stop condition

One full run, plus at most one round of prompt-wording revision on the calibration half. If a
verdict doesn't reach review-only after that revision, stop; more prompt tuning on ~100 pairs would
overfit.

### 10.6 Result (2026-09-22, typesafe/jev-1.13-20260917, the v2 wording)

The labelled set and the raw responses are in the pilot project's evidence package (README,
`derived/metrics.md`, `derived/metrics-topup.md`). 136 corpus pairs plus 84 constructed top-up pairs
(duplicates and extensions were too rare in the corpus), each run twice; about $0.017 in total.
Held-out precision / recall: `cannot_both_be_true` 1.00 / 0.83, `revision` 1.00 / 0.91, `same_fact`
1.00 / 0.71 (all reject), `restates_and_extends` 0.76 / 0.86 (review; 0.43 precision on corpus pairs
alone). The thresholds are in §6.4 and §9. Limits: small samples; the contradiction threshold sits
on the weakest calibration positive (PAIR-109 falls just below it on run 2); `same_fact` positives
are almost all constructed. The precisions are point estimates on small counts: 1.00 over n
predicted positives has an exact 95% lower bound of 0.025^(1/n), about 0.69 at n = 10 and 0.48
at n = 5, so reaching §10.4's reject bar by point estimate does not establish it. The counts behind each
figure are in `derived/metrics.md` and are still to be copied here with their intervals (§13). "The
v2 wording" is the wording measured under `prompt_version` 2, before prompt ids existed; its text is
in the evidence package.

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

### 10.7 Field monitoring

After calibration the verdicts are watched in use. This is not a pre-registered test: which pairs
get checked, and so which items exist, depends on the pipeline (desk-jev-wording A3). For each
verdict, the items it raised that have closed are counted: an item closed with `resolve --distinct`
is a false alarm, and one closed by an edit, a merge or a removal is not. Some of the latter were
false alarms that an unrelated edit closed, so the measured share of false alarms is a lower bound.
Once at least 20 items of a verdict have closed, recalibrating it (§10.3–§10.5, on pairs that
include long claims) is due when that share exceeds what its mode's pre-registered bar allows: 10%
for a reject verdict, counted over its rejected items, and 50% for a review verdict. Until then the
verdict keeps its mode. `kblam items --stats` (§7; not yet built) gives the counts; until it exists
they are counted in `.kblam/review.jsonl`, whose closed items keep their closing reason.

**Status (2026-09-25).** `restates_and_extends` raised 34 items in the calibration-topic
migration, and 18 were closed as distinct: at least 53% of its closed items, above the review
bar's 50%, so its recalibration is due as soon as 20 of its items have closed, if they have
not already (§13). For `low_confidence` at the 0.3 cut, and for the reject verdicts (whose rejected
items are recorded only since commit `af316b5`), this document has no counts yet.

## 11. Migration of an existing corpus

0. Run `kblam init` in the repository (§7.1): `kblam.toml`, the §8.2 rule and skill, the §8
   hooks, the pre-commit hook and the CLAUDE.md line are then in place before any finding is written.
1. Start with one topic; a calibration topic is the natural first, since two documents already
   conflict on one entry and cover the same subject.
2. Move that topic's source documents into a history folder (`history/`, one of
   `[kb] history_dirs`) that is excluded from agents' default search with `.ignore`/`.rgignore`. The Grep
   tool honours it (Appendix A); a shell `grep -r` or `Select-String` does not. The documents stay
   in the tree because `reported` findings quote them (K10, K11), and step 3 quotes them at their
   new path. A source document that no reported finding quotes may be deleted instead, since git
   keeps it; one that a reported finding quotes may not.
3. An agent extracts candidate claims from those documents into staged findings. For each
   falsified-and-corrected pair it writes only the current fact (P1/P2). Every staged finding goes
   in through `kblam put`, so the validator and Jev check the migration itself. A claim the source
   states that nothing in the tree reproduces, and that no later document or finding contradicts,
   goes in as `reported` (§4, K11), quoting the source at its `history/` path, so it is not lost
   with the source.
4. Before further topics migrate, an independent agent that wrote none of the findings reviews the
   result against the cited evidence. Its corrections go in through `kblam put` like any other.
5. Replace the documentation-discipline section of the repository's CLAUDE.md with the §8.2
   one-line pointer that `init` appended: findings live in `findings/`, they are written only
   via `kblam put`, `kblam validate` is the authority, and history is in git and `evidence/`.
   Check the §13 loading questions with real agents here. Remove the rules the validator now
   enforces from CLAUDE.md and from the research-desk definition (its answers-file INDEX-with-line-
   numbers scheme is failure mode 3). Narrow CLAUDE.md's manifest requirements to observations
   (Appendix C).

## 12. Implementation milestones

Each milestone's "Status" says what is implemented. Where a milestone's details now live in the
sections above, it points there.

- **M1.** Finding schema, `validate` (K1, K2, K4–K10), `index`, `new`, `edit`, `put` (without the Jev
  step, which M5 adds), `tree.hash`, with tests on fixture KBs. *Status: built.*
- **M2.** Fingerprints, `ack`, `deps`, K3, and `put`'s semantics: stamping null fingerprints, K3 on
  other findings not blocking, the edit-base guard, and the lock (§5.1, §7). *Status: built; the
  lock's heartbeat is M6.10.*
- **M3.** Jev client (endpoint, key loading, retries, cost log, cache) plus a smoke test on one
  synthetic pair (`kblam jev-smoke`). *Status: built.*
- **M4.** Calibration experiment (§10); thresholds written into config, with the `prompt_id` of the
  wording they were measured on (§6.2). *Status: done (§10.6).*
- **M5.** `check`, `audit`, review workflow, and the §6.4 decision policy. *Status: built.*
  - Candidate selection (§6.1) and quantity comparison (§6.3) are code, logged per check.
  - **Jev in `put`**: asked before the lock, decided under it (§7).
  - A pair or revision question that could not be asked (JevUnavailable) makes the write
    unchecked (§6.5), never accepted as passed.
  - `kblam check [F-…]` runs the same decision for existing findings without writing; reject
    verdicts found there become review items (the finding is already in the tree). `check
    --pending` retries unchecked items. `audit` checks every candidate pair with no cache entry
    for the current fingerprints, model and prompt id.
  - `validate` fails while `.kblam/review.jsonl` holds an open review or unchecked item.
    `validate --record` runs `check` on the changed findings first (§7).
  - Tests use a fake Jev client (no network); one opt-in live test is skipped without a key.
- **M6.** Hooks (§8) and the pre-commit hook; test that code writes outside `findings/` are
  unaffected and that a shell write into `findings/` is caught at Stop. The consuming repo gets
  `.gitattributes` with `findings/** -text` (§7.1). Agent guidance (§8.2): the `kblam-findings`
  rule and the `kblam-write` skill, written from this spec and checked against the CLI's actual
  messages and exit codes; every write-stopping message (deny hooks, Stop block, `put` refusals)
  names the skill, with a test that each one does. The console script dispatches `kblam hook`
  without importing the rest of the CLI (§8). *Status: built.*
- **M6.5.** Packaging: one install per machine, one command per project (user, 2026-09-23); the
  design and its as-built details are in §7.1. *Status: built.* Tests: `init` in a fresh git repo
  under `tmp_path` produces a KB that validates; a second `init` changes nothing; existing settings
  keys and hooks survive the merge; a foreign pre-commit hook is not overwritten; `--update`
  rewrites a changed skill but never `kblam.toml`; the hooks are silent with no `kblam.toml`.
- **M6.6.** Generality fixes (user, 2026-09-23), before M8. *Status: built.*
  - Candidate selection over the whole KB, replacing auto-extracted anchors and the ID tie-break:
    linked findings plus BM25 similarity (M6.7 and M6.8 later made it an embedding, with links as
    a bonus; §6.1). `topic_bonus` in §9. The check log (`checks.jsonl`) records each candidate's
    reason.
  - K6 defaults 250 words and 300 lines (§5, §9, the template, the skill).
  - K4's term list replaced by the phrases that an evaluation on the pilot corpus found used only
    in unwanted senses (Appendix B.5); a test per dropped term shows its legitimate use now passes
    (e.g. "the dark frame is used to subtract the offset").
  - `.kblam/` protection in the hooks (§8).
  - `{{kb_root}}` substitution in the assets (§7.1).
  - As built: the tokens, documents, scores, bonuses and log in §6.1; `.kblam/` in the hooks in §8;
    `{{kb_root}}` in §7.1; `[kb] root` in §9; K4 and K5 report once per matching term (so "was
    wrong about" is reported under both "was wrong" and "wrong about"), §5.
- **M6.7.** Embedding candidates (user, 2026-09-24): §6.1's embedding mechanism with BM25 fallback,
  the `.kblam/embeddings.sqlite` vector cache, and the §9 keys, in both `kblam.toml` files (§6.1
  gives the as-built details). *Status: built.* **Tests** use a fake ollama (a local HTTP server or
  an injected client), never a real server. They cover:
  - ranking by cosine, with ties by ID;
  - fallback on a refused connection, on a missing model, and on an embed request that fails
    partway through, each with its stderr line;
  - `embedding_model = ""` forcing BM25;
  - a cache hit sending no request, and a digest change sending one;
  - the 64-input chunking;
  - BM25 behaviour unchanged.
- **M6.8.** Link bonus (user, 2026-09-24): §6.1's links become a score bonus; `depends_on` stays
  first. `link_bonus` (≥ 0) in §9 and in both `kblam.toml` files (§6.1 gives the order and the log's
  reason strings). *Status: built.* **Tests** cover:
  - a dependency ranking first despite a lower score;
  - a linked finding overtaking an unlinked one within the bonus, and losing to one beyond it;
  - one bonus for several links;
  - a zero-score linked finding under BM25 being a candidate;
  - `link_bonus = 0` giving pure score order;
  - the new reason strings.
- **M6.9.** Reported claims (user, 2026-09-25): the `reported` label (§4) and K11 (§5).
  *Status: built.*
  - `[kb] reported_label` (default `"reported"`) and `history_dirs` (default `["history"]`) in §9;
    `"reported"` joins the default `labels` and both `kblam.toml` files.
  - The read rule (§8.2), the write skill's format and refusal table, and a "Reported findings"
    section in the skill say what the label means and how a reported finding is promoted.
  - **Tests** cover: a reported finding quoting history passing every rule; one with no history
    excerpt; history in another label's `evidence`; history in another label's prose and
    excerpts passing; a dependency on a reported finding, from another label and from a reported
    one; the label checks off when the label is not in the vocabulary; configurable folders.
- **M6.10.** Fixes from the review of draft 1 (2026-09-25, on the user's instruction to fix every
  finding; reasons in Appendix A). *Status: not built.* Each change is described where it belongs.
  Each needs tests, and the skill and the rule change with the messages they describe
  (CONTRIBUTING.md).
  - *State across clones:* resolutions in the committed `kblam.resolutions.jsonl`, keyed by state
    hashes (§6.4), with `init`'s `merge=union` line (§7.1) and the hooks' protection (§8); `new`
    allocating above every ID in git history, and `renumber` for a collision (§7); a new clone
    validated without Jev, with `validate --record` and the tree.hash rule's bootstrap recording a
    baseline (§8 item 3, §8).
  - *The pre-commit check* (§8 item 4): the committed tree, open items only for commits to the KB,
    immutable evidence, a warning for untracked evidence, a parseable resolution log.
  - *Removing a finding:* `kblam rm` (§7), and the hooks' denial of a shell removal of a finding
    (§8 item 2).
  - *`put`:* refuses only on errors it introduces (§7).
  - *Hashes:* fingerprint v2 (§5.1), state hashes for the cache (§6.5), per-question prompt ids
    (§6.2), and `kblam upgrade` to adopt them (§7, §9).
  - *Resolutions:* the `revision` item's messages and resolution kind (§6.4); `resolve` refusing a
    quantity conflict (§6.3).
  - *Enforcement:* the adjudicator gate (§8 item 2); `kblam items` with `--reworded` and `--stats`
    (§7); `ack` showing the old claim (§7); K12, K5's own terms, `[kb] topics` in K1,
    `evidence_roots` in K2 and K10's hex excerpts (§5); configurable scope symbols (§4, §9).
  - *The lock:* a heartbeat, so a live holder is never broken (§7).
  - *Calibration:* `kblam calibrate` (§7) and the counts of §10.7.
  - *`kblam recheck`* (user, 2026-09-26): argv without a shell, approvals that pin the files a
    command names and live in the git directory, the key's variable removed (§7).
  - *Committed state:* kblam acts on none of `.kblam/` while git tracks any of it (§8, §8.3).
  - *Assets:* the skill's "A reject means" heading becomes "A verdict that names an existing
    finding" (§8.2); the skill and the rule describe `rm`, `items`, the gate, K12 and resolving a
    `revision` item; the template gains the M6.10 keys (§9).
- **M7. Deferred (user, 2026-09-24).** kblam MCP server and a writing librarian with no
  Bash/Write/Edit tools (§8.1). The MCP `put` goes through the same edit-base guard and lock as the
  CLI (M2). Nothing before or in M8 depends on it: the librarian deployed now (§8.1) uses the CLI.
  *Status: deferred.*
- **M8.** `kblam init` in the pilot project, the calibration-topic migration (§11), done by the
  librarian if deployed, otherwise by a migration agent using the CLI, and the CLAUDE.md rewrite.
  Then the §13 loading questions are checked with real agents. *Status: in progress in the pilot
  project (§13, Appendix B.4, Appendix C).*

## 13. Open questions and unknowns

Settled questions move to Appendix A with their answers.

- **Rule and skill loading (§8.2), unknown:** whether project rules and skills reach subagents and
  teammates the same way they reach the main session (the docs are silent either way,
  desk-hooks H10). M8 checks it with a real teammate. (A path-scoped rule loads on Read, not on
  Grep; that is documented, and the CLAUDE.md one-liner covers it.) An agent whose definition lists
  its tools without `Skill` cannot load the skill (observed in M8, Appendix C). Whether a teammate
  whose definition lists `Skill` loads the skill through that tool is still to be checked with a
  real teammate, in §11 step 5.
- **Thresholds on long claims.** The §10 calibration used claims rewritten to at most 80 words; the
  default limit is now 250 (K6). The migration's checks on claims of up to 250 words behaved
  differently (Appendix B.4). By §10.7, `restates_and_extends` is due for recalibration on long
  claims once 20 of its items have closed, if they have not already. The reject verdicts' precision
  on long claims is unknown until enough rejected items close: they are recorded since `af316b5`,
  and each one closed as distinct is a false reject. The reject thresholds stay until §10.7 says
  otherwise.
- **Thresholds on other corpora.** The template's thresholds were measured on one hardware
  reverse-engineering corpus plus constructed pairs (§10). Whether they suit another project's
  findings is untested; §10.7's monitoring is how a project finds out.
- **Figures still to be published.** The pilot project holds, and this repository does not: the
  held-out counts behind §10.6's precisions, with their intervals; the exact threshold search of
  §10.3; the evaluations described as not published (the link-bonus replay, Appendix B.1; the
  candidate-recall evaluation, Appendix B.2, whose questions are in desk-sim; the K4 term
  evaluation, Appendix B.5); and the migration's `.kblam/checks.jsonl` and `review.jsonl`
  (Appendix B.4), which are gitignored machine state that one `git clean -fdx` erases. Each should
  become an evidence package or a research note that the spec cites by path. That pass should also
  say how Appendix B.1's "first 174 checks" relate to Appendix B.4's "166 checks": they are counts
  from the pilot's check log, and how the two extracts differ is not recorded.
- **The K5 term list** (§9, M6.10) has not been evaluated on any corpus. K4's list was; K5's
  should be, before a project turns it on.
- **`kblam migrate`** (§7) is planned but unspecified: which part of §11 step 3 it would do, and
  how its output would go through `put`.
- **Deferred (user, 2026-09-24):** the MCP server (M7). Idea recorded for then:
  rejection tickets. A rejected `put` returns `needs_rewrite` with a ticket stored under `.kblam/`,
  and the Stop/SubagentStop hook blocks the agent from finishing while it holds an open ticket.
  This works the same for the CLI and MCP. A ticket would be keyed on the hook input's `agent_id`
  (§8.1, desk-hooks H15). SubagentStop also fires for Claude Code's internal agents, with an empty
  or session-level `agent_type`, so a ticket check must ignore those.

## Appendix A. Decision log

Decisions whose history no longer belongs in the sections, by date. The sections keep each
decision's rule and its attribution.

**2026-09-22**
- **Implementation language: Python + official `typesafe_sdk` (decided by the user).**
  §6.4's decision policy copies jev-dsl's design of explicit, named threshold policies, enforced by
  tests instead of types. The facts behind the decision (desk-jevdocs Q2, Q9; claims marked ✓ were
  independently verified against the jev-dsl repo):
  - *Python + official `typesafe_sdk`* (PyPI `typesafe-sdk` 0.7.1, Python ≥ 3.10): talks to
    OpenRouter by changing the base URL; retries built in. Windows support is undocumented
    (desk-jevdocs Q2), but it runs on the pilot's Windows machine (Appendix C).
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
- **`low_confidence` at 0.49 (user).** Set from the post-hoc look at the calibration pairs
  (Appendix B.3).

**2026-09-23**
- **K6's claim limit** is 250 words: the user found 80 far too small (M6.6).

**2026-09-24**
- **Candidate mechanism (user).** From the recall measurement (Appendix B.2): use the embedding when
  ollama serves the model, and BM25 otherwise (§6.1, M6.7). Dependencies keep priority, and a shared
  anchor or evidence path is a score bonus (§6.1, M6.8): in the real KB, linked-first filled most of
  the budget in a tail of checks (Appendix B.1).
- **`low_confidence` at 0.3 (coordinator, on the user's delegation).** The calibration-topic
  migration measured the 0.49 cut on real claims of up to 250 words: 517 of 4077 asked pairs
  (12.7%) fell below 0.49. Of the 425 review items raised, 359 were closed as distinct and none
  was closed by a merge; the rest closed as a side effect of edits. On the migration's pairs, 15 of
  the 517 verdicts fall below 0.3.
- **The Grep tool honours `.ignore` (§11 step 2), observed.** In the pilot project, with
  `history/` in `.ignore`, a directory-wide Grep for `offset` found nothing under `history/`. The
  same Grep given one retired document's path found 8 lines, and `grep -c` also counts 8.

**Undated**
- **Shell-tool routing, not measured and not needed:** with `CLAUDE_CODE_USE_POWERSHELL_TOOL=1`
  the docs say a Bash-only matcher never fires, yet the Bash tool is still available in the pilot's
  sessions (desk-hooks H9). §8 matches both shell tools, so no answer would change anything.

**2026-09-25: draft 2, the fixes from the review of draft 1** (on the user's instruction to fix
every finding; M6.10 lists what they change in the code).
- *Judgement was machine-local.* Resolutions become committed state (`kblam.resolutions.jsonl`,
  §6.4), because an adjudicator writes its reasons for later readers, who may work on another
  machine. Review, rejected and unchecked items stay machine-local work queues: the pre-commit
  check keeps open items out of commits to the KB, and a re-check on another machine raises again
  whatever needs deciding there. IDs are allocated above git history, with `renumber` for the
  collisions two unsynchronised clones can still make (§7). A new clone trusts the validation the
  committing machines ran instead of re-asking Jev in a Stop hook that cannot finish (§8 item 3).
  Candidates stay machine-dependent by design, and §6.1 now says so.
- *The pre-commit check validated the working tree and blocked every commit on any open item.* It
  validates what is committed, blocks on open items only for commits to the KB, and enforces the
  immutability of evidence that P3 asked for and nothing checked (§8 item 4).
- *There was no way to remove a finding, and removed IDs could be reissued.* `kblam rm` after a
  merge, as an adjudicator's command; IDs are never reissued; a shell removal of a finding file is
  denied (§7, §8 item 2).
- *`put` refused on errors it did not cause.* It refuses only on errors it introduces, extending
  M2's reasoning for K3 to every rule (§7).
- *One fingerprint did two jobs.* Fingerprint v2 adds the label and a canonical list order (§5.1);
  a state hash keys cached answers and resolutions, so an edit Jev cannot see re-asks nothing and
  keeps its resolutions (§6.5).
- *A false `revision` reject had no stated appeal.* `resolve --distinct` on a `revision` item means
  "not a correction", which the code already allows; its messages are to say so (§6.4).
- *Rules enforced only by prose.* The adjudicator gate (§8 item 2), the reworded-item report and
  `ack`'s display of what changed (§7), K12 (§5), `evidence_roots` enforced in K2 (README.md already
  said it was meant to be), and a topic vocabulary (K1).
- *Calibration.* §10's heading now says what the template assumes; field monitoring (§10.7)
  replaces a recalibration trigger that any single false reject met; one prompt id per question
  (§6.2); counts and intervals with every precision (§10.3, §10.6); `kblam calibrate` (§7).
- *Kept as they are.* The Stop hook still leaves `tree.hash` stale after a clean out-of-band change,
  because CONTRIBUTING.md keeps "only `validate --record` accepts an out-of-band change" as an
  invariant, and the stale state keeps the change visible (§8 item 3). The default key file name
  stays, because renaming it would break every machine that keeps its key there (§9). The check's
  keys stay in `[jev]`, because moving them would break every existing `kblam.toml`; §9 now says why
  they are there. New strictness that could fail an existing KB (K5's own terms, K12, topics, the
  adjudicator gate) is off until a project's `kblam.toml` turns it on; `evidence_roots`, which
  README.md announced, and fingerprint v2, which `kblam upgrade` adopts, are the exceptions.
- *Presentation.* The status line, sources, glossary and failure-mode table were added; §2, §8 and
  §12 now render as intended (the principles, two paragraphs of §8 and the milestones had run
  together);
  history, measurements and pilot-specific material moved into these appendices; the review's
  remaining contradictions and undefined terms were resolved in place.

## Appendix B. Evidence and measurements

**B.1 The candidate budget and the link bonus (§6.1).** Across the first 174 checks of the pilot
corpus, 29 had 15 or more linked candidates and 4 had 25 or more; one interface specification took
577 slots. A replay of those checks, in an evaluation on the
pilot corpus that is not published (§13), found:
- at no bonus from 0 up does any `restates_and_extends` or `cannot_both_be_true` pair fall out of
  the budget;
- a rejected conflict that entered only through a link ranks 24th of 66 by cosine alone, and 5th at
  0.15;
- no pair that linked-first displaced draws a verdict under the current policy.

**B.2 Candidate recall (§6.1), measured on the M4 labelled pairs** (an evaluation over the pilot
corpus, not published, 2026-09-23; questions S1–S10 in desk-sim). Each positive pair was ranked in
both directions against a pool of 461 claims.
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

**B.3 The low-confidence band (§6.4)** comes from a post-hoc look at the calibration pairs
(evidence `derived/low-confidence.md` in the calibration package), not a pre-registered held-out
result. Below confidence 0.49, 12 of 179 pairs fell in the band and 7 were contradictions,
duplicates or extensions that the verdicts missed. Below 0.3, 2 pairs fell in it, and both were such
positives. desk-jev-wording A3 weighs the band's false reviews against the positives it catches.

**B.4 The calibration-topic migration** (166 checks, 4077 asked pairs, claims up to 250 words;
`.kblam/checks.jsonl` and `review.jsonl` in the pilot project, 2026-09-24):
- `low_confidence` at 12.7% of pairs, against 6.7% in calibration, with no merge among the
  items it raised (at the 0.49 cut; Appendix A).
- `restates_and_extends` fired 49 times; 18 of the 34 items it raised were closed as distinct.
- `cannot_both_be_true` rejected 17 puts and `revision` 4. Before `af316b5`, a reject left no
  record, so its precision on long claims is unknown. Rejected items (§6.4) now record it: each
  `R-…` closed distinct is a false reject.

**B.5 The K4 term list (§9)** comes from an evaluation on the pilot corpus, not published (37,483
finding lines, every occurrence classified): each term is used only in an unwanted sense there.
Bare "previously", "no longer", "used to", "falsif", "revised" and "correction:" have legitimate
uses ("the previously read buffers", "data not used to fit it") and are narrowed or dropped; a test
per dropped term shows its legitimate use passing (M6.6).

## Appendix C. The pilot project's deployment

What the pilot project decided or observed for its own deployment. kblam does not depend on it.

- **The adjudicating librarian's tools** (§8.1): Bash, used only for `kblam`; Read, Grep and Glob;
  Edit, for its staged files only; and the Skill tool, SendMessage, and Agent for research workers.
  It may spawn the deployment's read-only research workers to check cited evidence and look up
  sources (the deployment defines this worker type and where it looks sources up). A worker writes
  its report to a file whose path the librarian gives it, because an in-process teammate receives
  no subagent report. `agents/librarian.md` is this deployment's definition with the project's
  names, model and paths taken out (user, 2026-09-26).
- **Machines.** The pilot's machines set `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` (§8 item 2). On the
  Windows machine this design was developed on, hook commands run in Git Bash (desk-hooks H9, H13,
  H14), and `typesafe-sdk` 0.7.1 was observed on 2026-09-22 reaching OpenRouter's
  `/api/v1/systemone` (the M3 smoke); it carried the M4 calibration's requests without a transport
  failure.
- **Agent definitions and the skill** (observed in M8, 2026-09-23): an agent whose definition has
  an explicit `tools:` list without `Skill` (the `implementer` type) cannot load the skill. A
  teammate of that type found the CLAUDE.md line, then asked how to load the skill without the
  Skill tool. It was told to read `.claude/skills/kblam-write/SKILL.md` as a file. A teammate of the
  same type running a different model found and read that file without being told. Since
  2026-09-24 (user), the user-level `implementer` and `research-desk` definitions list `Skill`.
- **Data handling** (desk-jevdocs Q8): the user has approved sending finding text to TypeSafe via
  OpenRouter (§8.3).
- **Evidence manifests in the pilot project record observations only (P3; coordinator, on the
  user's delegation, 2026-09-24).**
  - A manifest keeps everything CLAUDE.md asks for now except interpretation: the objective and
    decision, the outcomes, controls and stop condition, the device state, the commands and
    hashes, the artifact inventory and the reproduction steps.
  - Its results are what was observed, and each names the finding IDs that interpret it.
  - `inferred` and `decoded` conclusions go in findings, where they can be corrected. In a
    manifest they would outlive their correction.
  - The CLAUDE.md edit is part of §11 step 5. Existing manifests are not rewritten.
