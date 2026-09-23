"""Explicit v1-to-v2 accepted-workspace migration into a new directory."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from lean_constellation.lhf.storage import safe_file


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _copy_tree(source, destination):
    if source.is_symlink():
        raise ValueError(f"migration refuses symlink: {source}")
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ValueError(f'migration refuses symlink: {path}')
    shutil.copytree(source, destination)


def _receipt(repo, operation, stage):
    meta = repo / '.lean_constellation/restructure'
    receipt = _read(safe_file(meta, f'builds/{operation}.json'))
    if not receipt['success'] or receipt['stage'] != stage or receipt['operation_id'] != operation:
        raise ValueError(f'not a successful {stage} receipt: {operation}')
    view = safe_file(meta, f'build_views/{operation}')
    for relative, expected in receipt['files'].items():
        if _digest(safe_file(view, relative).read_bytes()) != expected:
            raise ValueError(f'corrupt accepted source: {operation}/{relative}')
    return receipt, view


def migrate_workspace(*, source: Path, output: Path, review: Path,
                      declared_builds: dict[str, str], final_builds: dict[str, str]):
    """Require explicit mathematical review; never infer missing NL or origins."""
    from lean_constellation.domain.restructure import ContentWork, WorkspacePlan, DeclRecord
    from .source_contract import metadata_review_digest, contract_digest
    source, output = Path(source).resolve(), Path(output).absolute()
    safe_file(output.parent, output.name)
    if output.exists() or output.is_relative_to(source):
        raise ValueError('output must be a new directory outside source workspace')
    raw_workspace = _read(source / '.lean_constellation/restructure/workspace.json')
    keys = set(raw_workspace['repos'])
    if keys != set(final_builds) or keys != set(declared_builds):
        raise ValueError('explicit declared and final receipt required for every repo')
    review_data = _read(review)
    review_root = Path(review_data.get("source_root", source)).resolve()
    if not review_root.is_relative_to(source):
        raise ValueError("review source_root must be within source workspace")
    for relative, expected in review_data.get("source_metadata_files", {}).items():
        if _digest(safe_file(review_root, relative).read_bytes()) != expected:
            raise ValueError(f"review is stale for source metadata: {relative}")
    # Single-repo review patches may omit the redundant repo wrapper.
    reviews = review_data.get('repos') or ({next(iter(keys)): review_data} if len(keys) == 1 else {})
    if set(reviews) != keys:
        raise ValueError('review repo inventory must match workspace')
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=output.name + '.migration-', dir=output.parent))
    report = dict(source=str(source), output=str(output), review_digest=_digest(Path(review).read_bytes()), repos={})
    try:
        workspace = copy.deepcopy(raw_workspace)
        workspace.pop('_version', None)
        workspace.update(workspace_root=str(output))
        for key, spec in workspace['repos'].items():
            repo = safe_file(source, spec['directory'])
            target = safe_file(staging, spec['directory'])
            meta = repo / '.lean_constellation/restructure'
            target_meta = target / '.lean_constellation/restructure'
            declared, declared_view = _receipt(repo, declared_builds[key], 'declared')
            final, final_view = _receipt(repo, final_builds[key], 'final')
            if final.get('repo_key') != key or declared.get('repo_key') != key:
                raise ValueError('receipt belongs to another repo')
            from .build import RestructureBuildService
            inventory = {p.relative_to(repo).as_posix() for p in RestructureBuildService.input_files(repo) if p.is_file()}
            if inventory != set(final['files']):
                raise ValueError('current source inventory differs from final acceptance')
            for relative, digest in final['files'].items():
                data = safe_file(repo, relative).read_bytes()
                if _digest(data) != digest:
                    raise ValueError(f'current source differs from final acceptance: {relative}')
                dst = safe_file(target, relative)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
            # Preserve immutable v1 evidence separately, never rewrite old artifacts.
            legacy = target_meta / 'migration_v1'
            legacy.mkdir(parents=True)
            for name in ['plan.json', 'content', 'artifacts', 'artifact_files', 'builds', 'build_views', 'inputs']:
                src = meta / name
                if not src.exists():
                    continue
                dst = (target_meta if name in {'artifacts', 'artifact_files', 'builds', 'build_views', 'inputs'} else legacy) / name
                if src.is_dir():
                    _copy_tree(src, dst)
                else:
                    shutil.copy2(src, dst)
            plan = _read(meta / 'plan.json')
            plan.pop('_version', None)
            if any('scope' not in n or 'boundary' in n for n in plan['nodes'].values()):
                raise ValueError('migration requires legacy scope-based nodes')
            patch = reviews[key]
            if set(patch['nodes']) != set(plan['nodes']):
                raise ValueError('node review must cover every node exactly')
            for node, record in plan['nodes'].items():
                note = patch['nodes'][node]
                if not note.get('boundary', '').strip() or not note.get('evidence'):
                    raise ValueError(f'node requires reviewed mathematical boundary and evidence: {node}')
                record.pop('scope', None)
                record['boundary'] = note['boundary']
                record['constraints'] = note.get('constraints')
            spec['plan'] = plan
            content_nodes = {n for n, v in plan['nodes'].items() if v['kind'] == 'content'}
            if set(patch['declarations']) != content_nodes:
                raise ValueError('declaration review must cover every Content')
            count = 0
            for node in sorted(content_nodes):
                relative = f'content/{node.replace(".", "__")}.json'
                work = _read(meta / relative)
                work.pop('_version', None)
                if set(work['decls']) != set(patch['declarations'][node]):
                    raise ValueError(f'declaration review inventory mismatch: {node}')
                for name, decl in work['decls'].items():
                    if 'statement' in decl or 'statement_text' not in decl:
                        raise ValueError('migration requires legacy flat declaration fields')
                    if any(dep['role'] not in {'statement', 'proof'} for dep in decl['dependencies']):
                        raise ValueError(f'unknown legacy dependency role: {node}/{name}')
                    if decl['file'] not in declared['files'] or decl['file'] not in final['files']:
                        raise ValueError(f'declaration missing accepted snapshots: {name}')
                    reviewed = patch['declarations'][node][name]
                    if not reviewed.get('evidence'):
                        raise ValueError(f'declaration requires review evidence: {name}')
                    theorem = decl['kind'] in {'theorem', 'lemma', 'corollary'}
                    statement = dict(nl=dict(text=reviewed['statement_nl'], origins=reviewed['statement_origins']),
                                     deps=reviewed['statement_dependencies'],
                                     formal=dict(code=safe_file(declared_view, decl['file']).read_text()))
                    proof = dict(nl=dict(text=reviewed['proof_nl'], origins=reviewed['proof_origins']),
                                 deps=reviewed['proof_dependencies'],
                                 formal=dict(code=safe_file(final_view, decl['file']).read_text())) if theorem else None
                    if not theorem and (reviewed['proof_nl'] or reviewed['proof_origins'] or reviewed['proof_dependencies']):
                        raise ValueError(f'non-theorem has unresolved proof metadata: {name}')
                    for field in ['statement_nl', 'statement_text', 'proof_nl', 'proof_text', 'origins', 'dependencies']:
                        decl.pop(field, None)
                    decl.update(statement=statement, proof=proof)
                    # Definitions have no proof-stage section; their latest accepted
                    # complete file remains the statement formal (historical view is retained).
                    if not theorem:
                        decl["statement"]["formal"]["code"] = safe_file(final_view, decl["file"]).read_text()
                    candidate = DeclRecord.model_validate(decl)
                    final_code = safe_file(final_view, decl["file"]).read_text()
                    baseline = work.get("declared_baseline", {}).get(name)
                    if baseline and (contract_digest(candidate, safe_file(declared_view, decl["file"]).read_text()) != baseline
                                     or contract_digest(candidate, final_code) != baseline):
                        raise ValueError(f"selected declared snapshot does not match accepted interface: {name}")
                    decl["statement_review_digest"] = metadata_review_digest(candidate, final_code, "statement")
                    if theorem:
                        decl["proof_review_digest"] = metadata_review_digest(candidate, final_code, "proof")
                    count += 1
                validated = ContentWork.model_validate(work)
                _write(target_meta / relative, validated.model_dump(mode='json'))
            _write(target_meta / 'plan.json', plan)
            report['repos'][key] = dict(declared_build=declared_builds[key], final_build=final_builds[key],
                                       nodes=len(plan['nodes']), contents=len(content_nodes), declarations=count,
                                       source_files=len(final['files']))
        validated_workspace = WorkspacePlan.model_validate(workspace)
        _write(staging / '.lean_constellation/restructure/workspace.json', validated_workspace.model_dump(mode='json'))
        # No executable scheduler state is copied into an offline accepted migration.
        _write(staging / '.lean_constellation/restructure/migration_v1/workspace.json', raw_workspace)
        _write(staging / '.lean_constellation/restructure/migration-review.json', review_data)
        _write(staging / '.lean_constellation/restructure/migration-report.json', report)
        for relative, expected in review_data.get('source_metadata_files', {}).items():
            if _digest(safe_file(review_root, relative).read_bytes()) != expected:
                raise ValueError(f'source metadata changed during migration: {relative}')
        if output.exists():
            raise ValueError('output appeared during migration')
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging)
        raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--declared-build', action='append', required=True, metavar='REPO=ID')
    parser.add_argument('--final-build', action='append', required=True, metavar='REPO=ID')
    args = parser.parse_args()
    def mapping(values):
        result = {}
        for value in values:
            key, sep, identity = value.partition('=')
            if not sep or not key or not identity or key in result:
                raise ValueError('builds require unique REPO=ID entries')
            result[key] = identity
        return result
    print(json.dumps(migrate_workspace(source=args.source, output=args.output, review=args.review,
        declared_builds=mapping(args.declared_build), final_builds=mapping(args.final_build)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
