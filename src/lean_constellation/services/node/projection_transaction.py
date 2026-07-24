"""Atomic contract/projection mutation helper for Node business Services."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Literal

from lean_constellation.services.foundation import FoundationContext, ServiceResult, WriteMode
from lean_constellation.services.foundation.module_layout import local_projection_path
from lean_constellation.services.node.node_tree import NodeContract, NodeMetadata


def persist_contract_with_projection(
    runtime,
    *,
    repo_root: Path,
    node_path: str,
    candidate: NodeContract,
    projection_kind: Literal["prelude", "interfaces"],
    save: Callable[[Path, str, NodeContract], ServiceResult[object]],
    refresh: Callable[[], ServiceResult[object]],
) -> ServiceResult[object]:
    """Persist a candidate and restore truth/projection if refresh fails."""

    node = runtime.node.node_tree.node_store.resolve_active_node(repo_root, path=node_path)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    before_node = deepcopy(node.value)
    if before_node.current_contract_version is None:
        return runtime.foundation.fail(
            runtime.foundation.issue("node_contract_missing", "Node has no current contract version.", object_ref=node_path)
        )
    before_path = runtime.node.node_tree.node_store.contract_path(
        repo_root, node_id=before_node.node_id, version=before_node.current_contract_version
    )
    before_contract = runtime.foundation.store.read_json(before_path, NodeContract)
    if not before_contract.ok or before_contract.value is None:
        return runtime.foundation.fail(before_contract.issues)
    logical_projection_path = (
        runtime.foundation.prelude_path(FoundationContext(repo_root=repo_root), node_path)
        if projection_kind == "prelude"
        else runtime.foundation.interfaces_path(FoundationContext(repo_root=repo_root), node_path)
    )
    projection_path = local_projection_path(repo_root, logical_projection_path)
    projection_existed = projection_path.exists()
    try:
        projection_text = projection_path.read_text(encoding="utf-8") if projection_existed else None
    except OSError as exc:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                f"{projection_kind}_projection_read_failed",
                f"Failed to snapshot generated projection before mutation: {exc}",
                object_ref=node_path,
            )
        )

    saved = save(repo_root, node_path, candidate)
    if not saved.ok:
        return runtime.foundation.fail(saved.issues)
    refreshed = refresh()
    if refreshed.ok:
        return refreshed

    restored = _restore_truth(
        runtime,
        repo_root=repo_root,
        node_path=node_path,
        before_node=before_node,
        before_contract=before_contract.value,
    )
    projection_restored = _restore_projection(
        runtime,
        path=projection_path,
        existed=projection_existed,
        text=projection_text,
        node_path=node_path,
        projection_kind=projection_kind,
    )
    if not restored.ok or not projection_restored.ok:
        return runtime.foundation.fail([*refreshed.issues, *restored.issues, *projection_restored.issues])
    return runtime.foundation.fail(refreshed.issues)


def _restore_truth(
    runtime,
    *,
    repo_root: Path,
    node_path: str,
    before_node: NodeMetadata,
    before_contract: NodeContract,
) -> ServiceResult[object]:
    current = runtime.node.node_tree.node_store.resolve_active_node(repo_root, path=node_path)
    if not current.ok or current.value is None:
        return runtime.foundation.fail(current.issues)
    current_version = current.value.current_contract_version
    with runtime.foundation.store.mutation("rollback_node_projection_mutation") as mutation:
        mutation.stage_json(
            runtime.node.node_tree.node_store.node_file(repo_root, node_id=before_node.node_id),
            before_node,
            mode=WriteMode.UPDATE_EXISTING,
        )
        mutation.stage_json(
            runtime.node.node_tree.node_store.contract_path(
                repo_root, node_id=before_node.node_id, version=before_contract.version
            ),
            before_contract,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if current_version is not None and current_version != before_contract.version:
            mutation.stage_delete(
                runtime.node.node_tree.node_store.contract_path(
                    repo_root, node_id=before_node.node_id, version=current_version
                ),
                missing_ok=True,
            )
        committed = mutation.commit()
    if not committed.ok:
        return runtime.foundation.fail(committed.issues)
    return runtime.foundation.ok(committed.value)


def _restore_projection(
    runtime,
    *,
    path: Path,
    existed: bool,
    text: str | None,
    node_path: str,
    projection_kind: str,
) -> ServiceResult[object]:
    try:
        if existed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text or "", encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError as exc:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "node_projection_rollback_failed",
                f"Failed to restore {projection_kind} projection: {exc}",
                object_ref=node_path,
            )
        )
    return runtime.foundation.ok(None)


__all__ = [
    "persist_contract_with_projection",
]
