"""`kblam hook <event>`: the Claude Code hooks (SPEC §8 items 1-3).

Claude Code sends the hook's input as JSON on stdin; the answer is JSON on stdout. Allowing is
silence, so the normal permission flow still applies. With no kblam.toml there is no knowledge base,
and the hook is silent. A hook never stops an agent's unrelated work: with an unreadable or invalid
kblam.toml, malformed input or any unexpected error it allows the action and prints a note.

- PreToolUse (Write, Edit, NotebookEdit): deny a target under findings/, under the review root
  (records are kblam's) or under .kblam/ outside .kblam/staging/ and .kblam/review-staging/ (kblam's
  state).
- PreToolUse (Bash, PowerShell): deny a command that visibly writes under findings/ or the review
  root, or writes or removes under .kblam/ outside .kblam/staging/ and .kblam/review-staging/ (best
  effort, §8 items 1-2). Under the review root only a record file, a kind folder or the root itself
  is denied a removal: removing a stray file there is how a K12 stray-file error is fixed.
- Stop, SubagentStop: silent while findings/ and the review root are as kblam last wrote them
  (tree.hash); otherwise check and validate, and block with the failures. SubagentStop is silent for
  Claude Code's internal agents (empty agent_type), which cannot fix findings/.

Only the Stop path imports the validator and the Jev client, so a PreToolUse call stays fast.
"""

from __future__ import annotations

import fnmatch
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
REVIEW_USE = ("Review records are written only by kblam: stage one with kblam challenge new, kblam task new "
              "or kblam use review (or kblam challenge/task edit), edit it under .kblam/review-staging/, "
              "then kblam put it.")
REVIEW_KIND_FOLDERS = ("challenges", "tasks", "uses")   # <review root>/<kind> (records.KINDS)
REVIEW_RECORD_GLOBS = ("SC-*.yaml", "CT-*.yaml", "CU-*.yaml")
REVIEW_RECORD_SAMPLES = ("SC-0001.yaml", "CT-0001.yaml", "CU-0001.yaml")  # a glob matching one names a record
# fnmatch folds case on the platforms whose paths do (`fnmatch.fnmatch`, not `fnmatchcase`); these
# two match that, so a glob is judged the same way on Windows and on a case-sensitive filesystem.
_CASE = re.IGNORECASE if os.path.normcase("A") == "a" else 0
# A record file's name is (SC|CT|CU)-<4+ digits>.yaml. A glob names a record when the literal text
# before its first glob character could begin such a name and the literal text after its last could
# end one: REVIEW_RECORD_HEAD_RE matches every prefix, REVIEW_RECORD_TAIL_RE every suffix.
REVIEW_RECORD_HEAD_RE = re.compile(r"(?:S|SC|SC-|C|CT|CT-|CU|CU-|(?:SC|CT|CU)-[0-9]{4,}\.yaml"
                                   r"|(?:SC|CT|CU)-[0-9]*\.(?:y|ya|yam)?|(?:SC|CT|CU)-[0-9]*)?", _CASE)
REVIEW_RECORD_TAIL_RE = re.compile(r"(?:l|ml|aml|yaml|(?:-|C-|SC-|T-|CT-|U-|CU-)?[0-9]*\.yaml)?", _CASE)
GLOB_CHARS = "*?["

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


def _segments(raw: str, base: Path, root: Path) -> list[str] | None:
    """`raw`'s path segments below `root`, or None when it is not under it. [] is `root` itself."""
    if not _under(raw, base, root):
        return None
    path, top = _absolute(raw, base), os.path.normcase(os.path.normpath(root))
    rest = path[len(top):].strip(os.sep) if path != top else ""
    return [part for part in rest.split(os.sep) if part and part != "."]


def _glob_tokens(name: str) -> list[tuple[int, int]]:
    """The spans of `name`'s glob tokens, read as fnmatch reads them: `*` and `?` take one character
    each, and `[...]` is one token from its `[` through its closing `]` (a `]` straight after `[` or
    `[!` is a member of the set, not the end of it). An unclosed `[` is a literal character."""
    spans: list[tuple[int, int]] = []
    i, size = 0, len(name)
    while i < size:
        char = name[i]
        if char in "*?":
            spans.append((i, i + 1))
        elif char == "[":
            j = i + 1
            j += 1 if j < size and name[j] == "!" else 0
            j += 1 if j < size and name[j] == "]" else 0
            end = name.find("]", j)
            if end != -1:
                spans.append((i, end + 1))
                i = end
        i += 1
    return spans


def _names_a_record(name: str) -> bool:
    """`name` is a review record file's name, or a glob that could name one (SPEC §8: SC-*.yaml,
    CT-*.yaml, CU-*.yaml)."""
    if any(fnmatch.fnmatch(name, pattern) for pattern in REVIEW_RECORD_GLOBS):
        return True
    if any(fnmatch.fnmatch(sample, name) for sample in REVIEW_RECORD_SAMPLES):
        return True
    # A glob that does not begin with a literal `SC-`/`CT-`/`CU-` (`C?-0010.yaml`, `[S]C-0010.yaml`,
    # `*0010*`): the samples above miss it, so deny when the literal text before its first glob token
    # could begin a record name and the literal text after its last could end one (the registry
    # backstop is SPEC 1155-1156). Deliberately over-broad: it ignores the text between the tokens,
    # and so denies some globs that name no record.
    spans = _glob_tokens(name)
    if not spans:
        return False
    head, tail = name[:spans[0][0]], name[spans[-1][1]:]
    return bool(REVIEW_RECORD_HEAD_RE.fullmatch(head) and REVIEW_RECORD_TAIL_RE.fullmatch(tail))


def _review_removal(raw: str, base: Path, root: Path) -> bool:
    """SPEC §8: removing the review root, a kind folder or a record file (or a glob that could name one
    of them) is denied. Removing any other file under the root is allowed: that is how a K12
    stray-file error is fixed."""
    segments = _segments(raw, base, root)
    if segments is None:
        return False
    if not segments:
        return True                                    # the root itself
    if len(segments) == 1 and segments[0] in REVIEW_KIND_FOLDERS:
        return True                                    # a kind folder
    last = segments[-1]
    if _names_a_record(last):
        return True                                    # a record file, or a glob that could name one
    return len(segments) == 1 and any(char in last for char in GLOB_CHARS)


def _verb_list(written: list[str], removed: list[str]) -> str:
    """The `<what>` of a shell deny: "writing A, B", "removing C" or "writing A and removing C"."""
    return " and ".join(f"{verb} {', '.join(paths)}" for verb, paths in
                        (("writing", written), ("removing", removed)) if paths)


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
    base, findings, review = _base_dir(data), cfg.findings_path, cfg.review_path
    use = (f"{cfg.findings_dir}/ is written only by kblam put: stage the finding with kblam new <topic> "
           f"\"<title>\" or kblam edit <id>, edit the staged copy under .kblam/staging/, then kblam put it.")

    def state(path: str) -> bool:
        """Under .kblam/ (kblam's state), outside .kblam/staging/ and .kblam/review-staging/ (SPEC §8)."""
        return (_under(path, base, cfg.state_dir) and not _under(path, base, cfg.staging_dir)
                and not _under(path, base, cfg.review_staging_dir))

    if tool in FILE_TOOLS:
        path = tool_input.get(FILE_TOOLS[tool])
        if not isinstance(path, str):
            return _note("PreToolUse", f"{tool} has no {FILE_TOOLS[tool]} string")
        if _under(path, base, findings):
            return _deny(f"kblam: {tool} of {path} denied. {use}")
        if _under(path, base, review):
            return _deny(f"kblam: {tool} of {path} under {cfg.review_dir}/ denied. {REVIEW_USE}")
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
    # SPEC §8: a write under the review root is denied outright; a removal only for the root, a kind
    # folder, a record file or a glob that could name one of those.
    written = [t for t in targets if _under(t, base, review)]
    removed = [t for t in removals if _review_removal(t, base, review)]
    if written or removed:
        reasons.append(f"kblam: {_verb_list(written, removed)} under {cfg.review_dir}/ denied. {REVIEW_USE}")
    written, removed = [t for t in targets if state(t)], [t for t in removals if state(t)]
    if written or removed:
        reasons.append(f"kblam: {_verb_list(written, removed)} under .kblam/ denied. {STATE_USE}")
    return _deny(" ".join(reasons)) if reasons else 0


def _stop(cfg: Config, event: str, data: dict) -> int:
    from kblam.review import check_findings, open_items
    from kblam.rules import errors, validate
    from kblam.treehash import read_recorded, tree_digest_v2
    from kblam.view import load_view

    # SPEC §8 item 3: the format-2 digest of findings/ and the review root. A format-1 or unparseable
    # tree.hash is not (2, root, digest), so the tree counts as changed outside kblam and is validated.
    recorded = read_recorded(cfg)
    if recorded is None and not cfg.findings_path.exists():
        return 0  # no knowledge base yet
    digest = tree_digest_v2(load_view(cfg))
    if recorded == (2, cfg.review_dir, digest):
        return 0
    check_findings(cfg, None, command=f"hook {event}")
    view = load_view(cfg)
    # Warnings alone never block, so only the errors reach the block (SPEC §5, §5.1.4).
    lines = ([issue.format(view) for issue in errors(validate(view))]
             + [item.describe() for item in open_items(cfg, view)])
    marker = cfg.state_dir / STOP_BLOCK_NAME
    if not lines:
        return 0
    last = marker.read_text(encoding="ascii").strip() if marker.is_file() else None
    if data.get("stop_hook_active") is True and last == tree_digest_v2(view):
        return _note(event, f"{cfg.findings_dir}/ still fails kblam validate ({len(lines)} failure(s)) and is "
                            f"unchanged since the last block, so the stop is not blocked again")
    from kblam.store import atomic_write

    atomic_write(marker, (tree_digest_v2(view) + "\n").encode("ascii"))
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
