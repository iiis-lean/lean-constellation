"""LC Native Release -> LHF, using only fixed Git objects and registered facts."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import tomllib

from lean_constellation.repo_path_policy import classify_repo_path
from .git_source import GitTree, exact_commit
from .models import (Declaration, DeclRef, ExternalDependency, ExternalRef, NaturalLanguage,
                     Node, Origin, Repo, RepoData, RepoDependency, Section, Workspace,
                     WorkspaceData, node_path, relative_path, safe_segment)
from .storage import load_workspace, validate_workspace, write_metadata


def canonical_statement(code: str) -> str:
    """LC declared_api.py normalization, deliberately preserving imports/docstrings."""
    lines = [line.rstrip() for line in code.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines) + '\n'


def source_body(code: str) -> str:
    """Ignore only LC-owned projection regions; preserve helpers and ordinary comments."""
    code = code.replace('\r\n', '\n').replace('\r', '\n')
    begin = '-- lean-constellation: managed-imports-begin'
    end = '-- lean-constellation: managed-imports-end'
    lines = code.splitlines(keepends=True)
    starts = [i for i, s in enumerate(lines) if s.rstrip('\n') == begin]
    ends = [i for i, s in enumerate(lines) if s.rstrip('\n') == end]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError('malformed LC managed imports block')
        if any(s.strip() and not s.strip().startswith('import ') for s in lines[starts[0]+1:ends[0]]):
            raise ValueError('non-import content in managed imports block')
        del lines[starts[0]:ends[0]+1]
    code = ''.join(lines)
    # Balanced Lean comments avoid swallowing helper code across multiple docstrings.
    spans = []
    i = 0
    while i < len(code):
        if code.startswith('--', i):
            e = code.find('\n', i)
            i = len(code) if e < 0 else e + 1
        elif code.startswith('/-', i):
            start, depth = i, 1
            i += 2
            while i < len(code) and depth:
                if code.startswith('/-', i):
                    depth += 1; i += 2
                elif code.startswith('-/', i):
                    depth -= 1; i += 2
                else:
                    i += 1
            if depth:
                raise ValueError('unterminated Lean comment in capture/source')
            body = code[start:i]
            if body.startswith('/--') and any(s.strip() == '# lean-constellation target' for s in body.splitlines()):
                spans.append((start, i))
        elif code[i] == '"':
            i += 1
            while i < len(code):
                if code[i] == '\\':
                    i += 2
                elif code[i] == '"':
                    i += 1; break
                else:
                    i += 1
        else:
            i += 1
    if len(spans) > 1:
        raise ValueError('multiple managed target docstrings')
    for start, end in reversed(spans):
        code = code[:start] + code[end:]
    return canonical_statement(code.strip())


@dataclass
class NativeRepo:
    key: str
    tree: GitTree
    release: dict
    package: dict
    nodes: dict[str, tuple[str, dict, dict]] = field(default_factory=dict)
    selected: dict[tuple[str, str], tuple[dict, dict, str]] = field(default_factory=dict)
    materials: dict[str, str] = field(default_factory=dict)
    required_repos: set[str] = field(default_factory=set)

    @classmethod
    def read(cls, key: str, tree: GitTree, release_id: str | None = None) -> NativeRepo:
        if tree.json('.lean_constellation/repo_format.json').get('repo_format') != 'native':
            raise ValueError(f'{key}: only native LC repositories are supported')
        release = tree.release(release_id)
        package = tomllib.loads(tree.bytes('lakefile.toml').decode())
        result = cls(key, tree, release, package)
        versions = release.get('node_contract_versions', {})
        if not versions:
            raise ValueError(f'{key}: Release has no node contract versions')
        for nid, version in versions.items():
            safe_segment(nid)
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise ValueError('invalid contract version')
            stem = f'.lean_constellation/nodes/{nid}'
            meta = tree.json(stem + '/node.json')
            path = node_path(meta['path'])
            contract = tree.json(f'{stem}/contracts/{version}.json')
            if (meta.get('node_id') != nid or contract.get('version') != version
                    or contract.get('status') != 'committed' or contract.get('contract_kind') != meta['kind']):
                raise ValueError(f'{key}:{path}: invalid selected contract')
            if path in result.nodes:
                raise ValueError('duplicate selected node path')
            result.nodes[path] = (stem, meta, contract)
            for dep in contract.get('deps', []):
                if dep['target'].get('repo'):
                    result.required_repos.add(dep['target']['repo'])
            for ref in contract.get('exports', []):
                if ref.get('repo'):
                    result.required_repos.add(ref['repo'])
            if meta['kind'] == 'content':
                for name, revision in contract['decl_graph_head'].items():
                    safe_segment(name)
                    base = f'{stem}/decl_graph/decls/{name}'
                    catalog = tree.json(base + '/decl.json')
                    record = result.revision(base, revision)
                    if catalog.get('name') != name or catalog.get('node_path') != path:
                        raise ValueError('catalog identity mismatch')
                    result.selected[path, name] = (catalog, record, base)
                    for section in (record.get('statement'), record.get('proof')):
                        for dep in (section or {}).get('deps', []):
                            if dep.get('kind') == 'repo_decl' and dep['ref'].get('repo'):
                                result.required_repos.add(dep['ref']['repo'])
        result.required_repos.discard(key)
        return result

    def revision(self, base: str, revision: int) -> dict:
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError('invalid declaration revision')
        record = self.tree.json(f'{base}/revisions/{revision}.json')
        if record.get('revision') != revision or record.get('status') != 'committed':
            raise ValueError(f'invalid committed revision: {base}@{revision}')
        return record

    def pins(self) -> dict[str, dict]:
        packages = self.tree.json('lake-manifest.json')['packages']
        pins = {}
        for package in packages:
            if package['name'] in pins:
                raise ValueError('duplicate Lake manifest package')
            pins[package['name']] = package
        return pins

    def select_materials(self, source_paths: list[str] | None,
                         resource_paths: list[str] | None) -> set[tuple[str, str]]:
        """Select physical files before resolving origins; selection is not usage-driven."""
        self.filter_origins = source_paths is not None or resource_paths is not None
        matched = set()
        roots = [('source', '.lean_constellation/source/', '.lhf/materials/source/', source_paths),
                 ('resource', '.lean_constellation/resources/items/', '.lhf/materials/resources/', resource_paths)]
        for kind, prefix, destination, paths in roots:
            for source in self.tree.files:
                if not source.startswith(prefix):
                    continue
                relative = source[len(prefix):]
                hits = [(kind, p) for p in (paths or [])
                        if relative == p or relative.startswith(p + '/')]
                if not self.filter_origins or hits:
                    self.materials[destination + relative] = source
                    matched.update(hits)
        return matched

    def origin(self, raw: dict) -> Origin | None:
        origin = dict(raw)
        if origin['kind'] == 'source':
            path = relative_path(origin['source_path'])
            prefix = '.lean_constellation/source/'
            destination = '.lhf/materials/source/'
        elif origin['kind'] == 'resource':
            key = safe_segment(origin.get('resource_key') or '')
            prefix = f'.lean_constellation/resources/items/{key}/'
            destination = f'.lhf/materials/resources/{key}/'
            if self.filter_origins and not any(p.startswith(destination) for p in self.materials):
                return None
            metadata = self.tree.json(prefix + 'resource.json')
            path = relative_path(metadata['canonical_entry'])
        else:
            # Unlocated/external origins have no selected local file in a filtered view.
            return None if self.filter_origins else Origin.model_validate(origin)
        if self.filter_origins and destination + path not in self.materials:
            return None
        self.tree.bytes(prefix + path)
        origin['source_path'] = destination + path
        return Origin.model_validate(origin)


def _resolve_ref(owner: NativeRepo, raw: dict, repos: dict[str, NativeRepo]) -> DeclRef:
    ref = DeclRef(repo=raw.get('repo'), node=raw['node'], name=raw['name'])
    provider = repos.get(ref.repo or owner.key)
    if provider is None or (ref.node, ref.name) not in provider.selected:
        raise ValueError(f'{owner.key}: unresolved LC reference {raw}')
    _, head, base = provider.selected[ref.node, ref.name]
    anchor = provider.revision(base, raw['revision'])
    if anchor.get('lean_decl_name') != head.get('lean_decl_name'):
        raise ValueError(f'Lean identity changed for {raw}')
    if anchor['revision'] != head['revision']:
        a = (anchor.get('statement', {}).get('formal') or {}).get('code')
        b = (head.get('statement', {}).get('formal') or {}).get('code')
        if not a or not b or canonical_statement(a) != canonical_statement(b):
            raise ValueError(f'incompatible declared API revision: {raw}')
    if ref.repo == owner.key:
        ref = ref.model_copy(update={'repo': None})
    return ref


def _section(owner: NativeRepo, raw: dict | None, repos: dict[str, NativeRepo]) -> Section | None:
    if raw is None:
        return None
    nl = raw.get('nl')
    deps = []
    for dep in raw.get('deps', []):
        if dep['kind'] == 'repo_decl':
            deps.append(RepoDependency(ref=_resolve_ref(owner, dep['ref'], repos), reason=dep.get('reason')))
        elif dep['kind'] == 'mathlib_decl':
            deps.append(ExternalDependency(ref=ExternalRef(package='mathlib', **dep['ref']), reason=dep.get('reason')))
        else:
            raise ValueError(f'unsupported LC dependency: {dep["kind"]}')
    return Section(nl=NaturalLanguage(text=nl.get('text'), origin=[origin for o in nl.get('origin', []) if (origin := owner.origin(o)) is not None]) if nl else None,
                   fl=(raw.get('formal') or {}).get('code'), deps=deps)


def _convert(owner: NativeRepo, repos: dict[str, NativeRepo]) -> RepoData:
    nodes, declarations = {}, {}
    module_root = safe_segment(owner.package['name'])
    for path, (_, meta, contract) in owner.nodes.items():
        exports = []
        names = list(contract['decl_graph_head']) if meta['kind'] == 'content' else []
        if meta['kind'] == 'content':
            declarations[path] = {}
            for name in names:
                catalog, record, _ = owner.selected[path, name]
                module = catalog.get('module') or ''
                file = relative_path(module.replace('.', '/') + '.lean')
                if not file.startswith(module_root + '/' + path.replace('.', '/') + '/'):
                    raise ValueError(f'declaration file is outside owning node: {file}')
                statement = _section(owner, record['statement'], repos)
                proof = _section(owner, record.get('proof'), repos)
                decl = Declaration(name=name, lean_name=record['lean_decl_name'], kind=catalog['kind'],
                                   summary=catalog['summary'], state=record['state'], file=file,
                                   statement=statement, proof=proof)
                capture = proof.fl if decl.state == 'proved' else statement.fl
                if source_body(capture) != source_body(owner.tree.bytes(file).decode()):
                    raise ValueError(f'capture/source mismatch: {owner.key}:{file}')
                declarations[path][name] = decl
                if catalog.get('public'):
                    exports.append(DeclRef(node=path, name=name))
        else:
            exports = [_resolve_ref(owner, ref, repos) for ref in contract.get('exports', [])]
        nodes[path] = Node(kind=meta['kind'], goal=contract['goal'], boundary=contract['boundary'],
                           constraints=contract.get('constraints'), exports=exports, declarations=names,
                           children=sorted(p for p in owner.nodes if p != path and p.rsplit('.', 1)[0] == path))
    return RepoData(metadata=Repo(module_root=module_root), nodes=nodes, declarations=declarations)


_EXCLUDED_FILES = {'lc-export.json', 'publication-files.json', 'PUBLICATION_PROVENANCE.md'}


def _ordinary_file(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (classify_repo_path(PurePosixPath(path)).publication_eligible
            and parts[0] not in {'.lean_constellation', '.lhf'}
            and parts[:2] != ('docs', 'lean-constellation') and path not in _EXCLUDED_FILES)


def export_release(*, repo_paths: dict[str, Path], release_id: str, output: Path,
                   main_repo: str | None = None, release_commit: str | None = None,
                   source_paths: list[str] | None = None, resource_paths: list[str] | None = None) -> dict:
    """Export a complete pinned native closure to a new directory; return an external report."""
    def normalize(paths: list[str] | None) -> list[str] | None:
        if paths is None:
            return None
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            raise ValueError('material paths must be a list of relative files/directories')
        return list(dict.fromkeys(relative_path(p[:-1] if p.endswith('/') else p) for p in paths))

    source_paths, resource_paths = normalize(source_paths), normalize(resource_paths)
    if main_repo is None:
        if len(repo_paths) != 1:
            raise ValueError('main_repo is required when multiple repos are supplied')
        main_repo = next(iter(repo_paths))
    for key in repo_paths:
        safe_segment(key)
    if main_repo not in repo_paths:
        raise ValueError('main_repo has no local repo mapping')
    safe_segment(release_id)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f'output already exists: {output}')
    for source in repo_paths.values():
        if output.resolve().is_relative_to(Path(source).resolve()):
            raise ValueError('output must be outside every source repo')
    start = exact_commit(release_commit) if release_commit else f'refs/lean-constellation/releases/{release_id}'
    repos: dict[str, NativeRepo] = {}
    visiting = set()

    def visit(key: str, revision: str, rid: str | None = None) -> None:
        if key in visiting:
            raise ValueError(f'cyclic LC provider dependency: {key}')
        if key in repos:
            if repos[key].tree.commit != revision:
                raise ValueError(f'conflicting fixed commits for provider {key}')
            return
        if key not in repo_paths:
            raise ValueError(f'missing local LC provider mapping: {key}')
        owner = NativeRepo.read(key, GitTree(repo_paths[key], revision), rid)
        repos[key] = owner
        visiting.add(key)
        pins = owner.pins()
        requirements = owner.package.get('require', [])
        by_name = {r['name']: r for r in requirements}
        if len(by_name) != len(requirements):
            raise ValueError('duplicate Lake requirement')
        for required in owner.required_repos:
            if required not in by_name:
                raise ValueError(f'{key}: registered LC provider missing from Lake requirements: {required}')
        for name, req in by_name.items():
            is_lc = name in owner.required_repos or name in repo_paths
            if not is_lc:
                continue
            pin = pins.get(name)
            if not pin or pin.get('type') != 'git' or req.get('path') or req.get('subDir') or pin.get('subDir'):
                raise ValueError(f'{key}: LC provider requires a root Git package pin: {name}')
            commit = exact_commit(pin.get('rev', ''))
            if req.get('rev') != commit or (req.get('git') and req['git'].rstrip('/') != pin.get('url', '').rstrip('/')):
                raise ValueError(f'{key}: Lake requirement/manifest pin mismatch for {name}')
            visit(name, commit)
        visiting.remove(key)

    visit(main_repo, start, release_id)
    # Lake's consumer manifest also locks inherited packages. It must describe
    # the same native closure as the providers' own fixed manifests.
    for key, owner in repos.items():
        for name, pin in owner.pins().items():
            if name in repos and pin.get('rev') != repos[name].tree.commit:
                raise ValueError(f'{key}: inherited Lake pin conflicts with selected provider {name}')
    matched = set()
    for owner in repos.values():
        matched.update(owner.select_materials(source_paths, resource_paths))
    requested = {('source', p) for p in (source_paths or [])} | {('resource', p) for p in (resource_paths or [])}
    if requested - matched:
        raise ValueError(f'material selections matched no files: {sorted(requested - matched)}')
    data = WorkspaceData(metadata=Workspace(main_repo=main_repo, repos={key: key for key in repos}),
                         repos={key: _convert(owner, repos) for key, owner in repos.items()})
    validate_workspace(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{output.name}.staging-', dir=output.parent))
    try:
        for key, owner in repos.items():
            root = staging / key
            files = {p: p for p in owner.tree.files if _ordinary_file(p)}
            files.update(owner.materials)
            for destination, source in files.items():
                contents = owner.tree.bytes(source)
                path = root / destination
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
                if owner.tree.files[source].mode == '100755':
                    path.chmod(0o755)
        write_metadata(staging, data)
        loaded = load_workspace(staging)
        if loaded != data:
            raise ValueError('LHF roundtrip differs from the selected Release')
        if output.exists() or output.is_symlink():
            raise ValueError('output appeared during export')
        os.rename(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {'output': str(output), 'main_repo': main_repo, 'repos': {
        key: {'commit': owner.tree.commit, 'release_id': owner.release['release_id'],
              'nodes': len(data.repos[key].nodes), 'declarations': len(owner.selected),
              'exports': len(data.repos[key].nodes['Main'].exports)}
        for key, owner in repos.items()}}
