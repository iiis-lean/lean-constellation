"""Export explicitly sealed Restructure acceptances without a Native Release."""
from __future__ import annotations

import hashlib
from graphlib import CycleError, TopologicalSorter
import json
from pathlib import Path
import shutil
import tempfile

from lean_constellation.domain.restructure import BuildReceipt, ContentWork, RepoPlan
from lean_constellation.services.restructure.source_contract import metadata_review_digest, contract_digest
from .models import (Declaration, DeclRef, ExternalDependency, ExternalRef, NaturalLanguage,
                     Node, Origin, Repo, RepoData, RepoDependency, Section, Workspace, WorkspaceData,
                     safe_segment)
from .storage import load_workspace, safe_file, write_metadata

META = '.lean_constellation/restructure'


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    return json.dumps(data, ensure_ascii=False, indent=2).encode() + b'\n'


def _model(data, model):
    payload = json.loads(data)
    payload.pop('_version', None)
    return model.model_validate(payload)


def _atomic_directory(output, populate):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f'output already exists: {output}')
    safe_file(output.parent, output.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f'.{output.name}.', dir=output.parent))
    try:
        result = populate(temporary)
        temporary.rename(output)
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _put(root, relative, data):
    path = safe_file(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _material_refs(plan, works):
    refs = [ref for node in plan.nodes.values() for ref in node.material_refs]
    refs += [ref for values in plan.material_assignments.values() for ref in values]
    refs += [ref for work in works.values() for decl in work.decls.values()
             for section in (decl.statement, decl.proof) if section
             for origin in section.nl.origins for ref in origin.source_refs]
    return refs


def seal_restructure_acceptance(*, repo_root: Path, receipt_path: Path, output: Path) -> dict:
    """Freeze validated section metadata, successful final sources and provenance atomically.

    This is an explicit acceptance action, not an implicit repair or v1 migration.
    Future workspace edits do not alter this self-contained bundle.
    """
    repo_root = Path(repo_root)
    receipt_path = Path(receipt_path)
    for unsafe in (repo_root / (META + '/inputs'), repo_root / (META + '/build_views')):
        if Path(output).absolute().is_relative_to(unsafe.absolute()):
            raise ValueError('acceptance output cannot be inside source/provenance trees')
    receipt_relative = receipt_path.absolute().relative_to(repo_root.absolute()).as_posix()
    receipt_bytes = safe_file(repo_root, receipt_relative).read_bytes()
    receipt = _model(receipt_bytes, BuildReceipt)
    if not receipt.success or receipt.stage.value != 'final' or not receipt.files:
        raise ValueError('acceptance requires successful final receipt with source inventory')
    metadata = {'metadata/plan.json': safe_file(repo_root, f'{META}/plan.json').read_bytes()}
    plan = _model(metadata['metadata/plan.json'], RepoPlan)
    if receipt.repo_key != plan.repo_key:
        raise ValueError('receipt repo does not match plan')
    works = {}
    for node in plan.content_nodes():
        name = node.path.replace('.', '__') + '.json'
        data = safe_file(repo_root, f'{META}/content/{name}').read_bytes()
        metadata[f'metadata/content/{name}'] = data
        works[node.path] = _model(data, ContentWork)
    files = {}
    for relative, digest in receipt.files.items():
        frozen = safe_file(repo_root, f'{META}/build_views/{safe_segment(receipt.operation_id)}/{relative}')
        data = (frozen if frozen.is_file() else safe_file(repo_root, relative)).read_bytes()
        if _digest(data) != digest:
            raise ValueError(f'accepted source digest mismatch: {relative}')
        files[relative] = data
    for ref in _material_refs(plan, works):
        if ref.corpus != 'repo-local':
            raise ValueError(f'unregistered material corpus: {ref.corpus}')
        material = safe_file(repo_root, ref.path).read_bytes()
        if ref.path in files and files[ref.path] != material:
            raise ValueError('material conflicts with accepted source')
        files[ref.path] = material
    # Preserve attribution and input scope alongside the cited files.
    inputs = safe_file(repo_root, f'{META}/inputs')
    if inputs.exists():
        for path in inputs.rglob('*'):
            relative = path.relative_to(repo_root).as_posix()
            checked = safe_file(repo_root, relative)
            if checked.is_file():
                material = checked.read_bytes()
                if relative in files and files[relative] != material:
                    raise ValueError('provenance conflicts with accepted source')
                files[relative] = material
    for name in ('LICENSE', 'COPYING', 'NOTICE'):
        path = safe_file(repo_root, name)
        if path.is_file():
            material = path.read_bytes()
            if name in files and files[name] != material:
                raise ValueError('license conflicts with accepted source')
            files[name] = material
    manifest = {'repo_key': plan.repo_key,
                'receipt_digest': _digest(receipt_bytes),
                'metadata': {p: _digest(b) for p, b in metadata.items()},
                'files': {p: _digest(b) for p, b in files.items()}}
    def populate(root):
        _put(root, 'accepted.json', _json(manifest))
        _put(root, 'receipt.json', receipt_bytes)
        for path, data in metadata.items():
            _put(root, path, data)
        for path, data in files.items():
            _put(root, 'files/' + path, data)
        _load_acceptance(root)
        # Re-read mutable metadata, ensuring capture did not mix concurrent writes.
        for path, data in metadata.items():
            live = f'{META}/' + path.removeprefix('metadata/')
            if safe_file(repo_root, live).read_bytes() != data:
                raise ValueError('metadata changed while sealing acceptance')
        return {'repo_key': plan.repo_key, 'operation_id': receipt.operation_id, 'output': str(output)}
    return _atomic_directory(output, populate)


def _load_acceptance(root):
    root = Path(root)
    manifest = json.loads(safe_file(root, 'accepted.json').read_bytes())
    if set(manifest) != {'repo_key', 'receipt_digest', 'metadata', 'files'}:
        raise ValueError('invalid acceptance manifest fields')
    receipt_bytes = safe_file(root, 'receipt.json').read_bytes()
    if _digest(receipt_bytes) != manifest['receipt_digest']:
        raise ValueError('receipt digest mismatch')
    receipt = _model(receipt_bytes, BuildReceipt)
    if not receipt.success or receipt.stage.value != 'final' or not receipt.files:
        raise ValueError('acceptance requires successful final receipt')
    metadata, files = {}, {}
    for inventory, destination, prefix in ((manifest['metadata'], metadata, ''), (manifest['files'], files, 'files/')):
        for path, digest in inventory.items():
            data = safe_file(root, prefix + path).read_bytes()
            if _digest(data) != digest:
                raise ValueError(f'acceptance digest mismatch: {path}')
            destination[path] = data
    plan = _model(metadata['metadata/plan.json'], RepoPlan)
    if plan.repo_key != manifest['repo_key'] or receipt.repo_key != plan.repo_key:
        raise ValueError('acceptance repo identity mismatch')
    works = {}
    expected = {'metadata/plan.json'}
    for node in plan.content_nodes():
        name = f"metadata/content/{node.path.replace('.', '__')}.json"
        expected.add(name)
        work = _model(metadata[name], ContentWork)
        if work.repo_key != plan.repo_key or work.node_path != node.path or work.plan_version != plan.version:
            raise ValueError('content identity/plan version mismatch')
        if work.stage.value not in {'proved', 'final'}:
            raise ValueError(f'content proof stage not accepted: {node.path}')
        works[node.path] = work
    if set(metadata) != expected:
        raise ValueError('unindexed acceptance metadata')
    for path, digest in receipt.files.items():
        if path not in files or _digest(files[path]) != digest:
            raise ValueError(f'final receipt source mismatch: {path}')
    for work in works.values():
        for name, decl in work.decls.items():
            if name != decl.name or decl.status.value == 'draft' or decl.file not in receipt.files:
                raise ValueError(f'unaccepted declaration: {name}')
            if not decl.statement.formal or not decl.statement.formal.code.strip():
                raise ValueError(f'missing statement formal: {name}')
            current = files[decl.file].decode()
            accepted_contract = contract_digest(decl, current)
            if contract_digest(decl, decl.statement.formal.code) != accepted_contract:
                raise ValueError(f'statement formal contract mismatch: {name}')
            if work.declared_baseline.get(name) != accepted_contract:
                raise ValueError(f'declared baseline mismatch: {name}')
            for section_name in (['statement', 'proof'] if decl.is_theorem_like else ['statement']):
                section_value = getattr(decl, section_name)
                if section_value is None or not section_value.nl.text.strip():
                    raise ValueError(f'missing {section_name} NL: {name}')
                if getattr(decl, section_name + '_review_digest') != metadata_review_digest(decl, current, section_name):
                    raise ValueError(f'unreviewed {section_name} metadata: {name}')
            if decl.is_theorem_like:
                if decl.status.value != 'proved' or not decl.proof or not decl.proof.formal:
                    raise ValueError(f'missing proved formal: {name}')
                if decl.proof.formal.code != current:
                    raise ValueError(f'proof formal differs from accepted source: {name}')
            elif decl.statement.formal.code != current:
                raise ValueError(f'statement formal differs from accepted source: {name}')
    catalogue = {(node, name) for node, work in works.items() for name in work.decls}
    refs = [*plan.main_exports, *plan.interface_seeds]
    refs += [ref for node in plan.nodes.values() for ref in node.exports]
    refs += [dep.ref for work in works.values() for decl in work.decls.values()
             for dep in decl.dependencies if not dep.external]
    for ref in refs:
        if ref.repo != plan.repo_key:
            if ref.repo not in receipt.provider_refs:
                raise ValueError(f'cross-repo reference missing provider pin: {ref}')
            continue
        matches = [(node, name) for node, name in catalogue
                   if name == ref.name and (ref.node is None or node == ref.node)]
        if len(matches) != 1:
            raise ValueError(f'unresolved or ambiguous declaration: {ref}')
    for ref in _material_refs(plan, works):
        if ref.corpus != 'repo-local' or ref.path not in files:
            raise ValueError(f'unresolved material: {ref.corpus}:{ref.path}')
    return plan, works, files, receipt


def export_restructure(*, acceptances: dict[str, Path], output: Path, main_repo: str | None = None) -> dict:
    """Export a complete dependency closure of immutable acceptance bundles."""
    if not acceptances:
        raise ValueError('at least one acceptance is required')
    for path in acceptances.values():
        if Path(output).absolute().is_relative_to(Path(path).absolute()):
            raise ValueError('export output must be outside acceptance bundles')
    accepted = {safe_segment(key): _load_acceptance(path) for key, path in acceptances.items()}
    for key, (_, _, _, receipt) in accepted.items():
        for provider, operation in receipt.provider_refs.items():
            if provider not in accepted or accepted[provider][3].operation_id != operation:
                raise ValueError(f'provider acceptance pin mismatch: {key} -> {provider}/{operation}')
    try:
        tuple(TopologicalSorter({key: set(item[3].provider_refs) for key, item in accepted.items()}).static_order())
    except CycleError as exc:
        raise ValueError('cyclic provider acceptance references') from exc
    main_repo = main_repo or next(iter(accepted))
    catalogue = {(key, node, name): decl for key, (_, works, _, _) in accepted.items()
                 for node, work in works.items() for name, decl in work.decls.items()}
    def resolve(ref):
        matches = [(key, node, name) for key, node, name in catalogue
                   if key == ref.repo and name == ref.name and (ref.node is None or ref.node == node)]
        if len(matches) != 1:
            raise ValueError(f'unresolved or ambiguous declaration: {ref}')
        key, node, name = matches[0]
        return DeclRef(repo=key, node=node, name=name)
    def material_path(path):
        return '.lhf/materials/source/' + path.removeprefix(META + '/inputs/')
    def section(value):
        origins = []
        for origin in value.nl.origins:
            for ref in origin.source_refs:
                origins.append(Origin(kind='source', ref=f'{ref.corpus}:{ref.path}', source_path=material_path(ref.path),
                                      start_locator=ref.locator, note='; '.join(n for n in (origin.note, ref.note) if n) or None))
            if not origin.source_refs and origin.note:
                origins.append(Origin(kind='note', note=origin.note))
        deps = [ExternalDependency(ref=ExternalRef(package='mathlib' if d.ref.repo.lower() == 'mathlib' else d.ref.repo, name=d.ref.name), reason=d.reason)
                if d.external else RepoDependency(ref=resolve(d.ref), reason=d.reason) for d in value.deps]
        return Section(nl=NaturalLanguage(text=value.nl.text, origin=origins),
                       fl=value.formal.code if value.formal else None, deps=deps)
    repos = {}
    for key, (plan, works, _, receipt) in accepted.items():
        if plan.repo_key != key:
            raise ValueError('acceptance key differs from repo identity')
        for work in works.values():
            for decl in work.decls.values():
                for dependency in decl.dependencies:
                    if not dependency.external and dependency.ref.repo != key and dependency.ref.repo not in receipt.provider_refs:
                        raise ValueError('cross-repo dependency missing final provider pin')
        selected = [*plan.main_exports, *plan.interface_seeds]
        selected += [r for n in plan.nodes.values() for r in n.exports]
        selected += [dep.ref for _, other_works, _, _ in accepted.values() for work in other_works.values()
                     for decl in work.decls.values() for dep in decl.dependencies if not dep.external]
        nodes, declarations = {}, {}
        for path, node in plan.nodes.items():
            exports = {}
            for ref in (plan.main_exports if path == 'Main' else selected):
                resolved = resolve(ref)
                if resolved.repo == key and (path == 'Main' or resolved.node == path or resolved.node.startswith(path + '.')):
                    exports[(resolved.node, resolved.name)] = resolved
            nodes[path] = Node(kind='content' if path in works else 'scope', goal=node.goal,
                               boundary=node.boundary, constraints=node.constraints, summary=node.summary,
                               exports=list(exports.values()), children=[n.path for n in plan.nodes.values() if n.parent == path],
                               declarations=list(works[path].decls) if path in works else [])
            if path in works:
                declarations[path] = {name: Declaration(name=name, lean_name=d.lean_name, kind=d.kind.value,
                    summary=d.summary, state='proved' if d.is_theorem_like else 'declared', file=d.file,
                    statement=section(d.statement), proof=section(d.proof) if d.proof else None)
                    for name, d in works[path].decls.items()}
        repos[key] = RepoData(metadata=Repo(module_root=plan.module_root), nodes=nodes, declarations=declarations)
    data = WorkspaceData(metadata=Workspace(main_repo=main_repo, repos={k:k for k in repos}), repos=repos)
    def populate(root):
        for key, (_, _, files, receipt) in accepted.items():
            for relative, content in files.items():
                if relative in receipt.files:
                    _put(root, f'{key}/{relative}', content)
                if relative not in receipt.files or any(ref.path == relative for ref in _material_refs(accepted[key][0], accepted[key][1])):
                    _put(root, f'{key}/{material_path(relative)}', content)
        write_metadata(root, data)
        load_workspace(root)
        return {'main_repo': main_repo, 'repos': list(repos), 'declarations': len(catalogue), 'output': str(output)}
    return _atomic_directory(output, populate)
