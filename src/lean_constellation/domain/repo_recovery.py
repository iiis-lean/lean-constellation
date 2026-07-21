"""Fail-closed contracts for narrow native preparation recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Literal

from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel


class NativeSourceIndexRecoveryContract(StrictModel):
    """Auditable compare-and-swap contract for one failed SourceIndex lineage."""

    recovery_kind: Literal["native_source_index_successor"] = "native_source_index_successor"
    repo_key: str
    repo_root: str
    failed_parent_flow_id: str
    failed_source_index_flow_id: str
    failed_step_id: str
    failed_step_error_type: Literal["step_run_exception"]
    failed_step_error_message: str
    pre_run_mutation_checkpoint_id: str
    baseline_digest: str = Field(min_length=64, max_length=64)
    draft_digest: str = Field(min_length=64, max_length=64)
    review_round: int = Field(ge=2)
    max_review_rounds: int = Field(ge=2)
    reviewer_feedback: str
    latest_builder_summary: str | None = None
    resolved_file_paths: list[str] = Field(default_factory=list)
    readable_file_paths: list[str] = Field(default_factory=list)
    artifact_file_paths: list[str] = Field(default_factory=list)
    manifest_digest: str
    source_corpus_mode: Literal["existing", "prepare"]
    allow_interface_supplement: bool
    recovery_token: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def _validate_recovery_token(self) -> "NativeSourceIndexRecoveryContract":
        expected = native_source_index_recovery_token(self.model_dump(mode="json"))
        if not hmac.compare_digest(self.recovery_token, expected):
            raise ValueError("Native SourceIndex recovery token does not cover its contract")
        return self


def native_source_index_recovery_token(payload: dict[str, object]) -> str:
    """Hash every recovery invariant except the token itself."""

    canonical = dict(payload)
    canonical.pop("recovery_token", None)
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["NativeSourceIndexRecoveryContract", "native_source_index_recovery_token"]
