"""The review assets against the real tool (§8.2, §12 M6.11): the reading rule, the write skill and the
kblam.toml template name the review root through its token, every command they show parses with the
CLI's own parser, and every diagnostic they quote is one the code prints.

The assets ship in `src/kblam/assets/` and `kblam init` writes them into the consuming repo's
`.claude/` (init._asset renders the two root tokens). Nothing here reads a KB, asks a model or touches
the network: it compares text to text and parses with `cli.build_parser()`.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import io
import re
import shlex
import tomllib
from pathlib import Path

import pytest

from kblam import cli, decisions, init, k13, k14, k15, review_write
from kblam.config import DEFAULT_REVIEW, load_config

RULE = "rules/kblam-findings.md"
SKILL = "skills/kblam-write/SKILL.md"
TEMPLATE = "kblam.toml"
ASSETS = (RULE, SKILL)
# The commands each asset shows in full (a bare mention of a name does not count, so the rule's two
# are its floor): a rewrite that drops one is a rewrite that told the reader less.
MINIMUM = {RULE: 2, SKILL: 25}

# The values the assets' placeholders stand for: an MX-100 source and evidence package, as SPEC §5.2
# writes its examples. A substitution is applied to a command before it is parsed, longest first.
SOURCE = "resources/mx-docs/notes/full-scan-trace.md"
EVIDENCE = "observed:evidence/2026-09-27-mx100-trace-reread/README.md:row-102"
STAGED = ".kblam/review-staging/F-0137-sensor-curve-types.md"
SUBSTITUTIONS = (
    (re.compile(r"PROVENANCE:PATH:LOCATOR"), EVIDENCE),
    (re.compile(r"<source-path>"), SOURCE),
    (re.compile(r"<staged file>"), STAGED),
    (re.compile(r'"<title>"'), '"The two sensor curve types differ"'),
    (re.compile(r"<topic>"), "calibration"),
    (re.compile(r"<reason>"), "the two findings state distinct facts"),
    (re.compile(r"F-NNNN"), "F-0137"),
    (re.compile(r"F-target"), "F-0017"),
    (re.compile(r"F-x"), "F-0014"),
    (re.compile(r"SC-NNNN"), "SC-0001"),
    (re.compile(r"\bA-B\b"), "63-65"),
    (re.compile(r"\bD\b"), "0123456789ab"),
    (re.compile(r"\bTEXT\b"), '"the printed equality cannot hold"'),
    (re.compile(r"\bNAME\b"), "reviewer-b"),
)

# (a fragment the write skill quotes as tool output, the module whose code prints it). Every one is a
# diagnostic an author meets while writing a record, so the skill and the code must not drift apart.
QUOTED = (
    ("the source changed since kblam challenge new; run it again", review_write),
    ("records are never deleted or renamed", k13),
    ("INDEX.md is missing; run kblam review index", k13),
    ("INDEX.md differs from the generated review index", k13),
    ("holds only SC-, CT- and CU- records in their kind's folder", k13),
    ("only an open challenge can be pinned", review_write),
    ("changed since your edit", review_write),
    ("changed since you inspected it; show it again", review_write),
    ("a closing decision needs someone else", decisions),
    ("challenges this quoted assertion at", k14),
    ("and this excerpt quotes its assertion text from another version of that file", k14),
    ("reserved for the coordinator or the user", cli),
    ("with no primary support", k13),
    ("no basis entry has role counterevidence or internal-inconsistency", k13),
    ("no basis entry has role model-mismatch", k13),
    ("the source changed since", k13),
    ("the pinned version is not present", k13),
    ("a pin is never replaced", review_write),
    ("a decision that keeps the status belongs to", review_write),
    ("no longer has the excerpt it cites", review_write),
)


# --- helpers ------------------------------------------------------------------------------------


def rendered(rel: str, kb_root: str = "findings", review_root: str = "research-review") -> str:
    """An asset as `kblam init` writes it, with both root tokens substituted."""
    return init._asset(rel, kb_root, review_root).decode("utf-8")


def command_candidates(text: str) -> list[str]:
    """Every `kblam ...` code span and fenced-block line: a command to run, or a mention of one."""
    candidates = re.findall(r"`([^`\n]+)`", text)
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        candidates += block.splitlines()
    return [candidate.strip() for candidate in candidates if candidate.strip().startswith("kblam ")]


def shows_arguments(span: str) -> bool:
    """Whether a span carries an argument or a flag. `kblam put <staged file>` does; `kblam put`, a
    mention of the command the surrounding paragraph is about, does not."""
    return " --" in span or re.search(r"\s(?:<|F-|SC-|CT-|CU-|R-)", span) is not None


def one_line(text: str) -> str:
    """Text with its whitespace collapsed, so a quote the markdown wraps still compares."""
    return " ".join(text.split())


def substitute(command: str) -> str:
    """The command with its placeholders replaced by sample values."""
    for pattern, value in SUBSTITUTIONS:
        command = pattern.sub(value, command)
    return command


def printed_text(module) -> str:
    """Every string literal of a module, joined: a message split across source lines or written as an
    f-string is searchable as one string."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    return "\n".join(node.value for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str))


def commented_review_block(text: str) -> list[str]:
    """The template's commented-out [review] section: the `# [review]` line and the comments under it."""
    lines = text.splitlines()
    start = lines.index("# [review]")
    end = start
    while end + 1 < len(lines) and lines[end + 1].startswith("#"):
        end += 1
    return lines[start:end + 1]


def uncommented(block: list[str]) -> str:
    """The block as TOML: its comment markers removed."""
    return "\n".join(line.removeprefix("# ") for line in block) + "\n"


def load_in(directory, text: str):
    """Write a kblam.toml under `directory` and load it."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "kblam.toml").write_text(text, encoding="utf-8", newline="\n")
    return load_config(root=directory)


def settings_of(cfg) -> dict:
    """A Config without its repository root, so two directories' configs compare."""
    return {key: value for key, value in dataclasses.asdict(cfg).items() if key != "repo_root"}


# --- the commands the assets show ----------------------------------------------------------------


@pytest.mark.parametrize("rel", ASSETS)
def test_every_command_the_assets_show_parses_with_the_cli(rel):
    """A command the rule or the skill tells an author to run is one the CLI accepts (§5.2.5). The
    placeholders are the assets' own: an ID, a name, a digest prefix, a source path or a flag."""
    text = rendered(rel)
    broken = [span for span in re.findall(r"`([^`]*)`", text, re.S)
              if "\n" in span and span.startswith("kblam ")]
    assert broken == [], f"{rel} wraps a command across lines, which this check would skip: {broken}"
    parser = cli.build_parser()
    checked, problems = 0, []
    for span in command_candidates(text):
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                parser.parse_args(shlex.split(substitute(span))[1:])   # "kblam" is the program
            checked += 1
        except SystemExit as exc:
            error = stderr.getvalue().strip()
            if exc.code == 2 and "the following arguments are required" in error \
                    and not shows_arguments(span):
                continue                       # a mention of a command name, not one shown to run
            problems.append(f"{span!r} -> {error.splitlines()[-1] if error else exc.code}")
    assert problems == [], f"{rel} shows a command the CLI refuses: " + "; ".join(problems)
    assert checked >= MINIMUM[rel], f"{rel} had only {checked} commands checked as written"


def test_the_skill_shows_the_commands_the_diagnostics_print():
    """The command a K14 or a K15 diagnostic names is the one the skill gives the author (§5.2.4)."""
    skill = one_line(rendered(SKILL))
    assert k14.USE_REVIEW.format(challenge="SC-0001", finding="F-0012", ordinal=2) in skill
    assert k15.REBIND.format(rid="CT-0001") in skill
    assert f"{k15.REBIND.format(rid='CT-0001')} --evidence PROVENANCE:PATH:LOCATOR" in skill
    assert "kblam challenge new <source-path> --lines A-B --by NAME" in skill


# --- the diagnostics the assets quote ------------------------------------------------------------


@pytest.mark.parametrize("fragment, module", QUOTED)
def test_a_quoted_diagnostic_is_one_the_code_prints(fragment, module):
    """A fragment the skill quotes as tool output is the wording the module that reports it prints."""
    assert one_line(fragment) in one_line(rendered(SKILL)), f"the skill no longer quotes {fragment!r}"
    assert fragment in printed_text(module), f"{module.__name__} no longer prints {fragment!r}"


# --- the two roots -------------------------------------------------------------------------------


@pytest.mark.parametrize("rel", ASSETS)
def test_the_assets_name_the_roots_only_through_their_tokens(rel):
    """The rule and the skill name the KB root and the review root through init's tokens, and the
    rendered text carries the configured root and no token (§12 M6.11, init.KB_ROOT_TOKEN)."""
    raw = init._asset(rel).decode("utf-8")
    assert init.KB_ROOT_TOKEN in raw and init.REVIEW_ROOT_TOKEN in raw
    assert "findings/" not in raw and "research-review" not in raw   # neither default is written out

    text = rendered(rel, kb_root="kb", review_root="review/records")
    assert "review/records/" in text and "`kb/" in text
    assert "{{" not in text and "}}" not in text
    assert "findings/" not in text


def test_the_rule_stays_short():
    """SPEC §8.2: the reading rule is kept short -- it costs the reader who opens a finding."""
    assert len(rendered(RULE).splitlines()) <= 32


# --- the kblam.toml template ---------------------------------------------------------------------


def test_the_templates_review_section_is_commented_and_holds_the_defaults(tmp_path):
    """The §9 [review] keys and their defaults are shown, commented out, and uncommenting the section
    gives the config the defaults describe."""
    text = init._asset(TEMPLATE).decode("utf-8")
    assert tomllib.loads(text).get("review") is None          # commented: the template has no table
    block = commented_review_block(text)
    assert block[0] == "# [review]"
    for line in block[1:]:
        assert re.fullmatch(r"# [a-z_]+ = .+  # .+", line), line   # a default and a one-line comment

    table = tomllib.loads(uncommented(block))["review"]
    assert set(table) == set(DEFAULT_REVIEW)
    assert table == {"root": DEFAULT_REVIEW["root"], "provenance": [*DEFAULT_REVIEW["provenance"]],
                     "primary_provenance": [*DEFAULT_REVIEW["primary_provenance"]]}
    cfg = load_in(tmp_path, uncommented(block))
    assert (cfg.review_dir, list(cfg.provenance), list(cfg.primary_provenance)) == (
        DEFAULT_REVIEW["root"], DEFAULT_REVIEW["provenance"], DEFAULT_REVIEW["primary_provenance"])


def test_the_commented_section_changes_nothing(tmp_path):
    """The template kblam init writes loads to the same Config as one with the block removed."""
    text = init._asset(TEMPLATE).decode("utf-8")
    block = commented_review_block(text)
    without = "\n".join(line for line in text.splitlines() if line not in block) + "\n"
    assert settings_of(load_in(tmp_path / "with", text)) == settings_of(load_in(tmp_path / "without", without))


def test_a_project_root_in_the_section_is_the_configs_review_root(tmp_path):
    """Uncommenting the section with another folder names that folder, as SPEC §9 says it does."""
    text = init._asset(TEMPLATE).decode("utf-8")
    block = uncommented(commented_review_block(text)).replace(
        'root = "research-review"', 'root = "review/records"')
    cfg = load_in(tmp_path, f"[kb]\nroot = \"findings\"\n\n{block}")
    assert cfg.review_dir == "review/records"
