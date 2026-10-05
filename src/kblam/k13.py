"""K13, affected uses (SPEC §5.1.4 K13, "Where each rule blocks"): a verbatim excerpt that quotes a
confirmed challenge's assertion, and the warnings for references that only name the challenged source."""

from __future__ import annotations

from dataclasses import dataclass

from kblam import k12, matching, paths, records
from kblam.finding import Finding
from kblam.k12 import ChallengeInfo
from kblam.matching import ExcerptMatch
from kblam.rules import Issue

USE_REVIEW = "kblam use review {challenge} {finding} {ordinal} --by NAME --proponent NAME"
CHALLENGE_NEW = "kblam challenge new {path} --lines {lines} --by NAME"
CHALLENGE_USES = "kblam challenge uses {challenge}"

MESSAGE_SAME_BYTES = ("{challenge} challenges this quoted assertion at {source}@{version}:{lines}; edit the "
                      "finding or have this use reviewed ({review}). K10 is checked separately.")
MESSAGE_UNPROVED = ("{challenge} was judged on {source}@{version} only, and this excerpt quotes its "
                    "assertion text from another version of that file. This does not show that the version "
                    "is wrong: challenge it ({new}) or have this use reviewed ({review}).")
MESSAGE_RANGE = ("the cited range {path}:{cited} overlaps lines {lines} of {challenge}'s assertion without "
                 "quoting it; check that the excerpt does not rely on the challenged text ({uses} lists "
                 "what {challenge} affects)")
MESSAGE_EVIDENCE = ("evidence lists {path}, which {challenge} challenges; a listed source is not shown to "
                    "be safe, so check what this finding takes from it against {challenge}'s assertion and "
                    "limits ({uses} lists what {challenge} affects)")
MESSAGE_PROSE = ("the text names {path}, which {challenge} challenges; a named source is not shown to be "
                 "safe, so check that the claim does not rest on {challenge}'s assertion ({uses} lists "
                 "what {challenge} affects)")


@dataclass(frozen=True)
class _Hit:
    """One excerpt a confirmed challenge relates to. `kind` is "same" (same bytes, the assertion's span
    intersects), "other" (another version quotes the assertion text) or "range" (a line tag's range
    overlaps the assertion's lines: a warning)."""
    finding: Finding
    match: ExcerptMatch
    info: ChallengeInfo
    kind: str


def k13(view, reader) -> list[Issue]:
    """Every K13 issue, owner = the finding's ID: an error for each affected excerpt no current use
    covers, and the warnings of SPEC §5.1.4 K13."""
    issues: list[Issue] = []
    covering = _CurrentUses(view, reader)
    for hit in _hits(view, reader):
        if hit.kind == "range":
            issues.append(_at_excerpt(hit, _range_message(hit), "warning"))
        elif not covering.covers(hit):
            issues.append(_at_excerpt(hit, _error_message(reader, hit), "error"))
    issues += _reference_warnings(view, reader)
    return issues


def affected_triples(view, reader, finding_id: str) -> set[tuple[str, str, str]]:
    """(challenge ID, canonical source key, tag_sha256) of every excerpt of `finding_id` that a confirmed
    challenge affects (the same-bytes intersection or the version-unproved rule), covered by a use or
    not. A finding put compares the candidate's set against the installed finding's (SPEC §5.1.4)."""
    return {(hit.info.rec.id or "", hit.info.key or "", hit.match.tag_sha256)
            for hit in _hits(view, reader, finding_id) if hit.kind != "range"}


@dataclass(frozen=True)
class Relation:
    """One finding reference that K13 relates to a challenge, for `kblam challenge uses` (SPEC §5.1.5)."""
    finding: str             # the finding's ID
    path: str                # the finding's repo-relative path
    line: int                # the file line K13 reports it at (the tag line for an excerpt; 0 if none)
    ordinal: int | None      # the excerpt's 1-based ordinal among the finding's verbatim tags; None for
                             # an evidence or prose reference
    relation: str            # "same" | "other" | "range" | "evidence" | "prose" (the _Hit kinds, plus the
                             # two reference warnings)
    level: str               # "error" for an uncovered "same"/"other" excerpt, else "warning"; a covered
                             # excerpt is "current" (a current use covers it)
    use: str | None          # the ID of the current use that covers it, when level is "current"
    command: str             # the command that addresses it: USE_REVIEW for an error, CHALLENGE_NEW
                             # (as in MESSAGE_UNPROVED) beside it for "other"; "" for a covered excerpt;
                             # for a warning, the edit to check (kblam edit <finding>)


EDIT = "kblam edit {finding}"


def challenge_relations(view, reader, challenge_id: str) -> list[Relation]:
    """Every finding reference K13 relates to one challenge, in (finding path, line, ordinal) order:
    the excerpt hits of _hits restricted to this challenge (same, other and range, each with the
    covering current use if any, via _CurrentUses), then the evidence and prose warnings K13 gives for
    this challenge. [] when the challenge is not confirmed (K13 relates nothing to it). Owned by U16
    (wave 5), which may add private helpers here but must not change k13()'s issues or messages."""
    if not any(info.rec.id == challenge_id
               for infos in _confirmed(view, reader).values() for info in infos):
        return []
    covering = _CurrentUses(view, reader)
    relations = []
    for hit in _hits(view, reader):
        if hit.info.rec.id != challenge_id:
            continue
        # A range hit is a warning whatever covers it: k13() warns for every one of them, so a use
        # never turns the candidate overlap into a level K13 does not report.
        use = None if hit.kind == "range" else covering.covering(hit)
        level = "current" if use is not None else ("warning" if hit.kind == "range" else "error")
        relations.append(Relation(finding=hit.finding.file_id or "", path=hit.finding.path,
                                  line=hit.finding.body_start_line + hit.match.start,
                                  ordinal=hit.match.ordinal, relation=hit.kind, level=level,
                                  use=use.id if use is not None else None,
                                  command="" if use is not None else _hit_command(reader, hit)))
    relations += [Relation(finding=ref.finding.file_id or "", path=ref.finding.path, line=ref.line,
                           ordinal=None, relation=ref.kind, level="warning", use=None,
                           command=EDIT.format(finding=ref.finding.file_id or ""))
                  for ref in _references(view, reader) if ref.info.rec.id == challenge_id]
    return sorted(relations, key=lambda relation: (relation.path, relation.line,
                                                   relation.ordinal or 0))


def _hit_command(reader, hit: _Hit) -> str:
    """What addresses an uncovered excerpt: `use review` for the same-bytes rule, and beside it the new
    challenge that a "version unproved" excerpt needs (as MESSAGE_UNPROVED names it); a range hit is a
    candidate overlap to read, so the finding is what an author edits."""
    challenge = hit.info.rec.id or ""
    review = USE_REVIEW.format(challenge=challenge, finding=hit.finding.file_id,
                               ordinal=hit.match.ordinal)
    if hit.kind == "same":
        return review
    if hit.kind == "other":
        lines = _range_text(_cited_lines(reader, hit.match))
        return f"{review}; or {CHALLENGE_NEW.format(path=hit.match.path, lines=lines)}"
    return EDIT.format(finding=hit.finding.file_id or "")


# --- what each excerpt is affected by -----------------------------------------------------------


def _confirmed(view, reader) -> dict[str, list[ChallengeInfo]]:
    """The confirmed challenges by their source's canonical key, in record order."""
    by_key: dict[str, list[ChallengeInfo]] = {}
    for rec in view.records:
        if rec.kind != "SC" or not isinstance(rec.data, dict):
            continue
        info = k12.challenge_info(view, reader, rec)
        if info.confirmed and info.key is not None:
            by_key.setdefault(info.key, []).append(info)
    return by_key


def _assertion(info: ChallengeInfo) -> tuple[str, tuple[int, int]] | None:
    """The assertion's (text, lines) when the record has both in a usable shape, else None: K12 reports
    the rest."""
    source = info.rec.data.get("source")
    assertion = source.get("assertion") if isinstance(source, dict) else None
    if not isinstance(assertion, dict):
        return None
    text, lines = assertion.get("text"), assertion.get("lines")
    if not isinstance(text, str) or not text:
        return None
    if not (isinstance(lines, list) and len(lines) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) for n in lines)):
        return None
    return text, (lines[0], lines[1])


def _hits(view, reader, finding_id: str | None = None) -> list[_Hit]:
    """Each verified excerpt (a K10 failure and a binary-exempt excerpt are not K13's) of each parsed
    finding, against each confirmed challenge on its source's canonical key."""
    by_key = _confirmed(view, reader)
    if not by_key:
        return []
    hits: list[_Hit] = []
    for finding in view.findings:
        if finding.meta is None or (finding_id is not None and finding.file_id != finding_id):
            continue
        for match in matching.finding_matches(view, reader, finding):
            if not match.verified or match.key is None:
                continue
            for info in by_key.get(match.key, []):
                kind = _relation(info, match)
                if kind is not None:
                    hits.append(_Hit(finding, match, info, kind))
    return hits


def _relation(info: ChallengeInfo, match: ExcerptMatch) -> str | None:
    """"same", "other", "range" or None: how this excerpt relates to this challenge."""
    assertion = _assertion(info)
    if assertion is None:
        return None
    text, lines = assertion
    sha256 = info.rec.data["source"].get("sha256")
    if match.source_sha256 == sha256:
        if info.span is not None and any(start < info.span[1] and info.span[0] < end
                                         for start, end in match.spans):
            return "same"
        if (not match.is_offset and len(match.range) == 2
                and match.range[0] <= lines[1] and lines[0] <= match.range[1]):
            return "range"
        return None
    if match.text is not None and (text in match.text or match.text in text):
        return "other"
    return None


class _CurrentUses:
    """Whether a current use covers an excerpt: a CU whose challenge, finding and citation.ordinal name
    it and that k12.use_current accepts. Each use is judged once. `covering` gives the use, `covers`
    the yes or no k13 asks for."""

    def __init__(self, view, reader) -> None:
        self.view, self.reader = view, reader
        self._uses = [rec for rec in view.records if rec.kind == "CU" and isinstance(rec.data, dict)]
        self._current: dict[str, bool] = {}

    def covering(self, hit: _Hit) -> records.Record | None:
        """The current use that covers the excerpt, or None: the use `covers` would accept, kept so
        `challenge uses` can name it."""
        for rec in self._uses:
            data = rec.data
            citation = data.get("citation")
            if (data.get("challenge") != hit.info.rec.id or data.get("finding") != hit.finding.file_id
                    or not isinstance(citation, dict) or citation.get("ordinal") != hit.match.ordinal):
                continue
            if rec.path not in self._current:
                self._current[rec.path] = k12.use_current(self.view, self.reader, rec)
            if self._current[rec.path]:
                return rec
        return None

    def covers(self, hit: _Hit) -> bool:
        return self.covering(hit) is not None


# --- messages -----------------------------------------------------------------------------------


def _at_excerpt(hit: _Hit, message: str, level: str) -> Issue:
    return Issue(hit.finding.path, hit.finding.body_start_line + hit.match.start, "K13", message, level,
                 hit.finding.file_id or "")


def _version(info: ChallengeInfo) -> str:
    """The pin's blob ID, or else the source's sha256, first 12 hex digits."""
    source = info.rec.data["source"]
    pin = records.file_ref(source).pin
    ident = pin.blob if pin is not None else source.get("sha256")
    return str(ident)[:12]


def _source_path(info: ChallengeInfo) -> str:
    return str(info.rec.data["source"].get("path"))


def _range_text(numbers) -> str:
    return "-".join(str(n) for n in numbers)


def _error_message(reader, hit: _Hit) -> str:
    info, match = hit.info, hit.match
    challenge = info.rec.id or ""
    review = USE_REVIEW.format(challenge=challenge, finding=hit.finding.file_id, ordinal=match.ordinal)
    if hit.kind == "same":
        return MESSAGE_SAME_BYTES.format(challenge=challenge, source=_source_path(info),
                                         version=_version(info), lines=_range_text(_assertion(info)[1]),
                                         review=review)
    new = CHALLENGE_NEW.format(path=match.path, lines=_range_text(_cited_lines(reader, match)))
    return MESSAGE_UNPROVED.format(challenge=challenge, source=_source_path(info),
                                   version=_version(info), new=new, review=review)


def _cited_lines(reader, match: ExcerptMatch) -> tuple[int, int]:
    """The lines an excerpt cites: a line tag's range, and for an offset tag the lines its span covers."""
    if not match.is_offset:
        return match.range[0], match.range[-1]
    text = matching._source_text(reader.working(match.path) or b"") or ""
    start, end = match.spans[0]
    return text.count("\n", 0, start) + 1, text.count("\n", 0, max(end - 1, start)) + 1


def _range_message(hit: _Hit) -> str:
    challenge = hit.info.rec.id or ""
    return MESSAGE_RANGE.format(path=hit.match.path, cited=_range_text(hit.match.range),
                                lines=_range_text(_assertion(hit.info)[1]), challenge=challenge,
                                uses=CHALLENGE_USES.format(challenge=challenge))


# --- evidence and prose references --------------------------------------------------------------


@dataclass(frozen=True)
class _Ref:
    """One reference outside a verbatim excerpt that names a confirmed challenge's source: a finding that
    lists it in `evidence`, or names its path in prose. `kind` is "evidence" or "prose"."""
    finding: Finding
    info: ChallengeInfo
    kind: str
    line: int


def _references(view, reader) -> list[_Ref]:
    """Every (finding, challenge) reference K13 reports, in the order _reference_warnings lists them."""
    by_key = _confirmed(view, reader)
    found: list[_Ref] = []
    if not by_key:
        return found
    for finding in view.findings:
        if finding.meta is None:
            continue
        evidence = finding.meta.get("evidence")
        items = ([(i, _key(view, item)) for i, item in enumerate(evidence) if isinstance(item, str)]
                 if isinstance(evidence, list) else [])
        segments = _prose_segments(view, reader, finding)
        for key, infos in by_key.items():
            for info in infos:
                path = _source_path(info)
                listed = next((i for i, item_key in items if item_key == key), None)
                if listed is not None:
                    found.append(_Ref(finding, info, "evidence",
                                      finding.item_line("evidence", listed) or 0))
                line = _prose_line(segments, {path, key})
                if line is not None:
                    found.append(_Ref(finding, info, "prose", line))
    return found


def _reference_warnings(view, reader) -> list[Issue]:
    """A finding that lists a challenged source in `evidence`, or names its path in prose outside verbatim
    excerpts: one warning each per (finding, challenge). A path-only reference is never classified as safe."""
    issues: list[Issue] = []
    for ref in _references(view, reader):
        challenge = ref.info.rec.id or ""
        template = MESSAGE_EVIDENCE if ref.kind == "evidence" else MESSAGE_PROSE
        message = template.format(path=_source_path(ref.info), challenge=challenge,
                                  uses=CHALLENGE_USES.format(challenge=challenge))
        issues.append(Issue(ref.finding.path, ref.line, "K13", message, "warning",
                            ref.finding.file_id or ""))
    return issues


def _prose_segments(view, reader, finding: Finding) -> list[tuple[int, str]]:
    """(first file line, text) of the title and the body with every verbatim tag and its block blanked, as
    rules._prose_segments does for K4/K5 but also for an excerpt K10 did not verify: a tag's path is not
    prose, whatever K10 says of it."""
    segments = []
    title = finding.meta.get("title")
    if isinstance(title, str):
        segments.append((finding.key_line("title") or 1, title))
    body = list(finding.body_lines)
    for match in matching.finding_matches(view, reader, finding):
        body[match.start:match.end] = [""] * (match.end - match.start)
    segments.append((finding.body_start_line, "\n".join(body)))
    return segments


def _key(view, raw: str) -> str | None:
    """The canonical key of an evidence item, None when paths refuses it."""
    try:
        return paths.canonical_key(view.cfg, raw)
    except paths.PathRefused:
        return None


def _prose_line(segments: list[tuple[int, str]], names: set[str]) -> int | None:
    """The file line of the first place the prose names one of `names`, or None."""
    lines = [first + text.count("\n", 0, text.find(name))
             for first, text in segments for name in names if name and name in text]
    return min(lines) if lines else None
