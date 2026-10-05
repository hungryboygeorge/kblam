"""Warnings and owners through every issue consumer (SPEC §5, §5.1.4; M6.10 U2).

A warning prints and nothing else: it never fails `kblam validate`, never refuses a put and never
blocks a Stop. Only errors do, and those are what every consumer counts. K1-K11 issues name the
finding they are about as their owner.

The warning is injected by patching `validate` in each module that calls it: cli and store import it
into their own namespace, so patching `kblam.rules` alone would not reach them; the Stop hook imports
it from `kblam.rules` inside the call, so patching the module covers that path.
"""

from __future__ import annotations

import json

import pytest

from kblam import cli, hook, rules, store
from kblam.rules import Issue

from conftest import finding_text
from test_store import CLAIM_A, fill

FINDING = "findings/calibration/F-0001-motor.md"   # the finding the injected issue is about
MESSAGE = "synthetic: the excerpt no longer covers the assertion"


@pytest.fixture
def inject(monkeypatch, kb):
    """Patch every module that calls `validate`, so each run also sees one issue per given level."""
    real = rules.validate

    def install(*levels: str, code: str = "K13"):
        extras = [Issue(FINDING, 0, code, MESSAGE, level=level, owner="F-0001") for level in levels]

        def patched(view, focus=frozenset()):
            return list(real(view, focus)) + list(extras)

        for module in (cli, store, rules):
            monkeypatch.setattr(module, "validate", patched)
        return kb

    return install


def run_validate(kb) -> int:
    return cli.main(["--root", str(kb.root), "validate"])


def stop_hook(kb) -> int:
    return hook._stop(kb.cfg, "Stop", {"hook_event_name": "Stop", "stop_hook_active": False,
                                       "cwd": str(kb.root)})


def changed_tree(kb) -> None:
    """Rewrite a finding outside kblam, so the Stop hook validates (its digest differs from tree.hash)."""
    kb.write(FINDING, finding_text("F-0001", CLAIM_A, body="More detail, written by a shell command."))


# --- validate -----------------------------------------------------------------------------------


def test_a_warning_prints_and_validate_still_exits_zero(kb, inject, capsys):
    kb.add("F-0001", "motor", CLAIM_A)
    inject("warning")
    capsys.readouterr()

    assert run_validate(kb) == 0
    out = capsys.readouterr().out
    assert f"K13 warning {FINDING}: {MESSAGE}" in out
    assert "error(s)" not in out and "OK (1 findings)" in out


def test_an_error_of_the_same_shape_still_fails_validate(kb, inject, capsys):
    kb.add("F-0001", "motor", CLAIM_A)
    inject("error")
    capsys.readouterr()

    assert run_validate(kb) == 1
    out = capsys.readouterr().out
    assert f"K13 {FINDING}: {MESSAGE}" in out and " warning " not in out
    assert "kblam validate: 1 error(s)" in out


def test_validate_counts_the_errors_beside_a_warning(kb, inject, capsys):
    kb.add("F-0001", "motor", CLAIM_A)
    inject("error", "warning")
    capsys.readouterr()

    assert run_validate(kb) == 1
    out = capsys.readouterr().out
    assert f"K13 {FINDING}: {MESSAGE}" in out and f"K13 warning {FINDING}: {MESSAGE}" in out
    assert "kblam validate: 1 error(s)" in out


# --- put ----------------------------------------------------------------------------------------


def test_a_warning_does_not_refuse_a_put(kb, inject, capsys):
    inject("warning")
    assert cli.main(["--root", str(kb.root), "new", "calibration", "motor warm-up drift"]) == 0
    path = kb.root / capsys.readouterr().out.strip()
    fill(path, CLAIM_A)
    capsys.readouterr()

    assert cli.main(["--root", str(kb.root), "put", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"K13 warning {FINDING}: {MESSAGE}" in out
    assert "rejected" not in out and "is unchanged" not in out
    assert path.name in out and not path.exists()   # installed, not refused
    installed = kb.findings / "calibration" / path.name
    assert installed.is_file() and kb.issues() == []  # the only issue was the injected warning


def test_an_error_of_another_rule_still_refuses_a_put(kb, inject, capsys):
    inject("error", code="K1")   # a K1-K11 error refuses a finding put (SPEC §5.1.4)
    assert cli.main(["--root", str(kb.root), "new", "calibration", "motor warm-up drift"]) == 0
    path = kb.root / capsys.readouterr().out.strip()
    fill(path, CLAIM_A)
    capsys.readouterr()

    assert cli.main(["--root", str(kb.root), "put", str(path)]) == 1
    out = capsys.readouterr().out
    assert f"K1 {FINDING}: {MESSAGE}" in out
    assert "kblam put: rejected F-0001 (1 error(s))" in out and "findings/ is unchanged" in out
    assert path.is_file() and not (kb.findings / "calibration" / path.name).exists()


def test_a_k13_error_the_put_does_not_newly_affect_is_kept_and_does_not_refuse(kb, inject, capsys):
    """K13 refuses a put only for an excerpt the installed finding did not already have affected
    (SPEC §5.1.4), so the finding's other K13 errors are listed, not obeyed."""
    inject("error")
    assert cli.main(["--root", str(kb.root), "new", "calibration", "motor warm-up drift"]) == 0
    path = kb.root / capsys.readouterr().out.strip()
    fill(path, CLAIM_A)

    result = store.put(kb.cfg, path)
    assert result.ok and result.issues == []
    assert [(i.code, i.level) for i in result.kept] == [("K13", "error")]
    assert (kb.findings / "calibration" / path.name).is_file()


def test_put_reports_a_warning_in_result_warnings_not_issues(kb, inject, monkeypatch):
    """The store's split: `issues` refuses, `warnings` is only reported (cli prints both)."""
    kb.add("F-0001", "motor", CLAIM_A)
    path = kb.write(".kblam/staging/F-0002-drift.md",
                    finding_text("F-0002", "The motor warm-up drift settles within 90 seconds of power-on."))
    inject("warning")

    result = store.put(kb.cfg, path)
    assert result.ok and result.issues == []
    assert [i.code for i in result.warnings] == ["K13"] and result.warnings[0].level == "warning"
    assert (kb.findings / "calibration" / "F-0002-drift.md").is_file()


# --- Stop hook ----------------------------------------------------------------------------------


def test_a_warning_leaves_the_stop_hook_silent(kb, inject, capsys):
    kb.add("F-0001", "motor", CLAIM_A)
    changed_tree(kb)
    inject("warning")
    capsys.readouterr()

    assert stop_hook(kb) == 0
    assert capsys.readouterr().out == ""  # no block, no note: exactly as when the tree is clean


def test_an_error_of_the_same_shape_still_blocks_the_stop_hook(kb, inject, capsys):
    kb.add("F-0001", "motor", CLAIM_A)
    changed_tree(kb)
    inject("error")
    capsys.readouterr()

    assert stop_hook(kb) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["decision"] == "block"
    assert f"K13 {FINDING}: {MESSAGE}" in answer["reason"]


# --- owner --------------------------------------------------------------------------------------


def test_k1_to_k11_issues_name_the_finding_they_are_about(kb):
    kb.add("F-0001", "sensor", CLAIM_A, extra="anchors: [0x1A2B3C]\n")                  # K1, a field
    kb.add("F-0002", "drift", "The earlier reading is no longer true.", topic="motor")  # K4
    kb.write("findings/motor/F-0003-broken.md",                                        # K1, unparseable
             finding_text("F-0003", "The motor reaches steady output after 90 seconds.", topic="motor",
                          extra="evidence: [evidence/2026-09-22-ratio/]\n"))
    kb.reindex()

    assert [(i.code, i.owner) for i in kb.issues()] == [("K1", "F-0001"), ("K4", "F-0002"),
                                                        ("K1", "F-0003")]
