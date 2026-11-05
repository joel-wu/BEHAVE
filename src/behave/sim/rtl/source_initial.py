"""Explicit physical initial support, separate from a replayed source boundary.

Each compiler-owned field must be supplied: a native bit pattern fixes it;
None leaves its complete physical type free. Array positions follow the
compiler's declared left-to-right order. No DUT value is inferred from a seed,
an IR carrier width, or a reset that has not executed yet.

Physical support and HDL declaration initialization are distinct phases. A
receipt records physical bits before declarations execute; the symbolic state
then applies compiler-owned constants and defaults, including ANSI outputs.
Admitted initial processes run once through the shared event relation;
unencoded construction is never silently replaced with unconstrained storage.
"""

from dataclasses import dataclass, replace

import z3

from ..symbolic.encoder import encode_concrete_value, validated_width
from ..symbolic.step_template import _map_tree
from ..core.refusal import Refusal, Refused
from .source_declarations import _children
from .source_integral import integer_literal, integral_value_facts
from .source_values import ArrayType, source_type


def source_storage_leaves(program, *, check=None):
    """Native physical names and positional source paths from compiler types."""
    from .source_handles import physical_name

    def leaves(native, path, kind):
        if check is not None:
            check()
        if isinstance(kind, ArrayType):
            direction = 1 if kind.left <= kind.right else -1
            for position in range(kind.cells):
                yield from leaves((*native, kind.left + direction * position),
                                  (*path, position), kind.element)
        else:
            yield physical_name(native), path, kind.width

    seen = set()
    for name, field in program.fields.items():
        if not field.input and field.observation is not None:
            native = field.observation
            for leaf, path, width in leaves((native,) if isinstance(native, str) else native,
                                            (name,), program.kinds[name]):
                if leaf in seen:
                    raise Refused(Refusal('rtl-source-storage-ambiguous',
                        'distinct compiler storage cannot share one native leaf', field=leaf))
                seen.add(leaf)
                yield leaf, path, width


@dataclass(frozen=True, slots=True)
class SourceInitialState:
    bits: dict
    state: dict

    def fixed(self, bindings, *, check=None):
        """Specialize one member's initial image, not the universal template.

        Physical bits and mathematical carriers often share the same roots.
        Transport them together instead of rebuilding the full substitution
        table for every register or array cell. Unbound private bits stay free.
        """
        from ..symbolic.functional_array import FunctionalArray
        from ..symbolic.step_template import _tree
        from .symbolic_state import fold_fixed_terms

        roots = []

        def position(term):
            if check is not None:
                check()
            roots.append(term)
            return len(roots) - 1

        def pack(term):
            return (tuple(position(cell) for cell in _tree(term))
                    if isinstance(term, FunctionalArray) else position(term))

        layout = _map_tree({'bits': self.bits, 'state': self.state}, pack)
        values = fold_fixed_terms(roots, bindings, check=check)

        def unpack(index):
            return (FunctionalArray.from_cells([values[i] for i in index])
                    if isinstance(index, tuple) else values[index])

        return replace(self, **_map_tree(layout, unpack))

    def values(self, model):
        """Decode physical bits, not implementation carriers or private queues."""
        return _map_tree(self.bits, lambda term: model.eval(term, model_completion=True).as_long())

    def request_layout(self, program, artifact, *, check=None):
        """Bind compiler-owned writable storage and pins to existing codecs.

        Derived nets/continuous destinations stay in the source initial state,
        not in the native writable register choice. Independence from their
        private initial values remains a whole-history admission obligation.
        """
        from .initial_request import InitialStateCoordinates
        from .source_handles import physical_name
        from .source_scheduler import _node_at

        if self.bits.keys() != program.fields.keys():
            raise ValueError('initial layout requires every compiler source field')
        variables = {field.key for identity, field in program.bindings.items()
                     if program.compiler.symbols[identity].get('kind') == 'Variable'}
        continuous, procedural = set(), set()
        for process in program.processes:
            if check is not None:
                check()
            derived = process.context is None and process.initial_process is None
            if process.context is not None:
                node = _node_at(program.compiler.document, process.context.key, program.compiler.symbols)
                derived = node['kind'] in {'ContinuousAssign', 'Net'}
            (continuous if derived else procedural).update(name for name, _, _ in process.write_masks)
        if continuous & procedural:
            raise Refused(Refusal('rtl-source-initial-storage-mixed-drivers',
                'native construction cannot deposit storage with both continuous and procedural drivers'))
        registers, pins, terms = {}, {}, {}

        def leaves(path, kind, bits):
            if check is not None:
                check()
            if isinstance(kind, ArrayType):
                if not isinstance(bits, (tuple, list)) or len(bits) != kind.cells:
                    raise ValueError('initial layout requires the complete compiler array shape')
                direction = 1 if kind.left <= kind.right else -1
                for position, value in enumerate(bits):
                    yield from leaves((*path, kind.left + direction * position), kind.element, value)
            else:
                if not z3.is_bv(bits) or bits.size() != kind.width:
                    raise ValueError('initial layout requires physical-width source bits')
                yield physical_name(path), kind.width, bits

        for name, field in program.fields.items():
            if not field.input and (field.key not in variables or name in continuous):
                continue
            path = field.port if field.input else field.observation
            if path is None:
                raise Refused(Refusal('rtl-source-initial-storage-unmapped',
                    'writable source storage needs a compiler-owned native path', field=field.name))
            path = (path,) if isinstance(path, str) else path
            if field.input and isinstance(program.kinds[name], ArrayType):
                raise Refused(Refusal('rtl-source-initial-pin-unencoded',
                    'construction pins require the contract scalar layout', field=field.name))
            for key, width, term in leaves(path, program.kinds[name], self.bits[name]):
                if key in terms:
                    raise Refused(Refusal('rtl-source-initial-storage-ambiguous',
                        'distinct compiler storage cannot share one native register spelling', field=key))
                (pins if field.input else registers)[key] = width
                terms[key] = term
        layout = InitialStateCoordinates(artifact, registers, startup_pins=pins)
        return layout, {coordinate: terms[name] for coordinate, name, _ in
                        (*layout.coordinates, *layout.pin_coordinates)}


def _initial_nodes(document, symbols):
    """Visit actual elaborated instances, not disabled generate alternatives."""
    def visit(node):
        if isinstance(node, dict):
            if node.get('isUninstantiated'):
                return
            yield node
            if node.get('kind') == 'Instance':
                body = node['body']
                yield from visit(symbols[body.split(' ', 1)[0]] if isinstance(body, str) else body)
                return
        for _, child in _children(node):
            yield from visit(child)

    yield from visit(document['design'])


def _constant_initial_value(node, kind, compiler):
    """Slang has already applied assignment widths and array element order."""
    if isinstance(kind, ArrayType):
        pattern = node.get('kind')
        if pattern in {'SimpleAssignmentPattern', 'StructuredAssignmentPattern'}:
            items = node['elements' if pattern == 'SimpleAssignmentPattern' else 'resolvedElements']
            if len(items) == kind.cells:
                return [_constant_initial_value(item, kind.element, compiler) for item in items]
    else:
        value = (integer_literal(node['constant']) if 'constant' in node
                 else compiler.constant_initializer(node))
        if value is not None:
            return value & ((1 << kind.width) - 1)
    raise Refused(Refusal('rtl-source-initializer-unencoded',
        'this declaration initialization needs a typed construction relation',
        node_kind=node.get('kind'), file=node.get('source_file_start'),
        line=node.get('source_line_start')))


def _default_initial_value(node, symbols):
    while isinstance(node, str):
        node = symbols[node.split(' ', 1)[0]]
    if node.get('kind') == 'TypeAlias':
        return _default_initial_value(node['target'], symbols)
    kind = source_type(node, symbols)
    if isinstance(kind, ArrayType):
        return [_default_initial_value(node['elementType'], symbols) for _ in range(kind.cells)]
    # Four-state uninitialized storage retains the caller's declared physical
    # support. A two-state static variable has a language-defined zero default.
    return None if integral_value_facts(node, symbols)[2] else 0


def declaration_initial_values(program, *, check=None):
    """Return values after declaration initialization, never inferred from a run."""
    defaults, initialized = _declaration_initialization(program, check=check)
    return {**defaults, **initialized}


def _declaration_initialization(program, *, check=None):
    """Separate physical defaults from HDL assignments before initial processes.

    ANSI output-variable initializers belong to their Port nodes. Bind them to
    internalSymbol, not a similarly named local or sibling instance. Net
    declaration assignments are continuous drivers, not initial restrictions.
    """
    symbols, bindings = program.compiler.symbols, program.bindings
    names = {field.key: name for name, field in program.fields.items()}
    processes = {process.initial_process for process in program.processes}
    values, defaults = {}, {}
    for node in _initial_nodes(program.compiler.document, symbols):
        if check is not None:
            check()
        if (node.get('kind') == 'ProceduralBlock' and node.get('procedureKind') == 'Initial'
                and str(node['addr']) not in processes):
            raise Refused(Refusal('rtl-source-initial-process-unencoded',
                'initial processes need a one-shot startup relation before exact analysis',
                file=node.get('source_file'), line=node.get('source_line')))
        identity = (node.get('internalSymbol', '').split(' ', 1)[0]
                    if node.get('kind') == 'Port' else str(node.get('addr', '')))
        field = bindings.get(identity)
        if field is None or field.input:
            continue
        symbol = symbols[identity]
        if symbol.get('kind') != 'Variable' or symbol.get('lifetime') != 'Static':
            continue
        name = names.get(field.key)
        if name is not None:
            defaults[name] = _default_initial_value(symbol['type'], symbols)
        if 'initializer' not in node:
            continue
        # Even an unobserved initializer can have nonlocal effects. Establish
        # compiler-constant construction before omitting its unused storage.
        value = _constant_initial_value(node['initializer'], source_type(symbol['type'], symbols),
                                        program.compiler)
        if name is not None:
            values[name] = value
    return defaults, values


def _restrict_initial_value(chosen, declared):
    if isinstance(declared, list):
        if chosen is None:
            chosen = [None] * len(declared)
        if not isinstance(chosen, (list, tuple)) or len(chosen) != len(declared):
            raise ValueError('initial source storage needs its complete compiler array shape')
        return [_restrict_initial_value(value, required) for value, required in zip(chosen, declared, strict=True)]
    if declared is None:
        return chosen
    if chosen is not None and (type(chosen) is not int or chosen != declared):
        raise Refused(Refusal('rtl-source-initial-support-conflict',
            'the requested physical initial support contradicts HDL initialization'))
    return declared


def _physical(kind, value, name, *, context, check):
    if check is not None:
        check()
    if isinstance(kind, ArrayType):
        if value is None:
            value = (None,) * kind.cells
        if not isinstance(value, (list, tuple)) or len(value) != kind.cells:
            raise ValueError('initial source storage needs its complete compiler array shape')
        return [_physical(kind.element, item, f'{name}[{index}]', context=context, check=check)
                for index, item in enumerate(value)]
    if value is None:
        return z3.BitVec(name, kind.width, ctx=context)
    kind.decode(value)  # Validate native bits, including signed storage's representation.
    return z3.BitVecVal(value, kind.width, ctx=context)


def _carrier(bits, kind, width):
    if isinstance(kind, ArrayType):
        return [_carrier(item, kind.element, width) for item in bits]
    if width < kind.width:
        raise ValueError('the source carrier cannot truncate physical initial storage')
    extend = z3.SignExt if kind.signed else z3.ZeroExt
    return extend(width - kind.width, bits) if width != kind.width else bits


def bind_initial_state(machine, template, values, *, tag='source_initial', check=None,
                       declarations=False):
    """Allocate full physical support and the same private startup as replay.

    Explicit fixed values use *unsigned bit patterns*, like native capture and
    RTLInitialState. None can occur at a complete field or an array leaf. All
    fields are named, even if a selected goal cone does not read them. Further
    correlated initial restrictions belong to the caller's domain formula.
    """
    if template.procedure is not machine.procedure:
        raise ValueError('initial source support must bind its own scheduler')
    if values.keys() != machine.program.fields.keys():
        raise ValueError('initial source support must explicitly name every physical field')
    declared = {}
    if declarations:
        defaults, declared = _declaration_initialization(machine.program, check=check)
        values = {name: _restrict_initial_value(value, defaults.get(name)) for name, value in values.items()}
    context, env = template.context, template.environment
    kinds = machine.program.kinds
    bits = {name: _physical(kinds[name], value, f'{len(tag)}:{tag}:{name}',
                           context=context, check=check) for name, value in values.items()}
    # A native receipt precedes HDL declaration assignments. Keep those physical
    # choices in bits; overwrite only the state entering the event scheduler.
    # Constraining a pre-HDL word to its later initializer rejects real runs and
    # accidentally masks the phase error when replay deposits that same value.
    constructed = {name: _physical(kinds[name], declared[name], f'{tag}:declaration:{name}',
                                  context=context, check=check) if name in declared else value
                   for name, value in bits.items()}
    state = {name: _carrier(value, kinds[name], validated_width(env[name], name))
             for name, value in constructed.items()}
    boundary = machine.boundary(state, startup=True)
    payloads = {name: write.target for write, _, _ in machine.queue_edges
                for name in (write.data, write.first)}
    state_types = dict(machine.procedure.semantics.state_types)
    for name, value in boundary.items():
        if check is not None:
            check()
        if name in state:
            continue
        if name in payloads:
            target = payloads[name]
            state[name] = _carrier(constructed[target], kinds[target], validated_width(env[name], name))
        else:
            encoded = encode_concrete_value(value, env[name], name, kind=state_types[name])
            state[name] = _map_tree(encoded, lambda term:
                term if isinstance(term, z3.ExprRef) and term.ctx == context else term.translate(context))
    return SourceInitialState(bits, state)
