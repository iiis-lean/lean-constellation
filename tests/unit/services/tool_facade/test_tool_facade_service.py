from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.services.external_clients import ExternalClientService, LeanMcpToolkitClient
from lean_constellation.services.foundation import FoundationContext, FoundationService
from lean_constellation.services.tool_facade import (
    ContractMutationFieldGroup,
    RawToolCallContext,
    RuntimeToolContext,
    SubmissionView,
    SubmitBehavior,
    ToolCapability,
    ToolFacadeService,
    ToolGroupSpec,
    ToolSpec,
    ToolViewSpec,
)
from lean_constellation.domain.common import StrictModel


class EchoArgs(StrictModel):
    message: str = Field(description="Message to echo back to the Agent.")


class SubmitArgs(StrictModel):
    summary: str = Field(description="Summary to persist in the successful submission.")


class FileArgs(StrictModel):
    file_path: str = Field(description="Repo-relative file path to pass through the toolkit proxy.")


class FakeSubmissionGateway:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.accepted: list[SubmissionView] = []

    def accept_step_submission(self, ctx, submission: SubmissionView):
        del ctx
        self.accepted.append(submission)
        return self.foundation.ok({"accepted": True})


class FakeToolkitDispatcher:
    def __call__(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "tool_name": tool_name,
            "payload": payload,
            "repo_root": "/hidden/repo",
            "server_path": "/hidden/server",
            "traceback": "hidden",
            "items": [{"name": "Nat.add_assoc", "module": "Mathlib"}],
        }


def _write_preparation_input(repo_root: Path) -> None:
    foundation = FoundationService()
    prep = RepoPreparationInput(
        goal="Formalize a unit-test source.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        interface_inputs=[
            DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose the main theorem.")
        ],
    )
    path = foundation.layout.preparation_input_path(FoundationContext(repo_root=repo_root))
    assert foundation.store.write_json_atomic(path, prep).ok


def _runtime(repo_root: Path, *, view: str = "worker_view", role: str = "worker", successful: bool = False) -> RuntimeToolContext:
    return RuntimeToolContext(
        flow_id="flow_1",
        step_id="step_1",
        agent_id="agent_1",
        agent_type="statement_nl_worker",
        agent_role=role,
        expected_view_key=view,
        repo_root=repo_root,
        node_path="Main.Core",
        node_kind="content",
        contract_version=1,
        stage="statement_nl",
        round_id="round_1",
        batch_decls=["decl_a"],
        current_decl="decl_a",
        decl_kind="theorem",
        successful_submission_count=1 if successful else 0,
        successful_submission_kind="terminal" if successful else None,
    )


def _tool_service(tmp_path: Path) -> tuple[ToolFacadeService, list[str], FakeSubmissionGateway]:
    foundation = FoundationService()
    external = ExternalClientService(lean_mcp_toolkit=LeanMcpToolkitClient(dispatcher=FakeToolkitDispatcher()))
    gateway = FakeSubmissionGateway(foundation)
    calls: list[str] = []

    def echo_handler(ctx, args: EchoArgs):
        calls.append(f"{ctx.actor.role}:{args.message}")
        return foundation.ok({"summary": "Echo completed.", "message": args.message})

    def submit_handler(ctx, args: SubmitArgs):
        calls.append(f"submit:{args.summary}")
        return foundation.ok(
            {
                "summary": "Submit gate passed.",
                "flow_requests": [{"flow_type": "unit_child", "input": {"summary": args.summary}}],
            }
        )

    service = ToolFacadeService(foundation=foundation, external=external, submission_gateway=gateway)
    registered = service.register_application_tools(
        [
            ToolSpec(
                name="echo_write",
                description="Echo a message through a write-capability test tool.",
                args_model=EchoArgs,
                capability=ToolCapability.WRITE,
                backing_service="test",
                backing_method="echo",
                result_view="echo",
                required_context={"repo"},
                tool_groups={"worker_write"},
                allowed_roles={"worker"},
                backing_handler=echo_handler,
            ),
            ToolSpec(
                name="submit_ready",
                description="Submit a ready terminal result for the current AgentStep.",
                args_model=SubmitArgs,
                capability=ToolCapability.SUBMIT,
                backing_service="test",
                backing_method="submit_ready",
                result_view="submit_ready",
                required_context={"repo"},
                tool_groups={"worker_submit"},
                allowed_roles={"worker"},
                submit_behavior=SubmitBehavior.DISPATCH_CHILD_FLOWS,
                backing_handler=submit_handler,
            ),
            ToolSpec(
                name="toolkit_read_file",
                description="Call a toolkit proxy with a safe repo-relative file path.",
                args_model=FileArgs,
                capability=ToolCapability.READ,
                backing_service="external",
                backing_method="call_tool",
                result_view="toolkit_proxy",
                required_context={"repo"},
                tool_groups={"toolkit_read"},
                allowed_roles={"worker"},
                toolkit_proxy_name="toolkit_read_file",
            ),
        ]
    )
    assert registered.ok
    groups = service.register_tool_groups(
        [
            ToolGroupSpec(key="worker_write", tool_names=["echo_write"], skill_keys=["worker-write"]),
            ToolGroupSpec(key="worker_submit", tool_names=["submit_ready"]),
            ToolGroupSpec(key="toolkit_read", tool_names=["toolkit_read_file"]),
        ]
    )
    assert groups.ok
    views = service.register_tool_views(
        [
            ToolViewSpec(
                key="worker_view",
                group_keys=["worker_write", "worker_submit", "toolkit_read"],
                allowed_agent_types=["statement_nl_worker"],
            )
        ]
    )
    assert views.ok
    return service, calls, gateway


def test_context_resolver_parses_runtime_and_protected_interfaces(tmp_path: Path) -> None:
    _write_preparation_input(tmp_path)
    service, _, _ = _tool_service(tmp_path)
    raw = RawToolCallContext(endpoint_view_key="worker_view", runtime_context=_runtime(tmp_path))

    resolved = service.context_resolver.resolve_tool_context(raw)

    assert resolved.ok
    assert resolved.value is not None
    assert resolved.value.actor.role == "worker"
    assert resolved.value.actor.added_by == "worker"
    assert resolved.value.repo.repo_key == tmp_path.name
    assert resolved.value.node is not None
    assert resolved.value.node.node_path == "Main.Core"
    assert resolved.value.decl_stage is not None
    assert resolved.value.decl_stage.batch_decls == ["decl_a"]

    protected = service.context_resolver.resolve_root_interface_protection(resolved.value)
    assert protected.ok
    assert protected.value is not None
    assert protected.value.protected_names == ["main_result"]

    mismatch = service.context_resolver.resolve_tool_context(
        RawToolCallContext(endpoint_view_key="other_view", runtime_context=_runtime(tmp_path))
    )
    assert not mismatch.ok
    assert mismatch.issues[0].kind == "tool_view_mismatch"


def test_tool_view_registry_validates_views_and_alignment(tmp_path: Path) -> None:
    service, _, _ = _tool_service(tmp_path)

    app = service.build_mcp_view_server("worker_view")
    assert app.ok
    assert app.value is not None
    assert app.value.tool_names == ["echo_write", "submit_ready", "toolkit_read_file"]
    assert app.value.tools[0].args_schema["properties"]

    listed = service.list_registered_tools(group_key="worker_write")
    assert listed.ok
    assert [tool.name for tool in listed.value or []] == ["echo_write"]

    bad = service.register_tool_views(
        [
            ToolViewSpec(
                key="bad_overlap",
                group_keys=["worker_write"],
                extra_tool_names=["echo_write"],
                allowed_agent_types=["statement_nl_worker"],
            )
        ]
    )
    assert not bad.ok
    assert bad.issues[0].kind == "tool_view_group_overlap"

    alignment = service.tool_view.validate_tool_skill_alignment("statement_nl_worker")
    assert alignment.ok
    assert alignment.value is not None
    assert alignment.value.passed is True
    assert alignment.value.issues[0].kind == "tool_skill_alignment_missing"


def test_permission_guard_blocks_wrong_roles_and_object_mutations(tmp_path: Path) -> None:
    service, _, _ = _tool_service(tmp_path)
    ctx = service.context_resolver.resolve_tool_context(
        RawToolCallContext(endpoint_view_key="worker_view", runtime_context=_runtime(tmp_path))
    ).value
    assert ctx is not None

    assert service.permission_guard.assert_tool_allowed(ctx, tool_name="echo_write").ok
    denied_delete = service.permission_guard.assert_contract_mutation_allowed(
        ctx,
        field_group=ContractMutationFieldGroup.REFS,
        item_added_by="coordinator",
    )
    assert not denied_delete.ok
    assert denied_delete.issues[0].kind == "contract_mutation_rejected"

    wrong_stage = service.permission_guard.assert_decl_stage_mutation_allowed(
        ctx,
        stage="proof_nl",
        decl_name="decl_a",
    )
    assert not wrong_stage.ok
    assert wrong_stage.issues[0].kind == "decl_stage_mutation_rejected"

    reviewer_ctx = service.context_resolver.resolve_tool_context(
        RawToolCallContext(endpoint_view_key="worker_view", runtime_context=_runtime(tmp_path, role="reviewer"))
    ).value
    assert reviewer_ctx is not None
    role_rejected = service.permission_guard.assert_tool_allowed(reviewer_ctx, tool_name="echo_write")
    assert not role_rejected.ok
    assert role_rejected.issues[0].kind == "role_not_allowed"
    assert service.permission_guard.assert_review_only(reviewer_ctx).ok
    assert not service.permission_guard.assert_admin(reviewer_ctx).ok


def test_mcp_wrapper_invokes_handlers_and_records_submit(tmp_path: Path) -> None:
    service, calls, gateway = _tool_service(tmp_path)
    raw = RawToolCallContext(endpoint_view_key="worker_view", runtime_context=_runtime(tmp_path))

    echoed = service.invoke_agent_tool(raw, tool_name="echo_write", flat_args={"message": "hello"})
    assert echoed.ok
    assert echoed.value is not None
    assert echoed.value.ok is True
    assert echoed.value.value == {"summary": "Echo completed.", "message": "hello"}
    assert calls == ["worker:hello"]

    bad_args = service.invoke_agent_tool(raw, tool_name="echo_write", flat_args={})
    assert bad_args.ok
    assert bad_args.value is not None
    assert bad_args.value.ok is False
    assert bad_args.value.issues[0].kind == "tool_arguments_invalid"
    assert calls == ["worker:hello"]

    submitted = service.invoke_agent_tool(raw, tool_name="submit_ready", flat_args={"summary": "done"})
    assert submitted.ok
    assert submitted.value is not None
    assert submitted.value.ok is True
    assert "Stop making further" in submitted.value.summary
    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].submission_kind == "dispatch_child_flows"
    assert gateway.accepted[0].payload["flow_requests"][0]["flow_type"] == "unit_child"

    after_submit = service.invoke_agent_tool(
        RawToolCallContext(endpoint_view_key="worker_view", runtime_context=_runtime(tmp_path, successful=True)),
        tool_name="echo_write",
        flat_args={"message": "again"},
    )
    assert after_submit.ok
    assert after_submit.value is not None
    assert after_submit.value.ok is False
    assert after_submit.value.issues[0].kind == "submission_already_accepted"


def test_toolkit_proxy_path_guard_and_normalization(tmp_path: Path) -> None:
    service, _, _ = _tool_service(tmp_path)
    (tmp_path / "Main.lean").write_text("import Mathlib\n", encoding="utf-8")
    raw = RawToolCallContext(endpoint_view_key="worker_view", runtime_context=_runtime(tmp_path))
    ctx = service.context_resolver.resolve_tool_context(raw).value
    assert ctx is not None

    safe = service.mcp_wrapper.resolve_toolkit_path_arg(
        ctx,
        arg_name="file_path",
        arg_value="Main.lean",
        mode="repo_relative_file",
    )
    assert safe.ok
    assert safe.value is not None
    assert safe.value.exists is True

    escaped = service.invoke_agent_tool(
        raw,
        tool_name="toolkit_read_file",
        flat_args={"file_path": "../outside.lean"},
    )
    assert escaped.ok
    assert escaped.value is not None
    assert escaped.value.ok is False
    assert escaped.value.issues[0].kind == "toolkit_path_rejected"

    proxied = service.invoke_agent_tool(
        raw,
        tool_name="toolkit_read_file",
        flat_args={"file_path": "Main.lean"},
    )
    assert proxied.ok
    assert proxied.value is not None
    assert proxied.value.ok is True
    result = proxied.value.value["result"]
    assert "repo_root" not in result
    assert "server_path" not in result
    assert "traceback" not in result
    assert result["items"] == [{"name": "Nat.add_assoc", "module": "Mathlib"}]
