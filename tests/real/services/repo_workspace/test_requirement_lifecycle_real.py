from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.preparation import RepoDependencyRequirementStatus, SourceCorpusMode
from lean_constellation.services.repo_workspace import RepoWorkspaceService


@pytest.mark.real
def test_repo_workspace_requirement_group_lifecycle_real(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    consumer_a = workspace / "consumer_a"
    consumer_b = workspace / "consumer_b"
    workspace.mkdir()

    service = make_runtime().repo_workspace
    assert service.metadata.ensure_repo_model(consumer_a).ok
    assert service.metadata.ensure_repo_model(consumer_b).ok

    created_a = service.create_requirement_with_interfaces(
        consumer_a,
        name="need_shared_a",
        target_repo="analysis_provider",
        source_description="Source A needs the shared fixed point theorem.",
        reason="Consumer A imports the provider for its final theorem.",
        interfaces=[
            {
                "name": "shared_fixed_point",
                "kind": DeclKind.THEOREM.value,
                "summary": "Shared fixed point theorem from consumer A.",
            },
            {
                "name": "metric_support",
                "kind": DeclKind.DEFINITION.value,
                "summary": "Metric support definitions needed by consumer A.",
            },
        ],
    )
    created_b = service.create_requirement_with_interfaces(
        consumer_b,
        name="need_shared_b",
        target_repo="analysis_provider",
        source_description="Source B needs the same provider and one extra lemma.",
        reason="Consumer B needs reusable support lemmas.",
        interfaces=[
            {
                "name": "shared_fixed_point",
                "kind": DeclKind.LEMMA.value,
                "summary": "Conflicting summary from consumer B.",
            },
            {
                "name": "supporting_bound",
                "kind": DeclKind.LEMMA.value,
                "summary": "Supporting bound lemma needed by consumer B.",
            },
        ],
    )
    assert created_a.ok
    assert created_b.ok

    draft = service.preparation.build_preparation_input_from_group(
        workspace,
        target_repo="analysis_provider",
        source_corpus_mode=SourceCorpusMode.PREPARE,
    )
    assert draft.ok
    assert draft.value is not None
    assert draft.value.warnings == [
        "Interface conflict for shared_fixed_point; kept first from sorted requirement order."
    ]
    assert [ref.consumer_repo for ref in draft.value.input.requirement_refs] == ["consumer_a", "consumer_b"]
    assert [ref.requirement_name for ref in draft.value.input.requirement_refs] == [
        "need_shared_a",
        "need_shared_b",
    ]
    assert draft.value.input.source_corpus_relpath == ".lean_constellation/source"
    assert "consumer_a/need_shared_a" in (draft.value.input.source_description or "")
    assert "consumer_b/need_shared_b" in (draft.value.input.source_description or "")
    assert "supporting public definitions and lemmas" in draft.value.input.goal
    assert [interface.name for interface in draft.value.input.interface_inputs] == [
        "shared_fixed_point",
        "metric_support",
        "supporting_bound",
    ]
    assert draft.value.input.interface_inputs[0].kind == DeclKind.THEOREM

    shell = service.create_provider_repo_shell_from_group(workspace, target_repo="analysis_provider")
    assert shell.ok
    assert shell.value is not None
    provider = workspace / "analysis_provider"
    assert (provider / ".lean_constellation" / "repo.json").exists()
    assert (provider / ".lean_constellation" / "preparation_input.json").exists()

    bootstrap = service.validate_requirement_bootstrap_input(provider)
    assert bootstrap.ok
    assert bootstrap.value is not None
    assert bootstrap.value.passed is True
    assert bootstrap.value.requirement_count == 2

    groups_before_ready = service.workspace_catalog.list_open_requirement_groups(workspace)
    assert groups_before_ready.ok
    assert groups_before_ready.value is not None
    assert [group.target_repo for group in groups_before_ready.value] == ["analysis_provider"]
    assert groups_before_ready.value[0].interface_names == [
        "metric_support",
        "shared_fixed_point",
        "supporting_bound",
    ]

    ready = service.mark_provider_repo_ready(provider, summary="Provider repo completed for real lifecycle test.")
    assert ready.ok
    assert ready.value is not None
    assert ready.value.satisfied_requirement_count == 2

    requirement_a = service.requirement.get_requirement(consumer_a, name="need_shared_a")
    requirement_b = service.requirement.get_requirement(consumer_b, name="need_shared_b")
    assert requirement_a.ok and requirement_a.value is not None
    assert requirement_b.ok and requirement_b.value is not None
    assert requirement_a.value.requirement.status == RepoDependencyRequirementStatus.SATISFIED
    assert requirement_b.value.requirement.status == RepoDependencyRequirementStatus.SATISFIED
    assert requirement_a.value.requirement.provider_repo == "analysis_provider"
    assert requirement_b.value.requirement.provider_repo == "analysis_provider"

    handled = service.requirement.mark_requirement_handled(
        consumer_a,
        requirement_name="need_shared_a",
        note="Consumer A attached the provider dependency.",
    )
    obsolete = service.requirement.mark_requirement_obsolete(
        consumer_b,
        requirement_name="need_shared_b",
        note="Consumer B superseded this dependency after provider became ready.",
    )
    assert handled.ok
    assert obsolete.ok

    groups_after_close = service.workspace_catalog.list_open_requirement_groups(workspace)
    assert groups_after_close.ok
    assert groups_after_close.value == []

    ready_providers = service.workspace_catalog.list_ready_provider_repos(workspace, current_repo="consumer_a")
    assert ready_providers.ok
    assert ready_providers.value is not None
    assert [repo.repo_key for repo in ready_providers.value] == ["analysis_provider"]

    coordinator_view = service.inspect_workspace_for_coordinator(consumer_a)
    assert coordinator_view.ok
    assert coordinator_view.value is not None
    assert coordinator_view.value.current_repo_root == str(consumer_a)
    assert [repo.repo_key for repo in coordinator_view.value.ready_provider_repos] == ["analysis_provider"]
