"""Notification insertion follows command order, not the Python call stack."""

from types import SimpleNamespace

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.rtl.source_integral import IntegralType
from behave.sim.rtl.source_scheduler import Subscription, _SchedulerBuilder


def _builder():
    kind = IntegralType(4, False)
    process = SimpleNamespace(procedure=SimpleNamespace(types={}),
                              write_masks=(), deferred=())
    program = SimpleNamespace(fields={'x': SimpleNamespace(input=False, name='x')},
                              kinds={'x': kind}, domain=lambda name: kind.domain,
                              processes=(process, process))
    return _SchedulerBuilder(program, (Subscription(1, 'x', 15),))


def _writes():
    return ir.Seq(ir.Assign('x', ir.Lit(3)), ir.Seq(
        ir.Mark('changed'), ir.Seq(ir.Assign('x', ir.Lit(0)), ir.Mark('restored'))))


@pytest.mark.parametrize('shape', ['sequence', 'choice', 'loop'])
def test_deep_activation_preserves_transient_writes_and_hits(shape):
    command = _writes()
    for _ in range(1500):
        if shape == 'sequence':
            command = ir.Seq(ir.Skip(), command)
        elif shape == 'choice':
            command = ir.If(ir.Lit(True), command, ir.Fail('bad', 'failure'))
        else:
            command = ir.For('i', 1, command)
    transformed = _builder().activation(command, running=0)
    machine = interp._Interp({'x': 0, 'pending0': False, 'pending1': False})
    hits = set()
    machine.run(transformed, hits)
    assert machine.rho['x'] == 0
    assert machine.rho['pending1'] is True
    assert machine.rho['pending0'] is False
    assert hits == {'changed', 'restored'}


@pytest.mark.parametrize('running', [0, 1])
def test_activation_matches_recursive_rule_and_does_not_merge_shared_occurrences(running):
    shared = _writes()
    command = ir.Seq(shared, ir.If(ir.Var('choose'),
        ir.For('i', 2, shared), ir.Seq(shared, ir.Mark('other'))))

    def recursive(builder, node):
        if isinstance(node, ir.Seq):
            return ir.Seq(recursive(builder, node.first), recursive(builder, node.second))
        if isinstance(node, ir.If):
            return ir.If(node.guard, recursive(builder, node.then), recursive(builder, node.orelse))
        if isinstance(node, ir.For):
            return ir.For(node.index, node.trip_count, recursive(builder, node.body))
        return builder.activation(node, running)

    actual_builder, control_builder = _builder(), _builder()
    actual = actual_builder.activation(command, running)
    control = recursive(control_builder, command)
    assert ir.command_fingerprint(actual) == ir.command_fingerprint(control)
    assert actual_builder.types == control_builder.types
    assert actual_builder.scratch == control_builder.scratch == 6
