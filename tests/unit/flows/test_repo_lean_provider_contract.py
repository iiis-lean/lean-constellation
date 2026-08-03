from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.flows.repo_exploration.submissions import RepoLeanProviderCandidate
from lean_constellation.tools.submit_args import RepoLeanProviderCandidateArg


def _ready_candidate() -> dict[str, object]:
    return {
        "git_url": "https://github.com/owner/provider",
        "resolved_revision": "a" * 40,
        "subdir": None,
        "package_name": "Provider",
        "likely_import_modules": ["Provider.Main"],
        "relevant_interfaces": ["Provider.kneser_chromatic_number"],
        "lean_evidence": [
            "Provider/Main.lean: theorem Provider.kneser_chromatic_number"
        ],
        "adapter_feasibility": "ready",
        "gaps": [],
        "risks": ["Lean version compatibility still requires deterministic preparation."],
        "recommendation": "direct_adapter_requirement",
    }


def test_direct_adapter_candidate_requires_complete_immutable_lean_evidence() -> None:
    data = _ready_candidate()

    assert RepoLeanProviderCandidateArg.model_validate(data).resolved_revision == "a" * 40
    assert RepoLeanProviderCandidate.model_validate(data).package_name == "Provider"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("resolved_revision", "main", "immutable commit"),
        ("package_name", None, "package name"),
        ("likely_import_modules", [], "import module"),
        ("relevant_interfaces", [], "relevant Lean interfaces"),
        ("lean_evidence", [], "Lean file or declaration evidence"),
        ("lean_evidence", ["This is a Lean 4 repository."], "name a Lean path"),
        ("gaps", ["declaration not inspected"], "unresolved evidence gaps"),
    ],
)
def test_direct_adapter_candidate_rejects_missing_evidence(
    field: str,
    value: object,
    message: str,
) -> None:
    data = _ready_candidate()
    data[field] = value

    with pytest.raises(ValidationError, match=message):
        RepoLeanProviderCandidateArg.model_validate(data)
    with pytest.raises(ValidationError, match=message):
        RepoLeanProviderCandidate.model_validate(data)


def test_provider_candidate_consistency_rejects_mathlib_and_unsuitable_generic() -> None:
    mathlib = _ready_candidate()
    mathlib["git_url"] = "https://github.com/leanprover-community/mathlib4"
    unsuitable = _ready_candidate()
    unsuitable.update(
        {
            "adapter_feasibility": "unsuitable",
            "recommendation": "generic_requirement",
        }
    )

    with pytest.raises(ValidationError, match="Mathlib"):
        RepoLeanProviderCandidateArg.model_validate(mathlib)
    with pytest.raises(ValidationError, match="Mathlib"):
        RepoLeanProviderCandidate.model_validate(mathlib)
    with pytest.raises(ValidationError, match="must be ignored"):
        RepoLeanProviderCandidateArg.model_validate(unsuitable)
    with pytest.raises(ValidationError, match="must be ignored"):
        RepoLeanProviderCandidate.model_validate(unsuitable)


def test_plausible_generic_candidate_preserves_explicit_gaps() -> None:
    candidate = _ready_candidate()
    candidate.update(
        {
            "resolved_revision": "main",
            "package_name": None,
            "likely_import_modules": [],
            "relevant_interfaces": [],
            "lean_evidence": ["path: lean-toolchain"],
            "adapter_feasibility": "plausible",
            "gaps": ["immutable revision and declaration evidence remain unresolved"],
            "recommendation": "generic_requirement",
        }
    )

    parsed = RepoLeanProviderCandidate.model_validate(candidate)

    assert parsed.gaps == [
        "immutable revision and declaration evidence remain unresolved"
    ]
