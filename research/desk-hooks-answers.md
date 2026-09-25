# desk-hooks — answers file

Subject: Claude Code (CLI) hooks, project rules, project skills.

**Sources used** (provenance for every entry below):

- `hooks-reference` = a local copy of <https://code.claude.com/docs/en/hooks>, retrieved
  2026-09-22 (3,853 lines). Line numbers cited against that copy are point-in-time; the live URL
  is the durable citation.
- `hooks-guide` = <https://code.claude.com/docs/en/hooks-guide>, fetched live 2026-09-22.
- `memory-doc` = <https://code.claude.com/docs/en/memory>, fetched live 2026-09-22.
- `skills-doc` = <https://code.claude.com/docs/en/skills>, fetched live 2026-09-22.
- `tools-reference` = <https://code.claude.com/docs/en/tools-reference>, fetched live 2026-09-22.
- `installed-cli` = the installed Claude Code CLI binary, **Claude Code 2.1.280**, inspected by
  string search on 2026-09-22. Observed, not documented.

Labels used: **documented** (in the sources above), **observed** (I ran it), **inferred**.

**No live hook experiment was run.** A nested `claude -p` probe session in a scratch directory
was denied by the permission system, so there is no observed evidence in this file for what a
hook actually receives at runtime on this machine. Everything below is documented, except the
two entries explicitly marked observed (both from inspecting the installed binary).

---

## ⬅️ OPEN QUESTIONS

- Does the `PowerShell` tool actually replace `Bash` for shell commands when
  `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` is set (it is, in `~/.claude/settings.json`), or do both
  tools coexist? If PowerShell becomes the routing target, a `Bash`-only PreToolUse matcher
  silently stops firing. Documented as "routes shell commands through it" but not confirmed by
  observation here. See H9 and H14.
- **Does an in-repo plugin with a relative-path source, enabled by `enabledPlugins` in the same
  committed `.claude/settings.json`, load with no install step?** The docs say adding a marketplace
  "doesn't install plugins that come from an external source such as a GitHub repository or npm
  package" (P3), and that a relative-path source in a local-directory marketplace loads in place
  (P5). Neither page states what happens for the in-repo case. This decides whether kblam can ship
  as a no-install plugin, and it is a five-minute experiment. See P3.
- Does `disableAllHooks` suppress **plugin** hooks? It is documented for user/project/local and
  explicitly cannot disable managed hooks; `allowManagedHooksOnly` names plugin hooks but
  `disableAllHooks` does not. See P7.
- Does a plugin's `bin/` PATH injection reach the environment Claude Code uses to spawn
  exec-form hook commands, or only the Bash tool's PATH? See P2.
- Do `.claude/rules/*.md` load inside subagents, and do agent-team **teammates** inherit skills
  and hooks at all? The sub-agents page says a teammate gets "parts of" a subagent definition and
  does not say which parts. This is load-bearing for kblam's enforcement design. See H10 and P6.

---

## INDEX

```
config/shape       H1   settings.json hooks block, matcher syntax, minimal example      2026-09-22 @ kblam 2bdb121  L81
pretooluse/input   H2   PreToolUse stdin JSON, tool_input per tool, path form           2026-09-22 @ kblam 2bdb121  L177
stop/input         H3   Stop and SubagentStop stdin JSON, stop_hook_active              2026-09-22 @ kblam 2bdb121  L272
pretooluse/deny    H4   deny via hookSpecificOutput vs exit 2                           2026-09-22 @ kblam 2bdb121  L348
stop/block         H5   block via top-level decision vs exit 2, loop protection         2026-09-22 @ kblam 2bdb121  L411
notify             H6   systemMessage vs additionalContext vs stderr, per event         2026-09-22 @ kblam 2bdb121  L472
exitcodes          H7   exit-code and JSON-failure semantics                            2026-09-22 @ kblam 2bdb121  L526
env/timeout        H8   hook cwd, CLAUDE_PROJECT_DIR, timeout field and units           2026-09-22 @ kblam 2bdb121  L587
windows            H9   which shell runs a hook command on Windows, quoting            2026-09-22 @ kblam 2bdb121  L645
rules              H10  .claude/rules/*.md frontmatter and load trigger                  2026-09-22 @ kblam 2bdb121  L696
skills             H11  .claude/skills/<name>/SKILL.md frontmatter and loading          2026-09-22 @ kblam 2bdb121  L786
multiedit          H12  MultiEdit is not a tool in 2.1.280 — documented absence        2026-09-22 @ kblam 2bdb121  L861
kblam/packaging    H13  how to invoke `kblam hook <event>` from a hook on Windows       2026-09-22 @ kblam 2bdb121  L903
matcher/windows    H14  Bash|PowerShell matcher requirement on this machine             2026-09-22 @ kblam 2bdb121  L952
subagent/identity  H15  PreToolUse carries agent_id + agent_type inside a subagent     2026-09-22 @ kblam 2bdb121  L982

plugin/components  P1   plugin ships skills/hooks/agents, NOT rules (no rules/ dir)   2026-09-22 @ kblam 2bdb121  L1038
plugin/root-var    P2   ${CLAUDE_PLUGIN_ROOT} in exec+shell form; bin/ adds to PATH   2026-09-22 @ kblam 2bdb121  L1093
plugin/declaring   P3   committed settings.json declares it; NOTHING auto-installs    2026-09-22 @ kblam 2bdb121  L1142
plugin/sources     P4   marketplace + plugin source types, version pinning, updates  2026-09-22 @ kblam 2bdb121  L1208
plugin/on-disk     P5   ~/.claude/plugins/cache copy vs in-place; CLAUDE_PLUGIN_DATA  2026-09-22 @ kblam 2bdb121  L1262
plugin/subagents   P6   plugin hooks run in subagents; teammates unknown              2026-09-22 @ kblam 2bdb121  L1335
plugin/per-project P7   enabledPlugins per scope; no conditional activation          2026-09-22 @ kblam 2bdb121  L1382
plugin/merge       P8   plugin + settings hooks merge; all matching hooks run        2026-09-22 @ kblam 2bdb121  L1437
```

---

## H1 — `settings.json` hooks block, matcher syntax, minimal example

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (verbatim, local mirror) + hooks-guide (live fetch)

**Shape** — three levels of nesting (hooks-reference L237-241):

> Hooks are defined in JSON settings files. The configuration has three levels of nesting:
> 1. Choose a hook event to respond to, like `PreToolUse` or `Stop`
> 2. Add a matcher group to filter when it fires, like "only for the Bash tool"
> 3. Define one or more hook handlers to run when matched

**Matcher syntax** (hooks-reference L289-295) — the rule is decided by which characters the
matcher contains:

| Matcher value | Evaluated as | Example |
| :--- | :--- | :--- |
| `"*"`, `""`, or omitted | Match all | fires on every occurrence of the event |
| Only letters, digits, `_`, `-`, spaces, `,`, and `\|` | Exact string, or list of exact strings separated by `\|` or `,` with optional surrounding whitespace | `Bash` matches only the Bash tool; `Edit\|Write` and `Edit, Write` each match either tool exactly; `code-reviewer` matches only that agent type |
| Contains any other character | JavaScript regular expression, unanchored | `^Notebook` matches any tool whose name starts with `Notebook`; `mcp__memory__.*` matches every tool from the `memory` server |

> A matcher on the regular-expression path is tested with JavaScript's `RegExp.prototype.test`,
> which succeeds on a match anywhere in the value. `Edit.*` matches both `Edit` and
> `NotebookEdit`; wrap the pattern in `^` and `$`, as in `^Edit$`, when you need a whole-string match.

So `"Write|Edit|MultiEdit|NotebookEdit"` is on the **exact-list** path (only letters and `|`), and
each name is compared exactly. `MultiEdit` simply never matches anything (see H12).

**Which events take a matcher** (hooks-reference L327):

> `UserPromptSubmit`, `PostToolBatch`, `Stop`, `TeammateIdle`, `TaskCreated`, `TaskCompleted`,
> `WorktreeCreate`, `WorktreeRemove`, `MessageDisplay` | no matcher support | always fires on every occurrence

`PreToolUse` and `SubagentStop` **do** take a matcher; `Stop` does **not**. Adding one is not an
error: "If you add a `matcher` field to an event without matcher support, it is silently ignored."
(hooks-reference L353)

**Minimal settings.json** — this is the shape for kblam M6:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "kblam",
            "args": ["hook", "PreToolUse"]
          }
        ]
      },
      {
        "matcher": "Bash|PowerShell",
        "hooks": [
          {
            "type": "command",
            "command": "kblam",
            "args": ["hook", "PreToolUse"]
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "kblam", "args": ["hook", "Stop"] }
        ]
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          { "type": "command", "command": "kblam", "args": ["hook", "SubagentStop"] }
        ]
      }
    ]
  }
}
```

Presence of `args` (even `"args": []`) selects **exec form** — no shell, no tokenization
(hooks-reference L468-472). See H9 and H13 for why that matters on Windows.

Handler fields beyond `type`/`command`/`args` (hooks-reference L426-432): `if`, `timeout`,
`statusMessage`, `once`. `once` is honoured **only** in skill frontmatter; it is "ignored in
settings files and agent frontmatter".

Hook locations and scope (hooks-reference L253-261): `~/.claude/settings.json` (all your
projects), `.claude/settings.json` (single project, committable), `.claude/settings.local.json`
(single project, gitignored). Entries **merge** across levels rather than replacing each other
(L278).

---

## H2 — PreToolUse stdin JSON, `tool_input` per tool, path form

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (verbatim, local mirror); NotebookEdit key from installed-cli (observed)

**Common input fields** (hooks-reference L752-761), present on every event:

`session_id`, `prompt_id`, `transcript_path`, `cwd`, `scratchpad_dir`, `permission_mode`,
`effort`, `hook_event_name`.

Inside a subagent, two more are added (L765-768): `agent_id` (present only when the hook fires
inside a subagent call) and `agent_type`.

**PreToolUse adds** (L1603): `tool_name`, `tool_input`, `tool_use_id`.

Documented example payload (L778-796):

```json
{
  "session_id": "abc123",
  "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
  "transcript_path": "/home/user/.claude/projects/.../transcript.jsonl",
  "cwd": "/home/user/my-project",
  "scratchpad_dir": "/tmp/claude-1000/-home-user-my-project/abc123/scratchpad",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {
    "command": "npm test",
    "description": "Run test suite",
    "timeout": 120000,
    "run_in_background": false
  },
  "tool_use_id": "toolu_01ABC123..."
}
```

**`tool_input` keys per tool** (hooks-reference L1632-1742):

| Tool | `tool_input` keys |
| :--- | :--- |
| `Bash` | `command`, `description`, `timeout` (ms), `run_in_background` |
| `PowerShell` | `command`, `description`, `timeout` (ms), `run_in_background` |
| `Write` | `file_path`, `content` |
| `Edit` | `file_path`, `old_string`, `new_string`, `replace_all` |
| `Read` | `file_path`, `offset`, `limit` |
| `Glob` | `pattern`, `path` |
| `Grep` | `pattern`, `path`, `glob`, `output_mode`, `-i`, `multiline` |

`NotebookEdit` is **not** in the hooks-reference `tool_input` table. **Observed** from
`installed-cli`: its path key is `notebook_path`, not `file_path`. The binary carries the map

```
Edit:{input:"file_path",responseMembers:["filePath"]},MultiEdit:{input:"file_path",responseMembers:["filePath"]},NotebookEdit:{input:"notebook_path",responseMembers:["notebook_path"]}
```

A second, independent string search of the same binary corroborates it: the NotebookEdit
implementation's own error strings and UI labels sit next to the identifiers `notebook_path` and
`cell_id` (`Cannot destructure property 'notebook_path' from null or undefined value`,
`with: cat <notebook_path> | jq '.cells[`). Label: observed (two string searches of the 2.1.280
binary), not documented on the hooks page.

**Paths are absolute** (hooks-reference L1607-1612) — this is the load-bearing paragraph for a
path-matching hook:

> For the file tools `Write`, `Edit`, and `Read`, `tool_input.file_path` is always absolute:
> * Claude Code expands `~` and relative paths before hooks run, so a hook that matches on paths
>   can't be bypassed via `~` or a relative spelling of the same path
> * On Windows, the path arrives with backslash separators, even when your hook runs under Git
>   Bash where `$PWD` looks like `/c/project`
> * A comparison written with forward slashes, such as a `/src/` check, never matches a
>   backslash path, and the tool call proceeds as if the hook had nothing to block
> * Normalize separators before comparing: `FILE_PATH="${FILE_PATH//\\//}"` in Bash, or
>   `file_path.replace("\\", "/")` in Python, then match a path segment such as `/src/` rather
>   than anchoring with `^`, since the path is absolute

Documented `Write` payload on Windows (L1616-1626):

```json
{
  "hook_event_name": "PreToolUse",
  "tool_name": "Write",
  "tool_input": {
    "file_path": "C:\\project\\src\\index.ts",
    "content": "..."
  },
  ...
}
```

**Consequence for kblam:** `kblam hook PreToolUse` must parse `tool_input.file_path` (or
`notebook_path`) and normalize `\` to `/` before any path comparison.

---

## H3 — Stop and SubagentStop stdin JSON, `stop_hook_active`

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (verbatim, local mirror)

**Stop adds** (hooks-reference L2554): `stop_hook_active`, `last_assistant_message`,
`background_tasks`, `session_crons`.

> The `stop_hook_active` field is `true` when Claude Code is already continuing as a result of a
> stop hook. Check this value or process the transcript to avoid blocking on a condition that
> will never resolve. Claude Code overrides the hook and ends the turn after 8 consecutive blocks.

Documented Stop payload (L2585-2612), abbreviated:

```json
{
  "session_id": "abc123",
  "transcript_path": "~/.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
  "cwd": "/Users/...",
  "permission_mode": "default",
  "hook_event_name": "Stop",
  "stop_hook_active": true,
  "last_assistant_message": "I've completed the refactoring. Here's a summary...",
  "background_tasks": [ { "id": "task-001", "type": "shell", "status": "running", "...": "..." } ],
  "session_crons": [ { "id": "cron-001", "schedule": "0 9 * * 1-5", "recurring": true, "prompt": "check the build" } ]
}
```

**Yes, SubagentStop has `stop_hook_active` too** (hooks-reference L2403):

> In addition to the common input fields, SubagentStop hooks receive `stop_hook_active`,
> `agent_id`, `agent_type`, `agent_transcript_path`, and `last_assistant_message`.

Documented SubagentStop payload (L2413-2427):

```json
{
  "session_id": "abc123",
  "transcript_path": "~/.claude/projects/.../abc123.jsonl",
  "cwd": "/Users/...",
  "permission_mode": "default",
  "hook_event_name": "SubagentStop",
  "stop_hook_active": false,
  "agent_id": "def456",
  "agent_type": "Explore",
  "agent_transcript_path": "~/.claude/projects/.../abc123/subagents/agent-def456.jsonl",
  "last_assistant_message": "Analysis complete. Found 3 potential issues...",
  "background_tasks": [],
  "session_crons": []
}
```

**Identity fields.** `agent_type` is the value the matcher filters on. `transcript_path` is the
**main session's** transcript; `agent_transcript_path` is the subagent's own.

**Gotcha — SubagentStop fires for agents Claude never spawned** (L2405-2407):

> Not every SubagentStop event comes from a subagent Claude spawned. Claude Code also runs
> internal agents for some of its own features, such as prompt suggestions and `/btw` side
> questions, and SubagentStop fires when one of those finishes too. For those events,
> `agent_type` is the agent name the session itself runs as, such as one set with `--agent` or
> the `agent` setting, and an empty string when the session runs without one.
>
> A `matcher` that names agent types doesn't match an empty `agent_type`. A hook whose matcher is
> omitted, `""`, or `"*"`, or is a regular expression that matches an empty string, runs for
> events with an empty `agent_type` too.

So an unmatchered SubagentStop hook will fire for internal agents. If `kblam hook SubagentStop`
should only act on real subagents, matcher-narrow it or treat an empty `agent_type` as a no-op.

**Stop does not fire on interrupt** (L2544): "Runs when the main Claude Code agent has finished
responding. Does not run if the stoppage occurred due to a user interrupt. API errors fire
StopFailure instead."

---

## H4 — PreToolUse deny: JSON on stdout vs exit 2

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (verbatim, local mirror)

**JSON is the current documented format for PreToolUse** (hooks-reference L1808-1816). The
decision lives inside `hookSpecificOutput`, not at the top level:

> Unlike other hooks that use a top-level `decision` field, PreToolUse returns its decision inside
> a `hookSpecificOutput` object. This gives it richer control: four outcomes (allow, deny, ask, or
> defer) plus the ability to modify tool input before execution.

| Field | Description (verbatim, L1814-1817) |
| :--- | :--- |
| `permissionDecision` | `"allow"` skips the permission prompt... `"deny"` prevents the tool call. `"ask"` prompts the user to confirm. `"defer"` exits gracefully so the tool can be resumed later. |
| `permissionDecisionReason` | For `"allow"` and `"ask"`, shown to the user but not Claude. For `"deny"`, **shown to Claude**. For `"defer"`, ignored |
| `updatedInput` | Modifies the tool's input parameters before execution. Replaces the entire input object... |
| `additionalContext` | String added to Claude's context alongside the tool result. |

The deprecated top-level form (L1847-1849):

> PreToolUse previously used top-level `decision` and `reason` fields, but these are deprecated for
> this event. Use `hookSpecificOutput.permissionDecision` and
> `hookSpecificOutput.permissionDecisionReason` instead. The deprecated values `"approve"` and
> `"block"` map to `"allow"` and `"deny"` respectively. Other events like PostToolUse and Stop
> continue to use top-level `decision` and `reason` as their current format.

Exact deny payload (L1088-1096 and L1827-1839):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Database writes are not allowed"
  }
}
```

`hookEventName` is required — `hookSpecificOutput` "requires a `hookEventName` field set to the
event name" (L951).

**Exit 2 is an equivalent route, not a legacy one** (L1821):

> A hook that blocks by exiting 2 routes the same way as `"deny"`: Claude sees the stderr message
> as the denial reason.

**What the model sees:**

| Route | Model sees |
| :--- | :--- |
| exit 0 + JSON `permissionDecision: "deny"` | `permissionDecisionReason` |
| exit 2 + stderr text | the stderr text, as the denial reason |
| exit 0 + JSON `permissionDecision: "allow"` | nothing (reason goes to the user only) |
| exit 0, no JSON, stderr only | nothing — stderr goes to the debug log only (L822) |

Precedence when several PreToolUse hooks disagree (L1819): `deny` > `defer` > `ask` > `allow`.
A hook `"deny"` blocks even in `bypassPermissions` mode (hooks-guide, "Hooks and permission
modes"): "A hook that returns `permissionDecision: "deny"` blocks the tool even in
`bypassPermissions` mode or with `--dangerously-skip-permissions`."

---

## H5 — Stop / SubagentStop block: top-level `decision`, and loop protection

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (verbatim, local mirror) + hooks-guide (live fetch)

**These events use the top-level `decision` field, not `hookSpecificOutput`** (hooks-reference
L2616-2624):

| Field | Description |
| :--- | :--- |
| `decision` | `"block"` prevents Claude from stopping. Omit to allow Claude to stop |
| `reason` | Required when `decision` is `"block"`. Tells Claude why it should continue |
| `hookSpecificOutput.additionalContext` | Non-error feedback for Claude. The conversation continues so Claude can act on it, but unlike `decision: "block"` it is shown in the transcript as hook feedback rather than a hook error |

Exact payload (L2626-2631):

```json
{
  "decision": "block",
  "reason": "Must be provided when Claude is blocked from stopping"
}
```

> A hook that blocks by exiting 2 routes the same way as `reason`: Claude receives the stderr
> message as the explanation for why it should continue.

`SubagentStop` uses the same format (L2430): "SubagentStop hooks use the same decision control
format as Stop hooks, including `hookSpecificOutput.additionalContext` with `hookEventName` set
to `"SubagentStop"`, for non-error feedback that keeps the subagent running. Returning
`decision: "block"` with a `reason` keeps the subagent running and delivers `reason` to the
subagent as its next instruction."

**Avoiding an infinite Stop loop.** Two mechanisms, both documented:

1. Check `stop_hook_active` (hooks-reference L2554, quoted in H3) and exit 0 early when it is true.
   The hooks-guide carries the canonical guard:

```bash
#!/bin/bash
INPUT=$(cat)
if [ "$(echo "$INPUT" | jq -r '.stop_hook_active')" = "true" ]; then
  exit 0  # Allow Claude to stop
fi
# ... rest of your hook logic
```

2. A hard cap: "Claude Code overrides the hook and ends the turn after 8 consecutive blocks"
   (L2554). Raise it with `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` (hooks-guide, "Stop hook hits the
   block cap").

`additionalContext` is subject to the *same* loop protections as `decision: "block"` (L2633):

> It keeps the conversation going through the same loop protections as `decision: "block"`, namely
> the `stop_hook_active` input and the 8-consecutive-continuation cap, but the transcript labels
> it `Stop hook feedback` and no hook error notification is shown.

So a Stop hook that always returns `additionalContext` will also loop until the cap. If kblam's
Stop hook is advisory, it must still check `stop_hook_active`.

---

## H6 — Non-blocking notes: `systemMessage` vs `additionalContext` vs stderr

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (verbatim, local mirror)

Three channels, and they go to different places. Getting this wrong is the most common way a
hook appears to do nothing.

**Universal JSON fields** (hooks-reference L953-959):

| Field | Default | Description |
| :--- | :--- | :--- |
| `continue` | `true` | If `false`, Claude stops processing entirely after the hook runs. Takes precedence over any event-specific decision fields |
| `stopReason` | none | Message shown to the user when `continue` is `false`. It stays in the conversation, so Claude sees it if the conversation continues |
| `suppressOutput` | `false` | Has no effect: Claude Code accepts the field but doesn't act on it |
| `systemMessage` | none | Warning message **shown to the user** |
| `terminalSequence` | none | A terminal escape sequence for Claude Code to emit on your behalf |

**`additionalContext`** (L1004): "passes a string from your hook into Claude's context window.
Claude Code wraps the string in a system reminder and inserts it into the conversation at the
point where the hook fired. Claude reads the reminder on the next model request, but it doesn't
appear as a chat message in the interface." It is returned **inside `hookSpecificOutput`**
alongside `hookEventName` (L1006-1015).

So for a fail-open "allowing, because X" note:

| Want to tell | Use |
| :--- | :--- |
| the **user** | `systemMessage` at the top level |
| **Claude** | `hookSpecificOutput.additionalContext` (with `hookEventName`), or `permissionDecisionReason` on a `"deny"` |
| nobody (log only) | stderr on exit 0 — **debug log only, never the transcript** (L822) |

Verbatim on the stderr trap (L822):

> Stderr from a hook that exits 0 goes to the debug log only, never the transcript, and Claude
> never sees it. To read it yourself, enable debug logging. To surface a warning to Claude from a
> `PostToolUse` or `PostToolUseFailure` hook, exit 2 instead so Claude sees the stderr even though
> the tool already ran.

Note the asymmetry: for `PostToolUse`/`PostToolUseFailure`, exit 2 **does** surface stderr to
Claude; for `PreToolUse` on exit 0, stderr surfaces nowhere.

**Size cap** (L941): "A hook's `additionalContext`, `systemMessage`, and `initialUserMessage`
strings, and its plain stdout, are capped at 10,000 characters." Over the cap, the text is
written to a file and replaced by the path plus a 2,000-character preview, and "Claude Code
doesn't ask Claude to read the file, so keep anything Claude must always see within the cap."

**Prompt-injection caution** (L1037): "Write the text as factual statements rather than
imperative system instructions... Text framed as out-of-band system commands can trigger
Claude's prompt-injection defenses, which causes Claude to surface the text to you instead of
treating it as context."

---

## H7 — Exit codes and failure modes

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (verbatim, local mirror)

**Exit 2 is the only blocking exit code** for the events in scope (hooks-reference L863-865):

> For most hook events, exit code 2 is the only exit code that blocks through the code alone.
> Without valid JSON on stdout, Claude Code treats exit code 1 as a non-blocking error and
> proceeds with the action, even though 1 is the conventional Unix failure code. If your hook is
> meant to enforce a policy, use `exit 2`.

Per-event block table (L880-914), rows relevant to M6:

| Hook event | Can block? | What happens on exit 2 |
| :--- | :--- | :--- |
| `PreToolUse` | Yes | Blocks the tool call |
| `Stop` | Yes | Prevents Claude from stopping, continues the conversation |
| `SubagentStop` | Yes | Prevents the subagent from stopping |
| `PostToolUse` | No | Shows stderr to Claude; the tool already ran |

**exit 1 on PreToolUse and Stop: non-blocking, action proceeds.** Same for any code other than 2.

**Command not found / not executable** (L861):

> A hook that can't start lands in the same non-blocking bucket. When the script path doesn't
> exist or isn't executable, the shell exits with a code like 127 and you see the same notice with
> the interpreter's message, for example `Failed with non-blocking status code: /bin/sh:
> /path/to/hook.sh: No such file or directory`. For most hook events, the action proceeds. When
> you set up a policy hook, watch for this notice on its first run: a mistyped path in
> `settings.json` leaves the gate silently disabled.

**stdout that is not valid JSON** (L820):

> For events that use the standard decision model, when Claude Code tries to parse your stdout as
> JSON and can't, it reports a non-blocking error on every exit code other than 2. The transcript
> shows a `<hook name> hook error` notice with the parse message.

Parse rules (L812-816): output is parsed as JSON only when it **starts with `{` and ends with
`}`**, ignoring surrounding whitespace. Anything else is plain text. A shell profile that prints
on startup will prepend text and silently break the JSON — see the hooks-guide's "Hook JSON has
no effect" section, which also notes the fixes.

**JSON schema failure** (L818): on a non-2 exit code, a parsed object that fails schema
validation is a non-blocking error; the action proceeds. On exit 2 with invalid JSON, "a hook
that exits 2 while printing JSON that fails schema validation still blocks: Claude Code uses
stderr as the blocking reason" (L830).

**Misplaced fields are silently ignored** (hooks-guide, "Hook JSON has no effect"): putting
`permissionDecision` or `additionalContext` at the top level instead of inside
`hookSpecificOutput` means "the JSON still parses, and Claude Code ignores the misplaced fields
without reporting an error."

**Practical rule for `kblam hook`:** the process must exit 0 for "no opinion" and write valid
JSON to stdout for "deny" / "block". Any unhandled Python exception produces a non-zero exit and
a traceback on stderr — non-blocking, so the tool call proceeds and kblam's gate is silently
disabled. Wrap `main()` so an unexpected exception exits 0 with no JSON, or exits 2 with a
one-line stderr reason if the design is fail-closed.

---

## H8 — Hook cwd, `CLAUDE_PROJECT_DIR`, timeout field and units

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (verbatim, local mirror)

**Working directory** (hooks-reference L418):

> Handlers run in the current directory with Claude Code's environment. If the current directory
> no longer exists, for example a worktree or temp directory that another shell deleted
> mid-session, Claude Code runs command hooks from the first of these that still exists: the
> directory the session started in, the project root, your home directory, or the system temp
> directory.

**`cwd` in the stdin JSON** is the "Current working directory when the hook is invoked"
(L757) and it **follows Claude**, including into worktrees (L629). `CLAUDE_PROJECT_DIR` does
**not** follow — it stays at the project root where the session started (L626-628).

**`CLAUDE_PROJECT_DIR` is exported into the hook process environment** (L497):

> Both forms support the same path placeholders, and both export them as the environment
> variables `CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_ROOT`, and `CLAUDE_PLUGIN_DATA` on the spawned
> process, so a script can read `process.env.CLAUDE_PLUGIN_ROOT` regardless of how it was launched.

Plus the `${CLAUDE_PROJECT_DIR}` placeholder is substituted into `command` and each `args`
element (exec form) or into the shell string (shell form). Definition (L621): "the project root
where the session started."

So for the kblam design: read `cwd` from stdin to know which directory Claude is working in;
read `CLAUDE_PROJECT_DIR` from the environment to find the project the config lives in. On this
machine they are different spellings of the same place (no worktrees in play), but the
distinction is real.

**Timeout** (hooks-reference L430 and hooks-guide "Limitations"):

> `timeout` | no | **Seconds** before canceling. Claude Code doesn't enforce it on a command hook
> you run with `async: true`. Defaults: 600 for `command`, `http`, and `mcp_tool`; 30 for
> `prompt`; 60 for `agent`. Claude Code lowers the `command`, `http`, and `mcp_tool` default to
> 30 on `UserPromptSubmit`, `PreModelSwitch`, and `PostModelSwitch`, and to 10 on
> `MessageDisplay`.

**The unit is seconds, not milliseconds.** This differs from the Bash tool's `timeout` field,
which is milliseconds (L1640). A `"timeout": 5000` copied from a Bash tool call into a hook
means 5,000 seconds.

**On timeout, PreToolUse fails open** (L869-874):

> Apart from a command hook you run with `async: true`, Claude Code cancels a `command`, `http`,
> or `mcp_tool` hook that reaches its `timeout`, discarding the hook's output, so on most events a
> timed-out hook renders no decision.
>
> * A timed-out `command`, `http`, or `mcp_tool` hook doesn't block the tool call. The call
>   continues through the normal permission flow, so don't count on a stalled hook to act as a gate.

So a slow `kblam hook` cannot hold a gate. Default 600 s means a hung kblam would stall Claude
for ten minutes and then allow the call; set an explicit short `timeout` if that matters.

---

## H9 — Which shell runs a hook command on Windows, and quoting

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (verbatim, local mirror); Git Bash presence observed on this machine

**Shell form (no `args`)** (hooks-reference L472):

> **Shell form** runs when `args` is absent. The `command` string is passed to a shell: `sh -c`
> on macOS and Linux, **Git Bash on Windows**, or PowerShell when Git Bash isn't installed. Set
> the `shell` field to choose explicitly. The shell tokenizes the string, expands variables, and
> interprets pipes, `&&`, redirects, and globs.

**`shell` field** (L462):

> Shell to use for this hook. Accepts `"bash"` or `"powershell"`. Defaults to `"bash"`, or to
> `"powershell"` on Windows when Git Bash isn't installed. Setting `"powershell"` runs the command
> via PowerShell on Windows. Does not require `CLAUDE_CODE_USE_POWERSHELL_TOOL` since hooks spawn
> PowerShell directly. Ignored when `args` is set.

**Observed on this machine:** Git Bash is installed (`C:\Program Files\Git\bin\bash.exe`), so the
shell-form default here is **Git Bash**, not cmd.exe and not PowerShell. Hooks are spawned
directly, so the user's `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` setting does not change which shell a
hook command string runs in.

**Exec form (with `args`)** (L470-476): no shell at all, each `args` element passed verbatim. But
on Windows there is a trap:

> On Windows, exec form requires `command` to resolve to a real executable such as a `.exe`. The
> `.cmd` and `.bat` shims that npm, npx, eslint, and other tools install in `node_modules/.bin`
> are not executables and can't be spawned without a shell. To run them in exec form, invoke the
> underlying script with `node` directly.

**Quoting** (L632): "Prefer exec form for any hook that references a path placeholder. In shell
form, wrap each placeholder in double quotes."

**PowerShell specifics** (L3803-3837), if kblam ever ships a `.ps1` hook:

> Claude Code auto-detects `pwsh.exe`, the PowerShell 7 and later executable, and falls back to
> `powershell.exe` for Windows PowerShell 5.1.

> To reference the project root from a PowerShell shell-form command, write
> `${CLAUDE_PROJECT_DIR}` or `$env:CLAUDE_PROJECT_DIR`... PowerShell then resolves the value from
> the exported environment after parsing, so the placeholder works inside double-quoted strings
> but not inside single-quoted strings, where PowerShell never expands variables.

> Don't write the bare `$CLAUDE_PROJECT_DIR` spelling in a PowerShell hook. PowerShell parses it
> as an undefined local variable and resolves it to `$null`, which leaves the script path without
> its project-root prefix.

---

## H10 — Project rules `.claude/rules/*.md`: frontmatter and load trigger

verified: 2026-09-22 @ kblam 2bdb121
evidence: memory-doc (live fetch, verbatim)

**Where they live and what loads unconditionally:**

> Place markdown files in your project's `.claude/rules/` directory. Each file should cover one
> topic, with a descriptive filename like `testing.md` or `api-design.md`. All `.md` files are
> discovered recursively, so you can organize rules into subdirectories like `frontend/` or
> `backend/`

> Rules without `paths` frontmatter are loaded at launch with the same priority as
> `.claude/CLAUDE.md`.

**Path scoping** — the frontmatter is a `paths` key holding a YAML list of globs:

```markdown
---
paths:
  - "src/api/**/*.ts"
---

# API Development Rules

- All API endpoints must include input validation
- Use the standard error response format
- Include OpenAPI documentation comments
```

Multiple patterns and brace expansion:

```markdown
---
paths:
  - "src/**/*.{ts,tsx}"
  - "lib/**/*.ts"
  - "tests/**/*.test.ts"
---
```

**When a path-scoped rule loads** — verbatim:

> Rules without a `paths` field are loaded unconditionally and apply to all files. Path-scoped
> rules trigger when Claude **reads** files matching the pattern, not on every tool use.

So the trigger is the **Read** tool (or an equivalent file-content read the harness performs) on
a matching path — not Grep hits, not Edit, not Write. A rule scoped to `src/**/*.py` is not
guaranteed to be in context merely because Claude edited or grepped a file under `src`.

Also: "As of v2.1.198, matching also works when Claude reaches a file through a symlinked path to
the project directory."

**Frontmatter reference** — the complete set of fields:

> Configure a rule with YAML frontmatter between `---` markers at the top of the file. `paths` is
> the only field Claude Code reads from a rule; any other field is ignored without an error.
> Claude Code removes the frontmatter before loading the rule into context.

| Field | Required | Description |
| :--- | :--- | :--- |
| `paths` | No | Glob patterns that scope the rule to matching files. Accepts a YAML list or a comma-separated string |

If the YAML between the markers doesn't parse, the rule loads as if it had no `paths`.

**Glob gotchas:** `[` starts a bracket expression; an unparseable `[` pattern matches nothing
(escape as `\[`). A rule's whole `paths` list shares one budget of 1,000 expanded patterns and
4 MiB; patterns without braces don't count against it, and a pattern that would exceed the budget
is used unexpanded and "its literal braces match no files".

**User-level and precedence:** `~/.claude/rules/` applies to every project. "Claude Code loads
user-level rules before project rules, so a project rule appears later in Claude's context than a
user rule. Neither set overrides the other: if a user rule and a project rule conflict, Claude
may follow either one, so keep the two consistent."

**Symlinks:** supported; a symlink whose target is outside the working directory is treated as an
external import and its rules don't load until external imports are approved, after which "only
the ones without a `paths` field load."

**Subagents:** the memory doc does not state a subagent carve-out for rules. It does state, for
CLAUDE.md, that subdirectories' CLAUDE.md files load "when Claude reads files in those
subdirectories" rather than at launch. Whether rules load for subagents and teammates is **not
explicitly documented** in the pages I read — treat as unknown, see the OPEN QUESTIONS note in
H14's neighbourhood. The safer design assumption for kblam: do not depend on a rule being loaded
inside a subagent.

Rules are skipped if you exclude `project` from `--setting-sources`.

---

## H11 — Project skills `.claude/skills/<name>/SKILL.md`: frontmatter and loading

verified: 2026-09-22 @ kblam 2bdb121
evidence: skills-doc (live fetch)

**Location and naming:** `.claude/skills/<skill-name>/SKILL.md` (project) or
`~/.claude/skills/<skill-name>/SKILL.md` (personal). "The directory name becomes the command you
type to invoke the skill (e.g., `.claude/skills/deploy/SKILL.md` → `/deploy`)."

**Frontmatter — all fields are optional.** Field names use lowercase words separated by hyphens:

```yaml
---
name: my-skill
description: What this skill does
when_to_use: Additional context for when Claude should invoke it
argument-hint: [issue-number]
arguments: [issue, branch]
disable-model-invocation: false
user-invocable: true
allowed-tools: Read Grep
disallowed-tools: AskUserQuestion
model: claude-3-5-sonnet
effort: high
context: fork
agent: Explore
background: true
paths: "src/**/*.js,tests/**"
shell: bash
license: MIT
compatibility: "Claude Code v2.1.200+"
---
```

**`name` and `description` are not required**, but:

> `name` | No | Display name; directory name becomes the command in personal/project skills
> `description` | Recommended | Drives automatic model invocation; combined with `when_to_use` is
> truncated at 1,536 characters

> Claude uses this to decide when to apply the skill. If omitted, uses the first non-empty line of
> the markdown content.

> The `description` and `when_to_use` are concatenated in the skill listing and truncated at
> 1,536 characters total.

**Progressive disclosure** (skills-doc):

| Frontmatter | User can invoke | Claude can invoke | Context loading |
| :--- | :--- | :--- | :--- |
| (default) | Yes | Yes | Description always loaded; full skill loads when invoked |
| `disable-model-invocation: true` | Yes | No | Description NOT in context; skill loads only when you invoke |
| `user-invocable: false` | No | Yes | Description always loaded; skill loads when Claude invokes |

> Skill content lifecycle: When you or Claude invoke a skill, the rendered `SKILL.md` content
> enters the conversation as a single message and stays there across later turns.

`allowed-tools` "Grants permission for listed tools during invocation turn only"; the permissions
clear on the next user message.

**Hooks in skill frontmatter** are registered for the rest of the session, and `once: true` is
honoured there (hooks-reference L693). Skill frontmatter is the **only** place `once` works.

**Substitutions available in skill content:** `$ARGUMENTS`, `$0`/`$1`/`$N`, `$name` (from
`arguments`), `${CLAUDE_SESSION_ID}`, `${CLAUDE_SKILL_DIR}`, `${CLAUDE_PROJECT_DIR}`. Dynamic
context injection uses the `` !`command` `` syntax, which runs before Claude sees the skill.

**Subagents:** skills reach subagents via `context: fork` (runs the skill in an isolated subagent
context, where "Full skill content is the subagent's prompt") or via the `skills` field in a
subagent definition ("full skill content is injected at subagent startup"). A normal project
skill's *description* is what the main model sees; whether teammates inherit the project skill
list is **not documented** in the page I read.

---

## H12 — MultiEdit is not a tool in Claude Code 2.1.280 (documented absence)

verified: 2026-09-22 @ kblam 2bdb121
evidence: tools-reference + hooks-reference (documented absence) + installed-cli (observed)

The name `MultiEdit` does **not** appear anywhere in the hooks reference or the tools reference.
Pages checked, all fetched 2026-09-22:

- <https://code.claude.com/docs/en/tools-reference> — the built-in tool list does not include
  `MultiEdit`. (It does include `NotebookEdit`.)
- <https://code.claude.com/docs/en/hooks>, the PreToolUse `tool_input` table (local copy,
  L1628-1807) — documents `Bash`, `PowerShell`, `Write`,
  `Edit`, `Read`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `Agent`, `AskUserQuestion`,
  `ExitPlanMode`. No `MultiEdit`, and no `NotebookEdit` section either.
- The changelog shipped with the CLI (`~/.claude/cache/changelog.md`, 7,274 lines, back to
  version 0.2.21) contains **zero** occurrences of `MultiEdit`.

**The file-writing tools the docs actually name:**

| Tool | `tool_input` path key |
| :--- | :--- |
| `Write` | `file_path` |
| `Edit` | `file_path` |
| `NotebookEdit` | `notebook_path` (key observed in the binary, not documented on the hooks page) |

**Observed, installed-cli:** `MultiEdit` still exists as a *string* in the 2.1.280 binary, but
only in legacy name maps, never as a registered tool with a description:

- a hook file-tool classifier set: `var rP=["Write","Edit","MultiEdit","NotebookEdit"],Iy=new Set(rP)`
- a permission-rule path map: `Write":"...||r.toolName==="NotebookEdit"||r.toolName==="MultiEdit"?"Edit":...`
- an input-key map: `MultiEdit:{input:"file_path",responseMembers:["filePath"]}`

It is **absent** from `filePatternTools:["Read","Write","Edit","Glob","NotebookRead","NotebookEdit","Cd"]`.
**Inferred:** these are compatibility shims so an old `settings.json` that still names `MultiEdit`
in a deny rule does not break; the tool itself is gone.

**Consequence for kblam:** listing `MultiEdit` in a PreToolUse matcher is harmless but dead — it
can never match, because no `tool_name` of `MultiEdit` is ever produced. The matcher for
file-writing tools should be `Write|Edit|NotebookEdit`.

---

## H13 — Invoking `kblam hook <event>` from a hook on this machine

verified: 2026-09-22 @ kblam 2bdb121
evidence: kblam pyproject.toml + `.venv/Scripts` listing (observed) + hooks-reference (documented)

**Observed** from the development checkout's `pyproject.toml`:

```toml
[project.scripts]
kblam = "kblam.cli:main"

[build-system]
requires = ["uv_build>=0.11.13,<0.12.0"]
build-backend = "uv_build"
```

and `.venv\Scripts\` contains `kblam.exe` (plus `pytest.exe`,
`python.exe`, and the usual uv shims). uv's build backend writes real `.exe` launchers on Windows,
not `.cmd`/`.bat` shims.

**Why this matters:** in **shell form** the command string goes to Git Bash (H9), which resolves
`kblam` only if `.venv\Scripts` is on `PATH` in that non-interactive shell. A venv is on `PATH`
only when activated, and a hook shell is not an activated venv. So a plain
`"command": "kblam hook Stop"` will most likely fail with exit 127 — which, per H7, is a
**non-blocking** error, so the gate is silently disabled.

**Recommended form** — exec form with the absolute `.exe` path, which needs no shell, no quoting,
and no `PATH`:

```json
{
  "type": "command",
  "command": "${CLAUDE_PROJECT_DIR}/.venv/Scripts/kblam.exe",
  "args": ["hook", "Stop"],
  "timeout": 30
}
```

`${CLAUDE_PROJECT_DIR}` is substituted into `command` and each `args` element in exec form
(hooks-reference L470), and it is also exported as the environment variable `CLAUDE_PROJECT_DIR`
(L497). On Windows the `.exe` is a real executable, so exec form is legal there (L475). Note the
placeholder uses a forward slash here — Git Bash resolves `/` fine, and Claude Code substitutes
the string before spawning.

If kblam is ever invoked by name instead, it must be shell form, and the unactivated-venv `PATH`
problem returns.

---

## H14 — `Bash|PowerShell` matcher on this machine

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (documented) + `~/.claude/settings.json` (observed)

**Observed:** on the development machine (Windows) the PowerShell tool is enabled, with
`CLAUDE_CODE_USE_POWERSHELL_TOOL=1` set in the user-scope `~/.claude/settings.json`.

**Documented** (hooks-reference L1677-1681):

> Match `Bash|PowerShell` in hooks that inspect shell commands, so they cover both tools:
> * On Windows, wherever the PowerShell tool is enabled, Claude treats PowerShell as the primary
>   shell and routes shell commands through it.
> * On Windows without Git Bash, the tool is enabled automatically and Claude Code doesn't
>   register the Bash tool at all.
> * A hook that matches only `Bash` never fires there.

With `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` set, the config has explicitly enabled the PowerShell
tool, and the documented behaviour is that PowerShell becomes the primary shell. A PreToolUse
matcher of `"Bash"` alone is therefore a real risk of never firing for shell commands in this
environment. The `Bash` tool does still exist in this session (I have used it), so the two are
not mutually exclusive — but the routing preference points at PowerShell, and I could not confirm
the routing split without running the denied probe. **Flagged in OPEN QUESTIONS.**

The safer matcher is `"Bash|PowerShell"`, and `${CLAUDE_PROJECT_DIR}` handling for the two differ
if any hook is written in shell form (see H9's PowerShell quoting rules). **Inferred:** the risk
is low if the hook is written in exec form with `args`, since the shell never parses the command.

---

## H15 — A PreToolUse hook *can* identify the calling subagent

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (verbatim, local mirror)

SPEC §8.1 and §13 list "whether the hook can identify which subagent is writing" as unconfirmed.
It is confirmed: **yes**, via two additional common input fields.

First, hooks run inside subagents at all (hooks-reference L267):

> Hooks from settings files, managed policy settings, and plugins also run inside subagents. When a
> subagent calls a tool, tool events such as `PreToolUse` and `PostToolUse` fire the same configured
> hooks as in the main conversation, and the input carries the `agent_id` and `agent_type` common
> input fields that identify the subagent.

The two fields (L765-768):

| Field | Description (verbatim) |
| :--- | :--- |
| `agent_id` | Unique identifier for the subagent. **Present only when the hook fires inside a subagent call.** Use this to distinguish subagent hook calls from main-thread calls. |
| `agent_type` | Agent name (for example, `"Explore"` or `"security-reviewer"`). Present when the session uses `--agent` or the hook fires inside a subagent. For subagents, the subagent's type takes precedence over the session's `--agent` value. |

So a `kblam hook PreToolUse` handler can tell a subagent's write from the main thread's by testing
whether `agent_id` is present, and can key per-agent state (an open ticket, say) on `agent_id`.

**Two limits worth knowing before SPEC §13 relies on this:**

1. **`agent_type` is a type, not a teammate name.** It carries the subagent's *type* — `Explore`,
   `general-purpose`, or a custom agent name like `security-reviewer`. There is no documented field
   carrying the human-facing teammate name a coordinator addressed the agent by. If kblam's ticket
   design needs "which named teammate holds ticket 7", `agent_id` is the only per-call key
   available, and it is an opaque id, not a name.
2. **SubagentStop's identity fields have a false-positive case** (see H3): SubagentStop also fires
   for Claude Code's *internal* agents (prompt suggestions, `/btw` side questions), and for those
   `agent_type` is the session's own agent name, or an empty string when the session has none. An
   unmatchered SubagentStop hook sees them. If kblam's Stop-side ticket check must not fire for
   those, matcher-narrow it or treat an empty `agent_type` as a no-op.

Note also that `${CLAUDE_PROJECT_DIR}` and `cwd` behave the same inside a subagent as outside: the
subagent's tool calls carry the parent session's `cwd` unless a worktree is involved (H8).

---

# Plugin packaging (P1–P8)

Additional sources for this section, all fetched live 2026-09-22: `plugins` =
<https://code.claude.com/docs/en/plugins>, `plugins-reference` =
<https://code.claude.com/docs/en/plugins-reference>, `plugin-marketplaces` =
<https://code.claude.com/docs/en/plugin-marketplaces>, `discover-plugins` =
<https://code.claude.com/docs/en/discover-plugins>, `settings-reference` =
<https://code.claude.com/docs/en/settings-reference>, `settings` =
<https://code.claude.com/docs/en/settings>, `sub-agents` =
<https://code.claude.com/docs/en/sub-agents>.

---

## P1 — A plugin can ship skills, hooks and agents, but **not rules**

verified: 2026-09-22 @ kblam 2bdb121
evidence: plugins + plugins-reference (documented, verbatim)

**Components a plugin can ship** (plugins, "Plugin structure overview"):

| Directory | Purpose |
| :--- | :--- |
| `.claude-plugin/` | Contains `plugin.json` manifest (optional if components use default locations) |
| `skills/` | Skills as `<name>/SKILL.md` directories |
| `commands/` | Skills as flat Markdown files. Use `skills/` for new plugins |
| `agents/` | Custom agent definitions |
| `hooks/` | Event handlers in `hooks.json` |
| `.mcp.json` | MCP server configurations |
| `.lsp.json` | LSP server configurations |
| `monitors/` | Background monitor configurations in `monitors.json` |
| `bin/` | Executables added to the Bash tool's `PATH` while the plugin is enabled |
| `settings.json` | Default settings applied when the plugin is enabled |

The plugins-reference directory tree adds `workflows/`, `output-styles/` and `themes/`. It also
notes: "All other directories (`commands/`, `agents/`, `skills/`, `workflows/`, `output-styles/`,
`themes/`, `monitors/`, `hooks/`) must be at the plugin root, not inside `.claude-plugin/`."

**There is no `rules/` directory in any plugin layout**, and the plugin `settings.json` cannot
carry rules: "Plugins can include a `settings.json` file at the plugin root to apply default
configuration when the plugin is enabled. **Currently, only the `agent` and `subagentStatusLine`
keys are supported.**" A `.claude/rules/*.md` file placed in a plugin is not a documented
component and will not load.

**Closest thing a plugin can ship.** Two documented mechanisms, and only the second reproduces
path scoping:

1. **A skill** (`skills/<name>/SKILL.md`). Loads when its `description` matches, is
   model-invoked, and can be preloaded into a subagent only via the `skills` frontmatter field —
   which reaches project and user skills, not plugin skills (see P6). This replaces the rule on
   the *write* side (the skill is the "how to add a finding" guidance), but it does not reproduce
   "always in context when a `findings/**` file is touched".
2. **A hook that injects `additionalContext`.** A `PreToolUse` or `PostToolUse` hook matched on
   the `Read` tool, returning `hookSpecificOutput.additionalContext` when the path matches
   `findings/`, is the documented equivalent of a path-scoped rule: "Claude Code wraps the string
   in a system reminder and inserts it into the conversation at the point where the hook fired"
   (hooks-reference L1004). The hook can consult `tool_input.file_path`, so it can do the path
   scoping the plugin's missing `rules/` cannot. **Inferred**, from composing two documented
   facts; the composition itself is not written down anywhere. Caveats that are documented: the
   string is capped at 10,000 characters, and text "framed as out-of-band system commands can
   trigger Claude's prompt-injection defenses" (H6).

A third recorded option is the `InstructionsLoaded` lifecycle event, which "fires when a CLAUDE.md
or `.claude/rules/*.md` file is loaded into context" (plugins-reference). It is a **notification**
that such a file loaded — it cannot cause one to load, and it has no decision control. It is
useful only for observing that the consuming repo's rule was picked up.

---

## P2 — `${CLAUDE_PLUGIN_ROOT}` works in both forms; `bin/` puts executables on PATH

verified: 2026-09-22 @ kblam 2bdb121
evidence: plugins-reference + hooks-reference (documented, verbatim)

**Substitution in both forms — yes.**

- Exec form (hooks-reference L470): "Claude Code resolves `command` as an executable on `PATH` and
  spawns it directly with `args` as the argument vector. There is no shell... path placeholders
  like `${CLAUDE_PLUGIN_ROOT}` are substituted into `command` and into each `args` element as
  plain strings."
- Shell form (plugins-reference): "In hook commands, use exec form with `args` so each path is
  passed as one argument with no quoting. In shell-form hooks and monitor commands, wrap the
  variables in double quotes, as in `"${CLAUDE_PROJECT_DIR}/scripts/server.sh"`."

Which fields resolve placeholders (plugins-reference):

| Plugin component | Fields where placeholders resolve |
| :--- | :--- |
| Skill and agent content | Anywhere the placeholder appears |
| Hook and monitor commands | Anywhere the placeholder appears |
| MCP `stdio` servers | `command`, `args`, `env` |
| MCP `http`, `sse`, `ws` servers | `url`, `headers`, `headersHelper` |
| LSP servers | `command`, `args`, `env`, `workspaceFolder` |

> All three are exported as environment variables to hook processes and to MCP and LSP server
> subprocesses. They aren't present in the environment of commands Claude runs through the Bash
> tool, in the main session or in a subagent. In plugin content, write the placeholder instead,
> and Claude Code substitutes the path inline when it loads the content.

That last sentence is a trap worth stating plainly: a plugin's own Python code, invoked via the
Bash tool, cannot read `CLAUDE_PLUGIN_ROOT` from its environment. Only hook processes and MCP/LSP
subprocesses get it. A `kblam hook` process is a hook process, so it does get it.

**`bin/` is documented, with one restriction:**

> `bin/` | Plugin root | Executables added to the Bash tool's `PATH` and invokable as bare commands while the plugin is enabled. You can't include this directory in a plugin you distribute through claude.ai organization settings.

So a plugin can ship `bin/kblam` and it becomes a bare command for the Bash tool while enabled.
Two limits: the distribution restriction above, and (inferred) it does nothing for a hook that
runs `kblam` in **exec form**, which resolves `command` against `PATH` — the docs do not say
whether plugin `bin/` additions reach the hook-spawning PATH or only the Bash tool's.

**Recommendation, marked inferred:** for a hook, prefer
`"command": "${CLAUDE_PLUGIN_ROOT}/bin/kblam.exe"` with `args`, or the persistent data path, over
relying on `bin/` PATH injection. See P5.

---

## P3 — A repo can declare a marketplace and plugins; nothing auto-installs

verified: 2026-09-22 @ kblam 2bdb121
evidence: discover-plugins, plugin-marketplaces, settings (documented, verbatim)

**Yes, a committed `.claude/settings.json` can declare both.** Exact JSON (plugin-marketplaces):

```json
{
  "extraKnownMarketplaces": {
    "my-team-tools": {
      "source": {
        "source": "github",
        "repo": "your-org/claude-plugins"
      }
    }
  },
  "enabledPlugins": {
    "code-formatter@company-tools": true,
    "deployment-tools@company-tools": true
  }
}
```

**What actually happens on a machine where the plugin is not installed** — this is the
load-bearing answer, and it is *not* a prompt and *not* an auto-install:

> Once a team member [trusts the repository folder], Claude Code adds these marketplaces without a
> further prompt.

> As of Claude Code v2.1.195, adding the marketplace doesn't install plugins that come from an
> external source, on any path that loads plugins. A plugin that only the project's
> `.claude/settings.json` enables, and that comes from an external source such as a GitHub
> repository or npm package, doesn't load until the team member installs it. Until then, Claude
> Code reports the plugin as not installed and shows the `claude plugin install` command to run.

And from the settings page, on the trust gate:

> **The key waits for trust.** `permissions.allow` rules, `permissions.additionalDirectories`,
> `extraKnownMarketplaces`, and most `env` values apply only after each teammate trusts the folder.
> Until then they still see prompts and don't get plugins from a marketplace the file declares.

So the sequence for a teammate on a fresh clone is: clone → trust the folder → Claude Code
silently registers the marketplace → the plugin is reported as **not installed**, with the
install command shown → the teammate must run `claude plugin install <plugin>@<marketplace>`.
There is no prompt and no automatic install.

**The exception that matters for kblam.** The "doesn't install" sentence is explicitly scoped to
"a plugin... from an external source such as a GitHub repository or npm package". A plugin whose
marketplace entry uses a **relative path** inside the same repository is not an external source,
and relative-path sources have an in-place loading rule (P5). **Inferred, and flagged as the
single most important unverified point in this section:** an in-repo plugin declared by a relative
path, enabled by `enabledPlugins` in the same committed `.claude/settings.json`, very likely loads
with no install step at all. The docs do not state this in either direction — I checked the
relative-paths, copy/link-mode and enabledPlugins sections and the page is silent on it. It is
worth a five-minute experiment before SPEC commits to the distribution model.

Two further documented facts on scope:

- A session with several repositories "reads only the `enabledPlugins` and `extraKnownMarketplaces`
  keys from each repository's `.claude/settings.json`, not permission rules, hooks, `env`, or other
  keys" (`settings`).
- Plugins are among the things that do not carry over to a cloud session (settings, L748).

---

## P4 — Marketplace sources and how updates arrive

verified: 2026-09-22 @ kblam 2bdb121
evidence: plugin-marketplaces + discover-plugins (documented, verbatim)

**Marketplace source types** (`/plugin marketplace add` or `extraKnownMarketplaces`):

| Type | Example |
| :--- | :--- |
| Local directory | `./my-marketplace`, or a direct path to a `marketplace.json` |
| GitHub shorthand | `owner/repo`, `owner/repo@ref` |
| Git URL | `https://gitlab.com/team/plugin.git`, `git@github.com:owner/repo.git`, optional `#ref` |
| Remote URL | direct HTTPS URL to a `marketplace.json` |
| claude.ai | marketplaces hosted for your account, added by name |
| npm / archive / command | as **plugin** sources within a marketplace entry, not as marketplace sources |

Private repositories are supported: "Claude Code uses your existing git credential helpers, so
HTTPS access via `gh auth login`, macOS Keychain, or `git-credential-store` works the same as in
your terminal. SSH access works as long as the host is already in your `known_hosts` file and the
key is loaded in `ssh-agent`."

**Plugin source types inside a marketplace entry** (this is where the portability choice lives):

| Source | Format |
| :--- | :--- |
| Relative path | `"./plugins/my-plugin"` — "Paths resolve relative to the marketplace root, which is the directory containing `.claude-plugin/`" |
| GitHub | `{"source": "github", "repo": "owner/plugin-repo", "ref": "v2.0.0", "sha": "..."}` |
| Git URL | `{"source": "url", "url": "...", "ref": "main", "sha": "..."}` |
| Git subdirectory | `{"source": "git-subdir", "url": "...", "path": "tools/claude-plugin", "ref": "..."}` |
| npm | `{"source": "npm", "package": "@acme/claude-plugin", "version": "2.1.0", "registry": "..."}` |
| Zip archive | `{"source": "archive", "url": "...zip", "sha256": "..."}` |
| Command | `{"source": "command", "command": "my-tool claude-plugin-path", "timeout": 60, "mode": "copy"}` |

**Version pinning and updates.** The manifest `version` field controls it:

> Optional. Semantic version. Setting this pins the plugin to that version string, so users only
> receive updates when you bump it, except for a `command` source or a plugin loaded in place.

Auto-update is per marketplace and is **off by default for third-party and local development
marketplaces**; "an official Anthropic marketplace... have auto-update enabled by default". Updates
land after session start "with a random delay of up to ten minutes, so the running session keeps
using the versions it loaded at launch", and you are notified to run `/reload-plugins`.
`DISABLE_AUTOUPDATER` turns it off; `FORCE_AUTOUPDATE_PLUGINS=1` decouples it.

**Design consequence for kblam, inferred:** three distribution routes behave very differently for
a project-neutral tool. (a) A GitHub-hosted marketplace means every teammate runs an install step
and is version-pinned. (b) An in-repo relative-path marketplace means no install step and, for an
in-place load, edits take effect at next session or `/reload-plugins` with no version bump
(P5) — but it must be copied into each consuming repo, which is what the user was trying to avoid.
(c) A git URL to a dedicated kblam repo gives one source of truth but the version-pin and install
step back. The docs do not adjudicate this; it is a DECISION for the coordinator.

---

## P5 — Where an installed plugin lives, and copy vs in-place

verified: 2026-09-22 @ kblam 2bdb121
evidence: plugins-reference + plugin-marketplaces (documented, verbatim)

**Marketplace plugins are copied into a cache by default:**

> Claude Code copies *marketplace* plugins to the user's local plugin cache
> (`~/.claude/plugins/cache`), unless the plugin loads in place.

> Each installed version is a separate directory in the cache, grouped by marketplace and plugin
> and named for the resolved version, with its own copy of the plugin's files and Node.js package
> dependencies.

**It loads in place in exactly two documented cases:**

> A `command` source in link mode loads in place, and so does a **relative path source in a
> marketplace added from a local directory**.

**In-place behaviour** (plugins-reference, "Plugin caching and file resolution"):

> For a plugin loaded in place from a local-directory marketplace, your edits to the source
> directory take effect at the next session start or `/reload-plugins`. You don't need a version
> bump. The plugin's hook processes and MCP and LSP servers receive a `CLAUDE_PLUGIN_ROOT` that
> points at the source directory. Claude Code doesn't install the plugin's Node.js package
> dependencies into the source directory. Install them there yourself, or from a hook into the
> persistent data directory.

**Copied-plugin path instability** — the reason this matters for a Python venv:

> For a copied plugin, `${CLAUDE_PLUGIN_ROOT}` changes when the plugin updates. The previous
> version's directory remains on disk for a grace period after an update, but treat it as ephemeral
> and don't write state there.

> When you update or uninstall a plugin, Claude Code marks the previous version directory as
> orphaned and removes it in a background sweep roughly 14 days later.

Also: "When a copied plugin updates mid-session, hook commands, monitors, MCP servers, and LSP
servers keep using the previous version's path."

**The documented home for dependencies** is `${CLAUDE_PLUGIN_DATA}`:

> `${CLAUDE_PLUGIN_DATA}` | Persistent directory that survives plugin updates, created on first
> reference | Installed dependencies such as `node_modules` or **Python virtual environments**,
> generated code, and caches

> The `${CLAUDE_PLUGIN_DATA}` directory resolves to `~/.claude/plugins/data/{id}/`, where `{id}` is
> the plugin identifier with characters outside `a-z`, `A-Z`, `0-9`, `_`, and `-` replaced by `-`.
> For a plugin installed as `formatter@my-marketplace`, the directory is
> `~/.claude/plugins/data/formatter-my-marketplace/`.

**Consequence for `uv run --project ${CLAUDE_PLUGIN_ROOT}`, marked inferred:**

- Against a **copied** plugin, `--project ${CLAUDE_PLUGIN_ROOT}` would build the venv inside the
  versioned cache directory, which is destroyed by the ~14-day sweep on update, and the path
  changes on every update. The docs say outright not to write state there. `uv` would rebuild the
  venv on each update, and a mid-session update leaves hooks pointing at the old path until
  `/reload-plugins`.
- The documented pattern for exactly this ("Python virtual environments") is
  `${CLAUDE_PLUGIN_DATA}`, which survives updates. `uv` honours `UV_PROJECT_ENVIRONMENT`, so
  pointing the environment at `${CLAUDE_PLUGIN_DATA}/venv` while leaving the project metadata in
  `${CLAUDE_PLUGIN_ROOT}` matches the documented intent. This is design guidance composed from
  documented constraints, not a documented recipe.
- Against an **in-place** plugin, `${CLAUDE_PLUGIN_ROOT}` is the source directory, so a venv there
  survives — but note the parallel sentence about Node dependencies ("Install them there
  yourself") implies Claude Code does not manage them for you either way.

Symlink handling when a plugin is copied, worth knowing if the Python package is symlinked in:
within the plugin's own directory the symlink is preserved; elsewhere in the same marketplace it
is dereferenced and the content copied; **outside the marketplace it is skipped for security**.

---

## P6 — Plugin components in subagents and teammates

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference + sub-agents + plugins-reference (documented, verbatim); teammates unknown

**Plugin hooks in subagents — yes** (hooks-reference L267):

> Hooks from settings files, managed policy settings, and plugins also run inside subagents. When a
> subagent calls a tool, tool events such as `PreToolUse` and `PostToolUse` fire the same configured
> hooks as in the main conversation, and the input carries the `agent_id` and `agent_type` common
> input fields that identify the subagent.

**Plugin skills in subagents — partly.** A subagent "can still discover and invoke project, user,
and plugin skills through the Skill tool during execution", but the `skills` frontmatter field
that preloads skill content "only references project and user skills, not plugin skills". So a
plugin skill reaches a subagent only if the subagent chooses to invoke it.

**Hooks declared inside a plugin's own agents — no** (sub-agents):

> For security reasons, plugin subagents don't support the `hooks`, `mcpServers`, or
> `permissionMode` frontmatter fields. These fields are ignored when loading agents from a plugin.

That is a different thing from plugin *hooks* running during a subagent, which works (first point).
It means a plugin cannot ship an agent that carries its own frontmatter hooks.

**CLAUDE.md reaches subagents; rules are unstated.** "Every level of the CLAUDE.md hierarchy the
main conversation loads, including `~/.claude/CLAUDE.md`, project rules, `CLAUDE.local.md`,
managed policy files, and any `AGENTS.md` files" — note "project rules" appears inside the
CLAUDE.md-hierarchy list without being defined as a separate component. Explore and Plan are the
documented exceptions: they "skip your CLAUDE.md files and the git status snapshot".
`omitClaudeMd: true` skips them for a custom subagent. Whether `.claude/rules/*.md` specifically
load in a subagent remains **unknown** — this is the same gap as H10, and it now appears in two
independent pages.

**Teammates — unknown.** The only statement found is:

> Subagent definitions from any of these scopes are also available to agent teams: when spawning a
> teammate, you can reference a subagent type, and Claude Code applies **parts of** that definition
> to the teammate.

Which parts is not specified. Nothing on the hooks, plugins, sub-agents or discover-plugins pages
states whether a teammate inherits skills or hooks. For kblam this is load-bearing, because the
enforcement design depends on hooks firing for teammates; H3's `agent_id`/`agent_type` fields are
the only documented evidence that the harness treats teammate tool calls as subagent tool calls.

---

## P7 — Per-project disable, and making a plugin inert without `kblam.toml`

verified: 2026-09-22 @ kblam 2bdb121
evidence: settings-reference, settings, discover-plugins (documented); the conditional case is unknown

**`enabledPlugins` is a per-scope boolean, accepted in any settings file:**

> `enabledPlugins` | Any file (User, Project, Local, Managed) | Turn individual plugins on or off
> per scope

```json
{
  "enabledPlugins": {
    "pluginName": true,
    "anotherPlugin": false
  }
}
```

**Settings precedence, highest first** (settings page, `SettingsPrecedence` component):

| Rank | Level | File |
| :--- | :--- | :--- |
| 1 | Managed settings | `managed-settings.json`, MDM, or the claude.ai console |
| 2 | Command line | `claude --settings` |
| 3 | Project local | `.claude/settings.local.json` |
| 4 | Shared project | `.claude/settings.json` |
| 5 | User | `~/.claude/settings.json` |

"A key set at a higher level overrides the same key set lower down." So a project or project-local
`"plugin@marketplace": false` does override a user-level `true` — and the settings page's own
example of this model is the hooks case: "a `"disableAllHooks": false` in a project's
`.claude/settings.json` overrides a `true` in your user settings." Managed scope is the exception:
plugins installed by administrators "can't be modified" from below.

**There is no documented conditional activation.** Nothing in `enabledPlugins`,
`extraKnownMarketplaces`, or the marketplace schema supports "enable only if a file exists". The
options are per-scope booleans and a marketplace allowlist. **The documented-composition answer,
marked inferred:** the plugin must be enabled, and the *hook itself* must be a no-op when the
project is not a kblam project. That is straightforward and safe given H7: exit 0 with no JSON and
the tool call proceeds through the normal permission flow. A `kblam hook` that cannot find
`kblam.toml` should print nothing and exit 0.

Two blunter levers exist and are documented, both project- or admin-wide rather than per-plugin:
`"disableAllHooks": true` in any settings file, and `allowManagedHooksOnly` (managed only), which
"Run only the hooks your organization deploys" and blocks "your user, project, local, and plugin
hooks". Neither is suitable for making one plugin inert.

**One gap:** the docs state that `disableAllHooks` at user/project/local level cannot disable
*hooks configured through managed policy settings*, but they do not state whether `disableAllHooks`
suppresses **plugin** hooks. `allowManagedHooksOnly` explicitly reaches plugin hooks; `disableAllHooks`
is silent on them. **Unknown.**

---

## P8 — How settings hooks and plugin hooks combine

verified: 2026-09-22 @ kblam 2bdb121
evidence: hooks-reference (documented, verbatim)

**They merge; plugin hooks do not replace or shadow settings hooks.**

> Hook entries merge across settings levels rather than replacing each other: user, project, and
> local settings add their own hooks without removing managed ones. (hooks-reference L278)

> When a plugin is enabled, its hooks merge with your user and project hooks. (plugins page)

**All matching hooks run** (hooks-reference L416):

> All matching hooks run in parallel. If you define the same handler in more than one settings
> file, it runs once. A plugin's or skill's copy of the same handler stays separate.

So an identical `command` string in both `settings.json` and a plugin's `hooks.json` runs **twice**.
Deduplication applies only within settings files.

**Precedence when the decisions differ** (hooks-reference L1819, PreToolUse only):

> When multiple PreToolUse hooks return different decisions, precedence is `deny` > `defer` > `ask` > `allow`.

For Stop and SubagentStop there is no documented precedence rule between competing hooks — any
`decision: "block"` keeps the turn going, and the 8-consecutive-block cap applies to the turn as a
whole.

**Other documented interactions:**

- Parallelism means ordering is not guaranteed for `updatedInput`: "When multiple `PreToolUse` hooks
  return `updatedInput` to rewrite a tool's arguments, the last one to finish takes effect. Since
  hooks run in parallel, the order is non-deterministic." (hooks-guide)
- `allowManagedHooksOnly` (managed) blocks plugin hooks along with user, project and local ones
  (hooks-reference L271).
- A plugin's hook failures follow the same exit-code rules as any other hook (H7), so a plugin hook
  that crashes is non-blocking and silently inert.
- Plugin-scoped MCP tool matchers differ: "Tool matchers and `if` fields take the scoped tool name
  `mcp__plugin_<plugin-name>_<server-name>__<tool>`... A matcher written against the bare server key
  never fires."

**Design consequence for kblam, inferred:** any hook the plugin ships must be written to tolerate
running alongside a settings-file copy of itself, and alongside hooks from every other enabled
plugin. Because all matching hooks run in parallel and a `deny` from any one of them wins, a plugin
kblam hook that denies a write is enforced even if a project settings hook tries to allow it — but
the reverse also holds, so a stray deny in any other plugin's hook cannot be overridden by kblam.
