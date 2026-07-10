from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.services.foundation import (
    FoundationContext,
    FoundationService,
    IndexBuildContext,
    IndexBundle,
    IndexMetadata,
)
from lean_constellation.services.validation_snapshot import RepoCheckpointKind, ValidationSnapshotService


class RealSnapshotRuntimeStabilityProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str] | None = None,
    ):
        del repo_root, node_paths
        return self.foundation.ok(
            self.foundation.gate_passed(
                "runtime_stability",
                summary=f"Real-test runtime is stable for {checkpoint_kind.value}.",
            )
        )


class RealSnapshotArkProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.created: list[tuple[list[str], str | None]] = []
        self.restored: list[tuple[str, bool]] = []

    def create_runtime_snapshot(self, repo_root: Path, *, scope_ids: list[str], label: str | None = None):
        del repo_root
        self.created.append((scope_ids, label))
        return self.foundation.ok(f"real_ark_{len(self.created)}")

    def restore_runtime_snapshot(self, repo_root: Path, *, snapshot_id: str, leave_runtime_paused: bool = True):
        del repo_root
        self.restored.append((snapshot_id, leave_runtime_paused))
        return self.foundation.ok(
            self.foundation.mutation_view(
                object_ref=f"ark:{snapshot_id}",
                changed=True,
                summary="Restored runtime snapshot through a snapshot provider test double.",
            )
        )


class RealSnapshotIndexBuilder:
    index_name = "real_snapshot_index"

    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.calls = 0

    def build(self, ctx: IndexBuildContext):
        self.calls += 1
        main_file = Path(ctx.repo_root) / "Main.lean"
        try:
            text = main_file.read_text(encoding="utf-8")
        except OSError as exc:
            return self.foundation.fail(
                self.foundation.issue("real_snapshot_index_source_missing", f"Cannot read Main.lean: {exc}")
            )
        return self.foundation.ok(
            IndexBundle[dict[str, Any]](
                metadata=IndexMetadata(
                    index_name=self.index_name,
                    rebuilt_at=utc_now_iso(),
                    builder_name=self.__class__.__name__,
                    source_truth_refs=[str(main_file)],
                ),
                data={"main_text": text, "calls": self.calls},
            )
        )


def _write_real_preparation_input(foundation: FoundationService, repo_root: Path) -> None:
    prep = RepoPreparationInput(
        goal="Formalize the real snapshot test source.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        source_description="Real snapshot restore test corpus.",
        interface_inputs=[
            DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose the main result."),
        ],
    )
    path = foundation.layout.preparation_input_path(FoundationContext(repo_root=repo_root))
    written = foundation.store.write_json_atomic(path, prep)
    assert written.ok


@pytest.mark.real
def test_snapshot_restore_real_filesystem_checkpoint_roundtrip(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    foundation = make_runtime().foundation
    _write_real_preparation_input(foundation, repo_root)
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Source\n\n"
        "Source provenance: local snapshot restore fixture.\n"
        "Reading order: read this README.md entry as the main material.\n"
        "Main material: readable corpus entry.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    (repo_root / "Main.lean").write_text("import Std\n", encoding="utf-8")
    (repo_root / "lakefile.lean").write_text("import Lake\nopen Lake DSL\n", encoding="utf-8")
    (repo_root / ".lake").mkdir()
    (repo_root / ".lake" / "cache.txt").write_text("cache before snapshot\n", encoding="utf-8")

    builder = RealSnapshotIndexBuilder(foundation)
    assert foundation.register_index_builder(builder).ok
    ctx = FoundationContext(repo_root=repo_root, caller="real-snapshot-test")
    first_index = foundation.ensure_index(ctx, builder.index_name)
    assert first_index.ok and first_index.value is not None

    ark = RealSnapshotArkProvider(foundation)
    service = ValidationSnapshotService(
        foundation.runtime,
        runtime_stability_provider=RealSnapshotRuntimeStabilityProvider(foundation),
        ark_snapshot_provider=ark,
    )
    created = service.create_repo_stable_point_snapshot(
        repo_root,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
        label="real filesystem checkpoint",
    )
    assert created.ok
    assert created.value is not None
    assert ark.created == [(["repo:repo"], "real filesystem checkpoint")]

    (repo_root / "Main.lean").write_text("-- modified after checkpoint\n", encoding="utf-8")
    (repo_root / "Extra.lean").write_text("-- extra file should survive restore\n", encoding="utf-8")
    (repo_root / ".lake" / "cache.txt").write_text("cache after checkpoint\n", encoding="utf-8")

    restored = service.restore_repo_checkpoint_snapshot(repo_root, snapshot_id=created.value.snapshot_id)

    assert restored.ok
    assert restored.value is not None
    assert restored.value.restored_files
    assert (repo_root / "Main.lean").read_text(encoding="utf-8") == "import Std\n"
    assert (repo_root / "Extra.lean").read_text(encoding="utf-8") == "-- extra file should survive restore\n"
    assert (repo_root / ".lake" / "cache.txt").read_text(encoding="utf-8") == "cache after checkpoint\n"
    assert ark.restored == [("real_ark_1", True)]
    assert builder.calls == 2
    rebuilt = foundation.index.read_index(ctx, builder.index_name)
    assert rebuilt.ok
    assert rebuilt.value is not None
    assert rebuilt.value.data == {"main_text": "import Std\n", "calls": 2}
