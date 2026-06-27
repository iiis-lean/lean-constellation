from __future__ import annotations

from typing import Any

import pytest

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.foundation import (
    FoundationContext,
    FoundationService,
    IndexBuildContext,
    IndexBundle,
    IndexMetadata,
    WriteMode,
)


class RealRepoMetadata(StrictModel):
    repo_key: str
    status: str


class FilesystemIndexBuilder:
    index_name = "filesystem_index"

    def __init__(self, service: FoundationService) -> None:
        self.service = service
        self.calls = 0

    def build(self, ctx: IndexBuildContext):
        self.calls += 1
        repo_ctx = FoundationContext(repo_root=ctx.repo_root)
        metadata_path = self.service.repo_metadata_path(repo_ctx)
        metadata_result = self.service.read_json(metadata_path, RealRepoMetadata)
        if not metadata_result.ok:
            return self.service.fail(metadata_result.issues)
        metadata = metadata_result.value
        assert metadata is not None
        return self.service.ok(
            IndexBundle[dict[str, Any]](
                metadata=IndexMetadata(
                    index_name=self.index_name,
                    rebuilt_at=utc_now_iso(),
                    builder_name=self.__class__.__name__,
                    source_truth_refs=[str(metadata_path)],
                ),
                data={"repo_key": metadata.repo_key, "status": metadata.status, "calls": self.calls},
            )
        )


@pytest.mark.real
def test_foundation_filesystem_store_index_real(tmp_path) -> None:
    service = FoundationService()
    repo_root = tmp_path / "repo"
    ctx = FoundationContext(repo_root=repo_root, caller="real-test")

    constellation_dir = service.constellation_root(ctx)
    ensured = service.store.ensure_dir(constellation_dir)
    assert ensured.ok is True
    assert constellation_dir.is_dir()

    with pytest.raises(ValueError):
        service.source_corpus_root(ctx, "../outside")

    metadata_path = service.repo_metadata_path(ctx)
    metadata = RealRepoMetadata(repo_key="demo", status="draft")
    written = service.write_json_atomic(metadata_path, metadata, mode=WriteMode.CREATE_ONLY)
    loaded = service.read_json(metadata_path, RealRepoMetadata)
    duplicate = service.write_json_atomic(metadata_path, metadata, mode=WriteMode.CREATE_ONLY)

    assert written.ok is True
    assert written.value is not None and written.value.created is True
    assert loaded.ok is True and loaded.value == metadata
    assert duplicate.ok is False
    assert duplicate.issues[0].kind == "duplicate_file"

    temp_result = service.store.create_temp_dir(service.layout.resources_root(ctx) / "tmp", "resource")
    assert temp_result.ok is True
    assert temp_result.value is not None
    temp_dir = temp_result.value
    (temp_dir / "normalized.md").write_text("# Demo\n\nReadable resource.", encoding="utf-8")
    promoted = service.store.promote_dir_atomic(temp_dir, service.layout.resource_dir(ctx, "demo_resource"))
    assert promoted.ok is True
    assert (service.layout.resource_dir(ctx, "demo_resource") / "normalized.md").read_text(encoding="utf-8").startswith("# Demo")
    assert not temp_dir.exists()

    builder = FilesystemIndexBuilder(service)
    registered = service.register_index_builder(builder)
    first = service.ensure_index(ctx, "filesystem_index")
    marked = service.mark_index_stale(ctx, "filesystem_index", "metadata changed")
    stale_cache = service.index.read_index(ctx, "filesystem_index")
    second = service.ensure_index(ctx, "filesystem_index")
    metadata_list = service.index.list_index_metadata(ctx)

    assert registered.ok is True
    assert first.ok is True and first.value is not None
    assert first.value.data == {"repo_key": "demo", "status": "draft", "calls": 1}
    assert marked.ok is True
    assert marked.value is not None and marked.value.changed_items == ["metadata changed"]
    assert stale_cache.ok is True
    assert stale_cache.value is not None and stale_cache.value.metadata.stale is True
    assert second.ok is True and second.value is not None
    assert second.value.data == {"repo_key": "demo", "status": "draft", "calls": 2}
    assert builder.calls == 2
    assert metadata_list.ok is True
    assert [item.index_name for item in metadata_list.value or []] == ["filesystem_index"]

    deleted = service.store.delete_json(metadata_path)
    missing_read = service.read_json(metadata_path, RealRepoMetadata)
    assert deleted.ok is True
    assert missing_read.ok is False
    assert missing_read.issues[0].kind == "missing_file"
