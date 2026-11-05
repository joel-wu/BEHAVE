"""Deferred local obligations do not first prove the unrestricted activation."""

import pytest

from behave.sim.symbolic import interp, ir, ranges, typecheck as types
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.rtl import source_domains


def test_deferred_domains_keep_every_nonconstant_condition_without_unrolling(monkeypatch):
    access = ir.Assign('x', ir.Var('offset'))
    division = ir.Assign('x', ir.Var('divisor'))
    predicate = ir.Assign('x', ir.Var('defined'))
    constant = ir.Assign('x', ir.Lit(0))
    proc = ir.Procedure(('offset', 'divisor', 'defined'), (), ('x',), (),
        ir._sequence((access, division, predicate, constant)))
    cause = Refusal('test-domain', 'test domain obligation')

    def unnecessary(*args, **kwargs):
        pytest.fail('a deferred local query ran a whole-activation proof')

    walk = ranges.walk_post

    def unseeded(command, environment, witness, **options):
        # The deferred pass transfers writes from unknown entry values only;
        # seeding it with declared ranges would be the whole-activation proof.
        if environment:
            pytest.fail('a deferred local query seeded its transfer with entry ranges')
        return walk(command, environment, witness, **options)

    monkeypatch.setattr(ranges, 'walk_post', unseeded)
    monkeypatch.setattr(source_domains, '_prove_exact', unnecessary)
    conditions = source_domains.prove_source_domains(proc,
        {'offset': ranges.Range(0, 0), 'divisor': ranges.Range(1, 1),
         'defined': ranges.Range(1, 1)},
        {id(access): (ir.Var('offset'), 1, 4, cause),
         id(constant): (ir.Lit(0), 1, 4, cause)},
        divisors={id(division): (ir.Var('divisor'), cause)},
        predicates={id(predicate): (ir.Op('ne', ir.Var('defined'), ir.Lit(0)), cause)},
        defer=True)
    assert set(conditions) == {id(access), id(division), id(predicate)}
    # An entry range must not erase a check after the source changes that value.
    invalid = {id(access): {'offset': 4},
               id(division): {'divisor': 0},
               id(predicate): {'defined': 0}}
    for identity, (condition, returned_cause) in conditions.items():
        assert returned_cause is cause
        assert interp._Interp(invalid[identity]).boolean(condition) is True


def test_standalone_domains_still_require_the_full_proof():
    command = ir.Assign('x', ir.Var('divisor'))
    proc = ir.Procedure(('divisor',), (), ('x',), (), command,
                        types={'divisor': types.INTEGER, 'x': types.INTEGER})
    cause = Refusal('test-domain', 'zero divisor')
    with pytest.raises(Refused) as error:
        source_domains.prove_source_domains(proc, {'divisor': ranges.Range(0, 1)}, {},
            divisors={id(command): (ir.Var('divisor'), cause)})
    assert error.value.refusal.detail['domain_proof'] == 'sat'


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.solver
@pytest.mark.parametrize('guarded', [True, False])
def test_deferred_helpers_keep_local_safety_checks_and_native_hits(tmp_path, monkeypatch, guarded):
    from behave.sim.engines import probing
    from behave.sim.engines.rtl.source_query import query_source_interval
    from behave.sim.rtl import source_ir

    from .test_rtl_source_locations import _prepare_clocked_replay
    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower, _names, _state

    def no_universal_proof(*args, **kwargs):
        pytest.fail('a deferred helper ran an unrestricted all-argument lemma')

    monkeypatch.setattr(source_ir._Lowerer, 'helper_defined', no_universal_proof)
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [3:0] f(input [3:0] n,d);
        {"if(d==0) return 0;" if guarded else ""}
        return n/d;
      endfunction
      always @(posedge clk) begin q<=f(9,a); if(a==3) q<=7; end
    endmodule'''
    machine, plan = _lower(tmp_path, source, defer_domains=True)
    assert machine.domain_checks
    names = _names(machine)
    target = _goal(source, plan, 'a==3', 'true', 'control-choice')
    initial = _state(machine, {'clk': 0, 'a': 0, 'q': 0})
    env = {**machine.procedure.state_ranges, **machine.input_ranges}
    answer = query_source_interval(machine, env, initial,
        {names['clk']: 0, names['a']: 0}, [target], mutable_inputs=(names['a'],),
        following_inputs=({names['clk']: 1, names['a']: 0},),
        event_bound=machine.event_bound, timeout_ms=30_000)
    if not guarded:
        # A safe goal witness cannot hide a fault elsewhere in the local domain.
        assert answer.outcome is probing.Outcome.UNSUPPORTED, answer.detail
        assert 'invalid operation in this local input domain' in answer.detail
        return
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate[names['a']] == 3
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    outputs, hits = replay((answer.candidate[names['a']],))
    assert outputs == [7]
    assert target in hits[0]
