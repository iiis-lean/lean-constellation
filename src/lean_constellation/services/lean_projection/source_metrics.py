"""Read-only Lean source, marker, node, and declaration statistics."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import Field, ValidationError

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.repo import RepoFormat, RepoFormatState
from lean_constellation.services.decl_graph.availability_policy import is_theorem_like
from lean_constellation.services.decl_graph.models import (
    Decl,
    DeclGraphIndex,
    DeclLifecycle,
    DeclRevision,
    DeclState,
)
from lean_constellation.services.lean_projection.annotation import (
    adjacent_declaration_pattern,
    iter_target_marker_views,
    top_level_declaration_pattern,
)
from lean_constellation.services.lean_projection.managed_file import (
    DECLARATION_SOURCE_BEGIN,
)
from lean_constellation.services.node.node_store import NodeIndex

DEFAULT_MAX_LINE_LENGTH = 100
SOURCE_STATS_SCHEMA_VERSION = 3
_EXCLUDED_DIRECTORY_NAMES = frozenset({
    ".agent_runtime",
    ".git",
    ".lake",
    ".lean_constellation",
    ".runtime",
    "build",
})
_DOC_COMMENT_RE = re.compile(r"/--.*?-/|/-!.*?-/|^[ \t]*///[^\n]*", re.DOTALL | re.MULTILINE)


class SourceStatisticsError(ValueError):
    """Raised when a source statistics request cannot read its repository root."""


class SourceMetricView(StrictModel):
    byte_count: int
    physical_line_count: int
    nonempty_line_count: int


class SourceLayerView(StrictModel):
    layer: str
    description: str
    file_count: int
    metric: SourceMetricView


class SourceRollupStatisticsView(StrictModel):
    all_source: SourceMetricView
    headerless_source: SourceMetricView
    primary_declaration: SourceMetricView


class SourceLineRiskView(StrictModel):
    file_path: str
    line: int
    character_count: int
    kind: Literal["docstring"]


class SourceMarkerAnalysisView(StrictModel):
    managed_file_count: int
    target_marker_count: int
    target_docstring_count: int
    missing_marker_files: list[str] = Field(default_factory=list)
    duplicate_marker_files: list[str] = Field(default_factory=list)
    missing_primary_files: list[str] = Field(default_factory=list)
    docstring_block_count: int
    long_docstring_line_count: int
    max_docstring_line_length: int
    long_lines: list[SourceLineRiskView] = Field(default_factory=list)


class NodeEntryStatisticsView(StrictModel):
    path: str
    kind: str
    lifecycle: str
    decl_count: int


class NodeStatisticsView(StrictModel):
    total: int
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_lifecycle: dict[str, int] = Field(default_factory=dict)
    entries: list[NodeEntryStatisticsView] = Field(default_factory=list)


class DeclStatisticsView(StrictModel):
    total: int
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_lifecycle: dict[str, int] = Field(default_factory=dict)
    by_state: dict[str, int] = Field(default_factory=dict)
    by_revision_status: dict[str, int] = Field(default_factory=dict)
    by_node: dict[str, int] = Field(default_factory=dict)
    by_visibility: dict[str, int] = Field(default_factory=dict)
    by_kind_and_state: dict[str, dict[str, int]] = Field(default_factory=dict)
    theorem_like_total: int = 0
    theorem_like_proved: int = 0
    theorem_like_remaining: int = 0


class LeanSourceStatisticsView(StrictModel):
    schema_version: Literal[3] = SOURCE_STATS_SCHEMA_VERSION
    repo_root: str
    lean_file_count: int
    excluded_directory_names: list[str]
    layers: list[SourceLayerView]
    rollups: SourceRollupStatisticsView
    markers: SourceMarkerAnalysisView
    graph_status: Literal["available", "unavailable", "invalid"]
    nodes: NodeStatisticsView | None = None
    decls: DeclStatisticsView | None = None
    warnings: list[str] = Field(default_factory=list)


@dataclass
class _MetricAccumulator:
    byte_count: int = 0
    physical_line_count: int = 0
    nonempty_line_count: int = 0

    def add(self, text: str) -> None:
        self.byte_count += len(text.encode("utf-8"))
        # Partition boundaries are aligned to line ends below, so ordinary physical-line
        # counting remains additive even when a file has no trailing newline.
        self.physical_line_count += len(text.splitlines())
        self.nonempty_line_count += sum(bool(line.strip()) for line in text.splitlines())

    def view(self) -> SourceMetricView:
        return SourceMetricView(
            byte_count=self.byte_count,
            physical_line_count=self.physical_line_count,
            nonempty_line_count=self.nonempty_line_count,
        )


@dataclass
class _LayerAccumulator:
    file_count: int = 0
    metric: _MetricAccumulator = field(default_factory=_MetricAccumulator)

    def add(self, text: str) -> None:
        self.file_count += 1
        self.metric.add(text)

    def extend(self, text: str) -> None:
        """Add another fragment belonging to an already-counted source file."""
        self.metric.add(text)


def build_source_statistics(
    repo_root: Path,
    *,
    max_line_length: int = DEFAULT_MAX_LINE_LENGTH,
) -> LeanSourceStatisticsView:
    """Build a read-only source report without starting LC/ARK/Lean services."""

    root = Path(repo_root).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise SourceStatisticsError(f"Repository root does not exist or is not a directory: {root}")
    if max_line_length < 1:
        raise SourceStatisticsError("max_line_length must be positive")

    layer_accumulators = {
        "all_source": _LayerAccumulator(),
        "managed_header": _LayerAccumulator(),
        "support_import_only": _LayerAccumulator(),
        "managed_docstring": _LayerAccumulator(),
        "unmanaged_preamble_helpers": _LayerAccumulator(),
        "primary_declaration": _LayerAccumulator(),
        "formatting_gap": _LayerAccumulator(),
    }
    warnings: list[str] = []
    marker_files = 0
    target_marker_count = 0
    target_docstring_count = 0
    missing_marker_files: list[str] = []
    duplicate_marker_files: list[str] = []
    missing_primary_files: list[str] = []
    docstring_block_count = 0
    long_docstring_line_count = 0
    max_docstring_line_length = 0
    long_lines: list[SourceLineRiskView] = []
    lean_files = _lean_files(root)

    for path in lean_files:
        rel_path = path.relative_to(root).as_posix()
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            warnings.append(f"Skipped non-UTF-8 Lean source `{rel_path}`: {exc}.")
            continue
        except OSError as exc:
            warnings.append(f"Skipped unreadable Lean source `{rel_path}`: {exc}.")
            continue

        layer_accumulators["all_source"].add(text)
        source_marker_count = text.count(DECLARATION_SOURCE_BEGIN)
        if source_marker_count != 1:
            layer_accumulators["support_import_only"].add(text)
            if source_marker_count:
                warnings.append(
                    f"`{rel_path}` has {source_marker_count} declaration-source markers; classified as support source."
                )
        else:
            marker_files += 1
            _partition_managed_file(
                text,
                rel_path=rel_path,
                layers=layer_accumulators,
                missing_marker_files=missing_marker_files,
                duplicate_marker_files=duplicate_marker_files,
                missing_primary_files=missing_primary_files,
                warnings=warnings,
            )

        target_markers = iter_target_marker_views(text)
        target_marker_count += len(target_markers)
        target_docstring_count += len({marker.docstring_start_offset for marker in target_markers})
        for doc_match in _DOC_COMMENT_RE.finditer(text):
            docstring_block_count += 1
            start_line = text.count("\n", 0, doc_match.start()) + 1
            for offset, line in enumerate(doc_match.group(0).splitlines()):
                line_number = start_line + offset
                line_length = len(line)
                max_docstring_line_length = max(max_docstring_line_length, line_length)
                if line_length <= max_line_length:
                    continue
                long_docstring_line_count += 1
                long_lines.append(
                    SourceLineRiskView(
                        file_path=rel_path,
                        line=line_number,
                        character_count=line_length,
                        kind="docstring",
                    )
                )

    nodes, decls, graph_status = _read_graph_statistics(root, warnings)
    layers = [
        SourceLayerView(
            layer=layer,
            description=description,
            file_count=layer_accumulators[layer].file_count,
            metric=layer_accumulators[layer].metric.view(),
        )
        for layer, description in _LAYER_DESCRIPTIONS
    ]
    all_source_metric = layer_accumulators["all_source"].metric.view()
    managed_header_metric = layer_accumulators["managed_header"].metric.view()
    managed_docstring_metric = layer_accumulators["managed_docstring"].metric.view()
    return LeanSourceStatisticsView(
        repo_root=str(root),
        lean_file_count=len(lean_files),
        excluded_directory_names=sorted(_EXCLUDED_DIRECTORY_NAMES),
        layers=layers,
        rollups=SourceRollupStatisticsView(
            all_source=all_source_metric,
            headerless_source=_subtract_metrics(
                all_source_metric,
                managed_header_metric,
                managed_docstring_metric,
            ),
            primary_declaration=layer_accumulators["primary_declaration"].metric.view(),
        ),
        markers=SourceMarkerAnalysisView(
            managed_file_count=marker_files,
            target_marker_count=target_marker_count,
            target_docstring_count=target_docstring_count,
            missing_marker_files=sorted(missing_marker_files),
            duplicate_marker_files=sorted(duplicate_marker_files),
            missing_primary_files=sorted(missing_primary_files),
            docstring_block_count=docstring_block_count,
            long_docstring_line_count=long_docstring_line_count,
            max_docstring_line_length=max_docstring_line_length,
            long_lines=sorted(long_lines, key=lambda item: (item.file_path, item.line, item.kind)),
        ),
        graph_status=graph_status,
        nodes=nodes,
        decls=decls,
        warnings=warnings,
    )


def render_source_statistics_markdown(report: LeanSourceStatisticsView) -> str:
    """Render a compact human-readable view of a source statistics report."""

    lines = [
        f"# Lean source statistics: `{report.repo_root}`",
        "",
        f"- Lean files: **{report.lean_file_count}**",
        f"- Graph status: **{report.graph_status}**",
        "",
        "## Source layers",
        "",
        "| Layer | Files | Bytes | Physical lines | Non-empty lines |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for layer in report.layers:
        metric = layer.metric
        lines.append(
            f"| `{layer.layer}` | {layer.file_count} | {metric.byte_count} | "
            f"{metric.physical_line_count} | {metric.nonempty_line_count} |"
        )
    lines.extend(
        [
            "",
            "## Source rollups",
            "",
            "| Rollup | Bytes | Physical lines | Non-empty lines |",
            "| --- | ---: | ---: | ---: |",
            _render_rollup_row("all_source", report.rollups.all_source),
            _render_rollup_row("headerless_source", report.rollups.headerless_source),
            _render_rollup_row("primary_declaration", report.rollups.primary_declaration),
            "",
            "## Marker and docstring risks",
            "",
            f"- Target markers: **{report.markers.target_marker_count}**.",
            f"- Long docstring lines: **{report.markers.long_docstring_line_count}**.",
        ]
    )
    if report.decls is not None:
        lines.extend(
            [
                "",
                "## Declarations",
                "",
                f"- Total Decl records: **{report.decls.total}**",
                f"- Lifecycle: `{json.dumps(report.decls.by_lifecycle, ensure_ascii=False, sort_keys=True)}`",
                f"- State: `{json.dumps(report.decls.by_state, ensure_ascii=False, sort_keys=True)}`",
                f"- Visibility: `{json.dumps(report.decls.by_visibility, ensure_ascii=False, sort_keys=True)}`",
                (
                    "- Theorem-like proof progress: "
                    f"**{report.decls.theorem_like_proved}/{report.decls.theorem_like_total}** proved; "
                    f"**{report.decls.theorem_like_remaining}** remaining."
                ),
            ]
        )
    if report.warnings:
        lines.extend(["", "## Warnings", "", *[f"- {warning}" for warning in report.warnings]])
    return "\n".join(lines) + "\n"


_LAYER_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("all_source", "All scanned Lean source files."),
    ("managed_header", "Managed imports, fixed region markers, and their fixed separators."),
    ("support_import_only", "Lean files without one current declaration-source marker."),
    ("managed_docstring", "The current LC-generated target docstring."),
    ("unmanaged_preamble_helpers", "Source after declaration-source-begin and before the target docstring."),
    ("primary_declaration", "The marker-adjacent primary declaration through end of file."),
    ("formatting_gap", "Whitespace between the managed docstring and primary declaration."),
)


def _lean_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for current_root, directory_names, file_names in os.walk(root):
        directory_names[:] = sorted(
            name for name in directory_names if name not in _EXCLUDED_DIRECTORY_NAMES
        )
        paths.extend(Path(current_root) / name for name in file_names if name.endswith(".lean"))
    return sorted(paths)


def _partition_managed_file(
    text: str,
    *,
    rel_path: str,
    layers: dict[str, _LayerAccumulator],
    missing_marker_files: list[str],
    duplicate_marker_files: list[str],
    missing_primary_files: list[str],
    warnings: list[str],
) -> None:
    source_marker_start = text.index(DECLARATION_SOURCE_BEGIN)
    source_marker_end = _line_end(text, source_marker_start)
    layers["managed_header"].add(text[:source_marker_end])
    target_markers = [
        marker
        for marker in iter_target_marker_views(text)
        if marker.docstring_start_offset >= source_marker_end
    ]
    if not target_markers:
        missing_marker_files.append(rel_path)
        layers["unmanaged_preamble_helpers"].add(text[source_marker_end:])
        warnings.append(f"`{rel_path}` has a declaration-source marker but no target marker after it.")
        return
    if len(target_markers) != 1:
        duplicate_marker_files.append(rel_path)
        layers["unmanaged_preamble_helpers"].add(text[source_marker_end:])
        warnings.append(f"`{rel_path}` has {len(target_markers)} target markers; source partition is not canonical.")
        return

    marker = target_markers[0]
    docstring_end = _line_end(text, marker.docstring_end_offset)
    layers["managed_docstring"].add(text[marker.docstring_start_offset:docstring_end])
    suffix = text[docstring_end:]
    declaration = adjacent_declaration_pattern().match(suffix)
    if declaration is None:
        missing_primary_files.append(rel_path)
        layers["unmanaged_preamble_helpers"].add(text[source_marker_end:marker.docstring_start_offset])
        layers["unmanaged_preamble_helpers"].extend(suffix)
        warnings.append(f"`{rel_path}` has a target marker but no adjacent primary declaration.")
        return

    declaration_kind_offset = docstring_end + declaration.start("kind")
    primary_start = text.rfind("\n", 0, declaration_kind_offset) + 1
    layers["unmanaged_preamble_helpers"].add(text[source_marker_end:marker.docstring_start_offset])
    layers["formatting_gap"].add(text[docstring_end:primary_start])
    layers["primary_declaration"].add(text[primary_start:])
    later = _later_top_level_declarations(text[primary_start:])
    if later:
        warnings.append(
            f"`{rel_path}` has top-level declarations after the primary declaration: {', '.join(later)}."
        )


def _later_top_level_declarations(primary_text: str) -> list[str]:
    lines = primary_text.splitlines()
    if not lines:
        return []
    pattern = top_level_declaration_pattern()
    names: list[str] = []
    for line in lines[1:]:
        match = pattern.match(line)
        if match is not None:
            names.append(match.group("name"))
    return names


def _read_graph_statistics(
    root: Path,
    warnings: list[str],
) -> tuple[NodeStatisticsView | None, DeclStatisticsView | None, Literal["available", "unavailable", "invalid"]]:
    index_path = root / ".lean_constellation" / "index" / "nodes.json"
    if not index_path.exists():
        warnings.append("Current-schema node index is unavailable; node/Decl statistics were not computed.")
        return None, None, "unavailable"
    index = _read_model(index_path, NodeIndex, warnings)
    if index is None:
        return None, None, "invalid"
    repo_format = RepoFormat.UNKNOWN
    repo_format_path = root / ".lean_constellation" / "repo_format.json"
    if repo_format_path.exists():
        repo_format_state = _read_model(repo_format_path, RepoFormatState, warnings)
        if repo_format_state is None:
            return None, None, "invalid"
        repo_format = repo_format_state.repo_format

    node_kind = Counter()
    node_lifecycle = Counter()
    node_views: list[NodeEntryStatisticsView] = []
    decl_kind = Counter()
    decl_lifecycle = Counter()
    decl_state = Counter()
    decl_revision_status = Counter()
    decl_node = Counter()
    decl_visibility = Counter()
    decl_kind_and_state: dict[str, Counter[str]] = {}
    decl_total = 0
    theorem_like_total = 0
    theorem_like_proved = 0
    invalid = False
    for entry in index.entries:
        node_path = entry.path
        kind = entry.kind
        lifecycle = entry.lifecycle
        node_id = entry.node_id
        node_kind[kind] += 1
        node_lifecycle[lifecycle] += 1
        decl_count = 0
        graph_root = root / ".lean_constellation" / "nodes" / node_id / "decl_graph"
        decl_root = graph_root / "decls"
        decl_names: list[str] = []
        graph_index_path = graph_root / "index.json"
        graph_expected = kind == "content" or (
            kind == "scope" and node_path == "Main" and repo_format == RepoFormat.ADAPTER
        )
        if graph_index_path.exists() and not graph_expected:
            warnings.append(
                f"DeclGraph index is attached to a node that cannot own a catalog: `{graph_index_path}`."
            )
            invalid = True
        elif graph_expected:
            graph_index = _read_model(graph_index_path, DeclGraphIndex, warnings)
            if graph_index is None:
                invalid = True
            elif graph_index.node_id != node_id or graph_index.node_path != node_path:
                warnings.append(
                    "Current-schema DeclGraph index identity does not match its NodeIndex entry: "
                    f"`{graph_root / 'index.json'}`."
                )
                invalid = True
            else:
                decl_names = graph_index.decl_names
                disk_names = (
                    sorted(
                        path.name
                        for path in decl_root.iterdir()
                        if path.is_dir() and (path / "decl.json").is_file()
                    )
                    if decl_root.is_dir()
                    else []
                )
                if disk_names != decl_names:
                    warnings.append(
                        "Current-schema DeclGraph index declaration inventory does not match canonical files: "
                        f"`{graph_root / 'index.json'}`."
                    )
                    invalid = True
        for decl_name in decl_names:
            decl_path = decl_root / decl_name / "decl.json"
            decl = _read_model(decl_path, Decl, warnings)
            if decl is None:
                invalid = True
                continue
            if decl.name != decl_name or decl.node_path != node_path:
                warnings.append(
                    f"Current-schema declaration identity does not match its graph inventory: `{decl_path}`."
                )
                invalid = True
                continue
            decl_count += 1
            decl_total += 1
            decl_kind[decl.kind] += 1
            decl_lifecycle[decl.lifecycle.value] += 1
            decl_node[node_path] += 1
            decl_visibility["public" if decl.public else "private"] += 1
            if decl.current_revision not in decl.revision_ids:
                warnings.append(f"Current declaration revision is absent from revision_ids: `{decl_path}`.")
                invalid = True
                continue
            revision_path = decl_path.parent / "revisions" / f"{decl.current_revision}.json"
            revision = _read_model(revision_path, DeclRevision, warnings)
            if revision is None:
                invalid = True
                continue
            if revision.revision != decl.current_revision:
                warnings.append(f"Current declaration revision identity does not match its catalog: `{revision_path}`.")
                invalid = True
                continue
            state = revision.state.value
            decl_state[state] += 1
            decl_revision_status[revision.status.value] += 1
            decl_kind_and_state.setdefault(decl.kind, Counter())[state] += 1
            if entry.active and decl.lifecycle == DeclLifecycle.ACTIVE and is_theorem_like(decl.kind):
                theorem_like_total += 1
                if revision.state == DeclState.PROVED:
                    theorem_like_proved += 1
        node_views.append(
            NodeEntryStatisticsView(
                path=node_path,
                kind=kind,
                lifecycle=lifecycle,
                decl_count=decl_count,
            )
        )

    if invalid:
        return None, None, "invalid"

    return (
        NodeStatisticsView(
            total=len(node_views),
            by_kind=dict(sorted(node_kind.items())),
            by_lifecycle=dict(sorted(node_lifecycle.items())),
            entries=sorted(node_views, key=lambda item: item.path),
        ),
        DeclStatisticsView(
            total=decl_total,
            by_kind=dict(sorted(decl_kind.items())),
            by_lifecycle=dict(sorted(decl_lifecycle.items())),
            by_state=dict(sorted(decl_state.items())),
            by_revision_status=dict(sorted(decl_revision_status.items())),
            by_node=dict(sorted(decl_node.items())),
            by_visibility=dict(sorted(decl_visibility.items())),
            by_kind_and_state={
                kind: dict(sorted(counts.items()))
                for kind, counts in sorted(decl_kind_and_state.items())
            },
            theorem_like_total=theorem_like_total,
            theorem_like_proved=theorem_like_proved,
            theorem_like_remaining=theorem_like_total - theorem_like_proved,
        ),
        "available",
    )


_ModelT = TypeVar("_ModelT", bound=StrictModel)


def _read_model(path: Path, model_type: type[_ModelT], warnings: list[str]) -> _ModelT | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        warnings.append(f"Could not read current-schema graph file `{path}`: {exc}.")
        return None
    if not isinstance(payload, dict):
        warnings.append(f"Current-schema graph file is not an object: `{path}`.")
        return None
    if "schema_version" in model_type.model_fields and "schema_version" not in payload:
        warnings.append(f"Current-schema graph file does not declare `schema_version`: `{path}`.")
        return None
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        warnings.append(f"Could not read current-schema graph file `{path}`: {exc}.")
        return None


def _subtract_metrics(total: SourceMetricView, *excluded: SourceMetricView) -> SourceMetricView:
    return SourceMetricView(
        byte_count=total.byte_count - sum(item.byte_count for item in excluded),
        physical_line_count=total.physical_line_count - sum(item.physical_line_count for item in excluded),
        nonempty_line_count=total.nonempty_line_count - sum(item.nonempty_line_count for item in excluded),
    )


def _render_rollup_row(name: str, metric: SourceMetricView) -> str:
    return (
        f"| `{name}` | {metric.byte_count} | "
        f"{metric.physical_line_count} | {metric.nonempty_line_count} |"
    )


def _line_end(text: str, offset: int) -> int:
    newline = text.find("\n", offset)
    return len(text) if newline < 0 else newline + 1
