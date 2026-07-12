"""Shared declaration-kind policy for proof availability targets."""

from __future__ import annotations

from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.services.decl_graph.models import DeclState


_THEOREM_LIKE_KINDS = {"theorem", "lemma", "proposition", "corollary"}


def is_theorem_like(kind: str) -> bool:
    return kind.strip().lower() in _THEOREM_LIKE_KINDS


def required_state_for_availability(kind: str, target: ProofAvailability) -> DeclState:
    if target == ProofAvailability.PROVED and is_theorem_like(kind):
        return DeclState.PROVED
    return DeclState.DECLARED


def required_check_stage(kind: str, target: ProofAvailability) -> str:
    if target == ProofAvailability.PROVED and is_theorem_like(kind):
        return "proof"
    return "statement"


__all__ = ["is_theorem_like", "required_check_stage", "required_state_for_availability"]
