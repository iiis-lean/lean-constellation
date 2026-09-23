"""Accept repo repairs only after a complete build, retaining historical artifacts."""

from lean_constellation.domain.restructure import RestructureStage, file_digest
from .source_contract import contract_digest


def prepare_declared_repair(service, repo_key, request_id):
    with service.store._lock:
        workspace, _ = service.store.load_workspace_plan()
        run, version = service.store.load_run()
        current = next((b for b in run.bindings.values() if b.request_id == request_id
                        and not b.stale and not b.terminal_consumed), None) if run else None
        if current is None or current.repo_key != repo_key or current.stage is not RestructureStage.DECLARED or current.flow_type != "restructure_build":
            raise ValueError("declared repair requires an active declared build binding")
        affected = {repo_key}
        while True:
            expanded = affected | {key for key, deps in workspace.repo_dependencies.items() if affected.intersection(deps)}
            if expanded == affected:
                break
            affected = expanded
        impacted = [(key, b) for key, b in run.bindings.items()
                    if b.repo_key in affected and not b.stale and b is not current]
        if any(b.stage in {RestructureStage.PROVED, RestructureStage.FINAL} for _, b in impacted):
            raise ValueError("declared repair after proving requires explicit reopen_declared")
        if any(not b.terminal_consumed for _, b in impacted):
            raise ValueError("declared repair requires dependent tasks to be terminal and reconciled")
        for key in affected:
            repo = workspace.repos[key]
            if repo.plan:
                for node in repo.plan.content_nodes():
                    try:
                        work, _ = service.content.load(repo.directory, node.path)
                    except KeyError:
                        continue
                    if work.stage in {RestructureStage.PROVED, RestructureStage.FINAL}:
                        raise ValueError("declared repair cannot replace a proved interface")
        # Content stays declared; its consumers must pass a fresh full build
        # before the workspace proof barrier can open again.
        for key, binding in impacted:
            if binding.flow_type == "restructure_build":
                binding.stale = True
                run.blocked_issues = [i for i in run.blocked_issues if not i.startswith(key + ": ")]
        service.store.save_run(run, expected_version=version)


def accept_build_content(service, directory, result, *, declared_repair):
    with service.store._lock:
        root = service.store.repo_root(directory)
        current = {p.relative_to(root).as_posix(): file_digest(p.read_bytes())
                   for p in service.builds.input_files(root) if p.is_file()}
        if current != result.view.files:
            raise ValueError("build inputs changed before repair acceptance")
        stage = result.receipt.stage
        findings = service.builds._validate_catalogue(directory, stage, allow_declared_repair=declared_repair)
        if findings:
            raise ValueError("; ".join(findings))
        workspace, _ = service.store.load_workspace_plan()
        repo = workspace.repos[workspace.repo_key_for_directory(directory)]
        if repo.plan is None:
            return
        candidates = []
        for node in repo.plan.content_nodes():
            work, version = service.content.load(directory, node.path)
            if declared_repair:
                for name, decl in work.decls.items():
                    work.declared_baseline[name] = contract_digest(decl, service.content.read_decl_file(directory, node.path, name))
            candidates.append((node.path, work, version))
        for node, work, version in candidates:
            artifact = service.artifacts.seal_content(directory, work, stage=stage)
            work.accepted_artifact_ids.append(artifact.artifact_id)
            service.store.save_content(directory, node, work, expected_version=version)
