from __future__ import annotations

from starlette.testclient import TestClient

from lean_constellation.app import LeanAppConfig, create_production_app_server
from lean_constellation.app.operator_data.repo_material import SourceIndexOpenInput
from lean_constellation.app.operator_data.release import CheckpointIdInput
from lean_constellation.domain.repo_run import SourceScope

from tests.unit.app.operator_data._helpers import make_repo


def _server(tmp_path, *, enabled: bool):  # noqa: ANN001, ANN202
    workspace = tmp_path / "workspace"
    make_repo(workspace, "RepoA")
    make_repo(workspace, "RepoB")
    result = create_production_app_server(
        LeanAppConfig(
            workspace_root=workspace,
            materialize_agent_homes=False,
            scheduler_enabled=False,
            operator_data_api_enabled=enabled,
        )
    )
    assert result.ok and result.value is not None
    return result.value, workspace


def test_operator_routes_are_default_off(tmp_path) -> None:
    app, _ = _server(tmp_path, enabled=False)

    with TestClient(app) as client:
        response = client.get("/admin/operator/repos/RepoA/nodes")

    assert response.status_code == 404
    assert app.state.lean_constellation_operator_data_api is None


def test_operator_http_direct_parity_identity_isolation_and_no_runtime_creation(tmp_path) -> None:
    app, workspace = _server(tmp_path, enabled=True)
    request = {
        "path": "Main",
        "goal": "Root goal.",
        "boundary": "Root boundary.",
        "objective": "Organize the repository.",
        "success_criteria": "The public boundary is ready.",
    }

    with TestClient(app) as client:
        created = client.post("/admin/operator/repos/RepoA/nodes/scopes", json=request)
        listed = client.get("/admin/operator/repos/RepoA/nodes")
        other = client.post(
            "/admin/operator/repos/RepoB/nodes/get",
            json={"node_path": "Main"},
        )
        injected = client.post(
            "/admin/operator/repos/RepoA/nodes/get",
            json={"node_path": "Main", "repo_key": "RepoB"},
        )
        unknown = client.post(
            "/admin/operator/repos/RepoA/nodes/get",
            json={"node_path": "Main", "unknown": True},
        )
        query = client.post(
            "/admin/operator/repos/RepoA/nodes/get?repo_key=RepoB",
            json={"node_path": "Main"},
        )

    assert created.status_code == 200
    assert listed.status_code == 200
    direct = app.state.lean_constellation_operator_data_api.node.list_nodes("RepoA")
    assert direct.ok and direct.value is not None
    assert direct.model_dump(mode="json") == listed.json()
    assert other.status_code == 400
    assert injected.status_code == 422
    assert unknown.status_code == 422
    assert query.status_code == 422
    assert not (workspace / "RepoA" / ".agent_runtime").exists()
    assert not (workspace / "RepoB" / ".agent_runtime").exists()


def test_operator_http_rejects_malformed_non_object_and_unsafe_repo_keys(tmp_path) -> None:
    app, _ = _server(tmp_path, enabled=True)

    with TestClient(app) as client:
        malformed = client.post(
            "/admin/operator/repos/RepoA/nodes/get",
            content=b"{",
            headers={"content-type": "application/json"},
        )
        non_object = client.post(
            "/admin/operator/repos/RepoA/nodes/get",
            json=["Main"],
        )
        unsafe = client.get("/admin/operator/repos/bad%5Crepo/nodes")

    assert malformed.status_code == 422
    assert non_object.status_code == 422
    assert unsafe.status_code == 400
    assert unsafe.json()["issues"][0]["kind"] == "operator_repo_key_invalid"


def test_operator_missing_repo_issue_uses_safe_fixed_message_and_matches_http(tmp_path) -> None:
    app, workspace = _server(tmp_path, enabled=True)

    direct = app.state.lean_constellation_operator_data_api.node.list_nodes("Missing")
    with TestClient(app) as client:
        response = client.get("/admin/operator/repos/Missing/nodes")

    assert not direct.ok
    assert direct.issues[0].kind == "repo_not_found"
    assert direct.issues[0].message == "The requested repository does not exist."
    assert not hasattr(direct.issues[0], "object_ref")
    assert not hasattr(direct.issues[0], "details")
    assert response.status_code == 400
    assert response.json() == direct.model_dump(mode="json")
    assert str(workspace) not in response.text


def test_operator_store_failure_never_exposes_raw_path_message(
    tmp_path,
    monkeypatch,
) -> None:
    app, workspace = _server(tmp_path, enabled=True)
    api = app.state.lean_constellation_operator_data_api
    runtime = api._execution.registry.workspace_runtime()
    repo_root = workspace / "RepoA"
    config_path = runtime.repo_workspace.metadata._repo_config_path(repo_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{}\n", encoding="utf-8")
    raw_message = f"Failed to read repository data from {config_path}."

    def fail_read(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "read_failed",
                raw_message,
                object_ref=str(config_path),
                details={"path": str(config_path)},
            )
        )

    monkeypatch.setattr(runtime.foundation.store, "read_json", fail_read)
    direct = api.repo_material.get_repo_config("RepoA")
    with TestClient(app) as client:
        response = client.get("/admin/operator/repos/RepoA/config")

    assert not direct.ok
    assert direct.issues[0].message == "Stored repository data could not be read."
    assert raw_message not in direct.model_dump_json()
    assert str(workspace) not in direct.model_dump_json()
    assert response.status_code == 400
    assert response.json() == direct.model_dump(mode="json")
    assert raw_message not in response.text
    assert str(workspace) not in response.text


def test_source_index_open_hides_internal_baseline_locator_in_direct_and_http_output(
    tmp_path,
) -> None:
    app, workspace = _server(tmp_path, enabled=True)
    api = app.state.lean_constellation_operator_data_api
    runtime = api._execution.registry.workspace_runtime()
    for repo_key in ("RepoA", "RepoB"):
        source = workspace / repo_key / ".lean_constellation/source"
        source.mkdir(parents=True)
        (source / "README.md").write_text(
            "# Corpus\n\n"
            "Source provenance: local fixture.\n"
            "Reading order: read chapter.md.\n"
            "Main material: chapter.md.\n"
            "Known gaps and extraction limits: none.\n",
            encoding="utf-8",
        )
        (source / "chapter.md").write_text("Theorem B.\n", encoding="utf-8")
        prepared = runtime.material.submit_source_corpus_prepared(
            workspace / repo_key,
            entry_path="README.md",
            overview="Operator fixture.",
            preparation_summary="Prepared fixture.",
        )
        assert prepared.ok, prepared.issues

    missing = runtime.material.source_index.missing_source_index_digest()
    request = SourceIndexOpenInput(
        source_scope=SourceScope(mode="selected", selectors=["chapter.md"]),
        expected_baseline_digest=missing,
    )
    direct = api.repo_material.open_source_index_update("RepoA", request)
    with TestClient(app) as client:
        response = client.post(
            "/admin/operator/repos/RepoB/materials/source-index/update",
            json=request.model_dump(mode="json"),
        )

    assert direct.ok and direct.value is not None, direct.issues
    assert response.status_code == 200
    direct_json = direct.model_dump(mode="json")
    http_json = response.json()
    internal_locator = ".lean_constellation/source_index/operator_baseline.json"
    assert "baseline_locator" not in direct_json["value"]
    assert "baseline_locator" not in http_json["value"]
    assert internal_locator not in str(direct_json)
    assert internal_locator not in response.text


def test_operator_route_names_are_fixed_unique_and_separate_from_admin_mcp(tmp_path) -> None:
    app, _ = _server(tmp_path, enabled=True)
    operator_routes = [
        route for route in app.routes if getattr(route, "path", "").startswith("/admin/operator/")
    ]
    names = [route.name for route in operator_routes]

    assert len(operator_routes) == 107
    assert len(names) == len(set(names))
    assert all(name.startswith("operator_") for name in names)


def test_release_checkpoint_http_is_strict_lc_only_and_matches_direct_view(tmp_path) -> None:
    app, workspace = _server(tmp_path, enabled=True)
    (workspace / "RepoA" / "Main.lean").write_text("def fixture : Nat := 1\n", encoding="utf-8")

    with TestClient(app) as client:
        created = client.post(
            "/admin/operator/repos/RepoA/checkpoints",
            json={"checkpoint_kind": "manual_test_stable_point", "label": "HTTP fixture"},
        )
        forged_ark = client.post(
            "/admin/operator/repos/RepoA/checkpoints",
            json={
                "checkpoint_kind": "manual_test_stable_point",
                "ark_runtime_snapshot_id": "forged",
            },
        )
        forged_prepared = client.post(
            "/admin/operator/repos/RepoA/releases/publish",
            json={"summary": "forged", "candidate_digest": "forged"},
        )
        release_owned = client.post(
            "/admin/operator/repos/RepoA/checkpoints",
            json={"checkpoint_kind": "repo_release"},
        )

    assert created.status_code == 200
    assert forged_ark.status_code == 422
    assert forged_prepared.status_code == 422
    assert release_owned.status_code == 422
    value = created.json()["value"]
    assert "ark_runtime_snapshot_id" not in value
    assert "root" not in value
    direct = app.state.lean_constellation_operator_data_api.release_checkpoint.validate_checkpoint(
        "RepoA",
        CheckpointIdInput(snapshot_id=value["snapshot_id"]),
    )
    assert direct.ok and direct.value is not None
    assert direct.value.snapshot_id == value["snapshot_id"]
