"""Strict HTTP-boundary helpers for Operator Data API routes."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any, TypeVar

from pydantic import ValidationError

from lean_constellation.app.operator_data.common import (
    OperatorHttpEnvelope,
    OperatorInputModel,
    OperatorResult,
    operator_issue_view,
    project_operator_result,
)
from lean_constellation.services.foundation import ServiceIssue, ServiceResult


TInput = TypeVar("TInput", bound=OperatorInputModel)


def is_loopback_host(host: str) -> bool:
    """Accept only explicit localhost or loopback IP bind hosts."""

    value = str(host).strip().lower()
    if value == "localhost":
        return True
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return ip_address(value).is_loopback
    except ValueError:
        return False


def require_loopback_host(host: str) -> str:
    value = str(host).strip()
    if not is_loopback_host(value):
        raise ValueError("Operator Data API may only bind to a loopback host.")
    return value


def parse_operator_body(model_type: type[TInput], body: object) -> TInput:
    """Parse a JSON object into one strict typed business DTO."""

    if not isinstance(body, dict):
        raise ValueError("Operator request body must be a JSON object.")
    return model_type.model_validate(body)


def service_result_json(result: ServiceResult[Any] | OperatorResult[Any]) -> dict[str, Any]:
    """Serialize ServiceResult without replacing its issue vocabulary."""

    public_result = (
        result if isinstance(result, OperatorResult) else project_operator_result(result)
    )
    dumped = public_result.model_dump(mode="json")
    return OperatorHttpEnvelope.model_validate(dumped).model_dump(mode="json")


def validation_error_json(exc: ValidationError | ValueError) -> dict[str, Any]:
    """Return a stable request-validation envelope distinct from domain issues."""

    del exc
    issue = operator_issue_view(
        ServiceIssue(
            kind="operator_request_validation_failed",
            message="Request validation details remain private.",
        )
    )
    return OperatorHttpEnvelope(ok=False, issues=[issue]).model_dump(mode="json")


__all__ = [
    "is_loopback_host",
    "parse_operator_body",
    "require_loopback_host",
    "service_result_json",
    "validation_error_json",
]
