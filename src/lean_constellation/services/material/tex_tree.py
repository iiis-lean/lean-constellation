"""Narrow integrity checks for literal local TeX include trees."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


_LITERAL_INCLUDE = re.compile(r"\\(?:input|include)\s*\{([^{}]+)\}")


@dataclass(frozen=True)
class TexIncludeProblem:
    source_path: str
    line_number: int
    target: str
    reason: Literal["missing", "path_escape"]


def find_literal_tex_include_problems(root: Path, relative_paths: list[str]) -> list[TexIncludeProblem]:
    """Validate statically named local ``\\input`` and ``\\include`` targets.

    Macro-computed targets are intentionally left to the material reviewer or a
    real TeX build.  This helper only rejects deterministic missing or escaping
    paths without imposing a particular source-tree layout.
    """

    resolved_root = Path(root).resolve(strict=False)
    problems: list[TexIncludeProblem] = []
    for relative in relative_paths:
        if Path(relative).suffix.lower() != ".tex":
            continue
        source = resolved_root / relative
        if not source.is_file():
            continue
        for line_number, raw_line in enumerate(
            source.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = _strip_tex_comment(raw_line)
            for match in _LITERAL_INCLUDE.finditer(line):
                target = match.group(1).strip()
                if not target or any(marker in target for marker in ("\\", "#")):
                    continue
                candidate = source.parent / target
                if candidate.suffix == "":
                    candidate = candidate.with_suffix(".tex")
                resolved_candidate = candidate.resolve(strict=False)
                try:
                    resolved_candidate.relative_to(resolved_root)
                except ValueError:
                    problems.append(
                        TexIncludeProblem(
                            source_path=relative,
                            line_number=line_number,
                            target=target,
                            reason="path_escape",
                        )
                    )
                    continue
                if not resolved_candidate.is_file():
                    problems.append(
                        TexIncludeProblem(
                            source_path=relative,
                            line_number=line_number,
                            target=target,
                            reason="missing",
                        )
                    )
    return problems


def _strip_tex_comment(line: str) -> str:
    for index, char in enumerate(line):
        if char != "%":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and line[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return line[:index]
    return line
