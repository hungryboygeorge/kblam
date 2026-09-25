"""`.kblam/lock`: one writer at a time for new, edit, put, ack and index (SPEC §12 M2).

The lock is a file created with O_EXCL and holding the holder's pid, command and start time. A
waiter polls until `lock_wait_seconds`, then fails naming the holder. A lock whose holder is not
running, or that is older than `lock_stale_seconds`, is broken; `.kblam/lock.break` (also O_EXCL)
makes sure only one waiter breaks it, and only while it still holds the stale record.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from kblam.config import Config

POLL_SECONDS = 0.05


class LockError(Exception):
    """The lock could not be taken in time; the message names the holder."""


# On Windows, a file that another process is reading or deleting raises PermissionError on open
# and unlink; those cases are treated as "try again".


def _create(path: Path, content: bytes) -> bool:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
    except (FileExistsError, PermissionError):
        return False
    try:
        os.write(fd, content)
    finally:
        os.close(fd)
    return True


def _unlink(path: Path) -> None:
    for _ in range(100):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.01)
    path.unlink(missing_ok=True)  # raises the PermissionError if it persists


def _read(path: Path) -> tuple[bytes, dict, float] | None:
    """(raw bytes, holder record, age in seconds), or None when the file is gone."""
    for _ in range(100):
        try:
            raw = path.read_bytes()
            mtime = path.stat().st_mtime
            break
        except FileNotFoundError:
            return None
        except PermissionError:
            time.sleep(0.01)
    else:
        return None  # still being deleted or rewritten; the caller retries
    try:
        holder = json.loads(raw.decode("utf-8"))
        if not isinstance(holder, dict):
            holder = {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        holder = {}  # being written right now, or damaged; age falls back to the file time
    created = holder.get("created")
    start = created if isinstance(created, (int, float)) else mtime
    return raw, holder, max(0.0, time.time() - start)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        process_query_limited_information, still_active, error_access_denied = 0x1000, 259, 5
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return ctypes.get_last_error() == error_access_denied  # exists, owned by someone else
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # POSIX only: on Windows os.kill terminates the process
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def describe(holder: dict, age: float) -> str:
    pid = holder.get("pid", "unknown")
    command = holder.get("command", "unknown command")
    return f"pid {pid} (kblam {command}), held for {age:.0f}s"


def _stale_reason(cfg: Config, holder: dict, age: float) -> str | None:
    pid = holder.get("pid")
    if isinstance(pid, int) and not pid_alive(pid):
        return "its process is not running"
    if age > cfg.lock_stale_seconds:
        return f"it is older than lock_stale_seconds ({cfg.lock_stale_seconds:g}s)"
    return None


def _break_stale(cfg: Config, path: Path, seen: bytes, message: str) -> bool:
    """Remove the lock if it still holds exactly the stale record `seen`; True if it is gone."""
    guard = path.with_name(path.name + ".break")
    if not _create(guard, str(os.getpid()).encode("ascii")):
        state = _read(guard)
        if state is not None and state[2] > cfg.lock_stale_seconds:
            _unlink(guard)  # a breaker died mid-break
        return False
    try:
        state = _read(path)
        if state is not None and state[0] == seen:
            _unlink(path)
            print(message, file=sys.stderr)
        return True
    finally:
        _unlink(guard)


@contextmanager
def kb_lock(cfg: Config, command: str):
    """Hold `.kblam/lock` for the duration of the block."""
    path = cfg.state_dir / "lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = json.dumps({"pid": os.getpid(), "command": command, "created": time.time(),
                         "token": os.urandom(8).hex()}).encode("utf-8")
    deadline = time.monotonic() + cfg.lock_wait_seconds
    holder, age = {}, 0.0
    while not _create(path, record):
        state = _read(path)
        if state is not None:
            raw, holder, age = state
            reason = _stale_reason(cfg, holder, age)
            if reason and _break_stale(cfg, path, raw, f"kblam {command}: broke stale lock "
                                                       f"{cfg.state_dir.name}/lock held by "
                                                       f"{describe(holder, age)}: {reason}"):
                continue
        if time.monotonic() >= deadline:
            raise LockError(
                f"timed out after {cfg.lock_wait_seconds:g}s waiting for {cfg.state_dir.name}/lock, held by "
                f"{describe(holder, age)}; run the command again once that finishes"
            )
        time.sleep(POLL_SECONDS)
    try:
        yield
    finally:
        state = _read(path)
        if state is not None and state[0] == record:
            _unlink(path)
