"""The record-ID registry, `.kblam/review-ids` (SPEC §5.2.6)."""

from __future__ import annotations

import pytest

from kblam.registry import ensure, missing, read_ids, write_ids


def test_read_ids_is_none_without_a_registry(kb):
    assert not kb.cfg.review_ids_path.exists()
    assert read_ids(kb.cfg) is None


def test_write_ids_writes_a_sorted_json_list_and_a_newline(kb):
    write_ids(kb.cfg, {"SC-0002", "CT-0010", "SC-0001"})
    assert kb.cfg.review_ids_path.read_bytes() == b'["CT-0010", "SC-0001", "SC-0002"]\n'
    assert read_ids(kb.cfg) == {"SC-0001", "SC-0002", "CT-0010"}


def test_write_ids_of_no_ids_is_an_empty_list(kb):
    write_ids(kb.cfg, set())
    assert kb.cfg.review_ids_path.read_bytes() == b"[]\n"
    assert read_ids(kb.cfg) == set()


def test_read_ids_rejects_a_file_that_is_not_json(kb):
    kb.write(".kblam/review-ids", "[SC-0001\n")
    with pytest.raises(ValueError, match="review-ids"):
        read_ids(kb.cfg)


@pytest.mark.parametrize("text", ['{"SC-0001": 1}\n', '["SC-0001", 3]\n', '"SC-0001"\n', "null\n"])
def test_read_ids_rejects_a_file_that_is_not_a_list_of_strings(kb, text):
    kb.write(".kblam/review-ids", text)
    with pytest.raises(ValueError, match="review-ids"):
        read_ids(kb.cfg)


def test_ensure_creates_the_registry_from_the_records_present(kb):
    assert ensure(kb.cfg, {"SC-0001", "CT-0002"}) == {"SC-0001", "CT-0002"}
    assert kb.cfg.review_ids_path.read_bytes() == b'["CT-0002", "SC-0001"]\n'


def test_ensure_returns_the_registry_unchanged_when_it_exists(kb):
    write_ids(kb.cfg, {"SC-0001", "SC-0002"})
    # `present` is the review root again: a registry is never narrowed by what is there now.
    assert ensure(kb.cfg, {"SC-0001"}) == {"SC-0001", "SC-0002"}
    assert kb.cfg.review_ids_path.read_bytes() == b'["SC-0001", "SC-0002"]\n'


def test_missing_without_a_registry_is_empty(kb):
    assert missing(kb.cfg, {"SC-0001"}) == []


def test_missing_is_the_registered_ids_the_review_root_no_longer_has(kb):
    write_ids(kb.cfg, {"SC-0002", "CT-0001", "SC-0001"})
    assert missing(kb.cfg, {"SC-0001", "CT-0001"}) == ["SC-0002"]
    assert missing(kb.cfg, {"SC-0001", "SC-0002", "CT-0001"}) == []
