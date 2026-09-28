"""SPEC §7 "Lock" (M6.10): while `.kblam/lock` is held, a daemon thread refreshes the lock file's
modification time, and a lock is broken only when its holder's process is not running or its last
refresh is older than `lock_stale_seconds`. The stale age here is half a second, so each test takes
at most about a second."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from kblam import lock
from kblam.lock import LockError, kb_lock

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML

STALE = 0.5  # lock_stale_seconds: the heartbeat refreshes every STALE/4


def set_lock_config(kb, **values) -> None:
    # the lock keys are [kb]'s, so they go before the fixtures' [jev] section
    kb.write("kblam.toml",
             KBLAM_TOML + "".join(f"{k} = {v}\n" for k, v in values.items()) + NO_EMBEDDINGS + PROMPT_TOML)


def dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def write_lock(kb, *, pid: int, held_for: float, refreshed_ago: float):
    """A lock file another holder wrote: taken `held_for` seconds ago and last refreshed `refreshed_ago`."""
    now = time.time()
    path = kb.write(".kblam/lock", json.dumps({"pid": pid, "command": "put F-0009-held.md",
                                               "created": now - held_for, "token": "0123456789abcdef"}))
    os.utime(path, (now - refreshed_ago,) * 2)
    return path


def lock_path(kb):
    return kb.root / ".kblam" / "lock"


def test_a_holder_that_holds_longer_than_the_stale_age_is_not_broken(kb, capsys):
    set_lock_config(kb, lock_wait_seconds=10, lock_stale_seconds=STALE)
    cfg = kb.cfg
    held, events = threading.Event(), []

    def holder():
        with kb_lock(cfg, "index"):
            held.set()
            time.sleep(2.4 * STALE)  # well past the stale age, without releasing
            events.append("holder done")

    thread = threading.Thread(target=holder)
    thread.start()
    held.wait()
    with kb_lock(cfg, "new motor"):  # this process's pid is alive, so only the refresh time can break it
        events.append("waiter in")
    thread.join()
    assert events == ["holder done", "waiter in"]
    assert "broke stale lock" not in capsys.readouterr().err


def test_a_live_pid_does_not_keep_an_unrefreshed_lock(kb, capsys):
    """A lock whose pid is alive (here, this process's) but that nobody refreshes, as when the holder died
    and another process was given its pid: broken once its last refresh is older than the stale age, and
    not before, however long ago it was taken."""
    set_lock_config(kb, lock_wait_seconds=0.2, lock_stale_seconds=STALE)
    write_lock(kb, pid=os.getpid(), held_for=60, refreshed_ago=0)
    with pytest.raises(LockError, match=r"held by pid \d+ \(kblam put F-0009-held.md\), held for 60s"):
        with kb_lock(kb.cfg, "new motor"):
            pass
    assert "broke stale lock" not in capsys.readouterr().err

    write_lock(kb, pid=os.getpid(), held_for=60, refreshed_ago=2 * STALE)
    with kb_lock(kb.cfg, "new motor"):
        pass
    assert (f"kblam new motor: broke stale lock .kblam/lock held by pid {os.getpid()} (kblam put "
            f"F-0009-held.md), held for 60s: its last refresh is older than lock_stale_seconds (0.5s)"
            in capsys.readouterr().err)
    assert not lock_path(kb).exists()


def test_a_dead_holder_is_broken_however_recently_refreshed(kb, capsys):
    set_lock_config(kb, lock_wait_seconds=0.2, lock_stale_seconds=STALE)
    pid = dead_pid()
    write_lock(kb, pid=pid, held_for=0, refreshed_ago=0)
    with kb_lock(kb.cfg, "new motor"):
        pass
    err = capsys.readouterr().err
    assert f"kblam new motor: broke stale lock .kblam/lock held by pid {pid}" in err
    assert "its process is not running" in err


def test_the_heartbeat_refreshes_the_lock_and_stops_at_release(kb):
    set_lock_config(kb, lock_stale_seconds=STALE)
    path = lock_path(kb)
    before = set(threading.enumerate())
    with kb_lock(kb.cfg, "index"):
        [beat] = [t for t in threading.enumerate() if t not in before]
        assert beat.daemon and beat.is_alive()
        long_ago = time.time() - 60
        os.utime(path, (long_ago, long_ago))
        time.sleep(STALE / 2)  # two beats
        assert path.stat().st_mtime > long_ago + 50
    assert not beat.is_alive()
    assert not path.exists()


def test_the_heartbeat_stops_when_the_block_raises(kb):
    set_lock_config(kb, lock_stale_seconds=STALE)
    before = set(threading.enumerate())
    with pytest.raises(RuntimeError, match="the write failed"):
        with kb_lock(kb.cfg, "index"):
            [beat] = [t for t in threading.enumerate() if t not in before]
            raise RuntimeError("the write failed")
    assert not beat.is_alive()
    assert not lock_path(kb).exists()


def test_a_stale_age_meant_as_never_does_not_break_the_heartbeat(kb, monkeypatch):
    set_lock_config(kb, lock_stale_seconds="inf")  # TOML's infinity: the lock is never broken for its age
    failures = []
    monkeypatch.setattr(threading, "excepthook", failures.append)
    with kb_lock(kb.cfg, "index"):
        time.sleep(0.05)  # the heartbeat is waiting for its first beat
    assert failures == []
    assert not lock_path(kb).exists()


def test_the_heartbeat_never_refreshes_a_lock_that_is_not_its_own(kb):
    """A waiter broke this holder's lock and another holder took it: the heartbeat leaves the new lock
    alone and stops, and the release does not remove it."""
    set_lock_config(kb, lock_stale_seconds=STALE)
    before = set(threading.enumerate())
    with kb_lock(kb.cfg, "index"):
        [beat] = [t for t in threading.enumerate() if t not in before]
        lock._unlink(lock_path(kb))  # retries, as a breaker does: on Windows a beat may be reading it
        other = write_lock(kb, pid=os.getpid(), held_for=60, refreshed_ago=60)
        mtime = other.stat().st_mtime
        time.sleep(STALE / 2)  # two beats
        assert other.stat().st_mtime == mtime
        assert not beat.is_alive()
    assert json.loads(other.read_text(encoding="utf-8"))["token"] == "0123456789abcdef"
