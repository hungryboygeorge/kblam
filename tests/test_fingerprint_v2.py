"""Fingerprint v2 (SPEC §5.1): 12 hex digits over the id, claim, label, scope, quantities and evidence, each
list in a canonical order; and fingerprint v1, which only kblam upgrade and the old-format checks use.
Nothing here touches the network."""

from __future__ import annotations

import pytest

from kblam.finding import fingerprint, fingerprint_as, fingerprint_v1, is_v1_fingerprint, parse_finding

from kblam.review import ReviewItem, open_items, save_items
from kblam.store import put
from kblam.view import load_view

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, finding_text

CLAIM = "The two curve types agree to about 0.1% (median ratio 1.0017), so they are not two analog gains."

# A finding whose lists are out of canonical order, with its fingerprints pinned: v1 as the kblam before
# M6.10 computed it (checked against that code on main at 4f3f36e), and v2 as this kblam does. Stamps in
# every knowledge base depend on these values, so a change to either is a format change.
VECTOR = b"""\
---
id: F-0007
title: The two curve types are not two analog gains
topic: calibration
label: observed
scope: [MX-200, MX-100/MX-300]
evidence:
  - evidence/run1/log.txt
  - ./evidence/run1/
quantities:
  - {name: Curve  Ratio, value: 1.0017, unit: ratio}
  - {name: gain, value: 2, unit: dB}
verified: 2026-09-22
---

**Claim.** The two curve types agree to about 0.1% (median ratio 1.0017), so they are
not two analog gains.
"""


def fp(text: str, separator: str = "/") -> str:
    return fingerprint(parse_finding("findings/calibration/F-0001-curves.md", text.encode("utf-8")), separator)


def v1(text: str) -> str:
    return fingerprint_v1(parse_finding("findings/calibration/F-0001-curves.md", text.encode("utf-8")))


def finding(**kw) -> str:
    return finding_text("F-0001", CLAIM, **kw)


def test_the_pinned_vector():
    parsed = parse_finding("findings/calibration/F-0007-curves.md", VECTOR)
    assert fingerprint_v1(parsed) == "a7d69e22"
    assert fingerprint(parsed, "/") == "695f10458543"
    assert fingerprint(parsed, "") == "15541d9e8c91"  # MX-100/MX-300 is one scope value when nothing splits


def test_v2_is_twelve_hex_digits_and_v1_is_eight():
    assert len(fp(finding())) == 12 and int(fp(finding()), 16) >= 0
    assert is_v1_fingerprint(v1(finding())) and not is_v1_fingerprint(fp(finding()))
    assert not is_v1_fingerprint(None) and not is_v1_fingerprint("DEADBEEF") and not is_v1_fingerprint("deadbee")


@pytest.mark.parametrize("a, b", [
    (dict(scope="[MX-200, MX-100]"), dict(scope="[MX-100, MX-200]")),
    (dict(scope="[MX-200, MX-100]"), dict(scope="[MX-100, MX-200, MX-100]")),   # duplicates
    (dict(scope="[MX-100/MX-200]"), dict(scope="[MX-200, MX-100]")),             # the separator splits
    (dict(evidence="[evidence/a/x.txt, evidence/b/]"), dict(evidence="[evidence/b, evidence/a/x.txt]")),
    (dict(evidence="[evidence/b/]"), dict(evidence="['./evidence/b']")),
    (dict(evidence="[evidence/b]"), dict(evidence="['evidence\\b']")),  # a Windows path, as YAML holds it
    (dict(extra="quantities:\n  - {name: gain, value: 2, unit: dB}\n  - {name: ratio, value: 1.5, unit: x}\n"),
     dict(extra="quantities:\n  - {name: ratio, value: 1.5, unit: x}\n  - {name: gain, value: 2, unit: dB}\n")),
])
def test_reordering_a_list_is_not_an_edit(a, b):
    assert fp(finding(**a)) == fp(finding(**b))


@pytest.mark.parametrize("change", [
    dict(label="inferred"),
    dict(scope="[MX-100]"),
    dict(evidence="[evidence/2026-09-23-other/]"),
    dict(extra="quantities:\n  - {name: gain, value: 3, unit: dB}\n"),
])
def test_what_a_finding_asserts_is_an_edit(change):
    assert fp(finding(**change)) != fp(finding())


def test_the_label_is_what_v2_adds_over_v1():
    assert v1(finding(label="inferred")) == v1(finding())
    assert fp(finding(label="inferred")) != fp(finding())


def test_with_no_separator_a_scope_value_stands_whole():
    assert fp(finding(scope="[MX-100/MX-200]"), "") != fp(finding(scope="[MX-200, MX-100]"), "")
    assert fp(finding(scope="[MX-100/MX-200]"), "/") == fp(finding(scope="[MX-200, MX-100]"), "/")


@pytest.mark.parametrize("extra", [
    "quantities: not-a-list\n",
    "quantities:\n  - {name: gain}\n  - 7\n  - {name: ratio, value: 1.5, unit: x}\n",
])
def test_malformed_values_still_hash(extra):
    """K1 reports these; the fingerprint must still be a value, never an exception."""
    assert len(fp(finding(extra=extra))) == 12
    assert len(fp(finding(scope="MX-200"))) == 12  # a scalar where a list belongs


def test_fingerprint_as_answers_in_the_format_it_is_given():
    parsed = parse_finding("findings/calibration/F-0001-curves.md", finding().encode("utf-8"))
    assert fingerprint_as(fingerprint_v1(parsed), parsed, "/") == fingerprint_v1(parsed)
    assert fingerprint_as(fingerprint(parsed, "/"), parsed, "/") == fingerprint(parsed, "/")
    assert fingerprint_as(None, parsed, "/") == fingerprint(parsed, "/")


def test_every_part_of_kblam_fingerprints_under_the_configured_separator(kb):
    """put stamps, K3 compares, and the review items and checked marks record fingerprints; they must all
    split scope values at the same [kb] scope_separator, or a fresh stamp would read as suspect."""
    kb.write("kblam.toml", KBLAM_TOML.replace('"any"]', '"any", "MX-100/MX-200"]') + 'scope_separator = ""\n'
             + NO_EMBEDDINGS + PROMPT_TOML)
    kb.add("F-0001", "sensor", CLAIM, scope="[MX-100/MX-200]")
    staged = kb.write(".kblam/staging/F-0002-motor.md", finding_text(
        "F-0002", "The pump motor reaches steady output after 90 seconds.", topic="motor",
        extra="depends_on:\n  F-0001: null\n"))
    result = put(kb.cfg, staged)
    assert result.ok, [i.format(result.view) for i in result.issues]
    target = next(f for f in load_view(kb.cfg).findings if f.file_id == "F-0001")
    assert result.stamped == [("F-0001", fingerprint(target, ""))] and fingerprint(target, "") != fingerprint(target, "/")
    assert kb.issues() == []  # K3 reads the stamp as current
    save_items(kb.cfg, [ReviewItem("U-00000001", "unchecked", "open", "F-0001", fingerprint(target, ""),
                                   message="Jev could not answer", created="2026-09-28T10:00:00Z")])
    assert [i.id for i in open_items(kb.cfg, load_view(kb.cfg))] == ["U-00000001"]  # and so does reconcile
