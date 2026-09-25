"""The similar-candidate embedding mechanism (SPEC §6.1; M6.7): the ollama calls, the
`.kblam/embeddings.sqlite` vector cache and the cosine scores.

Standard library only: `urllib` for the two calls and a plain dot product for the cosine. Two facts
verified on this machine shaped this module (research/desk-sim-answers.md S5): `/api/embed` returns
unit vectors, so a dot product is the cosine, and one request carrying more than about 250 inputs
fails inside ollama's own tokenize helper, so a request carries at most 64 of them.

A failure anywhere raises EmbedUnavailable with a reason; the caller falls back to BM25 for the whole
check (SPEC §6.1 Choosing). `OLLAMA_HOST` is never read: the URL comes from `[jev] ollama_url`.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import threading
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from kblam.config import Config
from kblam.finding import Finding
from kblam.jev import JevSettings
from kblam.view import KBView

EMBEDDINGS_NAME = "embeddings.sqlite"
PROBE_TIMEOUT = 2.0      # §6.1: GET <ollama_url>/api/tags
EMBED_TIMEOUT = 300.0    # one request of at most 64 inputs; a first call also loads the model
MAX_INPUTS = 64          # larger requests fail inside ollama's tokenize helper
QUERY = "query"          # the two roles a vector is cached under
DOCUMENT = "document"


class EmbedUnavailable(Exception):
    """The embedding mechanism cannot be used for this check; the caller uses BM25 for the whole
    check instead. Not an error: the write is not unchecked (SPEC §6.1 Choosing)."""


def title_and_claim(finding: Finding) -> str:
    """The text both similarity mechanisms score: a finding's title and its claim paragraph.
    BM25 tokenises this and the embedding sends it through the model's prefix, so the two mechanisms
    rank the same text (SPEC §6.1)."""
    meta = finding.meta if isinstance(finding.meta, dict) else {}
    title = meta.get("title")
    return f"{title if isinstance(title, str) else ''}\n{finding.claim}"


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. `/api/embed` returns unit vectors, so this is a dot product; both sides are
    normalised anyway, since the legacy `/api/embeddings` endpoint returns unnormalised ones."""
    if len(a) != len(b):
        raise EmbedUnavailable(f"the embedding model returned vectors of {len(a)} and {len(b)} numbers")
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)


@dataclass(frozen=True)
class Model:
    """A model as `/api/tags` describes it. The digest keys the cache: re-pulling a model changes its
    digest, so vectors from the old weights are never reused (SPEC §6.1)."""

    name: str
    digest: str


def probe(url: str, name: str) -> Model:
    """`GET <url>/api/tags` with a 2 s timeout (SPEC §6.1): the model must be listed, and its digest
    comes from there."""
    url = url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=PROBE_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        raise EmbedUnavailable(f"ollama not reachable at {url}") from None
    listed = payload.get("models") if isinstance(payload, dict) else None
    for entry in listed if isinstance(listed, list) else ():
        if isinstance(entry, dict) and name in (entry.get("name"), entry.get("model")):
            digest = entry.get("digest")
            if isinstance(digest, str) and digest:
                return Model(name, digest)
            raise EmbedUnavailable(f"the ollama model list at {url} gives {name} no digest")
    raise EmbedUnavailable(f"{name} is not in the ollama model list at {url}")


def embed_request(url: str, model: Model, texts: list[str], timeout: float = EMBED_TIMEOUT) -> list[list[float]]:
    """`POST <url>/api/embed` for at most `MAX_INPUTS` texts, in order."""
    if len(texts) > MAX_INPUTS:
        raise EmbedUnavailable(f"an embed request carries at most {MAX_INPUTS} inputs, not {len(texts)}")
    body = json.dumps({"model": model.name, "input": texts}).encode("utf-8")
    request = urllib.request.Request(f"{url.rstrip('/')}/api/embed", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise EmbedUnavailable(f"the embed request to {url.rstrip('/')} failed ({exc})") from None
    vectors = payload.get("embeddings") if isinstance(payload, dict) else None
    if not isinstance(vectors, list) or not all(isinstance(vector, list) for vector in vectors):
        raise EmbedUnavailable(f"{url.rstrip('/')}/api/embed returned no usable embeddings")
    if len(vectors) != len(texts):
        raise EmbedUnavailable(f"ollama returned {len(vectors)} vectors for {len(texts)} inputs")
    return [[float(value) for value in vector] for vector in vectors]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}d", *vector)


def _unpack(blob) -> list[float] | None:
    """The vector in `blob`, or None when the row does not hold a whole number of doubles."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) % 8:
        return None
    return list(struct.unpack(f"<{len(blob) // 8}d", blob))


class VectorCache:
    """`.kblam/embeddings.sqlite` (§3): vectors keyed by model name, the model digest from `/api/tags`,
    role (query or document) and the sha256 of the prefixed text. Machine state, like `pairs.sqlite`:
    it holds no finding text and nothing outside this machine's own runs."""

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS vectors (
            model TEXT NOT NULL,
            digest TEXT NOT NULL,
            role TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            vector BLOB NOT NULL,
            created TEXT NOT NULL,
            PRIMARY KEY (model, digest, role, sha256)
        );"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(self.SCHEMA)

    def _connect(self):
        return closing(sqlite3.connect(self.path, timeout=30))

    def get_many(self, keys: list[tuple[str, str, str, str]]) -> dict[tuple[str, str, str, str], list[float]]:
        """The cached vectors among `keys`, by key. A row that no longer holds a vector counts as a miss."""
        found: dict[tuple[str, str, str, str], list[float]] = {}
        with self._lock, self._connect() as conn:
            for key in keys:
                row = conn.execute("SELECT vector FROM vectors WHERE model = ? AND digest = ? AND role = ? "
                                   "AND sha256 = ?", key).fetchone()
                vector = _unpack(row[0]) if row is not None else None
                if vector is not None:
                    found[key] = vector
        return found

    def put_many(self, rows: list[tuple[tuple[str, str, str, str], list[float]]]) -> None:
        with self._lock, self._connect() as conn, conn:
            conn.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?, ?, ?)",
                             [(*key, _pack(vector), _now()) for key, vector in rows])


def _prefix(template: str, text: str) -> str:
    """The model's documented prefix around the text (§9). `str.replace`, not `str.format`: a finding
    may contain braces."""
    return template.replace("{text}", text)


class Embedder:
    """The embedding mechanism for one check: the prefixed texts, the vector cache, the chunked
    requests and the cosines.

    A document's vector is cached under its (model, digest, role, text hash), so a re-run on the same
    machine and model sends only the texts the tree gained (SPEC §6.1). `scores` is called once per
    finding being checked; the first call embeds what the cache lacks, later ones hit this process's
    memory."""

    def __init__(self, cfg: Config, settings: JevSettings, model: Model):
        self.cfg = cfg
        self.settings = settings
        self.model = model
        self.cache = VectorCache(cfg.state_dir / EMBEDDINGS_NAME)
        self._vectors: dict[tuple[str, str, str, str], list[float]] = {}

    def query_text(self, finding: Finding) -> str:
        return _prefix(self.settings.embedding_query_prefix, title_and_claim(finding))

    def document_text(self, finding: Finding) -> str:
        return _prefix(self.settings.embedding_document_prefix, title_and_claim(finding))

    def scores(self, view: KBView, finding: Finding) -> dict[str, float]:
        """Cosine similarity between `finding`'s query text and every other readable finding's document
        text, by finding path. Every finding is scored, whatever its score: catching a paraphrase that
        shares no token is the reason to use embeddings at all (SPEC §6.1)."""
        others = [o for o in view.findings if o.ok and o.file_id and o.file_id != finding.file_id]
        if not others:
            return {}
        query, = self._vectors_for(QUERY, [self.query_text(finding)])
        documents = self._vectors_for(DOCUMENT, [self.document_text(o) for o in others])
        return {other.path: cosine(query, document) for other, document in zip(others, documents)}

    def _key(self, role: str, text: str) -> tuple[str, str, str, str]:
        return (self.model.name, self.model.digest, role, hashlib.sha256(text.encode("utf-8")).hexdigest())

    def _vectors_for(self, role: str, texts: list[str]) -> list[list[float]]:
        """The vectors of `texts`, in order: this process's memory, then the cache, then one chunked
        request per 64 texts still missing."""
        keys = [self._key(role, text) for text in texts]
        missing = [(key, text) for key, text in zip(keys, texts) if key not in self._vectors]
        if missing:
            cached = self.cache.get_many([key for key, _text in missing])
            self._vectors.update(cached)
            missing = [(key, text) for key, text in missing if key not in cached]
        for start in range(0, len(missing), MAX_INPUTS):  # larger requests fail inside ollama
            chunk = missing[start:start + MAX_INPUTS]
            vectors = embed_request(self.settings.ollama_url, self.model, [text for _key, text in chunk])
            self._vectors.update(zip((key for key, _text in chunk), vectors))
            self.cache.put_many(list(zip((key for key, _text in chunk), vectors)))
        return [self._vectors[key] for key in keys]
