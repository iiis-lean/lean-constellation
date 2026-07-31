"""Transactional formal-code application for controlled Decl-owned Lean files."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from collections.abc import Callable

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.decl_graph.models import DeclDependencyMutationReceipt
from lean_constellation.services.decl_graph.models import DeclState
from lean_constellation.services.foundation import FoundationContext, ServiceResult
from lean_constellation.services.foundation.module_layout import local_projection_path
from lean_constellation.services.lean_projection.module_identity import ModuleBuildView, module_artifact_relpaths

if TYPE_CHECKING:
    from lean_constellation.services.lean_projection.decl_file import DeclFileComponent, FormalCaptureView
    from lean_constellation.services.lean_projection.repair import RepairComponent
    from lean_constellation.services.runtime import LeanRuntimeServices


FormalApplyStage = Literal["statement", "proof"]


class SafeFormalApplyView(StrictModel):
    node_path: str
    decl_name: str
    revision: int
    state: DeclState
    stage: FormalApplyStage
    module: str
    lean_decl_name: str
    build: ModuleBuildView
    revision_digest: str
    file_path: str
    capture_summary: str
    projection_summary: str
    summary: str


class _TreeSnapshot:
    """Restore a small set of repo-owned trees if a compound mutation fails."""

    def __init__(self, paths: list[Path]) -> None:
        self.paths = list(dict.fromkeys(Path(path) for path in paths))
        self.temp_root = Path(tempfile.mkdtemp(prefix="lean-constellation-formal-apply-"))
        self.states: list[tuple[Path, bool, Path]] = []
        for index, path in enumerate(self.paths):
            backup = self.temp_root / str(index)
            existed = path.exists()
            if existed:
                if path.is_dir():
                    shutil.copytree(path, backup)
                else:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup)
            self.states.append((path, existed, backup))

    def restore(self) -> list[str]:
        failures: list[str] = []
        for path, existed, backup in reversed(self.states):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
                if existed:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if backup.is_dir():
                        shutil.copytree(backup, path)
                    else:
                        shutil.copy2(backup, path)
            except OSError as exc:
                failures.append(f"{path}: {exc}")
        return failures

    def close(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)


class SafeFormalApplyComponent:
    """Apply caller code only through the canonical file and configured diagnostics."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        decl_file: DeclFileComponent,
        repair: RepairComponent,
    ) -> None:
        self.runtime = runtime
        self.decl_file = decl_file
        self.repair = repair

    def current_revision_digest(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[str]:
        current = self.decl_file.revision_provider.get_current_decl_revision(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        return self.runtime.foundation.ok(self._digest(current.value.model_dump(mode="json")))

    def recapture_reviewer_dependency_mutation(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: FormalApplyStage,
        mutate: Callable[[], ServiceResult[DeclDependencyMutationReceipt]],
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        """Apply a reviewer-only dependency repair and keep formal capture atomic."""

        current = self.decl_file.revision_provider.get_current_decl_revision(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        section = (
            current.value.statement
            if stage == "statement"
            else current.value.proof
        )
        formal = section.formal if section is not None else None
        if formal is None or not (formal.code or "").strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "formal_capture_missing",
                    "Dependency mutation requires an existing formal capture.",
                    object_ref=f"{node_path}:{decl_name}",
                    field=f"{stage}_formal",
                )
            )

        before_sync = self.decl_file.check_decl_file_snapshot_sync(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            stage=stage,
        )
        if not before_sync.ok or before_sync.value is None:
            return self.runtime.foundation.fail(before_sync.issues)
        if not before_sync.value.passed:
            return self.runtime.foundation.fail(before_sync.value.issues)

        path_view = self.decl_file.derive_decl_file_path(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            kind=current.value.kind,
        )
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        before_unmanaged = self._read_unmanaged_source(Path(path_view.value.path))
        if not before_unmanaged.ok or before_unmanaged.value is None:
            return self.runtime.foundation.fail(before_unmanaged.issues)
        revision_path = self.runtime.decl_graph.graph_store.revision_path(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            revision=current.value.revision,
        )
        artifact_paths = [
            Path(repo_root) / relpath
            for relpath in module_artifact_relpaths(path_view.value.module)
        ]
        snapshot = _TreeSnapshot(
            [revision_path, Path(path_view.value.path), *artifact_paths]
        )
        try:
            warnings = [*before_sync.issues, *before_sync.value.issues]
            mutated = mutate()
            if not mutated.ok or mutated.value is None:
                return self._rollback_dependency_mutation(snapshot, mutated.issues)
            warnings.extend(mutated.issues)

            after_unmanaged = self._read_unmanaged_source(Path(path_view.value.path))
            if not after_unmanaged.ok or after_unmanaged.value is None:
                return self._rollback_dependency_mutation(
                    snapshot,
                    after_unmanaged.issues,
                )
            if after_unmanaged.value != before_unmanaged.value:
                return self._rollback_dependency_mutation(
                    snapshot,
                    [
                        self.runtime.foundation.issue(
                            "dependency_recapture_unmanaged_source_changed",
                            "Dependency mutation changed Lean source outside system-managed imports and docstring regions.",
                            object_ref=f"{node_path}:{decl_name}",
                        )
                    ],
                )

            after_sync = self.decl_file.check_decl_file_snapshot_sync(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
                stage=stage,
            )
            if not after_sync.ok or after_sync.value is None:
                return self._rollback_dependency_mutation(snapshot, after_sync.issues)
            warnings.extend(after_sync.issues)
            if after_sync.value.passed:
                warnings.extend(after_sync.value.issues)

            capture_refreshed = False
            if not after_sync.value.passed:
                blocking_issues = [
                    issue
                    for issue in after_sync.value.issues
                    if issue.severity in {"error", "blocker"}
                ]
                if not blocking_issues or any(
                    issue.kind != "decl_file_capture_stale"
                    for issue in blocking_issues
                ):
                    return self._rollback_dependency_mutation(
                        snapshot,
                        after_sync.value.issues,
                    )
                captured = self._capture(
                    repo_root,
                    node_path=node_path,
                    decl_name=decl_name,
                    stage=stage,
                )
                if not captured.ok or captured.value is None:
                    return self._rollback_dependency_mutation(
                        snapshot,
                        captured.issues,
                    )
                warnings.extend(captured.issues)
                capture_refreshed = True

            consistency = self.runtime.validation_snapshot.check_formal_stage_consistency(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
                stage=stage,
            )
            if not consistency.ok or consistency.value is None:
                return self._rollback_dependency_mutation(
                    snapshot,
                    consistency.issues,
                )
            warnings.extend(consistency.issues)
            if not consistency.value.passed:
                return self._rollback_dependency_mutation(
                    snapshot,
                    consistency.value.issues,
                )
            warnings.extend(consistency.value.issues)
            receipt = (
                mutated.value.model_copy(
                    update={"formal_capture_refreshed": True}
                )
                if capture_refreshed
                else mutated.value
            )
            return self.runtime.foundation.ok(
                receipt,
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001 - restore compound reviewer mutation.
            return self._rollback_dependency_mutation(
                snapshot,
                [
                    self.runtime.foundation.issue(
                        "dependency_recapture_failed",
                        f"Dependency recapture failed: {exc}",
                        object_ref=f"{node_path}:{decl_name}",
                    )
                ],
            )
        finally:
            snapshot.close()

    def apply(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: FormalApplyStage,
        lean_code: str,
        expected_revision: int,
        expected_state: DeclState | str,
        expected_revision_digest: str,
    ) -> ServiceResult[SafeFormalApplyView]:
        if stage not in {"statement", "proof"}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("formal_apply_stage_invalid", "Formal apply stage must be statement or proof.", field="stage")
            )
        if not lean_code.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("formal_apply_code_required", "Formal Lean code is required.", field="lean_code")
            )
        current = self.decl_file.revision_provider.get_current_decl_revision(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        revision = current.value
        actual_digest = self._digest(revision.model_dump(mode="json"))
        stale = (
            revision.revision != expected_revision
            or revision.state != DeclState(expected_state)
            or actual_digest != expected_revision_digest
        )
        if stale:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "formal_apply_revision_stale",
                    "Declaration revision changed before formal code could be applied.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=f"revision={revision.revision},state={revision.state.value},digest={actual_digest}",
                    expected=f"revision={expected_revision},state={DeclState(expected_state).value},digest={expected_revision_digest}",
                )
            )
        path_view = self.decl_file.derive_decl_file_path(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            kind=revision.kind,
        )
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        ctx = FoundationContext(repo_root=Path(repo_root))
        graph_root = self.runtime.decl_graph.graph_store.graph_root(repo_root, node_path=node_path)
        projection_root = local_projection_path(
            repo_root,
            self.runtime.foundation.layout.node_projection_dir(ctx, node_path),
        )
        artifact_paths = [Path(repo_root) / relpath for relpath in module_artifact_relpaths(path_view.value.module)]
        snapshot = _TreeSnapshot([graph_root, projection_root, *artifact_paths])
        try:
            written = self._write_candidate(Path(path_view.value.path), lean_code.rstrip() + "\n")
            if not written.ok:
                return self._rollback(snapshot, written.issues)
            capture = self._capture(repo_root, node_path=node_path, decl_name=decl_name, stage=stage)
            if not capture.ok or capture.value is None:
                return self._rollback(snapshot, capture.issues)
            # Interfaces depend on accepted/public readiness and are refreshed by
            # stage advance/round closeout. Prelude is the generated dependency
            # projection relevant while the revision is still open.
            projection = self.repair.node_projection.refresh_prelude(repo_root, node_path=node_path)
            if not projection.ok or projection.value is None:
                return self._rollback(snapshot, projection.issues)
            consistency = self.runtime.validation_snapshot.check_formal_stage_consistency(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
                stage=stage,
            )
            if not consistency.ok or consistency.value is None:
                return self._rollback(snapshot, consistency.issues)
            if not consistency.value.passed:
                return self._rollback(snapshot, consistency.value.issues)
            after = self.decl_file.revision_provider.get_current_decl_revision(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
            )
            if not after.ok or after.value is None:
                return self._rollback(snapshot, after.issues)
            after_digest = self._digest(after.value.model_dump(mode="json"))
            return self.runtime.foundation.ok(
                SafeFormalApplyView(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=after.value.revision,
                    state=after.value.state,
                    stage=stage,
                    module=capture.value.module,
                    lean_decl_name=capture.value.lean_decl_name,
                    build=capture.value.build,
                    revision_digest=after_digest,
                    file_path=path_view.value.path,
                    capture_summary=capture.value.summary,
                    projection_summary=projection.value.summary,
                    summary=f"Applied and captured {stage} formal code for {decl_name}.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - restore all compound mutation state.
            issue = self.runtime.foundation.issue(
                "formal_apply_failed",
                f"Formal apply failed: {exc}",
                object_ref=f"{node_path}:{decl_name}",
            )
            return self._rollback(snapshot, [issue])
        finally:
            snapshot.close()

    def _capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: FormalApplyStage,
    ) -> ServiceResult[FormalCaptureView]:
        if stage == "statement":
            return self.decl_file.capture_statement_formal_file(repo_root, node_path=node_path, decl_name=decl_name)
        return self.decl_file.capture_proof_formal_file(repo_root, node_path=node_path, decl_name=decl_name)

    def _rollback(self, snapshot: _TreeSnapshot, issues: list) -> ServiceResult[SafeFormalApplyView]:  # noqa: ANN001
        failures = snapshot.restore()
        if failures:
            issues = [
                *issues,
                self.runtime.foundation.issue(
                    "formal_apply_rollback_failed",
                    "Formal apply rollback did not fully restore project state.",
                    details={"failures": "; ".join(failures)},
                ),
            ]
        return self.runtime.foundation.fail(issues)

    def _rollback_dependency_mutation(
        self,
        snapshot: _TreeSnapshot,
        issues: list,
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        failures = snapshot.restore()
        if failures:
            issues = [
                *issues,
                self.runtime.foundation.issue(
                    "dependency_recapture_rollback_failed",
                    "Dependency recapture rollback did not fully restore project state.",
                    details={"failures": "; ".join(failures)},
                ),
            ]
        return self.runtime.foundation.fail(issues)

    def _read_unmanaged_source(self, path: Path) -> ServiceResult[str]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_file_read_failed",
                    f"Failed to read Decl-owned Lean file: {exc}",
                    details={"path": str(path)},
                )
            )
        return self.decl_file.managed_file.extract_unmanaged_source(text)

    def _write_candidate(self, path: Path, text: str) -> ServiceResult[None]:
        temp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, raw = tempfile.mkstemp(prefix=f".{path.name}.operator-", dir=path.parent)
            temp_path = Path(raw)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            return self.runtime.foundation.ok(None)
        except OSError as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_file_write_failed", f"Failed to write Decl-owned Lean file: {exc}", details={"path": str(path)})
            )

    @staticmethod
    def _digest(value: object) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


__all__ = ["FormalApplyStage", "SafeFormalApplyComponent", "SafeFormalApplyView"]
