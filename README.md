# kblam

## For humans

### What kblam is

kblam is a command-line tool for a knowledge base of research findings that several LLM agents
write. Each finding is one Markdown file that states one claim, with YAML frontmatter naming its
evidence, its scope and the findings it depends on. The knowledge base holds only current facts:
when a claim turns out to be wrong, the finding is rewritten in place so that it states what is true
now, and the earlier version survives only in git history. kblam enforces this with checks on the
files rather than with instructions to the agents, because instructions alone did not work.

### Why it exists

kblam was built against a pilot corpus of about 34,000 lines of agent-written findings documents,
in which five failure modes kept recurring despite written rules. A falsified claim stayed where it
was while its correction was appended somewhere else, so agents that searched landed on the wrong
one. New documents superseded old ones instead of editing them. Hand-maintained indexes drifted out
of date. Failed experiments were kept whole with a correction tacked on. The same fact was copied
into several files, and the copies diverged. Agents also reported following the rules when the
files showed otherwise, so kblam treats a check on the files as the only evidence. SPEC.md §1
describes these cases.

### How it works

Findings live at `findings/<topic>/F-NNNN-<slug>.md`, and `findings/INDEX.md` is generated from
them. The only way into `findings/` is `kblam put`, which validates the whole knowledge base as it
would be after the write and refuses the write if anything fails. The deterministic rules, K1 to
K11 in SPEC.md §5, check the frontmatter schema, that cited evidence exists, that no dependency
points at a finding rewritten since it was last checked, that no finding uses revision-history
language such as "was wrong" or "superseded", length limits, the generated index, stray files,
near-duplicate claims, that every excerpt marked verbatim occurs exactly in its cited source, and
the rules for claims that only a retired document reports.

Once those rules pass, `put` compares the new finding with the most similar existing ones. It
selects them with a local embedding model when ollama serves one, and with BM25 otherwise. It then
asks Jev, a model from TypeSafe AI reached through OpenRouter that answers multiple-choice and
yes-or-no questions with probabilities and a confidence, whether the new claim restates, extends or
contradicts each of them, and whether it reads as a correction of an earlier claim. Numeric
quantities are compared in code, not by Jev. A confident duplicate or contradiction, a claim that
reads as a correction, or a conflicting quantity refuses the write and says what to edit instead,
usually the existing finding. Weaker signals open review items, which a coordinator or a librarian
agent settles.

`kblam init` adds guards around this for Claude Code and for git. Claude Code hooks deny direct
writes under `findings/` and under `.kblam/`, where kblam keeps its state. When an agent stops,
another hook validates anything that changed outside `kblam put` and blocks the agent from finishing
while the tree fails; an agent that cannot fix it is released rather than looped. A git pre-commit
hook refuses commits while `kblam validate` fails. A project rule tells agents how to read findings,
a skill tells them how to write them, and every message that stops a write points to the skill.
Several agents can write at once: `put` takes a lock, and it refuses to overwrite a finding that
changed after the writer staged its edit.

### Requirements

kblam needs Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). The Jev check needs an
OpenRouter API key with credit. By the spec's estimate a write with 30 candidates costs about $0.001
on one-sentence claims, and more on longer ones; `kblam cost` reports what was actually spent.
Candidate selection uses ollama with the `embeddinggemma:300m` model when it is running at
`http://127.0.0.1:11434`, and falls back to BM25 without it. The Claude Code hooks run through bash
(Git Bash on Windows) or PowerShell 7. kblam was developed on Windows with Git Bash and PowerShell,
and its test suite also runs on Linux.

### Installing (once per machine)

```sh
uv tool install git+https://github.com/hungryboygeorge/kblam
ollama pull embeddinggemma:300m        # optional: embedding candidates instead of BM25
```

This puts `kblam` on your PATH. Save your OpenRouter key, alone on one line, in `~/kblam/jev!.txt`,
which is where kblam looks by default, or set `OPENROUTER_API_KEY` instead. Quote the file name in
interactive bash, where `!` triggers history expansion.

### Setting up a repository (once per project)

```sh
cd your-research-repo
kblam init
```

`kblam init` runs inside a git repository. It writes `kblam.toml`, the project configuration, whose
`[kb] scopes` vocabulary you should edit for your project. It also creates `findings/INDEX.md` and
adds a `.gitattributes` line that stops git from converting line endings under `findings/`, a
`.gitignore` line for `.kblam/`, the Claude Code rule and skill under `.claude/`, kblam's hook
entries in `.claude/settings.json`, a line in `CLAUDE.md`, and the git pre-commit hook. It reports
what it did to each file. It never overwrites `kblam.toml`, and it never overwrites a pre-commit hook
that is not kblam's. It finishes by running each hook once to check that it answers. Review the files
and commit them; a clone on another machine then carries the setup and needs only the per-machine
install. Later, `kblam init --update` rewrites the rule, the skill, the hook entries and the
pre-commit hook to match the installed version of kblam.

### Writing a finding

```sh
kblam new calibration "The two curve types are not two analog gains"
# edit the file it prints: .kblam/staging/F-0001-the-two-curve-types-are-not-two-analog-gains.md
kblam put .kblam/staging/F-0001-the-two-curve-types-are-not-two-analog-gains.md
```

`kblam new` allocates the next ID and writes a skeleton under `.kblam/staging/`. The first paragraph
after the frontmatter is the claim, and the rest of the file supports it. A finished finding looks
like this:

```markdown
---
id: F-0001
title: The two curve types are not two analog gains
topic: calibration
label: observed
scope: [any]
evidence: [evidence/run1/log.txt]
verified: 2026-09-25
---

**Claim.** The two curve types agree to about 0.1% (median ratio 1.0017), so they are not two
analog gains.

<!-- verbatim: evidence/run1/log.txt:1 -->
> median ratio 1.0017 over 2048 pixels
```

When `put` succeeds, it moves the file into `findings/calibration/`, regenerates the index and
deletes the staged copy. When it refuses, `findings/` is unchanged and the staged file stays for
you to fix. To change an existing finding, `kblam edit F-0001` stages a copy; edit it and `put` it,
and the finding is replaced in place. SPEC.md §4 and the installed skill describe every field.

### Commands

| Command | What it does |
|---|---|
| `kblam new <topic> "<title>"` | Stage a skeleton for a new finding and print its path. |
| `kblam edit <id>` | Stage a copy of an existing finding for rewriting. |
| `kblam put <file>` | Validate, run the Jev check, and move a staged finding into `findings/`. |
| `kblam validate` | Run every rule and list open review items; exit 1 on any failure. |
| `kblam validate --record` | Accept a change made outside kblam (a `git pull`, say) once the tree is clean. |
| `kblam check [<id> ...]` | Jev-check findings already in the tree: those named, or every one not checked since its claim, scope, quantities or evidence last changed. |
| `kblam check --pending` | Retry the findings Jev could not answer for. |
| `kblam audit` | Ask every candidate pair and question that has no cached answer. |
| `kblam resolve <R-id> --distinct "<reason>"` | Close a review or rejected item whose two findings state distinct facts. |
| `kblam ack <dependent> <target>` | After re-reading a rewritten finding, record that a finding depending on it still holds. |
| `kblam deps <id>` | List a finding's dependencies and dependents, marking stale ones. |
| `kblam index` | Regenerate `findings/INDEX.md`. |
| `kblam cost` | Summarise Jev requests, tokens and spend by day and by kind. |
| `kblam prompt-id` | Print the id of this project's Jev question wording. |
| `kblam jev-smoke` | Ask Jev one synthetic pair and one question, as a live check of the key and endpoint. |
| `kblam init [--update]` | Set up the current git repository. |

`kblam hook <event>` is the entry point for the Claude Code hooks and is not meant to be run by
hand. The exit status is 0 on success; 1 when kblam refuses (validation errors, open items, a
request it will not carry out, or Jev unavailable); 2 when there is no usable `kblam.toml` or the
arguments are wrong; 3 when another kblam write held the lock for too long, so retry; and 4 when Jev
or a quantity conflict rejected a `put`.

### Review items, and running without Jev

Jev's verdicts are gated by thresholds that a calibration run measured for one served model,
`typesafe/jev-1.13-20260917`, and for the default question wording (SPEC.md §10). `kblam.toml`
records both. If the model that answers or your wording differs from what the thresholds were
measured on, no Jev verdict rejects a write: every one that fires becomes a review item, and kblam
warns that recalibration is needed. Quantity conflicts still reject, because code decides them.

An open review item, or an unchecked item left when Jev could not be reached, makes
`kblam validate` fail, and the pre-commit hook with it, until the item is closed. A review item
closes when either of its findings is rewritten (the `put` re-checks the pair and raises a new
item if a verdict still fires), or when `kblam resolve ... --distinct` records that Jev misread
two distinct facts. An unchecked item closes when `kblam check --pending` gets an answer. This
means that without an OpenRouter key every `put` succeeds but leaves an unchecked item, and
commits are refused. To run without Jev on purpose, delete the `[jev.thresholds]` table from
`kblam.toml`. kblam then compares only numeric quantities and says so.

### Changes that arrive from outside kblam

kblam records a digest of `findings/` in `.kblam/tree.hash` after each of its own writes. A change
that arrives any other way, such as a `git pull` or a branch checkout, leaves the digest stale, and
the Stop hook then validates the tree every time an agent stops. Run `kblam validate --record` to
accept the change: it Jev-checks the findings that changed, validates, and records the new digest
if everything is clean.

### Configuration

`kblam.toml` holds the project's vocabularies (`labels`, and the `scopes` a finding may apply to;
topics are simply folders), the claim and file length limits, the revision-history phrases that
K4 looks for, the lock timeouts, the Jev endpoint, model and thresholds, the embedding settings, and
the wording of the Jev questions. SPEC.md §9 documents every key. The file is committed, so it holds
nothing machine-specific, and the API key never goes in it.

### Status

The design is settled except for the open questions in SPEC.md §13. Two commands in the spec's
command list are not implemented yet: `kblam recheck`, which would re-run each finding's optional
`check:` command, and `kblam migrate`, helpers for splitting an existing document into findings. The
`[kb] evidence_roots` setting is read but not yet enforced, so a finding may cite evidence from
anywhere inside the repository. An MCP server for a librarian agent without shell access is deferred
(SPEC.md §12, M7).

### Documentation and license

SPEC.md is the design and the reference for behaviour: the finding format, the rules, the Jev
decision policy, the hooks and the calibration. REFERENCES.md lists the external documentation kblam
was built against, and `research/` and `desk-jevdocs-answers.md` hold the research notes the spec
cites. kblam is released under the VibeCoded AI-Slop License v1.0 in LICENSE: do whatever you want
with it; there is no warranty and no support.

## For agents

This section is for coding agents. Which part applies depends on where you are working.

### In a repository that uses kblam

If you are working in a project whose findings kblam manages, you do not need this README. The
project's `.claude/rules/kblam-findings.md` loads when you open a finding, and the `kblam-write`
skill covers writing one; if you have no Skill tool, read `.claude/skills/kblam-write/SKILL.md` as a
file. The essentials are these. Never write under `findings/`, or under `.kblam/` outside
`.kblam/staging/`, yourself: the hooks deny it, and the Stop hook catches what they miss. Add or
change a finding with `kblam new` or `kblam edit`, edit the staged copy, and `kblam put` it. When a
put is rejected as a duplicate or a conflict, edit the existing finding it names instead of
rewording yours until it passes. Treat `kblam validate` as the only evidence that the knowledge
base is clean, including after your own work.

### Working on kblam itself

#### SPEC.md is the source of truth

SPEC.md is the design, and its "As built" paragraphs record implementation details. Read the
section for the area you are changing first; module docstrings cite their sections. Many decisions
carry an attribution and a date, such as "(user, 2026-09-24)": do not reverse one of those without
asking. A change in behaviour updates SPEC.md in the same commit. A change to a message or an exit
code that the `kblam-write` skill describes (`src/kblam/assets/skills/kblam-write/SKILL.md`)
updates the skill in the same commit too. Details of the Jev API are cited to entries in
`desk-jevdocs-answers.md` and to the TypeSafe pages listed in REFERENCES.md; check them before
changing how requests are built or answers are read.

#### Setup and tests

```sh
uv sync
uv run pytest                                  # about 30 s
uv run kblam --root <path to a scratch KB> validate
```

Expect two skips: a test of case-insensitive paths that runs only on Windows, and the live Jev test,
which runs only with `KBLAM_LIVE_JEV=1` and a real `OPENROUTER_API_KEY` and costs a fraction of a
cent. No other test may reach the network. Jev is a fake `httpx2.MockTransport` (`ScriptedJev` and
the `jkb` fixture in `tests/test_check.py`), and ollama is a local fake server (`FakeOllama` in
`tests/test_embed.py`). The fixtures in `tests/conftest.py` set `embedding_model = ""`, so a real
ollama running on the machine cannot change candidate selection, and point HOME at a scratch
directory, so no test reads a real key file. Build knowledge bases with the `kb` fixture under
`tmp_path`, never in this repository.

#### Where things are

| Module | Holds |
|---|---|
| `entry.py` | The `kblam` console script. It dispatches `kblam hook` without importing the CLI. |
| `cli.py` | Commands, argument parsing and exit codes (`EXIT_HELP`). |
| `config.py` | Loading `[kb]` from `kblam.toml` and finding the repository root, a resolved path. |
| `finding.py` | Parsing one finding (frontmatter, claim paragraph) and its fingerprint. |
| `view.py` | `KBView`, an in-memory snapshot of `findings/` as it is or as it would be after a put. |
| `rules.py` | The validator rules K1 to K11 and `validate()`. |
| `index.py`, `treehash.py` | `INDEX.md` generation, and the tree digest with the tree.hash rule. |
| `store.py` | `new`, `edit`, `put`, `ack` and `index`, the edit-base guard, and `atomic_write`. |
| `lock.py` | `.kblam/lock`, including breaking a stale lock. |
| `check.py` | Candidate selection, BM25, the quantity comparison, the decision policy and `checks.jsonl`. |
| `embed.py` | The ollama calls, the vector cache and the cosine scores. |
| `jev.py` | The Jev client (retries, throttling, key loading), `pairs.sqlite`, `calls.jsonl`, and `kblam cost`. |
| `jev_prompts.py` | The Jev question shapes, validation of `[jev.prompt]`, and `prompt_id`. |
| `review.py` | `review.jsonl` items, and `check`, `check --pending`, `audit` and `resolve`. |
| `hook.py` | The Claude Code hooks: the PreToolUse deny, and the Stop and SubagentStop validation. |
| `init.py` | `kblam init [--update]`. |
| `assets/` | The files `init` installs: the rule, the skill, the hook entries, the pre-commit hook and the `kblam.toml` template. |

Each test module's docstring says what it covers; `tests/test_rules.py` has at least one passing and
one failing case per rule.

#### Invariants to keep

**Only kblam writes the knowledge base.** `put`, `ack` and `index` are the only writers of
`findings/`, and they replace files there with `store.atomic_write`. They, `new`, `edit`, `resolve`
and the recording step of `check` and `audit` hold `.kblam/lock` for their read-validate-write
span. `put` asks
Jev before it takes the lock and, under the lock, recomputes the candidates against the current tree
and asks only about pairs the tree gained meanwhile. Keep network calls outside the lock.

**The tree.hash rule.** `put`, `ack` and `index` advance `.kblam/tree.hash` only when the tree
matched it before their write (`treehash.record_after_write`), and only `validate --record` accepts a
change made outside kblam. Without this rule, a shell write followed by `kblam index` would silence
the Stop hook.

**A failure is never a pass.** A Jev question that gets no answer leaves the finding unchecked, as
an open `U-` item; it is never recorded as checked. An embedding failure is different by design: the
whole check falls back to BM25, and one check never mixes the two mechanisms.

**Hooks fail open and stay fast.** `kblam hook` always exits 0, and its decision travels only in the
JSON it prints. When the upward search finds no `kblam.toml` it prints nothing; on an invalid
configuration, malformed input or any exception it allows the action and prints a `systemMessage`. PreToolUse runs on every Write, Edit and shell command, so
`entry.py` dispatches it without importing the CLI and `hook.py` imports only `kblam.config` at
module level. The Stop path imports the rest inside `_stop`. Do not add heavier module-level imports
to `hook.py`.

**Messages are written for agents.** Every refusal names the rule, the place and what to do about
it. Every message that stops a write (the deny hooks, the Stop block, and every `put` refusal except
a configuration error) ends with "Load the kblam-write skill for how to fix this.", and tests in
`tests/test_hook.py` check that each one does. The exit codes are fixed (SPEC.md §7).

**Nothing secret or textual goes into the logs.** The API key is held in `jev._Secret` and scrubbed
from error messages. `calls.jsonl` and `checks.jsonl` record IDs, fingerprints and verdicts, never
finding text.

**The question wording belongs to the project.** The Jev questions live in each consuming project's
`kblam.toml`. The code in `jev_prompts.py` owns only the option keys, the question types, the state
shapes and `SHAPE_VERSION`. Changing any of those changes every project's `prompt_id`, which turns
all of that project's reject verdicts into review items until it recalibrates, so treat such a change
as a breaking one.

**Output is deterministic.** `INDEX.md` must be reproducible byte for byte (K7), files are written
with LF line endings, `validate()` returns its issues sorted, and candidate ranking breaks ties by
ID. A `KBView` is treated as immutable once its `findings` or `memo` have been computed; build a new
view instead of changing one.

**Dependencies stay small.** The runtime dependencies are ruamel.yaml and typesafe-sdk. The embedding
path uses only the standard library, by decision (SPEC.md §12, M6.7): urllib and a pure-Python dot
product, with no numpy and no ollama package.

**It runs on Windows.** The main deployment is Windows with Git Bash and PowerShell, where agents may
write through either shell tool. Path handling in `hook.py` therefore covers drive letters, Git
Bash's `/c/...` form, PowerShell's backslash separator on every platform, and symlinked directories,
and `lock.py` checks whether a lock holder is alive through the Win32 API. When you change path or
process logic, test it on POSIX and reason through Windows.

#### Conventions

Match the surrounding code: `from __future__ import annotations`, dataclasses, type hints,
docstrings that say why and cite SPEC.md sections, and lines wrapped at about 110 characters.
Comments and messages are written in full sentences. Tests are named for the behaviour they check,
such as `test_k9_rejects_near_duplicate_claim`. The assets under `src/kblam/assets/` write the
configured findings folder as the token `{{kb_root}}`, which `kblam init` replaces; consuming
projects pick up asset changes with `kblam init --update`.

#### Not implemented yet

`kblam recheck` and `kblam migrate` (SPEC.md §7) and the MCP server (§12, M7) do not exist yet.
`[kb] evidence_roots` is parsed in `config.py`, but no rule reads it. Where you find the code and
SPEC.md disagreeing, ask which one is intended rather than silently changing either.
