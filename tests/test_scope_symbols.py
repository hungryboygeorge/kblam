"""M6.10's configurable scope symbols (SPEC §4, §6.1 Scope gating, §9): a scope value containing
[kb] scope_separator stands for each of its parts ("" never splits one), and [kb] scope_wildcard overlaps
every scope ("" means none does). Candidate selection and the quantity comparison take both from the
configuration; the defaults keep the fixed `/` and `any` of before."""

from __future__ import annotations

import pytest

from kblam.check import Checker, scope_parts, scopes_overlap, select_candidates
from kblam.view import load_view

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML

E1 = "The motor reaches steady output after 90 seconds of warm-up."
E2 = "The media tray reports its type through two contact pins read at load time."
SCOPES = 'scopes = ["MX-200", "MX-100", "any"]'


def configure(kb, kb_lines: str = "") -> None:
    """The fixture's kblam.toml with every scope value the tests use and `kb_lines` added to [kb]."""
    scopes = 'scopes = ["MX-200", "MX-100", "MX-100/MX-200", "MX-100+MX-200", "any", "all"]\n'
    kb.write("kblam.toml", KBLAM_TOML.replace(SCOPES + "\n", scopes) + kb_lines + NO_EMBEDDINGS + PROMPT_TOML)


def quantity(value: float) -> str:
    return f"quantities:\n  - {{name: curve ratio, value: {value}, unit: ratio}}\n"


# --- scope_parts and scopes_overlap -----------------------------------------------------------------


def test_scope_parts_split_at_the_separator():
    assert scope_parts(["MX-100/MX-200", "any"]) == {"MX-100", "MX-200", "any"}
    assert scope_parts(["MX-100 + MX-200"], "+") == {"MX-100", "MX-200"}
    assert scope_parts(["MX-100/MX-200"], "+") == {"MX-100/MX-200"}


def test_an_empty_separator_never_splits():
    assert scope_parts(["MX-100/MX-200", " any "], "") == {"MX-100/MX-200", "any"}


def test_the_wildcard_overlaps_every_scope():
    assert scopes_overlap(["all"], ["MX-100"], "/", "all") and scopes_overlap(["MX-100"], ["all"], "/", "all")
    assert scopes_overlap(["MX-100/all"], ["MX-200"], "/", "all")  # a part may be the wildcard
    assert not scopes_overlap(["any"], ["MX-100"], "/", "all")    # "any" is then an ordinary value


def test_an_empty_wildcard_overlaps_nothing_but_equal_parts():
    assert not scopes_overlap(["any"], ["MX-100"], "/", "")
    assert scopes_overlap(["any"], ["any"], "/", "")
    assert scopes_overlap(["MX-100/MX-200"], ["MX-200"], "/", "")


@pytest.mark.parametrize("a, b", [
    (["MX-100/MX-200"], ["MX-100"]), (["any"], ["MX-100"]), (["MX-200"], ["any"]), (["MX-100"], ["MX-200"]),
    (["MX-100", "MX-200"], ["MX-200/MX-300"]), ([], ["any"]), ([], []),
])
def test_the_defaults_are_the_kb_defaults(kb, a, b):
    cfg = kb.cfg
    assert (cfg.scope_separator, cfg.scope_wildcard) == ("/", "any")
    assert scopes_overlap(a, b) == scopes_overlap(a, b, cfg.scope_separator, cfg.scope_wildcard)
    assert scope_parts(a) == scope_parts(a, cfg.scope_separator)


# --- candidate selection ------------------------------------------------------------------------------


def selection_ids(kb, finding_id: str) -> tuple[list[str], list[str]]:
    """(candidates, different_scope) of `finding_id`, by BM25 over the KB."""
    view = load_view(kb.cfg)
    finding = next(f for f in view.findings if f.file_id == finding_id)
    selection = select_candidates(view, finding, 30, 0.2, link_bonus=0.15)
    return [c.finding_id for c in selection.candidates], [c.finding_id for c in selection.different_scope]


def test_selection_splits_scope_values_at_the_configured_separator(kb):
    configure(kb)
    kb.add("F-0001", "slash", E1, scope="[MX-100/MX-200]")
    kb.add("F-0002", "plus", E1 + " Both boards.", scope="[MX-100+MX-200]")
    kb.add("F-0003", "self", E1 + " Measured again.", scope="[MX-100]")
    assert selection_ids(kb, "F-0003") == (["F-0001"], ["F-0002"])
    configure(kb, 'scope_separator = "+"\n')
    assert selection_ids(kb, "F-0003") == (["F-0002"], ["F-0001"])
    configure(kb, 'scope_separator = ""\n')
    assert selection_ids(kb, "F-0003") == ([], ["F-0001", "F-0002"])


def test_selection_overlaps_the_configured_wildcard_only(kb):
    configure(kb)
    kb.add("F-0001", "any", E1, scope="[any]")
    kb.add("F-0002", "all", E1 + " Both boards.", scope="[all]")
    kb.add("F-0003", "self", E1 + " Measured again.", scope="[MX-100]")
    assert selection_ids(kb, "F-0003") == (["F-0001"], ["F-0002"])
    configure(kb, 'scope_wildcard = "all"\n')
    assert selection_ids(kb, "F-0003") == (["F-0002"], ["F-0001"])
    configure(kb, 'scope_wildcard = ""\n')
    assert selection_ids(kb, "F-0003") == ([], ["F-0001", "F-0002"])


# --- the quantity comparison ----------------------------------------------------------------------


def quantity_conflicts(kb, finding_id: str) -> list[str]:
    """The IDs a check of `finding_id` reports a quantity conflict with. The fixture enables no Jev
    verdict, so Jev is never asked. The findings share no token and no link, so none is a candidate or a
    different_scope pair: only §6.3's own loop over the KB compares them, by scope."""
    view = load_view(kb.cfg)
    finding = next(f for f in view.findings if f.file_id == finding_id)
    with Checker(kb.cfg) as checker:
        result = checker.check(view, finding, "check")
    assert result.jev_enabled is False and result.different_scope == [] and result.over_budget == []
    return [v.existing_id for v in result.verdicts if v.verdict == "quantity_conflict"]


def add_motor(kb, scope: str) -> None:
    """F-0002: E1 with its own evidence, so it shares no token and no link with an E2 finding."""
    kb.write("evidence/new-run/log.txt", "x\n")
    kb.add("F-0002", "motor", E1, title="Motor warm-up", scope=scope, evidence="[evidence/new-run/]",
           extra=quantity(1.5))


def test_quantity_comparison_splits_at_the_configured_separator(kb):
    configure(kb)
    kb.add("F-0001", "tray", E2, title="Media tray pins", scope="[MX-100/MX-200]", extra=quantity(1.0017))
    add_motor(kb, "[MX-200]")
    assert quantity_conflicts(kb, "F-0002") == ["F-0001"]
    configure(kb, 'scope_separator = ""\n')
    assert quantity_conflicts(kb, "F-0002") == []


def test_quantity_comparison_uses_the_configured_wildcard(kb):
    configure(kb)
    kb.add("F-0001", "tray", E2, title="Media tray pins", scope="[all]", extra=quantity(1.0017))
    add_motor(kb, "[MX-200]")
    assert quantity_conflicts(kb, "F-0002") == []
    configure(kb, 'scope_wildcard = "all"\n')
    assert quantity_conflicts(kb, "F-0002") == ["F-0001"]
    kb.add("F-0001", "tray", E2, title="Media tray pins", scope="[any]", extra=quantity(1.0017))
    configure(kb, 'scope_wildcard = ""\n')
    assert quantity_conflicts(kb, "F-0002") == []
