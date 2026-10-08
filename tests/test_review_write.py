"""`put`, `review decide`, `review rebind`, `challenge pin` and `review index` (SPEC §5.2.4 "Where each
rule blocks", §5.2.5, §5.2.6). The fixtures stage records exactly as review_stage does (a
`.kblam/review-staging/<ID>.yaml` file and an allocation or edit-base receipt in
`.kblam/review-receipts/`), because the staging commands are a different unit; the records themselves
and their sources come from the nested Git fixture repository. Offline and deterministic: `_today` is
pinned.
"""

from __future__ import annotations

import datetime
import hashlib
import json

import pytest

from conftest import SOURCE_REPO, TRACE_PATH, TRACE_TEXT, ZERO64, record_data
from kblam import journal, matching, receipts, records, review_write, treehash
from kblam.cli import main
from kblam.decisions import subject_digest
from kblam.finding import fingerprint
from kblam.review_index import generate_review_index
from kblam.rules import validate
from kblam.sources import SourceReader, sha256_hex
from kblam.store import StoreError
from kblam.treehash import format_line
from kblam.view import load_view

REVIEW = "research-review"
TRACE = f"{SOURCE_REPO}/{TRACE_PATH}"
TRACE_LINES = TRACE_TEXT.split("\n")
SHA = hashlib.sha256(TRACE_TEXT.encode("utf-8")).hexdigest()
WORD = "the two bytes are equal"                       # TRACE line 3
WORD_SHA = hashlib.sha256(WORD.encode("utf-8")).hexdigest()
TODAY = datetime.date(2026, 9, 28)
CREATOR = "reviewer-a"
DECIDER = "reviewer-b"                                 # independent of creator and proponent
FINDING = "F-0001"
EVIDENCE = "evidence/2026-09-22-ratio/README.md"


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch) -> datetime.date:
    """A decision's date is today's; the tests pin it, so the bytes kblam writes are deterministic."""
    monkeypatch.setattr(review_write, "_today", lambda: TODAY)
    return TODAY


# --- fixtures, records and receipts ---------------------------------------------------------------


def captured_lines(first: int, last: int) -> dict:
    """An SC receipt's `captured`: the lines `challenge new --lines A-B` copied out of the source."""
    return {"lines": [first, last], "text": "\n".join(TRACE_LINES[first - 1:last])}


def assertion(**fields) -> dict:
    """A source's assertion (SPEC §5.2.3): lines 3-3, the quoted text, its sha256 and occurrence."""
    base = {"lines": [3, 3], "text": WORD, "sha256": WORD_SHA, "occurrence": 1}
    base.update(fields)
    return base


def basis_entry(path: str = TRACE, sha: str | None = SHA, **fields) -> dict:
    entry = {"path": path, "sha256": sha, "repo": None, "commit": None, "blob": None, "snapshot": None,
             "locator": "row 102: printed byte values", "role": "internal-inconsistency",
             "provenance": "observed"}
    entry.update(fields)
    return entry


def source_ref(repo=None, *, sha: str = SHA, pin: bool = True, **fields) -> dict:
    """A challenge's source: a reference, pinned to the fixture repository's HEAD when `pin`."""
    reference = {"path": TRACE, "sha256": sha, "repo": None, "commit": None, "blob": None,
                 "snapshot": None}
    if pin and repo is not None:
        reference.update({"repo": SOURCE_REPO, "commit": repo.head(), "blob": repo.blob(TRACE_PATH)})
    reference.update(fields)
    return reference


def sc_data(repo=None, *, rec_id: str = "source-challenge-0002", spec: dict | None = None, basis=None,
            pin: bool = True, sha: str = SHA, **fields) -> dict:
    """A staged source challenge's data (SPEC §5.2.3). `spec` replaces fields of the assertion, `basis`
    the entries."""
    data = record_data("source-challenge", rec_id)
    data["source"] = {**source_ref(repo, sha=sha, pin=pin), "assertion": assertion(**(spec or {}))}
    data["basis"] = [basis_entry()] if basis is None else basis
    for key, value in fields.items():
        data[key] = value
    return data


def ct_data(kb, *, rec_id: str = "claim-task-0001", finding: str = FINDING, **fields) -> dict:
    """A staged claim task's data bound to the installed finding (record_data's placeholders replaced)."""
    digest, raw_sha = finding_binding(kb, finding)
    data = record_data("claim-task", rec_id)
    data["finding"] = finding
    data["claim_fingerprint"] = digest
    data["base_file_sha256"] = raw_sha
    for key, value in fields.items():
        data[key] = value
    return data


def cu_data(kb, *, rec_id: str = "checked-use-0001", challenge: str = "source-challenge-0001", finding: str = FINDING,
            ordinal: int = 1, **fields) -> dict:
    """A staged checked use's data bound to the installed challenge and finding (SPEC §5.2.3)."""
    digest, raw_sha = finding_binding(kb, finding)
    data = record_data("checked-use", rec_id)
    data["challenge"] = challenge
    data["challenge_bind"] = digest_of(kb, challenge)
    data["finding"] = finding
    data["finding_fingerprint"] = digest
    data["finding_file_sha256"] = raw_sha
    data["citation"] = citation_of(kb, finding, ordinal)
    for key, value in fields.items():
        data[key] = value
    return data


def finding_binding(kb, finding_id: str = FINDING) -> tuple[str, str]:
    """(the finding's K3 fingerprint, the sha256 of its file bytes)."""
    found = next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id)
    return fingerprint(found, kb.cfg.scope_separator), sha256_hex(found.raw)


def citation_of(kb, finding_id: str = FINDING, ordinal: int = 1) -> dict:
    """The citation of the finding's excerpt `ordinal`, as `use review` writes it."""
    view = load_view(kb.cfg)
    reader = SourceReader(kb.cfg, view)
    found = next(f for f in view.findings if f.file_id == finding_id)
    match = matching.finding_matches(view, reader, found)[ordinal - 1]
    return {"ordinal": match.ordinal, "path": match.path, "range": list(match.range),
            "tag_sha256": match.tag_sha256}


def digest_of(kb, rec_id: str) -> str:
    """The installed record's subject digest, as `show` prints it."""
    return subject_digest(rec_id.rsplit("-", 1)[0], record(kb, rec_id).data)


def with_decision(kind: str, data: dict, status: str, *, by: str = DECIDER,
                  evidence: list | None = None) -> dict:
    """Append the one decision `status` needs, with the subject digest it binds (SPEC §5.2.2)."""
    data["status"] = status
    data["decisions"] = [{"date": TODAY, "by": by, "status": status, "reason": "reviewed the record",
                          "evidence": list(evidence or []), "bind": None}]
    data["decisions"][0]["bind"] = subject_digest(kind, data)
    return data


def excerpt_body(*tags: tuple[str, str]) -> str:
    """A finding body of verbatim tags, each (the cited range, the quoted text)."""
    blocks = [f"<!-- verbatim: {TRACE}:{cite} -->\n```text\n{text}\n```" for cite, text in tags]
    return "\n\n".join(blocks) + "\n"


def allocation(kind: str, data: dict, captured: dict | None = None) -> dict:
    """The allocation receipt review_stage writes for `data` (SPEC §5.2.5 Receipts)."""
    created = data["created"]
    payload = {"id": data["id"], "creator": data["creator"],
               "created": created.isoformat() if isinstance(created, datetime.date) else created}
    if kind in ("claim-task", "checked-use"):
        payload["proponent"] = data["proponent"]
    if kind == "source-challenge":
        payload["source"] = {key: data["source"][key] for key in records.REF_KEYS}
        payload["captured"] = captured if captured is not None else captured_lines(3, 3)
    elif kind == "claim-task":
        for key in ("kind", "finding", "claim_fingerprint", "base_file_sha256"):
            payload[key] = data[key]
    else:
        for key in ("challenge", "challenge_bind", "finding", "finding_fingerprint",
                    "finding_file_sha256", "citation"):
            payload[key] = data[key]
    return payload


def stage(kb, data: dict, *, captured: dict | None = None, receipt: bool = True):
    """Stage a record as `challenge new` / `task new` / `use review` do: the file and its receipt."""
    kind = data["id"].rsplit("-", 1)[0]
    path = kb.root / ".kblam/review-staging" / f"{data['id']}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(records.dump(data))
    if receipt:
        receipts.write_allocation(kb.cfg, data["id"], allocation(kind, data, captured))
    return path


def stage_edit(kb, rec_id: str, mutate=None):
    """Stage a copy of an installed record with its edit-base receipt, as `challenge edit` does."""
    installed = record_path(kb, rec_id).read_bytes()
    path = kb.root / ".kblam/review-staging" / f"{rec_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(installed)
    receipts.write_edit_base(kb.cfg, rec_id, sha256_hex(installed))
    if mutate is not None:
        data = record(kb, rec_id).data
        mutate(data)
        path.write_bytes(records.dump(data))
    return path


def install(kb, kind: str, data: dict, *, index: bool = True) -> str:
    """Write a record at its canonical path (fixture setup only) and regenerate the review index."""
    path = record_path(kb, data["id"])
    kb.write(path.relative_to(kb.root).as_posix(), records.dump(data))
    if index:
        kb.write(f"{REVIEW}/INDEX.md", generate_review_index(load_view(kb.cfg)))
    return path.relative_to(kb.root).as_posix()


def record_path(kb, rec_id: str):
    return kb.root / REVIEW / records.KINDS[rec_id.rsplit("-", 1)[0]] / f"{rec_id}.yaml"


def record(kb, rec_id: str):
    return next(rec for rec in load_view(kb.cfg).records if rec.id == rec_id)


def installed_data(kb, rec_id: str) -> dict:
    return record(kb, rec_id).data


def blocking(kb) -> list:
    return [issue for issue in validate(load_view(kb.cfg)) if issue.is_error]


def mark_clean(kb) -> None:
    """Record the tree as kblam left it: a format-2 tree.hash of the current view."""
    treehash.write_tree_hash_v2(kb.cfg, load_view(kb.cfg))


def tree(kb) -> dict:
    """Every file of the KB, the nested repository's .git aside: a refusal writes nothing at all.

    KB.snapshot() walks findings/ only, which is not enough here: a refused put must leave the staged
    file, the receipts, the registry and the tree.hash exactly as they were."""
    return {p.relative_to(kb.root).as_posix(): p.read_bytes()
            for p in sorted(kb.root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(kb.root).parts}


def confirmed(kb, repo, rec_id: str = "source-challenge-0001") -> None:
    """Install a confirmed challenge (fixture setup only), as `decide --status confirmed` would leave it."""
    install(kb, "source-challenge", with_decision("source-challenge", sc_data(repo, rec_id=rec_id), "confirmed"))


def finding_file(kb, finding_id: str = FINDING):
    """The path of the one finding file with this ID."""
    return kb.root / next(f for f in load_view(kb.cfg).findings if f.file_id == finding_id).path


def unreadable(kb, finding_id: str = FINDING):
    """Take a finding out of the KB: a binding cannot be recomputed while its finding is not one readable
    file. Returns (its path, its bytes), so a test can put it back exactly as it was."""
    path = finding_file(kb, finding_id)
    raw = path.read_bytes()
    path.unlink()
    return path, raw


PLACEHOLDERS = ("NAME", "TEXT", "D", "PROVENANCE:PATH:LOCATOR")


def rebind_argv(message: str) -> list[str]:
    """The `kblam review rebind …` command a diagnostic printed, as argv, with its placeholders still in
    place: substituting them is the reader's job. A `--flag` takes the next word as its value when that
    word is a placeholder, `--reopen` stands alone, and the sentence around the command is left out, so
    the word ending it may carry a comma."""
    words = message[message.index("kblam review rebind "):].split()
    argv = words[:4]                                       # kblam review rebind <ID>
    index = 4
    while index < len(words):
        flag = words[index].rstrip(",;.")
        value = words[index + 1].rstrip(",;.") if index + 1 < len(words) else ""
        if flag == "--reopen":
            argv.append(flag)
            index += 1
        elif flag.startswith("--") and value in PLACEHOLDERS:
            argv += [flag, value]
            index += 2
        else:
            break
    return argv


def run_rebind(kb, message: str, digest: str) -> int:
    """The rebind a diagnostic printed, run the way the console entry point runs it, with each
    placeholder given a real value: a command missing a flag cannot be run."""
    values = {"NAME": DECIDER, "TEXT": "rechecked the binding", "D": digest,
              "PROVENANCE:PATH:LOCATOR": f"observed:{EVIDENCE}:section 2"}
    argv = rebind_argv(message)
    assert argv[:3] == ["kblam", "review", "rebind"]
    return main(["--root", str(kb.root), *[values.get(word, word) for word in argv[1:]]])


@pytest.fixture
def ready(kb, source_repo):
    """A KB with F-0001 (one verbatim excerpt of the trace) and source-challenge-0001 installed open, pinned and with
    primary support, so a confirmation has nothing to report."""
    kb.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.",
           body=excerpt_body(("3-3", WORD)))
    install(kb, "source-challenge", sc_data(source_repo, rec_id="source-challenge-0001"))
    return kb


# --- argument parsing ------------------------------------------------------------------------------


def test_parse_evidence_reads_a_path_and_a_locator_that_holds_colons(ready):
    entry = review_write.parse_evidence(ready.cfg, f"observed:{EVIDENCE}:section 2: row 102")

    assert list(entry) == [*records.REF_KEYS, "locator", "provenance"]
    assert (entry["path"], entry["locator"], entry["provenance"]) == \
        (EVIDENCE, "section 2: row 102", "observed")
    assert entry["sha256"] == sha256_hex((ready.root / EVIDENCE).read_bytes())
    assert (entry["repo"], entry["commit"], entry["blob"], entry["snapshot"]) == (None, None, None, None)


def test_parse_evidence_writes_the_path_as_a_record_writes_it(ready):
    entry = review_write.parse_evidence(ready.cfg, f"observed:./{EVIDENCE}:section 2")

    assert entry["path"] == EVIDENCE
    assert entry["sha256"] == sha256_hex((ready.root / EVIDENCE).read_bytes())


def test_parse_evidence_refuses_a_spec_it_cannot_split(ready):
    with pytest.raises(StoreError, match="takes PROVENANCE:PATH:LOCATOR"):
        review_write.parse_evidence(ready.cfg, "observed:evidence/x.md")
    with pytest.raises(StoreError, match="has an empty path"):
        review_write.parse_evidence(ready.cfg, "observed::section 2")
    with pytest.raises(StoreError, match="has an empty locator"):
        review_write.parse_evidence(ready.cfg, "observed:evidence/x.md:")


def test_parse_evidence_refuses_a_provenance_outside_the_vocabulary(ready):
    with pytest.raises(StoreError, match="provenance 'guessed' is not one of"):
        review_write.parse_evidence(ready.cfg, "guessed:evidence/x.md:section 2")


def test_parse_evidence_refuses_a_directory_and_a_missing_file(ready):
    with pytest.raises(StoreError, match="is a directory, not a file"):
        review_write.parse_evidence(ready.cfg, "observed:evidence:section 2")
    with pytest.raises(StoreError, match="does not exist"):
        review_write.parse_evidence(ready.cfg, "observed:evidence/nope.md:section 2")


# --- put: the first put of each kind --------------------------------------------------------------


def test_a_first_put_installs_the_record_the_index_and_the_registry(ready):
    data = sc_data()
    data["proposition"] = "A narrowed proposition"
    staged = stage(ready, data)

    result = review_write.put_record(ready.cfg, staged)

    assert result.ok and result.recorded is False        # tree.hash is still format 1: not advanced
    assert result.path == f"{REVIEW}/challenges/source-challenge-0002.yaml"
    assert installed_data(ready, "source-challenge-0002")["proposition"] == "A narrowed proposition"
    assert json.loads((ready.root / ".kblam/review-ids").read_text()) == ["source-challenge-0001", "source-challenge-0002"]
    assert generate_review_index(load_view(ready.cfg)) == \
        (ready.root / REVIEW / "INDEX.md").read_bytes()
    assert not staged.exists()
    assert blocking(ready) == []


def test_a_first_put_needs_its_allocation_receipt(ready):
    staged = stage(ready, sc_data(), receipt=False)

    with pytest.raises(StoreError, match="no allocation receipt"):
        review_write.put_record(ready.cfg, staged)

    assert staged.exists()
    assert not record_path(ready, "source-challenge-0002").exists()


def test_a_first_put_refuses_a_field_the_receipt_fixes(ready):
    data = sc_data()
    staged = stage(ready, data)
    data["creator"] = DECIDER                             # the author edits it in staging
    staged.write_bytes(records.dump(data))

    with pytest.raises(StoreError, match="creator is 'reviewer-b', but its allocation receipt has "
                                        "'reviewer-a'"):
        review_write.put_record(ready.cfg, staged)


def test_a_first_put_refuses_a_status_or_decision_that_is_not_open_and_empty(ready):
    data = sc_data()
    data["status"] = "confirmed"
    staged = stage(ready, data)

    with pytest.raises(StoreError, match="status must be open and its decisions empty"):
        review_write.put_record(ready.cfg, staged)


def test_a_first_put_refuses_blank_required_fields(ready):
    data = sc_data(proposition="")
    staged = stage(ready, data)

    result = review_write.put_record(ready.cfg, staged)

    assert not result.ok and result.recorded is False
    assert [issue.message for issue in result.issues] == ["proposition: required"]
    assert not record_path(ready, "source-challenge-0002").exists()


def test_a_first_put_computes_the_assertion_hash_and_occurrence(ready):
    data = sc_data(spec={"sha256": None, "occurrence": None})
    staged = stage(ready, data)

    result = review_write.put_record(ready.cfg, staged)

    assert result.ok, [issue.message for issue in result.issues]
    written = installed_data(ready, "source-challenge-0002")["source"]["assertion"]
    assert (written["sha256"], written["occurrence"]) == (WORD_SHA, 1)


def test_a_first_put_refuses_a_wrong_assertion_sha256(ready):
    data = sc_data(spec={"sha256": ZERO64})
    staged = stage(ready, data)

    with pytest.raises(StoreError, match="assertion.sha256 is 0{64}"):
        review_write.put_record(ready.cfg, staged)


def test_a_first_put_refuses_a_wrong_occurrence(ready):
    staged = stage(ready, sc_data(spec={"occurrence": 2}))

    with pytest.raises(StoreError, match="assertion.occurrence is 2, but the assertion matches at "
                                        "occurrence 1 in the source"):
        review_write.put_record(ready.cfg, staged)


def test_a_first_put_refuses_a_source_the_receipt_no_longer_matches(ready):
    staged = stage(ready, sc_data(pin=False, sha=ZERO64))
    before = tree(ready)

    with pytest.raises(StoreError, match="the source changed since kblam challenge new; run it again"):
        review_write.put_record(ready.cfg, staged)

    assert tree(ready) == before


def test_a_first_put_refuses_a_basis_entry_that_does_not_resolve(ready):
    data = sc_data(basis=[basis_entry(EVIDENCE, ZERO64, role="missing-support")])
    staged = stage(ready, data)

    with pytest.raises(StoreError, match=r"source-challenge-0002: basis\[0\] evidence/2026-09-22-ratio/README.md does "
                                        r"not resolve to the recorded bytes"):
        review_write.put_record(ready.cfg, staged)


def test_a_first_put_allows_narrowing_the_assertion(ready):
    data = sc_data(spec={"sha256": None, "occurrence": None, "lines": [3, 3], "text": WORD})
    staged = stage(ready, data, captured=captured_lines(2, 3))

    result = review_write.put_record(ready.cfg, staged)

    assert result.ok, [issue.message for issue in result.issues]
    written = installed_data(ready, "source-challenge-0002")["source"]["assertion"]
    assert (written["lines"], written["text"], written["occurrence"]) == ([3, 3], WORD, 1)


def test_a_first_put_refuses_narrowing_outside_the_captured_lines(ready):
    data = sc_data(spec={"lines": [2, 2], "text": "Row 101: bytes 0x3A 0x3B", "sha256": None,
                         "occurrence": None})
    staged = stage(ready, data, captured=captured_lines(3, 3))

    with pytest.raises(StoreError, match="outside the captured lines 3-3"):
        review_write.put_record(ready.cfg, staged)


def test_a_first_put_refuses_assertion_text_outside_the_captured_text(ready):
    # the assertion cites line 3, which the receipt captured, but quotes line 2: a replacement, not a
    # narrowing of the captured text
    data = sc_data(spec={"lines": [3, 3], "text": "Row 101: bytes 0x3A 0x3B", "sha256": None,
                         "occurrence": None})
    staged = stage(ready, data, captured=captured_lines(3, 3))

    with pytest.raises(StoreError, match="not part of the lines kblam challenge new captured"):
        review_write.put_record(ready.cfg, staged)


def test_a_first_put_fills_a_basis_entrys_hash_and_pin(ready):
    entry = basis_entry(EVIDENCE, None, role="missing-support")
    data = sc_data(basis=[basis_entry(), entry])
    staged = stage(ready, data)

    result = review_write.put_record(ready.cfg, staged)

    assert result.ok, [issue.message for issue in result.issues]
    written = installed_data(ready, "source-challenge-0002")["basis"][1]
    assert written["sha256"] == sha256_hex((ready.root / EVIDENCE).read_bytes())
    assert (written["repo"], written["commit"], written["blob"]) == (None, None, None)


def test_a_first_put_gives_a_basis_entry_on_the_source_the_sources_hash_and_no_pin(ready):
    data = sc_data(basis=[basis_entry(TRACE, None)])
    staged = stage(ready, data)

    result = review_write.put_record(ready.cfg, staged)

    assert result.ok, [issue.message for issue in result.issues]
    written = installed_data(ready, "source-challenge-0002")["basis"][0]
    assert written["sha256"] == SHA
    assert (written["repo"], written["commit"], written["blob"]) == (None, None, None)


def test_a_first_put_of_a_task_is_refused_after_the_finding_changed(ready):
    data = ct_data(ready, rec_id="claim-task-0001")
    staged = stage(ready, data)
    ready.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels, re-measured.",
              body=excerpt_body(("3-3", WORD)))

    with pytest.raises(StoreError, match=f"{FINDING} changed since kblam task new bound claim-task-0001 to it; "
                                        f"reread it and run kblam task new again"):
        review_write.put_record(ready.cfg, staged)


def test_a_first_put_of_a_use_carries_the_receipts_bindings(ready):
    data = cu_data(ready)
    staged = stage(ready, data)

    result = review_write.put_record(ready.cfg, staged)

    assert result.ok, [issue.message for issue in result.issues]
    written = installed_data(ready, "checked-use-0001")
    assert written["citation"]["ordinal"] == 1
    assert written["challenge_bind"] == digest_of(ready, "source-challenge-0001")


# --- put: over an installed record ----------------------------------------------------------------


def test_a_put_over_an_installed_record_needs_a_current_edit_base(ready):
    staged = stage_edit(ready, "source-challenge-0001")
    receipts.write_edit_base(ready.cfg, "source-challenge-0001", ZERO64)

    with pytest.raises(StoreError, match="source-challenge-0001 changed since your edit; run kblam challenge edit "
                                        "source-challenge-0001 again"):
        review_write.put_record(ready.cfg, staged)

    assert staged.exists()
    assert receipts.read_edit_base(ready.cfg, "source-challenge-0001") == ZERO64


def test_a_put_over_a_record_that_is_not_open_is_refused(ready, source_repo):
    install(ready, "source-challenge", with_decision("source-challenge", sc_data(source_repo, rec_id="source-challenge-0001"), "confirmed"))
    staged = stage_edit(ready, "source-challenge-0001")

    with pytest.raises(StoreError, match="source-challenge-0001 is confirmed; only an open challenge can be edited"):
        review_write.put_record(ready.cfg, staged)


def test_a_put_over_a_record_refuses_a_change_to_a_field_that_is_not_free(ready):
    staged = stage_edit(ready, "source-challenge-0001", lambda data: data["source"].update({"sha256": ZERO64}))

    with pytest.raises(StoreError, match="source is not a free field"):
        review_write.put_record(ready.cfg, staged)

    assert staged.exists()
    assert receipts.read_edit_base(ready.cfg, "source-challenge-0001") == \
        sha256_hex(record_path(ready, "source-challenge-0001").read_bytes())


def test_a_put_over_a_record_accepts_a_free_field_change(ready, source_repo):
    before = record_path(ready, "source-challenge-0001").read_bytes()
    staged = stage_edit(ready, "source-challenge-0001",
                        lambda data: data.update({"proposition": "Narrowed after rereading row 102"}))

    result = review_write.put_record(ready.cfg, staged)

    assert result.ok, [issue.message for issue in result.issues]
    assert record_path(ready, "source-challenge-0001").read_bytes() != before
    assert installed_data(ready, "source-challenge-0001")["proposition"] == "Narrowed after rereading row 102"
    assert installed_data(ready, "source-challenge-0001")["source"]["sha256"] == SHA
    assert not staged.exists()
    assert receipts.read_edit_base(ready.cfg, "source-challenge-0001") is None
    assert blocking(ready) == []


def test_a_put_over_a_record_refuses_a_change_to_the_status(ready):
    staged = stage_edit(ready, "source-challenge-0001", lambda data: data.update({"status": "confirmed"}))
    before = tree(ready)

    with pytest.raises(StoreError, match="source-challenge-0001: status is not a free field and changes only through "
                                        "kblam review decide or kblam review rebind"):
        review_write.put_record(ready.cfg, staged)

    assert tree(ready) == before


def test_a_put_over_a_record_refuses_a_change_to_the_decisions(ready):
    decision = {"date": TODAY, "by": DECIDER, "status": "open", "reason": "hand-written", "evidence": [],
                "bind": ZERO64}
    staged = stage_edit(ready, "source-challenge-0001", lambda data: data.update({"decisions": [decision]}))

    with pytest.raises(StoreError, match="source-challenge-0001: decisions is not a free field and changes only "
                                        "through kblam review decide or kblam review rebind"):
        review_write.put_record(ready.cfg, staged)

    install(ready, "claim-task", ct_data(ready))
    staged = stage_edit(ready, "claim-task-0001", lambda data: data.update({"decisions": [decision]}))

    with pytest.raises(StoreError, match="claim-task-0001: decisions is not a free field"):
        review_write.put_record(ready.cfg, staged)


def test_a_put_over_a_use_says_a_changed_use_is_a_new_use(ready, source_repo):
    confirmed(ready, source_repo)
    install(ready, "checked-use", cu_data(ready))
    staged = stage_edit(ready, "checked-use-0001")
    receipts.write_edit_base(ready.cfg, "checked-use-0001", ZERO64)

    with pytest.raises(StoreError) as caught:
        review_write.put_record(ready.cfg, staged)

    assert "checked-use-0001 changed since your edit" in str(caught.value)
    assert "kblam use edit" not in str(caught.value)
    assert "kblam use review" in str(caught.value)


def test_a_put_over_a_use_refuses_a_field_that_is_not_free_without_naming_an_edit_command(ready,
                                                                                         source_repo):
    confirmed(ready, source_repo)
    install(ready, "checked-use", cu_data(ready))
    staged = stage_edit(ready, "checked-use-0001", lambda data: data["citation"].update({"ordinal": 2}))

    with pytest.raises(StoreError) as caught:
        review_write.put_record(ready.cfg, staged)

    assert "checked-use-0001: citation is not a free field" in str(caught.value)
    assert "kblam use edit" not in str(caught.value)


def test_a_put_over_a_record_fills_a_new_basis_entrys_hash(ready):
    entry = basis_entry(EVIDENCE, None, role="missing-support")
    staged = stage_edit(ready, "source-challenge-0001", lambda data: data["basis"].append(entry))

    result = review_write.put_record(ready.cfg, staged)

    assert result.ok, [issue.message for issue in result.issues]
    written = installed_data(ready, "source-challenge-0001")["basis"][1]
    assert written["sha256"] == sha256_hex((ready.root / EVIDENCE).read_bytes())
    assert (written["repo"], written["commit"], written["blob"]) == (None, None, None)


def test_a_put_over_a_record_refuses_a_changed_basis_entry_that_does_not_resolve(ready):
    def change(data):
        data["basis"][0].update({"locator": "row 103: printed byte values", "sha256": ZERO64})

    staged = stage_edit(ready, "source-challenge-0001", change)

    with pytest.raises(StoreError, match=r"source-challenge-0001: basis\[0\].*does not resolve to the recorded bytes"):
        review_write.put_record(ready.cfg, staged)


def test_a_put_over_a_record_does_not_re_verify_an_installed_basis_entry(ready):
    stale = basis_entry(EVIDENCE, ZERO64, role="missing-support")
    install(ready, "source-challenge", sc_data(rec_id="source-challenge-0001", basis=[basis_entry(), stale]))
    staged = stage_edit(ready, "source-challenge-0001",
                        lambda data: data.update({"proposition": "Narrowed after rereading row 102"}))

    result = review_write.put_record(ready.cfg, staged)

    assert result.ok, [issue.message for issue in result.issues]
    assert installed_data(ready, "source-challenge-0001")["basis"][1]["sha256"] == ZERO64


def test_a_put_journals_its_multi_file_write(ready, monkeypatch):
    begun = []
    real_begin = journal.begin
    monkeypatch.setattr(journal, "begin",
                        lambda cfg, command, paths: (begun.append(list(paths)),
                                                     real_begin(cfg, command, paths))[1])

    result = review_write.put_record(ready.cfg, stage(ready, sc_data()))

    assert result.ok
    assert begun == [[f"{REVIEW}/challenges/source-challenge-0002.yaml", f"{REVIEW}/INDEX.md",
                      ".kblam/review-ids"]]
    assert not (ready.root / ".kblam/journal.json").exists()


def test_a_put_advances_a_format_2_tree_hash(ready):
    mark_clean(ready)
    before = (ready.root / ".kblam/tree.hash").read_bytes()

    result = review_write.put_record(ready.cfg, stage(ready, sc_data()))

    assert result.recorded is True
    after = (ready.root / ".kblam/tree.hash").read_bytes()
    assert after != before
    assert treehash.parse_line(after.decode("utf-8"))[0:2] == (2, REVIEW)


def test_a_root_change_refuses_every_write(ready):
    install(ready, "claim-task", ct_data(ready))          # so rebind's refusal is the root change, not a missing record
    (ready.root / ".kblam/tree.hash").write_bytes(format_line("other-review", ZERO64).encode("utf-8"))
    staged = stage(ready, sc_data())
    message = "the review root changed from other-review to research-review in kblam.toml"

    with pytest.raises(StoreError, match=message):
        review_write.put_record(ready.cfg, staged)
    with pytest.raises(StoreError, match=message):
        review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", DECIDER, "reviewed", digest_of(ready, "source-challenge-0001"), [])
    with pytest.raises(StoreError, match=message):
        review_write.rebind(ready.cfg, "claim-task-0001", DECIDER, "reviewed", digest_of(ready, "claim-task-0001"), [])
    with pytest.raises(StoreError, match=message):
        review_write.challenge_pin(ready.cfg, "source-challenge-0001", digest_of(ready, "source-challenge-0001"))
    with pytest.raises(StoreError, match=message):
        review_write.regenerate_review_index(ready.cfg)


# --- put: a refusal names the staged file the author is editing -----------------------------------


def staged_line(staged, key: str) -> int:
    """The line `key` starts on in the staged file's own text, 1-based (0 when it is not a key there)."""
    for number, line in enumerate(staged.read_text(encoding="utf-8").split("\n"), start=1):
        if line.startswith(f"{key}:"):
            return number
    return 0


def test_a_refused_put_reports_a_schema_error_at_the_staged_file(ready):
    """The record is not at its canonical path yet, so a refusal names the staged file `kblam challenge
    new` printed, at the line the staged file holds there (D33)."""
    staged = stage(ready, sc_data(rec_id="source-challenge-0002", proposition=""))

    result = review_write.put_record(ready.cfg, staged)

    assert not result.ok
    assert [issue.message for issue in result.issues] == ["proposition: required"]
    assert result.issues[0].path == ".kblam/review-staging/source-challenge-0002.yaml"
    assert result.issues[0].line == staged_line(staged, "proposition")
    assert not record_path(ready, "source-challenge-0002").exists()


def test_a_refused_put_maps_the_line_to_the_staged_file_when_put_filled_fields_in(ready, source_repo):
    """put computes an assertion's sha256 and occurrence and writes them into the record, so the bytes it
    would write can start a key two lines below where the staged file starts it. The refusal is still
    reported at the staged file's own line."""
    filled = sc_data(source_repo, rec_id="source-challenge-0002")
    for key in ("sha256", "occurrence"):
        del filled["source"]["assertion"][key]            # put writes both back, two lines longer
    assert review_write.put_record(ready.cfg, stage(ready, filled)).ok

    data = sc_data(source_repo, rec_id="source-challenge-0003", linked_findings=["F-0009"])
    for key in ("sha256", "occurrence"):
        del data["source"]["assertion"][key]
    staged = stage(ready, data)

    result = review_write.put_record(ready.cfg, staged)

    assert [issue.code for issue in result.issues] == ["K13"]
    issue = result.issues[0]
    assert issue.message.startswith("linked_findings[0] names F-0009, which is no finding in findings/")
    assert issue.path == ".kblam/review-staging/source-challenge-0003.yaml"
    assert issue.line == staged_line(staged, "linked_findings")
    assert issue.line == record(ready, "source-challenge-0002").key_line("linked_findings") - 2


# --- decide --------------------------------------------------------------------------------------


def test_decide_appends_a_decision_and_sets_the_status(ready):
    result = review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", DECIDER,
                                 "Row 102's printed values cannot be equal", digest_of(ready, "source-challenge-0001"),
                                 ["observed:evidence/2026-09-22-ratio/README.md:section 2"])

    assert result.ok, [issue.message for issue in result.issues]
    assert result.status == "confirmed" and result.digest == digest_of(ready, "source-challenge-0001")
    written = installed_data(ready, "source-challenge-0001")
    assert written["status"] == "confirmed"
    decision = written["decisions"][0]
    assert (decision["date"], decision["by"], decision["status"]) == (TODAY.isoformat(), DECIDER,
                                                                     "confirmed")
    assert decision["reason"].startswith("Row 102's")
    assert decision["bind"] == digest_of(ready, "source-challenge-0001")
    assert list(decision["evidence"][0]) == [*records.REF_KEYS, "locator", "provenance"]
    assert decision["evidence"][0]["locator"] == "section 2"
    assert decision["evidence"][0]["sha256"] == \
        sha256_hex((ready.root / EVIDENCE).read_bytes())


def test_decide_refuses_a_transition_the_table_does_not_allow(ready):
    review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", DECIDER, "reviewed", digest_of(ready, "source-challenge-0001"), [])

    with pytest.raises(StoreError, match="a challenge cannot be decided to its own status confirmed"):
        review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", DECIDER, "again",
                            digest_of(ready, "source-challenge-0001"), [])
    with pytest.raises(StoreError, match="a confirmed challenge is never reopened"):
        review_write.decide(ready.cfg, "source-challenge-0001", "open", DECIDER, "reopen",
                            digest_of(ready, "source-challenge-0001"), [])


def test_decide_refuses_a_decision_that_keeps_the_status(ready):
    install(ready, "claim-task", ct_data(ready))

    with pytest.raises(StoreError, match="claim-task-0001 is already open; a decision that keeps the status "
                                        "belongs to kblam review rebind claim-task-0001 --by NAME --reason TEXT "
                                        "--expect D"):
        review_write.decide(ready.cfg, "claim-task-0001", "open", DECIDER, "reviewed",
                            digest_of(ready, "claim-task-0001"), [])

    install(ready, "claim-task", with_decision("claim-task", ct_data(ready), "confirmed"))

    with pytest.raises(StoreError, match="claim-task-0001 is already confirmed"):
        review_write.decide(ready.cfg, "claim-task-0001", "confirmed", DECIDER, "again",
                            digest_of(ready, "claim-task-0001"), [])


def test_decide_refuses_a_decision_that_is_not_independent(ready):
    with pytest.raises(StoreError, match="reviewer-a is source-challenge-0001's creator; a closing decision needs "
                                        "someone else"):
        review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", CREATOR, "reviewed",
                            digest_of(ready, "source-challenge-0001"), [])


def test_decide_confirmed_needs_a_pinned_source_and_primary_basis(ready):
    install(ready, "source-challenge", sc_data(pin=False, basis=[basis_entry(EVIDENCE, SHA, role="missing-support",
                                                               provenance="inferred")]))

    result = review_write.decide(ready.cfg, "source-challenge-0002", "confirmed", DECIDER, "reviewed",
                                 digest_of(ready, "source-challenge-0002"), [])

    assert not result.ok and not any(issue.owner != "source-challenge-0002" for issue in result.issues)
    messages = " ".join(issue.message for issue in result.issues)
    assert "confirmation needs a pinned source" in messages
    assert "confirmation needs a basis entry whose provenance" in messages
    assert installed_data(ready, "source-challenge-0002")["status"] == "open"


def test_decide_confirmed_lists_every_newly_affected_finding_by_number(ready):
    ready.add("F-0002", "ratio", "The ratio is 1.0017 across 2048 pixels, re-measured once.",
              body=excerpt_body(("3-3", WORD)))
    ready.add("F-0010", "ratio", "The ratio is 1.0017 across 2048 pixels, re-measured twice.",
              body=excerpt_body(("3-3", WORD)))

    result = review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", DECIDER, "reviewed",
                                 digest_of(ready, "source-challenge-0001"), [])

    assert result.ok, [issue.message for issue in result.issues]
    assert result.newly_affected == [FINDING, "F-0002", "F-0010"]


def test_decide_confirmed_lists_newly_affected_findings(ready):
    result = review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", DECIDER, "reviewed",
                                 digest_of(ready, "source-challenge-0001"), [])

    assert result.ok, [issue.message for issue in result.issues]
    assert result.newly_affected == [FINDING]
    assert [(issue.code, issue.owner) for issue in result.remaining] == [("K14", FINDING)]
    assert [issue.code for issue in blocking(ready)] == ["K14"]


def test_decide_refuses_approving_a_use_that_is_not_current(ready):
    install(ready, "checked-use", cu_data(ready))

    with pytest.raises(StoreError, match="approving checked-use-0001 needs it to be current"):
        review_write.decide(ready.cfg, "checked-use-0001", "approved", DECIDER, "reviewed",
                            digest_of(ready, "checked-use-0001"), [])


def test_decide_approves_a_current_use(ready, source_repo):
    confirmed(ready, source_repo)
    install(ready, "checked-use", cu_data(ready))
    mark_clean(ready)

    result = review_write.decide(ready.cfg, "checked-use-0001", "approved", DECIDER,
                                 "The excerpt is used only for the raw byte values",
                                 digest_of(ready, "checked-use-0001"), [])

    assert result.ok, [issue.message for issue in result.issues]
    assert installed_data(ready, "checked-use-0001")["status"] == "approved"
    assert [issue for issue in blocking(ready) if issue.code == "K14"] == []


def test_decide_confirmed_needs_primary_evidence_from_evidence_flag(ready, source_repo):
    install(ready, "claim-task", ct_data(ready))

    result = review_write.decide(ready.cfg, "claim-task-0001", "confirmed", DECIDER, "the task replicated",
                                 digest_of(ready, "claim-task-0001"), [])
    assert not result.ok
    assert "has no evidence entry whose provenance" in " ".join(i.message for i in result.issues)

    result = review_write.decide(ready.cfg, "claim-task-0001", "confirmed", DECIDER, "the task replicated",
                                 digest_of(ready, "claim-task-0001"),
                                 [f"observed:{EVIDENCE}:section 2"])
    assert result.ok, [issue.message for issue in result.issues]
    assert installed_data(ready, "claim-task-0001")["status"] == "confirmed"
    assert blocking(ready) == []


def test_decide_checks_expect(ready):
    digest = digest_of(ready, "source-challenge-0001")

    with pytest.raises(StoreError, match="at least 12 lowercase hex digits"):
        review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", DECIDER, "reviewed", digest[:11], [])
    with pytest.raises(StoreError, match="source-challenge-0001 changed since you inspected it; show it again: kblam "
                                        "challenge show source-challenge-0001"):
        review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", DECIDER, "reviewed", "0" * 12, [])

    result = review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", DECIDER, "reviewed", digest[:12], [])
    assert result.ok, [issue.message for issue in result.issues]


def test_errors_owned_by_other_records_do_not_refuse_but_remain(ready):
    install(ready, "source-challenge", with_decision(
        "source-challenge", sc_data(pin=False, basis=[basis_entry(EVIDENCE, SHA, provenance="inferred")]),
        "confirmed"), index=False)

    result = review_write.decide(ready.cfg, "source-challenge-0001", "confirmed", DECIDER, "reviewed",
                                 digest_of(ready, "source-challenge-0001"), [])

    assert result.ok, [issue.message for issue in result.issues]
    assert {issue.owner for issue in result.remaining} == {"source-challenge-0002", FINDING}
    assert installed_data(ready, "source-challenge-0001")["status"] == "confirmed"


# --- rebind --------------------------------------------------------------------------------------


def test_rebind_refuses_a_challenge(ready):
    with pytest.raises(StoreError, match="a challenge has no rebind; a changed assertion or judgement "
                                        "is a new challenge"):
        review_write.rebind(ready.cfg, "source-challenge-0001", DECIDER, "reviewed", digest_of(ready, "source-challenge-0001"), [])


def test_rebind_refuses_a_stale_record(ready):
    install(ready, "claim-task", with_decision("claim-task", ct_data(ready), "stale"))

    with pytest.raises(StoreError, match="claim-task-0001 is stale; a retired record stays as audit data"):
        review_write.rebind(ready.cfg, "claim-task-0001", DECIDER, "reviewed", digest_of(ready, "claim-task-0001"), [])


def test_rebind_restores_a_tasks_binding_after_the_finding_changed(ready):
    install(ready, "claim-task", ct_data(ready))
    ready.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels, re-measured.",
              body=excerpt_body(("3-3", WORD)))
    assert {issue.code for issue in blocking(ready)} == {"K15"}

    result = review_write.rebind(ready.cfg, "claim-task-0001", DECIDER, "rechecked the revised finding",
                                 digest_of(ready, "claim-task-0001"), [])

    assert result.ok, [issue.message for issue in result.issues]
    written = installed_data(ready, "claim-task-0001")
    assert written["claim_fingerprint"] == finding_binding(ready)[0]
    assert written["base_file_sha256"] == finding_binding(ready)[1]
    assert written["status"] == "open" and written["decisions"][-1]["bind"] == result.digest
    assert blocking(ready) == []


def test_rebind_keeps_a_uses_excerpt_by_tag_sha256_when_the_ordinal_moved(ready, source_repo):
    confirmed(ready, source_repo)
    install(ready, "checked-use", cu_data(ready))
    cited = installed_data(ready, "checked-use-0001")["citation"]["tag_sha256"]
    ready.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.",
              body=excerpt_body(("2-2", "Row 101: bytes 0x3A 0x3B"), ("3-3", WORD)))

    result = review_write.rebind(ready.cfg, "checked-use-0001", DECIDER, "rechecked the excerpt",
                                 digest_of(ready, "checked-use-0001"), [])

    assert result.ok, [issue.message for issue in result.issues]
    citation = installed_data(ready, "checked-use-0001")["citation"]
    assert citation["ordinal"] == 2 and citation["tag_sha256"] == cited
    assert citation["path"] == TRACE and citation["range"] == [3, 3]


def test_rebind_refuses_when_no_excerpt_carries_the_cited_tag(ready, source_repo):
    confirmed(ready, source_repo)
    install(ready, "checked-use", cu_data(ready, citation={**citation_of(ready), "tag_sha256": ZERO64}))
    ready.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels, re-measured.",
              body=excerpt_body(("3-3", WORD)))

    with pytest.raises(StoreError, match="stage a new use: kblam use review source-challenge-0001 F-0001"):
        review_write.rebind(ready.cfg, "checked-use-0001", DECIDER, "rechecked", digest_of(ready, "checked-use-0001"), [])


def test_rebind_refuses_when_several_excerpts_carry_the_cited_tag(ready, source_repo):
    confirmed(ready, source_repo)
    install(ready, "checked-use", cu_data(ready))
    ready.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.",
              body=excerpt_body(("3-3", WORD), ("3-3", WORD)))
    ready.add(FINDING, "ratio", "The ratio is 1.0017 across 2048 pixels.",
              body=excerpt_body(("2-2", "Row 101: bytes 0x3A 0x3B"), ("3-3", WORD), ("3-3", WORD)))

    with pytest.raises(StoreError, match="2 excerpts carry its tag_sha256"):
        review_write.rebind(ready.cfg, "checked-use-0001", DECIDER, "rechecked", digest_of(ready, "checked-use-0001"), [])


def test_rebind_keeps_a_closed_tasks_status_and_needs_its_evidence_again(ready, source_repo):
    install(ready, "claim-task", with_decision("claim-task", ct_data(ready), "confirmed"))

    result = review_write.rebind(ready.cfg, "claim-task-0001", DECIDER, "rechecked the finding",
                                 digest_of(ready, "claim-task-0001"), [])

    assert not result.ok
    assert {issue.owner for issue in result.issues} == {"claim-task-0001"}
    assert "has no evidence entry whose provenance" in " ".join(issue.message for issue in result.issues)

    result = review_write.rebind(ready.cfg, "claim-task-0001", DECIDER, "rechecked the finding",
                                 digest_of(ready, "claim-task-0001"), [f"observed:{EVIDENCE}:section 2"])

    assert result.ok, [issue.message for issue in result.issues]
    written = installed_data(ready, "claim-task-0001")
    assert written["status"] == "confirmed"
    assert written["decisions"][-1]["evidence"][0]["locator"] == "section 2"
    assert written["decisions"][-1]["bind"] == result.digest


def test_rebind_refuses_a_use_whose_challenge_is_not_confirmed(ready):
    install(ready, "checked-use", cu_data(ready))

    with pytest.raises(StoreError, match="the challenge source-challenge-0001 is open, not confirmed; a use binds a "
                                        "confirmed challenge with an available source"):
        review_write.rebind(ready.cfg, "checked-use-0001", DECIDER, "rechecked the excerpt",
                            digest_of(ready, "checked-use-0001"), [])


def test_rebind_refuses_a_use_whose_challenges_source_is_unavailable(ready, source_repo):
    install(ready, "source-challenge", with_decision("source-challenge", sc_data(source_repo, rec_id="source-challenge-0001", sha=ZERO64),
                                       "confirmed"))
    install(ready, "checked-use", cu_data(ready))

    with pytest.raises(StoreError) as caught:
        review_write.rebind(ready.cfg, "checked-use-0001", DECIDER, "rechecked the excerpt",
                            digest_of(ready, "checked-use-0001"), [])

    assert str(caught.value) == (
        "the challenge source-challenge-0001's source is not available; restore the pinned version or the working "
        "file, then run kblam review rebind checked-use-0001 --by NAME --reason TEXT --expect D, or stage a new "
        "use (kblam use review source-challenge-… F-… <excerpt-ordinal> --by NAME --proponent NAME)")
    assert "challenge pin" not in str(caught.value)

    confirmed(ready, source_repo)                         # the pinned version is back in place
    assert run_rebind(ready, str(caught.value), digest_of(ready, "checked-use-0001")) == 0
    assert installed_data(ready, "checked-use-0001")["challenge_bind"] == digest_of(ready, "source-challenge-0001")


def test_rebind_refuses_a_use_whose_challenge_is_not_a_record(ready, source_repo):
    data = cu_data(ready)
    data["challenge"] = "source-challenge-0009"                          # no such record in the review root
    install(ready, "checked-use", data)

    with pytest.raises(StoreError) as caught:
        review_write.rebind(ready.cfg, "checked-use-0001", DECIDER, "rechecked the excerpt",
                            digest_of(ready, "checked-use-0001"), [])

    assert str(caught.value) == (
        "the challenge source-challenge-0009 is not a record in research-review/; restore it from git, then run "
        "kblam review rebind checked-use-0001 --by NAME --reason TEXT --expect D")

    install(ready, "source-challenge", with_decision("source-challenge", sc_data(source_repo, rec_id="source-challenge-0009"), "confirmed"))
    assert run_rebind(ready, str(caught.value), digest_of(ready, "checked-use-0001")) == 0
    assert installed_data(ready, "checked-use-0001")["challenge_bind"] == digest_of(ready, "source-challenge-0009")


def test_a_task_kept_closed_is_told_to_rebind_with_its_evidence(ready, source_repo):
    """D37(d): the rebind a diagnostic prints is the whole command (`--by` alone is a usage error), and a
    task it keeps confirmed cites its primary evidence once more. The printed command runs."""
    install(ready, "claim-task", with_decision("claim-task", ct_data(ready), "confirmed"))
    path, raw = unreadable(ready)

    with pytest.raises(StoreError) as caught:
        review_write.rebind(ready.cfg, "claim-task-0001", DECIDER, "rechecked the finding",
                            digest_of(ready, "claim-task-0001"), [])

    assert str(caught.value) == (
        "F-0001 is not one readable finding in findings/; run kblam validate and fix it, then run "
        "kblam review rebind claim-task-0001 --by NAME --reason TEXT --expect D "
        "--evidence PROVENANCE:PATH:LOCATOR")
    assert [word for word in rebind_argv(str(caught.value)) if word in PLACEHOLDERS] == \
        ["NAME", "TEXT", "D", "PROVENANCE:PATH:LOCATOR"]

    path.write_bytes(raw)
    assert run_rebind(ready, str(caught.value), digest_of(ready, "claim-task-0001")) == 0
    written = installed_data(ready, "claim-task-0001")
    assert written["status"] == "confirmed" and len(written["decisions"]) == 2


def test_a_reopening_rebind_is_told_to_reopen_and_runs(ready):
    """A rebind that reopens prints --reopen and no --evidence: it sets the record open, which closes
    nothing, so nothing has to be cited again. The printed command runs and leaves the task open."""
    install(ready, "claim-task", with_decision("claim-task", ct_data(ready), "confirmed",
                                       evidence=[review_write.parse_evidence(
                                           ready.cfg, f"observed:{EVIDENCE}:section 2")]))
    assert blocking(ready) == []                          # a valid closed task, its evidence cited
    path, raw = unreadable(ready)

    with pytest.raises(StoreError) as caught:
        review_write.rebind(ready.cfg, "claim-task-0001", DECIDER, "the question needs another look",
                            digest_of(ready, "claim-task-0001"), [], reopen=True)

    assert str(caught.value) == (
        "F-0001 is not one readable finding in findings/; run kblam validate and fix it, then run "
        "kblam review rebind claim-task-0001 --by NAME --reason TEXT --expect D --reopen")
    argv = rebind_argv(str(caught.value))
    assert [word for word in argv if word in PLACEHOLDERS] == ["NAME", "TEXT", "D"]
    assert argv[-1] == "--reopen"

    path.write_bytes(raw)
    assert run_rebind(ready, str(caught.value), digest_of(ready, "claim-task-0001")) == 0
    written = installed_data(ready, "claim-task-0001")
    assert written["status"] == "open" and len(written["decisions"]) == 2


def test_an_open_use_is_told_to_rebind_without_evidence(ready, source_repo):
    """A use's rebind keeps no evidence: only a task the rebind leaves confirmed or not_reproduced cites
    its primary evidence again. The printed command runs."""
    confirmed(ready, source_repo)
    install(ready, "checked-use", cu_data(ready))
    path, raw = unreadable(ready)

    with pytest.raises(StoreError) as caught:
        review_write.rebind(ready.cfg, "checked-use-0001", DECIDER, "rechecked the excerpt",
                            digest_of(ready, "checked-use-0001"), [])

    assert str(caught.value) == (
        "F-0001 is not one readable finding in findings/; run kblam validate and fix it, then run "
        "kblam review rebind checked-use-0001 --by NAME --reason TEXT --expect D")
    assert [word for word in rebind_argv(str(caught.value)) if word in PLACEHOLDERS] == \
        ["NAME", "TEXT", "D"]

    path.write_bytes(raw)
    assert run_rebind(ready, str(caught.value), digest_of(ready, "checked-use-0001")) == 0
    written = installed_data(ready, "checked-use-0001")
    assert written["status"] == "open" and len(written["decisions"]) == 1


def test_rebind_reopen_sets_open_without_independence(ready, source_repo):
    confirmed(ready, source_repo)
    install(ready, "checked-use", with_decision("checked-use", cu_data(ready), "approved", by=DECIDER))
    proponent = installed_data(ready, "checked-use-0001")["proponent"]

    result = review_write.rebind(ready.cfg, "checked-use-0001", proponent, "the excerpt needs a second look",
                                 digest_of(ready, "checked-use-0001"), [], reopen=True)

    assert result.ok, [issue.message for issue in result.issues]
    written = installed_data(ready, "checked-use-0001")
    assert written["status"] == "open" and written["decisions"][-1]["status"] == "open"


# --- pin -----------------------------------------------------------------------------------------


def test_pin_sets_the_git_pin_of_a_clean_source(ready, source_repo):
    install(ready, "source-challenge", sc_data(pin=False))

    result = review_write.challenge_pin(ready.cfg, "source-challenge-0002", digest_of(ready, "source-challenge-0002"))

    assert result.ok, [issue.message for issue in result.issues]
    source = installed_data(ready, "source-challenge-0002")["source"]
    assert (source["repo"], source["commit"], source["blob"]) == \
        (SOURCE_REPO, source_repo.head(), source_repo.blob(TRACE_PATH))
    assert installed_data(ready, "source-challenge-0002")["decisions"] == []
    assert installed_data(ready, "source-challenge-0002")["status"] == "open"
    assert blocking(ready) == []


def test_pin_refuses_a_dirty_source_and_points_at_a_snapshot(ready, source_repo):
    source_repo.write(TRACE_PATH, TRACE_TEXT.replace("Row 103", "Row 103 (edited)"))
    dirty = (source_repo.root / TRACE_PATH).read_bytes()
    install(ready, "source-challenge", sc_data(pin=False, sha=sha256_hex(dirty)))

    with pytest.raises(StoreError, match="pin a copy with --snapshot PATH"):
        review_write.challenge_pin(ready.cfg, "source-challenge-0002", digest_of(ready, "source-challenge-0002"))


def test_pin_accepts_a_snapshot_with_the_same_bytes(ready, source_repo):
    ready.write("snapshots/full-scan-trace.md", TRACE_TEXT)
    install(ready, "source-challenge", sc_data(pin=False))

    result = review_write.challenge_pin(ready.cfg, "source-challenge-0002", digest_of(ready, "source-challenge-0002"),
                                        "snapshots/full-scan-trace.md")

    assert result.ok, [issue.message for issue in result.issues]
    source = installed_data(ready, "source-challenge-0002")["source"]
    assert source["snapshot"] == "snapshots/full-scan-trace.md"
    assert (source["repo"], source["commit"], source["blob"]) == (None, None, None)
    assert blocking(ready) == []


def test_pin_refuses_the_source_itself_as_a_snapshot(ready, source_repo):
    install(ready, "source-challenge", sc_data(pin=False))
    before = tree(ready)

    with pytest.raises(StoreError, match="a snapshot is a copy of the bytes, not the source itself"):
        review_write.challenge_pin(ready.cfg, "source-challenge-0002", digest_of(ready, "source-challenge-0002"), TRACE)

    assert tree(ready) == before


def test_pin_refuses_a_snapshot_inside_kblams_own_state(ready):
    ready.write(".kblam/snapshots/trace.md", TRACE_TEXT)
    install(ready, "source-challenge", sc_data(pin=False))

    with pytest.raises(StoreError, match=r"\.kblam/ is kblam's state"):
        review_write.challenge_pin(ready.cfg, "source-challenge-0002", digest_of(ready, "source-challenge-0002"),
                                   ".kblam/snapshots/trace.md")


def test_pin_refuses_a_snapshot_inside_the_findings_or_the_review_root(ready):
    install(ready, "source-challenge", sc_data(pin=False))

    with pytest.raises(StoreError, match="findings/ holds the findings"):
        review_write.challenge_pin(ready.cfg, "source-challenge-0002", digest_of(ready, "source-challenge-0002"),
                                   "findings/snapshots/trace.md")
    with pytest.raises(StoreError, match=f"the review root {REVIEW}/ holds the records"):
        review_write.challenge_pin(ready.cfg, "source-challenge-0002", digest_of(ready, "source-challenge-0002"),
                                   f"{REVIEW}/snapshots/trace.md")


def test_pin_refuses_a_snapshot_inside_a_history_folder(ready):
    """A `history_dirs` folder is a protected root too (paths.protected), so it is no place for the
    project-owned copy that stands in for a source's bytes (SPEC §5.2.2: a snapshot is a copy of the
    source, and §5.2.2's canonical key rules a history path out as a source)."""
    ready.write("history/snapshots/trace.md", TRACE_TEXT)
    install(ready, "source-challenge", sc_data(pin=False))

    with pytest.raises(StoreError, match="a history folder holds the KB's older versions"):
        review_write.challenge_pin(ready.cfg, "source-challenge-0002", digest_of(ready, "source-challenge-0002"),
                                   "history/snapshots/trace.md")


def test_pin_refuses_a_snapshot_inside_a_source_repository(ready, source_repo):
    source_repo.write("snapshots/trace.md", TRACE_TEXT)
    install(ready, "source-challenge", sc_data(pin=False))

    with pytest.raises(StoreError, match="is inside the Git worktree"):
        review_write.challenge_pin(ready.cfg, "source-challenge-0002", digest_of(ready, "source-challenge-0002"),
                                   f"{SOURCE_REPO}/snapshots/trace.md")


def test_pin_refuses_an_already_pinned_source(ready):
    with pytest.raises(StoreError, match="source-challenge-0001's source is already pinned; a pin is never replaced"):
        review_write.challenge_pin(ready.cfg, "source-challenge-0001", digest_of(ready, "source-challenge-0001"))


# --- the review index ----------------------------------------------------------------------------


def test_review_index_writes_a_byte_identical_index(ready):
    index = ready.root / REVIEW / "INDEX.md"
    index.unlink()

    assert review_write.regenerate_review_index(ready.cfg) is False   # format-1 tree.hash
    written = index.read_bytes()
    assert written == generate_review_index(load_view(ready.cfg))

    review_write.regenerate_review_index(ready.cfg)
    assert index.read_bytes() == written


def test_review_index_creates_the_registry_from_the_records_present(ready):
    install(ready, "source-challenge", sc_data())
    registry = ready.root / ".kblam/review-ids"
    assert not registry.exists()

    review_write.regenerate_review_index(ready.cfg)

    assert json.loads(registry.read_text()) == ["source-challenge-0001", "source-challenge-0002"]


def test_review_index_does_not_forget_a_deleted_record(ready):
    ready.write(".kblam/review-ids", json.dumps(["source-challenge-0001", "source-challenge-0009"]) + "\n")
    record_path(ready, "source-challenge-0001").unlink()

    review_write.regenerate_review_index(ready.cfg)

    assert json.loads((ready.root / ".kblam/review-ids").read_text()) == ["source-challenge-0001", "source-challenge-0009"]
    step = ("records are never deleted or renamed; git's last commit does not hold a file at {path}, so "
            "leave it as it is and tell the user")
    assert [issue.message for issue in blocking(ready)] == [
        "source-challenge-0001 is missing from research-review/; "
        + step.format(path="research-review/challenges/source-challenge-0001.yaml"),
        "source-challenge-0009 is missing from research-review/; "
        + step.format(path="research-review/challenges/source-challenge-0009.yaml"),
    ]
