"""`kblam init [--update]`: set up the git repository containing the current directory (SPEC §12 M6.5).

It writes what the project needs and the user commits: kblam.toml (only if absent), the two indexes and
the record-ID registry (§5.2.6, §11 step 6: created when missing, from the files present, and never
replaced), the .gitattributes and .gitignore lines, the reading rule and the write skill, kblam's hook
entries merged into .claude/settings.json, the CLAUDE.md line (§8.2) and the git pre-commit hook. Those
indexes are written by init itself, not through writes.apply, so the only tree.hash line and warning is
its own. Last it writes `.kblam/tree.hash` in format 2 (§5.2.6 "Upgrade"): created, migrated from
format 1, updated with a changed review root, or kept, with the message saying what to run instead. It
never commits and never writes outside the repository root. A file kblam owns that differs from the
installed version is reported and left alone unless --update is given; kblam.toml is never overwritten.
Finally it runs each hook entry's command once through a shell.

The source files ship in kblam/assets/. Everything is written with LF line endings.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from kblam import k13, registry, review, rules, treehash, writes
from kblam.config import CONFIG_NAME, ConfigError, load_config
from kblam.index import generate_index
from kblam.lock import LockError
from kblam.review_index import generate_review_index
from kblam.store import StoreError, atomic_write
from kblam.view import load_view

ASSETS = Path(__file__).parent / "assets"
RULE = ".claude/rules/kblam-findings.md"
SKILL = ".claude/skills/kblam-write/SKILL.md"
SETTINGS = ".claude/settings.json"
PRE_COMMIT_MARKER = "# kblam pre-commit hook"  # the asset's second line; identifies an older kblam version
KBLAM_COMMAND = "kblam hook"                   # kblam's hook entries are the handlers whose command starts so
KB_ROOT_TOKEN = "{{kb_root}}"                  # the rule, the skill and the hook entries name the KB root so
REVIEW_ROOT_TOKEN = "{{review_root}}"          # ... and the review root is named the same way
GUIDANCE_LINE = ("Findings live in `{root}/`, hold current facts only, and are written only via `kblam put` "
                 "(load the `kblam-write` skill); reading guidance loads when you open one.")

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_LOCKED = 3


class InitError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _asset(rel: str, kb_root: str | None = None, review_root: str | None = None) -> bytes:
    """An asset with LF endings; with `kb_root`, every KB_ROOT_TOKEN replaced by it, and likewise
    REVIEW_ROOT_TOKEN with `review_root`. An asset that names neither token is returned as it is."""
    data = (ASSETS / rel).read_bytes().replace(b"\r\n", b"\n")
    for token, root in ((KB_ROOT_TOKEN, kb_root), (REVIEW_ROOT_TOKEN, review_root)):
        if root is not None:
            data = data.replace(token.encode(), root.encode("utf-8"))
    return data


def _template_prompt(template: bytes):
    """The [jev.prompt] table of the template kblam init writes (the default question wording)."""
    return tomllib.loads(template.decode("utf-8")).get("jev", {}).get("prompt")


def _hook_entries(kb_root: str, review_root: str | None = None) -> dict:
    """hooks.json parsed, then KB_ROOT_TOKEN and REVIEW_ROOT_TOKEN replaced inside its strings (so no
    root can break the JSON)."""
    def substitute(value):
        if isinstance(value, dict):
            return {k: substitute(v) for k, v in value.items()}
        if isinstance(value, list):
            return [substitute(v) for v in value]
        if not isinstance(value, str):
            return value
        for token, root in ((KB_ROOT_TOKEN, kb_root), (REVIEW_ROOT_TOKEN, review_root)):
            if root is not None:
                value = value.replace(token, root)
        return value
    return substitute(json.loads(_asset("hooks.json")))


def _write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Atomic write that keeps an existing file's mode (atomic_write's temp file is 0600 on POSIX)."""
    if mode is None:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    atomic_write(path, data)
    os.chmod(path, mode)


class Init:
    def __init__(self, repo: Path, update: bool):
        self.repo = repo
        self.update = update
        self.status = EXIT_OK

    def report(self, action: str, rel: str, note: str = "") -> None:
        print(f"  {action:<9} {rel}" + (f" ({note})" if note else ""))

    # --- files ------------------------------------------------------------------------------------

    def owned_file(self, rel: str, data: bytes) -> None:
        """A file kblam owns (the rule, the skill): written if absent, rewritten only with --update."""
        path = self.repo / rel
        if not path.exists():
            _write(path, data)
            self.report("created", rel)
        elif path.read_bytes() == data:
            self.report("unchanged", rel)
        elif self.update:
            _write(path, data)
            self.report("updated", rel, "rewritten to the installed version")
        else:
            self.report("kept", rel, "differs from the installed version; kblam init --update rewrites it")

    def append_line(self, rel: str, line: str, *, paragraph: bool = False) -> None:
        """Append `line` to the file unless some line of it already reads so (see append_lines)."""
        self.append_lines(rel, [line], paragraph=paragraph)

    def append_lines(self, rel: str, lines: list[str], *, paragraph: bool = False) -> None:
        """Append each of `lines` the file does not already hold, one per line; create the file if it is
        missing. One report line for the file, naming what it added. With `paragraph`, a blank line
        separates the first from existing text (a markdown paragraph), and a line that starts with one of
        `lines` counts as present: the project may have extended the sentence."""
        path = self.repo / rel
        existed = path.exists()
        old = path.read_bytes() if existed else b""
        present = [existing.strip() for existing in old.decode("utf-8", errors="replace").splitlines()]
        missing = [line for line in lines
                   if line not in present and not (paragraph and any(p.startswith(line) for p in present))]
        if not missing:
            self.report("unchanged", rel, "has the kblam line" if len(lines) == 1 else "has the kblam lines")
            return
        new = old
        if new and not new.endswith(b"\n"):
            new += b"\n"
        if paragraph and new and not new.endswith(b"\n\n"):
            new += b"\n"
        _write(path, new + "".join(f"{line}\n" for line in missing).encode("utf-8"))
        self.report("updated" if existed else "created", rel,
                    "added the kblam line" if paragraph else f"added {', '.join(missing)}")

    def findings_index(self, cfg) -> None:
        """The KB root's INDEX.md (SPEC §11 step 6).

        Created from the findings present when it is missing, and a file that is there is never touched.
        Written here, under the lock, with atomic_write and not through `writes.apply`: an index write
        that advanced tree.hash would print a tree.hash warning of its own before init's tree.hash line
        (D32 f). The lock is mutating, so a review root that changed while records exist is refused here
        (StoreError, exit 1); the recovery of an interrupted write runs in the step before this one.
        """
        rel = f"{cfg.findings_dir}/INDEX.md"
        with writes.locked(cfg, "init", mutating=True):
            view = load_view(cfg)
            path = self.repo / rel
            if path.exists():
                self.report("unchanged", rel, "exists; not regenerated")
            else:
                atomic_write(path, generate_index(view))
                self.report("created", rel, "kblam index")

    def review_index(self, cfg) -> None:
        """The review root's INDEX.md and the record-ID registry (SPEC §11 step 6, §5.2.6).

        The index is created from the records present when it is missing, and a file that is there,
        whatever it holds, is never touched. In the same locked step the registry is created from the IDs
        of the records present when there is none, and never in a KB with no records (D28 b). The lock is
        mutating, so a review root that changed while records exist is refused here (StoreError, exit 1).
        """
        rel = f"{cfg.review_dir}/INDEX.md"
        with writes.locked(cfg, "init", mutating=True):
            view = load_view(cfg)
            path = self.repo / rel
            if path.exists() or path.is_symlink():
                self.report("unchanged", rel, "exists; not regenerated")
            else:
                atomic_write(path, generate_review_index(view))
                self.report("created", rel, "kblam review index")
            registered = writes.registry_after(cfg, k13.present_ids(view), set())
            if registered is not None:
                registry.write_ids(cfg, registered)

    def tree_hash(self, cfg, started, matched: bool) -> None:
        """The tree.hash line, last (SPEC §12 M6.5 "As built", §5.2.6 "tree.hash, format 2" and "Upgrade").

        `started` is treehash.read_recorded of the file as init found it, before its own writes, and
        `matched` whether the tree matched it then. Nothing is written unless the rule allows it: the
        format-2 file covers init's own index writes, a format-1 file is migrated only from a matching
        tree with a clean validation, and an out-of-band change is left for `kblam validate --record`.
        Jev state is never rewritten.
        """
        rel = ".kblam/tree.hash"
        with writes.locked(cfg, "init", mutating=True):
            view = load_view(cfg)
            if started is None:
                if view.records:
                    self.report("kept", rel, "missing, and the review root holds records; run kblam "
                                             "validate --record")
                else:
                    _write_tree_hash_v2(cfg, view)
                    self.report("created", rel)
                return
            fmt, root, _digest = started
            if fmt == 1:
                if matched and _validation_clean(cfg, view):
                    _write_tree_hash_v2(cfg, view)
                    self.report("updated", rel, "migrated to format 2")
                else:
                    self.report("kept", rel, "run kblam validate --record")
            elif root != cfg.review_dir:
                _write_tree_hash_v2(cfg, view)
                self.report("updated", rel, f"review root {root} -> {cfg.review_dir} recorded")
            elif not matched:
                self.report("kept", rel, f"{cfg.findings_dir}/ or {cfg.review_dir}/ changed outside kblam; "
                                         f"run kblam validate --record")
            elif _write_tree_hash_v2(cfg, view):
                self.report("updated", rel, "init's own writes recorded")
            else:
                self.report("unchanged", rel)

    def prompt(self, project, installed) -> None:
        """Report a project `[jev.prompt]` that differs from the template's default wording (SPEC §6.2,
        §9). Informational: the prompt is the project's, so kblam never rewrites it."""
        if project == installed:
            return
        self.report("kept", "[jev.prompt]", "differs from the installed default wording; kblam prompt-id "
                                            "prints the id of this project's wording. Never overwritten")

    def settings(self, current: dict | None, installed: dict) -> None:
        rel = SETTINGS
        if current is None:
            _write(self.repo / rel, _dump(installed))
            self.report("created", rel, "kblam hook entries")
            return
        hooks = current.get("hooks", {})
        present = _kblam_entries(hooks)
        if present == _kblam_entries(installed["hooks"]):
            self.report("unchanged", rel, "kblam hook entries are current")
            return
        if present and not self.update:
            self.report("kept", rel, "kblam hook entries differ from the installed version; "
                                     "kblam init --update rewrites them")
            return
        _write(self.repo / rel, _dump(_merge(current, installed["hooks"])))
        self.report("updated", rel, "kblam hook entries replaced" if present else "kblam hook entries added")

    def pre_commit(self, path: Path) -> None:
        rel = _display(self.repo, path)
        data = _asset("pre-commit")
        if not path.exists():
            _write(path, data, 0o755)
            self.report("created", rel, "git pre-commit hook")
            return
        old = path.read_bytes()
        if old == data:
            if sys.platform != "win32" and not os.access(path, os.X_OK):
                os.chmod(path, 0o755)
                self.report("updated", rel, "made executable")
            else:
                self.report("unchanged", rel)
        elif PRE_COMMIT_MARKER.encode() not in old:
            self.report("refused", rel, "a different pre-commit hook is already there; not overwritten. Run "
                                        f"`kblam --root <repo> validate` from it, or replace it with {ASSETS / 'pre-commit'}")
            self.status = EXIT_REFUSED
        elif self.update:
            _write(path, data, 0o755)
            self.report("updated", rel, "rewritten to the installed version")
        else:
            self.report("kept", rel, "an older kblam pre-commit hook; kblam init --update rewrites it")

    # --- hook check -------------------------------------------------------------------------------

    def check_hooks(self, installed: dict, findings_dir: str) -> None:
        kblam = shutil.which("kblam")
        if kblam:
            print(f"kblam init: kblam on PATH is {kblam}")
        else:
            print("kblam init: kblam is not on PATH, so the hooks will report that they did not run and the "
                  "pre-commit hook refuses every commit. Install it: uv tool install <kblam repository>")
            self.status = EXIT_REFUSED
        shell = _hook_shell()
        if shell is None:
            print("kblam init: neither bash nor pwsh is on PATH; the hook commands were not run")
            self.status = EXIT_REFUSED
            return
        name, argv = shell
        print(f"kblam init: hook check through {name} ({argv[0]}):")
        outside = str(self.repo / "kblam-init-hook-check.txt")
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.repo)}
        for event, groups in installed["hooks"].items():
            for group in groups:
                label = event + (f" [{group['matcher']}]" if group.get("matcher") else "")
                if event == "PreToolUse":
                    payload = {"tool_name": "Write", "tool_input": {"file_path": outside, "content": "x"}}
                    label += f", a Write outside {findings_dir}/"
                else:
                    payload = {"stop_hook_active": False}
                payload = {"session_id": "kblam-init", "cwd": str(self.repo), "hook_event_name": event, **payload}
                for handler in group["hooks"]:
                    answered, text = _run_hook(argv, handler, payload, self.repo, env)
                    print(f"  {label}: {text}")
                    if not answered:
                        self.status = EXIT_REFUSED


def _display(repo: Path, path: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def _dump(settings: dict) -> bytes:
    return (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _is_kblam(handler) -> bool:
    return isinstance(handler, dict) and str(handler.get("command", "")).lstrip().startswith(KBLAM_COMMAND)


def _kblam_entries(hooks: dict) -> list[str]:
    """kblam's handlers in a hooks table, as sorted canonical JSON of [event, matcher, handler]."""
    return sorted(json.dumps([event, group.get("matcher"), handler], sort_keys=True)
                  for event, groups in hooks.items() for group in groups
                  for handler in group.get("hooks", []) if _is_kblam(handler))


def _merge(settings: dict, installed: dict) -> dict:
    """`settings` with every kblam handler removed (a group left empty by that is dropped) and the
    installed groups appended to their events. Other keys, events, groups and handlers are kept."""
    merged = copy.deepcopy(settings)
    hooks = merged.setdefault("hooks", {})
    for event, groups in list(hooks.items()):
        kept = []
        for group in groups:
            handlers = group.get("hooks", [])
            if any(_is_kblam(h) for h in handlers):
                handlers = [h for h in handlers if not _is_kblam(h)]
                if not handlers:
                    continue
                group = {**group, "hooks": handlers}
            kept.append(group)
        hooks[event] = kept
    for event, groups in installed.items():
        hooks.setdefault(event, []).extend(copy.deepcopy(groups))
    return merged


def _read_settings(path: Path) -> dict | None:
    """The parsed settings file, None if absent. Anything kblam cannot merge into is an InitError."""
    if not path.exists():
        return None
    try:
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise InitError(f"cannot parse {SETTINGS} ({exc}); fix it by hand and run kblam init again. "
                        f"Nothing was written", EXIT_REFUSED) from None
    problem = None
    if not isinstance(settings, dict):
        problem = "it is not a JSON object"
    elif not isinstance(settings.get("hooks", {}), dict):
        problem = '"hooks" is not an object'
    else:
        for event, groups in settings.get("hooks", {}).items():
            if not isinstance(groups, list) or not all(
                    isinstance(g, dict) and isinstance(g.get("hooks", []), list) for g in groups):
                problem = f'"hooks"."{event}" is not a list of matcher groups with a "hooks" list'
                break
    if problem:
        raise InitError(f"cannot merge into {SETTINGS}: {problem}; fix it by hand and run kblam init again. "
                        f"Nothing was written", EXIT_REFUSED)
    return settings


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    except OSError as exc:
        raise InitError(f"cannot run git ({exc.strerror})", EXIT_USAGE) from None


def _repo_root(cwd: Path) -> Path:
    done = _git(cwd, "rev-parse", "--show-toplevel")
    if done.returncode != 0 or not done.stdout.strip():
        raise InitError(f"{cwd} is not inside a git repository (git rev-parse --show-toplevel: "
                        f"{done.stderr.strip() or 'no output'}); run kblam init in the repository", EXIT_USAGE)
    return Path(done.stdout.strip()).resolve()


def _pre_commit_path(repo: Path) -> Path:
    done = _git(repo, "rev-parse", "--git-path", "hooks/pre-commit")
    if done.returncode != 0 or not done.stdout.strip():
        raise InitError(f"git rev-parse --git-path hooks/pre-commit failed: {done.stderr.strip()}", EXIT_REFUSED)
    path = Path(done.stdout.strip())
    return (path if path.is_absolute() else repo / path).resolve()


def _hook_shell() -> tuple[str, list[str]] | None:
    """The shell Claude Code runs a shell-form hook in (Git Bash, else PowerShell; desk-hooks H9) as
    (name, argv prefix). On Windows the WSL launchers (System32, WindowsApps) are not Git Bash."""
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        bash = shutil.which("bash", path=directory) if directory else None
        if bash is None:
            continue
        if sys.platform == "win32":
            lowered = os.path.normcase(directory)
            system = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
            if lowered.startswith(system) or lowered.rstrip("\\").endswith("windowsapps"):
                continue
        return "bash", [bash, "-c"]
    pwsh = shutil.which("pwsh")
    return ("pwsh", [pwsh, "-NoProfile", "-Command"]) if pwsh else None


def _run_hook(argv: list[str], handler: dict, payload: dict, repo: Path, env: dict) -> tuple[bool, str]:
    """(answered, description) for one hook handler run with `payload` on stdin. It answered if it exited 0
    with no output or with JSON other than the `||` branch's did-not-run message."""
    timeout = handler.get("timeout", 60)
    try:
        done = subprocess.run([*argv, handler["command"]], input=json.dumps(payload), capture_output=True,
                              text=True, encoding="utf-8", cwd=repo, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"did not answer within its {timeout} s timeout"
    out = done.stdout.strip()
    if done.returncode != 0:
        err = done.stderr.strip().splitlines()
        return False, f"exit {done.returncode}" + (f": {err[-1]}" if err else "")
    if not out:
        return True, "answered (no output)"
    try:
        answer = json.loads(out)
    except ValueError:
        return False, f"output is not JSON: {out[:200]}"
    message = answer.get("systemMessage", "") if isinstance(answer, dict) else ""
    if "did not run" in message:
        return False, message
    return True, f"answered {json.dumps(answer, ensure_ascii=False)[:300]}"


def _started_tree(cfg) -> tuple[tuple[int, str | None, str] | None, bool]:
    """(treehash.read_recorded as init finds it, whether the tree matches it), read under the lock and
    before init writes anything: an interrupted write is recovered by then, so `started` is the value
    recovery restored rather than the one it replaced (SPEC §5.2.6)."""
    started = treehash.read_recorded(cfg)
    if started is None:
        return None, False
    view = load_view(cfg)
    fmt, root, digest = started
    if fmt == 2 and root == cfg.review_dir:
        return started, treehash.tree_digest_v2(view) == digest
    return started, fmt == 1 and treehash.tree_digest(view) == digest


def _write_tree_hash_v2(cfg, view) -> bool:
    """Write the format-2 tree.hash of `view` when its bytes differ from the file's; True when it wrote."""
    path = cfg.state_dir / "tree.hash"
    line = treehash.format_line(cfg.review_dir, treehash.tree_digest_v2(view)).encode("utf-8")
    if path.is_file() and path.read_bytes() == line:
        return False
    atomic_write(path, line)
    return True


def _validation_clean(cfg, view) -> bool:
    """Whether the tree `view` passes the full validation (SPEC §5.2.6 "Upgrade"): no errors and no open
    review or unchecked item. Warnings and pending tasks are fine; nothing here asks Jev or the network."""
    return not rules.errors(rules.validate(view)) and not review.open_items(cfg, view)


def run(update: bool, cwd: Path | None = None) -> int:
    """`kblam init`: returns the exit status (0 done; 1 something was refused or a hook did not answer;
    2 not in a git repository or an invalid kblam.toml; 3 lock timeout, from kblam index)."""
    try:
        repo = _repo_root(cwd or Path.cwd())
        settings = _read_settings(repo / SETTINGS)
        pre_commit = _pre_commit_path(repo)
        config = repo / CONFIG_NAME
        if config.exists():
            load_config(root=repo)  # an invalid kblam.toml stops init before anything is written
        print(f"kblam init: {repo}")
        state = Init(repo, update)
        template = _asset(CONFIG_NAME)
        if not config.exists():
            _write(config, template)
            state.report("created", CONFIG_NAME, "edit [kb] evidence_roots and scopes for this project")
        elif config.read_bytes() == template:
            state.report("unchanged", CONFIG_NAME)
        else:
            state.report("kept", CONFIG_NAME, "init never overwrites it")
        cfg = load_config(root=repo)
        state.prompt(cfg.jev.get("prompt") if isinstance(cfg.jev, dict) else None,
                     _template_prompt(template))
        # The state of tree.hash as init finds it, read under the lock: taking it recovers an interrupted
        # write first, and recovery restores tree.hash to the journal's value (SPEC §5.2.6). A read before
        # that would let init record over the restored value and report its own undo as its own writes.
        with writes.locked(cfg, "init", mutating=False):
            started, matched = _started_tree(cfg)
        state.findings_index(cfg)
        state.review_index(cfg)
        state.append_lines(".gitattributes",
                           [f"{cfg.findings_dir}/** -text", f"{cfg.review_dir}/** -text"])
        state.append_line(".gitignore", ".kblam/")
        state.owned_file(RULE, _asset("rules/kblam-findings.md", cfg.findings_dir, cfg.review_dir))
        state.owned_file(SKILL, _asset("skills/kblam-write/SKILL.md", cfg.findings_dir, cfg.review_dir))
        installed = _hook_entries(cfg.findings_dir, cfg.review_dir)
        state.settings(settings, installed)
        state.append_line("CLAUDE.md", GUIDANCE_LINE.format(root=cfg.findings_dir), paragraph=True)
        if pre_commit.is_relative_to(repo):
            state.pre_commit(pre_commit)
        else:
            state.report("refused", str(pre_commit), "the git pre-commit hook path is outside the repository "
                                                     "(core.hooksPath, a worktree or a submodule); init writes "
                                                     f"nothing there. Install {ASSETS / 'pre-commit'} by hand")
            state.status = EXIT_REFUSED
        state.tree_hash(cfg, started, matched)
        state.check_hooks(installed, cfg.findings_dir)
    except InitError as exc:
        print(f"kblam init: {exc}", file=sys.stderr)
        return exc.status
    except ConfigError as exc:
        print(f"kblam init: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except LockError as exc:
        print(f"kblam init: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except OSError as exc:
        print(f"kblam init: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except StoreError as exc:
        # writes.locked raises StoreError for a failed recovery, and, in the mutating findings index,
        # review index and tree.hash steps, for a changed review root or an unreadable registry
        print(f"kblam init: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    if state.status == EXIT_OK:
        print("kblam init: done. Review the files above and commit them.")
    else:
        print("kblam init: finished with the problems reported above (exit 1).")
    return state.status
