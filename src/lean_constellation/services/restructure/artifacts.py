"""Immutable accepted artifacts for Content and repository boundaries."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from lean_constellation.domain.restructure import ArtifactManifest, ContentWork, RestructureStage, file_digest, utc_now_iso
from lean_constellation.services.restructure.store import RestructureStore


class RestructureArtifactService:
    def __init__(self, store: RestructureStore) -> None:
        self.store = store

    def seal_content(
        self,
        directory: str,
        work: ContentWork,
        *,
        stage: RestructureStage | None = None,
        provider_refs: dict[str, str] | None = None,
    ) -> ArtifactManifest:
        root = self.store.repo_root(directory)
        files: dict[str, str] = {}
        artifact_id = f"artifact_{uuid.uuid4().hex}"
        snapshot = self.store.repo_metadata_root(directory) / "artifact_files" / artifact_id
        for relative in [*(decl.file for decl in work.decls.values()), *work.support_files]:
            path = root / relative
            if path.is_file():
                data = path.read_bytes()
                destination = snapshot / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                files[relative] = file_digest(data)
        metadata = json.dumps(work.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()
        artifact = ArtifactManifest(
            artifact_id=artifact_id,
            content=work.model_dump(mode="json"),
            stage=stage or work.stage,
            repo_key=work.repo_key,
            node_path=work.node_path,
            files=files,
            metadata_digest=file_digest(metadata),
            provider_refs=dict(provider_refs or {}),
            source_epoch=work.attempt_epoch,
            created_at=utc_now_iso(),
        )
        self.store.save_artifact(directory, artifact)
        return artifact

    def list_repo_artifacts(self, directory: str) -> list[ArtifactManifest]:
        root = self.store.repo_metadata_root(directory) / "artifacts"
        if not root.is_dir():
            return []
        result: list[ArtifactManifest] = []
        for path in sorted(root.glob("*.json")):
            artifact, _ = self.store.load_model(path, ArtifactManifest)
            if artifact is not None:
                result.append(artifact)
        return result

    def verify(self, directory: str, artifact: ArtifactManifest) -> list[str]:
        root = self.store.repo_root(directory)
        issues: list[str] = []
        for relative, expected in artifact.files.items():
            path = root / relative
            if not path.is_file():
                issues.append(f"missing artifact file: {relative}")
            elif file_digest(path.read_bytes()) != expected:
                issues.append(f"artifact file changed: {relative}")
        return issues


__all__ = ["RestructureArtifactService"]
