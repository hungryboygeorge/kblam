"""M6.7: the embedding similarity mechanism (SPEC §6.1) — cosine ranking, the BM25 fallback, the
`.kblam/embeddings.sqlite` cache and the 64-input chunking.

ollama is a local fake server and Jev the fake transport from test_check: nothing here touches a
real server. The machine this runs on has a real ollama listening on 11434, which is why the other
fixtures set `embedding_model = ""`.
"""

from __future__ import annotations

import json
import math
import re
import socket
import sqlite3
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx2
import pytest

from kblam import cli, store
from kblam.check import Checker, select_candidates
from kblam.config import ConfigError
from kblam.embed import EmbedUnavailable, cosine
from kblam.jev import JevClient, jev_settings
from kblam.store import put
from kblam.view import load_view

from conftest import KBLAM_TOML, PROMPT_TOML, finding_text
from test_check import E1, E2, N, NEW_EVIDENCE, ScriptedJev, THRESHOLDS, ids, kinds, stage
from test_jev import JEV_TOML, KEY

MODEL = "fake-embed"
DIGEST = "a" * 64


# --- a fake ollama ----------------------------------------------------------------------------


def unit(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values] if norm else list(values)


def histogram(text: str) -> list[float]:
    """A deterministic 4-number vector for a text no test gave a vector for."""
    out = [0.0, 0.0, 0.0, 0.0]
    for char in text:
        out[ord(char) % 4] += 1.0
    return out


def by_keyword(*pairs) -> "callable":
    """A `vector_for` that returns `pairs`' vector for the first keyword the text contains (the
    keywords are lowercase; the text is matched case-insensitively)."""
    def vector_for(text: str) -> list[float]:
        for keyword, vector in pairs:
            if keyword in text.lower():
                return vector
        return [0.0, 0.0, 1.0]
    return vector_for


class _Failed(Exception):
    pass


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        fake = self.server.fake
        if self.path != "/api/tags":
            return self._send(404, {"error": "not found"})
        fake.tags_calls += 1
        self._send(200, {"models": [{"name": name, "model": name, "digest": digest}
                                    for name, digest in fake.models.items()]})

    def do_POST(self) -> None:
        fake = self.server.fake
        if self.path != "/api/embed":
            return self._send(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        try:
            vectors = fake.embed(body["model"], body["input"])
        except _Failed:
            return self._send(500, {"error": "the tokenize helper is not answering"})
        self._send(200, {"model": body["model"], "embeddings": vectors})

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass  # keep the test output clean


class FakeOllama:
    """A local HTTP server standing in for ollama: `GET /api/tags` lists `models` (name -> digest),
    `POST /api/embed` returns `vector_for`'s unit vector per input and records every request's inputs.
    `fail_from` fails /api/embed from the n-th request on, as a mid-check failure does."""

    def __init__(self, models=None, vector_for=None, fail_from: int | None = None):
        self.models = {MODEL: DIGEST} if models is None else models
        self.vector_for = vector_for or histogram
        self.fail_from = fail_from
        self.normalise = True  # /api/embed returns unit vectors; False stands in for one that does not
        self.requests: list[list[str]] = []
        self.tags_calls = 0
        self.on_request = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.fake = self
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread: threading.Thread | None = None

    def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        self.requests.append(list(inputs))
        if self.on_request:
            self.on_request(list(inputs))
        if self.fail_from is not None and len(self.requests) >= self.fail_from:
            raise _Failed
        return [unit(self.vector_for(text)) if self.normalise else self.vector_for(text) for text in inputs]

    def start(self) -> FakeOllama:
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._thread is not None:
            self.server.shutdown()
            self.server.server_close()
            self._thread.join(timeout=5)
            self._thread = None


def closed_port() -> int:
    """A port nothing is listening on: bind one, learn its number, close it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def ollama():
    fake = FakeOllama().start()
    try:
        yield fake
    finally:
        fake.stop()


# --- the fixture KB; a check without Jev -------------------------------------------------------


def embed_config(kb, url: str, model: str = MODEL, **keys) -> None:
    """The fixtures' kblam.toml with an ollama URL and a model of the test's choosing, in place of
    the `embedding_model = ""` the other fixtures use."""
    settings = {"embedding_model": model, "ollama_url": url, **keys}
    lines = "".join(f"{key} = {json.dumps(value) if isinstance(value, str) else value}\n"
                    for key, value in settings.items())
    kb.write("kblam.toml", KBLAM_TOML + "\n[jev]\n" + JEV_TOML + lines + THRESHOLDS + PROMPT_TOML)


def add_many(kb, count: int, topic: str = "bulk") -> None:
    """`count` findings written directly, with one reindex (chunking tests need 70 or more)."""
    for number in range(1, count + 1):
        kb.write(f"findings/{topic}/F-{number:04d}-bulk-{number}.md",
                 finding_text(f"F-{number:04d}", f"The pump feature {number} works.", topic=topic,
                              title=f"Feature {number}"))
    kb.reindex()


def select_with(kb, finding_id: str):
    """(selection, similarity label) exactly as a check computes them, without asking Jev."""
    cfg = kb.cfg
    view = load_view(cfg)
    finding = next(f for f in view.findings if f.file_id == finding_id)
    with Checker(cfg) as checker:
        selection = checker.select(view, finding)
        return selection, checker.similarity_label()


def check_log(kb) -> list[dict]:
    path = kb.root / ".kblam" / "checks.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run(kb, *args) -> int:
    return cli.main(["--root", str(kb.root), *args])


@pytest.fixture
def ekb(kb, monkeypatch, ollama):
    """A fixture KB whose checks use the fake ollama and the fake Jev transport."""
    embed_config(kb, ollama.url)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    kb.fake = ScriptedJev()
    kb.factory = lambda cfg: JevClient(cfg, transport=httpx2.MockTransport(kb.fake), sleep=lambda s: None)
    monkeypatch.setattr(cli, "JevClient", kb.factory)
    return kb


# --- §6.1 ranking -------------------------------------------------------------------------------


def test_rank_is_by_cosine_with_ties_by_id(ekb, ollama):
    ollama.vector_for = by_keyword(("motor", [1.0, 0.0, 0.0]), ("tray", [0.0, 1.0, 0.0]))
    ekb.add("F-0001", "motor", "The motor reaches steady output.", title="Motor warm-up")
    ekb.add("F-0002", "tray", "The media tray reports its type.", title="Media tray")
    ekb.add("F-0003", "motor-again", "The motor output is steady.", title="Motor output")
    ekb.add("F-0004", "self", "The motor drifts.", title="Motor drift", evidence=NEW_EVIDENCE)
    selection, label = select_with(ekb, "F-0004")

    assert label == f"embed {MODEL}"
    assert ids(selection.candidates) == ["F-0001", "F-0003", "F-0002"]  # 1.0, 1.0 (tie by ID), 0.0
    assert [c.reasons() for c in selection.candidates] == [
        ["similar 1.0000 (embed)"], ["similar 1.0000 (embed)"], ["similar 0.0000 (embed)"]]
    assert selection.over_budget == [] and selection.different_scope == []


def test_linked_findings_come_first_whatever_their_cosine(ekb, ollama):
    ollama.vector_for = by_keyword(("motor", [1.0, 0.0, 0.0]), ("tray", [0.0, 1.0, 0.0]))
    ekb.add("F-0001", "tray", "The media tray reports its type.", title="Media tray")
    ekb.add("F-0002", "motor", "The motor is bright.", title="Motor")
    ekb.add("F-0003", "self", "The motor drifts.", title="Motor drift", evidence=NEW_EVIDENCE,
            extra="depends_on: {F-0001: null}\n")
    selection, _label = select_with(ekb, "F-0003")

    assert ids(selection.candidates) == ["F-0001", "F-0002"]
    assert selection.candidates[0].reasons() == ["depends_on"]


def test_embedding_mode_has_no_topic_bonus(ekb, ollama):
    ollama.vector_for = lambda text: [1.0, 0.0, 0.0]  # every finding scores 1.0
    ekb.add("F-0001", "elsewhere", "The motor reaches steady output.", title="Motor", topic="optics")
    ekb.add("F-0002", "here", "The motor reaches steady output.", title="Motor")
    ekb.add("F-0003", "self", "The motor drifts.", title="Motor drift", evidence=NEW_EVIDENCE)
    selection, _label = select_with(ekb, "F-0003")

    assert ids(selection.candidates) == ["F-0001", "F-0002"]  # equal scores: ties by ID, not by topic
    assert [c.bonus for c in selection.candidates] == [0.0, 0.0]


def test_embedding_mode_keeps_the_scope_gate(ekb, ollama):
    ollama.vector_for = lambda text: [1.0, 0.0, 0.0]
    ekb.add("F-0001", "mx100", "The motor is warm.", title="Motor", scope="[MX-100]")
    ekb.add("F-0002", "here", "The motor is warm.", title="Motor")
    ekb.add("F-0003", "self", "The motor is warm.", title="Motor", evidence=NEW_EVIDENCE)
    selection, _label = select_with(ekb, "F-0003")

    assert ids(selection.different_scope) == ["F-0001"]  # Jev is not asked about a disjoint scope
    assert ids(selection.candidates) == ["F-0002"]


def test_the_check_log_records_the_mechanism_and_the_reason(ekb, ollama):
    ollama.vector_for = by_keyword(("motor", [1.0, 0.0, 0.0]))
    ekb.write("evidence/new-run/log.txt", "x\n")
    ekb.add("F-0001", "motor", E1, title="Motor warm-up")
    ekb.add("F-0002", "drift", N, title="Motor drift", evidence=NEW_EVIDENCE)
    assert run(ekb, "check", "F-0002") == 0

    record, = check_log(ekb)
    assert record["similarity"] == f"embed {MODEL}"
    candidate, = record["candidates"]
    assert candidate["id"] == "F-0001" and candidate["reasons"] == ["similar 1.0000 (embed)"]


# --- §6.1 Choosing: the BM25 fallback -----------------------------------------------------------


def test_fallback_when_ollama_is_not_reachable(kb, capsys):
    url = f"http://127.0.0.1:{closed_port()}"
    embed_config(kb, url)
    kb.add("F-0001", "motor", E1, title="Motor warm-up")
    kb.add("F-0002", "tray", E2, title="Media tray")
    kb.add("F-0003", "drift", N, title="Motor drift", evidence=NEW_EVIDENCE)

    capsys.readouterr()  # the fixture's own index writes are not this test's business
    selection, label = select_with(kb, "F-0003")
    assert capsys.readouterr().err == f"similarity: BM25 (ollama not reachable at {url})\n"
    assert label == f"bm25 (ollama not reachable at {url})"
    assert ids(selection.candidates) == ["F-0001"]  # BM25: F-0002 shares no token with N


def test_fallback_when_the_model_is_missing(kb, capsys, ollama):
    ollama.models = {"another-model": DIGEST}
    embed_config(kb, ollama.url)
    kb.add("F-0001", "motor", E1, title="Motor warm-up")
    kb.add("F-0002", "drift", N, title="Motor drift", evidence=NEW_EVIDENCE)

    capsys.readouterr()
    selection, label = select_with(kb, "F-0002")
    assert capsys.readouterr().err == (
        f"similarity: BM25 ({MODEL} is not in the ollama model list at {ollama.url})\n")
    assert label == f"bm25 ({MODEL} is not in the ollama model list at {ollama.url})"
    assert ids(selection.candidates) == ["F-0001"] and ollama.requests == []


def test_fallback_when_the_model_list_gives_no_digest(kb, capsys):
    fake = FakeOllama(models={MODEL: ""}).start()
    try:
        embed_config(kb, fake.url)
        kb.add("F-0001", "motor", E1, title="Motor warm-up")
        kb.add("F-0002", "drift", N, title="Motor drift")
        capsys.readouterr()
        _selection, label = select_with(kb, "F-0002")
    finally:
        fake.stop()
    assert label == f"bm25 (the ollama model list at {fake.url} gives {MODEL} no digest)"
    assert capsys.readouterr().err.startswith("similarity: BM25 (")


def test_fallback_when_an_embed_request_fails_partway_and_the_rest_is_cached(kb, capsys, ollama):
    add_many(kb, 70)
    kb.add("F-9001", "self", "The pump feature 9001 works.", title="Feature 9001",
           evidence=NEW_EVIDENCE)
    ollama.fail_from = 3  # the query and the first chunk answer; the second chunk fails
    embed_config(kb, ollama.url)

    capsys.readouterr()
    selection, label = select_with(kb, "F-9001")
    assert [len(inputs) for inputs in ollama.requests] == [1, 64, 6]
    assert capsys.readouterr().err.startswith(f"similarity: BM25 (the embed request to {ollama.url} failed (")
    assert label.startswith(f"bm25 (the embed request to {ollama.url} failed (")
    # the whole check fell back: BM25 scores every finding sharing "pump", 30 of them in budget
    assert len(selection.candidates) + len(selection.over_budget) == 70

    capsys.readouterr()
    ollama.fail_from = None
    _again, label = select_with(kb, "F-9001")
    assert label == f"embed {MODEL}"
    assert [len(inputs) for inputs in ollama.requests[3:]] == [6]  # only the uncached chunk
    assert "similarity" not in capsys.readouterr().err


def test_empty_embedding_model_uses_bm25_quietly(kb, capsys, ollama):
    embed_config(kb, ollama.url, model="")
    kb.add("F-0001", "motor", E1, title="Motor warm-up")
    kb.add("F-0002", "tray", E2, title="Media tray")
    kb.add("F-0003", "drift", N, title="Motor drift", evidence=NEW_EVIDENCE)
    view = load_view(kb.cfg)
    finding = next(f for f in view.findings if f.file_id == "F-0003")

    capsys.readouterr()
    selection, label = select_with(kb, "F-0003")
    assert label == "bm25"
    assert ids(selection.candidates) == ids(select_candidates(view, finding, 30, 0.2, link_bonus=0.15).candidates)
    assert ids(selection.candidates) == ["F-0001"]  # BM25 unchanged: no shared token, no candidate
    assert ollama.tags_calls == 0 and ollama.requests == []
    assert capsys.readouterr().err == ""  # "" is a configuration, not a fallback


def test_a_fallback_is_recorded_in_the_check_log(ekb, ollama):
    ekb.add("F-0001", "motor", E1, title="Motor warm-up")
    ekb.add("F-0002", "drift", N, title="Motor drift")
    ollama.stop()

    assert run(ekb, "check", "F-0002") == 0
    record, = check_log(ekb)
    assert record["similarity"] == f"bm25 (ollama not reachable at {ollama.url})"
    assert run(ekb, "validate") == 0  # a fallback is not an error and leaves no unchecked item


# --- §6.1 the vector cache ----------------------------------------------------------------------


def test_the_second_run_embeds_nothing(kb, ollama):
    embed_config(kb, ollama.url)
    kb.add("F-0001", "motor", E1, title="Motor warm-up")
    kb.add("F-0002", "drift", N, title="Motor drift")

    first, label = select_with(kb, "F-0002")
    assert label == f"embed {MODEL}" and [len(inputs) for inputs in ollama.requests] == [1, 1]
    again, _label = select_with(kb, "F-0002")  # a fresh Checker: nothing in this process's memory
    assert len(ollama.requests) == 2 and ids(again.candidates) == ids(first.candidates)


def test_a_digest_change_embeds_the_documents_again(kb, ollama):
    embed_config(kb, ollama.url)
    kb.add("F-0001", "motor", E1, title="Motor warm-up")
    kb.add("F-0002", "drift", N, title="Motor drift")
    select_with(kb, "F-0002")
    assert len(ollama.requests) == 2

    ollama.models = {MODEL: "b" * 64}  # the model was re-pulled: a new digest, a new cache key
    _selection, label = select_with(kb, "F-0002")
    assert label == f"embed {MODEL}"
    assert [len(inputs) for inputs in ollama.requests[2:]] == [1, 1]  # the query and the document


def test_cached_vectors_are_keyed_by_model_digest_and_role(kb, ollama):
    embed_config(kb, ollama.url)
    kb.add("F-0001", "motor", E1, title="Motor warm-up")
    kb.add("F-0002", "drift", N, title="Motor drift")
    select_with(kb, "F-0002")

    with sqlite3.connect(kb.root / ".kblam" / "embeddings.sqlite") as conn:
        rows = conn.execute("SELECT model, digest, role, COUNT(*) FROM vectors "
                            "GROUP BY model, digest, role").fetchall()
    assert sorted(rows) == [(MODEL, DIGEST, "document", 1), (MODEL, DIGEST, "query", 1)]


def test_requests_carry_at_most_64_inputs_and_the_budget_still_applies(kb, ollama):
    embed_config(kb, ollama.url)
    add_many(kb, 130)
    kb.add("F-9001", "self", "The pump feature 9001 works.", title="Feature 9001")

    selection, _label = select_with(kb, "F-9001")
    assert [len(inputs) for inputs in ollama.requests] == [1, 64, 64, 2]
    # every one of the 130 scored a cosine, so the budget decides: 30 candidates, 100 over it
    assert len(selection.candidates) == 30 and len(selection.over_budget) == 100


def test_the_prefixes_wrap_the_title_and_claim_bm25_scores(kb, ollama):
    embed_config(kb, ollama.url, embedding_query_prefix="Q: {text}", embedding_document_prefix="D: {text}")
    kb.add("F-0001", "motor", E1, title="Motor warm-up")
    kb.add("F-0002", "drift", N, title="Motor drift")

    select_with(kb, "F-0002")
    assert ollama.requests[0] == [f"Q: Motor drift\n{N}"]
    assert ollama.requests[1] == [f"D: Motor warm-up\n{E1}"]


# --- put: before the lock, and under it ---------------------------------------------------------


def do_put(kb, path):
    return put(kb.cfg, path, client_factory=kb.factory)


def test_put_embeds_before_the_lock_and_only_new_documents_under_it(ekb, ollama, monkeypatch):
    ekb.add("F-0001", "motor", E1, title="Motor warm-up")
    lock_file = ekb.root / ".kblam" / "lock"
    seen: list[tuple[list[str], bool]] = []
    ollama.on_request = lambda inputs: seen.append((inputs, lock_file.exists()))
    real_lock = store.kb_lock

    @contextmanager
    def lock_after_another_writer(cfg, command):
        if command.startswith("put") and not getattr(lock_after_another_writer, "done", False):
            lock_after_another_writer.done = True
            ekb.add("F-0002", "tray", E2, title="Media tray")  # lands between the ask and the lock
        with real_lock(cfg, command):
            yield

    monkeypatch.setattr(store, "kb_lock", lock_after_another_writer)
    ekb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    result = do_put(ekb, stage(ekb, "F-0003", "drift", N, title="Motor drift"))

    assert kinds(result) == [("same_fact", "reject")]  # decided against the tree under the lock
    before = [inputs for inputs, locked in seen if not locked]
    under = [inputs for inputs, locked in seen if locked]
    assert before == [[f"task: search result | query: Motor drift\n{N}"],
                      [f"title: none | text: Motor warm-up\n{E1}"]]
    # under the lock: N and F-0001 are cached; only the finding the tree gained meanwhile is embedded
    assert under == [[f"title: none | text: Media tray\n{E2}"]]


def test_a_failed_under_lock_embed_makes_the_whole_check_bm25(ekb, ollama, monkeypatch, capsys):
    """The pre-lock pass embeds; the under-lock embed of a finding the tree gained fails. The
    under-lock ranking is then BM25 over every candidate, never a mix of cosines and BM25."""
    ekb.write("evidence/new-run/log.txt", "x\n")
    ekb.add("F-0001", "motor", E1, title="Motor warm-up")
    real_lock = store.kb_lock

    @contextmanager
    def lock_after_another_writer(cfg, command):
        if command.startswith("put") and not getattr(lock_after_another_writer, "done", False):
            lock_after_another_writer.done = True
            ekb.add("F-0002", "tray", E2, title="Media tray")  # lands between the ask and the lock
            ollama.fail_from = len(ollama.requests) + 1          # its embed request fails
        with real_lock(cfg, command):
            yield

    monkeypatch.setattr(store, "kb_lock", lock_after_another_writer)
    ekb.fake.relations[(E1, N)] = ("same_fact", 0.93, 0.91)
    capsys.readouterr()
    result = do_put(ekb, stage(ekb, "F-0003", "drift", N, title="Motor drift", evidence=NEW_EVIDENCE))

    assert kinds(result) == [("same_fact", "reject")]
    assert [len(inputs) for inputs in ollama.requests] == [1, 1, 1]
    assert ollama.requests[-1] == [f"title: none | text: Media tray\n{E2}"]
    assert capsys.readouterr().err.count("similarity: BM25 (") == 1  # one line for the whole put
    record, = check_log(ekb)  # the mechanism that produced the candidate set the check decided on
    assert record["similarity"].startswith(f"bm25 (the embed request to {ollama.url} failed (")
    candidate, = record["candidates"]  # F-0002 shares no token, so BM25 is not a candidate of it
    assert candidate["id"] == "F-0001" and "(embed)" not in candidate["reasons"][0]


# --- configuration and the cosine ---------------------------------------------------------------


@pytest.mark.parametrize("line, message", [
    ('embedding_model = 7', "[jev] embedding_model must be str"),
    ('ollama_url = "127.0.0.1:11434"', "[jev] ollama_url must be an http:// or https:// URL"),
    ('embedding_query_prefix = "query: "',
     "[jev] embedding_query_prefix must contain the placeholder {text}"),
    ('embedding_document_prefix = "title: none"',
     "[jev] embedding_document_prefix must contain the placeholder {text}"),
])
def test_the_embedding_config_values_are_validated(kb, line, message):
    kb.write("kblam.toml", KBLAM_TOML + "\n[jev]\n" + JEV_TOML + line + "\n" + PROMPT_TOML)
    with pytest.raises(ConfigError, match=re.escape(message)):
        jev_settings(kb.cfg)


def test_scores_are_cosines_when_ollama_returns_unnormalised_vectors(kb, ollama):
    """The embedder normalises each vector once (memory holds unit vectors, the cache what ollama
    returned), so a score is the cosine whether the vector came from a request or from the cache."""
    ollama.normalise = False
    ollama.vector_for = by_keyword(("heater", [30.0, 40.0, 0.0]), ("pump", [0.0, 5.0, 0.0]),
                                   ("valve", [0.0, 2.0, 0.0]))
    embed_config(kb, ollama.url)
    kb.add("F-0001", "heater", "The heater warms the block.")
    kb.add("F-0002", "pump", "The pump moves the coolant.")
    kb.add("F-0003", "valve", "The valve opens at start.")
    for _ in range(2):  # the second selection reads every vector from .kblam/embeddings.sqlite
        selection, _label = select_with(kb, "F-0003")
        assert {c.finding_id: round(c.similar, 12) for c in selection.candidates} == {"F-0002": 1.0, "F-0001": 0.8}
    assert len(ollama.requests) == 2  # the first selection's query and documents; the second sent nothing


def test_cosine_is_a_dot_product_of_normalised_vectors():
    assert cosine([3.0, 4.0], [3.0, 4.0]) == 1.0        # normalised defensively
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    with pytest.raises(EmbedUnavailable, match="vectors of 2 and 3 numbers"):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])
