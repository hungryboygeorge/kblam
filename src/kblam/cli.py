"""Command-line entry point: `kblam <command>` (SPEC §7; M1-M3, M5, M6 and M6.5 commands, and the
§5.2.5 source challenge, claim task and reviewed use commands). Exit codes: EXIT_HELP."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from kblam import k13, k15, records, registry, review_stage, review_write, writes
from kblam.config import ConfigError, load_config
from kblam.finding import ID_RE
from kblam.hook import SKILL_POINTER
from kblam.hook import run as run_hook
from kblam.jev import CallInfo, CostBucket, JevClient, JevUnavailable, cost_summary, jev_settings, smoke_sides
from kblam.lock import LockError
from kblam.review import Recorded, ReviewError, audit, check_findings, check_pending, open_items, resolve
from kblam.review_write import WriteResult
from kblam.rules import Dependency, dependencies, errors, validate
from kblam.sources import SourceReader
from kblam.store import StoreError, ack, edit_finding, new_finding, put, regenerate_index
from kblam.treehash import read_recorded, tree_digest_v2, write_tree_hash_v2
from kblam.view import load_view

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2
EXIT_LOCKED = 3
EXIT_REJECTED = 4

EXIT_HELP = """\
exit status:
  0  success
  1  refused: validation errors, open review or unchecked items, or a request kblam will not carry
     out; Jev unavailable
  2  no usable kblam.toml, or bad command-line arguments
  3  timed out waiting for .kblam/lock (another kblam write is running); retry later
  4  put rejected by the Jev check or a quantity conflict (SPEC §6.4); findings/ is unchanged"""


def _pending_note(pending: list[str]) -> str:
    """The pending-task count a validate summary carries, when there is one (SPEC §5.2.4 K15)."""
    return f"; {len(pending)} pending task(s)" if pending else ""


def _forget_missing(cfg) -> None:
    """`validate --record --forget-missing`: drop the registered IDs whose records are gone, printing each
    (SPEC §5.2.6). The drop stands even when the validation that follows fails."""
    missing = registry.missing(cfg, k13.present_ids(load_view(cfg)))
    if not missing:
        return
    registry.write_ids(cfg, (registry.read_ids(cfg) or set()) - set(missing))
    for rec_id in missing:
        print(f"kblam validate --record: forgot {rec_id} (no record in {cfg.review_dir}/)")


def _validate(cfg, args) -> int:
    if args.record and args.forget_missing:
        _forget_missing(cfg)
    view = load_view(cfg)
    issues = validate(view)
    for issue in issues:  # warnings print too; they never fail (SPEC §5)
        print(issue.format(view))
    items = open_items(cfg, view)
    for item in items:
        print(item.describe())
    pending = k15.k15_pending(view, SourceReader(cfg, view))  # printed, never a failure (SPEC §5.2.4)
    for line in pending:
        print(line)
    failures = errors(issues)
    if failures or items:
        print(f"kblam validate: {len(failures)} error(s) in {cfg.findings_dir}/"
              + (f", {len(items)} open item(s) in .kblam/review.jsonl" if items else "")
              + ("; tree.hash not recorded" if args.record else ""))
        return EXIT_INVALID
    if args.record:
        ids = writes.registry_after(cfg, k13.present_ids(view), set())  # created after a clone (§5.2.6)
        if ids is not None:
            registry.write_ids(cfg, ids)
        write_tree_hash_v2(cfg, view)
        print(f"kblam validate: OK ({len(view.findings)} findings){_pending_note(pending)}; recorded "
              f".kblam/tree.hash for this tree")
    else:
        print(f"kblam validate: OK ({len(view.findings)} findings){_pending_note(pending)}")
    return EXIT_OK


def _cmd_validate(cfg, args) -> int:
    if not args.record:
        return _validate(cfg, args)
    _print_checks("validate --record", check_findings(cfg, None, command="validate --record",
                                                     client_factory=JevClient))
    with writes.locked(cfg, "validate --record", mutating=True):  # it writes tree.hash (SPEC §5.2.6)
        return _validate(cfg, args)


def _cmd_index(cfg, args) -> int:
    view = regenerate_index(cfg)
    recorded = read_recorded(cfg) == (2, cfg.review_dir, tree_digest_v2(view))
    print(f"kblam index: wrote {view.index_path} ({len(view.findings)} findings)"
          + (" and .kblam/tree.hash" if recorded else ""))
    return EXIT_OK


def _cmd_new(cfg, args) -> int:
    print(new_finding(cfg, args.topic, args.title))
    return EXIT_OK


def _cmd_edit(cfg, args) -> int:
    print(edit_finding(cfg, args.id))
    return EXIT_OK


def _check_notes(command: str, check) -> None:
    if not check.jev_enabled:
        print(f"kblam {command}: [jev.thresholds] enables no Jev verdict, so Jev was not asked "
              f"(quantities were compared)", file=sys.stderr)
    if check.mismatch:
        print(f"kblam {command}: WARNING: {check.mismatch}")


def _rebind_command(rec_id: str, view) -> str:
    """The rebind command the put suggests for a record it made stale (SPEC §5.2.4, §5.2.5). Running it
    keeps the status and passes that status's closing checks again, so a closed task must cite its primary
    evidence once more; every other record's rebind takes no evidence."""
    command = f"kblam review rebind {rec_id} --by NAME --reason TEXT --expect D"
    status = next((rec.status for rec in view.records if rec.id == rec_id), None)
    return command + (" --evidence PROVENANCE:PATH:LOCATOR" if status in k15.PRIMARY_EVIDENCE else "")


def _cmd_put(cfg, args) -> int:
    if records.FILENAME_RE.match(Path(args.file).name):  # an SC-/CT-/CU- file: a record put (§5.2.5)
        return _write_result(cfg, "put", review_write.put_record(cfg, Path(args.file)))
    result = put(cfg, Path(args.file), client_factory=JevClient)
    for issue in result.issues + result.warnings:
        print(issue.format(result.view))
    if result.issues:
        print(f"kblam put: rejected {result.finding_id} ({len(result.issues)} error(s)); "
              f"{cfg.findings_dir}/ is unchanged. Fix the staged file and put it again. {SKILL_POINTER}")
        return EXIT_INVALID
    _check_notes("put", result.check)
    if result.rejected:
        items = {(i.verdict, i.existing_id): i for i in result.rejected_items}
        for verdict in result.check.verdicts:
            item = items.get((verdict.verdict, verdict.existing_id))
            print(item.describe() if item else f"{verdict.mode} {verdict.describe()}")
        for line in result.check.unavailable:
            print(f"unchecked {line}")
        print(f"kblam put: rejected {result.finding_id} by the Jev check ({len(result.check.rejected)} reject "
              f"verdict(s)); {cfg.findings_dir}/ is unchanged. Fix every verdict above and put it again. {SKILL_POINTER}")
        return EXIT_REJECTED
    for verdict in result.check.suppressed:
        print(f"kblam put: resolved as distinct, not raised: {verdict.describe()}")
    print(f"kblam put: {result.finding_id} -> {result.target}")
    for old in result.removed:
        print(f"kblam put: removed {old} (same ID, old slug or topic)")
    for target, value in result.stamped:
        print(f"kblam put: stamped depends_on {target}: {value}")
    for dependent in result.suspect:
        print(f"kblam put: {dependent} is now suspect (it depends on {result.finding_id}, which this put "
              f"changed); re-read {result.finding_id}, then kblam ack {dependent} {result.finding_id}, or "
              f"edit {dependent}. kblam validate fails until then")
    for item in result.review + ([result.unchecked] if result.unchecked else []):
        print(f"kblam put: {item.describe()}. kblam validate fails until it is closed")
    for rec_id in result.stale:  # SPEC §5.2.4: the put lists what it makes stale
        print(f"kblam put: {rec_id} is now stale (this put changed {result.finding_id}, which it is bound "
              f"to); a reviewer rechecks it and runs {_rebind_command(rec_id, result.view)}. kblam "
              f"validate fails until then")
    for issue in (*result.kept, *result.remaining):
        print(issue.format(result.view))
    if result.stale or result.kept or result.remaining:
        print(f"kblam put: done, but kblam validate still fails "
              f"({len(result.kept) + len(result.remaining)} error(s) listed above that this put did not "
              f"refuse)")
    return EXIT_OK


# --- source challenges, claim tasks, reviewed uses and decisions (SPEC §5.2.5) -------------------


def _print_text(text: str) -> None:
    """A library message (show, uses, a pending line) verbatim, with exactly one final newline."""
    print(text, end="" if text.endswith("\n") else "\n")


def _write_result(cfg, command: str, result: WriteResult) -> int:
    """Print a record write's outcome: what refused it, what it left behind, or what it did (SPEC §5.2.4
    "Where each rule blocks", §5.2.5). Returns the exit status."""
    view = load_view(cfg)  # after the call: a refused write changed nothing, and `format` reads the map
    if not result.ok:
        for issue in result.issues:      # errors owned by this record refused the write
            print(issue.format(view))
        for issue in result.warnings:
            print(issue.format(view))
        if command == "put":
            print(f"kblam put: rejected {result.rec_id} ({len(result.issues)} error(s)); "
                  f"{cfg.review_dir}/ is unchanged. Fix the staged file and put it again. {SKILL_POINTER}")
        else:
            print(f"kblam {command}: refused {result.rec_id} ({len(result.issues)} error(s)); "
                  f"{cfg.review_dir}/ is unchanged. Fix what is listed above and run it again.")
        return EXIT_INVALID
    for issue in result.warnings:
        print(issue.format(view))
    short = result.digest[:12]
    if command == "put":
        print(f"kblam put: {result.rec_id} -> {result.path}")
    elif command == "review decide":
        print(f"kblam review decide: {result.rec_id} is now {result.status} (subject digest {short})")
    elif command == "review rebind":
        print(f"kblam review rebind: {result.rec_id} rebound, now {result.status} (subject digest {short})")
    else:  # challenge pin
        print(f"kblam challenge pin: {result.rec_id} pinned (subject digest {short})")
    if result.newly_affected:  # confirmations only (SPEC §5.2.4)
        print(f"kblam review decide: {result.rec_id} now affects {', '.join(result.newly_affected)}; run "
              f"kblam challenge uses {result.rec_id} for each excerpt and the command that fixes it")
    for issue in result.remaining:   # errors owned by other records or findings: validate still fails
        print(issue.format(view))
    if result.remaining:
        print(f"kblam {command}: done, but kblam validate still fails ({len(result.remaining)} error(s) "
              f"listed above, owned by other findings or records)")
    return EXIT_OK


def _lines(text: str) -> tuple[int, int]:
    """`--lines A-B`: two positive decimal integers with A <= B (SPEC §5.2.5); anything else is a usage
    error. Whether the range fits the source is review_stage's to refuse."""
    match = re.fullmatch(r"([0-9]+)-([0-9]+)", text)   # [0-9], so a Unicode digit such as '３' is not one
    if match is not None:
        first, last = int(match.group(1)), int(match.group(2))
        if first >= 1 and first <= last:
            return first, last
    raise argparse.ArgumentTypeError(f"takes A-B with 1 <= A <= B, not {text!r}")


def _cmd_challenge_new(cfg, args) -> int:
    print(review_stage.challenge_new(cfg, args.source_path, args.lines, args.by))
    return EXIT_OK


def _cmd_challenge_edit(cfg, args) -> int:
    print(review_stage.challenge_edit(cfg, args.id))
    return EXIT_OK


def _cmd_challenge_pin(cfg, args) -> int:
    result = review_write.challenge_pin(cfg, args.id, args.expect, args.snapshot)
    return _write_result(cfg, "challenge pin", result)


def _cmd_challenge_show(cfg, args) -> int:
    _print_text(review_stage.challenge_show(cfg, args.id))
    return EXIT_OK


def _cmd_challenge_uses(cfg, args) -> int:
    _print_text(review_stage.challenge_uses(cfg, args.id))
    return EXIT_OK


def _cmd_task_new(cfg, args) -> int:
    print(review_stage.task_new(cfg, args.finding, args.kind, args.by, args.proponent))
    return EXIT_OK


def _cmd_task_edit(cfg, args) -> int:
    print(review_stage.task_edit(cfg, args.id))
    return EXIT_OK


def _cmd_task_show(cfg, args) -> int:
    _print_text(review_stage.task_show(cfg, args.id))
    return EXIT_OK


def _cmd_use_review(cfg, args) -> int:
    print(review_stage.use_review(cfg, args.challenge, args.finding, args.ordinal, args.by, args.proponent))
    return EXIT_OK


def _cmd_review_decide(cfg, args) -> int:
    result = review_write.decide(cfg, args.id, args.status, args.by, args.reason, args.expect, args.evidence)
    return _write_result(cfg, "review decide", result)


def _cmd_review_rebind(cfg, args) -> int:
    result = review_write.rebind(cfg, args.id, args.by, args.reason, args.expect, args.evidence,
                                 reopen=args.reopen)
    return _write_result(cfg, "review rebind", result)


def _cmd_review_index(cfg, args) -> int:
    recorded = review_write.regenerate_review_index(cfg)
    print(f"kblam review index: wrote {load_view(cfg).review_index_path}"
          + (" and .kblam/tree.hash" if recorded else ""))
    return EXIT_OK


def _cmd_review_list(cfg, args) -> int:
    for item in review_stage.review_list(cfg, only_open=args.open):
        print(review_stage.format_listed(item))
    return EXIT_OK


def _add_by(parser) -> None:
    parser.add_argument("--by", required=True, metavar="NAME",
                        help="the person acting: a name starting with a letter or digit, holding only "
                             "letters, digits, '.', '_', '@' and '-' (SPEC §5.2.2)")


def _add_proponent(parser) -> None:
    parser.add_argument("--proponent", required=True, metavar="NAME",
                        help="the person the record is drafted for: the finding's author for a use, and "
                             "someone a closing decision must be independent of")


def _add_expect(parser) -> None:
    parser.add_argument("--expect", required=True, metavar="D",
                        help="the subject digest show or list printed, or a prefix of at least 12 "
                             "hex digits: the record the actor inspected")


def _add_evidence(parser) -> None:
    parser.add_argument("--evidence", action="append", default=[], metavar="PROVENANCE:PATH:LOCATOR",
                        help="a file cited by the decision, split at its first two colons; repeatable")


def _print_checks(command: str, recorded: list[Recorded]) -> bool:
    """Print each check's outcome; True if any left an open item."""
    left_open = False
    if recorded:
        _check_notes(command, recorded[0].result)
    for r in recorded:
        check = r.result
        if check.mismatch and r is not recorded[0]:
            _check_notes(command, check)
        scope = f", {len(check.different_scope)} different_scope" if check.different_scope else ""
        budget = f", {len(check.over_budget)} over budget" if check.over_budget else ""
        print(f"kblam {command}: {check.finding_id} ({check.fingerprint}): {len(check.candidates)} "
              f"candidate(s){scope}{budget}")
        for verdict in check.suppressed:
            print(f"  resolved as distinct, not raised: {verdict.describe()}")
        for item in r.opened + ([r.unchecked] if r.unchecked else []):
            print(f"  {item.describe()}")
            left_open = True
    return left_open


def _cmd_check(cfg, args) -> int:
    if args.pending and args.ids:
        print("kblam check: give finding IDs or --pending, not both", file=sys.stderr)
        return EXIT_USAGE
    if args.pending:
        recorded = check_pending(cfg, client_factory=JevClient)
        if not recorded:
            print("kblam check: no unchecked items")
    else:
        recorded = check_findings(cfg, args.ids or None, client_factory=JevClient)
        if not recorded:
            print("kblam check: every finding has been checked at its current fingerprint")
    return EXIT_INVALID if _print_checks("check", recorded) else EXIT_OK


def _cmd_audit(cfg, args) -> int:
    recorded = audit(cfg, client_factory=JevClient)
    if not recorded:
        print("kblam audit: every candidate pair and revision question has a cached answer")
    return EXIT_INVALID if _print_checks("audit", recorded) else EXIT_OK


def _cmd_resolve(cfg, args) -> int:
    for item in resolve(cfg, args.id, args.distinct):
        print(f"kblam resolve: closed {item.id} ({item.verdict} {item.new_id}"
              + (f" vs {item.existing_id}" if item.existing_id else "") + f"): {item.close_reason}")
    return EXIT_OK


def _cmd_ack(cfg, args) -> int:
    result = ack(cfg, args.dependent, args.target)
    if result.changed:
        print(f"kblam ack: {args.dependent} depends_on {args.target} set to {result.fingerprint} "
              f"({result.path})" + (" and .kblam/tree.hash rewritten" if result.recorded else ""))
    else:
        print(f"kblam ack: {args.dependent} depends_on {args.target} is already current ({result.fingerprint})")
    return EXIT_OK


def _dependency_line(d: Dependency, other: str, titles: dict[str, str]) -> str:
    if d.state == "current":
        detail = f"current    {d.current}"
    elif d.state == "suspect":
        detail = (f"suspect    recorded {d.recorded}, current {d.current}; re-read {d.target}, then "
                  f"kblam ack {d.dependent} {d.target}")
    elif d.state == "unstamped":
        detail = f"unstamped  re-read {d.target}, then kblam ack {d.dependent} {d.target}"
    elif d.state == "missing":
        detail = f"missing    {d.target} is not a finding in the KB"
    else:
        detail = "invalid    run kblam validate"
    title = titles.get(other)
    return f"  {other}  {detail}" + (f"  ({title})" if title else "")


def _cmd_deps(cfg, args) -> int:
    if not ID_RE.match(args.id):
        raise StoreError(f"{args.id!r} is not a finding ID like F-0137")
    view = load_view(cfg)
    if not any(f.file_id == args.id for f in view.findings):
        raise StoreError(f"{args.id} is not in {cfg.findings_dir}/")
    titles = {f.file_id: str(f.meta.get("title")) for f in view.findings if f.ok and f.meta.get("title")}
    links = dependencies(view)
    uses = [d for d in links if d.dependent == args.id]
    used_by = [d for d in links if d.target == args.id]
    print(f"{args.id} depends on:" + ("" if uses else " (none)"))
    for d in uses:
        print(_dependency_line(d, d.target, titles))
    print(f"{args.id} dependents:" + ("" if used_by else " (none)"))
    for d in used_by:
        print(_dependency_line(d, d.dependent, titles))
    return EXIT_OK


def _cost_line(label: str, b: CostBucket) -> str:
    return (f"{label}{b.requests} request(s), {b.failed} failed, {b.attempts} HTTP attempt(s), "
            f"{b.cache_hits} cache hit(s), {b.input_tokens} input + {b.output_tokens} output tokens, "
            f"${b.cost:.6f}")


def _cmd_cost(cfg, args) -> int:
    summary = cost_summary(cfg)
    if not summary.exists:
        print("kblam cost: no Jev requests logged (.kblam/calls.jsonl does not exist)")
        return EXIT_OK
    print(_cost_line("kblam cost: ", summary.total))
    if summary.total.cost_missing:
        print(f"  {summary.total.cost_missing} answered request(s) had no usage.cost; the total excludes them")
    if summary.total.mismatches:
        print(f"  {summary.total.mismatches} request(s) were served by a model other than the expected one")
    if summary.bad_lines:
        print(f"  {summary.bad_lines} line(s) of .kblam/calls.jsonl could not be parsed and were skipped")
    print("by day (UTC):")
    for day, bucket in sorted(summary.by_day.items()):
        print(_cost_line(f"  {day}  ", bucket))
    print("by kind:")
    for kind, bucket in sorted(summary.by_kind.items()):
        print(_cost_line(f"  {kind}  ", bucket))
    return EXIT_OK


def _cmd_prompt_id(cfg, args) -> int:
    """The id of this project's Jev prompt: the wording in its [jev.prompt] tables (§6.2, §9)."""
    settings = jev_settings(cfg)
    print(f"kblam prompt-id: {settings.prompt_id}")
    return EXIT_OK


def _call_line(call: CallInfo) -> str:
    if call.cached:
        text = f"  from the pair cache, no request (answered by {call.served_model})"
    else:
        cost = "not reported" if call.cost is None else f"${call.cost:.6f}"
        text = (f"  served by {call.served_model}, {call.input_tokens} input tokens, cost {cost}, "
                f"{call.latency_s:.2f} s, {call.attempts} attempt(s), generation {call.generation_id}")
    if call.model_mismatch:
        text += (f"\n  WARNING: expected served model {call.expected_served_model}; the pair cache will not "
                 f"reuse this answer")
    return text


def _cmd_jev_smoke(cfg, args) -> int:
    existing, new, revision = smoke_sides()
    with JevClient(cfg) as client:
        relation = client.ask_relation(existing, new)
        noul = client.ask_revision(revision)
    relation_ok = relation.winner == "same_fact"
    revision_ok = noul.noul >= 0.5
    probabilities = ", ".join(f"{k} {v:.3f}" for k, v in relation.probabilities.items())
    print(f"relation {existing.finding_id} -> {new.finding_id}: {relation.winner} "
          f"(p={relation.probabilities.get(relation.winner, 0):.3f}, confidence {relation.confidence:.3f}); "
          f"expected same_fact: {'yes' if relation_ok else 'NO'}")
    print(f"  probabilities: {probabilities}")
    print(_call_line(relation.call))
    print(f"revision {revision.finding_id}: noul {noul.noul:.3f}; expected >= 0.5: {'yes' if revision_ok else 'NO'}")
    print(_call_line(noul.call))
    calls = [c for c in (relation.call, noul.call) if not c.cached]
    total = sum(c.cost or 0.0 for c in calls)
    print(f"kblam jev-smoke: {len(calls)} request(s), cost ${total:.6f}; "
          f"{2 - len(calls)} answer(s) from .kblam/pairs.sqlite")
    return EXIT_OK if relation_ok and revision_ok else EXIT_INVALID


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kblam", description="Knowledge base of current facts.",
                                     epilog=EXIT_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, help="repository root containing kblam.toml "
                                                  "(default: search upward from the current directory)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("validate", help="run the deterministic rules; non-zero exit on any error")
    p.add_argument("--record", action="store_true",
                   help="on a clean result, write .kblam/tree.hash for the current tree (accepts an "
                        "out-of-band change such as git pull) and create .kblam/review-ids from the records "
                        "present; on failure neither is written")
    p.add_argument("--forget-missing", action="store_true",
                   help="with --record, also drop from .kblam/review-ids the record IDs whose records are "
                        "gone from the review root, printing each; reserved for the coordinator or the user")
    p.set_defaults(func=_cmd_validate)
    p = sub.add_parser("index", help="regenerate findings/INDEX.md (and .kblam/tree.hash, unless findings/ "
                                     "was changed outside kblam)")
    p.set_defaults(func=_cmd_index)
    p = sub.add_parser("new", help="stage a skeleton for a new finding and print its path")
    p.add_argument("topic")
    p.add_argument("title")
    p.set_defaults(func=_cmd_new)
    p = sub.add_parser("edit", help="stage a copy of an existing finding for rewriting and print its path")
    p.add_argument("id")
    p.set_defaults(func=_cmd_edit)
    p = sub.add_parser("put", help="validate and move a staged finding into findings/, or a staged SC-/CT-/"
                                   "CU- record into the review root")
    p.add_argument("file")
    p.set_defaults(func=_cmd_put)
    p = sub.add_parser("ack", help="after re-reading the target, record its current fingerprint in the "
                                   "dependent's depends_on")
    p.add_argument("dependent")
    p.add_argument("target")
    p.set_defaults(func=_cmd_ack)
    p = sub.add_parser("deps", help="list a finding's dependencies and dependents with their fingerprint state")
    p.add_argument("id")
    p.set_defaults(func=_cmd_deps)
    p = sub.add_parser("check", help="Jev check of the given findings, or of every finding not yet checked at its "
                                     "current fingerprint; findings stay in place, verdicts become review items")
    p.add_argument("ids", nargs="*", metavar="F-NNNN")
    p.add_argument("--pending", action="store_true", help="retry the findings with open unchecked items")
    p.set_defaults(func=_cmd_check)
    p = sub.add_parser("audit", help="ask every candidate pair and revision question with no cached answer "
                                     "for the current fingerprints, model and prompt")
    p.set_defaults(func=_cmd_audit)
    p = sub.add_parser("resolve", help="close a review or rejected item whose two findings state distinct facts")
    p.add_argument("id", metavar="R-XXXXXXXX")
    p.add_argument("--distinct", required=True, metavar="REASON",
                   help="why they are distinct; stored so the pair at these fingerprints is not raised again")
    p.set_defaults(func=_cmd_resolve)
    p = sub.add_parser("cost", help="summarise .kblam/calls.jsonl: Jev requests, tokens and cost, per day and kind")
    p.set_defaults(func=_cmd_cost)
    p = sub.add_parser("prompt-id", help="print the id of this project's Jev prompt (its [jev.prompt] tables): "
                                         "the value [jev.thresholds] records and calibration is tied to")
    p.set_defaults(func=_cmd_prompt_id)
    p = sub.add_parser("jev-smoke", help="live check: ask Jev one synthetic relation pair and one revision "
                                         "question (two requests, or none when both are cached)")
    p.set_defaults(func=_cmd_jev_smoke)
    p = sub.add_parser("hook", help="Claude Code hook entry point: reads the hook's JSON on stdin (SPEC §8)")
    p.add_argument("event", metavar="EVENT", help="PreToolUse, Stop or SubagentStop")
    p = sub.add_parser("init", help="set up the git repository containing the current directory for kblam: "
                                    "kblam.toml, INDEX.md, .gitattributes, .gitignore, the Claude Code rule, "
                                    "skill and hooks, CLAUDE.md and the git pre-commit hook")
    p.add_argument("--update", action="store_true",
                   help="rewrite the rule, the skill, the hook entries and a kblam pre-commit hook to the "
                        "installed version (kblam.toml is never overwritten)")

    p = sub.add_parser("challenge", help="source challenges (§5.2.5): new or edit stages one, put installs "
                                         "it, pin fixes its source version, decide closes it, and show and "
                                         "uses read it")
    ch = p.add_subparsers(dest="challenge_command", required=True, metavar="COMMAND")
    s = ch.add_parser("new", help="stage a challenge on a source's lines and print the staged path")
    s.add_argument("source_path", metavar="SOURCE-PATH",
                   help="the source file, relative to the repository root")
    s.add_argument("--lines", required=True, type=_lines, metavar="A-B",
                   help="the 1-based lines of the source whose assertion is challenged (A <= B)")
    _add_by(s)
    s.set_defaults(func=_cmd_challenge_new, command_name="challenge new")
    s = ch.add_parser("edit", help="stage a copy of an open challenge for rewriting and print its path")
    s.add_argument("id", metavar="SC-NNNN")
    s.set_defaults(func=_cmd_challenge_edit, command_name="challenge edit")
    s = ch.add_parser("pin", help="pin an open challenge's source to its owner's HEAD commit, or to a "
                                  "snapshot whose bytes hash to it")
    s.add_argument("id", metavar="SC-NNNN")
    _add_expect(s)
    s.add_argument("--snapshot", metavar="PATH",
                   help="a project-owned copy of the source's bytes, outside every source repository")
    s.set_defaults(func=_cmd_challenge_pin, command_name="challenge pin")
    s = ch.add_parser("show", help="print a challenge: subject digest, source version and state, the "
                                   "assertion, the basis, the usable remainder, the limits, its findings "
                                   "and its decisions")
    s.add_argument("id", metavar="SC-NNNN")
    s.set_defaults(func=_cmd_challenge_show, command_name="challenge show")
    s = ch.add_parser("uses", help="print every finding excerpt K14 relates to a confirmed challenge, and "
                                   "the command that fixes each")
    s.add_argument("id", metavar="SC-NNNN")
    s.set_defaults(func=_cmd_challenge_uses, command_name="challenge uses")

    p = sub.add_parser("task", help="claim tasks (§5.2.5): new or edit stages one, put installs it, decide "
                                    "closes it and show reads it")
    ta = p.add_subparsers(dest="task_command", required=True, metavar="COMMAND")
    s = ta.add_parser("new", help="stage a task bound to a finding's current revision and print its path")
    s.add_argument("finding", metavar="F-NNNN")
    s.add_argument("--kind", required=True, choices=records.TASK_KINDS,
                   help="the kind of work the task asks for")
    _add_by(s)
    _add_proponent(s)
    s.set_defaults(func=_cmd_task_new, command_name="task new")
    s = ta.add_parser("edit", help="stage a copy of an open task for rewriting and print its path")
    s.add_argument("id", metavar="CT-NNNN")
    s.set_defaults(func=_cmd_task_edit, command_name="task edit")
    s = ta.add_parser("show", help="print a task: subject digest, kind, finding, proponent, its binding, "
                                   "the plan and the decisions")
    s.add_argument("id", metavar="CT-NNNN")
    s.set_defaults(func=_cmd_task_show, command_name="task show")

    p = sub.add_parser("use", help="reviewed uses (§5.2.5): review stages one for a confirmed challenge's "
                                   "affected excerpt")
    us = p.add_subparsers(dest="use_command", required=True, metavar="COMMAND")
    s = us.add_parser("review", help="stage a use of an affected excerpt of an installed finding and print "
                                     "the staged path")
    s.add_argument("challenge", metavar="SC-NNNN")
    s.add_argument("finding", metavar="F-NNNN")
    s.add_argument("ordinal", type=int, metavar="ORDINAL",
                   help="the finding's verbatim excerpt, 1-based among its tags")
    _add_by(s)
    _add_proponent(s)
    s.set_defaults(func=_cmd_use_review, command_name="use review")

    p = sub.add_parser("review", help="decisions on records, the record list and the review index (§5.2.5)")
    rv = p.add_subparsers(dest="review_command", required=True, metavar="COMMAND")
    s = rv.add_parser("decide", help="append a decision to a record after checking the transition and the "
                                     "kind's closing requirements")
    s.add_argument("id", metavar="ID")
    s.add_argument("--status", required=True, metavar="S", help="the status the decision sets (see show)")
    _add_by(s)
    s.add_argument("--reason", required=True, metavar="TEXT", help="why: a decision is audit data")
    _add_expect(s)
    _add_evidence(s)
    s.set_defaults(func=_cmd_review_decide, command_name="review decide")
    s = rv.add_parser("rebind", help="recompute a task's or use's bindings that no longer hold from the "
                                     "installed finding and append a decision")
    s.add_argument("id", metavar="ID")
    _add_by(s)
    s.add_argument("--reason", required=True, metavar="TEXT", help="why the binding still holds")
    _add_expect(s)
    _add_evidence(s)
    s.add_argument("--reopen", action="store_true", help="set the record open again, instead of keeping "
                                                         "its status")
    s.set_defaults(func=_cmd_review_rebind, command_name="review rebind")
    s = rv.add_parser("index", help="regenerate <review root>/INDEX.md from the records present")
    s.set_defaults(func=_cmd_review_index, command_name="review index")
    s = rv.add_parser("list", help="one line per record: ID, kind, status, subject digest, subject and "
                                   "whether its binding is current")
    s.add_argument("--open", action="store_true", help="only the records whose status is open")
    s.set_defaults(func=_cmd_review_list, command_name="review list")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if args.command == "hook":
        return run_hook(args.event, args.root)
    if args.command == "init":
        if args.root is not None:
            print("kblam init: --root is not accepted; run kblam init inside the git repository to set up",
                  file=sys.stderr)
            return EXIT_USAGE
        from kblam.init import run as run_init

        return run_init(args.update)
    if args.command == "validate" and args.forget_missing and not args.record:
        # Checked here, before load_config: the flag alone is a usage error, and nothing is read
        print("kblam validate: --forget-missing needs --record", file=sys.stderr)
        return EXIT_USAGE
    try:
        cfg = load_config(root=args.root)
        return args.func(cfg, args)
    except (ConfigError, StoreError, LockError, JevUnavailable, ReviewError) as exc:
        pointer = f" {SKILL_POINTER}" if args.command == "put" and not isinstance(exc, ConfigError) else ""
        # A §5.2.5 command names all of its words ("kblam challenge new: ..."); the older ones keep
        # their single word, as they always have.
        command = getattr(args, "command_name", None) or args.command
        print(f"kblam {command}: {exc}{pointer}", file=sys.stderr)
        if isinstance(exc, LockError):
            return EXIT_LOCKED
        return EXIT_USAGE if isinstance(exc, ConfigError) else EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
