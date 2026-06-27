"""System docstring and target declaration helpers for Decl-owned Lean files."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import FoundationService, GateReport, ServiceResult


class TargetMarkerView(StrictModel):
    """Location of the lean-constellation marker inside a system docstring."""

    decl_name: str
    marker_line: int
    docstring_start_line: int
    docstring_end_line: int
    docstring: str
    summary: str

    @field_validator("decl_name")
    @classmethod
    def _non_empty_decl_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("decl_name must be non-empty")
        return value.strip()


class LeanDeclarationLocationView(StrictModel):
    """Conservative text location for a named Lean declaration header."""

    decl_name: str
    kind: str
    start_line: int
    header_end_line: int
    header: str
    summary: str

    @field_validator("decl_name", "kind", "header")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("value must be non-empty")
        return value.strip()


class AnnotationComponent:
    """Render and validate controlled docstrings in Decl-owned Lean files."""

    _DOCSTRING_RE = re.compile(r"/--(?P<body>.*?)-/", re.DOTALL)
    _MARKER_RE = re.compile(r"^\s*lean-constellation\s+target:\s*(?P<decl_name>\S.*?)\s*$")
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

    def __init__(self, foundation: FoundationService | None = None) -> None:
        self.foundation = foundation or FoundationService()

    def render_statement_docstring(self, revision: Any) -> ServiceResult[str]:
        decl_name = self._decl_name(revision)
        if decl_name is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "decl_name_missing",
                    "Cannot render statement docstring without a Decl name.",
                    field="revision.name",
                )
            )
        statement = self._get_path(revision, ("statement",))
        nl_text = self._nl_text(self._get_path(statement, ("nl",)))
        origin = self._get_path(self._get_path(statement, ("nl",)), ("origin",))
        deps = self._as_list(self._get_path(statement, ("deps",)))
        return self.foundation.ok(
            self._render_docstring(
                decl_name=decl_name,
                stage="statement",
                sections=[
                    ("statement.nl", nl_text),
                    ("statement.origin", self._format_optional(origin)),
                    ("statement.deps", self._format_list(deps)),
                ],
            )
        )

    def render_proof_docstring(self, revision: Any) -> ServiceResult[str]:
        decl_name = self._decl_name(revision)
        if decl_name is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "decl_name_missing",
                    "Cannot render proof docstring without a Decl name.",
                    field="revision.name",
                )
            )
        statement = self._get_path(revision, ("statement",))
        proof = self._get_path(revision, ("proof",))
        statement_nl = self._nl_text(self._get_path(statement, ("nl",)))
        proof_nl = self._nl_text(self._get_path(proof, ("nl",)))
        proof_origin = self._get_path(self._get_path(proof, ("nl",)), ("origin",))
        return self.foundation.ok(
            self._render_docstring(
                decl_name=decl_name,
                stage="proof",
                sections=[
                    ("statement.nl", statement_nl),
                    ("statement.deps", self._format_list(self._as_list(self._get_path(statement, ("deps",))))),
                    ("proof.nl", proof_nl),
                    ("proof.origin", self._format_optional(proof_origin)),
                    ("proof.deps", self._format_list(self._as_list(self._get_path(proof, ("deps",))))),
                ],
            )
        )

    def parse_target_marker(self, file_text: str) -> ServiceResult[TargetMarkerView]:
        markers: list[TargetMarkerView] = []
        for match in self._DOCSTRING_RE.finditer(file_text):
            docstring = match.group(0)
            body = match.group("body")
            body_start_line = self._line_number(file_text, match.start("body"))
            docstring_start_line = self._line_number(file_text, match.start())
            docstring_end_line = self._line_number(file_text, match.end())
            for offset, line in enumerate(body.splitlines()):
                marker = self._MARKER_RE.match(line)
                if marker is None:
                    continue
                decl_name = marker.group("decl_name").strip()
                markers.append(
                    TargetMarkerView(
                        decl_name=decl_name,
                        marker_line=body_start_line + offset,
                        docstring_start_line=docstring_start_line,
                        docstring_end_line=docstring_end_line,
                        docstring=docstring,
                        summary=f"Found target marker for {decl_name}.",
                    )
                )
        if not markers:
            return self.foundation.fail(
                self.foundation.issue(
                    "target_marker_missing",
                    "No lean-constellation target marker was found in the Lean file text.",
                )
            )
        if len(markers) > 1:
            return self.foundation.fail(
                self.foundation.issue(
                    "target_marker_duplicate",
                    "A Decl-owned Lean file must contain exactly one target marker.",
                    current=", ".join(marker.decl_name for marker in markers),
                )
            )
        return self.foundation.ok(markers[0])

    def validate_docstring(
        self,
        file_text: str,
        *,
        decl_name: str,
        stage: Literal["statement", "proof"] | str,
        expected_docstring: str,
    ) -> ServiceResult[GateReport]:
        if stage not in {"statement", "proof"}:
            return self.foundation.fail(
                self.foundation.issue(
                    "docstring_stage_invalid",
                    "Docstring stage must be statement or proof.",
                    field="stage",
                    current=str(stage),
                    expected="statement | proof",
                )
            )
        marker = self.parse_target_marker(file_text)
        if not marker.ok or marker.value is None:
            return self.foundation.ok(
                self.foundation.gate_failed(
                    f"{stage}_docstring",
                    marker.issues,
                    summary="Target docstring marker is missing or invalid.",
                )
            )
        issues = []
        if marker.value.decl_name != decl_name:
            issues.append(
                self.foundation.issue(
                    "target_marker_decl_mismatch",
                    "The target marker points to a different declaration.",
                    field="decl_name",
                    current=marker.value.decl_name,
                    expected=decl_name,
                )
            )
        if self._normalize_docstring(marker.value.docstring) != self._normalize_docstring(expected_docstring):
            issues.append(
                self.foundation.issue(
                    "system_docstring_changed",
                    "The system docstring does not match the expected generated docstring.",
                    object_ref=decl_name,
                    field="docstring",
                )
            )
        if issues:
            return self.foundation.ok(
                self.foundation.gate_failed(
                    f"{stage}_docstring",
                    issues,
                    summary="System docstring validation failed.",
                )
            )
        return self.foundation.ok(
            self.foundation.gate_passed(
                f"{stage}_docstring",
                summary=f"System {stage} docstring is synchronized.",
            )
        )

    def locate_target_declaration(self, file_text: str, *, decl_name: str) -> ServiceResult[LeanDeclarationLocationView]:
        matches = self._find_declaration_headers(file_text, decl_name)
        if not matches:
            return self.foundation.fail(
                self.foundation.issue(
                    "target_declaration_missing",
                    "Target declaration header could not be located conservatively.",
                    object_ref=decl_name,
                )
            )
        if len(matches) > 1:
            return self.foundation.fail(
                self.foundation.issue(
                    "target_declaration_duplicate",
                    "Multiple declaration headers match the target declaration name.",
                    object_ref=decl_name,
                    current=str(len(matches)),
                )
            )
        kind, start_line, header_end_line, header = matches[0]
        return self.foundation.ok(
            LeanDeclarationLocationView(
                decl_name=decl_name,
                kind=kind,
                start_line=start_line,
                header_end_line=header_end_line,
                header=header,
                summary=f"Found {kind} {decl_name} at line {start_line}.",
            )
        )

    def compare_theorem_header(self, statement_code: str, proof_code: str, *, decl_name: str) -> ServiceResult[GateReport]:
        statement_header = self._extract_theorem_header(statement_code, decl_name)
        proof_header = self._extract_theorem_header(proof_code, decl_name)
        issues = []
        if not statement_header.ok:
            issues.extend(statement_header.issues)
        if not proof_header.ok:
            issues.extend(proof_header.issues)
        if issues:
            return self.foundation.ok(
                self.foundation.gate_failed(
                    "theorem_header",
                    issues,
                    summary="The theorem header could not be extracted reliably.",
                )
            )
        assert statement_header.value is not None
        assert proof_header.value is not None
        expected = self._normalize_header(statement_header.value)
        current = self._normalize_header(proof_header.value)
        if expected != current:
            return self.foundation.ok(
                self.foundation.gate_failed(
                    "theorem_header",
                    self.foundation.issue(
                        "theorem_header_changed",
                        "Proof formalization changed the theorem statement header.",
                        object_ref=decl_name,
                        current=current,
                        expected=expected,
                    ),
                    summary="Theorem header changed.",
                )
            )
        return self.foundation.ok(self.foundation.gate_passed("theorem_header", summary="Theorem header is unchanged."))

    def _render_docstring(
        self,
        *,
        decl_name: str,
        stage: Literal["statement", "proof"],
        sections: Sequence[tuple[str, str]],
    ) -> str:
        lines = [
            "/--",
            f"lean-constellation target: {decl_name}",
            f"stage: {stage}",
        ]
        for title, value in sections:
            lines.append("")
            lines.append(f"{title}:")
            lines.extend(self._indent_doc_text(value))
        lines.append("-/")
        return "\n".join(lines)

    def _find_declaration_headers(self, file_text: str, decl_name: str) -> list[tuple[str, int, int, str]]:
        lines = file_text.splitlines()
        results: list[tuple[str, int, int, str]] = []
        for index, line in enumerate(lines):
            match = self._declaration_line_re(decl_name).match(line)
            if match is None:
                continue
            start_line = index + 1
            kind = match.group("kind")
            header_end_index, header = self._collect_header(lines, index)
            results.append((kind, start_line, header_end_index + 1, header))
        return results

    def _declaration_line_re(self, decl_name: str) -> re.Pattern[str]:
        escaped = re.escape(decl_name)
        modifier = rf"(?:(?:{'|'.join(self._MODIFIERS)})\s+)*"
        kind = rf"(?P<kind>{'|'.join(self._DECL_KEYWORDS)})"
        name = rf"(?:{escaped}|`{escaped}`)"
        return re.compile(rf"^\s*{modifier}{kind}\s+{name}(?=$|\s|[:({{\[])")

    def _collect_header(self, lines: Sequence[str], start_index: int) -> tuple[int, str]:
        collected: list[str] = []
        for index in range(start_index, len(lines)):
            line = lines[index]
            collected.append(line.rstrip())
            if self._header_terminator_index("\n".join(collected)) is not None:
                return index, "\n".join(collected)
        return len(lines) - 1, "\n".join(collected)

    def _extract_theorem_header(self, code: str, decl_name: str) -> ServiceResult[str]:
        location = self.locate_target_declaration(code, decl_name=decl_name)
        if not location.ok or location.value is None:
            return self.foundation.fail(location.issues)
        if location.value.kind not in {"theorem", "lemma"}:
            return self.foundation.fail(
                self.foundation.issue(
                    "target_not_theorem_like",
                    "The target declaration is not theorem-like.",
                    object_ref=decl_name,
                    current=location.value.kind,
                    expected="theorem | lemma",
                )
            )
        header = location.value.header
        terminator = self._header_terminator_index(header)
        if terminator is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "theorem_header_terminator_missing",
                    "The theorem header must contain a recognizable ':=' or proof-introducing 'by'.",
                    object_ref=decl_name,
                )
            )
        return self.foundation.ok(header[:terminator].strip())

    def _header_terminator_index(self, text: str) -> int | None:
        assign = text.find(":=")
        if assign >= 0:
            return assign
        by_match = re.search(r"(?<![\w'])\bby\b", text)
        if by_match is not None:
            return by_match.start()
        return None

    def _decl_name(self, value: Any) -> str | None:
        for path in (("decl_name",), ("name",), ("decl", "name"), ("ref", "name")):
            text = self._text_or_none(self._get_path(value, path))
            if text:
                return text
        return None

    def _nl_text(self, value: Any) -> str:
        text = self._text_or_none(self._get_path(value, ("text",)))
        if text:
            return text
        text = self._text_or_none(value)
        return text or "None"

    def _get_path(self, value: Any, path: Sequence[str]) -> Any:
        current = value
        for key in path:
            if current is None:
                return None
            current = self._get_one(current, key)
        return current

    def _get_one(self, value: Any, key: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(key)
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="python")
            if isinstance(dumped, Mapping):
                return dumped.get(key)
        return getattr(value, key, None)

    def _as_list(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return [value]

    def _text_or_none(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return None

    def _format_optional(self, value: Any) -> str:
        if value is None:
            return "None"
        return self._format_value(value)

    def _format_list(self, values: Sequence[Any]) -> str:
        if not values:
            return "- None"
        return "\n".join(f"- {self._format_value(value)}" for value in values)

    def _format_value(self, value: Any) -> str:
        if value is None:
            return "None"
        if isinstance(value, str):
            return value.strip() or "None"
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="python")
        if isinstance(value, Mapping) or isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            try:
                return json.dumps(value, ensure_ascii=True, sort_keys=True)
            except TypeError:
                return str(value)
        return str(value)

    def _indent_doc_text(self, value: str) -> list[str]:
        sanitized = self._sanitize_doc_text(value)
        if not sanitized:
            return ["  None"]
        return [f"  {line}" if line else "  " for line in sanitized.splitlines()]

    def _sanitize_doc_text(self, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").replace("-/", "- /").strip()

    def _normalize_docstring(self, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip() for line in normalized.strip().splitlines()]
        return "\n".join(lines)

    def _normalize_header(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def _line_number(self, text: str, offset: int) -> int:
        return text.count("\n", 0, offset) + 1
