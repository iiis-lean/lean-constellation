"""Strict parser and renderer for system-managed Decl file regions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lean_constellation.services.foundation import ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.lean_projection.annotation import AnnotationComponent
    from lean_constellation.services.runtime import LeanRuntimeServices


MANAGED_IMPORTS_BEGIN = "-- lean-constellation: managed-imports-begin"
MANAGED_IMPORTS_END = "-- lean-constellation: managed-imports-end"
DECLARATION_SOURCE_BEGIN = "-- lean-constellation: declaration-source-begin"


class ManagedDeclFileComponent:
    def __init__(self, runtime: LeanRuntimeServices, *, annotation: AnnotationComponent) -> None:
        self.runtime = runtime
        self.annotation = annotation

    def render_new(self, *, imports: list[str], docstring: str) -> str:
        return (
            self._imports_block(imports)
            + "\n\n"
            + DECLARATION_SOURCE_BEGIN
            + "\n\n"
            + docstring.rstrip()
            + "\n"
        )

    def refresh(self, file_text: str, *, imports: list[str], docstring: str) -> ServiceResult[str]:
        regions = self._regions(file_text)
        if not regions.ok or regions.value is None:
            return self.runtime.foundation.fail(regions.issues)
        import_start, import_end, source_start = regions.value
        prefix = file_text[:import_start]
        source = file_text[source_start:]
        marker = self.annotation.parse_target_marker(source)
        if not marker.ok or marker.value is None:
            return self.runtime.foundation.fail(marker.issues)
        doc_start = marker.value.docstring_start_offset
        doc_end = marker.value.docstring_end_offset
        refreshed_source = source[:doc_start] + docstring.rstrip() + source[doc_end:]
        text = prefix + self._imports_block(imports) + "\n\n" + refreshed_source
        if not text.endswith("\n"):
            text += "\n"
        return self.runtime.foundation.ok(text)

    def validate(self, file_text: str) -> ServiceResult[None]:
        regions = self._regions(file_text)
        if not regions.ok:
            return self.runtime.foundation.fail(regions.issues)
        return self.runtime.foundation.ok(None)

    def _regions(self, file_text: str) -> ServiceResult[tuple[int, int, int]]:
        counts = {
            MANAGED_IMPORTS_BEGIN: file_text.count(MANAGED_IMPORTS_BEGIN),
            MANAGED_IMPORTS_END: file_text.count(MANAGED_IMPORTS_END),
            DECLARATION_SOURCE_BEGIN: file_text.count(DECLARATION_SOURCE_BEGIN),
        }
        invalid = {marker: count for marker, count in counts.items() if count != 1}
        if invalid:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_managed_region_invalid",
                    "A Decl-owned Lean file must contain each managed-region marker exactly once.",
                    details={"counts": str(invalid)},
                )
            )
        import_start = file_text.index(MANAGED_IMPORTS_BEGIN)
        import_end = file_text.index(MANAGED_IMPORTS_END) + len(MANAGED_IMPORTS_END)
        source_marker = file_text.index(DECLARATION_SOURCE_BEGIN)
        source_start = source_marker
        if not import_start <= import_end < source_marker:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_managed_region_order_invalid",
                    "Managed imports must precede the declaration source region.",
                )
            )
        before_imports = file_text[:import_start]
        if before_imports.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_managed_imports_not_first",
                    "The managed imports region must be the first non-whitespace content in a Decl-owned file.",
                )
            )
        managed_block = file_text[import_start:import_end]
        for line in managed_block.splitlines()[1:-1]:
            if line and not line.startswith("import "):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_managed_import_line_invalid",
                        "Only Lean import commands may appear inside managed imports.",
                        current=line,
                    )
                )
        between_regions = file_text[import_end:source_marker]
        if between_regions.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_managed_region_gap_invalid",
                    "Only whitespace may appear between managed imports and the declaration source marker.",
                    current=between_regions.strip(),
                )
            )
        return self.runtime.foundation.ok((import_start, import_end, source_start))

    def _imports_block(self, imports: list[str]) -> str:
        modules = list(dict.fromkeys(module.strip() for module in imports if module and module.strip()))
        lines = [MANAGED_IMPORTS_BEGIN, *(f"import {module}" for module in modules), MANAGED_IMPORTS_END]
        return "\n".join(lines)


__all__ = [
    "DECLARATION_SOURCE_BEGIN",
    "MANAGED_IMPORTS_BEGIN",
    "MANAGED_IMPORTS_END",
    "ManagedDeclFileComponent",
]
