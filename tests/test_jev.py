"""The M3 Jev client against a fake OpenRouter transport: requests, parsing, cache, retries, key
hygiene, the cost log and `kblam cost`. Nothing here touches the network."""

from __future__ import annotations

import json
import re
import sqlite3
import tomllib
from pathlib import Path

import httpx2
import pytest

from kblam import cli, jev, jev_prompts
from kblam.finding import Finding
from kblam.jev import JevClient, JevUnavailable, PairCache, Side, cost_summary

from conftest import DEFAULT_PROMPT_ID, KBLAM_TOML, NO_EMBEDDINGS, PROMPT_TOML, TEMPLATE_TOML

KEY = "sk-or-v1-TESTKEY0123456789abcdef"
SERVED = "typesafe/jev-1.13-20260917"
# The rest of the fixtures' [jev] section: NO_EMBEDDINGS (conftest) opens that table and sets
# embedding_model, so these keys are appended inside it rather than repeating the [jev] header.
JEV_TOML = """
endpoint = "https://openrouter.ai/api/v1/systemone"
model = "typesafe/jev-1.13"
expected_served_model = "typesafe/jev-1.13-20260917"
key_env = "OPENROUTER_API_KEY"
"""

# The default config's questions as they go on the wire, written out in full (tests/conftest.py takes
# the prompt tables from the template kblam init writes). This is the v2 wording with the one agreed
# change: the relation instructions' scope example. Editing the template must fail this test — the
# prompt is sent verbatim, and the prompt id (§6.2) changes with every character of it.
DEFAULT_RELATION_QUESTION = {
    'type': 'choice',
    'instructions': '`existing` and `new` each hold one finding: a `claim` and the `scope` (products, versions or components) the claim applies to. Which option describes the claim in `new` relative to the claim in `existing`?',
    'criteria': {
        'same_fact': {
            'what': 'Everything the claim in `new` states is already stated by the claim in `existing`: the same fact in the same or in different words, or a part of it.',
            'not_for': 'A claim in `new` that adds, changes or conflicts with a detail of the claim in `existing`.',
            'examples': ["'The document feeder holds up to 50 sheets' and 'Up to 50 sheets fit in the document feeder'"],
        },
        'restates_and_extends': {
            'what': 'The claim in `new` restates the fact in `existing` and adds detail to it: a further property, condition, cause or consequence of that same fact.',
            'not_for': 'A claim in `new` that changes or conflicts with the claim in `existing`, or that states a different fact about the same subject.',
            'examples': ["'The document feeder holds up to 50 sheets' and 'The document feeder holds up to 50 sheets of 80 gsm paper; heavier paper lowers the limit'"],
        },
        'cannot_both_be_true': {
            'what': 'The claims are about the same subject and say things about it that cannot both hold at the same time.',
            'not_for': 'Claims that differ only in wording, or that describe different aspects of the subject.',
            'examples': ["'The platen lamp is switched by the main controller' and 'The platen lamp is switched by a separate power board'"],
        },
        'compatible_same_subject': {
            'what': 'The claims are about the same subject, both can hold at the same time, and `new` states a different fact from `existing` rather than restating it.',
            'not_for': 'A restatement of the fact in `existing`, or claims about different subjects.',
            'examples': ["'The document feeder holds up to 50 sheets' and 'The document feeder has a jam sensor at its exit roller'"],
        },
        'unrelated': {
            'what': 'The claims are about different subjects.',
            'not_for': 'Claims about one subject, whether they agree, extend each other or conflict.',
            'examples': ["'The document feeder holds up to 50 sheets' and 'The network interface supports IPv6'"],
        },
    },
}

DEFAULT_REVISION_QUESTION = {
    'type': 'noul',
    'instructions': 'Does the `claim` in `new` describe a correction or revision of an earlier claim, rather than stating a fact directly?',
    'criteria': {
        'true': 'The claim says that an earlier claim was wrong, changed or replaced, and gives the corrected version.',
        'false': 'The claim states a fact directly, without referring to an earlier claim.',
    },
}


def side(finding_id: str, claim: str, scope=("MX-200",)) -> Side:
    return Side.of(Finding(path=f"x/{finding_id}.md", raw=b"", file_id=finding_id,
                           meta={"scope": list(scope)}, claim=claim))


E = side("F-0001", "The motor reaches steady output after 90 seconds.")
N = side("F-0002", "Motor output is steady after 90 seconds of warm-up.")


class FakeJev:
    """An OpenRouter /api/v1/systemone stand-in. `queue` holds per-request overrides: an exception
    to raise, or (status, json body); after it empties, every request gets a normal answer."""

    def __init__(self, queue=(), served=SERVED, cost=0.0000168):
        self.queue = list(queue)
        self.served = served
        self.cost = cost
        self.requests: list[tuple[httpx2.Request, dict]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append((request, body))
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, Exception):
                raise item
            status, payload = item
            return httpx2.Response(status, json=payload)
        return httpx2.Response(200, json=self.answer(body))

    def answer(self, body: dict) -> dict:
        answers = {}
        for name, question in body["questions"].items():
            if question["type"] == "choice":
                answers[name] = {"type": "choice", "choice": "same_fact", "confidence": 0.91,
                                 "probabilities": {"same_fact": 0.93, "restates_and_extends": 0.01,
                                                   "cannot_both_be_true": 0.01,
                                                   "compatible_same_subject": 0.04, "unrelated": 0.01}}
            else:
                answers[name] = {"type": "noul", "noul": 0.87}
        usage = {"input_tokens": 400, "output_tokens": 8}
        if self.cost is not None:
            usage["cost"] = self.cost
        return {"id": "gen-dec-1789738314-abc", "provider": "TypeSafe", "model": self.served,
                "answers": answers, "usage": usage}


def write_config(kb, prompt: str = PROMPT_TOML, extra: str = "") -> None:
    """The fixtures' kblam.toml with `prompt` as its [jev.prompt] tables (default: the template's)."""
    kb.write("kblam.toml", KBLAM_TOML + NO_EMBEDDINGS + JEV_TOML + extra + prompt)


@pytest.fixture
def jkb(kb, monkeypatch):
    write_config(kb)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    return kb


def client(kb, fake: FakeJev, sleeps: list | None = None, **kw) -> JevClient:
    sleeps = [] if sleeps is None else sleeps
    return JevClient(kb.cfg, transport=httpx2.MockTransport(fake), sleep=sleeps.append, **kw)


def log_lines(kb) -> list[dict]:
    path = kb.root / ".kblam" / "calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_default_template_prompt_is_sent_verbatim(kb, monkeypatch):
    """The body for the default config, whole: the option keys and question types are code's, the text
    is the template's and goes on the wire unchanged (SPEC §6.2)."""
    kb.write("kblam.toml", TEMPLATE_TOML)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    fake = FakeJev()
    with JevClient(kb.cfg, transport=httpx2.MockTransport(fake), sleep=lambda s: None) as c:
        c.ask_relation(E, N)
        c.ask_revision(N)
    assert [body["questions"] for _, body in fake.requests] == [{"relation": DEFAULT_RELATION_QUESTION},
                                                               {"revision": DEFAULT_REVISION_QUESTION}]


def test_the_template_prompt_carries_the_code_owned_keys():
    relation, revision = jev_prompts.load_questions(tomllib.loads(TEMPLATE_TOML)["jev"]["prompt"])
    assert tuple(relation["criteria"]) == jev_prompts.RELATION_OPTIONS
    assert tuple(revision["criteria"]) == jev_prompts.REVISION_ANSWERS == ("true", "false")
    assert (relation["type"], revision["type"]) == ("choice", "noul")
    assert jev_prompts.prompt_id(relation, revision) == DEFAULT_PROMPT_ID


def test_prompt_id_ignores_formatting_and_follows_every_character(kb, monkeypatch):
    write_config(kb)
    first = jev.jev_settings(kb.cfg).prompt_id
    assert first == DEFAULT_PROMPT_ID

    # Same values, different TOML: another quote style, another key order, padded whitespace.
    reformatted = PROMPT_TOML.replace(
        'what = "The claims are about different subjects."\n'
        'not_for = "Claims about one subject, whether they agree, extend each other or conflict."',
        "not_for   = 'Claims about one subject, whether they agree, extend each other or conflict.'\n"
        "what      = 'The claims are about different subjects.'")
    assert reformatted != PROMPT_TOML
    write_config(kb, reformatted)
    assert jev.jev_settings(kb.cfg).prompt_id == first

    write_config(kb, PROMPT_TOML.replace("The claims are about different subjects.",
                                         "The claims are about entirely different subjects."))
    assert jev.jev_settings(kb.cfg).prompt_id != first

    write_config(kb)
    monkeypatch.setattr(jev_prompts, "SHAPE_VERSION", 2)  # a state shape is part of the identity too
    assert jev.jev_settings(kb.cfg).prompt_id != first


@pytest.mark.parametrize("old, new, message", [
    ("[jev.prompt.relation.criteria.unrelated]\n", "[jev.prompt.relation.criteria.other]\n",
     "[jev.prompt.relation.criteria] keys must be exactly same_fact, restates_and_extends, "
     "cannot_both_be_true, compatible_same_subject, unrelated (missing unrelated; unknown other)"),
    ("[jev.prompt.relation]\n", "[jev.prompt.relation]\ntype = \"choice\"\n",
     "[jev.prompt.relation] keys must be exactly instructions, criteria (unknown type)"),
    ('not_for = "Claims about one subject, whether they agree, extend each other or conflict."',
     'not_for = ""',
     "[jev.prompt.relation.criteria.unrelated] not_for must be a non-empty string"),
    ("""examples = ["'The document feeder holds up to 50 sheets' and 'The network interface supports IPv6'"]""",
     'examples = "one example"',
     "[jev.prompt.relation.criteria.unrelated] examples must be a list of strings"),
    ("false = \"The claim states a fact directly", "maybe = \"The claim states a fact directly",
     "[jev.prompt.revision.criteria] keys must be exactly true, false (missing false; unknown maybe)"),
])
def test_the_prompt_tables_are_validated(kb, old, new, message):
    assert old in PROMPT_TOML  # the mutation must bite, not silently miss
    write_config(kb, PROMPT_TOML.replace(old, new))
    with pytest.raises(jev.ConfigError, match=re.escape(message)):
        jev.jev_settings(kb.cfg)


def test_a_config_without_the_prompt_tables_is_a_config_error(kb):
    kb.write("kblam.toml", KBLAM_TOML + NO_EMBEDDINGS + JEV_TOML)
    with pytest.raises(jev.ConfigError,
                       match=re.escape("[jev] has no [jev.prompt] table: the Jev questions now live in "
                                       "kblam.toml under [jev.prompt.relation] and [jev.prompt.revision]")):
        jev.jev_settings(kb.cfg)


def test_a_legacy_prompt_version_is_a_config_error_naming_the_move(jkb):
    toml = (jkb.root / "kblam.toml").read_text(encoding="utf-8")
    jkb.write("kblam.toml", toml.replace('key_env = "OPENROUTER_API_KEY"',
                                         'key_env = "OPENROUTER_API_KEY"\nprompt_version = 2'))
    with pytest.raises(jev.ConfigError,
                       match=re.escape("[jev] prompt_version is gone: the Jev questions now live in "
                                       "kblam.toml under [jev.prompt.relation] and [jev.prompt.revision], "
                                       "and [jev.thresholds] carries prompt_id")):
        JevClient(jkb.cfg)


def test_an_old_schema_pair_cache_is_reported_not_dropped(jkb):
    path = jkb.root / ".kblam" / "pairs.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE answers (expected_model TEXT NOT NULL, prompt_version INTEGER NOT NULL, "
                     "kind TEXT NOT NULL, existing_fp TEXT NOT NULL, new_fp TEXT NOT NULL, "
                     "served_model TEXT NOT NULL, answer TEXT NOT NULL, call TEXT NOT NULL, "
                     "created TEXT NOT NULL, "
                     "PRIMARY KEY (expected_model, prompt_version, kind, existing_fp, new_fp))")
        conn.execute("INSERT INTO answers VALUES ('m', 2, 'relation', 'fp1', 'fp2', 'm', '{}', '{}', 'now')")
    with pytest.raises(jev.CacheSchemaError, match=re.escape("keyed by an integer prompt_version")):
        PairCache(path)
    with pytest.raises(jev.CacheSchemaError, match="Delete"):  # a client reports it, never replaces the file
        JevClient(jkb.cfg)
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(answers)")}
    assert {"prompt_version", "answer"} <= columns


def test_relation_request_shape(jkb):
    fake = FakeJev()
    with client(jkb, fake) as c:
        c.ask_relation(E, N)
    (request, body), = fake.requests
    assert str(request.url) == "https://openrouter.ai/api/v1/systemone"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == {"existing": {"claim": E.claim, "scope": ["MX-200"]},
                             "new": {"claim": N.claim, "scope": ["MX-200"]}}
    question = body["questions"]["relation"]
    assert list(body["questions"]) == ["relation"]
    assert question["type"] == "choice"
    assert tuple(question["criteria"]) == jev_prompts.RELATION_OPTIONS
    assert "`existing`" in question["instructions"] and "`new`" in question["instructions"]


def test_relation_options_are_code_owned():
    assert jev_prompts.RELATION_OPTIONS == ("same_fact", "restates_and_extends", "cannot_both_be_true",
                                            "compatible_same_subject", "unrelated")
    assert DEFAULT_RELATION_QUESTION["criteria"]  # the wording was replaced, never dropped
    for option in DEFAULT_RELATION_QUESTION["criteria"].values():
        assert set(option) == {"what", "not_for", "examples"} and option["examples"]


def test_revision_request_shape(jkb):
    fake = FakeJev()
    with client(jkb, fake) as c:
        c.ask_revision(N)
    (_, body), = fake.requests
    assert body["state"] == {"new": {"claim": N.claim, "scope": ["MX-200"]}}
    question = body["questions"]["revision"]
    assert question["type"] == "noul"
    assert set(question["criteria"]) == {"true", "false"}


def test_response_parsing_keeps_openrouter_fields(jkb):
    with client(jkb, FakeJev()) as c:
        relation = c.ask_relation(E, N)
        revision = c.ask_revision(N)
    assert (relation.existing, relation.new, relation.winner) == ("F-0001", "F-0002", "same_fact")
    assert relation.probabilities["same_fact"] == 0.93 and relation.confidence == 0.91
    call = relation.call
    assert (call.served_model, call.input_tokens, call.output_tokens) == (SERVED, 400, 8)
    assert (call.cost, call.generation_id, call.provider) == (0.0000168, "gen-dec-1789738314-abc", "TypeSafe")
    assert (call.attempts, call.cached, call.model_mismatch) == (1, False, False)
    assert revision.noul == 0.87 and not hasattr(revision, "confidence")
    record = log_lines(jkb)[0]
    assert record["status"] == "ok" and record["cost"] == 0.0000168
    assert (record["existing_id"], record["existing_fp"], record["new_id"], record["new_fp"]) == \
           ("F-0001", E.fingerprint, "F-0002", N.fingerprint)
    assert E.claim not in json.dumps(log_lines(jkb))  # no finding text in the log


def test_missing_cost_is_none(jkb):
    with client(jkb, FakeJev(cost=None)) as c:
        assert c.ask_revision(N).call.cost is None


def test_cache_hit_makes_no_request(jkb):
    fake = FakeJev()
    with client(jkb, fake) as c:
        first = c.ask_relation(E, N)
        c.ask_revision(N)
    with client(jkb, fake) as c:  # a new client: the cache is on disk
        again = c.ask_relation(E, N)
        c.ask_revision(N)
    assert len(fake.requests) == 2
    assert again.winner == first.winner and again.call.cached and again.call.served_model == SERVED
    assert [r["status"] for r in log_lines(jkb)] == ["ok", "ok", "cache_hit", "cache_hit"]


def test_cache_hit_needs_no_key(jkb, monkeypatch):
    fake = FakeJev()
    with client(jkb, fake) as c:
        c.ask_revision(N)
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with client(jkb, fake) as c:
        assert c.ask_revision(N).call.cached


def test_cache_invalidation(jkb, monkeypatch):
    fake = FakeJev()
    with client(jkb, fake) as c:
        c.ask_relation(E, N)
        c.ask_relation(N, E)  # directional: the reverse pair is a different question
        c.ask_relation(E, side("F-0002", N.claim + " At 4000 rpm."))  # new fingerprint
        assert len(fake.requests) == 3
        c.ask_relation(E, N)
        assert len(fake.requests) == 3

    toml = (jkb.root / "kblam.toml").read_text(encoding="utf-8")
    jkb.write("kblam.toml", toml.replace("Which option describes the claim",
                                         "Which of the options describes the claim"))
    assert jev.jev_settings(jkb.cfg).prompt_id != DEFAULT_PROMPT_ID
    with client(jkb, fake) as c:  # other wording: the cached answer is not this prompt's answer
        c.ask_relation(E, N)
    assert len(fake.requests) == 4

    jkb.write("kblam.toml", toml.replace("jev-1.13-20260917", "jev-1.14-20261101"))
    fake.served = "typesafe/jev-1.14-20261101"
    with client(jkb, fake) as c:
        c.ask_relation(E, N)
    assert len(fake.requests) == 5


def test_link_bonus_defaults_to_the_spec_value_and_must_not_be_negative(jkb):
    assert jev.jev_settings(jkb.cfg).link_bonus == jev.DEFAULT_JEV["link_bonus"] == 0.15
    write_config(jkb, extra="link_bonus = 0\n")
    assert jev.jev_settings(jkb.cfg).link_bonus == 0.0
    write_config(jkb, extra="link_bonus = -0.1\n")
    with pytest.raises(jev.ConfigError, match=r"\[jev\] link_bonus must be >= 0"):
        jev.jev_settings(jkb.cfg)


def test_served_model_mismatch_is_reported_and_not_reused(jkb):
    fake = FakeJev(served="typesafe/jev-1.14-20261101")
    with client(jkb, fake) as c:
        result = c.ask_relation(E, N)
        assert result.winner == "same_fact"  # the call still returns
        assert result.call.model_mismatch and result.call.served_model == "typesafe/jev-1.14-20261101"
        c.ask_relation(E, N)  # the stored row was served by another model, so it is asked again
    assert len(fake.requests) == 2
    assert [r["model_mismatch"] for r in log_lines(jkb)] == [True, True]


def test_retry_then_success(jkb):
    sleeps = []
    fake = FakeJev(queue=[(429, {"error": {"code": 429, "message": "Rate limit exceeded"}}),
                          (503, {"error": {"code": 503, "message": "unavailable"}})])
    with client(jkb, fake, sleeps) as c:
        result = c.ask_revision(N)
    assert result.call.attempts == 3 and len(fake.requests) == 3 and len(sleeps) == 2
    assert log_lines(jkb)[-1]["attempts"] == 3


def test_retries_exhausted_raise_jev_unavailable(jkb):
    sleeps = []
    request = httpx2.Request("POST", "https://openrouter.ai/api/v1/systemone")
    fake = FakeJev(queue=[(429, {"error": {"code": 429, "message": "Rate limit exceeded"}}),
                          (529, {"error": {"code": 529, "message": "Provider overloaded"}}),
                          httpx2.ReadTimeout("timed out", request=request),
                          (502, {"error": {"code": 502, "message": "Provider returned error"}})])
    with client(jkb, fake, sleeps) as c:
        with pytest.raises(JevUnavailable, match="failed after 4 attempt"):
            c.ask_relation(E, N)
    assert len(fake.requests) == 4 and len(sleeps) == 3
    assert sleeps[1] > sleeps[0] * 0.7  # backoff grows (jitter subtracts up to 25%)
    record = log_lines(jkb)[-1]
    assert (record["status"], record["attempts"]) == ("error", 4)
    with client(jkb, FakeJev()) as c:  # a failure is not cached
        assert not c.ask_relation(E, N).call.cached


def test_client_error_is_not_retried_and_key_never_leaks(jkb, capsys):
    echo = {"error": {"code": 401, "message": f"Invalid key {KEY}", "metadata": None}}
    fake = FakeJev(queue=[(401, echo)])
    c = client(jkb, fake)
    with pytest.raises(JevUnavailable) as caught:
        c.ask_relation(E, N)
    message = str(caught.value)
    assert "401" in message and "check the OpenRouter key" in message
    assert len(fake.requests) == 1
    assert KEY not in message and KEY not in repr(caught.value)
    assert KEY not in (jkb.root / ".kblam" / "calls.jsonl").read_text(encoding="utf-8")
    assert KEY not in repr(c) and KEY not in repr(c._key) and KEY not in str(vars(c))
    c.close()
    print(message)
    assert KEY not in capsys.readouterr().out


def machine_config(home, **values: str) -> None:
    """Write the per-machine settings file, ~/kblam/config.toml, in the tests' scratch home directory."""
    lines = "".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items())
    (home / "kblam").mkdir(exist_ok=True)
    (home / "kblam" / "config.toml").write_text("[jev]\n" + lines, encoding="utf-8")


def set_key_file(home, path) -> None:
    """A key_file other than the default is set per machine, never in kblam.toml (SPEC §9)."""
    machine_config(home, key_file=path)


def test_key_file_fallback(jkb, monkeypatch, tmp_path, home):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    key_file = tmp_path / "jev!.txt"
    key_file.write_text(KEY + "\n", encoding="utf-8")
    set_key_file(home, key_file.as_posix())
    fake = FakeJev()
    with client(jkb, fake) as c:
        c.ask_revision(N)
    assert fake.requests[0][0].headers["authorization"] == f"Bearer {KEY}"


def test_bad_key_file_message_hides_content(jkb, monkeypatch, tmp_path, home):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    key_file = tmp_path / "key.txt"
    key_file.write_text(f"OPENROUTER_API_KEY = {KEY}\n", encoding="utf-8")
    set_key_file(home, key_file.as_posix())
    with client(jkb, FakeJev()) as c:
        with pytest.raises(JevUnavailable, match="must contain only the API key") as caught:
            c.ask_revision(N)
    assert KEY not in str(caught.value)


def test_no_key_at_all(jkb, monkeypatch, home):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with client(jkb, FakeJev()) as c:
        with pytest.raises(JevUnavailable, match="no Jev API key") as caught:
            c.ask_revision(N)
    message = str(caught.value)
    assert "OPENROUTER_API_KEY" in message and "~/kblam/jev!.txt" in message
    assert str(home / "kblam" / "jev!.txt") in message


def test_key_file_default_is_in_the_home_directory(jkb, monkeypatch, home):
    assert Path("~/kblam/jev!.txt").expanduser() == home / "kblam" / "jev!.txt"  # the scratch home, not the real one
    monkeypatch.delenv("OPENROUTER_API_KEY")
    (home / "kblam").mkdir()
    (home / "kblam" / "jev!.txt").write_text(KEY + "\n", encoding="utf-8")
    fake = FakeJev()
    with client(jkb, fake) as c:
        c.ask_revision(N)
    assert fake.requests[0][0].headers["authorization"] == f"Bearer {KEY}"


def test_key_env_wins_over_the_key_file(jkb, home):
    (home / "kblam").mkdir()
    (home / "kblam" / "jev!.txt").write_text("sk-or-v1-FILEKEY\n", encoding="utf-8")
    fake = FakeJev()
    with client(jkb, fake) as c:
        c.ask_revision(N)
    assert fake.requests[0][0].headers["authorization"] == f"Bearer {KEY}"


# --- where the key comes from and where it goes: per machine, not in the committed kblam.toml ---------


def committed(kb, key: str, value: str) -> None:
    """The fixtures' kblam.toml with [jev] `key` set to `value`, replacing the line JEV_TOML has for it."""
    line = f"{key} = {json.dumps(value)}\n"
    pattern = re.compile(rf"^{key} = .*\n", re.M)
    jev_toml = pattern.sub(line, JEV_TOML) if pattern.search(JEV_TOML) else JEV_TOML + line
    kb.write("kblam.toml", KBLAM_TOML + NO_EMBEDDINGS + jev_toml + PROMPT_TOML)


@pytest.mark.parametrize("key, value, message", [
    ("endpoint", "https://collector.example/api/v1/systemone", "[jev] endpoint must be an https://openrouter.ai/ URL"),
    ("endpoint", "http://openrouter.ai/api/v1/systemone", "[jev] endpoint must be an https://openrouter.ai/ URL"),
    ("endpoint", "https://openrouter.ai@collector.example/api/v1/systemone",
     "[jev] endpoint must be an https://openrouter.ai/ URL"),
    ("key_env", "GITHUB_TOKEN", "[jev] key_env must be 'OPENROUTER_API_KEY' here, got 'GITHUB_TOKEN'"),
    ("key_file", "~/.npmrc", "[jev] key_file must be '~/kblam/jev!.txt' here, got '~/.npmrc'"),
    ("ollama_url", "http://ollama.example:11434", "[jev] ollama_url must name this machine here"),
    ("ollama_url", "http://[::1:11434", "[jev] ollama_url must name this machine here"),
])
def test_the_committed_config_cannot_redirect_the_key_or_the_findings(kb, key, value, message):
    """kblam.toml is committed, so it may not choose which secret is sent as the key, nor send the key or the
    findings' text off this machine anywhere but OpenRouter; the message says where such a setting goes."""
    committed(kb, key, value)
    with pytest.raises(jev.ConfigError, match=re.escape(message)) as caught:
        jev.jev_settings(kb.cfg)
    assert f"set it per machine in ~/kblam/config.toml ([jev] {key})" in str(caught.value)


@pytest.mark.parametrize("key, value", [
    ("key_env", "OPENROUTER_API_KEY"),
    ("key_file", "~/kblam/jev!.txt"),
    ("endpoint", "https://openrouter.ai/api/v1/systemone/"),
    ("endpoint", "https://OpenRouter.ai/api/v1/systemone"),
    ("ollama_url", "http://localhost:11434"),
    ("ollama_url", "http://[::1]:11434"),
    ("ollama_url", ""),
])
def test_the_committed_config_keeps_its_defaults_and_local_values(kb, key, value):
    committed(kb, key, value)
    jev.jev_settings(kb.cfg)


def test_the_machine_config_sets_what_kblam_toml_may_not(kb, home):
    committed(kb, "key_env", "OPENROUTER_API_KEY")
    machine_config(home, endpoint="http://127.0.0.1:9/api/v1/systemone", key_env="TEAM_OPENROUTER_KEY",
                   key_file="~/keys/openrouter.txt", ollama_url="http://ollama.lan:11434/")
    settings = jev.jev_settings(kb.cfg)
    assert settings.base_url == "http://127.0.0.1:9/api"
    assert (settings.key_env, settings.key_file) == ("TEAM_OPENROUTER_KEY", "~/keys/openrouter.txt")
    assert settings.ollama_url == "http://ollama.lan:11434"


def test_the_machine_key_env_is_the_one_sent(jkb, home, monkeypatch):
    monkeypatch.setenv("TEAM_OPENROUTER_KEY", "sk-or-v1-TEAMKEY")
    machine_config(home, key_env="TEAM_OPENROUTER_KEY")
    fake = FakeJev()
    with client(jkb, fake) as c:
        c.ask_revision(N)
    assert fake.requests[0][0].headers["authorization"] == "Bearer sk-or-v1-TEAMKEY"


@pytest.mark.parametrize("values, message", [
    ({"endpoint": "https://collector.example/v2/ask"}, "~/kblam/config.toml: [jev] endpoint must end in /v1/systemone"),
    ({"ollama_url": "ollama.lan:11434"}, "~/kblam/config.toml: [jev] ollama_url must be an http:// or https:// URL"),
])
def test_a_malformed_machine_value_names_the_machine_file(kb, home, values, message):
    committed(kb, "key_env", "OPENROUTER_API_KEY")
    machine_config(home, **values)
    with pytest.raises(jev.ConfigError, match=re.escape(message)):
        jev.jev_settings(kb.cfg)


@pytest.mark.parametrize("text, message", [
    ('[jev]\nmodel = "typesafe/other"\n', "unknown key(s) jev.model"),
    ('[kb]\nroot = "notes"\n', "unknown key(s) kb"),
    ("[jev]\nkey_env = 7\n", "each a string"),
    ('jev = "x"\n', "it holds only a [jev] table"),
    ("[jev\n", "config.toml"),
])
def test_a_bad_machine_config_is_a_config_error(kb, home, text, message):
    (home / "kblam").mkdir()
    (home / "kblam" / "config.toml").write_text(text, encoding="utf-8")
    with pytest.raises(jev.ConfigError, match=re.escape(message)) as caught:
        jev.jev_settings(kb.cfg)
    assert "~/kblam/config.toml" in str(caught.value)


def test_unusable_answer_is_unavailable(jkb):
    bad = {"id": "gen-1", "provider": "TypeSafe", "model": SERVED, "usage": {"input_tokens": 5, "output_tokens": 0},
           "answers": {"relation": {"type": "choice", "choice": "maybe", "confidence": 0.5,
                                    "probabilities": {"maybe": 1.0}}}}
    with client(jkb, FakeJev(queue=[(200, bad)])) as c:
        with pytest.raises(JevUnavailable, match="unusable"):
            c.ask_relation(E, N)
    assert log_lines(jkb)[-1]["status"] == "bad_response"


def test_parallel_runner_keeps_order_and_isolates_failures(jkb):
    pairs = [(E, side(f"F-01{i:02d}", f"Claim number {i} about the motor.")) for i in range(8)]
    failing = pairs[3][1].claim

    def handler(request):
        body = json.loads(request.content)
        if body["state"]["new"]["claim"] == failing:
            return httpx2.Response(400, json={"error": {"code": 400, "message": "bad"}})
        return httpx2.Response(200, json=FakeJev().answer(body))

    with JevClient(jkb.cfg, transport=httpx2.MockTransport(handler), sleep=lambda s: None) as c:
        results = c.ask_relations(pairs, workers=6)
    assert [getattr(r, "new", None) for r in results] == [p[1].finding_id if i != 3 else None
                                                          for i, p in enumerate(pairs)]
    assert isinstance(results[3], JevUnavailable)


def test_cost_summary_and_command(jkb, capsys):
    lines = [
        {"ts": "2026-09-21T10:00:00Z", "kind": "relation", "status": "ok", "attempts": 1,
         "input_tokens": 400, "output_tokens": 8, "cost": 0.0000168, "model_mismatch": False},
        {"ts": "2026-09-22T10:00:00Z", "kind": "relation", "status": "ok", "attempts": 2,
         "input_tokens": 600, "output_tokens": 8, "cost": 0.0000252, "model_mismatch": True},
        {"ts": "2026-09-22T10:00:01Z", "kind": "revision", "status": "ok", "attempts": 1,
         "input_tokens": 100, "output_tokens": 2, "model_mismatch": False},
        {"ts": "2026-09-22T10:00:02Z", "kind": "revision", "status": "error", "attempts": 4, "error": "x"},
        {"ts": "2026-09-22T10:00:03Z", "kind": "relation", "status": "cache_hit"},
    ]
    jkb.write(".kblam/calls.jsonl", "".join(json.dumps(r) + "\n" for r in lines) + "not json\n")
    summary = cost_summary(jkb.cfg)
    t = summary.total
    assert (t.requests, t.failed, t.attempts, t.cache_hits) == (4, 1, 8, 1)
    assert (t.input_tokens, t.output_tokens, t.cost_missing, t.mismatches) == (1100, 18, 1, 1)
    assert t.cost == pytest.approx(0.000042)
    assert summary.by_day["2026-09-22"].requests == 3 and summary.by_kind["revision"].failed == 1
    assert summary.bad_lines == 1

    assert cli.main(["--root", str(jkb.root), "cost"]) == 0
    out = capsys.readouterr().out
    assert "kblam cost: 4 request(s), 1 failed, 8 HTTP attempt(s), 1 cache hit(s), 1100 input + 18 output tokens, $0.000042" in out
    assert "  2026-09-21  1 request(s)" in out and "  revision  2 request(s), 1 failed" in out
    assert "1 answered request(s) had no usage.cost" in out


def test_cost_without_log(jkb, capsys):
    assert cli.main(["--root", str(jkb.root), "cost"]) == 0
    assert "no Jev requests logged" in capsys.readouterr().out


def test_jev_smoke_second_run_is_cached(jkb, monkeypatch, capsys):
    fake = FakeJev()
    monkeypatch.setattr(cli, "JevClient",
                        lambda cfg: JevClient(cfg, transport=httpx2.MockTransport(fake), sleep=lambda s: None))
    assert cli.main(["--root", str(jkb.root), "jev-smoke"]) == 0
    first = capsys.readouterr().out
    assert "same_fact" in first and "kblam jev-smoke: 2 request(s)" in first and SERVED in first
    assert cli.main(["--root", str(jkb.root), "jev-smoke"]) == 0
    second = capsys.readouterr().out
    assert "kblam jev-smoke: 0 request(s), cost $0.000000; 2 answer(s) from .kblam/pairs.sqlite" in second
    assert len(fake.requests) == 2
    assert KEY not in first + second
