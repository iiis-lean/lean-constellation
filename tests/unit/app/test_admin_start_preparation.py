from __future__ import annotations

from lean_constellation.app import (
    LeanAdminApi,
    RepoRunOptions,
    StartPreparationInput,
    create_app_runtime_services,
    initialize_repo_business_truth,
)
from lean_constellation.domain.preparation import RepoPreparationInput, RepoRequirementRef, SourceCorpusMode


def test_admin_starts_native_and_adapter_preparation_flows(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    native_repo = tmp_path / "NativeRepo"
    adapter_repo = tmp_path / "AdapterRepo"
    assert initialize_repo_business_truth(runtime, native_repo).ok
    assert initialize_repo_business_truth(runtime, adapter_repo).ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        native_repo,
        input=RepoPreparationInput(
            goal="Prepare the native repository.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            interface_inputs=[],
        ),
    ).ok
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


def test_requirement_provider_initial_run_cannot_bypass_aggregated_config(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    repo_root = tmp_path / "Provider"
    assert initialize_repo_business_truth(runtime, repo_root).ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Provide a declared interface.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            requirement_refs=[RepoRequirementRef(consumer_repo="Consumer", requirement_name="sieve")],
        ),
    ).ok
    result = LeanAdminApi(runtime).start_native_preparation(
        StartPreparationInput(
            repo_root=repo_root,
            run_request=RepoRunOptions(
                completion_mode="interface_declared",
            ),
            enqueue=False,
        )
    )
    assert not result.ok
    assert result.issues[0].kind == "provider_run_config_mismatch"
