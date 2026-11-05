"""Compact QF_BV representation of a finitely initialized array.

The ordinary encoder materializes a fixed array as one term per cell.  That is
the right representation for an arbitrary symbolic pre-state, but it needlessly
turns a large zeroed memory followed by a few indexed writes into the size of
the whole memory at every step.  ``FunctionalArray`` retains the same finite
componentwise semantics as a uniform default plus a persistent sequence of
guarded stores.  Selection expands only that sequence, so the resulting formula
still uses Boolean and bit-vector operations only (no SMT array theory).
Typed masked replacement also remains a lazy base: reading one word combines
only that word from the current, queued and mask arrays.
An explicit ``indexed`` projection is available to array-theory consumers;
ordinary encoding and template compilation do not select it automatically.

This representation is deliberately closed: elements are scalar bit-vectors,
the length is a fixed positive integer, and every update has an explicit
Boolean guard.  Nested and arbitrary symbolic arrays continue to use the
ordinary componentwise representation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from functools import lru_cache

import z3
from z3 import z3core as native
from z3.z3types import Ast

from behave.core import runtime_defaults as defaults

__all__ = ["FunctionalArray"]


@dataclass(frozen=True, slots=True)
class _ConstantBase:
    value: object


@dataclass(frozen=True, slots=True)
class _ParameterBase:
    """An internal transition-template parameter; eliminated before solving."""

    value: object
    result_width: int


@dataclass(frozen=True, slots=True)
class _IndexedBase:
    """An exact SMT-array image, distinct from an unexpanded template macro."""

    value: object
    result_width: int


@dataclass(frozen=True, slots=True)
class _MemoryBase:
    name: str
    data_width: int
    result_width: int


@dataclass(frozen=True, slots=True)
class _PackedBase:
    """An arbitrary finite component array carried by one QF_BV atom.

    Cell zero occupies the low ``storage_width`` bits.  Unsigned carriers omit
    the common leading zero bits and selection restores them to
    ``result_width``; full signed carriers retain all bits.  Thus every packed
    unconstrained boundary assignment is already in its physical carrier.
    A named successor may retain tighter independently proved value bounds;
    its owner must retain the defining equation, not constrain a free atom to
    those bounds. This is component wiring, not SMT array theory.
    """

    value: object
    storage_width: int
    result_width: int
    signed: bool
    lo: int
    hi: int


@dataclass(frozen=True, slots=True)
class _ChoiceBase:
    condition: object
    yes: object
    no: object


@dataclass(frozen=True, slots=True)
class _ArrayBase:
    """A shared immutable version, including its guarded store history."""

    value: object
    result_width: int


@dataclass(frozen=True, slots=True)
class _MergeBase:
    current: object
    data: object
    mask: object
    width: int
    signed: bool
    result_width: int


def _merge_cell(base, index):
    def bits(array):
        value = array.select(index)
        return (z3.SignExt(base.width - value.size(), value) if value.size() < base.width
                else value if value.size() == base.width
                else z3.Extract(base.width - 1, 0, value))
    a, b, m = (bits(array) for array in (base.current, base.data, base.mask))
    value = (a & ~m) | (b & m)
    extension = base.result_width - base.width
    return (value if not extension else z3.SignExt(extension, value) if base.signed
            else z3.ZeroExt(extension, value))


def _extend(term, width):
    extension = width - term.size()
    return term if not extension else z3.SignExt(extension, term)


def _widen(left, right):
    # These are already bit-vector ASTs. Query their native sort widths
    # without allocating and releasing two Python SortRef wrappers per alias.
    left_width = native.Z3_get_bv_sort_size(
        left.ctx_ref(), native.Z3_get_sort(left.ctx_ref(), left.as_ast()))
    right_width = native.Z3_get_bv_sort_size(
        right.ctx_ref(), native.Z3_get_sort(right.ctx_ref(), right.as_ast()))
    if left_width < right_width:
        return z3.SignExt(right_width - left_width, left), right
    if right_width < left_width:
        return left, z3.SignExt(left_width - right_width, right)
    return left, right


def _choose(condition, yes, no):
    # Every cell of a functional array already has its element_width. Stores,
    # widening and base composition establish that once; a read does not need
    # to re-infer or widen the two arms for every historical write.
    if z3.eq(yes, no):
        return yes
    context = condition.ctx
    truth = native.Z3_get_bool_value(context.ref(), condition.as_ast())
    if truth == z3.Z3_L_TRUE:
        return yes
    if truth == z3.Z3_L_FALSE:
        return no
    if context != yes.ctx or context != no.ctx:
        raise z3.Z3Exception("context mismatch")
    return z3.BitVecRef(native.Z3_mk_ite(
        context.ref(), condition.as_ast(), yes.as_ast(), no.as_ast()), context)


def _and_predicates(left, right):
    # Store guards and address equalities are already Boolean ASTs. Keep the
    # same ordered conjunction without Z3Py's general Python-value coercion.
    context = left.ctx
    if context != right.ctx:
        raise z3.Z3Exception("context mismatch")
    terms = (Ast * 2)(left.as_ast(), right.as_ast())
    return z3.BoolRef(native.Z3_mk_and(context.ref(), 2, terms), context)


def _same_index(left, right):
    if z3.eq(left, right):
        return z3.BoolVal(True, ctx=left.ctx)
    if z3.is_bv_value(left) and z3.is_bv_value(right):
        return z3.BoolVal(
            left.as_signed_long() == right.as_signed_long(), ctx=left.ctx)
    left, right = _widen(left, right)
    context = left.ctx
    if context != right.ctx:
        raise z3.Z3Exception("context mismatch")
    # Z3Py's numeral wrapper subclasses BitVecRef, so Python dispatches a
    # right-hand numeral's reflected equality first. Retain that orientation,
    # not just logical equivalence, while skipping general sort coercion.
    if isinstance(right, z3.BitVecNumRef) and not isinstance(left, z3.BitVecNumRef):
        left, right = right, left
    return z3.BoolRef(native.Z3_mk_eq(
        context.ref(), left.as_ast(), right.as_ast()), context)


def _same_write(left, right):
    """Whether two persistent-history rows are the same Z3 syntax."""

    return left is right or (z3.eq(left.guard, right.guard)
            and z3.eq(left.index, right.index)
            and z3.eq(left.value, right.value))


def _shared_prefix(left, right):
    """Length of the immutable write prefix shared by two versions."""

    common = 0
    for first, second in zip(left, right):
        if not _same_write(first, second):
            break
        common += 1
    return common


def _base_width(base):
    if type(base) is _ArrayBase:
        return base.result_width
    if type(base) in (_ParameterBase, _IndexedBase):
        return base.result_width
    if type(base) is _MergeBase:
        return base.result_width
    if type(base) is _ConstantBase:
        return base.value.size()
    if type(base) is _MemoryBase:
        return base.result_width
    if type(base) is _PackedBase:
        return base.result_width
    if type(base) is _ChoiceBase:
        return _base_width(base.yes)
    raise TypeError("unknown functional-array base")


def _term_context(*values):
    """Find the owning arena without enumerating cells or collecting the DAG.

    An implicit memory base has no Z3 terms and is context-neutral. Its owner
    supplies the arena when composing it with symbolic state or a predicate.
    """
    pending = list(reversed(values))
    while pending:
        value = pending.pop()
        if isinstance(value, FunctionalArray):
            if value.writes:
                return value.writes[0].guard.ctx
            pending.append(value._base)
        elif type(value) in (_ConstantBase, _ParameterBase, _IndexedBase, _PackedBase):
            return value.value.ctx
        elif type(value) is _ChoiceBase:
            return value.condition.ctx
        elif type(value) is _ArrayBase:
            pending.append(value.value)
        elif type(value) is _MergeBase:
            pending.extend((value.mask, value.data, value.current))
    return z3.main_ctx()


def _base_same(left, right):
    if left is right:
        return True
    if type(left) is not type(right):
        return False
    if type(left) is _ArrayBase:
        return left.value is right.value and left.result_width == right.result_width
    if type(left) in (_ParameterBase, _IndexedBase):
        return left.result_width == right.result_width and z3.eq(left.value, right.value)
    if type(left) is _MergeBase:
        return (left.current is right.current and left.data is right.data and left.mask is right.mask
                and (left.width, left.signed, left.result_width)
                == (right.width, right.signed, right.result_width))
    if type(left) is _ConstantBase:
        return z3.eq(left.value, right.value)
    if type(left) is _MemoryBase:
        return left == right
    if type(left) is _PackedBase:
        return (
            left.storage_width,
            left.result_width,
            left.signed,
            left.lo,
            left.hi,
        ) == (
            right.storage_width,
            right.result_width,
            right.signed,
            right.lo,
            right.hi,
        ) and z3.eq(left.value, right.value)
    if type(left) is _ChoiceBase:
        return (z3.eq(left.condition, right.condition)
                and _base_same(left.yes, right.yes)
                and _base_same(left.no, right.no))
    return False


def _base_leaf_select(base, index):
    if type(base) in (_ParameterBase, _IndexedBase):
        # The surrounding finite-array guard owns bounds. Truncation cannot
        # alias a valid index because this domain holds every finite address.
        width = base.value.domain().size()
        address = (z3.ZeroExt(width - index.size(), index) if index.size() < width else
                   index if index.size() == width else z3.Extract(width - 1, 0, index))
        return _extend(z3.Select(base.value, address), base.result_width)
    if type(base) is _MergeBase:
        return _merge_cell(base, index)
    if type(base) is _ConstantBase:
        return base.value
    if type(base) is _MemoryBase:
        from ..symbolic.memory_terms import memory_junk_term

        return memory_junk_term(
            base.name, index, base.data_width,
            result_width=base.result_width)
    if type(base) is _PackedBase:
        packed_width = base.value.size()
        literal = index.as_signed_long() if z3.is_bv_value(index) else None
        if literal is not None and 0 <= literal < packed_width // base.storage_width:
            low = literal * base.storage_width
            stored = z3.Extract(low + base.storage_width - 1, low, base.value)
            extension = base.result_width - base.storage_width
            result = (stored if not extension else z3.SignExt(extension, stored) if base.signed
                      else z3.ZeroExt(extension, stored))
            # Expose a closed word to template constant propagation; do not
            # recursively simplify arbitrary symbolic memory histories.
            return z3.simplify(result) if z3.is_bv_value(base.value) else result
        stored = _packed_select(base.value, index, base.storage_width)
        extension = base.result_width - base.storage_width
        if extension == 0:
            return stored
        return (z3.SignExt(extension, stored) if base.signed
                else z3.ZeroExt(extension, stored))
    raise TypeError("unknown functional-array base")


def _base_select(base, index):
    return _select_dag(base, index)


def _select_dag(root, index):
    """Read one address without recursion or visiting shadowed base history."""
    values, plans = {}, {}
    # Bounds and address aliases depend on this read, not on a version's data
    # or store guards. Share them across the version DAG, not just one node.
    # This lifetime owns every index term; no cache escapes into another read.
    comparisons, bounds = {}, {}
    key = (index.ctx, index.get_id())
    pending = [(root, False)]
    while pending:
        node, ready = pending.pop()
        identity = id(node)
        if identity in values:
            continue
        if isinstance(node, FunctionalArray):
            if key in node._selections:
                values[identity] = node._selections[key][1]
                continue
            if not ready:
                valid, zero, writes, base = node._read_plan(index, comparisons=comparisons, bounds=bounds)
                plans[identity] = valid, zero, writes, base
                children = () if isinstance(base, z3.ExprRef) else (base,)
            else:
                valid, zero, writes, base = plans.pop(identity)
                value = base if isinstance(base, z3.ExprRef) else values[id(base)]
                for guard, choices in reversed(writes):
                    if len(choices) == 1:
                        same, written = choices[0]
                        active = (guard if z3.is_true(same) else same if z3.is_true(guard)
                                  else _and_predicates(guard, same))
                        value = _choose(active, written, value)
                    else:
                        # Consecutive stores under one condition execute
                        # together. Apply their ordered aliases, then that
                        # condition once, instead of once per cell update.
                        selected = value
                        for same, written in reversed(choices):
                            selected = _choose(same, written, selected)
                        value = _choose(guard, selected, value)
                value = _choose(valid, value, zero)
                node._selections[key] = index, value
        elif type(node) is _ArrayBase:
            children = (node.value,)
            if ready:
                value = _extend(values[id(node.value)], node.result_width)
        elif type(node) is _ChoiceBase:
            children = node.yes, node.no
            if ready:
                value = _choose(node.condition, values[id(node.yes)], values[id(node.no)])
        elif type(node) is _MergeBase:
            children = node.current, node.data, node.mask
            if ready:
                value = _merge_cell(node, index)
        else:
            values[identity] = _base_leaf_select(node, index)
            continue
        if ready:
            values[identity] = value
        else:
            pending.append((node, True))
            pending.extend((child, False) for child in reversed(children)
                           if id(child) not in values)
    return values[id(root)]


def _array_versions(root, *, masked_inputs=True, packed_context=None):
    """Visit immutable versions without selecting a physical image width."""
    for array, _width in _packed_versions(
            root, None, packed_context, masked_inputs=masked_inputs):
        yield array


def _packed_versions(root, width, context, *, masked_inputs=True):
    """Child-first (version, width) images, sharing every requested projection.

    Truncation commutes with indexed stores and choices. A masked merge needs
    only the requested low bits, or its own width before signed extension.
    Thus a narrow boundary need not first build a wide vector of every cell.
    """
    pending, seen = [(root, width, False)], set()
    while pending:
        array, bits, ready = pending.pop()
        if ready:
            yield array, bits
            continue
        key = id(array), bits
        if key in seen:
            continue
        seen.add(key)
        image_width = array.element_width if bits is None else bits
        if context is not None and (image_width, context) in array._packed:
            continue
        canonical = (None if context is None else
                     array._packed.get((array.element_width, context)))
        if canonical is not None:
            array._packed[image_width, context] = _resize_packed_cells(
                canonical, array.element_width, image_width)
            continue
        pending.append((array, bits, True))
        bases = [array._base]
        while bases:
            base = bases.pop()
            if type(base) is _ArrayBase:
                pending.append((base.value, bits, False))
            elif type(base) is _MergeBase and masked_inputs:
                pending.extend((value, None if bits is None else min(bits, base.width), False)
                               for value in (base.current, base.data, base.mask))
            elif type(base) is _ChoiceBase:
                bases.extend((base.yes, base.no))


def _formula_terms(value):
    """Visit a shared version DAG once, not once per path through each join."""
    pending, seen, terms = [value], set(), {}
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, z3.ExprRef):
            terms.setdefault((value.ctx, value.get_id()), value)
        elif isinstance(value, FunctionalArray):
            pending.extend(reversed(value.writes))
            pending.append(value._base)
        elif isinstance(value, (_ConstantBase, _ParameterBase, _IndexedBase, _PackedBase,
                                _MemoryBase, _ChoiceBase, _ArrayBase, _MergeBase, _Write)):
            pending.extend(getattr(value, item.name) for item in reversed(fields(value))
                           if item.init)
    return tuple(terms.values())


def _widen_base(base, width):
    if _base_width(base) == width:
        return base
    if type(base) is _ArrayBase:
        return _ArrayBase(base.value, width)
    if type(base) in (_ParameterBase, _IndexedBase):
        return type(base)(base.value, width)
    if type(base) is _MergeBase:
        return _MergeBase(base.current, base.data, base.mask, base.width, base.signed, width)
    if type(base) is _ConstantBase:
        return _ConstantBase(_extend(base.value, width))
    if type(base) is _MemoryBase:
        return _MemoryBase(base.name, base.data_width, width)
    if type(base) is _PackedBase:
        return _PackedBase(
            base.value,
            base.storage_width,
            width,
            base.signed,
            base.lo,
            base.hi,
        )
    if type(base) is _ChoiceBase:
        return _ChoiceBase(
            base.condition,
            _widen_base(base.yes, width),
            _widen_base(base.no, width),
        )
    raise TypeError("unknown functional-array base")


def _choose_base(condition, yes, no):
    if _base_same(yes, no) or z3.is_true(condition):
        return yes
    if z3.is_false(condition):
        return no
    return _ChoiceBase(condition, yes, no)


def _has_packed_base(base):
    if type(base) is _ArrayBase:
        return True
    if type(base) in (_ParameterBase, _IndexedBase):
        return True
    if type(base) is _MergeBase:
        return True
    if type(base) is _PackedBase:
        return True
    if type(base) is _ChoiceBase:
        return _has_packed_base(base.yes) or _has_packed_base(base.no)
    return False


def _in_bounds(index, length):
    # SolverIR integers have signed semantics.  Widen the array bound instead
    # of truncating it to the index's current carrier.
    if z3.is_bv_value(index):
        return z3.BoolVal(0 <= index.as_signed_long() < length, ctx=index.ctx)
    bound_width = max(index.size(), max(1, int(length).bit_length() + 1))
    index = _extend(index, bound_width)
    return z3.And(
        index >= z3.BitVecVal(0, bound_width, ctx=index.ctx),
        index < z3.BitVecVal(length, bound_width, ctx=index.ctx),
    )


def _concat_slices(values):
    """Keep slice joins and repeated sign bits compact, without rewriting bases.

    Template array reads can unpack a symbolic vector and immediately repack
    it. Retaining those identity layers at every transition grows the history
    even when no memory word changes. Merge only adjacent slices of the SAME
    AST; this needs no model, range premise or array-content assumption.
    """
    merged = []
    for value in values:
        if (merged and z3.is_app_of(value, z3.Z3_OP_EXTRACT)
                and z3.is_app_of(merged[-1], z3.Z3_OP_EXTRACT)):
            left = merged[-1]
            high, low = left.params()
            other_high, other_low = value.params()
            base = value.arg(0)
            if low == other_high + 1 and left.arg(0).eq(base):
                merged[-1] = (base if high == base.size() - 1 and other_low == 0
                              else z3.Extract(high, other_low, base))
                continue
        merged.append(value)
    # Simplified sign extensions arrive as many identical one-bit operands.
    # Z3Py Concat rebuilds these as a binary chain. Preserve the same bits with
    # one extension, so each transition does not manufacture a width-sized DAG.
    extension = 0
    while z3.is_app_of(merged[0], z3.Z3_OP_SIGN_EXT):
        extension += merged[0].params()[0]
        merged[0] = merged[0].arg(0)
    repeat = 1
    if merged[0].size() == 1:
        while repeat < len(merged) and merged[0].eq(merged[repeat]):
            repeat += 1
    omitted = repeat - 1
    # Slice joining may already have reassembled the original signed word.
    # Its explicit sign bit is then redundant too, including a single copy.
    if repeat < len(merged) and z3.is_app_of(merged[0], z3.Z3_OP_EXTRACT):
        word = merged[repeat]
        if (merged[0].params() == [word.size() - 1, word.size() - 1]
                and merged[0].arg(0).eq(word)):
            omitted = repeat
    fields = merged[omitted:]
    extension += omitted
    result = fields[0] if len(fields) == 1 else z3.Concat(*fields)
    return z3.SignExt(extension, result) if extension else result


def _balanced_concat(values):
    """Concatenate high-to-low blocks with logarithmic expression depth."""

    layer = list(values)
    if not layer or any(not z3.is_bv(value) for value in layer):
        raise TypeError("a packed finite value needs bit-vector blocks")
    while len(layer) > 1:
        following = [
            _concat_slices((layer[index], layer[index + 1]))
            for index in range(0, len(layer) - 1, 2)
        ]
        if len(layer) % 2:
            following.append(layer[-1])
        layer = following
    return layer[0]


def _packed_literal(values, width, context, *, check=lambda: None):
    """Pack low-to-high concrete words without a Z3 node for every cell.

    Fixed-size numeral chunks avoid Python's decimal conversion limit, even
    for one very wide word. Chunking changes neither cell order nor bit width.
    No symbolic expression, observed value or context is cached.
    """
    if type(width) is not int or width < 1:
        raise ValueError('a packed literal needs positive-width words')
    chunk_width = 1024  # At most 309 decimal digits, below Python's minimum limit.
    chunk_mask = (1 << chunk_width) - 1
    pending, bits, parts = 0, 0, []
    for value in values:
        check()
        if type(value) is not int or not 0 <= value < 1 << width:
            raise ValueError('a packed literal word must fit its declared width')
        pending |= value << bits
        bits += width
        while bits >= chunk_width:
            check()
            parts.append(z3.BitVecVal(pending & chunk_mask, chunk_width, ctx=context))
            pending >>= chunk_width
            bits -= chunk_width
    if bits:
        parts.append(z3.BitVecVal(pending, bits, ctx=context))
    check()
    return _balanced_concat(reversed(parts))


def _repeat_block(value, count):
    """Repeat one block in logarithmic Z3 construction work."""

    if not z3.is_bv(value) or type(count) is not int or count < 1:
        raise TypeError("a repeated bit pattern needs one block and a count")
    selected = []
    block = value
    remaining = count
    while remaining:
        if remaining & 1:
            selected.append(block)
        remaining >>= 1
        if remaining:
            block = z3.Concat(block, block)
    return _balanced_concat(reversed(selected))


def _resize_packed_cells(value, source_width, result_width, *, signed=True):
    """Rewire each word's carrier, without reading its update history again."""
    if source_width == result_width:
        return value
    cells = []
    for low in range(0, value.size(), source_width):
        cell = z3.Extract(low + source_width - 1, low, value)
        if result_width < source_width:
            cell = z3.Extract(result_width - 1, 0, cell)
        else:
            extend = z3.SignExt if signed else z3.ZeroExt
            cell = extend(result_width - source_width, cell)
        cells.append(cell)
    return _balanced_concat(reversed(cells))


def _resize_cell(value, width, *, signed=True):
    if width < value.size():
        return z3.Extract(width - 1, 0, value)
    extend = z3.SignExt if signed else z3.ZeroExt
    return value if width == value.size() else extend(width - value.size(), value)


def _resize_indexed(value, width, *, signed=True):
    """Lift the existing scalar carrier conversion over an SMT array image."""
    source = value.range().size()
    if source == width:
        return value
    word = z3.BitVec('_array_map_cast', source, ctx=value.ctx)
    return z3.Map(_resize_cell(word, width, signed=signed).decl(), value)


@lru_cache(maxsize=defaults.SIM_FUNCTIONAL_ARRAY_CACHE_ENTRIES)
def _packed_memory(length, name, data_width, result_width):
    """The exact finite vector for one address-dependent memory base."""

    from ..core.memory import memory_junk_value

    cells = []
    for index in range(length - 1, -1, -1):
        cell = z3.BitVecVal(memory_junk_value(name, index, data_width), data_width)
        cells.append(_resize_cell(cell, result_width, signed=False))
    return _balanced_concat(cells)


def _base_packed_value(base, length, result_width, context):
    """Encode a base at the requested cell width, cell zero in low bits."""

    if type(base) is _ArrayBase:
        return base.value._packed_value(result_width, context)
    if type(base) is _MergeBase:
        # Bitwise replacement distributes over concatenation. Build it once
        # for the finite vector; only carrier conversion is per-word wiring.
        # Each input is signed-extended/truncated BEFORE the merge, exactly
        # like _merge_cell. The result's signedness owns its final extension.
        bits = min(base.width, result_width)
        a, b, m = (array._packed_value(bits, context)
                   for array in (base.current, base.data, base.mask))
        merged = (a & ~m) | (b & m)
        return _resize_packed_cells(merged, bits, result_width, signed=base.signed)
    if type(base) in (_ParameterBase, _IndexedBase):
        index_width = max(1, length.bit_length() + 1)
        return _balanced_concat(_resize_cell(_base_select(base, z3.BitVecVal(i, index_width, ctx=context)), result_width)
                                for i in range(length - 1, -1, -1))
    if type(base) is _ConstantBase:
        return _repeat_block(_resize_cell(base.value, result_width), length)
    if type(base) is _MemoryBase:
        packed = _packed_memory(
            length, base.name, base.data_width, result_width)
        # The shared cache contains only context-neutral constant images. Do
        # not retain query-owned arenas in that process-wide cache.
        return packed if packed.ctx == context else packed.translate(context)
    if type(base) is _PackedBase:
        return _resize_packed_cells(base.value, base.storage_width, result_width,
                                    signed=base.signed)
    if type(base) is _ChoiceBase:
        return z3.If(
            base.condition,
            _base_packed_value(base.yes, length, result_width, context),
            _base_packed_value(base.no, length, result_width, context),
        )
    raise TypeError("unknown functional-array base")


def _packed_select(packed, index, cell_width):
    """Select a word by successively halving its finite packed image.

    Unlike a whole-vector barrel shift, each address bit selects only the
    remaining half. The total selected width is linear in the memory size;
    the expression DAG has logarithmically many selectors. Padding is zero,
    not additional storage, and the original signed address still owns bounds.
    """
    length = packed.size() // cell_width
    levels = (length - 1).bit_length()
    padding = ((1 << levels) - length) * cell_width
    value = z3.ZeroExt(padding, packed) if padding else packed
    address = _extend(index, max(index.size(), levels))
    for bit in reversed(range(levels)):
        half = value.size() // 2
        value = z3.If(z3.Extract(bit, bit, address) == 0,
            z3.Extract(half - 1, 0, value),
            z3.Extract(value.size() - 1, half, value))
    return _choose(_in_bounds(index, length), value,
                   z3.BitVecVal(0, cell_width, ctx=packed.ctx))


def _packed_offset(index, packed_width, cell_width):
    """Compute the bit offset of a valid word at address width.

    The caller guards the update with the ORIGINAL signed address. Every
    valid product fits below packed_width; invalid addresses need no sentinel
    inside the wide shifts because they cannot enable the update. Only the
    final shift operand is extended to match its data, without enumerating cells.
    """
    length = packed_width // cell_width
    address_width = max(1, (length - 1).bit_length())
    offset_width = max(1, (packed_width - 1).bit_length())
    address = (z3.ZeroExt(address_width - index.size(), index) if index.size() < address_width
               else index if index.size() == address_width
               else z3.Extract(address_width - 1, 0, index))
    if offset_width > address_width:
        address = z3.ZeroExt(offset_width - address_width, address)
    offset = address * z3.BitVecVal(cell_width, offset_width, ctx=index.ctx)
    return z3.ZeroExt(packed_width - offset_width, offset) if packed_width > offset_width else offset


def _packed_store(packed, row, length, cell_width):
    """Apply one guarded scalar store to a full-width packed vector."""

    packed_width = packed.size()
    if packed_width != length * cell_width:
        raise TypeError("a packed vector does not match its finite array shape")
    if row.inactive:
        return packed
    if row.literal_index is not None:
        if not 0 <= row.literal_index < length:
            return packed
        low = row.literal_index * cell_width
        high = low + cell_width
        pieces = ([] if high == packed_width else
                  [z3.Extract(packed_width - 1, high, packed)])
        pieces.append(_resize_cell(row.value, cell_width))
        if low:
            pieces.append(z3.Extract(low - 1, 0, packed))
        return _choose(row.guard, _balanced_concat(pieces), packed)
    value = _resize_cell(row.value, cell_width)
    shift = _packed_offset(row.index, packed_width, cell_width)
    cell_mask = z3.ZeroExt(
        packed_width - cell_width,
        z3.BitVecVal((1 << cell_width) - 1, cell_width, ctx=packed.ctx),
    )
    payload = z3.ZeroExt(packed_width - cell_width, value)
    updated = (packed & ~(cell_mask << shift)) | (payload << shift)
    return _choose(z3.And(row.guard, _in_bounds(row.index, length)), updated, packed)


@dataclass(frozen=True, slots=True)
class _Write:
    guard: object
    index: object
    value: object
    literal_index: int | None = field(init=False, repr=False)
    inactive: bool = field(init=False, repr=False)
    unconditional: bool = field(init=False, repr=False)

    def __post_init__(self):
        # Immutable syntax facts, not path/range assumptions. Decoding these
        # again for every read makes constant-address initialization quadratic
        # in native Z3 calls even when all stored addresses are disjoint.
        object.__setattr__(self, "literal_index",
            self.index.as_signed_long() if z3.is_bv_value(self.index) else None)
        object.__setattr__(self, "inactive", z3.is_false(self.guard))
        object.__setattr__(self, "unconditional", z3.is_true(self.guard))


def _checked_writes(writes, width):
    """Validate supplied history once; internal updates preserve these types."""
    rows = tuple(writes)
    if any(type(row) is not _Write
           or not z3.is_bool(row.guard)
           or not z3.is_bv(row.index)
           or not z3.is_bv(row.value)
           or row.value.size() != width
           for row in rows):
        raise TypeError(
            "a functional array store has a Boolean guard and bit-vector "
            "index and value")
    return rows


def _translate_value(value, context, memo):
    """Translate a compact DAG without a closure retaining its native arena."""
    pending = [(value, False)]
    while pending:
        node, ready = pending.pop()
        key = id(node)
        if key in memo:
            continue
        if isinstance(node, z3.ExprRef):
            memo[key] = node if node.ctx == context else node.translate(context)
        elif isinstance(node, FunctionalArray):
            if node._translation_context == context:
                memo[key] = node
            elif ready:
                base = memo[id(node._base)]
                rows = tuple(memo[id(row)] for row in node.writes)
                # A term-free memory is semantically context-neutral, but its
                # read cache is not. Give a private arena its own cache owner.
                memo[key] = (node if base is node._base and _term_context(node) == context and all(
                    before is after for before, after in zip(node.writes, rows, strict=True))
                    else type(node)._from_base(node.length, base, rows))
                object.__setattr__(memo[key], '_translation_context', context)
            else:
                pending.append((node, True))
                pending.extend((child, False) for child in (node._base, *node.writes))
        elif isinstance(node, (_ConstantBase, _ParameterBase, _IndexedBase, _PackedBase, _MemoryBase,
                               _ChoiceBase, _ArrayBase, _MergeBase, _Write)):
            members = [getattr(node, item.name) for item in fields(node) if item.init]
            if ready:
                translated = [memo[id(child)] for child in members]
                memo[key] = (node if all(before is after for before, after in
                    zip(members, translated, strict=True)) else type(node)(*translated))
            else:
                pending.append((node, True))
                pending.extend((child, False) for child in members)
        else:
            memo[key] = node
    return memo[id(value)]


@dataclass(frozen=True, slots=True, init=False)
class _ArraySelection:
    """An exact selection together with its persistent source array.

    The term is constructed here, never supplied as an independent assertion.
    Range proofs can share the source-value proof across different indices;
    the solver still receives the ordinary, unchanged QF_BV selection term.
    """

    array: object
    term: object

    def __init__(self, array, index):
        if type(array) is not FunctionalArray:
            raise TypeError("a selection source is a functional array")
        object.__setattr__(self, "array", array)
        object.__setattr__(self, "term", array.select(index))


class FunctionalArray:
    """One fixed scalar array represented by a compact base and guarded stores."""

    __slots__ = ("_base", "_packed", "_indexed", "_selections", "_read_blocks", "_translation_context", "length", "writes")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("FunctionalArray is a closed internal value type")

    def __init__(self, length, default, writes=()):
        if not z3.is_bv(default):
            raise TypeError("a functional array default is a bit-vector term")
        self._set(length, _ConstantBase(default), _checked_writes(writes, default.size()))

    @classmethod
    def _from_base(cls, length, base, writes=(), *, context=None):
        # Closed internal constructors either retain immutable rows or create
        # them with typed Z3 operations. Revalidating the complete prefix after
        # every append makes n stores require n² native sort/width inspections.
        value = object.__new__(cls)
        value._set(length, base, writes, context=context)
        return value

    def _set(self, length, base, writes, *, context=None):
        if type(length) is not int or type(length) is bool or length < 1:
            raise TypeError("a functional array has a positive fixed length")
        rows = tuple(writes)
        # A scalar leaf owns exactly one arena. Derived values inherit that
        # fact only when all newly attached terms/children have the same owner.
        # Term-free memory bases deliberately remain context-neutral.
        if context is None and not rows and type(base) in (_ConstantBase, _ParameterBase, _IndexedBase, _PackedBase):
            context = base.value.ctx
        object.__setattr__(self, "length", length)
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "writes", rows)
        object.__setattr__(self, "_selections", {})
        object.__setattr__(self, "_packed", {})
        object.__setattr__(self, "_indexed", {})
        object.__setattr__(self, "_read_blocks", None)
        object.__setattr__(self, "_translation_context", context)

    def __setattr__(self, _name, _value):
        raise AttributeError("a functional array is immutable")

    @classmethod
    def uniform(cls, length, value):
        return cls(length, value)

    @classmethod
    def from_cells(cls, cells):
        """Retain an exact finite scalar vector as a uniform base plus stores."""
        width = max(cell.size() for cell in cells)
        result = cls.uniform(len(cells), _extend(cells[0], width))
        bits = len(cells).bit_length() + 1
        rows = []
        for i, cell in enumerate(cells[1:], 1):
            cell = _extend(cell, width)
            if not z3.eq(cell, result.default):
                rows.append(_Write(z3.BoolVal(True, ctx=cell.ctx), z3.BitVecVal(i, bits, ctx=cell.ctx), cell))
        return cls._from_base(len(cells), result._base, rows)

    @classmethod
    def from_packed(cls, value, length, width):
        """An exact full-carrier vector, with cell zero in the low bits."""
        if (type(length) is not int or length < 1 or type(width) is not int or width < 1
                or not z3.is_bv(value) or value.size() != length * width):
            raise ValueError("a packed array needs one complete fixed-width vector")
        return cls._from_base(length, _PackedBase(
            value, width, width, True, -(1 << (width - 1)), (1 << (width - 1)) - 1))

    def widen(self, width):
        if width == self.element_width:
            return self
        return type(self)._from_base(self.length, _widen_base(self._base, width),
            tuple(_Write(row.guard, row.index, _extend(row.value, width)) for row in self.writes),
            context=self._translation_context)

    def translate(self, context):
        """Move arenas, sharing unchanged immutable nodes and their read recipes.

        Reuse is decided bottom-up, not from the first term's arena: a composed
        value may contain leaves from different contexts. No target-context
        value is cached on its source, so a long-lived template cannot retain
        a completed query's private arena through translation.
        """
        return _translate_value(self, context, {})

    @classmethod
    def parameter(cls, length, name, width):
        """A recipe placeholder, never an SMT-array solver representation.

        StepTemplate replaces every read with the supplied finite QF_BV value
        before returning an encoding. The array sort describes only the typed
        macro's signature; no array axioms or free reads enter the solver.
        """
        term = z3.Array(name, z3.BitVecSort(length.bit_length() + 1), z3.BitVecSort(width))
        return cls._from_base(length, _ParameterBase(term, width))

    @classmethod
    def merge_bits(cls, current, data, mask, width, *, signed):
        """Lazy componentwise replacement; selecting one word stays word-sized.

        This is a finite QF_BV value, not an SMT array or a new constraint.
        Unwritten bits come from the commit-time current value.
        """
        if not current.length == data.length == mask.length:
            raise TypeError("bit replacement requires identical finite array shapes")
        base = _MergeBase(current, data, mask, width, signed, width + int(not signed))
        context = current._translation_context
        if data._translation_context != context or mask._translation_context != context:
            context = None
        return cls._from_base(current.length, base, context=context)

    @classmethod
    def memory(cls, length, name, data_width, result_width, writes=()):
        """A task memory with an exact address-dependent implicit base."""

        if (type(name) is not str or not name
                or type(data_width) is not int or isinstance(data_width, bool)
                or data_width < 1
                or type(result_width) is not int
                or isinstance(result_width, bool)
                or result_width < data_width):
            raise TypeError(
                "a symbolic memory base has a name and nontruncating widths")
        return cls._from_base(
            length, _MemoryBase(name, data_width, result_width),
            _checked_writes(writes, result_width))

    @classmethod
    def symbolic(cls, length, name, result_width, lo, hi):
        """An arbitrary component array with one exact compact QF_BV carrier.

        A compact representation exists when the semantic carrier is a
        singleton, the complete signed image of ``result_width``, or an
        unsigned ``k``-bit image.  Other intervals continue to use the ordinary
        componentwise representation: silently widening their state set would
        make a closure proof unsound.
        """

        if (type(name) is not str or not name
                or type(result_width) is not int
                or isinstance(result_width, bool) or result_width < 1
                or type(lo) is not int or isinstance(lo, bool)
                or type(hi) is not int or isinstance(hi, bool) or hi < lo):
            raise TypeError("a symbolic array needs one finite scalar carrier")
        if type(length) is not int or isinstance(length, bool) or length < 1:
            raise TypeError("a symbolic array has a positive fixed length")
        signed_lo = -(1 << (result_width - 1))
        signed_hi = (1 << (result_width - 1)) - 1
        if lo == hi:
            return cls.uniform(length, z3.BitVecVal(lo, result_width))
        if (lo, hi) == (signed_lo, signed_hi):
            storage_width = result_width
            signed = True
        elif lo == 0 and (hi + 1) & hi == 0 and hi <= signed_hi:
            storage_width = hi.bit_length()
            signed = False
        else:
            raise ValueError(
                "the scalar interval has no compact exact packed restriction")
        value = z3.BitVec(name, length * storage_width)
        return cls._from_base(
            length,
            _PackedBase(
                value, storage_width, result_width, signed, lo, hi),
        )

    @classmethod
    def from_uniform(cls, value, result_width):
        """Encode one compact concrete scalar array at ``result_width``."""

        from .uniform_array import UniformArray

        if type(value) is not UniformArray:
            raise TypeError("a functional array needs one UniformArray value")
        if type(result_width) is not int or result_width < 1:
            raise TypeError("a functional array needs a positive result width")

        def scalar(item):
            if type(item) is bool:
                item = int(item)
            elif type(item) is not int:
                raise TypeError("a functional scalar array contains integers")
            return z3.BitVecVal(item, result_width)

        if value.is_uniform:
            out = cls.uniform(value.length, scalar(value.default))
        else:
            _tag, name, data_width = value.base_image
            if result_width < data_width:
                raise ValueError("a functional memory carrier truncates its data")
            out = cls.memory(value.length, name, data_width, result_width)
        index_width = max(1, value.length.bit_length() + 1)
        for index, item in value.sparse_items():
            out = out.store(
                z3.BitVecVal(index, index_width), scalar(item), z3.BoolVal(True))
        return out

    @property
    def default(self):
        """The uniform default; indexed bases intentionally have no such value."""

        if type(self._base) is not _ConstantBase:
            raise AttributeError("an indexed functional array has no default term")
        return self._base.value

    @property
    def element_width(self):
        return _base_width(self._base)

    @property
    def data_terms(self):
        """Scalar data leaves, used only by carrier validation."""
        representative = z3.BitVecVal(0, self.element_width)
        return (representative,) + tuple(row.value for row in self.writes)

    @property
    def formula_terms(self):
        """All terms from which this persistent value is constructed."""
        return _formula_terms(self)

    @property
    def base_restrictions(self):
        """Compatibility seam: packed component products need no premises.

        Free packed boundaries use exactly representable carriers. Derived
        images obtain any tighter bounds from their source terms and retained
        defining equations, never by adding an assumed range restriction.
        """

        return ()

    def range_cases(self, *, guarded=True):
        """Guarded sources from which any selected scalar value can come.

        Each row is ``(guard, term, intrinsic_bounds)``.  Exactly one of
        ``term`` and ``intrinsic_bounds`` is present.  A caller can therefore
        prove componentwise range closure without enumerating the finite
        domain represented by a compact base.

        Branch-insensitive range consumers use ``guarded=False``: the same
        source union is returned with None guards, without building conditions
        they would discard. Exact guarded membership keeps the default.
        """

        # Propagate all incoming path guards before visiting a shared version.
        # A diamond must neither duplicate its complete history nor discard a
        # path's guard. OR joins path guards; store order is irrelevant here
        # because this API already returns all possible source values.
        order, seen, pending = [], set(), [(self, False)]
        while pending:
            node, ready = pending.pop()
            if ready:
                order.append(node)
                continue
            if id(node) in seen:
                continue
            seen.add(id(node))
            children = ((node._base,) if isinstance(node, FunctionalArray) else
                        (node.value,) if type(node) is _ArrayBase else
                        (node.yes, node.no) if type(node) is _ChoiceBase else ())
            pending.append((node, True))
            pending.extend((child, False) for child in reversed(children))
        guards = {}
        if guarded:
            context = next(iter(self.formula_terms), z3.BoolVal(True)).ctx
            guards[id(self)] = z3.BoolVal(True, ctx=context)
        rows = {}

        def reach(node, guard):
            if not guarded:
                return
            key = id(node)
            guards[key] = z3.Or(guards[key], guard) if key in guards else guard

        def source(guard, term, bounds):
            key = (term.ctx, term.get_id()) if term is not None else bounds
            if guarded and key in rows:
                guard = z3.Or(rows[key][0], guard)
            rows[key] = (guard, term, bounds)

        for base in reversed(order):
            guard = guards[id(base)] if guarded else None
            if isinstance(base, FunctionalArray):
                reach(base._base, guard)
                for row in base.writes:
                    source(z3.And(guard, row.guard) if guarded else None, row.value, None)
            elif type(base) is _ArrayBase:
                reach(base.value, guard)
            elif type(base) is _ConstantBase:
                source(guard, base.value, None)
            elif type(base) is _MemoryBase:
                source(guard, None, (0, (1 << base.data_width) - 1))
            elif type(base) is _PackedBase:
                source(guard, None, (base.lo, base.hi))
            elif type(base) in (_ParameterBase, _IndexedBase):
                width = base.value.range().size()
                source(guard, None, (-(1 << (width - 1)), (1 << (width - 1)) - 1))
            elif type(base) is _MergeBase:
                lower = -(1 << (base.width - 1)) if base.signed else 0
                upper = (1 << (base.width - int(base.signed))) - 1
                source(guard, None, (lower, upper))
            elif type(base) is _ChoiceBase:
                reach(base.yes, z3.And(guard, base.condition) if guarded else None)
                reach(base.no, z3.And(guard, z3.Not(base.condition)) if guarded else None)
            else:
                raise TypeError("unknown functional-array base")
        return tuple(rows.values())

    def store(self, index, value, guard):
        if not z3.is_bv(index) or not z3.is_bv(value) or not z3.is_bool(guard):
            raise TypeError("a functional array store has malformed terms")
        width = max(self.element_width, value.size())
        base = _widen_base(self._base, width)
        rows = self.writes if width == self.element_width else tuple(
            _Write(row.guard, row.index, _extend(row.value, width))
            for row in self.writes)
        valid = _in_bounds(index, self.length)
        active = guard if z3.is_true(valid) else z3.And(guard, valid)
        context = self._translation_context
        if any(term.ctx != context for term in (index, value, guard)):
            context = None
        return FunctionalArray._from_base(
            self.length, base,
            rows + (_Write(active, index, _extend(value, width)),),
            context=context,
        )

    def select(self, index):
        if not z3.is_bv(index):
            raise TypeError("a functional array index is a bit-vector term")
        # This array version is immutable. Shared merge branches and repeated
        # reads therefore need the same selection formula only once. Retain
        # the index as well as the result so its native AST ID cannot be reused
        # while the entry is live, even when the result discards the index.
        key = (index.ctx, index.get_id())
        cached = self._selections.get(key)
        if cached is None:
            packed = self._packed.get((self.element_width, index.ctx))
            if packed is not None and z3.is_bv_value(index):
                at = index.as_signed_long()
                width = self.element_width
                value = (z3.Extract((at + 1) * width - 1, at * width, packed)
                         if 0 <= at < self.length else z3.BitVecVal(0, width, ctx=index.ctx))
                if z3.is_bv_value(packed):
                    value = z3.simplify(value)
            else:
                value = self._select(index)
            self._selections[key] = index, value
            return value
        return cached[1]

    def _select(self, index):
        return _select_dag(self, index)

    def _read_history(self):
        """Group immutable writes; recognize an exact full-domain fill.

        A block writing the same value to every literal cell under one guard
        replaces the array on that branch. Keep the original history for all
        other consumers; only its read recipe is shared by subsequent reads.
        """
        if self._read_blocks is not None:
            return self._read_blocks
        blocks = []
        for row in self.writes:
            if row.inactive:
                continue
            if blocks and z3.eq(blocks[-1][0].guard, row.guard):
                blocks[-1][1].append(row)
            else:
                blocks.append((row, [row]))
        result = []
        for first, rows in blocks:
            fill = (len(rows) >= self.length
                    and all(z3.eq(row.value, first.value) for row in rows)
                    and len({row.literal_index for row in rows
                             if row.literal_index is not None
                             and 0 <= row.literal_index < self.length}) == self.length)
            result.append((first, () if fill else tuple(rows)))
        result = tuple(result)
        object.__setattr__(self, "_read_blocks", result)
        return result

    def _read_plan(self, index, *, comparisons, bounds):
        valid = bounds.get(self.length)
        if valid is None:
            valid = bounds[self.length] = _in_bounds(index, self.length)
        zero = z3.BitVecVal(0, self.element_width, ctx=index.ctx)
        if z3.is_false(valid):
            return valid, zero, (), zero
        # Read-over-write, newest first. Distinct literal indices cannot
        # alias; a known active matching write hides all older history.
        # Unknown aliases and guards retain their original priority.
        pending = []
        always = z3.BoolVal(True, ctx=index.ctx)
        literal = index.as_signed_long() if z3.is_bv_value(index) else None
        for first, rows in reversed(self._read_history()):
            if not rows:
                if first.unconditional:
                    return valid, zero, pending, first.value
                pending.append((first.guard, [(always, first.value)]))
                continue
            choices = []
            pending.append((first.guard, choices))
            for row in reversed(rows):
                if literal is not None and row.literal_index is not None:
                    if literal != row.literal_index:
                        continue
                    same = always
                else:
                    # An address equality is independent of the store's data
                    # and guard. Its ownership remains local to this read.
                    key = row.index.ctx, row.index.get_id()
                    same = comparisons.get(key)
                    if same is None:
                        same = _same_index(index, row.index)
                        comparisons[key] = same
                    if z3.is_false(same):
                        continue
                if row.unconditional and z3.is_true(same):
                    if not choices:
                        pending.pop()
                    return valid, zero, pending, row.value
                choices.append((same, row.value))
            if not choices:
                pending.pop()
        return valid, zero, pending, self._base

    def bind_state_steps(self, *, tag, bind=None, bounds=None, recipes=None):
        """Name a costly finite history with exact successor equations.

        Sparse histories and uniform fills stay lazy. Once a symbolic read
        visits more write/choice/merge cases than there are cells, define all
        cells at this boundary instead of expanding those histories at every
        later read. Count shared ancestors once, including histories hidden
        behind array choices and masked updates, not only direct writes.
        This is representation selection, not a history/work limit. No cell,
        guard or value is discarded, and no range invariant is assumed.
        The caller must retain the returned equation with this successor.
        An existing SSA owner may instead supply its binder, which owns the
        exact defining equation and shares an already-named image.
        That owner may also prove source-value bounds, without a proposed
        state-range premise. Retain both those bounds and a sufficient storage
        width; selection restores the original word width. Merely rounding
        the bounds to a bit width would lose exact facts such as 0 <= cell <= 5.
        During template compilation, an existing recipe owner can share the
        update instead; concrete successor equations belong to instantiation.
        """
        pending, seen, cases = [self], set(), 0
        while pending and cases <= self.length:
            node = pending.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            yield
            if isinstance(node, FunctionalArray):
                cases += sum(max(1, len(rows)) for _, rows in node._read_history())
                pending.append(node._base)
            elif isinstance(node, _ArrayBase):
                pending.append(node.value)
            elif isinstance(node, _ChoiceBase):
                cases += 1
                pending.extend((node.yes, node.no))
            elif isinstance(node, _MergeBase):
                cases += 1
                pending.extend((node.current, node.data, node.mask))
        if cases <= self.length:
            return self, ()
        # A reusable transition has array recipes, not actual entry storage.
        # Share the costly update there without prematurely reading/packing
        # every cell. Keep sparse writes and full-domain fills above intact.
        if recipes is not None:
            return recipes.bind(self), ()
        context = _term_context(self)
        # Naming the entire vector must not read its complete history once
        # per cell. Compose the shared write/choice/mask DAG once instead.
        width = self.element_width
        low, high = -(1 << (width - 1)), (1 << (width - 1)) - 1
        proved = None if bounds is None else bounds(self)
        if proved is not None:
            lo, hi = proved
            if type(lo) is not int or type(hi) is not int or not low <= lo <= hi <= high:
                raise ValueError("proved array bounds must fit its signed result carrier")
            low, high = lo, hi
        signed = low < 0 or width == 1
        bits = (max(1, high.bit_length()) if not signed else
                max((max(0, -low - 1)).bit_length(), max(0, high).bit_length()) + 1)
        image = yield from self._packed_value_steps(bits, context)
        atom = (z3.FreshConst(image.sort(), tag + '_array_state')
                if bind is None else bind(image))
        successor = type(self)._from_base(self.length,
            _PackedBase(atom, bits, width, signed, low, high))
        return successor, (atom == image,) if bind is None else ()

    def compact(self, lo=None, hi=None):
        """Commit the finite store history into one exact packed state value.

        This is used at a proved state-layer boundary.  A full-width cell is
        retained, so committing never truncates a value; ``lo`` and ``hi`` only
        record the already-proved carrier for later structural closure checks.
        """

        width = self.element_width
        signed_lo = -(1 << (width - 1))
        signed_hi = (1 << (width - 1)) - 1
        lo = signed_lo if lo is None else lo
        hi = signed_hi if hi is None else hi
        if (type(lo) is not int or isinstance(lo, bool)
                or type(hi) is not int or isinstance(hi, bool)
                or not signed_lo <= lo <= hi <= signed_hi):
            raise ValueError("a packed state carrier fits its signed cell width")
        packed = self._packed_value(width)
        return type(self)._from_base(
            self.length,
            _PackedBase(packed, width, width, True, lo, hi),
        )

    def indexed(self):
        """Project finite words into a persistent SMT Select/Store/Map image.

        This is an explicit solver representation, NOT a template parameter
        or a QF_BV encoding. The ordinary encoder and state binder are unchanged.
        Only a caller that accepts array theory may opt into this projection.
        No fresh free state, assumed ranges or whole-array equations are added.
        Original signed bounds still guard every read and write; values outside
        the finite domain are unobservable. Model decoding and equality retain
        the same finite component semantics.
        """
        from ..core.construction_checkpoint import finish_construction

        return finish_construction(self.indexed_steps())

    def indexed_steps(self):
        """Cancelable, child-first projection; share immutable images once."""
        yield
        if type(self._base) is _IndexedBase and not self.writes:
            return self
        context = self._translation_context or _term_context(self)
        if context in self._indexed:
            return type(self)._from_base(self.length, _IndexedBase(self._indexed[context], self.element_width))
        for array in _array_versions(self):
            yield
            if context in array._indexed:
                continue
            address = z3.BitVecSort(array.length.bit_length() + 1, ctx=context)

            def base_image(base):
                if type(base) is _ArrayBase:
                    return _resize_indexed(base.value._indexed[context], base.result_width)
                if type(base) is _IndexedBase:
                    return _resize_indexed(base.value, base.result_width)
                if type(base) is _ConstantBase:
                    return z3.K(address, base.value)
                if type(base) is _ChoiceBase:
                    yes = yield from base_image(base.yes)
                    no = yield from base_image(base.no)
                    return z3.If(base.condition, yes, no)
                if type(base) is _MergeBase:
                    a, b, m = (_resize_indexed(child._indexed[context], base.width)
                               for child in (base.current, base.data, base.mask))
                    x, y = z3.BitVecs('_array_map_x _array_map_y', base.width, ctx=context)
                    left = z3.Map((x & y).decl(), a, z3.Map((~x).decl(), m))
                    right = z3.Map((x & y).decl(), b, m)
                    return _resize_indexed(z3.Map((x | y).decl(), left, right),
                                           base.result_width, signed=base.signed)
                if type(base) is _ParameterBase:
                    raise TypeError('a template array must be instantiated before solver projection')
                if type(base) not in (_PackedBase, _MemoryBase):
                    raise TypeError('unknown functional-array base')
                # Import each original finite image once, never each update.
                # Literal addresses avoid memory-wide shifts even for a
                # packed arbitrary initial state. The output stays word-sized.
                image = z3.K(address, z3.BitVecVal(0, _base_width(base), ctx=context))
                for index in range(array.length):
                    yield
                    at = z3.BitVecVal(index, address.size(), ctx=context)
                    image = z3.Store(image, at, _base_leaf_select(base, at))
                return image

            image = yield from base_image(array._base)
            for first, rows in array._read_history():
                yield
                if not rows:
                    image = z3.If(first.guard, z3.K(address, first.value), image)
                for row in rows:
                    yield
                    at = _resize_cell(row.index, address.size())
                    image = z3.If(row.guard, z3.Store(image, at, row.value), image)
            array._indexed[context] = image
        return type(self)._from_base(self.length, _IndexedBase(self._indexed[context], self.element_width))

    def _packed_value(self, width, context=None):
        from ..core.construction_checkpoint import finish_construction

        context = _term_context(self) if context is None else context
        return finish_construction(self._packed_value_steps(width, context))

    def _packed_value_steps(self, width, context):
        if type(width) is not int or width < 1:
            raise ValueError('a packed image needs a positive cell width')
        key = width, context
        if key in self._packed:
            return self._packed[key]
        for array, cell_width in _packed_versions(self, width, context):
            yield
            own_key = cell_width, context
            if own_key not in array._packed:
                packed = _base_packed_value(array._base, array.length, cell_width, context)
                for first, rows in array._read_history():
                    yield
                    if not rows:
                        packed = _choose(first.guard, _repeat_block(
                            _resize_cell(first.value, cell_width), array.length), packed)
                    for row in rows:
                        yield
                        packed = _packed_store(packed, row, array.length, cell_width)
                array._packed[own_key] = packed
        return self._packed[key]

    @classmethod
    def choose(cls, condition, yes, no):
        """The finite array selected by ``condition`` without cell expansion."""
        if (not z3.is_bool(condition)
                or type(yes) is not cls
                or type(no) is not cls
                or yes.length != no.length):
            raise TypeError("conditional arrays need one functional shape")
        if z3.is_true(condition) or yes is no:
            return yes
        if z3.is_false(condition):
            return no
        context = (condition.ctx if yes._translation_context == no._translation_context == condition.ctx
                   else None)
        width = max(yes.element_width, no.element_width)
        shared = _shared_prefix(yes.writes, no.writes)
        if (not _base_same(yes._base, no._base)
                or shared < min(len(yes.writes), len(no.writes))):
            # Two independently updated values are a DAG join, not a request
            # to concatenate their complete histories under fresh guards.
            return cls._from_base(yes.length, _ChoiceBase(
                condition, _ArrayBase(yes, width), _ArrayBase(no, width)), context=context)
        yes_base = _widen_base(yes._base, width)
        no_base = _widen_base(no._base, width)
        base = _choose_base(condition, yes_base, no_base)
        prefix = yes.writes[:shared]
        if yes.element_width != width:
            prefix = tuple(
                _Write(row.guard, row.index, _extend(row.value, width))
                for row in prefix)
        rows = tuple(
            _Write(z3.And(z3.Not(condition), row.guard), row.index,
                   _extend(row.value, width))
            for row in no.writes[shared:]
        ) + tuple(
            _Write(z3.And(condition, row.guard), row.index,
                   _extend(row.value, width))
            for row in yes.writes[shared:]
        )
        return cls._from_base(yes.length, base, prefix + rows, context=context)

    def difference_indices(self, other):
        """A finite support containing every potentially different cell.

        Shared bases need only the nonshared store suffixes. A changed base
        retains the exact finite component domain rather than guessing that
        writes alone cover it. Used by componentwise atomic commit.
        """
        if type(other) is not FunctionalArray or self.length != other.length:
            raise TypeError("array differences need one fixed functional shape")
        if _base_same(self._base, other._base):
            shared = _shared_prefix(self.writes, other.writes)
            return (tuple(row.index for row in self.writes[shared:])
                    + tuple(row.index for row in other.writes[shared:]))
        width = max(1, self.length.bit_length() + 1)
        context = _term_context(self, other)
        return tuple(z3.BitVecVal(index, width, ctx=context) for index in range(self.length))

    def equal(self, other, *, context=None):
        """Extensional equality of two finite store chains.

        Away from every stored index, each array has its default.  At a stored
        index, comparing the two selections accounts for overlapping symbolic
        indices and update order.  The common uniform-base case therefore
        avoids domain enumeration; differing fully overwritten bases fall back
        to the finite componentwise definition.
        """
        context = _term_context(self, other) if context is None else context
        if type(other) is not FunctionalArray or self.length != other.length:
            return z3.BoolVal(False, ctx=context)
        same_base = _base_same(self._base, other._base)
        # Choose the exact representation before constructing any reads.
        # Different bases need the whole finite image; suffix-index checks
        # would be discarded, after expanding both histories unnecessarily.
        if not same_base and (
                _has_packed_base(self._base)
                or _has_packed_base(other._base)):
            width = max(self.element_width, other.element_width)
            return self._packed_value(width, context) == other._packed_value(width, context)
        if not same_base:
            width = max(
                max((row.index.size() for row in (*self.writes, *other.writes)), default=1),
                max(1, self.length.bit_length() + 1),
            )
            support = (z3.BitVecVal(position, width, ctx=context)
                       for position in range(self.length))
        else:
            shared = _shared_prefix(self.writes, other.writes)
            support = (row.index for row in (*self.writes[shared:], *other.writes[shared:]))
        checks = []
        for index in support:
            left, right = _widen(self.select(index), other.select(index))
            checks.append(left == right)
        return z3.And(checks) if checks else z3.BoolVal(True, ctx=context)

    def to_uniform(self, model, decode):
        """Decode one model value without expanding an indexed base."""

        from .uniform_array import UniformArray

        histories = []
        array = self
        while True:
            histories.append(array.writes)
            base = array._base
            while type(base) is _ChoiceBase:
                selected = model.eval(base.condition, model_completion=True)
                base = base.yes if z3.is_true(selected) else base.no
            if type(base) is not _ArrayBase:
                break
            array = base.value
        if type(base) is _ConstantBase:
            out = UniformArray.uniform(
                self.length, decode(model, base.value))
        elif type(base) is _MemoryBase:
            out = UniformArray.memory(
                self.length, base.name, base.data_width)
        elif type(base) in (_PackedBase, _MergeBase, _IndexedBase):
            width = max(1, self.length.bit_length() + 1)
            context = _term_context(array)
            cells = [decode(
                model,
                _base_select(base, z3.BitVecVal(index, width, ctx=context)))
                for index in range(self.length)]
            out = UniformArray.uniform(self.length, cells[0])
            for index, item in enumerate(cells[1:], 1):
                if item != cells[0]:
                    out = out.store(index, item)
        else:
            raise TypeError("unknown functional-array base")
        for row in (row for history in reversed(histories) for row in history):
            if not z3.is_true(model.eval(row.guard, model_completion=True)):
                continue
            encoded_index = model.eval(row.index, model_completion=True)
            index = (encoded_index.as_signed_long()
                     if z3.is_bv_value(encoded_index)
                     else int(str(encoded_index)))
            if 0 <= index < self.length:
                out = out.store(index, decode(model, row.value))
        return out

    def materialize(self, model, decode):
        """Decode the fixed array in linear output time, not solver-query time."""
        return self.to_uniform(model, decode).materialize()
