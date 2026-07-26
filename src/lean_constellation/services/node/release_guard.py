"""Derived release guards for Node contract commits and deletion."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.repo import (
    ProofAvailability,
    proof_availability_for_completion_mode,
)
from lean_constellation.services.decl_graph.availability_policy import required_state_for_availability
from lean_constellation.services.decl_graph.models import (
    DeclLifecycle,
    DeclRevisionStatus,
    DeclRoundStatus,
    DeclState,
    DeclStrategyStatus,
    RepoDeclDep,
)
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.node.node_tree import DeleteImpactView, NodeContract, NodeKind, NodeLifecycle

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class NodeReleaseGuard:
    """Compute Node protections from current truth and immutable releases."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def capture_content_contract_head(self, repo_root: Path, *, node_path: str) -> ServiceResult[dict[str, int]]:
        rounds = self.runtime.decl_graph.list_rounds(repo_root, node_path=node_path)
        if not rounds.ok or rounds.value is None:
            return self.runtime.foundation.fail(rounds.issues)
        unfinished = [item.round_id for item in rounds.value if item.status in {DeclRoundStatus.DRAFT, DeclRoundStatus.RUNNING}]
        if unfinished:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "content_head_round_unfinished",
                    "Content contract cannot commit while a declaration round is draft or running.",
                    object_ref=node_path,
                    current=", ".join(sorted(unfinished)),
                )
            )
        decls = self.runtime.decl_graph.list_decls(repo_root, node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        proof_availability = ProofAvailability.PROVED
        config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
        if config.ok and config.value is not None:
            proof_availability = proof_availability_for_completion_mode(
                config.value.config.completion_mode
            )
        head: dict[str, int] = {}
        for decl in decls.value:
            if decl.lifecycle != DeclLifecycle.ACTIVE:
                continue
            revision = self.runtime.decl_graph.get_decl_revision(
                repo_root, node_path=node_path, name=decl.name, revision=decl.current_revision
            )
            if not revision.ok or revision.value is None:
                return self.runtime.foundation.fail(revision.issues)
            if revision.value.state == DeclState.OBSOLETE:
                continue
            if revision.value.status != DeclRevisionStatus.COMMITTED:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "content_head_decl_revision_open",
                        "Every active declaration must have a committed current revision before Content commit.",
                        object_ref=f"{node_path}:{decl.name}@{decl.current_revision}",
                    )
                )
            if revision.value.state not in {DeclState.DECLARED, DeclState.PROOF_PLANNED, DeclState.PROVED}:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "content_head_decl_not_declared",
                        "Every active declaration must be at least declared before Content commit.",
                        object_ref=f"{node_path}:{decl.name}",
                        current=revision.value.state.value,
                    )
                )
            if revision.value.statement.formal is None or not (revision.value.statement.formal.code or "").strip():
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("content_head_statement_missing", "Declared Content head entry has no formal statement.", object_ref=f"{node_path}:{decl.name}")
                )
            sync_stage = "proof" if revision.value.state == DeclState.PROVED else "statement"
            sync = self.runtime.lean_projection.check_decl_file_snapshot_sync(
                repo_root, node_path=node_path, decl_name=decl.name, stage=sync_stage
            )
            if not sync.ok or sync.value is None:
                return self.runtime.foundation.fail(sync.issues)
            if not sync.value.passed:
                return self.runtime.foundation.fail(sync.value.issues)
            guarded = self.runtime.decl_graph.release_guard.check_update_candidate(
                repo_root, node_path=node_path, decl=decl, candidate=revision.value
            )
            if not guarded.ok:
                return self.runtime.foundation.fail(guarded.issues)
            for section, deps, availability in (
                ("statement", revision.value.statement.deps, ProofAvailability.DECLARED),
                ("proof", revision.value.proof.deps if revision.value.proof else [], proof_availability),
            ):
                for dep in deps:
                    if not isinstance(dep, RepoDeclDep):
                        continue
                    valid = self._validate_current_dep(
                        repo_root,
                        owner_node=node_path,
                        owner_decl=decl.name,
                        section=section,
                        dep=dep,
                        required_availability=availability,
                    )
                    if not valid.ok:
                        return self.runtime.foundation.fail(valid.issues)
            head[decl.name] = decl.current_revision
        return self.runtime.foundation.ok(dict(sorted(head.items())))

    def _validate_current_dep(
        self,
        repo_root: Path,
        *,
        owner_node: str,
        owner_decl: str,
        section: str,
        dep: RepoDeclDep,
        required_availability: ProofAvailability,
    ) -> ServiceResult[None]:
        object_ref = f"{owner_node}:{owner_decl}:{section}->{dep.ref.repo or ''}:{dep.ref.node}:{dep.ref.name}@{dep.ref.revision}"
        issue_kind = f"content_head_{section}_dependency_invalid"
        if dep.ref.repo is not None:
            resolved = self.runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
                repo_root, ref=dep.ref, required_availability=required_availability
            )
            if not resolved.ok or resolved.value is None:
                return self.runtime.foundation.fail(resolved.issues)
            if not resolved.value.compatible:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        issue_kind,
                        f"External {section} dependency is unavailable or incompatible.",
                        object_ref=object_ref,
                        current=resolved.value.reason,
                    )
                )
            return self.runtime.foundation.ok(None)
        target_node = dep.ref.node if dep.ref.node != "Main" else owner_node
        target = self.runtime.decl_graph.get_decl(repo_root, node_path=target_node, name=dep.ref.name)
        if not target.ok or target.value is None or target.value.lifecycle != DeclLifecycle.ACTIVE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(issue_kind, f"Current {section} dependency is missing or deleted.", object_ref=object_ref)
            )
        current = self.runtime.decl_graph.get_decl_revision(
            repo_root, node_path=target_node, name=dep.ref.name, revision=target.value.current_revision
        )
        floor = required_state_for_availability(target.value.kind, required_availability)
        state_rank = {
            DeclState.OBSOLETE: -1,
            DeclState.PLANNED: 0,
            DeclState.SPECIFIED: 1,
            DeclState.DECLARED: 2,
            DeclState.PROOF_PLANNED: 3,
            DeclState.PROVED: 4,
        }
        if (
            not current.ok
            or current.value is None
            or current.value.status != DeclRevisionStatus.COMMITTED
            or state_rank[current.value.state] < state_rank[floor]
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(issue_kind, f"Current {section} dependency does not satisfy availability.", object_ref=object_ref)
            )
        if dep.ref.revision != target.value.current_revision:
            anchor = self.runtime.decl_graph.declared_api.fingerprint(
                repo_root, node_path=target_node, decl_name=dep.ref.name, revision=dep.ref.revision
            )
            current_fp = self.runtime.decl_graph.declared_api.fingerprint(
                repo_root, node_path=target_node, decl_name=dep.ref.name, revision=target.value.current_revision
            )
            if (
                not anchor.ok
                or anchor.value is None
                or not current_fp.ok
                or current_fp.value is None
                or anchor.value.sha256 != current_fp.value.sha256
            ):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        f"content_head_{section}_dependency_incompatible",
                        f"{section.title()} dependency anchor is stale or incompatible.",
                        object_ref=object_ref,
                    )
                )
        return self.runtime.foundation.ok(None)

    def check_scope_contract_candidate(self, repo_root: Path, *, scope_path: str, candidate: NodeContract) -> ServiceResult[None]:
        if candidate.decl_graph_head:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("scope_decl_graph_head_not_empty", "Scope contracts must have an empty DeclGraph head.", object_ref=scope_path)
            )
        latest = self.runtime.repo_workspace.release.get_latest_release(repo_root)
        if not latest.ok:
            return self.runtime.foundation.fail(latest.issues)
        if latest.value is None:
            return self.runtime.foundation.ok(None)
        lineage = self.runtime.repo_workspace.release.resolve_release_lineage(
            repo_root, release_id=latest.value.release.release_id
        )
        if not lineage.ok or lineage.value is None:
            return self.runtime.foundation.fail(lineage.issues)
        nodes = self.runtime.node.node_tree.node_store.list_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        node = next((item for item in nodes.value if item.lifecycle == NodeLifecycle.ACTIVE and item.path == scope_path), None)
        if node is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_not_found", "Scope node is missing.", object_ref=scope_path))
        candidate_exports: dict[tuple[str | None, str, str], list[object]] = {}
        for ref in candidate.exports:
            candidate_exports.setdefault(self._ref_identity(ref), []).append(ref)
        candidate_interfaces = {item.name: item.bound_decl for item in candidate.interfaces}
        for release in lineage.value:
            version = release.node_contract_versions.get(node.node_id)
            if version is None:
                continue
            path = self.runtime.node.node_tree.node_store.contract_path(repo_root, node_id=node.node_id, version=version)
            historical = self.runtime.foundation.store.read_json(path, NodeContract)
            if not historical.ok or historical.value is None:
                return self.runtime.foundation.fail(historical.issues)
            for ref in historical.value.exports:
                replacements = candidate_exports.get(self._ref_identity(ref), [])
                compatible = any(
                    self._refs_semantically_compatible(repo_root, historical=ref, candidate=replacement)
                    for replacement in replacements
                )
                if not compatible:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "released_scope_export_removed",
                            "A released Scope export cannot be removed or rebound.",
                            object_ref=scope_path,
                            current=str(self._ref_identity(ref)),
                        )
                    )
            for interface in historical.value.interfaces:
                if interface.name not in candidate_interfaces:
                    compatible = False
                elif interface.bound_decl is None:
                    replacement = candidate_interfaces[interface.name]
                    compatible = replacement is None
                else:
                    replacement = candidate_interfaces[interface.name]
                    compatible = replacement is not None and self._refs_semantically_compatible(
                        repo_root, historical=interface.bound_decl, candidate=replacement
                    )
                if not compatible:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "released_scope_interface_changed",
                            "A released Scope interface binding cannot be removed or rebound.",
                            object_ref=f"{scope_path}:{interface.name}",
                        )
                    )
        return self.runtime.foundation.ok(None)

    def preview_delete_node(self, repo_root: Path, *, path: str) -> ServiceResult[DeleteImpactView]:
        node = self.runtime.node.node_tree.node_store.resolve_active_node(repo_root, path=path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        nodes = self.runtime.node.node_tree.node_store.list_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        children = sorted(
            item.path for item in nodes.value if item.lifecycle == NodeLifecycle.ACTIVE and item.path.startswith(f"{path}.")
        )
        inbound: list[str] = []
        open_decl_truth: list[str] = []
        for owner in nodes.value:
            if owner.lifecycle != NodeLifecycle.ACTIVE:
                continue
            if owner.current_contract_version is not None and owner.path != path:
                contract_path = self.runtime.node.node_tree.node_store.contract_path(
                    repo_root, node_id=owner.node_id, version=owner.current_contract_version
                )
                contract = self.runtime.foundation.store.read_json(contract_path, NodeContract)
                if not contract.ok or contract.value is None:
                    return self.runtime.foundation.fail(contract.issues)
                if any(dep.target.repo is None and dep.target.node and (dep.target.node == path or dep.target.node.startswith(f"{path}.")) for dep in contract.value.deps):
                    inbound.append(f"current:{owner.path}:deps")
                refs = [*contract.value.exports, *[item.bound_decl for item in contract.value.interfaces if item.bound_decl]]
                if any(ref.repo is None and (ref.node == path or ref.node.startswith(f"{path}.")) for ref in refs):
                    inbound.append(f"current:{owner.path}:public")
            if owner.kind != NodeKind.CONTENT:
                continue
            decls = self.runtime.decl_graph.list_decls(repo_root, node_path=owner.path)
            if not decls.ok or decls.value is None:
                return self.runtime.foundation.fail(decls.issues)
            for decl in decls.value:
                if decl.lifecycle != DeclLifecycle.ACTIVE:
                    continue
                revision = self.runtime.decl_graph.get_decl_revision(
                    repo_root, node_path=owner.path, name=decl.name, revision=decl.current_revision
                )
                if not revision.ok or revision.value is None:
                    return self.runtime.foundation.fail(revision.issues)
                if owner.path == path and revision.value.status == DeclRevisionStatus.OPEN:
                    open_decl_truth.append(f"revision:{decl.name}@{decl.current_revision}")
                for section, deps in (("statement", revision.value.statement.deps), ("proof", revision.value.proof.deps if revision.value.proof else [])):
                    for dep in deps:
                        if not isinstance(dep, RepoDeclDep) or dep.ref.repo is not None:
                            continue
                        target_node = dep.ref.node if dep.ref.node != "Main" else owner.path
                        if owner.path != path and (target_node == path or target_node.startswith(f"{path}.")):
                            inbound.append(f"current:decl:{owner.path}:{decl.name}:{section}")
        if node.value.kind == NodeKind.CONTENT:
            strategies = self.runtime.decl_graph.list_strategies(repo_root, node_path=path)
            if not strategies.ok or strategies.value is None:
                return self.runtime.foundation.fail(strategies.issues)
            open_decl_truth.extend(
                f"strategy:{item.strategy_id}" for item in strategies.value if item.status == DeclStrategyStatus.OPEN
            )
            rounds = self.runtime.decl_graph.list_rounds(repo_root, node_path=path)
            if not rounds.ok or rounds.value is None:
                return self.runtime.foundation.fail(rounds.issues)
            open_decl_truth.extend(
                f"round:{item.round_id}" for item in rounds.value if item.status in {DeclRoundStatus.DRAFT, DeclRoundStatus.RUNNING}
            )
        latest = self.runtime.repo_workspace.release.get_latest_release(repo_root)
        if not latest.ok:
            return self.runtime.foundation.fail(latest.issues)
        release_blocked = False
        public_count = 0
        if latest.value is not None:
            baseline = self.runtime.repo_workspace.release.resolve_release_baseline(
                repo_root, release_id=latest.value.release.release_id
            )
            if not baseline.ok or baseline.value is None:
                return self.runtime.foundation.fail(baseline.issues)
            release_blocked = node.value.node_id in baseline.value.protected_node_ids
            public_count = sum(
                1
                for protected in baseline.value.protected_decl_views
                if protected.node_path == path or protected.node_path.startswith(f"{path}.")
            )
            if release_blocked:
                inbound.append(f"released:{baseline.value.release_id}:{path}")
            historical = self._historical_node_refs(
                repo_root,
                path=path,
                node_id=node.value.node_id,
                lineage_release_ids=baseline.value.lineage_release_ids,
            )
            if not historical.ok or historical.value is None:
                return self.runtime.foundation.fail(historical.issues)
            inbound.extend(historical.value)
        blockers: list[str] = []
        if path == "Main":
            blockers.append("root_main")
        if children:
            blockers.append("active_children")
        if any(item.startswith("current:") for item in inbound):
            blockers.append("current_inbound_refs")
        if release_blocked:
            blockers.append("release_protected")
        if node.value.open_contract_version is not None:
            blockers.append("open_contract")
        if open_decl_truth:
            blockers.append("open_decl_graph_work")
            inbound.extend(f"current:{item}" for item in open_decl_truth)
        return self.runtime.foundation.ok(
            DeleteImpactView.build(
                path=path,
                deletable=not blockers,
                affected_children=children,
                inbound_refs=sorted(set(inbound)),
                blocking_reasons=blockers,
                public_decl_count=public_count,
                summary=("Node can be soft-deleted." if not blockers else f"Node delete is blocked by: {', '.join(blockers)}."),
            )
        )

    def _historical_node_refs(
        self,
        repo_root: Path,
        *,
        path: str,
        node_id: str,
        lineage_release_ids: list[str],
    ) -> ServiceResult[list[str]]:
        findings: list[str] = []
        for release_id in lineage_release_ids:
            release = self.runtime.repo_workspace.release.get_release(repo_root, release_id=release_id)
            if not release.ok or release.value is None:
                return self.runtime.foundation.fail(release.issues)
            if node_id in release.value.release.node_contract_versions:
                findings.append(f"historical:{release_id}:node")
            for owner_id, version in release.value.release.node_contract_versions.items():
                contract = self.runtime.repo_workspace.release._load_contract(
                    repo_root, node_id=owner_id, version=version
                )
                if not contract.ok or contract.value is None:
                    return self.runtime.foundation.fail(contract.issues)
                refs = [
                    *contract.value.exports,
                    *[item.bound_decl for item in contract.value.interfaces if item.bound_decl],
                ]
                if any(ref.repo is None and (ref.node == path or ref.node.startswith(f"{path}.")) for ref in refs):
                    findings.append(f"historical:{release_id}:{owner_id}:public")
                if any(
                    dep.target.repo is None
                    and dep.target.node
                    and (dep.target.node == path or dep.target.node.startswith(f"{path}."))
                    for dep in contract.value.deps
                ):
                    findings.append(f"historical:{release_id}:{owner_id}:deps")
        return self.runtime.foundation.ok(sorted(set(findings)))

    @staticmethod
    def _ref_identity(ref):
        return (ref.repo, ref.node, ref.name)

    def _refs_semantically_compatible(self, repo_root: Path, *, historical, candidate) -> bool:
        if self._ref_identity(historical) != self._ref_identity(candidate):
            return False
        resolver = self.runtime.decl_graph.ref_compatibility
        if historical.repo is not None:
            old = resolver.resolve_public_decl_ref(
                repo_root, ref=historical, required_availability=ProofAvailability.DECLARED
            )
            new = resolver.resolve_public_decl_ref(
                repo_root, ref=candidate, required_availability=ProofAvailability.DECLARED
            )
        else:
            old = resolver.resolve_decl_ref(
                repo_root, ref=historical, required_availability=ProofAvailability.DECLARED
            )
            new = resolver.resolve_decl_ref(
                repo_root, ref=candidate, required_availability=ProofAvailability.DECLARED
            )
        return bool(
            old.ok
            and old.value is not None
            and old.value.compatible
            and new.ok
            and new.value is not None
            and new.value.compatible
            and old.value.resolved_revision == new.value.resolved_revision
        )

__all__ = ["NodeReleaseGuard"]
