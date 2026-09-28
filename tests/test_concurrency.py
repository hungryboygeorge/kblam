"""Several writers: put's edit-base guard and the `.kblam/lock` around new/edit/put/ack/index."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from kblam import store
from kblam.lock import LockError, kb_lock
from kblam.store import StoreError, ack, edit_base_path, edit_finding, new_finding, put

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, finding_text

CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."


def replace_in(path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8", newline="\n")


def hold_lock(kb, *, pid: int, age: float = 0.0, command: str = "put F-0009-held.md"):
    record = {"pid": pid, "command": command, "created": time.time() - age}
    return kb.write(".kblam/lock", json.dumps(record))


def set_lock_config(kb, **values) -> None:
    # the lock keys are [kb]'s, so they go before the fixtures' [jev] section
    kb.write("kblam.toml",
             KBLAM_TOML + "".join(f"{k} = {v}\n" for k, v in values.items()) + NO_EMBEDDINGS + PROMPT_TOML)


def dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


# --- edit-base guard --------------------------------------------------------------------------


def test_put_over_existing_id_without_edit_is_refused(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    before = kb.snapshot()
    staged = kb.write(".kblam/staging/F-0001-other.md", finding_text("F-0001", CLAIM_B))
    with pytest.raises(StoreError, match="was not staged by kblam edit"):
        put(kb.cfg, staged)
    assert kb.snapshot() == before


def test_edit_records_base_and_put_clears_it(kb):
    old = kb.add("F-0001", "sensor", CLAIM_A)
    staged = edit_finding(kb.cfg, "F-0001")
    record = json.loads(edit_base_path(kb.cfg, "F-0001").read_text(encoding="utf-8"))
    assert record["path"] == "findings/calibration/F-0001-sensor.md"
    assert len(record["sha256"]) == 64
    replace_in(staged, "about 0.1%", "within 0.2%")
    assert put(kb.cfg, staged).ok
    assert not edit_base_path(kb.cfg, "F-0001").exists()
    assert "within 0.2%" in old.read_text(encoding="utf-8")


def test_put_refused_when_finding_changed_since_edit(kb):
    old = kb.add("F-0001", "sensor", CLAIM_A)
    staged = edit_finding(kb.cfg, "F-0001")
    replace_in(old, "about 0.1%", "near 0.1%")  # someone else's change lands first
    kb.reindex()
    before = kb.snapshot()
    replace_in(staged, "about 0.1%", "within 0.2%")
    with pytest.raises(StoreError, match="F-0001 changed since your edit"):
        put(kb.cfg, staged)
    assert kb.snapshot() == before


def test_ack_refuses_an_edit_of_the_dependent_begun_before_it(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    assert put(kb.cfg, kb.write(".kblam/staging/F-0002-motor.md", finding_text(
        "F-0002", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: null\n"))).ok
    staged_dependent = edit_finding(kb.cfg, "F-0002")

    staged_target = edit_finding(kb.cfg, "F-0001")
    replace_in(staged_target, "about 0.1%", "within 0.2%")
    assert put(kb.cfg, staged_target).suspect == ["F-0002"]
    assert ack(kb.cfg, "F-0002", "F-0001").changed

    replace_in(staged_dependent, "90 seconds", "95 seconds")
    before = kb.snapshot()
    with pytest.raises(StoreError, match="F-0002 changed since your edit"):
        put(kb.cfg, staged_dependent)
    assert kb.snapshot() == before

    staged_dependent.unlink()  # as the message says: set the copy aside, edit again, reapply
    again = edit_finding(kb.cfg, "F-0002")
    replace_in(again, "90 seconds", "95 seconds")
    assert put(kb.cfg, again).ok
    assert kb.issues() == []


# --- lock -------------------------------------------------------------------------------------


def test_threads_racing_new_get_distinct_ids(kb, monkeypatch):
    real = store.allocate_id

    def slow_allocate(cfg):
        finding_id = real(cfg)
        time.sleep(0.05)  # widen the window between choosing an ID and writing the file
        return finding_id

    monkeypatch.setattr(store, "allocate_id", slow_allocate)
    barrier = threading.Barrier(6)
    paths, errors = [], []

    def worker(i):
        barrier.wait()
        try:
            paths.append(new_finding(kb.cfg, "motor", f"Motor note {i}"))
        except Exception as exc:  # reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(p.name[:6] for p in paths) == [f"F-000{n}" for n in range(1, 7)]


def test_processes_racing_new_get_distinct_ids(kb):
    code = "import sys; from kblam.cli import main; sys.exit(main(sys.argv[1:]))"
    processes = [
        subprocess.Popen([sys.executable, "-c", code, "--root", str(kb.root), "new", "motor", f"Motor note {i}"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(5)
    ]
    outputs = [p.communicate(timeout=60) for p in processes]
    assert [p.returncode for p in processes] == [0] * 5, outputs
    names = sorted(os.path.basename(out.strip())[:6] for out, _err in outputs)
    assert names == [f"F-000{n}" for n in range(1, 6)]


def test_put_waits_for_a_held_lock(kb):
    staged = kb.write(".kblam/staging/F-0001-sensor.md", finding_text("F-0001", CLAIM_A))
    held = threading.Event()

    def holder():
        with kb_lock(kb.cfg, "index"):
            held.set()
            time.sleep(0.4)

    thread = threading.Thread(target=holder)
    thread.start()
    held.wait()
    start = time.monotonic()
    assert put(kb.cfg, staged).ok
    assert time.monotonic() - start >= 0.3
    thread.join()


def test_put_times_out_on_a_held_lock_and_names_the_holder(kb):
    set_lock_config(kb, lock_wait_seconds=0.3)
    staged = kb.write(".kblam/staging/F-0001-sensor.md", finding_text("F-0001", CLAIM_A))
    lock = hold_lock(kb, pid=os.getpid())
    before = kb.snapshot()
    start = time.monotonic()
    with pytest.raises(LockError) as caught:
        put(kb.cfg, staged)
    assert time.monotonic() - start >= 0.3
    message = str(caught.value)
    assert message.startswith("timed out after 0.3s waiting for .kblam/lock, held by ")
    assert f"pid {os.getpid()} (kblam put F-0009-held.md), held for " in message
    assert kb.snapshot() == before
    assert lock.is_file() and staged.is_file()


def test_cli_reports_lock_timeout(kb, capsys):
    from kblam.cli import main

    set_lock_config(kb, lock_wait_seconds=0)
    hold_lock(kb, pid=os.getpid())
    assert main(["--root", str(kb.root), "new", "motor", "Motor warm-up"]) == 3
    assert "kblam new: timed out after 0s waiting for .kblam/lock" in capsys.readouterr().err


def test_lock_held_by_a_dead_process_is_broken(kb, capsys):
    set_lock_config(kb, lock_wait_seconds=0.3)
    pid = dead_pid()
    hold_lock(kb, pid=pid)
    path = new_finding(kb.cfg, "motor", "Motor warm-up")
    assert path.name.startswith("F-0001-")
    err = capsys.readouterr().err
    assert f"kblam new motor: broke stale lock .kblam/lock held by pid {pid}" in err
    assert "its process is not running" in err
    assert not (kb.root / ".kblam" / "lock").exists()


def test_lock_older_than_stale_age_is_broken(kb, capsys):
    set_lock_config(kb, lock_wait_seconds=0.3, lock_stale_seconds=60)
    lock = hold_lock(kb, pid=os.getpid(), age=120)
    os.utime(lock, (time.time() - 120,) * 2)  # and not refreshed since: staleness is the last refresh's age
    kb.reindex()
    assert "older than lock_stale_seconds (60s)" in capsys.readouterr().err
