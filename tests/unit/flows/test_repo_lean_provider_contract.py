from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.flows.repo_exploration.submissions import RepoLeanProviderCandidate
from lean_constellation.tools.submit_args import RepoLeanProviderCandidateArg


def _ready_candidate() -> dict[str, object]:
    return {
        "git_url": "https://github.com/owner/provider",
        "revision": "a" * 40,
        "subdir": None,
        "capability_summary": "Provides the required Kneser theorem.",
        "relevant_declarations": ["Provider.kneser_chromatic_number"],
        "gaps": [],
        "risks": ["Lean version compatibility still requires deterministic preparation."],
        "recommendation": "direct_adapter_requirement",
    }


def test_direct_adapter_agent_candidate_only_contains_judgment_and_locator_fields() -> None:
    data = _ready_candidate()

    parsed = RepoLeanProviderCandidateArg.model_validate(data)

    assert parsed.revision == "a" * 40
    assert parsed.relevant_declarations == ["Provider.kneser_chromatic_number"]
    assert "package_name" not in RepoLeanProviderCandidateArg.model_fields


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("revision", "main", "immutable"),
        ("relevant_declarations", [], "relevant declaration"),
        ("gaps", ["declaration not inspected"], "unresolved evidence gaps"),
    ],
)
def test_direct_adapter_agent_candidate_rejects_invalid_judgment_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    data = _ready_candidate()
    data[field] = value

    with pytest.raises(ValidationError, match=message):
        RepoLeanProviderCandidateArg.model_validate(data)


@pytest.mark.parametrize(
    "legacy_field",
    [
        "resolved_revision",
        "package_name",
        "likely_import_modules",
        "relevant_interfaces",
        "lean_evidence",
        "adapter_feasibility",
    ],
)
def test_agent_candidate_rejects_backend_owned_legacy_fields(legacy_field: str) -> None:
    candidate = _ready_candidate()
    candidate[legacy_field] = "legacy"

    with pytest.raises(ValidationError, match=legacy_field):
        RepoLeanProviderCandidateArg.model_validate(candidate)


def test_plausible_generic_candidate_preserves_explicit_gaps() -> None:
    candidate = _ready_candidate()
    candidate.update(
        {
            "revision": None,
            "relevant_declarations": [],
            "gaps": ["immutable revision and declaration evidence remain unresolved"],
            "recommendation": "generic_requirement",
        }
    )

    parsed = RepoLeanProviderCandidateArg.model_validate(candidate)

    assert parsed.gaps == [
        "immutable revision and declaration evidence remain unresolved"
    ]


def test_canonical_provider_candidate_contains_backend_probe_facts() -> None:
    candidate = RepoLeanProviderCandidate(
        git_url="https://github.com/owner/provider",
        resolved_revision="a" * 40,
        package_name="Provider",
        likely_import_modules=["Provider.Main"],
        lean_toolchain="leanprover/lean4:v4.32.0",
        has_lakefile=True,
        has_lean_manifest=True,
        has_lean_files=True,
        capability_summary="Provides the required Kneser theorem.",
        relevant_declarations=["Provider.kneser_chromatic_number"],
        lean_evidence=["path:Provider/Main.lean"],
        recommendation="direct_adapter_requirement",
    )

    assert candidate.package_name == "Provider"
    assert candidate.lean_evidence == ["path:Provider/Main.lean"]
