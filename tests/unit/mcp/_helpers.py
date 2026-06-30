from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import BaseSubmission

from lean_constellation.mcp import runtime_context_to_env
from lean_constellation.services import LeanProviderOverrides, create_test_runtime_services
from lean_constellation.services.tool_facade import RuntimeToolContext
from lean_constellation.tools import register_submit_tooling


class FakeSubmissionGateway:
    def __init__(self) -> None:
        self.accepted: list[BaseSubmission] = []

    def accept_step_submission(self, ctx, submission: BaseSubmission):
        del ctx
        self.accepted.append(submission)
        return {"accepted": True}


def make_mcp_runtime(gateway: FakeSubmissionGateway | None = None):
    runtime = create_test_runtime_services(
        providers=LeanProviderOverrides(submission_gateway=gateway),
        register_application_tools=True,
    )
    assert register_submit_tooling(runtime).ok
    return runtime


def runtime_env(
    repo_root: Path,
    *,
    view: str,
    agent_type: str,
    role: str,
    flow_id: str = "flow_mcp",
    step_id: str = "step_mcp",
    agent_id: str = "agent_mcp",
) -> dict[str, str]:
    return runtime_context_to_env(
        RuntimeToolContext(
            flow_id=flow_id,
            step_id=step_id,
            agent_id=agent_id,
            scope_id="scope_mcp",
            agent_type=agent_type,
            agent_role=role,  # type: ignore[arg-type]
            expected_view_key=view,
            repo_root=repo_root,
            node_path="Main.Core",
            node_kind="content",
            contract_version=1,
            stage="statement_nl",
            round_id="round_mcp",
            batch_decls=["Main.result"],
            current_decl="Main.result",
            decl_kind="theorem",
        )
    )
