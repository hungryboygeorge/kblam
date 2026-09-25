"""`kblam hook <event>`: the Claude Code hooks (SPEC §8 items 1-3).

Claude Code sends the hook's input as JSON on stdin; the answer is JSON on stdout. Allowing is
silence, so the normal permission flow still applies. With no kblam.toml there is no knowledge base,
and the hook is silent. A hook never stops an agent's unrelated work: with an unreadable or invalid
kblam.toml, malformed input or any unexpected error it allows the action and prints a note.

- PreToolUse (Write, Edit, NotebookEdit): deny a target under findings/, or under .kblam/ outside
  .kblam/staging/ (kblam's state).
- PreToolUse (Bash, PowerShell): deny a command that visibly writes under findings/, or writes or
  removes under .kblam/ outside .kblam/staging/ (best effort, §8 items 1-2).
- Stop, SubagentStop: silent while findings/ is as kblam last wrote it (tree.hash); otherwise check
  and validate, and block with the failures. SubagentStop is silent for Claude Code's internal
  agents (empty agent_type), which cannot fix findings/.

Only the Stop path imports the validator and the Jev client, so a PreToolUse call stays fast.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from kblam.config import Config, ConfigError, NoConfig, load_config

SKILL_POINTER = "Load the kblam-write skill for how to fix this."
STATE_USE = (".kblam/ holds kblam's own state and only kblam writes it; stage findings under .kblam/staging/ "
             "(kblam new, kblam edit).")

FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "NotebookEdit": "notebook_path"}  # no MultiEdit tool
SHELL_TOOLS = ("Bash", "PowerShell")
STOP_EVENTS = ("Stop", "SubagentStop")
STOP_BLOCK_NAME = "stop-block"     # .kblam/stop-block: the tree digest at the Stop hook's last block
MAX_BLOCK_LINES = 30               # failures quoted in a Stop block; the rest are counted

# --- output -------------------------------------------------------------------------------------


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")


def _note(event: str, text: str) -> int:
    """Allow, and tell the user why the hook did not run."""
    _emit({"systemMessage": f"kblam hook {event}: {text}; allowed"})
    return 0


def _deny(reason: str) -> int:
    _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                  "permissionDecisionReason": f"{reason} {SKILL_POINTER}"}})
    return 0


def _block(reason: str) -> int:
    _emit({"decision": "block", "reason": f"{reason}\n{SKILL_POINTER}"})
    return 0


# --- paths --------------------------------------------------------------------------------------


def _base_dir(data: dict) -> Path:
    cwd = data.get("cwd")
    return Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()


def _config(root: Path | None, data: dict) -> Config:
    if root is not None:
        return load_config(root=root)
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    return load_config(cwd=Path(project) if project else _base_dir(data))


def _absolute(raw: str, base: Path) -> str:
    """`raw` as an absolute, normalised path, without touching the filesystem (it may hold globs)."""
    text = os.path.expanduser(os.path.expandvars(raw))
    if sys.platform == "win32":
        text = re.sub(r"^/([A-Za-z])(?=/|$)", r"\1:", text)  # Git Bash /c/Users -> c:/Users
    return os.path.normcase(os.path.normpath(os.path.join(base, text)))


def _under(raw: str, base: Path, directory: Path) -> bool:
    path, top = _absolute(raw, base), os.path.normcase(os.path.normpath(directory))
    return path == top or path.startswith(top.rstrip(os.sep) + os.sep)


# --- Bash ---------------------------------------------------------------------------------------


HEREDOC_RE = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
WRAPPERS = {"sudo", "command", "builtin", "env", "nohup", "time", "exec"}


def _strip_heredocs(command: str) -> str:
    """Drop here-document bodies, so their text is neither parsed as commands nor breaks quoting."""
    lines, out, pending = command.split("\n"), [], []
    for line in lines:
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        out.append(line)
        pending = [m.group(2) for m in HEREDOC_RE.finditer(line)]
    return "\n".join(out)


def _tokens(command: str, *, backslash_escapes: bool = True) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace = " \t\r"
    if not backslash_escapes:
        lexer.escape = ""  # PowerShell escapes with a backtick; a backslash is a path separator
    lexer.whitespace_split = True
    return list(lexer)


def _is_operator(token: str) -> bool:
    return bool(token) and all(c in "();<>|&\n" for c in token)


def _operands(words: list[str]) -> tuple[list[str], list[str]]:
    """(options, operands) of one command's arguments; `--` ends the options."""
    options, operands, ended = [], [], False
    for word in words:
        if not ended and word == "--":
            ended = True
        elif not ended and word.startswith("-") and word != "-":
            options.append(word)
        else:
            operands.append(word)
    return options, operands


def _simple_command(words: list[str]) -> tuple[str, list[str], list[str], list[str]]:
    """(command name, words from the command on, options, operands), after leading VAR=value words and
    wrappers; the name is "" for an empty command."""
    while words and ("=" in words[0] and not words[0].startswith("=") and not words[0].startswith("-")
                     or os.path.basename(words[0]).lower().removesuffix(".exe") in WRAPPERS):
        words = words[1:]
    if not words:
        return "", [], [], []
    options, operands = _operands(words[1:])
    return os.path.basename(words[0]).lower().removesuffix(".exe"), words, options, operands


def _copy_operands(words: list[str], options: list[str], operands: list[str]) -> tuple[list[str], list[str]]:
    """(sources, destinations) of cp or mv: `-t DIR`/`--target-directory=DIR`, else the last operand."""
    operands = list(operands)
    directory = [o.split("=", 1)[1] for o in options if o.startswith("--target-directory=")]
    for i, word in enumerate(words[1:-1], 1):
        if word == "-t" and words[i + 1] in operands:
            directory.append(words[i + 1])
            operands.remove(words[i + 1])
    if directory:
        return operands, directory
    return (operands[:-1], operands[-1:]) if len(operands) > 1 else ([], [])


def _command_targets(words: list[str]) -> list[str]:
    """Paths one simple command writes: cp/mv destinations, tee files, sed -i files."""
    name, words, options, operands = _simple_command(words)
    if name in ("cp", "mv"):
        return _copy_operands(words, options, operands)[1]
    if name == "tee":
        return operands
    if name == "sed" and any(o == "-i" or o.startswith("--in-place") or (not o.startswith("--") and "i" in o)
                             for o in options):
        scripted = any(o in ("-e", "-f") or o.startswith(("--expression", "--file")) for o in options)
        return operands if scripted else operands[1:]
    return []


def _command_removals(words: list[str]) -> list[str]:
    """Paths one simple command removes: rm and rmdir operands, and the sources of mv."""
    name, words, options, operands = _simple_command(words)
    if name in ("rm", "rmdir"):
        return operands
    if name == "mv":
        return _copy_operands(words, options, operands)[0]
    return []


PS_WRITERS = {  # cmdlet or alias -> (parameters naming the written path, position it takes unnamed)
    "set-content": (("path", "literalpath"), 1), "add-content": (("path", "literalpath"), 1),
    "ac": (("path", "literalpath"), 1), "out-file": (("filepath", "path", "literalpath"), 1),
    "new-item": (("path",), 1), "ni": (("path",), 1),
    "copy-item": (("destination",), 2), "copy": (("destination",), 2), "cp": (("destination",), 2),
    "cpi": (("destination",), 2), "move-item": (("destination",), 2), "move": (("destination",), 2),
    "mv": (("destination",), 2), "mi": (("destination",), 2),
}
PS_SWITCHES = ("force", "recurse", "append", "noclobber", "nonewline", "passthru", "whatif", "confirm",
               "asbytestream", "container")
PS_PARAMETERS = ("path", "literalpath", "filepath", "destination", "name", "itemtype", "value", "encoding")


PS_REMOVERS = {"remove-item", "rm", "del", "ri", "erase", "rd", "rmdir"}  # Remove-Item and its aliases
PS_MOVERS = {"move-item", "move", "mv", "mi"}


def _ps_arguments(rest: list[str]) -> tuple[dict[str, str], list[str]]:
    """(named parameters, positional arguments) of one PowerShell command's arguments. Parameter names
    match case-insensitively by unique prefix; `-Param:value` is accepted; switches take no value."""
    named: dict[str, str] = {}
    positional: list[str] = []
    while rest:
        word, rest = rest[0], rest[1:]
        if not (word.startswith("-") and len(word) > 1):
            positional.append(word)
            continue
        given, _, value = word[1:].partition(":")
        given = given.lower()
        if not value and any(s.startswith(given) for s in PS_SWITCHES):
            continue
        name = next((p for p in PS_PARAMETERS if p.startswith(given)), given)  # unique prefixes are allowed
        if not value and rest:
            value, rest = rest[0], rest[1:]
        named[name] = value
    return named, positional


def _ps_list(values: list[str]) -> list[str]:
    """The items of PowerShell array arguments (`a, b` or `a,b`)."""
    return [item.strip() for value in values for item in value.split(",") if item.strip()]


def _ps_command_removals(words: list[str]) -> list[str]:
    """Paths one PowerShell command removes: the paths of Remove-Item (every positional argument, since
    -Path takes an array) and the source of Move-Item (and their aliases)."""
    if not words:
        return []
    command = words[0].lower()
    if command not in PS_REMOVERS and command not in PS_MOVERS:
        return []
    named, positional = _ps_arguments(words[1:])
    given = [named[p] for p in ("path", "literalpath") if p in named]
    if command in PS_REMOVERS:
        return _ps_list(given + positional)
    return _ps_list(given or positional[:1])


def _ps_command_targets(words: list[str]) -> list[str]:
    """Paths one PowerShell command writes: the path of Set-Content, Add-Content, Out-File and New-Item
    (joined with -Name), and the destination of Copy-Item and Move-Item (and their aliases)."""
    if not words or words[0].lower() not in PS_WRITERS:
        return []
    wanted, position = PS_WRITERS[words[0].lower()]
    named, positional = _ps_arguments(words[1:])
    path = next((named[w] for w in wanted if w in named), None)
    if path is None and len(positional) >= position:
        path = positional[position - 1]
    if "name" in named and wanted == ("path",):
        path = os.path.join(path or ".", named["name"])
    return [path] if path else []


def bash_write_targets(command: str) -> list[str]:
    """Paths that a Bash `command` visibly writes: redirection targets plus _command_targets of each
    simple command. Best effort (SPEC §8 item 2); the Stop hook catches what this misses."""
    return _write_targets(_tokens(_strip_heredocs(command)), _command_targets)


def powershell_write_targets(command: str) -> list[str]:
    """The same for a PowerShell `command`: redirection targets plus _ps_command_targets."""
    return _write_targets(_tokens(command, backslash_escapes=False), _ps_command_targets)


def bash_removal_targets(command: str) -> list[str]:
    """Paths that a Bash `command` visibly removes (_command_removals of each simple command)."""
    return _write_targets(_tokens(_strip_heredocs(command)), _command_removals, redirections=False)


def powershell_removal_targets(command: str) -> list[str]:
    """The same for a PowerShell `command` (_ps_command_removals)."""
    return _write_targets(_tokens(command, backslash_escapes=False), _ps_command_removals, redirections=False)


def _write_targets(tokens: list[str], command_targets, *, redirections: bool = True) -> list[str]:
    """command_targets of each simple command in `tokens`, plus redirection targets if `redirections`."""
    targets: list[str] = []
    words: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if _is_operator(token) and ">" in token:
            if words and (words[-1].isdigit() or words[-1] == "*"):
                words.pop()  # the stream of 2> or *>, split off by the lexer
            following = tokens[i + 1] if i + 1 < len(tokens) else None
            if following is not None and not _is_operator(following):
                if redirections and not (token.endswith("&") and (following.isdigit() or following == "-")):
                    targets.append(following)
                i += 1
        elif _is_operator(token) and "<" in token:
            if words and words[-1].isdigit():
                words.pop()
            i += 1  # the input file or here-document delimiter
        elif _is_operator(token):
            targets += command_targets(words)
            words = []
        else:
            words.append(token)
        i += 1
    return targets + command_targets(words)


# --- events -------------------------------------------------------------------------------------


def _pre_tool_use(cfg: Config, data: dict) -> int:
    tool, tool_input = data.get("tool_name"), data.get("tool_input")
    if tool not in FILE_TOOLS and tool not in SHELL_TOOLS:
        return 0
    if not isinstance(tool_input, dict):
        return _note("PreToolUse", f"no tool_input object for {tool}")
    base, findings = _base_dir(data), cfg.findings_path
    use = (f"{cfg.findings_dir}/ is written only by kblam put: stage the finding with kblam new <topic> "
           f"\"<title>\" or kblam edit <id>, edit the staged copy under .kblam/staging/, then kblam put it.")

    def state(path: str) -> bool:
        """Under .kblam/ (kblam's state) and not under .kblam/staging/ (SPEC §8)."""
        return _under(path, base, cfg.state_dir) and not _under(path, base, cfg.staging_dir)

    if tool in FILE_TOOLS:
        path = tool_input.get(FILE_TOOLS[tool])
        if not isinstance(path, str):
            return _note("PreToolUse", f"{tool} has no {FILE_TOOLS[tool]} string")
        if _under(path, base, findings):
            return _deny(f"kblam: {tool} of {path} denied. {use}")
        if state(path):
            return _deny(f"kblam: {tool} of {path} under .kblam/ denied. {STATE_USE}")
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str):
        return _note("PreToolUse", f"{tool} has no command string")
    try:
        if tool == "Bash":
            targets, removals = bash_write_targets(command), bash_removal_targets(command)
        else:
            targets, removals = powershell_write_targets(command), powershell_removal_targets(command)
    except ValueError:
        return 0  # unbalanced quoting: the shell will refuse it too; the Stop hook covers the rest
    reasons = []
    hits = [t for t in targets if _under(t, base, findings)]
    if hits:
        reasons.append(f"kblam: this command writes under {cfg.findings_dir}/ ({', '.join(hits)}), so it is "
                       f"denied. {use}")
    written, removed = [t for t in targets if state(t)], [t for t in removals if state(t)]
    if written or removed:
        what = " and ".join(f"{verb} {', '.join(paths)}" for verb, paths in
                            (("writing", written), ("removing", removed)) if paths)
        reasons.append(f"kblam: {what} under .kblam/ denied. {STATE_USE}")
    return _deny(" ".join(reasons)) if reasons else 0


def _stop(cfg: Config, event: str, data: dict) -> int:
    from kblam.review import check_findings, open_items
    from kblam.rules import validate
    from kblam.treehash import read_tree_hash, tree_digest
    from kblam.view import load_view

    recorded = read_tree_hash(cfg)
    if recorded is None and not cfg.findings_path.exists():
        return 0  # no knowledge base yet
    digest = tree_digest(load_view(cfg))
    if digest == recorded:
        return 0
    check_findings(cfg, None, command=f"hook {event}")
    view = load_view(cfg)
    lines = [issue.format(view) for issue in validate(view)] + [item.describe() for item in open_items(cfg, view)]
    marker = cfg.state_dir / STOP_BLOCK_NAME
    if not lines:
        return 0
    last = marker.read_text(encoding="ascii").strip() if marker.is_file() else None
    if data.get("stop_hook_active") is True and last == tree_digest(view):
        return _note(event, f"{cfg.findings_dir}/ still fails kblam validate ({len(lines)} failure(s)) and is "
                            f"unchanged since the last block, so the stop is not blocked again")
    from kblam.store import atomic_write

    atomic_write(marker, (tree_digest(view) + "\n").encode("ascii"))
    shown = lines[:MAX_BLOCK_LINES]
    if len(lines) > len(shown):
        shown.append(f"... and {len(lines) - len(shown)} more; run kblam validate for all of them")
    return _block(
        f"kblam: {cfg.findings_dir}/ was changed outside kblam put, and the knowledge base fails kblam "
        f"validate:\n" + "\n".join(shown) + "\n"
        f"Fix each failure through kblam (kblam edit <id>, change the staged copy, kblam put it); never write "
        f"under {cfg.findings_dir}/ directly. Once the tree is clean, kblam validate --record accepts the change."
    )


def run(event: str, root: Path | None = None) -> int:
    """Answer one hook call. Always exits 0: a deny or block is carried by the JSON on stdout."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        data = json.loads(sys.stdin.read())
    except (ValueError, OSError) as exc:
        return _note(event, f"hook input is not JSON ({exc})")
    if not isinstance(data, dict):
        return _note(event, "hook input is not a JSON object")
    if event != "PreToolUse" and event not in STOP_EVENTS:
        return _note(event, f"unknown event; kblam handles PreToolUse, {', '.join(STOP_EVENTS)}")
    try:
        cfg = _config(root, data)
    except NoConfig:
        return 0  # no knowledge base here (hooks configured in a parent directory or a copied settings file)
    except ConfigError as exc:
        return _note(event, str(exc))
    try:
        if event == "PreToolUse":
            return _pre_tool_use(cfg, data)
        if event == "SubagentStop" and data.get("agent_type") == "":
            return 0  # an internal agent (prompt suggestions, /btw), not a subagent (SPEC §8 item 3)
        return _stop(cfg, event, data)
    except Exception as exc:  # a hook must never break the agent's work (SPEC §8)
        return _note(event, f"unexpected {type(exc).__name__}: {exc}")
