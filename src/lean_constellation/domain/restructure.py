"""Static and process contracts for the LC Restructure workflow.

The models in this module are deliberately independent from the native LC
DeclGraph/Round models.  Restructure stores the declarations an Agent has
explicitly registered and the exact source/template bytes it captured; it
never infers mathematical metadata from a Lean parser.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Iterable

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso


class RestructureError(ValueError):
    """Raised when a restructure contract is inconsistent."""


def safe_relpath(value: str, *, label: str = "path") -> str:
    """Validate and normalize one repository/workspace relative POSIX path."""

    text = str(value).replace("\\", "/").strip()
    if not text or text.startswith("/") or "\x00" in text:
        raise ValueError(f"{label} must be a non-empty relative path")
    path = PurePosixPath(text)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must not contain '.', '..', or empty segments")
    return path.as_posix()


def safe_module_segment(value: str, *, label: str = "module segment") -> str:
    text = str(value).strip()
    if not text or not text[0].isalpha() or any(not (c.isalnum() or c == "_") for c in text):
        raise ValueError(f"{label} must be a Lean module segment")
    return text


class RestructureStage(StrEnum):
    PLAN = "plan"
    DECLARED = "declared"
    PROVED = "proved"
    FINAL = "final"


class CompletionTarget(StrEnum):
    DECLARED = "declared"
    PROVED = "proved"


class NodeKind(StrEnum):
    MAIN = "main"
    SCOPE = "scope"
    CONTENT = "content"


class DeclKind(StrEnum):
    DEF = "def"
    ABBREV = "abbrev"
    STRUCTURE = "structure"
    CLASS = "class"
    INDUCTIVE = "inductive"
    INSTANCE = "instance"
    NOTATION = "notation"
    MACRO = "macro"
    LEMMA = "lemma"
    THEOREM = "theorem"
    COROLLARY = "corollary"
    SUPPORT = "support"


class DeclStatus(StrEnum):
    DRAFT = "draft"
    DECLARED = "declared"
    PROVED = "proved"


class CoverageDisposition(StrEnum):
    REGISTERED = "registered"
    SUPPORT = "support"
    DEFERRED = "deferred"
    EXCLUDED = "excluded"
    MOVED = "moved"


class ReviewOutcome(StrEnum):
    PASSED = "passed"
    NEEDS_CONTENT_FIX = "needs_content_fix"
    NEEDS_REPLAN = "needs_replan"
    BLOCKED = "blocked"


class StrictPathModel(StrictModel):
    @field_validator("file", "path", "directory", mode="before", check_fields=False)
    @classmethod
    def _path(cls, value: str) -> str:
        return safe_relpath(value)


class SourceRef(StrictModel):
    """An explicitly supplied source-corpus reference."""

    corpus: str
    path: str
    locator: str | None = None
    note: str | None = None

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return safe_relpath(value, label="source path")


class Origin(StrictModel):
    source_refs: list[SourceRef] = Field(default_factory=list)
    note: str | None = None


class DeclRef(StrictModel):
    repo: str
    name: str
    node: str | None = None

    @field_validator("repo", "name")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("declaration references require repo and name")
        return value


class DeclDependency(StrictModel):
    ref: DeclRef
    external: bool = False
    reason: str | None = None


class SectionNL(StrictModel):
    text: str = ""
    origins: list[Origin] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        return value.strip()


class FormalSnapshot(StrictModel):
    code: str


class SectionInput(StrictModel):
    """Agent-authored mathematical metadata; formal text is system-owned."""
    nl: SectionNL = Field(default_factory=SectionNL)
    deps: list[DeclDependency] = Field(default_factory=list)


class DeclarationSection(SectionInput):
    formal: FormalSnapshot | None = None


class EditRegion(StrictModel):
    """A protected template region, rather than an inferred source region."""

    file: str
    statement_digest: str | None = None
    proof_digest: str | None = None
    context_digest: str | None = None
    statement_writable: bool = True
    proof_writable: bool = False

    @field_validator("file")
    @classmethod
    def _file(cls, value: str) -> str:
        return safe_relpath(value, label="template file")


class DeclRecord(StrictModel):
    name: str
    lean_name: str
    kind: DeclKind
    summary: str
    file: str
    file_group: str | None = None
    status: DeclStatus = DeclStatus.DRAFT
    statement: DeclarationSection = Field(default_factory=DeclarationSection)
    proof: DeclarationSection | None = None
    statement_review_digest: str | None = None
    proof_review_digest: str | None = None
    edit_region: EditRegion | None = None
    file_digest: str | None = None
    captured_at: str | None = None

    @field_validator("name", "lean_name", "summary")
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("declaration identity and summary must be non-empty")
        return value

    @field_validator("file")
    @classmethod
    def _file(cls, value: str) -> str:
        return safe_relpath(value, label="declaration file")

    @model_validator(mode="after")
    def _stage_consistency(self) -> "DeclRecord":
        if self.status in {DeclStatus.DECLARED, DeclStatus.PROVED} and not self.statement_nl:
            raise ValueError("declared/proved declaration requires statement_nl")
        if self.status is DeclStatus.PROVED and self.kind not in {
            DeclKind.DEF,
            DeclKind.ABBREV,
            DeclKind.STRUCTURE,
            DeclKind.CLASS,
            DeclKind.INDUCTIVE,
            DeclKind.INSTANCE,
            DeclKind.NOTATION,
            DeclKind.MACRO,
            DeclKind.SUPPORT,
        } and not self.proof_nl:
            raise ValueError("proved theorem-like declaration requires proof_nl")
        return self

    @property
    def statement_nl(self) -> str:
        return self.statement.nl.text

    @property
    def proof_nl(self) -> str | None:
        return self.proof.nl.text if self.proof else None

    @property
    def dependencies(self) -> list[DeclDependency]:
        return self.statement.deps + (self.proof.deps if self.proof else [])

    @property
    def is_theorem_like(self) -> bool:
        return self.kind in {DeclKind.LEMMA, DeclKind.THEOREM, DeclKind.COROLLARY}

    @property
    def requires_real_declared_body(self) -> bool:
        """Whether the declared gate must reject a placeholder body."""

        return not self.is_theorem_like


class NodeRecord(StrictModel):
    path: str
    kind: NodeKind
    parent: str | None = None
    summary: str
    goal: str
    boundary: str
    constraints: str | None = None
    module: str
    exports: list[DeclRef] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    material_refs: list[SourceRef] = Field(default_factory=list)
    file_group: str | None = None

    @field_validator("boundary")
    @classmethod
    def _boundary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("node boundary must describe its mathematical responsibility")
        return value.strip()

    @field_validator("path", "parent", mode="before")
    @classmethod
    def _node_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip().strip(".")
        if not text or any(not part for part in text.split(".")):
            raise ValueError("node paths must be dotted non-empty segments")
        return ".".join(safe_module_segment(part, label="node segment") for part in text.split("."))

    @field_validator("module")
    @classmethod
    def _module(cls, value: str) -> str:
        return ".".join(safe_module_segment(part) for part in value.split("."))

    @model_validator(mode="after")
    def _kind_parent(self) -> "NodeRecord":
        if self.kind is NodeKind.MAIN and self.parent is not None:
            raise ValueError("Main node cannot have a parent")
        if self.kind is not NodeKind.MAIN and self.parent is None:
            raise ValueError("scope/content nodes require a parent")
        if self.kind is NodeKind.CONTENT and self.path == "Main":
            raise ValueError("Main cannot be a content leaf")
        return self


class RepoPlan(StrictModel):

    repo_key: str
    directory: str
    module_root: str
    goal: str
    summary: str = ""
    nodes: dict[str, NodeRecord] = Field(default_factory=dict)
    interface_seeds: list[DeclRef] = Field(default_factory=list)
    main_exports: list[DeclRef] = Field(default_factory=list)
    material_assignments: dict[str, list[SourceRef]] = Field(default_factory=dict)
    version: int = 1

    @field_validator("directory")
    @classmethod
    def _directory(cls, value: str) -> str:
        return safe_relpath(value, label="repo directory")

    @field_validator("module_root")
    @classmethod
    def _module_root(cls, value: str) -> str:
        return safe_module_segment(value, label="module root")

    @model_validator(mode="after")
    def _validate_tree(self) -> "RepoPlan":
        main = [node for node in self.nodes.values() if node.kind is NodeKind.MAIN]
        if len(main) != 1 or main[0].path != "Main":
            raise ValueError("repo plan requires exactly one Main node")
        keys = set(self.nodes)
        for key, node in self.nodes.items():
            if key != node.path:
                raise ValueError(f"node map key does not match node.path: {key}")
            if node.parent and node.parent not in keys:
                raise ValueError(f"node {key} refers to missing parent {node.parent}")
            if any(dep not in keys for dep in node.dependencies):
                raise ValueError(f"node {key} refers to a missing dependency")
        _assert_acyclic({key: node.dependencies for key, node in self.nodes.items()})
        for node_path, refs in self.material_assignments.items():
            if node_path not in keys:
                raise ValueError(f"material assignment refers to missing node {node_path}")
            if self.nodes[node_path].kind is NodeKind.MAIN:
                raise ValueError("material assignments belong to Scope/Content nodes")
        return self

    def content_nodes(self) -> list[NodeRecord]:
        return sorted((node for node in self.nodes.values() if node.kind is NodeKind.CONTENT), key=lambda node: node.path)

    def declared_frontier(self, completed: set[str]) -> list[str]:
        """Return Content leaves whose planned node dependencies are complete."""

        return [
            node.path
            for node in self.content_nodes()
            if node.path not in completed and all(dep in completed for dep in node.dependencies)
        ]


class RepoSpec(StrictModel):
    key: str
    directory: str
    module_root: str
    goal: str
    source_inputs: list[SourceRef] = Field(default_factory=list)
    plan: RepoPlan | None = None

    @field_validator("directory")
    @classmethod
    def _directory(cls, value: str) -> str:
        return safe_relpath(value, label="repo directory")

    @field_validator("module_root")
    @classmethod
    def _module_root(cls, value: str) -> str:
        return safe_module_segment(value, label="module root")


class WorkspacePlan(StrictModel):

    run_id: str
    workspace_root: str
    main_repo: str
    repos: dict[str, RepoSpec]
    repo_dependencies: dict[str, list[str]] = Field(default_factory=dict)
    completion_target: CompletionTarget = CompletionTarget.PROVED
    max_agents: int = Field(default=8, ge=1)
    max_builds: int = Field(default=1, ge=1)
    version: int = 1

    @field_validator("workspace_root")
    @classmethod
    def _workspace(cls, value: str) -> str:
        return str(value).strip()

    @model_validator(mode="after")
    def _validate_workspace(self) -> "WorkspacePlan":
        if self.main_repo not in self.repos:
            raise ValueError("main_repo must name one repo in repos")
        if len({repo.directory for repo in self.repos.values()}) != len(self.repos):
            raise ValueError("repo directories must be unique")
        for key, deps in self.repo_dependencies.items():
            if key not in self.repos or any(dep not in self.repos for dep in deps):
                raise ValueError("repo dependency graph contains an unknown repo")
        _assert_acyclic({key: self.repo_dependencies.get(key, []) for key in self.repos})
        return self

    def repo_directory(self, repo_ref: str) -> str:
        """Resolve either a logical repo key or its direct-child directory."""

        if repo_ref in self.repos:
            return self.repos[repo_ref].directory
        matches = [repo.directory for repo in self.repos.values() if repo.directory == repo_ref]
        if len(matches) == 1:
            return matches[0]
        raise KeyError(f"unknown restructure repo: {repo_ref}")

    def repo_key_for_directory(self, directory: str) -> str:
        for key, repo in self.repos.items():
            if repo.directory == directory:
                return key
        raise KeyError(f"unknown restructure repo directory: {directory}")


class CaptureReceipt(StrictModel):
    file: str
    digest: str
    bytes: int = Field(ge=0)
    captured_at: str = Field(default_factory=utc_now_iso)

    @field_validator("file")
    @classmethod
    def _file(cls, value: str) -> str:
        return safe_relpath(value, label="capture file")


class ArtifactManifest(StrictModel):
    artifact_id: str
    stage: RestructureStage
    repo_key: str
    node_path: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    content: dict[str, object] = Field(default_factory=dict)
    metadata_digest: str
    provider_refs: dict[str, str] = Field(default_factory=dict)
    module_inventory: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    source_epoch: int = 0


class TaskBinding(StrictModel):
    request_id: str
    run_id: str
    repo_key: str
    node_path: str | None = None
    stage: RestructureStage
    attempt_epoch: int = 0
    flow_id: str | None = None
    step_id: str | None = None
    reservation_id: str | None = None
    flow_params: dict[str, object] = Field(default_factory=dict)
    flow_type: str | None = None
    input_artifact_ids: list[str] = Field(default_factory=list)
    stale: bool = False
    terminal_consumed: bool = False
    terminal_outcome: str | None = None
    result_artifact_id: str | None = None


class WorkspaceRun(StrictModel):
    run_id: str
    plan_version: int
    completion_target: CompletionTarget
    status: str = "prepared"
    bindings: dict[str, TaskBinding] = Field(default_factory=dict)
    accepted_artifacts: list[str] = Field(default_factory=list)
    blocked_issues: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)


class CoverageEntry(StrictModel):
    source: SourceRef
    disposition: CoverageDisposition
    decl_refs: list[DeclRef] = Field(default_factory=list)
    reason: str | None = None


class ContentWork(StrictModel):
    """Explicit Agent-owned declaration catalogue for one Content leaf."""


    repo_key: str
    node_path: str
    plan_version: int = 1
    attempt_epoch: int = 0
    stage: RestructureStage = RestructureStage.PLAN
    declared_baseline: dict[str, str] = Field(default_factory=dict)
    decls: dict[str, DeclRecord] = Field(default_factory=dict)
    support_files: list[str] = Field(default_factory=list)
    coverage: list[CoverageEntry] = Field(default_factory=list)
    edit_regions: dict[str, EditRegion] = Field(default_factory=dict)
    accepted_artifact_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("support_files")
    @classmethod
    def _support_files(cls, values: list[str]) -> list[str]:
        return [safe_relpath(value, label="support file") for value in values]

    @model_validator(mode="after")
    def _leaf(self) -> "ContentWork":
        if not self.node_path or self.node_path == "Main":
            raise ValueError("ContentWork requires a non-Main node path")
        return self


class ReviewReceipt(StrictModel):
    artifact_id: str
    outcome: ReviewOutcome
    findings: list[str] = Field(default_factory=list)
    reviewer: str
    created_at: str = Field(default_factory=utc_now_iso)


class BuildReceipt(StrictModel):
    artifact_id: str
    success: bool
    operation_id: str
    stage: RestructureStage = RestructureStage.PROVED
    repo_key: str | None = None
    diagnostics: list[str] = Field(default_factory=list)
    provider_refs: dict[str, str] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)
    module_inventory: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class Reservation(StrictModel):
    """Durable workspace budget reservation for one ARK operation."""

    reservation_id: str
    run_id: str
    kind: str
    owner_ref: str
    created_at: str = Field(default_factory=utc_now_iso)
    released_at: str | None = None


class FrontierItem(StrictModel):
    repo_key: str
    directory: str
    node_path: str
    stage: RestructureStage
    plan_version: int
    attempt_epoch: int = 0
    dependencies: list[str] = Field(default_factory=list)


class ReplanImpact(StrictModel):
    repo_key: str
    node_paths: list[str] = Field(default_factory=list)
    reason: str
    new_epoch: int


def file_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _assert_acyclic(graph: dict[str, Iterable[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError(f"dependency graph contains a cycle at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, ()):
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


__all__ = [name for name in globals() if not name.startswith("_")]
