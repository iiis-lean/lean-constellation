"""Pure typed fields embedded in NodeContract truth."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef, MaterialRef, NodeRef


class MaterialRefActor(StrEnum):
    COORDINATOR = "coordinator"
    WORKER = "worker"


class ContractMaterialRef(StrictModel):
    ref_id: str
    ref: MaterialRef
    reason: str | None = None
    added_by: MaterialRefActor = MaterialRefActor.COORDINATOR


class NodeDepActor(StrEnum):
    COORDINATOR = "coordinator"
    WORKER = "worker"


class NodeDep(StrictModel):
    dep_id: str
    target: NodeRef
    expected_decl_refs: list[DeclRef] = Field(default_factory=list)
    reason: str | None = None
    added_by: NodeDepActor = NodeDepActor.COORDINATOR


class MathlibUseActor(StrEnum):
    COORDINATOR = "coordinator"
    WORKER = "worker"


class NodeMathlibModuleUse(StrictModel):
    module: str
    reason: str | None = None
    added_by: MathlibUseActor = MathlibUseActor.COORDINATOR


class NodeMathlibDeclUse(StrictModel):
    name: str
    module: str | None = None
    kind: str | None = None
    reason: str | None = None
    added_by: MathlibUseActor = MathlibUseActor.COORDINATOR
