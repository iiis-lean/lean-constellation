# Lean Hierarchical Formalization (LHF)

LHF is a static mathematical view of a Lean workspace. LC Native exports a fixed
Release into this structure; LC Restructure can produce the same metadata beside
reorganized Lean sources. The LHF reader does not construct an LC/ARK runtime,
start services, compile Lean, query an LSP, or access the network.

## Layout

```text
workspace/
  .lhf/workspace.json
  Demo/
    .lhf/
      repo.json
      nodes/Main/node.json
      nodes/Main/Topic/node.json
      nodes/Main/Topic/decls/result.json
      materials/source/...
      materials/resources/<resource_key>/...
    lakefile.toml
    lake-manifest.json
    lean-toolchain
    Demo.lean
    Demo/Main/Prelude.lean
    Demo/Main/Interfaces.lean
    Demo/Main/Topic/Prelude.lean
    Demo/Main/Topic/Interfaces.lean
    Demo/Main/Topic/Theorems/result.lean
```

Repos are direct children of the workspace: there is no `repos/` intermediate
directory. Repo keys and directory names are recorded explicitly and need not be
identical. A package/module root is a physical binding, not another mathematical
hierarchy. The initial LC exporter supports native repos with a root Lake TOML
package, using the package name as module root. Arbitrary Lake DSL layouts and LC
Adapter repos are not silently converted.

The intended source convention groups declarations under `Defs`, `Types`,
`Instances`, `Lemmas`, or `Theorems` in their Content node. Important declarations
normally have separate files. Helpers remain in source; explicitly shared files
are allowed for mutual/generated blocks. The reader checks that each binding is
inside its owning node, without forcing a split or deleting helper declarations.
`Prelude.lean` and `Interfaces.lean` exist for each node. These are module import
conventions, not Lean symbol access controls. Export preserves their exact bytes.

## Stored fields

| Object | Fields and meaning |
| --- | --- |
| Workspace | `title?`, `main_repo`, `repos` (key to direct-child directory). The main repo's Main exports are the workspace entry. |
| Repo | `root_node: "Main"`, `module_root` (Lean source directory root). |
| Node | `kind: scope/content`, `title?`, `goal` (mathematical target), `boundary` (mathematical scope), `constraints?`, `summary?`, `exports`, `children`, `declarations`. |
| Declaration | `name` (catalog/file key), `lean_name` (actual qualified symbol), `kind` (original fine kind), `summary`, `state: declared/proved`, `file` (repo-relative Lean file), `statement`, `proof?`. |
| Section | `nl?: {text?, origin: [...]}`, `fl?` (captured Lean text), `deps`. Statement and proof retain distinct dependencies. |
| Origin | `kind`, `ref?`, `source_path?` (repo-relative local material), `resource_key?`, optional line range, locators and note. Local source/resource origins require a resolvable path. |
| Internal dependency | `kind: repo_decl`, `ref: {repo?, node, name}`, `reason?`. Omitted repo means the containing repo. |
| External dependency | `kind: external_decl`, `ref: {package?, name, module?}`, `reason?`. Mathlib references use `package: mathlib`. |

Node paths are derived from their metadata directory: `nodes/Main/Topic` means
`Main.Topic`. In memory, paths key `RepoData.nodes`. Scope nodes own ordered
children; only Content leaves own ordered declarations. Exports use declaration
references, without a redundant `public` flag. A theorem can be declared without
a completed proof; non-theorem declarations in `declared` state are complete
formal artifacts. `proved` requires `proof.fl`. All declarations require
`statement.fl`. Captures can contain the entire source file, including helpers,
imports and context. A proved theorem's statement capture may still have `sorry`;
its proof capture and bound source file contain the final implementation.

There are no build/check results, release IDs, revisions, strategy/round records,
asset registries, or node-level source/dependency planning references in this
schema. Registered dependencies describe the author's mathematical graph; they
are not claimed to be an exhaustive compiler dependency graph. Loading validates
structure and references, not mathematical correctness or proof soundness.

## Export an LC Release

```bash
python -m lean_constellation.lhf export \
  --repo Demo=/path/to/original/Demo \
  --release-id release_example \
  --output /path/to/new-workspace
```

With multiple mapped repos, specify `--main-repo KEY`. Add each native provider
with `--repo Provider=/path/to/provider`. Maps identify local Git object stores;
they do not select provider versions. Providers are selected recursively from the
consumer Release's exact Lake pins. Mathlib and other unregistered third-party
packages stay external. No fetching occurs. Registered LC providers with missing
maps, objects, pins or Releases cause explicit errors. Different commits for the
same provider and dependency cycles are rejected. Provider requirements must use
full Git commits agreeing with `lake-manifest.json`; path/subdirectory provider
packages are not accepted by this exporter.

Normally `refs/lean-constellation/releases/<id>` identifies the main commit. When
that custom ref is absent, pass `--release-commit FULL_SHA`. The exporter requires
that commit's publication pointer and manifest to agree and that none of its
parents contain the same Release manifest. It rejects later publication overlays
and missing parent history rather than inferring a Release from HEAD. Explicit
commit selection is not an authorization to substitute arbitrary historical
contents for an immutable Release. Providers use the same identity check at their
pinned commit.

Every read comes from the selected Git tree, including metadata, materials and
Lean source. Release `node_contract_versions` select contracts; Content
`decl_graph_head` selects revisions, regardless of catalog `current_revision`.
Only committed declared/proved revisions are accepted. Older references are
collapsed only when the anchor and selected revision have the same Lean name and
LC's canonical complete statement capture. This normalization changes line endings
and trailing whitespace only. Capture-to-file comparison separately excludes
well-formed LC managed import blocks and target docstrings, preserving helper and
primary declaration bodies. A mismatch fails export.

Mapping rules:

- Node goal/boundary/constraints map directly; LC contract closeout `summary` does
  not become a mathematical node summary.
- Declaration `summary` maps from the catalog, never `change.summary`.
- Scope exports come from the selected contract; Content exports come from
  `public` declarations in the selected head. They are not inferred from usage.
- Source origins move to `.lhf/materials/source/...`; referenced resource bundles
  move to `.lhf/materials/resources/<key>/...`, with `canonical_entry` resolved to
  the readable file. Line ranges, locators and notes survive. Referenced material
  bundles retain their internal directory layout and attribution. By default all
  source/resource files are copied, including unreferenced materials. External
  references are retained as references unless a local-path filter is enabled.

The destination must not exist and must be outside source repositories. Export
writes a sibling staging directory, validates a complete reload, then renames it
into place. Failures remove staging; source Git refs, indexes and working trees
remain untouched. Symlinks and gitlinks needed in the output are rejected rather
than materialized as ordinary files. Ordinary portable files are copied, excluding
LC/ARK process metadata, prior LHF and publication metadata/receipts. Original
Lake configuration and pins are preserved; this is not an offline dependency cache
or a relocatable Lake dependency rewrite.

The JSON report on stdout records source commits and counts outside the LHF
mathematical schema. LHF and `.lean_constellation/restructure/` are excluded from
LC release/publication/semantic digests and managed publication ignores for now.

## Filter source materials and origins

A single path selection controls both the files supplied to readers and the
structured origin records retained in declarations. It does not alter NL, FL,
summary, dependencies, or the original LC project.

```bash
# Add to an export command: include only these corpus files/directories.
--source-path main.tex --source-path macros.tex --source-path sections/

# Include a specific resource as well, using its key and internal path.
--resource-path book/chapter.tex

# Alternatively, provide no source materials and no origins.
--no-sources
```

With no selection flags, export copies the complete source corpus and resource
library and preserves all origins. Once any path flag is supplied, only matching
files are copied: `--source-path` is relative to `.lean_constellation/source/`,
and `--resource-path` is relative to `.lean_constellation/resources/items/`.
Unselected roots are empty, so selecting only corpus TeX does not implicitly
include resources. `--no-sources` cannot be combined with path flags.

A file matches exactly; a directory includes all descendants. `sections/` does
not match `sections-extra/`. Paths are literal, not glob patterns. Selected files
are copied even when no declaration cites them. Their original relative layout
is preserved, with no automatic inclusion of linked files: select any macros,
bibliographies, figures or attribution documents needed for your setting.

An origin survives only if its resolved local target file is selected. Source
line ranges and resource locators are preserved. For resource origins, selection
must include the resource's `canonical_entry` file; choosing only its metadata
file does not retain the origin. External/unlocated origins have no matching
local file and are omitted in filtered mode. An empty origin list does not remove
the surrounding natural-language section. This filters structured origin records;
it does not rewrite citations embedded in mathematical prose or Lean comments.

Python callers pass `source_paths` and/or `resource_paths` to `export_release`:

- Both `None` (default): all materials and origins.
- Either explicitly set: filter to the union of selected paths under their two
  roots; an unspecified root selects nothing.
- `source_paths=[]` with no resource paths: no materials or origins.

The same selections apply to every repo in the pinned dependency closure. A path
need only match files in one repo; a selection matching nothing in the entire
closure fails before output is written, catching misspelled paths. No filter
configuration is added to the LHF mathematical schema.

For Ramsey, selecting `main.tex`, `macros.tex`, and `sections/` provides eight TeX
files. Its current 238 origins all point to the upstream Lean corpus file, so
those origins disappear while all 140 declarations remain. Future TeX origins
will survive the same selection automatically.

## Load or construct a workspace

```bash
python -m lean_constellation.lhf validate /path/to/new-workspace
```

```python
from pathlib import Path
from lean_constellation.lhf import load_workspace, validate_workspace, write_metadata

view = load_workspace(Path('/path/to/new-workspace'))
main = view.repos[view.metadata.main_repo]
for ref in main.nodes['Main'].exports:
    print(main.declarations[ref.node][ref.name].summary)
```

`WorkspaceData` contains workspace metadata and `RepoData` aggregates; each repo
contains its metadata, nodes keyed by dotted path, and declarations keyed by node
and catalog name. All model classes are exported from `lean_constellation.lhf`.
A manual producer can construct these models, write Lean files normally, and fill
`Section.fl` from `Path.read_text()` without compiler extraction. Then call
`validate_workspace(view, root)` and `write_metadata(root, view)` against fresh
metadata. Existing addressed metadata is not overwritten. This low-level writer
is for producer-owned directories; the Release exporter supplies the complete
staging/rename transaction. A restructuring scheduler and Agent capture tools are
separate work, not part of this exporter.

Validation checks a connected Main tree, immediate children, leaf ownership,
unique declaration identities, resolved internal dependencies/exports, provider
Main boundaries, source/material existence, no path escapes/symlinks, and no
unindexed node/declaration JSON. It does not infer public APIs from imports or
recheck the underlying Lean environment.

## Restructure acceptance export

Section-based Restructure uses a separate adapter; it does not manufacture a Native
Release. Final acceptance seals the successful receipt, plan, Content metadata,
source inventory and cited provenance into an immutable directory. Later working
source or metadata edits cannot change that export. Explicit v1 migration is
required before sealing.

```sh
python -m lean_constellation.lhf export-restructure \
  --acceptance Ramsey=/path/to/accepted/final-operation \
  --main-repo Ramsey --output /path/to/new-lhf
```

Repeat `--acceptance KEY=DIR` for providers, using precisely the final receipt
operation IDs pinned by the consumer. Output must be fresh and outside the input
acceptances. `seal_restructure_acceptance(repo_root=..., receipt_path=...,
output=...)` is the explicit Python acceptance API.

Statement and proof formal fields contain complete accepted files. A theorem's
historical declared statement may contain its placeholder proof; its protected
statement contract must match the final source and declared baseline. Proof
formal must exactly match the final source. Both mathematical sections require
source-bound metadata review. Exports retain planned interface selection, strict
references, mathematical node boundaries and section-specific dependencies and
origins. Registered `repo-local` materials and attribution are copied beneath
`.lhf/materials/source/`; unknown corpora fail rather than resolving heuristically.
The adapter verifies digests, provider pins and references, stages the result,
and reloads it with the ordinary LHF Reader before publishing. It does not run
Lean again or establish that natural-language explanations are mathematically
correct.
