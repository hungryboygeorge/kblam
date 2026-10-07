"""`kblam validate --commit`: the checks of the commit being made (SPEC §8 item 4, "What is validated").

The git pre-commit hook runs `kblam --root <repo> validate --commit`. Besides the K rules, the open items
and a person's approval of kblam.toml (approval.py), it checks the commit itself:

- No file under the KB root may have unstaged changes or be untracked: kblam validates the files on disk,
  so the commit must hold them as they are.
- Open review and unchecked items block only a commit that changes a file under the KB root or
  kblam.resolutions.jsonl; other commits, code-only ones included, are not held up by them.
- Committed evidence is immutable (P3): a commit may add files under an [kb] evidence_roots or history_dirs
  folder, but not modify, delete, move away or change the type of a file the last commit holds there.
- An evidence path or verbatim source that git does not track gets a warning: its finding passes K2 or
  K10 here and fails it on every clone. A path inside a nested Git repository under an evidence root is
  that repository's, and is exempt; for one inside a nested repository elsewhere, git add would stage
  nothing, so the warning names [kb] evidence_roots instead.
- The kblam.resolutions.jsonl the commit holds must parse.

git runs from the repository root with the hook's environment, so it sees the index the commit is made
from: for `git commit -a` or `git commit <paths>`, git hands the hook a temporary index in GIT_INDEX_FILE.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import subprocess
from dataclasses import dataclass, field

from kblam.approval import ApprovalError
from kblam.config import CONFIG_NAME, RESOLUTIONS_NAME, Config
from kblam.rules import VERBATIM_RE
from kblam.view import KBView

RESOLUTION_KEYS = ("sides", "kind", "reason", "date")  # every line of kblam.resolutions.jsonl has them (§6.4)
UNSTAGED = {"M": "modified", "D": "deleted", "T": "type changed", "A": "added", "R": "renamed", "C": "copied"}
EVIDENCE_CHANGES = {"M": "modified", "D": "deleted", "T": "type changed"}  # what a commit may not do to evidence


@dataclass
class CommitCheck:
    """What `validate --commit` found about the commit being made."""
    changes_kb: bool  # it changes a file under the KB root or kblam.resolutions.jsonl, so open items block it
    problems: list[str] = field(default_factory=list)  # each one refuses the commit
    warnings: list[str] = field(default_factory=list)  # printed; they do not refuse it


def check_commit(cfg: Config, view: KBView) -> CommitCheck:
    """The checks above for the commit git is making in the repository at cfg.repo_root; `view` is the tree
    kblam validates. Raises ApprovalError when git cannot answer."""
    prefix = _git(cfg, "rev-parse", "--show-prefix").decode("utf-8", "replace").strip()
    result = CommitCheck(changes_kb=_changes_kb(cfg))
    for problem in (_unstaged(cfg, prefix), _unstaged(cfg, prefix, cfg.review_dir),
                    _changed_evidence(cfg), _resolutions_problem(cfg)):
        if problem:
            result.problems.append(problem)
    result.warnings = _untracked_citations(cfg, view)
    return result


# --- git ------------------------------------------------------------------------------------------


def _run(cfg: Config, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=cfg.repo_root, capture_output=True)
    except OSError as exc:
        raise ApprovalError(f"cannot run git ({exc.strerror})") from None


def _git(cfg: Config, *args: str) -> bytes:
    """git's standard output; ApprovalError if it fails."""
    done = _run(cfg, *args)
    if done.returncode != 0:
        name = next((a for a in args if not a.startswith("-")), "")
        lines = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ApprovalError(f"git {name} failed ({lines[-1] if lines else f'exit {done.returncode}'}), so the "
                            f"commit cannot be checked; kblam validate --commit is what the git pre-commit hook "
                            f"runs, in a git work tree")
    return done.stdout


def _paths(out: bytes) -> list[str]:
    """The paths of NUL-separated (-z) output, which git prints without quoting."""
    return [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]


def _under(path: str, folders: list[str]) -> bool:
    return any(path == folder or path.startswith(folder + "/") for folder in folders)


# --- the checks -------------------------------------------------------------------------------------


def _changes_kb(cfg: Config) -> bool:
    """Whether the commit changes a file under the KB root or kblam.resolutions.jsonl (`git diff --cached
    --name-only`, with both sides of a rename listed): only such a commit is blocked by open items."""
    paths = _paths(_git(cfg, "diff", "--cached", "--name-only", "--no-renames", "--relative", "-z"))
    return any(p == RESOLUTIONS_NAME or _under(p, [cfg.findings_dir, cfg.review_dir]) for p in paths)


def _unstaged(cfg: Config, prefix: str, root: str | None = None) -> str | None:
    """The files under the KB root that the commit does not hold as they are on disk: `git status
    --porcelain` shows them with a worktree column other than a space, untracked ones (`??`) included.
    Its paths are relative to the top of the work tree, which is `prefix` above the repository root."""
    root = cfg.findings_dir if root is None else root
    fields = _git(cfg, "--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all", "--",
                  root).split(b"\0")
    found = []
    i = 0
    while i < len(fields):
        entry = fields[i].decode("utf-8", "replace")
        i += 1
        if len(entry) < 4:
            continue
        x, y, path = entry[0], entry[1], entry[3:]
        if x in "RC" or y in "RC":
            i += 1  # the path it was renamed or copied from follows
        if y == " ":
            continue
        if x + y == "??":
            state = "untracked"
        elif "U" in x + y or x + y in ("AA", "DD"):
            state = "unmerged"
        else:
            state = f"{UNSTAGED.get(y, 'changed')}, not staged"
        found.append(f"{path.removeprefix(prefix)} ({state})")
    if not found:
        return None
    return (f"this commit does not hold the files under {root}/ as they are on disk: "
            f"{', '.join(found)}. kblam validates the files on disk, so the commit must hold them as they are: "
            f"stage them (git add {root}/) or discard those changes, then commit again")


def _evidence_folders(cfg: Config, *, history: bool = True) -> list[str]:
    """[kb] evidence_roots and (unless `history` is False) history_dirs as repository-relative POSIX folders
    ("./x/" is "x")."""
    folders = []
    for raw in (*cfg.evidence_roots, *(cfg.history_dirs if history else ())):
        folder = posixpath.normpath(raw.replace("\\", "/")) if raw else "."
        if folder not in (".", "..") and not folder.startswith(("../", "/")):
            folders.append(folder)
    return folders


def _changed_evidence(cfg: Config) -> str | None:
    """Files under an evidence_roots or history_dirs folder that the last commit holds and this commit
    modifies, deletes, moves away or changes the type of (`git diff --cached --name-status -M HEAD`).
    Adding files there is allowed, and with no commit yet there is nothing to change."""
    folders = _evidence_folders(cfg)
    if not folders or _run(cfg, "rev-parse", "--verify", "--quiet", "HEAD^{commit}").returncode != 0:
        return None
    fields = _git(cfg, "diff", "--cached", "--name-status", "--no-color", "-M", "--relative", "-z",
                  "HEAD").decode("utf-8", "replace").split("\0")
    changes = []
    i = 0
    while i + 1 < len(fields) and fields[i]:
        status, old = fields[i][:1], fields[i + 1]
        new = fields[i + 2] if status in "RC" and i + 2 < len(fields) else None
        i += 3 if new is not None else 2
        if not _under(old, folders):
            continue
        if status in EVIDENCE_CHANGES:
            changes.append(f"{old} ({EVIDENCE_CHANGES[status]})")
        elif status == "R":
            changes.append(f"{old} (moved to {new})")
    if not changes:
        return None
    return (f"this commit changes evidence the last commit holds: {', '.join(changes)}. Evidence is immutable "
            f"(P3): findings cite and quote it as committed, and reported findings quote the retired documents "
            f"(K11). Restore it (git restore --source=HEAD --staged --worktree -- <path>) and add a new evidence "
            f"package instead. A person who must change committed evidence commits with git commit --no-verify, "
            f"knowing that the commit is then not validated")


def _resolutions_problem(cfg: Config) -> str | None:
    """Why the kblam.resolutions.jsonl the commit holds does not parse: every line that is not blank must be
    a JSON object with the keys sides, kind, reason and date (SPEC §6.4). None when it parses, or when the
    index holds no such file."""
    done = _run(cfg, "cat-file", "blob", f":./{RESOLUTIONS_NAME}")  # the staged bytes (cat-file: no textconv)
    if done.returncode != 0:
        return None
    try:
        lines = done.stdout.decode("utf-8").split("\n")
    except UnicodeDecodeError:
        what = "it is not UTF-8 text"
    else:
        bad = [str(number) for number, line in enumerate(lines, 1) if line.strip() and not _resolution(line)]
        if not bad:
            return None
        shown = (f"{', '.join(bad[:10])} and {len(bad) - 10} more" if len(bad) > 10
                 else f"{', '.join(bad[:-1])} and {bad[-1]}" if len(bad) > 1 else bad[0])
        keys = f"{', '.join(RESOLUTION_KEYS[:-1])} and {RESOLUTION_KEYS[-1]}"
        what = (f"line {shown} is not a JSON object with the keys {keys}" if len(bad) == 1 else
                f"lines {shown} are not JSON objects with the keys {keys}")
    return (f"the {RESOLUTIONS_NAME} this commit holds does not parse: {what}. Only kblam resolve writes it, one "
            f"resolution a line: put back the version kblam wrote (git restore --source=HEAD --staged --worktree -- "
            f"{RESOLUTIONS_NAME} restores the last commit's), have the adjudicator resolve again any item that "
            f"loses its resolution, and commit again")


def _resolution(line: str) -> bool:
    try:
        record = json.loads(line)
    except ValueError:
        return False
    return isinstance(record, dict) and all(key in record for key in RESOLUTION_KEYS)


def _relative(raw: str) -> str | None:
    """A cited path as a lexically normalised repository-relative POSIX path; None for one K2 or K10
    rejects anyway (absolute, or outside the repository)."""
    posix = raw.replace("\\", "/")
    if not posix or posix.startswith("/") or re.match(r"^[A-Za-z]:", posix):
        return None
    rel = posixpath.normpath(posix)
    return None if rel in (".", "..") or rel.startswith("../") else rel


def _untracked_citations(cfg: Config, view: KBView) -> list[str]:
    """A warning for each path a finding cites, as evidence (K2) or as a verbatim source (K10), that exists
    here but that git does not track, so that `git ls-files -- <path>` would print nothing: the finding
    passes the rule here and fails it on every clone. One `git ls-files` lists the tracked files under the
    cited paths' top folders. A path inside a nested Git repository under an evidence root is not warned
    about: that repository holds it, not this one (_in_nested_repository)."""
    cited: dict[str, tuple[list[str], set[str]]] = {}  # path -> (IDs of the findings citing it, rule codes)
    for f in view.findings:
        if not isinstance(f.meta, dict):
            continue
        evidence = f.meta.get("evidence")
        refs = [(p, "K2") for p in evidence if isinstance(p, str)] if isinstance(evidence, list) else []
        refs += [(m.group("path").strip(), "K10") for m in map(VERBATIM_RE.match, f.body_lines) if m]
        for raw, rule in refs:
            rel = _relative(raw)
            if rel is None or not (cfg.repo_root / rel).exists():
                continue  # K2 or K10 fails here already
            ids, rules = cited.setdefault(rel, ([], set()))
            if f.file_id not in ids:
                ids.append(f.file_id)
            rules.add(rule)
    if not cited:
        return []
    tops = sorted({rel.split("/", 1)[0] for rel in cited})
    tracked: set[str] = set()
    for path in _paths(_git(cfg, "--literal-pathspecs", "ls-files", "-z", "--", *tops)):
        parts = path.split("/")
        tracked.update("/".join(parts[:n]) for n in range(1, len(parts) + 1))  # the file and its folders
    roots = _evidence_folders(cfg, history=False)
    warnings = []
    for rel, (ids, rules) in cited.items():
        if rel in tracked or _in_nested_repository(cfg, rel, roots):
            continue
        one = len(ids) == 1
        codes = " and ".join(sorted(rules, key=lambda code: int(code[1:])))
        what = {"K2": "as evidence", "K10": "as a verbatim source"}.get(codes, "as evidence and a verbatim source")
        warning = (f"{rel} is not tracked by git, so {', '.join(ids)}, which cite{'s' if one else ''} it "
                   f"{what}, pass{'es' if one else ''} {codes} here and fail{'s' if one else ''} on every clone. ")
        nested = _nested_repository(cfg, rel)
        if nested is None:
            warnings.append(warning + f"Commit it (git add {rel}), or cite a file that is committed")
            continue
        # git add stages nothing inside another repository's work tree, so that advice would not work here
        warnings.append(warning + f"It is inside the git repository {nested}/, whose files this repository does "
                        f"not track, so git add cannot commit it. If {nested}/ is a source repository, it belongs "
                        f"in [kb] evidence_roots in {CONFIG_NAME}, which only a person changes: ask the user to add "
                        f"\"{nested}\" there and to approve the change with kblam approve-config before "
                        f"committing. Otherwise cite a file that is committed")
    return warnings


def _nested_repository(cfg: Config, rel: str) -> str | None:
    """The outermost folder from the repository root down to the cited path `rel` itself that holds its own
    `.git` (a nested Git repository, worktree or submodule), or None. The repository's own `.git` is not
    looked at."""
    parts = rel.split("/")
    for depth in range(1, len(parts) + 1):
        if (cfg.repo_root.joinpath(*parts[:depth]) / ".git").exists():
            return "/".join(parts[:depth])
    return None


def _in_nested_repository(cfg: Config, rel: str, roots: list[str]) -> bool:
    """Whether the cited path `rel` lies inside a nested Git repository under one of the evidence roots
    `roots` (SPEC §8 item 4): a folder from the evidence root down to `rel` itself that holds its own
    `.git`, a directory or the file a worktree or submodule has. Nothing above the evidence root is looked
    at, so the outer repository's own `.git` never counts. The root and the path are compared with
    os.path.normcase, so on Windows a root spelled in another case still matches."""
    parts = rel.split("/")
    folded = os.path.normcase(rel)
    for root in roots:
        prefix = os.path.normcase(root)
        if folded != prefix and not folded.startswith(prefix + os.sep):
            continue
        for depth in range(len(root.split("/")), len(parts) + 1):
            if (cfg.repo_root.joinpath(*parts[:depth]) / ".git").exists():
                return True
    return False
