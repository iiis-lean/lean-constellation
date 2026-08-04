"""Read-only Lean source, marker, node, and declaration statistics."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.lean_projection.annotation import (
    adjacent_declaration_pattern,
    iter_target_marker_views,
    top_level_declaration_pattern,
)
from lean_constellation.services.lean_projection.managed_file import (
    DECLARATION_SOURCE_BEGIN,
)


DEFAULT_MAX_LINE_LENGTH = 100
SOURCE_STATS_SCHEMA_VERSION = 1
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


class SourceLineRiskView(StrictModel):
    file_path: str
    line: int
    character_count: int
    kind: Literal["target_marker", "docstring"]
    policy_exempt: bool


class SourceMarkerAnalysisView(StrictModel):
    managed_file_count: int
    target_marker_count: int
    target_docstring_count: int
    missing_marker_files: list[str] = Field(default_factory=list)
    duplicate_marker_files: list[str] = Field(default_factory=list)
    missing_primary_files: list[str] = Field(default_factory=list)
    long_target_marker_count: int
    max_target_marker_length: int
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


class LeanSourceStatisticsView(StrictModel):
    schema_version: Literal[1] = SOURCE_STATS_SCHEMA_VERSION
    repo_root: str
    lean_file_count: int
    excluded_directory_names: list[str]
    layers: list[SourceLayerView]
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
    long_target_marker_count = 0
    max_target_marker_length = 0
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
        for marker in target_markers:
            marker_line = _line_text(text, marker.marker_line)
            marker_length = len(marker_line)
            max_target_marker_length = max(max_target_marker_length, marker_length)
            if marker_length > max_line_length:
                long_target_marker_count += 1
                long_lines.append(
                    SourceLineRiskView(
                        file_path=rel_path,
                        line=marker.marker_line,
                        character_count=marker_length,
                        kind="target_marker",
                        policy_exempt=True,
                    )
                )

        for doc_match in _DOC_COMMENT_RE.finditer(text):
            docstring_block_count += 1
            start_line = text.count("\n", 0, doc_match.start()) + 1
            for offset, line in enumerate(doc_match.group(0).splitlines()):
                line_number = start_line + offset
                line_length = len(line)
                max_docstring_line_length = max(max_docstring_line_length, line_length)
                if line_length <= max_line_length:
                    continue
                if line_number in {marker.marker_line for marker in target_markers}:
                    continue
                long_docstring_line_count += 1
                long_lines.append(
                    SourceLineRiskView(
                        file_path=rel_path,
                        line=line_number,
                        character_count=line_length,
                        kind="docstring",
                        policy_exempt=False,
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
    return LeanSourceStatisticsView(
        repo_root=str(root),
        lean_file_count=len(lean_files),
        excluded_directory_names=sorted(_EXCLUDED_DIRECTORY_NAMES),
        layers=layers,
        markers=SourceMarkerAnalysisView(
            managed_file_count=marker_files,
            target_marker_count=target_marker_count,
            target_docstring_count=target_docstring_count,
            missing_marker_files=sorted(missing_marker_files),
            duplicate_marker_files=sorted(duplicate_marker_files),
            missing_primary_files=sorted(missing_primary_files),
            long_target_marker_count=long_target_marker_count,
            max_target_marker_length=max_target_marker_length,
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
            "## Marker and docstring risks",
            "",
            f"- Target markers: **{report.markers.target_marker_count}**; max length: **{report.markers.max_target_marker_length}**.",
            f"- Long target markers: **{report.markers.long_target_marker_count}** (policy-exempt).",
            f"- Long non-marker docstring lines: **{report.markers.long_docstring_line_count}**.",
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
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        warnings.append(f"Current-schema node index could not be read: {exc}.")
        return None, None, "invalid"
    entries = index.get("entries")
    if index.get("schema_version") != 1:
        warnings.append(
            f"Unsupported current node index schema version: {index.get('schema_version')!r}."
        )
        return None, None, "invalid"
    if not isinstance(entries, list):
        warnings.append("Current-schema node index has no valid `entries` list.")
        return None, None, "invalid"

    node_kind = Counter()
    node_lifecycle = Counter()
    node_views: list[NodeEntryStatisticsView] = []
    decl_kind = Counter()
    decl_lifecycle = Counter()
    decl_state = Counter()
    decl_revision_status = Counter()
    decl_node = Counter()
    decl_total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            warnings.append("Node index contains a non-object entry.")
            continue
        node_path = str(entry.get("path") or "<unknown>")
        kind = str(entry.get("kind") or "unknown")
        lifecycle = str(entry.get("lifecycle") or "unknown")
        node_id = entry.get("node_id")
        node_kind[kind] += 1
        node_lifecycle[lifecycle] += 1
        decl_count = 0
        decl_root = root / ".lean_constellation" / "nodes" / str(node_id) / "decl_graph" / "decls"
        if node_id and decl_root.is_dir():
            for decl_path in sorted(decl_root.glob("*/decl.json")):
                decl = _read_json(decl_path, warnings)
                if decl is None:
                    continue
                decl_count += 1
                decl_total += 1
                decl_kind[str(decl.get("kind") or "unknown")] += 1
                decl_lifecycle[str(decl.get("lifecycle") or "unknown")] += 1
                decl_node[node_path] += 1
                current_revision = decl.get("current_revision")
                revision_path = decl_path.parent / "revisions" / f"{current_revision}.json"
                revision = _read_json(revision_path, warnings) if current_revision is not None else None
                if revision is None:
                    decl_state["unknown"] += 1
                    decl_revision_status["unknown"] += 1
                else:
                    decl_state[str(revision.get("state") or "unknown")] += 1
                    decl_revision_status[str(revision.get("status") or "unknown")] += 1
        node_views.append(
            NodeEntryStatisticsView(
                path=node_path,
                kind=kind,
                lifecycle=lifecycle,
                decl_count=decl_count,
            )
        )

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
        ),
        "available",
    )


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        warnings.append(f"Could not read current-schema graph file `{path}`: {exc}.")
        return None
    if not isinstance(value, dict):
        warnings.append(f"Current-schema graph file is not an object: `{path}`.")
        return None
    return value


def _line_end(text: str, offset: int) -> int:
    newline = text.find("\n", offset)
    return len(text) if newline < 0 else newline + 1


def _line_text(text: str, line_number: int) -> str:
    lines = text.splitlines()
    if line_number < 1 or line_number > len(lines):
        return ""
    return lines[line_number - 1]
