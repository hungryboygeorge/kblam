# Contributing to kblam

This file is for anyone changing kblam's own code, whether a person or a coding agent. Installing
and using kblam are covered in README.md.

## SPEC.md is the source of truth

SPEC.md is the design, and its "As built" paragraphs record implementation details. Read the
section for the area you are changing first; module docstrings cite their sections. Many decisions
carry an attribution and a date, such as "(user, 2026-09-24)": do not reverse one of those without
asking. A change in behaviour updates SPEC.md in the same commit. A change to a message or an exit
code that the `kblam-write` skill describes (`src/kblam/assets/skills/kblam-write/SKILL.md`)
updates the skill in the same commit too. Where you find the code and SPEC.md disagreeing, ask
which one is intended rather than silently changing either.

REFERENCES.md lists the external documentation kblam was built against, under the names the spec
cites it by, and `research/` and `desk-jevdocs-answers.md` hold the research notes the spec cites.
Details of the Jev API are cited to entries in `desk-jevdocs-answers.md` and to the TypeSafe pages
listed in REFERENCES.md; check them before changing how requests are built or answers are read.

## Setup and tests

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

The Claude Code hooks and the pre-commit hook that `kblam init` installs call `kblam` by name, and
`init` checks that it is on PATH. To try `init` or the hooks from a checkout, put the checkout's
`.venv/bin` (`.venv\Scripts` on Windows) first on PATH and run them in a scratch git repository
outside this one.

## Where things are

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

Each test module's docstring says what it covers. `tests/test_rules.py` has a passing case and at
least one failing case for every rule except K3, whose cases are in `tests/test_deps.py`.

## Invariants to keep

**Only kblam writes the knowledge base.** `put`, `ack` and `index` are the only writers of
`findings/`, and they replace files there with `store.atomic_write`. They, `new`, `edit`,
`resolve`, `validate --record` and the recording step of `check` and `audit` hold `.kblam/lock` for
their read-validate-write span. `put` asks Jev before it takes the lock and, under the lock,
recomputes the candidates against the current tree and asks only about pairs the tree gained
meanwhile. Keep network calls outside the lock.

**The tree.hash rule.** `put`, `ack` and `index` advance `.kblam/tree.hash` only when the tree
matched it before their write (`treehash.record_after_write`), and only `validate --record` accepts
a change made outside kblam. Without this rule, a shell write followed by `kblam index` would
silence the Stop hook.

**A failure is never a pass.** A Jev question that gets no answer leaves the finding unchecked, as
an open `U-` item; it is never recorded as checked. An embedding failure is different by design: the
whole check falls back to BM25, and one check never mixes the two mechanisms.

**Hooks fail open and stay fast.** `kblam hook` always exits 0, and its decision travels only in the
JSON it prints. When the upward search finds no `kblam.toml` it prints nothing; on an invalid
configuration, malformed input or any exception it allows the action and prints a `systemMessage`.
PreToolUse runs on every Write, Edit and shell command, so `entry.py` dispatches it without
importing the CLI and `hook.py` imports only `kblam.config` at module level. The Stop path imports
the rest inside `_stop`. Do not add heavier module-level imports to `hook.py`.

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

## Conventions

Match the surrounding code: `from __future__ import annotations`, dataclasses, type hints,
docstrings that say why and cite SPEC.md sections, and lines wrapped at about 110 characters.
Comments and messages are written in full sentences. Tests are named for the behaviour they check,
such as `test_k9_rejects_near_duplicate_claim`. The assets under `src/kblam/assets/` write the
configured findings folder as the token `{{kb_root}}`, which `kblam init` replaces; consuming
projects pick up asset changes with `kblam init --update`.

## Not implemented yet

`kblam recheck` and `kblam migrate` (SPEC.md §7) and the MCP server (§12, M7) do not exist yet.
`[kb] evidence_roots` is parsed in `config.py`, but no rule reads it.
