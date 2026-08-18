import json
from pathlib import Path

from tests.unit_services_helpers import make_runtime, valid_resource_readme

from lean_constellation.services.material import (
    ResourceMaterialManifest,
    ResourceMetadata,
    ResourceMetadataInput,
    ResourceTargetView,
)


def _write_valid_draft_files(draft_root: Path, *, text: str = "alpha\nbeta theorem\n") -> None:
    (draft_root / "README.md").write_text(valid_resource_readme(), encoding="utf-8")
    (draft_root / "_work" / "original").mkdir(parents=True, exist_ok=True)
    (draft_root / "_work" / "original" / "raw.txt").write_text("raw material\n", encoding="utf-8")
    (draft_root / "article").mkdir(parents=True, exist_ok=True)
    (draft_root / "article" / "main.md").write_text(text, encoding="utf-8")


def test_resource_draft_and_finalized_resource_persist_domain_target_not_view(tmp_path: Path) -> None:
    service = make_runtime().material

    normalized = service.normalize_resource_target("https://Example.com/math/page/")
    assert normalized.ok and normalized.value is not None
    assert isinstance(normalized.value, ResourceTargetView)
    assert normalized.value.summary == "Normalized resource target as web_url."

    draft = service.allocate_resource_draft(tmp_path, target=normalized.value, title_hint="Example page")
    assert draft.ok and draft.value is not None
    draft_json_path = Path(draft.value.metadata_path)
    draft_payload = json.loads(draft_json_path.read_text(encoding="utf-8"))
    assert draft_payload["target"] == {
        "kind": "web_url",
        "target": "https://Example.com/math/page/",
        "canonical_locator": "https://example.com/math/page",
        "version": None,
    }
    assert "summary" not in draft_payload["target"]

    _write_valid_draft_files(Path(draft.value.draft_root))
    finalized = service.finalize_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id, summary="Finalized curated resource.")

    assert finalized.ok and finalized.value is not None
    resource_key = finalized.value.resource.resource_key
    resource_json_path = tmp_path / ".lean_constellation" / "resources" / "items" / resource_key / "resource.json"
    resource_payload = json.loads(resource_json_path.read_text(encoding="utf-8"))
    assert resource_payload["target"] == draft_payload["target"]
    assert "summary" not in resource_payload["target"]

    loaded = make_runtime().foundation.store.read_json(resource_json_path, ResourceMetadata)
    assert loaded.ok and loaded.value is not None
    assert loaded.value.target.canonical_locator == "https://example.com/math/page"


def test_resource_metadata_uses_manifest_sha_only(tmp_path: Path) -> None:
    service = make_runtime().material
    draft = service.allocate_resource_draft(tmp_path, target="https://example.com/manifest-sha")
    assert draft.ok and draft.value is not None
    _write_valid_draft_files(Path(draft.value.draft_root), text="canonical bytes\n")
    finalized = service.finalize_resource_draft(
        tmp_path,
        draft_id=draft.value.draft.draft_id,
        summary="Manifest owns the canonical file digest.",
    )
    assert finalized.ok and finalized.value is not None

    resource_path = Path(finalized.value.resource_root) / "resource.json"
    manifest_path = Path(finalized.value.resource_root) / "manifest.json"
    resource_payload = json.loads(resource_path.read_text(encoding="utf-8"))
    manifest = make_runtime().foundation.store.read_json(manifest_path, ResourceMaterialManifest)

    assert "content_hash" not in resource_payload
    assert manifest.ok and manifest.value is not None
    canonical = next(item for item in manifest.value.files if item.path == manifest.value.canonical_entry)
    assert len(canonical.sha256) == 64


def test_resource_metadata_rejects_removed_content_hash_through_service(tmp_path: Path) -> None:
    service = make_runtime().material
    draft = service.allocate_resource_draft(tmp_path, target="https://example.com/old-resource")
    assert draft.ok and draft.value is not None
    _write_valid_draft_files(Path(draft.value.draft_root))
    finalized = service.finalize_resource_draft(
        tmp_path,
        draft_id=draft.value.draft.draft_id,
        summary="Current resource truth.",
    )
    assert finalized.ok and finalized.value is not None
    resource_path = Path(finalized.value.resource_root) / "resource.json"
    payload = json.loads(resource_path.read_text(encoding="utf-8"))
    payload["content_hash"] = "0" * 64
    resource_path.write_text(json.dumps(payload), encoding="utf-8")

    listed = service.resource_library.list_resources(tmp_path)

    assert not listed.ok
    assert listed.issues[0].kind == "schema_validation_failed"
    assert json.loads(resource_path.read_text(encoding="utf-8")) == payload


def test_register_local_resource_accepts_view_but_persists_domain_target(tmp_path: Path) -> None:
    service = make_runtime().material
    temp = tmp_path / "resource_tmp"
    (temp / "normalized").mkdir(parents=True)
    (temp / "normalized" / "main.md").write_text("registered resource\n", encoding="utf-8")

    target = service.normalize_resource_target("https://example.com/register")
    assert target.ok and target.value is not None
    registered = service.register_local_resource(
        tmp_path,
        target=target.value,
        temp_dir=temp,
        metadata=ResourceMetadataInput(title="Registered resource", source_url="https://example.com/register"),
    )

    assert registered.ok and registered.value is not None
    resource_json_path = (
        tmp_path
        / ".lean_constellation"
        / "resources"
        / "items"
        / registered.value.resource.resource_key
        / "resource.json"
    )
    resource_payload = json.loads(resource_json_path.read_text(encoding="utf-8"))
    assert resource_payload["target"]["canonical_locator"] == "https://example.com/register"
    assert "summary" not in resource_payload["target"]
