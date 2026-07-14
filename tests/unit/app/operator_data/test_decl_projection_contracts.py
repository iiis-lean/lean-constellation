from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.app.operator_data.decl_projection import FormalApplyInput


def test_formal_apply_input_requires_business_stale_guards_and_rejects_forged_check() -> None:
    payload = {
        "node_path": "Main.Topic.Core",
        "round_id": "round-1",
        "decl_name": "main_result",
        "expected_revision": 1,
        "expected_state": "planned",
        "expected_revision_digest": "abc",
        "lean_code": "theorem main_result : True := by sorry",
    }
    parsed = FormalApplyInput.model_validate(payload)
    assert parsed.expected_revision == 1
    with pytest.raises(ValidationError):
        FormalApplyInput.model_validate({**payload, "lean_check": {"status": "passed"}})
    with pytest.raises(ValidationError):
        FormalApplyInput.model_validate({**payload, "repo_root": "/tmp/repo"})
    with pytest.raises(ValidationError):
        FormalApplyInput.model_validate({**payload, "skip_check": True})
