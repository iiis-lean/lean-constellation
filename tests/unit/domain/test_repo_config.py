from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.domain.repo import DocstringProjectionConfig, WorkspaceConfig


def test_workspace_docstring_projection_defaults_to_statement_only() -> None:
    policy = WorkspaceConfig().docstring_projection
    assert policy.include_statement_nl
    assert not policy.include_proof_nl
    assert not policy.include_sources
    assert not policy.include_dependencies
    assert policy.fingerprint() == DocstringProjectionConfig().fingerprint()


def test_workspace_docstring_projection_round_trip_and_full_policy() -> None:
    policy = DocstringProjectionConfig.full()
    restored = WorkspaceConfig.model_validate(
        {"docstring_projection": policy.model_dump(mode="json")}
    ).docstring_projection
    assert restored == policy
    assert restored.fingerprint() != DocstringProjectionConfig().fingerprint()


def test_workspace_docstring_projection_requires_statement_text() -> None:
    with pytest.raises(ValidationError, match="NL statement"):
        DocstringProjectionConfig(include_statement_nl=False)
