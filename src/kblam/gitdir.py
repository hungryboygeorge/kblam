"""What git means for kblam's own state (SPEC §7 recheck, §8, §8.3; M6.10).

A pull writes every tracked file into the work tree, over an ignored file of the same name too, so a
commit that holds files under `.kblam/` replaces this machine's review items, tree.hash and cached
answers with someone else's. Two consequences:

- `tracked_state` lists what git tracks under `.kblam/`, and kblam acts on none of its state while
  anything is (the CLI refuses, the Stop hook blocks).
- What a person approves on this machine (kblam recheck's approvals) is kept in the repository's git
  directory, which `git_common_dir` finds: git refuses any path with a `.git` component, so no commit
  can write there.

`git_common_dir` reads the filesystem only, so the PreToolUse hook can call it on its fast path.
"""

from __future__ import annotations

from pathlib import Path

from kblam.config import STATE_DIR, Config

KBLAM_GIT_DIR = "kblam"  # <git common dir>/kblam/: this clone's approvals (SPEC §7 recheck)
GIT_TIMEOUT = 30         # seconds for `git ls-files`; a slower git counts as unable to answer


def git_common_dir(start: Path) -> Path | None:
    """The git directory that every work tree of the repository holding `start` shares, resolved: the
    `.git` directory at or above `start`, or, where `.git` is a file (a linked work tree, a submodule),
    the directory it names, followed to its `commondir` when it has one. None outside a git work tree.
    Found as git finds it, without running git; GIT_DIR and similar variables are not read."""
    for folder in (start, *start.parents):
        dot_git = folder / ".git"
        try:
            if dot_git.is_dir():
                return dot_git.resolve()
            if not dot_git.is_file():
                continue
            text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
            if not text.startswith("gitdir:"):
                return None
            git_dir = Path(text.removeprefix("gitdir:").strip())
            git_dir = git_dir if git_dir.is_absolute() else folder / git_dir
            common = git_dir / "commondir"
            if common.is_file():
                shared = Path(common.read_text(encoding="utf-8", errors="replace").strip())
                return (shared if shared.is_absolute() else git_dir / shared).resolve()
            return git_dir.resolve()
        except OSError:
            return None
    return None


def kblam_git_dir(cfg: Config) -> Path | None:
    """`<git common dir>/kblam/`, where this clone keeps what a person approved on this machine; None
    outside a git work tree."""
    common = git_common_dir(cfg.repo_root)
    return common / KBLAM_GIT_DIR if common is not None else None


def _ls_files(cfg: Config, root: str) -> list[str]:
    """The files git tracks under `root` (the index lists what a commit would hold), repository-relative.
    [] outside a git work tree and whenever git cannot answer: this guards against someone else's state,
    and must not stop kblam on a machine where git is missing."""
    if git_common_dir(cfg.repo_root) is None:
        return []
    import subprocess  # only here: the hooks' fast path never calls this

    try:
        done = subprocess.run(["git", "--literal-pathspecs", "ls-files", "-z", "--", root],
                              cwd=cfg.repo_root, capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if done.returncode != 0:
        return []
    return [p for p in done.stdout.decode("utf-8", "replace").split("\0") if p]


def tracked_state(cfg: Config) -> list[str]:
    """The paths under `.kblam/` that git tracks or is about to commit (the index lists both), relative
    to the repository root. [] outside a git work tree and whenever git cannot answer: this guards
    against someone else's state, and must not stop kblam on a machine where git is missing."""
    return _ls_files(cfg, STATE_DIR)


def committed_record(cfg: Config, path: str) -> bool:
    """Whether git's last commit holds a copy of a record at the repository-relative `path` that kblam
    reads as the record the file name gives: `git show HEAD:<path>` prints the committed bytes, and
    records.parse_record of them yields a mapping whose `id` equals the ID the file name gives. That is
    the copy a restore puts back, so it is the answer a restore step is built on: a commit that holds a
    file that does not parse, or that names another ID (the damage itself was committed, or a stray file
    was), would come back byte for byte and print the same step again.

    The last commit is read, not the index: a record that is only `git add`ed is not in HEAD, so a
    restore would delete the file rather than put it back, and `git show HEAD:<path>` fails for it (git
    prints the blob itself, so a path outside ASCII needs no quoting trick here). False outside a git
    work tree and whenever git cannot answer: an unborn HEAD (no commit yet) and a missing git both
    count as not holding a copy."""
    if git_common_dir(cfg.repo_root) is None:
        return False
    import subprocess  # only here: the hooks' fast path never calls this

    try:
        done = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=cfg.repo_root, capture_output=True,
                              timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if done.returncode != 0:
        return False
    from kblam import records  # only here: no import of the record tables on the hook's fast path

    rec = records.parse_record(path, done.stdout)
    value = rec.data.get("id") if isinstance(rec.data, dict) else None
    return rec.id is not None and value == rec.id


def tracked_state_problem(tracked: list[str]) -> str:
    """Why kblam will not act while git tracks `tracked` under `.kblam/`, and what fixes it."""
    shown = ", ".join(tracked[:5]) + (f" and {len(tracked) - 5} more" if len(tracked) > 5 else "")
    return (f"git tracks {shown}, under {STATE_DIR}/, which holds this machine's own state (review items, "
            f"tree.hash, cached Jev answers) and is never committed. A pull that brings such files writes them "
            f"over this machine's own, since git replaces ignored files, so kblam acts on none of that state "
            f"while git tracks any of it. Untrack it with git rm -r --cached {STATE_DIR} and commit that. If the "
            f"files came with a pull, what {STATE_DIR}/ holds now is another machine's state: delete {STATE_DIR}/, "
            f"then run kblam validate --record, which accepts the committed findings as a new clone does, and "
            f"kblam audit to have Jev check them")
