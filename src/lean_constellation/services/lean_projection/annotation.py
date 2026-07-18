"""Managed Markdown docstrings and marker-adjacent Lean declarations."""

from __future__ import annotations

import re
import textwrap
import unicodedata
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import field_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import GateReport, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.decl_graph.models import DeclFileRevisionView, DeclOriginRef
    from lean_constellation.services.runtime import LeanRuntimeServices


class ResolvedRepoDeclDependencyProjection(StrictModel):
    kind: Literal["repo_decl"] = "repo_decl"
    repo_key: str | None = None
    node_path: str
    decl_name: str
    module: str
    lean_decl_name: str | None = None
    resolved_revision: int | None = None


class ResolvedMathlibDependencyProjection(StrictModel):
    kind: Literal["mathlib_decl"] = "mathlib_decl"
    lean_decl_name: str
    module: str


ResolvedDependencyProjection = ResolvedRepoDeclDependencyProjection | ResolvedMathlibDependencyProjection


class TargetMarkerView(StrictModel):
    decl_name: str
    marker_line: int
    docstring_start_line: int
    docstring_end_line: int
    docstring_start_offset: int
    docstring_end_offset: int
    docstring: str
    summary: str

    @field_validator("decl_name")
    @classmethod
    def _non_empty_decl_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("decl_name must be non-empty")
        return value.strip()


class LeanDeclarationLocationView(StrictModel):
    source_name: str
    candidate_full_name: str
    namespace: str | None = None
    kind: str
    start_line: int
    header_end_line: int
    header: str
    summary: str

    @field_validator("source_name", "candidate_full_name", "kind", "header")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("value must be non-empty")
        return value.strip()


class ExternalDeclarationLocationView(StrictModel):
    """A declaration located in Adapter-captured upstream source text."""

    source_name: str
    lean_decl_name: str
    kind: str
    declaration_line: int
    source_name_start_offset: int
    source_name_end_offset: int
    summary: str


class ExternalDeclarationProbeView(StrictModel):
    """Isolated source used to compare an Adapter capture with Lean truth."""

    code: str
    probe_lean_decl_name: str
    source: ExternalDeclarationLocationView
    summary: str


class PrimaryDeclarationSourceView(StrictModel):
    """Primary declaration source without managed imports, helpers, or docstring."""

    code: str
    start_line: int
    line_count: int
    source_kind: Literal["managed", "external"]
    summary: str


class AnnotationComponent:
    """Render only the controlled docstring and identify its adjacent declaration."""

    _DOCSTRING_RE = re.compile(r"/--(?P<body>.*?)-/", re.DOTALL)
    _MARKER_RE = re.compile(
        r"^\s*#\s+lean-constellation\s+target:\s+`(?P<decl_name>[^`]+)`\s*$"
    )
    _DECL_KEYWORDS = (
        "def",
        "theorem",
        "lemma",
        "instance",
        "abbrev",
        "structure",
        "class",
        "inductive",
        "opaque",
        "axiom",
    )
    _MODIFIERS = (
        "private",
        "protected",
        "noncomputable",
        "unsafe",
        "partial",
        "scoped",
    )
    _SOURCE_NAME = r"(?:_root_\.)?[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*"

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def render_statement_docstring(
        self,
        revision: DeclFileRevisionView,
        *,
        dependencies: Sequence[ResolvedDependencyProjection] | None = None,
    ) -> ServiceResult[str]:
        resolved = self._require_resolved_dependencies(revision.statement.dep_refs, dependencies)
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        return self.runtime.foundation.ok(
            self._render_docstring(
                decl_name=revision.decl_name,
                statement_text=revision.statement.nl.text,
                statement_origins=revision.statement.nl.origin,
                statement_dependencies=resolved.value,
            )
        )

    def render_proof_docstring(
        self,
        revision: DeclFileRevisionView,
        *,
        statement_dependencies: Sequence[ResolvedDependencyProjection] | None = None,
        proof_dependencies: Sequence[ResolvedDependencyProjection] | None = None,
    ) -> ServiceResult[str]:
        statement = self._require_resolved_dependencies(revision.statement.dep_refs, statement_dependencies)
        if not statement.ok or statement.value is None:
            return self.runtime.foundation.fail(statement.issues)
        proof_refs = revision.proof.dep_refs if revision.proof is not None else []
        proof = self._require_resolved_dependencies(proof_refs, proof_dependencies)
        if not proof.ok or proof.value is None:
            return self.runtime.foundation.fail(proof.issues)
        return self.runtime.foundation.ok(
            self._render_docstring(
                decl_name=revision.decl_name,
                statement_text=revision.statement.nl.text,
                statement_origins=revision.statement.nl.origin,
                statement_dependencies=statement.value,
                proof_text=revision.proof.nl.text if revision.proof is not None else None,
                proof_origins=revision.proof.nl.origin if revision.proof is not None else [],
                proof_dependencies=proof.value,
            )
        )

    def parse_target_marker(self, file_text: str) -> ServiceResult[TargetMarkerView]:
        markers: list[TargetMarkerView] = []
        for match in self._DOCSTRING_RE.finditer(file_text):
            body = match.group("body")
            body_start_line = self._line_number(file_text, match.start("body"))
            for offset, line in enumerate(body.splitlines()):
                marker = self._MARKER_RE.match(line)
                if marker is None:
                    continue
                decl_name = marker.group("decl_name").strip()
                markers.append(
                    TargetMarkerView(
                        decl_name=decl_name,
                        marker_line=body_start_line + offset,
                        docstring_start_line=self._line_number(file_text, match.start()),
                        docstring_end_line=self._line_number(file_text, match.end()),
                        docstring_start_offset=match.start(),
                        docstring_end_offset=match.end(),
                        docstring=match.group(0),
                        summary=f"Found target marker for {decl_name}.",
                    )
                )
        if not markers:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "target_marker_missing",
                    "No current-format lean-constellation target marker was found.",
                )
            )
        if len(markers) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "target_marker_duplicate",
                    "A Decl-owned Lean file must contain exactly one target marker.",
                    current=", ".join(marker.decl_name for marker in markers),
                )
            )
        return self.runtime.foundation.ok(markers[0])

    def validate_docstring(
        self,
        file_text: str,
        *,
        decl_name: str,
        stage: Literal["statement", "proof"] | str,
        expected_docstring: str,
    ) -> ServiceResult[GateReport]:
        if stage not in {"statement", "proof"}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "docstring_stage_invalid",
                    "Docstring stage must be statement or proof.",
                    field="stage",
                    current=str(stage),
                    expected="statement | proof",
                )
            )
        marker = self.parse_target_marker(file_text)
        if not marker.ok or marker.value is None:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    f"{stage}_docstring",
                    marker.issues,
                    summary="Target docstring marker is missing or invalid.",
                )
            )
        issues = []
        if marker.value.decl_name != decl_name:
            issues.append(
                self.runtime.foundation.issue(
                    "target_marker_decl_mismatch",
                    "The target marker points to a different Constellation Decl.",
                    field="decl_name",
                    current=marker.value.decl_name,
                    expected=decl_name,
                )
            )
        if self._normalize_docstring(marker.value.docstring) != self._normalize_docstring(expected_docstring):
            issues.append(
                self.runtime.foundation.issue(
                    "system_docstring_changed",
                    "The system docstring does not match the current generated projection.",
                    object_ref=decl_name,
                    field="docstring",
                )
            )
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    f"{stage}_docstring",
                    issues,
                    summary="System docstring validation failed.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                f"{stage}_docstring",
                summary=f"System {stage} docstring is synchronized.",
            )
        )

    def locate_target_declaration(self, file_text: str, *, decl_name: str) -> ServiceResult[LeanDeclarationLocationView]:
        marker = self.parse_target_marker(file_text)
        if not marker.ok or marker.value is None:
            return self.runtime.foundation.fail(marker.issues)
        if marker.value.decl_name != decl_name:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "target_marker_decl_mismatch",
                    "The target marker points to a different Constellation Decl.",
                    current=marker.value.decl_name,
                    expected=decl_name,
                )
            )
        suffix = file_text[marker.value.docstring_end_offset :]
        declaration = self._adjacent_declaration_re().match(suffix)
        if declaration is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "target_declaration_missing",
                    "A named Lean declaration must immediately follow the managed target docstring.",
                    object_ref=decl_name,
                )
            )
        source_name = declaration.group("name")
        kind = declaration.group("kind")
        declaration_offset = marker.value.docstring_end_offset + declaration.start("kind")
        start_line = self._line_number(file_text, declaration_offset)
        lines = file_text.splitlines()
        start_index = start_line - 1
        header_end_index, header = self._collect_header(lines, start_index)
        later = self._top_level_declarations_after(lines, start_index + 1)
        if later:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "target_declaration_not_last",
                    "The marker-adjacent declaration must be the final top-level declaration in its module.",
                    object_ref=decl_name,
                    current=", ".join(later),
                )
            )
        namespace = self._active_namespace(file_text[: marker.value.docstring_start_offset])
        candidate = self._candidate_full_name(namespace, source_name)
        return self.runtime.foundation.ok(
            LeanDeclarationLocationView(
                source_name=source_name,
                candidate_full_name=candidate,
                namespace=namespace or None,
                kind=kind,
                start_line=start_line,
                header_end_line=header_end_index + 1,
                header=header,
                summary=f"Located marker-adjacent {kind} {source_name} at line {start_line}.",
            )
        )

    def extract_primary_declaration_source(
        self,
        code: str,
        *,
        decl_name: str,
        lean_decl_name: str | None,
        managed: bool,
    ) -> ServiceResult[PrimaryDeclarationSourceView]:
        """Extract the registered primary declaration from one captured formal file."""

        if managed:
            located = self.locate_target_declaration(code, decl_name=decl_name)
            if not located.ok or located.value is None:
                return self.runtime.foundation.fail(located.issues)
            start_line = located.value.start_line
            source_kind: Literal["managed", "external"] = "managed"
        else:
            if not lean_decl_name:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "lean_decl_name_missing",
                        "External formal source requires a registered Lean declaration name.",
                        object_ref=decl_name,
                    )
                )
            located = self.locate_external_declaration(code, lean_decl_name=lean_decl_name)
            if not located.ok or located.value is None:
                return self.runtime.foundation.fail(located.issues)
            start_line = located.value.declaration_line
            source_kind = "external"
        lines = code.splitlines(keepends=True)
        primary = "".join(lines[start_line - 1 :]).strip()
        if not primary:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "primary_formal_source_missing",
                    "The registered primary declaration has no source text.",
                    object_ref=lean_decl_name or decl_name,
                )
            )
        return self.runtime.foundation.ok(
            PrimaryDeclarationSourceView(
                code=primary,
                start_line=start_line,
                line_count=len(primary.splitlines()),
                source_kind=source_kind,
                summary=f"Extracted {source_kind} primary declaration source at line {start_line}.",
            )
        )

    def compare_theorem_header(self, statement_code: str, proof_code: str, *, decl_name: str) -> ServiceResult[GateReport]:
        statement_header = self._extract_theorem_header(statement_code, decl_name)
        proof_header = self._extract_theorem_header(proof_code, decl_name)
        issues = [*(statement_header.issues if not statement_header.ok else []), *(proof_header.issues if not proof_header.ok else [])]
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "theorem_header",
                    issues,
                    summary="The marker-adjacent theorem header could not be extracted reliably.",
                )
            )
        assert statement_header.value is not None and proof_header.value is not None
        expected = self._normalize_header(statement_header.value)
        current = self._normalize_header(proof_header.value)
        if expected != current:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "theorem_header",
                    self.runtime.foundation.issue(
                        "theorem_header_changed",
                        "Proof formalization changed the theorem statement header.",
                        object_ref=decl_name,
                        current=current,
                        expected=expected,
                    ),
                    summary="Theorem header changed.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("theorem_header", summary="The theorem header is unchanged.")
        )

    def compare_expected_theorem_header(
        self,
        expected_code: str,
        managed_code: str,
        *,
        decl_name: str,
        lean_decl_name: str,
    ) -> ServiceResult[GateReport]:
        """Compare an unmanaged exact interface statement with managed Decl source."""

        expected_header = self._extract_named_theorem_header(expected_code, lean_decl_name)
        managed_header = self._extract_theorem_header(managed_code, decl_name)
        issues = [
            *(expected_header.issues if not expected_header.ok else []),
            *(managed_header.issues if not managed_header.ok else []),
        ]
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "expected_theorem_header",
                    issues,
                    summary="The exact interface theorem header could not be compared reliably.",
                )
            )
        assert expected_header.value is not None and managed_header.value is not None
        expected = self._normalize_header(expected_header.value)
        current = self._normalize_header(managed_header.value)
        if expected != current:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "expected_theorem_header",
                    self.runtime.foundation.issue(
                        "theorem_header_changed",
                        "The managed declaration does not preserve the exact interface theorem header.",
                        object_ref=lean_decl_name,
                        current=current,
                        expected=expected,
                    ),
                    summary="The exact interface theorem header changed.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "expected_theorem_header",
                summary="The managed declaration preserves the exact interface theorem header.",
            )
        )

    def compare_external_theorem_header(
        self,
        statement_code: str,
        proof_code: str,
        *,
        lean_decl_name: str,
    ) -> ServiceResult[GateReport]:
        """Compare an Adapter/upstream theorem that has no managed target marker."""

        statement_header = self._extract_named_theorem_header(statement_code, lean_decl_name)
        proof_header = self._extract_named_theorem_header(proof_code, lean_decl_name)
        issues = [*(statement_header.issues if not statement_header.ok else []), *(proof_header.issues if not proof_header.ok else [])]
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "external_theorem_header",
                    issues,
                    summary="The registered external theorem header could not be extracted reliably.",
                )
            )
        assert statement_header.value is not None and proof_header.value is not None
        expected = self._normalize_header(statement_header.value)
        current = self._normalize_header(proof_header.value)
        if expected != current:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "external_theorem_header",
                    self.runtime.foundation.issue(
                        "theorem_header_changed",
                        "The external proof formalization changed the registered theorem statement header.",
                        object_ref=lean_decl_name,
                        current=current,
                        expected=expected,
                    ),
                    summary="External theorem header changed.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "external_theorem_header",
                summary="The registered external theorem header is unchanged.",
            )
        )

    def locate_external_declaration(
        self,
        code: str,
        *,
        lean_decl_name: str,
    ) -> ServiceResult[ExternalDeclarationLocationView]:
        """Locate one registered Adapter declaration in captured upstream code.

        Upstream extraction can return either a fully qualified declaration or
        a single short declaration with its surrounding namespace omitted.  A
        short name is therefore accepted only when the captured text itself is
        not inside another explicit namespace.
        """

        cleaned = self._strip_lean_comments(code)
        modifiers = rf"(?:(?:{'|'.join(self._MODIFIERS)})\s+)*"
        pattern = re.compile(
            rf"(?m)^[ \t]*{modifiers}(?P<kind>{'|'.join(self._DECL_KEYWORDS)})\s+"
            rf"(?P<name>{self._SOURCE_NAME})(?=$|\s|[:({{\[]|\.\{{)"
        )
        short_name = lean_decl_name.rsplit(".", 1)[-1]
        matches: list[tuple[re.Match[str], str]] = []
        for match in pattern.finditer(cleaned):
            source_name = match.group("name")
            bare_name = source_name.removeprefix("_root_.")
            namespace = self._active_namespace(cleaned[: match.start()])
            candidate = (
                bare_name
                if source_name.startswith("_root_.") or not namespace
                else f"{namespace}.{bare_name}"
            )
            if candidate == lean_decl_name:
                matches.append((match, source_name))
                continue
            # Toolkit declaration extraction can intentionally omit the
            # enclosing namespace and return one short declaration.
            if not namespace and bare_name == short_name:
                matches.append((match, source_name))
        if len(matches) != 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "external_declaration_ambiguous",
                    "External formal code must contain exactly one declaration matching the registered Lean declaration name.",
                    object_ref=lean_decl_name,
                    current=str(len(matches)),
                    expected="1",
                )
            )
        match, source_name = matches[0]
        line = self._line_number(cleaned, match.start("kind"))
        return self.runtime.foundation.ok(
            ExternalDeclarationLocationView(
                source_name=source_name,
                lean_decl_name=lean_decl_name,
                kind=match.group("kind"),
                declaration_line=line,
                source_name_start_offset=match.start("name"),
                source_name_end_offset=match.end("name"),
                summary=f"Located registered external {match.group('kind')} {lean_decl_name} at line {line}.",
            )
        )

    def build_external_declaration_probe(
        self,
        code: str,
        *,
        lean_decl_name: str,
    ) -> ServiceResult[ExternalDeclarationProbeView]:
        """Rename and isolate a captured declaration for compiler comparison."""

        located = self.locate_external_declaration(code, lean_decl_name=lean_decl_name)
        if not located.ok or located.value is None:
            return self.runtime.foundation.fail(located.issues)
        source = located.value
        probe_name = "LeanConstellationAdapterProbe.captured"
        cleaned = self._strip_lean_comments(code)
        namespace = self._active_namespace(cleaned[: source.source_name_start_offset])
        rewritten = (
            code[: source.source_name_start_offset]
            + f"_root_.{probe_name}"
            + code[source.source_name_end_offset :]
        )
        source_name = source.source_name.removeprefix("_root_.")
        registered_namespace = lean_decl_name.rpartition(".")[0]
        needs_restored_namespace = not namespace and "." not in source_name and bool(registered_namespace)
        probe_code = rewritten.rstrip()
        if needs_restored_namespace:
            probe_code = (
                f"namespace {registered_namespace}\n\n"
                f"{probe_code}\n\n"
                f"end {registered_namespace}"
            )
        return self.runtime.foundation.ok(
            ExternalDeclarationProbeView(
                code=probe_code,
                probe_lean_decl_name=probe_name,
                source=source,
                summary=f"Built isolated compiler probe {probe_name} for {lean_decl_name}.",
            )
        )

    def _render_docstring(
        self,
        *,
        decl_name: str,
        statement_text: str | None,
        statement_origins: Sequence[DeclOriginRef],
        statement_dependencies: Sequence[ResolvedDependencyProjection],
        proof_text: str | None = None,
        proof_origins: Sequence[DeclOriginRef] = (),
        proof_dependencies: Sequence[ResolvedDependencyProjection] = (),
    ) -> str:
        body: list[str] = [f"# lean-constellation target: `{decl_name}`"]
        self._append_text(body, statement_text)
        self._append_section(body, "Sources", [self._format_origin(item) for item in statement_origins])
        self._append_section(body, "Statement dependencies", [self._format_dependency(item) for item in statement_dependencies])
        if proof_text and proof_text.strip():
            self._append_section(body, "Proof outline", [self._sanitize_doc_text(proof_text)])
        self._append_section(body, "Proof sources", [self._format_origin(item) for item in proof_origins])
        self._append_section(body, "Proof dependencies", [self._format_dependency(item) for item in proof_dependencies])
        return "/--\n" + "\n".join(body).rstrip() + "\n-/"

    def _require_resolved_dependencies(
        self,
        refs: Sequence[object],
        dependencies: Sequence[ResolvedDependencyProjection] | None,
    ) -> ServiceResult[list[ResolvedDependencyProjection]]:
        if refs and dependencies is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_projection_missing",
                    "Structured dependency refs must be resolved before docstring rendering.",
                )
            )
        values = list(dependencies or [])
        if len(values) != len(refs):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_projection_count_mismatch",
                    "Resolved dependency projections do not match the structured dependency truth.",
                    current=str(len(values)),
                    expected=str(len(refs)),
                )
            )
        return self.runtime.foundation.ok(values)

    def _append_text(self, body: list[str], text: str | None) -> None:
        value = self._sanitize_doc_text(text or "")
        if value:
            body.extend(["", self._wrap_doc_text(value)])

    def _append_section(self, body: list[str], title: str, values: Sequence[str]) -> None:
        filtered = [value for value in values if value]
        if not filtered:
            return
        body.extend(["", f"## {title}", ""])
        if title == "Proof outline":
            body.append(self._wrap_doc_text(filtered[0]))
        else:
            body.extend(self._wrap_doc_text(f"- {value}", subsequent_indent="  ") for value in filtered)

    def _wrap_doc_text(self, value: str, *, subsequent_indent: str = "") -> str:
        """Wrap generated Markdown prose so managed docstrings satisfy Lean's style linter."""

        lines: list[str] = []
        for line in value.splitlines():
            if not line.strip():
                lines.append("")
                continue
            lines.append(
                textwrap.fill(
                    line,
                    width=100,
                    subsequent_indent=subsequent_indent,
                    break_long_words=True,
                    break_on_hyphens=False,
                )
            )
        return "\n".join(lines)

    def _format_origin(self, origin: DeclOriginRef) -> str:
        if origin.kind == "source":
            path = origin.source_path or origin.ref
            if not path:
                return ""
            return self._with_range(f"Source `{path}`", origin.start_line, origin.end_line)
        if origin.kind == "resource":
            key = origin.resource_key or origin.ref
            if not key:
                return ""
            base = f"Resource `{key}`"
            locators = [item for item in (origin.start_locator, origin.end_locator) if item]
            if locators:
                base += ", " + "–".join(f"`{item}`" for item in locators)
            return self._with_range(base, origin.start_line, origin.end_line)
        reference = origin.ref or origin.source_path or origin.resource_key
        return f"Reference `{reference}`" if reference else ""

    def _format_dependency(self, dependency: ResolvedDependencyProjection) -> str:
        return self.format_dependency(dependency)

    def format_dependency(self, dependency: ResolvedDependencyProjection) -> str:
        """Render one resolved dependency in query/use/import order."""

        if dependency.kind == "mathlib_decl":
            return f"`{dependency.lean_decl_name}` from `{dependency.module}`"
        prefix = f"{dependency.repo_key}::" if dependency.repo_key else ""
        locator = f"{prefix}{dependency.node_path}::{dependency.decl_name}"
        if dependency.lean_decl_name:
            return f"`{locator}` → `{dependency.lean_decl_name}` from `{dependency.module}`"
        return f"`{locator}` from `{dependency.module}`"

    def _with_range(self, base: str, start: int | None, end: int | None) -> str:
        if start is None:
            return base
        if end is None or end == start:
            return f"{base}, line {start}"
        return f"{base}, lines {start}–{end}"

    def _adjacent_declaration_re(self) -> re.Pattern[str]:
        modifiers = "|".join(self._MODIFIERS)
        kinds = "|".join(self._DECL_KEYWORDS)
        return re.compile(
            rf"\A(?:[ \t]*\n)*(?:[ \t]*@\[[^\n]*\][ \t]*\n)*(?:(?:{modifiers})[ \t]+)*(?P<kind>{kinds})[ \t]+(?P<name>{self._SOURCE_NAME})(?=$|[ \t\n:({{\[]|\.\{{)",
            re.MULTILINE,
        )

    def _top_level_declarations_after(self, lines: Sequence[str], start_index: int) -> list[str]:
        modifiers = rf"(?:(?:{'|'.join(self._MODIFIERS)})\s+)*"
        pattern = re.compile(
            rf"^[ \t]*{modifiers}(?:{'|'.join(self._DECL_KEYWORDS)})\s+(?P<name>{self._SOURCE_NAME})(?=$|\s|[:({{\[]|\.\{{)"
        )
        names: list[str] = []
        for line in lines[start_index:]:
            match = pattern.match(line)
            if match is not None:
                names.append(match.group("name"))
        return names

    def _active_namespace(self, prefix: str) -> str:
        cleaned = self._strip_lean_comments(prefix)
        stack: list[list[str]] = []
        for line in cleaned.splitlines():
            open_match = re.match(r"^namespace\s+([A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*)\s*$", line.strip())
            if open_match is not None:
                stack.append(open_match.group(1).split("."))
                continue
            if re.match(r"^end(?:\s+[A-Za-z_][A-Za-z0-9_'.]*)?\s*$", line.strip()) and stack:
                stack.pop()
        return ".".join(segment for entry in stack for segment in entry)

    def _candidate_full_name(self, namespace: str, source_name: str) -> str:
        if source_name.startswith("_root_."):
            return source_name.removeprefix("_root_.")
        return f"{namespace}.{source_name}" if namespace else source_name

    def _collect_header(self, lines: Sequence[str], start_index: int) -> tuple[int, str]:
        collected: list[str] = []
        for index in range(start_index, len(lines)):
            collected.append(lines[index].rstrip())
            if self._header_terminator_index("\n".join(collected)) is not None:
                return index, "\n".join(collected)
        return len(lines) - 1, "\n".join(collected)

    def _extract_theorem_header(self, code: str, decl_name: str) -> ServiceResult[str]:
        location = self.locate_target_declaration(code, decl_name=decl_name)
        if not location.ok or location.value is None:
            return self.runtime.foundation.fail(location.issues)
        if location.value.kind not in {"theorem", "lemma"}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "target_not_theorem_like",
                    "The marker-adjacent declaration is not theorem-like.",
                    object_ref=decl_name,
                    current=location.value.kind,
                    expected="theorem | lemma",
                )
            )
        terminator = self._header_terminator_index(location.value.header)
        if terminator is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "theorem_header_terminator_missing",
                    "The theorem header must contain ':=' or proof-introducing 'by'.",
                    object_ref=decl_name,
                )
            )
        return self.runtime.foundation.ok(location.value.header[:terminator].strip())

    def _extract_named_theorem_header(self, code: str, lean_decl_name: str) -> ServiceResult[str]:
        cleaned = self._strip_lean_comments(code)
        modifiers = rf"(?:(?:{'|'.join(self._MODIFIERS)})\s+)*"
        pattern = re.compile(
            rf"(?m)^{modifiers}(?P<kind>theorem|lemma)\s+(?P<name>{self._SOURCE_NAME})(?=$|\s|[:({{\[]|\.\{{)"
        )
        short_name = lean_decl_name.rsplit(".", 1)[-1]
        matches = [
            match
            for match in pattern.finditer(cleaned)
            if match.group("name") in {lean_decl_name, short_name, f"_root_.{lean_decl_name}"}
        ]
        if len(matches) != 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "external_theorem_declaration_ambiguous",
                    "External formal code must contain exactly one theorem matching the registered Lean declaration name.",
                    object_ref=lean_decl_name,
                    current=str(len(matches)),
                    expected="1",
                )
            )
        start_index = cleaned[: matches[0].start()].count("\n")
        _end_index, header = self._collect_header(cleaned.splitlines(), start_index)
        terminator = self._header_terminator_index(header)
        if terminator is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "theorem_header_terminator_missing",
                    "The external theorem header must contain ':=' or proof-introducing 'by'.",
                    object_ref=lean_decl_name,
                )
            )
        return self.runtime.foundation.ok(header[:terminator].strip())

    def _header_terminator_index(self, text: str) -> int | None:
        assign = text.find(":=")
        if assign >= 0:
            return assign
        by_match = re.search(r"(?<![\w'])\bby\b", text)
        return by_match.start() if by_match is not None else None

    def _sanitize_doc_text(self, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").replace("-/", "- /").strip()

    def _normalize_docstring(self, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(line.rstrip() for line in normalized.strip().splitlines())

    def _normalize_header(self, value: str) -> str:
        without_comments = self._strip_lean_comments(value)
        tokens: list[str] = []
        index = 0
        while index < len(without_comments):
            char = without_comments[index]
            if char.isspace():
                index += 1
                continue
            if char == '"':
                end = index + 1
                escaped = False
                while end < len(without_comments):
                    current = without_comments[end]
                    if current == '"' and not escaped:
                        end += 1
                        break
                    escaped = current == "\\" and not escaped
                    end += 1
                tokens.append(without_comments[index:end])
                index = end
                continue
            if self._is_identifier_start(char):
                end = index + 1
                while end < len(without_comments) and self._is_identifier_continue(without_comments[end]):
                    end += 1
                tokens.append(without_comments[index:end])
                index = end
                continue
            if char.isdigit():
                end = index + 1
                while end < len(without_comments) and without_comments[end].isdigit():
                    end += 1
                tokens.append(without_comments[index:end])
                index = end
                continue
            tokens.append(char)
            index += 1
        return " ".join(tokens)

    def _strip_lean_comments(self, value: str) -> str:
        # Preserve every offset so parser matches can safely index the original
        # source while comments are still excluded from declaration matching.
        output: list[str] = list(value)
        index = 0
        block_depth = 0
        in_string = False
        escaped = False
        while index < len(value):
            pair = value[index : index + 2]
            char = value[index]
            if in_string:
                if char == '"' and not escaped:
                    in_string = False
                escaped = char == "\\" and not escaped
                index += 1
                continue
            if block_depth:
                if pair == "/-":
                    output[index] = output[index + 1] = " "
                    block_depth += 1
                    index += 2
                    continue
                if pair == "-/":
                    output[index] = output[index + 1] = " "
                    block_depth -= 1
                    index += 2
                    continue
                if char != "\n":
                    output[index] = " "
                index += 1
                continue
            if pair == "--":
                newline = value.find("\n", index + 2)
                if newline < 0:
                    for offset in range(index, len(value)):
                        output[offset] = " "
                    break
                for offset in range(index, newline):
                    output[offset] = " "
                index = newline + 1
                continue
            if pair == "/-":
                output[index] = output[index + 1] = " "
                block_depth = 1
                index += 2
                continue
            if char == '"':
                in_string = True
                escaped = False
            index += 1
        return "".join(output)

    @staticmethod
    def _is_identifier_start(char: str) -> bool:
        return char == "_" or char.isalpha() or unicodedata.category(char).startswith("L")

    @staticmethod
    def _is_identifier_continue(char: str) -> bool:
        category = unicodedata.category(char)
        return char in {"_", "'"} or char.isalnum() or category.startswith("L") or category.startswith("M")

    @staticmethod
    def _line_number(text: str, offset: int) -> int:
        return text.count("\n", 0, offset) + 1


__all__ = [
    "AnnotationComponent",
    "ExternalDeclarationLocationView",
    "ExternalDeclarationProbeView",
    "LeanDeclarationLocationView",
    "ResolvedDependencyProjection",
    "ResolvedMathlibDependencyProjection",
    "ResolvedRepoDeclDependencyProjection",
    "TargetMarkerView",
]
