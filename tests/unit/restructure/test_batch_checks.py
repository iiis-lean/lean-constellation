from lean_constellation.domain.restructure import SectionInput, SectionNL
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from lean_constellation.services.restructure.checks import check_files, diagnostic_summary, check_content
from tests.unit.restructure.test_build_regressions import _fixture
from tests.unit.restructure.test_repo_repair import candidate
from lean_constellation.domain.restructure import RestructureStage


def test_batch_updates_dependencies_reuses_artifacts_and_formal_build(tmp_path):
    service, repo = _fixture(tmp_path)
    a = repo / 'Pkg/Main/A.lean'
    b = repo / 'Pkg/Main/B.lean'
    a.write_text('def number : Nat := 1\n')
    b.write_text('import Pkg.Main.A\ndef successor : Nat := number + 1\n')
    result = check_files(service, 'repo', ['Pkg/Main/B.lean'])
    assert result['passed'], result
    cache = service.store.repo_metadata_root('repo') / 'build_work/.lake/build/lib/lean/Pkg/Main'
    stamp = (cache / 'A.olean').stat().st_mtime_ns
    assert check_files(service, 'repo', ['Pkg/Main/B.lean'])['passed']
    assert (cache / 'A.olean').stat().st_mtime_ns == stamp
    assert service.build_repo('repo', stage=RestructureStage.DECLARED).receipt.success
    assert (cache / 'A.olean').stat().st_mtime_ns == stamp
    a.write_text('def number : String := "changed"\n')
    result = check_files(service, 'repo', ['Pkg/Main/B.lean'])
    assert not result['passed']
    assert result['errors']
    assert (cache / 'A.olean').stat().st_mtime_ns != stamp
    assert 'report_id' in result


def test_changed_source_cannot_be_registered(tmp_path):
    service, _ = candidate(tmp_path)
    source = service.store.repo_root('repo') / service.content.load('repo', 'Main.A')[0].decls['value'].file
    def mutate(*args, **kwargs):
        source.write_text(source.read_text() + '\n-- concurrent change\n')
        return SimpleNamespace(returncode=0)
    before = service.content.load('repo', 'Main.A')[0].decls['value'].file_digest
    with patch('lean_constellation.services.restructure.checks.subprocess.run', side_effect=mutate):
        result = check_content(service, 'repo', ['Main.A'], 'declared', allow_declared_repair=True)
    assert not result['passed']
    assert service.content.load('repo', 'Main.A')[0].decls['value'].file_digest == before


def test_check_registers_without_agent_capture(tmp_path):
    service, _ = candidate(tmp_path)
    source = service.content.read_decl_file('repo', 'Main.A', 'value').replace('"wrong"', '1')
    service.content.edit_decl_file('repo', 'Main.A', 'value', source)
    service.content.set_sections('repo', 'Main.A', 'value', statement=SectionInput(nl=SectionNL(text='one')))
    with patch('lean_constellation.services.restructure.checks.subprocess.run', return_value=SimpleNamespace(returncode=0)):
        result = check_content(service, 'repo', ['Main.A'], 'declared', allow_declared_repair=True)
    assert result['passed'], result
    assert not service.content.check_submission('repo', 'Main.A', stage=RestructureStage.DECLARED, allow_declared_repair=True)


def test_diagnostics_preserve_error_context_drop_progress():
    result = diagnostic_summary('✔ Built A\nwarning: allowed sorry\nerror: B.lean:3:2: type mismatch\n  actual Nat\nexpected String\n⚠ Replayed C\nwarning: sorry\n')
    assert result == ['error: B.lean:3:2: type mismatch\n  actual Nat\nexpected String']


def test_agent_surface_has_no_capture_or_digest():
    from lean_constellation.tools.restructure import build_tool_specs, RestructureEditDeclFileArgs
    names = {s.name for s in build_tool_specs()}
    assert 'capture_restructure_decl' not in names
    assert 'expected_file_digest' not in RestructureEditDeclFileArgs.model_fields
    assert {'check_restructure_files', 'read_restructure_build_report'} <= names


def test_batch_rejects_outside_and_private_files(tmp_path):
    import pytest
    service, repo = _fixture(tmp_path)
    for path in ['../escape.lean', str(repo / 'Pkg.lean'), '.lean_constellation/test.lean']:
        with pytest.raises(ValueError):
            check_files(service, 'repo', [path])


def theorem_candidate(tmp_path):
    from tests.unit.restructure.test_supervisor import _workspace
    from lean_constellation.domain.restructure import DeclKind, DeclStatus, Origin, SourceRef
    service, _ = _workspace(tmp_path, node_paths={'repo': ['Main.A', 'Main.B']})
    for node, name in [('Main.A', 'first'), ('Main.B', 'second')]:
        service.content.create_decl('repo', 'Result', node, name=name, lean_name=name,
                                    kind=DeclKind.THEOREM, summary='true')
        service.content.set_sections('repo', node, name, statement=SectionInput(nl=SectionNL(text='true', origins=[Origin(source_refs=[SourceRef(corpus='tex', path='paper.tex')])])))
        service.content.capture('repo', node, name, status=DeclStatus.DECLARED)
        source = service.content.read_decl_file('repo', node, name)
        service.content.edit_decl_file('repo', node, name, source.replace('sorry', 'trivial'))
    service.content.set_sections('repo', 'Main.A', 'first', proof=SectionInput(nl=SectionNL(text='True.intro', origins=[])))
    return service


def test_missing_proof_explanation_rejects_whole_batch_without_registration(tmp_path):
    service = theorem_candidate(tmp_path)
    before = {n: service.content.load('repo', n) for n in ['Main.A', 'Main.B']}
    with patch('lean_constellation.services.restructure.checks.subprocess.run', return_value=SimpleNamespace(returncode=0)):
        result = check_content(service, 'repo', ['Main.A', 'Main.B'], 'proved')
    assert not result['passed']
    assert any('second: proof_nl' in e for e in result['errors'])
    for node, original in before.items():
        assert service.content.load('repo', node) == original
    service.content.set_sections('repo', 'Main.B', 'second', proof=SectionInput(nl=SectionNL(text='True.intro', origins=[])))
    with patch('lean_constellation.services.restructure.checks.subprocess.run', return_value=SimpleNamespace(returncode=0)):
        result = check_content(service, 'repo', ['Main.A', 'Main.B'], 'proved')
    assert result['passed'], result
    assert service.content.load('repo', 'Main.B')[0].decls['second'].status == 'proved'


def test_legacy_capture_cannot_persist_invalid_proved_record(tmp_path):
    import pytest
    from lean_constellation.domain.restructure import DeclStatus
    service = theorem_candidate(tmp_path)
    before = service.content.load('repo', 'Main.B')
    with pytest.raises(ValueError, match='proof_nl'):
        service.content.capture('repo', 'Main.B', 'second', status=DeclStatus.PROVED)
    assert service.content.load('repo', 'Main.B') == before


def test_batch_captures_whole_file_preserves_statement_and_requires_metadata_review(tmp_path):
    service = theorem_candidate(tmp_path)
    service.content.set_sections('repo', 'Main.B', 'second', proof=SectionInput(nl=SectionNL(text='True.intro')))
    original = service.content.load('repo', 'Main.A')[0].decls['first'].statement.formal.code
    with patch('lean_constellation.services.restructure.checks.subprocess.run', return_value=SimpleNamespace(returncode=0)):
        assert check_content(service, 'repo', ['Main.A', 'Main.B'], 'proved')['passed']
        work, _ = service.content.load('repo', 'Main.A')
        assert work.decls['first'].statement.formal.code == original
        assert work.decls['first'].proof.formal.code == service.content.read_decl_file('repo', 'Main.A', 'first')
        source = service.content.read_decl_file('repo', 'Main.A', 'first').replace('trivial', 'exact True.intro')
        service.content.edit_decl_file('repo', 'Main.A', 'first', source)
        before = service.content.load('repo', 'Main.A')
        failed = check_content(service, 'repo', ['Main.A'], 'proved')
        assert not failed['passed'] and any('review proof' in error for error in failed['errors'])
        assert service.content.load('repo', 'Main.A') == before
        service.content.set_sections('repo', 'Main.A', 'first', proof=SectionInput(nl=SectionNL(text='True.intro')))
        assert check_content(service, 'repo', ['Main.A'], 'proved')['passed']
        assert service.content.load('repo', 'Main.A')[0].decls['first'].proof.formal.code == service.content.read_decl_file('repo', 'Main.A', 'first')


def test_stored_formal_mismatch_cannot_pass_submission(tmp_path):
    from lean_constellation.domain.restructure import FormalSnapshot
    service = theorem_candidate(tmp_path)
    service.content.set_sections('repo', 'Main.B', 'second', proof=SectionInput(nl=SectionNL(text='True.intro')))
    with patch('lean_constellation.services.restructure.checks.subprocess.run', return_value=SimpleNamespace(returncode=0)):
        assert check_content(service, 'repo', ['Main.A', 'Main.B'], 'proved')['passed']
    work, version = service.content.load('repo', 'Main.A')
    work.decls['first'].proof.formal = FormalSnapshot(code='wrong complete file')
    service.store.save_content('repo', 'Main.A', work, expected_version=version)
    assert any('formal snapshot' in error for error in service.content.check_submission('repo', 'Main.A', stage=RestructureStage.PROVED))


def test_agent_cannot_submit_formal_or_legacy_metadata():
    import pytest
    from lean_constellation.tools.restructure import RestructureSetDeclArgs
    for fields in [{'statement_text': 'theorem copied : True'}, {'statement': {'nl': {'text': 'True'}, 'formal': {'code': 'copied'}}}, {'statement': {'deps': [{'ref': {'repo': 'r', 'name': 'a'}, 'role': 'primary'}]}}]:
        with pytest.raises(ValueError):
            RestructureSetDeclArgs(node_path='Main.A', name='first', **fields)
