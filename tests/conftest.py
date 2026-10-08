"""Fixture KBs built under tmp_path."""

from __future__ import annotations

import copy
import ctypes
import datetime
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

from kblam import jev_prompts
from kblam.config import Config, load_config
from kblam.finding import yaml_rt
from kblam.rules import errors, validate
from kblam.store import regenerate_index
from kblam.treehash import write_tree_hash_v2
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
        write_tree_hash_v2(self.cfg, load_view(self.cfg))

    def issues(self):
        return validate(load_view(self.cfg))

    def codes(self) -> list[str]:
        """The blocking codes: warnings never fail (SPEC §5); `issues()` still returns everything."""
        return [issue.code for issue in errors(self.issues())]

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


# --- M6.11: review records and a nested source repository (SPEC §5.2, §12 M6.11 tests) ----------------

SOURCE_REPO = "resources/mx-docs"           # the nested source repository, relative to the KB root
TRACE_PATH = "notes/full-scan-trace.md"     # its committed text file, relative to SOURCE_REPO
TRACE_TEXT = """\
# MX-100 full-scan trace (transcribed)
Row 101: bytes 0x3A 0x3B
Row 102: bytes 0x3A 0x3B; the two bytes are equal.
Row 103: bytes 0x40 0x41
"""
ZERO64 = "0" * 64
DROP = object()  # record_text(..., key=DROP) leaves the key out
# Variables that send git to another repository, index or object store: under one of them neither a
# copied template nor <root>/.git can stand for what git would make or find, so the helpers ask git.
GIT_LOCATION_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                          "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")


class SourceRepo:
    """A nested Git repository under the KB root: the read-only source of challenges and excerpts."""

    def __init__(self, kb_root: Path, rel: str = SOURCE_REPO):
        self.kb_root = kb_root
        self.rel = rel
        self.root = kb_root / rel

    def git(self, *args: str, check: bool = True) -> str:
        done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True,
                              check=False)
        if check and done.returncode:
            raise AssertionError(f"git {' '.join(args)} failed: {done.stderr}")
        return done.stdout.strip()

    def init(self, *extra: str) -> "SourceRepo":
        """`git init` plus the fixture identity and settings; a copy of the "empty" template where
        there is one and <root> holds no .git yet."""
        self.root.mkdir(parents=True, exist_ok=True)
        template = None if extra or os.path.lexists(self.root / ".git") else git_template("empty")
        if template is not None:
            copy_repository(template, self.root)
            return self
        return self.init_by_git(*extra)

    def init_by_git(self, *extra: str) -> "SourceRepo":
        """init() as git does it: `git init` and four `git config` calls."""
        self.root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", *extra, str(self.root)], check=True, capture_output=True)
        for key, value in (("user.name", "fixture"), ("user.email", "fixture@example.invalid"),
                           ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
            self.git("config", key, value)
        return self

    def write(self, path: str, data: str | bytes) -> Path:
        full = self.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return full

    def commit(self, path: str, data: str | bytes, message: str = "update") -> str:
        """Write, add and commit one file; the new HEAD commit ID."""
        self.write(path, data)
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def blob(self, path: str, commit: str = "HEAD") -> str:
        return self.git("rev-parse", f"{commit}:{path}")

    def kb_path(self, path: str = TRACE_PATH) -> str:
        """The file's path relative to the KB root, as records and verbatim tags write it."""
        return f"{self.rel}/{path}"

    def own_git_dir(self) -> Path | None:
        """<root>/.git where it is the directory `git -C <root>` uses: HEAD naming a ref or an object
        ID, objects/ and refs/, no commondir, no reftable/ (git 2.45 and later keep the refs in that
        directory, which the fast snapshot would miss), and no variable sending git elsewhere. None
        otherwise (a gitfile, a linked worktree, a directory git would pass over)."""
        dot = self.root / ".git"
        if any(name in os.environ for name in GIT_LOCATION_VARIABLES) or not dot.is_dir():
            return None
        head = dot / "HEAD"
        if not (head.is_file() and (dot / "objects").is_dir() and (dot / "refs").is_dir()) \
                or os.path.lexists(dot / "commondir") or os.path.lexists(dot / "reftable"):
            return None
        return dot if re.fullmatch(rb"ref: refs/[^\n]+\n|[0-9a-f]{40}\n|[0-9a-f]{64}\n",
                                   head.read_bytes()) else None

    def snapshot(self) -> dict:
        """Working bytes, HEAD, index and refs: equal before and after means "the source is unchanged".
        In a repository whose own .git directory is <root>/.git, the refs are its files (every loose ref
        under refs/, and packed-refs), which with symbolic_head decide what `rev-parse HEAD` and
        `for-each-ref` would print, so only `git status` runs; elsewhere git prints them."""
        files = {p.relative_to(self.root).as_posix(): p.read_bytes()
                 for p in sorted(self.root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(self.root).parts}
        git_dir = self.own_git_dir()
        if git_dir is None:
            git_dir = Path(self.git("rev-parse", "--absolute-git-dir"))
            head = self.git("rev-parse", "HEAD", check=False)
            refs = self.git("for-each-ref", "--format=%(refname) %(objectname)")
        else:
            head = None
            refs = {p.relative_to(git_dir).as_posix(): p.read_bytes()
                    for p in sorted((git_dir / "refs").rglob("*")) if p.is_file()}
        index = git_dir / "index"
        packed = git_dir / "packed-refs"
        return {
            "files": files,
            "head": head,
            "symbolic_head": (git_dir / "HEAD").read_bytes(),
            "index": index.read_bytes() if index.is_file() else None,
            "refs": refs,
            "packed_refs": packed.read_bytes() if packed.is_file() else None,
            "status": self.git("status", "--porcelain", "--ignored"),      # last: it may rewrite the index
        }


GIT_TEMPLATES: dict[tuple, Path] = {}     # (kind, GIT_ variables) -> a repository made in this process
TEMPLATE_FACTORY: list = []               # the session's tmp_path_factory, where they are made


@pytest.fixture(scope="session", autouse=True)
def git_template_factory(tmp_path_factory):
    """Lets git_template make its repositories under this session's temporary root: once per test
    process, so once per xdist worker."""
    TEMPLATE_FACTORY.append(tmp_path_factory)
    yield
    TEMPLATE_FACTORY.clear()
    GIT_TEMPLATES.clear()


def git_template(kind: str) -> Path | None:
    """A repository made once in this process by git and copied into tests from then on: "empty" is
    what SourceRepo.init() makes, "trace" what the source_repo fixture makes. None unless git reads no
    configuration file and no variable sends it elsewhere (the state source_repo sets), where a copy
    stands for what git would make; kept per value of the other GIT_ variables."""
    env = os.environ
    if not (TEMPLATE_FACTORY and env.get("GIT_CONFIG_NOSYSTEM") == "1" and env.get("GIT_CONFIG_GLOBAL")
            and not os.path.lexists(env["GIT_CONFIG_GLOBAL"])
            and not any(name in env for name in GIT_LOCATION_VARIABLES)):
        return None
    key = (kind, tuple(sorted((name, value) for name, value in env.items()
                              if name.startswith("GIT_") and name != "GIT_CONFIG_GLOBAL")))
    if key not in GIT_TEMPLATES:
        repo = SourceRepo(TEMPLATE_FACTORY[0].mktemp(f"git-template-{kind}"), "repo").init_by_git()
        if kind == "trace":
            repo.commit(TRACE_PATH, TRACE_TEXT, "trace")
        GIT_TEMPLATES[key] = repo.root
    return GIT_TEMPLATES[key]


def copy_repository(template: Path, root: Path) -> None:
    """Copy a template's tree into `root`, as git made it there. Git for Windows hides .git, which
    copytree does not carry over. File times are kept; the index's stat data still describes the
    template's files, so a caller copying a worktree refreshes the index."""
    shutil.copytree(template, root, dirs_exist_ok=True)
    if sys.platform == "win32":
        hidden = os.stat(template / ".git").st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN
        if hidden:
            attributes = os.stat(root / ".git").st_file_attributes & ~stat.FILE_ATTRIBUTE_DIRECTORY
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            if not kernel32.SetFileAttributesW(str(root / ".git"), attributes | hidden):
                raise ctypes.WinError(ctypes.get_last_error())


@pytest.fixture
def source_repo(kb, monkeypatch) -> SourceRepo:
    """A nested Git repository at <KB>/resources/mx-docs with TRACE_TEXT committed at notes/full-scan-trace.md
    (LF). System and global Git config are ignored, for the fixture and for kblam's own git calls. A copy
    of the "trace" template, its index refreshed for the copied file, where there is one."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(kb.root.parent / "no-global-gitconfig"))
    config = kb.root / "kblam.toml"
    kb.write("kblam.toml", config.read_text(encoding="utf-8").replace(
        '[kb]\n', '[kb]\nevidence_roots = ["evidence", "resources"]\n', 1))
    repo = SourceRepo(kb.root)
    template = None if os.path.lexists(repo.root) else git_template("trace")
    if template is None:
        repo.init().commit(TRACE_PATH, TRACE_TEXT, "trace")
    else:
        copy_repository(template, repo.root)
        repo.git("update-index", "-q", "--refresh")
    return repo


def _ref(path: str, **fields) -> dict:
    ref = {"path": path, "sha256": ZERO64, "repo": None, "commit": None, "blob": None, "snapshot": None}
    ref.update(fields)
    return ref


def record_data(kind: str, rec_id: str | None = None, **fields) -> dict:
    """A structurally valid record of `kind` ("source-challenge", "claim-task" or "checked-use") as plain data; `fields` replace top-level
    keys (DROP removes one). Hashes and bindings are placeholders: set them for semantic checks."""
    rec_id = rec_id or f"{kind}-0001"
    common = {"schema": 1, "id": rec_id, "created": datetime.date(2026, 9, 28), "creator": "reviewer-a"}
    source = f"{SOURCE_REPO}/{TRACE_PATH}"
    if kind == "source-challenge":
        data = {**common, "status": "open",
                "source": {**_ref(source), "assertion": {"lines": [3, 3], "text": "the two bytes are equal",
                                                         "sha256": ZERO64, "occurrence": 1}},
                "proposition": "The printed byte equality follows from the printed byte values",
                "scope": ["MX-100 capture transcription"],
                "classification": "contradicted",
                "basis": [{**_ref(source), "locator": "row 102: printed byte values",
                           "role": "internal-inconsistency", "provenance": "observed"}],
                "usable": "The printed byte values may be cited as a report.",
                "limits": "Do not infer the capture bytes from this row.",
                "linked_findings": [],
                "decisions": []}
    elif kind == "claim-task":
        data = {**common, "proponent": "researcher-a", "status": "open", "kind": "replication",
                "finding": "F-0001", "claim_fingerprint": "0badf00d0000", "base_file_sha256": ZERO64,
                "question": "Does an independent measurement establish the claim?",
                "method": "Repeat the capture with the documented settings.",
                "outcomes": {"supports": "The ratio is within 0.1%.", "refutes": "The ratio differs by more.",
                             "inconclusive": "The capture is too noisy to tell."},
                "controls": ["same firmware version"],
                "stop": "Stop after three captures.",
                "expected_evidence": ["an evidence/ capture package"],
                "decisions": []}
    elif kind == "checked-use":
        data = {**common, "proponent": "researcher-a", "status": "open", "challenge": "source-challenge-0001",
                "challenge_bind": ZERO64, "finding": "F-0001", "finding_fingerprint": "0badf00d0000",
                "finding_file_sha256": ZERO64,
                "citation": {"ordinal": 1, "path": source, "range": [2, 3], "tag_sha256": ZERO64},
                "disposition": "unaffected_raw_bytes",
                "reason": "The excerpt is used only for the printed byte values.",
                "decisions": []}
    else:
        raise ValueError(kind)
    data = copy.deepcopy(data)
    for key, value in fields.items():
        if value is DROP:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def dump_record(data: dict) -> str:
    """YAML text of a record mapping, block style, as kblam writes records."""
    yaml = yaml_rt()
    yaml.default_flow_style = False
    yaml.width = 4096
    buffer = io.StringIO()
    yaml.dump(data, buffer)
    return buffer.getvalue()


def record_text(kind: str, rec_id: str | None = None, **fields) -> str:
    """record_data(...) as YAML text."""
    return dump_record(record_data(kind, rec_id, **fields))
