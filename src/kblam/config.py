"""Load `kblam.toml` and locate the repository root (SPEC §9)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "kblam.toml"
STATE_DIR = ".kblam"
RESOLUTIONS_NAME = "kblam.resolutions.jsonl"  # committed resolutions of misread items (SPEC §6.4)
ROOT_RE = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")  # [kb] root, as a POSIX path

DEFAULT_KB = {
    "root": "findings",
    "evidence_roots": ["evidence"],
    "labels": ["observed", "decoded", "inferred", "unknown", "reported"],
    "scopes": ["any"],
    "max_lines": 300,
    "max_claim_words": 250,
    # SPEC §9: phrases used only in an unwanted sense in a classified corpus (an evaluation on the
    # pilot corpus, not published).
    # Bare "previously", "no longer", "used to", "falsif", "revised" and "correction:" have legitimate uses.
    "history_terms": [
        "was wrong", "were wrong", "wrong about", "supersed", "withdrawn", "retract",
        "refuted", "is falsified", "was falsified", "falsified by", "now known",
        "previous revision", "previously believed", "previously reported",
        "previously said", "i previously", "not previously", "no longer true",
        "no longer holds", "no longer valid", "no longer safe", "used to argue",
        "turned out", "once quoted", "do not quote", "must not be quoted",
        "do not revive", "removed from this list",
    ],
    # K9: token-set (Jaccard) similarity at or above which two claims are duplicates.
    "duplicate_similarity": 0.9,
    # K5: maximum distance in words between another finding's ID and a history term.
    "history_id_window": 10,
    # .kblam/lock: how long new/edit/put/ack/index wait for it, and the age at which it is broken.
    "lock_wait_seconds": 30.0,
    "lock_stale_seconds": 300.0,
    # K11: the label for a claim that only a retired document states, and where retired documents live.
    # "" disables the reported-label checks; the history-path check still applies.
    "reported_label": "reported",
    "history_dirs": ["history"],
    # M6.10 (SPEC §9 "Keys M6.10 adds"). Each default keeps the behaviour of a kblam.toml without it.
    # K1: the allowed topics; empty means any.
    "topics": [],
    # K12: every blockquote in a finding's body is a verbatim excerpt.
    "verbatim_blockquotes": False,
    # §4, §6.1: a scope value containing the separator stands for each of its parts ("" never
    # splits), and the wildcard overlaps every scope ("" means none does).
    "scope_separator": "/",
    "scope_wildcard": "any",
}

# M6.10 keys whose absence means something other than any value (SPEC §9): None when absent.
# adjudicators: agent types that may run kblam resolve and kblam rm (§8 item 2); absent = no gate.
# history_id_terms: K5's own terms; absent = K5 uses history_terms.
OPTIONAL_KB = ("adjudicators", "history_id_terms")


class ConfigError(Exception):
    pass


class NoConfig(ConfigError):
    """No kblam.toml in the start directory or any parent: there is no knowledge base here. The hooks
    are silent for this (SPEC §8); an unreadable or invalid kblam.toml is a plain ConfigError."""


@dataclass(frozen=True)
class Config:
    repo_root: Path
    findings_dir: str
    evidence_roots: tuple[str, ...]
    labels: tuple[str, ...]
    scopes: tuple[str, ...]
    max_lines: int
    max_claim_words: int
    history_terms: tuple[str, ...]
    duplicate_similarity: float
    history_id_window: int
    lock_wait_seconds: float = 30.0
    lock_stale_seconds: float = 300.0
    reported_label: str = "reported"
    history_dirs: tuple[str, ...] = ("history",)
    jev: dict = field(default_factory=dict)
    topics: tuple[str, ...] = ()
    verbatim_blockquotes: bool = False
    scope_separator: str = "/"
    scope_wildcard: str = "any"
    adjudicators: tuple[str, ...] | None = None
    history_id_terms: tuple[str, ...] | None = None

    @property
    def resolutions_path(self) -> Path:
        return self.repo_root / RESOLUTIONS_NAME

    @property
    def state_dir(self) -> Path:
        return self.repo_root / STATE_DIR

    @property
    def staging_dir(self) -> Path:
        return self.state_dir / "staging"

    @property
    def findings_path(self) -> Path:
        return self.repo_root / self.findings_dir


def find_root(start: Path) -> Path:
    """Walk up from `start` to the first directory containing kblam.toml."""
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / CONFIG_NAME).is_file():
            return directory
    raise NoConfig(
        f"no {CONFIG_NAME} found in {start} or any parent directory; "
        f"run kblam inside a repository that has one, or pass --root"
    )


def _check_type(key: str, value, kind) -> None:
    if kind is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif kind is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind is list:
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
    else:
        ok = isinstance(value, kind)
    if not ok:
        want = "a list of strings" if kind is list else kind.__name__
        raise ConfigError(f"{CONFIG_NAME}: [kb] {key} must be {want}, got {value!r}")


def load_config(root: Path | None = None, cwd: Path | None = None) -> Config:
    if root is None:
        repo_root = find_root(cwd or Path.cwd())
    else:
        repo_root = root.resolve()
        if not (repo_root / CONFIG_NAME).is_file():
            raise ConfigError(f"--root {repo_root} does not contain {CONFIG_NAME}")
    try:
        raw = tomllib.loads((repo_root / CONFIG_NAME).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{repo_root / CONFIG_NAME}: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) else "not UTF-8 text"
        raise ConfigError(f"cannot read {repo_root / CONFIG_NAME}: {reason}") from exc

    kb_raw = raw.get("kb", {})
    allowed = (*DEFAULT_KB, *OPTIONAL_KB)
    unknown = sorted(set(kb_raw) - set(allowed))
    if unknown:
        raise ConfigError(
            f"{CONFIG_NAME}: unknown [kb] key(s) {', '.join(unknown)}; "
            f"allowed: {', '.join(allowed)}"
        )
    kb = {**DEFAULT_KB, **kb_raw}
    for key, default in DEFAULT_KB.items():
        _check_type(key, kb[key], type(default))
    for key in OPTIONAL_KB:
        if key in kb_raw:
            _check_type(key, kb_raw[key], list)

    findings_dir = Path(kb["root"])
    if findings_dir.is_absolute() or ".." in findings_dir.parts or not findings_dir.parts:
        raise ConfigError(f"{CONFIG_NAME}: [kb] root must be a relative path inside the repository")
    if not ROOT_RE.fullmatch(findings_dir.as_posix()):
        # init writes the root into YAML, JSON and shell-quoted text (SPEC §12 M6.5 {{kb_root}})
        raise ConfigError(f"{CONFIG_NAME}: [kb] root must be /-separated segments of letters, digits, '.', "
                          f"'_' and '-', got {kb['root']!r}")
    if not 0 < kb["duplicate_similarity"] <= 1:
        raise ConfigError(f"{CONFIG_NAME}: [kb] duplicate_similarity must be in (0, 1]")
    if kb["lock_wait_seconds"] < 0 or kb["lock_stale_seconds"] <= 0:
        raise ConfigError(f"{CONFIG_NAME}: [kb] lock_wait_seconds must be >= 0 and lock_stale_seconds > 0")

    return Config(
        repo_root=repo_root,
        findings_dir=findings_dir.as_posix(),
        evidence_roots=tuple(kb["evidence_roots"]),
        labels=tuple(kb["labels"]),
        scopes=tuple(kb["scopes"]),
        max_lines=kb["max_lines"],
        max_claim_words=kb["max_claim_words"],
        history_terms=tuple(t.lower() for t in kb["history_terms"]),
        duplicate_similarity=float(kb["duplicate_similarity"]),
        history_id_window=kb["history_id_window"],
        lock_wait_seconds=float(kb["lock_wait_seconds"]),
        lock_stale_seconds=float(kb["lock_stale_seconds"]),
        reported_label=kb["reported_label"],
        history_dirs=tuple(d.strip("/") for d in kb["history_dirs"]),
        jev=raw.get("jev", {}),
        topics=tuple(kb["topics"]),
        verbatim_blockquotes=kb["verbatim_blockquotes"],
        scope_separator=kb["scope_separator"],
        scope_wildcard=kb["scope_wildcard"],
        adjudicators=tuple(kb_raw["adjudicators"]) if "adjudicators" in kb_raw else None,
        history_id_terms=(tuple(t.lower() for t in kb_raw["history_id_terms"])
                          if "history_id_terms" in kb_raw else None),
    )
