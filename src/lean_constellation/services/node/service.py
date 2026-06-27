"""NodeService composition and public wrappers."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import FoundationService, GateReport, ServiceResult
from lean_constellation.services.material import MaterialService
from lean_constellation.services.node.contract import ContractComponent, NodeContractView
from lean_constellation.services.node.dependency import DependencyComponent
from lean_constellation.services.node.export import ContentPublicDeclProvider, DeclPublicView, DeclRefView, ExportComponent
from lean_constellation.services.node.interface import InterfaceComponent, InterfaceListView
from lean_constellation.services.node.material_ref import MaterialRefComponent
from lean_constellation.services.node.node_tree import NodeKind, NodeTreeComponent, NodeView
from lean_constellation.services.repo_workspace import RepoWorkspaceService


class NodeBoundaryView(StrictModel):
    """Current public boundary view for a Scope or Content node."""

    node_path: str
    node_kind: NodeKind
    interfaces: InterfaceListView
    public_decls: list[DeclPublicView] = Field(default_factory=list)
    exports: list[DeclRefView] = Field(default_factory=list)
    summary: str


class NodeService:
    """Composition root for node tree, contracts, interfaces, refs, deps, and exports."""

    _NATIVE_MAIN_BOUNDARY = (
        "Main covers the full native repository formalization scope, including the prepared source corpus, "
        "formalized content, internal node tree, and repository public interface."
    )
    _NATIVE_MAIN_OBJECTIVE = (
        "Maintain the root scope for the repository Coordinator: organize child scopes/content nodes, "
        "track the public boundary, and eventually expose all required repository interfaces."
    )
    _ADAPTER_MAIN_BOUNDARY = (
        "Main covers the adapter repository facade for the upstream Lean dependency and exposes selected upstream declarations "
        "through the repository public interface."
    )
    _ADAPTER_MAIN_OBJECTIVE = (
        "Register and expose upstream Lean declarations needed by the preparation input without creating a native scope/content subtree."
    )

    def __init__(
        self,
        *,
        foundation: FoundationService | None = None,
        repo_workspace: RepoWorkspaceService | None = None,
        material: MaterialService | None = None,
        node_tree: NodeTreeComponent | None = None,
        contract: ContractComponent | None = None,
        interface: InterfaceComponent | None = None,
        dependency: DependencyComponent | None = None,
        material_ref: MaterialRefComponent | None = None,
        export: ExportComponent | None = None,
        public_decl_provider: ContentPublicDeclProvider | None = None,
        node_projection: object | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.repo_workspace = repo_workspace
        self.material = material or MaterialService(foundation=self.foundation)
        self.node_tree = node_tree or NodeTreeComponent(self.foundation)
        self.contract = contract or ContractComponent(self.foundation, self.node_tree)
        self.export = export or ExportComponent(
            self.foundation,
            node_tree=self.node_tree,
            contract=self.contract,
            public_decl_provider=public_decl_provider,
            node_projection=node_projection,  # type: ignore[arg-type]
        )
        self.interface = interface or InterfaceComponent(
            self.foundation,
            contract=self.contract,
            export=self.export,
            node_projection=node_projection,  # type: ignore[arg-type]
        )
        self.dependency = dependency or DependencyComponent(
            self.foundation,
            node_tree=self.node_tree,
            contract=self.contract,
            node_projection=node_projection,  # type: ignore[arg-type]
            repo_workspace=self.repo_workspace,
        )
        self.material_ref = material_ref or MaterialRefComponent(
            self.foundation,
            contract=self.contract,
            material=self.material,
        )

    def create_scope_node(
        self,
        repo_root: Path,
        *,
        path: str,
        goal: str,
        boundary: str,
        objective: str | None = None,
        constraints: str | None = None,
        success_criteria: str | None = None,
    ) -> ServiceResult[NodeView]:
        return self.node_tree.create_scope_node(
            repo_root,
            path=path,
            goal=goal,
            boundary=boundary,
            objective=objective,
            constraints=constraints,
            success_criteria=success_criteria,
        )

    def create_content_node(
        self,
        repo_root: Path,
        *,
        path: str,
        goal: str,
        boundary: str,
        objective: str,
        success_criteria: str,
        constraints: str | None = None,
    ) -> ServiceResult[NodeView]:
        return self.node_tree.create_content_node(
            repo_root,
            path=path,
            goal=goal,
            boundary=boundary,
            objective=objective,
            success_criteria=success_criteria,
            constraints=constraints,
        )

    def ensure_native_root_main_contract(
        self,
        repo_root: Path,
        *,
        boundary: str | None = None,
        objective: str | None = None,
    ) -> ServiceResult[NodeContractView]:
        return self._ensure_root_main_contract(
            repo_root,
            boundary=boundary or self._NATIVE_MAIN_BOUNDARY,
            objective=objective or self._NATIVE_MAIN_OBJECTIVE,
        )

    def ensure_adapter_root_main_contract(self, repo_root: Path) -> ServiceResult[NodeContractView]:
        return self._ensure_root_main_contract(
            repo_root,
            boundary=self._ADAPTER_MAIN_BOUNDARY,
            objective=self._ADAPTER_MAIN_OBJECTIVE,
        )

    def check_root_main_handoff_interfaces(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.interface.check_root_interfaces_include_preparation_inputs(repo_root, node_path="Main")

    def prepare_content_task_admission(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        return self.contract.check_content_task_admission(repo_root, node_path=node_path)

    def submit_content_node_batch_preflight(self, repo_root: Path, *, node_paths: list[str]) -> ServiceResult[GateReport]:
        return self.dependency.check_content_batch_independent(repo_root, node_paths=node_paths)

    def commit_scope_contract(self, repo_root: Path, *, scope_path: str, summary: str) -> ServiceResult[NodeContractView]:
        return self.contract.commit_scope_contract(repo_root, scope_path=scope_path, summary=summary)

    def commit_content_contract(self, repo_root: Path, *, node_path: str, summary: str) -> ServiceResult[NodeContractView]:
        return self.contract.commit_content_contract(repo_root, node_path=node_path, summary=summary)

    def get_node_public_boundary(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeBoundaryView]:
        node = self.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.foundation.fail(node.issues)
        interfaces = self.interface.list_interfaces(repo_root, node_path=node_path)
        if not interfaces.ok or interfaces.value is None:
            return self.foundation.fail(interfaces.issues)
        if node.value.kind == NodeKind.CONTENT:
            public = self.export.list_content_public_decls(repo_root, node_path=node_path)
            if not public.ok or public.value is None:
                return self.foundation.fail(public.issues)
            return self.foundation.ok(
                NodeBoundaryView(
                    node_path=node_path,
                    node_kind=node.value.kind,
                    interfaces=interfaces.value,
                    public_decls=public.value,
                    summary=f"Content node {node_path} exposes {len(public.value)} public declarations.",
                ),
                warnings=public.issues,
            )
        exports = self.export.list_scope_exports(repo_root, scope_path=node_path)
        if not exports.ok or exports.value is None:
            return self.foundation.fail(exports.issues)
        return self.foundation.ok(
            NodeBoundaryView(
                node_path=node_path,
                node_kind=node.value.kind,
                interfaces=interfaces.value,
                exports=exports.value,
                summary=f"Scope node {node_path} exposes {len(exports.value)} exports.",
            )
        )

    def _ensure_root_main_contract(
        self,
        repo_root: Path,
        *,
        boundary: str,
        objective: str,
    ) -> ServiceResult[NodeContractView]:
        root = self.node_tree.ensure_root_scope_node(repo_root, path="Main")
        if not root.ok:
            return self.foundation.fail(root.issues)
        initialized = self.contract.initialize_main_contract_from_preparation_input(
            repo_root,
            boundary=boundary,
            objective=objective,
        )
        if not initialized.ok or initialized.value is None:
            return self.foundation.fail(initialized.issues)
        synced = self.interface.sync_protected_root_interfaces_from_preparation_input(repo_root, node_path="Main")
        if not synced.ok or synced.value is None:
            return self.foundation.fail(synced.issues)
        return synced

