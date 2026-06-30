from __future__ import annotations

from lean_constellation.app import LeanAdminApi, StartFlowInput, create_app_runtime_services


def test_admin_starts_arbitrary_registered_flow_and_rejects_unknown_flow(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)

    started = admin.start_arbitrary_flow(
        StartFlowInput(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={"repo_key": "Repo", "repo_root": str(tmp_path / "Repo"), "start_mode": "admin_start"},
        )
    )
    unknown = admin.start_arbitrary_flow(
        StartFlowInput(flow_type="missing_flow_type", scope_id="repo:Repo", params={})
    )

    assert started.ok and started.value is not None
    assert runtime.ark.flow_service.get_flow(started.value.flow_id).flow_type == "native_repo_coordinator"
    assert not unknown.ok
    assert unknown.issues[0].kind == "admin_start_flow_failed"
