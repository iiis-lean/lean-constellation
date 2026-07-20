"""Atomic composition for Decl truth mutations and managed Lean projection."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.services.decl_graph.models import DeclRevision, DeclStageMutationView
from lean_constellation.services.foundation import ServiceIssue, ServiceResult, WriteMode

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


def mutate_decl_with_projection(
    runtime: LeanRuntimeServices,
    *,
    repo_root: Path,
    node_path: str,
    decl_name: str,
    mutate: Callable[[], ServiceResult[DeclRevision]],
) -> ServiceResult[DeclStageMutationView]:
    """Persist one stage mutation and roll truth/file back if refresh fails."""

    decl = runtime.decl_graph.decl_catalog.get_decl(repo_root, node_path=node_path, name=decl_name)
    if not decl.ok or decl.value is None:
        attempted = mutate()
        if not attempted.ok:
            return runtime.foundation.fail(attempted.issues)
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_projection_transaction_precondition_failed",
                "Stage mutation unexpectedly succeeded without a readable declaration catalog entry.",
                object_ref=f"{node_path}:{decl_name}",
            )
        )
    revision_path = runtime.decl_graph.graph_store.revision_path(
        repo_root,
        node_path=node_path,
        decl_name=decl_name,
        revision=decl.value.current_revision,
    )
    before_revision = runtime.foundation.store.read_json(revision_path, DeclRevision)
    if not before_revision.ok or before_revision.value is None:
        return runtime.foundation.fail(before_revision.issues)
    path_view = runtime.lean_projection.decl_file.derive_decl_file_path(
        repo_root,
        node_path=node_path,
        decl_name=decl_name,
        kind=decl.value.kind,
    )
    if not path_view.ok or path_view.value is None:
        return runtime.foundation.fail(path_view.issues)
    projection_path = Path(path_view.value.path)
    projection_existed = projection_path.exists()
    try:
        projection_bytes = projection_path.read_bytes() if projection_existed else None
    except OSError as exc:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_projection_snapshot_failed",
                f"Failed to snapshot the Decl-owned Lean file before mutation: {exc}",
                object_ref=f"{node_path}:{decl_name}",
                details={"path": str(projection_path)},
            )
        )

    mutated = mutate()
    if not mutated.ok or mutated.value is None:
        return runtime.foundation.fail(mutated.issues)
    refreshed = runtime.lean_projection.refresh_decl_managed_projection(
        repo_root,
        node_path=node_path,
        decl_name=decl_name,
    )
    if refreshed.ok and refreshed.value is not None:
        return runtime.foundation.ok(
            DeclStageMutationView(
                revision=mutated.value,
                projection_stage=refreshed.value.effective_stage,
                managed_projection_changed=refreshed.value.changed,
                changed_files=list(refreshed.value.changed_files),
                reread_required=refreshed.value.reread_required,
                summary=refreshed.value.summary,
            ),
            warnings=[*mutated.issues, *refreshed.issues],
        )

    truth_restored = runtime.foundation.store.write_json_atomic(
        revision_path,
        before_revision.value,
        mode=WriteMode.UPDATE_EXISTING,
    )
    file_restore_issues = _restore_projection_file(
        runtime,
        path=projection_path,
        existed=projection_existed,
        contents=projection_bytes,
        object_ref=f"{node_path}:{decl_name}",
    )
    issues = list(refreshed.issues)
    if not truth_restored.ok:
        issues.extend(truth_restored.issues)
    issues.extend(file_restore_issues)
    return runtime.foundation.fail(issues)


def _restore_projection_file(
    runtime: LeanRuntimeServices,
    *,
    path: Path,
    existed: bool,
    contents: bytes | None,
    object_ref: str,
) -> list[ServiceIssue]:
    temp_path: Path | None = None
    try:
        if not existed:
            path.unlink(missing_ok=True)
            return []
        if contents is None:
            raise RuntimeError("existing projection snapshot has no contents")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.rollback-", dir=path.parent)
        temp_path = Path(raw_path)
        with os.fdopen(fd, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return []
    except Exception as exc:  # noqa: BLE001 - normalized into ServiceIssue.
        return [
            runtime.foundation.issue(
                "decl_projection_rollback_failed",
                f"Failed to restore the Decl-owned Lean file after projection failure: {exc}",
                object_ref=object_ref,
                details={"path": str(path)},
            )
        ]
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


__all__ = ["mutate_decl_with_projection"]
