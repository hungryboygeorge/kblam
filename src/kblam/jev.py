"""Jev client (SPEC §6.2, §6.5; M3): key loading, retries, the pair cache and the cost log.

Requests go through the official `typesafe_sdk` pointed at OpenRouter. The SDK's own retries are
off; this module retries 408/429/5xx and connection failures itself so that every request is
logged once to `.kblam/calls.jsonl` with its attempt count. Any failure raises JevUnavailable:
a pair that could not be asked is unchecked, never passed.

The API key is loaded only when a request is actually needed (a cache hit needs no key) and is
kept out of every message, repr and log line.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from typesafe_sdk import (
    RetryPolicy,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeAPIResponseValidationError,
    TypeSafeClient,
    TypeSafeError,
)

from kblam import jev_prompts
from kblam.config import CONFIG_NAME, Config, ConfigError
from kblam.finding import Finding, fingerprint, plain_data

SYSTEM_ONE_PATH = "/v1/systemone"  # the SDK appends this to its base URL
CACHE_NAME = "pairs.sqlite"
LOG_NAME = "calls.jsonl"

DEFAULT_JEV = {
    "endpoint": "https://openrouter.ai/api/v1/systemone",
    "model": "typesafe/jev-1.13",
    "expected_served_model": "typesafe/jev-1.13-20260917",
    "key_env": "OPENROUTER_API_KEY",
    "key_file": "~/kblam/jev!.txt",  # ~ is the user's home directory (SPEC §9)
    "max_candidates": 30,
    "topic_bonus": 0.2,             # §6.1: same-topic similarity bonus, as a fraction of the top score
    "link_bonus": 0.15,             # §6.1: shared anchor or evidence bonus, as a fraction of the top score
    "embedding_model": "embeddinggemma:300m",  # §6.1; "" means BM25 always
    "ollama_url": "http://127.0.0.1:11434",    # OLLAMA_HOST is never read
    "embedding_query_prefix": "task: search result | query: {text}",
    "embedding_document_prefix": "title: none | text: {text}",
    "prompt": None,                 # §6.2: the questions themselves ([jev.prompt.*]); required
    "quantity_rel_tolerance": 0.0,  # §6.3
    "workers": 6,                   # parallel pair requests
    "thresholds": {},               # §6.4; parsed by kblam.check.parse_policy
}

MAX_ATTEMPTS = 4                # first try plus three retries
BACKOFF_INITIAL = 1.0           # seconds, doubled per retry
BACKOFF_MAX = 16.0
RETRY_AFTER_MAX = 60.0          # cap on a server-requested 429 wait
REQUEST_TIMEOUT = 30.0          # seconds per HTTP attempt
DEFAULT_WORKERS = 6
MIN_START_INTERVAL = 60 / 1000  # at most 1,000 request starts a minute (the limit is 1,200)
RETRY_STATUSES = frozenset({408, 429})  # plus every 5xx


class JevUnavailable(Exception):
    """Jev could not answer: no key, a failed request, or an unusable response. The pair is unchecked."""


@dataclass(frozen=True)
class JevSettings:
    base_url: str
    model: str
    expected_served_model: str
    key_env: str
    key_file: str
    max_candidates: int
    topic_bonus: float
    link_bonus: float
    embedding_model: str
    ollama_url: str
    embedding_query_prefix: str
    embedding_document_prefix: str
    prompt_id: str                  # sha256 of the questions as sent (jev_prompts.prompt_id)
    relation_question: dict         # the questions to send: built from [jev.prompt], never from code
    revision_question: dict
    quantity_rel_tolerance: float
    workers: int
    thresholds: dict


def jev_settings(cfg: Config) -> JevSettings:
    """The [jev] table of kblam.toml, with SPEC §9 defaults for absent keys. `[jev.prompt]` has no
    default: the questions are the project's (SPEC §6.2), so a config without them is an error."""
    if "prompt_version" in cfg.jev:
        raise ConfigError(f"{CONFIG_NAME}: [jev] prompt_version is gone: {jev_prompts.PROMPT_MOVED}")
    unknown = sorted(set(cfg.jev) - set(DEFAULT_JEV))
    if unknown:
        raise ConfigError(f"{CONFIG_NAME}: unknown [jev] key(s) {', '.join(unknown)}; "
                          f"allowed: {', '.join(DEFAULT_JEV)}")
    raw = {**DEFAULT_JEV, **cfg.jev}
    for key, default in DEFAULT_JEV.items():
        if default is None:  # "prompt", validated below: absent is an error, not a default
            continue
        value = raw[key]
        if isinstance(default, int):
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif isinstance(default, float):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            ok = isinstance(value, type(default))
        if not ok:
            raise ConfigError(f"{CONFIG_NAME}: [jev] {key} must be {type(default).__name__}, got {value!r}")
    endpoint = raw["endpoint"].rstrip("/")
    if not endpoint.endswith(SYSTEM_ONE_PATH):
        raise ConfigError(f"{CONFIG_NAME}: [jev] endpoint must end in {SYSTEM_ONE_PATH}, got {endpoint!r}")
    if raw["max_candidates"] < 1 or raw["workers"] < 1:
        raise ConfigError(f"{CONFIG_NAME}: [jev] max_candidates and workers must be at least 1")
    if raw["quantity_rel_tolerance"] < 0:
        raise ConfigError(f"{CONFIG_NAME}: [jev] quantity_rel_tolerance must be >= 0")
    for key in ("topic_bonus", "link_bonus"):
        if raw[key] < 0:
            raise ConfigError(f"{CONFIG_NAME}: [jev] {key} must be >= 0")
    if raw["ollama_url"] and not raw["ollama_url"].startswith(("http://", "https://")):
        raise ConfigError(f"{CONFIG_NAME}: [jev] ollama_url must be an http:// or https:// URL, "
                          f"got {raw['ollama_url']!r}")
    for key in ("embedding_query_prefix", "embedding_document_prefix"):
        if "{text}" not in raw[key]:
            raise ConfigError(f"{CONFIG_NAME}: [jev] {key} must contain the placeholder {{text}}, "
                              f"got {raw[key]!r}")
    if cfg.jev.get("prompt") is None:
        raise ConfigError(f"{CONFIG_NAME}: [jev] has no [jev.prompt] table: {jev_prompts.PROMPT_MOVED}. "
                          f"Copy the [jev.prompt] tables from the kblam.toml template kblam init writes")
    prompt_id, relation, revision = jev_prompts.questions_for_api(cfg.jev["prompt"])
    return JevSettings(
        base_url=endpoint[: -len(SYSTEM_ONE_PATH)],
        model=raw["model"],
        expected_served_model=raw["expected_served_model"],
        key_env=raw["key_env"],
        key_file=raw["key_file"],
        max_candidates=raw["max_candidates"],
        topic_bonus=float(raw["topic_bonus"]),
        link_bonus=float(raw["link_bonus"]),
        embedding_model=raw["embedding_model"],
        ollama_url=raw["ollama_url"].rstrip("/"),
        embedding_query_prefix=raw["embedding_query_prefix"],
        embedding_document_prefix=raw["embedding_document_prefix"],
        prompt_id=prompt_id,
        relation_question=relation,
        revision_question=revision,
        quantity_rel_tolerance=float(raw["quantity_rel_tolerance"]),
        workers=raw["workers"],
        thresholds=raw["thresholds"],
    )


class _Secret:
    """Holds the API key; prints as a placeholder so it cannot leak through repr or str."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def scrub(self, text: str) -> str:
        return text.replace(self._value, "[key]") if self._value else text

    def __repr__(self) -> str:
        return "<secret>"

    __str__ = __repr__


def load_key(cfg: Config, settings: JevSettings) -> _Secret:
    """The OpenRouter key from the environment, else from [jev].key_file (a leading ~ is the user's home
    directory; another relative path is relative to the repository root). Messages never contain it."""
    value = os.environ.get(settings.key_env, "").strip()
    if value:
        return _Secret(value)
    if not settings.key_file:
        raise JevUnavailable(f"no Jev API key: set the {settings.key_env} environment variable or "
                             f"[jev] key_file in {CONFIG_NAME}")
    path = Path(settings.key_file).expanduser()
    if not path.is_absolute():
        path = cfg.repo_root / path
    if not path.exists():
        raise JevUnavailable(f"no Jev API key: the {settings.key_env} environment variable is not set and the "
                             f"key file {settings.key_file} ({path}) does not exist; save the OpenRouter key "
                             f"there or set {settings.key_env}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) else "not UTF-8 text"
        raise JevUnavailable(f"cannot read the Jev key file {path}: {reason}") from None
    value = text.strip()
    if not value or any(c.isspace() for c in value) or not value.isascii() or not value.isprintable():
        raise JevUnavailable(f"the Jev key file {path} must contain only the API key, on one line")
    return _Secret(value)


@dataclass(frozen=True)
class Side:
    """One finding as Jev sees it: claim and scope, plus the ID and M2 fingerprint used for caching."""

    finding_id: str
    claim: str
    scope: tuple[str, ...]
    fingerprint: str

    @classmethod
    def of(cls, finding: Finding) -> Side:
        meta = finding.meta if isinstance(finding.meta, dict) else {}
        scope = plain_data(meta.get("scope"))
        if isinstance(scope, str):
            scope = [scope]
        return cls(finding.file_id or finding.path, " ".join(finding.claim.split()),
                   tuple(str(s) for s in scope or ()), fingerprint(finding))

    def state(self) -> dict:
        return jev_prompts.side_state(self.claim, list(self.scope))


@dataclass(frozen=True)
class CallInfo:
    """What the request that produced an answer reported. `cached` answers carry the original request's values."""

    requested_model: str
    served_model: str
    expected_served_model: str
    input_tokens: int | None
    output_tokens: int | None
    cost: float | None          # OpenRouter's usage.cost in USD; None when the response had none
    latency_s: float            # wall time of the attempt that answered
    attempts: int
    generation_id: str | None   # OpenRouter's `id`
    provider: str | None
    cached: bool = False

    @property
    def model_mismatch(self) -> bool:
        return self.served_model != self.expected_served_model


@dataclass(frozen=True)
class RelationResult:
    existing: str               # finding IDs
    new: str
    winner: str
    probabilities: dict[str, float]
    confidence: float
    call: CallInfo


@dataclass(frozen=True)
class RevisionResult:
    new: str
    noul: float                 # a Noul has no confidence
    call: CallInfo


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CacheSchemaError(JevUnavailable):
    """The pair cache on disk was written by an older kblam, whose rows were keyed by prompt_version
    (an integer). It is never dropped silently: the answers in it are only a cache, but replacing the
    file is the user's call."""


class PairCache:
    """`.kblam/pairs.sqlite`: answers keyed by (expected served model, prompt id, kind,
    fingerprint(existing) or "" for revision, fingerprint(new)). A row is only used when its
    served model is the expected one.

    Two more tables hold review-workflow state (M5): `distinct_pairs`, the reasons given to
    `kblam resolve --distinct` for a pair at given fingerprints (unordered; a revision item's
    second side is empty), and `checked`, each (finding, fingerprint) whose Jev check got an
    answer to every question."""

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS answers (
            expected_model TEXT NOT NULL,
            prompt_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            existing_fp TEXT NOT NULL,
            new_fp TEXT NOT NULL,
            served_model TEXT NOT NULL,
            answer TEXT NOT NULL,
            call TEXT NOT NULL,
            created TEXT NOT NULL,
            PRIMARY KEY (expected_model, prompt_id, kind, existing_fp, new_fp)
        );
        CREATE TABLE IF NOT EXISTS distinct_pairs (
            a_id TEXT NOT NULL,
            a_fp TEXT NOT NULL,
            b_id TEXT NOT NULL,
            b_fp TEXT NOT NULL,
            reason TEXT NOT NULL,
            created TEXT NOT NULL,
            PRIMARY KEY (a_id, a_fp, b_id, b_fp)
        );
        CREATE TABLE IF NOT EXISTS checked (
            finding_id TEXT NOT NULL,
            fp TEXT NOT NULL,
            created TEXT NOT NULL,
            PRIMARY KEY (finding_id, fp)
        );"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(answers)")}
            if columns and "prompt_id" not in columns:
                raise CacheSchemaError(
                    f"{path} was written by an older kblam: its answers table is keyed by an integer "
                    f"prompt_version, and answers are now keyed by prompt_id. Delete {path} and the "
                    f"missing pairs are asked again (nothing else in .kblam/ is affected), or migrate "
                    f"the table by hand: rename prompt_version to prompt_id and put the id kblam "
                    f"prompt-id prints in every row")
            conn.executescript(self.SCHEMA)

    def _connect(self):
        return closing(sqlite3.connect(self.path, timeout=30))

    def get(self, key: tuple, expected_model: str) -> tuple[dict, dict] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT served_model, answer, call FROM answers WHERE expected_model = ? AND "
                "prompt_id = ? AND kind = ? AND existing_fp = ? AND new_fp = ?", key).fetchone()
        if row is None or row[0] != expected_model:
            return None
        return json.loads(row[1]), json.loads(row[2])

    def contains(self, key: tuple, expected_model: str) -> bool:
        """Whether get() would hit, without the cache-hit log line (for `kblam audit`)."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT served_model FROM answers WHERE expected_model = ? AND prompt_id = ? AND "
                "kind = ? AND existing_fp = ? AND new_fp = ?", key).fetchone()
        return row is not None and row[0] == expected_model

    def put(self, key: tuple, served_model: str, answer: dict, call: dict) -> None:
        with self._lock, self._connect() as conn, conn:
            conn.execute("INSERT OR REPLACE INTO answers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (*key, served_model, json.dumps(answer, sort_keys=True),
                          json.dumps(call, sort_keys=True), _now()))

    @staticmethod
    def _pair(a: tuple[str, str], b: tuple[str, str] | None) -> tuple[str, str, str, str]:
        first, second = sorted([a, b or ("", "")])
        return (*first, *second)

    def mark_distinct(self, a: tuple[str, str], b: tuple[str, str] | None, reason: str) -> None:
        """Record `kblam resolve --distinct` for (id, fingerprint) sides a and b (None for a revision item)."""
        with self._lock, self._connect() as conn, conn:
            conn.execute("INSERT OR REPLACE INTO distinct_pairs VALUES (?, ?, ?, ?, ?, ?)",
                         (*self._pair(a, b), reason, _now()))

    def distinct_reason(self, a: tuple[str, str], b: tuple[str, str] | None) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT reason FROM distinct_pairs WHERE a_id = ? AND a_fp = ? AND b_id = ? "
                               "AND b_fp = ?", self._pair(a, b)).fetchone()
        return row[0] if row else None

    def mark_checked(self, finding_id: str, fp: str) -> None:
        with self._lock, self._connect() as conn, conn:
            conn.execute("INSERT OR REPLACE INTO checked VALUES (?, ?, ?)", (finding_id, fp, _now()))

    def was_checked(self, finding_id: str, fp: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT 1 FROM checked WHERE finding_id = ? AND fp = ?", (finding_id, fp)).fetchone()
        return row is not None


class CallLog:
    """`.kblam/calls.jsonl`: one line per request, and one per cache hit (status "cache_hit").
    Finding IDs and fingerprints only; never finding text, never the key."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        line = json.dumps({"ts": _now(), **record}, sort_keys=True, ensure_ascii=False) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)


def _retryable(exc: TypeSafeError) -> bool:
    if isinstance(exc, TypeSafeAPIConnectionError):  # includes timeouts
        return True
    if isinstance(exc, TypeSafeAPIResponseValidationError):
        return False
    return isinstance(exc, TypeSafeAPIError) and (exc.status in RETRY_STATUSES or exc.status >= 500)


def _failure_hint(exc: TypeSafeError) -> str:
    status = getattr(exc, "status", None)
    if status == 401:
        return "; check the OpenRouter key"
    if status == 402:
        return "; the OpenRouter account is out of credits"
    return ""


class _Failed(Exception):
    def __init__(self, error: TypeSafeError, attempts: int):
        self.error = error
        self.attempts = attempts


class JevClient:
    """Asks the §6.2 questions. Use as a context manager, or call close()."""

    def __init__(self, cfg: Config, *, transport=None, sleep=time.sleep,
                 max_attempts: int = MAX_ATTEMPTS, timeout: float = REQUEST_TIMEOUT):
        self.cfg = cfg
        self.settings = jev_settings(cfg)
        self.cache = PairCache(cfg.state_dir / CACHE_NAME)
        self.log = CallLog(cfg.state_dir / LOG_NAME)
        self.max_attempts = max_attempts
        self.timeout = timeout
        self._transport = transport  # an httpx2 transport; tests pass a fake one
        self._sleep = sleep
        self._key: _Secret | None = None
        self._client: TypeSafeClient | None = None
        self._client_lock = threading.Lock()
        self._throttle_lock = threading.Lock()
        self._next_start = 0.0

    def __enter__(self) -> JevClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # --- public questions -------------------------------------------------------------------

    def ask_relation(self, existing: Side | Finding, new: Side | Finding) -> RelationResult:
        """Which §6.2 relation option describes `new` relative to `existing` (directional)."""
        existing, new = _side(existing), _side(new)
        key = self._key_for("relation", existing.fingerprint, new.fingerprint)
        ids = {"existing_id": existing.finding_id, "existing_fp": existing.fingerprint,
               "new_id": new.finding_id, "new_fp": new.fingerprint}
        hit = self._cached("relation", key, ids)
        if hit is not None:
            answer, call = hit
            return RelationResult(existing.finding_id, new.finding_id, answer["winner"],
                                  answer["probabilities"], answer["confidence"], call)
        state = jev_prompts.relation_state(existing.state(), new.state())

        def parse(response) -> dict:
            got = response.choices.get(jev_prompts.RELATION_KEY)
            if got is None or got.choice not in jev_prompts.RELATION_OPTIONS:
                raise ValueError(f"no usable {jev_prompts.RELATION_KEY!r} choice in the response")
            return {"winner": got.choice, "probabilities": dict(got.probabilities),
                    "confidence": got.confidence}

        answer, call = self._ask("relation", key, ids, state,
                                 jev_prompts.relation_questions(self.settings.relation_question), parse)
        return RelationResult(existing.finding_id, new.finding_id, answer["winner"],
                              answer["probabilities"], answer["confidence"], call)

    def ask_revision(self, new: Side | Finding) -> RevisionResult:
        """Noul: does `new` read as a correction of an earlier claim (asked once per finding)."""
        new = _side(new)
        key = self._key_for("revision", "", new.fingerprint)
        ids = {"existing_id": None, "existing_fp": None, "new_id": new.finding_id, "new_fp": new.fingerprint}
        hit = self._cached("revision", key, ids)
        if hit is not None:
            answer, call = hit
            return RevisionResult(new.finding_id, answer["noul"], call)
        state = jev_prompts.revision_state(new.state())

        def parse(response) -> dict:
            got = response.nouls.get(jev_prompts.REVISION_KEY)
            if got is None:
                raise ValueError(f"no {jev_prompts.REVISION_KEY!r} noul in the response")
            return {"noul": got.noul}

        answer, call = self._ask("revision", key, ids, state,
                                 jev_prompts.revision_questions(self.settings.revision_question), parse)
        return RevisionResult(new.finding_id, answer["noul"], call)

    def ask_relations(self, pairs, workers: int = DEFAULT_WORKERS) -> list[RelationResult | JevUnavailable]:
        """ask_relation over (existing, new) pairs on a worker pool; results in input order, a
        failed pair as its JevUnavailable."""
        return self._parallel(lambda pair: self.ask_relation(*pair), pairs, workers)

    def ask_revisions(self, findings, workers: int = DEFAULT_WORKERS) -> list[RevisionResult | JevUnavailable]:
        return self._parallel(self.ask_revision, findings, workers)

    # --- internals --------------------------------------------------------------------------

    def _key_for(self, kind: str, existing_fp: str, new_fp: str) -> tuple:
        return (self.settings.expected_served_model, self.settings.prompt_id, kind, existing_fp, new_fp)

    def _record(self, kind: str, status: str, ids: dict, **fields) -> dict:
        return {"kind": kind, "status": status, "requested_model": self.settings.model,
                "expected_served_model": self.settings.expected_served_model,
                "prompt_id": self.settings.prompt_id, **ids, **fields}

    def _cached(self, kind: str, key: tuple, ids: dict) -> tuple[dict, CallInfo] | None:
        hit = self.cache.get(key, self.settings.expected_served_model)
        if hit is None:
            return None
        answer, call = hit
        info = CallInfo(**{**call, "cached": True})
        self.log.write(self._record(kind, "cache_hit", ids, served_model=info.served_model))
        return answer, info

    def _parallel(self, fn, items, workers: int) -> list:
        def one(item):
            try:
                return fn(item)
            except JevUnavailable as exc:
                return exc

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            return list(pool.map(one, list(items)))

    def _sdk(self) -> TypeSafeClient:
        with self._client_lock:
            if self._client is None:
                self._key = load_key(self.cfg, self.settings)
                try:
                    self._client = TypeSafeClient(
                        api_key=self._key.reveal(), base_url=self.settings.base_url,
                        model=self.settings.model, retry=RetryPolicy(max_retries=0),
                        timeout=self.timeout, transport=self._transport)
                except TypeSafeError as exc:
                    raise JevUnavailable(f"could not create the Jev client: {self._key.scrub(str(exc))}") from None
            return self._client

    def _scrub(self, text: str) -> str:
        return self._key.scrub(text) if self._key is not None else text

    def _throttle(self) -> None:
        with self._throttle_lock:
            now = time.monotonic()
            wait = self._next_start - now
            self._next_start = max(now, self._next_start) + MIN_START_INTERVAL
        if wait > 0:
            time.sleep(wait)

    def _delay(self, attempt: int, exc: TypeSafeError) -> float:
        retry_after_ms = getattr(exc, "retry_after_ms", None)
        if retry_after_ms is not None:
            return min(retry_after_ms / 1000, RETRY_AFTER_MAX)
        delay = min(BACKOFF_INITIAL * 2 ** (attempt - 1), BACKOFF_MAX)
        return delay * (1 - 0.25 * random.random())

    def _send(self, client: TypeSafeClient, state: dict, questions: dict):
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            started = time.monotonic()
            try:
                response = client.system_one(state, questions, model=self.settings.model)
                return response, attempt, time.monotonic() - started
            except TypeSafeError as exc:
                if attempt >= self.max_attempts or not _retryable(exc):
                    raise _Failed(exc, attempt) from None
                self._sleep(self._delay(attempt, exc))

    def _ask(self, kind: str, key: tuple, ids: dict, state: dict, questions: dict, parse) -> tuple[dict, CallInfo]:
        client = self._sdk()
        try:
            response, attempts, latency = self._send(client, state, questions)
        except _Failed as failed:
            detail = self._scrub(f"{type(failed.error).__name__}: {failed.error}")
            self.log.write(self._record(kind, "error", ids, attempts=failed.attempts, error=detail))
            raise JevUnavailable(f"Jev {kind} request failed after {failed.attempts} attempt(s): "
                                 f"{detail}{_failure_hint(failed.error)}") from None

        raw = response.raw_http_response.json()
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        cost = usage.get("cost")
        info = CallInfo(
            requested_model=self.settings.model,
            served_model=response.model,
            expected_served_model=self.settings.expected_served_model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
            latency_s=round(latency, 3),
            attempts=attempts,
            generation_id=raw.get("id"),
            provider=raw.get("provider"),
        )
        record = dict(served_model=info.served_model, model_mismatch=info.model_mismatch,
                      input_tokens=info.input_tokens, output_tokens=info.output_tokens, cost=info.cost,
                      latency_s=info.latency_s, attempts=attempts, generation_id=info.generation_id)
        try:
            answer = parse(response)
        except ValueError as exc:
            self.log.write(self._record(kind, "bad_response", ids, **record, error=str(exc)))
            raise JevUnavailable(f"Jev {kind} response unusable: {exc}") from None
        self.log.write(self._record(kind, "ok", ids, **record))
        stored = {k: v for k, v in asdict(info).items() if k != "cached"}
        self.cache.put(key, info.served_model, answer, stored)
        return answer, info


def _side(value: Side | Finding) -> Side:
    return value if isinstance(value, Side) else Side.of(value)


# --- kblam cost ----------------------------------------------------------------------------


@dataclass
class CostBucket:
    requests: int = 0          # logged requests (ok, bad_response, error); cache hits excluded
    failed: int = 0            # error or bad_response
    attempts: int = 0          # HTTP attempts, retries included
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    cost_missing: int = 0      # answered requests whose response had no usage.cost
    cache_hits: int = 0
    mismatches: int = 0        # answered requests whose served model was not the expected one

    def add(self, record: dict) -> None:
        status = record.get("status")
        if status == "cache_hit":
            self.cache_hits += 1
            return
        self.requests += 1
        self.attempts += int(record.get("attempts") or 0)
        if status != "ok":
            self.failed += 1
        self.input_tokens += int(record.get("input_tokens") or 0)
        self.output_tokens += int(record.get("output_tokens") or 0)
        if record.get("model_mismatch"):
            self.mismatches += 1
        if isinstance(record.get("cost"), (int, float)):
            self.cost += record["cost"]
        elif status in ("ok", "bad_response"):
            self.cost_missing += 1


@dataclass
class CostSummary:
    total: CostBucket = field(default_factory=CostBucket)
    by_day: dict[str, CostBucket] = field(default_factory=dict)   # UTC date
    by_kind: dict[str, CostBucket] = field(default_factory=dict)
    bad_lines: int = 0
    exists: bool = True


def cost_summary(cfg: Config) -> CostSummary:
    path = cfg.state_dir / LOG_NAME
    summary = CostSummary()
    if not path.is_file():
        summary.exists = False
        return summary
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            summary.bad_lines += 1
            continue
        if not isinstance(record, dict):
            summary.bad_lines += 1
            continue
        day = str(record.get("ts", ""))[:10] or "unknown"
        kind = str(record.get("kind", "unknown"))
        for bucket in (summary.total, summary.by_day.setdefault(day, CostBucket()),
                       summary.by_kind.setdefault(kind, CostBucket())):
            bucket.add(record)
    return summary


# --- kblam jev-smoke -----------------------------------------------------------------------

SMOKE_EXISTING = "The pump motor reaches steady output after 90 seconds of warm-up."
SMOKE_NEW = "The pump motor output is steady after 90 seconds of warm-up."
SMOKE_REVISION = "Earlier we said the motor needs 60 seconds; that was wrong, it needs 90."


def smoke_sides() -> tuple[Side, Side, Side]:
    """Synthetic findings for the live smoke test: (existing, new) expected same_fact, and a
    revision candidate expected to get a high noul."""
    def synthetic(finding_id: str, claim: str) -> Side:
        return Side.of(Finding(path=f"smoke/{finding_id}.md", raw=b"", file_id=finding_id,
                               meta={"scope": ["any"]}, claim=claim))

    return synthetic("F-9001", SMOKE_EXISTING), synthetic("F-9002", SMOKE_NEW), synthetic("F-9003", SMOKE_REVISION)
