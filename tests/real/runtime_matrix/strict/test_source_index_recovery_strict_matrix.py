from __future__ import annotations

from types import SimpleNamespace

import pytest

from lean_constellation.app.admin_api import (
    LeanAdminApi,
    NativeSourceIndexRecoveryPreviewInput,
    NativeSourceIndexRecoveryStartInput,
)
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.unit.flows.repo_lifecycle.test_native_source_index_recovery import (
    _failed_native_source_index,
)
from tests.unit.flows.repo_lifecycle.test_native_repo_preparation_flow import (
    _advance_and_run,
    _runtime,
)


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_source_index_recovery_executes_successor_validation(
    tmp_path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    failed_parent_id, _, _ = _failed_native_source_index(runtime, lean_runtime, repo_root)
    admin = LeanAdminApi(lean_runtime)
    preview = admin.preview_native_source_index_recovery(
        NativeSourceIndexRecoveryPreviewInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=failed_parent_id,
        )
    )
    assert preview.ok and preview.value is not None
    started = admin.recover_native_source_index(
        NativeSourceIndexRecoveryStartInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=failed_parent_id,
            expected_recovery_token=preview.value.recovery_token,
            enqueue=False,
        )
    )
    assert started.ok and started.value is not None

    _advance_and_run(runtime, started.value.flow_id)
    dispatch_step_id = _advance_and_run(runtime, started.value.flow_id)
    children = runtime.flow_service.store.list_child_flows(
        parent_flow_id=started.value.flow_id,
        parent_dispatch_step_id=dispatch_step_id,
    )
    assert len(children) == 1
    successor_child_id = children[0].flow_id
    lean_runtime.ark.schedule_service = SimpleNamespace(
        active_flow_advances=set(),
        enqueue_flow=lambda _flow_id: None,
        enqueue_step=lambda _step_id: None,
    )
    lean_runtime.ark.pause_controller = SimpleNamespace(is_paused=lambda _scope_id=None: False)

    _advance_and_run(runtime, successor_child_id)
    evidence_recorder.record_runtime_state(lean_runtime)

    assert "validate_source_index_recovery_step" in evidence_recorder.evidence.logic_step_types
