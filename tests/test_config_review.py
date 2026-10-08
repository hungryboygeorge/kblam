"""M6.11 wave 0: the [review] config table (SPEC §9), Issue level and owner (SPEC §5, §5.2.4), and the
review fixtures."""

from __future__ import annotations

import re

import pytest

from conftest import KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, TRACE_PATH, TRACE_TEXT, record_text
from kblam.config import ConfigError, load_config
from kblam.finding import plain_data, yaml_rt
from kblam.rules import Issue, errors
from kblam.view import KBView


def _config(kb, review: str):
    kb.write("kblam.toml", KBLAM_TOML + review + NO_EMBEDDINGS + PROMPT_TOML)
    return load_config(root=kb.root)


def test_review_defaults(kb):
    cfg = kb.cfg
    assert cfg.review_dir == "research-review"
    assert cfg.provenance == ("observed", "decoded", "inferred", "unknown")
    assert cfg.primary_provenance == ("observed", "decoded")
    assert cfg.review_path == kb.root.resolve() / "research-review"
    assert cfg.review_staging_dir == cfg.state_dir / "review-staging"
    assert cfg.review_receipts_dir == cfg.state_dir / "review-receipts"
    assert cfg.review_ids_path == cfg.state_dir / "review-ids"
    assert cfg.journal_path == cfg.state_dir / "journal.json"


def test_review_table_values(kb):
    cfg = _config(kb, '[review]\nroot = "work/review"\nprovenance = ["observed", "reported"]\n'
                      'primary_provenance = ["observed"]\n')
    assert (cfg.review_dir, cfg.provenance, cfg.primary_provenance) == (
        "work/review", ("observed", "reported"), ("observed",))


@pytest.mark.parametrize("review, message", [
    ('[review]\nrooot = "x"\n', "unknown [review] key(s) rooot"),
    ('[review]\nroot = "../x"\n', "[review] root must be"),
    ('[review]\nroot = "/abs"\n', "[review] root must be"),
    ('[review]\nroot = "a b"\n', "[review] root must be"),
    ('[review]\nroot = "findings"\n', "the [kb] root"),
    ('[review]\nroot = "findings/review"\n', "the [kb] root"),
    ('[review]\nroot = ".kblam"\n', ".kblam/"),
    ('[review]\nroot = "evidence/review"\n', "the evidence root evidence"),
    ('[review]\nroot = "history"\n', "the history folder history"),
    ('[review]\nprovenance = []\n', "must list at least one value"),
    ('[review]\nprimary_provenance = ["measured"]\n', "must be a subset"),
    ('[review]\nprovenance = "observed"\n', "[review] provenance must be a list of strings"),
])
def test_review_table_errors(kb, review, message):
    with pytest.raises(ConfigError, match=re.escape(message)):
        _config(kb, review)


def test_review_root_in_nested_repository_refused(kb):
    (kb.root / "sub" / ".git").mkdir(parents=True)
    with pytest.raises(ConfigError, match="nested Git"):
        _config(kb, '[review]\nroot = "sub/review"\n')


def test_review_root_containing_kb_root_refused(kb):
    kb.write("kblam.toml", KBLAM_TOML.replace('root = "findings"', 'root = "kb/findings"')
             + '[review]\nroot = "kb"\n' + NO_EMBEDDINGS + PROMPT_TOML)
    with pytest.raises(ConfigError, match=re.escape("must not be, contain or lie under the [kb] root")):
        load_config(root=kb.root)


def test_issue_level_owner_and_format(kb):
    view = KBView(cfg=kb.cfg, files={})
    error = Issue("findings/a/F-0001-x.md", 3, "K1", "bad")
    warning = Issue("research-review/challenges/source-challenge-0001.yaml", 0, "K13", "stale source", "warning", "source-challenge-0001")
    assert (error.level, error.owner) == ("error", "")
    assert error.format(view) == "K1 findings/a/F-0001-x.md:3: bad"
    assert warning.format(view) == "K13 warning research-review/challenges/source-challenge-0001.yaml: stale source"
    assert errors([error, warning]) == [error]


def test_source_repo_fixture(source_repo):
    assert (source_repo.root / TRACE_PATH).read_text(encoding="utf-8") == TRACE_TEXT
    before = source_repo.snapshot()
    assert len(source_repo.head()) == 40 and len(source_repo.blob(TRACE_PATH)) == 40
    assert source_repo.snapshot() == before


@pytest.mark.parametrize("kind", ["source-challenge", "claim-task", "checked-use"])
def test_record_text_parses(kind):
    data = plain_data(yaml_rt().load(record_text(kind)))
    assert data["id"] == f"{kind}-0001" and data["status"] == "open" and data["decisions"] == []
