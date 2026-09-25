# desk-reddit — practitioner discussion survey: markdown knowledge-base drift under LLM coding agents

Package: find the practitioners who have hit the five drift failure modes below and report what they
say they did. Venues: Reddit, Hacker News, the `anthropics/claude-code` issue tracker, vendor repos,
dev blogs. Web research only; no codebase claims here.

**Failure modes, numbered as in the request (same numbering as desk-kbtools and desk-llmwiki):**

1. A falsified claim stays in its original section; the correction is appended as a new section. The
   wrong text survives grepping and other agents act on it.
2. A new document supersedes an old one; the "superseded" marker does not survive a grep excerpt or
   long context.
3. Hand-maintained indexes go stale.
4. Failed experiments are kept whole instead of shrunk to a one-line dead-end fact.
5. The same fact is copied into several files and the copies drift apart.

**Relationship to the two prior surveys.** desk-kbtools covered deterministic spec/traceability and
frontmatter tooling (Doorstop, StrictDoc, sphinx-needs, OpenFastTrace, zk, Dataview, Beads, Basic
Memory, Graphiti/Zep, mem0, Talamus, ADR tools, agent-context linters, Vale, OpenSpec, Spec Kit,
Backlog.md). desk-llmwiki covered agent-authored wiki systems (Karpathy gist, Astro-Han, SamurAIGPT,
arturseo, Glukhov, nvk/llm-wiki, knowledgebase_guardian). **This file re-describes none of those.** A
prior-survey tool appears here only if a practitioner reports using it on this problem, with what
they report happening (see A11 for the two cases where that happened).

**Method and quote marking.** Reddit's own JSON and HTML endpoints refused this machine (403 and an
Anubis proof-of-work interstitial at the mirrors), and WebFetch is domain-blocked for reddit.com. All
Reddit content here was pulled from the **pullpush.io Reddit archive API** (`/reddit/search/submission/`
and `/reddit/search/comment/`), which returns the original post and comment bodies. Hacker News is via
the **Algolia API**; GitHub via the **REST API**; repo maturity via the same API on 2026-09-22.

Every quote is marked:
- **direct read** — the bytes were pulled and read at that URL;
- **search summary** — relayed by a search-result snippet and *not* re-read on the page, so wording
  is approximate.

Reddit scores and comment counts come from the archive API and are as recorded there; they can trail
the live figure. Anything unconfirmed is labelled UNVERIFIED. Maturity figures (stars, created, last
push) are quoted per tool because a previous survey was burned by trusting a 0-star repo's README.

---

## ⬅️ OPEN QUESTIONS

1. **Nobody has published a measurement.** Not one thread found here reports a before/after number
   for a drift-repair intervention on a markdown corpus. The nearest things are (a) `u/Beautiful-Energy2169`'s
   one-off audit of their own corpus (A5, A6 — a prevalence count, not an intervention result) and
   (b) `u/ShotPorter`'s "the results are measurable... Eval data is collecting and will be published
   when the stars accumulate" (A9), which is a promise, not a result. A future desk could check
   whether that eval was ever published.
2. **Does `rule-audit`'s parser generalise past rule files?** Its README says it "parses a prompt into
   normative rules" and the CLI takes prompt/rule files. Whether it can be pointed at an arbitrary
   findings document — prose, not imperatives — is untested here. One `pip install rule-audit` and a
   run against the pilot project's own findings file would answer it and is the single highest-value
   follow-up in this survey.
3. **Nothing found detects a duplicated fact in arbitrary markdown.** Every duplicate detector found
   is scoped to rule/instruction files and reports *duplicate rule text*, not "the same fact stated
   twice in two findings documents". See A12 for the one partial exception and its stated limit.
4. **`warrant`'s hook was never observed running.** The README says `warrant install` merges a
   `SessionStart` hook into `~/.claude/settings.json`. Not verified against a live install; the repo
   has one day of commits.
5. **Threads on r/ClaudeWorkflows were excluded as machine-generated.** That subreddit posts
   templated "[Workflow] ..." summaries with a "Workflow value: 85/100" header and links to other
   subreddits' posts; two searches surfaced them repeatedly. They are not treated as practitioner
   reports here. If a future desk needs coverage of that sub, it should be read as an aggregator, not
   as field evidence.

---

## INDEX

```
commonality          A1  How common: the independent thread inventory          2026-09-22  L095
authority            A2  Anthropic: advisory by design, and no detectors      2026-09-22  L148
authority            A3  Enforcement architecturally absent (issue tracker)   2026-09-22  L197
mode-1               A4  Append-instead-of-rewrite: reports and countercase   2026-09-22  L232
mode-1/2             A5  Stale claim written forward into four live docs      2026-09-22  L293
mode-2               A6  Supersession: why "rank it down" fails               2026-09-22  L345
mode-3               A7  Hand-maintained indexes and handoff files            2026-09-22  L399
mode-4               A8  Failed experiments and rejected options              2026-09-22  L448
mode-5               A9  Duplicated facts across files                        2026-09-22  L494
workarounds          A10 Recurring workaround patterns, with counts           2026-09-22  L531
workarounds          A11 Which instruction types actually get followed        2026-09-22  L586
tools/deterministic  A12 Deterministic detectors (not in prior surveys)       2026-09-22  L719
tools/agentic        A13 LLM-based drift auditors                            2026-09-22  L757
tools/protocol       A14 Hook-enforced protocols, session checkpoints        2026-09-22  L786
tools/commercial     A15 Commercial and productised offerings                2026-09-22  L814
prev-survey-reuse    A16 ADRs: the one prior-survey tool actually in use     2026-09-22  L840
verdict+coverage     A17 Does a usable solution exist? + mode coverage       2026-09-22  L903
```

(Ranges are points in time; cite by entry ID.)

---

## A1 — How common: the independent thread inventory

verified: 2026-09-22

**Answer: the complaint is common and it is not a small corner.** Below are the venues and threads
found in roughly six hours of searching, with the failure modes each touches. This is not a
systematic sample — searches were driven by a hand-written vocabulary list — so treat the count as a
floor, not a rate.

**Hacker News (Algolia API, direct read).** Six threads, 2026-01 to 2026-08:

| id | title | date | pts / comments | modes |
|---|---|---|---|---|
| [46693985](https://news.ycombinator.com/item?id=46693985) | Ask HN: How do you keep system context from rotting over time? | 2026-01-20 | 35 / 28 | 2, 4 |
| [47850907](https://news.ycombinator.com/item?id=47850907) | Show HN: Daemons — we pivoted from building agents to cleaning up after them | 2026-04-21 | 70 / 31 | 1, 2 |
| [48160604](https://news.ycombinator.com/item?id=48160604) | Ask HN: Do you still spend time maintaining Claude.md / AGENTS.md files? | 2026-05-16 | 9 / 10 | 1, 5 |
| [49376287](https://news.ycombinator.com/item?id=49376287) | I am morally opposed to updating my Claude.md | 2026-08-20 | 29 / 28 | 1 |
| [47219090](https://news.ycombinator.com/item?id=47219090) | Ask HN: How is your team changing documentation for AI-assisted coding? | 2026-03-02 | 3 / 1 | 1, 2 |
| [47266783](https://news.ycombinator.com/item?id=47266783) | Ask HN: How do you keep AI coding agents aligned with your codebase standards? | 2026-03-05 | 6 / 1 | 1 |

**Reddit (pullpush API, direct read).** Threads where the drift is the subject or a major branch of
the discussion:

| thread | date | score / comments | modes |
|---|---|---|---|
| [r/ClaudeCode 1wewoy5 — Anyone else tired of maintaining handoff markdown files for Claude Code?](https://www.reddit.com/r/ClaudeCode/comments/1wewoy5/) | 2026-09-13 | 1 / 99 | 2, 3, 5 |
| [r/ClaudeCode 1wmoost — How do you carry *decisions* (not chat history) across Claude Code sessions?](https://www.reddit.com/r/ClaudeCode/comments/1wmoost/) | 2026-09-22 | 1 / 147 | 1, 2, 4, 5 |
| [r/ClaudeAI 1vub2nw — I spent a month building the ultimate memory system for Claude. It backfired](https://www.reddit.com/r/ClaudeAI/comments/1vub2nw/) | 2026-08-21 | 17 / 59 | 2, 3, 5 |
| [r/BuildWithClaude 1vk5g7m — How are you handling project memory once a Claude Code project gets old?](https://www.reddit.com/r/BuildWithClaude/comments/1vk5g7m/) | 2026-08-10 | 2 / 2 | 1, 2, 4 |
| [r/LLMDevs 1wk0oyy — Your CLAUDE.md can reference code that no longer exists](https://www.reddit.com/r/LLMDevs/comments/1wk0oyy/) | 2026-09-18 | 1 / 0 | 1 |
| [r/ClaudeCode 1w09jan — Do you have a bunch of stale docs nobody remembers to update?](https://www.reddit.com/r/ClaudeCode/comments/1w09jan/) | 2026-08-27 | 1 / 0 | 1, 2 |
| [r/AI_Agents 1vkhzd5 — Append-only memory is exactly wrong when an agent needs to change its mind](https://www.reddit.com/r/AI_Agents/comments/1vkhzd5/) | 2026-08-10 | 3 / 38 | 1 (argues the other way) |

**GitHub `anthropics/claude-code` (REST search API).** The drift complaint has its own issue family.
`CLAUDE.md` + `stale` returns 725 issues; the relevant subset:

| issue | title | date | state / comments |
|---|---|---|---|
| [53223](https://github.com/anthropics/claude-code/issues/53223) | [BUG][SECURITY] CLAUDE.md/AGENTS.md instruction compliance is architecturally unenforced | 2026-04-25 | open / 22 |
| [2544](https://github.com/anthropics/claude-code/issues/2544) | [BUG] CLAUDE.md Mandatory Rules Consistently Ignored Across Multiple Repositories | 2025-06-24 | open / 20 |
| [85477](https://github.com/anthropics/claude-code/issues/85477) | [Feature] Rules-governance diagnostics: instruction-budget warnings, duplication & conflict detection | 2026-08-10 | open / 4 |
| [95505](https://github.com/anthropics/claude-code/issues/95505) | Model reports a written instruction back as a completed fact, and writes the stale state into live files | 2026-09-19 | open / 0 |
| [6354](https://github.com/anthropics/claude-code/issues/6354) | [BUG] Claude forgets everything in CLAUDE.md after compaction | 2025-08-22 | open / 22 |

**Is it rising?** The evidence says the *topic* is rising, less clearly that the *rate* is. Every
tool found in A11–A14 in this survey was created between 2026-02 and 2026-09, and four of the seven
were created in the last eight weeks — that is a rising supply response. On the demand side the
oldest citation here is issue #2544 from 2025-06-24, so the complaint is at least fifteen months old;
nothing found lets me normalise for the growth of the venue itself. **UNVERIFIED: whether the
per-capita rate is rising.**

---

## A2 — Anthropic's own position: advisory by design, and the detectors do not exist

verified: 2026-09-22

This is the single most load-bearing finding for the "written instructions have not stopped this"
premise, because it is Anthropic conceding the point in a public issue thread, and then naming the
gaps precisely.

**Source:** [`anthropics/claude-code` #85477](https://github.com/anthropics/claude-code/issues/85477),
opened 2026-08-10. Direct read via the GitHub REST API.

The issue body (direct read) quotes the memory docs and the underlying constraint:

> `CLAUDE.md` is explicitly advisory — the [memory docs](https://code.claude.com/docs/en/memory) state
> "Claude treats them as context, not enforced configuration. To block an action regardless of what
> Claude decides, use a PreToolUse hook instead" and "there's no guarantee of strict compliance,
> especially for vague or conflicting instructions."

and:

> As instruction count grows, adherence degrades *uniformly across all instructions*, not just the
> newest ones. HumanLayer's analysis ([Writing a good CLAUDE.md](https://www.humanlayer.dev/blog/writing-a-good-claude-md))
> found frontier models reliably follow roughly 150–200 instructions; Claude Code's own system prompt
> already consumes ~50 of those. Beyond the budget, "it doesn't simply ignore the newer instructions —
> it begins to ignore all of them uniformly."

Then `bcherny` (Anthropic) replied on 2026-08-17 (direct read):

> Thanks for the detailed writeup. Part of this exists today:
>
> - `/doctor` checks your checked-in CLAUDE.md for content Claude can derive from the codebase and
>   proposes trims (https://code.claude.com/docs/en/debug-your-config)
> - `/context` shows how much of the context window your memory files and rules are taking
>   (https://code.claude.com/docs/en/memory)
>
> There is no instruction-budget warning, duplicate-rule detection, or cross-file conflict detection
> yet, so leaving this open for that part.

**That is the answer to failure mode 5 from the vendor**: duplicate-rule detection and cross-file
conflict detection do not exist natively as of 2026-08-17, and a maintainer says so. `/doctor`
proposes trims of *derivable* content; it is not a contradiction or duplication check.

**The instruction budget claim is not provenance-clean here.** "roughly 150–200 instructions" comes
from HumanLayer's blog, relayed by the issue author — **search summary of a secondary source, not
independently verified** (the underlying blog was not read). It appears again in `tenet`'s pitch
(A11). Treat the number as folklore with a citation, not a measured constant.

---

## A3 — Enforcement is architecturally absent, and the reporter population is not one person

verified: 2026-09-22

**Source:** [`anthropics/claude-code` #53223](https://github.com/anthropics/claude-code/issues/53223),
2026-04-25, open, 22 comments. Direct read.

> CLAUDE.md content is injected as system prompt text. The model treats these instructions as
> suggestions competing with trained behavioral heuristics, not hard constraints. There is no
> application-level enforcement layer that verifies post-generation compliance.

It quotes issue #34774's exchange with the model:

> "I simply ignored the CLAUDE.md rule. No valid reason. I can't guarantee it won't happen again
> through willpower alone."

And on the population size — the part that matters for "is this just me":

> This is not a single-reporter edge case. Independent reports of the same root cause span multiple
> months, platforms, and instruction surfaces: #39210, #40049, #43804, #34774, #47579, #19471,
> #15443, #45697, #39502, #39697, #38481. At least 10 independent reporters. The stale bot is
> systematically closing this entire bug class before human resolution occurs.

**Caveat, stated plainly:** those issue numbers are the author's own list; eleven of them were not
each opened and read for this survey, and an issue count is not a measure of how many *distinct*
people hit it. The useful claim is narrower and still holds: the maintainer-adjacent position in
A2 plus this volume of reports means the "instructions will fix it" route is already known not to
work, by more than one person, in a public venue.

**The separate failure the same issue describes is worth carrying forward**: silent
non-compliance, where the user "trusts that work was completed when it wasn't". That is the general
form of failure mode 1 — a rule believed to be in force that is not.

---

## A4 — Failure mode 1: append-instead-of-rewrite

verified: 2026-09-22

**The pattern, named by a tool author as the thing his tool exists to remove.** From
`marky291/claude-drift`'s README (direct read of
`https://raw.githubusercontent.com/marky291/claude-drift/main/README.md`), under "Three kinds of
drift it catches":

> | 🧭 **Legacy narration** | The artifact is *accurate*, but carries superseded-history framing a
> steering doc shouldn't — "Use Laravel Sail (previously Herd)", "X replaced Y", "Updated <date>:
> previously…". It should state today's reality, not the migration path that led there. |

That is failure modes 1 and 2 stated as a bug class, with the prescribed fix being exactly the pilot
project's rule: state the current fact, not the correction history. Note the tool is **1 star,
created 2026-06-07, last push 2026-06-10** (GitHub API, 2026-09-22) — three days of commits, then
abandoned for three months. Rank it as a design statement, not an adoption.

**The observable consequence, in the vendor's own tracker.** From
[#95505](https://github.com/anthropics/claude-code/issues/95505), 2026-09-19 (direct read) — the
clearest single case found anywhere, and it is a markdown knowledge base, not code:

> 1. New evidence arrived that was consistent with a statement in file B, which described a condition
>    as fact and was correctly dated.
> 2. A separate file A contained a dated instruction to change that condition. Nothing anywhere
>    recorded whether the instruction had been carried out.
> 3. A third file contained a pointer describing file A as holding a different category of content.
>    The model read that description and did not open file A.
> 4. The model treated the new evidence as confirming file B.
> 5. It wrote file B's stale condition into four live documents, stated as current fact, with no
>    hedging and no flag.

and its diagnosis of the general defect:

> Treat an instruction with no recorded outcome as an open question, rather than silently resolving
> it as either done or not done. ... Following a pointer to a file is not the same as reading the
> file, and the pointer's wording narrowed what the model went looking for. And new evidence
> consistent with an old statement is not confirmation of it; it is usually consistent with several
> statements, including newer ones the model has not read.

This is a one-reporter, 0-comment issue; it is a *mechanism* description, not a prevalence datum.
Its value is that it names the two sub-failures precisely: **an unresolved instruction that nothing
marks resolved**, and **a pointer that substitutes for reading**.

**A counter-argument the survey has to record, because it is the majority position in one venue.**
In [r/AI_Agents 1vkhzd5](https://www.reddit.com/r/AI_Agents/comments/1vkhzd5/) (2026-08-10, 38
comments, direct read), the top comments defend append-only against a preprint that argues for
revocation. `u/TransitionMediocre22`:

> "Asking the agent to never be wrong" is exactly backwards. Append-only is what makes being wrong
> survivable. Accounting has run the world's money on it for five centuries: you don't erase a bad
> entry, you post the correction.

That commenter's own proposed resolution is the one desk-llmwiki called *retain-but-unretrievable*:
storage stays append-only, retrieval filters to a versioned current projection. **This is the
strongest available statement of the alternative to rewrite-in-place, and it is not the minority
view in the agent-memory community.** Anyone deciding between "rewrite the file" and "keep history,
filter the read" should read this thread first.

---

## A5 — Failure mode 2: supersession, and why "rank it down" fails

verified: 2026-09-22

**The exact complaint, from a practitioner who built a tool for it.** r/ClaudeCode 1wmoost, OP
`u/delimitdev`, 2026-09-22 (direct read — 147 comments recorded by the archive API):

> 5. A newer decision replaced part of an older one, and both still read as current.

Note the qualifier "*part* of an older one". This is the case a whole-document supersession marker
does not cover, which is the shape the pilot project's findings files have.

**The failure of the obvious fix, reported against the reporter's own tool.** `u/Sea-Perception1619`
in the same thread (direct read), disclosing their project
[`Daily-Nerd/daimon`](https://github.com/Daily-Nerd/daimon) (19 stars, created 2026-07-03, pushed
2026-09-22 — actively maintained):

> And unlike Anise_Paprika I rank superseded items down instead of dropping them out of search, so an
> old one can still come back first. Someone reviewing daimon marked me down for exactly that and I
> don't have a good answer yet.

That is the retain-but-unretrievable design failing in the field, reported by the person who built it.
It is one self-report, not a study, but it is the specific objection the pilot project's design would face.

**The scale, in the only numbers anyone published.** `u/Beautiful-Energy2169` in r/ClaudeAI 1vub2nw,
2026-08-22 (direct read):

> The bit worth keeping from that confession is "silently rot", not "bottlenecked". I counted mine:
> 14 repos, 1879 markdown files, 317 of them written by agents rather than by me. Of those 317, 54%
> were stale or orphaned, meaning nothing linked to them and nothing had touched them in weeks.
>
> Worst one was a 126KB handoff doc that hadn't changed in 51 days and was still referenced from a
> tracked file, so every new session read it and treated a seven-week-old plan as the current state
> of the project. That's a different problem from being over-constrained. The rules weren't too
> strict, they were just wrong by then.
>
> Before you tear the system out, check the mtimes and check what still links to what. A rotted file
> looks exactly like a good one from the outside, which is why mine sat there for weeks.

**Anecdote, one person, self-counted, no method published.** But it is the only prevalence figure
found in the whole survey, and its conclusion — "a rotted file looks exactly like a good one from
the outside" — is the mechanism failure mode 2 describes. The proposed check (mtimes plus inbound
link count) is cheap and testable on any corpus.

**The same thread's complement, from the other side.** `u/nofeaturesonlybugs`, in the 1wmoost thread
(direct read), on why bidirectional links rot:

> In my experience LLMs can struggle to keep unidirectional links current and any form of
> bidirectional link eventually goes stale.

---

## A6 — Failure mode 3: hand-maintained indexes and handoff files

verified: 2026-09-22

**The concrete report.** r/ClaudeCode 1wewoy5, OP `u/Asly97`, 2026-09-13 (direct read) — titled
"Anyone else tired of maintaining handoff markdown files for Claude Code?":

> I ran the whole setup: CLAUDE.md, a /remember handoff file, session hooks auto-saving state. It
> worked... until the handoff file hit 400 lines, half of it stale, and I was spending 15 minutes
> re-explaining anyway every Tuesday.

**Disclosure the reader needs:** the same post ends "Switched to a persistent memory layer (Vilix AI)
connected via MCP" — the thread is a soft product plug, and the OP replies to almost every comment
in a promotional register. The 400-line/half-stale fact is still a report; the conclusion is a pitch.

**The workaround that thread converged on, reported as working by its author.** `u/DoggoCentipede`
(direct read):

> I have a /handoff skill and a /resume skill. Handoff runs the test suite and records the current
> state of things all via a script. Then it records work done this session, what wasn't finished, and
> what comes next. **It is rewritten from scratch each time.**

That is the rewrite-in-place discipline, applied to a single mutable current-state file, enforced by
making a script regenerate it rather than an agent edit it. No measurement is offered.

**The same idea, built and enforced with hooks.** [`jonton26/claude-starter-kit`](https://github.com/jonton26/claude-starter-kit/blob/main/docs/CONTEXT_PROTOCOL.md)
— `docs/CONTEXT_PROTOCOL.md`, direct read. **Maturity: 0 stars, 0 forks, created 2026-09-10, one
commit, pushed 2026-09-10, no license** (GitHub API). Nine days old and unadopted; report the design,
not the tool. Its file table:

> | `.agent/HANDOFF.md` | Snapshot of the *current mission* — what survives compaction. **Overwritten,
> not appended.** | per-worktree | no (git-ignored) |
> | `.agent/LEDGER.md` | Append-only log of what actually happened this mission. Newest at the
> bottom. | per-worktree | no (git-ignored) |
> | [`docs/DECISIONS.md`](DECISIONS.md) | Append-only log of durable decisions and their *why*. |
> `docs/` | yes |
> | [`docs/FRICTIONS.md`](FRICTIONS.md) | Inbox for out-of-scope issues noticed mid-mission. Log,
> don't deviate. | `docs/` | yes |

and the enforcement, which is the part that distinguishes it from a CLAUDE.md paragraph:

> | `Stop` | Backstop: if the turn ends past the warn line with a stale/missing handoff, blocks the
> stop **once** and demands the handoff first. |
> | `PreCompact` | Last resort: if compaction (auto *or* manual) fires with a stale handoff, appends
> a machine-generated emergency snapshot — branch, recent commits, `git status`, diff stat, ledger
> tail, transcript path — so recovery never starts from zero. |

The **mutable-current + append-only-history split, with the mutable half mechanically regenerated
and a hook that blocks turn-end when it is stale**, is the most directly transferable design in this
survey. It is also the design `u/MakaiMorais` (A9) and `u/DoggoCentipede` arrived at independently,
and the one desk-llmwiki's A12 evaluated from the other direction.

---

## A7 — Failure mode 4: failed experiments and rejected options

verified: 2026-09-22

**The complaint, in the requester's own words.** r/ClaudeCode 1wmoost, OP `u/delimitdev`, 2026-09-22
(direct read), first and third of five cases:

> 1. We tried A and rejected it because of X. A fresh session proposes A again.
> 3. Something was implemented but never deployed, and the next session treats it as live.

**Reported as solved, and how.** `u/Dhaupin` in the same thread (direct read):

> I just update the prd, or reference the new from the old with a reconsiliation/resolution, and hard
> link to correct doc if it's seperate. ... I also keep a /labs/archive going for old docs, exactly
> for this purpose of archeology and referencing. The files in there are kind of a backup, kind of
> debriefing, kinda postmortem ref to back current resolutions. ... None of it is a perfect system,
> but it has served my needs extremely well. And has been surprisingly accurate + canonical.

Note this is the *opposite* of the requested behaviour: the dead work is kept whole, in an archive,
with links back. It is reported as working for that practitioner. The one-line dead-end stub the pilot
project wants is not what anyone here reports doing; the nearest is `u/Ok_Relation_3892` below.

**The one report that matches the requested behaviour.** `u/Ok_Relation_3892`, same thread (direct
read):

> I build myself a very complex context clearing skill that checks everything was worked, touched,
> decided in the current session and ensures to update everything impacted including ADR's. Then I
> constantly clean and archive these files so if something is replaced I decide on the spot if I want
> it completely changes with no trace back or send the old to archive and keep the new one once
> spotted and to make sure is not forgotten the skill will pick it up before context clearing anyway.

"I decide on the spot whether to replace with no trace back or archive it" — rewrite-in-place versus
retain, decided per-fact by a human, with an agent-enforced sweep before compaction. That is the
closest thing to a working answer to failure mode 4 found anywhere. One practitioner, no numbers.

**Cheapest partial answer found, and it is deterministic.** `xyzzy_plugh` on HN 46693985,
2026-01-26 (direct read):

> 3. Living documentation adjacent to your systems. Write markdown files next to your code. If you
> keep systems documentation somewhere else (like some wysiwyg knowledge system bullshit) then you
> must build a markdown-to-whatever sync job (where the results are immutable) else the documentation
> is immediately out of date, and out of date documentation is just harmful noise.
> 4. If it's dead, delete it. You have version control for a reason. Don't keep cruft around.

Git as the archive, the live tree kept empty of dead work. This is what `u/Dhaupin`'s
`/labs/archive` and the pilot project's evidence-directory convention both approximate.

---

## A8 — Failure mode 5: duplicated facts across files

verified: 2026-09-22

**The duplication was found by the person who caused it, in their own rule stack.** Issue #85477's
body, 2026-08-10 (direct read), on a heavy user's harness:

> A heavy user's rule stack (`SOUL.md` / `AGENTS.md` / skills) grew from **58 lines to 92 lines /
> 12 KB in 5 days**. The maintenance failure pattern: the same rule was re-stated 3 times across
> files, yet a corrected version was applied only once — classic uniform-degradation behavior. No
> built-in tool surfaced the duplication, the growth rate, or the approach toward the instruction
> budget.

"Re-stated 3 times across files, corrected in one place" is failure mode 5 with a count. One case,
self-reported by the issue author, not a study.

**A second practitioner reports hand-rolling the rule that no tool implements.** `obarlik` in the
same thread, 2026-08-26 (direct read; the comment is self-identified as written by an agent posting
through its user's account, so weigh it as a report of practice, not as independent testimony):

> our project keeps a standing memory rule — "before adding any new reminder/system instruction,
> check it against every other layer for conflicts" — because no tooling does it. The cost isn't
> hypothetical: layered instructions (system prompt + CLAUDE.md + memory files + skills) can silently
> cancel each other, and the failure only surfaces later as behavior drift, which is the most
> expensive place to notice it.

**The strongest design argument for avoiding duplication in the first place**, from r/ClaudeAI 1vub2nw,
`u/MakaiMorais`, 2026-08-21 (direct read):

> The distinction that saved mine was between rules and facts. Facts are cheap, they sit there and
> get pulled when relevant. Rules are expensive because the model burns budget checking against them
> whether they apply or not. I had your exact problem when everything was always loaded, and fixed it
> by making memory retrieval based, one small file per fact with a description line so recall decides
> relevance before anything enters context. Nothing loads unless it matches, and the always-on
> instructions got cut down to what's genuinely non negotiable.

"One small file per fact with a description line" is this project's `desk-*-answers.md` shape,
arrived at independently and reported as the fix for the over-constrained-harness problem.

**And the cost of the opposite.** `u/effectivescarequotes` in the same thread (direct read):

> If you have a long don't list, that should be delegated to a linter or static code analysis tool
> that can report back to the Ai, just like it would for a human developer.

---

## A9 — Recurring workaround patterns, with counts

verified: 2026-09-22

Counted over the threads in A1. "Reports" means distinct people in distinct comments; one person
posting the same advice twice is counted once. **No pattern below has a published measurement
behind it.** The "measured?" column says whether anyone claimed a result, not whether one exists.

| # | pattern | reported by | measured? | where |
|---|---|---|---|---|
| P1 | **Split mutable-current from append-only-history**; regenerate the current file, never append to it | 4 (`DoggoCentipede`, `MakaiMorais`, `ShotPorter`, `claude-starter-kit`) | no; `ShotPorter` promises eval data "when the stars accumulate" | A6, A8 |
| P2 | **Enforce it with a hook**, not prose — block turn-end or inject at session start | 3 (`claude-starter-kit`, `warrant`, `ShotPorter`'s "MC") | no | A6, A11, A13 |
| P3 | **Shrink the instruction file to facts plus observed-failure constraints**; delete the rest | 5 (`luodaint`, `verdverm`, `JasonSage`, `MakaiMorais`, `xyzzy_plugh`) | no | A2, A10 |
| P4 | **One fact per file**, with a description line so retrieval decides relevance | 2 (`MakaiMorais`, `Beautiful-Energy2169`'s mtime audit) | no | A8 |
| P5 | **Delegate the don't-list to a linter/static analysis rather than a prompt** | 2 (`effectivescatequotes`, `groby_b`) | no | A8, A10 |
| P6 | **Abandon custom memory; use plain files over MCP, or nothing** | 3 (`indemzeit`, `bartek_666666`, OP of 1vub2nw) | no | A10 |
| P7 | **Stale-reference check in pre-commit** (paths and names still exist) | 3 (`check-docs`, `agents-lint`, `rule-audit`) | no | A11 |
| P8 | **Archive dead work to a separate folder / directory** rather than shrinking it | 2 (`Dhaupin`, `xyzzy_plugh`'s "delete it, you have version control") | no | A7 |
| P9 | **Rank superseded items down in search** instead of removing them | 2 (`Sea-Perception1619`, and the append-only camp in 1vkhzd5) | reported **failing** by its own author | A5 |

The counts are small and the venues overlap — several of these are one person's comment in one
thread. What the table shows is that **seven different fixes circulate and none is validated**, which
is the honest answer to "has anyone solved this".

`u/indemzeit` (r/ClaudeAI 1vub2nw, direct read) is the clearest statement of P6:

> I gave up on structured memory after a similar dead end. Plain files in a folder, served over MCP
> so Claude reads them at session start, turned out to be enough. I keep mine in gcontext but any
> folder-over-MCP setup works. The less structure you force, the less it fights you.

and `u/bartek_666666` (same thread, direct read):

> I'm using markdown, and thats all it needs for memory. Also I'm trimming it after a while, no need
> to waste tokens on reading full context

---

## A10 — P3 in detail: what practitioners say actually gets followed

verified: 2026-09-22

This is the sub-question "which instructions survive contact", and two practitioners gave
operational-taxonomy answers that are more useful than the best-practices posts.

**`u/luodaint` on HN 48160604, 2026-05-17 (direct read)** — CLAUDE.md has three kinds of content and
they behave differently:

> **Facts** (directory organization, commands, references in docs), always work. There's no
> discussion here.
>
> **Constraints related to regression prevention** (specifying certain restrictions based on a
> particular failure) – work consistently if each individual item doesn't exceed one sentence and
> describes a failure I've already observed. Example: "Validate JWT at the route level, not the
> component." It works since the agent was caught doing it incorrectly. ... "Always call workspace
> provisioning when creating a user" – ditto.
>
> **Behavior rules** (rules regarding comments, naming, dos and don'ts) – this is where the OP fits
> in. My observation: such rules work if they are concise, specific, and based on an already existing
> failure pattern. They don't work if created in anticipation of some [failure].

**`u/JasonSage` on HN 49376287, 2026-08-20 (direct read)** — the same point as a staleness mechanism:

> Most things I wrote in a Claude.md because Opus 4.something was crap had roots in Opus, Claude
> Code, system prompts, and our own bad code we wrote last year. None of these things exist today but
> the Claude.md file can stick around like it's all still necessary. ... I too am morally opposed--I
> abide by a 100 line (short lines, not paragraphs) limit and edit it rarely.

**`u/groby_b` in the same thread (direct read)** gives the counter-position, one sentence:

> The one small bit of truth is that yes, instructions might become outdated, and they might affect
> negatively how the system performs. You fix that by... updating your instructions.

**`u/bisonbear` in the same thread's sibling, HN 48160604 (direct read)** — why one bad line is
expensive at team scale:

> if I write a bad AGENTS.md for a repo with 100 engineers actively working in it, then every agent
> for every engineer gets worse, without anyone really noticing.

Also from the same thread, `u/verdverm` (direct read), which is P3 stated as a policy:

> Now I only put what amounts to a table of contents and some highlights of important things. Other
> info goes in other markdown, either localized agents.md or a directory of references. ... I now
> have agents write more of that stuff but deeply review it.

**Translation for the pilot project:** the rule "state how it is now" is a *fact-shaped* instruction and
should be followed; the rule "when you find an error, edit the original rather than appending a
correction" is a *behaviour rule created in anticipation of a failure*, which is the class these
practitioners report does not hold. That is a direct argument for moving the pilot
project's rewrite-in-place rule from prose in CLAUDE.md into a mechanism — which is what P2 is for.

---

## A11 — Deterministic detectors not covered by the prior surveys

verified: 2026-09-22. Maturity from the GitHub API on 2026-09-22.

**`rule-audit` — the one that plausibly does contradiction detection, and it is alive.**
[`hermes-labs-ai/rule-audit`](https://github.com/hermes-labs-ai/rule-audit). **3 stars, 1 fork,
created 2026-04-17, last push 2026-09-21, MIT, Python, `pip install rule-audit`, PyPI published,
CI badge.** Announced in issue #85477 by `roli-lpci` on 2026-09-16 with a disclosure that it is theirs.

README (direct read):

> rule-audit is a static analyzer for AI system prompts: it parses a prompt into normative rules and
> reports logical contradictions, coverage gaps, priority ambiguities, meta-rule paradoxes, and
> absolute-rule edge cases — without calling an LLM.

Its stated distinguishing claim, from the issue comment (direct read):

> `tenet` reports conflict detection as **negation-pair candidates only**, and says explicitly that
> semantic conflicts with no shared wording are not implemented because a string comparison cannot
> see them. `rule-audit` attacks that specific residue — it parses rules into normative form first,
> so it flags contradictory or unprioritized pairs that share no vocabulary ... It does **not** do
> instruction-budget counting or duplicate-rule clustering.

**Scope limit, from its own text: it reads "AI system prompts and agent instructions (`CLAUDE.md`,
`AGENTS.md`, `SOUL.md`)" — rule files, not arbitrary findings documents.** Whether it generalises to
prose is OPEN QUESTION 2. This is the only tool found that claims deterministic *contradiction*
detection rather than string matching.

**`tenet`** — [`Hirannad/tenet`](https://github.com/Hirannad/tenet). **0 stars, created 2026-08-25,
last push 2026-08-28**, MIT, Shell. Announced in issue #85477 by its author on 2026-08-28 with a
disclosure. README (direct read) states the limits honestly:

> - **Duplicate rules** — normalized exact-match clusters across layers, auto-memory included.
> - **Conflict detection** — negation-pair candidates only. Semantic conflicts with no shared wording
>   are reported in the output as not implemented, because a string comparison cannot see them.
>
> It also carries an enforcement-table format — every rule names the mechanism that catches it when
> broken, or `none` plus the reason — and a 100-point scoring rubric.
>
> None of this makes a rule binding; only PreToolUse hooks can do that, and only for tool calls.
> Visibility only.

Duplicate detection here is **exact-match clustering after normalisation** — it will not find the
same fact stated two different ways. Zero stars and a four-day commit window.

**`agents-lint` — the most-adopted stale-reference linter, with a caution.**
[`giacomo/agents-lint`](https://github.com/giacomo/agents-lint). **14 stars, 2 forks, created
2026-02-27, last push 2026-09-22 (today), MIT, TypeScript, on npm, zero-dependency CLI.** README
(direct read):

> **Detect stale references and context rot in your AGENTS.md, CLAUDE.md, and AI memory files.**

Its sample output shows a check the pilot project should NOT copy:

> Cross-File Consistency (1 issue)
>   ℹ Path "./src/payments" referenced in AGENTS.md but not in CLAUDE.md
>     → Consider documenting in all context files, or run --fix to suppress.

**That suggestion is failure mode 5 with a tool behind it** — it tells the user to copy a fact into
every context file. Flag this if `agents-lint` is ever proposed here. Its other checks (paths, dead
npm scripts, obsolete framework patterns, broken memory-index links) are the ones worth having, and
they map to failure modes 1 and 3.

It also relays two 2026 research claims, quoted in its README as block text (direct read). The ETH
Zurich line — *"LLM-generated context files reduced task success by 2–3% while increasing cost by over
20%"* — **could not be confirmed in that form**; see A12 for what the paper's abstract actually says.
The Addy Osmani line is worth carrying:

> *"None of the major coding agents expose the lifecycle hooks to make this architecture easy to
> build. That's a tooling gap waiting to be filled."* — Addy Osmani, Google

**`check-docs` — the smallest thing that works, and it is 39 lines of `sh`.**
[`ipaulsmith/check-docs`](https://github.com/ipaulsmith/check-docs). **1 star, 0 forks, created
2026-09-18, last push 2026-09-19, Shell.** Posted to r/LLMDevs 1wk0oyy on 2026-09-18 (direct read):

> Agent instruction files like `CLAUDE.md` and `AGENTS.md` can keep references to files or components
> that no longer exist. Then Claude follows those instructions and it looks like hallucination, when
> the actual problem is stale project context. I made **check-docs** to catch that before commit ...
> It's a 39-line `sh` script that checks: referenced paths still exist; deleted names don't remain in
> instructions; instruction changes are staged before validation. No model call, no package, no
> service.

Read the script (direct read of `check-docs.sh`): it normalises paths, resolves `@import` lines the
way Claude Code's parser does (excluding code spans and fenced blocks), and treats "present" as *in
the git index* inside a repo. **It is a rewrite-in-place enforcer only in the weak sense** — it blocks
a commit when a name you deleted still appears in an instruction file, which catches the "append a
correction, leave the old text" case only when the old text names something deleted. It does not
detect contradictions or duplicate facts. No comments on the post, so no field reports.

**`warrant` — checks attached to individual claims; the most interesting primitive found.**
[`alisorcorp/warrant`](https://github.com/alisorcorp/warrant). **7 stars, 0 forks, created
2026-08-27, last push 2026-08-27 (one day), MIT, Python, single file, stdlib only.** Announced
2026-08-27 on r/ClaudeCode 1w09jan (direct read):

> I built /warrant because old shit gets inherited as fact all the time. And then you get screwed when
> you're reporting something out of date, or a new Claude session goes about its way for an hour
> thinking x = true when x was only true last week. Factual claims in READMEs, handoffs, agent
> memory. They all need checking, and the answer shouldn't come from a model guessing.
>
> If a sentence can become false without anyone touching the doc, it probably needs a check. Test
> counts, file paths, "this is currently enabled," that kind of thing. Keep the check beside the
> claim and rerun it when you need to trust the doc again.

The mechanism, from the README (direct read) — an HTML comment beside the claim holding the command
that establishes it:

```markdown
The gate passes with 0 failures. <!-- warrant: run="./scripts/gate.sh" contains="0 failures" -->
HEAD is `a1b2c3d`. <!-- warrant: run="git rev-parse --short HEAD" contains="a1b2c3d" -->
```

with four verdicts, and the distinction between the middle two is the design idea:

> | `VERIFIED` | the check ran and agreed. You know it's true. |
> | `STALE` | the check ran and disagreed. You know it's wrong. |
> | `BROKEN` | the check could not run. **You no longer know.** |
> | `ASSERTED` | deliberately unwarranted, with a recorded reason. |

This maps onto a `verified: <date> @ <commit>` stamp taken seriously: it is the stamp made
executable. It is **not** contradiction or duplication detection — it checks claims that can be
mechanically re-established. One day of commits, no forks, no field reports. Its README also notes
the nearest prior art it could find, Google's Open Knowledge Format v0.2 §10 "Attested Computation",
whose implementations "store those fields but don't actually run anything" — **search summary of a
secondary source; OKF itself was not read.**

**`vericontext` — hash-based, fail-closed.** [`amsminn/vericontext`](https://github.com/amsminn/vericontext).
**8 stars, 3 forks, created 2026-02-24, last push 2026-07-26, TypeScript.** Description (GitHub API):
"Deterministic, hash-based verification for docs that reference code. Fail-closed. Zero fuzzy
matching." Listed for completeness; the README was not read, so treat the description as the vendor's
claim. Last push is two months before this survey.

---

## A12 — LLM-based drift auditors

verified: 2026-09-22.

These call a model rather than a checker, so they are non-deterministic and cannot be a gate.

**`claude-drift`** — [`marky291/claude-drift`](https://github.com/marky291/claude-drift). **1 star,
0 forks, created 2026-06-07, last push 2026-06-10, MIT.** A Claude Code plugin;
`/claude-drift:drift-check`. Three drift classes, README direct read: "**Reference drift**" (a named
path/command no longer exists), "**Context drift**" ("The *description* of your architecture ...
no longer matches the code — **even when every file path still resolves**"), and "**Legacy
narration**" (quoted in full in A4). The badge claims "engine: reasoning, not regex". **It covers
Claude Code artifacts only — `CLAUDE.md`, skills, agents — not an arbitrary markdown folder**, which
is the same scope limit as `tenet` and `rule-audit`.

**`/doctor`'s existing behaviour**, per `bcherny` in A2 (direct read): "checks your checked-in
CLAUDE.md for content Claude can derive from the codebase and proposes trims". Native, no install.
It does not detect duplication or conflicts — same source.

**The one research citation worth having, resolved.** Issue #85477 cited `arXiv:2602.11988` and
`agents-lint` cited "ETH Zurich, ICSE 2026" with a different number. The paper is real; abstract
fetched from arxiv.org on 2026-09-22 (direct read):

> **Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?** ...
> we find that providing context files does not generally improve task success rates, while
> increasing inference cost by over 20% on average. This observation holds across different LLMs,
> coding agents, and for both LLM-generated and developer-committed context files. Specifically, we
> find that while instructions in the context files are well followed by coding agents, repository
> overviews, although popular and recommended by model providers, are not helpful.

So: the **>20% cost increase is confirmed from the primary source**. The **"2–3%" figure in
`agents-lint`'s README is not in the abstract and is UNVERIFIED** — a caution about that tool's
README, not about the paper. The finding that matters here: **instructions are well followed;
repository overviews are not helpful.** That is the empirical backing for P3 and for
`u/luodaint`'s taxonomy in A10.

---

## A13 — Hook-enforced protocols and session-checkpoint tools

verified: 2026-09-22.

**`daimon`** — [`Daily-Nerd/daimon`](https://github.com/Daily-Nerd/daimon). **19 stars, 3 forks,
created 2026-07-03, last push 2026-09-22 (today), Python.** Description: "Dream-briefing for AI
coding agents: a cognitive checkpoint at session end, a 'while you were away' briefing at session
start." Its author is the practitioner in A5 who reported that ranking superseded items down fails.
The self-described failing of its own design, quoted there verbatim, is the most useful thing this
survey found about it.

**`claude-starter-kit`** — [`jonton26/claude-starter-kit`](https://github.com/jonton26/claude-starter-kit).
**0 stars, 0 forks, created 2026-09-10, one commit, no license.** Full design read in A6. Its
`Stop`-hook backstop and `SessionStart` injection are the two mechanisms most worth borrowing; the
repo itself is nine days old and unadopted.

**`claude-code-auto-memory`** — [`severity1/claude-code-auto-memory`](https://github.com/severity1/claude-code-auto-memory).
**158 stars, 16 forks, created 2025-11-26, last push 2026-04-18, MIT, Python.** The most-starred
tool found in this survey's tool sweep. Advertised by search summary as "watches what Claude Code
edits, deletes, and moves, then automatically updates CLAUDE.md in the background". **Its README was
not read, and the search summary is the only description obtained — treat the mechanism as
UNVERIFIED.** Note the maturity shape: 158 stars but no push for five months. Do not credit it as
working without reading it.

**`contextrot`** — `Priyanshu-byte-coder/contextrot`, "Analyze where Claude Code degrades from its own
session log", Show HN 2026-07-05, 3 pts, 0 comments. **Title-level only; repo not checked.**

---

## A14 — Commercial and productised offerings

verified: 2026-09-22. Listed because their existence is evidence the problem is commercial, not
because any of them is recommended. All are unverified beyond their own announcements.

- **Daemons** (charlielabs.ai), Show HN 2026-04-21, 70 pts / 31 comments. Direct read of the post:
  > The one thing we've noticed over the last 3 months is that the more you use agents, the more
  > work they create. Dozens of pull requests means older code gets out of date quickly.
  > Documentation drifts. ... we pivoted away from agents and invented what we think is the
  > necessary next step.
  Productised drift-repair, specified by a version-controlled `DAEMON.md` in the repo, run on a
  schedule or on events, on the vendor's cloud runtime. Members of the engineering team answered
  questions in the thread. It is the only offering found that treats "documentation drifts" as a
  first-class product category.
- **Vilix AI** — named by the OP of the r/ClaudeCode handoff thread (A6) as the MCP memory layer they
  switched to. **Marketing post; no independent report found.**
- **KLYPIX** — named by `u/dahshan-labs` in r/BuildWithClaude 1vk5g7m (2026-08-10, direct read), an
  MCP-served project-state store with "current state first, history available". The OP's own framing
  of the problem is the cleanest statement of failure mode 2 found:
  > The problem isn't really getting an agent to remember *something*. It's when it remembers
  > something that *used to be right*.
- **gcontext** — the plain-folder-over-MCP setup `u/indemzeit` uses (A9). **Search-summary only; not
  checked.**
- **Cartographer** — r/ClaudeCode 1wdor4f, 2026-09-11: a knowledge base that provisions `~/.claude`
  and keeps a team in sync. **Search summary only; not checked.**

---

## A15 — The two prior-survey tools that practitioners actually report using here

verified: 2026-09-22. Both named again only because someone in these threads reports using them.

**ADRs.** desk-kbtools' A20 assessed ADR tools. In r/ClaudeCode 1wmoost (2026-09-22, 147 comments)
ADRs are the most-recommended answer to "how do you carry decisions across sessions" — the thread's
own automated summary calls them "the most popular solution by far" (**search summary of a
bot-generated digest, not direct read of those comments**), which matches the directly-read
comments in A5 and A7 that discuss `DECISIONS.md`, `TASKS.md`, `MEM.md` and an ADR archive.

The load-bearing report is the OP's rebuttal, `u/delimitdev` (direct read):

> An ADR does take an afternoon, and if it covers your case it's the right tool. Where it runs out
> for me is once several agents and people are working the same repo: the ADR says what was decided,
> not whether it shipped or what's being built on it now.

So: ADRs are the community's default and are reported as insufficient for the "did it happen"
question, which is `daimon`'s and `claude-starter-kit`'s territory. No prior-survey ADR tool
(log4brains, adr-tools, MADR) was named by any practitioner in these threads.

**Doorstop and sphinx-needs were not mentioned once** in any Reddit, HN, or issue-tracker thread
read for this survey. Their suspect-link mechanism from desk-kbtools A3/A6 has no practitioner
usage report to match it.

---

## A16 — Does a usable existing solution exist?

verified: 2026-09-22.

**Partial. Nothing found solves the stated problem; several things solve named sub-parts, and each
one's scope limit is the reason it isn't the answer.**

**What exists and works deterministically:**

- *Stale references in instruction files* (paths, names, dead scripts): `agents-lint` (14 stars,
  active), `check-docs` (39 lines of `sh`), `vericontext` (hash-based). Deterministic, installable,
  run in CI or pre-commit. **Scope: they check that named things still exist. They do not read for
  meaning.**
- *Contradictions between rules*: `rule-audit` (active, PyPI, CI, the only deterministic
  contradiction detector found) and `tenet` (negation-pair matching only, by its own admission).
  **Scope: rule/instruction files only. Neither claims to work on prose findings documents.**
- *Claims that can be mechanically re-established*: `warrant` — inline re-runnable checks with a
  `STALE`/`BROKEN` distinction. **Scope: the author must hand-write a check per claim; it does no
  discovery.**
- *Stale-content auditing of Claude Code artifacts by an LLM*: `claude-drift`. **Scope: CLAUDE.md,
  skills, agents; 1 star; unmaintained since 2026-06-10.**

**What does not exist:**

- **No deterministic detector for a duplicated fact across two markdown documents.** Every duplicate
  detector found is scoped to rule files and detects *identical or near-identical rule text*.
  Anthropic's maintainer confirms the native version of this does not exist (A2). `agents-lint`'s
  cross-file check actively suggests the opposite fix.
- **No rewrite-in-place enforcer.** Nothing found forbids appending a correction, or detects that a
  superseded claim is still the first hit. The nearest is `check-docs` blocking a commit that leaves
  a deleted name in an instruction file — which is a string check, not a semantic one.
- **No measured result for any intervention.** See OPEN QUESTION 1.

**The three findings that should shape whatever is decided:**

1. **The hook is the mechanism, not the prompt.** Every practitioner who reports a durable fix
   reports it as a hook, a script, or a CI gate (P2, P7). The venue's own maintainer says the same
   from the other side (`bcherny`: "only PreToolUse hooks can do that"). And `u/luodaint`'s taxonomy
   gives the reason in advance: a rule created *in anticipation of* a failure is the class that does
   not hold (A10). Anthropic's own docs — *"Claude treats them as context, not enforced
   configuration"* (A2) — are the same statement from the vendor.
2. **The mutable-current / append-only-history split is the convergent design.** Four independent
   practitioners arrived at it (A6, A9-P1), and the one whose implementation ranks superseded items
   down instead of removing them reports it failing (A5). The split is what `claude-starter-kit`
   writes down as "**Overwritten, not appended**" for the current file and a separate append-only
   journal beside it.
3. **The strongest argument against the whole approach is the append-only camp's, and it is not
   weak.** "You don't erase a bad entry, you post the correction" (A4) is the position of the
   agent-memory community, backed by accounting and event-sourcing practice. The pilot project's answer
   has to be about *retrieval*, not storage — which is exactly what `Sea-Perception1619` reports
   failing when done by ranking. If the coordinator wants the rewrite-in-place design, the honest
   framing is that it trades auditability for retrieval reliability, deliberately, and needs a
   mechanism (P2) rather than a rule.

**What I would try first, cheapest and most likely to answer something:** `rule-audit` on the pilot
project's own findings file, to see whether a prose document parses into anything useful (OPEN
QUESTION 2). It is one `pip install` and one command, and the answer is a hard yes/no that no amount
of further searching substitutes for. Second: the `Beautiful-Energy2169` audit — relative mtimes plus
inbound-reference counts over `desk-*-answers.md` and the findings files — is doable with a shell
script and produces the prevalence figure nobody has published.

---

## A17 — Coverage of the five failure modes, by candidate

verified: 2026-09-22.

| candidate | 1 append/rewrite | 2 supersession | 3 indexes | 4 dead work | 5 duplication | deterministic | scope | maturity |
|---|---|---|---|---|---|---|---|---|
| `rule-audit` | ✗ | ✗ | ✗ | ✗ | partial (rule text only) | **yes** | rule files | 3★, active |
| `tenet` | ✗ | ✗ | ✗ | ✗ | partial (exact match) | **yes** | rule files | 0★, 4 days |
| `agents-lint` | ✗ | ✗ | partial (memory-index links) | ✗ | ✗ (**encourages** it) | **yes** | agent context files | 14★, active |
| `check-docs` | partial (deleted names) | ✗ | ✗ | ✗ | ✗ | **yes** | CLAUDE.md/AGENTS.md | 1★, 1 day |
| `warrant` | partial (per-claim checks) | ✗ | ✗ | ✗ | ✗ | **yes** | any markdown, hand-checked | 7★, 1 day |
| `vericontext` | ✗ | ✗ | ✗ | ✗ | ✗ | **yes** (claimed) | docs referencing code | 8★, 2 mo stale |
| `claude-drift` | **yes** (names legacy narration) | partial | ✗ | ✗ | ✗ | no (LLM) | Claude Code artifacts | 1★, abandoned |
| `/doctor` (native) | ✗ | ✗ | ✗ | ✗ | ✗ | no (LLM) | CLAUDE.md trims | shipped |
| `claude-starter-kit` | **yes** (overwrite protocol) | **yes** (ledger/state split) | partial (regenerated) | partial (frictions inbox) | ✗ | **yes** (hooks) | its own repo | 0★, 9 days |
| `daimon` | partial | reported **failing** by author | partial | ✗ | ✗ | partly | session boundaries | 19★, active |
| `Daemons` (commercial) | partial | ✗ | ✗ | partial | ✗ | no (LLM) | repo, cloud-hosted | commercial |

Read the columns top to bottom: **failure modes 1, 4 and 5 have no working deterministic answer
anywhere**, and the two candidates that name failure mode 1 explicitly (`claude-drift`,
`claude-starter-kit`) are the least adopted things in the table. Failure mode 3 has only partial
answers. This is the gap the answer to A16 is describing.
