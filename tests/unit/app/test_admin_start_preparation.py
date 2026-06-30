from __future__ import annotations

from lean_constellation.app import LeanAdminApi, StartPreparationInput, create_app_runtime_services, initialize_repo_runtime


def test_admin_starts_native_and_adapter_preparation_flows(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    native_repo = tmp_path / "NativeRepo"
    adapter_repo = tmp_path / "AdapterRepo"
    assert initialize_repo_runtime(runtime, native_repo).ok
    assert initialize_repo_runtime(runtime, adapter_repo).ok
    admin = LeanAdminApi(runtime)

    native = admin.start_native_preparation(StartPreparationInput(repo_root=native_repo, admin_notes="native start"))
    adapter = admin.start_adapter_preparation(StartPreparationInput(repo_root=adapter_repo, admin_notes="adapter start"))

    assert native.ok and native.value is not None
    assert adapter.ok and adapter.value is not None
    native_flow = runtime.ark.flow_service.get_flow(native.value.flow_id)
    adapter_flow = runtime.ark.flow_service.get_flow(adapter.value.flow_id)
    assert native_flow.flow_type == "native_repo_preparation"
    assert native_flow.input.repo_key == "NativeRepo"
    assert adapter_flow.flow_type == "adapter_repo_preparation"
    assert adapter_flow.input.repo_key == "AdapterRepo"
