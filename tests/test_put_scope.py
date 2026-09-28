"""M6.10 `put` validation scope (SPEC §7 put, Validation): only errors in the incoming finding and errors the
move introduces elsewhere block a put; errors already in the tree are warnings; K3 on other findings and open
items never block."""

from __future__ import annotations

from kblam import review
from kblam.cli import main
from kblam.finding import fingerprint
from kblam.review import ReviewItem
from kblam.store import edit_finding, put
from kblam.view import load_view

from conftest import finding_text
from test_check import E1, N, jkb  # noqa: F401 (jkb is a fixture)

POINTER = "Load the kblam-write skill for how to fix this."
CLAIM_A = "The two sensor curve types agree to about 0.1%, so they are not two analog gains."
CLAIM_B = "The motor warm-up drift settles within 90 seconds of power-on at 4000 rpm."
CLAIM_C = "The media tray reports its type through two contact pins read at load time."
BROKEN_A = CLAIM_A.replace("so they are not", "as previously believed, not")  # K4: "previously believed"
BROKEN_B = CLAIM_B + " The 30 second figure turned out to be a logging artefact."  # K4: "turned out"


def run(kb, *args) -> int:
    return main(["--root", str(kb.root), *args])


def stage(kb, finding_id: str, slug: str, claim: str, **kw):
    return kb.write(f".kblam/staging/{finding_id}-{slug}.md", finding_text(finding_id, claim, **kw))


def replace_in(path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8", newline="\n")


def test_two_broken_findings_are_fixed_one_put_at_a_time(kb, capsys):
    kb.add("F-0001", "sensor", BROKEN_A)
    kb.add("F-0002", "motor", BROKEN_B, topic="motor")
    assert kb.codes() == ["K4", "K4"]

    first = edit_finding(kb.cfg, "F-0001")
    replace_in(first, "as previously believed, not", "so they are not")
    assert run(kb, "put", str(first)) == 0
    out = capsys.readouterr().out.splitlines()
    warning, = [line for line in out if line.startswith("kblam put: warning: ")]
    assert warning.startswith('kblam put: warning: K4 findings/motor/F-0002-motor.md:11: revision-history '
                              'language "turned out"')
    assert out[-1] == ("kblam put: the 1 warning(s) above were already in findings/ before this put, so they did "
                       "not block it; kblam validate fails until each is fixed")
    assert [(i.code, i.path) for i in kb.issues()] == [("K4", "findings/motor/F-0002-motor.md")]

    second = edit_finding(kb.cfg, "F-0002")
    replace_in(second, " The 30 second figure turned out to be a logging artefact.", "")
    result = put(kb.cfg, second)
    assert result.ok and result.warnings == []
    assert kb.issues() == []


def test_the_incoming_findings_own_errors_still_block_and_old_ones_are_listed(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    kb.write("findings/summary.md", "# stray\n")
    before = kb.snapshot()
    staged = stage(kb, "F-0002", "motor", BROKEN_B, topic="motor")

    result = put(kb.cfg, staged)
    assert [(i.code, i.path) for i in result.issues] == [("K4", "findings/motor/F-0002-motor.md")]
    assert [(i.code, i.path) for i in result.warnings] == [("K8", "findings/summary.md")]
    assert kb.snapshot() == before

    assert run(kb, "put", str(staged)) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("K4 .kblam/staging/F-0002-motor.md:11: revision-history language")
    assert out[1].startswith("kblam put: warning: K8 findings/summary.md: findings/ holds only findings")
    assert out[-1].startswith("kblam put: rejected F-0002 (1 error(s)); findings/ is unchanged")
    assert out[-1].endswith(POINTER)


def test_an_error_the_move_introduces_in_another_finding_blocks(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    quote = f"""
        <!-- verbatim: findings/calibration/F-0001-sensor.md:11 -->
        > **Claim.** {CLAIM_A}
    """
    kb.add("F-0002", "quote", CLAIM_C, topic="tray", body=quote)
    assert kb.issues() == []
    before = kb.snapshot()

    staged = edit_finding(kb.cfg, "F-0001")  # the rewrite breaks F-0002's verbatim excerpt of F-0001
    replace_in(staged, "about 0.1%", "within 0.2%")
    result = put(kb.cfg, staged)
    assert [(i.code, i.path) for i in result.issues] == [("K10", "findings/tray/F-0002-quote.md")]
    assert result.warnings == []
    assert kb.snapshot() == before

    assert run(kb, "put", str(staged)) == 1
    out = capsys.readouterr().out
    assert "K10 findings/tray/F-0002-quote.md:13: the excerpt does not occur verbatim" in out
    assert out.rstrip().endswith(POINTER)


def test_a_jev_reject_lists_old_errors_and_still_ends_with_the_skill_pointer(jkb, capsys):
    jkb.add("F-0001", "motor", E1)
    jkb.write("findings/summary.md", "# stray\n")
    jkb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    capsys.readouterr()
    assert run(jkb, "put", str(stage(jkb, "F-0002", "drift", N))) == 4
    out = capsys.readouterr().out.splitlines()
    assert out[-2].startswith("kblam put: warning: K8 findings/summary.md: ")
    assert out[-1].startswith("kblam put: rejected F-0002 by the Jev check") and out[-1].endswith(POINTER)


def test_k3_on_other_findings_never_blocks_and_only_an_old_one_is_a_warning(kb):
    kb.add("F-0001", "sensor", CLAIM_A)
    assert put(kb.cfg, stage(kb, "F-0002", "motor", CLAIM_B, topic="motor",
                             extra="depends_on:\n  F-0001: null\n")).ok

    rewrite = edit_finding(kb.cfg, "F-0001")
    replace_in(rewrite, "about 0.1%", "within 0.2%")
    result = put(kb.cfg, rewrite)  # makes F-0002 suspect: reported as such, not as a warning
    assert result.ok and result.suspect == ["F-0002"] and result.warnings == []
    assert kb.codes() == ["K3"]

    result = put(kb.cfg, stage(kb, "F-0003", "tray", CLAIM_C, topic="tray"))
    assert result.ok and result.suspect == []
    assert [(i.code, i.path) for i in result.warnings] == [("K3", "findings/motor/F-0002-motor.md")]


def test_open_review_and_unchecked_items_never_block_a_put(kb, capsys):
    kb.add("F-0001", "sensor", CLAIM_A)
    current = fingerprint(next(f for f in load_view(kb.cfg).findings if f.file_id == "F-0001"), "/")
    review.save_items(kb.cfg, [
        ReviewItem("U-00000001", "unchecked", "open", "F-0001", current, message="no answer", created="t"),
        ReviewItem("R-00000002", "review", "open", "F-0001", current, "F-0009", "deadbeef0000", "same_fact",
                   message="m", created="t"),
    ])
    assert run(kb, "validate") == 1
    assert put(kb.cfg, stage(kb, "F-0002", "motor", CLAIM_B, topic="motor")).ok
