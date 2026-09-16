import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lean_constellation.services.external_clients.lean_toolchain import LeanToolchainClient, ToolchainLeanCheckView


def messages(*items):
    return '\n'.join(json.dumps({'data': item}) for item in items)


@pytest.mark.parametrize('output', [
    messages('Int.target : True'),
    messages('Int.targetSuffix : True', 'theorem Int.targetSuffix : True := trivial'),
    messages('Int.target : True', 'theorem Foo.target : True := trivial'),
])
def test_requires_exact_printed_name_in_addition_to_check(output):
    signature, kind = LeanToolchainClient._parse_compiler_decl_output(output, 'Int.target')
    assert signature is None or kind is None


def test_exact_printed_header_is_accepted():
    assert LeanToolchainClient._parse_compiler_decl_output(
        messages('Int.target : True', 'theorem Int.target : True := trivial'), 'Int.target') == ('Int.target : True', 'theorem')


def test_exact_printed_header_with_universe_parameters_is_accepted():
    output = messages(
        'MvPolynomial.coeff_mul.{u, u_1} {R : Type u} : True',
        'theorem MvPolynomial.coeff_mul.{u, u_1} : True := trivial',
    )
    assert LeanToolchainClient._parse_compiler_decl_output(
        output,
        'MvPolynomial.coeff_mul',
    ) == (
        'MvPolynomial.coeff_mul.{u, u_1} {R : Type u} : True',
        'theorem',
    )


@pytest.mark.parametrize('module,name', [('Mathlib.X\n#check True', 'Int.x'), ('Mathlib.X', 'Int.x\naxiom bad : False')])
def test_unsafe_identifiers_never_reach_compiler(tmp_path, module, name):
    client = LeanToolchainClient.__new__(LeanToolchainClient)
    client.run_snippet_check = Mock()
    result = client.inspect_core_declaration(tmp_path, module=module, decl_name=name)
    assert not result.ok and result.issue_code == 'core_decl_input_invalid'
    client.run_snippet_check.assert_not_called()


@pytest.mark.parametrize('defining,exists,accepted', [('Init.Data.Int.Order', True, True),
    ('Project.Int', True, False), ('Init.Data.Int.Order', False, False)])
def test_metadata_requires_compiler_core_module_and_matching_toolchain_source(tmp_path, defining, exists, accepted):
    client = LeanToolchainClient.__new__(LeanToolchainClient)
    client.run_snippet_check = Mock(return_value=ToolchainLeanCheckView(
        ok=True, provider='lake_command', summary='checked',
        diagnostics_excerpt=messages(f'LC_CORE_DECL|Int.target|{defining}')))
    prefix = tmp_path / 'selected-toolchain'
    if exists:
        source = prefix / 'src/lean' / (defining.replace('.', '/') + '.lean')
        source.parent.mkdir(parents=True)
        source.write_text('-- core source')
    client.lake = SimpleNamespace(config=SimpleNamespace(lake_bin='lake', lean_bin='lean'),
        run_command=Mock(return_value=SimpleNamespace(ok=True, stdout_excerpt=str(prefix))))
    result = client._compiler_core_metadata(tmp_path, module='Mathlib.X', decl_name='Int.target', timeout_seconds=10)
    assert (result is not None) == accepted
    if accepted:
        assert result[0] == defining and result[2] == str(prefix)
        client.lake.run_command.assert_called_once_with(tmp_path, ['lake', 'env', 'lean', '--print-prefix'], timeout=10)


def test_import_context_gate_precedes_lean_introspection(tmp_path):
    client = LeanToolchainClient.__new__(LeanToolchainClient)
    client.run_snippet_check = Mock(return_value=ToolchainLeanCheckView(ok=False, provider='lake_command', summary='import failure'))
    client._compiler_core_metadata = Mock()
    assert not client.inspect_core_declaration(tmp_path, module='Mathlib.X', decl_name='Int.target').ok
    client._compiler_core_metadata.assert_not_called()
    assert client.run_snippet_check.call_args.kwargs['imports'] == ['Mathlib.X']


def test_local_mathlib_declaration_requires_exact_compiler_defining_module(tmp_path):
    module = 'Mathlib.Algebra.MvPolynomial.Basic'
    name = 'MvPolynomial.coeff_mul'
    source = tmp_path / '.lake/packages/mathlib/Mathlib/Algebra/MvPolynomial/Basic.lean'
    source.parent.mkdir(parents=True)
    source.write_text('theorem coeff_mul : True := trivial')
    client = LeanToolchainClient.__new__(LeanToolchainClient)
    client.run_snippet_check = Mock(return_value=ToolchainLeanCheckView(
        ok=True,
        provider='lake_command',
        summary='checked',
        diagnostics_excerpt=messages(
            f'{name}.{{u, u_1}} {{R : Type u}} : True',
            f'theorem {name}.{{u, u_1}} : True := trivial',
        ),
    ))
    client._compiler_defining_module = Mock(return_value=module)

    result = client.inspect_local_mathlib_declaration(
        tmp_path,
        module=module,
        decl_name=name,
    )

    assert result.ok
    assert result.module == module
    assert result.kind == 'theorem'
    assert str(source) in (result.raw_excerpt or '')


def test_local_mathlib_declaration_rejects_different_defining_module(tmp_path):
    module = 'Mathlib.Algebra.MvPolynomial.Basic'
    name = 'MvPolynomial.coeff_mul'
    client = LeanToolchainClient.__new__(LeanToolchainClient)
    client.run_snippet_check = Mock(return_value=ToolchainLeanCheckView(
        ok=True,
        provider='lake_command',
        summary='checked',
        diagnostics_excerpt=messages(
            f'{name} : True',
            f'theorem {name} : True := trivial',
        ),
    ))
    client._compiler_defining_module = Mock(return_value='Mathlib.Other')

    result = client.inspect_local_mathlib_declaration(
        tmp_path,
        module=module,
        decl_name=name,
    )

    assert not result.ok
    assert result.issue_code == 'mathlib_decl_defining_module_mismatch'
