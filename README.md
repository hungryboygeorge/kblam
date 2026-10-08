<div align="center"><h1>kblam</h1>
<h2>the knowledge base for LLM-assisted mereology</h2>
<h5>(mereology: the study of part-whole relationships and examination of how components interact in a system)</h5>
<h5>Yes I Came Up With The Backronym Myself</h5></div>

## what this is
kblam is a flat-file knowledge base (a claim, fact, and finding management system) with semi-automated organizational enforcement. it's designed primarily for use by LLM research and coding agents, and mainly for use with Claude Code hooks; it also has Git hooks for other harnesses, but i have not personally tested them.

it is intended to fix the problem of research agents producing "findings" files or similar structures: Markdown files, manually indexed by line number, with individual entries of facts gleaned from research, testing, or experimentation. i have found that, because of the inhuman way LLMs "think," function, and work, these files tend to drift and accumulate internal contradictions, supercessions, overrides, and so on, until they're a mess that's so confusing as to be indecipherable to humans and damaging to the LLMs' comprehension of the system. agents would leave obsolete information in place and either override it elsewhere in a file or append to it, information would wind up in multiple files and its copies would drift apart, and so on. agents would end up pulling incorrect inforation out of files with grep without reading the whole (enormous) file to find supercessions, leading to trouble and wasted time.

no attempts instructing agents in better organization seems to help as the problems are fundamental to how agents work. i got tired of this while attempting to coordinate a large hardware RE project and had clod serve me some slop.

kblam is designed to eliminate this gradual accumulation of organizational debt by enforcing structure. agents are disallowed from storing facts that override or duplicate other facts via a combination of heuristics, BM25 or (if available via Ollama) embedding-based semantic comparison, and claim pair analysis performed quickly and cheaply by TypeSafe AI's decision model Jev (served via Openrouter).

## how it works

each finding is a Markdown file with YAML frontmatter, which stores one claim (a current fact), along with metadata: evidence for that fact, the scope in which the fact rests, and the other findings that fact depends on. when an agent is caught attempting to store a fact that revises another fact, it is rebuffed and told to rewrite the previous finding instead. because of this enforcement, and because findings aren't stored in a single file from which an agent can pull an obsolete and incorrect section via grep, agents' knowledge of the system at hand should be closer to current and correct than with manual organization.

indexing is automatic; a markdown index is kept up-to-date in the root of the `findings/` directory. findings are written to a specified markdown file and stored with `kblam put`, which validates both the finding and the knowledge base as it would be after the write, and rejects the write if the new finding fails, or if it breaks another one. heuristics check for incorrectly formatted frontmatter, the existence of cited evidence, dependencies pointing to findings that were rewritten since last checked, language indicating revision or supercession that agents tend to use, length limits, near-duplicate claims, stray files, that quoted (and marked) excerpts actually appear verbatim in cited sources, and rules for claims that retired documents report. several agents can write at once; `put` takes a lock and refuses to overwrite findings that changed since the agent staged an edit.

once it passes the heuristic, the new finding is compared with the most semantically similar existing findings. these are located with an embedding model of the user's choice served by ollama, or by BM25 as a fallback. these comparisons are then sent to Jev, which provides probabilities and confidence on whether the claim restates, extends, or contradicts each older findings, and whether it reads as a correction of an earlier finding. numeric quantities are compared with code, as Jev isn't great at that. confident duplicate and contradictions, corrections, or numerical conflicts trigger a refusal, along with instructions on what the agent should be editing instead. weaker signals open review items, which an orchestrator or dediated librarian agent can resolve. (kblam includes a definition for a librarian subagent. i've been running the librarian with deepseek v4.1 flash using bman654's [clodex](https://github.com/bman654/clodex) and opencode go.)

### docs and license

SPEC.md is the document claude wrote to tell itself how to write the program, and should match the software unless something stupid happens and it starts drifting. CONTRIBUTING.md is what you think it is.
kblam is released under the VibeCoded AI-Slop License v1.0.

### potential future changes

the base design shouldn't change much. i haven't implemented `kblam migrate` (helps split an existing document into findings) yet. i might add an MCP server for the librarian, semantic search using the embeddings/BM25, and some other stuff. i might add the ability to get embeddings from openrouter. i might package it and put it on pypi. who knows

# big disclaimer
i can't code; my brain isn't built right for syntax, but i understand the systems at work. kblam was entirely implemented by LLMs, which means that *nobody fully understands it.* LLM-written software is inherently disposable. it may have problems, major security vulnerabilities, it may not be appropriate for the range of things i thought it was, it may not work on your machine, it may fuck things up. i mean it when i say there's no warranty. you wanna be sure? have your own agent check it and blame it if the software fucks up, or look at the software yourself. i bet it's full of spaghetti.

kblam may not be maintained properly. if you make a pull request i might accept it if it looks useful. if you ask me to do something for you i will probably ignore you. *i built this software as a personal tool, and i'm putting it up here in case others have this problem and it solves it in a manner that works well enough,* so they can maybe waste less of their time and tokens (and, collectively, our power and water). if you want it to be something it isn't but don't care enough to either do it yourself or pay a robot to do it, too bad.

you want a guarantee? you want something *objectively good?* go find software a human wrote. machines don't possess the capability to understand and thus they can't write good code. i wouldn't use them if i had the ability and time to build personal tools without their assistance. the negative externalities are ruining the world. the seas are rising and we're pumping all our solar power into silicon heaters, and opening new thermal power plants to run them too. openAI kills kids and helps people plan mass shootings. claude plans bombing runs for the air force and blows up kids in iran. sam altman and dario amodei belong in prison, or elsewhere. my friends are all out of jobs. anti-minority bias is getting embedded in everything and we're all being surveilled everywhere we go. the entire field must be destroyed or we're all fucked; it's not the AI that'll kill us, it's the people running and using it.

anyway here's how this tool for robots works.

### requirements

kblam needs python 3.11 or newer and [uv](https://docs.astral.sh/uv/). the Jev check needs an
OpenRouter API key with credit. i estimate a write with 30 candidates costs about $0.001
on one-sentence claims, and more on longer ones; `kblam cost` reports the actual cost.
candidate selection checks for ollama at `http://127.0.0.1:11434` and if it finds it, embeds findings with `embeddinggemma:300m` (i tested; this one seems to work ideally for our case). if ollama isn't present, it falls back to BM25. the Claude Code hooks run with Bash or Powershell. my kblam installation has mostly been tested on Windows with Git bash and Powershell, but it has also been tested on Linux. mac testing and more configurability for the model (including embeddings via openrouter) soon. you may need to `ollama pull embeddinggemma:300m` manually upon setup for now.

### installation (once per machine)

```sh
uv tool install git+https://github.com/hungryboygeorge/kblam # installs on your PATH
```

your OpenRouter key goes in `~/kblam/jev!.txt` (configurable), or in `OPENROUTER_API_KEY` instead. you'll need to quote the filename if you refer to the file in bash because i am perfectly willing to kneecap myself for a joke.

A machine that keeps the key elsewhere, calls Jev through an endpoint other than OpenRouter's, or
uses an ollama on another host says so in its own 

`~/kblam/config.toml` contains a `[jev]` table holding any
of `key_env`, `key_file`, `endpoint` and `ollama_url`. these cannot be set in a project's local
`kblam.toml`; this hopefully helps prevent security issues.

### setting up a knowledge base (once per project)

```sh
cd your-research-repo
kblam init
```

`kblam init` runs inside a git repo. it creates a project-local `kblam.toml`, whose
`[kb] scopes` entries you or an agent should edit for your project; after editing it, run
`kblam approve-config` before you commit.

it also installs Claude Code and Git hooks, and other harness stuff:
* the Claude Code hooks; these deny agent writes to `findings/`, `.kblam/`, and `kblam.toml`, to prevent an agent from solving a finding problem by bypassing kblam.
* a Claude Code stop hook that checks for changes made without `kblam put` and refuses stopping while the knowledge base fails validation; if it can't fix it, the agent is released.
* a git pre-commit hook refuses commits while the knowledge base fails `kblam validate`, and requests human confirmation for changes to `kblam.toml`.
* a project rule which tells agents how to read findings.
* a skill that tells agents how to write them and use kblam. messages that stop writes point to the skill.
* some other stuff. the index. etc
  
it'll tell you what it did to each file. it won't overwrite `kblam.toml` or another pre-commit hook; it wraps up by testing the hooks. a git clone then carries the setup (minus the pre-commit hook, which lives in `.git/`).

right now a git clone starts without kblam's state, which lives in `.kblam`. a new clone (on a machine with kblam already installed) needs you to run `kblam init`, which installs the Git hook and leaves committed files alone. you then need to run `kblam validate --record` to accept the cloned knowledge base. this accepts the findings as they were committed, without asking Jev. (`kblam audit` asks Jev if you want that.)

### how a robot writes a finding
clod wrote everything after this. i'm tired.

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
| `kblam put <file>` | Validate, run the Jev check, and move a staged finding into `findings/`; for a staged review record, validate it and move it into `research-review/`. |
| `kblam validate` | Run every rule and list open review items; exit 1 on any failure. |
| `kblam validate --record` | Accept a change made outside kblam (a `git pull`, say) once the tree is clean. |
| `kblam validate --commit` | Also check the commit itself: it must hold `findings/` and `research-review/` as they are on disk, may not change committed evidence, and may not change `kblam.toml` without approval. The pre-commit hook runs this. |
| `kblam approve-config` | Show how `kblam.toml` changed and, at a terminal, approve it for commits on this machine. |
| `kblam check [<id> ...]` | Jev-check findings already in the tree: those named, or every one not checked since its claim, label, scope, quantities or evidence last changed. |
| `kblam check --pending` | Retry the findings Jev could not answer for. |
| `kblam audit` | Ask every candidate pair and question that has no cached answer. |
| `kblam resolve <R-id> --distinct "<reason>"` | Close a review or rejected item that Jev misread, and record why in the committed `kblam.resolutions.jsonl`. |
| `kblam items [--reworded] [--stats]` | List the open items; `--reworded` lists rejected items whose finding later went in changed, and `--stats` counts how each verdict's items closed. |
| `kblam rm <id> --merged-into <target>` | Remove a finding after a merge moved everything it stated into another. Refused while a review record that is not retired links the finding. |
| `kblam renumber <path>` | Give a new ID to one of two findings that share one after two clones' work is merged. Refused while a review record that is not retired links that finding. |
| `kblam ack <dependent> <target>` | After re-reading a rewritten finding, record that a finding depending on it still holds. |
| `kblam deps <id>` | List a finding's dependencies and dependents, marking stale ones. |
| `kblam challenge new\|edit\|pin\|show\|uses` | Stage a source challenge or a copy of an open one, pin its source's version, print it, or list the finding excerpts a confirmed one affects (see Review records below). |
| `kblam task new\|edit\|show` | Stage a claim task on a finding or a copy of an open one, or print it. |
| `kblam use review <challenge> <id> <excerpt-ordinal>` | Stage a checked use of a finding excerpt that a confirmed challenge affects. |
| `kblam review decide\|rebind\|list\|index` | Record a decision that changes a record's status, bind a task or use again to an edited finding, list the records, or regenerate `research-review/INDEX.md`. |
| `kblam recheck [<id> ...]` | Run findings' `check:` commands, each only once it is approved on this machine: the agent running it approves each new or changed command with `kblam recheck <id> --approve <digest>`, unless `recheck_person_approval = true` leaves that to a person at a terminal; `--list` shows which are approved. |
| `kblam upgrade` | Move a knowledge base and this machine's state from an older kblam to the current formats (see below). |
| `kblam index` | Regenerate `findings/INDEX.md`. |
| `kblam cost` | Summarise Jev requests, tokens and spend by day and by kind. |
| `kblam prompt-id` | Print the ids of this project's Jev question wording, the whole and each question's own. |
| `kblam jev-smoke` | Ask Jev one synthetic pair and one question, as a live check of the key and endpoint. |
| `kblam init [--update]` | Set up the current git repository. |

`kblam hook <event>` is the entry point for the Claude Code hooks and is not meant to be run by
hand. The exit status is 0 on success; 1 when kblam refuses (validation errors, open items, a
request it will not carry out, Jev unavailable, or `.kblam/` state that git tracks or that
`kblam upgrade` has not migrated yet); 2 when there is no usable `kblam.toml` or the
arguments are wrong; 3 when another kblam write held the lock for too long, so retry; and 4 when Jev
or a quantity conflict rejected a `put`.

### Review items, and running without Jev

Jev's verdicts are gated by thresholds that a calibration run measured for one served model,
`typesafe/jev-1.13-20260917`, and for the default question wording (SPEC.md §10). `kblam.toml`
records both. If the model that answers or your wording differs from what the thresholds were
measured on, no Jev verdict rejects a write: every one that fires becomes a review item, and kblam
warns that recalibration is needed. Quantity conflicts still reject, because code decides them.

An open review item, or an unchecked item left when Jev could not be reached, makes
`kblam validate` fail until the item is closed, and the pre-commit hook refuses commits that
change `findings/`, `research-review/` or `kblam.resolutions.jsonl` meanwhile; other commits go
through. A review item closes when either of its findings is rewritten (the `put` re-checks the pair
and raises a new item if a verdict still fires), or when `kblam resolve ... --distinct` records that
Jev misread it. Resolutions are committed, in `kblam.resolutions.jsonl`, so every clone keeps them,
and git merges that file line by line. An unchecked item closes when `kblam check --pending` gets an
answer. This means that without an OpenRouter key every `put` succeeds but leaves an unchecked item,
and commits to the knowledge base are refused. To run without Jev on purpose, delete the
`[jev.thresholds]` table from `kblam.toml`. kblam then compares only numeric quantities and says so.

### Changes that arrive from outside kblam

kblam records a digest of `findings/` and `research-review/` in `.kblam/tree.hash` after each of its
own writes. A change that arrives any other way, such as a `git pull` or a branch checkout, leaves
the digest stale, and the Stop hook then validates the tree every time an agent stops. Run
`kblam validate --record` to accept the change: it Jev-checks the findings that changed, validates,
and records the new digest if everything is clean. On a new clone, where there is no digest yet, it
asks Jev nothing: the findings are accepted as committed, and kblam starts its list of review record
IDs from the records present, so that it can report one that later goes missing.

### Upgrading from an older kblam

Earlier versions of kblam recorded fingerprints in an older, 8-digit format, kept resolutions
only on the machine that made them, and cached Jev's answers under other keys. The current kblam
says so where it meets them: `kblam validate` names each old stamp, and the commands that would
misread old state refuse. After installing it, run `kblam upgrade` once in the repository. It re-stamps each
`depends_on` value whose target is unchanged, moves this machine's open items, checked marks,
resolutions and cached answers to the new formats, asks Jev nothing, and prints what it did.
Commit the re-stamped findings and `kblam.resolutions.jsonl`; every other machine then runs
`kblam upgrade` once for its own `.kblam/`. Until a machine has, `validate`, `put`, `check`,
`audit`, `resolve`, `items` and `rm` refuse there, since they would read its old state as changes.
A stamp it leaves in the old format means its target changed since it was recorded: re-read the
target, then `kblam ack` it. If `kblam.toml` records the old combined `prompt_id` and it is still the
id of the question wording, `upgrade` prints the two per-question ids to record in its place; it
never edits `kblam.toml` itself.

### Configuration

`kblam.toml` holds the project's vocabularies (`labels`, the `scopes` a finding may apply to, and
optionally the allowed `topics`, which are folders), the folders evidence may lie in, the claim and
file length limits, the revision-history phrases that K4 and K5 look for, the agent types allowed
to resolve items and remove findings (`adjudicators`), the lock and recheck timeouts, who approves
the `check:` commands that `kblam recheck` runs (`recheck_person_approval`: false by default, so the
agent running `recheck` approves each one itself, and true to leave that to a person at a terminal),
the review records' folder and provenance vocabulary (`[review]`), the Jev model and thresholds, the
embedding settings, and the wording of the Jev questions. SPEC.md §9 documents every key. The file
is committed, so it holds nothing machine-specific, and the API key never goes in it; where the key
is read from, and where requests go beyond OpenRouter and a local ollama, are set per machine as
described under installation. Agents are not allowed to edit `kblam.toml`, so change it by hand.
Because the hooks cannot catch every way a file can change, the pre-commit hook also refuses a
commit that changes `kblam.toml` until a person has approved that exact version: after editing it,
run `kblam approve-config` in a terminal, read the diff it shows, and answer `y`. The approval is
recorded under `.kblam/` on your machine, and it can only be given at an interactive terminal, so an
agent cannot give it. A project set up before this change gets the new pre-commit hook from
`kblam init --update`.

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
direct write under `findings/` or `research-review/`, and Jev does not compare a finding written
that way with the others unless someone runs `kblam check`. Such an agent also has to be pointed at
the rule and the skill that `kblam init` installs under `.claude/`, since only Claude Code loads
them by itself.

Each `kblam put` sends the claim paragraph and scope of the new finding, and of up to 30 similar
existing findings, to Jev, a model from TypeSafe AI, through OpenRouter. The rest of each finding
is not sent, and the similarity search that chooses them runs locally, with ollama or BM25. The
Jev check needs an OpenRouter API key with credit, and the Requirements section above gives its
cost. Whether to send claim text to that service and pay for it is your user's decision; OpenRouter
describes what it keeps on its
[privacy and logging page](https://openrouter.ai/docs/features/privacy-and-logging). With the Jev
check switched off, kblam still applies its deterministic rules and compares numeric quantities.

kblam also assumes that someone other than a finding's author settles the review items the Jev
check raises, and decides the review records an author files: a librarian agent if one is deployed,
else a coordinating agent. While an item is open, `kblam validate` fails, and the pre-commit hook
refuses any commit that changes the knowledge base.

kblam is at version 0.1.0 and was built for one research project. It is installed from its GitHub
repository, needs Python 3.11 or newer, uv and git, and comes with no warranty and no support.
CONTRIBUTING.md lists what is not implemented yet.

### Setting kblam up for a project

The commands are in the sections "installation" and "setting up a knowledge base" above. An agent
running them should also know the following.

The OpenRouter key belongs to your user. Ask them to save it in `~/kblam/jev!.txt` or to set
`OPENROUTER_API_KEY`, and never write it into the repository or into `kblam.toml`. A different key
file or variable, another endpoint, or an ollama on another host goes in the user's own
`~/kblam/config.toml`, never in `kblam.toml`. Without a key,
every `put` is accepted but left unchecked, and commits to the knowledge base are refused until
`kblam check --pending` succeeds with the key in place.

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
`kblam validate --record`, as the section "setting up a knowledge base" explains.

The skill tells authors to send the review items and rejected items that their writes raise to a
coordinator, or to a librarian agent if one is deployed. Agree with your user on who that is, and
write it into the project's instructions; SPEC.md §8.1 describes the librarian role. To deploy one,
copy `agents/librarian.md` from kblam's repository into the project's `.claude/agents/`. Its model
is your user's choice, set with a `model:` line in its frontmatter. `kblam init` writes
`adjudicators = []` into `kblam.toml`, which leaves `kblam resolve` and `kblam rm` to the main
session, so your user adds `"librarian"` to that list. A Claude Code agent definition with an
explicit tool list needs the Skill tool in that list to load the `kblam-write` skill, or an
instruction to read the skill's file.

If the project already keeps its findings in long documents, SPEC.md §11 describes moving them into
kblam one topic at a time, with every claim staged and put like any other. The `kblam migrate`
helpers are not implemented yet, so the splitting is done by hand or by an agent.

### Working in a repository that uses kblam

If you are working in a project whose findings kblam manages, the rule and the skill installed in
that project are your instructions, and this README is only background. The project's
`.claude/rules/kblam-findings.md` loads when you open a finding, and the `kblam-write` skill covers
writing one; if you have no Skill tool, read `.claude/skills/kblam-write/SKILL.md` as a file. The
essentials are these. Never write under `findings/` or `research-review/`, under `.kblam/` outside
`.kblam/staging/` and `.kblam/review-staging/`, or to `kblam.resolutions.jsonl` yourself: the hooks
deny it, and the Stop hook catches what they miss under `findings/` and `research-review/`. Add or
change a finding with `kblam new` or `kblam edit`, edit the staged copy, and `kblam put` it. When a
put is rejected as a duplicate or a conflict, edit the existing finding it names instead of
rewording yours until it passes. Send the IDs of review and rejected items that your writes raise to
the coordinator, or to the librarian if there is one, and carry on rather than resolving them
yourself. Never edit `kblam.toml` either, and never try to approve a change to it; if you need one,
such as a new scope, ask your user. Treat `kblam validate` as the only evidence that the knowledge
base is clean, including after your own work.

Outside Claude Code the hooks do not run, so nothing stops a direct write under `findings/` or
`research-review/` until the pre-commit hook runs `kblam validate`, and that does not ask Jev. Write
through `kblam put` all the same. If `kblam` is not on your PATH, it has to be installed as
described under installation before you write a finding.

#### Review records

A project can also keep review records: YAML files under `research-review/` that are data about
evidence and work, not findings. A source challenge (`source-challenge-NNNN`) disputes one assertion
in one version of a source as `contradicted`, `unsupported` or `wrong_model`, and says which of the
source's material stays usable. A claim task (`claim-task-NNNN`) asks for a replication or an
independent confirmation of one finding's claim. A checked use (`checked-use-NNNN`) records that a
reviewer checked one verbatim excerpt of a finding against a confirmed challenge of its source. A
record is staged under `.kblam/review-staging/`, edited there and installed only by `kblam put`; the
hooks deny writing or removing a record under `research-review/`. Commit the records and
`research-review/INDEX.md` as you commit findings.

Challenge a source when one of its assertions is wrong in one of those three ways:
`kblam challenge new <source-path> --lines A-B --by NAME`. Task a claim that needs a fresh
measurement or an independent check:
`kblam task new <id> --kind replication|confirmation --by NAME --proponent NAME`, where the
proponent is whoever stands behind the claim. `kblam challenge edit` and `kblam task edit` stage a
copy of an open record, and `show` prints one. Once a challenge is confirmed,
`kblam challenge uses <challenge-id>` lists each finding excerpt it affects and the command that
fixes it: edit the finding, or, where the excerpt draws only on the material the challenge leaves
usable, stage a use with
`kblam use review <challenge-id> <id> <excerpt-ordinal> --by NAME --proponent NAME`, the proponent
being the finding's author.

A record's status changes only through
`kblam review decide <record-id> --status S --by NAME --reason TEXT --expect D`, where D is the
subject digest that `kblam challenge show`, `kblam task show` or `kblam review list` printed; it
refuses if the record changed since. Confirming or rejecting a challenge, closing a task and
approving a use each need a `--by` that is not the challenge's creator, not the task's creator or
proponent, and not the use's proponent. So send the ID of each record you file to the librarian, or
to the coordinator if there is none, and carry on. `kblam review rebind` binds a task or use again
to a finding that was edited since, and `kblam review list --open` lists the open records.

`kblam validate` and the pre-commit hook fail on errors of K13 (record integrity), K14 (a finding
excerpt that quotes a confirmed challenge's assertion, unless an approved use that is still current
covers it) and K15 (a claim task no longer bound to its finding's current revision); `validate`
lists each open task whose binding still holds as pending, without failing. A put that would add
such an excerpt is refused, so quote the usable material outside the challenged assertion instead.
No record changes a finding: a challenge that shows a claim false is followed by a `kblam edit` of
that finding. `kblam rm` and `kblam renumber` refuse a finding that a record which is not retired
links. The refusal prints the way out for the adjudicator, so send it to the librarian or the
coordinator; the adjudicator retires each such record with the
`kblam review decide ... --status stale` command it gives, runs the `rm` or `renumber`, and files
each retired record again where its question still applies. The installed `kblam-write` skill gives
the whole workflow and the fix for each refusal, and SPEC.md §5.2 gives the rules.
