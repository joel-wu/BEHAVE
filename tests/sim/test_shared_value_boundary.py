"""Shared value carriers have one owner and no frontend dependency."""

import ast
import importlib
import importlib.util
import pickle
import subprocess
import sys
from pathlib import Path

import pytest


def test_production_solver_consumers_do_not_depend_on_the_analysis_package():
    root = Path(__file__).resolve().parents[2] / 'src/behave/sim'
    for path in root.rglob('*.py'):
        module = 'behave.sim.' + '.'.join(path.relative_to(root).with_suffix('').parts)
        package = module.removesuffix('.__init__') if path.name == '__init__.py' else module.rpartition('.')[0]
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                owner = (importlib.util.resolve_name('.' * node.level + (node.module or ''), package)
                         if node.level else node.module or '')
                names = (owner, *(owner + '.' + alias.name for alias in node.names))
            elif isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            else:
                continue
            assert not any(name == 'behave.sim.analysis' or name.startswith('behave.sim.analysis.')
                           for name in names), (path, node.lineno, names)


@pytest.mark.parametrize('name', ('types', 'values', 'uniform_array', 'functional_array',
                                  'value_contract', 'value_layout', 'value_binding', 'procedure_contract',
                                  'capabilities', 'ir', 'cfg', 'ssa', 'typecheck', 'validation',
                                  'native_values', 'state_projection', 'primitives',
                                  'ranges', 'range_relations', 'term_bounds', 'term_rewrite', 'bitvectors',
                                  'encoder', 'plan', 'step_template', 'slicing', 'guarded_reads',
                                  'dependency_sets', 'template_arrays', 'closure_proof', 'state_boundary',
                                  'interp', 'operation_goals'))
def test_shared_values_do_not_import_frontends(name):
    solver_modules = {'functional_array', 'term_rewrite', 'bitvectors', 'encoder',
                      'step_template', 'guarded_reads', 'template_arrays'}
    if name in solver_modules:
        pytest.importorskip('z3')
    script = f'''
import importlib, sys
importlib.import_module('behave.sim.symbolic.{name}')
assert not any(n.startswith(('behave.sim.behavior', 'behave.sim.rtl')) for n in sys.modules)
if {name!r} not in {solver_modules!r}:
    assert 'z3' not in sys.modules
'''
    result = subprocess.run((sys.executable, '-c', script), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_graph_checker_reuses_shared_type_identity():
    from behave.sim.symbolic import typecheck as checking
    from behave.sim.symbolic import types

    for name in ('Array', 'BOOLEAN', 'INTEGER', 'VALUE', '_Boolean', '_Integer', '_Value',
                 'is_type', 'of_shape', 'shape_of', 'of_value'):
        assert getattr(checking, name) is getattr(types, name)
    assert checking.of_value([[True]]) == types.Array(1, types.Array(1, types.BOOLEAN))
    assert checking.of_value([True, 1]) is None


def test_rtl_does_not_depend_on_behavior_execution_or_compilation():
    root = Path(__file__).resolve().parents[2] / 'src/behave/sim/rtl'
    for path in root.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                owner = (importlib.util.resolve_name('.' * node.level + (node.module or ''),
                                                    'behave.sim.rtl')
                         if node.level else node.module)
                if owner.startswith('behave.sim.behavior'):
                    # Native runtime packaging needs the bundled public Python
                    # library directory, not a Python compiler/execution product.
                    assert (path.name, owner, [alias.name for alias in node.names]) == (
                        'runtime_dependencies.py', 'behave.sim.behavior', ['LIBRARY_DIR'])
            elif isinstance(node, ast.Import):
                assert not any(alias.name.startswith('behave.sim.behavior') for alias in node.names)


@pytest.mark.parametrize('compact', (False, True))
def test_model_decoding_keeps_recursive_types_and_compact_arrays(compact):
    z3 = pytest.importorskip('z3')
    from behave.sim.behavior.atomic_exact import _decode_value
    from behave.sim.symbolic import encoder, types, values
    from behave.sim.symbolic.functional_array import FunctionalArray
    from behave.sim.symbolic.uniform_array import UniformArray

    assert _decode_value is encoder.decode_value
    solver = z3.Solver()
    negative = z3.BitVec('decoded_negative', 8)
    solver.add(negative == -3)
    assert solver.check() == z3.sat
    model = solver.model()
    dense = [[negative, z3.BitVecVal(2, 8)], [z3.IntVal(-7), z3.IntVal(0)]]
    assert encoder.decode_value(model, dense, types.Array(2, types.Array(2, types.INTEGER)),
                                compact=compact) == [[-3, 2], [-7, 0]]
    bits = FunctionalArray.uniform(4, z3.BitVecVal(0, 1)).store(
        z3.BitVecVal(2, 4), z3.BitVecVal(1, 1), z3.BoolVal(True))
    decoded = encoder.decode_value(model, bits, types.Array(4, types.BOOLEAN), compact=compact)
    assert type(decoded) is (UniformArray if compact else list)
    assert values.value_equal(decoded, [False, False, True, False])
    assert all(type(value) is bool for value in decoded)
    with pytest.raises(ValueError, match='outside'):
        encoder.decode_value(model, z3.BitVecVal(2, 2), types.BOOLEAN)
    with pytest.raises(TypeError, match='shape'):
        encoder.decode_value(model, dense, types.Array(3, types.INTEGER))
    with pytest.raises(TypeError, match='wrong type'):
        encoder.decode_value(model, bits, types.Array(3, types.BOOLEAN))
    with pytest.raises(TypeError, match='semantic type'):
        encoder.decode_value(model, negative, None)


def test_shared_operation_predicate_runs_without_language_frontends():
    script = '''
import sys
from behave.sim.core.goal_predicate import GoalCondition as C, GoalField as F
from behave.sim.core.operation_goal import OperationGoal, OperationValue
from behave.sim.symbolic import ir, interp
from behave.sim.symbolic.operation_goals import predicate_expression
bound = OperationGoal('edge', C('and', C('eq', F('x', bit=1), True),
                               C('lt', F('x'), 4))).bind((OperationValue('x'),))
predicate = predicate_expression(bound, (ir.Var('a'),))
proc = ir.Procedure((), ('a',), (), ('y',), ir.Seq(
    ir.Assign('y', ir.Var('a')), ir.If(predicate, ir.Mark('edge'), ir.Skip())))
for a in range(8):
    state, output, hits, error = interp.run_step(proc, {'a': a})
    assert set(hits) == ({'edge'} if a in (2, 3) else set())
    assert error == ''
assert not any(name.startswith(('behave.sim.behavior', 'behave.sim.rtl', 'behave.sim.engines'))
               for name in sys.modules)
assert 'z3' not in sys.modules
'''
    result = subprocess.run((sys.executable, '-c', script), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_solver_memory_policy_has_one_common_owner():
    from behave.sim.behavior import budgets
    from behave.sim.core import resources

    assert budgets.solver_memory_mb is resources.solver_memory_mb
    assert budgets.SOLVER_MEMORY_MB == resources.SOLVER_MEMORY_MB
    assert budgets.ARTIFACT_MEMORY_MB == resources.ARTIFACT_MEMORY_MB
    assert resources.solver_memory_mb(None) is None
    for memory in (1, 400, 4096, 65536):
        assert resources.solver_memory_mb(memory) == max(
            1, memory * budgets.SOLVER_MEMORY_MB // budgets.ARTIFACT_MEMORY_MB)


def test_shared_construction_runs_without_frontends_or_execution_layers():
    pytest.importorskip('z3')
    script = '''
import sys, z3
blocked = ('behave.sim.behavior', 'behave.sim.rtl', 'behave.sim.engines')
class NoFrontends:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(blocked) or fullname in {'behave.sim.command', 'behave.sim.evaluation'}:
            raise AssertionError(fullname)
sys.meta_path.insert(0, NoFrontends())
from behave.sim.symbolic import encoder, ir, ranges, plan, closure_proof, step_template
body = ir._sequence((
    ir.Assign('state', ir.Op('and', ir.Op('add', ir.Var('state'), ir.Var('a')), ir.Lit(3))),
    ir.Assign('y', ir.Var('state')),
    ir.If(ir.Op('eq', ir.Var('state'), ir.Lit(3)), ir.Mark('target'), ir.Skip())))
proc = ir.Procedure(('state',), ('a',), (), ('y',), body, initial={'state': 0})
env = {'state': ranges.Range(0, 3), 'a': ranges.Range(0, 15)}
assert plan.plan_encoding(proc, env).representation_complete
positions = closure_proof.plan_positions(proc, env, 3)
assert positions.complete, positions.unresolved
plain = encoder.encode_step(proc, env=env)
shared = step_template.StepTemplate(proc, env).instantiate(plain.pre)
solver = z3.Solver()
solver.add(plain.constraint, shared.constraint)
x, y = encoder.widen(plain.post['y'], shared.post['y'])
solver.add(z3.Or(x != y, plain.ok != shared.ok, plain.error != shared.error,
                plain.hits['target'] != shared.hits['target']))
assert solver.check() == z3.unsat
selected = step_template.StepTemplate.selected(proc, env, ('target',)).project(
    {'target': ('target',)}, outputs=()).encode()
solver = z3.Solver()
solver.add(selected.constraint, selected.hits['target'])
assert solver.check() == z3.sat
assert not any(name.startswith(blocked) for name in sys.modules)
'''
    result = subprocess.run((sys.executable, '-c', script), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_production_encoding_consumers_use_the_shared_owner():
    root = Path(__file__).resolve().parents[2] / 'src/behave/sim'
    moved = {'encoder', 'plan', 'step_template', 'slicing', 'guarded_reads',
             'dependency_sets', 'template_arrays', 'closure_proof', 'state_boundary',
             'interp', 'operation_goals'}
    old = {'behave.sim.behavior.' + name for name in moved}
    for path in root.rglob('*.py'):
        module = 'behave.sim.' + '.'.join(path.relative_to(root).with_suffix('').parts)
        package = module.removesuffix('.__init__') if path.name == '__init__.py' else module.rpartition('.')[0]
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom):
                continue
            owner = (importlib.util.resolve_name('.' * node.level + (node.module or ''), package)
                     if node.level else node.module)
            assert owner not in old, (path, node.lineno)
            if owner == 'behave.sim.behavior':
                assert not moved.intersection(alias.name for alias in node.names), (path, node.lineno)


def test_output_certificate_and_legacy_checkpoints_have_one_shared_owner():
    from behave.sim.behavior import output_boundary, budgets
    from behave.sim.symbolic import capabilities, procedure_contract
    from .test_behavior_output_contract import _lower

    for name in ('OutputBoundaryError', '_OutputBoundaryAuthority', '_restore_output_boundary',
                 'output_boundary_bounds', 'output_boundary_evidence'):
        assert getattr(output_boundary, name) is getattr(procedure_contract, name)
    assert budgets.scalar_magnitude_bits is capabilities.scalar_magnitude_bits
    proc = _lower("[{'y': a}, {'y': a}]").proc
    expected = procedure_contract.output_boundary_bounds(proc)
    assert expected == {'#count': (0, 2), 'y': (0, 1)}
    payload = pickle.dumps(proc, protocol=2)
    new_name = b'cbehave.sim.symbolic.procedure_contract\n_restore_output_boundary\n'
    assert new_name in payload
    restored = pickle.loads(payload.replace(
        new_name, b'cbehave.sim.behavior.output_boundary\n_restore_output_boundary\n'))
    assert procedure_contract.output_boundary_bounds(restored) == expected


def test_shared_term_proofs_and_rewrites_do_not_load_frontends_or_model_proofs():
    pytest.importorskip('z3')
    script = '''
import sys, z3
blocked = ('behave.sim.behavior', 'behave.sim.rtl', 'behave.sim.engines')
class BlockFrontends:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(blocked):
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockFrontends())
from behave.sim.symbolic import bitvectors, ranges, term_bounds, term_rewrite
x = z3.BitVec('independent_x', 16)
divisor = z3.BitVecVal(3, 16)
proof = term_bounds._SyntacticClosureProof({})
assert proof.signed_hull(x & 7) == ranges.Range(0, 7)
quotient = bitvectors._floor_div(x, divisor)
assert proof._is_floor_division(quotient, x, divisor)
assert proof.signed_hull(bitvectors._floor_mod(x, divisor)) == ranges.Range(0, 2)
original = z3.If((x & 7) < 8, z3.BitVecVal(1, 16), z3.BitVecVal(0, 16))
reduced = term_rewrite._ComparisonRewriter([]).rewrite(original)
assert z3.is_bv_value(reduced) and reduced.as_long() == 1
solver = z3.Solver()
solver.add(original != reduced)
assert solver.check() == z3.unsat
declined = term_rewrite._ComparisonRewriter([], work_limit=0).rewrite(original)
assert z3.eq(declined, original)
assert not any(name.startswith(blocked) for name in sys.modules)
'''
    result = subprocess.run((sys.executable, '-c', script), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_encoding_no_longer_imports_run_closure_proofs_through_rewriting():
    pytest.importorskip('z3')
    script = '''
import sys, z3
blocked = ('behave.sim.symbolic.closure_proof', 'behave.sim.behavior.extract',
           'behave.sim.behavior.lowering', 'behave.sim.rtl', 'behave.sim.engines')
class BlockHigherLayers:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(blocked):
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockHigherLayers())
from behave.sim.symbolic import encoder, interp
from behave.sim.symbolic import ir, ranges
proc = ir.Procedure((), ('a',), (), ('y',), ir.Assign('y', ir.Op('fmod', ir.Var('a'), ir.Lit(3))))
encoded = encoder.encode_step(proc, env={'a': ranges.Range(-8, 7)})
for a in range(-8, 8):
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.pre['a'] == a)
    assert solver.check() == z3.sat
    result = solver.model().eval(encoded.post['y']).as_signed_long()
    _, output, hits, error = interp.run_step(proc, {'a': a})
    assert result == a % 3 and output.transactions == [{'y': result}]
    assert not hits and not error
assert not any(name.startswith(blocked) for name in sys.modules)
'''
    result = subprocess.run((sys.executable, '-c', script), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_model_proofs_and_encoder_reexport_the_same_term_implementations():
    pytest.importorskip('z3')
    from behave.sim.symbolic import closure_proof, encoder
    from behave.sim.symbolic import bitvectors, term_bounds

    for name in ('_StructuralProofLimit', '_StructuralProofWork', '_SyntacticClosureProof',
                 '_PropositionalProof', '_syntactic_signed_hull'):
        assert getattr(closure_proof, name) is getattr(term_bounds, name)
    for name in ('widen', '_extend', '_floor_div', '_floor_mod', '_positive_power_of_two'):
        assert getattr(encoder, name) is getattr(bitvectors, name)


@pytest.mark.parametrize('kind', ('integer', 'boolean', 'nested', 'compact'))
def test_legacy_checkpoint_type_names_still_restore(kind):
    from behave.sim.symbolic import types, values
    from behave.sim.symbolic.uniform_array import UniformArray

    original = {'integer': types.INTEGER, 'boolean': types.BOOLEAN,
                'nested': types.Array(2, types.Array(3, types.BOOLEAN)),
                'compact': UniformArray.uniform(5, [1, True]).store(2, [7, False])}[kind]
    # Protocol 2 names globals without frame-length fields. Recreate the
    # former module paths, not a new-format checkpoint with a new name.
    payload = pickle.dumps(original, protocol=2)
    payload = payload.replace(b'behave.sim.symbolic.types', b'behave.sim.symbolic.typecheck')
    payload = payload.replace(b'behave.sim.symbolic.uniform_array', b'behave.sim.symbolic.uniform_array')
    restored = pickle.loads(payload)
    assert type(restored) is type(original)
    assert values.value_equal(restored, original) if kind == 'compact' else restored == original


def test_shared_value_package_stays_frontend_neutral():
    root = Path(__file__).resolve().parents[2] / 'src/behave/sim/symbolic'
    for path in root.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                names = (node.module or '').split('.')
            elif isinstance(node, ast.Import):
                names = [part for alias in node.names for part in alias.name.split('.')]
            else:
                continue
            assert not {'behavior', 'rtl', 'engines', 'agent'}.intersection(names), (path, node.lineno)


def test_source_planner_reexports_the_one_contract_algebra():
    from behave.sim.behavior import source_options, source_values
    from behave.sim.symbolic import value_contract

    for name in value_contract.__all__:
        old = source_options if name in {'narrow_none', 'optional_sequence'} else source_values
        assert getattr(old, name) is getattr(value_contract, name), name


def test_semantic_contract_is_not_owned_by_source_or_output_proofs():
    from behave.sim.behavior import signature
    from behave.sim.symbolic import procedure_contract

    for name in procedure_contract.__all__:
        assert getattr(signature, name) is getattr(procedure_contract, name)
    script = '''
import sys
class BlockFrontends:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('behave.sim.behavior', 'behave.sim.rtl')) or fullname == 'z3':
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockFrontends())
from behave.sim.core.contract import FiniteType
from behave.sim.symbolic.types import INTEGER
from behave.sim.symbolic.procedure_contract import ProcedureContract
contract = ProcedureContract((), (('x', INTEGER),), (), (('y', INTEGER),),
                             (('x', FiniteType(4)),), (('y', FiniteType(4)),))
assert contract.validate_inputs({'x': 3}) == {'x': 3}
contract.validate_output(1, {'y': [3]}, capacity=1)
try:
    contract.validate_output(1, {'y': [16]}, capacity=1)
except ValueError:
    pass
else:
    raise AssertionError('output validation was lost')
'''
    result = subprocess.run((sys.executable, '-c', script), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('value', [None, [1, None, 3], {'x': (1, 2), 'y': None},
                                 {1: 7, 3: 2}, (1, [2, 3]), b'abc'])
def test_legacy_value_contract_checkpoint_restores_the_shared_type(value):
    from behave.sim.symbolic.value_contract import ValueContract, contract_of_value

    original = contract_of_value(value)
    current_name = b'cbehave.sim.symbolic.value_contract\nValueContract\n'
    payload = pickle.dumps(original, protocol=2)
    assert current_name in payload
    legacy = payload.replace(current_name, b'cbehave.sim.behavior.source_values\nValueContract\n')
    restored = pickle.loads(legacy)
    assert type(restored) is ValueContract
    assert restored == original
    assert hash(restored) == hash(original)


def test_ir_state_contracts_do_not_load_python_source_planning():
    script = '''
import sys
blocked = {'behave.sim.behavior.' + name for name in (
    'source_values', 'source_options', 'source_sequences', 'lowering', 'extract')}
class BlockPlanner:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockPlanner())
from behave.sim.symbolic.ir import StateContract
from behave.sim.symbolic.state_projection import SourceProductBinding
from behave.sim.symbolic.value_contract import ValueContract, SCALAR
shape = ValueContract.record({'x': SCALAR, 'y': ValueContract.option(SCALAR)})
binding = SourceProductBinding('record', shape, (
    (('x',), 'x'), (('y', 'present'), 'yp'), (('y', 'payload'), 'yv')))
contract = StateContract(('record',), {'record': ('state',)}, (binding,))
restored = contract.projection('record').reconstruct({'x': 2, 'yp': False, 'yv': 99})
assert restored == {'x': 2, 'y': None}
assert not blocked.intersection(sys.modules)
'''
    result = subprocess.run((sys.executable, '-c', script), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_production_callers_do_not_use_legacy_value_owners():
    root = Path(__file__).resolve().parents[2] / 'src/behave/sim'
    retired = {'functional_array', 'uniform_array', 'ir_values', 'value_binding', 'value_layout',
               'ir', 'cfg', 'ssa', 'types', 'validation', 'primitives', 'native_values', 'state_projection',
               'ranges', 'range_relations', 'term_rewrite'}
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom):
                continue
            parts = (node.module or '').split('.')
            legacy = ('behavior' in parts and parts[-1] in retired) or (
                path.parent == root / 'behavior' and node.level == 1 and node.module in retired)
            behavior_package = node.module == 'behave.sim.behavior' or (
                node.level > 0 and node.module == 'behavior') or (
                path.parent == root / 'behavior' and node.level == 1 and node.module is None)
            legacy |= behavior_package and bool(retired.intersection(a.name for a in node.names))
            assert not legacy, (path, node.lineno)


def test_shared_ir_checks_work_without_either_frontend_or_worker_runtime():
    script = '''
import sys
blocked = ('behave.sim.behavior', 'behave.sim.rtl', 'behave.sim.engines',
           'behave.sim.analysis', 'behave.sim.job_', 'behave.sim.core.resources', 'z3')
class BlockHigherLayers:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(blocked):
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockHigherLayers())
from behave.sim.symbolic import ir, cfg, ssa, typecheck, validation
proc = ir.Procedure((), ('a',), (), ('y',),
                    ir.Seq(ir.Mark('branch'), ir.Assign('y', ir.Op('add', ir.Var('a'), ir.Lit(1)))),
                    types={'a': typecheck.INTEGER, 'y': typecheck.INTEGER})
assert ir.normalized(proc) == []
assert not typecheck.type_errors(proc)
graph = cfg.of_command(proc.body)
assert not ssa.undefined_names(graph, {'a'})
assert cfg.graph_fingerprint(graph)
owner = validation._procedure_validation(proc)
assert validation._checked_procedure_validation(owner, proc) is owner
assert ir.procedure_representation_cost(proc) > 0
ir.validate_procedure_representation(proc)
assert not any(name.startswith(blocked) for name in sys.modules)
'''
    result = subprocess.run((sys.executable, '-c', script), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_static_procedure_checks_and_planning_do_not_load_the_solver():
    script = '''
import pickle, sys
blocked = {'behave.sim.behavior.' + name for name in (
    'encoder', 'signature', 'residual', 'lowering', 'extract', 'exact_solver', 'closure_proof')}
class BlockProofs:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked or fullname == 'z3' or fullname.startswith('z3.'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockProofs())
from behave.sim.symbolic import interp, ir, plan, ranges, typecheck as types, validation
proc = ir.Procedure((), ('a',), (), ('y',), ir.Assign('y', ir.Var('a')),
                    types={'a': types.INTEGER, 'y': types.INTEGER})
owner = validation._procedure_validation(proc)
assert validation._checked_procedure_validation(owner, proc) is owner
assert not ir.normalized(proc)
env = {'a': ranges.Range(0, 15)}
prepared = plan.reuse_encoding_plan(owner, env)
assert prepared is plan.reuse_encoding_plan(owner, dict(env))
assert prepared is not plan.reuse_encoding_plan(owner, {'a': ranges.Range(0, 31)})
prepared.validate_binding(proc, env, _procedure_validation=owner)
layers = plan._derive_position_layers(proc, env, 4, _validation=owner)
assert layers.bound == 4 and len(layers.steps) == 4
assert not layers.unresolved and not hasattr(layers, 'complete')
assert layers.proof_positions == (0, 1, 2, 3)
assert all(step is layers.steps[0] for step in layers.steps)
_, outputs, hits, error = interp.run_step(proc, {'a': 3})
assert outputs.transactions == [{'y': 3}] and not hits and not error
try:
    pickle.dumps(owner)
except TypeError:
    pass
else:
    raise AssertionError('invocation-local validation leaked into a checkpoint')
other = proc.derived()
try:
    validation._checked_procedure_validation(owner, other)
except TypeError:
    pass
else:
    raise AssertionError('validation transferred to another procedure')
proc.body = ir.Fail('Changed', 'changed')
assert any('semantic contract is inconsistent' in fault for fault in ir.normalized(proc))
assert not blocked.intersection(sys.modules) and 'z3' not in sys.modules
'''
    result = subprocess.run((sys.executable, '-c', script), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_static_checks_have_one_owner_and_no_proof_imports():
    from behave.sim.behavior import signature
    from behave.sim.symbolic import encoder, validation

    for name in ('EncodingError', '_ProcedureValidation', '_procedure_validation',
                 '_checked_procedure_validation', 'validate_procedure'):
        assert getattr(encoder, name) is getattr(validation, name)
    for name in ('build_procedure_contract', 'bind_procedure_contract'):
        assert getattr(signature, name) is getattr(validation, name)
    assert not hasattr(validation._ProcedureValidation, 'implicit_plan')
    forbidden = {'encoder', 'plan', 'signature', 'residual', 'lowering', 'z3', 'exact_solver'}
    path = Path(validation.__file__)
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            names = (node.module or '').split('.')
        elif isinstance(node, ast.Import):
            names = [part for alias in node.names for part in alias.name.split('.')]
        else:
            continue
        assert not forbidden.intersection(names), (path, node.lineno)


def test_position_drafts_cannot_be_consumed_as_proved_plans():
    from behave.sim.symbolic import closure_proof, ir, plan, ranges

    proc = ir.Procedure((), ('a',), (), ('y',), ir.Assign('y', ir.Var('a')))
    env = {'a': ranges.Range(0, 3)}
    draft = plan._derive_position_layers(proc, env, 3)
    assert not hasattr(draft, 'complete')
    with pytest.raises(TypeError, match='not a PositionEncodingPlan'):
        closure_proof._checked_position_plan(draft, proc, env, 3)
    with pytest.raises(TypeError, match='exact raw plan'):
        closure_proof._PreparedPositionPlan(draft, closure_proof._PREPARE_TOKEN)


def test_planning_has_no_proof_calls_and_production_uses_the_proof_owner():
    from behave.sim.symbolic import plan

    tree = ast.parse(Path(plan.__file__).read_text())
    forbidden = {'encoder', 'exact_solver', 'closure_proof', 'exact_solve', 'z3'}
    # One explicit legacy-name lookup remains for old imports/checkpoints. It
    # is not part of representation construction or any production call path.
    for declaration in tree.body:
        if isinstance(declaration, ast.FunctionDef) and declaration.name == '__getattr__':
            continue
        for node in ast.walk(declaration):
            if isinstance(node, ast.ImportFrom):
                names = (node.module or '').split('.') + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [part for a in node.names for part in a.name.split('.')]
            else:
                continue
            assert not forbidden.intersection(names), node.lineno
    proof_owned = {'prepare_positions', 'plan_positions', '_PreparedPositionPlan',
                   '_checked_position_plan', '_prepare_supplied_positions',
                   'position_analysis_identities'}
    root = Path(plan.__file__).parents[1]
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[-1] == 'plan':
                assert not proof_owned.intersection(a.name for a in node.names), (path, node.lineno)
