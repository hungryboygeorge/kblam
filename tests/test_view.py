"""load_view: the review root's bytes beside the findings' (SPEC §5.1.6)."""

from __future__ import annotations

import os
import shutil

import pytest

from kblam import treehash
from kblam.rules import k8_stray_files, validate
from kblam.view import load_view

from conftest import record_text

CLAIM = ("The two sensor curve types agree to about 0.1% (median ratio 1.0017 on line 0), "
         "so they are not two analog gains.")


def _symlink(target: str, link, *, directory: bool = False) -> None:
    """A relative symlink, or a skip where the OS denies symlink creation (Windows without the
    privilege). The test is about what load_view does with a link, not about being able to make one."""
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create a symlink here")


def test_review_files_hold_every_file_under_the_review_root(kb):
    expected = {
        "research-review/INDEX.md": b"# Review index\n",
        "research-review/README": b"no extension, still a file\n",
        "research-review/challenges/SC-0001.yaml": record_text("SC").encode("utf-8"),
        "research-review/tasks/CT-0001.yaml": record_text("CT").encode("utf-8"),
        "research-review/uses/CU-0001.yaml": record_text("CU").encode("utf-8"),
        "research-review/notes/deep/scratch.bin": b"\x00\x01binary\xff",
    }
    for path, data in expected.items():
        kb.write(path, data)

    view = load_view(kb.cfg)
    assert view.review_files == expected
    assert view.review_symlinks == set()
    assert view.review_index_path == "research-review/INDEX.md"
    assert not [p for p in view.files if p.startswith("research-review/")]


def test_each_root_loads_without_the_other(kb):
    assert load_view(kb.cfg).review_files == {}
    kb.write("research-review/INDEX.md", b"# Review index\n")
    shutil.rmtree(kb.findings)
    view = load_view(kb.cfg)
    assert view.files == {}
    assert view.review_files == {"research-review/INDEX.md": b"# Review index\n"}


def test_records_are_the_kind_folder_yamls_in_path_order(kb):
    for kind, folder in (("SC", "challenges"), ("CT", "tasks"), ("CU", "uses")):
        kb.write(f"research-review/{folder}/{kind}-0001.yaml", record_text(kind))
    kb.write("research-review/challenges/notes.txt", "not a record\n")
    kb.write("research-review/challenges/nested/SC-0002.yaml", record_text("SC", "SC-0002"))
    kb.write("research-review/other/SC-0003.yaml", record_text("SC", "SC-0003"))
    kb.write("research-review/SC-0004.yaml", record_text("SC", "SC-0004"))

    view = load_view(kb.cfg)
    assert [(r.path, r.id, r.kind) for r in view.records] == [
        ("research-review/challenges/SC-0001.yaml", "SC-0001", "SC"),
        ("research-review/tasks/CT-0001.yaml", "CT-0001", "CT"),
        ("research-review/uses/CU-0001.yaml", "CU-0001", "CU"),
    ]
    for rec in view.records:
        assert rec.error is None
        assert rec.data["id"] == rec.id
        assert rec.raw == view.review_files[rec.path]
    assert view.records is view.records  # parsed once per view


def test_symlinks_under_the_review_root_are_flagged(kb):
    real = kb.write("research-review/challenges/SC-0001.yaml", record_text("SC"))
    kb.write("research-review/uses/CU-0001.yaml", record_text("CU"))
    kb.write("outside/loose.txt", "outside the review root\n")
    review = kb.root / "research-review"
    _symlink("SC-0001.yaml", review / "challenges" / "SC-0002.yaml")
    _symlink("nowhere.yaml", review / "uses" / "CU-0002.yaml")
    _symlink("../outside", review / "extra", directory=True)

    view = load_view(kb.cfg)
    assert view.review_symlinks == {
        "research-review/challenges/SC-0002.yaml",
        "research-review/uses/CU-0002.yaml",
        "research-review/extra",
    }
    # a link to a readable record still contributes its bytes; a dangling or directory link does not
    assert view.review_files["research-review/challenges/SC-0002.yaml"] == real.read_bytes()
    assert "research-review/uses/CU-0002.yaml" not in view.review_files
    assert not [p for p in view.review_files if p.startswith("research-review/extra")]
    assert [r.id for r in view.records] == ["SC-0001", "SC-0002", "CU-0001"]


def test_a_stray_review_file_is_no_k8_issue_and_leaves_digest_v1_alone(kb):
    kb.add("F-0001", "sensor", CLAIM)
    before = treehash.tree_digest(load_view(kb.cfg))
    kb.write("research-review/INDEX.md", "# Review index\n")
    kb.write("research-review/notes.txt", "scratch\n")

    view = load_view(kb.cfg)
    assert k8_stray_files(view) == []
    assert [i.format(view) for i in kb.issues() if i.code == "K8"] == []
    assert [i.format(view) for i in validate(view) if i.code == "K8"] == []
    assert treehash.tree_digest(view) == before


def test_load_view_reads_no_state(kb):
    kb.write(".kblam/staging/F-0001-x.md", "# staged\n")
    kb.write(".kblam/review-staging/SC-0001.yaml", record_text("SC"))
    kb.write("research-review/INDEX.md", "# Review index\n")

    state = kb.root / ".kblam"
    before = {p.relative_to(kb.root).as_posix(): p.read_bytes()
              for p in sorted(state.rglob("*")) if p.is_file()}

    view = load_view(kb.cfg)
    after = {p.relative_to(kb.root).as_posix(): p.read_bytes()
             for p in sorted(state.rglob("*")) if p.is_file()}
    assert before == after
    assert view.records == []
    assert not [p for p in (*view.files, *view.review_files) if p.startswith(".kblam/")]
