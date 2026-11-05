"""Finite-array recipes inside a transition template, not solver array theory.

The template DAG shares both scalar expressions and persistent array operations.
Instantiation interprets its array constructors/read macros into FunctionalArray
values. All returned constraints and event predicates remain ordinary QF_BV.
"""

import z3

from ..core.construction_checkpoint import finish_construction
from .bitvectors import COMPARISONS, _wire_value
from .functional_array import (
    FunctionalArray,
    _ArrayBase,
    _ArraySelection,
    _ChoiceBase,
    _ConstantBase,
    _MemoryBase,
    _MergeBase,
    _PackedBase,
    _ParameterBase,
    _array_versions,
    _concat_slices,
    _widen_base,
)


class TemplateArrays:
    def __init__(self):
        self.constructors = {}
        self._conditional_arguments = {}
        self._functions = {}
        self._values = {}

    def bind(self, value):
        """Keep an intermediate array as a shared recipe, not a packed image.

        Reads remain template SELECTs until actual state is supplied. The
        ordinary instantiator eliminates every recipe before solving; its
        state-boundary owner still names costly concrete update histories.
        """
        return FunctionalArray._from_base(value.length,
            _ParameterBase(self.term(value), value.element_width))

    def _call(self, key, length, width, terms, build):
        conditional = {'choose': (0, 1, 2), 'store': (3, None, 0)}.get(key)
        key = (key, length, width, tuple(term.sort().sexpr() for term in terms))
        if key not in self._functions:
            name = f'_step_array_{len(self._functions)}'
            sort = z3.ArraySort(z3.BitVecSort(length.bit_length() + 1), z3.BitVecSort(width))
            self._functions[key] = z3.Function(name, *(term.sort() for term in terms), sort)
            self.constructors[name] = build
            if conditional is not None:
                self._conditional_arguments[name] = conditional
        return self._functions[key](*terms)

    def term(self, value):
        if not isinstance(value, FunctionalArray):
            return value
        key = id(value)
        if key in self._values:
            return self._values[key][1]
        for array in _array_versions(value):
            if id(array) not in self._values:
                self._term(array)
        return self._values[key][1]

    def _term(self, value):
        """Referenced versions have already been encoded by the DAG walk."""
        length, width = value.length, value.element_width

        def base_term(base):
            if isinstance(base, _ArrayBase):
                term = self.term(base.value)
                if base.result_width == base.value.element_width:
                    return term
                return self._call(('widen', base.result_width), length, base.result_width,
                    (term,), lambda a, width=base.result_width: a.widen(width))
            if isinstance(base, _ParameterBase):
                if base.result_width == base.value.range().size():
                    return base.value
                return self._call(('widen', base.result_width), length, base.result_width,
                    (base.value,), lambda a, width=base.result_width: a.widen(width))
            if isinstance(base, _ConstantBase):
                return self._call('uniform', length, base.value.size(), (base.value,),
                    lambda v: FunctionalArray.uniform(length, v))
            if isinstance(base, _MemoryBase):
                return self._call(('memory', base.name, base.data_width), length, base.result_width, (),
                    lambda: FunctionalArray.memory(length, base.name, base.data_width, base.result_width))
            if isinstance(base, _PackedBase):
                metadata = (base.storage_width, base.result_width, base.signed, base.lo, base.hi)
                return self._call(('packed', *metadata), length, base.result_width, (base.value,),
                    lambda v: FunctionalArray._from_base(length, _PackedBase(v, *metadata)))
            if isinstance(base, _ChoiceBase):
                return self._call('choose', length, width,
                    (base.condition, base_term(base.yes), base_term(base.no)), FunctionalArray.choose)
            if isinstance(base, _MergeBase):
                terms = tuple(self.term(a) for a in (base.current, base.data, base.mask))
                def merge(a, b, m, bits=base.width, signed=base.signed, result_width=base.result_width):
                    result = FunctionalArray.merge_bits(a, b, m, bits, signed=signed)
                    return FunctionalArray._from_base(length, _widen_base(result._base, result_width),
                                                       context=result._translation_context)
                return self._call(('merge', base.width, base.signed), length, base.result_width, terms, merge)
            raise TypeError('unknown template array base')

        term = base_term(value._base)
        for first, rows in value._read_history():
            if not rows:
                # A checked full-domain fill is one conditional replacement,
                # not N store recipes to instantiate at every clock boundary.
                filled = self._call('uniform', length, width, (first.value,),
                                    lambda v: FunctionalArray.uniform(length, v))
                term = (filled if first.unconditional else self._call('choose', length, width,
                    (first.guard, filled, term), FunctionalArray.choose))
            for row in rows:
                term = self._call('store', length, width, (term, row.index, row.value, row.guard),
                                  lambda a, i, v, g: a.store(i, v, g))
        self._values[id(value)] = value, term
        return term

    def freeze(self):
        """The compiled image owns its terms; discard construction-only DAGs."""
        self._values.clear()
        self._functions.clear()

    def instantiate(self, image, arguments, *, check=None):
        """Interpret array macros while sharing each scalar/template DAG node."""
        return finish_construction(self.instantiate_steps(image, arguments), check)

    def prepare(self, image):
        """Own the immutable instruction graph, not a step's evaluated values."""
        return _ArrayImage(self, image)

    def instantiate_steps(self, image, arguments):
        return (yield from self.prepare(image).instantiate_steps(arguments))


class _ArrayImage:
    """One template/arena's lazy structural decoding, bounded by its image DAG.

    The image owns the native node handles. Values, scalar definitions and array
    selections belong to each instantiation; none is retained here. Preparing a
    translated or projected image creates its own decoding owner.
    """

    def __init__(self, recipes, image):
        self.recipes = recipes
        self.image = image
        self.nodes = {}
        self.declarations = {}
        self.scalar_regions = {}
        self.scalar_parameters = {}
        self.literal_reads = {}

    def _node(self, term, key):
        from z3 import z3core as native

        if key not in self.nodes:
            ctx = self.image.ctx_ref()
            if native.Z3_get_ast_kind(ctx, term) == z3.Z3_VAR_AST:
                node = native.Z3_get_index_value(ctx, term), (), None, None
            else:
                children = tuple(native.Z3_get_app_arg(ctx, term, index)
                                 for index in range(native.Z3_get_app_num_args(ctx, term)))
                children = tuple((child, native.Z3_get_ast_id(ctx, child)) for child in children)
                declaration = native.Z3_get_app_decl(ctx, term)
                declaration_id = native.Z3_get_func_decl_id(ctx, declaration)
                if declaration_id not in self.declarations:
                    wrapped = z3.FuncDeclRef(declaration, self.image.ctx)
                    self.declarations[declaration_id] = str(wrapped.name()), wrapped.kind()
                name, kind = self.declarations[declaration_id]
                node = None, children, name, kind
                if kind == z3.Z3_OP_SELECT:
                    address, _ = children[1]
                    if native.Z3_get_ast_kind(ctx, address) == z3.Z3_NUMERAL_AST:
                        from z3.z3 import _to_expr_ref
                        at = _to_expr_ref(address, self.image.ctx).as_signed_long()
                        if at >= 0:
                            owner = children[0][1]
                            reads, high = self.literal_reads.setdefault(owner, (set(), -1))
                            reads.add(at)
                            if at > high:
                                self.literal_reads[owner] = reads, at
            self.nodes[key] = node
        return self.nodes[key]

    def _scalar_region_steps(self, term, key):
        """Find native-only subgraphs; array recipes remain interpreter barriers.

        A mask names exactly the scalar parameters below this node. None means
        the node needs the lazy control/finite-array interpreter. Do not walk
        inactive branches merely to find a native scalar region. This is
        immutable syntax, not an assumption about a boundary or solver answer.
        """
        from z3 import z3core as native

        pending = [(term, key, False)]
        ctx = self.image.ctx_ref()
        while pending:
            yield
            term, key, ready = pending.pop()
            if key in self.scalar_regions:
                continue
            parameter, children, name, kind = self._node(term, key)
            if parameter is not None:
                if native.Z3_get_sort_kind(ctx, native.Z3_get_sort(ctx, term)) == z3.Z3_ARRAY_SORT:
                    self.scalar_regions[key] = None
                else:
                    self.scalar_regions[key] = 1 << parameter
                    self.scalar_parameters[parameter] = term
            elif name in self.recipes.constructors or name == '_step_fields' or kind in (
                    z3.Z3_OP_SELECT, z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_ITE) or kind in COMPARISONS:
                self.scalar_regions[key] = None
            elif not ready:
                pending.append((term, key, True))
                pending.extend((child, child_key, False) for child, child_key in reversed(children)
                               if child_key not in self.scalar_regions)
            else:
                masks = tuple(self.scalar_regions[child_key] for _, child_key in children)
                mask = 0
                for child_mask in masks:
                    if child_mask is None:
                        mask = None
                        break
                    mask |= child_mask
                self.scalar_regions[key] = mask

    def instantiate_steps(self, arguments, *, _facts=None):
        """Reuse structural decoding; values belong only to this invocation.

        Keep the prepared image unchanged even for literal arguments. Rewriting
        the whole image into a temporary decoder at every step loses this
        sharing and repeatedly creates and releases large native DAGs. Literal
        branches and closed operations are evaluated by the lazy walk below.
        """
        from z3 import z3core as native
        from z3.z3 import _to_expr_ref
        from z3.z3types import Ast
        from .term_bounds import _SyntacticClosureProof, _compare_hulls

        image = self.image
        context, ctx = image.ctx, image.ctx_ref()
        # The image/arguments own unchanged nodes. Own rebuilt scalar nodes
        # until the returned wrappers/FunctionalArrays take their references.
        # Traversal itself needs native handles, not one Python ExprRef per edge.
        owned = z3.AstVector(ctx=context)
        values = {}
        booleans = {}
        substitutions = {}
        argument_values = {}
        literals = {}
        closed_masks = {}
        wires = {}
        wire_bounds = _SyntacticClosureProof({}) if _facts is None else _facts
        pending = [(image.as_ast(), image.get_id())]

        def scalar(term):
            return _to_expr_ref(term, context) if isinstance(term, Ast) else term

        def retain(term):
            native.Z3_ast_vector_push(ctx, owned.vector, term)
            return term

        def literal(term):
            if not isinstance(term, Ast):
                return False
            # The image, arguments and owned vector retain every handle for
            # this invocation. Aliases of the same evaluated value need no
            # repeated native kind/decl inspection, including shared literals.
            key = term.value
            if key not in literals:
                kind = native.Z3_get_ast_kind(ctx, term)
                literals[key] = kind == z3.Z3_NUMERAL_AST or (kind == z3.Z3_APP_AST and
                    native.Z3_get_decl_kind(ctx, native.Z3_get_app_decl(ctx, term))
                    in (z3.Z3_OP_TRUE, z3.Z3_OP_FALSE))
            return literals[key]

        def prepare_arguments(indices):
            pending = {}
            for index in indices:
                value = arguments[index]
                if not isinstance(value, z3.ExprRef):
                    continue
                key = value.get_id()
                if key in argument_values or key in pending:
                    continue
                term = value.as_ast()
                if literal(term):
                    argument_values[key] = term
                else:
                    pending[key] = value
            if not pending:
                return
            fields = tuple(pending.values())
            if len(fields) == 1:
                values = (retain(native.Z3_simplify(ctx, fields[0].as_ast())),)
            else:
                # These are demanded pure scalar arguments, not source
                # executions. A tuple-like application preserves every field
                # while one native rewrite shares their overlapping DAG.
                pack = z3.FreshFunction(*(field.sort() for field in fields),
                                        z3.BoolSort(ctx=context))
                packed = pack(*fields)
                root = retain(packed.as_ast())
                reduced = retain(native.Z3_simplify(ctx, root))
                values = tuple(retain(native.Z3_get_app_arg(ctx, reduced, index))
                               for index in range(len(fields)))
            argument_values.update(zip(pending, values, strict=True))

        def argument(index):
            value = arguments[index]
            if not isinstance(value, z3.ExprRef):
                return value
            key = value.get_id()
            if key not in argument_values:
                prepare_arguments((index,))
            return argument_values[key]

        def substitute(terms, mask):
            if mask not in substitutions:
                indices = []
                remaining = mask
                while remaining:
                    bit = remaining & -remaining
                    indices.append(bit.bit_length() - 1)
                    remaining ^= bit
                prepare_arguments(indices)
                before = (Ast * len(indices))(*(self.scalar_parameters[i] for i in indices))
                after = (Ast * len(indices))(*(argument(i) for i in indices))
                substitutions[mask] = before, after
            before, after = substitutions[mask]
            if len(terms) == 1:
                root = terms[0][0]
            else:
                # A pure tuple-like application preserves false, duplicate
                # and differently typed roots. Substitute their shared DAG
                # once, rather than walking every overlapping cone again.
                fields = [scalar(term) for term, _ in terms]
                pack = z3.FreshFunction(*(field.sort() for field in fields),
                                        z3.BoolSort(ctx=context))
                packed = pack(*fields)
                root = retain(packed.as_ast())
            value = (retain(native.Z3_substitute(ctx, root, len(before), before, after))
                     if mask else root)
            for index, (_, key) in enumerate(terms):
                values[key] = value if len(terms) == 1 else retain(native.Z3_get_app_arg(ctx, value, index))

        def closed(mask):
            if mask in closed_masks:
                return closed_masks[mask]
            indices = []
            remaining = mask
            while remaining:
                bit = remaining & -remaining
                indices.append(bit.bit_length() - 1)
                remaining ^= bit
            prepare_arguments(indices)
            closed_masks[mask] = all(literal(argument(index)) for index in indices)
            return closed_masks[mask]

        while pending:
            yield
            term, key = pending.pop()
            if key in values:
                continue
            parameter, children, name, kind = self._node(term, key)
            if parameter is not None:
                values[key] = argument(parameter)
                continue
            # Keep Boolean short circuit and array conditionals lazy. For an
            # ordinary scalar operation, let the native engine substitute the
            # entire array-free region instead of interpreting every edge in
            # Python on every transition. No array is converted to SMT arrays.
            if kind not in (z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_ITE) and kind not in COMPARISONS:
                if key not in self.scalar_regions:
                    yield from self._scalar_region_steps(term, key)
                mask = self.scalar_regions[key]
                if mask is not None and (not children or not closed(mask)):
                    substitute(((term, key),), mask)
                    continue
                # Ground regions use the shared value table below. Rewriting
                # whole overlapping ground cones here repeats their work at
                # every control boundary, even with sibling batching.
            if kind in (z3.Z3_OP_AND, z3.Z3_OP_OR):
                # These are pure formula nodes, not source evaluations. A
                # decisive constant needs neither the remaining operands nor
                # their array recipes. Keep each shared event/constraint root
                # independently: an unused Boolean operand is not a lost hit.
                conjunction = kind == z3.Z3_OP_AND
                decisive = z3.Z3_L_FALSE if conjunction else z3.Z3_L_TRUE
                index, kept = booleans.pop(key, (0, []))
                while index < len(children):
                    child, child_key = children[index]
                    if child_key not in values:
                        booleans[key] = index, kept
                        pending.extend(((term, key), (child, child_key)))
                        break
                    value = values[child_key]
                    truth = native.Z3_get_bool_value(ctx, value)
                    if truth == decisive:
                        values[key] = value
                        break
                    if truth == z3.Z3_L_UNDEF:
                        kept.append(value)
                    index += 1
                else:
                    if not kept:
                        # Native zero-argument AND/OR remain applications,
                        # not Boolean literals. Publish their identities so
                        # downstream conditionals can skip inactive recipes.
                        build = native.Z3_mk_true if conjunction else native.Z3_mk_false
                        values[key] = retain(build(ctx))
                    elif len(kept) == 1:
                        values[key] = kept[0]
                    else:
                        build = native.Z3_mk_and if conjunction else native.Z3_mk_or
                        values[key] = retain(build(ctx, len(kept), (Ast * len(kept))(*kept)))
                continue
            conditional = ((0, 1, 2) if kind == z3.Z3_OP_ITE else
                           self.recipes._conditional_arguments.get(name))
            if conditional is not None:
                condition, condition_key = children[conditional[0]]
                if condition_key not in values:
                    pending.extend(((term, key), (condition, condition_key)))
                    continue
                truth = native.Z3_get_bool_value(ctx, values[condition_key])
                selected = (None if truth == z3.Z3_L_UNDEF else
                            conditional[1 if truth == z3.Z3_L_TRUE else 2])
                if selected is not None:
                    # These are pure, typed expressions. A literal condition
                    # selects exactly one arm, including finite-array macros;
                    # no discarded arm can constrain the result or emit a hit.
                    child, child_key = children[selected]
                    if child_key not in values:
                        pending.extend(((term, key), (child, child_key)))
                    else:
                        values[key] = values[child_key]
                    continue
            missing = [(child, child_key) for child, child_key in reversed(children)
                       if child_key not in values]
            if missing:
                # Every child here is demanded: lazy Boolean/conditional
                # branches were handled above. Batch only array-free siblings;
                # recipes and reads keep their existing lazy interpreter path.
                regions, scalar_mask = [], 0
                for child, child_key in missing:
                    yield
                    if child_key not in self.scalar_regions:
                        yield from self._scalar_region_steps(child, child_key)
                    mask = self.scalar_regions[child_key]
                    if mask is not None:
                        regions.append((child, child_key, mask))
                        scalar_mask |= mask
                # Normalize the union once before testing sibling regions;
                # normalizing each mask first would repeat shared actual DAGs.
                closed(scalar_mask)
                roots, root_mask = {}, 0
                for child, child_key, mask in regions:
                    if not closed(mask):
                        roots[child_key] = child, child_key
                        root_mask |= mask
                if len(roots) > 1:
                    substitute(tuple(roots.values()), root_mask)
                pending.append((term, key))
                pending.extend((child, child_key) for child, child_key in missing if child_key not in values)
                continue
            args = [values[child_key] for _, child_key in children]
            if name in self.recipes.constructors:
                value = self.recipes.constructors[name](*(scalar(arg) for arg in args))
            elif kind == z3.Z3_OP_SELECT:
                array = args[0]
                reads, high = self.literal_reads.get(children[0][1], ((), -1))
                if len(reads) == array.length and high == array.length - 1:
                    # This immutable image requests the complete finite vector
                    # (e.g. RTL change/edge masks). Compose its history once,
                    # then extract the cells. Isolated or dynamic reads remain
                    # lazy. Decoding stays demand-driven: no inactive branch
                    # is traversed merely to discover potential reads.
                    yield from array._packed_value_steps(array.element_width, context)
                if _facts is None:
                    selected = array.select(scalar(args[1]))
                else:
                    # Retain the exact source, as the scalar encoder does.
                    # Control bounds can reuse its value union instead of
                    # reopening every packed read/index circuit. This adds no
                    # constraint and belongs to the caller's active prefix.
                    selection = _ArraySelection(array, scalar(args[1]))
                    selected = selection.term
                    _facts.array_selections.setdefault(selected.get_id(), selection)
                value = retain(selected.as_ast())
            elif name == '_step_fields':
                value = [scalar(arg) for arg in args]
            elif all(isinstance(arg, Ast) for arg in args):
                # Actual parameters can alias even when template variables
                # differ. Fold these operator-local identities before their
                # Boolean users decide which array recipes to instantiate;
                # never simplify the operands' arithmetic histories again.
                if kind == z3.Z3_OP_EQ and args[0].value == args[1].value:
                    value = retain(native.Z3_mk_true(ctx))
                elif kind == z3.Z3_OP_DISTINCT and len({arg.value for arg in args}) < len(args):
                    value = retain(native.Z3_mk_false(ctx))
                elif kind == z3.Z3_OP_ITE and args[1].value == args[2].value:
                    value = args[1]
                elif kind == z3.Z3_OP_CONCAT:
                    value = retain(_concat_slices(scalar(arg) for arg in args).as_ast())
                else:
                    value = (term if all(old.value == new.value
                                         for (old, _), new in zip(children, args, strict=True))
                             else retain(native.Z3_update_term(ctx, term, len(args), (Ast * len(args))(*args))))
                if args and all(literal(arg) for arg in args):
                    # Evaluate closed scalar operations only. Symbolic
                    # datapaths keep their DAG; this is not whole-cone rewriting.
                    value = retain(native.Z3_simplify(ctx, value))
                elif kind in COMPARISONS and len(args) == 2 and all(
                        z3.is_bv(scalar(arg)) and (_facts is not None or
                            _wire_value(scalar(arg), wires)) for arg in args):
                    # Native simplification leaves even wiring comparisons
                    # such as 7 <= ZeroExt(31, one_bit) undecided. Reuse the
                    # shared hull rules: wiring only by default, or the
                    # caller-owned active-prefix scalar definitions. Never
                    # import proposed ranges or solver/model assumptions.
                    hulls = [wire_bounds.coarse_signed_hull(scalar(arg)) for arg in args]
                    truth = _compare_hulls(kind, *hulls)
                    if truth is not None:
                        build = native.Z3_mk_true if truth else native.Z3_mk_false
                        value = retain(build(ctx))
            else:
                raise TypeError(f'unexpanded array macro {name}')
            values[key] = value
        return scalar(values[image.get_id()])
