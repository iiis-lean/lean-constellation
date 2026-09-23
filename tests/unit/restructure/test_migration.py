import hashlib
import json
from pathlib import Path

import pytest

from lean_constellation.services.restructure.migration import migrate_workspace


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path):
    source = tmp_path / 'v1'
    repo = source / 'R'
    meta = repo / '.lean_constellation/restructure'
    file = 'R/Main/A/Theorems/result.lean'
    declared = 'theorem result : True :=\n-- LC proof begin\nby sorry\n-- LC proof end\n'
    final = declared.replace('sorry', 'trivial')
    plan = dict(repo_key='R', directory='R', module_root='R', goal='true', nodes={
        'Main': dict(path='Main', kind='main', parent=None, summary='root', goal='true', scope='Main', module='R.Main'),
        'Main.A': dict(path='Main.A', kind='content', parent='Main', summary='truth', goal='true', scope='Main', module='R.Main.A')})
    write(source / '.lean_constellation/restructure/workspace.json', dict(run_id='old', workspace_root=str(source), main_repo='R', repos={'R':dict(key='R',directory='R',module_root='R',goal='true',plan=plan)}))
    write(meta / 'plan.json', plan)
    write(meta / 'content/Main__A.json', dict(repo_key='R', node_path='Main.A', stage='proved',decls={'result':dict(name='result', lean_name='result',kind='theorem',summary='true',file=file,status='proved',statement_nl='true',proof_nl='old',statement_text='broken',proof_text='wrong',origins=[],dependencies=[])}))
    for operation, stage, code in [('d','declared',declared),('f','final',final)]:
        view=meta/'build_views'/operation/file;view.parent.mkdir(parents=True);view.write_text(code)
        write(meta/'builds'/f'{operation}.json',dict(success=True,stage=stage,operation_id=operation,repo_key='R',files={file:hashlib.sha256(code.encode()).hexdigest()}))
    (repo/file).parent.mkdir(parents=True);(repo/file).write_text(final)
    patch=tmp_path/'review.json'
    write(patch,dict(nodes={n:dict(boundary='Truth only',evidence='final file') for n in plan['nodes']},declarations={'Main.A':{'result':dict(statement_nl='True holds',proof_nl='Apply True.intro',statement_origins=[],proof_origins=[],statement_dependencies=[],proof_dependencies=[],evidence='final result theorem')}}))
    return dict(source=source,output=tmp_path/'v2',review=patch,declared_builds={'R':'d'},final_builds={'R':'f'}),file,declared,final


def test_migration_uses_exact_stage_snapshots_and_preserves_legacy(tmp_path):
    args,file,declared,final=fixture(tmp_path)
    report=migrate_workspace(**args)
    meta=args['output']/'R/.lean_constellation/restructure'
    work=json.loads((meta/'content/Main__A.json').read_text())
    decl=work['decls']['result']
    assert decl['statement']['formal']['code']==declared
    assert decl['proof']['formal']['code']==final
    assert decl['proof']['nl']['text']=='Apply True.intro'
    assert 'proof_text' not in decl
    assert decl['proof_review_digest']
    assert json.loads((meta/'migration_v1/content/Main__A.json').read_text())['decls']['result']['proof_text']=='wrong'
    assert (args['source']/'R'/file).read_text()==final
    assert report['repos']['R']['declarations']==1
    with pytest.raises(ValueError,match='new directory'):
        migrate_workspace(**args)


@pytest.mark.parametrize('failure',['missing_review','unknown_role','source_changed','snapshot_changed','symlink'])
def test_failed_migration_leaves_no_partial_output(tmp_path,failure):
    args,file,_,_=fixture(tmp_path)
    meta=args['source']/'R/.lean_constellation/restructure'
    if failure=='missing_review':
        data=json.loads(args['review'].read_text());data['nodes'].pop('Main.A');write(args['review'],data)
    elif failure=='unknown_role':
        p=meta/'content/Main__A.json';data=json.loads(p.read_text());data['decls']['result']['dependencies']=[dict(role='primary',ref={'repo':'R','name':'result'})];write(p,data)
    elif failure=='source_changed':
        (args['source']/'R'/file).write_text('changed')
    elif failure=='snapshot_changed':
        (meta/'build_views/d'/file).write_text('changed')
    else:
        (meta/'inputs').mkdir();(meta/'inputs/link').symlink_to(args['review'])
    with pytest.raises(ValueError):
        migrate_workspace(**args)
    assert not args['output'].exists()
    assert not list(tmp_path.glob('v2.migration-*'))


def test_definition_formal_uses_final_complete_file_without_proof_section(tmp_path):
    args,file,_,_=fixture(tmp_path)
    meta=args['source']/'R/.lean_constellation/restructure'
    path=meta/'content/Main__A.json';work=json.loads(path.read_text())
    work['decls']['result'].update(kind='def',status='declared')
    write(path,work)
    patch=json.loads(args['review'].read_text());d=patch['declarations']['Main.A']['result'];d['proof_nl']=None
    write(args['review'],patch)
    old='-- LC imports begin\n-- LC imports end\ndef result : Nat := 1\n'
    new='-- LC imports begin\nimport Mathlib\n-- LC imports end\ndef result : Nat := 1\n'
    for operation,code in [('d',old),('f',new)]:
        (meta/'build_views'/operation/file).write_text(code)
        p=meta/'builds'/f'{operation}.json';r=json.loads(p.read_text());r['files'][file]=hashlib.sha256(code.encode()).hexdigest();write(p,r)
    (args['source']/'R'/file).write_text(new)
    migrate_workspace(**args)
    result=json.loads((args['output']/'R/.lean_constellation/restructure/content/Main__A.json').read_text())['decls']['result']
    assert result['statement']['formal']['code']==new
    assert result['proof'] is None


def test_stale_review_rejected(tmp_path):
    args,_,_,_=fixture(tmp_path)
    patch=json.loads(args['review'].read_text());patch.update(source_root=str(args['source']/'R'),source_metadata_files={'.lean_constellation/restructure/plan.json':'wrong'})
    write(args['review'],patch)
    with pytest.raises(ValueError,match='review is stale'):
        migrate_workspace(**args)
    assert not args['output'].exists()
