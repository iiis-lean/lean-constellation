"""Explicit editable regions; no Lean declaration discovery or parsing."""
from __future__ import annotations

import json

from lean_constellation.domain.restructure import DeclRecord, file_digest

IMPORT_BEGIN = "-- LC imports begin"
IMPORT_END = "-- LC imports end"
PROOF_BEGIN = "-- LC proof begin"
PROOF_END = "-- LC proof end"


def without_imports(text: str) -> str:
    if IMPORT_BEGIN not in text and IMPORT_END not in text:
        return text
    if text.count(IMPORT_BEGIN) != 1 or text.count(IMPORT_END) != 1:
        raise ValueError("managed import markers must occur exactly once")
    before, rest = text.split(IMPORT_BEGIN)
    _, after = rest.split(IMPORT_END)
    return before + after


def contract_digest(decl: DeclRecord, text: str) -> str:
    text = without_imports(text)
    if decl.is_theorem_like:
        if text.count(PROOF_BEGIN) != 1 or text.count(PROOF_END) != 1:
            raise ValueError("theorem files require exactly one LC proof begin/end region")
        prefix, rest = text.split(PROOF_BEGIN)
        _, suffix = rest.split(PROOF_END)
        text = prefix + PROOF_BEGIN + PROOF_END + suffix
    identity = [decl.name, decl.lean_name, decl.kind.value, decl.file, text]
    return file_digest(json.dumps(identity, ensure_ascii=False).encode())


def with_imports(text: str, modules: list[str]) -> str:
    section = IMPORT_BEGIN + "\n" + "".join(f"import {m}\n" for m in sorted(set(modules))) + IMPORT_END
    if IMPORT_BEGIN in text:
        if text.count(IMPORT_BEGIN) != 1 or text.count(IMPORT_END) != 1:
            raise ValueError("invalid managed import markers")
        before, rest = text.split(IMPORT_BEGIN)
        _, after = rest.split(IMPORT_END)
        return before + section + after
    return section + "\n" + text


def metadata_review_digest(decl: DeclRecord, text: str, section: str) -> str:
    """Bind a metadata review to mathematical source, excluding managed imports."""
    mathematical_text = without_imports(text).strip()
    if section == "statement":
        return contract_digest(decl, mathematical_text)
    return file_digest(mathematical_text.encode("utf-8"))
