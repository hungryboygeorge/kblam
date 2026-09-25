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
writes under `findings/` and under `.kblam/`, where kblam keeps its state, and to `kblam.toml`, which
only a person should change. When an agent stops,
another hook validates anything that changed outside `kblam put` and blocks the agent from finishing
while the tree fails; an agent that cannot fix it is released rather than looped. A git pre-commit
hook refuses commits while `kblam validate` fails, and commits that change `kblam.toml` until a
person has approved the new version. A project rule tells agents how to read findings,
a skill tells them how to write them, and every message that stops a write points to the skill.
Several agents can write at once: `put` takes a lock, and it refuses to overwrite a finding that
changed after the writer staged its edit.

### Requirements

kblam needs Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). The Jev check needs an
OpenRouter API key with credit. By the spec's estimate a write with 30 candidates costs about $0.001
on one-sentence claims, and more on longer ones; `kblam cost` reports what was actually spent.
Candidate selection uses ollama with the `embeddinggemma:300m` model when it is running at
`http://127.0.0.1:11434`, and falls back to BM25 without it. The Claude Code hooks run through bash
(Git Bash on Windows) or PowerShell 7. kblam is used mainly on Windows with Git Bash and PowerShell,
and it is also tested on Linux.

### Installing (once per machine)

```sh
uv tool install git+https://github.com/hungryboygeorge/kblam
ollama pull embeddinggemma:300m        # optional: embedding candidates instead of BM25
```

This puts `kblam` on your PATH. Save your OpenRouter key, alone on one line, in `~/kblam/jev!.txt`,
which is where kblam looks by default, or set `OPENROUTER_API_KEY` instead. Quote the file name in
interactive bash, where `!` triggers history expansion.

A machine that keeps the key elsewhere, calls Jev through an endpoint other than OpenRouter's, or
uses an ollama on another host says so in its own `~/kblam/config.toml`, a `[jev]` table holding any
of `key_env`, `key_file`, `endpoint` and `ollama_url`. These cannot be set in the project's
`kblam.toml`: that file is committed, so anyone who could change it could otherwise make kblam send
some other secret on your machine, or your findings, to a server of their choosing.

### Setting up a repository (once per project)

```sh
cd your-research-repo
kblam init
```

`kblam init` runs inside a git repository. It writes `kblam.toml`, the project configuration, whose
`[kb] scopes` vocabulary you should edit for your project; after editing it, run
`kblam approve-config` before you commit, as described under Configuration. It also creates `findings/INDEX.md` and
adds a `.gitattributes` line that stops git from converting line endings under `findings/`, a
`.gitignore` line for `.kblam/`, the Claude Code rule and skill under `.claude/`, kblam's hook
entries in `.claude/settings.json`, a line in `CLAUDE.md`, and the git pre-commit hook. It reports
what it did to each file. It never overwrites `kblam.toml`, and it never overwrites a pre-commit hook
that is not kblam's. It finishes by running each hook once to check that it answers. Review the files
and commit them. A clone then carries the setup except for the pre-commit hook, which lives inside
`.git/`, and it starts without kblam's state, since `.kblam/` is not committed: no review items and
no record of what Jev has checked. In a new clone, after the per-machine install, run `kblam init`
there as well, which installs the pre-commit hook and leaves the committed files as they are, and
then `kblam validate --record` to accept the cloned tree. With the Jev check on, that first
`--record` asks Jev about every finding. Later, `kblam init --update` rewrites the rule, the skill,
the hook entries and the pre-commit hook to match the installed version of kblam.

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
| `kblam validate --commit` | Also refuse a commit that changes `kblam.toml` without approval; the pre-commit hook runs this. |
| `kblam approve-config` | Show how `kblam.toml` changed and, at a terminal, approve it for commits on this machine. |
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
K4 looks for, the lock timeouts, the Jev model and thresholds, the embedding settings, and the
wording of the Jev questions. SPEC.md §9 documents every key. The file is committed, so it holds
nothing machine-specific, and the API key never goes in it; where the key is read from, and where
requests go beyond OpenRouter and a local ollama, are set per machine as described under Installing.
Agents are not allowed to edit `kblam.toml`, so change it by hand. Because the hooks cannot catch
every way a file can change, the pre-commit hook also refuses a commit that changes `kblam.toml`
until a person has approved that exact version: after editing it, run `kblam approve-config` in a
terminal, read the diff it shows, and answer `y`. The approval is recorded under `.kblam/` on your
machine, and it can only be given at an interactive terminal, so an agent cannot give it. A project
set up before this change gets the new pre-commit hook from `kblam init --update`.

### Status

The design is settled except for the open questions in SPEC.md §13. Two commands in the spec's
command list are not implemented yet: `kblam recheck`, which would re-run each finding's optional
`check:` command, and `kblam migrate`, helpers for splitting an existing document into findings. The
`[kb] evidence_roots` setting is read but not yet enforced, so a finding may cite evidence from
anywhere inside the repository. An MCP server for a librarian agent without shell access is deferred
(SPEC.md §12, M7).

### Documentation and license

SPEC.md is the design and the reference for behaviour: the finding format, the rules, the Jev
decision policy, the hooks and the calibration. CONTRIBUTING.md is for working on kblam itself.
kblam is released under the VibeCoded AI-Slop License v1.0 in LICENSE: do whatever you want with
it; there is no warranty and no support.

## For agents

This section is for agents. The first part helps an agent decide whether kblam fits a problem it
has been asked to solve, the second is for an agent setting kblam up for a project, and the third
is for an agent working in a repository that already uses kblam. Changing kblam's own code is
covered in CONTRIBUTING.md.

### Deciding whether kblam fits

kblam fits when several agents, or one agent over many sessions, record research findings in a git
repository and the record keeps drifting from what is true: corrected claims survive beside their
corrections, one fact is written in several places, and indexes go stale. It keeps one claim per
file and only the current version of each, and every finding has to cite at least one file or
folder in the repository as its evidence. That suits evidence-backed research, such as
measurements, experiments or reverse engineering, better than notes or opinions.

kblam is not a search engine, a retrieval layer or a memory store for agents, and it has no search
command: agents find findings through the generated `findings/INDEX.md` and their ordinary file
tools. Nor is it a wiki that keeps each page's history on the page. A claim that turns out wrong is
rewritten, and its earlier versions survive only in git history.

The parts that act while an agent works are built for Claude Code: hooks that deny direct writes to
the knowledge base, a Stop hook that validates anything that got past them, a rule for reading
findings and a skill for writing them. With any other agent, `kblam put` still refuses a bad write
and the git pre-commit hook still refuses a commit while `kblam validate` fails, but nothing stops a
direct write under `findings/`, and Jev does not compare a finding written that way with the others
unless someone runs `kblam check`. Such an agent also has to be pointed at the rule and the skill
that `kblam init` installs under `.claude/`, since only Claude Code loads them by itself.

Each `kblam put` sends the claim paragraph and scope of the new finding, and of up to 30 similar
existing findings, to Jev, a model from TypeSafe AI, through OpenRouter. The rest of each finding
is not sent, and the similarity search that chooses them runs locally, with ollama or BM25. The
Jev check needs an OpenRouter API key with credit, and the Requirements section above gives its
cost. Whether to send claim text to that service and pay for it is your user's decision; OpenRouter
describes what it keeps on its
[privacy and logging page](https://openrouter.ai/docs/features/privacy-and-logging). With the Jev
check switched off, kblam still applies its deterministic rules and compares numeric quantities.

kblam also assumes that someone other than a finding's author settles the review items the Jev
check raises: a coordinating agent, a librarian agent or a person. While an item is open,
`kblam validate` fails, and so the pre-commit hook refuses every commit in the repository.

kblam is at version 0.1.0 and was built for one research project. It is installed from its GitHub
repository, needs Python 3.11 or newer, uv and git, and comes with no warranty and no support. The
Status section above lists what is not implemented yet.

### Setting kblam up for a project

The commands are in the Installing and Setting up a repository sections above. An agent running
them should also know the following.

The OpenRouter key belongs to your user. Ask them to save it in `~/kblam/jev!.txt` or to set
`OPENROUTER_API_KEY`, and never write it into the repository or into `kblam.toml`. A different key
file or variable, another endpoint, or an ollama on another host goes in the user's own
`~/kblam/config.toml`, never in `kblam.toml`. Without a key,
every `put` is accepted but left unchecked, and commits are refused until `kblam check --pending`
succeeds with the key in place.

Run `kblam init` at the root of the git repository and read its report. It exits 0 when everything
is in place, and on exit 1 the report says what went wrong. For example, an existing pre-commit
hook that is not kblam's is left alone, and the report says to run
`kblam --root <repo> validate --commit` from it or to replace it with kblam's.

Then `[kb] scopes` in `kblam.toml` needs to name the products, versions or components the project's
findings apply to. Once the hooks are active they deny agents any edit to `kblam.toml`, since it
also decides where the API key goes, so propose the scopes and let your user write them. Your
user then approves the edited file with `kblam approve-config` at a terminal, because the
pre-commit hook refuses a commit of a `kblam.toml` that no person has approved; the unedited file
`init` wrote needs no approval. Jev is never asked about two findings whose scopes do not overlap, and the
default scope, `any`, overlaps every other. `kblam validate` should now report OK, and
`kblam jev-smoke` tests the key and the endpoint for a fraction of a cent. Commit the files `init`
created or changed. Each further clone of the repository then needs `kblam init` and
`kblam validate --record`, as the Setting up a repository section explains.

The skill tells authors to send the review items and rejected items that their writes raise to a
coordinator, or to a librarian agent if one is deployed. Agree with your user on who that is, and
write it into the project's instructions; SPEC.md §8.1 describes the librarian role. A Claude Code
agent definition with an explicit tool list needs the Skill tool in that list to load the
`kblam-write` skill, or an instruction to read the skill's file.

If the project already keeps its findings in long documents, SPEC.md §11 describes moving them into
kblam one topic at a time, with every claim staged and put like any other. The `kblam migrate`
helpers are not implemented yet, so the splitting is done by hand or by an agent.

### Working in a repository that uses kblam

If you are working in a project whose findings kblam manages, the rule and the skill installed in
that project are your instructions, and this README is only background. The project's
`.claude/rules/kblam-findings.md` loads when you open a finding, and the `kblam-write` skill covers
writing one; if you have no Skill tool, read `.claude/skills/kblam-write/SKILL.md` as a file. The
essentials are these. Never write under `findings/`, or under `.kblam/` outside `.kblam/staging/`,
yourself: the hooks deny it, and the Stop hook catches what they miss. Add or change a finding with
`kblam new` or `kblam edit`, edit the staged copy, and `kblam put` it. When a put is rejected as a
duplicate or a conflict, edit the existing finding it names instead of rewording yours until it
passes. Send the IDs of review and rejected items that your writes raise to the coordinator, or to
the librarian if there is one, and carry on rather than resolving them yourself. Never edit
`kblam.toml` either, and never try to approve a change to it; if you need one, such as a new
scope, ask your user. Treat
`kblam validate` as the only evidence that the knowledge base is clean, including after your own
work.

Outside Claude Code the hooks do not run, so nothing stops a direct write under `findings/` until
the pre-commit hook runs `kblam validate`, and that does not ask Jev. Write through `kblam put` all
the same. If `kblam` is not on your PATH, it has to be installed as described under Installing
before you write a finding.
