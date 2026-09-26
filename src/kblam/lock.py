"""`.kblam/lock`: one writer at a time for new, edit, put, ack, index and kblam's other writers (SPEC §7
"Lock"; M2, with M6.10's heartbeat).

The lock is a file created with O_EXCL and holding the holder's pid, command and start time. While it is
held, a daemon thread refreshes the file's modification time every `lock_stale_seconds`/4 (at least
hourly), for as long as the file still holds this holder's record. A waiter polls until
`lock_wait_seconds`, then fails naming the holder. A lock is broken when its holder's process is not
running, or when its last refresh is older than `lock_stale_seconds`: so a live holder is never broken
however long it holds the lock, and a dead holder whose pid another process now uses is broken once its
refreshes stop. `.kblam/lock.break` (also O_EXCL) makes sure only one waiter breaks it, and only while it
still holds the stale record.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from kblam.config import Config

POLL_SECONDS = 0.05
REFRESHES_PER_STALE_AGE = 4  # SPEC §7 asks for a refresh at least every lock_stale_seconds/3; 4 leaves slack
MAX_REFRESH_SECONDS = 3600.0  # a lock_stale_seconds meant as "never" (even inf) would overflow Event.wait


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


def _read(path: Path) -> tuple[bytes, dict, float, float] | None:
    """(raw bytes, holder record, seconds since the holder took it, seconds since its last refresh), or
    None when the file is gone. The bytes and the modification time come from one open file."""
    for _ in range(100):
        try:
            with open(path, "rb") as handle:
                raw, mtime = handle.read(), os.fstat(handle.fileno()).st_mtime
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
    now = time.time()
    created = holder.get("created")
    start = created if isinstance(created, (int, float)) else mtime
    return raw, holder, max(0.0, now - start), max(0.0, now - mtime)


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


def _stale_reason(cfg: Config, holder: dict, idle: float) -> str | None:
    """Why the lock may be broken, or None while its holder may still be at work. `idle` is the time
    since the last refresh: a running holder refreshes well within lock_stale_seconds, so an older one
    means the holder stopped, even when its pid is alive because another process now has it."""
    pid = holder.get("pid")
    if isinstance(pid, int) and not pid_alive(pid):
        return "its process is not running"
    if idle > cfg.lock_stale_seconds:
        return f"its last refresh is older than lock_stale_seconds ({cfg.lock_stale_seconds:g}s)"
    return None


def _break_stale(cfg: Config, path: Path, seen: bytes, command: str) -> bool:
    """Remove the lock if it still holds exactly the stale record `seen` and is still stale (its holder
    may have refreshed it since the caller looked); True if the caller should try to take it again."""
    guard = path.with_name(path.name + ".break")
    if not _create(guard, str(os.getpid()).encode("ascii")):
        state = _read(guard)
        if state is not None and state[3] > cfg.lock_stale_seconds:
            _unlink(guard)  # a breaker died mid-break
        return False
    try:
        state = _read(path)
        if state is not None and state[0] == seen:
            _raw, holder, age, idle = state
            reason = _stale_reason(cfg, holder, idle)
            if reason:
                _unlink(path)
                print(f"kblam {command}: broke stale lock {cfg.state_dir.name}/lock held by "
                      f"{describe(holder, age)}: {reason}", file=sys.stderr)
        return True
    finally:
        _unlink(guard)


def _refresh(path: Path, record: bytes) -> bool:
    """Set the lock file's modification time to now if the file still holds `record`; False once it does
    not, which means a waiter broke the lock. The time is set through the open file where the platform
    can (POSIX), so a lock file that replaced this one after the read is never touched; on Windows an
    open file cannot be deleted or replaced."""
    try:
        with open(path, "rb") as handle:
            if handle.read() != record:
                return False
            os.utime(handle.fileno() if os.utime in os.supports_fd else path)
    except FileNotFoundError:
        return False
    except OSError:
        pass  # e.g. on Windows another process is deleting it right now; the next beat looks again
    return True


def _heartbeat(path: Path, record: bytes, interval: float, stop: threading.Event) -> None:
    """Refresh the lock every `interval` seconds until `stop` is set or the lock is no longer this holder's."""
    while not stop.wait(interval):
        if not _refresh(path, record):
            return


@contextmanager
def kb_lock(cfg: Config, command: str):
    """Hold `.kblam/lock` for the duration of the block, refreshing it from a daemon thread (SPEC §7)."""
    path = cfg.state_dir / "lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = json.dumps({"pid": os.getpid(), "command": command, "created": time.time(),
                         "token": os.urandom(8).hex()}).encode("utf-8")
    deadline = time.monotonic() + cfg.lock_wait_seconds
    holder, age = {}, 0.0
    while not _create(path, record):
        state = _read(path)
        if state is not None:
            raw, holder, age, idle = state
            if _stale_reason(cfg, holder, idle) and _break_stale(cfg, path, raw, command):
                continue
        if time.monotonic() >= deadline:
            raise LockError(
                f"timed out after {cfg.lock_wait_seconds:g}s waiting for {cfg.state_dir.name}/lock, held by "
                f"{describe(holder, age)}; run the command again once that finishes"
            )
        time.sleep(POLL_SECONDS)
    stop = threading.Event()
    interval = min(cfg.lock_stale_seconds / REFRESHES_PER_STALE_AGE, MAX_REFRESH_SECONDS)
    beat = threading.Thread(target=_heartbeat, name="kblam-lock-heartbeat", daemon=True,
                            args=(path, record, interval, stop))
    try:
        beat.start()
        yield
    finally:
        stop.set()
        if beat.ident is not None:  # started
            beat.join()
        state = _read(path)
        if state is not None and state[0] == record:
            _unlink(path)
