"""The Jev check (SPEC §6; M5): candidate pairs, quantity comparison and the §6.4 decision policy.

Candidate selection (§6.1), scope gating and quantity comparison (§6.3) are code. Similar findings
are scored by a local ollama embedding when one is available and by BM25 otherwise, with one
mechanism for the whole check (§6.1 Choosing, M6.7). Jev is asked only the questions an enabled
verdict reads: a KB whose `[jev.thresholds]` enables no verdict sends nothing, and only §6.3
applies. A verdict that the committed resolutions (kblam.resolutions.jsonl, §6.4) cover is
suppressed. Each check is logged to `.kblam/checks.jsonl` (IDs, fingerprints and verdicts; never
finding text).
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath

from kblam import jev_prompts, resolutions
from kblam.config import CONFIG_NAME, Config, ConfigError
from kblam.embed import EmbedUnavailable, Embedder, probe, title_and_claim
from kblam.finding import Finding, fingerprint, id_number, plain_data
from kblam.jev import (
    CACHE_NAME,
    CallLog,
    JevClient,
    JevSettings,
    JevUnavailable,
    PairCache,
    Side,
    cache_key,
    jev_settings,
)
from kblam.view import KBView

CHECK_LOG_NAME = "checks.jsonl"

SAME_FACT = "same_fact"
RESTATES = "restates_and_extends"
CONFLICT = "cannot_both_be_true"
REVISION = "revision"
QUANTITY_CONFLICT = "quantity_conflict"
LOW_CONFIDENCE = "low_confidence"
RELATION_VERDICTS = (SAME_FACT, CONFLICT, RESTATES)  # relation options a threshold can make fire
MODES = ("reject", "review")

# §6.1 similarity: BM25 over each finding's title and claim paragraph.
BM25_K1 = 1.2
BM25_B = 0.75
TOKEN_RE = re.compile(r"[\w./-]+")  # \w: letters, digits and _ (Unicode)
TRAILING_PUNCTUATION = ".-/"
# Lucene's English stop set (EnglishAnalyzer.ENGLISH_STOP_WORDS_SET): small, fixed and project-neutral.
STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in", "into", "is", "it",
    "no", "not", "of", "on", "or", "such", "that", "the", "their", "then", "there", "these", "they",
    "this", "to", "was", "will", "with",
})


# --- §6.4 policy ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Threshold:
    mode: str
    p: float | None = None           # relation verdicts
    confidence: float | None = None
    noul: float | None = None        # revision


@dataclass(frozen=True)
class Policy:
    """`[jev.thresholds]`. A verdict with no entry is disabled. Each question's thresholds hold for the
    wording whose id they record (§6.2, §6.4): relation_prompt_id and revision_prompt_id, or, in a table
    written before M6.10, the combined prompt_id, which stands for both questions while it is current."""

    served_model: str | None
    prompt_id: str | None                   # the combined id; None when each question's id is recorded
    verdicts: dict[str, Threshold]
    low_confidence_review: float | None
    relation_prompt_id: str | None = None
    revision_prompt_id: str | None = None

    @property
    def asks_relation(self) -> bool:
        return any(v in self.verdicts for v in RELATION_VERDICTS) or self.low_confidence_review is not None

    @property
    def asks_revision(self) -> bool:
        return REVISION in self.verdicts

    @property
    def enabled(self) -> bool:
        return self.asks_relation or self.asks_revision


def _unit_number(where: str, value) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        raise ConfigError(f"{CONFIG_NAME}: [jev.thresholds] {where} must be a number from 0 to 1, got {value!r}")
    return float(value)


# §6.2: the id of each question's wording, as `[jev.thresholds]` records it; (key, question, the default's id)
QUESTION_IDS = (("relation_prompt_id", jev_prompts.RELATION_KEY, "6d79e4e0e409"),
                ("revision_prompt_id", jev_prompts.REVISION_KEY, "d9a34c823fe3"))


def parse_policy(thresholds: dict) -> Policy:
    """SPEC §9 `[jev.thresholds]`: served_model, the prompt ids, one inline table per verdict, and
    optional low_confidence_review. The prompt ids are relation_prompt_id and revision_prompt_id, one
    per question (§6.2), or the combined prompt_id recorded before M6.10, but not both styles. An
    enabled verdict needs served_model and the id of every question an enabled verdict reads."""
    if "prompt_version" in thresholds:
        raise ConfigError(f"{CONFIG_NAME}: [jev.thresholds] prompt_version is gone: "
                          f"{jev_prompts.PROMPT_MOVED}")
    allowed = ("served_model", "relation_prompt_id", "revision_prompt_id", "prompt_id", "low_confidence_review",
               *RELATION_VERDICTS, REVISION)
    unknown = sorted(set(thresholds) - set(allowed))
    if unknown:
        raise ConfigError(f"{CONFIG_NAME}: unknown [jev.thresholds] key(s) {', '.join(unknown)}; "
                          f"allowed: {', '.join(allowed)}")
    per_question = [key for key, _question, _default in QUESTION_IDS if key in thresholds]
    if "prompt_id" in thresholds and per_question:
        raise ConfigError(f"{CONFIG_NAME}: [jev.thresholds] records both prompt_id and "
                          f"{' and '.join(per_question)}. prompt_id is the id of the whole prompt, recorded before "
                          f"each question had its own; record relation_prompt_id and revision_prompt_id (kblam "
                          f"prompt-id prints them) and delete prompt_id")
    verdicts = {}
    for name in (*RELATION_VERDICTS, REVISION):
        if name not in thresholds:
            continue
        entry = thresholds[name]
        need = ("noul", "mode") if name == REVISION else ("p", "confidence", "mode")
        if not isinstance(entry, dict) or set(entry) != set(need):
            shape = ("{ noul = 0.75, mode = \"reject\" }" if name == REVISION
                     else "{ p = 0.67, confidence = 0.59, mode = \"reject\" }")
            raise ConfigError(f"{CONFIG_NAME}: [jev.thresholds] {name} must be {shape}")
        if entry["mode"] not in MODES:
            raise ConfigError(f"{CONFIG_NAME}: [jev.thresholds] {name} mode must be one of "
                              f"{', '.join(MODES)}, got {entry['mode']!r}")
        numbers = {k: _unit_number(f"{name}.{k}", entry[k]) for k in need if k != "mode"}
        verdicts[name] = Threshold(mode=entry["mode"], **numbers)
    low = thresholds.get("low_confidence_review")
    if low is not None:
        low = _unit_number("low_confidence_review", low)

    served, prompt = thresholds.get("served_model"), thresholds.get("prompt_id")
    policy = Policy(served, prompt, verdicts, low, thresholds.get("relation_prompt_id"),
                    thresholds.get("revision_prompt_id"))
    if policy.enabled:
        if not isinstance(served, str) or not served:
            raise ConfigError(f"{CONFIG_NAME}: [jev.thresholds] served_model must name the served model "
                              f"the thresholds were calibrated on, e.g. \"typesafe/jev-1.13-20260917\"")
        if "prompt_id" in thresholds:  # the combined id stands for both questions
            if not isinstance(prompt, str) or not prompt:
                raise ConfigError(f"{CONFIG_NAME}: [jev.thresholds] prompt_id must be the id of the prompt these "
                                  f"thresholds were calibrated on, as kblam prompt-id prints it, "
                                  f"e.g. \"4dda2f781f12\"")
            return policy
        enabled = {jev_prompts.RELATION_KEY: policy.asks_relation, jev_prompts.REVISION_KEY: policy.asks_revision}
        for key, question, default in QUESTION_IDS:
            value = thresholds.get(key)
            if enabled[question] and (not isinstance(value, str) or not value):
                raise ConfigError(f"{CONFIG_NAME}: [jev.thresholds] {key} must be the id of the prompt's "
                                  f"{question} question these thresholds were calibrated on, as kblam prompt-id "
                                  f"prints it (its \"{question}:\" line), e.g. \"{default}\"")
    return policy


# --- §6.1 candidates --------------------------------------------------------------------------


def _meta(finding: Finding) -> dict:
    return finding.meta if isinstance(finding.meta, dict) else {}


def scope_parts(values, separator: str = "/") -> frozenset[str]:
    """A scope value containing `separator` stands for each of its parts, and with "" no value splits
    ([kb] scope_separator, SPEC §4, §9)."""
    parts = set()
    for value in values or ():
        pieces = str(value).split(separator) if separator else [str(value)]
        parts.update(p.strip() for p in pieces if p.strip())
    return frozenset(parts)


def scopes_overlap(a, b, separator: str = "/", wildcard: str = "any") -> bool:
    """`wildcard` overlaps every scope, and with "" none does; otherwise the part sets must intersect
    ([kb] scope_separator and scope_wildcard, SPEC §6.1 Scope gating, §9). A check passes its view's
    configuration; the defaults are [kb]'s."""
    pa, pb = scope_parts(a, separator), scope_parts(b, separator)
    if wildcard and (wildcard in pa or wildcard in pb):
        return True
    return bool(pa & pb)


def _scope(finding: Finding) -> list:
    scope = plain_data(_meta(finding).get("scope"))
    if isinstance(scope, str):
        return [scope]
    return scope if isinstance(scope, list) else []


def _hex(digits: str) -> str:
    return f"0x{int(digits, 16):X}"


def _anchor(text: str) -> str:
    text = " ".join(text.split())
    match = re.fullmatch(r"0[xX]([0-9A-Fa-f]+)", text)
    return _hex(match.group(1)) if match else text.casefold()


def anchors_of(finding: Finding) -> frozenset[str]:
    """Declared anchors only. Hex is compared by value (0x001a2b3c == 0x1A2B3C); other anchors
    case-insensitively with whitespace runs collapsed."""
    declared = _meta(finding).get("anchors")
    if not isinstance(declared, list):
        return frozenset()
    return frozenset(_anchor(a) for a in declared if isinstance(a, str) and a.strip())


def _evidence(finding: Finding) -> list[PurePosixPath]:
    items = _meta(finding).get("evidence")
    if not isinstance(items, list):
        return []
    paths = []
    for item in items:
        if isinstance(item, str) and item.strip():
            paths.append(PurePosixPath(item.strip().replace("\\", "/").rstrip("/")))
    return paths


def _shared_evidence(a: list[PurePosixPath], b: list[PurePosixPath]) -> list[str]:
    """Evidence paths of `a` that equal, contain or sit inside one of `b`'s."""
    return sorted({p.as_posix() for p in a for q in b if p == q or p in q.parents or q in p.parents})


def _depends(finding: Finding) -> set[str]:
    depends = _meta(finding).get("depends_on")
    return {str(k) for k in depends} if isinstance(depends, dict) else set()


def tokens(text: str) -> list[str]:
    """Lowercased runs of letters, digits, `_`, `.`, `-` and `/`, trailing `.`, `-` and `/` stripped,
    stop words dropped, no stemming: `0x1A2B3C`, `MX-200` and `foo_bar()` stay whole."""
    out = []
    for match in TOKEN_RE.finditer(text.lower()):
        token = match.group(0).rstrip(TRAILING_PUNCTUATION)
        if token and token not in STOP_WORDS:
            out.append(token)
    return out


def _document(finding: Finding) -> list[str]:
    return tokens(title_and_claim(finding))


class Corpus:
    """BM25 statistics over every readable finding in a view (the whole KB, every topic)."""

    def __init__(self, view: KBView):
        self.view = view
        self.postings: dict[str, list[tuple[str, int]]] = {}   # token -> [(finding path, term frequency)]
        self.lengths: dict[str, int] = {}
        for finding in view.findings:
            if not finding.ok or not finding.file_id:
                continue
            document = _document(finding)
            self.lengths[finding.path] = len(document)
            for token, count in sorted(Counter(document).items()):
                self.postings.setdefault(token, []).append((finding.path, count))
        self.average = sum(self.lengths.values()) / len(self.lengths) if self.lengths else 0.0

    def scores(self, query: list[str]) -> dict[str, float]:
        """BM25 of `query` against each document, by finding path; absent means 0. Each query token
        counts once. IDF is ln(1 + (N - n + 0.5) / (n + 0.5)), positive for every token that occurs,
        so a document scores 0 exactly when it shares no token with the query. Tokens are summed in
        sorted order, so equal documents score exactly equal."""
        total = len(self.lengths)
        scores: dict[str, float] = {}
        for token in sorted(set(query)):
            postings = self.postings.get(token)
            if not postings:
                continue
            idf = math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
            for path, count in postings:
                norm = 1 - BM25_B + BM25_B * self.lengths[path] / self.average
                scores[path] = scores.get(path, 0.0) + idf * count * (BM25_K1 + 1) / (count + BM25_K1 * norm)
        return scores


@dataclass(frozen=True)
class Similarity:
    """The similar ranking for one check (SPEC §6.1): every other finding's raw score by path, the
    topic bonus to apply, and the label `checks.jsonl` records.

    One mechanism scores the whole check. Embeddings carry no topic bonus and exclude nobody — a
    paraphrase that shares no token is exactly what they are for. BM25 adds `topic_bonus` times the
    top score to same-topic findings and never makes an unlinked finding with no shared token a
    candidate."""

    label: str                  # "embed <model>" | "bm25 (<reason>)" | "bm25"
    scores: dict[str, float]    # finding path -> raw score; absent means 0
    topic_bonus: float
    embedding: bool


@dataclass(frozen=True)
class Candidate:
    finding: Finding
    finding_id: str
    fingerprint: str
    depends: bool               # a depends_on edge in either direction
    anchors: tuple[str, ...]    # shared declared anchors
    evidence: tuple[str, ...]   # shared evidence paths
    similar: float              # the mechanism's raw score: cosine, or BM25 (0 = no shared token)
    bonus: float                # topic_bonus x the top score, for a similar finding in the same topic
    link_bonus: float           # link_bonus x the top score, once, for a shared anchor or evidence path
    scope_overlap: bool
    embedding: bool             # scored by an embedding, so the reason names the cosine

    @property
    def linked(self) -> bool:
        return self.depends or bool(self.anchors) or bool(self.evidence)

    @property
    def score(self) -> float:
        return self.similar + self.bonus + self.link_bonus

    def reasons(self) -> list[str]:
        """The links, then, for every candidate but a dependency, the score and its terms (zero
        terms omitted; with no bonus, just the raw score)."""
        out = ["depends_on"] if self.depends else []
        out += [f"anchor {a}" for a in self.anchors] + [f"evidence {e}" for e in self.evidence]
        if self.depends:
            return out
        if not self.bonus and not self.link_bonus:
            return out + [f"similar {self.similar:.4f}" + (" (embed)" if self.embedding else "")]
        terms = [("embed" if self.embedding else "bm25", self.similar), ("topic", self.bonus),
                 ("link", self.link_bonus)]
        detail = " + ".join(f"{name} {value:.4f}" for name, value in terms if value)
        return out + [f"similar {self.score:.4f} ({detail})"]


@dataclass
class Selection:
    candidates: list[Candidate] = field(default_factory=list)       # scopes overlap, within the budget
    over_budget: list[Candidate] = field(default_factory=list)      # scopes overlap, ranked past the budget
    different_scope: list[Candidate] = field(default_factory=list)  # disjoint scopes: Jev is not asked


def _rank(c: Candidate) -> tuple:
    # SPEC §6.1 (M6.8): dependencies first, then every other finding; within each, by score with its
    # bonuses, and by ID among equal scores.
    return (not c.depends, -c.score, id_number(c.finding_id))


def select_candidates(view: KBView, finding: Finding, max_candidates: int, topic_bonus: float,
                      similarity: Similarity | None = None, *, link_bonus: float) -> Selection:
    """The dependencies, then the other findings by score, ranked (SPEC §6.1). A shared anchor or
    evidence path adds `link_bonus` times the top raw score, once. `similarity` is the check's one
    mechanism; without one this scores BM25 over `view`. A finding is never its own candidate, and in
    BM25 a finding with no link and no shared token is never one."""
    if similarity is None:
        similarity = Similarity("bm25", Corpus(view).scores(_document(finding)), topic_bonus, embedding=False)
    topic = _meta(finding).get("topic")
    anchors = anchors_of(finding)
    evidence = _evidence(finding)
    depends = _depends(finding)
    scope = _scope(finding)
    raw = similarity.scores
    others = [o for o in view.findings if o.ok and o.file_id and o.file_id != finding.file_id]
    top = max((raw.get(o.path, 0.0) for o in others), default=0.0)
    matched = []
    for other in others:
        similar = raw.get(other.path, 0.0)
        same_topic = topic is not None and _meta(other).get("topic") == topic
        shared_anchors = tuple(sorted(anchors & anchors_of(other)))
        shared_evidence = tuple(_shared_evidence(evidence, _evidence(other)))
        candidate = Candidate(
            finding=other,
            finding_id=other.file_id,
            fingerprint=fingerprint(other),
            depends=other.file_id in depends or finding.file_id in _depends(other),
            anchors=shared_anchors,
            evidence=shared_evidence,
            similar=similar,
            bonus=similarity.topic_bonus * top if same_topic and similar > 0 else 0.0,
            link_bonus=link_bonus * top if shared_anchors or shared_evidence else 0.0,
            scope_overlap=scopes_overlap(scope, _scope(other), view.cfg.scope_separator,
                                         view.cfg.scope_wildcard),
            embedding=similarity.embedding,
        )
        # BM25 excludes a finding that shares no token; an embedding excludes none (SPEC §6.1)
        if candidate.linked or similarity.embedding or similar > 0:
            matched.append(candidate)
    matched.sort(key=_rank)
    overlapping = [c for c in matched if c.scope_overlap]
    return Selection(candidates=overlapping[:max_candidates], over_budget=overlapping[max_candidates:],
                     different_scope=[c for c in matched if not c.scope_overlap])


# --- §6.3 quantities --------------------------------------------------------------------------


@dataclass(frozen=True)
class QuantityConflict:
    name: str
    existing_value: float
    existing_unit: str
    new_value: float
    new_unit: str


def _quantities(finding: Finding) -> list[tuple[str, str, float, str]]:
    """(normalised name, name as written, value, unit) for each well-formed quantity."""
    items = _meta(finding).get("quantities")
    out = []
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        name, value = item.get("name"), item.get("value")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        unit = item.get("unit")
        unit = " ".join(unit.split()) if isinstance(unit, str) else ""
        out.append((" ".join(name.split()).casefold(), " ".join(name.split()), float(value), unit))
    return out


def _differ(a: float, b: float, tolerance: float) -> bool:
    return not math.isclose(a, b, rel_tol=tolerance, abs_tol=0.0) if tolerance else a != b


def quantity_conflicts(existing: Finding, new: Finding, tolerance: float) -> list[QuantityConflict]:
    """Same-named quantities whose units differ or whose values differ by more than `tolerance`
    (relative). Names match case-insensitively with whitespace runs collapsed."""
    conflicts = []
    theirs = _quantities(existing)
    for key, name, value, unit in _quantities(new):
        for other_key, _other_name, other_value, other_unit in theirs:
            if key == other_key and (unit != other_unit or _differ(value, other_value, tolerance)):
                conflicts.append(QuantityConflict(name, other_value, other_unit, value, unit))
    return conflicts


# --- verdicts ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """One verdict that fired. `mode` is the effective one (after a model/prompt mismatch downgrade).
    Each side is named by its ID, its fingerprint, which items carry, and its state hash (§6.5), which
    resolutions are keyed by."""

    verdict: str
    mode: str
    new_id: str
    new_fp: str
    existing_id: str | None     # None for revision
    existing_fp: str | None
    message: str
    winner: str | None = None
    p: float | None = None
    confidence: float | None = None
    noul: float | None = None
    new_state: str | None = None
    existing_state: str | None = None   # None for revision

    def scores(self) -> str:
        parts = []
        if self.p is not None:
            parts.append(f"p {self.p:.2f}")
        if self.confidence is not None:
            parts.append(f"confidence {self.confidence:.2f}")
        if self.noul is not None:
            parts.append(f"noul {self.noul:.2f}")
        return ", ".join(parts)

    def describe(self) -> str:
        """`<verdict> <new> vs <existing> (scores): message`, without the mode."""
        who = self.new_id + (f" vs {self.existing_id}" if self.existing_id else "")
        scores = self.scores()
        return f"{self.verdict} {who}" + (f" ({scores})" if scores else "") + f": {self.message}"


def _format_value(value: float, unit: str) -> str:
    text = f"{value:g}"
    return f"{text} {unit}" if unit else text


def _message(verdict: str, existing: str | None, *, p: float | None = None, winner: str | None = None,
             confidence: float | None = None, quantity: QuantityConflict | None = None) -> str:
    if verdict == SAME_FACT:
        return f"{existing} already states this; edit {existing} instead (kblam edit {existing})"
    if verdict == CONFLICT:
        return (f"probable conflict with {existing} (p={p:.2f}): rewrite {existing} in place so it states the "
                f"current fact, or correct this finding")
    if verdict == RESTATES:
        return f"this restates {existing} and adds detail; move the detail into {existing} (kblam edit {existing})"
    if verdict == REVISION:
        return "this reads as a correction; rewrite the original finding instead"
    if verdict == LOW_CONFIDENCE:
        return f"Jev could not place this against {existing} (winner {winner}, confidence {confidence:.2f}); read both"
    q = quantity
    return (f"{existing} gives {q.name} = {_format_value(q.existing_value, q.existing_unit)} and this finding "
            f"gives {_format_value(q.new_value, q.new_unit)}; rewrite {existing} in place, correct this finding, "
            f"or rename the quantity if they measure different things")


@dataclass
class CheckResult:
    finding_id: str
    fingerprint: str
    command: str
    similarity: str                                           # checks.jsonl §6.1: "embed <model>" or "bm25 (<reason>)"
    candidates: list[str] = field(default_factory=list)       # finding IDs asked about (or compared)
    over_budget: list[str] = field(default_factory=list)
    different_scope: list[str] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)     # fired and not resolved as distinct
    suppressed: list[Verdict] = field(default_factory=list)   # fired, but a resolution covers it (§6.4)
    unavailable: list[str] = field(default_factory=list)      # questions Jev could not answer, with the reason
    mismatch: str | None = None                               # re-calibration warning (§6.4)
    jev_enabled: bool = True

    @property
    def rejected(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.mode == "reject"]

    @property
    def complete(self) -> bool:
        return not self.unavailable


# --- the checker ------------------------------------------------------------------------------


# The checker's answers are keyed, like the pair cache, by the sides' state hashes (§6.5).
def _relation_key(existing_state: str, new_state: str) -> tuple:
    return ("relation", existing_state, new_state)


def _revision_key(new_state: str) -> tuple:
    return ("revision", new_state)


def _question(verdict: str) -> str:
    """The Jev question a Jev verdict answers (§6.2): `revision` its own, every other the relation's."""
    return jev_prompts.REVISION_KEY if verdict == REVISION else jev_prompts.RELATION_KEY


class Checker:
    """Runs §6 checks. Answers are kept for the checker's life, so put's under-lock pass reuses its
    pre-lock answers even where the pair cache would not (it never stores a mismatched model's answer).
    Use as a context manager, or call close().

    The similar ranking's mechanism is chosen once, on the first selection, and holds for every pair
    of every check this checker runs: an ollama embedding when it is there, otherwise BM25 (§6.1
    Choosing). So a put embeds before it takes the lock and, under the lock, only what the tree gained
    meanwhile — the cache decides that, not a second decision."""

    def __init__(self, cfg: Config, client_factory=None):
        self.cfg = cfg
        self.settings: JevSettings = jev_settings(cfg)
        self.policy = parse_policy(self.settings.thresholds)
        self.cache = PairCache(cfg.state_dir / CACHE_NAME)
        self.log = CallLog(cfg.state_dir / CHECK_LOG_NAME)
        self._factory = client_factory or JevClient
        self._client: JevClient | None = None
        self._answers: dict[tuple, object] = {}   # key -> RelationResult | RevisionResult | JevUnavailable
        self._corpus: Corpus | None = None        # BM25 statistics, for the last view selected against
        self._embedder: Embedder | None = None    # set once the embedding mechanism is chosen
        self._embedding: bool | None = None       # None until the choice is made
        self._bm25_reason: str | None = None      # why BM25, when the choice was a fallback

    def __enter__(self) -> Checker:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def client(self) -> JevClient:
        if self._client is None:
            self._client = self._factory(self.cfg)
        return self._client

    # --- asking ---------------------------------------------------------------------------------

    def _ask(self, relation_pairs: list[tuple[Side, Side]], revisions: list[Side]) -> None:
        """Ask every question this checker has no answer to yet, once per state: sides that Jev sees
        alike share an answer (§6.5). A failed question keeps its JevUnavailable, so put does not retry
        it under the lock; `check --pending` retries it."""
        pairs: dict[tuple, tuple[Side, Side]] = {}
        for e, n in relation_pairs:
            key = _relation_key(e.state_hash, n.state_hash)
            if key not in self._answers:
                pairs.setdefault(key, (e, n))
        sides: dict[tuple, Side] = {}
        for n in revisions:
            key = _revision_key(n.state_hash)
            if key not in self._answers:
                sides.setdefault(key, n)
        if not pairs and not sides:
            return
        client = self.client()
        if pairs:
            for key, got in zip(pairs, client.ask_relations(list(pairs.values()), workers=self.settings.workers)):
                self._answers[key] = got
        if sides:
            for key, got in zip(sides, client.ask_revisions(list(sides.values()), workers=self.settings.workers)):
                self._answers[key] = got

    def _questions(self, finding: Finding, selection: Selection) -> tuple[list[tuple[Side, Side]], list[Side]]:
        new = Side.of(finding)
        pairs = [(Side.of(c.finding), new) for c in selection.candidates] if self.policy.asks_relation else []
        return pairs, [new] if self.policy.asks_revision else []

    def similarity_label(self) -> str:
        """The `similarity` field of `checks.jsonl` for this checker's checks (§6.1)."""
        self._choose()
        if self._embedding:
            return f"embed {self.settings.embedding_model}"
        return f"bm25 ({self._bm25_reason})" if self._bm25_reason else "bm25"

    def _choose(self) -> None:
        """Choose the mechanism once (§6.1 Choosing): the embedding when `embedding_model` names a
        model ollama has, otherwise BM25. A failure prints one line naming the reason; `""` is a
        configuration, not a failure, so it is quiet. Neither is an error, and neither leaves a write
        unchecked."""
        if self._embedding is not None:
            return
        self._embedding = False
        name = self.settings.embedding_model
        if not name:
            return
        try:
            model = probe(self.settings.ollama_url, name)
        except EmbedUnavailable as exc:
            self._fall_back(exc)
            return
        self._embedder, self._embedding = Embedder(self.cfg, self.settings, model), True

    def _fall_back(self, exc: EmbedUnavailable) -> None:
        """A probe or embed request that failed sends the whole check back to BM25."""
        self._embedder, self._embedding, self._bm25_reason = None, False, str(exc)
        print(f"similarity: BM25 ({exc})", file=sys.stderr)

    def _similarity(self, view: KBView, finding: Finding) -> Similarity:
        """The similar ranking of `finding` against `view`, scored by this checker's one mechanism."""
        self._choose()
        if self._embedding:
            try:
                scores = self._embedder.scores(view, finding)
            except EmbedUnavailable as exc:
                self._fall_back(exc)
            else:
                # no topic bonus and no zero-score exclusion: every finding is eligible (§6.1)
                return Similarity(f"embed {self.settings.embedding_model}", scores, 0.0, embedding=True)
        if self._corpus is None or self._corpus.view is not view:
            self._corpus = Corpus(view)
        return Similarity(self.similarity_label(), self._corpus.scores(_document(finding)),
                          self.settings.topic_bonus, embedding=False)

    def select(self, view: KBView, finding: Finding) -> Selection:
        """§6.1 candidates: the dependencies, then the other findings by score with bonuses."""
        return select_candidates(view, finding, self.settings.max_candidates, self.settings.topic_bonus,
                                 self._similarity(view, finding), link_bonus=self.settings.link_bonus)

    def prefetch(self, view: KBView, finding: Finding) -> None:
        """Ask the questions a check of `finding` against `view` needs, without deciding (put asks
        before it takes the lock)."""
        if self.policy.enabled:
            self._ask(*self._questions(finding, self.select(view, finding)))

    # --- deciding -------------------------------------------------------------------------------

    def _mismatch(self, served: set[str], asked: set[str]) -> tuple[set[str], str | None]:
        """§6.4 Model or prompt mismatch, for a check that used answers to the questions `asked`
        ("relation", "revision") from the models `served`: the questions whose verdicts are demoted to
        review, and the warning naming what differs. A served model other than the thresholds' demotes
        every Jev verdict of the check; a question whose wording has another id than the one recorded for
        it demotes only that question's (§6.2). A combined prompt_id, recorded before M6.10, vouches for
        both questions while it is the current prompt's id, and for neither once it is not."""
        policy, settings = self.policy, self.settings
        if not asked:
            return set(), None
        problems, demoted = [], set()
        if policy.prompt_id is not None:
            if policy.prompt_id != settings.prompt_id:
                problems.append(f"prompt {settings.prompt_id} (thresholds: {policy.prompt_id})")
                demoted |= asked
        else:
            for question, current, recorded in (
                    (jev_prompts.RELATION_KEY, settings.relation_prompt_id, policy.relation_prompt_id),
                    (jev_prompts.REVISION_KEY, settings.revision_prompt_id, policy.revision_prompt_id)):
                if question in asked and current != recorded:
                    problems.append(f"{question} prompt {current} (thresholds: {recorded})")
                    demoted.add(question)
        other = sorted(s for s in served if s != policy.served_model)
        if other:
            problems.append(f"served model {', '.join(other)} (thresholds: {policy.served_model})")
            demoted |= asked
        if not problems:
            return set(), None
        if demoted == asked:
            effect = "No Jev verdict of this check rejects: every one that fires is a review item"
        elif jev_prompts.RELATION_KEY in demoted:
            effect = (f"No verdict of the relation question ({', '.join(RELATION_VERDICTS)}) rejects: every one "
                      f"that fires is a review item. The revision question's thresholds still apply")
        else:
            effect = ("No revision verdict rejects: one that fires is a review item. The relation question's "
                      "thresholds still apply")
        return demoted, (f"the [jev.thresholds] in {CONFIG_NAME} were calibrated on {policy.served_model}, but "
                         f"this check used {' and '.join(problems)}. {effect}. Re-calibrate (SPEC §10) and "
                         f"update [jev.thresholds]")

    def _relation_verdicts(self, existing: Side, new: Side, answer) -> list[Verdict]:
        winner, confidence = answer.winner, answer.confidence
        p = answer.probabilities.get(winner, 0.0)
        threshold = self.policy.verdicts.get(winner) if winner in RELATION_VERDICTS else None
        if threshold and p >= threshold.p and confidence >= threshold.confidence:
            return [Verdict(winner, threshold.mode, new.finding_id, new.fingerprint, existing.finding_id,
                            existing.fingerprint, _message(winner, existing.finding_id, p=p),
                            winner=winner, p=p, confidence=confidence,
                            new_state=new.state_hash, existing_state=existing.state_hash)]
        return []

    def _low_confidence(self, existing: Side, new: Side, answer) -> list[Verdict]:
        low = self.policy.low_confidence_review
        if low is None or answer.confidence >= low:
            return []
        p = answer.probabilities.get(answer.winner, 0.0)
        return [Verdict(LOW_CONFIDENCE, "review", new.finding_id, new.fingerprint, existing.finding_id,
                        existing.fingerprint,
                        _message(LOW_CONFIDENCE, existing.finding_id, winner=answer.winner,
                                 confidence=answer.confidence),
                        winner=answer.winner, p=p, confidence=answer.confidence,
                        new_state=new.state_hash, existing_state=existing.state_hash)]

    def _resolved(self, verdict: Verdict, resolved: dict) -> bool:
        """§6.4 Resolutions: a verdict on a pair is suppressed by a `distinct` resolution on the unordered
        pair of (ID, state hash) sides, a revision verdict by a `not_revision` one on its side; or by a
        resolution recorded before M6.10, a row of pairs.sqlite on (ID, fingerprint) sides, kept until
        `kblam upgrade` moves it. No resolution covers a quantity_conflict (§6.3). `resolved` is the
        committed log, by resolutions.key."""
        if verdict.verdict == QUANTITY_CONFLICT:
            return False
        new = (verdict.new_id, verdict.new_state)
        if verdict.existing_id is None:
            committed = resolutions.key(resolutions.NOT_REVISION, [new])
            other = None
        else:
            committed = resolutions.key(resolutions.DISTINCT, [new, (verdict.existing_id, verdict.existing_state)])
            other = (verdict.existing_id, verdict.existing_fp)
        if committed in resolved:
            return True
        return self.cache.distinct_reason((verdict.new_id, verdict.new_fp), other) is not None

    def _decide(self, result: CheckResult, new: Side, pairs: list[tuple[Finding, list[QuantityConflict]]],
                ask_revision: bool, resolved: dict) -> None:
        """Fill result.verdicts / suppressed / unavailable / mismatch from the answers held. `resolved` is
        the committed resolutions, by resolutions.key."""
        served: set[str] = set()
        asked: set[str] = set()    # the questions whose answers this check used
        jev_fired: list[Verdict] = []
        fired: list[Verdict] = []
        for existing_finding, conflicts in pairs:
            existing = Side.of(existing_finding)
            on_pair = [Verdict(QUANTITY_CONFLICT, "reject", new.finding_id, new.fingerprint, existing.finding_id,
                               existing.fingerprint, _message(QUANTITY_CONFLICT, existing.finding_id, quantity=q),
                               new_state=new.state_hash, existing_state=existing.state_hash)
                       for q in conflicts]
            answer = self._answers.get(_relation_key(existing.state_hash, new.state_hash))
            if isinstance(answer, JevUnavailable):
                result.unavailable.append(f"relation {new.finding_id} vs {existing.finding_id}: {answer}")
            elif answer is not None:
                served.add(answer.call.served_model)
                asked.add(jev_prompts.RELATION_KEY)
                relation = self._relation_verdicts(existing, new, answer)
                if not relation and not on_pair:
                    relation = self._low_confidence(existing, new, answer)
                jev_fired += relation
            fired += on_pair
        if ask_revision:
            answer = self._answers.get(_revision_key(new.state_hash))
            if isinstance(answer, JevUnavailable):
                result.unavailable.append(f"revision {new.finding_id}: {answer}")
            elif answer is not None:
                served.add(answer.call.served_model)
                asked.add(jev_prompts.REVISION_KEY)
                threshold = self.policy.verdicts[REVISION]
                if answer.noul >= threshold.noul:
                    jev_fired.append(Verdict(REVISION, threshold.mode, new.finding_id, new.fingerprint, None, None,
                                             _message(REVISION, None), noul=answer.noul,
                                             new_state=new.state_hash))

        demoted, result.mismatch = self._mismatch(served, asked)
        jev_fired = [replace(v, mode="review") if _question(v.verdict) in demoted else v for v in jev_fired]
        for verdict in fired + jev_fired:
            (result.suppressed if self._resolved(verdict, resolved) else result.verdicts).append(verdict)

    def check(self, view: KBView, finding: Finding, command: str) -> CheckResult:
        """Candidates, quantity comparison, Jev questions and the §6.4 decision for `finding` against
        the other findings in `view`. Reject verdicts are returned as such; callers that cannot
        refuse a write (check, audit) record them as review items."""
        resolved = resolutions.by_key(resolutions.load(self.cfg))  # a damaged log stops the check before Jev
        selection = self.select(view, finding)
        new = Side.of(finding)
        result = CheckResult(finding.file_id, new.fingerprint, command,
                             similarity=self.similarity_label(),
                             candidates=[c.finding_id for c in selection.candidates],
                             over_budget=[c.finding_id for c in selection.over_budget],
                             different_scope=[c.finding_id for c in selection.different_scope],
                             jev_enabled=self.policy.enabled)
        if self.policy.enabled:
            self._ask(*self._questions(finding, selection))
        tolerance = self.settings.quantity_rel_tolerance
        pairs = [(c.finding, quantity_conflicts(c.finding, finding, tolerance)) for c in selection.candidates]
        asked = {c.finding_id for c in selection.candidates}
        scope = _scope(finding)
        for other in view.findings:  # §6.3 is code: every finding whose scope overlaps, candidate or not
            if (not other.ok or not other.file_id or other.file_id == finding.file_id or other.file_id in asked
                    or not scopes_overlap(scope, _scope(other), view.cfg.scope_separator,
                                          view.cfg.scope_wildcard)):
                continue
            conflicts = quantity_conflicts(other, finding, tolerance)
            if conflicts:
                result.candidates.append(other.file_id)
                pairs.append((other, conflicts))
        self._decide(result, new, pairs, self.policy.asks_revision, resolved)
        self._log(result, selection)
        return result

    def audit(self, view: KBView) -> list[CheckResult]:
        """`kblam audit`: ask every candidate pair and revision question that has no cache entry for the
        current state hashes, model and question's prompt id (§6.5), and decide on those. A pair counts
        as asked when either direction is cached; a new pair is asked with the higher ID as `new`."""
        resolved = resolutions.by_key(resolutions.load(self.cfg))  # a damaged log stops the audit before Jev
        findings = [f for f in view.findings if f.ok and f.file_id]
        expected = self.settings.expected_served_model

        def cached(kind: str, existing_state: str, new_state: str) -> bool:
            return self.cache.contains(cache_key(self.settings, kind, existing_state, new_state), expected)

        by_new: dict[str, list[Finding]] = {}
        seen: set[frozenset[str]] = set()
        revisions: list[Finding] = []
        if self.policy.asks_relation:
            for finding in findings:
                for c in self.select(view, finding).candidates:
                    pair = frozenset({finding.file_id, c.finding_id})
                    if pair in seen:
                        continue
                    seen.add(pair)
                    state_new, state_c = Side.of(finding).state_hash, Side.of(c.finding).state_hash
                    if cached("relation", state_c, state_new) or cached("relation", state_new, state_c):
                        continue
                    existing, new = sorted([finding, c.finding], key=lambda f: id_number(f.file_id))
                    by_new.setdefault(new.file_id, []).append(existing)
        if self.policy.asks_revision:
            revisions = [f for f in findings if not cached("revision", "", Side.of(f).state_hash)]

        by_id = {f.file_id: f for f in findings}
        targets = sorted(set(by_new) | {f.file_id for f in revisions}, key=id_number)
        relation_pairs = [(Side.of(e), Side.of(by_id[n])) for n in targets for e in by_new.get(n, [])]
        self._ask(relation_pairs, [Side.of(f) for f in revisions])

        results = []
        revision_ids = {f.file_id for f in revisions}
        for finding_id in targets:
            finding = by_id[finding_id]
            new = Side.of(finding)
            result = CheckResult(finding_id, new.fingerprint, "audit",
                                 similarity=self.similarity_label(),
                                 candidates=[e.file_id for e in by_new.get(finding_id, [])])
            self._decide(result, new, [(e, []) for e in by_new.get(finding_id, [])], finding_id in revision_ids,
                         resolved)
            self._log(result, None)
            results.append(result)
        return results

    def _log(self, result: CheckResult, selection: Selection | None) -> None:
        record = {
            "kind": "check", "command": result.command, "finding_id": result.finding_id,
            "fingerprint": result.fingerprint, "similarity": result.similarity,
            "jev_enabled": result.jev_enabled,
            "different_scope": result.different_scope, "over_budget": result.over_budget,
            "verdicts": [{"verdict": v.verdict, "mode": v.mode, "existing_id": v.existing_id,
                          "existing_fp": v.existing_fp, "p": v.p, "confidence": v.confidence, "noul": v.noul}
                         for v in result.verdicts],
            "suppressed": [{"verdict": v.verdict, "existing_id": v.existing_id} for v in result.suppressed],
            "unavailable": len(result.unavailable), "mismatch": result.mismatch is not None,
        }
        if selection is not None:
            record["candidates"] = [{"id": c.finding_id, "fp": c.fingerprint, "reasons": c.reasons()}
                                    for c in selection.candidates]
        else:
            record["candidates"] = [{"id": i} for i in result.candidates]
        self.log.write(record)
