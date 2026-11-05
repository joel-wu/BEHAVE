"""Exact array state names bound construction cost, not reachable values."""

import gc
import weakref

import pytest
import z3

from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.step_template import _bind_successors
from behave.sim.core.construction_checkpoint import finish_construction

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('length', [1, 3])
@pytest.mark.parametrize('width', [1, 8])
@pytest.mark.parametrize('position', [-1, 0, 1, 2, 3, 7])
@pytest.mark.parametrize('enabled', [False, True, None])
def test_literal_packed_store_matches_dense_cells_without_dynamic_shift(
        length, width, position, enabled):
    from behave.sim.symbolic.functional_array import _Write, _packed_store

    context = z3.Context()
    vector = z3.BitVec('whole_memory', length * width, ctx=context)
    data = z3.BitVec('new_word', width, ctx=context)
    guard = (z3.Bool('write_enable', ctx=context) if enabled is None else
             z3.BoolVal(enabled, ctx=context))
    index = z3.BitVecVal(position, 5, ctx=context)
    row = _Write(guard, index, data)
    result = _packed_store(vector, row, length, width)
    cells = []
    for i in reversed(range(length)):
        original = z3.Extract((i + 1) * width - 1, i * width, vector)
        cells.append(z3.If(z3.And(guard, index == i), data, original))
    expected = cells[0] if length == 1 else z3.Concat(*cells)
    solver = z3.Solver(ctx=context)
    solver.add(result != expected)
    assert solver.check() == z3.unsat
    pending = [result]
    while pending:
        term = pending.pop()
        assert term.decl().kind() not in (z3.Z3_OP_BSHL, z3.Z3_OP_BLSHR, z3.Z3_OP_BASHR)
        pending.extend(term.children())


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('position', [-1, 0, 1, 2, 3])
def test_closed_packed_word_is_a_literal_for_template_branch_pruning(cached, position):
    array = FunctionalArray.from_packed(z3.BitVecVal(0x80ff03, 24), 3, 8)
    if cached:
        array._packed_value(8)
    selected = array.select(z3.BitVecVal(position, 5))
    assert z3.is_bv_value(selected)
    assert selected.as_long() == ([3, 255, 128][position] if 0 <= position < 3 else 0)


def _array(base, context, length=3):
    if base == 'constant':
        value = FunctionalArray.uniform(length, z3.BitVecVal(-3, 7))
    elif base == 'memory':
        value = FunctionalArray.memory(length, 'boundary_mem', 6, 7)
    elif base == 'packed':
        value = FunctionalArray.symbolic(length, 'arbitrary', 7, -64, 63)
    elif base == 'choice':
        value = FunctionalArray.choose(z3.Bool('choose'),
            _array('constant', z3.main_ctx(), length), _array('packed', z3.main_ctx(), length))
    else:
        value = FunctionalArray.merge_bits(_array('packed', z3.main_ctx(), length),
            _array('memory', z3.main_ctx(), length),
            FunctionalArray.symbolic(length, 'mask', 7, -64, 63), 7, signed=True)
    return value.translate(context)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('base', ['constant', 'memory', 'packed', 'choice', 'masked'])
@pytest.mark.parametrize('length', [1, 3, 4])
def test_boundary_preserves_all_cells_and_signed_out_of_bounds(base, private, length):
    context = z3.Context() if private else z3.main_ctx()
    array = _array(base, context, length)
    for index in range(length + 2):
        array = array.store(z3.BitVec(f'index{index}', 4, ctx=context),
            z3.BitVec(f'value{index}', 7, ctx=context), z3.Bool(f'guard{index}', ctx=context))
    read = z3.BitVec('read', 5, ctx=context)
    after, definitions = finish_construction(array.bind_state_steps(tag='test'))
    assert after is not array and after.writes == ()
    assert len(definitions) == 1
    solver = z3.Solver(ctx=context)
    solver.add(*definitions, array.select(read) != after.select(read))
    assert solver.check() == z3.unsat
    solver = z3.Solver(ctx=context)
    solver.add(*definitions)
    assert solver.check() == z3.sat  # No vacuous equivalence from contradictory definitions.


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('base', ['constant', 'memory', 'packed', 'choice', 'masked'])
@pytest.mark.parametrize('width', [1, 4, 7, 12])
def test_packed_projection_matches_cellwise_truncation_and_sign_extension(private, base, width):
    context = z3.Context() if private else z3.main_ctx()
    array = _array(base, context).widen(11)
    index = z3.BitVec('projection_index', 5, ctx=context)
    value = z3.BitVec('projection_value', 11, ctx=context)
    guard = z3.Bool('projection_guard', ctx=context)
    array = array.store(index, value, guard)
    original_cells = [array.select(z3.BitVecVal(at, 5, ctx=context))
                      for at in reversed(range(array.length))]
    # Request differently sized images from the same immutable DAG, including
    # widening a prior narrow image; omitted high bits must never be reused.
    for bits in (width, 13, 2):
        packed = array._packed_value(bits, context)
        cells = []
        for cell in original_cells:
            cells.append(z3.Extract(bits - 1, 0, cell) if bits < cell.size() else
                         z3.SignExt(bits - cell.size(), cell))
        expected = z3.Concat(*cells)
        solver = z3.Solver(ctx=context)
        solver.add(packed != expected)
        assert solver.check() == z3.unsat


def test_narrow_image_does_not_build_full_width_store_vectors(monkeypatch):
    from behave.sim.symbolic import encoder, functional_array as implementation

    enc = encoder._Encoder(None, {}, 'narrow_image')
    enc.compact_arrays = True
    array = FunctionalArray.uniform(16, z3.BitVecVal(0, 212))
    index, word = z3.BitVec('narrow_address', 8), z3.BitVec('narrow_word', 212)
    original, widths = implementation._packed_store, []

    def measured(packed, row, length, bits):
        widths.append(bits)
        return original(packed, row, length, bits)

    monkeypatch.setattr(implementation, '_packed_store', measured)
    for turn in range(19):
        array = enc.bind_value(array.store(index + turn, word & 5, z3.BoolVal(True)))
    assert array.element_width == 212
    assert array._base.storage_width == 3
    assert widths and set(widths) == {3}


def test_sparse_large_memory_and_short_uniform_fill_history_stay_lazy(monkeypatch):
    sparse = FunctionalArray.uniform(65536, z3.BitVecVal(0, 17)).store(
        z3.BitVec('sparse_at', 19), z3.BitVec('sparse_value', 17), z3.Bool('sparse_guard'))
    fills = FunctionalArray.uniform(5, z3.BitVecVal(0, 8))
    for epoch in range(3):
        for index in range(5):
            fills = fills.store(z3.BitVecVal(index, 4), z3.BitVecVal(epoch, 8),
                                z3.Bool(f'fill{epoch}'))
    def no_enumeration(*_args):
        raise AssertionError('an inexpensive representation must remain lazy')
    monkeypatch.setattr(FunctionalArray, 'select', no_enumeration)
    for array in (sparse, fills):
        after, definitions = finish_construction(array.bind_state_steps(tag='lazy'))
        assert after is array and definitions == ()


@pytest.mark.parametrize('kind', ['choice', 'masked', 'mixed'])
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('length', [1, 3, 8])
def test_composite_history_is_bound_even_without_top_level_writes(kind, private, length):
    context = z3.Context() if private else z3.main_ctx()
    array = FunctionalArray.symbolic(length, 'composite_initial', 7, -64, 63).translate(context)
    mask = FunctionalArray.symbolic(length, 'composite_mask', 7, -64, 63).translate(context)
    for step in range(length + 2):
        data = FunctionalArray.uniform(length, z3.BitVec(f'composite_value{step}', 7, ctx=context))
        if kind == 'choice' or kind == 'mixed' and step % 2:
            array = FunctionalArray.choose(z3.Bool(f'composite_guard{step}', ctx=context), data, array)
        else:
            array = FunctionalArray.merge_bits(array, data, mask, 7, signed=True)
    assert not array.writes  # The old direct-write-only check missed this DAG.
    after, definitions = finish_construction(array.bind_state_steps(tag='composite'))
    assert after is not array and len(definitions) == 1
    read = z3.BitVec('composite_read', length.bit_length() + 2, ctx=context)
    solver = z3.Solver(ctx=context)
    solver.add(*definitions)
    assert solver.check() == z3.sat
    solver.add(array.select(read) != after.select(read))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('signed', [False, True])
@pytest.mark.parametrize('width', [1, 4, 9])
def test_packed_boundary_masks_convert_each_input_before_merging(private, signed, width):
    context = z3.Context() if private else z3.main_ctx()
    arrays, cells = [], []
    address = z3.BitVec('mask_store_at', 5, ctx=context)
    guard = z3.Bool('mask_store_guard', ctx=context)
    for n, carrier in enumerate((3, 6, 11)):
        values = [z3.BitVec(f'mask_input_{n}_{i}', carrier, ctx=context) for i in range(3)]
        value = z3.BitVec(f'mask_update_{n}', carrier, ctx=context)
        arrays.append(FunctionalArray.from_cells(values).store(address, value, guard))
        cells.append([z3.If(z3.And(guard, address == i), value, old)
                      for i, old in enumerate(values)])

    def bits(value):
        return (z3.SignExt(width - value.size(), value) if value.size() < width else
                value if value.size() == width else z3.Extract(width - 1, 0, value))

    expected = []
    for column in zip(*cells, strict=True):
        a, b, m = map(bits, column)
        value = (a & ~m) | (b & m)
        if not signed:
            value = z3.ZeroExt(1, value)
        expected.append(z3.SignExt(2, value))
    array = FunctionalArray.merge_bits(*arrays, width, signed=signed).widen(width + int(not signed) + 2)
    # Full arbitrary mask carriers and several symbolic writes force a bound
    # without supplying a range invariant or a concrete observed valuation.
    for i in range(5):
        at = z3.BitVec(f'mask_after_at{i}', 6, ctx=context)
        value = z3.BitVec(f'mask_after_value{i}', array.element_width, ctx=context)
        enabled = z3.Bool(f'mask_after_guard{i}', ctx=context)
        array = array.store(at, value, enabled)
        expected = [z3.If(z3.And(enabled, at == j), value, old)
                    for j, old in enumerate(expected)]
    after, definitions = finish_construction(array.bind_state_steps(tag='masked_carriers'))
    read = z3.BitVec('mask_final_at', 8, ctx=context)
    oracle = z3.BitVecVal(0, array.element_width, ctx=context)
    for i, value in enumerate(expected):
        oracle = z3.If(read == i, value, oracle)
    solver = z3.Solver(ctx=context)
    solver.add(*definitions)
    assert solver.check() == z3.sat
    solver.add(after.select(read) != oracle)
    assert solver.check() == z3.unsat


def test_boundary_composes_history_once_instead_of_reading_every_cell(monkeypatch):
    from behave.sim.symbolic import functional_array as implementation

    length = 128
    base = FunctionalArray.symbolic(length, 'vector_initial', 7, -64, 63)
    for i in range(length + 1):
        base = base.store(z3.BitVec(f'vector_address{i}', 10),
                          z3.BitVec(f'vector_data{i}', 7), z3.Bool(f'vector_guard{i}'))
    array = FunctionalArray.merge_bits(base, base,
        FunctionalArray.uniform(length, z3.BitVecVal(63, 7)), 6, signed=False)
    packed_store, calls = implementation._packed_store, []

    def counted(*args):
        calls.append(None)
        return packed_store(*args)

    def no_replay(*_args):
        raise AssertionError('packing revisited an array history through individual reads')

    monkeypatch.setattr(implementation, '_packed_store', counted)
    monkeypatch.setattr(FunctionalArray, 'select', no_replay)
    after, definitions = finish_construction(array.bind_state_steps(tag='vector'))
    assert after is not array and len(definitions) == 1
    assert len(calls) == length + 1  # Shared base is packed once, not once per arm/cell.


def test_packing_recognizes_guarded_fills_and_retains_later_aliases(monkeypatch):
    from behave.sim.symbolic import functional_array as implementation

    base = FunctionalArray.symbolic(5, 'fill_initial', 7, -64, 63)
    original = base
    for i in range(5):
        base = base.store(z3.BitVecVal(i, 5), z3.BitVecVal(-7, 7), z3.Bool('vector_fill'))
    at, value = z3.BitVec('fill_at', 5), z3.BitVec('fill_value', 7)
    base = base.store(at, value, z3.Bool('fill_after'))
    store, calls = implementation._packed_store, []

    def counted(*args):
        calls.append(None)
        return store(*args)

    monkeypatch.setattr(implementation, '_packed_store', counted)
    packed = base.compact()
    assert len(calls) == 1
    read = z3.BitVec('fill_read', 5)
    expected = z3.If(z3.Bool('vector_fill'), z3.BitVecVal(-7, 7), original.select(read))
    expected = z3.If(z3.And(z3.Bool('fill_after'), at == read), value, expected)
    expected = z3.If(z3.And(read >= 0, read < 5), expected, z3.BitVecVal(0, 7))
    solver = z3.Solver()
    solver.add(packed.select(read) != expected)
    assert solver.check() == z3.unsat


def test_shared_composite_ancestors_are_not_counted_once_per_path(monkeypatch):
    base = FunctionalArray.symbolic(64, 'shared_initial', 7, -64, 63)
    data = FunctionalArray.uniform(64, z3.BitVecVal(3, 7))
    for _ in range(12):
        base = FunctionalArray.merge_bits(base, data, base, 7, signed=True)
    def no_enumeration(*_args):
        raise AssertionError('a shared DAG is not an exponentially large history')
    monkeypatch.setattr(FunctionalArray, 'select', no_enumeration)
    after, definitions = finish_construction(base.bind_state_steps(tag='shared'))
    assert after is base and not definitions


def test_chained_state_names_keep_aliases_overwrites_and_widened_values():
    original = current = FunctionalArray.uniform(3, z3.BitVecVal(0, 5))
    definitions = []
    for step in range(13):
        # An address repeats across boundaries; the last enabled write wins.
        address = z3.BitVec(f'address{step % 2}', 4)
        value = z3.BitVec(f'value{step}', 5 if step < 7 else 9)
        guard = z3.Bool(f'enabled{step}')
        original = original.store(address, value, guard)
        current = current.store(address, value, guard)
        current, equations = finish_construction(current.bind_state_steps(tag=f'step{step}'))
        definitions.extend(equations)
        assert len(current.writes) <= current.length
    read = z3.BitVec('read_after_chain', 5)
    solver = z3.Solver()
    solver.add(*definitions, original.select(read) != current.select(read))
    assert solver.check() == z3.unsat
    solver = z3.Solver()
    solver.add(*definitions)
    assert solver.check() == z3.sat


def test_cancelled_boundary_does_not_publish_partial_state_or_change_history():
    array = _array('constant', z3.main_ctx())
    for index in range(5):
        array = array.store(z3.BitVec(f'cancel_index{index}', 4),
            z3.BitVecVal(index, 7), z3.Bool(f'cancel_guard{index}'))
    history, checks = array.writes, []
    def cancel():
        checks.append(None)
        if len(checks) == 2:
            raise TimeoutError('at a cell boundary')
    steps = array.bind_state_steps(tag='resumed')
    with pytest.raises(TimeoutError, match='cell boundary'):
        finish_construction(steps, cancel)
    after, definitions = finish_construction(steps)
    assert array.writes is history
    read = z3.BitVec('cancelled_read', 5)
    solver = z3.Solver()
    solver.add(*definitions, after.select(read) != array.select(read))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('finish', [False, True])
def test_array_boundary_releases_context_without_cyclic_collection(finish):
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        def query():
            context = z3.Context()
            array = _array('packed', context)
            for index in range(5):
                array = array.store(z3.BitVec(f'owned_index{index}', 4, ctx=context),
                    z3.BitVecVal(index, 7, ctx=context), z3.BoolVal(True, ctx=context))
            definitions = []
            steps = _bind_successors({'a': array, 'aliases': [array, array]}, definitions, {}, 'owned')
            if finish:
                state = finish_construction(steps)
                assert state['a'] is state['aliases'][0] is state['aliases'][1]
                assert len(definitions) == 1
            else:
                next(steps)
            return weakref.ref(context)
        assert query()() is None
    finally:
        if enabled:
            gc.enable()
        gc.collect()


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('base', ['constant', 'memory', 'packed', 'choice', 'masked'])
def test_same_arena_translation_preserves_immutable_history_and_read_cache(base, private):
    context = z3.Context() if private else z3.main_ctx()
    array = _array(base, context)
    for index in range(5):
        array = array.store(z3.BitVec(f'shared_index{index}', 4, ctx=context),
            z3.BitVecVal(index, 7, ctx=context), z3.Bool(f'shared_guard{index}', ctx=context))
    read = z3.BitVec('shared_read', 4, ctx=context)
    selected = array.select(read)
    array._read_history()
    translated = array.translate(context)
    assert translated is array
    assert translated.writes is array.writes
    assert translated._read_blocks is array._read_blocks
    assert translated.select(read) is selected
    updated = translated.store(read, z3.BitVecVal(62, 7, ctx=context), z3.BoolVal(True, ctx=context))
    assert updated is not array
    assert array.select(read) is selected
    solver = z3.Solver(ctx=context)
    solver.add(read == 1, updated.select(read) != 62)
    assert solver.check() == z3.unsat


def test_translation_checks_all_leaves_not_only_first_write_context():
    context = z3.Context()
    base = FunctionalArray.symbolic(3, 'mixed_base', 7, -64, 63)
    address = z3.BitVec('mixed_address', 4, ctx=context)
    array = base.store(address, z3.BitVecVal(19, 7, ctx=context), z3.Bool('mixed_guard', ctx=context))
    translated = array.translate(context)
    assert translated is not array
    assert all(term.ctx == context for term in translated.formula_terms)
    # The row was already in the target arena; only the foreign base moves.
    assert translated.writes[0] is array.writes[0]
    assert translated.translate(context) is translated
    read = z3.BitVec('mixed_read', 4, ctx=context)
    solver = z3.Solver(ctx=context)
    expected = base.translate(context).store(address, z3.BitVecVal(19, 7, ctx=context),
                                            z3.Bool('mixed_guard', ctx=context))
    solver.add(translated.select(read) != expected.select(read))
    assert solver.check() == z3.unsat


def test_translation_does_not_cache_a_private_arena_on_the_source():
    source = _array('choice', z3.main_ctx())
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        def query():
            context = z3.Context()
            moved = source.translate(context)
            assert moved is not source
            assert moved.translate(context) is moved
            return weakref.ref(context)
        assert query()() is None
    finally:
        if enabled:
            gc.enable()
        gc.collect()


@pytest.mark.parametrize('wrapped', [False, True])
def test_context_neutral_memory_reads_belong_to_the_translated_owner(wrapped):
    source = FunctionalArray.memory(5, 'neutral_memory', 6, 7)
    if wrapped:
        source = FunctionalArray.merge_bits(source, source,
            FunctionalArray.uniform(5, z3.BitVecVal(63, 7)), 6, signed=False)
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        def query():
            context = z3.Context()
            moved = source.translate(context)
            assert moved is not source
            assert moved.translate(context) is moved
            read = moved.select(z3.BitVec('neutral_at', 4, ctx=context))
            assert read.ctx == context
            return weakref.ref(context)
        assert query()() is None
        assert not source._selections
    finally:
        if enabled:
            gc.enable()
        gc.collect()


def test_same_arena_translation_does_not_rewalk_a_certified_immutable_value(monkeypatch):
    import behave.sim.symbolic.functional_array as implementation
    context = z3.Context()
    array = _array('masked', context)
    def no_context_scan(*_args):
        raise AssertionError('unchanged translated array should retain its known arena')
    monkeypatch.setattr(implementation, '_term_context', no_context_scan)
    assert array.translate(context) is array


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('operation', ['uniform', 'packed', 'store', 'widen', 'choose', 'join', 'merge', 'boundary'])
def test_same_arena_updates_keep_ownership_without_walking_history(monkeypatch, private, operation):
    import behave.sim.symbolic.functional_array as implementation
    context = z3.Context() if private else z3.main_ctx()
    array = _array('packed', context)
    address = z3.BitVec('owned_at', 4, ctx=context)
    value = z3.BitVec('owned_value', 7, ctx=context)
    guard = z3.Bool('owned_guard', ctx=context)
    updated = array.store(address, value, guard)
    if operation == 'uniform':
        result = FunctionalArray.uniform(3, value)
    elif operation == 'packed':
        result = array.compact()
    elif operation == 'store':
        result = updated
    elif operation == 'widen':
        result = updated.widen(9)
    elif operation == 'choose':
        result = FunctionalArray.choose(guard, updated, array)
    elif operation == 'join':
        other = array.store(address + 1, value + 1, guard)
        result = FunctionalArray.choose(guard, updated, other)
    elif operation == 'merge':
        result = FunctionalArray.merge_bits(array, updated, array, 7, signed=True)
    else:
        for i in range(4):
            updated = updated.store(address + i, value + i, guard)
        result, _definitions = finish_construction(updated.bind_state_steps(tag='owned_boundary'))

    def no_history_walk(*_args):
        raise AssertionError('same-arena operation must not rewalk old immutable history')
    monkeypatch.setattr(implementation, 'fields', no_history_walk)
    assert result.translate(context) is result


@pytest.mark.parametrize('operation', ['store', 'join', 'condition', 'merge'])
def test_mixed_arena_updates_do_not_inherit_a_partial_ownership_claim(operation):
    context = z3.Context()
    original = _array('packed', z3.main_ctx())
    local = _array('packed', context)
    guard = z3.Bool('foreign_guard', ctx=context)
    if operation == 'store':
        mixed = original.store(z3.BitVecVal(1, 4, ctx=context),
            z3.BitVecVal(11, 7, ctx=context), guard)
    elif operation == 'join':
        mixed = FunctionalArray.choose(guard, original, local)
    elif operation == 'condition':
        mixed = FunctionalArray.choose(guard, original, _array('constant', z3.main_ctx()))
    else:
        mixed = FunctionalArray.merge_bits(original, local, local, 7, signed=True)
    assert mixed._translation_context is None
    moved = mixed.translate(context)
    assert moved is not mixed
    assert all(term.ctx == context for term in moved.formula_terms)
    assert moved.translate(context) is moved


@pytest.mark.parametrize('width,bounds', [(7, (-64, 63)), (4, (-8, 7)), (7, (0, 31))])
@pytest.mark.parametrize('private', [False, True])
def test_literal_packed_reads_are_exact_wiring_without_symbolic_shifts(width, bounds, private):
    context = z3.Context() if private else z3.main_ctx()
    array = FunctionalArray.symbolic(3, 'literal_base', width, *bounds).widen(7).translate(context)
    read = z3.BitVec('literal_read', 5, ctx=context)
    for index in range(3):
        cell = array.select(z3.BitVecVal(index, 5, ctx=context))
        pending = [cell]
        while pending:
            term = pending.pop()
            assert term.decl().kind() not in (z3.Z3_OP_BLSHR, z3.Z3_OP_BASHR, z3.Z3_OP_BMUL)
            pending.extend(term.children())
        solver = z3.Solver(ctx=context)
        solver.add(read == index, cell != array.select(read))
        assert solver.check() == z3.unsat
