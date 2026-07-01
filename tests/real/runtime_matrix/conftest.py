import pytest

from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import runtime_matrix_workspace


@pytest.fixture(scope="session")
def runtime_matrix_session_evidence() -> EvidenceRecorder:
    return EvidenceRecorder()


@pytest.fixture(scope="session")
def runtime_matrix_evidence_dir(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("runtime_matrix_session_evidence"))


@pytest.fixture
def evidence_recorder(runtime_matrix_session_evidence: EvidenceRecorder) -> EvidenceRecorder:
    recorder = EvidenceRecorder()
    yield recorder
    runtime_matrix_session_evidence.merge_from(recorder)


__all__ = [
    "runtime_matrix_workspace",
    "runtime_matrix_session_evidence",
    "runtime_matrix_evidence_dir",
    "evidence_recorder",
]
