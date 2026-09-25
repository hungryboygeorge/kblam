# Jev relation-question wording review

## ⬅️ OPEN QUESTIONS

- Does the proposed relation wording improve accuracy and reduce low-confidence reviews on the 179 M4 labelled pairs, particularly the corpus-only half and the five technical-class boundaries? No A/B call has been made; the exact candidate and evaluation protocol are in A2 and A4.
- Should §6.4 change the low-confidence policy? A positive-winner-only gate suppresses eight of eleven already-resolved false migration reviews but misses three of seven M4 positives in the existing band (A3). This is a policy decision, not a documented model requirement.

## INDEX

prompt/docs       A1  TypeSafe guidance versus current request       2026-09-23 @ kblam 69763fc  L16-29
prompt/proposal   A2  Exact replacement relation question           2026-09-23 @ kblam 69763fc  L30-76
policy/data       A3  M4 and migration low-band tradeoffs            2026-09-23 @ kblam 69763fc  L77-90
experiment        A4  Recalibration and A/B comparison protocol      2026-09-23 @ kblam 69763fc  L91-98
experiment/code   A5  Safe client/config use and M4 input schema    2026-09-23 @ kblam dc51102  L99+

## A1 — TypeSafe guidance versus current request

verified: 2026-09-23 @ kblam 69763fc (working-tree SPEC.md modified; prompt and checker files unchanged); TypeSafe local mirror dated 2026-09-23. The requester directed use of the locally copied documentation, rather than Context7. The local mirror is the documentation provenance; interpretation below is explicitly identified as such. Pages cited as `typesafe/<page>.md` are listed in REFERENCES.md. "The pilot corpus" is a private findings corpus this wording work was evaluated on; that evaluation and its files are not published, so they are named by role below.
evidence: verbatim excerpts from the named files, with file:line attribution.

- **Question and option shape.** `typesafe/primitives_choice.md:284`: “The option names and their descriptions are both sent to the model, so write descriptions that separate the options from each other.” `:599-601`: “Start with a one-line description per option. When two options are similar and the model keeps confusing them, describe each one with an object instead of a string. Give it fields for what the option covers, what belongs to a neighboring option instead, and a few example inputs.” `src/kblam/jev_prompts.py:37-83` already uses `what`, `not_for` and `examples`, and keys each Choice outcome as a condition. The gap is **domain-specific boundaries**, not a missing schema feature: its examples come from an unrelated device domain (`:50-80`); they do not settle whether two firmware claims that share a routine or address but assert different operations are distinct.
- **Framing.** `typesafe/model-jaggedness_jev-1.13.md:31-33`: “state the exact condition in the `instructions`. Be specific. Put boundary cases in the criteria”; where interpretation is unavoidable, “split it into two literal questions and combine them in code.” `:90-92`: “write your instructions as directly as possible. When possible, identify the relevant parts of state by name.” Current `src/kblam/jev_prompts.py:40-42` asks which option describes `new` relative to `existing`, identifies both claim and scope, but leaves “subject” unqualified. Interpretation: specify the particular asserted actor, operation, property and conditions, not simply topical proximity.
- **Criteria must agree with the question.** `typesafe/model-jaggedness_jev-1.13.md:110-114`: “When the `instructions` and the `criteria` ask for different things, `jev-1.13` might get confused”; “treat the criteria as an extension of the instruction.” Current `src/kblam/jev_prompts.py:61-80` alternates between “same subject” and “different subjects” without specifying whether a shared device, routine or address is a subject; it can send overlapping signals for distinct facts. The proposed instructions and every option use *specific assertion/operation* as the common boundary.
- **Relevant context only.** `typesafe/model-jaggedness_jev-1.13.md:94-98`: “Accuracy falls as the state grows with content unrelated to the decision”; “retrieve and filter in code first, and send only the fields the question needs.” `typesafe/cookbooks_entity_alignment.md:118-119`: “One request goes out per pair”; `:147-150`: both entities are in one state, so questions are about the pair. Current `src/kblam/jev.py:198-200,414-435` and `src/kblam/jev_prompts.py:23-29` do that correctly: one pair, two claims/scopes. Do not add full findings, evidence, all candidate pairs or the selection reasons to state. Note the exception: if the claim text omits a necessary subject or condition, no wording can reconstruct it reliably.
- **Numeric/technical claims.** `typesafe/model-jaggedness_jev-1.13.md:66-72`: semantic representations work better than numeric ones, and low-level assembly/binary encoded instructions underperform high-level programming languages; “do the conversion in code.” `src/kblam/check.py:356-365` compares named quantities in code, but raw claim paragraphs still contain hex addresses. Interpretation: tell Jev that a shared address is not proof of the same assertion, and do not expect it to establish arithmetic equality. Changing the state shape to pass derived semantic actor/stage labels would be separate work, not assumed by A2.
- **Competing Choice labels and confidence.** `typesafe/model-jaggedness_jev-1.13.md:137`: “A Choice over options and one Noul per option answer different questions: the Choice is relative, settling *which* option, while each Noul is absolute and can be low for all of them.” `typesafe/confidence.md:143-157`: confidence is calculated from distribution concentration, and a flat distribution may indicate ambiguous or insufficient state. `typesafe/cookbooks_consistency_choice_cookbook.md:935-945` illustrates abstention using top probability and says production cutoffs require labelled examples and the cost of review. Thus sharpening boundaries may concentrate probabilities but **cannot be assumed** to reduce the review band, and low confidence is not itself evidence of a harmful overlap.
- **Compound claims remain a structural limit.** `typesafe/model-jaggedness_jev-1.13.md:145-151` cautions against “Hiding several judgments inside one question.” A claim paragraph may contain multiple assertions, some duplicate and others independent or conflicting. A single five-way Choice must still pick one label; a separate contradiction Noul or claim splitting would need its own design and calibration. A2 is a minimal wording test, not proof that compound claims are solved.
- **Published pair precedents.** `typesafe/cookbooks_citation_check.md:210-235` uses one Choice over supports/contradicts/says-nothing for a claim and a *contextual section*, then holds answers under a confidence gate (`:216-224`). `typesafe/cookbooks_entity_alignment.md:149-158` spells out the middle class carefully so variants reach a curator; its observations are on `jev-1.12` (`:78`), so are a design analogy, **not** a measurement for kblam's `jev-1.13`. The analogous kblam boundary is compatible-same-subject versus unrelated, and both versus restates/contradicts.

## A2 — Exact candidate for `src/kblam/jev_prompts.py`

verified: 2026-09-23 @ kblam 69763fc. This is an unmeasured candidate, not an edit. Replace the present `PROMPT_VERSION = 2` at `src/kblam/jev_prompts.py:14` with `PROMPT_VERSION = 3`, and replace exactly the `RELATION_QUESTION` assignment at `:37-83` with the block below. Leave `RELATION_OPTIONS`, state constructors, `REVISION_QUESTION` and all other code unchanged for the first A/B, so only relation wording changes. `src/kblam/jev.py:106-110` requires `[jev].prompt_version` to match before a live check; `SPEC.md:247-261,289-295` requires recalibration before enabling rejection at version 3.

```python
RELATION_QUESTION = {
    "type": "choice",
    "instructions": {
        "question": "How does the assertion in `new.claim` relate to the assertion in `existing.claim` where their `scope` values overlap?",
        "focus": "Compare the specific actor, operation, property and conditions asserted by each claim. Sharing a device, routine, address, evidence source or technical term alone does not make two assertions the same or contradictory. Different operations or processing stages can both be true.",
    },
    "criteria": {
        "same_fact": {
            "what": "Every assertion in `new.claim` is already made by `existing.claim`, perhaps in different words or as a subset; `new.claim` adds no distinct fact.",
            "not_for": "An added fact, a changed condition, or merely a shared actor, routine or address.",
            "examples": ["existing: 'The transfer engine writes the sample stream to RAM during capture.'; new: 'During capture, sample data is written to RAM by the transfer engine.'"],
        },
        "restates_and_extends": {
            "what": "`new.claim` repeats a factual assertion in `existing.claim` and adds a further detail about that same assertion, without changing it.",
            "not_for": "A different operation of the same routine, a separate compatible fact, or a changed condition or result.",
            "examples": ["existing: 'The controller writes a correction table to ASIC RAM during setup.'; new: 'The controller writes a correction table to ASIC RAM during setup and alternates banks between segments.'"],
        },
        "cannot_both_be_true": {
            "what": "At least one assertion in each claim gives incompatible facts for the same actor, property and conditions in their shared scope; both assertions cannot hold together.",
            "not_for": "Different operations, actors, stages or conditions that can coexist; a statement that a fact is unproven is not by itself an assertion of its opposite.",
            "examples": ["existing: 'During capture the transfer engine enables sample correction.'; new: 'During capture the same transfer engine disables sample correction.'"],
        },
        "compatible_same_subject": {
            "what": "Both claims concern the same specific component or operation, but assert different facts that can both hold; `new.claim` does not repeat an assertion from `existing.claim`.",
            "not_for": "An actual restatement, an incompatible assertion under the same conditions, or only a broad device or calibration topic in common.",
            "examples": ["existing: 'The controller computes a correction table for the ASIC.'; new: 'The ASIC applies a correction table to the sample stream.'"],
        },
        "unrelated": {
            "what": "The claims concern different specific components or operations; their commonality, if any, is only the broader device, pipeline or technical domain.",
            "not_for": "Different properties of one specific component or operation, a restatement, or assertions about one property that conflict.",
            "examples": ["existing: 'The pump motor advances one step.'; new: 'The USB interface reports a command phase.'"],
        },
    },
}
```

Why this candidate: the new `question` preserves the two named state paths and one-hop Choice (`typesafe/model-jaggedness_jev-1.13.md:88-92`). `focus` and `not_for` spell out the ambiguous shared-routine/address and different-stage cases instead of making the model infer the intended boundary (`:29-33`, `typesafe/primitives_choice.md:599-601`). Each option describes a state condition with a technical example, rather than advocating an outcome (`typesafe/primitives_choice.md:284,599-601`). The contradiction option covers one incompatible assertion inside a compound claim but must be tested for false positives (`typesafe/model-jaggedness_jev-1.13.md:145-151`). No additional evidence text or list of other candidates is inserted (`:94-98`).

The revision Noul already has the affirmative polarity the docs prefer: `src/kblam/jev_prompts.py:85-96`; `typesafe/model-jaggedness_jev-1.13.md:110-114` cautions against inverted yes/no criteria. There is no observed revision failure here to justify changing that second question. **Do not transfer version-2 thresholds or cache entries to version 3:** `src/kblam/jev.py:244-247,468-469` keys answers by version; `src/kblam/check.py:532-545` downgrades mismatched thresholds to review.

Potential refinement only after A/B error inspection: if `unrelated` still loses to `compatible_same_subject` on genuinely different routines, a separate absolute Noul for “Do these claims assert a fact about the same specific operation?” may help. That is a different primitive and must have an independently calibrated threshold; its probability cannot be compared directly with the Choice (`typesafe/model-jaggedness_jev-1.13.md:116-137`). Do not add it to the initial A/B, which would confound the wording comparison.

## A3 — Existing M4 and migration evidence; review-band decision

verified: 2026-09-23 @ the pilot corpus d9be1cd (M4 evidence), kblam 69763fc (`check.py` policy). Evidence: the gold and top-up gold files of the unpublished evaluation on the pilot corpus, and its recorded run-1 and top-up run-1 response files; read-only recomputation on 2026-09-23 against the thresholds in `SPEC.md:254-261`. This is **version 2 only**, not an A/B result. The gold set includes constructed examples and corpus pairs, so its mix differs from new findings.

The 179 relation pairs have gold counts: contradiction 47, restate-and-extend 45, same-fact 28, compatible-same-subject 30, unrelated 29. Winner-only agreement by gold class is respectively 44/47, 42/45, 23/28, 27/30, 20/29; overall 156/179. The metrics report and the pooled metrics report give the source confusion matrices. The low-confidence rule in `src/kblam/check.py:546-565` raises review whenever no relation verdict fires and confidence < 0.49, regardless of winning class; relation evaluation in `:581-586` skips the low band if a quantity conflict exists. M4's post-hoc pooled analysis (the low-confidence report) found 12 low-band pairs, **7 actionable gold positives** (contradiction/duplicate/extension) and 5 distinct/compatible negatives.

Among those 12: 4 have a positive-class winner, all 4 actionable gold positives. The other 8 have `compatible_same_subject` as winner (none has `unrelated`): 3 actionable positives and 5 negatives. Thus a “review low confidence only when the winning class is positive” proposal reduces 12 reviews to 4 and eliminates all five false reviews **in this M4 set**, but misses PAIR-025 (contradiction), PAIR-105 (extension), PAIR-120 (duplicate). The low-confidence report shows those rows. A **hypothesis for evaluation, not a recommended threshold**, is to keep low-band review for a negative-class winner if the sum of the three positive-class probabilities is at least 0.40. Replaying the already recorded distributions gives 9 reviews, 7 positives and 2 negatives; the 0.40 cut was selected by inspecting these same cases and is not validated. The low-band split itself is calibration 3 (1 positive, 2 negatives) / held-out 9 (6 positives, 3 negatives); the held-out half is no longer untouched because it informed this post-hoc choice.

At a snapshot of the pilot corpus's `.kblam/review.jsonl` on 2026-09-23, eleven closed items were marked `distinct` with explicit explanations, and all eleven were `low_confidence`; winner counts were `compatible_same_subject` 6, `unrelated` 2, `cannot_both_be_true` 3 (rows 1-4 and 6-12 of that log). A positive-winner-only policy would have prevented 8 of those 11 reviews but retained the three false contradiction winners. There were also an open restates-and-extends review and a later open low-confidence compatible-same-subject review at the time of that read (rows 5 and 13 of that log); neither was treated as a labelled false case. Do not count this migrating corpus as an independent held-out calibration set: these pairs were selected because the current pipeline reviewed them, and their disposition was observed after seeing Jev's answer.

Live false-pair illustration (neutral example): F-0009's first claim sentence says MX-100 `FW:0x1A2B3C` *assigns an operating selector*, while F-0010 says that same routine *computes a correction table*; sharing a routine is not contradiction. That review was closed as distinct in the pilot's review log. The `typesafe/model-jaggedness_jev-1.13.md:66-72,88-98` limitations make exact address matching and the added unrelated detail poor ways to force a decision by prose alone.

**Policy recommendation for the coordinator:** do not immediately remove all low-confidence negative winners. The M4 test demonstrates three real positives would be lost, including one contradiction; the live set demonstrates substantial false-review load. First measure candidate wording on M4 and an independently labelled, representative sample of new technical pairs, with review count and missed actionable cases as separate objectives. If review load still dominates, calibrate a risk-weighted low-band rule using the full positive-class probability mass, or narrow the negative-winner band; choose that threshold on one split and test on a truly new split. Keep a conservative path for uncertain contradiction-positive mass rather than suppressing it solely because `compatible_same_subject` won. TypeSafe documents domain-specific thresholds (`typesafe/confidence.md:177-219`), not one universal policy.

## A4 — Reproducible A/B plan and present measurement limit

verified: 2026-09-23 @ kblam 69763fc. No live Jev A/B was run in this desk: **new requests 0, actual additional cost $0.00**. The M4 historical run costs are recorded, not charged here: 95 relation calls at $0.003981 (the metrics report), and 84 top-up relation calls at $0.003597 (the pooled metrics report), or $0.007578 for a fresh 179-pair relation pass at those historical prices. A fresh two-wording pass is about $0.015156 if both are called, well below the assigned $1 cap, but bill the returned `usage.cost` rather than assuming historical prices. The user explicitly authorized scripts; the desk's developer write scope still restricts it to this answers file, so a writable implementer must run them.

For an A/B implementer: use all 179 relation IDs from the gold and top-up gold files and the matching rows of the two pairs files; pass each finding's `claim` and `scope` through `Side`/`JevClient` in `src/kblam/jev.py`, never open or print the key file or the key environment variable. Use a separate experimental state directory (e.g. `experiments/jev-wording/` inside the kblam checkout) for cache and cost logs, never the pilot corpus's `.kblam/`. Run version 2 with the existing question and version 3 with exactly A2's question under the same requested and served model; account for all retries and missing cost fields. Version-2 recorded responses may serve as historical baseline, but a same-day fresh A/B on both wordings better controls model serving/drift. A cloned config/state directory or a scoped monkeypatch of `jev_prompts.RELATION_QUESTION` and `PROMPT_VERSION` may drive the existing client without editing `src/` or `SPEC.md`. Do not copy a key; let `JevClient` load it through its own existing mechanism (`src/kblam/jev.py:153-177,382-435,540-575`).

Report per gold class: total, winning-label accuracy, confusion matrix, actionable verdict precision/recall under **separately calibrated** thresholds, and low-band counts (all / actionable positives / negatives); report calibration and held-out separately as well as pooled. First compare uncalibrated winners and confidence at a fixed descriptive 0.49 band; then choose version-3 thresholds only on calibration and evaluate once on held-out. Since the old 0.49 rule and this candidate were proposed after examining both M4 halves, even that held-out result is exploratory; use a newly labelled technical corpus before production adoption. Compare the migration false examples for directional qualitative behavior but do not mistake them for unbiased accuracy. Require the same served model ID for every comparison and give actual aggregate OpenRouter `usage.cost` (`src/kblam/jev.py:550-575`); stop before the cumulative $1 limit.

## A5 — Safe relation-only client/config use and M4 input schema

verified: 2026-09-23 @ kblam dc51102; the intervening commit changed only `SPEC.md` among `src/kblam/{jev,jev_prompts,config,check}.py` and `SPEC.md` (`git diff --stat 69763fc..dc51102`); no unstaged changes in those source files. M4 evidence live-checked at the pilot corpus d9be1cd. Evidence and exact interfaces follow.

**API and request path.** `src/kblam/jev.py:180-200`: `Side(finding_id: str, claim: str, scope: tuple[str,...], fingerprint: str)` is a frozen dataclass, `Side.state()` returns `jev_prompts.side_state(self.claim, list(self.scope))`. `:382-405`: `JevClient(cfg: Config, *, transport=None, sleep=time.sleep, max_attempts=4, timeout=30.0)` is a context manager. `:414-435`: `client.ask_relation(existing: Side | Finding, new: Side | Finding) -> RelationResult` constructs `relation_state(existing.state(),new.state())`, sends `jev_prompts.relation_questions()`, and parses the Choice. `:458-461`: `client.ask_relations(pairs, workers=6)` returns results **in input order**, with `JevUnavailable` as an element for any failed pair; it is not a raising all-or-nothing batch. Call only these two relation methods; do not invoke `ask_revision` or `Checker`.

The result is `RelationResult(existing, new, winner, probabilities, confidence, call)` (`src/kblam/jev.py:223-230`). Its `call` is `CallInfo(requested_model, served_model, expected_served_model, input_tokens, output_tokens, cost, latency_s, attempts, generation_id, provider, cached=False)` (`:202-220`). `cost` comes from OpenRouter's `raw_http_response.json()["usage"]["cost"]`, or `None` when missing (`:550-575`); the client writes only IDs/fingerprints/results/cost to its log (`:343-356,565-575`), not claims or the key. `ask_relation` first checks its pair cache (`:417-425`); for a fresh A/B require `result.call.cached is False`, and report `None` costs rather than treating them as zero. Check `served_model == expected_served_model` for every result.

**Safe isolated config.** `src/kblam/config.py:51-69` declares frozen `Config` with `repo_root`, `jev` and `state_dir == repo_root / '.kblam'`; `:106-158` loads the pilot corpus's committed config. That config has `[jev]` model, expected model, key-file *path* and `prompt_version = 2`; the path names a file but contains no key. With `dataclasses.replace`, keep the loaded Jev settings and redirect only state plus experimental version:

```python
from dataclasses import replace
from pathlib import Path
from kblam.config import load_config
from kblam.jev import JevClient, JevUnavailable
from kblam import jev_prompts

base = load_config(Path('<pilot repo>'))
# Create run_root under <experiment root>/ in the kblam checkout.
# Give every fresh variant a distinct run_root and do not reuse an existing cache.
def isolated_config(run_root: Path, version: int):
    return replace(base, repo_root=run_root,
                   jev={**base.jev, 'prompt_version': version, 'thresholds': {}})

original_question = jev_prompts.RELATION_QUESTION
# candidate_question is the complete A2 RELATION_QUESTION dict, copied into the runner.
for version, question in ((2, original_question), (3, candidate_question)):
    jev_prompts.PROMPT_VERSION = version
    jev_prompts.RELATION_QUESTION = question
    config = isolated_config(output_root / f'run-v{version}', version)
    with JevClient(config) as client:
        first = client.ask_relation(*pairs[0])  # fail fast on auth/credits
        rest = client.ask_relations(pairs[1:], workers=6)
        answers = [first, *rest]
    failures = [a for a in answers if isinstance(a, JevUnavailable)]
    if failures:
        raise RuntimeError(f'{version}: {len(failures)} Jev request(s) unavailable')
    if any(a.call.cached for a in answers):
        raise RuntimeError(f'{version}: unexpected cache hit')
    if any(a.call.served_model != a.call.expected_served_model for a in answers):
        raise RuntimeError(f'{version}: served model mismatch')
```

This block is pseudocode: the two paths are placeholders, and `output_root`, `candidate_question` and `pairs` are runner-defined and `run_root` must be created and writable. It illustrates the exact client calls and patch timing, not a complete runner. The order matters: `jev_settings(cfg)` validates `cfg.jev['prompt_version'] == jev_prompts.PROMPT_VERSION` (`src/kblam/jev.py:86-111`) when `JevClient` is constructed (`:388`); its settings/cache key then fix the version (`:468-469`), while the question object is looked up when `ask_relation()` sends the request (`:425-435`). Patch both **before** constructing each client, complete all worker calls, close the client, then switch the global prompt; never run variants concurrently in one process. Use separate run roots so `.kblam/pairs.sqlite` and `.kblam/calls.jsonl` (`:244-287,343-356,385-390`) stay under the authorized experiment tree. `src/kblam/jev.py:153-177,495-505` loads the key internally on the first noncached request, preferring the configured environment variable to the configured file, without exposing the value. Do not open, print, pass around or log the key file or variable in the runner.

**M4 rows and split.** The original calibration runner (not published) normalizes claim whitespace, wraps scalar scope as a one-element tuple, hashes `(item_id, role, claim, JSON(scope))` for a per-role/per-pair fingerprint, constructs `Side(f'{item_id}/{role}', claim, tuple(scope), fp)`, calls `ask_relation`, checks uncached and serializes `asdict(res.call)`. It reads the labeller-facing claims file and the gold file; the top-up runner reads the top-up claims file and the top-up gold file. These claim rows have `pair_id`, `existing:{claim,scope}`, `new:{claim,scope}`; `scope` can be a string or list. The corpus pairs file (120 pair rows) and the top-up pairs file (90 pair rows) contain the same `existing`/`new` claim-and-scope objects as those two request-claims files respectively: a read-only equality comparison found **zero differences** for all matching pair IDs. The pair rows additionally carry excerpts, corpus signals, builder class, etc.; only pass the two claim/scope objects to Jev. The labeller-facing claims file also contains revision-only rows (`item_id`, `claim`, `scope`) that must be excluded from this relation-only A/B.

Gold rows are JSON objects with `id`, `gold`, `source`, `half` (`'calibration'` or `'heldout'`), and `numeric`; top-up rows additionally have `constructed` and `construction`. Select IDs present in a pair-row map by `pair_id` rather than assuming row order; this yields **95 PAIR relation rows plus 84 TOP relation rows = 179**, with 91 calibration and 88 held-out. The gold file also contains 41 REV items; they are not part of this comparison. Do not reconstruct the split by hashing or alternating IDs: use each gold row's `half` field. Running a runner as `uv run --project <kblam repo> --locked python <experiment root>/<runner>.py` imports the project's installed `kblam` from any cwd; the M4 runners used the same `--project ... --locked` form from their experiment root. They store fresh calls in isolated per-run state, fail fast on one request before parallelization, check `cached`, preserve actual cost/model metadata, and refuse to overwrite existing raw output. Follow that shape under the experiment directory and keep aggregate `usage.cost` below $1.

**Original 12 low-band cases for focused error inspection.** Read-only join of the gold labels and the version-2 run-1 and top-up run-1 response files, with current §6.4 verdict thresholds and `confidence < 0.49`; `p` below means p(winner), `positive mass` is the sum over same_fact, restates_and_extends and cannot_both_be_true. These values are historical, not the expected version-3 answer.

| ID | Gold | Winner | p | Confidence | Positive mass |
|---|---|---|---:|---:|---:|
| PAIR-005 | cannot_both_be_true | cannot_both_be_true | .55 | .43 | .57 |
| PAIR-025 | cannot_both_be_true | compatible_same_subject | .43 | .29 | .57 |
| PAIR-101 | restates_and_extends | same_fact | .54 | .42 | .99 |
| PAIR-105 | restates_and_extends | compatible_same_subject | .59 | .47 | .41 |
| PAIR-115 | compatible_same_subject | compatible_same_subject | .44 | .31 | .54 |
| PAIR-116 | restates_and_extends | restates_and_extends | .36 | .20 | .65 |
| PAIR-120 | same_fact | compatible_same_subject | .47 | .32 | .53 |
| PAIR-130 | compatible_same_subject | compatible_same_subject | .54 | .42 | .45 |
| PAIR-133 | unrelated | compatible_same_subject | .49 | .35 | .04 |
| TOP-014 | same_fact | same_fact | .49 | .36 | .68 |
| TOP-084 | compatible_same_subject | compatible_same_subject | .58 | .47 | .02 |
| TOP-090 | compatible_same_subject | compatible_same_subject | .50 | .38 | .01 |

**Exact version-2 policy predicate for the A/B.** The current configured thresholds (the pilot corpus's committed `kblam.toml`) are `same_fact: p >= 0.67 and confidence >= 0.59`, `cannot_both_be_true: p >= 0.59 and confidence >= 0.49`, `restates_and_extends: p >= 0.47 and confidence >= 0.34`, `revision: noul >= 0.75`, and `low_confidence_review = 0.49`; the three relation keys alone are members of `RELATION_VERDICTS` (`src/kblam/check.py:24-31`). `compatible_same_subject` and `unrelated` have no firing threshold, regardless of their probability. For an answered pair, code computes `winner`, `p = probabilities.get(winner, 0.0)` and returns a relation verdict only when that winner has a configured threshold and **both** inclusive comparisons hold (`src/kblam/check.py:546-554`). It then raises low-confidence review if and only if `low_confidence_review` is configured, `confidence < 0.49` (**strict**), no relation verdict fired **on this pair**, and no `quantity_conflict` fired on this pair (`:556-586`). A relation-only M4 A/B has only claim/scope pair inputs, not finding metadata quantities, so reproduce it as `not relation_fired and confidence < 0.49`. A separate revision Noul on the new finding is not consulted in this pair's low-band decision (`:588-597`); do not require `not revision_fired`. Live `Checker` also suppresses already resolved distinct pairs after computing verdicts (`:599-607`); gold A/B has no such workflow state.

Choice `probabilities` and `confidence` both use **unit interval 0..1**, not percents: `typesafe/primitives_choice.md:343-347` says the distribution sums to one and confidence is computed from its concentration, not a second independent probability. The M4 logs store decimals such as 0.55 and 0.43; compare floats directly with the inclusive/strict operators above and do not round before thresholding (the observed binary-float artifacts are noted in the metrics report). A low confidence value is not the probability the winner is wrong; p(winner) is one relative Choice probability, not a prevalence estimate (`typesafe/model-jaggedness_jev-1.13.md:116-137`).

**Model identifiers and fingerprints.** The pilot corpus's committed `kblam.toml` requests exactly `typesafe/jev-1.13` and expects exactly `typesafe/jev-1.13-20260917`; there is no normalization or prefix stripping. `src/kblam/jev.py:218-220` defines `CallInfo.model_mismatch` as plain `served_model != expected_served_model`; `:293-299` rejects a cache row with a different served string, while `:468-483` keys and logs the exact strings. The checker separately compares returned served strings to the calibrated policy string and turns *fired* Jev verdicts into reviews on a mismatch, without altering the scores (`src/kblam/check.py:532-545,599-601`). For this experiment, keep the actual returned ID in every row and refuse a mismatched run rather than silently normalizing it. `Side.fingerprint` is an opaque caller-supplied string (`src/kblam/jev.py:180-200`), and the cache key uses it as-is with direction and prompt version (`:244-265,468-469`). A deterministic full SHA-256 of `item_id`, role (`existing`/`new`), normalized claim and JSON scope is valid and prevents distinct labelled items with identical prose from accidentally sharing a row; the previous M4 runner did exactly this. It is not required to match a real finding fingerprint for a completely isolated experimental cache. The separate run roots and `cached is False` check remain necessary even with these fingerprints.
