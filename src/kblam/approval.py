"""A person's approval of kblam.toml before a commit changes it (SPEC §8 item 4).

kblam.toml sets every rule kblam enforces and, within §9's limits, where requests go. The PreToolUse
hook denies agent writes to it, but only the writes it can parse: a `git checkout` of an older version
or a script gets past it. So the pre-commit hook runs `kblam validate --commit`, which refuses a commit
whose kblam.toml differs from the last commit's unless a person approved that exact version on this
machine with `kblam approve-config`. That command asks on an interactive terminal, which an agent's
shell is not, and the approvals live in `.kblam/`, which the hooks protect. Like the rest of the hooks
this stops an agent taking a shortcut, not one set on evading it: `git commit --no-verify` skips every
pre-commit check, and a wrapper such as `script` can give a command a terminal.
"""

from __future__ import annotations

import difflib
import hashlib
import subprocess

from kblam.config import CONFIG_NAME, Config
from kblam.lock import kb_lock

APPROVALS_NAME = "config-approved"  # .kblam/config-approved: the digest of each approved kblam.toml, one a line


class ApprovalError(Exception):
    """git could not answer (not a work tree, git missing); the message says what to do."""


def config_digest(data: bytes) -> str:
    """sha256 of kblam.toml with CRLF line endings normalised, so a working copy that core.autocrlf
    checked out with CRLF compares equal to the LF blob git stores."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _approvals_path(cfg: Config):
    return cfg.state_dir / APPROVALS_NAME


def approvals(cfg: Config) -> list[str]:
    path = _approvals_path(cfg)
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="ascii", errors="replace").splitlines() if line.strip()]


def is_approved(cfg: Config, data: bytes) -> bool:
    return config_digest(data) in approvals(cfg)


def record_approval(cfg: Config, data: bytes) -> bool:
    """Record `data` as approved on this machine; False if it already was (nothing is written then)."""
    from kblam.store import atomic_write  # avoid an import cycle

    digest = config_digest(data)
    with kb_lock(cfg, "approve-config"):
        known = approvals(cfg)
        if digest in known:
            return False
        atomic_write(_approvals_path(cfg), "".join(f"{d}\n" for d in [*known, digest]).encode("ascii"))
    return True


def _git(cfg: Config, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=cfg.repo_root, capture_output=True)
    except OSError as exc:
        raise ApprovalError(f"cannot run git ({exc.strerror})") from None


def _check_work_tree(cfg: Config) -> None:
    done = _git(cfg, "rev-parse", "--is-inside-work-tree")
    if done.returncode != 0 or done.stdout.strip() != b"true":
        raise ApprovalError(f"{cfg.repo_root} is not in a git work tree, so there is no commit to check; "
                            f"kblam validate --commit is what the git pre-commit hook runs")


def committed_config(cfg: Config, rev: str) -> bytes | None:
    """kblam.toml as `rev` holds it ("HEAD" for the last commit, "" for the index, which is what the
    commit being made will hold); None when it has none, or there is no commit yet."""
    _check_work_tree(cfg)
    if rev and _git(cfg, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").returncode != 0:
        return None
    # cat-file, not show: the blob's bytes, with no textconv. `./` makes the path relative to the root.
    done = _git(cfg, "cat-file", "blob", f"{rev}:./{CONFIG_NAME}")
    return done.stdout if done.returncode == 0 else None


def commit_problems(cfg: Config) -> list[str]:
    """Why the commit being made may not go ahead as far as kblam.toml goes; empty when it may."""
    head, staged = committed_config(cfg, "HEAD"), committed_config(cfg, "")
    if head == staged:
        return []
    if staged is None:
        return [f"this commit removes {CONFIG_NAME}, which turns kblam off for this repository. Restore it "
                f"(git checkout HEAD -- {CONFIG_NAME}); only a person decides to remove kblam"]
    if is_approved(cfg, staged):
        return []
    return [f"this commit changes {CONFIG_NAME}, which sets the rules kblam enforces and where kblam sends the "
            f"Jev API key, and no person has approved this version of it on this machine. A person reviews "
            f"the change with kblam approve-config at a terminal, then commits again; an agent asks the user "
            f"to do that"]


def config_diff(base: bytes | None, base_label: str, current: bytes) -> str:
    """A unified diff of kblam.toml from `base` to `current`, for the person approving it."""
    def lines(data: bytes | None) -> list[str]:
        return [] if data is None else data.replace(b"\r\n", b"\n").decode("utf-8", "replace").splitlines(True)

    return "".join(difflib.unified_diff(lines(base), lines(current), fromfile=f"{CONFIG_NAME} ({base_label})",
                                        tofile=f"{CONFIG_NAME} (working tree)"))
