"""Testing-oriented AgentTypeSpec helpers."""

from __future__ import annotations

from collections.abc import Iterable

from lean_constellation.agents.models import AgentTypeSpec
from lean_constellation.agents.registry import build_agent_type_specs, derive_agent_type_spec


def controlled_test_agent_type_name(base_agent_type: str) -> str:
    """Return the conventional controlled test AgentType name for a base AgentType."""

    key = base_agent_type.strip()
    stem = key.removesuffix("Agent")
    return f"{stem}ControlledTestAgent"


def build_controlled_test_agent_type_specs(
    *,
    specs: Iterable[AgentTypeSpec] | None = None,
    base_agent_types: Iterable[str] | None = None,
) -> list[AgentTypeSpec]:
    """Derive `{Base}ControlledTestAgent` specs without mutating production specs."""

    resolved_specs = list(specs) if specs is not None else build_agent_type_specs()
    by_type = {spec.agent_type: spec for spec in resolved_specs}
    target_bases = list(base_agent_types) if base_agent_types is not None else [spec.agent_type for spec in resolved_specs]
    derived_specs: list[AgentTypeSpec] = []
    existing = set(by_type)
    for base_agent_type in target_bases:
        base = by_type[base_agent_type.strip()]
        if base.extends_agent_type:
            continue
        agent_type = controlled_test_agent_type_name(base.agent_type)
        if agent_type in existing:
            continue
        derived = derive_agent_type_spec(
            base_agent_type=base.agent_type,
            agent_type=agent_type,
            specs=resolved_specs,
        )
        derived_specs.append(derived)
        existing.add(agent_type)
    return derived_specs


__all__ = [
    "build_controlled_test_agent_type_specs",
    "controlled_test_agent_type_name",
]
