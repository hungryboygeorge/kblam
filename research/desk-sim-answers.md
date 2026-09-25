# desk-sim answers — candidate-selection mechanism evaluation

Package: for a new or changed finding N, which pairs should be asked? Compare kblam's
M5 mechanism (topic + auto-extracted anchors + evidence + depends_on), the current §6.1
mechanism (linked + BM25 + topic bonus), and embedding similarity, against the labelled
pairs of the M4 calibration. The decision this changes: whether candidate selection stays
BM25, adds embeddings, or becomes a hybrid, and at what budget.

Sources: the kblam checkout, and a labelled relation-pair set built for the M4 calibration on the pilot project's
corpus. That evaluation is not published; this note names its files by role and keeps the measured numbers.
TypeSafe pages cited as `typesafe/<page>.md` are listed in REFERENCES.md.

## ⬅️ OPEN QUESTIONS

- None open. (Nothing here yet means every question asked so far is answered below.)

## INDEX

| area | id | what it answers | verified |
|---|---|---|---|
| data/labelled | S1 | record shapes, where claim texts live, classes, corpus vs constructed, per-claim metadata (and what is missing), the ranking pool and its size | 2026-09-23 |
| data/labelled | S2 | how the 210 pairs relate to source files and to each other: excerpt-path sharing, source-finding recovery, anchor-like tokens in claims | 2026-09-23 |
| lexical | S3 | `rank_bm25`, `bm25s`, scikit-learn: versions, licences, install result, and whether pure Python suffices | 2026-09-23 |
| lexical | S4 | kblam's own BM25 in `src/kblam/check.py` at 1713b0b (M6.6): exact tokenizer, scoring, ranking, budget | 2026-09-23 |
| embeddings | S5 | ollama: install, server, the three models, digests, dims, context, prefixes, HTTP and Python call shapes, measured throughput | 2026-09-23 |
| embeddings | S6 | sentence-transformers and rerankers, one line each | 2026-09-23 |
| data/encoding | S7 | how gold encodes positive vs negative, id names and types, how to assemble the pool, and the fields that leak or mislead | 2026-09-23 |
| spec | S8 | file:line ranges and verbatim text for the current §6.1 and the M5 §6.1, plus the tokenizer and stop-word list as implemented | 2026-09-23 |
| environment | S9 | interpreter, uv version, what is importable, the exact `uv run --with` invocations that work here, HTTP-vs-`ollama`-package trade-off, and the in-repo scripts worth reusing | 2026-09-23 |
| spec | S10 | the tokenizer's positive facts, `id_number`, budget-vs-rank order, `scope_overlap` semantics, scope statistics on the labelled set, and the stemmer audit | 2026-09-23 |

Line ranges are valid only as of this writing; address entries by ID and re-derive the range.

---

## S1 — The labelled set: shapes, classes, metadata, pool

verified: 2026-09-23 @ the pilot corpus 9563e3d (the labelled set is not part of the kblam repo)
evidence: direct reads of every file named below; record shapes printed from the JSONL

**Locations.** Every file named below comes from one evaluation directory tree on the pilot
corpus, which is not published; each is referred to below by its role.

| file | rows | sha256 | holds |
|---|---|---|---|
| corpus pairs file | 120 | `11a16a6e7cdcb80ba79064cefc4a860a7ba75b677c0b2b2653bf4f7a5451b52f` | corpus pairs, both claim texts, excerpts, builder class, `numeric` |
| top-up pairs file | 90 | `0980cdc73f19c8f1303fc9c13b3dc6c315375ba8199b5a66835eaefbde67ad68` | constructed pairs, same shape + `constructed`/`construction` |
| revision file | 46 | `941547950747b70a8dda3f96cf7362e4a2172c3c6fe5a38a732f5dd09a456221` | single-claim revision items |
| labeller-facing claims file | 166 | `ce49c5f20ee721c294245bc217d88f7ed2fcc3f46243f7321dd113c0244270b5` | 120 pair rows + 46 item rows, claims + scope only, shuffled |
| gold file | 136 | `dd25963a8e9d8c3f4ea697cbf8b594272ccf85778fe2570ccbd70776411766e9` | gold labels for corpus items: **id, gold, source, half, numeric only — no claim text** |
| top-up gold file | 84 | `d42a111e2e731d7d6bddcacd15fab7cfd7a226f03274734acf9779cf99a2cdaa` | gold labels for constructed pairs: id, gold, source, half, numeric, constructed, construction |

**Record shapes (verbatim keys, all rows uniform).**

The corpus pairs file — every row has exactly
`{pair_id, existing, new, excerpts, corpus_signal, builder_class, numeric, notes}`;
`existing` and `new` are each exactly `{claim, scope}`; `excerpts` is a 2-element list of
`{side, path, lines, text}` with one `side: "existing"` and one `side: "new"`.

The top-up pairs file — identical plus `{constructed, construction}`.

The revision file — `{item_id, claim, scope, excerpts, builder_class, notes}`, with the
single `excerpts[0].side == "claim"`.

First row of the corpus pairs file, claim texts replaced by a neutral example:
```json
{"pair_id": "PAIR-001",
 "existing": {"claim": "The vendor interface spec's shading-data layout names the two curve types by analog gain: the table is ordered gain 1 then gain 2, each crossed with 2-line and 1-line mode and with sensor lines A and B. The type axis of the correction data is an analog-gain axis.", "scope": "MX-200"},
 "new": {"claim": "The two curve types of the pressure-sensor correction page are not two analog gains. Their curves agree to about 0.1%, a median within-line ratio of 1.0017, where a 1.8x gain difference would show a ratio near 1.8. The lookup table is a row-onto-reference-row remap inside one type; the two types are never combined.", "scope": "MX-200"},
 ...}
```

**Where each pair's two claim texts live.** In the two pairs files, at
`row["existing"]["claim"]` and `row["new"]["claim"]`. Neither gold file carries any claim
text at all: join on `gold["id"] == row["pair_id"]` (pairs) or
`gold["id"] == row["item_id"]` (revision). The labeller-facing claims file is a third copy of
the same 166 claim pairs/items in shuffled order, minus `corpus_signal`/`builder_class`/
`numeric`; it is the labeller's view, not a separate dataset.

**Classes.** Five relation classes plus two single-claim classes.

- Relation positives by verdict, as the M4 run defined them (the metrics script's docstring,
  quoted below): `cannot_both_be_true`, `same_fact`, `restates_and_extends` — each with
  **every other pair class as its negative**.
- `compatible_same_subject` and `unrelated` are **always negatives**, never positives.
- `revision` is a single-claim class; its negative is `direct`.
- `numeric` is a truth value for pair rows only (61 true / 34 false in corpus gold); it is
  `null` on the 41 revision rows.

```python
# the metrics script, docstring
- Contradiction: positive = gold cannot_both_be_true, every other pair class negative. Fires when the winner is
  cannot_both_be_true, p(cannot_both_be_true) >= T and confidence >= C.
```
```python
# the metrics script, CLASSES
CLASSES = ("same_fact", "restates_and_extends", "cannot_both_be_true", "compatible_same_subject", "unrelated")
```

Gold counts, and the 50/50 split already fixed in the files:

| file | class | calibration | heldout |
|---|---|---|---|
| gold file | cannot_both_be_true | 24 | 23 |
| gold file | unrelated | 14 | 14 |
| gold file | revision | 12 | 11 |
| gold file | direct | 9 | 9 |
| gold file | restates_and_extends | 6 | 6 |
| gold file | compatible_same_subject | 3 | 3 |
| gold file | same_fact | 1 | 1 |
| top-up gold file | restates_and_extends | 17 | 16 |
| top-up gold file | same_fact | 13 | 13 |
| top-up gold file | compatible_same_subject | 12 | 12 |
| top-up gold file | unrelated | 1 | 0 |

So: **95 gold corpus pairs + 84 gold top-up pairs = 179 relation pairs**, plus 41 single-claim
items (23 revision, 18 direct). That 179 matches the "Relation pairs: 179" line in
the low-confidence report.

**Corpus pairs vs constructed top-up pairs.** The gold file is entirely corpus-derived
(`source` ∈ {agree-v1 114, agree-v2 18, adjudicated-v2 4}); the top-up gold file is entirely
constructed — `constructed: true` on all 84 rows — with `construction` ∈
{extend 29, compatible 26, subset 15, paraphrase 14}. The construction→gold mapping:
extend→`restates_and_extends` (29), compatible→`compatible_same_subject` (24),
subset→`same_fact` (14), paraphrase→`same_fact` (12); seven rows landed elsewhere.
A top-up pair row's `corpus_signal` says where the fact came from, e.g.
`"constructed from mx-200-protocol-findings.md L367-L374; MX-200 protocol R5: boundary independence."`

Excluded: 25 corpus pairs + 5 revision items (the exclusion list, 30 rows;
`{"id": "PAIR-018", "reason": "fidelity: meaning_changed"}`), and 6 top-up pairs
(the top-up exclusion list). Reasons are 20 `fidelity: uncheckable`, 8
`fidelity: meaning_changed`, and 2 user exclusions.

**Per-claim metadata — what exists and what does not.** Each claim carries **`claim` and
`scope` and nothing else** (240/240 corpus slots and 180/180 top-up slots have exactly those
two keys). Therefore, for the §6.1 mechanism:

- **`topic`: does not exist** anywhere in the labelled set. No per-claim topic, no topic
  folder. The topic bonus cannot be evaluated on this data without inventing a topic
  assignment, and any invented assignment is the evaluator's, not the KB's.
- **`anchors`: does not exist.** M5 extracted anchors from the claim paragraph, so they could
  be re-derived — but see S2: only 14 of 420 claim texts contain a `0x` hex literal of 4+
  digits and **none contains a backticked token**, so auto-extraction would fire on ~3% of
  claims.
- **`depends_on`: does not exist.** No dependency edges are recorded anywhere in the set.
- **Evidence path: proxy only.** kblam's `evidence` is a frontmatter list of repo paths; here
  the nearest thing is `excerpts[side].path`, which is the **source document** the claim was
  rewritten from, not an evidence artifact. Using it as `evidence` for linking is a proxy and
  should be labelled as one in any measurement. See S2 for how often it is shared.
- **`title`: does not exist.** §6.1 scores BM25 over *title + claim*. There is no title field,
  so a BM25 run on this set is claim-only unless titles are recovered from the source files
  (S2 explains how, and how well).
- **`label`, `verified`, `quantities`: do not exist.**

**Can a claim be traced back to its source finding entry?** Yes, but only through the excerpt.
The claim text itself is a rewrite: **0 of 420 claim texts occur verbatim in any pilot-corpus
markdown file**, while **420 of 420 excerpts do** (checked by substring against the file named in
`excerpts[*].path`). So the excerpt (`path` + `lines`, e.g. `mx-200-cal-findings.md`
`L830-L838`) is the only handle on the source entry. See S2 for recovering the enclosing
heading.

**The ranking pool.** Distinct claim texts, deduplicated by exact string:

- corpus pairs: **236** distinct from 240 slots (4 claim texts repeat across pairs)
- top-up pairs: **180** distinct from 180 slots
- the two sets are disjoint: **pairs + top-up = 416 distinct claims**
- adding the revision file: **461 distinct claims** (45 revision-only; 1 revision claim
  text is also a pair claim)

Which number is "the pool" depends on whether revision items are in the KB. 416 is the pool of
the 210 pair claims; 461 is every claim text in the labelled corpus. Both are exact; state
which one a measurement used. For scale, kblam's `max_candidates` default is 30 (§9).

Source: SPEC.md:174 (`max_candidates` (§9, default 30)).

---

## S2 — Pair-to-source and pair-to-pair relations in the labelled set

verified: 2026-09-23 @ the pilot corpus 9563e3d
evidence: excerpts and source files read directly; counts computed over all 210 pairs

**Excerpt-path sharing (the `evidence`-link proxy).** In **146 of 210** pairs both excerpts
come from the same source file, and in **31 of 210** they are the *identical* line range. Of
those 31, 30 are top-up pairs (subset 15, paraphrase 14, compatible 1) and 1 is a corpus pair.
Because top-up paraphrase/subset pairs were built by rewriting one passage, a shared-source
link would admit 30 constructed pairs for free — worth separating in the measurement.

**Recovering the enclosing heading (the `title` proxy).** For all 420 excerpts, walking back
from the cited first line to the nearest markdown heading in the cited file finds one — so a
title proxy is always available, but it lives in the pilot corpus, not in the pair records,
and it is not clean: the 420 excerpts map onto **166 distinct `(path, heading)` entries**, and
several of the most-reused headings are index or withdrawal sections rather than findings
(names illustrative): `mx-200-cal-findings.md ## INDEX` (15 excerpts), `desk-firmware-answers.md ## INDEX` (11),
`mx-100-cal-findings.md ## Index` (11), `mx-200-state.md ## 3. Withdrawn, or corrected
since first stated` (12), `desk-sensor-notes.md ## Claims I have withdrawn` (10).

**Do the cited source files exist?** All 30 distinct excerpt paths resolve to files in the
pilot corpus. Two of them are **not** listed in the labeller's source-file table,
though the files are present. The other 28 all appear in that table.

**Anchor-like tokens in the 420 claim texts** (what M5 would have auto-extracted):

- `0x` hex literals of 4+ digits: **14 claims**; 10 distinct values, the most repeated one
  occurring in 3 claims and four others in 2 claims each
- backticked tokens: **0 claims**
- other bare hex or `0x` with fewer than 4 digits: 2 claims

**Claim length.** Character length min 43 / median 159 / max 364; word count median 27,
max 64. Short enough that any embedding model's context window is irrelevant for the claim
text itself.

---

## S3 — Lexical libraries usable from Python 3.11 on Windows

verified: 2026-09-23, installed and smoke-tested in a throwaway Python 3.11.9 venv on this machine
evidence: PyPI JSON API for versions/licences; live install + API call (transcript below)

| library | latest | licence | deps | installs on 3.11/Windows |
|---|---|---|---|---|
| `rank-bm25` | 0.2.2 | Apache-2.0 (PyPI `license` field) | `numpy` | yes, 1.7 s |
| `bm25s` | 0.3.11 | MIT (classifier `License :: OSI Approved :: MIT License`) | `numpy` (core extras: orjson, tqdm, PyStemmer, numba) | yes |
| `scikit-learn` | 1.9.1 | BSD-3-Clause (PyPI `license_expression` field, SPDX; the older `license` field is null) | `numpy>=1.24.1`, `scipy>=1.10.0`, `joblib`, `narwhals`, `threadpoolctl` | not installed here; pure wheels, needs no compiler |
| `numpy` already present | 1.26.4 in the system Python; 2.4.6 pulled by uv into a fresh venv | BSD-3-Clause | — | yes |

`uv pip install rank_bm25 bm25s` into an empty 3.11 venv resolved and installed in 1.7 s.
Both import and run:

```
rank_bm25  classes: BM25, BM25L, BM25Okapi, BM25Plus
BM25Okapi(k1=1.2, b=0.75).get_scores(<four-token query>) -> [1.4417, 0.0, 0.0]
BM25Plus(...).get_scores(...) -> [8.0485, 4.1589, 4.1589]   # BM25Plus floors IDF, so it scores a non-matching doc
BM25L(...).get_scores(...)   -> [3.5467, 0.0, 0.0]
bm25s: tokenize(texts) -> Tokenized; BM25().index(tokens); retrieve(qt, k=2) -> [[0.4436, 0.0]]
```

Notes that matter for this evaluation:

- `rank_bm25` is **bag-of-words on tokens the caller supplies** — it does no tokenization,
  lowercasing, stop-word removal or length analysis of its own (`ok.idf` and `ok.avgdl` are
  plain dicts/floats over the token lists you handed in). Any comparison against kblam's BM25
  is therefore a comparison of *two different tokenizers* as much as two implementations.
- `rank_bm25` builds `self.idf` from the corpus at construction; there is no incremental
  update API, so the whole corpus must be re-scored per query. At 416 documents that is
  irrelevant (sub-millisecond).
- The three `rank_bm25` variants differ in IDF handling — `BM25Okapi` uses the Robertson
  IDF, `BM25Plus` uses `log((N+1)/df)` and adds a floor, `BM25L` uses the BM25L form. None of
  them matches kblam's `ln(1 + (N - n + 0.5) / (n + 0.5))` exactly; the closest is
  `BM25Okapi`. Do not treat them as interchangeable when reproducing a kblam score.
- `bm25s` is the fast (numpy/scipy-sparse) implementation; its speed advantage appears at
  corpus sizes far above 416. Its `tokenize` defaults also stem and stop-word-filter depending
  on options, which again differs from kblam's tokenizer.

**Is pure-Python BM25 enough?** Yes, and it is already in the repo: kblam's own BM25 (S4) is
pure Python, dependency-free, and runs over 416 documents in negligible time. A 416-document
corpus is roughly nine orders of magnitude below the size at which `bm25s`'s speed matters.
The reason to add `rank_bm25` would be to have an independent implementation to cross-check
kblam's, not to be faster.

---

## S4 — kblam's own BM25, as committed at 1713b0b (M6.6)

verified: 2026-09-23 @ kblam `1713b0b` (re-read against the committed revision; first read from the
working tree while 1713b0b was still uncommitted, every quoted line identical)
evidence: verbatim excerpts from `src/kblam/check.py`

**Provenance.** The BM25 mechanism is **committed at `1713b0b`** ("kblam M6.6: generality
fixes", 2026-09-23 12:38), which is where `BM25_K1` first appears. HEAD when this was last
checked was `3eb9368` (the eval commit), which does not touch `src/kblam/check.py`
(`git diff --stat 1713b0b 3eb9368 -- src/kblam/check.py` is empty), so every line quoted
below is current. `git show 1713b0b:src/kblam/check.py` contains `BM25_K1` twice. The revision
**before** it, `cc2dfee`, is the M5 anchors design and contains no `BM25_K1` at all, so a
replay must name its revision: `git show cc2dfee:src/kblam/check.py` gives M5,
`git show 1713b0b:src/kblam/check.py` gives the BM25 mechanism quoted below. (This desk first
read check.py from the working tree while 1713b0b was still uncommitted; every line quoted
below was re-read against 1713b0b and matches.)

Constants and tokenizer (`src/kblam/check.py` at 1713b0b):

```python
# SPEC §6.1 similarity: BM25 over each finding's title and claim paragraph.
BM25_K1 = 1.2
BM25_B = 0.75
TOKEN_RE = re.compile(r"[\w./-]+")  # \w: letters, digits and _ (Unicode)
TRAILING_PUNCTUATION = ".-/"
```

The document is title + claim, and IDF is the `ln(1 + ...)` form:

```python
def _document(finding: Finding) -> list[str]:
    title = _meta(finding).get("title")
    return tokens(f"{title if isinstance(title, str) else ''}\n{finding.claim}")
...
            idf = math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
```

Ranking and budget (`select_candidates`, `_rank`):

```python
def _rank(c: Candidate) -> tuple:
    # SPEC §6.1: linked first (dependencies, then shared anchors, then shared evidence), then similar;
    # within each, by score, and by ID among equal scores.
    link = 0 if c.depends else 1 if c.anchors else 2 if c.evidence else 3
    return (link, -c.score, id_number(c.finding_id))
```
```python
            bonus=topic_bonus * top if same_topic and bm25 > 0 else 0.0,
...
        if candidate.linked or bm25 > 0:
            matched.append(candidate)
```

Consequences for an evaluation on the labelled set: kblam's candidate selection reads
`title`, `topic`, `anchors`, `evidence` and `depends_on` from finding frontmatter, and **none
of those five fields exists in the labelled set** (S1). Only `claim` and `scope` are present.
`scope_overlap` and the BM25 claim score are the only two parts of the mechanism that can be
run unmodified; everything else needs a substitute, and the substitute must be named in the
result.

---

## S5 — ollama on this machine: models, dimensions, context, prefixes, calls

verified: 2026-09-23, live calls to `http://127.0.0.1:11434` from this machine
evidence: `ollama --version`, `ollama list`, `ollama show`, `/api/embed` responses, timings measured here

**Installed and running.** `ollama version is 0.34.1`, from a local Ollama install (binary
under `%LOCALAPPDATA%\Programs\Ollama\`), server answering
`GET /api/version` → `{"version":"0.34.1"}`.

**Models available now** (`ollama list`): `embeddinggemma:300m` (621 MB, pulled by this desk),
`nomic-embed-text:latest` (274 MB), `qwen3-embedding:8b` (4.7 GB). Also present but not
embedding models: qwen3:4b, qwen3:30b, qwen3-vl:8b/30b, a Qwen3-Coder GGUF, a qwen3-vl
abliterated build.

The three embedding models. `dim`, `params` and the quantisation are from `ollama show`; the
card context and the prefix forms are from each model card, fetched 2026-09-23 (provenance
noted below); the plateau is what this machine actually did, measured by embedding synthetic
inputs of increasing length and reading `prompt_eval_count`.

| model | params | dim | card context | **measured plateau** | quant | prefix, query / document |
|---|---|---|---|---|---|---|
| `nomic-embed-text:latest` | 137M | 768 (MRL to 512/256/128/64) | 8192 tokens | **2048 tokens** | F16 | query `search_query: `, document `search_document: ` (also `classification: `, `clustering: `) |
| `embeddinggemma:300m` | 307.58M | 768 (MRL to 512/256/128) | 2048 tokens | **2048 tokens** | BF16 | query `task: {task description} | query: {content}`, document `title: {title or none} | text: {content}` |
| `qwen3-embedding:8b` | 7.6B | 4096 (MRL 32–4096) | 32K tokens | **4095 tokens** | Q4_K_M | query `Instruct: {task_description}\nQuery: {query}`, document **no prefix** |

Measured plateaus, raw: nomic `prompt_eval_count` is 1502 at 500 words then flat at **2048**
for 1000, 1500, 2000, 3000, 5000, 9000 and 15000 words; embeddinggemma flat at **2048** from
3000 words up; qwen3-embedding:8b 1002 at 1000 words, 3002 at 3000, then flat at **4095**.

The plateau is what a run gets, regardless of the card: ollama's own `num_ctx` governs, and
nomic's 8192 card value and qwen3-embedding's 32K card value are both unreachable at the
defaults (`ollama show` reports `num_ctx 8192` for nomic yet it stopped at 2048).
**Truncation is silent** — a normal 200 with no warning; `prompt_eval_count` is the only
evidence. It does not matter for claims (median 27 words, S2) but would for a whole finding
body.

**Digests.** `ollama list`'s ID column is the **first 12 hex characters of the model's
manifest digest** (the registry's `ollama-content-digest` response header), not the config
digest. Full values, read from that header on 2026-09-23:

| tag | full manifest digest (= `ollama list` prefix) |
|---|---|
| `nomic-embed-text:latest` | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` |
| `embeddinggemma:300m` | `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1` |
| `qwen3-embedding:8b` | `64b933495768fbd3b87c20583d379728a07471e0c66733a9df87cd1901b3c44b` |

The manifests' `config.digest` values are different and are not what `ollama list` shows:
`embeddinggemma` `sha256:3901c6a1…5fc40cc`, `nomic` `sha256:31df23ea…c0139d4f`, `qwen3`
`sha256:0a3d61b0…3762f8bb89`. Name the manifest digest in a README, as that is the one a
reader can match against `ollama list`.

Server: listening on port **11434**, `GET /api/version` → `{"version":"0.34.1"}`.

**Prefix provenance.** Fetched from the model cards 2026-09-23: `huggingface.co/google/embeddinggemma-300m`
(mandatory prompts, quoted as `"task: {task description} | query: {content}"` and
`"title: {title | "none"} | text: {content}"`; task names `search result` (default),
`question answering`, `fact checking`, `classification`, `clustering`, `sentence similarity`,
`code retrieval`), `huggingface.co/Qwen/Qwen3-Embedding-8B` (`"Instruct: {task_description}\nQuery: {query}"`,
with the card's literal example `"Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: What is the capital of China?"`,
and *"No need to add instruction for retrieval documents"*), and
`huggingface.co/nomic-ai/nomic-embed-text-v1.5` (`search_document`, `search_query`,
`clustering`, `classification`; 8192 native sequence length). Cards carry no trailing space in
the quoted prefix names; the convention in use is prefix + one space, which is what was tested
below and is what the model was trained with.

**Prefixes are not cosmetic.** Embedding the same sentence with and without its documented
prefix gives cosine 0.9270 (nomic), 0.8980 (embeddinggemma), 0.9257 (qwen3-embedding). A run
that skips them is measuring a different model than the model card describes.

**Python call.** Two shapes, both verified here:

```python
# HTTP (no dependency; /api/embed is the batch endpoint)
POST http://127.0.0.1:11434/api/embed
  {"model": "nomic-embed-text:latest", "input": ["doc one", "doc two"]}
-> {"model": ..., "embeddings": [[...], [...]], "prompt_eval_count": ..., "total_duration": ..., ...}
```
```python
# ollama package (ollama-python); verified installed and callable
from ollama import Client
c = Client(host="http://127.0.0.1:11434")     # host is REQUIRED here, see below
r = c.embed(model="nomic-embed-text:latest", input=["a", "b"])
r["embeddings"]                               # list per input
```
`Client.embed(model, input, truncate=None, options=None, keep_alive=None, dimensions=None)`
is the current batch API. `Client.embeddings(model, prompt)` is the legacy single-input one.

**Two environment traps, both observed here:**

1. **`OLLAMA_HOST=0.0.0.0` is set in this shell's environment.** The `ollama` Python package
   honours it as the client's default host, so `Client()` with no argument fails with
   `ConnectionError: Failed to connect to Ollama` while `curl 127.0.0.1:11434` succeeds.
   Pass `host="http://127.0.0.1:11434"` explicitly (verified working) or drop the variable.
2. **Normalisation differs between the two endpoints.** `/api/embeddings` (legacy) returns an
   **unnormalised** vector (L2 norm 20.52 for a one-sentence nomic input); `/api/embed` returns
   a **unit** vector (norm 1.0). Cosine between the two is exactly 1.0, max component
   difference 3.35. Use one endpoint throughout; with `/api/embed`, dot product *is* cosine
   similarity, which is convenient and silently wrong if mixed with `/api/embeddings` output.

**Throughput measured here (all three models, warm).** One `input` list is one HTTP request;
the server embeds the whole list. Requests are cheap and per-call latency is tens of
milliseconds after the model is loaded (first call pays 2.6 s to 18 s of load time).

| batch | nomic-embed-text | embeddinggemma:300m | qwen3-embedding:8b |
|---|---|---|---|
| 100 docs | 2.15 s | 2.06 s | 6.21 s |
| 200 docs | 0.46 s | 0.87 s | 3.65 s |
| 416 docs in 7 chunks of 64 | 1.17 s | — | 7.48 s |

A single request carrying a few hundred inputs fails on this machine — deterministic for the
sizes tried, e.g. at n=300 for nomic and n=256 for qwen3-embedding:8b, with the body
`{"error":"Post \"http://127.0.0.1:63691/tokenize\": dial tcp ...: connectex: No connection
could be made because the target machine actively refused it."}` The failing component is
ollama's own tokenize helper, not a documented input cap. **Chunk at 64 inputs per request**;
416 documents then embed in about 1.2 s (nomic) or 7.5 s (qwen3-embedding:8b). Embedding the
whole 461-claim pool is not a budget concern.

**Model choice note for the pool size here.** At 416 to 461 claims anything works. nomic is
the cheapest and already resident; embeddinggemma:300m is a different family at the same
768 dimensions; qwen3-embedding:8b is the strongest of the three by training quality and is
Q4_K_M-quantised, which costs some embedding fidelity relative to the BF16 original.

**Other candidates, with download sizes from the ollama registry** (bytes, manifest layer
totals — each is model weights + licence + params layers; none pulled except embeddinggemma):

| model | size |
|---|---|
| `all-minilm:latest` | 45,960,589 B (44 MB) |
| `nomic-embed-text:latest` = `:v1.5` (installed) | 274,302,030 B (262 MB) |
| `granite-embedding:278m` | 562,776,964 B (537 MB) |
| `embeddinggemma:300m` (pulled) | 621,875,501 B (593 MB) |
| `qwen3-embedding:0.6b` | 639,150,592 B (610 MB) |
| `snowflake-arctic-embed:335m` | 669,492,861 B (638 MB) |
| `mxbai-embed-large:latest` | 669,615,085 B (639 MB) |
| `bge-m3:latest` | 1,157,672,268 B (1.10 GB) |

Chosen three (two already present, one pulled): nomic-embed-text, qwen3-embedding:8b, and
embeddinggemma:300m. The others were not pulled.

---

## S6 — Other options, one line each

verified: 2026-09-23, PyPI metadata + the rerank cookbook in the TypeSafe documentation
evidence: PyPI JSON API; `typesafe/cookbooks_rerank_typesafe.md`

- **sentence-transformers** (6.1.0, Apache-2.0, `requires-python >=3.10`) would put any
  Hugging Face encoder in process, but it drags `torch>=2.2` plus `transformers>=5`,
  `tokenizers`, `huggingface-hub`, `scikit-learn` and `scipy` into the dependency set — the
  download is measured in gigabytes, against ollama's already-installed models. Not installed.
- **Cross-encoder rerankers** (e.g. `BAAI/bge-reranker-*`) score a *pair* jointly and would
  need sentence-transformers or a similar stack; ollama does not serve reranker architectures,
  so there is no zero-install route to one on this machine.
- **TypeSafe as the reranker** is already the architecture kblam uses: N candidates, one Jev
  question each. `typesafe/cookbooks_rerank_typesafe.md` is exactly this pattern —
  BM25 builds a 30-candidate shortlist, then one TypeSafe question per query-candidate pair;
  it reports top-1 rising 5% → 18% and top-10 38% → 62% on the CLERC legal set. Relevant
  because it makes "BM25 shortlist + LLM per pair" a documented, already-benchmarked shape
  rather than something the project invented.

---

## S7 — Gold encoding, ids, pool assembly, and the fields that mislead

verified: 2026-09-23 @ the pilot corpus 9563e3d
evidence: every gold row joined back to its source row; counts computed over all 220 gold rows

**Ids.** Stable **strings**, not integers. Three prefixes, unique across the whole set:

- `PAIR-001` … `PAIR-nnn` — corpus pairs (the corpus pairs file, `pair_id`)
- `TOP-001` … `TOP-nnn` — constructed pairs (the top-up pairs file, `pair_id`)
- `REV-001` … `REV-nnn` — single-claim revision items (the revision file, `item_id`)

`gold["id"]` matches those fields exactly. 220 gold ids total: 95 `PAIR`, 84 `TOP`, 41 `REV`.
**Every gold id resolves** to a source row, and **no gold pair has an empty side** (checked
all 179).

**How a positive pair is represented.** There is **no positive/negative field**. Every pair
row carries one `gold` class, and negativity is *derived per verdict*:

```python
# the metrics script, docstring, verbatim
- Contradiction: positive = gold cannot_both_be_true, every other pair class negative. Fires when the winner is
  cannot_both_be_true, p(cannot_both_be_true) >= T and confidence >= C.
```
```python
# the metrics script, the pre-registered rule for the other two relation verdicts
#   same_fact and restates_and_extends: calibrated on the pooled pairs (corpus gold + top-up gold),
#   each keeping its own 50/50 split, positive = gold class, negative = every other pair.
```

So for a given verdict: `gold["gold"] == <verdict>` is the positive set, `gold["gold"] !=
<verdict>` is the negative set — including rows from the *other two* verdicts' positive sets.
`compatible_same_subject` and `unrelated` are negatives for all three and positive for none.
`revision` is a single-claim class with `direct` as its negative; the two live in the same
gold file as rows with `REV-` ids and `numeric: null`, so a relation-only script
must filter by id prefix or by class.

**Gold-class values** (the complete set, no others occur):
`same_fact`, `restates_and_extends`, `cannot_both_be_true`, `compatible_same_subject`,
`unrelated`, `revision`, `direct`.

**Which file a gold pair comes from.** The gold file holds all corpus items
(95 pairs + 41 single claims, `source` ∈ {`agree-v1` 114, `agree-v2` 18, `adjudicated-v2` 4});
the top-up gold file holds the 84 constructed pairs (`constructed: true` on every row,
`source` ∈ {`agree-topup` 80, `adjudicated-topup` 4}). To rebuild the split and the class of
each item, read both files and concatenate; `half` ∈ {`calibration`, `heldout`} is already
assigned in each.

**Assembling the pool.** The pool is not a supplied file; concatenate
`["existing"]["claim"]` and `["new"]["claim"]` over the corpus pairs file and
the top-up pairs file, and (if revision items are in scope) `["claim"]` over
the revision file. Dedup by exact string: **416** distinct from the two pair files,
**461** with the revision claims. The labeller-facing claims file is the same 166 claims+items in
shuffled order and is *not* the pool. No claim is orphaned: both sides of every gold pair are
in the pool by construction.

**Fields that leak or mislead — do not use these as features:**

- `corpus_signal` (both pair files) states in prose why the pair exists, e.g.
  `"mx-200-cal-findings.md:1340 heading: \"C16 — C10's pressure-range identification is FALSIFIED.\""`.
  It is the class answer in English. It was deliberately removed from
  `the labeller's input file` before labelling.
- `builder_class` is the **builder's intent, not the gold label** — they disagree often.
  Corpus: 69 of 95 agree, **26 disagree**. Top-up: 79 of 84 agree, 5 disagree
  (paraphrase 2, subset 1, compatible 2). Treating `builder_class` as ground truth would
  mislabel a quarter of the corpus pairs.
- `numeric` (boolean on corpus pairs) is a weak, ambiguous builder hint, not a clean leak:
  by gold class it is `cannot_both_be_true` 35 true / 12 false, `unrelated` 15 true /
  13 false, `restates_and_extends` 6 / 6, `same_fact` 1 / 1, `compatible_same_subject` 4 / 2.
  A real KB has no such field, so using it makes the measurement non-representative even
  where it would help.
- `notes` and `construction` are builder commentary; `construction` names the construction
  tactic (`paraphrase`/`subset`/`extend`/`compatible`) and therefore predicts the class.
- For the 30 top-up pairs of construction `paraphrase`/`subset`, both sides cite the
  **identical** excerpt line range; for the 30 `extend` and 30 `compatible` pairs the two
  sides cite the **same file** but different lines (S2). A candidate selector that links on
  the excerpt path gets all 90 top-up pairs for free; one that requires an identical range
  gets 30.

---

## S8 — The two §6.1 texts, and the tokenizer as implemented

verified: 2026-09-23 @ kblam `1713b0b` for the current §6.1 and `cc2dfee` for the M5 §6.1
evidence: verbatim SPEC lines; verbatim `src/kblam/check.py`

**Current §6.1 (BM25 + linked):** `SPEC.md` **lines 158–189** at
kblam HEAD **`1713b0b`** (`git show 1713b0b:SPEC.md` gives the same line numbers; the file is
clean against that revision). `git show cc2dfee:SPEC.md` returns the M5 version instead.

**M5 §6.1 (topic + auto-anchors):** `git show cc2dfee:SPEC.md`, **lines 158–181** (§6.2 starts
at 183 there). Print that range from the kblam checkout:
`git show cc2dfee:SPEC.md | sed -n '158,181p'`.

M5's rules, verbatim from that range — these are what the brief calls "the previous mechanism":

```
### 6.1 Candidate pairs (code, not Jev)
For a new or changed finding N, the candidates are the existing findings that share any of:
- the topic;
- an anchor: a declared `anchors` entry, or one auto-extracted from the **claim paragraph only**
  (hex literals of 4 or more digits, and `backticked` symbols). The body is excluded because verbatim
  hex dumps there would link almost every finding. Hex anchors compare by value (`0x001a2b3c` =
  `0x1A2B3C`), others case-insensitively;
- an evidence path (equal, or one contains the other);
- a `depends_on` edge in either direction.

Budget: `max_candidates` (§9, default 30) pairs whose scopes overlap. If more match, rank by number
of shared anchors, then topic, then `depends_on`, then number of shared evidence paths, then ID.
Pairs with disjoint scopes don't count against the budget. The quantity comparison (§6.3) also covers
candidates past the budget, since it is code. Each check's candidate set, with the reasons each
matched, is logged to `.kblam/checks.jsonl`.
```

Confirmed on the anchor rule as you had it: hex literals of **4 or more digits**, **backticked
symbols**, **from the claim paragraph only**, hex compared by value and others case-insensitively.
The tie-break order is exactly **shared-anchor count, then topic, then depends_on, then
shared-evidence count, then ID** — note it is *not* "topic first".

M5 as **implemented** (not just specified), from `git show cc2dfee:src/kblam/check.py`:

```python
HEX_RE = re.compile(r"(?<![0-9A-Za-z_])0[xX]([0-9A-Fa-f]+)(?![0-9A-Za-z_])")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
MIN_AUTO_HEX_DIGITS = 4  # auto-extracted hex must look like an address, not a small constant
```
```python
def anchors_of(finding: Finding) -> frozenset[str]:
    """Declared anchors, plus hex addresses (4+ digits) and `backticked` symbols in the claim.
    Hex is compared by value (0x001a2b3c == 0x1A2B3C); other anchors case-insensitively."""
```
```python
def _rank(c: Candidate) -> tuple:
    # SPEC §6.1: shared anchors, then topic; then depends_on, shared evidence and ID so the order is total.
    return (-len(c.anchors), not c.topic, not c.depends, -len(c.evidence), id_number(c.finding_id))
```

Both `finditer` calls scan `finding.claim` only, never the body. The hex match requires a
non-word character (or string start/end) on both sides, and the backtick token may not span a
newline. So on the labelled set (S2: 14 of 420 claims hold a 4+ digit `0x` literal, 0 hold a
backtick) M5's auto-anchor link fires on roughly 3% of pairs at most — and the *declared*
`anchors` field it also reads does not exist in the labelled data at all (S1).

**Current §6.1, verbatim, SPEC.md:165–172 and 174–180** (the parts a harness must reproduce):

```
- **Similar** findings: every other finding, scored by lexical similarity to N. The score is BM25
  (k1 = 1.2, b = 0.75) of N's title and claim paragraph as the query against each finding's title
  and claim paragraph, over the whole KB (every topic). Tokens are lowercased runs of letters,
  digits, `_`, `.`, `-` and `/` with trailing punctuation stripped, so `0x1A2B3C`, `MX-100` and
  `foo_bar()` stay whole; a small fixed English stop-word list is dropped; there is no stemming.
  Pure Python, no dependency, deterministic. A finding in N's topic gets a bonus of
  `topic_bonus` (§9, default 0.2) times N's top score, so same-topic findings rank ahead of equally
  similar ones elsewhere.
```
```
Budget: `max_candidates` (§9, default 30) pairs whose scopes overlap. Linked findings come first
(dependencies, then shared anchors, then shared evidence); the rest of the budget goes to similar
findings in score order, ties by ID. A similar finding scoring 0 (no shared token) is never a
candidate. Pairs with disjoint scopes don't count against the budget. The quantity comparison (§6.3)
is code, so it covers every finding whose scope overlaps N's, candidate or not; a finding it
conflicts with is added to the check with only the quantity verdict. Each check's candidate set, with the reasons each
matched, is logged to `.kblam/checks.jsonl`.
```

Note the last sentence changed in `1713b0b`: it used to read "The quantity comparison (§6.3) also
covers candidates past the budget, since it is code." It now covers *every scope-overlapping
finding*, candidate or not, and adds a conflicting one to the check with the quantity verdict
alone. That is broader than the old text and is a harness-relevant difference if you are counting
what the budget actually gates.

**The tokenizer as implemented** — `src/kblam/check.py` (working tree):

```python
TOKEN_RE = re.compile(r"[\w./-]+")  # \w: letters, digits and _ (Unicode)
TRAILING_PUNCTUATION = ".-/"
```
```python
def tokens(text: str) -> list[str]:
    """Lowercased runs of letters, digits, `_`, `.`, `-` and `/`, trailing `.`, `-` and `/` stripped,
    stop words dropped, no stemming: `0x1A2B3C`, `MX-100` and `foo_bar()` stay whole."""
    out = []
    for match in TOKEN_RE.finditer(text.lower()):
        token = match.group(0).rstrip(TRAILING_PUNCTUATION)
        if token and token not in STOP_WORDS:
            out.append(token)
    return out
```

Order of operations: lowercase the whole string first, then match, then strip trailing
`.`, `-` and `/` from each token, then drop stop words, then drop empties. Unicode `\w`, so
`<` `>` `(` `)` are boundaries rather than part of a token. `TOKEN_RE` has no `re.ASCII`.

**The stop-word list, verbatim** (`src/kblam/check.py`; the comment names its provenance):

```python
# Lucene's English stop set (EnglishAnalyzer.ENGLISH_STOP_WORDS_SET): small, fixed and project-neutral.
STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in", "into", "is", "it",
    "no", "not", "of", "on", "or", "such", "that", "the", "their", "then", "there", "these", "they",
    "this", "to", "was", "will", "with",
})
```

**33 words** (counted from the set above; all 33 distinct — Lucene's English set is also 33).
Note what is **not** in it: `is`/`are`/`was` are, but `do`, `does`, `has`, `have`,
`from`, `we`, `our`, `its`, `itself`, `than`, `so`, `all`, `any` are not — so
"does not show" reduces to `does`, `show`, and any harness using a different stop list will
score differently.

**Scoring**, verbatim (`Corpus.scores`):

```python
            idf = math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
            for path, count in postings:
                norm = 1 - BM25_B + BM25_B * self.lengths[path] / self.average
                scores[path] = scores.get(path, 0.0) + idf * count * (BM25_K1 + 1) / (count + BM25_K1 * norm)
```
with the docstring: *"Each query token counts once. IDF is ln(1 + (N - n + 0.5) / (n + 0.5)),
positive for every token that occurs, so a document scores 0 exactly when it shares no token
with the query."* The document is `title + "\n" + claim` (`_document`), and the query is the
same function applied to N — so **N's own title and claim are the query, term frequencies
included**.

`topic_bonus` weight: **0.2**, `§9` line 600 —
`topic_bonus = 0.2              # §6.1: same-topic similarity bonus, as a fraction of the top score`.
It is a fraction of *N's top score*, so the bonus is a different absolute size for every
finding; `bonus = topic_bonus * top if same_topic and bm25 > 0 else 0.0`.

---

## S9 — Environment for the eval

verified: 2026-09-23, commands run from the kblam checkout on this machine
evidence: `uv --version`, live `uv run --with` invocations, import results

- `uv` 0.11.13 (`x86_64-pc-windows-msvc`).
- kblam's project venv is Python **3.11.9** (`.venv` inside the checkout).
  `kblam/.python-version` pins 3.11. `requires-python = ">=3.11"`.
- The plain system `python` on PATH is also **3.11.9** — a machine-wide Python 3.11 install
  under `%LOCALAPPDATA%\Programs\Python\Python311` — and it already has
  `numpy` 1.26.4 and `scipy` 1.17.1: that is the interpreter the M4 evidence scripts
  (the metrics script etc.) were run with. The kblam `.venv` has **no** numpy.
- Importable in the kblam `.venv` as it stands: `ruamel.yaml`, `typesafe-sdk` and their
  deps. **Not** importable anywhere without an install: `rank_bm25`, `bm25s`, `ollama`,
  `scikit-learn`.

**`uv run --with` works here and does not touch `pyproject.toml` or `uv.lock`** (verified:
`git status` after three such runs shows no change to either file). Exact invocations, all
run and confirmed:

```bash
# from the kblam checkout
uv run --no-project --with rank-bm25 --with ollama python -c "import rank_bm25, ollama; print('ok')"
# -> ok, Python 3.11.9, 14 packages installed, 1.5 s

uv run --no-project --with scikit-learn python -c "import sklearn, numpy, scipy; print(sklearn.__version__, numpy.__version__, scipy.__version__)"
# -> 1.9.1 2.4.6 1.17.1, 7.9 MiB wheel, 2.6 s total

uv run --with rank-bm25 python -c "import rank_bm25; print('ok')"
# -> ok (also works without --no-project; uv builds an ephemeral env either way)
```

`--no-project` is the safer form for the eval, since it does not involve kblam's own
dependency resolution at all. For a script file rather than `-c`, the same prefix takes the
script's path; the similarity-eval scripts these notes cite were run that way (they were removed
from the public tree, so no command for them is quoted here).
Because `--with` is ephemeral, the run pays the resolve cost each time (1–3 s here), and
numba/`bm25s`'s `[core]` extras are *not* pulled by a bare `bm25s`.

Two additions, both measured after S9 was written. **The plain `python` on PATH is 3.11.9 and
numpy 2.4.6 is importable in it** (that install's `python.exe`),
so a numpy-and-rank_bm25-only script does not need uv at all — but that interpreter is a
machine-wide one and nothing gets installed into it cleanly. One invocation run and confirmed
end to end from the kblam checkout:

```bash
uv run --no-project --with numpy --with rank-bm25 python -c "import numpy,rank_bm25,sys;print('OK',sys.version.split()[0],numpy.__version__)"
# -> OK 3.11.9 2.4.6
```

`--no-project` is the right form for the eval: it skips kblam's project resolution entirely,
which is what keeps `pyproject.toml` and `uv.lock` untouched.

**HTTP versus the `ollama` package — the trade-off, not a recommendation.** Both are documented
in S5 and both were exercised here. Stdlib `urllib` against `http://127.0.0.1:11434/api/embed`
needs no dependency, cannot hit the `OLLAMA_HOST` trap, and is what every measurement in S5 was
taken over; the `ollama` package adds `--with ollama` (verified installing and calling with
`Client(host=...)`), a typed response object, and its own request handling, at the cost of a
dependency and a host that must be passed explicitly. At ~8 requests per model for the whole
pool, request-handling convenience buys little. Which one to use is the implementer's call —
nothing about the measured numbers changes between them, because both reach the same endpoint.

**Nothing about embeddings exists in kblam to reuse.** `grep` over `src/kblam/*.py` for
`embed`, `vector`, `cosine`, `numpy` returns only K9's unrelated token-set Jaccard
(`duplicate_similarity`, `src/kblam/rules.py:509-527`) — so an embedding arm is not
reimplementing anything that is already in the repo, and kblam's runtime dependencies are just
`ruamel-yaml` and `typesafe-sdk`. Likewise the BM25 side: reuse `Corpus`/`tokens` from
`src/kblam/check.py` if you want kblam's tokenizer exactly (S4, S10 give it verbatim), but it
is written against `Finding`/`KBView`, not against a list of claim strings, so a harness needs
either a thin adapter or a faithful copy — say which you did.

**One thing already in the tree that is worth reusing rather than hand-rolling: the M4 metric
scripts, which implement the pre-registered evaluation rule verbatim.** The unpublished
evaluation left four small modules: the metrics script (the threshold grid over
"the distinct values returned on the calibration half, plus 0.50-0.99 in steps of 0.01",
highest recall at precision ≥ 0.90 else ≥ 0.50, ties to the higher T then the higher C, then
the reject/review/disabled cut-offs on the held-out half, and rounding of returned values to 10
decimals before any comparison), the pooled metrics script (the pooled-pairs rule for the
duplicate verdicts and the separate reporting of constructed versus corpus items), the gold
builder (gold construction and the 50/50 per-class split, seed 20260923), and the
low-confidence script (the post-hoc review band). A candidate-selection eval measures recall
rather than thresholds, so these are a rule source and a style precedent more than a drop-in —
but if the harness reports precision and recall on halves, matching the metrics script's
its numbers comparable to the M4 ones already in the SPEC.

---

## S10 — Tokenizer positive facts, `id_number`, rank-vs-budget order, scope, stemmer audit

verified: 2026-09-23 @ kblam `1713b0b` for everything quoted from the tree, `cc2dfee` for M5
evidence: verbatim code excerpts; scope counts computed over all 210 pairs

**The tokenizer, in full, and what it does and does not do.** `src/kblam/check.py`, working
tree, complete function and every constant it references:

```python
TOKEN_RE = re.compile(r"[\w./-]+")  # \w: letters, digits and _ (Unicode)
TRAILING_PUNCTUATION = ".-/"
# Lucene's English stop set (EnglishAnalyzer.ENGLISH_STOP_WORDS_SET): small, fixed and project-neutral.
STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in", "into", "is", "it",
    "no", "not", "of", "on", "or", "such", "that", "the", "their", "then", "there", "these", "they",
    "this", "to", "was", "will", "with",
})


def tokens(text: str) -> list[str]:
    """Lowercased runs of letters, digits, `_`, `.`, `-` and `/`, trailing `.`, `-` and `/` stripped,
    stop words dropped, no stemming: `0x1A2B3C`, `MX-100` and `foo_bar()` stay whole."""
    out = []
    for match in TOKEN_RE.finditer(text.lower()):
        token = match.group(0).rstrip(TRAILING_PUNCTUATION)
        if token and token not in STOP_WORDS:
            out.append(token)
    return out
```

Positive facts, each answered explicitly rather than by omission:

- **A stop-word set exists.** **33** words, verbatim and complete above, all 33 distinct. It is
  Lucene's English set, per the comment naming `EnglishAnalyzer.ENGLISH_STOP_WORDS_SET`; Lucene's
  `ENGLISH_STOP_WORDS_SET` is also 33 words. Counted by parsing the set out of the source, not
  read off the prose.
- **Lowercasing: yes**, applied to the whole string before matching (`text.lower()`), so it is
  Unicode-lowercased, not ASCII.
- **`TRAILING_PUNCTUATION` is applied with `rstrip` only — trailing end only, never leading,
  and per token.** `rstrip` removes *all* trailing characters in the set, so `foo...` → `foo`
  and `a/b/` → `a/b`. A leading `.` or `-` is kept, though `TOKEN_RE` can only produce one at
  the start of a token if the token starts with it.
- **No minimum token length.** Single-character tokens survive unless they are stop words.
- **No stemming.** None, anywhere in the function.
- **Digits are not dropped.** Digits are inside the token class, so `0x1a2b3c`, `4000`, and
  `1.0017` are tokens; `4000dpi` and `dpi` are different tokens.
- **Punctuation other than `_ . - /` is a boundary**, so `foo_bar()` tokenizes to `foo_bar`.
- **No empty-string check beyond `if token`**, which also drops a token that was entirely
  trailing punctuation.

**`id_number`** — verbatim, `src/kblam/finding.py`:

```python
def id_number(finding_id: str) -> int:
    return int(finding_id[2:])
```

It assumes kblam's own id shape (`F-0137` → `int("0137")` → 137). On a labelled-set id it
raises: `id_number("PAIR-001")` is `int("IR-001")` → `ValueError`, and `id_number("TOP-001")`
is `int("P-001")` → `ValueError`; `id_number("REV-001")` is `int("V-001")` → `ValueError`. Any
harness reusing kblam's tie-break needs its own `id_number`; the equivalent for this set is the
trailing integer after the last `-`, or plain string sort (which agrees with it for
zero-padded ids within one prefix).

**Ranking happens before truncation — a linked candidate does displace a higher-BM25 one.**
`select_candidates`, working tree:

```python
    matched.sort(key=_rank)
    overlapping = [c for c in matched if c.scope_overlap]
    return Selection(candidates=overlapping[:max_candidates], over_budget=overlapping[max_candidates:],
                     different_scope=[c for c in matched if not c.scope_overlap])
```
```python
def _rank(c: Candidate) -> tuple:
    # SPEC §6.1: linked first (dependencies, then shared anchors, then shared evidence), then similar;
    # within each, by score, and by ID among equal scores.
    link = 0 if c.depends else 1 if c.anchors else 2 if c.evidence else 3
    return (link, -c.score, id_number(c.finding_id))
```

The sort key's first element is the link class, so **every linked candidate ranks above every
unlinked one regardless of BM25**, and the budget slice is taken after that sort. M5 does the
same thing in the same order (`overlapping = sorted(...); ... [:max_candidates]`), with the
M5 rank tuple. Note `_rank`'s ordering here is by link *class*, so a candidate that is both
linked and high-BM25 keeps its class rank; the link class also wins over the topic bonus,
which is folded into `score`.

**`topic_bonus` default: 0.2**, from the code, not the spec —
`src/kblam/jev.py`, `DEFAULT_JEV`:

```python
    "max_candidates": 30,
    "topic_bonus": 0.2,             # §6.1: same-topic similarity bonus, as a fraction of the top score
```

`src/kblam/assets/kblam.toml` writes the same two values (lines 33–34). Nothing else in
kblam defaults them; `config.py` passes the raw `[jev]` table through and `jev.py` fills
missing keys from `DEFAULT_JEV`.

**`scope_overlap` on mismatch: neither dropped nor scored 0 — the candidate is set aside into
a third list.** From the return statement quoted above, a candidate with disjoint scopes goes
to `Selection.different_scope`, and the caller records the pair as `different_scope` and does
not ask Jev. It does not consume budget, and it is not silently discarded — it is visible in
the `Selection` object and, per §6.1, pairs with disjoint scopes "don't count against the
budget". `scopes_overlap` itself:

```python
def scopes_overlap(a, b) -> bool:
    """`any` overlaps every scope; otherwise the part sets must intersect."""
    pa, pb = scope_parts(a), scope_parts(b)
    return ANY_SCOPE in pa or ANY_SCOPE in pb or bool(pa & pb)
```

**Scope on the labelled set — not a constant, so `scope_overlap` is informative.** Five
distinct values across the 420 claim slots:

| scope value | slots |
|---|---|
| `MX-200` | 248 |
| `host-software` | 103 |
| `any` | 40 |
| `MX-100` | 23 |
| `MX-100/MX-200` | 6 |

Of the 210 pairs: **189 have the identical scope string on both sides**, and **18 have disjoint
scopes** under kblam's rule (`MX-200`+`host-software` ×8, `host-software`+`MX-100` ×3,
`MX-100`+`MX-200` ×2, `host-software`+`MX-200` ×2, `MX-100`+`host-software` ×2,
`MX-100/MX-200`+`host-software` ×1). All 18 are corpus pairs; no top-up pair is disjoint.
So **192 of 210 pairs overlap and 18 do not** — that is 8.6% of the set that the scope gate
removes before Jev is asked, and it is worth reporting rather than glossing as "100% overlap".

**What the gate costs, exactly.** Of the 18 disjoint pairs, **16 carry a gold label and all 16
are `unrelated`** — so the scope gate removes no positive pair of any class, and its 16 removals
are all true negatives it gets right for free. The 192 overlapping pairs carry 163 gold labels
(163 + 16 = 179, the full relation set). Confirmed independently twice: computed here, and
reported by impl-sim's loader with the same six scope combinations and multiplicities. A
selector's recall should therefore be reported over all 179 or over the 192; either way the
scope gate is not a source of missed positives.

**Stemmer audit: kblam names none, and defines no second stop-word list.** A case-insensitive
search of `SPEC.md`, `src/`, `tests/` and the research notes for `snowball`, `porter`, `nltk`,
`stemmer`, `stemming`, `stop_word`/`stopword`/`stop word` returns exactly three kinds of hit:
`SPEC.md:169` and the `tokens()` body/docstring (which say *no stemming* and name the Lucene
stop set), and two lines in `typesafe/cookbooks_rerank_typesafe.md` — a **TypeSafe
cookbook**, not kblam code — which call `bm25s.tokenize(..., stopwords="en")`. So:

- No stemmer is named or used anywhere in kblam. `snowballstemmer`'s English stemmer is a
  reasonable choice and is consistent with the one neighbouring use in the tree: `bm25s`'s
  optional `[stem]` extra is PyStemmer, i.e. Snowball bindings, and its `stopwords="en"` is
  its own list, not kblam's.
- No other stop-word list exists in kblam to prefer over the 33-word `STOP_WORDS`. The K4
  `history_terms` list in kblam's own `kblam.toml` is a different thing entirely (revision-history
  phrases the validator rejects) and must not be repurposed.
- So the honest framing is the one proposed: the no-stemming arm is kblam's tokenizer exactly;
  the stemming arm is kblam's tokenizer plus Snowball-English applied to each token after
  `rstrip`, with no stop-word removal beyond kblam's own 33. State in the README whether the
  stemmer runs before or after the stop-word filter, because the two orders differ: filtering
  first means the 33 stop words never reach the stemmer (`was` would otherwise become `wa`),
  while stemming first would let a token stem *into* a stop word. Filtering first is the
  faithful reading — kblam's own pipeline filters the raw token and never stems at all — and
  note that `does`, `do`, `has`, `from` are **not** in kblam's 33, so they pass the filter and
  do reach the stemmer under that order.
