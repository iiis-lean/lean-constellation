from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from lean_constellation.lhf import load_workspace, validate_workspace
from lean_constellation.lhf.git_source import GitTree
from lean_constellation.lhf.lc_export import export_release, source_body
from lean_constellation.lhf.models import Declaration, Node, Workspace
from lean_constellation.repo_path_policy import classify_repo_path
from pathlib import PurePosixPath


CODE = ('-- lean-constellation: managed-imports-begin\nimport Demo.Main.Leaf.Prelude\n'
        '-- lean-constellation: managed-imports-end\n\n'
        '-- lean-constellation: declaration-source-begin\n'
        '/--\n# lean-constellation target\nA useful declaration.\n-/\n'
        'def helper : Nat := 1\ndef result : Nat := helper\n')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def save(root, path, value):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(value), encoding='utf-8')


def state(root):
    return (git(root, 'rev-parse', 'HEAD'), git(root, 'show-ref'), git(root, 'status', '--porcelain=v1', '--untracked-files=all'))


def commit(root, label='fixture'):
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', label)
    return git(root, 'rev-parse', 'HEAD')


def fixture(root: Path, *, key='Demo', providers=(), deps=(), old_anchor=False, resource=False):
    root.mkdir()
    git(root, 'init', '-q')
    code = CODE.replace('Demo.', key + '.')
    stmt = {'formal': {'code': code, 'check': {'status': 'passed'}},
            'nl': {'text': 'One.', 'origin': [{'kind': 'source', 'source_path': 'text.md', 'start_line': 1, 'end_line': 1}]},
            'deps': list(deps)}
    if resource:
        stmt['nl']['origin'].append({'kind': 'resource', 'resource_key': 'book', 'start_locator': 'Lemma 1'})
        save(root, '.lean_constellation/resources/items/book/resource.json', {'canonical_entry': 'chapter.md'})
        (root / '.lean_constellation/resources/items/book/chapter.md').write_text('A chapter')
    module = key + '.Main.Leaf.Defs.result'
    base = '.lean_constellation/nodes/leaf/decl_graph/decls/result'
    save(root, base + '/decl.json', {'name': 'result', 'node_path': 'Main.Leaf', 'kind': 'definition', 'summary': 'Mathematical summary', 'module': module, 'public': True, 'current_revision': 99})
    save(root, base + '/revisions/1.json', {'revision': 1, 'status': 'committed', 'state': 'declared', 'lean_decl_name': 'result', 'statement': stmt, 'change': {'summary': 'DO NOT EXPORT'}})
    selected = 2 if old_anchor else 1
    if old_anchor:
        save(root, base + '/revisions/2.json', {'revision': 2, 'status': 'committed', 'state': 'declared', 'lean_decl_name': 'result', 'statement': stmt})
    source = root / (module.replace('.', '/') + '.lean')
    source.parent.mkdir(parents=True)
    source.write_text(code.replace('A useful declaration.', 'Updated projection.'))
    for nid, path, kind in [('main', 'Main', 'scope'), ('leaf', 'Main.Leaf', 'content')]:
        save(root, f'.lean_constellation/nodes/{nid}/node.json', {'node_id': nid, 'path': path, 'kind': kind, 'current_contract_version': 99})
        save(root, f'.lean_constellation/nodes/{nid}/contracts/1.json', {
            'version': 1, 'status': 'committed', 'contract_kind': kind,
            'goal': 'Goal', 'boundary': 'Boundary', 'summary': 'CLOSEOUT NOT MATHEMATICS',
            'exports': [{'node': 'Main.Leaf', 'name': 'result', 'revision': 1}] if kind == 'scope' else [],
            'decl_graph_head': {'result': selected} if kind == 'content' else {},
            'deps': [{'target': {'repo': p[0], 'node': 'Main'}} for p in providers]})
        folder = root / key / path.replace('.', '/')
        folder.mkdir(parents=True, exist_ok=True)
        for name in ['Prelude.lean', 'Interfaces.lean']:
            (folder / name).write_text('-- projection\n')
    save(root, '.lean_constellation/repo_format.json', {'repo_format': 'native'})
    save(root, '.lean_constellation/repo_publication.json', {'latest_release_id': 'r1'})
    save(root, '.lean_constellation/releases/r1.json', {'release_id': 'r1', 'node_contract_versions': {'main': 1, 'leaf': 1}})
    save(root, '.lean_constellation/source/config.json', {})
    (root / '.lean_constellation/source/text.md').write_text('Original text\n')
    req = ''.join(f'\n[[require]]\nname = "{n}"\ngit = "https://example.com/{n}"\nrev = "{c}"\n' for n,c in providers)
    (root / 'lakefile.toml').write_text(f'name = "{key}"\n[[lean_lib]]\nname = "{key}"\n' + req)
    save(root, 'lake-manifest.json', {'packages': [{'name': n, 'type': 'git', 'url': f'https://example.com/{n}', 'rev': c} for n,c in providers]})
    (root / 'lean-toolchain').write_text('leanprover/lean4:test\n')
    (root / (key + '.lean')).write_text(f'import {key}.Main.Interfaces\n')
    c = commit(root)
    git(root, 'update-ref', 'refs/lean-constellation/releases/r1', c)
    return c


def test_fixed_head_roundtrip_source_and_resource(tmp_path):
    root = tmp_path / 'source'
    c = fixture(root, old_anchor=True, resource=True)
    # Make HEAD and worktree disagree with the selected release.
    (root / 'lean-toolchain').write_text('later\n')
    commit(root, 'later')
    (root / 'lean-toolchain').write_text('dirty\n')
    before = state(root)
    dest = tmp_path / 'out'
    report = export_release(repo_paths={'Demo': root}, release_id='r1', output=dest)
    data = load_workspace(dest)
    decl = data.repos['Demo'].declarations['Main.Leaf']['result']
    assert decl.summary == 'Mathematical summary'
    assert data.repos['Demo'].nodes['Main'].summary is None
    assert data.repos['Demo'].nodes['Main.Leaf'].exports[0].name == 'result'
    assert decl.statement.nl.origin[1].source_path == '.lhf/materials/resources/book/chapter.md'
    assert decl.statement.nl.origin[1].start_locator == 'Lemma 1'
    assert report['repos']['Demo']['commit'] == c
    assert (dest / 'Demo/lean-toolchain').read_bytes() == GitTree(root, c).bytes('lean-toolchain')
    assert not (dest / 'Demo/.lean_constellation').exists()
    payload = (dest / 'Demo/.lhf/nodes/Main/Leaf/decls/result.json').read_text()
    assert 'check' not in payload and 'DO NOT EXPORT' not in payload and 'revision' not in payload
    assert state(root) == before
    assert not list(tmp_path.glob('.out.staging-*'))


def test_recursive_exact_pin_and_no_latest(tmp_path):
    provider = tmp_path / 'provider'
    pin = fixture(provider, key='Provider')
    (provider / 'later.txt').write_text('not in release')
    commit(provider)
    root = tmp_path / 'source'
    deps = [{'kind': 'repo_decl', 'ref': {'repo': 'Provider', 'node': 'Main.Leaf', 'name': 'result', 'revision': 1}}]
    fixture(root, providers=[('Provider', pin)], deps=deps)
    before = [state(p) for p in (root, provider)]
    result = export_release(repo_paths={'Demo': root, 'Provider': provider}, main_repo='Demo', release_id='r1', output=tmp_path / 'out')
    assert result['repos']['Provider']['commit'] == pin
    assert not (tmp_path / 'out/Provider/later.txt').exists()
    assert [state(p) for p in (root, provider)] == before


def test_explicit_commit_without_ref_and_reject_later_overlay(tmp_path):
    root = tmp_path / 'source'
    pin = fixture(root)
    git(root, 'update-ref', '-d', 'refs/lean-constellation/releases/r1')
    export_release(repo_paths={'Demo': root}, release_id='r1', release_commit=pin, output=tmp_path / 'ok')
    (root / 'README.md').write_text('later')
    later = commit(root)
    before = state(root)
    with pytest.raises(ValueError, match='first introduction'):
        export_release(repo_paths={'Demo': root}, release_id='r1', release_commit=later, output=tmp_path / 'bad')
    assert state(root) == before and not (tmp_path / 'bad').exists()


def test_missing_provider_no_partial_output(tmp_path):
    root = tmp_path / 'source'
    fixture(root, providers=[('Provider', 'a' * 40)])
    before = state(root)
    with pytest.raises(ValueError, match='missing local LC provider'):
        export_release(repo_paths={'Demo': root}, release_id='r1', output=tmp_path / 'out')
    assert not (tmp_path / 'out').exists() and state(root) == before


def test_conflicting_recursive_pins(tmp_path):
    provider = tmp_path / 'P'
    first = fixture(provider, key='P')
    (provider / 'extra').write_text('later')
    second = commit(provider)
    middle = tmp_path / 'M'
    mid = fixture(middle, key='M', providers=[('P', second)])
    root = tmp_path / 'D'
    fixture(root, providers=[('P', first), ('M', mid)])
    with pytest.raises(ValueError, match='conflicting fixed commits'):
        export_release(repo_paths={'Demo': root, 'P': provider, 'M': middle}, main_repo='Demo', release_id='r1', output=tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def amend_release(root):
    new = commit(root)
    git(root, 'update-ref', 'refs/lean-constellation/releases/r1', new)


def test_old_anchor_strict_comparison_not_projection_comparison(tmp_path):
    root = tmp_path / 'source'
    fixture(root, old_anchor=True)
    path = '.lean_constellation/nodes/leaf/decl_graph/decls/result/revisions/1.json'
    raw = json.loads((root / path).read_text())
    raw['statement']['formal']['code'] = raw['statement']['formal']['code'].replace('A useful declaration.', 'Changed documentation')
    save(root, path, raw)
    amend_release(root)
    with pytest.raises(ValueError, match='incompatible declared API'):
        export_release(repo_paths={'Demo': root}, release_id='r1', output=tmp_path / 'out')


def test_capture_helper_change_fails(tmp_path):
    root = tmp_path / 'source'
    fixture(root)
    source = root / 'Demo/Main/Leaf/Defs/result.lean'
    source.write_text(source.read_text().replace(':= 1', ':= 2'))
    amend_release(root)
    with pytest.raises(ValueError, match='capture/source mismatch'):
        export_release(repo_paths={'Demo': root}, release_id='r1', output=tmp_path / 'out')


def test_existing_destination_untouched_and_symlink_cleanup(tmp_path):
    root = tmp_path / 'source'
    fixture(root)
    dest = tmp_path / 'out'
    dest.mkdir()
    (dest / 'keep').write_text('untouched')
    before = state(root)
    with pytest.raises(ValueError, match='already exists'):
        export_release(repo_paths={'Demo': root}, release_id='r1', output=dest)
    assert (dest / 'keep').read_text() == 'untouched' and state(root) == before
    (root / 'bad-link').symlink_to('/etc/passwd')
    amend_release(root)
    before = state(root)
    with pytest.raises(ValueError, match='unsupported Git entry'):
        export_release(repo_paths={'Demo': root}, release_id='r1', output=tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists() and not list(tmp_path.glob('.bad.staging-*'))
    assert state(root) == before


def test_loader_rejects_escape_and_symlink(tmp_path):
    root = tmp_path / 'source'
    fixture(root)
    dest = tmp_path / 'out'
    export_release(repo_paths={'Demo': root}, release_id='r1', output=dest)
    target = dest / 'Demo/Demo/Main/Leaf/Defs/result.lean'
    target.unlink()
    target.symlink_to(root / 'Demo/Main/Leaf/Defs/result.lean')
    with pytest.raises(ValueError, match='symlink'):
        load_workspace(dest)
    with pytest.raises(ValidationError):
        Workspace(main_repo='Demo', repos={'Demo': '../source'})


def test_structural_ownership_and_proved_content(tmp_path):
    root = tmp_path / 'source'
    fixture(root)
    dest = tmp_path / 'out'
    export_release(repo_paths={'Demo': root}, release_id='r1', output=dest)
    data = load_workspace(dest)
    raw = data.repos['Demo'].declarations['Main.Leaf']['result'].model_dump()
    raw['state'] = 'proved'
    with pytest.raises(ValidationError, match='proof.fl'):
        Declaration.model_validate(raw)
    with pytest.raises(ValidationError, match='scope cannot own'):
        Node(kind='scope', goal='', boundary='', declarations=['x'])
    data.repos['Demo'].nodes['Main'].children = []
    with pytest.raises(ValueError, match='unreachable'):
        validate_workspace(data)


def test_projection_parser_preserves_helpers_and_rejects_malformed():
    assert 'def helper' in source_body(CODE)
    assert 'A useful' not in source_body(CODE)
    with pytest.raises(ValueError, match='malformed'):
        source_body(CODE.replace('-- lean-constellation: managed-imports-end', ''))
    with pytest.raises(ValueError, match='non-import'):
        source_body(CODE.replace('import Demo.Main.Leaf.Prelude', 'def stolen := 1'))


def test_path_policy_and_runtime_free_import():
    for path in ['.lhf/repo.json', '.lean_constellation/restructure/plan.json']:
        result = classify_repo_path(PurePosixPath(path))
        assert not result.release_eligible and not result.publication_eligible and not result.semantic_digest_eligible
    result = subprocess.run([sys.executable, '-c',
        'import sys; import lean_constellation.lhf.lc_export; assert "lean_constellation.services" not in sys.modules; assert not any(k.startswith("agent_runtime") for k in sys.modules)'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_missing_material_and_pin_mismatch_fail_before_output(tmp_path):
    root = tmp_path / 'source'
    fixture(root)
    (root / '.lean_constellation/source/text.md').unlink()
    amend_release(root)
    with pytest.raises(ValueError, match='missing .*text.md'):
        export_release(repo_paths={'Demo': root}, release_id='r1', output=tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()
    provider = tmp_path / 'provider'
    pin = fixture(provider, key='P')
    consumer = tmp_path / 'consumer'
    fixture(consumer, providers=[('P', pin)])
    (consumer / 'lakefile.toml').write_text((consumer / 'lakefile.toml').read_text().replace(pin, '0' * 40))
    amend_release(consumer)
    with pytest.raises(ValueError, match='pin mismatch'):
        export_release(repo_paths={'Demo': consumer, 'P': provider}, main_repo='Demo', release_id='r1', output=tmp_path / 'bad')


def test_reader_rejects_orphan_and_dangling_metadata(tmp_path):
    root = tmp_path / 'source'
    fixture(root)
    dest = tmp_path / 'out'
    export_release(repo_paths={'Demo': root}, release_id='r1', output=dest)
    save(dest, 'Demo/.lhf/nodes/Main/Unused/node.json', {'kind': 'content', 'goal': '', 'boundary': ''})
    with pytest.raises(ValueError, match='unindexed'):
        load_workspace(dest)
    (dest / 'Demo/.lhf/nodes/Main/Unused/node.json').unlink()
    path = dest / 'Demo/.lhf/nodes/Main/node.json'
    raw = json.loads(path.read_text())
    raw['exports'][0]['name'] = 'missing'
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='unresolved declaration'):
        load_workspace(dest)


def test_cross_repo_dependency_requires_main_export(tmp_path):
    provider = tmp_path / 'provider'
    fixture(provider, key='P')
    path = '.lean_constellation/nodes/main/contracts/1.json'
    raw = json.loads((provider / path).read_text())
    raw['exports'] = []
    save(provider, path, raw)
    amend_release(provider)
    pin = git(provider, 'rev-parse', 'HEAD')
    consumer = tmp_path / 'consumer'
    fixture(consumer, providers=[('P', pin)], deps=[{'kind': 'repo_decl', 'ref': {'repo': 'P', 'node': 'Main.Leaf', 'name': 'result', 'revision': 1}}])
    with pytest.raises(ValueError, match='not a Main export'):
        export_release(repo_paths={'Demo': consumer, 'P': provider}, main_repo='Demo', release_id='r1', output=tmp_path / 'out')


def test_manual_writer_refuses_partial_metadata_without_overwrite(tmp_path):
    from lean_constellation.lhf import write_metadata
    import shutil

    root = tmp_path / 'source'
    fixture(root)
    dest = tmp_path / 'out'
    export_release(repo_paths={'Demo': root}, release_id='r1', output=dest)
    data = load_workspace(dest)
    (dest / '.lhf/workspace.json').unlink()
    (dest / 'Demo/.lhf/repo.json').unlink()
    existing = (dest / 'Demo/.lhf/nodes/Main/node.json').read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        write_metadata(dest, data)
    assert not (dest / '.lhf/workspace.json').exists()
    assert (dest / 'Demo/.lhf/nodes/Main/node.json').read_bytes() == existing
    shutil.rmtree(dest / 'Demo/.lhf/nodes')
    write_metadata(dest, data)
    assert load_workspace(dest) == data


def test_inherited_lake_pin_conflict(tmp_path):
    provider = tmp_path / 'P'
    p_commit = fixture(provider, key='P')
    middle = tmp_path / 'M'
    m_commit = fixture(middle, key='M', providers=[('P', p_commit)])
    consumer = tmp_path / 'source'
    fixture(consumer, providers=[('M', m_commit)])
    path = 'lake-manifest.json'
    manifest = json.loads((consumer / path).read_text())
    manifest['packages'].append({'name': 'P', 'type': 'git', 'rev': '0' * 40, 'inherited': True})
    save(consumer, path, manifest)
    amend_release(consumer)
    with pytest.raises(ValueError, match='inherited Lake pin conflicts'):
        export_release(repo_paths={'Demo': consumer, 'M': middle, 'P': provider}, main_repo='Demo', release_id='r1', output=tmp_path / 'out')


def test_release_ref_names_match_exactly(tmp_path):
    root = tmp_path / 'source'
    c = fixture(root)
    git(root, 'update-ref', 'refs/lean-constellation/releases/r10', c)
    assert GitTree(root, c).release('r1')['release_id'] == 'r1'


def test_path_filter_selects_unreferenced_material_without_prefix_leaks(tmp_path):
    root = tmp_path / 'source'
    fixture(root, resource=True)
    corpus = root / '.lean_constellation/source'
    (corpus / 'sections').mkdir()
    (corpus / 'sections/main.tex').write_text('Paper')
    (corpus / 'sections-extra').mkdir()
    (corpus / 'sections-extra/other.tex').write_text('Not selected')
    (corpus / 'main.tex').write_text('Entry')
    (corpus / 'main.tex.bak').write_text('Not selected')
    amend_release(root)
    before = state(root)
    dest = tmp_path / 'out'
    export_release(repo_paths={'Demo': root}, release_id='r1', output=dest,
                   source_paths=['main.tex', 'sections/'])
    materials = dest / 'Demo/.lhf/materials'
    assert {p.relative_to(materials).as_posix() for p in materials.rglob('*') if p.is_file()} == {
        'source/main.tex', 'source/sections/main.tex'}
    decl = load_workspace(dest).repos['Demo'].declarations['Main.Leaf']['result']
    assert decl.statement.nl.origin == []
    assert decl.statement.nl.text == 'One.' and decl.statement.fl == CODE
    assert state(root) == before


@pytest.mark.parametrize('paths, expected', [(['text.md'], 1), ([], 0)])
def test_source_filter_retains_only_selected_origins(tmp_path, paths, expected):
    root = tmp_path / 'source'
    fixture(root, resource=True)
    dest = tmp_path / 'out'
    export_release(repo_paths={'Demo': root}, release_id='r1', output=dest, source_paths=paths)
    decl = load_workspace(dest).repos['Demo'].declarations['Main.Leaf']['result']
    assert len(decl.statement.nl.origin) == expected
    if expected:
        origin = decl.statement.nl.origin[0]
        assert origin.source_path == '.lhf/materials/source/text.md'
        assert origin.start_line == origin.end_line == 1
    else:
        assert not (dest / 'Demo/.lhf/materials').exists()


@pytest.mark.parametrize('paths, expected', [(['book/chapter.md'], 1), (['book/resource.json'], 0)])
def test_resource_filter_requires_canonical_file_selected(tmp_path, paths, expected):
    root = tmp_path / 'source'
    fixture(root, resource=True)
    dest = tmp_path / 'out'
    export_release(repo_paths={'Demo': root}, release_id='r1', output=dest, resource_paths=paths)
    decl = load_workspace(dest).repos['Demo'].declarations['Main.Leaf']['result']
    assert len(decl.statement.nl.origin) == expected
    if expected:
        assert decl.statement.nl.origin[0].start_locator == 'Lemma 1'
    assert not (dest / 'Demo/.lhf/materials/source').exists()


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'sections//file', 'not-present'])
def test_invalid_or_unmatched_material_selection_is_not_silent(tmp_path, path):
    root = tmp_path / 'source'
    fixture(root)
    before = state(root)
    with pytest.raises(ValueError):
        export_release(repo_paths={'Demo': root}, release_id='r1', output=tmp_path / 'out', source_paths=[path])
    assert state(root) == before and not (tmp_path / 'out').exists()


def test_default_includes_unreferenced_material_and_no_sources_skips_missing_origins(tmp_path):
    root = tmp_path / 'source'
    fixture(root)
    material = '.lean_constellation/resources/items/unused/chapter.md'
    (root / material).parent.mkdir(parents=True)
    (root / material).write_text('Unreferenced resource')
    amend_release(root)
    dest = tmp_path / 'all'
    export_release(repo_paths={'Demo': root}, release_id='r1', output=dest)
    assert (dest / 'Demo/.lhf/materials/resources/unused/chapter.md').read_text() == 'Unreferenced resource'
    (root / '.lean_constellation/source/text.md').unlink()
    amend_release(root)
    export_release(repo_paths={'Demo': root}, release_id='r1', output=tmp_path / 'none', source_paths=[])
    assert not (tmp_path / 'none/Demo/.lhf/materials').exists()


def test_filter_cli(tmp_path, capsys):
    from lean_constellation.lhf.__main__ import main
    root = tmp_path / 'source'
    fixture(root)
    args = ['export', '--repo', f'Demo={root}', '--release-id', 'r1', '--output', str(tmp_path / 'out')]
    assert main(args + ['--source-path', 'text.md', '--no-sources']) == 1
    assert 'cannot be combined' in capsys.readouterr().err
    assert main(args + ['--no-sources']) == 0
    assert load_workspace(tmp_path / 'out').repos['Demo'].declarations['Main.Leaf']['result'].statement.nl.origin == []


def test_material_selection_is_applied_across_pinned_closure(tmp_path):
    provider = tmp_path / 'P'
    pin = fixture(provider, key='P')
    consumer = tmp_path / 'source'
    fixture(consumer, providers=[('P', pin)])
    (consumer / '.lean_constellation/source/paper.tex').write_text('Only in consumer')
    amend_release(consumer)
    dest = tmp_path / 'out'
    export_release(repo_paths={'Demo': consumer, 'P': provider}, main_repo='Demo',
                   release_id='r1', output=dest, source_paths=['paper.tex'])
    data = load_workspace(dest)
    assert set(data.repos) == {'Demo', 'P'}
    assert (dest / 'Demo/.lhf/materials/source/paper.tex').is_file()
    assert not (dest / 'P/.lhf/materials').exists()
    assert all(not d.statement.nl.origin for r in data.repos.values()
               for ds in r.declarations.values() for d in ds.values())
