"""Typed helper arguments: values, return-time copy-out, and true references.

Slang represents output/inout actuals as assignments whose RHS contains an
EmptyArgument of the formal type. Keep those conversions; they are not a
source assignment to execute before calling the helper. Output lvalues are
evaluated on return, inout additionally reads its actual on entry, and ref
captures an lvalue once and aliases it throughout the call.
"""

from dataclasses import dataclass

from ..symbolic.slicing import reads
from .source_values import source_type


@dataclass(frozen=True, slots=True)
class CallPhases:
    """Argument evaluation and invocation are distinct source events.

    Ordinary calls concatenate these phases. A deferred assertion report must
    keep every captured value in its own frame and run ``execution`` only at
    dispatch. Source storage is deliberately not a captured argument value.
    This describes a call, not a report queue or permission to admit one.
    """

    encounter: tuple
    execution: tuple
    value: object
    captures: tuple[str, ...] = ()


@dataclass
class CallArguments:
    commands: list
    values: list
    references: dict
    outputs: list

    @property
    def captures(self):
        names = set()
        for value in self.values:
            reads(value, names)
        for bound in self.references.values():
            for selection in bound.selections:
                reads(selection.index, names)
        return tuple(sorted(names))

    def copy_out(self, lowerer, values):
        commands = []
        previous_reads, previous_writes = set(), {}
        for (_, assignment), value in zip(self.outputs, values, strict=True):
            value = _output_value(lowerer, assignment['right'], value)
            target = assignment['left']
            if target['kind'] == 'Concatenation':
                after = lowerer.store(target, value, deferred=False)
                effects = lowerer.effects(after, None)
                destinations = {}  # Retain the conservative aggregate check.
            else:
                before, bound = lowerer.bind_lvalue(target)
                effects = lowerer.effects(before, value)
                destinations = {bound.name: lowerer.lvalue_mask(bound)}
                after = [*before, *lowerer.store_lvalue(bound, value, deferred=False)]
            # Argument copy-back order is not a portable aliasing contract.
            # Check selector/value effects separately from the destination's
            # read/modify/write carrier. Preserved neighboring cells are not
            # source reads; disjoint compiler-resolved destinations commute.
            lowerer.require_independent(assignment, (previous_reads, set(previous_writes)), effects)
            for name, mask in destinations.items():
                if name in previous_reads or mask & previous_writes.get(name, 0):
                    return lowerer.unsupported(assignment, 'copy-out operand effects need a qualified evaluation order')
            previous_reads.update(effects[0])
            for name in effects[1]:
                previous_writes[name] = -1
            for name, mask in destinations.items():
                previous_writes[name] = previous_writes.get(name, 0) | mask
            commands.extend(after)
        return commands


def _output_value(lowerer, node, value):
    if node['kind'] == 'EmptyArgument':
        return source_type(node['type'], lowerer.symbols).wrap(value)
    if node['kind'] != 'Conversion':
        return lowerer.unsupported(node, 'copy-out needs the compiler formal-value conversion')
    return lowerer.convert(node, _output_value(lowerer, node['operand'], value))


def prepare_arguments(lowerer, callee, node, *, deferred=False):
    formals, actuals = callee.get('arguments', ()), node.get('arguments', ())
    if len(formals) != len(actuals):
        return lowerer.unsupported(node, 'source calls need every compiler-resolved argument')
    result = CallArguments([], [], {}, [])
    previous = (set(), set())
    for formal, actual in zip(formals, actuals):
        direction = formal['direction']
        commands, value = [], None
        if deferred and direction in {'Out', 'InOut'}:
            return lowerer.unsupported(actual, 'deferred action arguments cannot copy out')
        if direction == 'In':
            commands, value = lowerer.snapshot(*lowerer.expression(actual))
        elif direction in {'Out', 'InOut'}:
            if actual['kind'] != 'Assignment':
                return lowerer.unsupported(actual, 'copy arguments need the compiler copy-out assignment')
            result.outputs.append((formal, actual))
            if direction == 'InOut':
                commands, value = lowerer.snapshot(*lowerer.expression(actual['left']))
                value = source_type(formal['type'], lowerer.symbols).wrap(value)
        elif direction == 'Ref':
            commands, bound = lowerer.bind_lvalue(actual)
            if deferred and bound.name not in lowerer.storage.values():
                return lowerer.unsupported(actual, 'deferred action references need static source storage')
            result.references[str(formal['addr'])] = bound
        else:
            return lowerer.unsupported(actual, 'source call argument direction has no binding')
        effects = lowerer.effects(commands, value)
        lowerer.require_independent(node, previous, effects)
        previous = previous[0] | effects[0], previous[1] | effects[1]
        result.commands.extend(commands)
        result.values.append(value)
    return result
