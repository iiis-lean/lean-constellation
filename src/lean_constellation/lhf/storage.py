"""Directory serialization and lightweight structural validation for LHF."""
from __future__ import annotations

import json
from pathlib import Path

from .models import (Declaration, DeclRef, Node, Repo, RepoData, RepoDependency,
                     Workspace, WorkspaceData, relative_path)


def safe_file(root: Path, relative: str) -> Path:
    relative_path(relative)
    root = Path(root)
    path = root
    for part in relative.split('/'):
        path = path / part
        if path.is_symlink():
            raise ValueError(f'symlink not allowed: {path}')
    if any(p.is_symlink() for p in (root.absolute(), *root.absolute().parents)):
        raise ValueError(f'symlink root not allowed: {root}')
    return path


def _read(root: Path, relative: str, model):
    return model.model_validate_json(safe_file(root, relative).read_text(encoding='utf-8'))


def _node_dir(path: str) -> str:
    return '.lhf/nodes/' + path.replace('.', '/')


def validate_workspace(data: WorkspaceData, root: Path | None = None) -> None:
    """Check declared structure and references; never run Lean or infer dependencies."""
    if set(data.metadata.repos) != set(data.repos):
        raise ValueError('workspace repo inventory differs from loaded repos')

    def target(owner: str, ref: DeclRef) -> Declaration:
        repo_key = ref.repo or owner
        repo = data.repos.get(repo_key)
        decl = repo.declarations.get(ref.node, {}).get(ref.name) if repo else None
        if decl is None:
            raise ValueError(f'unresolved declaration: {owner} -> {ref}')
        if repo_key != owner:
            if not any((r.repo or repo_key, r.node, r.name) == (repo_key, ref.node, ref.name)
                       for r in repo.nodes['Main'].exports):
                raise ValueError(f'cross-repo reference is not a Main export: {ref}')
        return decl

    for key, repo in data.repos.items():
        if 'Main' not in repo.nodes:
            raise ValueError(f'{key}: missing Main')
        symbols: set[str] = set()
        seen: set[str] = set()
        pending = ['Main']
        while pending:
            path = pending.pop()
            if path in seen or path not in repo.nodes:
                raise ValueError(f'{key}: duplicate or missing node {path}')
            seen.add(path)
            node = repo.nodes[path]
            for child in node.children:
                if child.rsplit('.', 1)[0] != path or child == path:
                    raise ValueError(f'{key}: child is not an immediate descendant: {child}')
            pending.extend(node.children)
            decls = repo.declarations.get(path, {})
            if set(decls) != set(node.declarations):
                raise ValueError(f'{key}:{path}: declaration inventory mismatch')
            for name, decl in decls.items():
                if decl.lean_name in symbols:
                    raise ValueError(f'duplicate Lean declaration identity: {decl.lean_name}')
                symbols.add(decl.lean_name)
                prefix = f"{repo.metadata.module_root}/{path.replace('.', '/')}/"
                if not decl.file.startswith(prefix):
                    raise ValueError(f'declaration file outside owning node: {decl.file}')
                if name != decl.name:
                    raise ValueError('declaration name disagrees with its key')
                for section in (decl.statement, decl.proof):
                    if section:
                        for dep in section.deps:
                            if isinstance(dep, RepoDependency):
                                target(key, dep.ref)
            for ref in node.exports:
                if ref.repo is not None and ref.repo != key:
                    raise ValueError('node exports must belong to their own repo')
                if ref.node != path and not (node.kind == 'scope' and ref.node.startswith(path + '.')):
                    raise ValueError('export must belong to this node subtree')
                target(key, ref)
        if seen != set(repo.nodes) or not set(repo.declarations) <= seen:
            raise ValueError(f'{key}: unreachable nodes or declarations')
        if root is not None:
            repo_root = safe_file(root, data.metadata.repos[key])
            for path in repo.nodes:
                for filename in ('Prelude.lean', 'Interfaces.lean'):
                    relative = f'{repo.metadata.module_root}/{path.replace(".", "/")}/{filename}'
                    if not safe_file(repo_root, relative).is_file():
                        raise ValueError(f'missing node module: {relative}')
            for decls in repo.declarations.values():
                for decl in decls.values():
                    if not safe_file(repo_root, decl.file).is_file():
                        raise ValueError(f'missing declaration file: {decl.file}')
                    for section in (decl.statement, decl.proof):
                        for origin in (section.nl.origin if section and section.nl else []):
                            if origin.source_path and not safe_file(repo_root, origin.source_path).is_file():
                                raise ValueError(f'missing origin material: {origin.source_path}')


def load_workspace(root: Path) -> WorkspaceData:
    root = Path(root)
    metadata = _read(root, '.lhf/workspace.json', Workspace)
    repos = {}
    for key, directory in metadata.repos.items():
        repo_root = safe_file(root, directory)
        repo = _read(repo_root, '.lhf/repo.json', Repo)
        nodes, decls = {}, {}
        pending = [repo.root_node]
        while pending:
            path = pending.pop()
            if path in nodes:
                raise ValueError(f'duplicate node: {path}')
            node = _read(repo_root, _node_dir(path) + '/node.json', Node)
            nodes[path] = node
            pending.extend(node.children)
            if node.kind == 'content':
                decls[path] = {name: _read(repo_root, f'{_node_dir(path)}/decls/{name}.json', Declaration)
                               for name in node.declarations}
        inventory = safe_file(repo_root, '.lhf/nodes')
        expected = {_node_dir(p) + '/node.json' for p in nodes}
        expected.update(f'{_node_dir(p)}/decls/{n}.json' for p, ns in decls.items() for n in ns)
        actual = {p.relative_to(repo_root).as_posix() for p in inventory.rglob('*.json')}
        if actual != expected:
            raise ValueError(f'{key}: unindexed or missing node/declaration metadata')
        repos[key] = RepoData(metadata=repo, nodes=nodes, declarations=decls)
    result = WorkspaceData(metadata=metadata, repos=repos)
    validate_workspace(result, root)
    return result


def write_metadata(root: Path, data: WorkspaceData) -> None:
    """Write fresh metadata alongside existing Lean files, refusing existing metadata."""
    root = Path(root)
    validate_workspace(data, root)
    locations = [safe_file(root, '.lhf/workspace.json')]
    locations += [safe_file(safe_file(root, p), '.lhf/repo.json') for p in data.metadata.repos.values()]
    for key, repo in data.repos.items():
        repo_root = safe_file(root, data.metadata.repos[key])
        for path, node in repo.nodes.items():
            locations.append(safe_file(repo_root, _node_dir(path) + '/node.json'))
            locations.extend(safe_file(repo_root, f'{_node_dir(path)}/decls/{name}.json')
                             for name in node.declarations)
    if any(p.exists() for p in locations):
        raise ValueError('LHF metadata already exists')

    def write(base: Path, relative: str, model) -> None:
        path = safe_file(base, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(model.model_dump(mode='json', exclude_none=True),
                                   ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    write(root, '.lhf/workspace.json', data.metadata)
    for key, repo in data.repos.items():
        repo_root = safe_file(root, data.metadata.repos[key])
        write(repo_root, '.lhf/repo.json', repo.metadata)
        for path, node in repo.nodes.items():
            write(repo_root, _node_dir(path) + '/node.json', node)
            for name in node.declarations:
                write(repo_root, f'{_node_dir(path)}/decls/{name}.json', repo.declarations[path][name])
