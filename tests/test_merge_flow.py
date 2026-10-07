"""Merging a finding whose fact another finding already states: what `kblam put` refuses while the
duplicate is still installed (K9, and a Jev `same_fact` reject), the sequence the kblam-write skill
gives, and the sequence `kblam rm`'s refusal for a linked finding gives — both remove the other
finding before the put. Each refusal's exit status and whole output are pinned, and every command a
refusal or the skill names is run in the state it names and succeeds (D49)."""

from __future__ import annotations

import m611_helpers as m
from kblam.review import load_items

from test_check import jkb  # noqa: F401 (jkb is a fixture)

A_CLAIM = "The platen lamp is driven from the main controller's J3 header."
B_CLAIM = "The platen lamp is switched by the main controller, not by a separate power board."
# A's claim after the merge: it states B's fact in other words, so K9 does not read the two as one
# claim but Jev does. A_NEW_K9 is B's claim exactly, which K9 refuses.
A_NEW = "The main controller switches the platen lamp; there is no separate power board for it."
A_NEW_K9 = B_CLAIM
# A's claim after the merge when it adds B's detail without stating B's fact: nothing is refused.
A_NEW_DIFF = ("The platen lamp is driven from the main controller's J3 header; it is not fed from a "
              "separate power board.")
QUANTITY = 'quantities:\n  - {name: lamp supply, value: 5, unit: "V"}\n'
STAGED = ".kblam/staging/F-0012-lamp.md"
B_PATH = "findings/motor/F-0020-lamp.md"
A_PATH = "findings/calibration/F-0012-lamp.md"
POINTER = "Load the kblam-write skill for how to fix this."
# The merge sentence `kblam rm`'s refusal for a linked finding gives, with no copy staged: the quantity
# route first, one staged copy, and the removal before the put.
MERGE = ("Merge the other way, in one staged copy of F-0012: kblam edit F-0012 stages one at "
         f"{STAGED}. If F-0020 gives a quantity F-0012 lacks, kblam rm F-0020 --merged-into F-0012 is "
         "refused for it: add only that quantity to that copy, leave F-0012's claim as it is installed, "
         "and kblam put it; that put leaves nothing staged, so kblam edit F-0012 stages the next copy to "
         "work in. Add what F-0020 states that F-0012 does not yet (its detail and quantities) to the "
         "copy you are working in, run kblam rm F-0020 --merged-into F-0012, then kblam put that copy (a "
         "put of F-0012 that states F-0020's fact is refused while F-0020 is installed)")


def pair(kb, *, quantity: bool = False) -> None:
    """A (F-0012) and the duplicate B (F-0020), installed with claims that do not duplicate each
    other; the merge edits A to state B's fact."""
    kb.add("F-0012", "lamp", A_CLAIM)
    kb.add("F-0020", "lamp", B_CLAIM, topic="motor", extra=QUANTITY if quantity else "")


def edit_a(kb, old: str, new: str, *, quantity: str = "") -> str:
    """`kblam edit F-0012`, then the author's change to the staged copy: `old` becomes `new`, and a
    `quantity` block goes above `verified:`. The staged path `kblam edit` printed."""
    staged = m.ok(m.kblam(kb, "edit", "F-0012"), "edit").out.strip()
    path = kb.root / staged
    text = path.read_text(encoding="utf-8").replace(old, new, 1)
    if quantity:
        text = text.replace("verified:", quantity + "verified:", 1)
    path.write_bytes(text.encode("utf-8"))
    return staged


def add_quantity(path) -> None:
    """Add B's quantity to a staged copy of A, leaving its claim as it is installed: the put the skill's
    quantity route names first, which needs the quantity in A before the removal."""
    path.write_bytes(path.read_text(encoding="utf-8").replace("verified:", QUANTITY + "verified:", 1).encode("utf-8"))


def restate(path, old: str = A_CLAIM, new: str = A_NEW) -> None:
    """State B's fact in a staged copy of A: the put the merge names last."""
    path.write_bytes(path.read_text(encoding="utf-8").replace(old, new, 1).encode("utf-8"))


def open_items(kb) -> list:
    return [item for item in load_items(kb.cfg) if item.open]


def items_output(kb) -> str:
    run = m.ok(m.kblam(kb, "items"), "items")
    return run.out


# --- what the put refuses while the duplicate is installed ----------------------------------------


def test_k9_refuses_the_put_that_states_the_installed_duplicate(kb):
    """A's edited claim is B's claim, so K9 refuses the put and its text sends the author to B."""
    pair(kb)
    staged = edit_a(kb, A_CLAIM, A_NEW_K9)
    put = m.kblam(kb, "put", staged)
    assert put.code == 1
    assert put.err == ""
    assert put.out == (
        f"K9 {STAGED}:11: claim duplicates F-0020 ({B_PATH}), token-set similarity 1.00 >= 0.9. One fact "
        f"belongs in one finding: edit F-0020 instead (kblam edit F-0020)\n"
        f"kblam put: rejected F-0012 (1 error(s)); findings/ is unchanged. Fix the staged file and put it "
        f"again. {POINTER}\n")
    assert (kb.root / B_PATH).is_file() and (kb.root / A_PATH).is_file()   # nothing moved


def test_a_same_fact_reject_refuses_the_put_that_states_the_installed_duplicate(jkb):
    """Jev reads A's edited claim as B's fact, so the put exits 4, raises a rejected item, and its text
    also sends the author to B."""
    pair(jkb)
    jkb.fake.relations[(B_CLAIM, A_NEW)] = ("same_fact", 0.93, 0.91)
    staged = edit_a(jkb, A_CLAIM, A_NEW)
    put = m.kblam(jkb, "put", staged)
    assert put.code == 4
    assert put.err == ""
    items = open_items(jkb)
    assert [(i.kind, i.new_id, i.existing_id) for i in items] == [("rejected", "F-0012", "F-0020")]
    rid = items[0].id
    assert put.out == (
        f"rejected {rid} same_fact F-0012 vs F-0020 (p 0.93, confidence 0.91): F-0020 already states this; "
        f"edit F-0020 instead (kblam edit F-0020). The put was refused: send {rid} to the librarian, which "
        f'resolves it only when the two findings state distinct facts (kblam resolve {rid} --distinct '
        f'"<reason>")\n'
        f"kblam put: rejected F-0012 by the Jev check (1 reject verdict(s)); findings/ is unchanged. Fix every "
        f"verdict above and put it again. {POINTER}\n")


# --- the sequence the skill gives: remove the duplicate first (D49) --------------------------------


def test_the_merge_removes_the_duplicate_and_then_puts(jkb):
    """`kblam edit A`, add B's detail, `kblam rm B --merged-into A`, `kblam put <staged file>`: the rm
    closes the rejected item the refused put raised, the staged copy still puts, validate is clean and
    no item is left open for either finding."""
    pair(jkb)
    jkb.fake.relations[(B_CLAIM, A_NEW)] = ("same_fact", 0.93, 0.91)
    staged = edit_a(jkb, A_CLAIM, A_NEW)
    refused = m.kblam(jkb, "put", staged)
    assert refused.code == 4 and "same_fact F-0012 vs F-0020" in refused.out
    rid = open_items(jkb)[0].id

    removed = m.ok(m.kblam(jkb, "rm", "F-0020", "--merged-into", "F-0012"), "rm")
    assert f"kblam rm: removed F-0020 ({B_PATH}), merged into F-0012" in removed.out
    assert (f"kblam rm: closed rejected item {rid} (same_fact F-0012 vs F-0020): F-0020 was removed "
            f"(merged into F-0012)") in removed.out
    assert not (jkb.root / B_PATH).exists()

    assert m.ok(m.kblam(jkb, "put", staged), "put").out == f"kblam put: F-0012 -> {A_PATH}\n"
    assert A_NEW in (jkb.root / A_PATH).read_text(encoding="utf-8")
    assert m.validate(jkb).code == 0
    assert items_output(jkb) == "kblam items: no open review, rejected or unchecked items\n"


def test_the_merge_removes_the_duplicate_and_then_puts_after_k9(kb):
    """The same sequence where the refusal is K9 (exit 1, no item): the rm still unblocks the put."""
    pair(kb)
    staged = edit_a(kb, A_CLAIM, A_NEW_K9)
    assert m.kblam(kb, "put", staged).code == 1
    m.ok(m.kblam(kb, "rm", "F-0020", "--merged-into", "F-0012"), "rm")
    assert m.ok(m.kblam(kb, "put", staged), "put").code == 0
    assert m.validate(kb).code == 0
    assert items_output(kb) == "kblam items: no open review, rejected or unchecked items\n"


def test_the_merge_moves_a_quantity_the_target_lacks_in_two_puts(jkb):
    """The skill's quantity route, run literally: the rm is refused for B's quantity, so A goes in first
    with only that quantity added and its claim left as it is; then the rm, then a fresh `kblam edit A`
    with the rest of B's detail."""
    pair(jkb, quantity=True)
    jkb.fake.relations[(B_CLAIM, A_NEW)] = ("same_fact", 0.93, 0.91)
    refused = m.kblam(jkb, "rm", "F-0020", "--merged-into", "F-0012")
    assert refused.code == 1
    assert refused.err == (
        f"kblam rm: F-0020 cannot be removed: F-0020 gives lamp supply = 5 V, which F-0012 does not give "
        f"with the same name, value and unit; move it into F-0012 first (kblam edit F-0012), or keep "
        f"F-0020. findings/ is unchanged. {POINTER}\n")

    staged = m.ok(m.kblam(jkb, "edit", "F-0012"), "edit").out.strip()   # "kblam edit A": none staged
    add_quantity(jkb.root / staged)                     # the quantity, A's claim left as it is
    assert m.ok(m.kblam(jkb, "put", staged), "put").code == 0
    m.ok(m.kblam(jkb, "rm", "F-0020", "--merged-into", "F-0012"), "rm")
    again = m.ok(m.kblam(jkb, "edit", "F-0012"), "edit").out.strip()    # "kblam edit A again"
    restate(jkb.root / again)                           # the rest of B's detail
    assert m.ok(m.kblam(jkb, "put", again), "put").code == 0
    assert m.validate(jkb).code == 0
    assert items_output(jkb) == "kblam items: no open review, rejected or unchecked items\n"
    assert sorted(p.name for p in jkb.findings.rglob("*.md")) == ["F-0012-lamp.md", "INDEX.md"]


# --- the rm refusal for a linked finding: the sequence it names (D49) ------------------------------


def sequence(kb, staged: str, claim: str) -> str:
    """The commands the rm refusal names, in its order: add what F-0020 states to the copy it named,
    `kblam rm F-0020 --merged-into F-0012`, `kblam put <that copy>`. Returns the put's output."""
    restate(kb.root / staged, A_CLAIM, claim)
    removed = m.ok(m.kblam(kb, "rm", "F-0020", "--merged-into", "F-0012"), "rm")
    assert f"kblam rm: removed F-0020 ({B_PATH}), merged into F-0012" in removed.out
    return m.ok(m.kblam(kb, "put", staged), "put").out


def finish(kb, ct: str, put: str) -> None:
    """The rebind command the put printed, run as printed, then validate and the items list."""
    assert (f"kblam put: {ct} is now stale (this put changed F-0012, which it is bound to); a reviewer "
            f"rechecks it and runs kblam review rebind {ct} --by NAME --reason TEXT --expect D. kblam "
            f"validate fails until then") in put
    m.ok(m.rebind(kb, ct, by="reviewer-b"), "review rebind")
    assert m.validate(kb).code == 0
    assert items_output(kb) == "kblam items: no open review, rejected or unchecked items\n"


def linked(kb) -> tuple[str, str]:
    """F-0012 and F-0020 installed with a CT linking F-0012, and the refusal's message: (CT id, stderr)."""
    pair(kb)
    ct = m.task(kb, "F-0012", by="reviewer-a", proponent="researcher-a")
    refused = m.kblam(kb, "rm", "F-0012", "--merged-into", "F-0020")
    assert refused.code == 1 and refused.out == ""
    return ct, refused.err


def test_the_linked_rm_sequence_succeeds_for_a_k9_duplicate(kb):
    """K9 variant: A's edited claim is B's claim, so the put the old text named first was refused while B
    was installed. The sequence the refusal now names removes B first, and doing exactly that succeeds."""
    ct, err = linked(kb)
    assert MERGE in err
    staged = m.ok(m.kblam(kb, "edit", "F-0012"), "edit").out.strip()   # "kblam edit F-0012"
    finish(kb, ct, sequence(kb, staged, A_NEW_K9))


def test_the_linked_rm_sequence_succeeds_for_a_same_fact_duplicate(jkb):
    """Jev variant: Jev reads A's edited claim as B's fact, so the put the old text named first exited 4
    while B was installed. The removal comes first now, so the put never meets the verdict."""
    ct, err = linked(jkb)
    assert "run kblam rm F-0020 --merged-into F-0012, then kblam put that copy" in err
    jkb.fake.relations[(B_CLAIM, A_NEW)] = ("same_fact", 0.93, 0.91)
    staged = m.ok(m.kblam(jkb, "edit", "F-0012"), "edit").out.strip()
    finish(jkb, ct, sequence(jkb, staged, A_NEW))


def test_the_linked_rm_sequence_succeeds_for_a_new_detail(kb):
    """The common case: A's edited claim adds B's detail without stating B's fact, so nothing is refused;
    the one sequence still removes B before the put."""
    ct, err = linked(kb)
    assert "run kblam rm F-0020 --merged-into F-0012, then kblam put that copy" in err
    staged = m.ok(m.kblam(kb, "edit", "F-0012"), "edit").out.strip()
    finish(kb, ct, sequence(kb, staged, A_NEW_DIFF))
