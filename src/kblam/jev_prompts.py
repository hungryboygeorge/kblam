"""The Jev question shapes (SPEC §6.2).

The wording itself lives in each project's `kblam.toml` under `[jev.prompt]` (§9), so a project can
adapt it. This module owns what the API contract fixes: the relation option keys, the question
`type`s, the state shapes and SHAPE_VERSION. `load_questions` turns the config's tables into the two
question dicts as they are sent, and `prompt_id` hashes them: any edit to the text, and any change to
a shape, changes the id. The id is the pair-cache key (§6.5) and what `[jev.thresholds]` records, so
answers and thresholds calibrated on other wording are never reused or trusted.

Rules the wording follows (desk-jevdocs Q5): name the parts of state with backtick paths; ask one
hop deep; each Choice option describes a condition the state could satisfy and never argues for
being picked; Noul `true` means yes and `false` means no.
"""

from __future__ import annotations

import hashlib
import json

from kblam.config import CONFIG_NAME, ConfigError

SHAPE_VERSION = 1  # bump when a state shape or a question type changes; it is part of the prompt id

RELATION_KEY = "relation"
REVISION_KEY = "revision"
RELATION_TYPE = "choice"
REVISION_TYPE = "noul"

RELATION_OPTIONS = ("same_fact", "restates_and_extends", "cannot_both_be_true", "compatible_same_subject",
                    "unrelated")
REVISION_ANSWERS = ("true", "false")   # the Noul's answers, as [jev.prompt.revision.criteria] keys

PROMPT_KEYS = (RELATION_KEY, REVISION_KEY)
CRITERION_KEYS = ("what", "not_for", "examples")
QUESTION_KEYS = ("instructions", "criteria")

# Where the prompt went (SPEC §9): every message about prompt_version says this, so a project
# upgrading kblam is told what to do rather than just that its key is unknown.
PROMPT_MOVED = (f"the Jev questions now live in {CONFIG_NAME} under [jev.prompt.relation] and "
                f"[jev.prompt.revision], and [jev.thresholds] carries prompt_id, the id of that prompt "
                f"(kblam prompt-id prints it), instead of prompt_version")


def side_state(claim: str, scope: list[str]) -> dict:
    """One finding as it appears in state: its claim paragraph and the scope it applies to."""
    return {"claim": claim, "scope": scope}


def relation_state(existing: dict, new: dict) -> dict:
    return {"existing": existing, "new": new}


def revision_state(new: dict) -> dict:
    # Keyed by `new` so the instructions can name it; nothing else goes in.
    return {"new": new}


def _keys(where: str, value, keys: tuple[str, ...]) -> dict:
    """`value` as a table whose keys are exactly `keys`, else a ConfigError naming what is wrong."""
    if not isinstance(value, dict):
        raise ConfigError(f"{CONFIG_NAME}: {where} must be a table with the key(s) {', '.join(keys)}, "
                          f"got {value!r}")
    missing = [key for key in keys if key not in value]
    extra = sorted(set(value) - set(keys))
    if missing or extra:
        detail = "; ".join(filter(None, [f"missing {', '.join(missing)}" if missing else "",
                                         f"unknown {', '.join(extra)}" if extra else ""]))
        raise ConfigError(f"{CONFIG_NAME}: {where} keys must be exactly {', '.join(keys)} ({detail})")
    return value


def _text(where: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{CONFIG_NAME}: {where} must be a non-empty string, got {value!r}")
    return value


def _examples(where: str, value) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{CONFIG_NAME}: {where} must be a list of strings, got {value!r}")
    return [_text(f"{where} example", item) for item in value]


def _relation_question(raw) -> dict:
    table = _keys(f"[jev.prompt.{RELATION_KEY}]", raw, QUESTION_KEYS)
    criteria = _keys(f"[jev.prompt.{RELATION_KEY}.criteria]", table["criteria"], RELATION_OPTIONS)
    options = {}
    for option in RELATION_OPTIONS:  # in code order, so the request body does not depend on the file
        where = f"[jev.prompt.{RELATION_KEY}.criteria.{option}]"
        entry = _keys(where, criteria[option], CRITERION_KEYS)
        options[option] = {"what": _text(f"{where} what", entry["what"]),
                           "not_for": _text(f"{where} not_for", entry["not_for"]),
                           "examples": _examples(f"{where} examples", entry["examples"])}
    return {"type": RELATION_TYPE, "instructions": _text(f"[jev.prompt.{RELATION_KEY}] instructions",
                                                         table["instructions"]),
            "criteria": options}


def _revision_question(raw) -> dict:
    table = _keys(f"[jev.prompt.{REVISION_KEY}]", raw, QUESTION_KEYS)
    criteria = _keys(f"[jev.prompt.{REVISION_KEY}.criteria]", table["criteria"], REVISION_ANSWERS)
    return {"type": REVISION_TYPE,
            "instructions": _text(f"[jev.prompt.{REVISION_KEY}] instructions", table["instructions"]),
            "criteria": {answer: _text(f"[jev.prompt.{REVISION_KEY}.criteria] {answer}", criteria[answer])
                         for answer in REVISION_ANSWERS}}


def load_questions(prompt) -> tuple[dict, dict]:
    """The `[jev.prompt]` table of kblam.toml as (relation question, revision question), validated:
    the option keys are exactly RELATION_OPTIONS, the Noul's exactly true/false, every text field a
    non-empty string and every `examples` a list of strings. Anything else raises ConfigError."""
    table = _keys("[jev.prompt]", prompt, PROMPT_KEYS)
    return _relation_question(table[RELATION_KEY]), _revision_question(table[REVISION_KEY])


def prompt_id(relation: dict, revision: dict) -> str:
    """The prompt's identity: 12 hex digits of the sha256 of the questions as they are sent (with
    SHAPE_VERSION), over canonical JSON. Parsed values, so reformatting the TOML does not change it
    and any change to the text does."""
    payload = {"shape": SHAPE_VERSION, RELATION_KEY: relation, REVISION_KEY: revision}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def questions_for_api(prompt) -> tuple[str, dict, dict]:
    """(prompt_id, relation question, revision question) for the `[jev.prompt]` table `prompt`."""
    relation, revision = load_questions(prompt)
    return prompt_id(relation, revision), relation, revision


def relation_questions(question: dict) -> dict:
    return {RELATION_KEY: question}


def revision_questions(question: dict) -> dict:
    return {REVISION_KEY: question}
