from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import lean_check_payload, make_runtime, publish_native_provider_release

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.preparation import RepoDependencyRequirementStatus, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services import LeanProviderOverrides
from lean_constellation.services.foundation import FoundationService, ServiceResult
from lean_constellation.services.node import DeclPublicView


class FakePublicDeclProvider:
    def __init__(self, foundation: FoundationService, decls: dict[tuple[str, str], list[DeclPublicView]]) -> None:
        self.foundation = foundation
        self.decls = decls

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.foundation.ok(self.decls.get((str(Path(repo_root)), node_path), []))


def _runtime_with_public_decls(decls: dict[tuple[str, str], list[DeclPublicView]]):
    base = make_runtime()
    return make_runtime(providers=LeanProviderOverrides(content_public_decl_provider=FakePublicDeclProvider(base.foundation, decls)))


@pytest.mark.real
def test_repo_workspace_requirement_group_lifecycle_real(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    consumer_a = workspace / "consumer_a"
    consumer_b = workspace / "consumer_b"
    provider = workspace / "analysis_provider"
    workspace.mkdir()

    service = _runtime_with_public_decls(
        {
            (str(provider), "Main.Core"): [
                DeclPublicView(
                    ref=DeclRef(repo=None, node="Main.Core", name="shared_fixed_point", revision=1),
                    kind=DeclKind.THEOREM.value,
                    summary="Shared fixed point theorem.",
                ),
                DeclPublicView(
                    ref=DeclRef(repo=None, node="Main.Core", name="metric_support", revision=1),
                    kind=DeclKind.DEFINITION.value,
                    summary="Metric support definitions.",
                ),
                DeclPublicView(
                    ref=DeclRef(repo=None, node="Main.Core", name="supporting_bound", revision=1),
                    kind=DeclKind.LEMMA.value,
                    summary="Supporting bound lemma.",
                ),
            ]
        }
    ).repo_workspace
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
                "kind": DeclKind.THEOREM.value,
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
    assert (provider / ".lean_constellation" / "repo.json").exists()
    assert (provider / ".lean_constellation" / "preparation_input.json").exists()
    configured = service.metadata.update_repo_config(
        provider,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    )
    assert configured.ok, configured.issues

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

    assert service.runtime.node.node_tree.ensure_root_scope_node(provider).ok
    assert service.runtime.node.create_content_node(
        provider,
        path="Main.Core",
        goal="Provider public interface goal.",
        boundary="Provider public interface boundary.",
        objective="Expose the requested requirement interfaces.",
        success_criteria="Main exports all requested interface declarations.",
    ).ok
    strategy = service.runtime.decl_graph.ensure_open_strategy(
        provider,
        node_path="Main.Core",
        objective="Provide requirement lifecycle interface declarations.",
    )
    assert strategy.ok and strategy.value is not None, strategy.issues
    round_record = service.runtime.decl_graph.create_round_draft(
        provider,
        node_path="Main.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Seed public interface declarations for provider ready validation.",
    )
    assert round_record.ok and round_record.value is not None, round_record.issues
    seeded_decls = (
        ("shared_fixed_point", DeclKind.THEOREM.value),
        ("metric_support", DeclKind.DEFINITION.value),
        ("supporting_bound", DeclKind.LEMMA.value),
    )
    for name, kind in seeded_decls:
        created = service.runtime.decl_graph.create_decl(
            provider,
            node_path="Main.Core",
            round_id=round_record.value.round_id,
            name=name,
            kind=kind,
            objective=f"Create {name}.",
            summary=f"{name} summary.",
            public=True,
            end_after_state=DeclState.DECLARED,
        )
        assert created.ok, created.issues
    started = service.runtime.decl_graph.start_round(provider, node_path="Main.Core", round_id=round_record.value.round_id)
    assert started.ok, started.issues
    for name, kind in seeded_decls:
        assert service.runtime.decl_graph.write_statement_nl(
            provider,
            node_path="Main.Core",
            round_id=round_record.value.round_id,
            decl_name=name,
            nl=f"{name} is provided by the real requirement lifecycle fixture.",
            deps=[],
        ).ok
        assert service.runtime.decl_graph.write_statement_formal(
            provider,
            node_path="Main.Core",
            round_id=round_record.value.round_id,
            decl_name=name,
            lean_code=f"theorem {name} : True := by\n  trivial",
            lean_check=lean_check_payload(),
            deps=[],
        ).ok
        if kind in {DeclKind.THEOREM.value, DeclKind.LEMMA.value}:
            assert service.runtime.decl_graph.write_proof_nl(
                provider,
                node_path="Main.Core",
                round_id=round_record.value.round_id,
                decl_name=name,
                nl="The proof is by triviality in this lifecycle fixture.",
                deps=[],
            ).ok
            assert service.runtime.decl_graph.write_proof_formal(
                provider,
                node_path="Main.Core",
                round_id=round_record.value.round_id,
                decl_name=name,
                lean_code=f"theorem {name} : True := by\n  trivial",
                lean_check=lean_check_payload(),
                deps=[],
            ).ok
            target_state = DeclState.PROVED
        else:
            target_state = DeclState.DECLARED
        committed = service.runtime.decl_graph.commit_decl_revision(
            provider,
            node_path="Main.Core",
            name=name,
            state=target_state,
        )
        assert committed.ok, committed.issues
    for name in ("shared_fixed_point", "metric_support", "supporting_bound"):
        exported = service.runtime.node.export.add_scope_export(provider, scope_path="Main", decl_node="Main.Core", decl_name=name)
        assert exported.ok, exported.issues

    release = publish_native_provider_release(
        service.runtime,
        provider,
        summary="Provider repo completed for real lifecycle test.",
        release_id="requirement_lifecycle_r1",
    )
    publication = service.metadata.get_repo_publication(provider)
    assert publication.ok and publication.value is not None
    assert publication.value.publication.latest_release_id == release.release_id
    available = service.provider_availability.check_provider_available(provider)
    assert available.ok and available.value is not None and available.value.passed
    reconciliation = service.runtime.validation_snapshot.release_finalizer.reconcile_provider_requirements(
        provider,
        release_id=release.release_id,
    )
    assert reconciliation.ok and reconciliation.value is not None, reconciliation.issues
    assert reconciliation.value.satisfied == ["consumer_a/need_shared_a", "consumer_b/need_shared_b"]
    assert reconciliation.value.pending == []
    assert reconciliation.value.conflicts == []

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
