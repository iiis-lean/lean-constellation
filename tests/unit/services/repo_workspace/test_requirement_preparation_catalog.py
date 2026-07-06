from tests.unit_services_helpers import make_runtime

from pathlib import Path

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import (
    RepoDependencyRequirementStatus,
    RepoPreparationInput,
    RepoRequirementRef,
    SourceCorpusMode,
)
from lean_constellation.domain.repo import ProofAvailability, RepoFormat, RepoWorkMode
from lean_constellation.services.foundation import FoundationService
from lean_constellation.services.repo_workspace import (
    LakeDependencyComponent,
    RepoMetadataComponent,
    RepoPreparationComponent,
    RepoRequirementComponent,
    WorkspaceCatalogComponent,
)


class _FakeExternal:
    lake = None


def _components() -> tuple[
    FoundationService,
    RepoMetadataComponent,
    RepoRequirementComponent,
    RepoPreparationComponent,
]:
    runtime = make_runtime()
    return (
        runtime.foundation,
        runtime.repo_workspace.metadata,
        runtime.repo_workspace.requirement,
        runtime.repo_workspace.preparation,
    )


def _catalog_components() -> tuple[
    FoundationService,
    RepoMetadataComponent,
    RepoRequirementComponent,
    RepoPreparationComponent,
    WorkspaceCatalogComponent,
]:
    foundation, metadata, requirement, preparation = _components()
    catalog = metadata.runtime.repo_workspace.workspace_catalog
    return foundation, metadata, requirement, preparation, catalog


def test_requirement_lifecycle_and_interface_rules(tmp_path: Path) -> None:
    _, _, requirement, _ = _components()

    created = requirement.create_requirement(
        tmp_path,
        name="need_fixed_point",
        target_repo="fixed_point_provider",
        source_description="Banach fixed point theorem",
        reason=None,
    )
    assert created.ok

    added = requirement.add_requirement_interface(
        tmp_path,
        requirement_name="need_fixed_point",
        interface_name="banach_fixed_point",
        kind=DeclKind.THEOREM,
        summary="Banach fixed point theorem.",
    )
    assert added.ok
    assert added.value is not None
    assert len(added.value.requirement.interfaces) == 1

    duplicate_conflict = requirement.add_requirement_interface(
        tmp_path,
        requirement_name="need_fixed_point",
        interface_name="banach_fixed_point",
        kind=DeclKind.LEMMA,
        summary="Different.",
    )
    assert not duplicate_conflict.ok
    assert duplicate_conflict.issues[0].kind == "interface_duplicate"

    satisfied = requirement.mark_requirement_satisfied(
        tmp_path,
        requirement_name="need_fixed_point",
        provider_repo="fixed_point_provider",
        note="provider ready",
    )
    assert satisfied.ok
    assert satisfied.value is not None
    assert satisfied.value.requirement.status == "satisfied"
    assert satisfied.value.requirement.provider_repo == "fixed_point_provider"

    handled = requirement.mark_requirement_handled(tmp_path, requirement_name="need_fixed_point")
    assert handled.ok
    assert handled.value is not None
    assert handled.value.requirement.status == "handled"


def test_requirement_create_get_and_list_failure_and_sorting(tmp_path: Path) -> None:
    _, _, requirement, _ = _components()

    missing_context = requirement.create_requirement(
        tmp_path,
        name="need_context",
        target_repo="provider",
    )
    assert not missing_context.ok
    assert missing_context.issues[0].kind == "requirement_missing_context"

    missing = requirement.get_requirement(tmp_path, name="missing")
    assert not missing.ok
    assert missing.issues[0].kind == "requirement_not_found"

    requirement.create_requirement(tmp_path, name="z_need", target_repo="provider", reason="z")
    requirement.create_requirement(tmp_path, name="a_need", target_repo="provider", reason="a")
    duplicate = requirement.create_requirement(tmp_path, name="a_need", target_repo="provider", reason="again")
    assert not duplicate.ok
    assert duplicate.issues[0].kind == "requirement_name_duplicate"

    listed = requirement.list_requirements(tmp_path)
    assert listed.ok
    assert listed.value is not None
    assert [item.requirement.name for item in listed.value] == ["a_need", "z_need"]

    requirement.mark_requirement_obsolete(tmp_path, requirement_name="z_need", note="not needed")
    open_only = requirement.list_requirements(tmp_path, status=RepoDependencyRequirementStatus.OPEN)
    assert open_only.ok
    assert open_only.value is not None
    assert [item.requirement.name for item in open_only.value] == ["a_need"]


def test_requirement_interface_idempotent_remove_and_open_state_rules(tmp_path: Path) -> None:
    _, _, requirement, _ = _components()
    requirement.create_requirement(tmp_path, name="need", target_repo="provider", reason="use provider")

    first = requirement.add_requirement_interface(
        tmp_path,
        requirement_name="need",
        interface_name="main",
        kind=DeclKind.THEOREM,
        summary="Main theorem.",
        statement_hint="statement",
    )
    assert first.ok
    repeated = requirement.add_requirement_interface(
        tmp_path,
        requirement_name="need",
        interface_name="main",
        kind=DeclKind.THEOREM,
        summary="Main theorem.",
        statement_hint="statement",
    )
    assert repeated.ok
    assert repeated.value is not None
    assert len(repeated.value.requirement.interfaces) == 1

    missing_remove = requirement.remove_requirement_interface(
        tmp_path,
        requirement_name="need",
        interface_name="missing",
    )
    assert not missing_remove.ok
    assert missing_remove.issues[0].kind == "interface_not_found"

    removed = requirement.remove_requirement_interface(
        tmp_path,
        requirement_name="need",
        interface_name="main",
    )
    assert removed.ok
    assert removed.value is not None
    assert removed.value.requirement.interfaces == []

    requirement.mark_requirement_satisfied(tmp_path, requirement_name="need", provider_repo="provider")
    add_closed = requirement.add_requirement_interface(
        tmp_path,
        requirement_name="need",
        interface_name="later",
        kind=DeclKind.LEMMA,
        summary="Later lemma.",
    )
    assert not add_closed.ok
    assert add_closed.issues[0].kind == "requirement_not_open"

    remove_closed = requirement.remove_requirement_interface(
        tmp_path,
        requirement_name="need",
        interface_name="main",
    )
    assert not remove_closed.ok
    assert remove_closed.issues[0].kind == "requirement_not_open"


def test_requirement_status_transition_failures_and_obsolete(tmp_path: Path) -> None:
    _, _, requirement, _ = _components()
    requirement.create_requirement(tmp_path, name="need", target_repo="provider", reason="use provider")

    handled_too_early = requirement.mark_requirement_handled(tmp_path, requirement_name="need")
    assert not handled_too_early.ok
    assert handled_too_early.issues[0].kind == "requirement_not_satisfied"

    missing_obsolete_note = requirement.mark_requirement_obsolete(
        tmp_path,
        requirement_name="need",
        note=" ",
    )
    assert not missing_obsolete_note.ok
    assert missing_obsolete_note.issues[0].kind == "missing_note"

    satisfied = requirement.mark_requirement_satisfied(
        tmp_path,
        requirement_name="need",
        provider_repo="provider",
        note="ready",
    )
    assert satisfied.ok
    handled = requirement.mark_requirement_handled(tmp_path, requirement_name="need", note="attached")
    assert handled.ok

    satisfied_after_handled = requirement.mark_requirement_satisfied(
        tmp_path,
        requirement_name="need",
        provider_repo="provider",
    )
    assert not satisfied_after_handled.ok
    assert satisfied_after_handled.issues[0].kind == "requirement_not_open"

    obsolete = requirement.mark_requirement_obsolete(
        tmp_path,
        requirement_name="need",
        note="superseded",
    )
    assert obsolete.ok
    assert obsolete.value is not None
    assert obsolete.value.requirement.status == RepoDependencyRequirementStatus.OBSOLETE
    assert obsolete.value.requirement.note == "superseded"


def test_workspace_catalog_lists_repos_and_current_repo_first(tmp_path: Path) -> None:
    _, metadata, requirement, _, catalog = _catalog_components()
    workspace = tmp_path
    plain_dir = workspace / "plain"
    plain_dir.mkdir()
    current = workspace / "consumer"
    provider = workspace / "provider"
    metadata.ensure_repo_model(current)
    metadata.ensure_repo_model(provider)
    metadata.set_repo_format(current, repo_format=RepoFormat.NATIVE, reason="current")
    metadata.set_repo_format(provider, repo_format=RepoFormat.ADAPTER, reason="provider")
    metadata.set_provider_ready(provider, summary="Provider exposes the required public interface.")
    requirement.create_requirement(current, name="need_provider", target_repo="provider", reason="use provider")

    missing = catalog.list_workspace_repos(workspace / "missing")
    assert not missing.ok
    assert missing.issues[0].kind == "workspace_not_found"

    repos = catalog.list_workspace_repos(workspace)
    assert repos.ok
    assert repos.value is not None
    assert [repo.repo_key for repo in repos.value] == ["consumer", "provider"]
    assert repos.value[0].open_requirement_count == 1
    assert repos.value[1].provider_ready is True
    assert repos.value[1].repo_summary == "Provider exposes the required public interface."

    view = catalog.get_workspace_catalog(workspace, current_repo="provider")
    assert view.ok
    assert view.value is not None
    assert [repo.repo_key for repo in view.value.repos] == ["provider", "consumer"]


def test_workspace_catalog_ready_filter_and_coordinator_view(tmp_path: Path) -> None:
    _, metadata, _, _, catalog = _catalog_components()
    workspace = tmp_path
    current = workspace / "current"
    ready_provider = workspace / "ready_provider"
    not_ready_provider = workspace / "not_ready_provider"
    metadata.ensure_repo_model(current)
    metadata.ensure_repo_model(ready_provider)
    metadata.ensure_repo_model(not_ready_provider)
    metadata.set_provider_ready(current, summary="current ready but should be excluded")
    metadata.set_provider_ready(ready_provider, summary="Ready provider summary.")

    ready = catalog.list_ready_provider_repos(workspace, current_repo="current")
    assert ready.ok
    assert ready.value is not None
    assert [repo.repo_key for repo in ready.value] == ["ready_provider"]
    assert ready.value[0].repo_summary == "Ready provider summary."

    coordinator_view = catalog.inspect_workspace_for_coordinator(current)
    assert coordinator_view.ok
    assert coordinator_view.value is not None
    assert coordinator_view.value.current_repo_root == str(current)
    assert [repo.repo_key for repo in coordinator_view.value.ready_provider_repos] == ["ready_provider"]


def test_workspace_catalog_requirement_groups_and_lake_dependency_wrapper(tmp_path: Path) -> None:
    _, metadata, requirement, _, catalog = _catalog_components()
    workspace = tmp_path
    consumer_a = workspace / "consumer_a"
    consumer_b = workspace / "consumer_b"
    metadata.ensure_repo_model(consumer_a)
    metadata.ensure_repo_model(consumer_b)
    requirement.create_requirement(
        consumer_a,
        name="need_alpha",
        target_repo="alpha",
        required_proof_availability=ProofAvailability.PROVED,
        source_description="Alpha source.",
        reason=None,
    )
    requirement.add_requirement_interface(
        consumer_a,
        requirement_name="need_alpha",
        interface_name="alpha_theorem",
        kind=DeclKind.THEOREM,
        summary="Alpha theorem.",
    )
    requirement.create_requirement(
        consumer_b,
        name="need_beta",
        target_repo="beta",
        source_description="Beta source.",
        reason=None,
    )
    requirement.create_requirement(
        consumer_b,
        name="need_alpha_b",
        target_repo="alpha",
        source_description=None,
        reason="Alpha support.",
    )
    requirement.add_requirement_interface(
        consumer_b,
        requirement_name="need_alpha_b",
        interface_name="alpha_support",
        kind=DeclKind.LEMMA,
        summary="Alpha support.",
    )

    groups = catalog.list_open_requirement_groups(workspace)
    assert groups.ok
    assert groups.value is not None
    assert [group.target_repo for group in groups.value] == ["alpha", "beta"]
    alpha = groups.value[0]
    assert alpha.requirement_count == 2
    assert alpha.required_proof_availability == ProofAvailability.PROVED
    assert alpha.provider_work_mode == RepoWorkMode.PROVED_FULL_GRAPH
    assert alpha.consumer_repos == ["consumer_a", "consumer_b"]
    assert alpha.interface_names == ["alpha_support", "alpha_theorem"]
    assert alpha.source_description_summary == "Alpha source."

    found = catalog.get_requirement_group(workspace, target_repo="alpha")
    assert found.ok
    assert found.value is not None
    assert [item.requirement.name for item in found.value.requirements] == ["need_alpha", "need_alpha_b"]
    assert found.value.required_proof_availability == ProofAvailability.PROVED
    assert found.value.provider_work_mode == RepoWorkMode.PROVED_FULL_GRAPH

    empty = catalog.get_requirement_group(workspace, target_repo="missing")
    assert empty.ok
    assert empty.value is not None
    assert empty.value.requirements == []

    lake_repo = workspace / "lake_repo"
    metadata.ensure_repo_model(lake_repo)
    (lake_repo / "lakefile.toml").write_text(
        'name = "lake_repo"\n\n[[require]]\nname = "provider"\npath = "../provider"\n',
        encoding="utf-8",
    )
    deps = catalog.list_current_lake_dependency_repos(lake_repo)
    assert deps.ok
    assert deps.value is not None
    assert [(dep.name, dep.path) for dep in deps.value] == [("provider", "../provider")]


def test_requirement_group_builds_preparation_input_and_shell(tmp_path: Path) -> None:
    workspace = tmp_path
    foundation, metadata, requirement, preparation = _components()
    consumer_a = workspace / "consumer_a"
    consumer_b = workspace / "consumer_b"
    metadata.ensure_repo_model(consumer_a)
    metadata.ensure_repo_model(consumer_b)

    requirement.create_requirement(
        consumer_a,
        name="need_metric",
        target_repo="fixed_point_provider",
        source_description="Metric preliminaries",
        reason="Consumer A needs metric lemmas.",
    )
    requirement.add_requirement_interface(
        consumer_a,
        requirement_name="need_metric",
        interface_name="complete_space",
        kind=DeclKind.DEFINITION,
        summary="A complete metric space structure.",
    )
    requirement.create_requirement(
        consumer_b,
        name="need_banach",
        target_repo="fixed_point_provider",
        source_description="Banach fixed point theorem",
        reason="Consumer B needs the final theorem.",
    )
    requirement.add_requirement_interface(
        consumer_b,
        requirement_name="need_banach",
        interface_name="banach_fixed_point",
        kind=DeclKind.THEOREM,
        summary="Banach fixed point theorem.",
    )

    group = preparation.aggregate_requirement_group(workspace, target_repo="fixed_point_provider")
    assert group.ok
    assert group.value is not None
    assert [item.requirement.name for item in group.value.requirements] == ["need_metric", "need_banach"]
    assert group.value.required_proof_availability == ProofAvailability.DECLARED
    assert group.value.provider_work_mode == RepoWorkMode.DECLARED_INTERFACE

    draft = preparation.build_preparation_input_from_group(workspace, target_repo="fixed_point_provider")
    assert draft.ok
    assert draft.value is not None
    assert draft.value.input.source_corpus_mode == SourceCorpusMode.PREPARE
    assert [ref.consumer_repo for ref in draft.value.input.requirement_refs] == ["consumer_a", "consumer_b"]
    assert {interface.name for interface in draft.value.input.interface_inputs} == {
        "complete_space",
        "banach_fixed_point",
    }
    assert "consumer_a" in draft.value.input.goal
    assert "consumer_b" in draft.value.input.goal
    assert "supporting public definitions and lemmas" in draft.value.input.goal
    assert "consumer_a/need_metric" in (draft.value.input.source_description or "")
    assert "reason: Consumer B needs the final theorem." in (draft.value.input.source_description or "")
    assert "banach_fixed_point (theorem): Banach fixed point theorem." in (
        draft.value.input.source_description or ""
    )
    assert "Requirement ordering" in (draft.value.input.notes or "")

    shell = preparation.create_provider_repo_shell_from_group(workspace, target_repo="fixed_point_provider")
    assert shell.ok
    assert shell.value is not None
    assert (workspace / "fixed_point_provider" / ".lean_constellation" / "repo.json").exists()
    assert (workspace / "fixed_point_provider" / ".lean_constellation" / "preparation_input.json").exists()
    provider_config = metadata.get_repo_config(workspace / "fixed_point_provider")
    assert provider_config.ok and provider_config.value is not None
    assert provider_config.value.config.target_proof_availability == ProofAvailability.DECLARED
    assert provider_config.value.config.work_mode == RepoWorkMode.DECLARED_INTERFACE

    duplicate = preparation.create_provider_repo_shell(workspace, target_repo="fixed_point_provider")
    assert not duplicate.ok
    assert duplicate.issues[0].kind == "target_repo_already_exists"

    catalog = metadata.runtime.repo_workspace.workspace_catalog
    summaries = catalog.list_open_requirement_groups(workspace)
    assert summaries.ok
    assert summaries.value is not None
    assert summaries.value[0].target_repo == "fixed_point_provider"
    assert summaries.value[0].requirement_count == 2


def test_provider_runtime_shell_derives_strictest_required_proof_availability(tmp_path: Path) -> None:
    workspace = tmp_path
    _, metadata, requirement, preparation = _components()
    consumer_declared = workspace / "consumer_declared"
    consumer_proved = workspace / "consumer_proved"
    metadata.ensure_repo_model(consumer_declared)
    metadata.ensure_repo_model(consumer_proved)
    requirement.create_requirement(
        consumer_declared,
        name="need_declared",
        target_repo="mixed_provider",
        required_proof_availability=ProofAvailability.DECLARED,
        reason="Declared interface is enough.",
    )
    requirement.create_requirement(
        consumer_proved,
        name="need_proved",
        target_repo="mixed_provider",
        required_proof_availability=ProofAvailability.PROVED,
        reason="Need proved interface.",
    )
    draft = preparation.build_preparation_input_from_group(workspace, target_repo="mixed_provider")
    assert draft.ok and draft.value is not None

    prepared = preparation.prepare_provider_repo_runtime_shell(
        workspace,
        target_repo="mixed_provider",
        preparation_input=draft.value.input,
    )

    assert prepared.ok and prepared.value is not None
    config = metadata.get_repo_config(workspace / "mixed_provider")
    assert config.ok and config.value is not None
    assert config.value.config.target_proof_availability == ProofAvailability.PROVED
    assert config.value.config.work_mode == RepoWorkMode.PROVED_FULL_GRAPH
    assert (workspace / "mixed_provider" / ".agent_runtime").is_dir()


def test_main_input_and_native_handoff_base_gate(tmp_path: Path) -> None:
    _, metadata, _, preparation = _components()
    input_result = preparation.build_main_repo_preparation_input(
        goal="Formalize a source corpus.",
        source_corpus_mode=SourceCorpusMode.EXISTING,
        source_description="Local notes",
        interface_inputs=[DeclInterface(name="main_theorem", kind=DeclKind.THEOREM, summary="Main theorem.")],
    )
    assert input_result.ok
    assert input_result.value is not None

    shell = preparation.create_main_repo_shell(
        tmp_path,
        repo_name="main_repo",
        project_name="MainProject",
        input=input_result.value.input,
    )
    assert shell.ok
    repo_root = tmp_path / "main_repo"
    metadata.set_repo_format(repo_root, repo_format=RepoFormat.NATIVE, reason="test")
    handoff = preparation.validate_native_handoff(repo_root)
    assert handoff.ok
    assert handoff.value is not None
    assert handoff.value.passed is False
    assert handoff.value.issues[0].kind == "native_handoff_source_corpus_not_found"

    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    handoff_ok = preparation.validate_native_handoff(repo_root)
    assert handoff_ok.ok
    assert handoff_ok.value is not None
    assert handoff_ok.value.passed is True
    assert handoff_ok.value.issues[0].kind == "native_handoff_deferred_checks"


def test_preparation_input_validation_and_missing_read(tmp_path: Path) -> None:
    _, _, _, preparation = _components()

    duplicate = preparation.write_preparation_input(
        tmp_path,
        input=RepoPreparationInput(
            goal="Prepare.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            interface_inputs=[
                DeclInterface(name="main", kind=DeclKind.THEOREM, summary="Main."),
                DeclInterface(name="main", kind=DeclKind.THEOREM, summary="Main again."),
            ],
        ),
    )
    assert not duplicate.ok
    assert duplicate.issues[0].kind == "interface_duplicate"

    invalid_mode_relpath = preparation.write_preparation_input(
        tmp_path,
        input=RepoPreparationInput(
            goal="Prepare.",
            source_corpus_mode=SourceCorpusMode.NONE,
            source_corpus_relpath=".lean_constellation/source",
        ),
    )
    assert not invalid_mode_relpath.ok
    assert invalid_mode_relpath.issues[0].kind == "invalid_source_corpus_mode"

    missing = preparation.get_preparation_input(tmp_path)
    assert not missing.ok
    assert missing.issues[0].kind == "preparation_input_missing"


def test_build_preparation_input_rejects_invalid_mode_empty_group_and_reports_interface_conflict(
    tmp_path: Path,
) -> None:
    workspace = tmp_path
    _, metadata, requirement, preparation = _components()
    consumer_a = workspace / "consumer_a"
    consumer_b = workspace / "consumer_b"
    metadata.ensure_repo_model(consumer_a)
    metadata.ensure_repo_model(consumer_b)

    empty = preparation.build_preparation_input_from_group(workspace, target_repo="provider")
    assert not empty.ok
    assert empty.issues[0].kind == "requirement_group_empty"

    requirement.create_requirement(
        consumer_a,
        name="need_alpha",
        target_repo="provider",
        reason="Use alpha.",
    )
    requirement.add_requirement_interface(
        consumer_a,
        requirement_name="need_alpha",
        interface_name="shared",
        kind=DeclKind.THEOREM,
        summary="Alpha statement.",
    )
    requirement.create_requirement(
        consumer_b,
        name="need_beta",
        target_repo="provider",
        reason="Use beta.",
    )
    requirement.add_requirement_interface(
        consumer_b,
        requirement_name="need_beta",
        interface_name="shared",
        kind=DeclKind.LEMMA,
        summary="Beta statement.",
    )

    invalid_mode = preparation.build_preparation_input_from_group(
        workspace,
        target_repo="provider",
        source_corpus_mode="bad",
    )
    assert not invalid_mode.ok
    assert invalid_mode.issues[0].kind == "invalid_source_corpus_mode"

    draft = preparation.build_preparation_input_from_group(workspace, target_repo="provider")
    assert draft.ok
    assert draft.value is not None
    assert draft.value.warnings == [
        "Interface conflict for shared; kept first from sorted requirement order."
    ]
    assert draft.value.input.interface_inputs[0].summary == "Alpha statement."
    assert "reason: Use beta." in (draft.value.input.source_description or "")


def test_create_main_repo_shell_rejects_existing_repo(tmp_path: Path) -> None:
    _, _, _, preparation = _components()
    input_result = preparation.build_main_repo_preparation_input(
        goal="Formalize notes.",
        source_corpus_mode=SourceCorpusMode.EXISTING,
    )
    assert input_result.ok
    existing = tmp_path / "main_repo"
    existing.mkdir()

    shell = preparation.create_main_repo_shell(
        tmp_path,
        repo_name="main_repo",
        project_name="MainProject",
        input=input_result.value.input,  # type: ignore[union-attr]
    )

    assert not shell.ok
    assert shell.issues[0].kind == "target_repo_already_exists"


def test_validate_native_handoff_failure_branches(tmp_path: Path) -> None:
    _, metadata, _, preparation = _components()
    repo_root = tmp_path / "repo"
    metadata.ensure_repo_model(repo_root)

    missing_input = preparation.validate_native_handoff(repo_root)
    assert missing_input.ok
    assert missing_input.value is not None
    assert missing_input.value.passed is False
    assert {issue.kind for issue in missing_input.value.issues} >= {
        "native_handoff_repo_format_invalid",
        "preparation_input_missing",
    }

    input_result = preparation.build_main_repo_preparation_input(
        goal="Prepare.",
        source_corpus_mode=SourceCorpusMode.NONE,
    )
    assert input_result.ok
    preparation.write_preparation_input(repo_root, input=input_result.value.input)  # type: ignore[union-attr]
    metadata.set_repo_format(repo_root, repo_format=RepoFormat.NATIVE, reason="native")

    source_none = preparation.validate_native_handoff(repo_root)
    assert source_none.ok
    assert source_none.value is not None
    assert source_none.value.passed is False
    assert source_none.value.issues[0].kind == "native_handoff_source_corpus_missing"


def test_validate_requirement_bootstrap_input_detects_mismatched_refs(tmp_path: Path) -> None:
    _, metadata, _, preparation = _components()
    repo_root = tmp_path / "provider"
    metadata.ensure_repo_model(repo_root)
    preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Prepare provider.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            requirement_refs=[RepoRequirementRef(consumer_repo="consumer", requirement_name="need")],
        ),
    )

    valid = preparation.validate_requirement_bootstrap_input(
        repo_root,
        requirement_refs=[RepoRequirementRef(consumer_repo="consumer", requirement_name="need")],
    )
    assert valid.ok
    assert valid.value is not None
    assert valid.value.passed is True

    invalid = preparation.validate_requirement_bootstrap_input(
        repo_root,
        requirement_refs=[RepoRequirementRef(consumer_repo="consumer", requirement_name="other")],
    )
    assert invalid.ok
    assert invalid.value is not None
    assert invalid.value.passed is False
    assert invalid.value.issue_code == "requirement_refs_mismatch"
