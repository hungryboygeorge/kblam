"""The [kb] keys M6.10 adds (SPEC §9 "Keys M6.10 adds"): each one's default keeps today's behaviour,
and the two whose absence means something (adjudicators, history_id_terms) load as None."""

from __future__ import annotations

import pytest

from kblam.config import RESOLUTIONS_NAME, ConfigError, load_config


def _load(tmp_path, kb_extra: str = ""):
    (tmp_path / "kblam.toml").write_text(f'[kb]\nroot = "findings"\n{kb_extra}', encoding="utf-8")
    return load_config(root=tmp_path)


def test_defaults_keep_todays_behaviour(tmp_path):
    cfg = _load(tmp_path)
    assert cfg.topics == ()
    assert cfg.verbatim_blockquotes is False
    assert cfg.scope_separator == "/"
    assert cfg.scope_wildcard == "any"
    assert cfg.adjudicators is None
    assert cfg.history_id_terms is None
    assert cfg.resolutions_path == tmp_path.resolve() / RESOLUTIONS_NAME


def test_values_load(tmp_path):
    cfg = _load(tmp_path, 'topics = ["calibration"]\nverbatim_blockquotes = true\nscope_separator = ""\n'
                          'scope_wildcard = ""\nadjudicators = []\nhistory_id_terms = ["Wrong", "replaces"]\n')
    assert cfg.topics == ("calibration",)
    assert cfg.verbatim_blockquotes is True
    assert cfg.scope_separator == ""
    assert cfg.scope_wildcard == ""
    assert cfg.adjudicators == ()          # present and empty: only the main session (§8 item 2)
    assert cfg.history_id_terms == ("wrong", "replaces")


@pytest.mark.parametrize("line", ['topics = "calibration"', "verbatim_blockquotes = 1",
                                  "scope_separator = 3", 'adjudicators = "librarian"',
                                  "history_id_terms = [1]"])
def test_wrong_types_are_config_errors(tmp_path, line):
    with pytest.raises(ConfigError):
        _load(tmp_path, line + "\n")


def test_unknown_key_still_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown \\[kb\\] key"):
        _load(tmp_path, "adjudicator = []\n")
