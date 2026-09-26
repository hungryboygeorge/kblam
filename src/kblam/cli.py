"""Command-line entry point: `kblam <command>` (SPEC §7; M1-M3, M5, M6 and M6.5 commands). Exit codes: EXIT_HELP."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kblam import approval
from kblam.approval import ApprovalError
from kblam.check import parse_policy
from kblam.config import CONFIG_NAME, ConfigError, load_config
from kblam.finding import ID_RE
from kblam.hook import SKILL_POINTER
from kblam.hook import run as run_hook
from kblam.jev import CallInfo, CostBucket, JevClient, JevUnavailable, cost_summary, jev_settings, smoke_sides
from kblam.lock import LockError, kb_lock
from kblam.review import Recorded, ReviewError, audit, check_findings, check_pending, open_items, resolve
from kblam.rules import Dependency, dependencies, validate
from kblam.store import StoreError, ack, edit_finding, new_finding, put, regenerate_index
from kblam.treehash import read_tree_hash, tree_digest, write_tree_hash
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


def _validate(cfg, args) -> int:
    view = load_view(cfg)
    issues = validate(view)
    for issue in issues:
        print(issue.format(view))
    items = open_items(cfg, view)
    for item in items:
        print(item.describe())
    config = approval.commit_problems(cfg) if args.commit else []
    for problem in config:
        print(f"kblam validate: {problem}")
    if issues or items or config:
        print(f"kblam validate: {len(issues)} error(s) in {cfg.findings_dir}/"
              + (f", {len(items)} open item(s) in .kblam/review.jsonl" if items else "")
              + (f", and {CONFIG_NAME} needs approval before this commit" if config else "")
              + ("; tree.hash not recorded" if args.record else ""))
        return EXIT_INVALID
    if args.record:
        write_tree_hash(cfg, tree_digest(view))
        print(f"kblam validate: OK ({len(view.findings)} findings); recorded .kblam/tree.hash for this tree")
    else:
        print(f"kblam validate: OK ({len(view.findings)} findings)")
    return EXIT_OK


def _cmd_validate(cfg, args) -> int:
    if not args.record:
        return _validate(cfg, args)
    _print_checks("validate --record", check_findings(cfg, None, command="validate --record",
                                                     client_factory=JevClient))
    with kb_lock(cfg, "validate --record"):
        return _validate(cfg, args)


def _cmd_approve_config(cfg, args) -> int:
    """A person approves this kblam.toml for commits on this machine (SPEC §8 item 4). It must load (the
    config was loaded to get here; the [jev] settings and thresholds are checked too) and be confirmed on
    an interactive terminal, which an agent's shell is not."""
    parse_policy(jev_settings(cfg).thresholds)
    current = (cfg.repo_root / CONFIG_NAME).read_bytes()
    head = approval.committed_config(cfg, "HEAD")
    if head is not None and approval.config_digest(head) == approval.config_digest(current):
        print(f"kblam approve-config: {CONFIG_NAME} is as the last commit has it; a commit that leaves it "
              f"unchanged needs no approval")
        return EXIT_OK
    if approval.is_approved(cfg, current):
        print(f"kblam approve-config: this version of {CONFIG_NAME} is already approved on this machine")
        return EXIT_OK
    if not sys.stdin.isatty():
        print(f"kblam approve-config: a person approves {CONFIG_NAME} at an interactive terminal, and this is "
              f"not one, so nothing was approved. An agent asks the user to run kblam approve-config.",
              file=sys.stderr)
        return EXIT_INVALID
    if head is None:
        from kblam.init import ASSETS  # the template kblam init writes: a new file shows only its edits
        base, label = (ASSETS / CONFIG_NAME).read_bytes(), "the template kblam init writes"
    else:
        base, label = head, "last commit"
    print(approval.config_diff(base, label, current) or f"(no difference from {label})")
    answer = input(f"Approve this {CONFIG_NAME} for commits on this machine? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        print(f"kblam approve-config: not approved; a commit that changes {CONFIG_NAME} stays refused")
        return EXIT_INVALID
    approval.record_approval(cfg, current)
    print(f"kblam approve-config: approved; a commit of this version of {CONFIG_NAME} is now accepted")
    return EXIT_OK


def _cmd_index(cfg, args) -> int:
    view = regenerate_index(cfg)
    recorded = read_tree_hash(cfg) == tree_digest(view)
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


def _cmd_put(cfg, args) -> int:
    result = put(cfg, Path(args.file), client_factory=JevClient)
    # Errors that were in findings/ before this put do not block it (SPEC §7 put, Validation); they are printed as
    # warnings after the put's own report, and before the final line of a refusal.
    warnings = [f"kblam put: warning: {issue.format(result.view)}" for issue in result.warnings]
    if result.issues:
        for issue in result.issues:
            print(issue.format(result.view))
        for line in warnings:
            print(line)
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
        for line in warnings:
            print(line)
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
    for line in warnings:
        print(line)
    if warnings:
        print(f"kblam put: the {len(warnings)} warning(s) above were already in {cfg.findings_dir}/ before this "
              f"put, so they did not block it; kblam validate fails until each is fixed")
    return EXIT_OK


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
    print(f"kblam resolve: recorded the resolution in {cfg.resolutions_path.name} (SPEC §6.4); commit it, so "
          f"every clone of the repository has it")
    return EXIT_OK


def _refused(command: str, exc: Exception) -> int:
    """A refused `rm` or `renumber` leaves findings/ unchanged; like a refused put, its message ends with the
    skill pointer (SPEC §8 item 6)."""
    print(f"kblam {command}: {exc}. {SKILL_POINTER}", file=sys.stderr)
    return EXIT_LOCKED if isinstance(exc, LockError) else EXIT_INVALID


def _cmd_rm(cfg, args) -> int:
    from kblam.store import remove_finding

    try:
        result = remove_finding(cfg, args.id, args.merged_into)
    except (StoreError, LockError, ReviewError) as exc:
        return _refused("rm", exc)
    print(f"kblam rm: removed {result.finding_id} ({result.path}), merged into {result.target_id}")
    if result.folder:
        print(f"kblam rm: removed {result.folder}, which the removal left empty")
    print(f"kblam rm: regenerated {result.index_path}" + (" and .kblam/tree.hash" if result.recorded else ""))
    for item in result.closed:
        pair = item.new_id + (f" vs {item.existing_id}" if item.existing_id else "")
        print(f"kblam rm: closed {item.kind} item {item.id} ({item.verdict or 'unchecked'} {pair}): "
              f"{item.close_reason}")
    for staged in result.staged:
        print(f"kblam rm: {staged} is a staged copy of {result.finding_id}; putting it would add "
              f"{result.finding_id} again, so delete it unless that is what you want")
    title = f" ({result.title})" if result.title else ""
    print(f"kblam rm: commit this with the reason for the removal in the message, e.g.: Remove "
          f"{result.finding_id}{title}, merged into {result.target_id}: <what made it redundant>")
    return EXIT_OK


def _cmd_renumber(cfg, args) -> int:
    from kblam.store import renumber

    try:
        result = renumber(cfg, Path(args.path))
    except (StoreError, LockError) as exc:
        return _refused("renumber", exc)
    print(f"kblam renumber: {result.old_id} -> {result.new_id}: {result.old_path} is now {result.new_path} "
          f"(fingerprint {result.fingerprint}); {result.old_id} stays with {', '.join(result.kept)}")
    for dependent, path in result.rekeyed:
        print(f"kblam renumber: {dependent} depends_on {result.old_id} is now {result.new_id}: "
              f"{result.fingerprint} ({path}), since its fingerprint showed it meant {result.old_path}")
    print(f"kblam renumber: regenerated {result.index_path}" + (" and .kblam/tree.hash" if result.recorded else ""))
    if result.mentions:
        print(f"kblam renumber: {len(result.mentions)} other mention(s) of {result.old_id} may mean either "
              f"finding; a person checks each and points it at {result.new_id} where it meant the renumbered one:")
        for mention in result.mentions:
            print(f"  {mention}")
    else:
        print(f"kblam renumber: no other mention of {result.old_id} in {cfg.findings_dir}/")
    return EXIT_OK


def _cmd_items(cfg, args) -> int:
    from kblam import items

    view = load_view(cfg)
    lines = [] if args.reworded or args.stats else items.listing_lines(items.open_items(cfg, view))
    if args.reworded:
        lines += items.reworded_lines(items.reworded(cfg, view))
    if args.stats:
        lines += items.stats_lines(items.stats(cfg, view))
    for line in lines:
        print(line)
    return EXIT_OK


def _cmd_ack(cfg, args) -> int:
    result = ack(cfg, args.dependent, args.target)
    if result.changed:
        # SPEC §7 ack: the target as the dependent recorded it, beside its current claim, for the re-reading
        if result.claim_then is not None:
            print(f"{args.target} as recorded (commit {result.then_commit}): {result.claim_then}")
        else:
            print(f"kblam ack: {result.history_note}")
        print(f"{args.target} now: {result.claim_now}")
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


def _cmd_recheck(cfg, args) -> int:
    """Run the findings' check: commands that a person approved on this machine (SPEC §7, §8.3)."""
    from kblam.recheck import RecheckError, recheck

    try:
        return recheck(cfg, args.ids)
    except RecheckError as exc:
        print(f"kblam recheck: {exc}", file=sys.stderr)
        return EXIT_INVALID


def _cmd_prompt_id(cfg, args) -> int:
    """The ids of this project's Jev prompt, the wording in its [jev.prompt] tables (§6.2, §9): the
    combined id, then each question's own, which [jev.thresholds] records as relation_prompt_id and
    revision_prompt_id."""
    settings = jev_settings(cfg)
    print(f"kblam prompt-id: {settings.prompt_id}")
    print(f"relation: {settings.relation_prompt_id}")
    print(f"revision: {settings.revision_prompt_id}")
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
                        "out-of-band change such as git pull); on failure nothing is written")
    p.add_argument("--commit", action="store_true",
                   help="also refuse a commit that changes kblam.toml without a person's approval "
                        "(kblam approve-config); what the git pre-commit hook runs")
    p.set_defaults(func=_cmd_validate)
    p = sub.add_parser("approve-config", help="show how kblam.toml changed and, at an interactive terminal, "
                                              "approve it for commits on this machine")
    p.set_defaults(func=_cmd_approve_config)
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
    p = sub.add_parser("put", help="validate and move a staged finding into findings/")
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
    p = sub.add_parser("rm", help="remove a finding after a merge moved everything it stated into another one "
                                  "(an adjudicator's command); the reason goes in the commit message")
    p.add_argument("id", metavar="F-NNNN")
    p.add_argument("--merged-into", required=True, metavar="F-NNNN", dest="merged_into",
                   help="the finding that now states what the removed one stated")
    p.set_defaults(func=_cmd_rm)
    p = sub.add_parser("renumber", help="give a new ID to one of two findings that share an ID (K1, after the work "
                                        "of two clones is merged), re-keying the depends_on entries that mean it")
    p.add_argument("path", help="the finding file under the KB root to renumber")
    p.set_defaults(func=_cmd_renumber)
    p = sub.add_parser("items", help="list the open review, rejected and unchecked items in .kblam/review.jsonl")
    p.add_argument("--reworded", action="store_true",
                   help="instead, list the rejected items whose finding went in later at another fingerprint while "
                        "the other side stayed as it was: a correction, or rewording to pass the check")
    p.add_argument("--stats", action="store_true",
                   help="instead, count each verdict's closed items as closed distinct or otherwise, and say "
                        "whether its recalibration is due (SPEC §10.7)")
    p.set_defaults(func=_cmd_items)
    p = sub.add_parser("cost", help="summarise .kblam/calls.jsonl: Jev requests, tokens and cost, per day and kind")
    p.set_defaults(func=_cmd_cost)
    p = sub.add_parser("recheck", help="run the check: commands of the given findings, or of every finding that has "
                                       "one; a command runs only once a person approved it at a terminal")
    p.add_argument("ids", nargs="*", metavar="F-NNNN")
    p.set_defaults(func=_cmd_recheck)
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
    try:
        cfg = load_config(root=args.root)
        return args.func(cfg, args)
    except (ConfigError, StoreError, LockError, JevUnavailable, ReviewError, ApprovalError) as exc:
        pointer = f" {SKILL_POINTER}" if args.command == "put" and not isinstance(exc, ConfigError) else ""
        print(f"kblam {args.command}: {exc}{pointer}", file=sys.stderr)
        if isinstance(exc, LockError):
            return EXIT_LOCKED
        return EXIT_USAGE if isinstance(exc, ConfigError) else EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
