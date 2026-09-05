from pathlib import Path

from tests.unit_services_helpers import (
    initialize_native_test_repo,
    lean_check_payload,
    make_runtime,
    publish_native_provider_release,
    write_proof_formal_for_test,
    write_statement_formal_for_test,
)
from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo, _write_decl

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoCompletionMode,
    RepoPublicationState,
    RepoPublicationStatus,
)
from lean_constellation.services.decl_graph import DeclReadinessReason, DeclState
from lean_constellation.services.decl_graph.models import RepoDeclDep
from lean_constellation.services.foundation import WriteMode
from lean_constellation.services.lean_projection.lean_check import (
    LeanCheckView,
)


NODE_PATH = "Main.Topic.Core"


def _create_content_node(
    tmp_path: Path,
    *,
    completion_mode: RepoCompletionMode | None = None,
) -> None:
    initialize_native_test_repo(tmp_path)
    runtime = make_runtime()
    if completion_mode is not None:
        assert runtime.repo_workspace.metadata.update_repo_config(
            tmp_path,
            completion_mode=completion_mode,
        ).ok
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal",
        boundary="Topic boundary",
    ).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path=NODE_PATH,
        goal="Core goal",
        boundary="Core boundary",
        objective="Build the core declarations.",
        success_criteria="The core declarations are ready.",
    ).ok


def _create_round_draft(tmp_path: Path) -> str:
    runtime = make_runtime()
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path=NODE_PATH, objective="Strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Round objective.",
    )
    assert round_record.ok and round_record.value is not None
    return round_record.value.round_id


def _start_round(tmp_path: Path, round_id: str) -> None:
    started = make_runtime().decl_graph.start_round(tmp_path, node_path=NODE_PATH, round_id=round_id)
    assert started.ok


def _create_decl(
    tmp_path: Path,
    *,
    round_id: str,
    name: str,
    kind: str = "theorem",
    public: bool = False,
    target_state: DeclState = DeclState.PROVED,
) -> None:
    runtime = make_runtime()
    created = runtime.decl_graph.create_decl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        name=name,
        kind=kind,
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        public=public,
        target_state=target_state,
    )
    assert created.ok


def _prove_theorem(tmp_path: Path, *, round_id: str, name: str, deps: list[str] | None = None) -> None:
    runtime = make_runtime()
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        nl=f"{name} states True.",
        deps=[],
    ).ok
    assert write_statement_formal_for_test(runtime,
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  sorry",
        lean_check=lean_check_payload(contains_sorry=True),
        deps=[],
    ).ok
    assert runtime.decl_graph.write_proof_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        nl="The proof is by triviality.",
        deps=deps or [],
    ).ok
    assert write_proof_formal_for_test(runtime,
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  trivial",
        lean_check=lean_check_payload(),
        deps=deps or [],
    ).ok
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name=name, state=DeclState.PROVED).ok


def _declare_theorem(tmp_path: Path, *, round_id: str, name: str, deps: list[str] | None = None) -> None:
    runtime = make_runtime()
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        nl=f"{name} states True.",
        deps=deps or [],
    ).ok
    assert write_statement_formal_for_test(runtime,
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  sorry",
        lean_check=lean_check_payload(contains_sorry=True),
        deps=deps or [],
    ).ok
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name=name, state=DeclState.DECLARED).ok


def _declare_definition(tmp_path: Path, *, round_id: str, name: str) -> None:
    runtime = make_runtime()
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        nl=f"{name} is a unit-valued definition.",
    ).ok
    assert write_statement_formal_for_test(runtime,
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"def {name} : Unit := ()",
        lean_check=lean_check_payload(),
    ).ok
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name=name, state=DeclState.DECLARED).ok


def _publish_committed_heads(tmp_path: Path, names: list[str]) -> None:
    runtime = make_runtime()
    for round_record in runtime.decl_graph.list_rounds(tmp_path, node_path=NODE_PATH).value:
        if round_record.status.value in {"draft", "running"}:
            for ref in round_record.revision_refs:
                assert runtime.decl_graph.write_decl_change_summary(
                    tmp_path,
                    node_path=NODE_PATH,
                    round_id=round_record.round_id,
                    change_id=ref.change_id,
                    summary=f"Completed {ref.decl_name}.",
                ).ok
            assert runtime.decl_graph.write_round_summary(
                tmp_path,
                node_path=NODE_PATH,
                round_id=round_record.round_id,
                summary="Completed readiness fixture round.",
            ).ok
            assert runtime.decl_graph.strategy_round.record_round_execution_result(
                tmp_path,
                node_path=NODE_PATH,
                round_id=round_record.round_id,
                result_kind="blocked",
                reason="Test fixture committed revisions before round closeout.",
            ).ok
            assert runtime.decl_graph.strategy_round.persist_round_closeout(
                tmp_path,
                node_path=NODE_PATH,
                round_id=round_record.round_id,
                result_kind="blocked",
                reason="Test fixture committed revisions before round closeout.",
                acknowledged_by="test-fixture",
            ).ok
    for name in names:
        assert runtime.lean_projection.sync_decl_file_after_revision_reset(
            tmp_path, node_path=NODE_PATH, decl_name=name
        ).ok
    assert runtime.node.commit_content_contract(
        tmp_path,
        node_path=NODE_PATH,
        summary="Publish committed dependency heads for readiness.",
    ).ok


def _passed_check() -> LeanCheckView:
    return LeanCheckView.model_validate(lean_check_payload())


def test_theorem_ready_recurses_through_ready_dependencies(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="supporting_lemma")
    _create_decl(tmp_path, round_id=round_id, name="main_result", public=True)
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="supporting_lemma")
    _prove_theorem(tmp_path, round_id=round_id, name="main_result", deps=["supporting_lemma"])
    _publish_committed_heads(tmp_path, ["supporting_lemma", "main_result"])

    runtime = make_runtime()
    report = runtime.decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="main_result")

    assert report.ok and report.value is not None
    assert report.value.ready is True
    assert report.value.blocker is None


def test_batch_readiness_evaluates_shared_exact_state_once(tmp_path: Path, monkeypatch) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="shared")
    _create_decl(tmp_path, round_id=round_id, name="left", public=True)
    _create_decl(tmp_path, round_id=round_id, name="right", public=True)
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="shared")
    _prove_theorem(tmp_path, round_id=round_id, name="left", deps=["shared"])
    _prove_theorem(tmp_path, round_id=round_id, name="right", deps=["shared"])
    _publish_committed_heads(tmp_path, ["shared", "left", "right"])

    runtime = make_runtime()
    readiness = runtime.decl_graph.readiness
    original = readiness._current_decl_and_revision
    calls: dict[str, int] = {}

    def counted_current(repo_root: Path, *, node_path: str, decl_name: str):
        calls[decl_name] = calls.get(decl_name, 0) + 1
        return original(repo_root, node_path=node_path, decl_name=decl_name)

    monkeypatch.setattr(readiness, "_current_decl_and_revision", counted_current)
    batch = runtime.decl_graph.check_decl_proof_policy_batch(
        tmp_path,
        roots=[
            (NODE_PATH, "left", ProofAvailability.PROVED),
            (NODE_PATH, "right", ProofAvailability.PROVED),
        ],
    )

    assert batch.ok and batch.value is not None
    assert [report.ready for report in batch.value] == [True, True]
    assert calls == {"left": 1, "shared": 1, "right": 1}

    batch_dump = [report.model_dump(mode="json") for report in batch.value]
    separate = [
        runtime.decl_graph.check_decl_proof_policy_satisfied(
            tmp_path,
            node_path=NODE_PATH,
            decl_name=name,
            target_proof_availability=ProofAvailability.PROVED,
        )
        for name in ("left", "right")
    ]
    assert all(result.ok and result.value is not None for result in separate)
    assert batch_dump == [result.value.model_dump(mode="json") for result in separate if result.value is not None]


def test_decl_ref_policy_batch_rejects_non_proved_provider_override(
    tmp_path: Path,
) -> None:
    runtime = make_runtime()

    recursive = runtime.decl_graph.check_decl_proof_policy_batch(
        tmp_path,
        roots=[],
        provider_target_override=ProofAvailability.DECLARED,
    )
    refs = runtime.decl_graph.check_decl_refs_proof_policy_batch(
        tmp_path,
        refs=[],
        fallback_node_path=NODE_PATH,
        local_target=ProofAvailability.PROVED,
        provider_target_override=ProofAvailability.DECLARED,
    )

    for checked in (recursive, refs):
        assert not checked.ok
        assert checked.issues[0].kind == "provider_target_override_invalid"


def test_decl_ref_policy_batch_preserves_alias_cross_node_and_input_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider_root = tmp_path / "Provider"
    _create_content_node(provider_root)
    provider_round = _create_round_draft(provider_root)
    _create_decl(
        provider_root,
        round_id=provider_round,
        name="external_shared",
        public=True,
    )
    _start_round(provider_root, provider_round)
    _prove_theorem(
        provider_root,
        round_id=provider_round,
        name="external_shared",
    )
    _publish_committed_heads(provider_root, ["external_shared"])
    runtime = make_runtime()
    assert runtime.node.export.add_scope_export(
        provider_root,
        scope_path="Main.Topic",
        decl_node=NODE_PATH,
        decl_name="external_shared",
    ).ok
    assert runtime.node.contract._commit_scope_contract_after_guard(
        provider_root,
        scope_path="Main.Topic",
        summary="Publish the provider Topic boundary.",
    ).ok
    assert runtime.node.export.add_scope_export(
        provider_root,
        scope_path="Main",
        decl_node=NODE_PATH,
        decl_name="external_shared",
    ).ok
    publish_native_provider_release(
        runtime,
        provider_root,
        release_id="provider_r1",
    )
    consumer_root = tmp_path / "Consumer"
    runtime, _versions = _prepare_release_repo(consumer_root)
    relative = DeclRef(node="Main", name="PublicResult", revision=1)
    cross_node = DeclRef(
        node="Main.Foundation.Defs",
        name="Support",
        revision=1,
    )
    external = DeclRef(
        repo="Provider",
        node=NODE_PATH,
        name="external_shared",
        revision=1,
    )
    original_public_batch = (
        runtime.decl_graph.ref_compatibility.resolve_public_decl_refs_batch
    )
    public_batches: list[list[DeclRef]] = []

    def record_public_batch(*args, **kwargs):  # noqa: ANN001, ANN202
        public_batches.append(list(kwargs["refs"]))
        return original_public_batch(*args, **kwargs)

    monkeypatch.setattr(
        runtime.decl_graph.ref_compatibility,
        "resolve_public_decl_refs_batch",
        record_public_batch,
    )

    checked = runtime.decl_graph.check_decl_refs_proof_policy_batch(
        consumer_root,
        refs=[relative, external, cross_node, relative],
        fallback_node_path="Main.Results",
        local_target=ProofAvailability.PROVED,
    )

    assert checked.ok and checked.value is not None
    assert [report.node_path for report in checked.value] == [
        "Main.Results",
        NODE_PATH,
        "Main.Foundation.Defs",
        "Main.Results",
    ]
    assert [report.decl_name for report in checked.value] == [
        "PublicResult",
        "external_shared",
        "Support",
        "PublicResult",
    ]
    assert [report.ready for report in checked.value] == [True, True, True, True]
    assert public_batches == [[external]]


def test_existing_recursive_readiness_resolves_main_alias_against_fallback_node(
    tmp_path: Path,
) -> None:
    runtime, _versions = _prepare_release_repo(tmp_path)
    _write_decl(tmp_path, node_path="Main.Results", name="LocalHelper")
    contract = runtime.node.contract.get_current_contract(
        tmp_path,
        node_path="Main.Results",
    )
    assert contract.ok and contract.value is not None, contract.issues
    contract.value.contract.decl_graph_head["LocalHelper"] = 1
    node = runtime.node.node_tree.get_node(tmp_path, path="Main.Results")
    assert node.ok and node.value is not None, node.issues
    assert runtime.foundation.store.write_json_atomic(
        runtime.node.node_tree.node_store.contract_path(
            tmp_path,
            node_id=node.value.node_id,
            version=contract.value.contract.version,
        ),
        contract.value.contract,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    revision = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path="Main.Results",
        name="PublicResult",
        revision=1,
    )
    assert revision.ok and revision.value is not None, revision.issues
    revision.value.proof.deps = [
        RepoDeclDep(
            ref=DeclRef(node="Main", name="LocalHelper", revision=1),
        )
    ]
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            tmp_path,
            node_path="Main.Results",
            decl_name="PublicResult",
            revision=1,
        ),
        revision.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok

    checked = runtime.decl_graph.check_decl_proof_policy_satisfied(
        tmp_path,
        node_path="Main.Results",
        decl_name="PublicResult",
        target_proof_availability=ProofAvailability.PROVED,
    )

    assert checked.ok and checked.value is not None
    assert checked.value.ready is True


def test_decl_ref_policy_batch_uses_each_provider_policy_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider_root = tmp_path / "Provider"
    _create_content_node(
        provider_root,
        completion_mode=RepoCompletionMode.GRAPH_DECLARED,
    )
    provider_round = _create_round_draft(provider_root)
    _create_decl(
        provider_root,
        round_id=provider_round,
        name="external_shared",
        public=True,
        target_state=DeclState.DECLARED,
    )
    _start_round(provider_root, provider_round)
    _declare_theorem(
        provider_root,
        round_id=provider_round,
        name="external_shared",
    )
    _publish_committed_heads(provider_root, ["external_shared"])
    runtime = make_runtime()
    assert runtime.node.export.add_scope_export(
        provider_root,
        scope_path="Main.Topic",
        decl_node=NODE_PATH,
        decl_name="external_shared",
    ).ok
    assert runtime.node.contract._commit_scope_contract_after_guard(
        provider_root,
        scope_path="Main.Topic",
        summary="Publish the provider Topic boundary.",
    ).ok
    assert runtime.node.export.add_scope_export(
        provider_root,
        scope_path="Main",
        decl_node=NODE_PATH,
        decl_name="external_shared",
    ).ok
    publish_native_provider_release(
        runtime,
        provider_root,
        release_id="provider_r1",
    )
    consumer_root = tmp_path / "Consumer"
    consumer_root.mkdir()
    ref = DeclRef(
        repo="Provider",
        node=NODE_PATH,
        name="external_shared",
        revision=1,
    )
    original = runtime.repo_workspace.metadata.get_repo_config
    original_public_batch = (
        runtime.decl_graph.ref_compatibility.resolve_public_decl_refs_batch
    )
    provider_reads = 0
    public_batches: list[list[DeclRef]] = []

    def count_provider_config(repo_root: Path):
        nonlocal provider_reads
        if Path(repo_root).resolve() == provider_root.resolve():
            provider_reads += 1
        return original(repo_root)

    def record_public_batch(*args, **kwargs):  # noqa: ANN001, ANN202
        public_batches.append(list(kwargs["refs"]))
        return original_public_batch(*args, **kwargs)

    monkeypatch.setattr(
        runtime.repo_workspace.metadata,
        "get_repo_config",
        count_provider_config,
    )
    monkeypatch.setattr(
        runtime.decl_graph.ref_compatibility,
        "resolve_public_decl_refs_batch",
        record_public_batch,
    )

    checked = runtime.decl_graph.check_decl_refs_proof_policy_batch(
        consumer_root,
        refs=[ref, ref],
        fallback_node_path="Main.Results",
        local_target=ProofAvailability.PROVED,
    )

    assert checked.ok and checked.value is not None
    assert [report.ready for report in checked.value] == [True, True]
    assert [report.required_availability for report in checked.value] == [
        ProofAvailability.DECLARED,
        ProofAvailability.DECLARED,
    ]
    assert provider_reads == 1
    assert public_batches == [[ref, ref]]


def test_shared_external_dependency_uses_fresh_operation_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider_root = tmp_path / "Provider"
    _create_content_node(provider_root)
    provider_round = _create_round_draft(provider_root)
    _create_decl(
        provider_root,
        round_id=provider_round,
        name="external_shared",
        public=True,
    )
    _start_round(provider_root, provider_round)
    _prove_theorem(
        provider_root,
        round_id=provider_round,
        name="external_shared",
    )
    _publish_committed_heads(provider_root, ["external_shared"])
    runtime = make_runtime()
    assert runtime.node.export.add_scope_export(
        provider_root,
        scope_path="Main.Topic",
        decl_node=NODE_PATH,
        decl_name="external_shared",
    ).ok
    assert runtime.node.contract._commit_scope_contract_after_guard(
        provider_root,
        scope_path="Main.Topic",
        summary="Publish the provider Topic boundary.",
    ).ok
    assert runtime.node.export.add_scope_export(
        provider_root,
        scope_path="Main",
        decl_node=NODE_PATH,
        decl_name="external_shared",
    ).ok
    publish_native_provider_release(
        runtime,
        provider_root,
        release_id="provider_r1",
    )

    consumer_root = tmp_path / "Consumer"
    _create_content_node(consumer_root)
    consumer_round = _create_round_draft(consumer_root)
    for name in ("left", "right"):
        _create_decl(
            consumer_root,
            round_id=consumer_round,
            name=name,
            public=True,
        )
    _start_round(consumer_root, consumer_round)
    for name in ("left", "right"):
        _prove_theorem(
            consumer_root,
            round_id=consumer_round,
            name=name,
        )
    _publish_committed_heads(consumer_root, ["left", "right"])
    external_ref = DeclRef(
        repo="Provider",
        node=NODE_PATH,
        name="external_shared",
        revision=1,
    )
    for name in ("left", "right"):
        revision = runtime.decl_graph.get_decl_revision(
            consumer_root,
            node_path=NODE_PATH,
            name=name,
            revision=1,
        )
        assert revision.ok and revision.value is not None
        assert revision.value.proof is not None
        revision.value.proof.deps = [
            RepoDeclDep(
                ref=external_ref,
                reason="Both readiness roots share one external dependency.",
            )
        ]
        assert runtime.foundation.store.write_json_atomic(
            runtime.decl_graph.graph_store.revision_path(
                consumer_root,
                node_path=NODE_PATH,
                decl_name=name,
                revision=1,
            ),
            revision.value,
            mode=WriteMode.UPDATE_EXISTING,
        ).ok

    resolver = runtime.decl_graph.ref_compatibility
    original_batch = resolver.resolve_public_decl_refs_batch
    original_boundary = resolver._load_public_boundary_context
    batch_sizes: list[int] = []
    contexts: list[object] = []
    boundary_reads = 0

    def record_batch(*args, **kwargs):  # noqa: ANN001, ANN202
        batch_sizes.append(len(kwargs["refs"]))
        contexts.append(kwargs["operation_context"])
        return original_batch(*args, **kwargs)

    def count_boundary(*args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal boundary_reads
        boundary_reads += 1
        return original_boundary(*args, **kwargs)

    monkeypatch.setattr(resolver, "resolve_public_decl_refs_batch", record_batch)
    monkeypatch.setattr(resolver, "_load_public_boundary_context", count_boundary)
    monkeypatch.setattr(
        resolver,
        "resolve_public_decl_ref",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("readiness must not call the public single wrapper")
        ),
    )

    first = runtime.decl_graph.check_decl_proof_policy_batch(
        consumer_root,
        roots=[
            (NODE_PATH, "left", ProofAvailability.PROVED),
            (NODE_PATH, "right", ProofAvailability.PROVED),
        ],
    )

    assert first.ok and first.value is not None
    assert [report.ready for report in first.value] == [True, True]
    assert batch_sizes == [1, 1]
    assert contexts[0] is contexts[1]
    assert boundary_reads == 1

    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(provider_root),
        RepoPublicationState(status=RepoPublicationStatus.DEVELOPING),
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    second = runtime.decl_graph.check_decl_proof_policy_batch(
        consumer_root,
        roots=[
            (NODE_PATH, "left", ProofAvailability.PROVED),
            (NODE_PATH, "right", ProofAvailability.PROVED),
        ],
    )

    assert second.ok and second.value is not None
    assert [report.ready for report in second.value] == [False, False]
    assert batch_sizes == [1, 1, 1, 1]
    assert contexts[0] is contexts[1]
    assert contexts[2] is contexts[3]
    assert contexts[0] is not contexts[2]
    assert boundary_reads == 1


def test_definition_declared_with_statement_check_is_ready(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(
        tmp_path,
        round_id=round_id,
        name="main_def",
        kind="definition",
        public=True,
        target_state=DeclState.DECLARED,
    )
    _start_round(tmp_path, round_id)
    _declare_definition(tmp_path, round_id=round_id, name="main_def")

    report = make_runtime().decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="main_def")

    assert report.ok and report.value is not None
    assert report.value.ready is True


def test_declared_policy_accepts_declared_theorem_with_satisfied_statement_deps(tmp_path: Path) -> None:
    _create_content_node(
        tmp_path,
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
    )
    round_id = _create_round_draft(tmp_path)
    _create_decl(
        tmp_path,
        round_id=round_id,
        name="supporting_def",
        kind="definition",
        public=True,
        target_state=DeclState.DECLARED,
    )
    _create_decl(tmp_path, round_id=round_id, name="main_result", public=True, target_state=DeclState.DECLARED)
    _start_round(tmp_path, round_id)
    _declare_definition(tmp_path, round_id=round_id, name="supporting_def")
    _declare_theorem(tmp_path, round_id=round_id, name="main_result", deps=["supporting_def"])
    _publish_committed_heads(tmp_path, ["supporting_def", "main_result"])

    runtime = make_runtime()
    declared = runtime.decl_graph.check_decl_proof_policy_satisfied(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
    )
    legacy_ready = runtime.decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="main_result")

    assert declared.ok and declared.value is not None
    assert declared.value.ready is True
    assert declared.value.required_availability == ProofAvailability.DECLARED
    assert declared.value.blocker is None
    assert legacy_ready.ok and legacy_ready.value is not None
    assert legacy_ready.value.ready is False
    assert legacy_ready.value.blocker is not None
    assert legacy_ready.value.blocker.reason == DeclReadinessReason.STATE_TOO_LOW


def test_proved_policy_checks_proof_deps_but_declared_policy_ignores_them(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="supporting_lemma", target_state=DeclState.DECLARED)
    _create_decl(tmp_path, round_id=round_id, name="main_result", public=True)
    _start_round(tmp_path, round_id)
    _declare_theorem(tmp_path, round_id=round_id, name="supporting_lemma")
    _prove_theorem(tmp_path, round_id=round_id, name="main_result", deps=["supporting_lemma"])

    runtime = make_runtime()
    declared = runtime.decl_graph.check_decl_proof_policy_satisfied(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
        target_proof_availability=ProofAvailability.DECLARED,
    )
    proved = runtime.decl_graph.check_decl_proof_policy_satisfied(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
        target_proof_availability=ProofAvailability.PROVED,
    )

    assert declared.ok and declared.value is not None
    assert declared.value.ready is True
    assert declared.value.blocker is None
    assert proved.ok and proved.value is not None
    assert proved.value.ready is False
    assert proved.value.blocker is not None
    assert proved.value.blocker.reason == DeclReadinessReason.DEPENDENCY_NOT_READY
    assert proved.value.blocker.blocking_decl is not None
    assert proved.value.blocker.blocking_decl.name == "supporting_lemma"


def test_strict_proved_audit_rejects_declared_only_public_theorem(tmp_path: Path) -> None:
    _create_content_node(
        tmp_path,
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
    )
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="public_result", public=True, target_state=DeclState.DECLARED)
    _start_round(tmp_path, round_id)
    _declare_theorem(tmp_path, round_id=round_id, name="public_result")

    runtime = make_runtime()
    policy = runtime.decl_graph.check_decl_proof_policy_satisfied(tmp_path, node_path=NODE_PATH, decl_name="public_result")
    audit = runtime.decl_graph.run_strict_proved_audit(tmp_path, node_path=NODE_PATH)

    assert policy.ok and policy.value is not None
    assert policy.value.ready is True
    assert audit.ok and audit.value is not None
    assert audit.value.passed is False
    assert audit.value.audit_name == "strict_proved_audit"
    assert audit.value.findings[0].kind == "strict_proved_decl_not_satisfied"
    assert audit.value.checked_items == [f"{NODE_PATH}:public_result"]


def test_dependency_not_ready_blocks_recursive_readiness(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="supporting_lemma")
    _create_decl(tmp_path, round_id=round_id, name="main_result")
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="main_result", deps=["supporting_lemma"])

    report = make_runtime().decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="main_result")

    assert report.ok and report.value is not None
    assert report.value.ready is False
    assert report.value.blocker is not None
    assert report.value.blocker.reason == DeclReadinessReason.DEPENDENCY_NOT_READY
    assert report.value.blocker.blocking_decl is not None
    assert report.value.blocker.blocking_decl.name == "supporting_lemma"


def test_cycle_is_reported_as_not_ready(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="a")
    _create_decl(tmp_path, round_id=round_id, name="b")
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="a", deps=["b"])
    _prove_theorem(tmp_path, round_id=round_id, name="b", deps=["a"])
    _publish_committed_heads(tmp_path, ["a", "b"])

    report = make_runtime().decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="a")

    assert report.ok and report.value is not None
    assert report.value.ready is False
    assert report.value.blocker is not None
    assert report.value.blocker.reason == DeclReadinessReason.CYCLE_DETECTED
    assert report.value.blocker.blocking_decl is not None
    assert report.value.blocker.blocking_decl.name == "a"


def test_default_public_decl_provider_uses_decl_graph(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="public_result", public=True)
    _create_decl(tmp_path, round_id=round_id, name="private_result", public=False)
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="public_result")
    _prove_theorem(tmp_path, round_id=round_id, name="private_result")

    runtime = make_runtime()
    public = runtime.node.export.list_content_public_decls(tmp_path, node_path=NODE_PATH)

    assert public.ok and public.value is not None
    assert [item.ref.name for item in public.value] == ["public_result"]
    assert public.value[0].source == "decl_graph"
    assert public.value[0].ready is True


def test_default_public_decl_provider_batches_release_status_lookup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    for name, public in (
        ("first_public", True),
        ("second_public", True),
        ("private_result", False),
    ):
        _create_decl(tmp_path, round_id=round_id, name=name, public=public)
    _start_round(tmp_path, round_id)
    for name in ("first_public", "second_public", "private_result"):
        _prove_theorem(tmp_path, round_id=round_id, name=name)

    runtime = make_runtime()
    calls = 0
    original = runtime.repo_workspace.release.create_release_audit_context

    def count_audit(*args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        runtime.repo_workspace.release,
        "create_release_audit_context",
        count_audit,
    )

    public = runtime.node.export.list_content_public_decls(
        tmp_path,
        node_path=NODE_PATH,
    )

    assert public.ok and public.value is not None
    assert [item.ref.name for item in public.value] == [
        "first_public",
        "second_public",
    ]
    assert calls == 1


def test_decl_graph_is_default_lean_projection_revision_provider(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="main_result")
    _start_round(tmp_path, round_id)
    runtime = make_runtime()
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
    ).ok

    saved = runtime.lean_projection.decl_file.revision_provider.save_statement_formal_capture(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
        code="theorem main_result : True := by\n  sorry",
        check=_passed_check(),
        lean_decl_name="TestProject.main_result",
    )

    assert saved.ok and saved.value is not None
    assert saved.value.state == DeclState.PLANNED
    assert saved.value.statement.formal is not None
    assert saved.value.statement.formal.check is not None
    assert saved.value.statement.formal.check.status == "passed"


def test_default_validation_providers_delegate_to_decl_graph(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="public_result", public=True)
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="public_result")

    runtime = make_runtime()
    content_gate = runtime.validation_snapshot.readiness_gate.content_readiness_provider.check_content_node_ready(
        tmp_path,
        node_path=NODE_PATH,
    )
    formal_gate = runtime.validation_snapshot.consistency.formal_stage_provider.check_formal_stage_consistency(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="public_result",
        stage="proof",
    )
    audit = runtime.validation_snapshot.audit.decl_graph_provider.run_round_local_audit(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        stage="proof_formal",
    )

    assert content_gate.ok and content_gate.value is not None
    assert content_gate.value.passed is True
    assert formal_gate.ok and formal_gate.value is not None
    assert formal_gate.value.passed is True
    assert audit.ok and audit.value is not None
    assert audit.value.audit_name == "round_local_audit"
