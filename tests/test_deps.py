"""Fingerprints, K3, and the M2 dependency workflow: put stamping, suspect reports, ack, deps."""

from __future__ import annotations

import pytest

from kblam import rules
from kblam.cli import main
from kblam.finding import fingerprint, parse_finding
from kblam.store import StoreError, _yaml_scalar, ack, edit_finding, put, stamp_dependency
from kblam.treehash import current_digest, read_tree_hash
from kblam.view import load_view

from conftest import finding_text

CLAIM_A = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
           "so they are not two analog gains.")
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."
CLAIM_C = "The media tray reports its type through two contact pins read at load time."


def fp_of_text(text: str) -> str:
    return fingerprint(parse_finding("findings/calibration/F-0001-sensor.md", text.encode("utf-8")))


def fp_in_kb(kb, finding_id: str) -> str:
    return fingerprint(next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id))


def stage(kb, finding_id: str, slug: str, claim: str, **kw):
    return kb.write(f".kblam/staging/{finding_id}-{slug}.md", finding_text(finding_id, claim, **kw))


def rewrite_claim(kb, finding_id: str, old: str, new: str):
    staged = edit_finding(kb.cfg, finding_id)
    staged.write_text(staged.read_text(encoding="utf-8").replace(old, new), encoding="utf-8", newline="\n")
    return put(kb.cfg, staged)


def kb_with_dependent(kb):
    """F-0001 in calibration, and F-0002 (motor) stamped against it through put."""
    kb.add("F-0001", "sensor", CLAIM_A)
    result = put(kb.cfg, stage(kb, "F-0002", "motor", CLAIM_B, topic="motor",
                               extra="depends_on:\n  F-0001: null\n"))
    assert result.ok, [i.format(result.view) for i in result.issues]
    return kb.findings / "motor" / "F-0002-motor.md"


# --- fingerprint ------------------------------------------------------------------------------

QUANTITY = "quantities:\n  - {name: curve ratio, value: 1.0017, unit: ratio}\n"


def test_fingerprint_ignores_reflow_title_body_and_depends_on():
    base = fp_of_text(finding_text("F-0001", CLAIM_A, extra=QUANTITY))
    reflowed = CLAIM_A.replace(" agree to ", "\nagree  to ").replace(" so they", "\n   so\tthey")
    same = [
        finding_text("F-0001", reflowed, extra=QUANTITY),
        finding_text("F-0001", CLAIM_A, extra=QUANTITY, title="Another title entirely"),
        finding_text("F-0001", CLAIM_A, extra=QUANTITY, body="New supporting detail paragraph."),
        finding_text("F-0001", CLAIM_A, extra=QUANTITY + "depends_on:\n  F-0002: abcdef12\n"),
        finding_text("F-0001", CLAIM_A, extra=QUANTITY, label="inferred"),
    ]
    assert [fp_of_text(t) for t in same] == [base] * len(same)
    assert len(base) == 8 and int(base, 16) >= 0


def test_fingerprint_changes_on_claim_scope_quantity_or_evidence():
    base = fp_of_text(finding_text("F-0001", CLAIM_A, extra=QUANTITY))
    changed = [
        finding_text("F-0001", CLAIM_A.replace("about", "roughly"), extra=QUANTITY),
        finding_text("F-0001", CLAIM_A, extra=QUANTITY, scope="[MX-100]"),
        finding_text("F-0001", CLAIM_A, extra=QUANTITY.replace("1.0017", "1.0018")),
        finding_text("F-0001", CLAIM_A, extra=QUANTITY, evidence="[evidence/2026-09-22-ratio/log.txt]"),
        finding_text("F-0001", CLAIM_A, extra=QUANTITY,
                     evidence="[evidence/2026-09-22-ratio/, evidence/2026-09-22-ratio/log.txt]"),
    ]
    prints = [fp_of_text(t) for t in changed]
    assert base not in prints
    assert len(set(prints)) == len(prints)


# --- K2 self-dependency / K3 ------------------------------------------------------------------


def test_k2_rejects_self_dependency(kb):
    kb.add("F-0001", "sensor", CLAIM_A, extra="depends_on:\n  F-0001: null\n")
    issues = rules.k2_references(load_view(kb.cfg))
    assert len(issues) == 1 and "cannot depend on itself" in issues[0].message
    assert rules.k3_suspect(load_view(kb.cfg)) == []  # K2 owns it


def test_k3_passes_when_fingerprint_is_current(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra=f"depends_on:\n  F-0001: {fp_in_kb(kb, 'F-0001')}\n")
    assert rules.k3_suspect(load_view(kb.cfg)) == []
    assert kb.issues() == []


def test_k3_rejects_suspect_and_unstamped(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.add("F-0002", "motor", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: deadbeef\n")
    kb.add("F-0003", "tray", CLAIM_C, topic="tray", extra="depends_on:\n  F-0001: null\n")
    issues = rules.k3_suspect(load_view(kb.cfg))
    assert [(i.path.split("/")[-1], i.line) for i in issues] == [("F-0002-motor.md", 10), ("F-0003-tray.md", 10)]
    current = fp_in_kb(kb, "F-0001")
    assert issues[0].message == (
        f"suspect: F-0001 was rewritten since this finding was checked against it (recorded deadbeef, "
        f"current {current}); re-read F-0001, then run kblam ack F-0002 F-0001, or edit this finding")
    assert "has no fingerprint (unstamped)" in issues[1].message
    assert "kblam ack F-0003 F-0001" in issues[1].message
    assert kb.codes() == ["K3", "K3"]


# --- put --------------------------------------------------------------------------------------


@pytest.mark.parametrize("written", [
    "depends_on:\n  F-0001: null   # read 2026-09-22\n",
    "depends_on: {F-0001: ~}\n",
    "depends_on:\n  F-0001:\n",
])
def test_put_stamps_null_and_changes_nothing_else(kb, written):
    kb.add("F-0001", "sensor", CLAIM_A)
    staged = stage(kb, "F-0002", "motor", CLAIM_B, topic="motor", extra=written, body="Detail.")
    before = staged.read_text(encoding="utf-8")
    result = put(kb.cfg, staged)
    assert result.ok, [i.format(result.view) for i in result.issues]

    current = fp_in_kb(kb, "F-0001")
    assert result.stamped == [("F-0001", current)]
    stamped = written.replace("null", current).replace("~", current)
    if stamped == written:
        stamped = written.replace("F-0001:", f"F-0001: {current}")
    after = (kb.findings / "motor" / "F-0002-motor.md").read_text(encoding="utf-8")
    assert after == before.replace(written, stamped)
    assert kb.issues() == []


def test_stamp_quotes_a_fingerprint_yaml_would_read_as_a_number():
    finding = parse_finding("findings/motor/F-0002-motor.md",
                            finding_text("F-0002", CLAIM_B, extra="depends_on:\n  F-0001: null\n").encode())
    data = stamp_dependency(finding, "F-0001", "00001234", finding.path)
    assert b"  F-0001: '00001234'\n" in data
    assert parse_finding(finding.path, data).meta["depends_on"]["F-0001"] == "00001234"


def test_put_rejects_stale_fingerprint_on_incoming_finding(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    before = kb.snapshot()
    staged = stage(kb, "F-0002", "motor", CLAIM_B, topic="motor", extra="depends_on:\n  F-0001: deadbeef\n")
    staged_bytes = staged.read_bytes()
    result = put(kb.cfg, staged)
    assert [i.code for i in result.issues] == ["K3"]
    assert "set depends_on F-0001 to null in this file and put it again" in result.issues[0].message
    assert result.issues[0].format(result.view).startswith("K3 .kblam/staging/F-0002-motor.md:10: suspect")
    assert kb.snapshot() == before
    assert staged.read_bytes() == staged_bytes


def test_rewrite_target_reports_suspect_then_ack_restores_validity(kb):
    dependent = kb_with_dependent(kb)
    stale = fp_in_kb(kb, "F-0001")
    result = rewrite_claim(kb, "F-0001", "about 0.1%", "within 0.2%")
    assert result.ok, [i.format(result.view) for i in result.issues]
    assert result.suspect == ["F-0002"]
    assert kb.codes() == ["K3"]
    assert "suspect: F-0001 was rewritten" in kb.issues()[0].message

    before = dependent.read_text(encoding="utf-8")
    acked = ack(kb.cfg, "F-0002", "F-0001")
    assert acked.changed and acked.fingerprint == fp_in_kb(kb, "F-0001")
    assert kb.issues() == []
    assert rules.k7_index(load_view(kb.cfg)) == []
    assert read_tree_hash(kb.cfg) == current_digest(kb.cfg)
    after = dependent.read_text(encoding="utf-8")
    # ack rewrote the stale stamp in place and touched nothing else (a fingerprint YAML would read
    # as a number is quoted, so the file's length can change)
    assert after == before.replace(_yaml_scalar(stale), _yaml_scalar(acked.fingerprint))


def test_title_only_rewrite_leaves_dependents_current(kb):
    kb_with_dependent(kb)
    result = rewrite_claim(kb, "F-0001", "title: Title of F-0001", "title: sensor curve types")
    assert result.ok and result.suspect == []
    assert kb.issues() == []


def test_unrelated_put_succeeds_while_another_finding_is_suspect(kb):
    kb_with_dependent(kb)
    assert rewrite_claim(kb, "F-0001", "about 0.1%", "within 0.2%").ok
    result = put(kb.cfg, stage(kb, "F-0003", "tray", CLAIM_C, topic="tray"))
    assert result.ok, [i.format(result.view) for i in result.issues]
    assert (kb.findings / "tray" / "F-0003-tray.md").is_file()
    assert kb.codes() == ["K3"]


def test_dependent_restamps_by_setting_null_in_an_edit(kb):
    kb_with_dependent(kb)
    old = fp_in_kb(kb, "F-0001")
    assert rewrite_claim(kb, "F-0001", "about 0.1%", "within 0.2%").ok
    new = fp_in_kb(kb, "F-0001")

    staged = edit_finding(kb.cfg, "F-0002")
    blocked = put(kb.cfg, staged)  # still carries the old fingerprint
    assert [i.code for i in blocked.issues] == ["K3"]

    staged.write_text(staged.read_text(encoding="utf-8").replace(f"F-0001: {old}", "F-0001: null"),
                      encoding="utf-8", newline="\n")
    result = put(kb.cfg, staged)
    assert result.ok, [i.format(result.view) for i in result.issues]
    assert result.stamped == [("F-0001", new)]
    assert kb.issues() == []


# --- ack --------------------------------------------------------------------------------------


def test_ack_refuses_a_target_the_dependent_does_not_list(kb):
    kb_with_dependent(kb)
    with pytest.raises(StoreError, match="F-0001 does not list F-0002 in depends_on"):
        ack(kb.cfg, "F-0001", "F-0002")
    with pytest.raises(StoreError, match="F-0009 is not in findings/"):
        ack(kb.cfg, "F-0009", "F-0001")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_ack_changes_only_the_fingerprint_value(kb, newline):
    kb.add("F-0001", "sensor", CLAIM_A)
    text = (
        "---\n"
        "id: F-0002\n"
        "title: Motor warm-up    # hand-aligned comment\n"
        "topic: motor\n"
        "label: observed\n"
        "scope: [MX-200]\n"
        "evidence:\n"
        "    - evidence/2026-09-22-ratio/    # indented four\n"
        "depends_on:\n"
        "  # checked against the curve page\n"
        "  F-0001: \"deadbeef\"     # stale\n"
        "verified: 2026-09-22\n"
        "---\n"
        "\n"
        f"**Claim.** {CLAIM_B}\n"
    ).replace("\n", newline)
    path = kb.write("findings/motor/F-0002-motor.md", text.encode("utf-8"))
    kb.reindex()
    index_before = (kb.findings / "INDEX.md").read_bytes()

    result = ack(kb.cfg, "F-0002", "F-0001")
    current = fp_in_kb(kb, "F-0001")
    assert result.changed and result.path == "findings/motor/F-0002-motor.md"
    assert path.read_bytes() == text.replace('"deadbeef"', current).encode("utf-8")
    assert (kb.findings / "INDEX.md").read_bytes() == index_before
    assert kb.issues() == []

    again = ack(kb.cfg, "F-0002", "F-0001")
    assert not again.changed and again.fingerprint == current


# --- deps -------------------------------------------------------------------------------------


def test_deps_lists_dependencies_and_dependents_with_state(kb, capsys):
    kb_with_dependent(kb)
    kb.add("F-0003", "tray", CLAIM_C, topic="tray", extra="depends_on:\n  F-0001: deadbeef\n  F-0002: null\n")
    current = fp_in_kb(kb, "F-0001")
    root = ["--root", str(kb.root)]

    assert main(root + ["deps", "F-0001"]) == 0
    assert capsys.readouterr().out == (
        "F-0001 depends on: (none)\n"
        "F-0001 dependents:\n"
        f"  F-0002  current    {current}  (Title of F-0002)\n"
        f"  F-0003  suspect    recorded deadbeef, current {current}; re-read F-0001, then kblam ack F-0003 "
        f"F-0001  (Title of F-0003)\n"
    )
    assert main(root + ["deps", "F-0003"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("F-0003 depends on:\n  F-0001  suspect")
    assert "  F-0002  unstamped  re-read F-0002, then kblam ack F-0003 F-0002  (Title of F-0002)\n" in out
    assert out.endswith("F-0003 dependents: (none)\n")
    assert main(root + ["deps", "F-0042"]) == 1
    assert "F-0042 is not in findings/" in capsys.readouterr().err


def test_cli_put_reports_stamp_and_suspect_dependents(kb, capsys):
    kb_with_dependent(kb)
    capsys.readouterr()
    staged = edit_finding(kb.cfg, "F-0001")
    staged.write_text(staged.read_text(encoding="utf-8").replace("about 0.1%", "within 0.2%"),
                      encoding="utf-8", newline="\n")
    root = ["--root", str(kb.root)]
    assert main(root + ["put", str(staged)]) == 0
    assert "kblam put: F-0002 is now suspect (it depends on F-0001" in capsys.readouterr().out
    assert main(root + ["validate"]) == 1
    assert "K3 findings/motor/F-0002-motor.md:10: suspect" in capsys.readouterr().out
    assert main(root + ["ack", "F-0002", "F-0001"]) == 0
    assert main(root + ["validate"]) == 0
