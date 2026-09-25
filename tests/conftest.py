"""Fixture KBs built under tmp_path."""

from __future__ import annotations

import textwrap
import tomllib
from pathlib import Path

import pytest

from kblam import jev_prompts
from kblam.config import Config, load_config
from kblam.rules import validate
from kblam.store import regenerate_index
from kblam.treehash import current_digest, write_tree_hash
from kblam.view import load_view

KBLAM_TOML = """\
[kb]
root = "findings"
labels = ["observed", "decoded", "inferred", "unknown"]
scopes = ["MX-200", "MX-100", "any"]
"""

# The [jev] section the fixtures add, last in the file: `""` forces BM25 (SPEC §6.1). Embeddings are
# exercised in test_embed.py, against a fake ollama; no test may reach a real one, which may be
# running on this machine and would otherwise make candidate selection depend on it.
NO_EMBEDDINGS = """\
[jev]
embedding_model = ""
"""

ASSETS = Path(__file__).resolve().parents[1] / "src" / "kblam" / "assets"
TEMPLATE_TOML = (ASSETS / "kblam.toml").read_text(encoding="utf-8")


def _template_prompt_toml() -> str:
    """The [jev.prompt] tables of the installed template: the fixtures carry the default wording, as
    a project does after `kblam init`. Sliced by header so the template can order them freely."""
    lines = TEMPLATE_TOML.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith("[jev.prompt"))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("[") and not lines[i].startswith("[jev.prompt")), len(lines))
    return "".join(lines[start:end])


PROMPT_TOML = _template_prompt_toml()
# The id of that default wording: what [jev.thresholds] records and the fixtures calibrate against.
DEFAULT_PROMPT_ID = jev_prompts.prompt_id(
    *jev_prompts.load_questions(tomllib.loads(TEMPLATE_TOML)["jev"]["prompt"]))

SOURCE_TEXT = """\
Line one of the capture log.
The two curve types agree to 0.1% on line 0.
Median ratio 1.0017 across 2048 pixels.
A fourth line with more detail.
"""


def finding_text(
    finding_id: str,
    claim: str,
    *,
    title: str | None = None,
    topic: str = "calibration",
    label: str = "observed",
    scope: str = "[MX-200]",
    evidence: str = "[evidence/2026-09-22-ratio/]",
    extra: str = "",
    body: str = "",
) -> str:
    title = title or f"Title of {finding_id}"
    text = (
        "---\n"
        f"id: {finding_id}\n"
        f"title: {title}\n"
        f"topic: {topic}\n"
        f"label: {label}\n"
        f"scope: {scope}\n"
        f"evidence: {evidence}\n"
        "verified: 2026-09-22\n"
        f"{extra}"
        "---\n"
        "\n"
        f"**Claim.** {claim}\n"
    )
    if body:
        text += "\n" + textwrap.dedent(body).strip("\n") + "\n"
    return text


class KB:
    def __init__(self, root: Path):
        self.root = root

    @property
    def cfg(self) -> Config:
        return load_config(root=self.root)

    @property
    def findings(self) -> Path:
        return self.root / "findings"

    def write(self, rel: str, text: str | bytes) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8") if isinstance(text, str) else text
        path.write_bytes(data)
        return path

    def add(self, finding_id: str, slug: str, claim: str, *, topic: str = "calibration", **kw) -> Path:
        """Place a finding directly (fixture setup only) and regenerate the index."""
        path = self.write(f"findings/{topic}/{finding_id}-{slug}.md",
                          finding_text(finding_id, claim, topic=topic, **kw))
        self.reindex()
        return path

    def reindex(self) -> None:
        """Fixture setup writes findings/ directly, so it also accepts the tree, as validate --record would."""
        regenerate_index(self.cfg)
        write_tree_hash(self.cfg, current_digest(self.cfg))

    def issues(self):
        return validate(load_view(self.cfg))

    def codes(self) -> list[str]:
        return [issue.code for issue in self.issues()]

    def snapshot(self) -> dict[str, bytes]:
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in sorted(self.findings.rglob("*")) if p.is_file()}


@pytest.fixture(autouse=True)
def home(tmp_path_factory, monkeypatch) -> Path:
    """A scratch home directory for every test: the default [jev] key_file is ~/kblam/jev!.txt, and no
    test may read the real one. Path.expanduser uses USERPROFILE on Windows and HOME elsewhere."""
    directory = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(directory))
    monkeypatch.setenv("USERPROFILE", str(directory))
    return directory


@pytest.fixture
def kb(tmp_path: Path) -> KB:
    root = tmp_path / "repo"
    root.mkdir()
    base = KB(root)
    base.write("kblam.toml", KBLAM_TOML + NO_EMBEDDINGS + PROMPT_TOML)
    base.write("evidence/2026-09-22-ratio/README.md", "manifest\n")
    base.write("evidence/2026-09-22-ratio/log.txt", SOURCE_TEXT)
    base.write("evidence/2026-09-22-ratio/dump.bin", b"\x00\x01binary\xff")
    base.reindex()
    return base
