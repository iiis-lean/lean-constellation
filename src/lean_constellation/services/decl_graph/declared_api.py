"""Declared API canonical payloads and fingerprints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.decl_graph.models import DeclRevisionStatus
from lean_constellation.services.foundation import ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class DeclaredApiFingerprintView(StrictModel):
    node_id: str
    node_path: str
    decl_name: str
    decl_kind: str
    module: str
    statement_formal_code: str
    sha256: str


class DeclaredApiFingerprintComponent:
    """Fingerprint the complete committed statement formal source."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def fingerprint(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        revision: int,
    ) -> ServiceResult[DeclaredApiFingerprintView]:
        node = self.runtime.node.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        decl = self.runtime.decl_graph.decl_catalog.get_decl(repo_root, node_path=node_path, name=decl_name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        record = self.runtime.decl_graph.decl_catalog.get_decl_revision(
            repo_root, node_path=node_path, name=decl_name, revision=revision
        )
        if not record.ok or record.value is None:
            return self.runtime.foundation.fail(record.issues)
        if record.value.status != DeclRevisionStatus.COMMITTED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "declared_api_revision_not_committed",
                    "Declared API fingerprint requires a committed revision.",
                    object_ref=f"{node_path}:{decl_name}@{revision}",
                )
            )
        code = record.value.statement.formal.code if record.value.statement.formal is not None else None
        if code is None or not code.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "declared_api_statement_missing",
                    "Committed declaration has no stored statement formal code.",
                    object_ref=f"{node_path}:{decl_name}@{revision}",
                )
            )
        canonical = canonicalize_statement_formal_code(code)
        module = record.value.module or decl.value.module or f"{node_path}.{decl.value.kind}.{decl_name}"
        payload = {
            "decl_kind": decl.value.kind,
            "decl_name": decl.value.name,
            "module": module,
            "node_id": node.value.node_id,
            "node_path": node.value.path,
            "statement_formal_code": canonical,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self.runtime.foundation.ok(
            DeclaredApiFingerprintView(
                node_id=node.value.node_id,
                node_path=node.value.path,
                decl_name=decl.value.name,
                decl_kind=decl.value.kind,
                module=module,
                statement_formal_code=canonical,
                sha256=hashlib.sha256(encoded).hexdigest(),
            )
        )


def canonicalize_statement_formal_code(code: str) -> str:
    normalized = code.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


__all__ = ["DeclaredApiFingerprintComponent", "DeclaredApiFingerprintView", "canonicalize_statement_formal_code"]
