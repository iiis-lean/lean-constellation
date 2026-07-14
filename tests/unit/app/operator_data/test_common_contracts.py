from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from lean_constellation.app.operator_data.common import (
    OperatorAccess,
    OperatorInputModel,
    OperatorLockPolicy,
    OperatorOperationSpec,
    operator_gate_view,
)
from lean_constellation.app.operator_data.http_support import (
    is_loopback_host,
    parse_operator_body,
    require_loopback_host,
    service_result_json,
    validation_error_json,
)
from lean_constellation.services.foundation.result_error import ResultErrorComponent


class ExampleInput(OperatorInputModel):
    label: str
    mode: Literal["safe"] = "safe"


@pytest.mark.parametrize(
    "field",
    [
        "repo_key",
        "repo_root",
        "flow_id",
        "step_id",
        "agent_id",
        "scope_id",
        "ark_runtime_snapshot_id",
        "added_by",
        "lean_check",
        "diagnostics_passed",
        "method_name",
        "service_name",
        "lock_policy",
    ],
)
def test_operator_body_rejects_execution_controlled_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        ExampleInput.model_validate({"label": "x", field: "forged"})


def test_operator_body_is_strict_and_requires_json_object() -> None:
    assert parse_operator_body(ExampleInput, {"label": "x"}).label == "x"
    with pytest.raises(ValidationError):
        parse_operator_body(ExampleInput, {"label": "x", "unknown": True})
    with pytest.raises(ValueError, match="JSON object"):
        parse_operator_body(ExampleInput, ["x"])


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.12.34.56", "::1", "[::1]"])
def test_loopback_hosts_are_accepted(host: str) -> None:
    assert is_loopback_host(host)
    assert require_loopback_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.2", "example.test", "localhost.localdomain", "::"])
def test_non_loopback_hosts_are_rejected(host: str) -> None:
    assert not is_loopback_host(host)
    with pytest.raises(ValueError, match="loopback"):
        require_loopback_host(host)


def test_operation_policy_is_internal_and_consistent() -> None:
    read = OperatorOperationSpec(
        name="read",
        access=OperatorAccess.READ,
        lock_policy=OperatorLockPolicy.NONE,
    )
    assert read.access is OperatorAccess.READ
    with pytest.raises(ValueError, match="cannot acquire"):
        OperatorOperationSpec(
            name="bad-read",
            access=OperatorAccess.READ,
            lock_policy=OperatorLockPolicy.OPERATOR,
        )
    with pytest.raises(ValueError, match="must use"):
        OperatorOperationSpec(
            name="bad-mutation",
            access=OperatorAccess.MUTATION,
            lock_policy=OperatorLockPolicy.NONE,
            requires_stable_runtime=True,
        )
    with pytest.raises(ValueError, match="stable runtime"):
        OperatorOperationSpec(
            name="unstable-mutation",
            access=OperatorAccess.MUTATION,
            lock_policy=OperatorLockPolicy.OPERATOR,
        )


def test_service_result_json_preserves_result_and_issue_vocabulary() -> None:
    result = ResultErrorComponent()
    success = service_result_json(result.ok({"value": 1}))
    raw_message = "No: /private/operator/path"
    failure = service_result_json(result.fail(result.issue("blocked", raw_message)))

    assert success == {"ok": True, "value": {"value": 1}, "issues": []}
    assert failure["ok"] is False
    assert failure["issues"][0]["kind"] == "blocked"
    assert failure["issues"][0]["message"] == (
        "The operator request could not be completed. Inspect server logs for internal details."
    )
    assert raw_message not in str(failure)
    assert validation_error_json(ValueError("bad"))["issues"][0]["kind"] == "operator_request_validation_failed"


def test_operator_gate_issue_projection_never_uses_raw_service_message() -> None:
    result = ResultErrorComponent()
    raw_message = "Internal gate failed at /private/workspace/Repo/Main.lean"
    public = operator_gate_view(
        result.gate_failed(
            "fixture_gate",
            result.issue("unmapped_internal_failure", raw_message),
            summary="Fixture gate failed.",
        )
    )

    assert public.issues[0].kind == "unmapped_internal_failure"
    assert public.issues[0].message == (
        "The operator request could not be completed. Inspect server logs for internal details."
    )
    assert raw_message not in public.model_dump_json()
