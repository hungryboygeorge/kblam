"""Command-line entry point: `kblam <command>` (SPEC §7; M1-M3, M5, M6 and M6.5 commands, and the
§5.2.5 source challenge, claim task and reviewed use commands). Exit codes: EXIT_HELP."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from kblam import approval, k13, k15, recheck, records, registry, review_stage, review_write, writes
from kblam.approval import ApprovalError
from kblam.check import parse_policy
from kblam.config import CONFIG_NAME, ConfigError, load_config
from kblam.finding import ID_RE
from kblam.gitdir import tracked_state, tracked_state_problem
from kblam.hook import SKILL_POINTER
from kblam.hook import run as run_hook
from kblam.jev import CallInfo, CostBucket, JevClient, JevUnavailable, cost_summary, jev_settings, smoke_sides
from kblam.lock import LockError
from kblam.recheck import RecheckError, shown
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

# The commands that read review items, checked marks or resolutions, and would read state recorded before
# fingerprint v2 as changes: they refuse until kblam upgrade has migrated it (SPEC §7).
UPGRADE_FIRST = ("validate", "put", "check", "audit", "resolve", "items", "rm")

EXIT_HELP = """\
exit status:
  0  success
  1  refused: validation errors, open review or unchecked items, a recheck that failed, could not run
     or was not approved, or a request kblam will not carry out; Jev unavailable
  2  no usable kblam.toml, or bad command-line arguments
  3  timed out waiting for .kblam/lock (another kblam write is running); retry later
  4  put rejected by the Jev check or a quantity conflict (SPEC §6.4); findings/ is unchanged"""


def _pending_note(pending: list[str]) -> str:
    """The pending-task count a validate summary carries, when there is one (SPEC §5.2.4 K15)."""
    return f"; {len(pending)} pending task(s)" if pending else ""


def _error_roots(cfg, failures) -> str:
    """Where validate's errors are: findings/, the review root, or both. An error outside findings/ concerns
    the review records (a record, the review index, the registry or the review root's setting)."""
    prefix = f"{cfg.findings_dir}/"
    in_findings = any(issue.path.startswith(prefix) for issue in failures)
    in_review = any(not issue.path.startswith(prefix) for issue in failures)
    if in_review and in_findings:
        return f"{cfg.findings_dir}/ and {cfg.review_dir}/"
    return f"{cfg.review_dir}/" if in_review else prefix


def _forget_missing(cfg) -> None:
    """`validate --record --forget-missing`: drop the registered IDs whose records are gone, printing each
    (SPEC §5.2.6). The drop stands even when the validation that follows fails."""
    missing = registry.missing(cfg, k13.present_ids(load_view(cfg)))
    if not missing:
        return
    registry.write_ids(cfg, (registry.read_ids(cfg) or set()) - set(missing))
    for rec_id in missing:
        print(f"kblam validate --record: forgot {rec_id} (no record in {cfg.review_dir}/)")


def _validate(cfg, args, *, baseline: bool = False) -> int:
    """`kblam validate`, with --record and --commit. `baseline`: --record on a tree kblam has no tree.hash
    for (a new clone, or .kblam/ deleted), whose findings are accepted from the repository (SPEC §8 item 3)."""
    from kblam.commit_checks import check_commit
    from kblam.config import RESOLUTIONS_NAME
    from kblam.treehash import accept_from_repository

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
    config = approval.commit_problems(cfg) if args.commit else []
    commit = check_commit(cfg, view) if args.commit else None
    blocking = items if commit is None or commit.changes_kb else []  # SPEC §8 item 4: only commits to the KB
    if items and not blocking:
        print(f"kblam validate: note: this commit changes nothing under {cfg.findings_dir}/ or "
              f"{RESOLUTIONS_NAME}, so the {len(items)} open item(s) above do not block it")
    for problem in config:
        print(f"kblam validate: {problem}")
    refusals = commit.problems if commit else []
    for problem in refusals:
        print(f"kblam validate: {problem}")
    for warning in commit.warnings if commit else []:
        print(f"kblam validate: warning: {warning}")
    if failures or blocking or config or refusals:
        print(f"kblam validate: {len(failures)} error(s) in {_error_roots(cfg, failures)}"
              + (f", {len(blocking)} open item(s) in .kblam/review.jsonl" if blocking else "")
              + (f", {len(refusals)} problem(s) with the commit being made" if refusals else "")
              + (f", and {CONFIG_NAME} needs approval before this commit" if config else "")
              + ("; tree.hash not recorded" if args.record else ""))
        return EXIT_INVALID
    if args.record:
        # The marks go first, so a failure while writing them leaves no tree.hash and the next run starts over.
        accepted = accept_from_repository(cfg, view) if baseline else 0
        ids = writes.registry_after(cfg, k13.present_ids(view), set())  # created after a clone (§5.2.6)
        if ids is not None:
            registry.write_ids(cfg, ids)
        write_tree_hash_v2(cfg, view)
        print(f"kblam validate: OK ({len(view.findings)} findings){_pending_note(pending)}; recorded "
              f".kblam/tree.hash for this tree")
        if baseline:
            print(f"kblam validate: there was no .kblam/tree.hash (a new clone, or .kblam/ was deleted), so "
                  f"Jev was not asked: {accepted} finding(s) accepted from the repository as checked at their "
                  f"current fingerprints. kblam audit checks them with Jev")
    else:
        print(f"kblam validate: OK ({len(view.findings)} findings){_pending_note(pending)}")
    return EXIT_OK


def _cmd_validate(cfg, args) -> int:
    if not args.record:
        return _validate(cfg, args)
    if read_recorded(cfg) is None:
        # A new clone, or .kblam/ deleted (SPEC §8 item 3): record without asking Jev, creating the registry
        # from any review records present. Decide again under the lock in case another command recorded the
        # tree meanwhile.
        with writes.locked(cfg, "validate --record", mutating=True):
            if read_recorded(cfg) is None:
                return _validate(cfg, args, baseline=True)
    _print_checks("validate --record", check_findings(cfg, None, command="validate --record",
                                                     client_factory=JevClient))
    with writes.locked(cfg, "validate --record", mutating=True):  # it writes tree.hash (SPEC §5.2.6)
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


def _stale_line(command: str, rec_id: str, finding_id: str, view) -> str:
    """The line a finding write prints for a CT or CU it made stale by changing `finding_id` (SPEC §5.2.4;
    `put`, and `renumber` for a dependent it re-keyed), with the rebind command for it."""
    return (f"kblam {command}: {rec_id} is now stale (this {command} changed {finding_id}, which it is bound "
            f"to); a reviewer rechecks it and runs {_rebind_command(rec_id, view)}. kblam validate fails until then")


def _cmd_put(cfg, args) -> int:
    if records.FILENAME_RE.match(Path(args.file).name):  # an SC-/CT-/CU- file: a record put (§5.2.5)
        return _write_result(cfg, "put", review_write.put_record(cfg, Path(args.file)))
    result = put(cfg, Path(args.file), client_factory=JevClient)
    # Errors that were in findings/ before this put do not block it (SPEC §7 put, Validation); they are printed as
    # warnings after the put's own report, and before the final line of a refusal.
    warnings = [f"kblam put: warning: {issue.format(result.view)}"
                for issue in result.warnings if issue.is_error]
    notes = [issue.format(result.view) for issue in result.warnings if not issue.is_error]
    if result.issues:
        for issue in result.issues:
            print(issue.format(result.view))
        for line in (*warnings, *notes):
            print(line)
        print(f"kblam put: rejected {result.finding_id} ({len(result.issues)} error(s)); "
              f"{cfg.findings_dir}/ is unchanged. Fix the staged file and put it again. {SKILL_POINTER}")
        return EXIT_INVALID
    for line in notes:
        print(line)
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
    existing_errors = [issue for issue in result.warnings if issue.is_error]
    if existing_errors:
        print(f"kblam put: the {len(existing_errors)} warning(s) above were already in {cfg.findings_dir}/ before this "
              f"put, so they did not block it; kblam validate fails until each is fixed")
    for rec_id in result.stale:  # SPEC §5.2.4: the put lists what it makes stale
        print(_stale_line("put", rec_id, result.finding_id, result.view))
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
    if result.stale:
        view = load_view(cfg)  # renumber writes no record, so the statuses the rebind commands depend on hold
        for rec_id, dependent in result.stale:
            print(_stale_line("renumber", rec_id, dependent, view))
    if result.resolutions:
        print(f"kblam renumber: copied {result.resolutions} resolution(s) of {result.old_id} to {result.new_id} in "
              f"{cfg.resolutions_path.name}, since their state hash showed they meant {result.old_path}; commit "
              f"it with the renumbered finding")
    print(f"kblam renumber: regenerated {result.index_path}" + (" and .kblam/tree.hash" if result.recorded else ""))
    if result.mentions:
        print(f"kblam renumber: {len(result.mentions)} other mention(s) of {result.old_id} may mean either "
              f"finding; a person checks each and points it at {result.new_id} where it meant the renumbered one:")
        for mention in result.mentions:
            print(f"  {mention}")
    else:
        print(f"kblam renumber: no other mention of {result.old_id} in {cfg.findings_dir}/")
    return EXIT_OK


def _tree_hash_kept(cfg, because: str | None) -> str:
    """Why `kblam upgrade` did not advance tree.hash (upgrade.KEPT_*), and what a person does next."""
    from kblam import upgrade

    fix = "Run kblam validate, fix anything it lists, then run kblam validate --record"
    none = "recorded no .kblam/tree.hash: there is none (a new clone, or .kblam/ was deleted), and "
    old = "left .kblam/tree.hash as it was: it is in the old format, which cannot vouch for review records, and "
    if because == upgrade.KEPT_NONE_RECORDS:
        return f"{none}kblam upgrade does not record one while {cfg.review_dir}/ holds review records. {fix}"
    if because == upgrade.KEPT_NONE_INVALID:
        return f"{none}the tree as it was before this upgrade failed kblam validate. {fix}"
    if because == upgrade.KEPT_OLD_RECORDS:
        return f"{old}{cfg.review_dir}/ holds some. {fix}"
    if because == upgrade.KEPT_OLD_REGISTRY:
        return f"{old}.kblam/review-ids lists some. {fix}"
    if because == upgrade.KEPT_OLD_UNREADABLE:
        return f"{old}.kblam/review-ids cannot be read as a list of record IDs. {fix}"
    # Format 1 covers findings/ only; format 2 covers the review root as well.
    roots = (f"{cfg.findings_dir}/ or {cfg.review_dir}/" if because == upgrade.KEPT_CHANGED
             and cfg.review_path.is_dir() else f"{cfg.findings_dir}/")
    return (f"left .kblam/tree.hash as it was: {roots} had changed outside kblam, and kblam validate --record "
            f"accepts that once the tree is clean")


def _cmd_upgrade(cfg, args) -> int:
    """`kblam upgrade` (SPEC §7): what it migrated, step by step, and what a person does next."""
    from kblam.upgrade import upgrade

    result = upgrade(cfg)
    out = []
    if result.restamped:
        out.append(f"kblam upgrade: re-stamped {len(result.restamped)} depends_on value(s) in {cfg.findings_dir}/ "
                   f"with v2 fingerprints:")
        out += [f"  {r.path}: {r.target} {r.old} -> {r.new}" for r in result.restamped]
        out.append("kblam upgrade: " + ("recorded .kblam/tree.hash for the tree" if result.recorded else
                                        _tree_hash_kept(cfg, result.kept_because)))
    if result.stale:
        out.append(f"kblam upgrade: {len(result.stale)} depends_on value(s) stay in the old format, because "
                   f"their target changed since it was recorded; re-read each target, then kblam ack "
                   f"<dependent> <target>:")
        out += [f"  {r.path}: {r.target} {r.old}" for r in result.stale]
    if result.staged:
        out.append(f"kblam upgrade: re-stamped {len(result.staged)} depends_on value(s) in staged findings:")
        out += [f"  {r.path}: {r.target} {r.old} -> {r.new}" for r in result.staged]
    if result.edit_records:
        out.append(f"kblam upgrade: updated the edit record of {', '.join(result.edit_records)}, whose staged "
                   f"copy still puts over the re-stamped file")
    if result.items_rekeyed:
        moves = ", ".join(f"{old} is now {new}" for old, new in result.items_rekeyed)
        out.append(f"kblam upgrade: moved {len(result.items_rekeyed)} open item(s) to v2 fingerprints, under the "
                   f"IDs a check now gives them: {moves}")
    if result.items_closed:
        out.append(f"kblam upgrade: closed {len(result.items_closed)} item(s) whose finding changed since it was "
                   f"raised: {', '.join(result.items_closed)}")
    if result.marks_moved or result.marks_dropped:
        out.append(f"kblam upgrade: carried {result.marks_moved} checked mark(s) over to v2 fingerprints"
                   + (f"; dropped {result.marks_dropped} for versions no longer in the KB" if result.marks_dropped
                      else ""))
    if result.resolutions_moved or result.resolutions_present or result.resolutions_lapsed:
        out.append(f"kblam upgrade: moved {result.resolutions_moved} resolution(s) from .kblam/pairs.sqlite into "
                   f"{cfg.resolutions_path.name}"
                   + (f"; {result.resolutions_present} were there already" if result.resolutions_present else "")
                   + (f"; dropped {result.resolutions_lapsed} whose finding changed since, so it no longer applied"
                      if result.resolutions_lapsed else ""))
    if result.answers_moved or result.answers_dropped:
        out.append(f"kblam upgrade: re-keyed {result.answers_moved} cached Jev answer(s) by state hash"
                   + (f"; dropped {result.answers_dropped} about another wording or versions no longer in the KB"
                      if result.answers_dropped else ""))
    if result.prompt_ids:
        relation, revision = result.prompt_ids
        out.append(f"kblam upgrade: [jev.thresholds] in {CONFIG_NAME} records prompt_id, the id of the whole "
                   f"prompt. Record each question's own id instead and delete prompt_id: relation_prompt_id = "
                   f"\"{relation}\" and revision_prompt_id = \"{revision}\" (kblam prompt-id prints them). "
                   f"{CONFIG_NAME} is a person's to edit; after editing it, run kblam approve-config before "
                   f"committing")
    if result.prompt_id_differs:
        recorded, current = result.prompt_id_differs
        out.append(f"kblam upgrade: [jev.thresholds] in {CONFIG_NAME} records prompt_id {recorded}, but the id of the "
                   f"current wording in [jev.prompt] is {current}: the thresholds were calibrated on other wording, so "
                   f"no Jev verdict rejects until they are recalibrated (SPEC §6.4, §10). Leave prompt_id as it is "
                   f"until then: recording the current per-question ids would apply the thresholds to wording nobody "
                   f"calibrated")
    if result.restamped or result.resolutions_moved:
        committed = (["the re-stamped findings"] if result.restamped else []) + (
            [cfg.resolutions_path.name] if result.resolutions_moved else [])
        out.append(f"kblam upgrade: commit {' and '.join(committed)}, so every clone has them; each other machine "
                   f"runs kblam upgrade once for its own .kblam/")
    if not result.changed and not result.prompt_ids and not result.prompt_id_differs and not result.stale:
        out.append("kblam upgrade: nothing to upgrade; the knowledge base and this machine's state are already in "
                   "the M6.10 formats")
    for line in out:
        print(line)
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
    elif d.state == "old" and d.upgradable:
        detail = (f"old        recorded {d.recorded} before fingerprint v2, and {d.target} is unchanged since; "
                  f"kblam upgrade re-stamps it")
    elif d.state == "old":
        detail = (f"old        recorded {d.recorded} before fingerprint v2, and {d.target} changed since; re-read "
                  f"{d.target}, then kblam ack {d.dependent} {d.target}")
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
    """Run the check: commands approved on this machine (SPEC §7). At a terminal, ask about each new or
    changed one first; without one, either show each for the agent to approve with --approve (the default)
    or, when kblam.toml requires a person's approval, report it as not approved."""
    recheck.check_state_paths(cfg)
    if args.list and args.approve is not None:
        raise RecheckError("--list prints each check: command and runs nothing, while --approve approves one "
                           "command and runs it; use one or the other")
    person_only = cfg.recheck_person_approval
    if args.approve is not None:
        return _approve(cfg, args)
    checks, problems = recheck.collect(cfg, args.ids)
    approvals = recheck.load_approvals(cfg)
    states = {c.finding_id: recheck.approval_state(c, approvals, person_only=person_only) for c in checks}
    if args.list:
        return _list_rechecks(checks, states, problems)
    terminal = recheck.at_terminal()
    declined = set()
    if terminal:  # a person is here: ask about every command that needs it before running any
        for c in checks:
            if c.problem is None and not states[c.finding_id].approved:
                print(recheck.approval_prompt(cfg, c, states[c.finding_id]))
                try:
                    answer = input(f"Run it, and approve it for {c.finding_id} on this machine? [y/N] ")
                except EOFError:
                    answer = ""
                if answer.strip().lower() in ("y", "yes"):
                    recheck.record_approval(cfg, c)
                    states[c.finding_id] = recheck.State(True, approver=recheck.PERSON)
                else:
                    declined.add(c.finding_id)
                    states[c.finding_id] = recheck.State(False, "you did not approve it")
    counts = Counter(_recheck_one(cfg, c, states[c.finding_id], terminal, c.finding_id in declined, person_only)
                     for c in checks)
    for problem in problems:
        print(f"kblam recheck: {problem}")
    if not checks and not problems:
        print("kblam recheck: no finding has a check: command")
        return EXIT_OK
    return _recheck_summary(len(checks), counts, len(problems))


def _approve(cfg, args) -> int:
    """`kblam recheck F-NNNN --approve DIGEST`: the agent running kblam records its own approval of the one
    check whose block it read, then that check runs."""
    if cfg.recheck_person_approval:
        raise RecheckError("--approve is refused: kblam.toml sets recheck_person_approval = true, so only a person "
                           "at a terminal approves a check: command. Ask the user to run kblam recheck with that "
                           "finding's ID at a terminal, which shows the command and asks them")
    if len(args.ids) != 1:
        got = ", ".join(args.ids) if args.ids else "none"
        raise RecheckError(f"--approve approves the one check whose block you read, so it needs exactly one finding "
                           f"ID; {got} was given. Run kblam recheck --list to see which findings have a check: "
                           f"command, then approve one by ID with the digest its block prints")
    check = recheck.collect(cfg, args.ids)[0][0]
    if check.problem:
        raise RecheckError(f"{check.finding_id} cannot be approved: {check.problem}")
    digest = recheck.approval_digest(check)
    if args.approve != digest:
        state = recheck.approval_state(check, recheck.load_approvals(cfg), person_only=False)
        if state.approved:
            raise RecheckError(f"{check.finding_id} is already approved as it is now, so there is nothing to "
                               f"approve, and {shown(args.approve)} is not the digest of its check: command and "
                               f"files ({digest}). Run kblam recheck {check.finding_id} to run it")
        print(f"kblam recheck: {check.finding_id} was not approved: {shown(args.approve)} does not name its check: "
              f"command and files as they are now, so the command or a file it names changed since that digest "
              f"was shown. Read the block below again, then approve the digest it prints, or leave the finding "
              f"as it is.")
        print(recheck.approval_block(cfg, check, state))
        return EXIT_INVALID
    recheck.record_approval(cfg, check, approver=recheck.AGENT)
    outcome = _recheck_one(cfg, check, recheck.State(True, approver=recheck.AGENT), False, False, False)
    return _recheck_summary(1, Counter([outcome]), 0)


def _recheck_summary(total: int, counts: Counter, problems: int) -> int:
    """The run's last line and its exit status: 0 when every selected check passed, 1 otherwise."""
    parts = [f"{counts['passed']} passed"] + [f"{counts[k]} {k}" for k in ("failed", "could not run", "not approved")
                                              if counts[k]]
    if problems:
        parts.append(f"{problems} finding(s) not considered")
    bad = total - counts["passed"] + problems
    print(f"kblam recheck: {total} check(s): {', '.join(parts)}" + (f". {SKILL_POINTER}" if bad else ""))
    return EXIT_INVALID if bad else EXIT_OK


def _recheck_one(cfg, c: recheck.Check, state: recheck.State, terminal: bool, declined: bool,
                 person_only: bool) -> str:
    """Run or report one check; the summary's category for it."""
    if c.problem:
        print(f"kblam recheck: {c.finding_id} could not run: {c.problem}")
        recheck.log(cfg, c, "not_started", terminal=terminal)
        return "could not run"
    if state.approved:
        now = recheck.named_files(cfg.repo_root, c.argv)
        if now != c.files:  # changed while this run asked, or ran an earlier check
            state = recheck.State(False, f"{recheck.changed_files(c.files, now)} changed since approval")
    if not state.approved:
        print(f"kblam recheck: {c.finding_id} not run: not approved on this machine ({state.reason}): "
              f"{shown(c.command)}")
        if not terminal and not person_only:  # the agent running kblam reads the block and approves it itself
            print(recheck.approval_block(cfg, c, state))
        elif not terminal:
            print(f"  A person approves it by running kblam recheck {c.finding_id} at a terminal, which shows the "
                  f"command first; an agent asks the user to do that. Anyone who can push to this repository "
                  f"can put a command in a finding, so an agent never runs an unapproved one itself.")
        elif not declined:
            print(f"  Run kblam recheck {c.finding_id} again to see it and decide.")
        recheck.log(cfg, c, "declined" if declined else "not_approved", terminal=terminal)
        return "not approved"
    print(f"kblam recheck: {c.finding_id} running: {shown(c.command)}", flush=True)
    result = recheck.run_check(cfg, c)
    recheck.log(cfg, c, result.status, terminal=terminal, approver=state.approver, result=result)
    if result.status == "passed":
        print(f"kblam recheck: {c.finding_id} passed ({result.detail} after {result.seconds:.1f} s)")
        return "passed"
    if result.status == "not_started":
        print(f"kblam recheck: {c.finding_id} could not run: {result.detail}")
        return "could not run"
    output = recheck.output_path(cfg, c.finding_id).relative_to(cfg.repo_root).as_posix()
    what = result.detail if result.status == "timed_out" else f"{result.detail} after {result.seconds:.1f} s"
    print(f"kblam recheck: {c.finding_id} FAILED ({what}); its output is in {output}"
          + (", ending:" if result.tail else ", and is empty"))
    for line in result.tail:
        print(f"  | {line}")
    if result.status == "timed_out":
        print(f"  {c.finding_id}'s check did not finish. If it needs longer, ask the user to raise [kb] "
              f"recheck_timeout_seconds in kblam.toml; otherwise find out why it hangs, and kblam edit "
              f"{c.finding_id} to fix its check:.")
    else:
        print(f"  {c.finding_id}'s key number did not reproduce, or its command broke. Read the output, then "
              f"kblam edit {c.finding_id} so the finding states what its evidence shows now, or so its check: "
              f"runs what reproduces it.")
    return "failed"


def _list_rechecks(checks: list[recheck.Check], states: dict[str, recheck.State], problems: list[str]) -> int:
    """`kblam recheck --list`: each check: command and its approval state on this machine; nothing runs."""
    approved = broken = 0
    for c in checks:
        state = states[c.finding_id]
        if c.problem:
            broken += 1
            label = "cannot run"
        elif state.approved:
            approved += 1
            label = "approved"
        else:
            label = f"not approved ({state.reason})"
        print(f"kblam recheck: {c.finding_id} {label}: {shown(c.command)}")
        if c.problem:
            print(f"  {c.problem}")
    for problem in problems:
        print(f"kblam recheck: {problem}")
    print(f"kblam recheck --list: {len(checks)} check(s): {approved} approved, "
          f"{len(checks) - approved - broken} not approved on this machine"
          + (f", {broken} cannot run" if broken else "") + "; nothing was run")
    return EXIT_OK


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
                        "out-of-band change such as git pull) and create .kblam/review-ids from the records "
                        "present; on failure neither is written")
    p.add_argument("--commit", action="store_true",
                   help="also refuse a commit that changes kblam.toml without a person's approval "
                        "(kblam approve-config); what the git pre-commit hook runs")
    p.add_argument("--forget-missing", action="store_true",
                   help="with --record, also drop from .kblam/review-ids the record IDs whose records are "
                        "gone from the review root, printing each; reserved for the coordinator or the user")
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
                                     "for the current state hashes, model and prompt ids")
    p.set_defaults(func=_cmd_audit)
    p = sub.add_parser("resolve", help="close a review or rejected item that Jev misread, recording why in "
                                       "kblam.resolutions.jsonl (an adjudicator's command)")
    p.add_argument("id", metavar="R-XXXXXXXX")
    p.add_argument("--distinct", required=True, metavar="REASON",
                   help="why Jev misread the item, for a later reader; its sides are not raised again until a "
                        "claim or scope changes")
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
    p = sub.add_parser("upgrade", help="move the knowledge base and this machine's .kblam/ state to the formats "
                                       "M6.10 introduces (fingerprint v2, state hashes, committed resolutions)")
    p.set_defaults(func=_cmd_upgrade)
    p = sub.add_parser("cost", help="summarise .kblam/calls.jsonl: Jev requests, tokens and cost, per day and kind")
    p.set_defaults(func=_cmd_cost)
    p = sub.add_parser("recheck", help="run the check: commands of the given findings, or of every finding; a "
                                       "new or changed command runs only once it is approved on this machine, "
                                       "by the agent that read its block (--approve) or, when kblam.toml sets "
                                       "recheck_person_approval, by a person at a terminal")
    p.add_argument("ids", nargs="*", metavar="F-NNNN")
    p.add_argument("--list", action="store_true",
                   help="print each check: command and whether it is approved on this machine; run nothing")
    p.add_argument("--approve", metavar="DIGEST",
                   help="approve the check: command of the one finding given, exactly as the block printed for it "
                        "shows, and run it; refused when kblam.toml sets recheck_person_approval = true")
    p.set_defaults(func=_cmd_recheck)
    p = sub.add_parser("prompt-id", help="print the ids of this project's Jev prompt (its [jev.prompt] tables): "
                                         "the combined id, then each question's own, which [jev.thresholds] "
                                         "records and calibration is tied to")
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
        tracked = tracked_state(cfg)
        if tracked:  # SPEC §8.3: a pull may have written someone else's state there
            print(f"kblam {args.command}: {tracked_state_problem(tracked)}. {SKILL_POINTER}", file=sys.stderr)
            return EXIT_INVALID
        if args.command in UPGRADE_FIRST:
            from kblam.upgrade import old_state, old_state_problem

            try:
                stale = old_state(cfg)
            except ReviewError:
                stale = []  # a damaged review.jsonl: the command reports it in its own terms
            if stale:
                print(f"kblam {args.command}: {old_state_problem(stale)}. {SKILL_POINTER}", file=sys.stderr)
                return EXIT_INVALID
        return args.func(cfg, args)
    except (ConfigError, StoreError, LockError, JevUnavailable, ReviewError, ApprovalError, RecheckError) as exc:
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
