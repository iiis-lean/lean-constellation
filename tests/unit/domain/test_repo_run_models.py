from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.domain.repo_run import RepoRunContext, RepoRunSpec, SourceScope


def _interface(name: str) -> DeclInterface:
    return DeclInterface(name=name, kind=DeclKind.THEOREM, summary=f"Interface {name}.")


def test_source_scope_validates_and_normalizes_mode_specific_selectors() -> None:
    assert SourceScope(mode="none").selectors == []
    assert SourceScope(mode="all").selectors == []
    assert SourceScope(mode="selected", selectors=[" chapter_10.tex "]).selectors == ["chapter_10.tex"]

    with pytest.raises(ValidationError):
        SourceScope(mode="none", selectors=["chapter_10.tex"])
    with pytest.raises(ValidationError):
        SourceScope(mode="all", selectors=["chapter_10.tex"])
    with pytest.raises(ValidationError):
        SourceScope(mode="selected")
    with pytest.raises(ValidationError):
        SourceScope(mode="selected", selectors=[" "])


def test_repo_run_spec_reuses_repo_config_validation_and_rejects_duplicate_interfaces() -> None:
    value = RepoRunSpec(
        run_objective=" Build the declared provider interface. ",
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
        source_scope=SourceScope(mode="selected", selectors=["chapter_10.tex"]),
        index_policy="auto",
        root_interface_policy="prepare",
        additional_required_interfaces=[_interface("WeightedSieve.bound")],
    )

    assert value.run_objective == "Build the declared provider interface."
    assert value.model_validate(value.model_dump(mode="json")) == value

    with pytest.raises(ValidationError):
        RepoRunSpec(
            run_objective="Invalid target and work mode.",
            target_proof_availability=ProofAvailability.PROVED,
            work_mode=RepoWorkMode.DECLARED_INTERFACE,
            source_scope=SourceScope(mode="none"),
            index_policy="reuse",
            root_interface_policy="reuse",
        )
    with pytest.raises(ValidationError):
        RepoRunSpec(
            run_objective="Duplicate interface names.",
            target_proof_availability=ProofAvailability.DECLARED,
            work_mode=RepoWorkMode.DECLARED_INTERFACE,
            source_scope=SourceScope(mode="none"),
            index_policy="reuse",
            root_interface_policy="reuse",
            additional_required_interfaces=[_interface("T"), _interface("T")],
        )


def test_repo_run_context_roundtrips_without_becoming_repo_truth() -> None:
    run_spec = RepoRunSpec(
        run_objective="Continue the existing proof graph.",
        target_proof_availability=ProofAvailability.PROVED,
        work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
        source_scope=SourceScope(mode="none"),
        index_policy="reuse",
        root_interface_policy="auto",
    )
    context = RepoRunContext(
        start_kind="continuation",
        run_spec=run_spec,
        resolved_source_files=["chapter_10.tex"],
        source_index_delta_summary="No index update was required.",
        base_release_id="release_1",
    )

    assert RepoRunContext.model_validate(context.model_dump(mode="json")) == context


def test_source_none_with_explicit_update_is_a_valid_flow_level_no_op_request() -> None:
    value = RepoRunSpec(
        run_objective="Do not expand the source responsibility in this run.",
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_FULL_GRAPH,
        source_scope=SourceScope(mode="none"),
        index_policy="update",
        root_interface_policy="reuse",
    )

    assert value.source_scope.mode == "none"
    assert value.index_policy == "update"
