from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from lean_constellation.domain.refs import DeclRef, NodeRef
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoFormat,
    RepoPublicationState,
    RepoPublicationStatus,
)
from lean_constellation.services.foundation import FoundationContext, WriteMode
from lean_constellation.services.node.contract_fields import NodeDep
from tests.unit.flows.decl_round._helpers import (
    NODE_PATH,
    create_round_with_decl,
    make_decl_round_runtime,
)
from tests.unit.flows.decl_round.test_decl_round_dependency_resolution import (
    _check_round_decl,
    _prepare_ready_adapter_provider,
    _write_proved_round_theorem,
)
from tests.unit.services.node.test_public_decl_access import (
    _create_consumer_tree,
    _create_provider_repo,
)
from tests.unit.services.repo_workspace.test_lake_dependency_service import FakeExternal
from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo
from tests.unit_services_helpers import make_runtime


@dataclass(frozen=True)
class ProviderCase:
    name: str
    available: bool
    decl_ref: DeclRef
    availability_issue: str | None = None
    hard_failure: bool = False


CASES = (
    ProviderCase(
        name="new_valid_native",
        available=True,
        decl_ref=DeclRef(repo="Provider", node="Main.Core", name="provider_result", revision=1),
    ),
    ProviderCase(
        name="native_stable_release_missing",
        available=False,
        decl_ref=DeclRef(repo="Provider", node="Main.Core", name="provider_result", revision=1),
        availability_issue="provider_native_stable_release_missing",
    ),
    ProviderCase(
        name="dangling_native_git_release",
        available=False,
        decl_ref=DeclRef(repo="Provider", node="Main.Core", name="provider_result", revision=1),
        availability_issue="provider_native_git_release_invalid",
        hard_failure=True,
    ),
    ProviderCase(
        name="ready_adapter_latest_none",
        available=True,
        decl_ref=DeclRef(repo="Provider", node="Main", name="main_result", revision=1),
    ),
    ProviderCase(
        name="broken_adapter_projection",
        available=False,
        decl_ref=DeclRef(repo="Provider", node="Main", name="main_result", revision=1),
        availability_issue="provider_adapter_not_ready",
    ),
)


def _issue_kinds(result) -> set[str]:
    return {issue.kind for issue in result.issues}


def _prepare_provider(runtime, provider_root: Path, case: ProviderCase) -> None:
    if "adapter" in case.name:
        _prepare_ready_adapter_provider(runtime, provider_root, bind_interface=True)
        if case.name == "broken_adapter_projection":
            path = runtime.foundation.layout.adapter_interfaces_path(
                FoundationContext(repo_root=provider_root)
            )
            path.write_text("import Mathlib\n", encoding="utf-8")
        return

    _create_provider_repo(provider_root)
    if case.name == "native_stable_release_missing":
        publication = RepoPublicationState(status=RepoPublicationStatus.STABLE)
        assert runtime.foundation.store.write_json_atomic(
            runtime.repo_workspace.metadata._repo_publication_path(provider_root),
            publication,
            mode=WriteMode.OVERWRITE,
        ).ok
    elif case.name == "dangling_native_git_release":
        publication = runtime.repo_workspace.metadata.get_repo_publication(provider_root)
        assert publication.ok and publication.value is not None
        release = runtime.repo_workspace.release.get_release(
            provider_root,
            release_id=publication.value.publication.latest_release_id,
        )
        assert release.ok and release.value is not None
        deleted = runtime.repo_workspace.git_release.delete_release_ref(
            provider_root,
            release_id=release.value.release.release_id,
        )
        assert deleted.ok and deleted.value is True


def _write_workspace_dependency(consumer_root: Path) -> None:
    (consumer_root / "lakefile.toml").write_text(
        'name = "Consumer"\n\n[[require]]\nname = "Provider"\npath = "../Provider"\n',
        encoding="utf-8",
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_provider_availability_is_shared_by_workspace_callers(tmp_path: Path, case: ProviderCase) -> None:
    workspace = tmp_path
    consumer = workspace / "Consumer"
    provider = workspace / "Provider"
    consumer.mkdir()
    provider.mkdir()
    external = FakeExternal()
    runtime = make_runtime(external_overrides={"lake": external.lake})
    _create_consumer_tree(consumer)
    (consumer / "lakefile.toml").write_text('name = "Consumer"\n', encoding="utf-8")
    _prepare_provider(runtime, provider, case)

    availability = runtime.repo_workspace.provider_availability.check_provider_available(provider)
    if case.hard_failure:
        assert not availability.ok
        assert case.availability_issue in _issue_kinds(availability)
    else:
        assert availability.ok and availability.value is not None
        assert availability.value.passed is case.available
        if case.availability_issue is not None:
            assert case.availability_issue in {issue.kind for issue in availability.value.issues}

    state = runtime.repo_workspace.metadata.get_repo_state_view(provider)
    assert state.ok and state.value is not None
    assert state.value.provider_ready is case.available
    if case.hard_failure:
        assert case.availability_issue in _issue_kinds(state)

    ready = runtime.repo_workspace.workspace_catalog.list_ready_provider_repos(
        workspace,
        current_repo="Consumer",
    )
    assert ready.ok and ready.value is not None
    assert ("Provider" in {item.repo_key for item in ready.value}) is case.available
    if case.hard_failure:
        assert case.availability_issue in _issue_kinds(ready)

    attached = runtime.repo_workspace.attach_ready_workspace_repo_dependency(
        consumer,
        provider_repo="Provider",
    )
    if case.available:
        assert attached.ok, attached.issues
    else:
        assert not attached.ok
        expected = case.availability_issue if case.hard_failure else "provider_repo_not_ready"
        assert expected in _issue_kinds(attached)

    requirement = runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="provider_requirement",
        target_repo="Provider",
        reason="Exercise provider availability parity.",
    )
    assert requirement.ok
    waiting = runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="provider_requirement",
        provider_repo="Provider",
    )
    assert waiting.ok
    satisfied = runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="provider_requirement",
        provider_repo="Provider",
    )
    assert satisfied.ok

    truth = runtime.repo_workspace.requirement.validate_requirement_provider_truth(
        consumer,
        requirement_name="provider_requirement",
        provider_repo="Provider",
        require_stable=True,
    )
    candidates = runtime.repo_workspace.list_resume_candidates_for_requirement(
        workspace,
        provider_repo="Provider",
    )
    if case.available:
        assert truth.ok, truth.issues
        assert candidates.ok and candidates.value is not None
        assert [(item.consumer_repo, item.requirement_name) for item in candidates.value] == [
            ("Consumer", "provider_requirement")
        ]
    else:
        assert not truth.ok and not candidates.ok
        expected = case.availability_issue if case.hard_failure else "provider_repo_not_ready"
        assert expected in _issue_kinds(truth)
        assert expected in _issue_kinds(candidates)

    public = runtime.node.public_decl_access.list_repo_public_decls(
        consumer,
        repo_key="Provider",
        actor_role="coordinator",
    )
    if case.available:
        assert public.ok and public.value is not None
        assert [item.ref.name for item in public.value] == [case.decl_ref.name]
    else:
        assert not public.ok
        assert "repo_public_decl_not_visible" in _issue_kinds(public)

    _write_workspace_dependency(consumer)
    boundaries = runtime.node.dependency.list_visible_node_boundaries(
        consumer,
        node_path="Main.Topic.Consumer",
    )
    if case.hard_failure:
        assert not boundaries.ok
        assert case.availability_issue in _issue_kinds(boundaries)
    else:
        assert boundaries.ok and boundaries.value is not None
        external_repos = {item.repo for item in boundaries.value.boundaries if item.repo is not None}
        assert ("Provider" in external_repos) is case.available

    resolved = runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
        consumer,
        ref=case.decl_ref,
        required_availability=ProofAvailability.DECLARED,
    )
    if case.hard_failure:
        assert not resolved.ok
        assert case.availability_issue in _issue_kinds(resolved)
    else:
        assert resolved.ok and resolved.value is not None
        assert resolved.value.compatible is case.available
        if not case.available:
            assert resolved.value.reason == "provider_not_stable"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_content_decl_round_uses_the_same_provider_availability_matrix(
    tmp_path: Path,
    case: ProviderCase,
) -> None:
    _flow_runtime, runtime, consumer = make_decl_round_runtime(tmp_path)
    provider = tmp_path / "Provider"
    _prepare_provider(runtime, provider, case)
    _strategy_id, round_id, _round_index = create_round_with_decl(
        runtime,
        consumer,
        decl_name="A",
    )
    assert runtime.decl_graph.start_round(consumer, node_path=NODE_PATH, round_id=round_id).ok
    _write_proved_round_theorem(
        runtime,
        consumer,
        round_id=round_id,
        decl_name="A",
        proof_ref=case.decl_ref,
    )

    satisfied, reason = _check_round_decl(
        runtime,
        consumer,
        round_id=round_id,
        decl_name="A",
    )

    assert satisfied is case.available
    if case.available:
        assert reason is None
    else:
        assert reason is not None


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_candidate_release_cross_repo_readiness_uses_provider_availability(
    tmp_path: Path,
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    runtime, _versions = _prepare_release_repo(consumer)
    assert runtime.repo_workspace.metadata.set_repo_format(
        consumer,
        repo_format=RepoFormat.NATIVE,
        reason="Provider availability caller parity fixture.",
    ).ok
    _prepare_provider(runtime, provider, case)
    _write_workspace_dependency(consumer)

    contract = runtime.node.contract.get_visible_contract(
        consumer,
        node_path="Main.Foundation.Defs",
    )
    assert contract.ok and contract.value is not None
    contract.value.contract.deps.append(
        NodeDep(
            dep_id="provider",
            target=NodeRef(repo="Provider", node="Main"),
            reason="Exercise cross-repo provider readiness.",
        )
    )
    contract_path = runtime.node.node_tree.node_store.contract_path(
        consumer,
        node_id=contract.value.node_id,
        version=contract.value.version,
    )
    assert runtime.foundation.store.write_json_atomic(
        contract_path,
        contract.value.contract,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    monkeypatch.setattr(
        runtime.validation_snapshot.readiness_gate,
        "check_repo_ready",
        lambda *args, **kwargs: runtime.foundation.ok(
            runtime.foundation.gate_passed("repo_ready", summary="isolated pass")
        ),
    )

    preview = runtime.validation_snapshot.preview_candidate_release(
        consumer,
        base_release_id=None,
        summary="Provider availability parity candidate.",
    )

    assert preview.ok and preview.value is not None
    kinds = set(preview.value.blocking_issue_kinds)
    if case.available:
        assert "node_dep_external_provider_unavailable" not in kinds
        assert case.availability_issue is None or case.availability_issue not in kinds
    elif case.hard_failure:
        assert case.availability_issue in kinds
    else:
        assert "node_dep_external_provider_unavailable" in kinds
