# desk-jevdocs — TypeSafe/Jev API answers for kblam

Package: kblam. Subject: the System One / Jev HTTP API and the `typesafe_sdk` Python SDK,
as called through OpenRouter, for a knowledge-base finding-contradiction checker.

Evidence base: a local mirror of the vendor pages listed in `REFERENCES.md`; the citation
names used below are that file's "Cited as" column. The mirror was retrieved 2026-09-23 and
is quoted verbatim. Two gaps it did not cover were fetched from the web (the SDK's supported
Python versions from PyPI, in Q2; OpenRouter's own retention and training statements, in Q8);
both are labelled as fetched in their entries. Everything else is mirror-sourced.

## ⬅️ OPEN QUESTIONS

- **Does the `typesafe` provider endpoint on OpenRouter count as a ZDR / no-train endpoint?**
  Q8 establishes that OpenRouter can restrict routing to ZDR endpoints and that TypeSafe offers
  ZDR to enterprise customers, but nothing read so far states the `typesafe` endpoint
  qualifies. OpenRouter's provider table is rendered dynamically and could not be fetched.
  Resolving this needs a browser read of `https://openrouter.ai/docs/features/privacy-and-logging`
  (Claude in Chrome) — an operator-permitted-domain question, not a fetch rung to retry.
- **Source of the `confidence` formula.** TypeSafe documents it as unpublished (Q3). Not
  blocking — the API returns the value — but if kblam ever needs to recompute confidence after
  re-weighting probabilities, that is unanswered.
- **Does jev-dsl's decoder accept OpenRouter's extra top-level response members?** (Q9.5) It
  decodes with named `DecodeError`s including "unexpected" answers, and OpenRouter adds `id`,
  `provider` and `usage.cost` that native TypeSafe does not return. Whether those are tolerated
  is not established by anything read; it is the first thing to test if the Haskell route is
  taken. Resolvable by building the repo and sending one OpenRouter response through
  `jev-dsl-example decode`.
- **No Windows build of jev-dsl has been verified.** (Q9.4) The flake lists no Windows system
  and the repo documents only `nix develop` / `nix-shell`. Whether GHCup's GHC 9.12.3 plus cabal
  builds it natively on Windows is untested by the author and by me.

## INDEX

topic                        entry  subject                                              verified
api/schema                   Q1     POST /v1/systemone request + response schema,        2026-09-22 @ mirror-2026-09-23
                                    limits, error codes
api/sdk-python               Q2     typesafe_sdk: install, clients, base_url/api_key,     2026-09-22 @ mirror-2026-09-23
                                    question classes, result attributes, retries,
                                    exceptions, Windows
api/confidence               Q3     confidence definition, range, relation to             2026-09-22 @ mirror-2026-09-23
                                    probabilities, threshold selection from labels
pairwise/precedents          Q4     entity-alignment + citation-check cookbooks           2026-09-22 @ mirror-2026-09-23
                                    extracted as designs (state shape, exact wording,
                                    thresholds, reported numbers)
prompt-design                Q5     state design and question wording do/don't rules      2026-09-22 @ mirror-2026-09-23
batching/fan-out             Q6     one pair per request vs ~30 candidates in one state   2026-09-22 @ mirror-2026-09-23
versioning/determinism       Q7     model IDs and aliases, pinning, repeatability,        2026-09-22 @ mirror-2026-09-23
                                    temperature/seed absence
legal/data-handling          Q8     TypeSafe and OpenRouter retention and training        2026-09-22 @ mirror-2026-09-23
tooling/jev-dsl              Q9     inanna-malick/jev-dsl evaluated: capabilities,        2026-09-22 @ jev-dsl HEAD 2026-09-22
                                    Windows toolchain, OpenRouter, gaps,                         (v0.1.0.0 unreleased)
                                    ideas worth copying

## Q1 — POST /v1/systemone: request and response schema, limits, error codes

verified: 2026-09-22 @ mirror retrieved 2026-09-23
evidence: verbatim from `openrouter/api_api-reference_systemone_submit-a-system-one-request.md`
(OpenAPI YAML embedded in that page) and `typesafe/api.md`

### Endpoint

`typesafe/api.md` L13-17:

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

Via OpenRouter the base is `https://openrouter.ai/api`, and the SDK appends `/v1/systemone`
— `openrouter/guides_community_typesafe-sdk.md` L17-23:

> OpenRouter's System One API is available at the following base URL:
>
> ```text
> https://openrouter.ai/api
> ```
>
> The SDK appends `/v1/systemone` to the base URL, so requests are sent to `https://openrouter.ai/api/v1/systemone`.

Auth is the OpenRouter key as a bearer token — same file, `:25-27`:

> Use your [OpenRouter API key](https://openrouter.ai/settings/keys) as the `Authorization: Bearer <token>` header. The SDK sets this header from the `apiKey` option (`api_key` in Python) or the `TYPESAFE_API_KEY` environment variable.

### Request body

From the OpenAPI `DecisionsRequest` schema, `openrouter/api_api-reference_systemone_submit-a-system-one-request.md` L337-393:

- `model` (string, **required**)
- `state` (**required**), `anyOf`: `string` | `object` (`additionalProperties: {}`) | `array` (`items: {}`). Description: "The content to evaluate: a plain string, or a JSON object or array of related context."
- `questions` (**required**), `type: object`, `additionalProperties` one of the three question schemas discriminated on `type`
- `provider` — `ProviderPreferences` (routing preferences: `allow_fallbacks`, `data_collection`
  (`allow`/`deny`), `ignore`, `only`, `order`, `max_price`, `quantiles?`, `quantization`,
  `require_parameters`, `sort`, `zdr`, `predefined_*`). See `:689-842`.
- `session_id` (string, maxLength 256) — "A unique identifier for grouping related requests
  (e.g., a conversation or agent workflow). Used for observability grouping in Broadcast and
  private logging; **never sent to the provider**. If provided in both the request body and
  the `x-session-id` header, the body value takes precedence." (`:364-373`)
- `trace` — `TraceConfig`, "Metadata for observability and tracing. Known keys (`trace_id`,
  `trace_name`, `span_name`, `generation_name`, `parent_span_id`) have special handling."
  (`:947-968`)
- `user` (string, maxLength 256)

### Question schemas (all three)

Every question is `{type, instructions, criteria}`. `type` is the discriminator.

**Noul** — `DecisionsNoulQuestion`, `:873-916`. `required: [type, instructions]`.
`type` is the literal `"noul"`. `criteria` is an object with `required: ['true','false']`,
each value `string | object | array`. Note: the OpenAPI marks `criteria` optional at the
question level but **both `true` and `false` required if `criteria` is present**.

**Choice** — `DecisionsChoiceQuestion`, `:843-872`. `required: [type, instructions, criteria]`.
`type` is `"choice"`. `criteria` is an object whose **keys are the option labels** and whose
values are `string | object | array | null`. No per-schema `maxProperties` in the OpenAPI,
but see the documented 255-option limit below.

**Score** — `DecisionsScoreQuestion`, `:917-946`. `required: [type, instructions, criteria]`.
`type` is `"score"`. `criteria` is an **array** (`minItems: 1` in the OpenAPI), ordered, one
entry per level starting at 0.

`instructions` and each criteria value are `string | object | array` in all three types —
"a plain string, or a JSON object or array of structured guidance". This is the escape hatch
for structured questions; see Q5.

### Documented limits

`typesafe/api.md` L125 (Choice):

> A map of option to rubric description; use null when an option needs no extra detail. You can have a maximum of 255 options per Choice.

`typesafe/api.md` L162-163 (Score):

> An ordered array of level descriptions. A Score should have at least two levels; the API accepts up to 10.

`typesafe/models.md` L15-20:

> | Context length              | 64k tokens per request; 32k tokens for `state` plus the longest question                  |

> * **Context length:** Jev ingests the `state` once and evaluates every question against it in parallel. The 64k budget covers the `state` plus all questions combined; the 32k budget applies to the `state` plus the single longest question.

Rate limits, `typesafe/models.md` L14,19:

> | Rate limits                 | 250,000 tokens per second / 1,200 requests per minute                                     |

> * **Rate limits:** Measured in tokens per second and requests per minute. A request over either limit returns `429 Too Many Requests`.

**Questions per request: no numeric cap is documented anywhere in the mirror.** Verified
negative by string search over `typesafe/*.md` and `openrouter/*.md` for
"per request", "maximum of", "max.*questions", "questions per". The only documented per-request
constraint is the token budget above. Cost note, `typesafe/models.md` L18:

> * **Price:** Charged per input token. Output tokens are free.

### Response body

`DecisionsResponse`, `openrouter/api_api-reference_systemone_submit-a-system-one-request.md` L394-436:
`required: [model, answers, usage]`; optional `id`, `provider`.

- `model` (string) — the model that answered. Via OpenRouter this is the **versioned
  snapshot ID**, not what you sent: the OpenRouter guide's example response
  (`openrouter/guides_community_typesafe-sdk.md` L131-141) shows
  `"model": "typesafe/jev-1.13-20260917"` for a request that sent `"model": "jev-1.13"`.
- `id` (string) — OpenRouter generation id, e.g. `gen-dec-1789738314-X5e5eKGQdvR9rblyX250`
- `provider` (string) — `"TypeSafe"`
- `answers` (object) — one entry per question key, same keys you sent
- `usage` — object; `required: [input_tokens, output_tokens]`, plus `cost` (double, USD).
  `openrouter/guides_community_typesafe-sdk.md` L113 — "OpenRouter additionally returns
  `id`, `provider`, and `usage.cost`, which both SDKs pass through without error."

Answer shapes (`:969-1032`):

- **Noul answer**: `required: [type, noul]`. `noul` is a number 0..1 (the probability of "yes").
  **No `confidence` and no `probabilities`** — `typesafe/confidence.md` L145:
  "The answer's `confidence` property collapses that shape into a single number from 0 to 1,
  so you can threshold on it without doing the math yourself. (Noul answers don't carry one.)"
- **Choice answer**: `required: [type, choice]`; optional `probabilities` (object, option →
  number) and `confidence` (number). `choice` is "The highest-probability option."
- **Score answer**: `required: [type, score]`; optional `legend` (object, level index as a
  **string** key → its description), `probabilities` (object, level index as a string key →
  number), `confidence` (number). `score` is "The probability-weighted answer across the
  levels; can land between levels."

Worked response example, `typesafe/api.md` L208-219 (native TypeSafe):

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "is_urgent": {
      "type": "noul",
      "noul": 0.95
    }
  },
  "usage": { "input_tokens": 296, "output_tokens": 20 }
}
```

and `:268-281` for a Choice answer:

```json
    "department": {
      "type": "choice",
      "choice": "billing",
      "probabilities": { "billing": 0.88, "technical": 0.12, "sales": 0.0 },
      "confidence": 0.81
    }
```

Probabilities sum to 1 — `:255`: "Every option mapped to its probability (floats that sum to 1)."

### Error codes

Native TypeSafe, `typesafe/api.md` L329-335:

| Status                     | Meaning                                                                                                                                  |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `401 Unauthorized`         | Missing or invalid API key. Check the `Authorization` header.                                                                            |
| `422 Unprocessable Entity` | The request body failed validation — for example a missing required field or a malformed question. The body details the offending field. |
| `429 Too Many Requests`    | You have exceeded your rate limit. Back off and retry after a short delay.                                                               |
| `529 Overloaded`           | TypeSafe is temporarily overloaded. Retry after a short delay.                                                                           |

Through OpenRouter the same endpoint additionally documents (`..._submit-a-system-one-request.md:213-334`):
`400` Invalid request parameters · `401` Missing Authentication header · `402` Insufficient
credits · `403` Forbidden · `404` Resource not found · `413` Request payload too large ·
`429` Rate limit exceeded · `500` Internal Server Error · `502` Provider returned error ·
`503` Service temporarily unavailable · `524` Request timed out · `529` Provider overloaded.

Every error body has the shape `{"error": {"code": <int>, "message": <string>, "metadata": <object|null>}}`
(e.g. `:1033-1051`). Through OpenRouter the envelope may also carry `openrouter_metadata` and
`user_id` (`:443-457`).

**Note for the implementer: `402` is the OpenRouter-specific code for running out of credits.**
Native TypeSafe does not document it.

## Q2 — `typesafe_sdk` Python SDK

verified: 2026-09-22 @ mirror retrieved 2026-09-23
evidence: verbatim from `typesafe/sdk_python*.md` and
`openrouter/guides_community_typesafe-sdk.md`

### Package, install, import name

Import name is `typesafe_sdk`; **PyPI distribution name is `typesafe-sdk`**.
`typesafe/sdk_python.md` L21-32:

```sh
uv add typesafe-sdk
```

```sh
pip install typesafe-sdk
```

Source: `https://github.com/typesafe-ai/typesafe-sdk-python` (`:11`).

**Supported Python versions: not stated anywhere in the mirror** — verified negative by string
search over `typesafe/*.md` for "Python 3", "requires-python", "3.10" … "3.13".
Resolved from PyPI instead (WebFetch, `https://pypi.org/pypi/typesafe-sdk/json`, fetched
2026-09-22; the mirror lacked it, so this is the one web fetch behind this file):

> **Name:** "typesafe-sdk" — **Version:** "0.7.1" — **Requires Python:** ">=3.10" —
> **Summary:** "Python SDK for TypeSafe AI API."
> Python Language Classifiers: `Python :: 3 :: Only`, `3.10`, `3.11`, `3.12`, `3.13`, `3.14`
> Requires-Dist: `httpx2>=2.0.0`, `pydantic>=2.12.0`, `pydantic-core>=2.41.1`,
> `tenacity>=9.0.0`, `typing-extensions>=4.13.0`

So **Python >= 3.10**, and the latest released version is **0.7.1**, matching the changelog
head (`sdk_python_changelog.md:11`). Note `httpx2` (not `httpx`) is the HTTP layer, which
matches the `httpx2.Timeout` reference in the exceptions page, and `tenacity` is the retry
engine behind `RetryPolicy`.

`pydantic` is a hard dependency as of v0.7.0 — `typesafe/sdk_python_changelog.md` L31-35:

> ### Breaking Changes
>
> * ser/de library has been changed from `msgspec` to `pydantic`

### Windows

**Not documented.** Verified negative: no occurrence of "Windows", "win32", or "platform" in
any `typesafe/sdk_python*.md` file. The transport is `httpx`-derived (the exceptions
page references `httpx2.Timeout`, `typesafe/sdk_python_api_exceptions.md` L248), which
is pure-Python and cross-platform, but the mirror makes no statement. Treat "runs on Windows"
as unverified.

### Clients: sync and async

`typesafe/sdk_python.md` L39-93 — `AsyncTypeSafeClient` (async, used as
`async with ... as client:` and awaited) and `TypeSafeClient` (sync, used as
`with TypeSafeClient() as client:`). Both expose the same `system_one` method and the same
result object. `TypeSafeClient()` also works without a context manager
(`typesafe/sdk_python_usage.md` L54).

### Pointing at OpenRouter

Two equivalent routes.

1. Constructor (`typesafe/sdk_python_usage.md` L162-177):

```python
import os

from typesafe_sdk import Noul, TypeSafeClient

with TypeSafeClient(
    api_key=os.environ["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api",
    model="~typesafe/jev-latest",
) as client:
    result = client.system_one(
        "I was charged twice.",
        {"billing": Noul(instructions="Is this about billing?")},
    )
    print(result.nouls["billing"].noul)
```

2. Environment variables — `openrouter/guides_community_typesafe-sdk.md` L55:

> Both SDKs also read the base URL from the `TYPESAFE_BASE_URL` environment variable, so you can leave the constructor unchanged and set `TYPESAFE_BASE_URL=https://openrouter.ai/api` and `TYPESAFE_API_KEY=<your OpenRouter API key>` instead.

Note the two model-name forms differ between those two sources: `~typesafe/jev-latest` in the
SDK usage guide, plain `jev-1.13` in the OpenRouter guide (which also shows mapping rules —
`openrouter/guides_community_typesafe-sdk.md` L97-101):

> * `jev-1.13` is routed as `typesafe/jev-1.13`.
> * `jev-latest` is routed as `~typesafe/jev-latest`, OpenRouter's alias for the newest Jev release.
> * IDs that already carry an author prefix, such as `typesafe/jev-1.13`, are used as-is.

### Environment variables and defaults

`typesafe/sdk_python_usage.md` L269-274:

| Variable                 | Configures                                          | Default                   |
| ------------------------ | --------------------------------------------------- | ------------------------- |
| `TYPESAFE_API_KEY`       | API key (required)                                  | —                         |
| `TYPESAFE_BASE_URL`      | API root URL                                        | `https://api.typesafe.ai` |
| `TYPESAFE_DEFAULT_MODEL` | Default model                                       | `jev-latest`              |
| `TYPESAFE_LOG_LEVEL`     | `typesafe_sdk` logger level, applied once at import | unset                     |

`typesafe/sdk_python_api_constants.md` L96 — `DEFAULT_TIMEOUT = 10.0`, "Default
timeout in seconds for each HTTP operation."

Key hygiene, `sdk_python_usage.md:278`: leading/trailing whitespace is stripped (including
newlines from key files); empty keys, internal whitespace, control characters and non-ASCII
characters are rejected before sending; an explicitly empty key does not fall back to the env var.

### Question classes

`typesafe/sdk_python_api_types_questions.md`. `Noul`, `Choice`, `Score` are pydantic
models with `additionalProperties: false`.

- `Noul(instructions=None, criteria=None)` — `:265-297`. `type` is `Literal['noul']` defaulting
  to `"noul"`. `instructions` is `JSONContent | None`, **optional in the SDK** (the wire schema
  marks it required). `criteria` is `NoulCriteria | None`, a TypedDict with keys `true` and
  `false`, each `JSONContent | None` (`:50-138`, `:234-261`).
- `Choice(instructions=None, criteria=<required>)` — `:507-547`. `criteria` is
  `Mapping[str, JSONContent | None]` and is **required** (`"required": ["criteria"]`).
- `Score(instructions=None, criteria=<required>)` — `:771-804`. `criteria` is
  `Sequence[JSONContent]` and is **required**; documented at `:855` as "A nonempty, ordered
  list of text, object, or array descriptions, one per score from zero."

`JSONContent = str | dict | list`; `JSONValue` is the recursive JSON scalar/array/object union
(`:157-233`). Raw dicts are also accepted and may be mixed with objects —
`sdk_python_api_types_questions.md:993`: "Question dictionaries include a `type` key:
`\"noul\"`, `\"choice\"`, or `\"score\"`. You can mix dictionaries and question objects in the
same request."

### `system_one` signature and result attributes

Positional and keyword forms both appear: `client.system_one(state, questions)`
(`sdk_python_usage.md:65`) and `client.system_one(state=..., questions=...)`
(`typesafe/confidence.md` L182-183), plus `model=`, `retry=`, `response_model=`,
`extra_body=` (`sdk_python_usage.md:78, 217, 290-302`).

Result attributes, `typesafe/sdk_python.md` L62-64:

```python
print(response.nouls["billing"].noul)
print(response.choices["tone"].choice)
print(response.scores["urgency"].score)
```

So the result is **split by answer kind**: `.nouls`, `.choices`, `.scores` are all keyed by the
question id you chose, and are distinct from the raw `.answers` map. `.nouls[id]` has `.noul`;
`.choices[id]` has `.choice`; `.scores[id]` has `.score`. Additional attributes seen:
`result.request_id` (`sdk_python.md:97`) and `result.raw_http_response` (`sdk_python_usage.md:338`,
`raw_answers = result.raw_http_response.json()["answers"]`). `typesafe/confidence.md` L196
uses the raw-form accessor `response.answers["action"]` then `.confidence` and `.choice`.

`raw_http_response` matters for cost: the SDK's typed result does not carry `usage.cost`, which
is OpenRouter-specific (see Q1). Read it from `result.raw_http_response.json()["usage"]["cost"]`.
**This is inferred from the attribute's documented purpose, not stated verbatim — flag it as
inferred.**

Unknown answer kinds: "The SDK logs a warning and skips unrecognized answer kinds. Use
`raw_http_response` to inspect the complete API response, including those answers"
(`sdk_python_usage.md:329-331`).

### Retries

`typesafe/sdk_python_api_retries.md` L45 — the default `RetryPolicy`:

```python
RetryPolicy(
    max_retries: int = 2,
    backoff_initial: float = 0.5,
    backoff_max: float = 5.0,
    backoff_jitter: float = 0.25,
    http_statuses: set[int] = field(
        default_factory=lambda: {
            408,
            429,
            *range(500, 600),
        }
    ),
    respect_retry_after: bool = True,
    api_connection_error: bool = True,
    api_timeout_error: bool = True,
    exceptions: set[type[BaseException]] = field(default_factory=set),
    predicate: Callable[[BaseException], bool] | None = None,
    timeout: float | None = 30.0,
)
```

Key semantics:

- `max_retries` — `:100`: "Maximum retries after the initial attempt; `0` disables retries."
- `backoff_initial` — `:140`: "First backoff delay in seconds, doubled each attempt up to
  `backoff_max`; zero disables backoff."
- `backoff_jitter` — `:220`: "Fraction of each backoff delay randomly subtracted, between 0 and 1."
- `http_statuses` — `:232`: "HTTP status codes that are retried." The default is
  `{408, 429, *range(500, 600)}`, which covers every code TypeSafe documents as retryable
  (429, 529, and the OpenRouter 5xx set) but **not 400, 401, 402, 403, 404, 413, 422** — those
  raise immediately.
- `respect_retry_after` — `:272`: "Whether to honor `Retry-After` and `retry-after-ms` response headers."
- `timeout` — `:428-430`: "Total retry budget in seconds per SDK call, including the initial
  attempt and delays; `None` disables the limit. Stops before a retry whose delay would reach
  or exceed the budget, re-raising the last error."

`timeout` on `RetryPolicy` is the **whole-call retry budget (30 s default)**, a different thing
from `DEFAULT_TIMEOUT = 10.0`, "Default timeout in seconds for each HTTP operation"
(`sdk_python_api_constants.md:96`).

Pass `retry=` on the client or per call (`sdk_python_usage.md:210-229`):

```python
client = TypeSafeClient(retry=RetryPolicy(max_retries=3, backoff_max=0.2, timeout=1.0))
```

```python
client.system_one(
    state, questions, retry=RetryPolicy(max_retries=3, backoff_max=0.2, timeout=1.0)
)
```

`sdk_python_usage.md:210` — "Invalid API keys raise `TypeSafeError` during client creation,
before any request or retry."

### Exceptions

`typesafe/sdk_python_api_exceptions.md`. Hierarchy:

- `TypeSafeError(Exception)` — base (`:42-48`)
- `TypeSafeAPIError(TypeSafeError)` — "An unsuccessful HTTP response with its body and request
  metadata." Attributes: `status`, `body`, `headers`, `endpoint`, and the property
  `request_id` = "The `x-typesafe-request-id` response header, or `None` if absent." (`:54-148`)
- Subclass per status: `TypeSafeBadRequestError` (400), `TypeSafeAuthenticationError` (401),
  `TypeSafePermissionDeniedError` (403), `TypeSafeNotFoundError` (404),
  `TypeSafeUnprocessableEntityError` (422), `TypeSafeRateLimitError` (429, with
  `retry_after_ms`), `TypeSafeInternalServerError` (5xx) (`:150-216`)
- `TypeSafeAPIConnectionError(TypeSafeError, ConnectionError)` — "A request failed without an
  HTTP response." (`:222-228`)
- `TypeSafeAPITimeoutError(TypeSafeAPIConnectionError, TimeoutError)` — with `.timeout` (`:230-248`)
- `TypeSafeAPIResponseValidationError(TypeSafeAPIError)` — "A successful HTTP response whose
  body was missing or structurally invalid required data." With `.field_path`, "Dotted path to
  the offending field, such as `answers.tone.confidence`." (`:254-288`)

Usage, `sdk_python_usage.md:238-245`:

```python
from typesafe_sdk import TypeSafeAPIError

try:
    client.system_one(state, questions)
except TypeSafeAPIError as error:
    print(error.status, error.request_id)
```

Note the exception set is **not** a 1:1 mirror of the OpenRouter status list: there is no
`TypeSafePaymentRequiredError` (402) class; a 402 would fall to `TypeSafeAPIError`.
**Inferred from the documented class list, not stated verbatim.**

## Q3 — Confidence: definition, range, relation to probabilities, threshold selection

verified: 2026-09-22 @ mirror retrieved 2026-09-23
evidence: verbatim from `typesafe/confidence.md`, `typesafe/api.md`,
`typesafe/model-jaggedness_jev-1.13.md`,
`openrouter/cookbook_evaluate-and-optimize_jev-classification.md`,
`typesafe/cookbooks_entity_alignment.md`,
`typesafe/cookbooks_citation_check.md`,
`typesafe/cookbooks_classification_using_confidence.md`,
`typesafe/cookbooks_consistency_noul_cookbook.md`

### What it is, and its range

`typesafe/api.md` L223:

> Every answer carries a `type` matching its question. Choice and Score answers also carry a `confidence` between 0 to 1, derived from the answer's probability distribution. See [Confidence](/confidence).

**Which primitives return it:**

| Primitive | `probabilities` | `confidence` | Notes |
| --- | --- | --- | --- |
| Noul | no | **no** | the `noul` value *is* the probability of "yes" |
| Choice | yes (option → p, sums to 1) | yes | |
| Score | yes (level index → p, sums to 1) | yes | plus `legend`, plus `score` |

`typesafe/confidence.md` L143-145:

> All Score and Choice answers from TypeSafe include a `probabilities` property representing the probability distribution across the options (for Choice) or levels (for Score). The *shape* of that distribution is what tells you how certain the model is: concentrated on one outcome means a confident answer, spread out means an uncertain one.
>
> The answer's `confidence` property collapses that shape into a single number from 0 to 1, so you can threshold on it without doing the math yourself. (Noul answers don't carry one.)

**For the kblam design this matters directly: a Noul question gets no `confidence` field.**
Threshold a Noul on its `noul` value itself. `typesafe/model-jaggedness_jev-1.13.md` L51
does exactly that: `YES = 0.5  # up to you on what you want the threshold to be, depends on your usecase.`

### There is no published formula

The exact computation is **documented as unpublished**. `typesafe/confidence.md` L153-155:

> **A solid default:** We provide `confidence` as a convenient measure that fits most use-cases, but you are never locked into our definition. Depending on what you are evaluating, a different measure may serve you better, which is exactly why we give you the full `probabilities` in the response. The pros and cons of different computations is a specialized topic that we'll keep to a separate cookbook rather than this page, and will add the link here when we do!

The one arithmetic hint in the mirror is inside the page's interactive widget, not the prose
(`confidence.md:136-139`):

> TypeSafe computes confidence from how the probability is spread across the options. All of it on one option gives 1.0; the more evenly it spreads, the lower the confidence. This demo uses `(3 × largest probability − 1) / 2` to approximate confidence for three options.

That is the widget's own approximation for a 3-option case, and it does not reproduce the
published values exactly (0.88/0.12/0.00 → 0.82 vs. the published 0.81 at `api.md:275`).
**Treat the formula as unknown; use the returned `confidence` and the returned
`probabilities` as-is, and do not recompute confidence in code.**

### Choosing thresholds from labelled data — the documented method

Two different procedures, one per primitive. Both are from
`openrouter/cookbook_evaluate-and-optimize_jev-classification.md`.

**Noul tags — sweep thresholds and read precision/recall** (`:214-216`):

> A `noul` probability only becomes a tag once it crosses a threshold, and the threshold level differs for each tag. Here's a procedure for learning appropriate thresholds for each tag. Label 100 to 200 representative items by hand, and use `runBatch` to classify them in the same order that you used when labeling them. Then compute precision and recall for the different tags at each of several candidate thresholds. Raising the threshold increases precision at the expense of recall. If you can afford to miss a few items that should have the tag, set the lowest threshold where you are satisfied with the precision. If you can't afford to miss the items, set the highest threshold whose recall you can accept.

The sweep set used is `[0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]` (`:246`). Reported results from
that run (`:328`, `:339`):

> A threshold sweep was performed on the 150-item calibration sample, showing `precision/recall` at different thresholds, with true positives, false positives, and false negatives as `tp/fp/fn` in parentheses. `sports` was clean at any threshold. `music` traded 12 recall points for 26 precision points between threshold 0.5 and 0.8. `news` and `film_tv` could not reach high recall at any threshold with these instructions. `celebrity` could not reach high precision.

> The `choice` answer matched the human category on 127 of 150 calibration items. Confidence splits here are at 0.8 or above: 114 of 122 matched; at 0.5 to below 0.8: 9 of 18; below 0.5: 4 of 10. A review threshold of 0.8 on `confidence` would have sent the 28 items below it to a person. On the 400-item batch it got 350 of 400 correct.

**Choice — no threshold sweep; group the calibration sample by confidence band** (`:259`):

> The `choice` answer needs no threshold since its `confidence` tells you which items to hand to a person. Group the sample by confidence band, compare the category with the human label in each band, and send to review anything below the band that meets your accuracy bar. Run this calibration once, separately from the production batch in step 3, since both are paid.

And the design rule when no threshold works (`:291`):

> When a tag's precision stays low at every threshold, fix the question rather than the threshold. In the captured run, `celebrity` never passed 0.41 precision because its instruction, `Is the post about a celebrity or pop culture?`, overlaps with `music` and `film_tv`. Tighten the instruction or the criteria and re-run the sample before shipping that tag.

Cost for that calibration-plus-batch run, `:13`: "the captured run came out to $0.014 across
550 tweets", with a calibration sample of 150 and a batch of 400, `concurrency` of 8.

### Threshold values actually used in the mirror's own cookbooks

These are starting points the documentation author used, not recommendations for kblam:

| Source | Value | Quote |
| --- | --- | --- |
| `cookbooks_citation_check.md:81` | `AUTO_ACCEPT = 0.8` | "start high for more human review as you build trust in the model" |
| `cookbooks_classification_using_confidence.md:251` | 0.9 | "At 0.9 confidence or above, the answer is reported as an industry group; below that, the same answer is reported as the division" |
| `patterns_confidence-routing.md:287` | 0.6 floor / >0.85 | "Below 0.6 confidence on any action, route to a human"; "approving a transfer requires very high confidence (>0.85)" |
| `typesafe/confidence.md` L199,208 | 0.5 floor / >0.9 | "if confidence < 0.5: # Model is genuinely unsure. Don't guess." ; "if confidence > 0.9: # High stakes, high confidence." |
| `cookbooks_consistency_choice_cookbook.md:38-40` | `0.60` top-probability | "we also require a top probability of at least `0.60`; otherwise the result is `uncertain` and goes to human review. TypeSafe's agreement then rises to 99.2%, with automatic labels on 74.2% of answers." |

The governing advice, `typesafe/confidence.md` L218-220:

> The correct threshold values depend on your domain and the performance of the model for your use case. Start with conservative thresholds, test with your own data, and adjust as you observe results.

### Run-to-run variability you should budget for

`typesafe/cookbooks_consistency_choice_cookbook.md` L30-36 (8 Choice questions, 15
repeats per condition, `jev-latest` on the production API, sampled 2026-09-11):

> What to look for: picked labels can flip inside a single condition, including TypeSafe, and conditions disagree with each other.
>
> In this run the LLM distribution settings repeat their plurality labels 87.5% to 100% of the time, compared with TypeSafe's 90.8%. TypeSafe has lower mean probability variation than five of the six LLM distribution conditions; Haiku at temperature 0 varies less. Close probabilities still permit routing changes: TypeSafe flips on 2 of the 8 questions.

`typesafe/cookbooks_consistency_noul_cookbook.md` L29-33 (14 Noul questions):

> What to look for: the LLM answers move from run to run, at temperature `0` too, and on the judgment calls the models disagree with *themselves*. TypeSafe's mean per-question probability standard deviation is `0.0102`, below all LLM probability conditions here. Its `covered` answers span `0.43` to `0.53`, crossing a `0.5` decision threshold.

`typesafe/cookbooks_consistency_choice_cookbook.md` L1050-1052:

> This policy does not make the model deterministic. Abstaining can replace competing labels with the same human-review outcome, but a probability near `0.60` can still move between a concrete label and `uncertain`.

## Q4 — The two pairwise-comparison precedents, extracted as designs

verified: 2026-09-22 @ mirror retrieved 2026-09-23
evidence: verbatim from `typesafe/cookbooks_entity_alignment.md` and
`typesafe/cookbooks_citation_check.md`. Numbers are the cookbooks' own published
run outputs, not measurements on kblam data.

### 4a — Entity alignment (`cookbooks_entity_alignment.md`)

**Task**: decide, per candidate pair, whether two catalogue entries are the same product.
450 pairs, one request per pair. Model `jev-1.12`, run 2026-08-11.

**How state is built for a pair** (`:149-150`, and the `score()` function at `:200`):

> Both entities go into a single state, as `entity_a` and `entity_b`, so the questions are about the *pair* and not about either side on its own. All four ride in one request.

```python
response = client.system_one(
    state={"entity_a": pair["entity_a"], "entity_b": pair["entity_b"]},
    questions=QUESTIONS,
    model=TYPESAFE_MODEL,
)
```

**Why Score rather than Choice or Noul** (`:32-35`):

> We use a Score question because we want to attach a semantic label, the score criteria, directly to each outcome, including the middle outcome. A Noul question could accomplish this indirectly through thresholding on its output instead, and a Choice question would lose the ordered relationship of the three outcomes.

This is the most transferable sentence in the cookbook, and it is a two-edged one. TypeSafe's
own reason for choosing Score was that the three outcomes *are* ordered (unlinked → curator →
merged) and Score lets the level descriptions carry each outcome's meaning. For kblam's
`{same_fact, cannot_both_be_true, compatible_same_subject, unrelated}` no such order exists,
so by the cookbook's own reasoning a Score would be the wrong primitive; a Choice is what
carries per-outcome meaning without inventing an ordering. That is an inference from the
documented rationale, not a documented instruction — flagged as such.

**The exact questions** (`:169-192`):

```python
LEVELS = [
    "They describe two different products.",
    "They describe closely related products that may or may not be the same one: "
    "a variant, a special edition, or a name that could plausibly refer to either.",
    "They describe one and the same product.",
]
OUTCOME = {0: "leave unlinked", 1: "curator queue", 2: "assert sameAs"}

QUESTIONS = {
    "link_state": Score(
        instructions="How do the two entity descriptions relate as products?",
        criteria=LEVELS,
    ),
    "same_name": Noul(
        instructions="Do the two entities state the same beer name?",
    ),
    "same_brewery": Noul(
        instructions="Are the two entities from the same brewery?",
    ),
    "same_style": Noul(
        instructions="Do the two entities describe the same beer style?",
    ),
}
```

**How the decision is made — rounding, not thresholding** (`:218-220`):

```python
def route(score_value: float) -> str:
    """The whole decision rule: the nearest level names the outcome."""
    return OUTCOME[min(int(score_value + 0.5), len(LEVELS) - 1)]
```

`:153-158`: "The three level descriptions below are the entire decision: each level is one
outcome. There is no threshold constant anywhere in this file. You can also write these
descriptions before you have seen a single score, which is not true of a number you have to fit."

**The three companion Nouls are for the human, not the router** (`:37-41`, `:64-65`):

> Next, for each field of the entity we want to consider, `Noul` questions about whether those fields match can ride along in the same request. These nouls provide more detailed information for the curator, if the score lands neither in the "same product" nor "different product" levels.

**Which fields get a Noul** (`:164-165`): "Three of the four fields get a `Noul` question:
name, brewery, and style. Alcohol content gets none, because comparing two numbers is
arithmetic; compute it in code if you want it."

**Reported results.** Distribution over the 450 pairs (`:330-334`):

```
assert sameAs      40  ( 8.9%)
curator queue      50  (11.1%)
leave unlinked    360  (80.0%)
```

Four worked examples with scores and confidences (`:249-269`):

```
c446  score 1.94  confidence 0.92  ->  assert sameAs
    name 0.97   brewery 0.99   style 0.81

c427  score 0.03  confidence 0.95  ->  leave unlinked
    name 0.02   brewery 0.09   style 0.08

c100  score 1.30  confidence 0.27  ->  curator queue
    name 0.95   brewery 0.94   style 0.35

c428  score 1.10  confidence 0.77  ->  curator queue
    name 0.63   brewery 0.98   style 0.74
```

On calibration quality of the middle band (`:342-352`):

> On this set the scores do not sit neatly on the whole numbers. Most land near 0.25. Two beers with nothing in common might still share a style name, and their brewery names might look alike, so the model gives the middle level some of its probability instead of none. What decides a pair is which side of a cut point it falls on. How near it sits to a level does not enter into it.
>
> The two cut points are not equally crowded. Nine pairs sit within 0.1 of the upper one, at 1.5, which is the one deciding what gets merged into the graph. Forty-seven sit that close to the lower one, at 0.5, which only decides whether a curator sees the pair. Neither number is something you tune. Both follow from how you worded the levels, and the wording of the middle level is what moves pairs between the curator and the pairs left unlinked.

**Cost.** The cookbook caches tokens, not cost, and says so (`:212-215`):

> # tokens and requests are the durable units; don't cache a derived cost
> "input_tokens": response.usage.input_tokens or 0,
> "output_tokens": response.usage.output_tokens or 0,

No dollar figure and no accuracy/precision/recall number is reported for this cookbook. It
reports the outcome distribution above only. Concurrency (`:95`): `MAX_WORKERS = 6  # small
pool; the public endpoint rate-limits above roughly eight`. Client timeout is raised to
`timeout=120.0` (`:102`).

### 4b — Citation check (`cookbooks_citation_check.md`)

**Task**: given a claim and the source section it cites, decide whether the section supports
it. Model `jev-1.12`, run 2026-08-16. Eight citations, four of them deliberately broken.

**Two-stage design.** Stage 1 is a string match with no model call; stage 2 is one Choice
question on the surviving section (`:16-19`):

> To automate that check, we first look for missing quotes with an ordinary string match, and then we use a `Choice` question to read each surviving quote's context and decide whether it supports the claim.

**The exact question and criteria** (`:227-242`):

```python
QUESTIONS = {
    "relation": Choice(
        instructions="How does the section relate to the claim?",
        criteria={
            "supports": "The section states the claim or directly implies that it is true",
            "contradicts": "The section states the opposite of the claim or implies it is false",
            "says_nothing": "The section does not address what the claim asserts, either way",
        },
    ),
}

RELATION_TO_VERDICT = {
    "supports": "verified",
    "contradicts": "contradicted",
    "says_nothing": "unsupported",
}
```

**State shape** (`:247-252`): `state={"claim": claim, "section": section}`. Two named keys,
the claim and the passage. This is the closest published analogue to a kblam pair.

**Threshold** (`:80-81`, `:218-224`):

```python
TYPESAFE_MODEL = "jev-1.12"
AUTO_ACCEPT = 0.8  # start high for more human review as you build trust in the model
```

> The option with the highest probability is the verdict, and `AUTO_ACCEPT` (0.8 in the code above) decides what happens to it:
>
> * confidence at or above 0.8: the verdict stands on its own;
> * below 0.8: a human confirms the verdict before anything acts on it.
>
> Start high, and lower the threshold as you see how the model does on your own documents.

**Reported results** (`:300-310`):

```
citation          quote         relation        conf  verdict       action
epoch_seconds     found         supports        0.93  verified        auto
aud_reject        found         supports        0.95  verified        auto
sig_reporting     missing       -                  -  fabricated      auto
clock_skew        found         supports        0.99  verified        auto
exp_required      found         contradicts     0.99  contradicted    auto
pii_encryption    found         says_nothing    0.27  unsupported   review
iat_future        section-only  says_nothing    0.56  unsupported   review
duplicate_names   found         supports        0.99  verified        auto
```

`:312-316`: "Four citations came back `verified`, one `fabricated`, one `contradicted`, and
two `unsupported`." All four accurate citations came back `verified` at confidence 0.93+;
all four planted failures were caught.

Note the two `says_nothing` verdicts came back at 0.27 and 0.56 — **the low confidence
tracks the "neither" outcome**, which is the outcome kblam's `unrelated` /
`compatible_same_subject` most resembles. Budget for routing those to a human.

**The string-match rung is exact after normalization** (`:330-332`):

> The string match is exact after normalization: a quote that is truncated or lightly reworded comes back as `fabricated`. A production system that tolerates sloppy quoting would need fuzzy matching instead.

**No precision/recall figure is reported** — the sample is 8 hand-built citations, not a
labelled set. Do not cite this cookbook as evidence of measured accuracy. `:48-51`:
"`check_citation()`, the function you build here, takes a source document and one citation
and returns one of four verdicts: `verified`, `unsupported`, `contradicted`, or `fabricated`.
It also returns a confidence that flags the ones a human should look at."

## Q5 — State design and question wording: do / don't rules for pairwise technical prose

verified: 2026-09-22 @ mirror retrieved 2026-09-23
evidence: verbatim from `typesafe/model-jaggedness_jev-1.13.md`,
`typesafe/primitives_advanced.md`, `typesafe/concepts_state.md`,
`typesafe/api.md`, `typesafe/concepts_how-to-build-with-system-one.md`

Every rule below is a direct quote or a direct consequence of one. The jaggedness page is
"Last reviewed 2026-09-17" (`model-jaggedness_jev-1.13.md:10`).

### Naming parts of state with backtick paths

`typesafe/api.md` L58-69 — the mechanism, stated for structured `instructions`:

> The `instructions` property can be a string, an object, or an array. You can break up a long question that has extra context, or data it needs to reference, into a structured object. Put the question in one field and the data in the others, and refer to the data fields by name in backticks, the same way you point a question at a nested `state` value:
>
> ```json
> "instructions": {
>   "potential_duplicate": {
>     "name": "John Smith",
>     "location": "Oakland, California",
>     "last_employer": "Google"
>   },
>   "question": "Is the resume for the same person as `potential_duplicate`?"
> }
> ```

The name in backticks resolves either against a sibling key of the structured instructions or
against a key of `state`. Worked examples in `primitives_advanced.md` use both directions:
`'Does `extracted_value` match the `field` as it appears in `source_text`?'` (`:278`) points at
siblings, while `'Does the `message` ask the recipient to disclose a sensitive credential?'`
(`:491`) points at a `state` key.

**Do**: name the two findings in `state` and point the question at them by name.
`typesafe/model-jaggedness_jev-1.13.md` L92: "When possible, identify the relevant
parts of state by name."

### Instructions and criteria as JSON structure

`typesafe/primitives_advanced.md` L252-254 — when to structure:

> * **When it helps with clarity.** When a question has multiple parts, putting them in the form of JSON helps with clarity because the keys are labeled.
> * **When question needs supporting data.** A schema, a taxonomy, or a database row is already JSON. Use the JSON entirely or pass in the relevant subfields instead of serializing them into a string template.

The permitted shapes, `primitives_advanced.md:244-249`:

| Field                                   | Applies to          | Accepted shape                         |
| --------------------------------------- | ------------------- | -------------------------------------- |
| `instructions`                          | Choice, Score, Noul | `string`, `object`, `array`, or `null` |
| `criteria` values (option descriptions) | Choice              | `string`, `object`, `array`, or `null` |
| `criteria` entries (level descriptions) | Score               | `string`, `object`, `array`, or `null` |
| `criteria.true` and `criteria.false`    | Noul                | `string`, `object`, `array`, or `null` |

A structured Choice option that states what the option does **and does not** cover, plus
examples (`primitives_advanced.md:372-388`):

```json
"billing": {
  "what": "Charges, invoices, refunds, or subscriptions",
  "not_for": "Order tracking or account access",
  "examples": ["I was charged twice", "Where is my refund?"],
}
```

> The example tells the model what each option does and does *not* cover. It sharpens the boundary between options.

Structured Score levels with signals (`primitives_advanced.md:454-467`):

```json
criteria: [
  {
    summary: 'One change, clearly stated',
    signals: ['A single fix or feature', 'Nothing described as "also" or "while I was in there"'],
  },
  ...
]
```

**Do** for kblam's four options (`same_fact`, `cannot_both_be_true`,
`compatible_same_subject`, `unrelated`): give each a `what` / `not_for` / `examples` object.
The `not_for` field is what disambiguates `compatible_same_subject` from `unrelated`, which is
the pair most likely to blur.

### Literal reading

`typesafe/model-jaggedness_jev-1.13.md` L31-33:

> `jev-1.13` answers the question you wrote, not the one you meant. Scoping words, negations, and implied conditions are read at face value. A question will be answered based on the words written in the instruction, whereas a person might have read the intent behind the instructions.
>
> **Instead:** state the exact condition in the `instructions`. Be specific. Put boundary cases in the criteria. When you look at a wrong answer and find yourself explaining what you really meant, that explanation is the missing half of the instruction. Where interpretation is unavoidable, split it into two literal questions and combine them in code.

### Negation

Covered by the literal-reading rule above and by the contradictory-instructions rule below.
`typesafe/model-jaggedness_jev-1.13.md` L112:

> When the `instructions` and the `criteria` ask for different things, `jev-1.13` might get confused. The best performance comes from clear phrasing. For example, a Noul where `true` maps to no and `false` maps to yes will perform worse. Aim for instructions which are easy for the average person to read and understand.

**Don't** write a Noul whose answer polarity fights its wording. **Do** keep `criteria.true`
meaning yes and `criteria.false` meaning no.

See also the negation finding under structural invariants (below): a question and its explicit
negation do not sum to 1.

### Indirection

`typesafe/model-jaggedness_jev-1.13.md` L90-92:

> Instructions carrying double negatives or complex indirection are answered less reliably. A question about a property of a property or something that requires multiple hops of reasoning costs accuracy.
>
> **Instead:** write your instructions as directly as possible. When possible, identify the relevant parts of state by name.

`:13`: "`jev-1.13` does the best on [System One](/concepts/system-one) tasks. It may struggle
with tasks that require additional levels of indirection."

**Do** ask one hop: "Does finding `new` contradict finding `existing`?" **Don't** ask
"Given that the earlier finding describes the mechanism and the new one describes the
calibration outcome, does the difference in framing mean the two are irreconcilable?" — that
is several hops and a hidden judgment.

### Numeric content — hex addresses, ratios, magnitudes

`typesafe/model-jaggedness_jev-1.13.md` L35-37:

> Jev is not a calculator. We strongly recommend implementing any mathematical logic in code. Jev will perform better on semantic questions than mathematical ones.

`:41`: "`jev-1.13` does not count reliably. This covers characters in a word, occurrences of
a term in a passage, and items in a long list. The model recognizes the shape of an answer
rather than tallying, and the error grows with the size of the thing being counted."

`:66-68`, which directly covers hex addresses and numeric identifiers:

> `jev-1.13` will perform better on semantic representations than numeric. For example, questions about colors using hex values will underperform compared to those using the English names. Given RGB triples or hex values it cannot reliably judge whether two values are near each other.
>
> Similarly, questions about high-level programming languages will perform better than questions about low level assembly, or binary encoded instructions.

`:70-72`: "**Instead:** do the conversion in code and pass in either the computed number or a
named bucket. Keep the model for the part that is genuinely a judgment, such as whether a
color reads as a warning."

`:76-77` on Score magnitudes:

> Please do not use score outputs (e.g., expectations and probability) to compute the exact magnitude of a number between two levels of a criterion. You can use the expectation to check if it passes a particular threshold, but `jev-1.13`'s score levels are weak in numerical calibration. It will not be able to help you reconstruct the exact number by interpolating between the nearest two levels.

**Do** for kblam: compare hex addresses, token counts and ratios in code; never ask Jev
whether `0x1A2B20` is near `0x1A2B30`, and never read a magnitude off a Score value.

### Large state

`typesafe/model-jaggedness_jev-1.13.md` L94-98:

> Accuracy falls as the state grows with content unrelated to the decision. Unrelated detail acts as a distractor, and a large state makes it harder to tell which part of the input produced a wrong answer.
>
> **Instead:** retrieve and filter in code first, and send only the fields the question needs. When it's not possible to filter in state, you can use a [Noul](/primitives/noul) to filter for relevance.

`:151`: "Giving it more context in `state` than the question needs. Jev suffers from context rot,
so unrelated material in the `state` costs you accuracy."

This is the direct argument against the ~30-candidates-in-one-request design — see Q6.

### Adversarial content

`typesafe/model-jaggedness_jev-1.13.md` L104-108:

> State is data, and `jev-1.13` does not treat it as hostile by default. Content written to adversarially steer the model, whether that is an injected instruction, a deliberately misleading framing, or **text that argues for its own classification**, can move the answer. We expect to improve on this in the future.
>
> **Instead:** be explicit in the criteria. Test your integration thoroughly before deploying it to many users.

(Emphasis added.) **This is a live risk for kblam**: a research finding is prose, and prose
about a subject frequently argues for its own reading. A finding whose own text asserts "this
supersedes the earlier measurement" is exactly the shape the page warns about.

### The structural-invariant warning — thresholds are not interchangeable

`typesafe/model-jaggedness_jev-1.13.md` L116-137, in full:

> `jev-1.13` is extremely consistent, meaning you should expect quantitatively similar outputs for semantically similar inputs.
> However there are many structural invariants one might imagine to hold that simply aren't guaranteed by the model.
>
> For example, "Is the customer asking for a refund?", asked as a [Noul](/primitives/noul) and as a yes/no [Choice](/primitives/choice) on the ticket "I'm not happy with the fit. What are my options here?":
>
> | Noul `noul` | Choice `yes` | Choice `no` | Choice `confidence` |
> | ----------- | ------------ | ----------- | ------------------- |
> | 0.22        | 0.01         | 0.99         | 0.97                |
>
> The comparable numbers are `noul` and `probabilities["yes"]`, and it is not obvious how to interpret either the Choice output and confidence for the Noul question or vice versa.
>
> The same question and its negation, "Is the customer asking for something other than a refund?", as two Nouls on the ticket "I was charged twice for the same order. Can someone look into this?":
>
> | `refund` | `not_refund` | Sum  |
> | -------- | ------------ | ---- |
> | 0.72     | 0.47         | 1.19 |
>
> There are many reasons that `P(noul)` and `1 - P(not noul)` may not be directly comparable.
>
> **Instead:** don't rely on expected structural invariance, and word questions to mean directly what you want. Don't carry a threshold tuned on a Noul over to a Choice, and don't hold the model to arithmetic identities between separate questions. A Choice over options and one Noul per option answer different questions: the Choice is relative, settling *which* option, while each Noul is absolute and can be low for all of them.

Three separate consequences:

1. A threshold fitted on one primitive does not transfer to another primitive.
2. A question and its negation need not sum to 1 (0.72 + 0.47 = 1.19 in the published run).
3. A Choice is relative ("which of these") while a Noul is absolute ("is this true at all").
   **A set of Nouls over the same options can all be low simultaneously; a Choice always
   returns a winner.** For kblam, a Choice over `{same_fact, cannot_both_be_true,
   compatible_same_subject, unrelated}` will name *some* option even when every option is a
   poor fit — the low `confidence` is the only signal that it did so.

### Do / don't summary for kblam

**Do**

- Put both findings in one `state` under two named keys (`existing`, `new`), mirroring
  `{"claim": ..., "section": ...}` (`cookbooks_citation_check.md:249`) and
  `{"entity_a": ..., "entity_b": ...}` (`cookbooks_entity_alignment.md:200`).
- Phrase the question one hop deep and refer to the parts by backticked name.
- Encode each option's boundary as `what` / `not_for` / `examples`.
- Compute numeric agreement (hex addresses, ratios, counts, dates) in code and pass a named
  bucket or a boolean into the question.
- Filter `state` to the two findings plus only the fields the decision needs.
- Gate on `confidence` (Choice) or on the `noul` value (Noul), separately, with separately
  chosen thresholds.
- Expect a real fraction of pairs to land in the low-confidence band and route them to review.

**Don't**

- Don't ask Jev to do arithmetic, distance-between-values, or date ordering.
- Don't hide multiple judgments in one question.
- Don't write an instruction whose polarity fights its criteria.
- Don't reuse a Noul threshold on a Choice (or vice versa).
- Don't assume a question and its negation are complementary.
- Don't pack unrelated candidate findings into `state` around the pair being judged.
- Don't rely on the model resisting text that argues for its own classification.
- Don't ask for generation — `:139-143`: "`jev-1.13` is not trained to generate text. While you can force it to by chaining choices, this will not work well and will be very slow."

## Q6 — Fan-out and batching: ~30 candidates in one state, or one pair per request?

verified: 2026-09-22 @ mirror retrieved 2026-09-23
evidence: verbatim from `typesafe/primitives.md`, `typesafe/models.md`,
`typesafe/patterns_fan-out.md`, `typesafe/model-jaggedness_jev-1.13.md`,
`typesafe/cookbooks_entity_alignment.md`, `typesafe/cookbooks_citation_check.md`,
plus a string-search sweep of `typesafe/llms.txt` and all `openrouter/*.md`
for any documented per-request question cap (none found)

**Short answer: one pair per request. The ~30-candidates-in-one-state design is the thing the
documentation explicitly warns against, on two separate grounds.**

### What the documentation does endorse: many questions, one state

`typesafe/primitives.md` L362:

> Send every question that uses the same state in one request. You can mix question types freely. System One models evaluate every question in a request in parallel. Adding questions barely changes the response time and costs only the tokens for the extra questions, which are cheap. Asking a question you might not need is close to free.

`typesafe/patterns_fan-out.md` L238: "Because TypeSafe supports sending many questions
in a single API call, we recommend putting all of the questions your system needs in a single
request, and then using code to decide what is relevant after the fact. All questions are
evaluated in parallel, so adding more questions usually has little effect on response time."

**The key qualifier is "every question that uses the same state."** Fan-out multiplies
*questions against one shared state*. It does not multiply states.

### Why the 30-candidate design fails

**(a) The token budget.** `typesafe/models.md` L15,20:

> | Context length              | 64k tokens per request; 32k tokens for `state` plus the longest question                  |

> * **Context length:** Jev ingests the `state` once and evaluates every question against it in parallel. The 64k budget covers the `state` plus all questions combined; the 32k budget applies to the `state` plus the single longest question.

Both budgets are hard. With 30 candidate findings in `state` plus the new finding, you must
fit all 31 texts plus 30 full question texts inside 64k, and the `state` plus the single
longest question inside 32k. The *combination* is what fails, not either budget alone: 64k
total with a 32k state cap leaves 32k for all questions combined, and 30 questions each
carrying a candidate's full text is a large question payload.

**(b) Context rot — the direct warning.** `typesafe/model-jaggedness_jev-1.13.md` L94-98:

> Accuracy falls as the state grows with content unrelated to the decision. Unrelated detail acts as a distractor, and a large state makes it harder to tell which part of the input produced a wrong answer.
>
> **Instead:** retrieve and filter in code first, and send only the fields the question needs. When it's not possible to filter in state, you can use a [Noul](/primitives/noul) to filter for relevance.

In the fan-out design, 29 of the 30 candidate findings in `state` are unrelated to any one
question — each question is about one pair, and the other 28 candidates are distractors.

There is also a documented degradation curve to consult: `typesafe/models.md` L20
points at `[Jev 1.13 jaggedness](/model-jaggedness/jev-1.13) for how accuracy shifts as the
state grows`. That page does not publish a curve — it states the qualitative rule quoted above.

**(c) Cost does not favour batching here.** Input tokens are charged and output tokens are
free (`models.md:18`), and the same candidate texts are re-sent once per question under the
one-state design. Twenty-nine distractors re-sent per question is not free.

### What the precedents actually do

`typesafe/cookbooks_entity_alignment.md`:

- `:118-119`: "One request goes out per pair, so what you spend follows the number of pairs you were handed rather than the size of either source."
- `:150`: "Both entities go into a single state, as `entity_a` and `entity_b` ... All four ride in one request."
- `:274`: `# 450 candidate pairs, one request each; a small pool keeps a live run to a few minutes.`

`typesafe/cookbooks_citation_check.md` L249: `state={"claim": claim, "section": section}`
— one claim and one passage per request.

So the closest published analogues to kblam both run **one request per pair**, with a small
thread pool for throughput: `MAX_WORKERS = 6  # small pool; the public endpoint rate-limits
above roughly eight` (`cookbooks_entity_alignment.md:95`).

### Consequences for kblam's request shape

- Fan out over candidates with a client-side thread pool, not inside one request.
- If 30 candidates must be resolved per new finding, that is 30 requests.
- Rate limits to size the pool against (`models.md:14`): "250,000 tokens per second / 1,200
  requests per minute"; over either returns `429`. The SDK retries `429` with backoff by
  default (see Q2).
- The one legitimate exception to one-pair-per-request: companion Nouls about *the same pair*
  (e.g. "do both findings name the same command code?", "do both cite the same evidence
  file?"). That is the entity-alignment pattern exactly — four questions, one pair, one
  request.

## Q7 — Versioning, model IDs, and determinism

verified: 2026-09-22 @ mirror retrieved 2026-09-23
evidence: verbatim from `typesafe/models.md`,
`openrouter/guides_community_typesafe-sdk.md`,
`typesafe/model-jaggedness_jev-1.13.md`,
`typesafe/cookbooks_consistency_choice_cookbook.md`,
`typesafe/cookbooks_consistency_noul_cookbook.md`

### Model IDs and aliases

`typesafe/models.md` L29-40:

> An alias is a model name that resolves to a versioned model ID. Send it in the `model` field like any other name.
>
> | Alias         | Points to    | Meaning                                                                                                                       |
> | :------------ | :----------- | :---------------------------------------------------------------------------------------------------------------------------- |
> | `jev-latest`  | `jev-1.13.0` | The most recent stable, official release. The default in our client SDKs, and the name the examples in these docs use.        |
> | `jev-preview` | `jev-1.13.0` | The most recent release, whether or not it is an official one. Moves ahead of `jev-latest` when a preview build is available. |
>
> An alias moves when a new release ships, so the answers behind it can change without a change on your side. The response's `model` field reports the versioned ID that answered, so you can log which model produced each result. If you have tuned confidence thresholds against a specific version, pin that version's ID instead of the alias and move to the new one on your own schedule.

Current versioned IDs in the mirror: `jev-1.13.0` (TypeSafe native, per `models.md:11` and the
example responses at `api.md:210`), and on OpenRouter `typesafe/jev-1.13` with dated snapshot
`typesafe/jev-1.13-20260917` (`guides_community_typesafe-sdk.md:99, 134`).

`openrouter/guides_community_typesafe-sdk.md` L97-103 — mapping rules:

> The System One API accepts TypeSafe's bare System One model IDs and maps them onto OpenRouter's `typesafe/` namespace before routing:
>
> * `jev-1.13` is routed as `typesafe/jev-1.13`.
> * `jev-latest` is routed as `~typesafe/jev-latest`, OpenRouter's alias for the newest Jev release.
> * IDs that already carry an author prefix, such as `typesafe/jev-1.13`, are used as-is.
>
> The `model` field in the response contains the OpenRouter model ID of the System One model that served the request.

`typesafe/models.md` L60:

> `GET /v1/models` returns the names your account can send in the `model` field, with a description and release date for each. It currently lists the aliases. Versioned IDs such as `jev-1.13.0` are accepted by the `model` field whether or not they appear in the list.

**Caution for kblam**: through OpenRouter, `client.models.list()` **does not work**,
`guides_community_typesafe-sdk.md:109`:

> The TypeSafe SDK's model listing (`client.models.list()` in JavaScript) calls `GET /api/v1/models`, which is OpenRouter's [Models API] and returns OpenRouter's response shape rather than TypeSafe's, so the SDK rejects it.

### Pinning, explicitly recommended when thresholds are tuned

`models.md:40` (quoted above): "If you have tuned confidence thresholds against a specific
version, pin that version's ID instead of the alias and move to the new one on your own
schedule."

The mirror's own cookbooks demonstrate a mix of practices, which is worth knowing when
reading their published numbers: `cookbooks_entity_alignment.md:94` and
`cookbooks_citation_check.md:80` both pin `TYPESAFE_MODEL = "jev-1.12"` and record the date
("Numbers below came from `jev-1.12` on 2026-08-11" / "on 2026-08-16"), while
`cookbooks_consistency_choice_cookbook.md:50` uses the moving alias: "This run uses
`jev-latest` on the production API, sampled on 2026-09-11."

Note that the pin examples use a **two-component** ID (`jev-1.12`) while the models page
documents a **three-component** one (`jev-1.13.0`). Both appear in the mirror; the versioned
ID in the published response example is `jev-1.13.0`.

### Determinism

**No.** Three separate statements:

`typesafe/cookbooks_consistency_choice_cookbook.md` L30-31:

> What to look for: picked labels can flip inside a single condition, including TypeSafe, and conditions disagree with each other.

`:1050-1052`:

> This policy does not make the model deterministic. Abstaining can replace competing labels with the same human-review outcome, but a probability near `0.60` can still move between a concrete label and `uncertain`.

`typesafe/model-jaggedness_jev-1.13.md` L118-119:

> `jev-1.13` is extremely consistent, meaning you should expect quantitatively similar outputs for semantically similar inputs.
> However there are many structural invariants one might imagine to hold that simply aren't guaranteed by the model.

Measured variability (`cookbooks_consistency_choice_cookbook.md:33-36`, 15 repeats, 8 Choice
questions): "In this run the LLM distribution settings repeat their plurality labels 87.5% to
100% of the time, compared with TypeSafe's 90.8%. TypeSafe has lower mean probability
variation than five of the six LLM distribution conditions; Haiku at temperature 0 varies
less. Close probabilities still permit routing changes: TypeSafe flips on 2 of the 8 questions."

`cookbooks_consistency_noul_cookbook.md:29-32` (15 repeats, 14 Noul questions): "TypeSafe's
mean per-question probability standard deviation is `0.0102`, below all LLM probability
conditions here. Its `covered` answers span `0.43` to `0.53`, crossing a `0.5` decision
threshold."

**Budget for it**: a deterministic decision needs the pair to land clear of the decision
boundary. Anything within roughly ±0.04 of a threshold on a Noul (`0.43`–`0.53` straddling
`0.5`) may flip between runs.

### Temperature and seed

**Neither exists on this API.** Verified negative: no `temperature`, `top_p`, `seed`, or
sampling parameter appears anywhere in `openrouter/api_api-reference_systemone_submit-a-system-one-request.md`
(`DecisionsRequest` properties are exactly `model`, `provider`, `questions`, `session_id`,
`state`, `trace`, `user`) nor in the OpenAPI request schema. The only request-shape escape
hatch is `extra_body` on the SDK (Q2), and `sdk_python_usage.md:290` says "The `beam_width`
field below is illustrative; only send fields supported by the API."

The one mention of temperature in the entire mirror is about **other** models used as
comparison conditions: `cookbooks_consistency_choice_cookbook.md:23-25` describes
`claude-haiku-4-5` and `gpt-5.4-mini` "at temperature `0` and the API default", and notes
"reasoning models and TypeSafe run without a temperature setting" (`:488-489`).

So: **you cannot pin sampling.** Reproducibility comes from pinning the model ID and caching
responses, which is what the cookbooks do — every cookbook caches to `json_cache.json`:
"Every call is cached to `json_cache.json`, which ships with the cookbook, so re-rendering
replays the published numbers without calling the API. Delete that file to re-run everything
live." (`cookbooks_entity_alignment.md:73-76`).

## Q8 — Data handling: retention and training

verified: 2026-09-22 @ mirror retrieved 2026-09-23
evidence: verbatim from `typesafe/models.md`, `typesafe/legal.md`,
`openrouter/api_api-reference_systemone_submit-a-system-one-request.md`.
**Not** fetched: the three linked legal documents themselves, which are off-mirror.

### TypeSafe

`typesafe/models.md` L54-56:

> ## Data handling
>
> Jev is not trained on customer requests or responses. See [Legal](/legal) for the Data Processing Agreement, the Privacy Policy, and details on zero data retention (ZDR) for enterprise customers.

`typesafe/models.md` L44-45 (same page, customisation section):

> Jev is not fine-tuned or LoRA-adapted with customer data. It is trained with [RLCD](/introduction/machine-learning-primer) to return calibrated decisions, and the same weights serve every account.

`typesafe/legal.md` L9-17, verbatim and complete:

> These documents cover how TypeSafe handles your data when you have an account with us, including data retention, our commitment not to train models on user data, and the general customer agreements that govern your use of TypeSafe.
>
> ## Legal documents
>
> * [Data Processing Agreement](https://typesafe.ai/legal/data-processing) — how we process customer data on your behalf, including data retention.
> * [Master Customer Agreement](https://typesafe.ai/legal/mca) — the general terms that apply to your TypeSafe account.
> * [Privacy Policy](https://typesafe.ai/legal/privacy-policy) — what data we collect and how we use it, including our commitment not to train models on user data.
>
> We also offer zero data retention (ZDR) for enterprise customers. Contact [privacy@typesafe.ai](mailto:privacy@typesafe.ai) to learn more.

**The mirror contains no retention period, no data-processing-agreement text, and no ZDR
terms.** Verified negative by string search over `typesafe/*.md` for "retention",
"retain", "days", "ZDR". If kblam needs a retention period, it is in the off-mirror DPA at
`https://typesafe.ai/legal/data-processing` — a check to run before relying on it.

### OpenRouter

The OpenRouter System One page documents two per-request routing controls, both under
`provider` (`api_api-reference_systemone_submit-a-system-one-request.md`).

`data_collection` (`:709-718`):

> Data collection setting. If no available model provider meets the requirement, your request will return an error.
>
> - allow: (default) allow providers which store user data non-transiently and may train on it
>
> - deny: use only providers which do not collect user data.

`zdr` (`:831-835`):

> Whether to restrict routing to only ZDR (Zero Data Retention) endpoints. When true, only endpoints that do not retain prompts will be used.

Both are optional and default to the permissive setting on `data_collection` and to off on
`zdr`. **Setting `provider.data_collection = "deny"` or `provider.zdr = true` can make a
request fail** — the docs say so explicitly for `data_collection` ("your request will return
an error"). Treat either as a routing constraint that must be tested against live availability,
not a free option.

**Note the OpenRouter docs' own guidance for Jev routes is silent on whether the TypeSafe
provider endpoint is ZDR-eligible.** The TypeSafe page says ZDR is an enterprise offering
(contact `privacy@typesafe.ai`). So the two statements do not compose into a guarantee:
OpenRouter can route to only-ZDR endpoints, and TypeSafe offers ZDR to enterprise customers,
but nothing in the mirror states that the `typesafe` endpoint qualifies. **Unknown — flag
rather than assume.**

### OpenRouter's own policy — fetched from the web, not in the mirror

The mirror contains no OpenRouter privacy or logging page, so these two are WebFetch results
(fetched 2026-09-22, not mirror-verified).

From `https://openrouter.ai/privacy`:

> **"OpenRouter does not use your Inputs or Outputs for model training."**
>
> **"Some Model Providers may use your Inputs and Outputs for model training or improvement."**
>
> **"We do not persist image, audio or video files beyond the duration necessary to route the request, except as required for abuse detection, security, billing, or legal compliance."**
>
> **"We retain Uploaded Files until you delete them or close your account."**

From `https://openrouter.ai/docs/features/privacy-and-logging`:

> **"Each provider on OpenRouter has its own data handling policies"** … **"set whether you would like to allow routing to providers that may train on your data."**
>
> **"Providers also have their own data retention policies, often for compliance reasons"** — "as reflected in each provider's terms are shown below."
>
> **"If you opt out of training in your account settings, OpenRouter will not route to providers that train."** … this "setting has no bearing on OpenRouter's own policies and what we do with your prompts."

The provider retention/training table on that page is rendered dynamically from OpenRouter's
API, so its **TypeSafe row could not be read by fetch** — see the open question at the end of
this file.

### Practical note

`session_id` is documented as **not** leaving OpenRouter's own systems
(`api_api-reference_systemone_submit-a-system-one-request.md:365-370`): "A unique identifier
for grouping related requests (e.g., a conversation or agent workflow). Used for
observability grouping in Broadcast and private logging; **never sent to the provider**."
Useful if kblam is subject to a rule about what identifiers may reach a model provider.

## Q9 — `inanna-malick/jev-dsl`: evaluation as a dependency or a design source

verified: 2026-09-22 @ repo state fetched 2026-09-22 (git `HEAD`, `CHANGELOG.md` head says
"## 0.1.0.0 (unreleased, early alpha)")
evidence: files fetched into scratch (not into the tree) from
`https://raw.githubusercontent.com/inanna-malick/jev-dsl/HEAD/`: `README.md`, `docs/authoring.md`,
`jev-dsl.cabal`, `cabal.project`, `app/Main.hs`, `flake.nix`, `shell.nix`, `CHANGELOG.md`,
`check.sh`, `scripts/transport.sh`, `scripts/example.sh`, `test/fixtures/README.md`;
tree listing via the GitHub git-trees API. Metadata the coordinator supplied (Haskell, MIT,
7 stars, 42 commits, created 2026-09-17, last push 2026-09-18) is taken as given; I
independently confirmed MIT (`jev-dsl.cabal:14` and the `LICENSE` file, 1070 bytes).

### 9.1 What it is

From `README.md:1-14`:

> # jev-dsl
>
> A Haskell DSL for agents that would rather have a question judged than
> guessed. A packet of labelled questions is written once as an expression;
> its type is inferred, it renders to the exact request JSON for
> [TypeSafe's Jev](https://docs.typesafe.ai), and the answers come back under
> the same labels as records read by field. A choice is consumed through
> exhaustive labelled handlers or its carried payload, with a policy when
> needed. The branch that runs is one the program wrote and carries the
> payload it was offered with. No schema, no instance, no codec, no network.
>
> **Early alpha.** The interface is still moving, and this repository is the
> only place it has been used.

Package metadata (`jev-dsl.cabal:1-34`): `name: jev-dsl`, `version: 0.1.0.0`,
`license: MIT`, `tested-with: GHC == 9.12.3`, `default-language: GHC2021`.
The changelog's only released section is `## 0.1.0.0 (unreleased, early alpha)` and it ends
"Expect the interface to change."

### 9.2 What it does beyond the official Python SDK

Five things, all of which the Python SDK leaves to the caller:

**1. Typed packets with inferred types.** The type is never written by hand —
`README.md:85-99` shows an inferred type and explains:

> Nobody wrote that. It is what the compiler inferred from the packet in [Route](#route) below, and it is printable, so a model that has lost track of a value's shape can ask the compiler instead of guessing.

**2. Answers read back as records, under the same labels.** `README.md:210`:

> Answers come back under the same labels, ready to consume under a policy. Handlers are found by their label, so they need not follow the order the alternatives were written in

and `README.md:225-232`:

> A choice answers with `key`, `mass`, `margin`, `confidence` and `masses`; a Noul with `yes`; a score with `expectation`, `confidence` and `masses`. Those are for logs and thresholds. Dispatch goes through the branches instead: `settle` or `takenUnder` for a choice under a policy, `judge` for a Noul, `grade` for a score. A missing, extra, duplicated or misspelled handler is a compile error naming the label.

This is the substantive difference from `response.choices["tone"].choice`. In the Python SDK a
verdict is a string key you compare against your own literals; here it is a branch the
compiler proves exhaustive over the alternatives you offered.

**3. Compile-time exhaustiveness over the alternatives.** `README.md:154-160`:

> `settle` consumes a choice, and it gives no result without a handler for every alternative. The handler receives the row the program offered, so there is nothing to look up afterwards, and a confident "not here" runs its own branch instead of reading as a pass. A verdict carries the policy that reached it, so a step that must not be taken lightly can demand one:
>
> ```haskell
> merge :: Settled Strict Patch -> IO ()     -- a lenient verdict will not typecheck here
> ```

**4. Named policies over mass, margin and confidence.** `README.md:49-54`:

> **The numbers are the product, not a by-product.** A choice at 0.78 with a runner-up at 0.17 is a different situation from the same winner at 0.78 with a runner-up at 0.74, and a program can act on that difference. This is what the policies in this library are: three named floors over mass, margin and confidence, so "the model said yes" becomes "the model said yes strongly enough for a step this expensive".

`README.md:174-177`:

> Three policies are named for how bad it is to be wrong: `lenient` for a read-only choice, `careful` for starting work, `strict` for anything with a receipt. The same three apply to a Noul through `judge`, which returns yes, no, or the same structured doubt.

A failed policy yields no verdict, only a prose reason (`README.md:166-172`):

```
> a
Choice {key = "142", mass = 0.78, margin = 0.61, confidence = 0.80, masses = ["142" 0.78, "137" 0.17, "not_here" 0.05]}
> d.why
"doubted 142 (Unconfident): confidence 0.80 < 0.85 by 0.05; mass 0.78, margin 0.61"
```

**5. State field names checked against the state, rendered in backticks.** `README.md:293-310`:

> and wording that names a field names it through the compiler, not through a string that happens to match:
>
> ```haskell
> noul ("Do " <> field #diagnostics world <> " alone establish the mechanism of " <> field #failure world <> "?")
> ```
>
> `field` renders the key in backticks the way the provider reads it, and a name the state does not have is a compile error listing the names it does. Nested states read back through record dot: `world.gate.posters`. Name the same nested field in wording with a checked path:
>
> ```haskell
> field (#gate :/ #posters) st
> ```
>
> Each segment is checked against its packet. This renders `gate.posters` in backticks; **its effect on model answers has not yet been measured.**

(The last sentence matters: Q5's evidence for backtick naming came from *top-level* references.
The README repeats the caveat in Status, `:586-588`.)

Also present and not in the Python SDK: `each` (a per-item battery — one question per row, one
call, answers paired with the row that produced them, `README.md:357-366`), `optional` (a
`Maybe` question that is omitted from the wire, `:440-446`), `holds policy answer` (`:234-237`,
"`True` only for a settled yes, because a doubt is not a no"), and `margin`/`contenders`
(`docs/authoring.md:531`).

### 9.3 Library or CLI? And can Python call it?

**It is a library. It is not a client, and it contains no network code at all.**
`jev-dsl.cabal:13`: "No network: any transport carries the JSON."
`README.md:14`: "No schema, no instance, no codec, no network."

The cabal file defines two libraries and two executables:

- `library jev-core` — `core/`, depends only on `base` and `text`; polymorphic over a
  `JsonValue` class, "so it cannot reach aeson and a program that depends on it never pays for
  one" (`README.md:530-539`).
- `library` (jev-dsl) — `src/`, `jev-core` plus one `JsonValue` instance for aeson's `Value`
  and `Jev.Operators`.
- `executable jev-dsl-example` — `app/Main.hs`, an `optparse-applicative` CLI with `request`
  and `decode` subcommands.
- `executable jev-dsl-guard` — `examples/Guard.hs`, an interactive dialogue-tree demo.

The subprocess protocol is the interchange. `scripts/example.sh` is the whole of it:

```sh
request=$("$example" request "${args[@]}")
...
"$example" decode "${args[@]}" < <(curl -sS --fail-with-body \
  -X POST https://api.typesafe.ai/v1/systemone \
  -H "Authorization: Bearer $TYPESAFE_API_KEY" \
  -H "Content-Type: application/json" \
  --data "$request")
```

**So Python can drive it, but only as two subprocess calls per request**, and note that
`decode` is given **the same `args`**, not the request JSON — the packet is rebuilt on the
Haskell side from the original command-line arguments both times. A Python integration would
have to re-specify the packet identically for the decode half, or write a new Haskell
executable exposing build/decode as a JSON-in/JSON-out filter. Neither is an FFI binding;
there is no Python module, no C ABI, and no shared library target in the cabal file.

### 9.4 Windows toolchain

- **`tested-with: GHC == 9.12.3`** (`jev-dsl.cabal:34`), and the dependency bounds require it:
  `base >= 4.21 && < 5` (`:67`, `:82`). `base` 4.21 ships with GHC 9.12, so **GHC 9.12 or newer
  is required**; older GHC will not resolve.
- `cabal.project` pins the Hackage index: `index-state: 2026-06-11T20:22:08Z`.
- The documented dev environment is Nix — `nix develop` or `nix-shell` (`README.md:610-611`),
  which provides "GHC 9.12 with every dependency, cabal, and curl".
- **The flake does not list any Windows system.** `flake.nix`:
  `systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ];` — no
  `x86_64-windows` or mingw entry. So the documented `nix develop` path gives nothing on
  Windows; Nix-on-Windows means WSL2, i.e. a Linux toolchain.
- A native-Windows path is not documented but nothing in the dependency set blocks it:
  `aeson ^>= 2.2`, `scientific`, `vector`, `bytestring`, `text >= 2.0 && < 2.2`,
  `optparse-applicative`, `process`, `directory`, `filepath` are all cross-platform, and
  `process` (used to shell out to a transport) works on Windows. That would mean GHCup
  (GHC 9.12.3 + cabal) rather than the flake.
- The four shell scripts are bash (`scripts/transport.sh`, `scripts/example.sh`,
  `scripts/guard.sh`, `check.sh`) — Git Bash or WSL on Windows. `check.sh` is the strict build
  and it also requires `test/reject/*.hs` to fail to compile with a named diagnostic phrase.

So: **no documented Windows story.** Building it on this machine means either WSL2 plus Nix,
or an undocumented GHCup route the author has not tested (the repo has no CI configuration in
the tree listing).

### 9.5 Does it talk to OpenRouter?

**No.** The endpoint is hardcoded to TypeSafe's own API in both transport scripts:
`https://api.typesafe.ai/v1/systemone`, with `TYPESAFE_API_KEY` (`scripts/transport.sh`,
`scripts/example.sh`). The library has no endpoint at all, so this is a property of the
example transport, not of the DSL.

Pointing it at OpenRouter is therefore a *replacement transport*, not a code change to the DSL:
write a script that POSTs the same request JSON to `https://openrouter.ai/api/v1/systemone`
with `Authorization: Bearer $OPENROUTER_API_KEY` (Q1, and the OpenRouter guide confirms the
SDK appends `/v1/systemone` to base `https://openrouter.ai/api`). Two consequences for that:

- OpenRouter's response adds `id`, `provider` and `usage.cost` (Q1). The README says the
  library decodes with named `DecodeError`s for "missing, unexpected, or malformed answers"
  (`docs/authoring.md:433-436`), so **whether the extra top-level members are rejected is not
  established by anything I read** — it is the first thing to test.
- The response's `model` will be the OpenRouter snapshot ID (`typesafe/jev-1.13-20260917`), not
  `jev-1.13.0`. Any check that the served model matches the pinned one (kblam §6.5) must be
  built by us.

### 9.6 Does it provide pairwise comparison, calibration, caching or thresholds?

| kblam need (SPEC §6.5, §10) | In jev-dsl? | Evidence |
| --- | --- | --- |
| Pairwise state (`existing` / `new`) | **Yes, trivially** — state is a packet of named fields | `README.md:277-291` |
| Choice with a per-option condition, plus an exit option | **Yes, and it is the recommended shape** | `docs/authoring.md:296-303` |
| Threshold *mechanism* (floors over mass / margin / confidence) | **Yes** — three named policies | `README.md:49-54`, `:174-177` |
| Threshold *values* fitted to data | **No** | `README.md:583-584`: "The policy floors are plausible, not calibrated. Three named policies cover the cases seen so far. The numbers come from judgment about the cost of being wrong, not from measurement." |
| Calibration experiment / labelled-sample sweep | **No** — nothing in the tree | string search of `README.md`, `docs/authoring.md`, `CHANGELOG.md` for "calibrat", "precision", "recall", "sweep": no hits |
| Response cache | **No** — the word "cache" does not appear | same search |
| Cost log / `usage.cost` | **No** | `README.md:225-226` lists what an answer carries; `cost` is not among them |
| Batch or parallel runner | **No** — one request at a time | `scripts/transport.sh` is a sequential `for attempt in 1 2 3 4 5` retry loop with `sleep "$attempt"` |
| Rate-limit handling | **Partly** — 5xx and 529 retried with backoff, other bodies passed through to the decoder | `README.md:519-521`: "The provider returns 529 under load. A transport that retries 5xx and 529 with backoff and passes every other body back lets the library decode real rejections." Note `429` is **not** in the transport's retry list (`500|502|503|504|529`) |
| Model pinning config | **No** — the README's requests "go to `jev-latest`" | `README.md:67-69` |
| A CLI for kblam's commands | **No** — `jev-dsl-example` is a demo taking `--failure`/`--diagnostic`/`--check` flags | `scripts/example.sh` |

The two features kblam most needs that are entirely absent are **the pair cache** and **the
calibration run**. Both are ours to build regardless of language.

### 9.7 How the planned design would be expressed in it

The pair check (SPEC §6.2) maps onto its idioms directly:

```haskell
-- state, one packet of two named fields
pair e n = state (#existing := claimOf e :& #new := claimOf n)

-- the Choice, every option a condition, with an exit
#relation := choice "Which describes `new` relative to `existing`?"
   (  alt #same_fact                "Both state the same fact about the same subject, possibly in different words" ()
   .| alt #cannot_both_be_true      "They make claims about the same subject that cannot both hold"                     ()
   .| alt #compatible_same_subject  "Same subject, and both can be true"                                               ()
   .| alt #unrelated                "They are about different subjects"                                                 () )

-- consume it under a policy; a missing branch will not compile
case settle careful a.relation
       (  #same_fact               (\() -> Reject "duplicate")
       .| #cannot_both_be_true     (\() -> Reject "conflict")
       .| #compatible_same_subject (\() -> Accept)
       .| #unrelated               (\() -> Accept) ) of
  Right (Settled v) -> v
  Left d            -> Review d.why
```

The `revision` Noul becomes `#revision := noul (...)` with `judge` and its own policy, and
per §6.2's own reasoning it belongs in its own request because its state is `new` alone;
`docs/authoring.md:525` agrees on the general principle — "Ask one packet per semantic
boundary; put every question the current evidence can answer into it."

The single-check shape the DSL enforces is the one that catches kblam's failure mode P6
("one fact, one place"): **adding a fifth relation verdict later is a compile error in every
existing handler until it is handled**, which is a mechanical enforcement that a four-option
Python dict does not give you.

### 9.8 Design ideas worth copying into a Python implementation

Ranked by what they buy. All are ideas, not code, so they transfer regardless of language.

1. **"Rivals come from evidence, not from symmetry."** `docs/authoring.md:299-301`:

   > **Rivals come from evidence, not from symmetry.** Offer an alternative because the state could support it. **An option that argues for itself steers the answer; an option that merely describes its condition does not.**

   This is the concrete remedy for the adversarial-content failure mode in Q5 — where the
   documentation only says "be explicit in the criteria", this says how: word each option as a
   *condition the state could satisfy*, never as a case *for* choosing it. For kblam's four
   options that means `cannot_both_be_true: "They make claims about the same subject that cannot
   both hold"` rather than "This finding contradicts the earlier one".

2. **A choice's runner-up mass is doubt, not multiplicity.** `docs/authoring.md:474-479`:

   > **A choice picks one; a Noul each says how many.** A choice's distribution is uncertainty about which single alternative fits, not evidence that several apply. When things can be true at the same time, ask a Noul per thing with `each`, in the same packet, and judge each one. Reading a choice's runner-up mass as "this also applies" conflates doubt with multiplicity.

   Directly relevant: kblam must not read `probabilities["cannot_both_be_true"] = 0.4` as "40%
   conflicting". If a pair should be tested for two relations at once, that needs separate
   Nouls, not a reading of the Choice distribution.

3. **"Ambiguity and absent evidence are different failures."** `docs/authoring.md:507-510`:

   > A `Doubt` means the provider was not clear. It does not mean the evidence was missing: a model can be confident and wrong because the state never carried what it needed. Ask that as its own question.

   kblam's §6.4 review band conflates these today. Low confidence on `relation` means the
   provider could not separate the options; it does not mean the two claim paragraphs lacked
   the information. That distinction is worth a field in `review.jsonl`.

4. **Give every choice an exit.** `docs/authoring.md:302-303`:

   > **Give every choice an exit**: `#not_here`, `#none`, `#ask_model`, so "none of these" is an answer rather than a forced pick. An exit is worth more than a policy floor, because it is a branch you wrote.

   kblam's `unrelated` already is the exit. The point to carry over is the ranking: an authored
   exit branch beats a confidence floor for handling the "none of these" case.

5. **Three-layer check taxonomy.** `docs/authoring.md:424-436`: checks split into
   *before the request* (label uniqueness, handler coverage, empty offers, level counts),
   *at request build* (named `PrepError`: empty question maps, colliding runtime keys, shapes
   the provider rejects), and *at decode* (named `DecodeError`: missing/unexpected/malformed
   answers, selections outside the offered set, masses outside it, legends differing from what
   was sent). A Python implementation should keep the same three stages and the same named
   errors rather than raising one generic exception.

6. **Decode-time validation against what was sent.** "selections outside the offered set,
   masses outside it, legends that differ from what was sent" (`docs/authoring.md:435-436`).
   kblam should reject a verdict whose `choice` key is not one it offered, and a `legend` that
   does not match the criteria it sent, instead of trusting the response.

7. **The wording measurement.** `docs/authoring.md:500-506` — rewriting one alternative "from
   'cannot be squared with what the traveller said earlier' to a sentence naming the state field
   and the three concrete ways it could conflict moved that answer from mass 0.56 at 0.34
   confidence to mass 0.81 at 0.72". And `README.md:346-352`, a sufficiency gate over three code
   reviews: shown compact summaries it returned doubts at 0.26 and 0.34 confidence and one
   "more needed" at 0.69 mass; shown the actual declarations and diffs, the same gate passed all
   three at 0.78–0.92 mass and 0.67–0.89 confidence. Both are independent evidence for Q5's
   wording rules and for sending the real text rather than a summary — relevant to kblam's
   §6.2 decision to send "the claim paragraph plus the scope", which is a *summary* choice.

8. **`holds` semantics: a doubt is not a no.** `README.md:234-237`: "It is `True` only for a
   settled yes, because a doubt is not a no — which is exactly what comparing a verdict for
   equality would quietly make it." Any `if noul > threshold` in Python collapses doubt into
   false; the three-way yes/no/undecided should survive to the caller.

9. **The compile error as the interface.** `README.md:100-117`: "The compile error is the
   feedback loop, so it is the most-read text in the library... `test/reject/` is a directory
   of programs that must fail to compile, each naming the sentence it expects. The messages are
   a tested interface, not a courtesy." The transferable version in Python is that kblam's
   validation errors are a tested interface — each error message asserted in a test — which is
   the same discipline kblam §5 already needs for its rule codes.

### 9.9 What it does not do that kblam's design assumes

- **No structured question content.** `docs/authoring.md:438-454`, "Deliberately unsupported":

  > - Wording for what yes and no mean on a Noul. Put it in the question.
  > - A rubric whose levels exist only at runtime. Levels are authored.
  > - A question sent verbatim, or a question id the flattening would not produce.
  > - The provider's distinction between an omitted and a null criteria block or instruction.
  > - Structured, non-`Text` wording: an object, array, or `Null` sent where a question or an alternative's wording goes.

  This is a real gap against kblam's plan. SPEC §6.2 says "Name the parts in the instructions
  with backtick paths (per TypeSafe guidance; exact rules in Q5)", and Q5's guidance recommends
  giving each Choice option a `{what, not_for, examples}` **object**. jev-dsl can construct the
  backtick names (`field`), but it cannot send an object or array as wording at all, and it has
  no `criteria.true`/`criteria.false` on a Noul. A kblam design that relies on structured
  criteria is not expressible in this DSL today.
- **No runtime rubrics**: Score levels must be authored in the source, so a rubric read from
  config is out.
- **Scores are discouraged outright.** `README.md:601-602`: "**Scores are rarely the right
  shape.** Most judgments are not ordered and exclusive. Reach for a Noul or a choice first."
  This independently supports kblam's Choice-based relation question (Q4).
- **The operators must be read even though they are never written.** `README.md:589-593`:
  "`::=`, `:&`, `::>`, `::*`, `:|:`, `:/` and `:-` appear in inferred types and in error
  messages, so an author who prints a type meets all of them. Nothing on the authoring surface
  requires writing one; the cost is reading, and it is real." With an LLM as the author this is
  the main ergonomic risk: the inferred types are the thing most likely to be misread.
- **Two checks still speak in GHC's voice** (`README.md:594-600`), so not every error is in the
  author's vocabulary.

### 9.10 Bearing on the language decision

Stated as facts, not a recommendation — the call is the coordinator's and the user's.

- Every capability kblam needs that jev-dsl lacks (pair cache, cost log, calibration,
  parallel runner, kblam's CLI) is absent from it in *any* language, so choosing Haskell would
  not save building them.
- What Haskell would buy is the compile-time exhaustiveness over the relation options and the
  checked state field references — enforcement that a Python implementation gets only from
  tests. kblam's own principle P4 is "enforcement is mechanical", and this DSL is the only
  thing read here that enforces question shape at compile time rather than at run time.
- What Haskell would cost on this machine is a toolchain the repository does not document for
  Windows: GHC 9.12.3 (or newer, for `base >= 4.21`) via GHCup, or WSL2 plus Nix, plus the
  example transport replaced to reach OpenRouter, plus a new executable if the two-subprocess
  `request`/`decode` split is not wanted. The other three documents kblam consumes are
  markdown, gitignored state and a SQLite file; none of them is Haskell-aware, so a Haskell
  core would sit behind a CLI boundary or a second runtime for kblam's own commands.
- Its age is what the README says it is: "**Early alpha.** The interface is still moving, and
  this repository is the only place it has been used," and the changelog's only section is
  unreleased. Nothing here is a reason to reject it on quality; it is a reason to treat the
  interface as unstable and to expect the pinning of a commit, not a version bound.
- The design ideas in 9.8 are free regardless — they are documented guidance about how Jev
  answers, independent of the language that sends the request.
