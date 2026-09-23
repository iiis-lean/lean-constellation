import hashlib
import json
from pathlib import Path

import pytest

from lean_constellation.domain.restructure import DeclRecord
from lean_constellation.services.restructure.source_contract import metadata_review_digest, contract_digest
from lean_constellation.lhf.restructure_export import export_restructure, seal_restructure_acceptance
from lean_constellation.lhf.storage import load_workspace


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def accepted(tmp_path):
    repo = tmp_path / 'repo'
    meta = repo / '.lean_constellation/restructure'
    file = 'R/Main/C/Theorems/t.lean'
    code = 'theorem t : True := by\n-- LC proof begin\n  trivial\n-- LC proof end\n'
    statement = code.replace('  trivial', '  sorry')
    ref = {'repo': 'R', 'node': 'Main.C', 'name': 't'}
    material = '.lean_constellation/restructure/inputs/source.txt'
    origin = {'source_refs': [{'corpus': 'repo-local', 'path': material, 'locator': 'theorem 1'}]}
    decl = DeclRecord(name='t', lean_name='t', kind='theorem', summary='Truth', file=file, status='proved',
        statement={'nl': {'text': 'Truth holds', 'origins': [origin]}, 'formal': {'code': statement}},
        proof={'nl': {'text': 'By triviality', 'origins': [origin]}, 'formal': {'code': code}})
    decl.statement_review_digest = metadata_review_digest(decl, code, 'statement')
    decl.proof_review_digest = metadata_review_digest(decl, code, 'proof')
    plan = {'repo_key': 'R', 'directory': 'R', 'module_root': 'R', 'goal': 'Truth',
        'main_exports': [ref], 'nodes': {
        'Main': {'path': 'Main', 'kind': 'main', 'module': 'R.Main', 'summary': 'Root', 'goal': 'Truth', 'boundary': 'Truth package'},
        'Main.C': {'path': 'Main.C', 'parent': 'Main', 'kind': 'content', 'module': 'R.Main.C',
                   'summary': 'Truth', 'goal': 'Prove truth', 'boundary': 'Only truth'}}}
    write(meta / 'plan.json', plan)
    write(meta / 'content/Main__C.json', {'repo_key': 'R', 'node_path': 'Main.C', 'stage': 'proved', 'decls': {'t': decl.model_dump(mode='json')}, 'declared_baseline': {'t': contract_digest(decl, code)}})
    sources = {file: code, **{f'R/{n}/{f}.lean': '-- module\n' for n in ('Main', 'Main/C') for f in ('Prelude', 'Interfaces')}}
    for relative, text in {**sources, material: 'Theorem 1: truth'}.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    receipt = meta / 'builds/final.json'
    write(receipt, {'artifact_id': 'a', 'operation_id': 'final', 'repo_key': 'R', 'stage': 'final', 'success': True,
                    'files': {p: hashlib.sha256(t.encode()).hexdigest() for p,t in sources.items()}})
    bundle = tmp_path / 'accepted'
    return repo, receipt, bundle, file


def test_frozen_export_preserves_historical_statement_and_materials(accepted, tmp_path):
    repo, receipt, bundle, file = accepted
    seal_restructure_acceptance(repo_root=repo, receipt_path=receipt, output=bundle)
    (repo / file).write_text('broken live source')
    write(repo / '.lean_constellation/restructure/plan.json', {})
    report = export_restructure(acceptances={'R': bundle}, output=tmp_path / 'lhf')
    data = load_workspace(tmp_path / 'lhf')
    decl = data.repos['R'].declarations['Main.C']['t']
    assert 'sorry' in decl.statement.fl and 'trivial' in decl.proof.fl
    assert decl.proof.nl.origin[0].start_locator == 'theorem 1'
    assert data.repos['R'].nodes['Main'].exports[0].name == 't'
    assert report['declarations'] == 1


@pytest.mark.parametrize('failure', ['source', 'boundary', 'review', 'formal', 'reference', 'corpus', 'symlink', 'receipt'])
def test_seal_rejects_invalid_acceptance_atomically(accepted, failure):
    repo, receipt, bundle, file = accepted
    meta = repo / '.lean_constellation/restructure'
    content = meta / 'content/Main__C.json'
    value = json.loads(content.read_text())
    if failure == 'source':
        (repo / file).write_text('changed')
    elif failure == 'boundary':
        plan = json.loads((meta / 'plan.json').read_text()); plan['nodes']['Main']['boundary'] = ''
        write(meta / 'plan.json', plan)
    elif failure == 'review':
        value['decls']['t']['proof_review_digest'] = None
    elif failure == 'formal':
        value['decls']['t']['proof']['formal']['code'] = 'old proof'
    elif failure == 'reference':
        value['decls']['t']['proof']['deps'] = [{'ref': {'repo': 'R', 'name': 'missing'}}]
    elif failure == 'corpus':
        value['decls']['t']['proof']['nl']['origins'][0]['source_refs'][0]['corpus'] = 'unknown'
    elif failure == 'symlink':
        original = repo / file; original.unlink(); original.symlink_to(receipt)
    elif failure == 'receipt':
        data = json.loads(receipt.read_text()); data['success'] = False; write(receipt, data)
    write(content, value)
    with pytest.raises(ValueError):
        seal_restructure_acceptance(repo_root=repo, receipt_path=receipt, output=bundle)
    assert not bundle.exists()


def test_tamper_and_existing_output_rejected(accepted, tmp_path):
    repo, receipt, bundle, file = accepted
    seal_restructure_acceptance(repo_root=repo, receipt_path=receipt, output=bundle)
    with pytest.raises(ValueError, match='exists'):
        seal_restructure_acceptance(repo_root=repo, receipt_path=receipt, output=bundle)
    (bundle / 'files' / file).write_text('tampered')
    with pytest.raises(ValueError, match='digest'):
        export_restructure(acceptances={'R': bundle}, output=tmp_path / 'lhf')
    assert not (tmp_path / 'lhf').exists()


def test_wrong_historical_statement_and_missing_baseline_rejected(accepted):
    repo, receipt, bundle, _ = accepted
    content = repo / '.lean_constellation/restructure/content/Main__C.json'
    value = json.loads(content.read_text())
    original = value['decls']['t']['statement']['formal']['code']
    value['decls']['t']['statement']['formal']['code'] = original.replace('True', 'False')
    write(content, value)
    with pytest.raises(ValueError, match='contract mismatch'):
        seal_restructure_acceptance(repo_root=repo, receipt_path=receipt, output=bundle)
    value['decls']['t']['statement']['formal']['code'] = original
    value['declared_baseline'] = {}
    write(content, value)
    with pytest.raises(ValueError, match='baseline mismatch'):
        seal_restructure_acceptance(repo_root=repo, receipt_path=receipt, output=bundle)


def test_missing_provider_pin_and_nested_output_rejected(accepted, tmp_path):
    repo, receipt, bundle, _ = accepted
    value = json.loads(receipt.read_text()); value['provider_refs'] = {'Provider': 'required-operation'}
    write(receipt, value)
    seal_restructure_acceptance(repo_root=repo, receipt_path=receipt, output=bundle)
    with pytest.raises(ValueError, match='pin mismatch'):
        export_restructure(acceptances={'R': bundle}, output=tmp_path / 'lhf')
    with pytest.raises(ValueError, match='outside'):
        export_restructure(acceptances={'R': bundle}, output=bundle / 'lhf')


def test_cli_export_restructure(accepted, tmp_path):
    from lean_constellation.lhf.__main__ import main
    repo, receipt, bundle, _ = accepted
    seal_restructure_acceptance(repo_root=repo, receipt_path=receipt, output=bundle)
    assert main(['export-restructure', '--acceptance', f'R={bundle}', '--output', str(tmp_path / 'lhf')]) == 0


@pytest.mark.parametrize('location', ['main_exports', 'interface_seeds', 'node_exports', 'stage'])
def test_seal_checks_plan_refs_and_stage(accepted, location):
    repo, receipt, bundle, _ = accepted
    meta = repo / '.lean_constellation/restructure'
    if location == 'stage':
        path = meta / 'content/Main__C.json'
        value = json.loads(path.read_text()); value['stage'] = 'declared'
    else:
        path = meta / 'plan.json'
        value = json.loads(path.read_text())
        refs = [{'repo': 'R', 'node': 'Main.C', 'name': 'missing'}]
        if location == 'node_exports':
            value['nodes']['Main.C']['exports'] = refs
        else:
            value[location] = refs
    write(path, value)
    with pytest.raises(ValueError):
        seal_restructure_acceptance(repo_root=repo, receipt_path=receipt, output=bundle)
    assert not bundle.exists()
