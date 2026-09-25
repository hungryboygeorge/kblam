# desk-kbtools — lightweight knowledge-base tooling survey

Package: a survey of **external** tools that could support a "one finding per file, YAML frontmatter,
generated index, write-time validator" design over the pilot project's markdown findings corpus
(~34k lines total, 1,000–3,500 lines per file). Web research only; no codebase claims here.

**Failure modes being solved, numbered as in the request:**

1. A falsified claim stays in its original section; the correction is appended as a new section
   ("C16 — C10 is FALSIFIED") and grep-by-excerpt keeps landing on the stale line.
2. A new document supersedes an old one; the "superseded" marker does not survive grepping.
3. Hand-maintained indexes go stale when content is inserted without updating them.
4. Failed experiments are kept whole with a correction appended, instead of a short dead-end stub.
5. The same fact is copied between working "answers" files and findings files and the copies drift.

**Method.** WebFetch/WebSearch of primary docs, GitHub sources, and PyPI/npm pages on
**2026-09-22**. Quotes are marked with how they were obtained: "direct fetch" = WebFetch of that
URL; "search summary" = relayed by a search-result summary and **not** re-read on the page, so
treat the wording as approximate. Anything unverified is labelled UNVERIFIED.

---

## ⬅️ OPEN QUESTIONS

1. **Does Doorstop's markdown item format preserve unknown frontmatter keys verbatim?** Its
   default command "reformat[s] item files during validation" (`--no-reformat` disables it, from a
   direct fetch of `doorstop/cli/main.py`). If it rewrites files on every validation, the write
   gate churns the git tree, and whether it round-trips custom keys is unestablished. A one-file
   experiment answers it.
2. **What exactly does `doorstop --error-all --warn-all` print and exit with for a suspect link?**
   The message string `"suspect link: {}"` and its WARNING level are source-verified, and
   `sys.exit(1)` on failure is source-verified, but the two were not observed together. Test:
   edit a parent item's text, run with both flags, read the exit code.
3. **Does StrictDoc implement a content-hash suspect-link mechanism?** Its traceability backlog
   and its "Impact Analysis screen" are documented; a hash/fingerprint scheme was not found in any
   page reachable on 2026-09-22. UNVERIFIED either way.
4. **Redundant one-file-per-finding tools not yet found by name.** The category (SQLite-less,
   file-per-item, frontmatter, typed links, validator) may have 2026 entrants under names the
   searches did not surface. A follow-up sweep of `awesome-agent-memory` style lists would cost
   one to two hours and could add candidates.

---

## INDEX

```
requirements/suspect-links   A1  Doorstop — what it is, version, license, footprint   2026-09-22  L79
requirements/suspect-links   A2  Doorstop — item format and on-disk layout           2026-09-22  L103
requirements/suspect-links   A3  Doorstop — suspect-link / change-impact mechanism   2026-09-22  L129
requirements/suspect-links   A4  Doorstop — validation severity table and gating     2026-09-22  L206
requirements/suspect-links   A5  Doorstop — what it does NOT do                      2026-09-22  L259
requirements/suspect-links   A6  sphinx-needs — suspect links: proposed, not built   2026-09-22  L285
requirements/traceability    A7  StrictDoc — formats, traceability, impact screen    2026-09-22  L309
requirements/traceability    A8  OpenFastTrace — Java, markdown item tags            2026-09-22  L337
indexes/generated            A9  zk — CLI query over frontmatter, no validation      2026-09-22  L365
validation/frontmatter      A10  Frontmatter schema validators (remark-lint, JSONS)  2026-09-22  L385
validation/links            A11  Link and dangling-reference checkers (lychee, remark) 2026-09-22 L423
indexes/generated           A12  markdown-to-sqlite / sqlite-utils / Datasette / Dolt 2026-09-22 L443
indexes/generated           A13  Dataview data model, Dendron schemas                2026-09-22  L470
agent-memory                A14  Beads — agent issue tracker on embedded Dolt         2026-09-22 L491
agent-memory                A15  Basic Memory — markdown + SQLite, AGPL               2026-09-22 L514
agent-memory                A16  Graphiti / Zep — bi-temporal edge invalidation       2026-09-22 L536
agent-memory                A17  mem0 — LLM fact rewriting, loses verbatim text      2026-09-22 L557
agent-memory                A18  MCP knowledge-graph memory server                   2026-09-22 L576
agent-memory                A19  Talamus — markdown + bitemporal, review-gated fixes 2026-09-22 L590
supersession/adr            A20  ADR tools — supersession model assessment           2026-09-22 L611
supersession/claims         A21  Nanopublications — formal retraction predicates      2026-09-22 L637
docs-drift/2026             A22  Agent-context linters + Vale for prose rules         2026-09-22 L657
docs-drift/2026             A23  OpenSpec, GitHub Spec Kit, Backlog.md                2026-09-22 L705
rankings                    A24  Adopt-as-is candidates (2 verified)                2026-09-22  L737
rankings                    A25  Top 3 borrow-the-design-and-build-in-Python          2026-09-22 L766
summary                     A26  Coverage of the five failure modes, by candidate     2026-09-22 L796
```

(Ranges are points in time; cite by entry ID.)

---

## A1 — Doorstop: identity, version, license, install footprint

verified: 2026-09-22

- **Latest release 3.2, released 2026-07-10** — direct fetch of https://pypi.org/project/doorstop/,
  checked 2026-09-22. Actively maintained (the repo shows 2,794 commits on `develop`; direct fetch
  of https://github.com/doorstop-dev/doorstop, 2026-09-22).
- **License LGPLv3** — PyPI page and `pyproject.toml` (`license = "LGPLv3"`), both direct fetches
  2026-09-22. `Operating System :: OS Independent` is in the classifiers, and `python = ">=3.10,<3.15"`.
- **Declared dependencies** (direct fetch of
  https://raw.githubusercontent.com/doorstop-dev/doorstop/develop/pyproject.toml, 2026-09-22):
  `pyyaml, markdown, bottle, requests, python-frontmatter, python-markdown-math, plantuml-markdown,
  six, openpyxl, verchew`. This is not a tiny install: `bottle` brings a WSGI server (used by the
  optional `doorstop server` web UI), `openpyxl` brings Excel export, `plantuml-markdown` and
  `python-markdown-math` bring diagram/MathJax rendering. Nothing here needs a system service; all
  are pure-Python wheels, so Windows install is a plain `pip install doorstop` (unverified by
  running it).
- Self-description, verbatim from the GitHub README (direct fetch, 2026-09-22): "a requirements
  management tool that facilitates the storage of textual requirements alongside source code in
  version control", with "mechanisms for modifying this tree, validating item traceability, and
  publishing documents in several formats".

Relevance: it is the closest existing implementation of the design under consideration.

## A2 — Doorstop: item format and on-disk layout

verified: 2026-09-22

Direct fetch of https://doorstop.readthedocs.io/en/latest/reference/item.html, 2026-09-22:

- Two on-disk formats, selected per document by `itemformat`: YAML (default) and Markdown.
- Markdown format, verbatim: "the `header` attribute is automatically derived from the first *level
  1* header line within the text, the remaining content of the markdown text section is used as
  `text` attribute".
- Standard attributes: `active`, `derived`, `normative`, `level`, `header`, `reviewed`, `links`,
  `references`, `ref`, `text`, plus unknown "Extended attributes – Custom key-value pairs".
- Inactive items, verbatim: "Only active items are included when the corresponding document is
  published. Inactive items are excluded from validation."

**One file per item is the settled design.** A 2019 proposal to put a whole document in one markdown
file (https://github.com/doorstop-dev/doorstop/issues/401) was closed "not planned" — direct fetch,
2026-09-22. That maps the pilot project's "one finding per file with a stable ID" onto Doorstop's existing
model without a fork.

Document configuration (direct fetch of
https://raw.githubusercontent.com/doorstop-dev/doorstop/develop/docs/reference/document.md,
2026-09-22) includes `prefix`, `digits` (UID width, default 3), `sep`, and an `attributes` block with
`defaults` and **`reviewed` — "which extended attributes contribute to the item fingerprint"**. That
last one is the hook for change propagation (see A3).

## A3 — Doorstop: suspect links and change-impact propagation

verified: 2026-09-22. This is the capability the design most needs, and Doorstop already has it.

Source-verified from a direct fetch of
https://raw.githubusercontent.com/doorstop-dev/doorstop/develop/doorstop/core/item.py, 2026-09-22
(verbatim):

```python
@property  # type: ignore
@auto_load
def reviewed(self):
    """Indicate if the item has been reviewed."""
    stamp = self.stamp(links=True)
    if self._data["reviewed"] == Stamp(True):
        self._data["reviewed"] = stamp
    return self._data["reviewed"] == stamp
```

```python
@auto_load
def stamp(self, links=False):
    """Hash the item's key content for later comparison."""
    values = [self.uid, self.text, self.ref]

    if self.references:
        values.append(self.references)

    if links:
        values.extend(self.links)
    for key in self.document.extended_reviewed:
        if key in self._data:
            values.append(_convert_to_str(self._data[key], ""))
    return Stamp(*values)
```

```python
@property  # type: ignore
@auto_load
def cleared(self):
    """Indicate if no links are suspect."""
    for uid, item in self._get_parent_uid_and_item():
        if uid.stamp != item.stamp():
            return False
    return True
```

```python
@auto_save
def clear(self, parents=None):
    """Clear suspect links."""
    log.info("clearing suspect links...")
    for uid, item in self._get_parent_uid_and_item():
        if not parents or uid in parents:
            uid.stamp = item.stamp()
```

Semantics that fall out of that code, plainly: each item stores, per link, a **fingerprint of the
parent as it was when the link was last cleared/reviewed**; if the parent's `uid`, `text`, `ref`,
`references`, or link list later changes, the child's stored stamp no longer matches and the link is
reported suspect. Item.md states the same thing in prose (direct fetch of
https://doorstop.readthedocs.io/en/latest/reference/item.html, 2026-09-22): "The link fingerprint is
used by Doorstop to detect when a parent item is changed, as a convenience to the writer since such
change may also affect its children", and "The UID, text, ref, and links UID portions contribute to
the fingerprint, while active, derived, normative, level, and header do not."

Two consequences worth stating explicitly, because they decide which failure modes this closes:

- **Adding a status field to `extended_reviewed` makes a status flip propagate.** Put `status` and
  `depends_on` in the document's `extended_reviewed` list and changing a finding from `current` to
  `withdrawn` marks every dependant's link suspect automatically. That is failure mode 1's and 2's
  mechanism (a reader is forced to revisit the dependent), implemented without new code.
- **It is a review-flag scheme, not a semantic one.** Doorstop has no notion of "current depends on
  withdrawn"; it knows only "this link's target changed since you last cleared it". The stronger
  rule the design wants (a current finding depending on a withdrawn one is *rejected*) has to come
  from a status vocabulary layered on `active` (see A4) or from custom validation.

## A4 — Doorstop: validation severity table, and using it as a hard gate

verified: 2026-09-22

Direct fetch of https://doorstop.readthedocs.io/en/latest/cli/validation.html, 2026-09-22: "Each
link consists of the parent item UID and the fingerprint of the parent item" ... "You can clear
suspect links with the `doorstop clear` command."

Message levels from the docs page (direct fetch, same date; the docs state these as lists of
conditions per level):

- **INFO** — skipped levels, items lacking initial review, UID prefix mismatches, link UID prefix
  mismatches.
- **WARNING** — duplicate levels, empty text, unreviewed changes, inactive child links,
  self-linking items, cycle detection.
- **ERROR** — inactive parent links, invalid UIDs, unfound external references.

Source confirms and refines this. Direct fetch of
https://raw.githubusercontent.com/doorstop-dev/doorstop/develop/doorstop/core/validators/item_validator.py,
2026-09-22 (verbatim strings with their level):

| level | message string |
|---|---|
| INFO | `"skipped inactive item: %s"` |
| WARNING | `"no text"` |
| WARNING | `"non-normative, but has links"` |
| WARNING | `"no links to parent document: {}"` |
| ERROR | `"invalid UID in links: {}"` |
| ERROR | `"linked to unknown item: {}"` |
| INFO | `"linked to inactive item: {}"` |
| WARNING | `"linked to non-normative item: {}"` |
| WARNING | `"suspect link: {}"` |
| INFO | `"needs initial review"` |
| WARNING | `"unreviewed changes"` |
| WARNING | `"no links from child document: {}"` / `"no links from document: {}"` |

**Correction to a claim that is easy to get wrong:** "linked to inactive item" is **INFO**, not
ERROR. The docs page's ERROR line "inactive parent links" does not mean an active item linking to an
inactive one is an error by default. To make it fatal you promote levels, which the CLI supports —
direct fetch of
https://raw.githubusercontent.com/doorstop-dev/doorstop/develop/doorstop/cli/main.py, 2026-09-22:

- `--warn-all` / `-w`: "display all info-level issues as warnings"
- `--error-all` / `-e`: "display all warning-level issues as errors"
- `--no-suspect-check` / `-S`, `--no-review-check` / `-W`, `--no-ref-check` / `-R`,
  `--no-child-check` / `-C`, `--strict-child-check` / `-Z`, `--skip` / `-s`, `--no-reformat` / `-F`

and on failure the CLI "invokes `sys.exit(1)`". So `doorstop -e -w` turns "linked to inactive item"
into an error and "suspect link" into an error — a gate that fails until a human or agent reviews
and clears. The cost is that `-e -w` also promotes everything else in the table, including
informational ones like "needs initial review" and "parent is X but linked to Y", so a corpus using
it must be clean of those or the gate is noise. That trade is a design decision, not a tool fact.

## A5 — Doorstop: what it does not do

verified: 2026-09-22

- **Failure 1 (falsified claim left in place).** Doorstop cannot detect prose asserting another
  item is wrong. Nothing in the validator reads for statements like "X is FALSIFIED"; it checks
  structure, links, and review state. Two of the design's rules are therefore not covered: "prose in
  one finding declaring another falsified" and "oversized files". Doorstop does check `"no text"`
  (empty body) but not a maximum size.
- **Failure 3 (stale index).** Addressed indirectly: Doorstop's answer to an index is `doorstop
  publish`, which renders the tree to HTML/RST/other formats from the items themselves (README:
  "publishing documents in several formats"; direct fetch 2026-09-22). There is no hand-edited index
  to go stale, but the published artifact is a generated site, not a grep-index of the kind agents
  in the pilot project read.
- **Failure 4 (short dead-end stubs).** Partially addressed. `active: false` items stay in the tree
  but are excluded from publication and marked `"skipped inactive item"` in validation. Nothing
  enforces a length limit or a stub shape, so the shrinking is a convention, not a rule.
- **Failure 5 (copies drifting).** Not addressed. Doorstop has no cross-document duplicate
  detection.
- **No JSON-Schema-style field validation.** Unknown keys are accepted as extended attributes;
  `document.md` (direct fetch, 2026-09-22) says "Doorstop will allow any number of custom extended
  attributes (key-value pairs) in the YAML file". Required-field enforcement and enum checks on
  `status` must be added with a frontmatter schema validator (A10) or custom code.
- **Windows.** Classifier says `OS Independent` and the dependency set is pure-Python, but no
  Windows CI run was inspected. UNVERIFIED in practice.

## A6 — sphinx-needs: suspect links are a proposal, not a feature

verified: 2026-09-22

Direct fetch of https://github.com/useblocks/sphinx-needs/discussions/1352, 2026-09-22. The
discussion is filed under Ideas. The proposed design is versioned links — "REQ_001:2"-style version
suffixes on link targets, with a `:version:` attribute the user sets by hand, detection at build
time, and the ability to clear findings that have been reviewed. Maintainer status, verbatim from
that page: a maintainer's reply of **2024-11-26** — "Thanks [@danwos](https://github.com/danwos) I
will have a look more in time" — and another maintainer's **2025-05-09** analysis calling it "a
major backwards incompatible change" requiring core-team discussion.

No implementation date. Treat this as unavailable today. Related closed-side point from a search
summary of https://github.com/useblocks/sphinx-needs/issues/685 ("Trigger reviews for dependent
'needs'", not re-read directly): the request is framed as a wish, consistent with the feature being
absent.

Cost and shape if it did exist: sphinx-needs is a Sphinx extension (`extensions = ["sphinx_needs",]`,
https://sphinx-needs.readthedocs.io/en/latest/installation.html, direct fetch 2026-09-22), so the
source of truth is reStructuredText/Markdown compiled by a Sphinx build, and the artifacts agents
would read are generated HTML under `_build/`. Requiring a Sphinx build between an agent's edit and
the agent's next grep is a large step for a corpus that is currently read directly as markdown.
Failure modes 1, 4, and 5: not addressed. Failure 3: addressed (Sphinx builds its own index).

## A7 — StrictDoc

verified: 2026-09-22

- **Apache-2.0, Python 3.10+**, install from PyPI (`pip install strictdoc`), per a search summary of
  https://pypi.org/project/strictdoc/ and https://github.com/strictdoc-project/strictdoc, not
  re-read directly. Outputs HTML, RST, ReqIF, PDF, JSON, Excel from an in-memory model (same
  sources).
- **Markdown support is experimental.** Direct fetch of https://strictdoc-project.github.io/formats/,
  2026-09-22: "StrictDoc's Markdown dialect adds traceability features on top of familiar Markdown
  syntax and mirrors the schema of SDoc. It is less strict than SDoc but more familiar to users
  already writing Markdown." The release that introduced it (0.19.0, search summary of
  https://github.com/strictdoc-project/strictdoc/releases/tag/0.19.0) called it "Initial Markdown
  support". The Markdown dialect's `Relations:` field is what carries traceability; markdown
  requirements "can also participate in a StrictDoc project and be included in the traceability
  graph" (search summary).
- **Impact analysis exists as a screen**, per a search summary of the StrictDoc backlog/
  traceability docs (https://strictdoc.readthedocs.io/en/stable/stable/docs/strictdoc_28_Backlog-TRACE.html):
  StrictDoc "provides an Impact Analysis screen that helps get information about the impact that a
  given change to a requirement has on the other requirements in the project tree". A
  content-hash/fingerprint suspect-link mechanism is **not** documented in anything reachable on
  2026-09-22 — UNVERIFIED.
- Shape: like sphinx-needs, the readable artifact is an export, not the source files. Its native
  format is SDoc (`.sdoc`), with Markdown as the newer, less strict option. For a corpus that agents
  must be able to grep in place, that is a real cost.
- Failure modes: 3 addressed by generation; 1, 4, 5 not addressed; 2 partially, by "less strict than
  SDoc" being the wrong direction if the goal is rejection of a bad edit.

## A8 — OpenFastTrace

verified: 2026-09-22 (partially — see caveats)

- License **GPL-3.0**; a Java artifact — "OpenFastTrace at its core is a Java Archive (short 'JAR')"
  and "OpenFastTrace 4.0.0 and above only needs a Java 17 (or later) runtime environment" (search
  summary of https://github.com/itsallcode/openfasttrace, not re-read directly). A JVM is a
  substantial addition to a Python/uv project, and GPL-3.0 may matter depending on how the tool is
  wired in.
- Markdown is the native spec format ("OFT's native format for writing specifications is Markdown …
  Markdown is a suitable candidate for writing specification that can be read and maintained over a
  long time", same search summary).
- Item syntax is tag-in-prose, e.g. `[dsn~name~1]` in a heading and coverage tags such as
  `[impl->dsn~name~1]`; coverage chains like `req~my-requirement~2 -> dsn~my-requirement~4 ->
  impl~my-requirement~4` appear in a search summary of
  https://github.com/itsallcode/openfasttrace-demo/blob/main/oft-live-demo-medium.md. **The exact
  tag grammar and the full tracing-status vocabulary (covered / uncovered / outdated / orphaned)
  are UNVERIFIED** — three attempts to fetch `doc/user_guide.md` on both `develop` and `main` on
  2026-09-22 returned HTTP 404, so this entry rests on search summaries of the repo pages.
- What it does for the failure modes: 3 addressed if you accept a generated tracing report; 1, 2, 4,
  5 not addressed — OFT tracks coverage of items by other items and code, and has no withdrawal,
  supersession, or status-change semantics that I could verify.
- One concrete robustness note, from a search summary (not re-read) of
  https://github.com/itsallcode/openfasttrace/issues/582: "Markdown importer silently drops all
  requirements after a fenced code block that directly follows a heading". A corpus full of fenced
  code blocks (the pilot project's findings files are full of them) is exactly the input shape that
  hits that bug. Verify against the current release before relying on OFT for markdown.

## A9 — zk (zk-org)

verified: 2026-09-22

- Go single binary, CLI-first; "zk is a command-line tool helping you to maintain a plain text
  Zettelkasten or personal wiki" with "advanced search and filtering capabilities including tags,
  links and mentions" and an fzf-driven interactive browser (search summary of
  https://github.com/zk-org/zk).
- YAML frontmatter supported; Markdown is "the only note format supported at the moment" (search
  summary of https://zk-org.github.io/zk/notes/note-format.html). Templates can inject frontmatter
  on note creation (https://zk-org.github.io/zk/notes/template.html).
- License not confirmed on 2026-09-22 — UNVERIFIED (the search summary did not carry it).
- **No validation of any kind, no links graph check, no invalidation semantics.** What it gives is
  failure mode 3 and nothing else: an index and set of saved queries over frontmatter
  (`zk list --format json …`) that a hook could regenerate, so no hand-maintained index exists to
  rot. Since the queries are config-declared, they are cheap to keep current.
- Notable as a *sibling idea* rather than an adoption: `zk`'s named filters are a small alternative
  to a generated index file — agents run the query instead of reading a file that can be stale.
  Costs: a third-party binary on PATH; a `.zk` config per corpus.

## A10 — Frontmatter/schema validators

verified: 2026-09-22

The category's job is one rule from the design: frontmatter must parse and must satisfy a declared
schema (required `id`, `status ∈ {current, provisional, withdrawn}`, `withdrawn_by`, `depends_on`,
`evidence` paths, evidence label). None of these tools know anything about the *other* rules
(cross-file consistency, size limits, prose checks), so each is a component, not a solution.

- **remark-lint-frontmatter-schema** (https://github.com/JulianCataldo/remark-lint-frontmatter-schema,
  direct fetch 2026-09-22): "Validate Markdown frontmatter YAML against an associated JSON schema" as
  a remark-lint rule. Direct-fetch wording: it "supports remark-cli and/or unifiedjs.vscode-remark",
  and "will not source .remarkrc automatically, isn't aware of your file structure, nor will it
  associate or import any schema/Markdown files, but will integrate easier with your own business
  logic and existing pipelines". The schema is supplied programmatically as a `JSONSchema7` object,
  file-association is done with a wildcard convention (`/*.md: schema.json`). That association
  caveat matters: it is a library-ish plugin you wire up yourself, in a Node toolchain.
- **remark-lint-frontmatter-validation** (https://www.npmjs.com/package/remark-lint-frontmatter-validation,
  search summary 2026-09-22): a remark lint rule **and CLI** for "validating Markdown frontmatter
  against JSON Schema", "intended as a modern replacement for remark-lint-frontmatter-schema". If a
  Node-based validator were chosen, this is the one to check first; whether it is maintained is
  UNVERIFIED.
- **check-jsonschema** (https://check-jsonschema.readthedocs.io/en/latest/precommit_usage.html, direct
  fetch 2026-09-22; https://pypi.org/project/check-jsonschema/, search summary): "a CLI and set of
  pre-commit hooks for jsonschema validation with built-in support for GitHub Workflows, Renovate,
  Azure Pipelines, and more". Apache-licensed, Python (**>=3.6.2** per the search summary of PyPI;
  stale-looking, verify the current floor). **It has no markdown-frontmatter mode** — it validates
  JSON/YAML files, so a project using it must extract frontmatter itself (e.g. `python-frontmatter`,
  which Doorstop already depends on) and hand the result to `jsonschema`. Verbatim from the docs page,
  the hooks are configured with a schema via `args`; there is no frontmatter-aware hook listed.
- **frontmatter-json-schema-action** (https://github.com/mheap/frontmatter-json-schema-action, search
  result 2026-09-22): a GitHub Action that validates YAML frontmatter against a JSON schema. CI-only;
  wrong execution point for a write-time hook. Existence confirmed, details UNVERIFIED.
- **DIY, and this is the honest comparison.** For a Python 3.11 + uv project the whole capability is
  roughly: `python-frontmatter.load` each `*.md`, validate `post.metadata` against a `jsonschema` or
  pydantic model, print violations, `sys.exit(1)`. The tool adds nothing but dependency weight over
  that, because the design's other rules are custom anyway.

## A11 — Link checkers (dangling references)

verified: 2026-09-22

- **lychee** (https://github.com/lycheeverse/lychee, direct fetch 2026-09-22): Rust, "Fast, async,
  stream-based link checker", cross-platform with "binaries available for Linux, macOS, and Windows
  for every release", distributed as a single static binary, JSON output for CI. It checks *URLs*,
  including relative file links and anchors in markdown.
- **remark-validate-links** (https://github.com/remarkjs/remark-validate-links, search summary
  2026-09-22): "plugin to check that markdown links and images reference existing files and headings"
  in a Git repo; can suggest corrections ("Cannot find heading for `#apha`; did you mean `alpha`").
  Node 16+, unified/remark stack.
- Fit: this covers the design's "dangling references" rule if references are written as markdown
  links or `file:line` text that a rule can be pointed at. **Neither tool understands an
  item-ID reference** (`depends_on: [C10]` pointing at a withdrawn finding). That check is custom,
  unless Doorstop's `links` field is used — Doorstop's `"linked to unknown item: {}"` is an ERROR at
  default severity and covers exactly that case for its own link field (see A4).
- Failure modes addressed: part of 1 and 2 (a link to a withdrawn or renamed target is at least
  detectable). Not 3, 4, 5.

## A12 — Generated, queryable catalogs from frontmatter

verified: 2026-09-22

- **markdown-to-sqlite** (https://github.com/simonw/markdown-to-sqlite,
  https://datasette.io/tools/markdown-to-sqlite, search summaries 2026-09-22): "CLI tool for loading
  markdown files into a SQLite database… YAML embedded in the markdown files will be used to populate
  additional columns", creating "a SQLite table with a schema matching the metadata". Python, single
  purpose, no server.
- **sqlite-utils** (https://sqlite-utils.datasette.io/en/stable/): "helps create SQLite databases…
  most functionality available as either a Python API or through the `sqlite-utils` command-line
  tool", with query output that can be emitted as markdown/CSV. This is the piece that turns a
  catalog into something an agent greps or that a hook regenerates.
- **Datasette** (https://docs.datasette.io): the browser/JSON API over the same SQLite file. It is a
  web app; the useful part for agents is the SQL CLI path, not the server. Do not adopt the server.
- **Dolt** (referenced in the request; also the engine under Beads, A14): a version-controlled SQL
  database with cell-level merge and `dolt diff`/branch semantics. Search summaries on 2026-09-22
  confirm it can diff table contents between commits, which would give "what changed in the index
  since the last review" for free. Cost: a second VCS with its own merge conflicts in a repo whose
  history is already git. This entry is a pointer, not a verified evaluation — treat Dolt as
  UNVERIFIED.
- Fit: failure mode 3, cleanly and cheaply. A generated SQLite catalog is never hand-edited, and a
  hook can rebuild it and diff it to detect a stale index. It does not decide anything about
  invalidation, withdrawal, or drift between documents (1, 2, 4, 5).
- Windows: all Python/Rust-single-binary; sqlite-utils and markdown-to-sqlite are pip-installable
  alongside the pilot project's existing uv environment.

## A13 — Dataview data model, and Dendron schemas

verified: 2026-09-22

- **Obsidian Dataview** (data model only, per the request): fields come from YAML frontmatter or
  inline `[key:: value]` syntax; types are inferred from the value ("In Inline fields, you need to
  wrap text values into quotes to be recognized as a list"), and links placed in frontmatter for
  Dataview "won't show up in the outgoing links, won't be displayed on graph view and won't be
  updated on rename" (direct fetch of
  https://blacksmithgu.github.io/obsidian-dataview/annotation/add-metadata/ and a search summary of
  the same site, 2026-09-22). Two borrowable ideas: (a) **inline fields** let a claim carry its
  status at the point of the claim instead of only in a header block, which is grep-friendly; (b)
  **query-as-index** — the index is a query, so it cannot be stale. What it does not give: any
  validation, any write-time gate, any invalidation. It runs inside Obsidian, not from a shell, so it
  is not adoptable here — only its data model is relevant.
- **Dendron** (https://wiki.dendron.so/notes/c5e5adde-5459-409b-b34d-a0d75cbb1052/, search summary
  2026-09-22): hierarchy-pattern schemas that can apply templates to matching notes; "Dendron doesn't
  force you to use schemas if you don't want to, which is why you can create notes that don't match
  any schema". Status, same search: **maintenance only, active development has ceased**, and it is a
  VS Code extension. Ruled out on both counts (no CLI, unmaintained).

## A14 — Beads (steveyegge/beads, `bd`)

verified: 2026-09-22

- **MIT, Go**, repo at https://github.com/steveyegge/beads (direct fetch 2026-09-22; the
  `gastownhall/beads` path also serves it). Installs via Homebrew, npm, or an install script; Windows
  is listed as supported.
- Storage: **embedded Dolt by default** (`.beads/embeddeddolt/`) with an optional external server
  mode, plus an export file `.beads/issues.jsonl` that is "for interchange" and "is not the source of
  truth" (direct fetch, 2026-09-22). So it is not a markdown-frontmatter store: prose lives in the
  database, and git sees JSONL exports, not the findings files agents grep.
- Dependency/link vocabulary, from the README and FAQ (direct fetch 2026-09-22 and search summaries):
  dependency tracking with graph links including "relates-to, duplicates, supersedes, and
  replies-to"; the FAQ calls `discovered-from` "an agent semantic". There is a "Semantic 'memory
  decay' summarizes old closed tasks to save context window" feature.
- Fit: this is an issue tracker for agent work, and it does have a `supersedes` relation — so
  failure mode 2's *bookkeeping* (B supersedes A) is representable. But the corpus would move out of
  markdown into Dolt, which breaks the pilot project's grep-the-findings workflow and the "immutable
  dated evidence folders" layout. It also has no schema validation for prose quality, no size limits, and
  no drift detection between two copies of a fact (1, 4, 5 uncovered). Adopt only if the intent were
  to replace the findings corpus with a task/claim database, which is a much larger decision than
  the design under consideration.

## A15 — Basic Memory

verified: 2026-09-22

- Direct fetch of https://docs.basicmemory.com/reference/technical-information, 2026-09-22:
  file-first — "Markdown Files - Source of Truth", with SQLite as "a secondary index" for the
  knowledge graph; semantics are extracted from markdown into Entity/Observation/Relation objects
  (`- relates_to [[Another Note]]` becomes a graph edge). The "Sync Process" "Detects Changes",
  "Parses Files", "Updates the database with changes".
- **License AGPL-3.0** (same page, verbatim: "Free Software", "Copyleft — Derivative works must be
  distributed under the same license", "Commercial Use — Allowed, subject to license
  requirements"). For a tool invoked from hooks in a private repo this is usually fine; for anything
  distributed, AGPL is the strictest of the licenses in this survey and should be a conscious choice.
- Installation is a Python tool (uv/pipx; the docs reference `uv tool install` and `bm update`, search
  summary 2026-09-22). Interface is primarily **MCP**, with CLI commands for management.
- **No invalidation, supersession, or retraction semantics.** The documentation page states no
  update/delete/deprecate semantics for facts — search summaries and the direct fetch agree there is
  a sync/index, not a status model. It preserves markdown verbatim as the source of truth, which is
  the good half; the missing half is exactly the design's core.
- Fit: 1, 2, 4 uncovered; 3 partially (it can answer queries over the graph, so an index need not be
  hand-written); 5 uncovered.

## A16 — Graphiti / Zep

verified: 2026-09-22

- Bi-temporal model: every fact edge carries `valid_at`/`invalid_at` (world time) plus
  `created_at`/`expired_at` (system time), per a search summary of
  https://mintlify.wiki/getzep/graphiti/concepts/temporal-model and
  https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/, 2026-09-22.
- Invalidation semantics, from the same sources: when new information contradicts an existing fact
  the old fact's validity window is closed rather than the fact being deleted ("A new invalidate
  action soft-invalidates matching currently-valid edges (valid_until = now); history is never
  deleted"), and out-of-window edges are "reported as hidden so the agent knows history exists". This
  is the most complete *conceptual* match in the survey for "a fact that used to be believed and is
  no longer".
- Cost: Graphiti is a graph-database-backed service — Neo4j or FalkorDB — plus LLM/embedding API
  calls to extract and resolve facts (the project page describes it as knowledge-graph memory for
  agents, and the deployment write-ups pair it with Neo4j). That fails the "no heavy server"
  requirement outright, and the facts it stores are LLM-extracted and paraphrased, not verbatim
  findings text.
- Verbatim-vs-paraphrase: **paraphrased**. The invalidation is real, the evidence preservation is not.

## A17 — mem0

verified: 2026-09-22

- Mechanism, per a search summary of https://memo.d.foundation/breakdown/mem0 and
  https://github.com/mem0ai/mem0/discussions/4787, 2026-09-22: after extracting facts from a turn,
  the updater compares them with existing memories by vector similarity and an LLM chooses ADD /
  UPDATE / **DELETE** ("remove memories contradicted by new information") / NOOP. The graph variant
  "marks conflicting relationships as invalid, supporting temporal reasoning without deleting data",
  and a "Dream" feature marks an older fact outdated when contradicted, with superseded memories "not
  deleted and not hidden by default".
- Storage: vector database plus a relational database.
- Verbatim-vs-paraphrase: **paraphrased**, and destructively so in the base path (`DELETE` on
  contradiction). For a research corpus whose whole value is the verbatim evidence and provenance,
  this is the wrong primitive: a contradicted claim would be replaced by a summary of it, and the
  audit trail would be an LLM's account of what it decided.
- Cost: needs embeddings and an LLM at write time; a vector store on disk or a service. Fails the
  lightweight/no-server requirement.

## A18 — MCP knowledge-graph memory server

verified: 2026-09-22

- https://github.com/modelcontextprotocol/servers/blob/main/src/memory/README.md (search summary
  2026-09-22): entities, relations ("always stored in active voice"), and observations; persisted as
  a line-delimited JSON file, default `memory.jsonl`, path overridable with `MEMORY_FILE_PATH`.
- Tools: `create_entities`, `create_relations`, `add_observations`, `delete_entities`,
  `delete_observations`, `delete_relations`, `read_graph`, `search_nodes`, `open_nodes`.
- **No invalidation or supersession semantics** — a fact is a relation or observation that either
  exists or has been deleted. Storage is JSONL, not markdown, so the corpus stops being greppable
  prose. Cheapest thing in the survey to run, and the least of what the design needs (uncovered: 1,
  2, 3, 4, 5 — it makes no promises about any of them).

## A19 — Talamus

verified: 2026-09-22

- Direct fetch of https://raw.githubusercontent.com/ampres-ai/talamus/main/README.md, 2026-09-22:
  **Apache-2.0**, install `pipx install "talamus[mcp]"` (or `uvx --from talamus talamus demo …`).
  Notes are "ordinary Markdown" with an index; retrieval uses FTS5 "without requiring embeddings",
  with a rebuildable index (a DuckDB index is mentioned in search summaries of the same project).
- Commands: `talamus setup`, `ingest`, `ask`, `recall`, `search`, `read`, `verify`, `ui`. Verbatim:
  "Notes have version history, facts have valid-time windows"; `talamus ask --as-of 2026-01` answers
  "from the brain as it was"; `talamus verify` "proposes corrections to review"; answers cite the
  notes they used; every note carries provenance.
- LLM requirement: plain search and recall work entirely locally; LLM-backed features (smart
  expansion, corrections) need a provider you supply (Claude CLI, Ollama, Anthropic API).
- Fit: the closest thing found to "verbatim markdown + provenance + bitemporal + review-gated
  correction" that is also small. Caveats: it is one author's young project (verify commit activity
  before relying on it), the correction path is LLM-proposed rather than rule-validated, and its
  interface is MCP-first with a CLI alongside. Failure modes 1 and 2 are addressed by review-gated
  corrections and valid-time windows; 3 by the generated index; 5 not at all (no cross-document
  duplicate detection verified); 4 not addressed.

## A20 — ADR tools: does their supersession model help or hurt?

verified: 2026-09-22

- **adr-tools** (https://github.com/npryce/adr-tools, search summaries 2026-09-22): bash CLI; ADRs are
  markdown files in a directory. `adr new -s 2 Use Azure` creates a new ADR flagged as superseding
  ADR 2 and **changes ADR 2's status line to "Superceded by" with a link**. License not confirmed
  (UNVERIFIED). It is a bash script collection, so Windows support means Git Bash/WSL.
- **log4brains** (https://github.com/thomvaill/log4brains, search summaries 2026-09-22): markdown ADRs
  in git, statuses "Proposed, accepted, deprecated, superseded"; "Only the ADR's status can change.
  An ADR can be deprecated or superseded by another one, but it was at least true one day"; metadata
  is extracted automatically from git history and content and published as a searchable static site
  (`log4brains-web`). Node/npm toolchain, static-site publication.
- **Assessment, which is what was asked for.** The *status field plus a superseded-by link* is
  directly useful and is what the design already wants. The rest of the ADR model works against this
  corpus in two ways. First, ADR immutability is intentional — the old record is kept verbatim
  because a decision log's value is the historical record. That is exactly failure mode 1's shape,
  and the pilot project's own convention (CLAUDE.md: do not let an answers file become a ruling log)
  deliberately rejects it. Second, an ADR is a *decision*, not a *finding*: decisions are true by
  having been made, findings are true or false against hardware. Supersession in ADRs means "we
  changed our mind"; the design needs "this was wrong, and nothing may depend on it", which is a
  withdrawal with dependent invalidation — a stronger relation than any ADR tool implements.
- Verdict as evidence, not as advice: borrow the status vocabulary, not the tooling. Failure modes
  2 and 3 are partly addressed by log4brains' generated site; 1, 4, 5 are not addressed, and 1 is
  actively reinforced by the immutability norm.

## A21 — Nanopublications: formal retraction as a first-class relation

verified: 2026-09-22

- Direct fetch of https://nanopub.readthedocs.io/en/latest/publishing/retraction.html, 2026-09-22:
  retraction is done "by publishing a new nanopublication that states that you retract the original
  publication"; the assertion "states that the researcher (denoted by the ORCID iD from your profile)
  retracts the provided nanopublication using the `npx:retracts` predicate". Retracted publications
  are **filtered out by default** ("the default being True, returning only publications that are not
  retracted"), and only the signing key's owner can retract, with a `force` override.
- The design lesson worth taking: retraction is a *separate signed assertion about another record*,
  not an edit to it, and consumers see the retraction by default without needing to have read the
  original. That is a stronger guarantee than "the status field of the target" if the risk is that an
  agent reads a stale excerpt and never opens the target — though it costs a lookup, since a retracted
  claim still exists and its text is still retrievable.
- Cost here: nanopub-py is Python (`NanopubClient.retract`), but the model assumes publication to a
  nanopublication server with cryptographic signing and ORCID identity, and the content model is RDF
  triples, not prose findings. Adopting it means leaving markdown. Reference only.
- Failure modes: 2 addressed conceptually; 1, 3, 4, 5 not addressed.

## A22 — Agent-context linters (2025–2026) and Vale

verified: 2026-09-22

This category is the only one that specifically targets *agent documentation drift*, which is the
pilot project's problem. All of them are bound to `AGENTS.md` / `CLAUDE.md` / `.cursorrules` shapes, and
for agents-md-check that bound is enforced by hardcoded filename discovery — so the category is a
source of rule ideas, not of adoptable tools.

- **agents-md-check** (https://github.com/duke5am/agents-md-check, direct fetch 2026-09-22): **MIT**,
  "Copyright (c) 2026 duke5am", `pip install agents-md-check`, **Python 3.9+**, options
  `--json --severity --max-lines --quiet`. Checks, verbatim from the page: "dead path references,
  contradictions, duplicates, unscoped rules", malformed Cursor `.mdc` frontmatter, "overlapping rule
  scopes with conflicting directives", "files exceeding line/word budgets", "missing or divergent
  `CLAUDE.md`/`AGENTS.md` pairs". It validates three frontmatter keys and reports malformed
  frontmatter at high severity and unknown keys at low severity. **It cannot be pointed at a findings
  corpus.** The same page, fetched 2026-09-22, shows discovery is a hardcoded filename list —
  `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`, `.cursorrules`,
  `.github/copilot-instructions.md` — so a findings folder yields no work regardless of the path
  argument. The repository is also immature: 0 stars and 6 commits. Its rule list is the best
  available statement of what a purpose-built findings linter would check; its code is not
  adoptable.
- **ctxlint** (https://github.com/YawLabs/ctxlint, search summary 2026-09-22): **MIT**, npm
  (`npx @yawlabs/ctxlint@latest`), lints CLAUDE.md/AGENTS.md/.cursorrules/.mcp.json "against your
  actual codebase", checking "paths, commands, tokens, contradictions, and frontmatter", with `--fix`
  that rewrites a broken path when git shows a rename, and `--strict` for CI. Active (v0.27.0).
- **cclint** (https://github.com/felixgeelhaar/cclint, search result 2026-09-22): TypeScript linter for
  CLAUDE.md files "validating and optimizing" them. **AgentLinter** (https://agentlinter.com/) and
  **claudelint** (https://claudelint.com/) are the same idea; all three surfaced only through search
  results and are UNVERIFIED beyond existence and one-line description. `ctxlint` likewise lints
  "CLAUDE.md/AGENTS.md/.cursorrules/.mcp.json" by name (search summary of
  https://github.com/YawLabs/ctxlint, 2026-09-22: **MIT**, npm, `--fix` rewrites a path broken by a
  git rename, `--strict` for CI, v0.27.0) — a different filename list, the same structural limit.
- **Vale** (https://vale.sh/docs, https://github.com/vale-cli/vale, direct fetch/search summary
  2026-09-22): a markup-aware prose linter, single binary, "runs on macOS, Windows, and Linux", rules
  are **YAML files** with regex/pattern and scope, so a project-specific rule such as "a finding body
  must not contain the words FALSIFIED/SUPERSEDED — change the target's status instead" is
  expressible. It takes file paths or globs, so unlike the linters above it does run over this
  corpus. Exit code on findings. License not confirmed on the page reached (UNVERIFIED; the project
  is widely described as open source, and it is used by AWS, Microsoft, GitLab per the search
  summary).
- Fit: the category's value is its **rule list**, not its code. **Failure 1's enforcement** is what
  these rules aim at (duplicate and contradiction detection, forbidden prose), and **failure 4's size
  rule** is the one check worth reimplementing (agents-md-check's `--max-lines` semantics; Vale has no
  size rule). Failure 3 and 5 are not addressed by any of them. The caveat applies to any
  reimplementation: contradiction detection here is heuristic, not proof, and a regex rule forbidding
  the word "FALSIFIED" is gameable by any paraphrase — it detects the *habit*, not the *error*.

## A23 — Agent-facing spec frameworks (2025–2026): OpenSpec, Spec Kit, Backlog.md

verified: 2026-09-22

- **OpenSpec** (https://github.com/Fission-AI/OpenSpec, direct fetch of `docs/cli.md` 2026-09-22):
  markdown specs under `openspec/specs/` plus proposed changes under `openspec/changes/`, where a
  change carries a **delta spec** whose sections are ADDED / MODIFIED / REMOVED, and
  `openspec archive` "validates and merges the active delta specs into `openspec/specs/`". Verbatim:
  "A change with zero spec deltas fails validation unless its `.openspec.yaml` declares
  `skip_specs: true`"; `openspec validate` performs "structural validation" plus a check of "a
  change's MODIFIED requirements against the main specs they would replace". Archives move to
  `openspec/changes/archive/YYYY-MM-DD-<name>/`.
  Why this matters for the design: **MODIFIED-means-overwrite** is the "rewrite in place, don't
  append a correction" rule expressed as an enforceable file format, and the change/archive split
  separates "proposed truth" from "current truth" — which is exactly the discipline the pilot
  project lacks. It does not do suspect-link propagation (nothing verifies that a requirement depending on a
  MODIFIED one was revisited) and it has no field-level schema. Install is npm; license UNVERIFIED
  here.
- **GitHub Spec Kit** (https://github.com/github/spec-kit, search summary 2026-09-22): a `specify` CLI
  that installs markdown templates and slash commands for a constitution → specify → plan → tasks →
  implement pipeline; `/analyze` "validates against the constitution, helping catch violations and
  gaps before implementation". The constitution is the interesting part: a small set of
  non-negotiable principles that every later artifact is checked against, which is how a project can
  make "no claim survives being falsified" a written rule rather than a hope. Validation is
  LLM-executed, not a deterministic gate.
- **Backlog.md** (https://github.com/MrLesk/Backlog.md, search summaries 2026-09-22): markdown-native
  task manager for git repos, "every task is a plain `.md` file under `backlog/tasks/`", with
  milestones and dependencies and a task view showing "what a task waits on and what waits on it"
  (i.e. dependency impact in both directions), a terminal kanban, a local web UI, MCP/CLI agent
  integration, and no database, account, or network. Reference for the one-file-per-item + generated
  board shape; it is a task tracker, so it has no claim-validity model.

## A24 — Adopt-as-is candidates (two survived verification)

verified: 2026-09-22

1. **Doorstop** — the only tool found that already implements item-per-file + typed links +
   fingerprint-based suspect links + a validation command that exits non-zero, with an
   `extended_reviewed` hook that makes a status change propagate to dependents. Off-the-shelf it
   covers: the write-time gate for dangling links (ERROR by default), change impact (suspect links,
   WARNING, promotable), and index rot (publishing, no hand-written index). It does not cover:
   cross-document duplicate drift, stub enforcement, prose-contradiction detection, per-field schema.
   Cost: LGPLv3, Python 3.10+, a 10-package dependency set including a WSGI server and Excel/diagram
   extras, and a corpus that gets rewritten in Doorstop's item format (`itemformat: markdown`).
2. **markdown-to-sqlite + sqlite-utils** — Python, no server, turns frontmatter into a queryable
   catalog, so the index is regenerated rather than maintained. Cheap, additive, and it does not
   require rewriting the corpus. It solves exactly one failure mode (3) and nothing else.

**Agents-md-check was removed from this list** after a direct fetch of
https://github.com/duke5am/agents-md-check on 2026-09-22 showed its file discovery is a hardcoded
filename list (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`, `.cursorrules`,
`.github/copilot-instructions.md`), so it cannot lint a findings corpus at all, and the repository
has 0 stars and 6 commits. **No replacement is listed**: nothing else in this survey passed
verification as adoptable-and-pointable-at-this-corpus. Vale (A22) runs over arbitrary paths and is
the remaining config-only option, but it was not verified at the level the two entries above were, so
it is recorded there rather than ranked here.

Deliberately not ranked: sphinx-needs (feature does not exist), StrictDoc and OpenFastTrace (both
require a build/export step and a second format), Graphiti/Zep, mem0, Basic Memory (server,
paraphrase, or no invalidation model).

## A25 — Top 3 for "borrow the design and build it in Python"

verified: 2026-09-22

Ranked by how much of the design can be lifted with the least new code, given Python 3.11 + uv.

1. **Doorstop's stamp/cleared mechanism, reimplemented over the existing markdown corpus.** Take the
   exact semantics in A3: each link stores a hash of the parent's key content (`id`, body text,
   `depends_on`, and a declared set of "reviewed attributes" including `status`), compared on every
   run; a mismatch is a suspect link. This gives change-impact propagation in tens of lines, without
   adopting Doorstop's item format, dependency set, or LGPL obligations — and it keeps the corpus as
   the files agents already grep. The `extended_reviewed` idea (declare which fields participate in
   the fingerprint) is the part worth copying verbatim.
2. **OpenSpec's MODIFIED-overwrite + archive split.** Two rules, both cheap: an edit to a finding is
   an in-place rewrite of that finding (never a new section declaring the old one wrong), and
   superseded material moves to a dated archive rather than staying in the live index. Read the
   delta-discussion in A23 for the wording, not the npm tool.
3. **Graphiti's bi-temporal fields / nanopublications' separate retraction record.** Store
   `valid_from`/`invalid_at` (or simply `withdrawn_on` plus `withdrawn_by`) so an entry can be wrong
   *now* and still be queryable *as of* a past date; and make retraction a pointer
   (`withdrawn_by: <id>`) rather than an edit, so a reader who lands on the withdrawn line sees the
   pointer. Both are field-level ideas costing a few lines each in the validator.

Component stack for the DIY path, in order of usefulness: `python-frontmatter` (already a Doorstop
dependency) to parse; `jsonschema` or pydantic for the frontmatter schema; a ~50-line graph walk for
dangling `depends_on`, status propagation, and "current depends on withdrawn"; `lychee` or
`remark-validate-links` only if external/relative links matter too; `markdown-to-sqlite` or a plain
Python `sqlite3` write for the generated index; the write gate itself as a hook that runs the
validator and exits non-zero.

## A26 — Coverage of the five failure modes, by candidate

verified: 2026-09-22

| candidate | 1 falsified text left | 2 supersession | 3 index rot | 4 stub shrinking | 5 copies drift |
|---|---|---|---|---|---|
| Doorstop | partial (suspect link flags the child; no prose check) | yes (fingerprint + `active`) | yes (generated publish) | partial (`active: false`, no length rule) | no |
| sphinx-needs | no (feature unbuilt) | partial | yes (Sphinx build) | no | no |
| StrictDoc | no | partial | yes (export) | no | no |
| OpenFastTrace | no | no | yes (report) | no | no |
| zk | no | no | yes (queries) | no | no |
| markdown-to-sqlite / sqlite-utils | no | no | yes | no | no |
| Beads | no | yes (bookkeeping only) | n/a (DB) | no | no |
| Basic Memory | no | no | partial | no | no |
| Graphiti / Zep | yes (edge invalidation) | yes | partial | no | no |
| mem0 | yes (deletes contradicted) | yes | partial | no | no |
| MCP memory server | no | no | n/a | no | no |
| Talamus | partial (review-gated correction) | yes (valid-time windows) | yes | no | no |
| ADR tools | **anti** (immutability keeps the stale text) | yes (status + link) | yes (log4brains) | no | no |
| nanopublications | partial (separate retraction record) | yes | no | no | no |
| agents-md-check | **cannot run over this corpus** (hardcoded filename discovery, 0 stars / 6 commits — A22) | — | — | — | — |
| Vale | partial (forbidden-prose rules) | no | no | no | no |
| OpenSpec | yes (MODIFIED overwrites) | yes (archive) | yes (single source) | no | no |
| Spec Kit | no | partial | no | no | no |
| Backlog.md | no | no | yes (generated board) | no | no |

**The uncovered cells, stated plainly.** No tool found detects *prose in one file asserting another
file is wrong* — that is a semantic check, and the only mechanisms available are an LLM pass
(knowledgebase_guardian, OpenKB, and the `llm-wiki` family all take this route; a weekly
"list every pair of claims that contradict" prompt is the common pattern) or a regex that catches the
habit word rather than the mistake. No tool found forces a withdrawn item to *shrink* to a stub. No
tool found detects the same fact stated twice in two files and drifting. The one rule in the survey
that claims cross-document duplicate detection, `agents-md-check`'s "duplicates" check, applies only
inside a single agent-instruction file and cannot be run over this corpus (A22), so that cell is
empty rather than filled by a weaker tool.

**Nothing in this survey should be read as an adoption recommendation.** The two rankings above
answer "what exists and what it does" for the two paths named in the request; the choice between
adopting Doorstop, adopting a linter, or building the validator is a project decision.
