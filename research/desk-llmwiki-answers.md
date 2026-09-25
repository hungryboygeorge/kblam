# desk-llmwiki — agentic / LLM-maintained wikis

Package: a survey of **external** systems in which an LLM agent authors and maintains a knowledge
base of markdown files. Web research only; no codebase claims. This is the gap desk-kbtools left
open ("knowledgebase_guardian, OpenKB, and the `llm-wiki` family", named only in passing in that
file's A26).

**Failure modes being solved, numbered as in the request (same numbering as desk-kbtools):**

1. A falsified claim stays in its original section; the correction is appended as a new section
   ("C16 — C10 is FALSIFIED") and grep-by-excerpt keeps landing on the stale line.
2. A new document supersedes an old one; the "superseded" marker does not survive grepping.
3. Hand-maintained indexes go stale when content is inserted without updating them.
4. Failed experiments are kept whole with a correction appended, instead of a short dead-end stub.
5. The same fact is copied between working "answers" files and findings files and the copies drift.

**Method.** WebSearch, WebFetch, direct `curl` of raw sources and GitHub API, on **2026-09-22**.
Quotes are marked with how they were obtained: "direct fetch" = the bytes were pulled and read;
"search summary" = relayed by a search-result summary and **not** re-read on the page. The
Karpathy gist and the two main repos were read as raw file bytes, not through a summarizer.
Anything unverified is labelled UNVERIFIED. Maturity (stars, created, last push) is from the
GitHub API on 2026-09-22, quoted per entry, because the previous survey was burned by taking a
README claim at face value on a 0-star repo.

---

## ⬅️ OPEN QUESTIONS

1. **No measured evaluation exists yet.** Question 5's answer so far is negative: the gist thread
   (A6) yields one-month self-reports and no numbers, and the academic work (A9) measures
   stale-fact rate in retrieval memory rather than degradation of a markdown wiki under multiple
   agents. A sweep of the 2026 agent-memory literature for a wiki-specific measurement is still
   worth 1–2 hours and could change the ranking in A11.
2. **Does `nvk/llm-wiki`'s Claude Code plugin register hooks?** A6 and A7 settle the category
   question — a write-time hook exists in this ecosystem and is used for session recording, not
   validation. Unverified: whether the *Claude Code* variant registers any hook at all (the file
   read is the Codex variant, and `claude-plugin/` shows no `settings.json`). One fetch of the
   plugin's install docs would close it.
3. **`OpenKB`** — named by desk-kbtools and not located by name in this survey's searches. Either
   it is too small to rank in search results, or the name is inexact. Unresolved.

---

## INDEX

```
primary-source      A1  Karpathy's "LLM Wiki" gist — full structure, verbatim   2026-09-22  L63
implementations     A2  Astro-Han/karpathy-llm-wiki — SKILL.md + checker        2026-09-22  L190
implementations     A3  SamurAIGPT/llm-wiki-agent — health/lint split, tools     2026-09-22  L361
spec-standard       A4  arturseo llm-knowledge-base — quarantine/confidence    2026-09-22  L516
maintenance-rules   A5  Glukhov — drift taxonomy + linter check list           2026-09-22  L650
field-evidence      A6  Gist comment thread — hooks, multi-agent, compaction   2026-09-22  L845
implementations     A7  nvk/llm-wiki — hooks, derived indexes, retract         2026-09-22  L1014
also-rans           A8  knowledgebase_guardian + the immature others           2026-09-22  L1195
literature          A9  Measured stale-fact results (arXiv, 2026)              2026-09-22  L1265
rankings            A10 Coverage table, adopt-as-is, borrow-to-build         2026-09-22  L1366
model-analysis      A11 Rewrite-in-place re-classification, ranking   2026-09-22 L1485
model-analysis      A12 Retain-but-unretrievable, evaluated            2026-09-22 L1646
```

(Entry IDs are the address. Line ranges are a point in time; re-derive from this index.)

---

## A1 — Karpathy's "LLM Wiki": the primary source

verified: 2026-09-22

**Identity.** Gist at `https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f`. The
file inside it is named `llm-wiki.md` (from the gist page's own header markup, direct fetch
2026-09-22). The page's creation stamp is `datetime="2026-04-04T16:25:13Z"`, rendered "Created
April 4, 2026 16:25" — **so the primary source is dated 2026-04-04**. The gist has **no
description field**. Comment timestamps on the page run 2026-09-03 through at least 2026-09-19,
so the gist is still receiving discussion five months later. The thread is large: the GitHub
comments API reported `rel="last"` = page 12 at `per_page=100`, so roughly **1,100–1,200
comments**; the first is dated 2026-04-04T16:49:23Z, 39 minutes after the gist was created. What is
in that thread is entry A6. No starred/forked count could be read off the page (gists do not show
them the way repos do); the "5,000+ stars" figure that appears in search summaries is
**UNVERIFIED** and I would not repeat it.

**How obtained.** The gist page and `https://gist.githubusercontent.com/karpathy/442a6bf555914893e9891c11519de94f/raw`
were both fetched with `curl` on 2026-09-22 and read as bytes (raw returned HTTP 200). Everything
quoted below is from the raw file. WebFetch on the same URL returned a *summary* rather than the
text, so its output was discarded — no secondhand summary is used for this description.

The document opens, verbatim:

> This is an idea file, it is designed to be copy pasted to your own LLM Agent (e.g. OpenAI Codex,
> Claude Code, OpenCode / Pi, or etc.). Its goal is to communicate the high level idea, but your
> agent will build out the specifics in collaboration with you.

### The three layers, verbatim

> **Raw sources** — your curated collection of source documents. Articles, papers, images, data
> files. These are immutable — the LLM reads from them but never modifies them. This is your source
> of truth.
>
> **The wiki** — a directory of LLM-generated markdown files. Summaries, entity pages, concept
> pages, comparisons, an overview, a synthesis. The LLM owns this layer entirely. It creates pages,
> updates them when new sources arrive, maintains cross-references, and keeps everything
> consistent. You read it; the LLM writes it.
>
> **The schema** — a document (e.g. CLAUDE.md for Claude Code or AGENTS.md for Codex) that tells
> the LLM how the wiki is structured, what the conventions are, and what workflows to follow when
> ingesting sources, answering questions, or maintaining the wiki. This is the key configuration
> file — it's what makes the LLM a disciplined wiki maintainer rather than a generic chatbot.

### The three operations, verbatim (abridged to the operative sentences)

**Ingest** — "An example flow: the LLM reads the source, discusses key takeaways with you, writes
a summary page in the wiki, updates the index, updates relevant entity and concept pages across
the wiki, and appends an entry to the log. A single source might touch 10-15 wiki pages."

**Query** — "the LLM searches for relevant pages, reads them, and synthesizes an answer with
citations. ... **good answers can be filed back into the wiki as new pages.** A comparison you
asked for, an analysis, a connection you discovered — these are valuable and shouldn't disappear
into chat history."

**Lint** — "Periodically, ask the LLM to health-check the wiki. Look for: contradictions between
pages, stale claims that newer sources have superseded, orphan pages with no inbound links,
important concepts mentioned but lacking their own page, missing cross-references, data gaps that
could be filled with a web search."

### Contradiction and staleness: what the document actually says

Two sentences, and they are the whole of the mechanism:

> When you add a new source, the LLM doesn't just index it for later retrieval. It reads it,
> extracts the key information, and integrates it into the existing wiki — updating entity pages,
> revising topic summaries, **noting where new data contradicts old claims**, strengthening or
> challenging the evolving synthesis.

> The cross-references are already there. **The contradictions have already been flagged.**

So the design intent is: flag, do not resolve. There is no rule about which of two contradicting
claims survives, no status vocabulary, no withdrawal, no dependent invalidation, and no
requirement that the corrected sentence be the one a grep lands on. The word "noting" is doing all
the work, and nothing downstream consumes the note.

### Index and log

Verbatim on `index.md`:

> **index.md** is content-oriented. It's a catalog of everything in the wiki — each page listed
> with a link, a one-line summary, and optionally metadata like date or source count. Organized by
> category (entities, concepts, sources, etc.). **The LLM updates it on every ingest.** When
> answering a query, the LLM reads the index first to find relevant pages, then drills into them.
> This works surprisingly well at moderate scale (~100 sources, ~hundreds of pages) and avoids the
> need for embedding-based RAG infrastructure.

Verbatim on `log.md`:

> **log.md** is chronological. It's an append-only record of what happened and when — ingests,
> queries, lint passes. A useful tip: if each entry starts with a consistent prefix (e.g.
> `## [2026-04-02] ingest | Article Title`), the log becomes parseable with simple unix tools —
> `grep "^## \[" log.md | tail -5` gives you the last 5 entries.

Both the link summary and the timestamp are hand-written by the LLM in the same pass as the
content edit. There is no generator and no check that index and files agree.

### Verbatim preservation

Karpathy's document contains **no rule requiring quoted source text to appear verbatim in the
wiki**. The immutability is on `raw/` only ("These are immutable — the LLM reads from them but
never modifies them"). The wiki layer is explicitly "Summaries, entity pages, concept
pages, comparisons, an overview, a synthesis" written by the LLM. Nothing forbids a number in a
wiki page from being invented, transcribed wrong, or rounded. The word "verbatim" does not occur
in the document. This is the single largest gap between the pattern as published and the pilot
project's requirement that verbatim evidence and citations survive — and it is the gap the
derivative in A2 exists to close.

### Enforcement: none

There is no script, no validator, no schema file with a machine-checkable grammar, no hook, and no
exit code anywhere in the gist. Every rule is prose addressed to the LLM. "Everything mentioned
above is optional and modular — pick what's useful, ignore what isn't." The document says of
itself: "This document is intentionally abstract. It describes the idea, not a specific
implementation."

Two incidental tool notes, for completeness: the gist recommends **qmd**
(`https://github.com/tobi/qmd`, MIT, TypeScript, ~29.9k stars, last push 2026-09-09, GitHub API
2026-09-22) as a local markdown search engine with CLI and MCP server, and mentions Obsidian
Dataview for queries "over page frontmatter". It also frames the whole thing as "just a git repo
of markdown files."

### The one-sentence answer to failure modes 1–5

Not addressed by the primary source as a mechanism: all five are delegated to LLM prose in a
schema file, with the flagging of contradictions (failure 1) being the only one the document
names, and its remedy being an annotation rather than a correction.

## A2 — Astro-Han/karpathy-llm-wiki: the largest derivative, and the only one with a checker

verified: 2026-09-22

**Maturity, from the GitHub API (`api.github.com/repos/Astro-Han/karpathy-llm-wiki`, 2026-09-22):
2,334 stars, 279 forks, 7 open issues, MIT, created 2026-04-05T06:17:20Z (the day after
Karpathy's gist), last push 2026-07-23T16:57:44Z, language Python, repo size 372 KB.** So: real
traction, one day younger than the pattern it implements, and roughly two months without a push as
of the survey date.

**What it is.** A Claude Code / Codex / Cursor **Agent Skill** — a `SKILL.md` (14 KB) plus
templates under `references/` and one script. Full tree, from
`api.github.com/repos/Astro-Han/karpathy-llm-wiki/git/trees/HEAD?recursive=1`, 2026-09-22:

```
SKILL.md                          14342   README.md                          7852
scripts/check_evidence.py         14475   tests/test_check_evidence.py      28043
references/archive-template.md      752   references/article-template.md     1283
references/index-template.md        541   references/raw-template.md          310
examples/ (4 files, incl. a log-sample.md and a rendered example wiki)
```

Note what is **absent**: no `hooks/`, no `.claude/settings.json`, no CI workflow, no CLI. Nothing
in the repo runs on its own.

### The rule it exists to enforce: the Grounding Invariant

Verbatim from `SKILL.md` (fetched as raw bytes from
`https://raw.githubusercontent.com/Astro-Han/karpathy-llm-wiki/HEAD/SKILL.md`, 2026-09-22):

> ## The Grounding Invariant
>
> Every load-bearing fact in wiki/ — numbers, dates, direct quotes — exists verbatim in the raw/
> files linked by that article's Raw field. Compile *establishes* this invariant (locate before you
> write); lint *verifies* it (`scripts/check_evidence.py` greps the high-signal literals — suffixed
> or large numbers, decimals, ISO dates, longer quotes — in the linked raws; the compile-time
> locate-before-write rule covers the rest). Because raw/ is immutable, a verified article stays
> verified; the script re-checks the whole wiki in seconds, so there is no incremental state to
> maintain.

The compile-time half, verbatim:

> **Source fidelity.** Every number, date, and direct quote must be located in the raw file (grep
> or read) *before* it is written; write the value exactly as found — if the source says 42K, write
> 42K, not 42,000. Derived values (sums, deltas, counts you computed) must show their components so
> each component is findable in raw. If you cannot locate a value, do not write its exact form; drop
> it or state it without precision.

This is the closest thing in the survey to what the pilot project needs for provenance: it preserves
the literal, forbids silent rounding, and *requires derived numbers to publish their inputs*. Note
the split it depends on — `raw/` is byte-immutable and the wiki may only restate it.

### The checker: what `scripts/check_evidence.py` mechanically does

Read from the raw file (14,475 bytes, fetched 2026-09-22). Module docstring, verbatim:

> Report-only; never modifies files. Three sweeps:
>
> 1. Fidelity — extract candidate literals (specific numbers, ISO dates, direct quotes) from each
>    wiki article and verify that each candidate appears verbatim in the body of the raw files
>    linked by that article's Raw field. Misses are listed as suspects. Derived values, product
>    names, and deliberate paraphrases will show up as suspects; judging them is the reader's job,
>    not this script's.
> 2. Evidence errors — articles that cannot be verified at all: a missing Raw field on a
>    non-archive article, Raw links that do not resolve, or Raw links that escape raw/ (evidence
>    must live in immutable raw/).
> 3. Inventory — raw files that no article's Raw field references, excluding files whose ingest
>    was logged as "no material".

The coverage boundary is stated as a closed, frozen set, verbatim:

> Coverage boundary (closed candidate set, frozen): candidates are
> - quotes of 15+ characters (double-quoted spans and body blockquotes)
> - ISO dates (YYYY-MM-DD, YYYY-MM)
> - specific numbers: thousands-grouped (10,000), dotted (2.1.80, 3.14), suffixed (42K, 99.9%),
>   or 4+ digits (2026)
> Small plain integers ("42", "500") and exotic forms (signs, currencies, spelled-out dates) are
> deliberately not checked; they belong to the compile-time locate-before-write rule and to
> judgment review. New prose forms extend this list in the docstring, not the regexes.

And the line that decides how it can be used, verbatim:

> The exit code carries no information; the report is the interface.

Confirmed in the code: `main` ends `return 0` on every completed run. The only non-zero return is
`return 1` when there is no `wiki/` directory at all. **This checker is not a gate and cannot be
turned into one without editing it.**

Two implementation details worth copying, both visible in the source:

- A value must match with word-boundary discipline, not substring. From `contains`: the pattern is
  `r"(?<![\d.,])" + re.escape(value) + right + r"(?![A-Za-z0-9]|[.,]\d|%)"`, with `right = r"(?!-\d{2})"`
  when a 7-character date (a bare `YYYY-MM`) would otherwise pass as the prefix of a full ISO date.
  Verbatim comment: "Values must stand on their own, while sentence punctuation remains valid. A
  month may not pass as the prefix of a full ISO date."
- Collection metadata is fenced off from the search space, so a date cannot false-pass against the
  header. Verbatim from `source_content`: "Raw file body with the metadata header removed.
  Collection metadata (Source/Collected/Published) is bookkeeping, not evidence; letting it match
  candidates would false-pass dates and years." The same idea runs on the article side: the
  extractor skips lines inside a `> **Status:` block, so the writer's own commentary is never
  checked as if it were evidence.

### Contradiction handling: the opposite of what this project wants

Verbatim from `SKILL.md`, under Cascade Updates:

> When the new source supersedes or contradicts an existing claim, keep the old claim for the
> record but mark it with a Status block (see `references/article-template.md`): **Outdated** when
> something newer replaces it, **Disputed** when sources disagree. **Never silently rewrite
> history.**

And under Triage, the disposition vocabulary: **New / Update / Disputed / No material**, where
"No material" is exclusive and means "adds no knowledge beyond what the wiki already holds. Keep
the raw file, log it (see Post-Ingest), and stop."

This is failure mode 1 deliberately chosen as the design. The falsified claim stays in place, with
a `> **Status: Outdated**` blockquote above it, and the file's premise is that a reader who lands
on the claim will read the status block. The system also acknowledges the grep risk and works
around it only by convention: "Do not rely on the index alone: search the full wiki for the source's
key entities, aliases, and the claims it touches."

Archive pages are the one place a claim is frozen rather than annotated — verbatim: "Archive pages
are never cascade-updated (they are point-in-time snapshots)", and a page whose header carries
`> Archived:` is exempted in code from needing a Raw field at all (the `ARCHIVED_RE` branch of
`check_article`).

### Index generation and the lint trigger

The index is **hand-maintained by the LLM, then checked and repaired by the lint pass**, not
generated. Verbatim from the Lint → Safe Fixes section, which is the only place in the repo where
anything is fixed automatically:

> **Index consistency** — compare `wiki/index.md` against actual wiki/ files (excluding index.md
> and log.md):
> - File exists but missing from index → add entry with `(no summary)` placeholder. ...
> - Index entry points to nonexistent file → mark as `[MISSING]` in the index. Do not delete the
>   entry; let the user decide.
> - Index entry's Updated differs from the article's metadata Updated ... → update the index entry
>   to match the article.

The lint pass has three authority tiers, verbatim headings: **Safe Fixes (auto-fix)** — index
consistency, internal links, Raw references, See Also links; **Mechanical Reports (no fixes)** —
the three `check_evidence.py` sweeps, with "Report findings; never auto-fix facts"; **Judgment
Reports (no fixes)** — "Factual contradictions across articles", "Outdated claims superseded by
newer sources but still presented without a Status block", "Missing conflict annotations where
sources disagree", orphan pages, and "Archive pages whose cited source articles have been
substantially updated since archival".

**When does validation run? Never automatically.** Lint is one of three operations invoked by
user phrasing — the skill's trigger list is `ingesting`, `querying`, `linting`, "'add to wiki'",
"'what do I know about'". There is no hook on write, no stop-of-session check, no pre-commit hook,
no CI. Lint ends by appending `## [YYYY-MM-DD] lint | <N> issues found, <M> auto-fixed` to
`wiki/log.md` — a self-report, not a record of what was checked.

**Writes made outside the agent's tools**: not addressed anywhere in the repo. The skill assumes
all wiki writes go through the agent's file-edit tools while the skill is active. A `sed`, a
heredoc, or a Python script writing into `wiki/` would simply not be noticed, and the checker would
only find the result the next time someone asked for a lint. This is the gap named in OPEN
QUESTIONS item 3, and it is the gap that makes a lint-command design insufficient for a corpus
that several agents also write code and scratch files into.

### Coverage of the five failure modes

Falsified claim left in place (1): **actively reinforced** — the `Status: Outdated` block is the
prescribed remedy. Supersession (2): present as an annotation, not as a rewrite, and nothing
verifies a dependent was revisited. Index rot (3): best-in-survey — the lint pass mechanically
reconciles index against files, marks `[MISSING]`, and fixes Updated dates. Dead-end stubs (4):
"No material" is logged and the raw kept, but there is **no size rule and no stub enforcement**
anywhere in `SKILL.md` or the checker. Copies drifting (5): not addressed; no cross-article
duplicate detection, only the "Factual contradictions across articles" judgment sweep.

## A3 — SamurAIGPT/llm-wiki-agent: the second design, with the health/lint split

verified: 2026-09-22

**Maturity, GitHub API 2026-09-22: 3,559 stars, 410 forks, 5 open issues, MIT, language Python,
repo size 413 KB, last push 2026-09-21T08:59:04Z — one day before this survey.** The API reports
`created_at 2023-04-21T08:31:00Z`, three years before the LLM-wiki gist, so the repository was
repurposed rather than born with this content; do not read the creation date as the project's age.
Self-description from the same API response, verbatim: "A personal knowledge base that builds and
maintains itself. Drop in sources — Claude (or Codex/Gemini) reads them, extracts knowledge, and
maintains a persistent interlinked wiki. Works with Claude Code, Codex, OpenCode, Gemini CLI. No
API key needed."

**Shape.** Same three layers as A1/A2 (`raw/` immutable, `wiki/` agent-owned, a schema file —
shipped three times over as `CLAUDE.md`, `AGENTS.md`, and `GEMINI.md`, 7.9/9.3/6.2 KB), plus
**standalone Python tools** that a human or an agent runs by hand. Full tool list from
`git/trees/HEAD?recursive=1`, 2026-09-22: `tools/build_graph.py` (45.9 KB), `tools/lint.py`
(15.7 KB), `tools/ingest.py` (15.3 KB), `tools/health.py` (10.1 KB), `tools/pdf2md.py`,
`tools/query.py`, `tools/refresh.py`, `tools/file_to_md.py`, `tools/heal.py`, `tools/_utils.py`.

### The health/lint split — the most useful structural idea in this survey

Verbatim from the module docstring of `tools/health.py`
(`https://raw.githubusercontent.com/SamurAIGPT/llm-wiki-agent/HEAD/tools/health.py`, direct fetch
2026-09-22):

> Structural health checks for the LLM Wiki.
>
> Unlike lint.py (which includes expensive LLM-powered semantic analysis), health.py is purely
> deterministic — zero API calls, fast enough to run every session.
>
> Checks:
>   - Empty / stub files (pages with no real content beyond frontmatter)
>   - Index sync (wiki/index.md entries vs actual files on disk)
>   - Log coverage (source pages without a corresponding log entry)
>
> Design boundary (see AGENTS.md):
>   health.py = structural integrity, deterministic, run every session
>   lint.py   = content quality, semantic (LLM), run every 10-15 ingests

`CLAUDE.md` states the same boundary as a table, verbatim rows: `**LLM calls**` — "Zero" vs "Yes
(semantic analysis)"; `**Cost**` — "Free" vs "Tokens"; `**Frequency**` — "Every session, before
other work" vs "Every 10-15 ingests"; with the note "Run `health` first — linting an empty file
wastes tokens."

This is the design worth naming: **split the cheap deterministic checks from the expensive
semantic ones, and run the cheap ones on every session.** Every project in this survey that has
any deterministic check at all has this split or something like it; none of them gates a write
with it.

### What health.py checks mechanically, and the one size rule in the survey

- **Stub detection, and it is a length rule.** The module constant is `STUB_THRESHOLD_CHARS = 100`
  (verbatim source), and the check is `if len(body) < threshold:` after `strip_frontmatter`, with
  the reported status being `"empty" if len(body) == 0 else "stub"`. Verbatim docstring on the
  check: "Find wiki pages that are empty or contain only frontmatter / minimal content." The
  `CLAUDE.md` gloss names what it is for: "**Empty / stub files** — pages with no content beyond
  frontmatter (rate-limit damage)". Note the intent: this is a detector for a *failed write*
  (a truncated LLM output), not a rule that a withdrawn finding must shrink. **Failure mode 4's
  "shrink to a stub" is still not enforced by anyone**, but a threshold constant is the shape such
  a rule would take.
- **Index sync, deterministic.** `_parse_index_links` is one regex, `r'\[.*?\]\(([^)]+\.md)\)'`,
  over `wiki/index.md`; the result is diffed both ways into `in_index_not_on_disk` (stale index
  entries) and `on_disk_not_in_index` (files missing from the index), with `overview.md` excluded
  on both sides as a known meta-page. This is the only deterministic index-vs-disk reconciliation
  found in the survey.
- **Log coverage.** Extracts `## [YYYY-MM-DD] ingest | Title` headings from `wiki/log.md` and
  reports source pages lacking one.
- **Exit codes: no failure path.** `tools/health.py` and `tools/lint.py` contain no `sys.exit` and
  no failing return — they print a report (`--json` for machine-readable) and exit 0. `--save`
  writes the report to `wiki/health-report.md`. So, as with A2's checker, **none of this can be
  used as a gate without editing it.** (`tools/refresh.py` does `sys.exit(1)` on three error
  conditions — missing args, a bad page argument — but those are usage errors, not validation
  failures.)

### Staleness: the one hash-based mechanism found

`tools/refresh.py` docstring, verbatim: "Refresh stale source pages by re-ingesting from raw
documents. ... Compares raw document hashes against stored hashes to detect changes. Re-ingests
changed documents to update wiki/sources/ pages with accurate facts." The cache is
`graph/.refresh_cache.json` (a `sha256` helper lives in `tools/_utils.py`). This is the only place
in the survey where a staleness question is answered by comparing a stored hash rather than by an
LLM's judgement — and it detects "the raw source changed", not "a claim was falsified", so it does
not close failure mode 1.

### Contradiction handling: annotate, never rewrite — the same choice A2 made

`CLAUDE.md` defines a per-page section, in the Source Page Format, verbatim:

```
## Key Quotes
> "Quote here" — context

## Connections
- [[EntityName]] — how they relate
- [[ConceptName]] — how it connects

## Contradictions
- Contradicts [[OtherPage]] on: ...
```

So **verbatim quotes get their own section, separated from the agent's `## Summary` and
`## Key Claims`** — the fact/synthesis boundary is at least a structural convention here, though
nothing checks that a `## Key Quotes` entry actually appears in `raw/` (unlike A2, which does check
it mechanically). And a contradiction becomes a bullet pointing at the other page. The Ingest
Workflow's step 8 states the rule, verbatim: "Flag any contradictions with existing wiki content".
Nothing in `CLAUDE.md` says which of the two conflicting claims is wrong, or that either should be
edited; the prescribed end state is two pages that each name the other.

The same is true of the Lint workflow, which is **prompt-driven, not the script**: `/wiki-lint`
tells the agent to "Use Grep and Read tools to check for: ... **Contradictions** — claims that
conflict across pages; **Stale summaries** — pages not updated after newer sources". `tools/lint.py`
exists and does call an LLM (`from tools._utils import ... call_llm`), but the slash command that
the user actually invokes is the prose workflow in `CLAUDE.md`. This is a real trap for anyone
reading the repo: **the file named `lint.py` is not what the documented lint workflow runs.**

### Index currency and the log

The index is hand-written by the agent and reconciled afterwards by `health.py` (above) — the same
architecture as A2, with the difference that A2's lint pass is allowed to *repair* index entries
while SamurAIGPT's tool only *reports* them. `CLAUDE.md` gives the index format explicitly
(verbatim): `## Sources` / `- [Source Title](sources/slug.md) — one-line summary`, grouped
Overview / Sources / Entities / Concepts / Syntheses. The log format is the same greppable idea as
A1's, verbatim: "Each entry starts with `## [YYYY-MM-DD] <operation> | <title>` so it's
grep-parseable", with operations `ingest`, `query`, `health`, `lint`, `graph`.

The Ingest Workflow ends with a step that is a *validation trigger*, verbatim: "10. **Post-ingest
validation** — check for broken `[[wikilinks]]`, verify all new pages are in `index.md`, print a
change summary". This is the closest thing in the survey to a write-time check: it runs at the end
of one documented workflow, executed by the agent as prose, with no script behind it and no
failure signal.

### Validation trigger, and writes made outside the agent's tools

- Triggers are five Claude Code slash commands, defined as markdown files in `.claude/commands/`
  (`wiki-ingest.md`, `wiki-query.md`, `wiki-lint.md`, `wiki-health.md`, `wiki-graph.md`) — invoked
  by the user, one at a time. **The repo's `.claude/` contains `commands/` and nothing else: there
  is no `settings.json`, therefore no hooks, so nothing in this project fires on a write.**
- The only automated timing is **`docs/automated-sync.md`**, which is a cron/launchd recipe "for
  local Mac/Linux environments": a shell script that loops `python3 tools/ingest.py "$file"` over
  every markdown file under `raw/` and then runs `python3 tools/heal.py`. It is a nightly batch,
  and it is macOS/Linux-only. Nothing here is Windows-shaped.
- **Writes made outside the agent's tools are not addressed at all.** The `tools/` scripts are the
  one place a shell-driven write *would* be seen — but only if someone runs `health.py`. There is
  no watch, no hook, no post-write check anywhere in the repo.

### Coverage of the five failure modes

Falsified claim left in place (1): **actively reinforced** — contradictions become
`## Contradictions` bullets on both pages. Supersession (2): partial, via `last_updated`
frontmatter plus `refresh.py`'s hash check, which detects a changed *source* not a superseded
*claim*. Index rot (3): **best-in-survey deterministic check** in `health.py`, report-only.
Dead-end stubs (4): a `< 100`-character stub *detector* exists, intended for truncated writes; no
rule requires a withdrawn finding to be reduced to one. Copies drifting (5): not addressed.

## A4 — arturseo-geo/llm-knowledge-base: a schema spec with quarantine and confidence

verified: 2026-09-22

**Maturity, GitHub API 2026-09-22: 40 stars, 6 forks, 0 open issues, MIT, `language: null`,
repo size 52 KB, created 2026-04-05T19:46:27Z, last push 2026-04-06T20:24:35Z.** That is one day of
authorship — the day after Karpathy's gist — and no activity since. **There is no code in the
repository at all** (`language: null`; the tree is markdown only). Its value to this survey is
entirely in the rules it writes down, and every one of those rules is enforced by nothing but the
agent's compliance. Read it as a *rule source*, the same way desk-kbtools A22 read
`agents-md-check`.

**What it is.** Self-description from the API, verbatim: "A schema standard for LLM-compiled
personal knowledge bases. AGENTS.md spec, templates, worked example, spaced repetition learning
layer." Tree, 2026-09-22: `AGENTS.md` (12,235 bytes; header verbatim "`Version: 1.1.0 | Status:
Stable | Last updated: 2026-04-06`"), `CHANGELOG.md`, `CONTRIBUTING.md`, `README.md`,
`templates/topic.md`, `examples/ai-alignment/` (a worked wiki plus `output/reports/lint-report.md`),
and five docs including `docs/contamination-mitigation.md` (10,143 bytes) and
`docs/two-vault-setup.md`.

### Layout, and the authorship split

From `AGENTS.md` §1, verbatim structure: `AGENTS.md` ("agent reads first, always"), `raw/` ("source
material, never edited by agent"), `wiki/` ("LLM-compiled knowledge base (agent owns this)") with
`_index.md`, `_concepts.md`, `_graph.md`, `concepts/`, `summaries/`, `topics/`; then `insights/`
("human-written notes only. Agent never writes here."), `output/`, `learning/`.

§2 states the rule, verbatim:

> The agent is the **sole author and maintainer** of everything under `wiki/`, `output/`, and
> `learning/`. The human never edits these directories directly.
>
> The agent **never modifies** anything under `raw/` or `insights/`. Raw files are immutable source
> inputs. Insights files are immutable human outputs — your own thinking, not the agent's synthesis.
>
> The distinction matters: `wiki/` contains what the LLM compiled. `insights/` contains what you
> actually thought. A summary of a source is noise. An insight you formed from reading it is
> signal. These belong in different directories with different authorship rules.

That is the survey's cleanest answer to failure mode 5, and note what kind of answer it is: it does
**not** detect a fact copied in two places. It **forbids one of the two copies from existing**, by
separating agent synthesis from human notes into directories with disjoint authorship. The failure
mode is prevented by construction rather than checked.

### Quarantine: the strongest contradiction mechanism found

From `docs/contamination-mitigation.md`, verbatim:

> When a contradiction is detected between two articles or between an article and a source, the
> linting workflow adds a quarantine flag:
>
> ```yaml
> status: quarantined
> quarantine_reason: "Contradicts claims in summaries/source-b.md re: claim X"
> quarantine_date: YYYY-MM-DD
> ```
>
> Quarantined articles:
> - Are excluded from `_graph.md` until the contradiction is resolved
> - Are marked visibly in `_index.md` with a `[quarantined]` tag
> - Are not used as sources for new article synthesis
> - Appear as the first items in the next linting report

The three named resolution paths, verbatim: human decides which is correct and the agent updates
and clears the flag; "The contradiction is real and both claims are valid in different contexts →
agent splits the article or adds a nuance section"; "One source supersedes the other → agent
updates confidence and provenance."

Why this matters here: **quarantine is the only mechanism in the survey that stops a suspect claim
from propagating into new pages.** Every other system annotates the contradicting pair and leaves
both readable. Quarantine adds a *downstream* consequence — the page may not be cited by anything
else — which is the same shape as the pilot project's "a current finding may not depend on a withdrawn
one". The cost is that it is a whole-page flag, not a claim-level one, and it is still applied by
an LLM's judgement with no validator behind it.

### Confidence: a four-level field with published criteria, and a graph consequence

Verbatim criteria table from the same doc:

| Level | Criteria |
|---|---|
| `high` | 2+ primary sources, no contradictions detected, recently linted |
| `medium` | 1–2 sources, or sources older than 6 months, or minor ambiguities |
| `low` | Single source, or thin coverage, or imputed from web search |
| `speculative` | Agent-inferred, no direct source, or contradictions unresolved |

Verbatim consequences: "**Speculative articles are not linked into the graph.** The `_graph.md`
adjacency list only includes articles with `confidence: medium` or above. This prevents speculative
content from propagating through backlinks into otherwise well-sourced articles." And: "Confidence
can only be **upgraded** when new sources are added or contradictions resolved — never
automatically." The lint pass downgrades on staleness (>6 months), `< 2` sources, or a detected
contradiction.

### Citation traceability and file-back discipline

Verbatim: "Every claim in a wiki article must trace to a file in `raw/` or be explicitly marked",
with the two escape hatches, verbatim: "`source: web-imputed` # filled in by agent using web search
during linting" and "`source: agent-inferred` # logical inference from other wiki articles, no
direct source". The agent must supply either a citation, a web-imputed marker with the query used,
or an agent-inferred marker with the reasoning chain.

And the circular-citation control, verbatim: "The most common contamination path is this: you run a
query, get a useful answer, and ask the agent to file it back into the wiki. The answer was
generated by the LLM reasoning over wiki articles — it's not a new source, it's a synthesis. If it
gets filed as an article with `confidence: high`, you've created a circular citation: the wiki
citing itself." The fix is a `filed_back: true` / `filed_back_to: [...]` pair plus three
obligations on file-back: mark the touched article `source: agent-inferred`, cap its confidence at
`medium` "regardless of the agent's confidence in the answer", and add the note "This section
synthesised from query output YYYY-MM-DD-slug.md — not directly sourced".

This is the single most transferable idea in A4 for a provenance-bearing corpus: **assertion type
is a required field, and "the agent inferred this" is a legal value rather than a hidden
assumption.**

### The worked example is thin, and the provenance claim is secondhand

The `examples/ai-alignment/` wiki is one topic page and one concept page plus reports — enough to
show the format, not enough to test the schema at scale. Two claims in the docs are **UNVERIFIED**:
the attribution that the two-vault model and the insight/noise distinction were "articulated by
Steph Ango (@kepano), co-creator of Obsidian, in response to Andrej Karpathy's April 2026 LLM
Knowledge Bases post" — note it names an "LLM Knowledge Bases post" rather than the `llm-wiki`
gist in A1, so there may be a second Karpathy artifact this survey has not located, or the
attribution of the title is loose. Not checked against a primary source.

### Coverage of the five failure modes

Falsified claim left in place (1): **better than every other candidate** — quarantine removes the
page from the graph, from the index's normal entry, and from use as a source, though the claim text
itself stays where it is. Supersession (2): the resolution path "one source supersedes the other →
agent updates confidence and provenance" is a rewrite, not an append, and the superseded *source*
stays in `raw/`. Index rot (3): `_index.md` is still hand-maintained; the `[quarantined]` tag is
written by the LLM, and nothing checks index-vs-disk. Dead-end stubs (4): not addressed. Copies
drifting (5): prevented by construction via the `insights/` authorship rule, not detected.

## A5 — Rost Glukhov, "LLM Wiki Maintenance: Drift, Contradictions and Review"

verified: 2026-09-22

**Identity.** `https://www.glukhov.org/knowledge-management/knowledge-systems-architectures/compiled-knowledge/llm-wiki-maintenance-knowledge-drift/`,
subtitle verbatim "Keep compiled knowledge trustworthy". Publication stamp from the page's own Open
Graph metadata: `og:published_time 2026-07-16T19:43:56+10:00` (direct fetch of the page HTML with
`curl`, then tag-stripped to text, 2026-09-22; the page is ~26.7 KB of text).

**What it is, stated plainly.** A single-author practitioner article — a checklist and taxonomy,
**not an evaluation and not a tool**. There is no data, no measurement, and no implementation in
it. It is the best available statement of *what a linter for this problem should check*, in the
same way desk-kbtools A22 used `agents-md-check`'s rule list. Treat every claim in it as informed
opinion.

### Why it is worth reading at all

It opens on exactly the pilot project's problem, verbatim:

> An LLM Wiki has a different failure mode. **It can look clean even when it is wrong.**
>
> The pages may be well formatted. The links may work. The summaries may sound balanced. But
> underneath that neat surface, the system may have dropped critical facts, merged incompatible
> concepts, cited summaries instead of sources, or **preserved an old decision as if it still
> applied**.

And it names the remedies as questions a maintained wiki must be able to answer, verbatim: "What
sources support this claim? When was this page last reviewed? ... Are there conflicting pages? Is
this summary still current? Did the agent rewrite more than it should? Can we roll back a bad
update?"

### The drift taxonomy — six named kinds, each with its own check

This is the most useful part, because it separates failure modes the pilot project has been treating as
one. Headings verbatim, with the operative sentence and the prescribed responses:

- **Source drift** — "the underlying source material changes ... The old wiki page may still be
  accurate for the previous version, but wrong for current use." Response: record source dates and
  last-reviewed dates, mark version-specific pages, link old pages to superseding pages.
- **Concept drift** — "the meaning of a term changes over time ... A wiki can accidentally preserve
  several meanings of the same term without explaining the difference." Response: glossary pages,
  "meaning in this wiki" sections, split overloaded pages.
- **Terminology drift** — several names for one thing ("LLM Wiki", "compiled knowledge base",
  "AI-maintained wiki"). Response: canonical page names, aliases in front matter, "Lint for
  near-duplicate titles".
- **Decision drift** — "a past decision remains documented but no longer reflects current
  practice", with the sentence that matters here, verbatim: "a decision page is only trustworthy if
  superseded choices are marked as superseded rather than **silently overwritten**." Response:
  "Mark decisions as proposed, accepted, superseded, or rejected ... Preserve historical context."
- **Citation drift** — "a page cites a source, but the claim no longer matches what the source says
  ... This can happen after a rewrite. The citation remains in place, but the sentence around it
  changes. The page still looks sourced, yet the citation no longer supports the claim." The
  article calls this "one of the most serious LLM Wiki failure modes because it creates false
  confidence."
- **Structure drift** — "New pages are created instead of updating old ones. Index pages fall
  behind. Duplicate pages appear. Orphan pages accumulate."

Citation drift is the one the pilot project has not separately named: it is the case where the evidence
citation survives a rewrite but stops supporting the sentence next to it, and neither A2's checker
nor SamurAIGPT's `health.py` can see it — A2's checker verifies that literals appear in `raw/`, not
that the sentence around the literal still means what it did.

### The two check lists, verbatim, split structural from semantic

Structural (automate first): "Broken internal links; Orphan pages; Duplicate titles; Missing source
sections; Missing review dates; Pages without backlinks; Empty or placeholder sections; Very long
pages without section structure; Pages with no incoming links from an index; Inconsistent naming
conventions; Invalid front matter; Stale "current" pages older than a review threshold." Its own
caveat, verbatim: "These checks do not prove the knowledge is true. They prove the wiki is still
maintainable."

Semantic: "Claims without direct source support; Two pages making incompatible claims; Old
decisions presented as current; Duplicate concepts with different names; One concept page mixing
several meanings; Summaries that omit known constraints; "Best" or "recommended" claims without
criteria; Version-specific claims without version labels; Pages that contradict newer sources."

And the operating rule that decides how such a tool should be wired in, verbatim:

> These checks should not automatically rewrite the wiki. They should usually produce a report for
> review.
>
> The safer pattern is:
>
> **Detect automatically. Explain clearly. Update deliberately. Review risky changes.**

Compare that with every implementation in this survey (A2, A3): all of them landed on
report-only, exit-zero checkers. The practitioner literature reaches the same conclusion
independently. **Nothing in the LLM-wiki ecosystem gates a write** — the gate is a missing pattern,
not a design someone rejected.

### Contradiction detection: compare claims, do not ask "do these contradict?"

Verbatim: "Contradiction detection is not just asking an LLM whether two pages contradict each
other. That can help, but it is too vague. A better approach is to compare claims." The proposed
flow, verbatim: "A[Extract claims from page] --> B[Find related pages and sources] -->
C[Extract claims from related material] --> D[Group claims by subject] --> E[Compare status, date,
version, and scope] --> F{Conflict found?} ... H[Classify conflict] --> I[Create contradiction
report]".

The classification vocabulary — the part worth stealing — verbatim (from the Contradiction Check
Prompt): "Classify each conflict as: real contradiction / version difference / scope difference /
terminology difference / unresolved uncertainty". Justification, verbatim: "many apparent
contradictions are not real contradictions. One page may describe version 1.0 and another version
2.0. One page may describe personal use and another enterprise use. One page may describe the design
goal and another the implementation reality."

The prohibition, verbatim, and it is the strongest sentence in the article:

> Do not let the agent silently resolve contradictions by blending both claims into a vague
> compromise. That creates smooth nonsense.

The contradiction report's required fields, verbatim: "Conflicting claims; Pages involved; Source
references; Date or version context; Likely explanation; Suggested resolution; Whether human review
is required."

### Staleness: a review-date metadata block, and the "lying quietly" rule

Verbatim metadata block:

```
status: current
last_reviewed: 2026-07-10
review_after: 2026-10-10
source_confidence: medium
```

With per-page-type review intervals, verbatim selections: "Tool version pages 30 to 90 days;
Pricing or availability pages 7 to 30 days; Architecture principles 6 to 18 months; Historical
decision records Only when superseded; Source summaries When source changes." And the line that
justifies the field, verbatim: "The interval is less important than the habit. **A page without a
review date is a page that will eventually lie quietly.**"

`review_after` is the one *deterministic, cheap, content-independent* staleness rule in this entire
survey: a validator compares today's date to a field and prints the overdue pages. It does not
prove the page is wrong; it proves nobody has looked. It is weaker than A2's literal check and it
costs almost nothing to implement.

### Git as the review surface

Verbatim: "Git is one of the best maintenance tools for an LLM Wiki. Not because Git is fashionable,
but because generated knowledge needs reviewable change history." The review-listing, verbatim:
"New pages; Deleted sections; Changed claims; Changed citations; Renamed pages; Index updates; Link
changes; Status changes; Broad rewrites." And the single habit it calls most important, verbatim:

> The most important review habit is to inspect deletions. LLMs often remove details while making
> prose cleaner.
>
> Clean prose is not always better knowledge.

Risk-tiered review is proposed with three levels — low (`Broken link fixes`, `Formatting cleanup`,
typos), medium (`Adding source summaries`, `Creating new concept pages`, `Changing page status`),
high ("Deleting claims; Rewriting canonical pages; Changing decision records; Resolving
contradictions; Marking content as current or superseded") and the rule "High-risk changes should
get human review."

### The section that argues against this project's preferred fix

"Archive, Supersede, or Delete" gives a three-way table, verbatim: "Archive — The page is
historical but still useful; Supersede — A newer page replaces the old answer; Delete — The page is
duplicate, empty, wrong, or unrecoverable", with the superseded-page format, verbatim:

```
Status: superseded
Superseded by: wiki/concepts/agent-memory-architecture.md
Reason: This page used an older definition of agent memory before the project separated session
memory, user memory, and compiled knowledge.
```

And a warning against the other direction, verbatim: "The fourth mistake is deleting historical
context. Old decisions, failed experiments, and superseded recommendations can be valuable. Mark
them clearly instead of erasing them."

So this source lands where A2 and A3 landed: annotate, link, keep. **That is the ecosystem's
consensus, and it is the opposite of failure mode 1's requested fix.** One detail in it is still
worth taking, though, and it is the reason the consensus fails in practice: the `Status:` block is
placed at the **top of the page**. A2 puts its `> **Status: Outdated**` block above the contested
claim, mid-document, and A3 puts `## Contradictions` at the bottom. For a reader who arrives by
grep excerpt, only the top-of-file placement is reliably in the excerpt. If the pilot project annotates
instead of rewriting, the marker must be in the frontmatter or the first heading — and that is a
decision for the coordinator, not a recommendation from this desk.

### Coverage of the five failure modes

This is not a tool, so the honest reading is "which failure modes does it supply a checkable rule
for". Falsified claim left in place (1): it supplies the claim-comparison workflow and the
five-way conflict classification, and it explicitly rejects the "blend both claims" resolution; it
does not supply a rule that forces the stale line to change. Supersession (2): supplies the
`status` + `Superseded by:` + `review_after` vocabulary. Index rot (3): supplies "index pages that
do not link to new pages" and "Pages with no incoming links from an index" as checks, no mechanism.
Dead-end stubs (4): **the nearest thing in the survey** — "Empty or placeholder sections" is a
structural check, and "Very long pages without section structure" is a size-related check, but
neither is a "shrink a failed experiment to a dead-end note" rule. Copies drifting (5): only
"Duplicate titles" and "Duplicate concepts with different names"; no cross-file duplicate-fact
detection.

## A6 — The gist's comment thread: the only field evidence found, and it is thin

verified: 2026-09-22

**What this is.** Karpathy's gist carries a very large comment thread. Via
`api.github.com/gists/442a6bf555914893e9891c11519de94f/comments?per_page=100&page=N` on
2026-09-22, the `Link` header reported `rel="last"` = page 12, so the thread holds roughly
**1,100–1,200 comments**. I fetched pages 1–8 (800 comments) and read them; the sample spans
2026-04-04T16:49:23Z (39 minutes after the gist was created) to 2026-05-14T23:36:24Z. The gist
page's own HTML shows comment timestamps well into September 2026, so the thread outlives my
sample; **statements below are drawn from the first 800 comments only.**

This is the closest thing to question 5's field evidence that exists. It is **self-reported and
unverified** — much of it is people announcing their own project, and a long tail is a flame war
about whether an LLM-generated folder deserves the word "wiki". Nothing in the 800 comments is a
measurement; the longest self-reported production run is one month (below), the longest
"been doing something like this" claim is six months, and no one reports a benchmark, a drift
rate, or a before/after.

### The enforcement design: hooks at the agent boundary, not rules in a prompt

User `n7-ved`, 2026-04-13, verbatim:

> Enforcement works best at the agent boundary, not the conversation boundary; Rather than trying
> to block the main conversation from editing the wiki, we let each specialised agent be its own
> enforcement unit. The writer agent's frontmatter excludes Bash and web; a PreToolUse hook on it
> blocks writes to any path outside the four content layers. The maintainer agent has Bash, but a
> PreToolUse hook validates every command (no `rm -rf`, no force-push, etc.). The auditor is
> read-only. The main conversation's write discipline is instructional, it's trusted to respect the
> rule in CLAUDE.md because it's the "planner," not the "executor." Hooks do the heavy lifting on
> the executors. This gives you structural guarantees on the agents that actually mutate things,
> without the friction of locking the conversation itself.

Two things here bear directly on the pilot project's open question about shell-made writes. First, the
answer this practitioner found is **not** to try to catch a `sed` or a heredoc — it is to remove
Bash from the writing agent's toolset entirely, so the bypass does not exist. Second, the
distinction they draw is between the *planner* (instructions are enough) and the *executor* (hooks,
because instructions fail). That is a directly transferable framing.

The same commenter splits claim types, verbatim: "We shipped four claim types as Obsidian
callouts: `Source` (verbatim quote with citation), `Analysis` (our inference from sourced facts,
with reasoning shown), `Unverified` (no authoritative source yet), `Gap` (explicitly missing,
never fill with a plausible guess). The Analysis / Unverified split is the one that earned its
keep. It prevents paraphrasing-bias, where the model rewrites what a source says and nobody can
tell afterwards whether it got it right."

And a mechanical staleness metric, verbatim: "Each file carries a score derived from how far behind
its outgoing wiki-link dependencies it is. Forward-only, no backlink tracking. Update a source,
every downstream file's score ticks up, the auditor surfaces the worst offenders. Replaces a lot of
the 'who might have stale claims about this?' review burden that otherwise falls back on humans."

This is the same graph-distance notion as A2's fingerprint check and Doorstop's suspect links
(desk-kbtools A3), arrived at independently and without a tool. They also added a fourth layer for
schema rationale, verbatim: "Schema-in-CLAUDE.md works until the schema has non-trivial rationale
worth preserving across changes. Then it wants its own records."

### The multi-agent production report

User `redmizt`, 2026-04-12, verbatim:

> We adopted it in April 2026 for a large-scale multi-agent production system (6 specialized AI
> agents running in parallel tabs on Claude Code with Opus, 50+ sub-agents per session) and
> discovered it scales beautifully — but needed extensions for the realities of concurrent
> multi-agent access. ... We pushed it into production and found that the single-user,
> single-agent assumptions break down when you have parallel agents sharing a filesystem. Identity,
> access control, contamination prevention, and concurrency coordination all become first-class
> concerns.

Their 13 extensions, verbatim selections: "Multi-domain wiki architecture — 5 specialized wikis
instead of 1 (rules, domain knowledge, memory, insights, sources), each with different access
cadences and permission models"; "Capability tokens — file-based identity tokens (env vars don't
persist between Claude Code Bash calls — a runtime constraint that drove the entire architecture)";
"Verify Before Assert gate — a UserPromptSubmit hook that enforces reality-checking before any
factual claim. In multi-agent pipelines, one wrong assertion compounds through the dispatch chain.
A 0.2-second verification call prevents 30-minute downstream error cascades."; "Security hook suite
— 8 PreToolUse hooks enforcing access at the tool level, not the prompt level. **Rules-as-text fail
under cognitive load; hooks don't.**"; "Wiki locking — file-pattern-level mutual exclusion with
TTL-based expiry for concurrent editing"; "The Twice Rule — any problem fixed twice gets automated
prevention before a third occurrence."

**Caveat, and it is a real one: the implementation they link is gone.** The comment names
`https://github.com/redmizt/multi-agent-wiki-toolkit`; the GitHub API answered `Not Found` on
2026-09-22. The design survives only as this comment plus a companion gist. Treat the whole report
as a claim by an interested party with nothing left to inspect — but the concurrency problems it
names (parallel agents sharing a filesystem, no persistent identity between shell calls, no
write locking) are structural and would apply to any multi-agent corpus, the pilot project's included.

### The one clear production post-mortem, and the only "shrink it" rule in the survey

User `benjimixvidz`, 2026-04-15, opening sentence verbatim:

> I've been running this pattern in production for a month across 6 projects using Claude Code +
> Obsidian on Linux. **Every append-only wiki eventually becomes the same mess it was supposed to
> replace.**

That is failure mode 2 stated as an observation after a month of real use, and it is the closest
thing in this survey to a degradation report. Their remedy is a compaction discipline, verbatim:

> Every wiki page uses an **Actuel/Archive** pattern: ... When something in "Actuel" changes, the
> old version moves to "Archive" as a one-liner with date. **Nothing is deleted, just compressed.**
>
> Special files have different rules: **state.md**: rewritten every update (it's a snapshot, not
> history); **log.md**: append + compact (sessions > 30 days become one-line-per-week summaries);
> **Other pages**: Actuel/Archive pattern

with explicit per-file line budgets, verbatim: "state.md ~30 [lines] Rewrite; log.md ~60 Append +
compact; architecture.md ~80 Actuel/Archive; decisions.md ~60 Actuel/Archive. Total project wiki:
**~300 lines max. Always.** Even after a year."

**This is the only rule found anywhere in the survey that implements failure mode 4** — a failed or
superseded claim is reduced to a dated one-liner rather than left whole with a correction appended.
It is also a *budget*, which is the property that makes it checkable. Note its own limits, stated by
its author: "I know enough to know that markdown is not a database. That's exactly why the wiki is
kept small and structured with strict rules ... For 6 projects and ~20 wiki pages each, markdown +
git is the right tool. At 10,000 pages, you're right, it breaks." The pilot corpus is ~34k
lines across a handful of files, so the scale is comparable per file, though the budget discipline
would require the files to be split first.

And the hook-granularity finding, verbatim, which answers the trigger question with a negative
result from practice:

> I tried a Stop hook (fires when Claude finishes a response) but it triggered on every response,
> not just session end. Removed it. Manual is better: you decide when the session was meaningful
> enough to persist.

### Content-hash provenance for staleness, proposed the day after the gist

User `Jwcjwc12`, 2026-04-05, verbatim: "The problem I kept hitting: the LLM compiles knowledge from
source files, but the moment those files change, the compiled knowledge might be wrong — and
doesn't know it. Health checks help, but that's just the LLM re-reading and guessing whether
something drifted. So I made provenance structural. **Every proposition (chunk of information)
records which source files produced it and their content hashes at compilation time.** When you
query, it checks whether the files on disk still match. Match = valid. Mismatch = stale."

A reply from `barrygfox` immediately names the hard case, verbatim: "Change in file hash invalidates
all propositions derived from that file?" — i.e. the mechanism has no notion of a *partial* change.
For the pilot project, where evidence files are large and append-heavy, that granularity question is the
whole difficulty, and it is unresolved in the thread.

### The skepticism worth recording

User `laphilosophia`, 2026-04-04, verbatim:

> I think the hardest part is understated a bit: truth maintenance. The appealing part of the
> workflow is that the LLM updates summaries, cross-links pages, integrates new sources, and flags
> contradictions. But that is also exactly where models tend to fail quietly. Bad synthesis, weak
> generalization, **stale claims surviving new evidence, page sprawl, and false consistency can
> accumulate without being obvious.** So for me the risky sentence is effectively "the LLM owns
> this layer entirely."

And `singularityjason`, 2026-04-07, on two unsolved problems, verbatim: "**1. Formation.** 'Every
conclusion goes back to the wiki' is a rule, not a mechanism. ... **2. Retrieval.** Reading
`index.md` works at 20 pages. At 100+ it blows the context window".

Also worth one line for scope: `Vitalii-Ivanov-Rakuten`, 2026-04-10, verbatim — "After reading this
gist and reviewing ~17 implementations linked in the comments, using Claude Code, we built a
team-oriented version on top of this concept" — the same practitioner also built the
always-in-context variant with `@~/Vault/Wiki/index.md` in `CLAUDE.md`, relying on Claude Code's
`@import`.

### Answer to question 5

**No published evaluation or post-mortem with numbers exists in anything found.** What exists is
one-month self-reports (benjimixvidz), one production architecture description with a dead repo
(redmizt), and two design reports (n7-ved, Jwcjwc12). Every one of them independently concluded
that prompts are insufficient and that enforcement has to move to hooks, tool restriction, or
deterministic scripts — and **not one of them built the write-gate for a knowledge base that the
pilot project is considering.** The nearest published work is the academic literature in A9.

## A7 — nvk/llm-wiki (llm-wiki.net): the most mature system, and the only one with hooks

verified: 2026-09-22

**Maturity, GitHub API 2026-09-22: 1,334 stars, 125 forks, MIT, language Python, repo size 1.2 MB,
created 2026-04-04T23:52:25Z (the day Karpathy posted), last push 2026-09-15T14:48:29Z.** Of
everything in this survey this is the one with sustained development and real tests. API
description, verbatim: "LLM-compiled knowledge bases for any AI agent. Parallel multi-agent
research, thesis-driven investigation, source ingestion, wiki compilation, querying, and artifact
generation."

**Identity, confirmed rather than assumed:** `https://llm-wiki.net/` links to
`https://github.com/nvk/llm-wiki` (direct fetch of the site's HTML, 2026-09-22), so the product site
and the repo are the same project. The site's own copy, verbatim: "Ships as a Claude Code plugin, an
OpenAI Codex plugin, an OpenCode instruction file, or a portable AGENTS.md. Obsidian-compatible."

**Scale, from `git/trees/HEAD?recursive=1` (1,487 entries, 2026-09-22):** `AGENTS.md` 60 KB,
`README.md` 54 KB, `claude-plugin/skills/wiki-manager/SKILL.md` 27.6 KB, a 43 KB
`references/linting.md`, a 24.5 KB `references/wiki-structure.md`, 17.1 KB `references/ingestion.md`,
a 243 KB bundled CLI (`bin/llm-wiki`, duplicated into three plugin directories), a
`plugins/llm-wiki/hooks/` directory, a `benchmarks/` tree with JSONL cases, and a `tests/` directory
including a 33 KB `test-local-cli-lint.sh`.

**This is a system, not a pattern sketch.** Everything below was read from the raw files named;
where I did not verify something I say so.

### It ships real hooks — and they record, they do not validate

`plugins/llm-wiki/hooks/hooks.json` (4,244 bytes, direct fetch 2026-09-22) registers the same
command under six events: `SessionStart`, `UserPromptSubmit`, **`PostToolUse` with
`"matcher": "*"`**, `PreCompact`, `PostCompact`, and `Stop`. Every entry runs
`hooks/llm_wiki_session.py` (67,665 bytes) with `hook --harness codex --if-enabled`, a 5-second
timeout, and a status message.

What those status messages say the hooks do, verbatim from the file: "Loading llm-wiki session
context", "Checking llm-wiki session context", "Recording llm-wiki session event", "Capturing
llm-wiki pre-compact session context", "Saving llm-wiki session checkpoint".

**So the answer to "does anyone hook every write" is: yes, this one does — and the hook is
telemetry, not a gate.** There is no validator in the hook path. It is worth stating plainly
because it is the closest anyone in this survey comes to the design under consideration: the
mechanism exists in the wild, and the use it is put to is session-context capture.

The hook file is the Codex variant (`--harness codex`); a Claude Code variant may exist elsewhere
in the tree. I did not verify one. `claude-plugin/` in the tree listing shows
`.claude-plugin/plugin.json` and no `settings.json` — **UNVERIFIED whether the Claude Code plugin
registers hooks by another route.**

### Contradiction and retraction: the one place claims get deleted rather than annotated

`claude-plugin/commands/retract.md` (5,615 bytes, direct fetch 2026-09-22) is about removing
sensitive data or a named source, not about falsified claims — but two of its rules are the
strongest statement in the survey of what should happen to a claim whose support is gone, verbatim:

> 4. **Delete claims supported only by that source. Rewrite a claim only when the remaining sources
>    independently support it.**

and, on scope, verbatim: "Remove its frontmatter, link, citation, index, output, and session
references."

That is claim-level provenance granularity plus a delete-not-annotate policy — the opposite of
A2's "Never silently rewrite history" and A5's "Mark them clearly instead of erasing them". Where
A6's `benjimixvidz` compresses to a dated one-liner, this deletes outright when nothing else
supports the claim.

Retraction is also the only operation in the survey with a **machine-checkable completion status**,
verbatim: "The command is dry-run by default. `--apply` is the only mutation switch. After
applying, it scans the same scope again and **returns a nonzero status when matches or technical
failures remain.**" And: "The final status is `verified` only when the selected local scope has no
remaining matches and no technical failure. Otherwise it is `incomplete` with specific next
actions."

The matching itself is deterministic and worth noting as a technique, verbatim: "The default
`common` variant mode checks exact bytes plus common JSON, URL, base64, URL-safe base64, and hex
forms. `--variants exact` limits matching to the exact value. Text files are atomically rewritten
with `[RETRACTED]` and matching path names are renamed."

Also relevant to the shell-write problem: the command's own frontmatter restricts the tools it may
be run with — verbatim `allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*),
Bash(date:*), Bash(rm:*), Bash(grep:*), Bash(scripts/llm-wiki:*)`. A per-command shell allowlist is
a second mechanism (after A6's `n7-ved` removing Bash from the writer agent) for bounding writes
made outside the editor tools.

### The lint system: ~20 numbered checks, each with a severity and an auto-fix policy

From `claude-plugin/skills/wiki-manager/references/linting.md` (43,218 bytes) and
`claude-plugin/commands/lint.md` (13,815 bytes), both direct fetches 2026-09-22. The design
principles, verbatim:

> **Lint rules are the schema.** There is no `/wiki:migrate` command and there should never be one.

> **Mechanical layer (C11/C12/C13)** — raw-source and wiki-article placement and frontmatter schema.
> Fully auto-fixable because the canonical location and field shape are pure functions of
> frontmatter. No judgment required.

> A `raw/` or `wiki/` file's correct path is a pure function of its frontmatter. Misplacement is a
> structural defect regardless of whether the cause was user error or an old wiki layout.

Severity vocabulary is three-level — `**Critical**`, `**Warning**`, `**Suggestion**` — and each
check carries an explicit disposition. The auto-fix boundary is stated once, verbatim, and it is
the same boundary every other system in this survey drew:

> IMPORTANT: Only auto-fix issues with clear, unambiguous fixes — missing index entries, dead index
> links, broken stats, legacy `_project.md` → `WHY.md` migration (C8c), stale `output/_index.md`
> when `projects/` exists, ... files in the wrong canonical `raw/` or `wiki/` directory (C11),
> ... **Do NOT auto-fix content quality issues. ... Do NOT rewrite article bodies except for
> explicitly requested recompilation.**

The dispatcher rule, verbatim: `**Critical**` — architecture violations and broken references;
`**Warning**` — surface, suggest, never act; `**Suggestion**` — human re-evaluates, "never
auto-fixed".

Three mechanisms bear directly on the pilot project's failure modes.

**Index rot: indexes are derived caches.** Verbatim from `linting.md`: "Indexes are already derived
caches (see `indexing.md` Derived Index Protocol) — this principle extends to file placement and
frontmatter shape", and on the consequence of a move, verbatim: "After any move, the containing
indexes on both sides are invalidated and **will rebuild on next read** per the Derived Index
Protocol." A hand-maintained index that can go stale is replaced by a cache recomputed on read.
This is the strongest structural answer to failure mode 3 in the survey — stronger than A2's
reconciliation check, because there is nothing to reconcile.

**Staleness by following the provenance chain.** Check C8b, verbatim: "compute staleness by
following each member file's `sources:` chain ... If any raw source has an `ingested:` newer than
the member's `updated:`, flag the project (suggestion — human re-evaluates, never auto-fixed)", and
from `linting.md`: "Report as: `Project <slug> may be stale: N source(s) newer than member
artifacts.` Never auto-fixed — staleness triggers human re-evaluation, not automatic regeneration."
Companion check C18 catches articles with no `sources:` at all, verbatim: "Wiki articles that lack
`sources:` in their frontmatter — or carry an empty list — cannot have their source-chain integrity
scored, which leaves them stuck near the freshness floor regardless of how recently they were
verified or compiled." There is an escape hatch, verbatim: "The exemption is
`compiled-from: conversation` — articles whose evidence is the conversation that authored them
rather than fetchable raw files."

That is the same mechanism as desk-kbtools A3's Doorstop stamp and A6's `Jwcjwc12` hash scheme,
implemented here as **dates plus `sources:` edges**: it detects "the evidence underneath this claim
changed", not "the claim is false".

**Reference resolution and dangling sources.** Verbatim: "All `sources:` entries in wiki article
frontmatter point to existing raw files (no dangling references to deleted/retracted sources)", and
the failure policy, verbatim: "If exact path resolution fails but slug fallback resolves to exactly
one raw file, rewrite to that exact `raw/...md` path. If resolution still fails or is ambiguous,
warn for human review; **never auto-remove provenance entries**."

**Schema migration without rewriting history.** Verbatim, and this is a genuinely different idea:
"**Renamed a frontmatter field?** Append an entry to C13's alias table (old → new). Never remove
old aliases. **Changed an enum value?** Add a value alias in C13. Never remove old values." Old
field and enum names stay valid forever as aliases, so a rename never invalidates an older page.
For a corpus where agents read excerpts written at different times, that is a cheap way to avoid
one class of drift.

**The report's own rule about reports**, verbatim, and it is the kind of discipline the pilot project's
answers files already follow: "**Lead every user-visible line with a plain-English description of
what happened — never with a check code (C1, C8c, etc.).** Check codes are internal identifiers for
developers."

And the log entry, verbatim: "Append to `log.md`: `## [YYYY-MM-DD] lint | N checks, N critical, N
warnings, N suggestions, N candidates, N auto-fixed`" — the same greppable log convention as A1,
A2, and A3.

### Verification status of the trigger question

There is a bundled 243 KB CLI and a 33 KB test file for its lint subcommand
(`tests/test-local-cli-lint.sh`), which suggests the lint checks are also runnable outside the
agent. **I did not read the CLI's source or its test file, so I cannot state its exit-code
behaviour.** What is verified: the documented lint workflow is the slash command `/wiki:lint` with
a `--fix` mode ("`/wiki:lint --fix` heals both, idempotently"), invoked by a human or an agent, not
by a hook. On the question the request marks as mattering — whether validation is triggered on a
write — the answer for the pilot project is **no: the write-time hook exists and does not validate.**

### Coverage of the five failure modes

Falsified claim left in place (1): **the strongest stance in the survey** — `retract` deletes
claims whose only support is gone, and rewrites only when the remaining sources independently
support them; but this is coupled to source removal, not to contradiction detection. Supersession
(2): alias tables for fields and enum values, plus the archive/lifecycle machinery in
`references/archive.md` (not read in full — UNVERIFIED). Index rot (3): **solved by construction** —
derived caches that rebuild on read. Dead-end stubs (4): not found; nothing enforces a size or
shape for a withdrawn item. Copies drifting (5): not addressed; no cross-article duplicate-fact
detection was found in `linting.md`'s check list.

## A8 — knowledgebase_guardian, and the also-rans checked and rejected on maturity

verified: 2026-09-22

### knowledgebase_guardian — asked for by name; a 2023 proof of concept, dead for three years

`https://github.com/datarootsio/knowledgebase_guardian`. **GitHub API 2026-09-22: 23 stars, 4
forks, 4 open issues, MIT, Python, created 2023-06-27T15:31:25Z, last push 2023-07-14T14:18:56Z.**
Nothing has landed in more than three years. It predates the LLM-wiki pattern entirely and is not
part of it.

README, verbatim: "Welcome to the KnowledgeBase Guardian, an LLM-powered solution to **keep your
knowledge base consistent and free of contradictions**! How, you ask? Well, every time you want to
add new information to your knowledge base, the Guardian will check that it does not conflict with
information that is already contained in there."

The mechanism, verbatim: "Before adding a document, we first retrieve the most semantically similar
documents in the vector store. We then use an LLM to compare the documents and search for
contradictions: If no contradiction is detected, the document is added to the vector store. If a
contradiction is detected, the document is not added and we keep a log of the failed attempt."

Its own status statement, verbatim: "Keep in mind that this repo acts as a Proof of Concept and not
as a full-fledged knowledge base management system." Prerequisites are Linux or macOS, Python 3.9+,
and an OpenAI or Azure OpenAI account with deployed embedding and LLM models. Storage is a FAISS
vector store of `.txt` chunks, not markdown pages.

**Why it does not transfer.** Three reasons, in descending order of importance. It is a **rejecting
gate at ingest**, so a contradicting document never enters — which is the one design the pilot
project cannot use, since the new finding is often the correct one and the old one is the error. It
**rewrites nothing and preserves nothing**: the document is refused, and the record is a line in
`contradictions.log`. And it asks an LLM to decide contradiction by semantic similarity, which is
exactly the operation A9's measurements show is near-chance (AUROC 0.59) for this class of
judgement.

Its one transferable idea is the shape of the trigger: a **script run before a write that can
refuse it**, with the refusal logged. That shape is a gate; the decision inside it is the weak part.

### Also-rans, checked so nobody re-checks them

Every entry below was verified via the GitHub API on 2026-09-22. All are derivations of A1's
pattern; none is a knowledge-base validator. Named here so a later sweep does not re-pay for
finding them.

| repo | stars | created | last push | license | what it is |
|---|---|---|---|---|---|
| `nvk/llm-wiki` | 1,334 | 2026-04-04 | 2026-09-15 | MIT | A7 — the mature one |
| `SamurAIGPT/llm-wiki-agent` | 3,559 | 2023-04-21 | 2026-09-21 | MIT | A3 |
| `Astro-Han/karpathy-llm-wiki` | 2,334 | 2026-04-05 | 2026-07-23 | MIT | A2 |
| `arturseo-geo/llm-knowledge-base` | 40 | 2026-04-05 | 2026-04-06 | MIT | A4 — spec only, no code |
| `qhuang20/obsidian-skills` | 30 | 2026-04-15 | 2026-04-15 | none | Claude Code plugin, "First skill: llm-wiki", injects the pattern via a **`SessionStart` hook** when CWD is inside an Obsidian vault (a hook used for *context injection*, not validation) |
| `IlyaGorsky/memory-toolkit` | 13 | 2026-04-09 | 2026-04-19 | MIT | session-memory plugin; `PreCompact` hook saves state before compaction, a Haiku watcher extracts decisions every 3 min into `notes/`, `docs-reflect` routes findings to `.claude/rules/<domain>.md` "with explicit confirmation"; author's rule, from A6: "nothing writes without your approval" |
| `akash-r34/llm-project-wiki` | 9 | 2026-04-12 | 2026-04-12 | none | a bootstrap prompt plus Obsidian template, one day of authorship, no code |
| `schladt/mb-agent-rules` | 1 | 2026-04-06 | 2026-09-22 | none | memory bank kept identical across four coding agents; detects and replaces a stale byte-identical `CLAUDE.md` copy of `AGENTS.md` — a duplicate-**file** detector, not a duplicate-fact detector |
| `supachai-j/llm-wiki-101` | 1 | 2026-05-09 | 2026-05-22 | none | a bilingual course about the pattern; not a tool |
| `redmizt/multi-agent-wiki-toolkit` | — | — | — | — | **`Not Found` on 2026-09-22**; the multi-agent report in A6 links to a repository that no longer exists |

Named in the thread but **not checked** (one search each, no verification): `emipanelliok/engram`,
`waydelyle`'s SwarmVault, `paulmchen`'s Synthadoc Community Edition, `tashisleepy/knowledge-engine`,
`jurajskuska`'s NONO_AIAGENT, `xoai/sage-wiki`, `kfchou/wiki-skills`, `luna-prompts/skillnote`,
`dkushnikov/mnemon`. All are self-announced in A6's thread and none was fetched; treat their
existence as UNVERIFIED.

### The category-wide pattern, restated because it holds for every entry above

Of the ten systems whose source was actually read (A2, A3, A4, A7, plus the checked-and-rejected
set), **not one gates a write**. Two ship hooks and use them for context injection and session
recording. One (A7) has a nonzero-exit validation path, and it is on a data-retraction command, not
on the wiki's content checks. Every deterministic checker found — A2's `check_evidence.py`, A3's
`health.py`, A5's proposed checks — is report-only.

## A9 — The measurement literature, 2026: what actually works for stale facts

verified: 2026-09-22. All four abstracts were fetched from `arxiv.org/abs/<id>` and quoted directly;
each paper's own submission date is given. These are preprints — none is reported here as
peer-reviewed, and the single-author ones carry claims larger than their evidence.

### A9.1 — Yadav, "Temporal Validity in Retrieval Memory" (arXiv:2606.26511, submitted 2026-06-25)

This is the paper that answers the "does anything measured actually fix failure mode 1" question.
Abstract, verbatim:

> When a fact changes (e.g., a function is renamed or API restructured), RAG retrieves both the
> stale and current value with near-identical embedding similarity. The agent then either abstains
> or serves the superseded fact. We show this is a structural problem: on a calibrated dataset,
> cosine similarity distinguishes a contradicted fact from a duplicated one with **AUROC 0.59 (near
> chance)**, as contradictions are often more embedding-similar to the original than rephrased
> duplicates.
>
> We present MemStrata, a retrieval memory maintaining temporal validity. It stores facts like RAG,
> preserving static recall, but when a fact's value is contradicted, **a deterministic (subject,
> relation, object) supersession rule retires the stale value in a bi-temporal ledger — with no
> similarity threshold and no LLM call.** Across six benchmarks run locally with a 7B model,
> MemStrata ties RAG on static knowledge and reaches 0.95–1.00 accuracy on evolving knowledge
> (where RAG reaches 0.20–0.47). The central result is the stale-fact-error rate: when required to
> answer, **RAG serves superseded values 15–40% of the time; MemStrata drives this to ~0%**, a
> failure class RAG cannot avoid. MemStrata achieves this at retrieval latency (~2.1s) versus
> ~16–18s for LLM-reranking baselines.

Two consequences for the pilot project, and they are opposite in sign. The good news: the mechanism that
works is **deterministic, symbolic, and LLM-free** — the same conclusion A6's practitioners reached
independently, and the same class of mechanism as Doorstop's stamp (desk-kbtools A3) and A2's
literal check. The bad news: it works on **atomic (subject, relation, object) triples**. A hardware
finding is prose with embedded measurements, so the supersession rule has to be supplied by an
author, not inferred.

### A9.2 — Yadav, "Temporal Validity on Real Software Histories" (arXiv:2608.20685, 2026-08-21)

The follow-up, and its most useful sentence is its scope limitation. Abstract, verbatim:

> From 707 real GitHub issues (SWE-bench Lite + Verified) we extract 130 clean atomic state
> transitions, a fix that changes one identifiable value from a pre-fix to a post-fix form, and
> render each marker-free (the stale and current statements differ only in the value). On this set,
> MemStrata reaches 0.91 answer accuracy versus RAG's 0.57–0.59; and, the structural result, when
> forced to answer **RAG serves the superseded value 36–38% of the time** (an LLM reranker does not
> help) while MemStrata drives this to ~0.
>
> We are explicit about scope: **only ~18% of real fixes are clean atomic transitions**; Paper 2
> isolates the memory mechanism on that class, and extraction coverage of the remaining fixes is
> the orthogonal problem we defer to follow-on work.

That 18% figure is the load-bearing caveat for anyone planning to build this. A deterministic
supersession layer handles one fifth of real changes; the rest need extraction that the paper does
not supply.

### A9.3 — Ding, Nannapaneni, Liu, Zhang, "Always-On Agents: A Survey of Persistent Memory, State, and Governance in LLM Agents" (arXiv:2606.30306, 2026-06-29)

The survey's finding is the academic form of the pilot project's problem statement. Abstract, verbatim:

> Across a 435-work coded corpus, treated as a scoped map rather than an exhaustive census, **the
> literature concentrates more heavily on accumulating and retrieving state than on governing,
> recovering, or relinquishing it.** We therefore introduce the Always-On Evaluation Protocol
> (AOEP-v0), a pilot evaluation contract that makes these governance requirements concrete by
> **scoring state mutation and recovery obligations rather than answer quality alone.**

Its diagnostic axes for any state item — verbatim "authority, scope, mutability, provenance,
recoverability, and actionability" — and its lifecycle — verbatim "state is written, validated,
organized, retrieved, acted upon, updated, forgotten, audited, and sometimes rolled back" — are a
usable checklist vocabulary for a spec, and the "concentrates on accumulating rather than
relinquishing" sentence is the best single citation for why the pilot project's fifth failure mode has
no off-the-shelf fix. I did **not** read the full paper or verify the 435-work figure; it is the
authors' claim.

### A9.4 — Liu, "Silent Failure in LLM Agent Systems: The Entropy Principle" (arXiv:2606.08162, 2026-06-06)

Single-author, very large claims, and the largest self-reported scale in the literature found.
Abstract, verbatim:

> Through systematic analysis of over 40,000 controlled trials and long-term production
> observations spanning 100,000+ agent interactions, we identify a common structural logic
> underlying these failures. ... whenever a sufficient subset of these properties co-exist, system
> entropy — the measurable accumulation of disorder: loss of output consistency, task accuracy, and
> cross-session coherence — **increases monotonically with interaction rounds.** We formalize this
> as the Entropy Principle: S(t) = S0 * e^(alpha * t), with alpha measured empirically across
> multiple architectures.

**Treated with caution.** This is a preprint with a formal-law claim from one author and no
independent replication found; the specific numbers quoted earlier in this survey's searches (a
"42% drop in task success rate", "3.2x increase in human interventions") come from a *different*
paper (arXiv:2601.04170, "Quantifying Agent Drift in Multi-Agent LLMs") that I did **not** fetch and
therefore do not report here. What is worth carrying forward is only the qualitative shape, which
A6's practitioner reports match: degradation grows with interaction count and does so without an
error signal.

### What the literature does not contain

No paper found measures **a markdown LLM-maintained wiki** under multiple writing agents — not
contradiction counts, not drift rate, not index staleness over time. Every measurement above is
about retrieval memory (vector stores, triples, ledgers) or about agent-task drift, not about a
shared prose corpus that several agents edit. That is the gap in question 5's answer, and it means
the pilot project's own corpus would be the measurement rather than a consumer of one.

## A10 — Coverage of the five failure modes, and the two rankings

verified: 2026-09-22

Read this table as "does the system *prevent or detect* the failure mode, not merely name it".
"Weak" = a rule exists in prose only, with no check behind it, and every system below that is all
prose gets "weak". "Anti" = the system's prescribed behaviour makes the failure mode worse.

The `model` column is the re-classification against **strict rewrite-in-place** (no withdrawn
stubs, no superseded status, no retraction pointers, no "this was wrong" notes); the five
failure-mode columns answer the original question and are unchanged. `incompatible` means the
system retains superseded content and is therefore not a partial fit. Detail and the per-mechanism
verdicts are in **A11**; a system that is `incompatible` can still supply a compatible component,
which is what the `model` note records.

| candidate | model (rewrite-in-place?) | 1 falsified text left in place | 2 supersession | 3 index rot | 4 stub shrink | 5 copies drift |
|---|---|---|---|---|---|---|
| Karpathy gist (A1) | **incompatible** (flags contradictions, never resolves) | weak (flag only, nothing consumes the flag) | weak | no (hand-written index) | no | no |
| Astro-Han (A2) | **incompatible** — "Never silently rewrite history" | **anti** — `> **Status: Outdated**` block keeps the claim, mid-document | weak (annotation) | **yes** — lint reconciles index vs disk, repairs Updated dates | no | no |
| SamurAIGPT (A3) | **incompatible**; its `health.py` is model-neutral | **anti** — `## Contradictions` bullets on both pages | weak (`last_updated` + source-hash refresh) | **yes** — `health.py` index-sync diff, report-only | partial (`STUB_THRESHOLD_CHARS = 100` catches truncated writes, not withdrawn findings) | no |
| arturseo spec (A4) | **incompatible** (quarantine retains the page and its reason) | **best of the prose designs** — quarantine removes the page from the graph, tags it in the index, and bars it as a source; text stays | partial (resolution path rewrites) | no | no | prevented by construction (`insights/` authorship split) |
| Glukhov (A5) | **incompatible** — "marked as superseded rather than silently overwritten" | weak (claim-comparison workflow + five-way classification; explicitly forbids blending) | weak (`status` + `Superseded by:` + `review_after`) | weak (checks proposed) | partial ("empty or placeholder sections", "very long pages without section structure") | no |
| Gist thread (A6) | `n7-ved` **compatible** (no retention component); `benjimixvidz` **incompatible** — its `state.md` rule ("rewritten every update (it's a snapshot, not history)") is the target model, but its Actuel/Archive compaction and its dated one-liner archive are retained markers | weak (`n7-ved`'s dependency-distance staleness score) | weak (the Archive one-liner is itself a retained marker) | no | **incompatible** for Actuel/Archive (a dated one-liner in the same file is retrievable by any grep common to both sections); the per-file line budget alone is compatible | no |
| nvk/llm-wiki (A7) | **incompatible** (`superseded`/`archived` lifecycle, alias table "Never remove old aliases", and `retract`'s `[RETRACTED]` text substitution plus its appended retraction log entry — all retained markers) | **half-compatible** — `retract` deletes claims whose only support is gone, and rewrites only if remaining sources independently support them, but it leaves a marker where the value was and preserves surrounding log history by default | partial (append-only alias tables for field and enum renames; lifecycle/archive machinery not read in full) | **yes, by construction** — indexes are derived caches that "rebuild on next read" | no | no |
| knowledgebase_guardian (A8) | **incompatible and actively harmful** — refuses the new document, so the stale claim is kept and the right one discarded | **anti** | no | n/a | no | no |
| MemStrata (A9) | **incompatible** (bi-temporal ledger is the retention) | **yes, measured** — ~0% stale-fact service vs RAG's 15–40% / 36–38%; but atomic triples only, and ~18% of real changes are of that class | yes (bi-temporal ledger) | n/a | n/a | n/a |

**The two cells that stay empty across the entire survey.** No system detects *prose in one file
asserting another file is wrong*, and no system detects *the same fact stated in two files and
drifting*. The first requires semantics; the second requires either deduplication over prose or an
authorship rule (A4's `insights/` split is the only thing that addresses it at all, and it does so
by forbidding one of the two copies rather than by detecting drift). A4's quarantine and A7's
`retract` are page- and claim-level flags a **human or agent** sets; neither is triggered by
detection.

**The trigger question, answered for the whole category.** No surveyed system validates a
knowledge-base write. What exists instead, verified: A2 — no hook, lint is a user-invoked skill.
A3 — no `settings.json`, five user-invoked slash commands, plus a nightly macOS/Linux cron batch.
A7 — `PostToolUse` with `matcher: "*"` fires on every tool call and its status message is
"Recording llm-wiki session event"; a `Stop` hook saves a session checkpoint. `qhuang20/obsidian-skills`
(A8) uses a `SessionStart` hook to inject context. `IlyaGorsky/memory-toolkit` (A8) uses
`PreCompact`. A6's `benjimixvidz` tried a `Stop` hook and **removed it** because it fired on every
response rather than at session end. The only nonzero-exit validation path found anywhere is A7's
`scripts/llm-wiki retract`. **The mechanism is available in the harness and nobody has pointed it
at knowledge integrity.**

**Writes made outside the agent's tools, answered for the whole category.** No system inspects
them. The two answers practitioners actually shipped are not detection but *capability removal*:
A6's `n7-ved` removes Bash from the writing agent and blocks writes outside the content directories
with a `PreToolUse` hook, and A7's `retract` command scopes its own `allowed-tools` to specific
Bash prefixes. Both bound what can be written rather than checking what was.

### Rankings, re-scoped to strict rewrite-in-place

The detail behind both lists is in A11; the summary here is adjusted for what the
retain-but-unretrievable model in A12 would and would not accept.

**Top 3 to adopt as-is.** Under this model no surveyed system is adoptable as-is: every plausible
one carries a retention model the model forbids. Three components survive, each only with its
retention behaviour removed or inverted.

1. **`SamurAIGPT/llm-wiki-agent`'s `tools/health.py` (MIT)** — model-neutral. 10 KB, stdlib plus a
   small helper, zero LLM calls, three checks: index-vs-disk reconciliation both ways, a stub
   threshold (`STUB_THRESHOLD_CHARS = 100`), and log coverage. Nothing in it depends on whether
   superseded content is kept. Caveats: it assumes a `wiki/` layout and a relative-link
   `wiki/index.md`, and it exits 0 regardless of findings.
2. **`Astro-Han/karpathy-llm-wiki`'s `scripts/check_evidence.py` (MIT)** — adoptable **with its
   convention inverted**. The literal-fidelity core (extract quotes of 15+ characters, ISO dates,
   and numbers that are grouped, decimal, suffixed, or 4+ digits; require each to appear in the
   cited evidence, using the boundary rules in the borrow item below) is what protects provenance
   under in-place rewriting. Its `> **Status:` block is what this model forbids, and the same
   source *skips* those lines — so that file supplies both the residue detector (borrow item 2) and
   the fidelity check. Caveats: hard-coded `raw/`+`wiki/` layout, "the exit code carries no
   information", and a deliberately frozen candidate list.
3. **`nvk/llm-wiki`'s derived-index mechanism** — the *component*, not the system. Its indexes are
   derived caches that "rebuild on next read", which removes failure mode 3 with nothing to
   reconcile, and that is model-neutral. Its `superseded`/`archived` lifecycle, its
   "Never remove old aliases" table, and its `retract` command are all incompatible — `retract`
   deletes claims whose support is gone, which is aligned, but it substitutes a `[RETRACTED]`
   placeholder in the text and appends an operation entry to the log, and both are retained markers
   inside the corpus. Unverified either way: I did not read its 243 KB CLI or its tests, so even
   the derived-index component's determinism is unverified.

**Top 3 patterns to borrow into the validator**, ranked by the criteria in A11 — (a)
rewrite-by-default, (b) residue detection, (c) dependant flagging.

1. **(c) Doorstop's suspect-link stamp**, lifted from desk-kbtools A3 and reimplemented over
   findings. Store, inside each finding, a hash of the key content of every finding it depends on
   at the moment it was last checked; compare on every run; a mismatch means a dependency was
   rewritten and this finding must be re-read. This is the strongest (c) mechanism available and it
   needs no retained history, so it is compatible with in-place rewriting. Prefer it over nvk's
   date-chain variant (C8b) and over whole-file hashes, which a large append-heavy evidence file
   defeats.
2. **(b) Revision-residue detection, in two layers.** A deterministic marker sweep seeded by A2's
   own exclusion patterns (`^>\s*\*\*Status:`, `^>\s*Archived:`) extended with the vocabulary
   the surveyed systems actually emit — `Superseded by`, `superseded_by`, `withdrawn_by`,
   `Outdated`, `Disputed`, `Deprecated`, `RETRACTED`, `FALSIFIED`, and the self-referential prose
   forms ("this was wrong", "previously", "no longer", "formerly", "originally", "corrected") —
   then an LLM pass carrying A5's semantic checks ("Old decisions presented as current", "Claims
   without direct source support") and its Citation-drift rule. The token half catches the marker
   and is defeated by paraphrase; the semantic half catches the paraphrase and is not
   deterministic. Vale (desk-kbtools A22) can carry the token half over arbitrary markdown paths
   today. This item is also the whole of what the retain-but-unretrievable model of A12 would need
   in reverse: the same sweep finds retained content, and the same absent barrier is why that model
   cannot be built from corpus-layer parts.
3. **(a) A per-file line ceiling plus the stub-size threshold — and nothing else from A6.** The
   ceiling comes from `benjimixvidz` ("state.md ~30 [lines] Rewrite. ... Total project wiki: ~300
   lines max. Always.") and the threshold from A3 (`STUB_THRESHOLD_CHARS = 100`, intended there for
   truncated writes). A hard cap is the only mechanism found anywhere that makes rewriting the
   *only* path rather than a preference, because an appended correction has nowhere to go; the
   stub threshold supplies the mirror-image floor, so a rewritten finding is a real page rather
   than a placeholder. **The rest of A6's compaction scheme is incompatible and is excluded:** its
   Actuel/Archive pattern moves the old value into an `## Archive` list as a dated one-liner, which
   is a retained marker inside the same file and is retrievable by any grep that matches a word
   common to both sections. Same for A2's archive pages, A4's quarantine reason and index tag,
   A5's `Status: superseded` block, and A7's `[RETRACTED]` substitutions and appended retraction
   log entries — all are markers that keep superseded content findable and therefore belong on the
   incompatible side of the coverage table.

## A11 — Re-classified against strict rewrite-in-place

verified: 2026-09-22

**The criteria, as restated by the coordinator.** The target model is strict rewrite-in-place: when
a claim turns out wrong, the agent edits the original text so it states only what is true now.
There are no withdrawn stubs, no superseded status, no `withdrawn_by` or retraction pointers, and
no "this was wrong" notes. A negative result is a current fact ("X does not do Y; evidence: ..."),
not a correction of an earlier claim. Every system is classified on one axis: does it rewrite in
place, or does it retain superseded content (retraction records, bi-temporal invalidation, status
flags, append-and-mark). **The second group is incompatible, not a partial fit.** Borrowed
mechanisms are preferred if they (a) make in-place rewriting the default or only path, (b) detect
revision-history or supersession language left inside the knowledge base, or (c) flag the pages that
depend on a rewritten page for re-checking.

**The single most important finding of this re-classification: retention is the field's deliberate
consensus, not an oversight.** Every system in this survey except one chose retention, and several
argue for it explicitly — A2's "Never silently rewrite history", A5's "superseded choices are marked
as superseded rather than silently overwritten" and "The fourth mistake is deleting historical
context. Old decisions, failed experiments, and superseded recommendations can be valuable. Mark
them clearly instead of erasing them." The convention has independent roots outside this family too:
ADR immutability (desk-kbtools A20), bi-temporal invalidation (A16), retraction records (A21). So a
strict rewrite-in-place corpus is a deliberate departure from every surveyed convention, and the
consequence for sourcing rules is concrete: **the rules and mechanisms to borrow come from
requirements-engineering and validation tooling, not from the LLM-wiki family**, which supplies
nothing for (c) at all.

### Classification, one row per system

| system | what it does with a superseded claim | verdict |
|---|---|---|
| Karpathy gist (A1) | "noting where new data contradicts old claims"; "The contradictions have already been flagged" — annotation only, and no rule about which of the two survives | **incompatible** |
| Astro-Han (A2) | "keep the old claim for the record but mark it with a Status block ... **Never silently rewrite history**"; archive pages are "point-in-time snapshots" that are never cascade-updated | **incompatible — the strongest statement of the opposite model in the survey** |
| SamurAIGPT (A3) | a `## Contradictions` bullet naming the other page; nothing is rewritten | **incompatible** |
| arturseo (A4) | `status: quarantined` + `quarantine_reason` + `quarantine_date` retained on the page; a resolution path may "split the article or add a nuance section"; `insights/` and `raw/` are immutable by rule | **incompatible** |
| Glukhov (A5) | `Status: superseded` / `Superseded by:` block; per-page-type review windows; delete is listed as rare and "invisible pruning is dangerous" | **incompatible — the clearest statement that retention is a norm** |
| benjimixvidz (A6) | Actuel/Archive: "Nothing is deleted, just compressed", the old value kept as a dated one-liner | **incompatible as a whole — but its `state.md` rule is the target model, see below** |
| n7-ved (A6) | its four claim types (`Source` / `Analysis` / `Unverified` / `Gap`) are *assertion kinds*, not history markers | **compatible** — the only surveyed design with no retention component |
| Jwcjwc12 (A6) | a source-hash mismatch marks propositions "stale"; it flags, it does not rewrite | **incompatible as stated; the mechanism is reusable under (c)** |
| nvk/llm-wiki (A7) | lifecycle values `superseded` and `archived`; C13 alias table "Never remove old aliases"; `retract` deletes, but only for sensitive data and named sources | **incompatible — append-only compatibility shims are the retention model** |
| knowledgebase_guardian (A8) | refuses the incoming document, so the older claim is kept by default | **incompatible and actively harmful here — it retains the wrong claim and discards the right one** |
| MemStrata (A9) | bi-temporal ledger; "a deterministic supersession rule **retires** the stale value" | **incompatible — the ledger is the retention**; the deterministic-decision shape survives |
| OpenSpec (desk-kbtools A23) | change deltas whose sections are ADDED / MODIFIED / REMOVED, merged into the spec on archive; **MODIFIED means overwrite** | **compatible — the only system in either survey whose default path is overwrite** |
| Doorstop (desk-kbtools A3) | keeps items (`active: false` excluded from publication); but its suspect-link stamp is hash-based and retains no history of the parent | **mixed: retention model incompatible, the stamp mechanism compatible and the best (c) available** |
| ADR tools (desk-kbtools A20) | immutability is the point of the format | **incompatible** |
| Graphiti/Zep, mem0, nanopublications (desk-kbtools A16/A17/A21) | bi-temporal invalidation, ADD/UPDATE/DELETE with history, signed retraction records | **incompatible** |

That is 13 incompatible and 2 compatible-or-mixed out of 15. **The category this survey was asked to
cover does not contain the target model.**

### The one thing in the survey that IS the target model, and it is a file class not a tool

`benjimixvidz` (A6) runs a two-tier layout where one file per project is defined as a snapshot
rather than a record, verbatim: "**state.md**: rewritten every update (**it's a snapshot, not
history**)", under a budget of "~30 [lines] Rewrite". Everything else in their layout uses
Actuel/Archive and is therefore incompatible, but that one rule is precisely the target: the file
is re-derived on every update, nothing is retained, and there is no marker language because there is
no history to mark.

The transferable half is the budget, and it is the strongest (a) mechanism found anywhere in the
survey, including the prior one. Their per-file ceilings — "state.md ~30, log.md ~60,
architecture.md ~80, decisions.md ~60", "Total project wiki: **~300 lines max. Always.**" — do
something a rule cannot: **a file with a hard line ceiling has nowhere to put an appended
correction, so rewriting in place becomes the only path that fits.** An instruction saying "rewrite,
do not append" is a preference; a ceiling that an append violates is a constraint. The ceiling is
one line of Python to check.

Caveat stated by its author, and it applies here: the corpus must be split into files small enough
for a ceiling to be meaningful — "For 6 projects and ~20 wiki pages each, markdown + git is the
right tool."

### Mechanisms surviving under (a) — make rewrite the default or only path

1. **OpenSpec's MODIFIED-means-overwrite** (desk-kbtools A23, verbatim): a change carries a delta
   spec whose sections are ADDED / MODIFIED / REMOVED, `openspec archive` "validates and merges the
   active delta specs into `openspec/specs/`", and validation checks "a change's MODIFIED
   requirements against the main specs they would replace". This is the only surveyed design where
   the *file format* makes overwrite the enforced operation. Cost noted there: npm toolchain, and
   it is a spec-workflow tool, not a findings-corpus tool.
2. **A per-file line ceiling** (benjimixvidz, above). Nothing else in either survey does this.
3. **Nothing else.** No tool enforces rewrite-in-place; the (a) column of the tooling table is
   empty.

### Mechanisms surviving under (b) — detect revision-history language left in the knowledge base

This cell was empty before the re-scope because nobody was looking for it: every system treats the
presence of a status marker as a *feature*. Under this model its presence is the defect, and the
detection is cheap.

1. **A marker regex sweep, seeded by A2's own exclusion logic.** `check_evidence.py` deliberately
   *skips* these lines, and its two patterns are a ready-made residue detector — verbatim from that
   source: `STATUS_LINE_RE = re.compile(r"^>\s*\*\*Status:")` and
   `ARCHIVED_RE = re.compile(r"^>\s*Archived:")`. The same source's blockquote grouping
   (`skip_status_block`) is the parsing logic for finding one. Extend with the vocabulary the
   survey's systems actually emit: `Superseded by`, `superseded_by`, `withdrawn_by`, `Outdated`,
   `Disputed`, `Deprecated`, `Archived:`, `RETRACTED`, `FALSIFIED`, and the self-referential prose
   forms — "this was wrong", "previously", "no longer", "formerly", "originally", "corrected".
   A2 and A5 both *prescribe* the first group, so a corpus adopting this model must lint for
   exactly what they recommend writing.
2. **Vale** (desk-kbtools A22) is the only tool in either survey that can carry such a rule
   mechanically over arbitrary markdown paths: YAML rules with regex and scope, exit code on
   findings. Its limit is already recorded there and it applies unchanged: it "detects the *habit*,
   not the *error*", and any paraphrase defeats a token rule.
3. **For semantic residue, A5's check list is the best available as prose rules to hand an LLM
   pass**, verbatim: "Old decisions presented as current", "Pages that contradict newer sources",
   "Claims without direct source support", plus its Citation-drift definition — "the citation
   remains in place, but the sentence around it changes. The page still looks sourced, yet the
   citation no longer supports the claim." Citation drift is the residue that survives a sloppy
   in-place rewrite, and no mechanical checker in either survey can see it.
4. **A structural rule rather than a token rule**, which is what this desk would reach for if a
   validator were written: a negative result must be *self-contained as a current fact* — the
   sentence must carry its own evidence citation and must not reference another claim as its
   subject. That is checkable as "every sentence in an evidence-bearing section carries a citation",
   which is the same shape as A4's citation-traceability rule ("Every claim in a wiki article must
   trace to a file in `raw/` or be explicitly marked") with the direction reversed: the residue
   forms all reference *another claim* instead of a source.

### Mechanisms surviving under (c) — flag pages depending on a rewritten page

This is the requirement with a real mechanism already built, and it does not come from the
LLM-wiki family. Under rewrite-in-place it is also the *hard* one, for a reason worth stating: a
diff-based signal is destroyed by the very act of rewriting, because after an in-place edit the
dependant has nothing to compare against unless an expectation was stored before the rewrite.

1. **Doorstop's suspect-link stamp — the best fit.** From desk-kbtools A3's verbatim source:
   `stamp()` hashes the parent's "key content" (`uid`, `text`, `ref`, `references`, and the link
   list) plus any fields the document lists in `extended_reviewed`; each child stores the parent's
   stamp at the moment the link was last cleared; a mismatch on the next run makes the link
   suspect. **Nothing about this requires retaining the parent's old text** — the child holds a hash
   of what the parent said when the child was last checked, and the parent is free to be rewritten.
   That is exactly the (c) mechanism for this model, and it is the one piece of the prior survey
   that gets stronger under the new criteria rather than weaker.
2. **nvk/llm-wiki's C8b** — the date-chain variant, verbatim: follow each file's `sources:` chain
   and flag when "any raw source has an `ingested:` newer than the member's `updated:`", reported as
   "`Project <slug> may be stale: N source(s) newer than member artifacts.`", "Never auto-fixed".
   Cheaper than hashing and less precise.
3. **n7-ved's forward-only dependency score** (A6), verbatim: "Each file carries a score derived
   from how far behind its outgoing wiki-link dependencies it is. Forward-only, no backlink
   tracking." The score ranks *which* dependants to re-check first, which is the practical form of
   (c) once the corpus is large.
4. **Jwcjwc12's content hash per proposition** (A6), with the caveat its own thread raised: a
   whole-file hash invalidates everything derived from the file, which is useless for a large
   append-heavy evidence file. The hash must be per-cited-region, not per-file, for this corpus —
   and that is a design decision, not something any surveyed tool implements.

### The empty cells, restated for this model

- **No tool enforces rewrite-in-place (a).** The two mechanisms found are a spec-workflow format
  (OpenSpec) and a practitioner's line budget (A6). Both are ideas, not components.
- **No tool detects revision-history residue in a knowledge base (b).** Vale can carry the token
  rule; the semantic half is an LLM pass; and a token rule is defeated by paraphrase, so (b) is
  necessarily two layers.
- **Only requirements engineering supplies (c).** Doorstop is the mechanism, and it is compatible
  with rewrite-in-place in a way its own retention model (`active: false` items) is not. Nothing in
  the LLM-wiki family — not A2's checker, not A3's `health.py`, not A7's lint spec with its ~20
  checks — flags a dependant when an upstream page changes, except A7's date-chain check.

**Nothing here is an adoption recommendation.** The classification above is what the systems do; the
choice of model is the coordinator's, and the one fact this desk would put in front of that
decision is that the field chose the opposite, deliberately, and says so in writing.

## A12 — Third model: retain-but-unretrievable, and whether anything supports it

verified: 2026-09-22

**The model.** Superseded claims may stay in the corpus, but only if it is structurally impossible
for an agent looking for correct information to retrieve them.

Stated this way it is a genuinely distinct third architecture, not a compromise between the two:
A11 classified retention and rewrite-in-place, and this one is retention with a *retrieval barrier*.
Its correctness therefore rests entirely on the barrier — the claim's continued presence is
tolerable only while it cannot be found. That makes it the one model whose failure mode is silent:
if the barrier leaks, the corpus contains a false claim that is indistinguishable from a true one,
and nothing about the file marks which is which.

**No surveyed system implements it, and the two nearest analogues defeat themselves by making
retained content *more* retrievable, not less.** These were checked rather than assumed.

- **A4's quarantine** is the closest design, and it goes the wrong way. Verbatim from
  `docs/contamination-mitigation.md`: quarantined articles "Are excluded from `_graph.md` until the
  contradiction is resolved", "Are marked visibly in `_index.md` with a `[quarantined]` tag", "Are
  not used as sources for new article synthesis", and "Appear as the first items in the next
  linting report". Two of those four properties *increase* retrievability — the index tag and the
  prominent position in the report — and the graph exclusion is a navigation barrier, not a
  retrieval barrier. An agent that greps the corpus finds a quarantined page exactly as easily as
  any other page. What makes it safe in that system is not the barrier; it is the on-page
  `quarantine_reason`, i.e. a marker, which is the thing this model forbids.
- **A2's archive pages** are the second analogue, and they are plainly retrievable: archive pages
  live under `wiki/`, they are listed in `wiki/index.md` (the Summary is prefixed `[Archived]`), and
  the only structural difference is that they are never cascade-updated. The `ARCHIVED_RE`
  exemption in `check_evidence.py` (`^>\s*Archived:`) exists precisely because an archive page is
  still a page in the same searchable tree.
- **benjimixvidz's Actuel/Archive** (A6) is the third, and it is the same file. An `## Archive`
  section below `## Actuel` is retrievable by any grep that matches a word appearing in both.
- **nvk/llm-wiki's derived indexes** (A7) get the closest mechanically — a cache that "rebuilds on
  next read" could simply omit retired pages — but the pages themselves remain files in `wiki/`,
  and the lint spec's own inventory and placement checks would flag a file that no index lists.
  Omitting a page from a derived index makes it unreachable *by navigation* and leaves it entirely
  reachable by grep, which is how the pilot project's agents actually read.

**What a real barrier would require, and why nothing in this survey has one.** For an agent that
reads by grep-and-excerpt over the working tree, "structurally impossible to retrieve" means the
text must be outside the searchable namespace. The only mechanisms that achieve that are (i) a
location outside the repository or outside every path the agent's tools glob, (ii) a non-text form,
or (iii) a tool-layer exclusion enforced by the harness rather than by the corpus. The nearest
things in the survey are A4's two-vault model — two separate Obsidian vaults where "the agent
[is] pointed at the agent vault" and the personal vault stays human-written — and A4's note that in
Obsidian "Use Obsidian's 'Excluded files' setting to exclude `wiki/`, `output/`, and `learning/` from
search and graph". Both are exclusions in a *human's* reading tool. Neither bounds what an agent's
`grep` returns, and A6's `n7-ved` reached exactly this conclusion from the other direction, verbatim:
enforcement belongs "at the agent boundary, not the conversation boundary", and the writer agent's
toolset is restricted because it is the executor rather than the planner.

**The concrete consequence for the pilot project, stated plainly because it is a fact about the design
and not an opinion about the choice:** every barrier available here is a harness-level configuration
(which paths an agent's search tools are pointed at, or a path exclusion), never a property of the
markdown. A harness-level barrier is real — it stops an agent that uses the sanctioned tools — but
it is not a property of the corpus, so it does not travel with the file if it is copied, quoted,
committed to another repo, or read by an agent that was pointed at the path directly. It also fails
against a shell command that greps the tree, which is precisely the write-and-read path A6's
practitioners found prompts cannot police. Benjimixvidz's own Stop-hook experience and n7-ved's
tool-boundary rule both point the same way: mechanisms at the corpus layer cannot bound a reader
that does not consult them.

**One further property to record, since it decides how it interacts with the pilot project's existing
rules.** A retained-but-unretrievable set is a second truth that no one reads. The pilot project's own
CLAUDE.md rule 7 forbids keeping an obsolete statement "merely because a later section or document
says it is superseded" — that rule exists because a reader under time pressure lands on the wrong
line — and an unretrievable copy is the same second truth with its warning removed, since nothing
about the file can tell a future reader that it is stale. That is a statement about the model's
interaction with an existing project rule; whether it is disqualifying is the coordinator's
decision.

### How the surveyed mechanisms score against this model

| mechanism | barrier it provides | verdict |
|---|---|---|
| A4 quarantine | graph exclusion + an index tag + report priority | **fails** — the tag and report make it more retrievable, and the on-page reason is a marker |
| A2 archive pages | none (same tree, listed in `index.md`) | **fails** |
| A6 Actuel/Archive | a section heading inside the same file | **fails** |
| A7 derived indexes | omits a page from navigation | **partial** — unreachable by index navigation, fully reachable by grep |
| A4 two-vault / Obsidian excluded-files | a human tool's search scope | **partial** — bounds a person's reading, not an agent's grep |
| A6 `n7-ved` tool-boundary hooks | file-pattern write blocking, Bash removed from writers | **partial and the strongest** — this is the only mechanism found that bounds an executor, but it bounds *writes*, and this model needs to bound *reads* |

**Nothing in either survey bounds an agent's reads.** The read-side machinery that exists
(generated indexes, quarantine tags, search scopes) all either ignores the retained content by
convention or advertises it. A read barrier would be a new component, and its correctness would
depend on every reader — including a shell `grep` — respecting it.
