"""Deterministic validator rules K1-K11 (SPEC §5).

Each rule takes a KBView and returns Issues. Messages are addressed to an agent: they name the
rule, the place, and what to do about it.
"""

from __future__ import annotations

import bisect
import functools
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from kblam.finding import (
    FILENAME_RE,
    ID_IN_TEXT_RE,
    ID_RE,
    Finding,
    fingerprint,
    id_number,
    normalise_newlines,
)
from kblam.index import generate_index
from kblam.view import KBView

REQUIRED_KEYS = ("id", "title", "topic", "label", "scope", "verified")  # evidence: checked by K2
OPTIONAL_KEYS = ("depends_on", "anchors", "quantities", "check")
ALLOWED_KEYS = REQUIRED_KEYS + ("evidence",) + OPTIONAL_KEYS
QUANTITY_KEYS = ("name", "value", "unit")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
NOT_PROSE_PREFIXES = ("#", "```", "~~~", ">", "<!--", "|")

VERBATIM_START_RE = re.compile(r"^\s*<!--\s*verbatim\b", re.IGNORECASE)
VERBATIM_RE = re.compile(
    r"^\s*<!--\s*verbatim:\s*(?P<path>.+?):"
    r"(?:(?P<first>\d+)(?:-(?P<last>\d+))?|@0x(?P<offset>[0-9A-Fa-f]+))\s*-->\s*$"
)
FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
WORD_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.\-/:][a-z0-9]+)*")


@dataclass(frozen=True, order=True)
class Issue:
    path: str
    line: int          # 0 when no line applies
    code: str
    message: str

    def format(self, view: KBView) -> str:
        where = view.show(self.path) + (f":{self.line}" if self.line else "")
        return f"{self.code} {where}: {self.message}"


def _issue(code: str, finding: Finding, line: int | None, message: str) -> Issue:
    return Issue(finding.path, line or 0, code, message)


def _parsed(view: KBView) -> list[Finding]:
    return [f for f in view.findings if f.meta is not None]


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _repo_path(view: KBView, raw: str) -> tuple[Path | None, str | None]:
    """Resolve a repo-relative path; return (path, None) or (None, reason)."""
    posix = raw.replace("\\", "/")
    pure = PurePosixPath(posix)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", posix):
        return None, "must be relative to the repository root (the directory holding kblam.toml)"
    root = view.cfg.repo_root.resolve()
    full = (root / pure).resolve()
    if not full.is_relative_to(root):
        return None, "points outside the repository root"
    return full, None


def _mapping_key_line(f: Finding, mapping, key, fallback_key: str) -> int | None:
    try:
        return mapping.lc.key(key)[0] + 2
    except (AttributeError, KeyError):
        return f.key_line(fallback_key)


# --- K1 -------------------------------------------------------------------------------------


def k1_schema(view: KBView) -> list[Issue]:
    issues: list[Issue] = []
    for f in view.findings:
        for line, message in f.parse_errors:
            issues.append(_issue("K1", f, line, message))
        if f.meta is not None:
            issues += _k1_fields(view, f)

    by_id: dict[str, list[Finding]] = defaultdict(list)
    for f in view.findings:
        by_id[f.file_id].append(f)
    for finding_id, group in by_id.items():
        if len(group) > 1:
            for f in group:
                others = ", ".join(view.show(o.path) for o in group if o is not f)
                issues.append(_issue("K1", f, None, f"ID {finding_id} is also used by {others}; IDs must be unique"))
    return issues


def _k1_fields(view: KBView, f: Finding) -> list[Issue]:
    cfg = view.cfg
    meta = f.meta
    issues: list[Issue] = []

    def add(line, message):
        issues.append(_issue("K1", f, line, message))

    for key in REQUIRED_KEYS:
        if key not in meta:
            add(1, f"frontmatter is missing required key '{key}'")
    for key in meta:
        if key not in ALLOWED_KEYS:
            add(f.key_line(key), f"unknown frontmatter key '{key}'; allowed keys: {', '.join(ALLOWED_KEYS)}")

    if "id" in meta:
        value = meta["id"]
        if not isinstance(value, str) or not ID_RE.match(value):
            add(f.key_line("id"), f"id {value!r} must look like F-0137")
        elif value != f.file_id:
            add(f.key_line("id"), f"id {value} does not match the filename ID {f.file_id}; the ID never "
                                  f"changes, so set id: {f.file_id}")

    if "title" in meta:
        value = meta["title"]
        if not isinstance(value, str) or not value.strip() or "\n" in value.strip():
            add(f.key_line("title"), "title must be a non-empty one-line string")

    folder = view.rel_to_findings(f.path).parent.as_posix()  # view.findings are all in a topic folder
    if "topic" in meta:
        value = meta["topic"]
        if not isinstance(value, str) or not TOPIC_RE.match(value):
            add(f.key_line("topic"), f"topic {value!r} must be a folder name of lowercase letters, digits, "
                                     f"'-' or '_'")
        elif value != folder:
            add(f.key_line("topic"), f"topic '{value}' does not match the folder '{folder}'; the topic field "
                                     f"and the folder must agree (kblam put files a finding under its topic)")

    if "label" in meta and meta["label"] not in cfg.labels:
        add(f.key_line("label"), f"label {meta['label']!r} is not in the vocabulary; use one of: "
                                 f"{', '.join(cfg.labels)}")

    if "scope" in meta:
        value = meta["scope"]
        if not _is_str_list(value) or not value:
            example = cfg.scopes[0] if cfg.scopes else "any"
            add(f.key_line("scope"), f"scope must be a non-empty list of strings, e.g. [{example}]")
        else:
            for i, item in enumerate(value):
                if item not in cfg.scopes:
                    add(f.item_line("scope", i), f"scope {item!r} is not in the vocabulary; use one of: "
                                                 f"{', '.join(cfg.scopes)}")

    if "verified" in meta:
        value = meta["verified"]
        ok = type(value) is date
        if isinstance(value, str):
            try:
                date.fromisoformat(value)
                ok = len(value) == 10
            except ValueError:
                ok = False
        if not ok or isinstance(value, datetime):
            add(f.key_line("verified"), "verified must be a date: YYYY-MM-DD")

    if "depends_on" in meta:
        value = meta["depends_on"]
        if not isinstance(value, dict):
            add(f.key_line("depends_on"), "depends_on must be a mapping of finding ID to fingerprint, "
                                          "e.g. {F-0102: 3fa9c1d2}")
        else:
            for key, fp in value.items():
                line = _mapping_key_line(f, value, key, "depends_on")
                if not isinstance(key, str) or not ID_RE.match(key):
                    add(line, f"depends_on key {key!r} must be a finding ID like F-0102")
                if fp is not None and not isinstance(fp, str):
                    add(line, f"depends_on fingerprint for {key} parsed as {type(fp).__name__}; quote it: "
                              f"{key}: \"{fp}\"")

    if "anchors" in meta:
        value = meta["anchors"]
        if not isinstance(value, list):
            add(f.key_line("anchors"), "anchors must be a list of strings")
        else:
            for i, item in enumerate(value):
                if not isinstance(item, str):
                    written = f.item_source("anchors", i) or str(item)
                    add(f.item_line("anchors", i),
                        f"anchor {written} is not a string (YAML reads it as {type(item).__name__}); "
                        f"quote it: \"{written}\"")

    if "quantities" in meta:
        value = meta["quantities"]
        if not isinstance(value, list):
            add(f.key_line("quantities"), "quantities must be a list of mappings")
        else:
            for i, item in enumerate(value):
                line = f.item_line("quantities", i)
                if not isinstance(item, dict):
                    add(line, "each quantity must be {name: ..., value: ..., unit: ...}")
                    continue
                extra = [str(k) for k in item if k not in QUANTITY_KEYS]
                if extra:
                    add(line, f"quantity has unknown key(s) {', '.join(extra)}; allowed: name, value, unit")
                name = item.get("name")
                if not isinstance(name, str) or not name.strip():
                    add(line, "quantity needs a non-empty string name")
                number = item.get("value")
                if not isinstance(number, (int, float)) or isinstance(number, bool):
                    add(line, f"quantity value {number!r} must be a number")
                if "unit" in item and not isinstance(item["unit"], str):
                    add(line, "quantity unit must be a string")

    if "check" in meta and (not isinstance(meta["check"], str) or not meta["check"].strip()):
        add(f.key_line("check"), "check must be a non-empty command string")
    return issues


# --- K2 -------------------------------------------------------------------------------------


def k2_references(view: KBView) -> list[Issue]:
    issues: list[Issue] = []
    ids = {f.file_id for f in view.findings}
    for f in _parsed(view):
        evidence = f.meta.get("evidence")
        if not _is_str_list(evidence) or not evidence:
            line = f.key_line("evidence") if "evidence" in f.meta else 1
            issues.append(_issue(
                "K2", f, line,
                "evidence must be a list of at least one repo-relative path (evidence/ folder, capture, "
                "source file) that supports the claim",
            ))
        if isinstance(evidence, list):
            for i, item in enumerate(evidence):
                if not isinstance(item, str):
                    continue
                full, problem = _repo_path(view, item)
                if problem:
                    issues.append(_issue("K2", f, f.item_line("evidence", i), f"evidence path {item} {problem}"))
                elif not full.exists():
                    issues.append(_issue(
                        "K2", f, f.item_line("evidence", i),
                        f"evidence path {item} does not exist; cite a file or folder that exists, "
                        f"relative to the repository root",
                    ))
        depends = f.meta.get("depends_on")
        if isinstance(depends, dict):
            for key in depends:
                if not isinstance(key, str) or not ID_RE.match(key):
                    continue
                line = _mapping_key_line(f, depends, key, "depends_on")
                if key == f.file_id:
                    issues.append(_issue("K2", f, line, f"a finding cannot depend on itself; remove {key} "
                                                        f"from depends_on"))
                elif key not in ids:
                    issues.append(_issue("K2", f, line, f"depends_on names {key}, which is not a finding in the KB"))
    return issues


# --- K3 -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Dependency:
    """One depends_on entry and the state of its recorded fingerprint."""
    dependent: str
    path: str               # the dependent's file
    line: int
    target: str
    recorded: object        # the value as written: a fingerprint, None, or a K1 error
    current: str | None     # the target's fingerprint; None when it cannot be computed
    state: str              # current | suspect | unstamped | missing | invalid


def dependencies(view: KBView) -> list[Dependency]:
    """Every depends_on entry in the view. `missing` and `invalid` entries are K1/K2 errors."""
    by_id: dict[str, list[Finding]] = defaultdict(list)
    for f in view.findings:
        by_id[f.file_id].append(f)
    links = []
    for f in _parsed(view):
        depends = f.meta.get("depends_on")
        if not isinstance(depends, dict):
            continue
        for key, recorded in depends.items():
            if not isinstance(key, str) or not ID_RE.match(key):
                continue
            targets = by_id.get(key, [])
            current = fingerprint(targets[0]) if len(targets) == 1 and targets[0].ok else None
            if isinstance(recorded, str):
                recorded = str(recorded)
            if not targets:
                state = "missing"
            elif key == f.file_id or current is None or not (recorded is None or isinstance(recorded, str)):
                state = "invalid"
            elif recorded is None:
                state = "unstamped"
            else:
                state = "current" if recorded == current else "suspect"
            line = _mapping_key_line(f, depends, key, "depends_on") or 0
            links.append(Dependency(f.file_id, f.path, line, key, recorded, current, state))
    return links


def k3_suspect(view: KBView, focus: frozenset[str] = frozenset()) -> list[Issue]:
    """Unstamped and stale depends_on fingerprints. `focus` findings are staged, so ack cannot fix them."""
    issues = []
    for d in dependencies(view):
        if d.state == "unstamped":
            message = (f"depends_on {d.target} has no fingerprint (unstamped); re-read {d.target}, then run "
                       f"kblam ack {d.dependent} {d.target}")
        elif d.state == "suspect":
            if d.dependent in focus:
                fix = (f"set depends_on {d.target} to null in this file and put it again (put stamps the "
                       f"current fingerprint)")
            else:
                fix = f"run kblam ack {d.dependent} {d.target}, or edit this finding"
            message = (f"suspect: {d.target} was rewritten since this finding was checked against it "
                       f"(recorded {d.recorded}, current {d.current}); re-read {d.target}, then {fix}")
        else:
            continue
        issues.append(Issue(d.path, d.line, "K3", message))
    return issues


# --- K4 / K5 --------------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def _term_pattern(term: str) -> re.Pattern:
    """Case-insensitive, anchored at a word start, so a stem like `falsif` matches `Falsified`.
    Cached: K4 and K5 match every term against two texts of every finding."""
    words = [re.escape(w) for w in term.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words), re.IGNORECASE)


def _prose_segments(view: KBView, f: Finding) -> list[tuple[int, str]]:
    """(first file line, text) for the prose K4/K5 read: the title and the body.

    Verbatim excerpts that pass K10 are blanked (line count kept): they quote sources, whose
    wording is not the finding's own.
    """
    segments = []
    title = f.meta.get("title")
    if isinstance(title, str):
        segments.append((f.key_line("title") or 1, title))
    body = list(f.body_lines)
    for excerpt in verbatim_excerpts(view, f):
        if excerpt.verified:
            body[excerpt.start:excerpt.end] = [""] * (excerpt.end - excerpt.start)
    segments.append((f.body_start_line, "\n".join(body)))
    return segments


@functools.lru_cache(maxsize=None)
def _any_term_pattern(terms: tuple[str, ...]) -> re.Pattern:
    """One pattern matching wherever any term's pattern matches: a single scan tells whether a text
    has a history term at all, which most findings do not."""
    return re.compile("|".join(f"(?:{_term_pattern(term).pattern})" for term in terms), re.IGNORECASE)


def _history_hits(view: KBView, text: str) -> list[tuple[int, str]]:
    """(char offset, term) for every history term in text."""
    terms = view.cfg.history_terms
    if _any_term_pattern(terms).search(text) is None:
        return []
    hits = []
    for term in terms:
        for match in _term_pattern(term).finditer(text):
            hits.append((match.start(), term))
    return sorted(hits)


def _history_segments(view: KBView, f: Finding) -> list[tuple[int, str, list[tuple[int, str]]]]:
    """(first file line, text, history hits) for each of _prose_segments; once per finding per view,
    since K4 and K5 both read them."""
    memo = view.memo.setdefault("history", {})
    if f.path not in memo:
        memo[f.path] = [(line, text, _history_hits(view, text)) for line, text in _prose_segments(view, f)]
    return memo[f.path]


def k4_history_language(view: KBView) -> list[Issue]:
    issues: list[Issue] = []
    for f in _parsed(view):
        for first_line, text, hits in _history_segments(view, f):
            seen = set()
            for offset, term in hits:
                line = first_line + text.count("\n", 0, offset)
                if (line, term) in seen:
                    continue
                seen.add((line, term))
                issues.append(_issue(
                    "K4", f, line,
                    f"revision-history language \"{term}\". State only the current fact, directly; "
                    f"earlier versions live in git history, not in the finding. A negative result is "
                    f"written positively: \"X does not do Y; evidence: ...\"",
                ))
    return issues


def k5_id_near_history(view: KBView) -> list[Issue]:
    window = view.cfg.history_id_window
    issues: list[Issue] = []
    for f in _parsed(view):
        for first_line, text, hits in _history_segments(view, f):
            if not hits:
                continue
            starts = [m.start() for m in re.finditer(r"\S+", text)]

            def word_index(offset: int) -> int:
                return bisect.bisect_right(starts, offset) - 1

            seen = set()
            for match in ID_IN_TEXT_RE.finditer(text):
                other = match.group(0)
                if other == f.file_id:
                    continue
                id_word = word_index(match.start())
                for offset, term in hits:
                    if abs(word_index(offset) - id_word) <= window and (other, term) not in seen:
                        seen.add((other, term))
                        line = first_line + text.count("\n", 0, match.start())
                        issues.append(_issue(
                            "K5", f, line,
                            f"{other} appears within {window} words of \"{term}\". Do not describe "
                            f"another finding as wrong or changed; if {other} is wrong, rewrite it in "
                            f"place (kblam edit {other}) so it states the current fact",
                        ))
    return issues


# --- K6 -------------------------------------------------------------------------------------


def k6_length(view: KBView) -> list[Issue]:
    cfg = view.cfg
    issues: list[Issue] = []
    for f in view.findings:
        if f.text is None:
            continue
        count = len(f.text.split("\n")) - (1 if f.text.endswith("\n") else 0)
        if count > cfg.max_lines:
            issues.append(_issue(
                "K6", f, None,
                f"file has {count} lines (max {cfg.max_lines}); a finding holds one claim, so cut "
                f"detail or split independent claims into separate findings",
            ))
        if f.meta is None:
            continue
        if not f.claim_line:
            issues.append(_issue(
                "K6", f, f.body_start_line or None,
                "no claim paragraph: the first paragraph after the frontmatter must state the claim",
            ))
        elif f.claim_first_line.lstrip().startswith(NOT_PROSE_PREFIXES):
            issues.append(_issue(
                "K6", f, f.claim_line,
                "the first paragraph after the frontmatter must be the claim in prose, not a heading, "
                "quote, table, comment or code block",
            ))
        elif not f.claim:
            issues.append(_issue("K6", f, f.claim_line, "the claim paragraph is empty; write the claim after **Claim.**"))
        else:
            words = len(f.claim.split())
            if words > cfg.max_claim_words:
                issues.append(_issue(
                    "K6", f, f.claim_line,
                    f"claim paragraph has {words} words (max {cfg.max_claim_words}); shorten it and move "
                    f"supporting detail to later paragraphs",
                ))
    return issues


# --- K7 / K8 --------------------------------------------------------------------------------


def k7_index(view: KBView) -> list[Issue]:
    path = view.index_path
    if path not in view.files:
        return [Issue(path, 0, "K7", "INDEX.md is missing; run kblam index")]
    if view.files[path] != generate_index(view):
        return [Issue(
            path, 0, "K7",
            "INDEX.md differs from the generated index; it is never edited by hand. Run kblam index "
            "to regenerate it",
        )]
    return []


def k8_stray_files(view: KBView) -> list[Issue]:
    root = view.cfg.findings_dir
    issues = []
    for path in sorted(view.files):
        if path == view.index_path or view.is_finding_path(path):
            continue
        name = PurePosixPath(path).name
        if FILENAME_RE.match(name):
            message = (f"a finding must sit directly in a topic folder ({root}/<topic>/{name}); put it "
                       f"through kblam put, which files it under its topic, and delete this copy")
        else:
            message = (f"{root}/ holds only findings (F-NNNN-<slug>.md in a topic folder) and the "
                       f"generated INDEX.md. Record each fact here as a finding via kblam new and kblam "
                       f"put, then delete this file")
        issues.append(Issue(path, 0, "K8", message))
    return issues


# --- K9 -------------------------------------------------------------------------------------


def claim_tokens(claim: str) -> frozenset[str]:
    text = re.sub(r"[*_`]", "", claim.lower())
    return frozenset(WORD_TOKEN_RE.findall(text))


def similar_pairs(sets: list[frozenset[str]], threshold: float) -> list[tuple[int, int]]:
    """Every index pair (i < j), sorted, whose sets are both non-empty and may reach Jaccard similarity
    `threshold`: a superset of the pairs that do, which the caller measures exactly. Comparing every
    pair is quadratic, and validate runs under put's lock, so this uses prefix filtering.

    With every set's tokens in one global order (rarest first), two sets that share at least α tokens
    share one within the first |x| - α + 1 tokens of each (Chaudhuri et al., 2006): the first shared
    token has at least α - 1 shared tokens after it. J(a, b) >= t needs |a ∩ b| >= t·|a ∪ b| >= t·|x|
    for x either set, and the overlap is a whole number, so each set is indexed by the prefix that
    α = ⌈t·|x|⌉ gives. It is taken just below t·|x| (by 1e-9, far above the float error of t·|x|
    and of the caller's ratio for any claim length), so rounding can add a candidate, never drop one."""
    frequency = Counter(token for tokens in sets for token in tokens)
    index: dict[str, list[int]] = defaultdict(list)
    pairs: set[tuple[int, int]] = set()
    for j, tokens in enumerate(sets):
        if not tokens:
            continue
        ordered = sorted(tokens, key=lambda token: (frequency[token], token))
        overlap = max(0, math.ceil(threshold * len(ordered) - 1e-9))
        for token in ordered[:len(ordered) - overlap + 1]:
            for i in index[token]:
                pairs.add((i, j))
            index[token].append(j)
    return sorted(pairs)


def k9_duplicates(view: KBView, focus: frozenset[str] = frozenset()) -> list[Issue]:
    threshold = view.cfg.duplicate_similarity
    candidates = [(f, claim_tokens(f.claim)) for f in _parsed(view) if f.claim and f.file_id]
    issues = []
    for i, j in similar_pairs([tokens for _finding, tokens in candidates], threshold):
        (a, ta), (b, tb) = candidates[i], candidates[j]
        similarity = len(ta & tb) / len(ta | tb)
        if similarity < threshold:
            continue
        if b.file_id in focus and a.file_id not in focus:
            target, other = b, a
        elif a.file_id in focus and b.file_id not in focus:
            target, other = a, b
        else:
            target, other = (b, a) if id_number(b.file_id) >= id_number(a.file_id) else (a, b)
        issues.append(_issue(
            "K9", target, target.claim_line,
            f"claim duplicates {other.file_id} ({view.show(other.path)}), token-set similarity "
            f"{similarity:.2f} >= {threshold}. One fact belongs in one finding: edit {other.file_id} "
            f"instead (kblam edit {other.file_id})",
        ))
    return issues


# --- K10 ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Excerpt:
    """One verbatim tag and its block, as body-line indices [start, end)."""
    start: int
    end: int
    problem: str | None    # the K10 failure, or None
    verified: bool         # the excerpt was found in a text source


def _excerpt_after(body: list[str], i: int) -> tuple[str | None, int, str | None]:
    """The fenced block or blockquote starting at body[i]: (excerpt, end index, problem)."""
    follow = "a verbatim tag must be directly followed (next line) by a fenced code block or a blockquote"
    if i >= len(body):
        return None, i, follow
    fence = FENCE_RE.match(body[i])
    if fence:
        marker = fence.group("fence")
        for j in range(i + 1, len(body)):
            stripped = body[j].strip()
            if stripped.startswith(marker) and not stripped.strip(marker[0]):
                return "\n".join(body[i + 1:j]), j + 1, None
        return None, i, "the fenced block after the verbatim tag is never closed"
    if body[i].lstrip().startswith(">"):
        lines = []
        j = i
        while j < len(body) and body[j].lstrip().startswith(">"):
            content = body[j].lstrip()[1:]
            lines.append(content[1:] if content.startswith(" ") else content)
            j += 1
        return "\n".join(lines), j, None
    return None, i, follow


def _source_bytes(view: KBView, full: Path) -> bytes | None:
    rel = full.relative_to(view.cfg.repo_root.resolve()).as_posix()
    if rel in view.files:
        return view.files[rel]
    if rel.startswith(view.cfg.findings_dir + "/"):
        return None  # a findings/ path absent from the view does not exist in this tree
    return full.read_bytes() if full.is_file() else None


@dataclass(frozen=True)
class _Source:
    """A verbatim source as K10 reads it. `text` (newlines normalised) and `lines` are None for a
    binary source: one holding a NUL byte or not valid UTF-8."""
    data: bytes
    text: str | None
    lines: list[str] | None


def _source(view: KBView, full: Path) -> _Source | None:
    """The source at `full`, or None if it does not exist. Read and decoded once per view, since
    many excerpts quote one evidence file."""
    memo = view.memo.setdefault("sources", {})
    if full not in memo:
        data = _source_bytes(view, full)
        text = None
        if data is not None and b"\0" not in data:
            try:
                text = normalise_newlines(data.decode("utf-8"))
            except UnicodeDecodeError:
                pass
        memo[full] = None if data is None else _Source(data, text, None if text is None else text.split("\n"))
    return memo[full]


def verbatim_excerpts(view: KBView, f: Finding) -> list[Excerpt]:
    """Each verbatim tag in the finding's body and its K10 result. Computed once per finding per view:
    K4 and K5 read which excerpts are verified, K10 reads their problems."""
    memo = view.memo.setdefault("excerpts", {})
    if f.path not in memo:
        memo[f.path] = _verbatim_excerpts(view, f)
    return memo[f.path]


def _verbatim_excerpts(view: KBView, f: Finding) -> list[Excerpt]:
    excerpts: list[Excerpt] = []
    body = f.body_lines
    for i, line in enumerate(body):
        if not VERBATIM_START_RE.match(line):
            continue
        tag = VERBATIM_RE.match(line)
        if not tag:
            excerpts.append(Excerpt(i, i + 1, "malformed verbatim tag; use <!-- verbatim: path:LINE -->, "
                                              "<!-- verbatim: path:FIRST-LAST --> or "
                                              "<!-- verbatim: path:@0xOFFSET -->", False))
            continue
        excerpt, end, problem = _excerpt_after(body, i + 1)
        excerpts.append(_check_excerpt(view, tag, i, end, excerpt, problem))
    return excerpts


def _check_excerpt(view: KBView, tag: re.Match, start: int, end: int, excerpt: str | None,
                   problem: str | None) -> Excerpt:
    def failed(message: str) -> Excerpt:
        return Excerpt(start, end, message, False)

    if problem:
        return failed(problem)
    if not excerpt.strip():
        return failed("the verbatim excerpt is empty")
    source = tag.group("path").strip()
    full, problem = _repo_path(view, source)
    if problem:
        return failed(f"verbatim source {source} {problem}")
    found = _source(view, full)
    if found is None:
        return failed(f"verbatim source {source} does not exist; cite an existing file relative to the "
                      f"repository root")
    if found.text is None:
        # Binary source: K10 does not check it (the finding's check: command does), and an
        # unchecked excerpt is not exempt from K4/K5.
        return Excerpt(start, end, None, False)
    problem = _excerpt_problem(tag, source, excerpt, found)
    return failed(problem) if problem else Excerpt(start, end, None, True)


def k10_verbatim(view: KBView) -> list[Issue]:
    return [
        _issue("K10", f, f.body_start_line + e.start, e.problem)
        for f in _parsed(view)
        for e in verbatim_excerpts(view, f)
        if e.problem
    ]


def _excerpt_problem(tag: re.Match, source: str, excerpt: str, found: _Source) -> str | None:
    copy_hint = "copy the text exactly from the source (no paraphrase, no reflowing)"
    if tag.group("offset") is not None:
        offset = int(tag.group("offset"), 16)
        for form in (excerpt, excerpt.replace("\n", "\r\n")):
            if found.data.startswith(form.encode("utf-8"), offset):
                return None
        return f"the excerpt does not occur verbatim at byte offset 0x{offset:X} of {source}; {copy_hint}"

    text, lines = found.text, found.lines
    total = len(lines) - (1 if text.endswith("\n") else 0)
    first = int(tag.group("first"))
    last = int(tag.group("last") or first)
    if first < 1 or last < first or last > total:
        return f"line range {first}-{last} is outside {source}, which has {total} lines"
    if excerpt in "\n".join(lines[first - 1:last]):
        return None
    where = text.find(excerpt)
    if where >= 0:
        start = text.count("\n", 0, where) + 1
        end = start + excerpt.count("\n")
        return (f"the excerpt is not within {source}:{first}-{last}, but it occurs at "
                f"{source}:{start}-{end}; fix the cited range")
    return f"the excerpt does not occur verbatim in {source}:{first}-{last}; {copy_hint}"


# --- K11 ------------------------------------------------------------------------------------


def _in_history(view: KBView, raw: str) -> bool:
    parts = PurePosixPath(raw.replace("\\", "/")).parts
    parts = parts[1:] if parts[:1] == (".",) else parts
    return any(parts[:len(d.split("/"))] == tuple(d.split("/")) for d in view.cfg.history_dirs if d)


def k11_reported(view: KBView) -> list[Issue]:
    """A reported claim quotes the retired document that states it; nothing else rests on one."""
    cfg = view.cfg
    reported = cfg.reported_label if cfg.reported_label in cfg.labels else ""
    labels = {f.file_id: f.meta.get("label") for f in _parsed(view)}
    history = " or ".join(f"{d}/" for d in cfg.history_dirs if d) or "a history folder"
    issues: list[Issue] = []
    for f in _parsed(view):
        is_reported = bool(reported) and f.meta.get("label") == reported
        if is_reported:
            sources = [m.group("path").strip() for m in map(VERBATIM_RE.match, f.body_lines) if m]
            if not any(_in_history(view, s) for s in sources):
                issues.append(_issue(
                    "K11", f, f.key_line("label"),
                    f"a {reported} finding quotes the retired document that states its claim: add a "
                    f"<!-- verbatim: {history}...:FIRST-LAST --> excerpt of those lines, copied exactly",
                ))
            continue
        evidence = f.meta.get("evidence")
        if isinstance(evidence, list):
            for i, item in enumerate(evidence):
                if isinstance(item, str) and _in_history(view, item):
                    hint = f", or label the finding {reported} and quote the lines" if reported else ""
                    issues.append(_issue(
                        "K11", f, f.item_line("evidence", i),
                        f"evidence path {item} is a retired document, not evidence: cite what the claim "
                        f"rests on{hint}",
                    ))
        depends = f.meta.get("depends_on")
        if reported and isinstance(depends, dict):
            for key in depends:
                if isinstance(key, str) and labels.get(key) == reported:
                    issues.append(_issue(
                        "K11", f, _mapping_key_line(f, depends, key, "depends_on"),
                        f"depends_on names {key}, which is {reported}: nothing in the tree reproduces it, so "
                        f"this claim cannot rest on it. Reproduce {key} and relabel it, or label this finding "
                        f"{reported}",
                    ))
    return issues


# --- entry point ----------------------------------------------------------------------------


def validate(view: KBView, focus: frozenset[str] = frozenset()) -> list[Issue]:
    """Run every rule. `focus` names findings being written, so K3 and K9 address them."""
    issues = (
        k1_schema(view)
        + k2_references(view)
        + k3_suspect(view, focus)
        + k4_history_language(view)
        + k5_id_near_history(view)
        + k6_length(view)
        + k7_index(view)
        + k8_stray_files(view)
        + k9_duplicates(view, focus)
        + k10_verbatim(view)
        + k11_reported(view)
    )
    return sorted(set(issues), key=lambda i: (i.path, i.line, int(i.code[1:]), i.message))
