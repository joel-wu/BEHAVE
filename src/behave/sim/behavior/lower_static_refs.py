"""Lowering-only references to finite immutable source collections.

These rules retain closed Python collection structure while selectors narrow it
to scalar or flat-tuple SolverIR payloads. The carrier owns storage and
iteration; module rules own source discovery, selection and lookup. They
borrow the lowerer explicitly for evaluation, flow facts and command
placement, so term and graph lowering share one implementation without
inheritance, forwarded attributes or a second mutable lowering context.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ..symbolic import ir
from ..symbolic import typecheck as value_types
from .extract import LoweringError
from .source_records import RecordValue
from .source_rom import is_source_key
from ..symbolic.value_contract import (
    INCOMPATIBLE,
    UNKNOWN,
    ValueContract,
    contract_is_deeply_immutable,
    contract_of_value,
    join_contracts,
)

_NO_STATIC_DEFAULT = object()
_NO_STATIC_LITERAL = object()


def _zero_static_shape(shape):
    """One zero-valued SolverIR expression with recursive ``shape``."""
    shape = tuple(shape or ())
    if not shape:
        return ir.Lit(0)
    child = _zero_static_shape(shape[1:])
    return ir.Op("arraylit", *[child for _ in range(shape[0])])


@dataclass(frozen=True)
class _StaticRef:
    """Finite source collection alternatives carried only by the lowerer.

    The IR never receives a Python object.  Each alternative says under which
    already-lowered condition one immutable source subtree is selected.  A
    chain of source reads narrows the alternatives until a scalar or a flat
    tuple can be encoded; any other use is an explicit subset boundary.
    """

    alternatives: tuple
    # A local reference is represented in SolverIR by an integer tag.  Direct
    # selectors remain unbound expression values; once assigned, ``tag`` names
    # the IR local that carries the selected source subtree through branches
    # and semantic loops.
    tag: str | None = None

    @staticmethod
    def bound(low, name, values, node):
        """Describe the source subtrees denoted by an encoded local tag."""
        unique = {}
        for value in values:
            tag = _tag(low, value, node)
            unique.setdefault(tag, value)
        alternatives = tuple(
            (ir.Op("eq", ir.Var(name), ir.Lit(tag)), value)
            for tag, value in unique.items())
        return _StaticRef(alternatives, name)

    def bind(self, low, name, node):
        """Encode one source collection-reference assignment in SolverIR."""
        tagged = []
        for active, value in self.alternatives:
            tagged.append((active, _tag(low, value, node), value))
        if not tagged:
            # Every normal path has already failed.  Keep the IR total; this
            # assignment is unreachable after the emitted failure guards.
            expression = ir.Lit(0)
        else:
            # The final arm remains the total fallback. Reuse the balanced
            # priority selector so finite tags do not form a linear-depth IR.
            expression = choose_payload(
                low, tuple((active, tag) for active, tag, _value in tagged[:-1])
                + ((ir.Lit(True), tagged[-1][1]),), node)
        return (ir.Assign(name, expression),
                _StaticRef.bound(low, name, (value for _active, _tag, value in tagged), node))

    def assign(self, low, target, source):
        """Install one lowering-only reference in a lexical local."""
        name = low._written(target)
        command, bound = self.bind(low, name, source)
        bound.install(low, name)
        return low._flush(command)

    def install(self, low, name):
        """Keep source identity separate from the tag's numeric storage."""
        low.flow.static_values[name] = self
        low.flow.lengths.pop(name, None)
        low.flow.carriers.pop(name, None)
        low.flow.contracts.pop(name, None)
        # The source value is a collection or discriminator, not an Integer.
        # Its hidden tag must not leak into source-level range facts.
        low.flow.ranges.pop(name, None)
        low.flow.bounds.pop(name, None)
        low.flow.unknown_ranges.discard(name)

    @staticmethod
    def merge_bound(refs):
        """Union encoded alternatives that use the same carried tag local."""
        if not refs or any(ref.tag is None for ref in refs):
            return None
        name = refs[0].tag
        if any(ref.tag != name for ref in refs[1:]):
            return None
        alternatives = {}
        for ref in refs:
            for active, value in ref.alternatives:
                alternatives.setdefault(value_key(value),
                                        (active, value))
        return _StaticRef(tuple(alternatives.values()), name)


    def iterable(self, low, node):
        """Materialise one finite, possibly variable-length static iterable.

        A static reference is normally kept out of SolverIR until a selector
        reaches a scalar payload.  Iteration is the other exact elimination:
        each alternative becomes an immutable IR array plus its exact length.
        Text elements instead retain their finite source identities; a string
        iterator yields strings, whereas a bytes iterator yields integers.
        Alternatives may have different lengths, but their elements must share
        one recursive shape; shorter arrays are padded beyond their length and
        those cells are never selected.  The reference itself is still
        retained by the caller, so mutation, identity observation and escape
        remain rejected at their ordinary boundaries.
        """
        if not isinstance(self, _StaticRef) or not self.alternatives:
            raise LoweringError(
                node, "a static iterable has no reachable finite value",
                code="static-collection-iteration-ambiguous")

        sequences = []
        for active, value in self.alternatives:
            if not isinstance(value, (tuple, dict, str, bytes)):
                raise LoweringError(node, "iteration over a static scalar",
                                    code="static-collection-depth-unsupported")
            sequences.append((active, tuple(value)))
        if any(type(item) is str for _active, seq in sequences for item in seq):
            if any(type(item) is not str for _active, seq in sequences for item in seq):
                raise LoweringError(node, "iteration mixes text and non-text elements",
                                    code="static-collection-iteration-ambiguous")
            lengths = tuple(len(seq) for _active, seq in sequences)
            count = choose_payload(
                low, tuple((active, len(seq)) for active, seq in sequences[:-1])
                + ((ir.Lit(True), lengths[-1]),), node)
            low._spend(max(1, sum(lengths)))

            def select(index):
                return _StaticRef(tuple(
                    (ir.Op("land", active, ir.Op("eq", index, ir.Lit(position))), item)
                    for active, seq in sequences for position, item in enumerate(seq)))

            return select, count, (min(lengths), max(lengths))

        alternatives = []
        element_shape = None
        for active, sequence in sequences:
            elements = tuple(
                low._sequence_table_expr(item) for item in sequence)
            if any(item is None for item in elements):
                raise LoweringError(
                    node, "a static iterable has no uniform recursive IR "
                          "representation",
                    code="static-collection-iteration-ambiguous")
            shapes = {low._shape(item) for item in elements}
            if len(shapes) > 1:
                raise LoweringError(
                    node, "one static iterable has elements with different "
                          "recursive shapes",
                    code="static-collection-iteration-ambiguous")
            if shapes:
                shape = next(iter(shapes))
                if element_shape is None:
                    element_shape = shape
                elif element_shape != shape:
                    raise LoweringError(
                        node, "possible static iterables have elements with "
                              "different recursive shapes",
                        code="static-collection-iteration-ambiguous")
            alternatives.append((active, sequence, elements))

        lengths = tuple(len(sequence)
                        for _active, sequence, _elements in alternatives)
        maximum = max(lengths)
        minimum = min(lengths)
        padding = _zero_static_shape(element_shape or ())
        padded = []
        for active, sequence, elements in alternatives:
            cells = (*elements, *((padding,) * (maximum - len(sequence))))
            padded.append((active, len(sequence), ir.Op("arraylit", *cells)))

        # The first true alternative has source priority.  A well-formed
        # reference always has one active alternative on a reaching path; the
        # final literal is only a total IR fallback outside those paths.
        selected = padded[-1][2]
        count = ir.Lit(padded[-1][1])
        for active, length, literal in reversed(padded[:-1]):
            selected = ir.Op("ite", active, literal, selected)
            count = ir.Op("ite", active, ir.Lit(length), count)
        low._spend(max(1, len(padded) * max(1, maximum)))
        return lambda index: ir.Sel(selected, index), count, (minimum, maximum)

    def length(self, low, node):
        """Return the exact length of one guarded finite source value."""
        if not isinstance(self, _StaticRef) or not self.alternatives:
            raise LoweringError(
                node, "len has no reachable finite source collection",
                code="static-collection-length-unsupported")
        lengths = []
        for active, value in self.alternatives:
            if not isinstance(value, (dict, tuple, str, bytes)):
                raise LoweringError(
                    node, "len may receive something other than a finite "
                          "source collection",
                    code="static-collection-length-unsupported")
            lengths.append((active, len(value)))
        return choose_payload(low, tuple(lengths[:-1]) + ((ir.Lit(True), lengths[-1][1]),), node)

@dataclass(frozen=True)
class _StaticIssue:
    """Why a name cannot denote one static reference on every live path."""

    code: str


@dataclass(frozen=True)
class _TupleKey:
    """An immutable source key; components are already evaluated selectors."""

    items: tuple


_STATIC_SCALAR = "scalar"


def value_key(value):
    """An exact, hashable identity for an admitted immutable subtree."""
    if type(value) in (bool, int, str, bytes, type(None)):
        return (type(value).__name__, value)
    if type(value) is tuple:
        return ("tuple", tuple(
            value_key(item)
            for item in value))
    if type(value) is dict:
        # Insertion order is observable when a mapping is iterated.
        return ("dict", tuple(
            (value_key(key),
             value_key(item))
            for key, item in value.items()))
    raise TypeError(type(value).__name__)


def payload_shape(value):
    """The flat SolverIR shape of one finite table payload."""
    if type(value) in (bool, int):
        return ()
    if (isinstance(value, tuple)
            and all(type(item) in (bool, int) for item in value)):
        return (len(value),)
    return None


def disjunction(terms):
    return ir._balanced_op("lor", terms, ir.Lit(False))


def numeric_equal(left, right):
    """Python equality for the admitted Boolean/integer scalar subset.

    SolverIR deliberately does not identify a Boolean with an Integer.
    Python collection membership and mapping-key lookup nevertheless use
    numeric equality, under which ``True == 1`` and ``False == 0``.  Say
    that source-language conversion explicitly at the lowering boundary;
    keeping it out of ``eq`` itself preserves exact semantic types for
    every IR producer and consumer.
    """
    return ir.Op(
        "eq", ir.Op("toint", left), ir.Op("toint", right))


def integer_membership(needle, values):
    """An exact union of integer intervals, not an enumerated equality tree.

    Numeric equality identifies Boolean keys with zero/one. Coalescing
    adjacent keys preserves every gap, reduces dense ROM predicates, and
    exposes their exact bounds to the shared range analysis.
    """
    intervals = []
    for value in sorted({int(value) for value in values}):
        if intervals and intervals[-1][1] + 1 == value:
            intervals[-1] = (intervals[-1][0], value)
        else:
            intervals.append((value, value))
    needle = ir.Op("toint", needle)
    return disjunction([
        ir.Op("eq", needle, ir.Lit(lo)) if lo == hi else
        ir.Op("land", ir.Op("le", ir.Lit(lo), needle),
              ir.Op("le", needle, ir.Lit(hi)))
        for lo, hi in intervals])


def _is_collection(value):
    return isinstance(value, (dict, tuple, str, bytes))


def reference_result(values):
    """Whether values need a lowering-only collection reference."""
    return bool(values) and any(
        _is_collection(value)
        for value in values) and all(
            value is None
            or _is_collection(value)
            for value in values)


def payload_encodable(value):
    """Whether a selected source value has an ordinary SolverIR image."""
    if payload_shape(value) is not None:
        return True
    return (type(value) is dict and bool(value)
            and all(type(field) is str for field in value)
            and all(payload_encodable(child)
                    for child in value.values()))


def _tag(low, value, node):
    """Stable procedure-local tag for one immutable source subtree."""
    try:
        key = value_key(value)
    except TypeError as exc:
        raise LoweringError(
            node, f"a static reference reaches an unsupported "
                  f"{exc.args[0]} subtree",
            code="static-collection-depth-unsupported") from None
    tag = low._static_tag_ids.get(key)
    if tag is None:
        tag = len(low._static_tag_ids) + 1
        low._static_tag_ids[key] = tag
    return tag


def choose_payload(low, alternatives, node):
    """Select one uniform scalar or flat-tuple literal payload."""
    if not alternatives:
        # Every reaching alternative has already taken a failure guard.
        # The expression has no normal value, but SolverIR remains total,
        # so provide a scalar value that is unreachable on every normal
        # path.  Keeping this as the unique bottom representation also
        # lets a failed selector propagate through further selectors
        # without evaluating their keys.
        low._spend()
        return ir.Lit(0)
    record_values = [value for _active, value in alternatives]
    if record_values and all(
            type(value) is dict and value
            and all(type(field) is str for field in value)
            for value in record_values):
        schemas = [frozenset(value) for value in record_values]
        if any(schema != schemas[0] for schema in schemas[1:]):
            raise LoweringError(
                node, "possible record payloads have different fields",
                code="table-value-shape-unsupported")
        fields = []
        contracts = {}
        for field in sorted(schemas[0]):
            selected = choose_payload(low, tuple(((active, value[field]) for active, value in alternatives)), node)
            contract = contract_of_value(record_values[0][field])
            for value in record_values[1:]:
                contract = join_contracts(
                    contract, contract_of_value(value[field]))
            if contract in (UNKNOWN, INCOMPATIBLE):
                raise LoweringError(
                    node, f"record field {field!r} has no common finite "
                          "representation",
                    code="table-value-shape-unsupported")
            fields.append((field, selected))
            contracts[field] = contract
        orders = [tuple(value) for value in record_values]
        order = orders[0] if all(candidate == orders[0]
                                 for candidate in orders[1:]) else None
        return RecordValue(
            tuple(fields), ValueContract.record(contracts), order)

    shapes = [payload_shape(value)
              for _active, value in alternatives]
    shape = shapes[0] if shapes else None
    if shape is None or any(candidate != shape for candidate in shapes):
        raise LoweringError(
            node, "a nested static collection ends in nonuniform or "
                  "nested payloads, which have no flat IR representation",
            code="table-value-shape-unsupported")

    width = shape[0] if shape else 1
    # Charge before constructing the ite forest.  A final whole-IR cost
    # check is too late: a large ROM path must not first allocate the
    # object whose construction the budget is meant to prevent.
    low._spend(max(1, len(alternatives) * width))

    def choose(part):
        """Priority-select one guarded scalar in logarithmic depth.

        Alternatives are ordered: the old right-folded ``ite`` made the
        first true guard win, but also made depth linear in a finite table's
        size.  Each node returns both whether its half is active and the
        value selected within it.  Choosing the left half whenever it is
        active preserves that priority even if guards overlap, while the
        tree has one node per alternative and logarithmic depth.
        """
        layer = []
        all_boolean = True
        for active, value in alternatives:
            scalar = value if not shape else value[part]
            all_boolean &= type(scalar) is bool
            layer.append((active, ir.Lit(scalar)))
        while len(layer) > 1:
            following = []
            for index in range(0, len(layer) - 1, 2):
                left_active, left_value = layer[index]
                right_active, right_value = layer[index + 1]
                following.append((
                    ir.Op("lor", left_active, right_active),
                    ir.Op("ite", left_active, left_value, right_value),
                ))
            if len(layer) % 2:
                following.append(layer[-1])
            layer = following
        active, selected = layer[0]
        return ir.Op("ite", active, selected,
                     ir.Lit(False if all_boolean else 0))

    parts = []
    for part in range(width):
        parts.append(choose(part))
    return ir.Op("arraylit", *parts) if shape else parts[0]


def lookup(low, name, key, node, default=None,
           default_source=_NO_STATIC_DEFAULT, *, entries=None):
    """A constant table read at ``key``.

    A table whose keys are a static set of integers is represented by one
    array per scalar payload component.  This covers scalar values and
    fixed flat tuples, as well as negative and sparse keys.  A nested or
    nonuniform payload has no flat SolverIR type and is refused here.

    ``node`` is the read, and it is here because the goal is named after it. The
    goal used to be named after the *table*, so every read of one table shared a
    goal, which contradicts `goals.POLICY`: a goal is a position in the source, and
    two reads of a table are two positions. It was also the one identifier in the
    program that a declaration made from the source could not match, and the 45
    models that read a table were the only ones whose declared and produced sets
    could not be compared at all.
    """
    entries = low.tables[name] if entries is None else entries
    values = list(entries.values())
    has_default = (default is not None
                   or default_source is not _NO_STATIC_DEFAULT)

    if not isinstance(key, ir.Lit) and type(key) not in (bool, int, str)\
            and low._shape(key):
        raise LoweringError(
            node, "a static collection key that is not a scalar integer",
            code="static-collection-key-unsupported")

    # The key and default have already been lowered in Python evaluation
    # order.  When the key is fixed, only its selected payload constrains
    # the result representation; an unreachable table row need not share
    # the default's type (or even the selected row's type).
    fixed_key = (key.value if isinstance(key, ir.Lit)
                 else key if type(key) in (bool, int, str) else None)
    if fixed_key is not None:
        selected = entries.get(fixed_key)
        goal = low._safety_goal(node, "indexerror")
        if has_default:
            if selected is None:
                return (choose_payload(low, ((ir.Lit(True), default_source),), node)
                        if default_source is not _NO_STATIC_DEFAULT else
                        default)
        elif selected is None:
            low.provenance.setdefault(
                goal, {"file": low._source_file(node), "line": node.lineno,
                       "col": node.col_offset, "table": name,
                       "kind": "keyerror"})
            low._guard(ir.Lit(True), goal, "KeyError", node)
            return ir.Lit(0)
        else:
            low._guard(ir.Lit(False), goal, "KeyError", node)
        return choose_payload(low, ((ir.Lit(True), selected),), node)

    if not entries:
        if has_default:
            return (choose_payload(low, ((ir.Lit(True), default_source),), node)
                    if default_source is not _NO_STATIC_DEFAULT else
                    default)
        raise LoweringError(
            node, f"a read from the empty static mapping {name} has no "
                  "value representation",
            code="table-value-shape-unsupported")

    if (isinstance(key, (_StaticRef, _TupleKey))
            or any(type(item) not in (bool, int) for item in entries)):
        # Eliminate the source discriminator into a dense ROM selector.
        # The missing sentinel still follows the existing KeyError/default
        # rule; it is not an assumed membership constraint.
        choices = [key_equal(key, item, node) for item in entries]
        present = disjunction(choices)
        key = choose_payload(
            low, [(choice, index) for index, choice in enumerate(choices)]
            + [(ir.Op("not", present), -1)], node)
        entries = dict(enumerate(entries.values()))

    if any(type(item) not in (bool, int) for item in entries):
        raise LoweringError(
            node, "a run-time lookup into a mapping with non-integer "
                  "keys",
            code="static-collection-key-unsupported")

    # Python's Boolean keys occupy the same hash/equality slots as 0 and
    # 1.  Canonicalize that source-level key space to Integer explicitly;
    # the IR's Boolean and Integer carriers themselves remain disjoint.
    entries = {int(item): value for item, value in entries.items()}
    values = list(entries.values())
    key = ir.Op("toint", key)

    # A fixed-schema mapping is a product, not a homogeneous array.  Select
    # each field under the same key guard and reconstruct the product at this
    # boundary.  Python's mapping object and its string keys never enter
    # SolverIR.  A default source record follows the same rule; other default
    # values continue through the scalar/array path below.
    record_rows = bool(values) and all(
        type(value) is dict and value
        and all(type(field) is str for field in value)
        for value in values)
    if record_rows:
        if (default_source is not _NO_STATIC_DEFAULT
                and not (type(default_source) is dict and default_source
                         and all(type(field) is str
                                 for field in default_source))):
            raise LoweringError(
                node, "a mapping lookup joins a record with a non-record "
                      "default",
                code="table-default-shape-mismatch")
        # Charge the comparison tree before constructing it.  Record
        # rows cannot be represented by one homogeneous SolverIR array,
        # so every field uses this same finite key partition.
        low._spend(max(1, 2 * len(entries)))
        choices = [numeric_equal(key, ir.Lit(item))
                   for item in entries]
        present = disjunction(choices)
        alternatives = [
            (choice, value)
            for choice, value in zip(choices, entries.values())
        ]
        goal = low._safety_goal(node, "indexerror")
        if default_source is _NO_STATIC_DEFAULT:
            low.provenance.setdefault(
                goal, {"file": low._source_file(node), "line": node.lineno,
                       "col": node.col_offset, "table": name,
                       "kind": "keyerror"})
            low._guard(ir.Op("not", present), goal, "KeyError", node)
        else:
            alternatives.append(
                (ir.Op("not", present), default_source))
        return choose_payload(low, tuple(alternatives), node)

    shapes = [payload_shape(value) for value in values]
    shape = shapes[0]
    if shape is None or any(candidate != shape for candidate in shapes):
        raise LoweringError(
            node, f"the module table {name} has nested or nonuniform "
                  "payloads, which have no flat IR representation",
            code="table-value-shape-unsupported")
    if default is not None and low._shape(default) != shape:
        raise LoweringError(
            node, f"the default for {name}.get has shape "
                  f"{low._shape(default)}, but its values have shape {shape}",
            code="table-default-shape-mismatch")

    lo, hi = min(entries), max(entries)
    index = key if lo == 0 else ir.Op("sub", key, ir.Lit(lo))

    width = shape[0] if shape else 1
    span = hi - lo + 1
    if span != len(entries):
        # A sparse mapping is finite in its number of entries, not in the
        # numerical distance between its keys.  Materialising every hole
        # would make {0: x, 2**31: y} require billions of cells.  Select
        # the declared entries with a balanced comparison tree instead.
        # This is also the exact Python ``dict`` semantics: an absent key
        # reaches the default, or KeyError for ``[]``.
        low._spend(max(1, 2 * len(entries)))
        choices = [ir.Op("eq", key, ir.Lit(item))
                   for item in entries]
        present = disjunction(choices)
        selected = choose_payload(low, tuple(zip(choices, entries.values())), node)
        goal = low._safety_goal(node, "indexerror")
        if default is None:
            low.provenance.setdefault(
                goal, {"file": low._source_file(node), "line": node.lineno,
                       "col": node.col_offset, "table": name,
                       "kind": "keyerror"})
            low._guard(ir.Op("not", present), goal, "KeyError", node)
            return selected
        if not shape:
            return ir.Op("ite", present, selected, default)
        return ir.Op("arraylit", *[
            ir.Op("ite", present,
                  ir.Sel(selected, ir.Lit(part)),
                  ir.Sel(default, ir.Lit(part)))
            for part in range(width)
        ])

    # The remaining case has one entry at every integer in the span, so a
    # compact array lookup is exact.  Charge the cells before allocating
    # them; the sparse case above is bounded by its entry count instead.
    low._spend(max(1, span * (width + 1)))

    def component(value, part):
        return value if not shape else value[part]

    def default_component(part):
        if default is None:
            return ir.Lit(0)
        return default if not shape else ir.Sel(default, ir.Lit(part))

    tables = []
    for part in range(width):
        cells = [
            (ir.Lit(component(entries[k], part))
             if k in entries else default_component(part))
            for k in range(lo, hi + 1)
        ]
        tables.append(ir.Op("arraylit", *cells))

    def read(guard=None):
        parts = [ir.Sel(table, index) for table in tables]
        if guard is not None:
            parts = [ir.Op("ite", guard, value,
                           default_component(part))
                     for part, value in enumerate(parts)]
        return (ir.Op("arraylit", *parts) if shape else parts[0])

    outside = ir.Op("lor",
                    ir.Op("lt", index, ir.Lit(0)),
                    ir.Op("ge", index, ir.Lit(hi - lo + 1)))
    if default is None:
        # Without a default a key the table does not hold is an error, which
        # is the same case the subscript rule covers, so it is the same rule.
        goal = low._safety_goal(node, "indexerror")
        low.provenance.setdefault(
            goal, {"file": low._source_file(node), "line": node.lineno,
                   "col": node.col_offset, "table": name, "kind": "keyerror"})
        low._guard(outside, goal, "KeyError", node)
        return read()
    # ``dict.get`` with a default is total.  Unlike ``table[key]``, its source
    # occurrence declares no safety goal, so there is nothing to discharge.
    return read(ir.Op("not", outside))


def key_equal(key, literal, node):
    """Python equality between a represented selector and a closed key."""
    if not is_source_key(literal):
        raise LoweringError(node, "a key outside the finite immutable subset",
                            code="static-collection-key-unsupported")
    if isinstance(key, _TupleKey):
        if type(literal) is not tuple or len(key.items) != len(literal):
            return ir.Lit(False)
        return ir._balanced_op("land", [
            key_equal(part, item, node)
            for part, item in zip(key.items, literal)], ir.Lit(True))
    if isinstance(key, _StaticRef):
        if any(not is_source_key(value)
               for _active, value in key.alternatives):
            raise LoweringError(node, "an unhashable finite selector",
                                code="static-collection-key-unsupported")
        return disjunction([active for active, value in key.alternatives if value == literal])
    # Ordinary scalar IR selectors carry Boolean/integer values, never
    # Python strings. In particular an enum's storage tag is not its value.
    return (ir.Lit(False) if type(literal) in (str, tuple) else
            numeric_equal(key, ir.Lit(literal)))


def _closed_literal(low, node, *, mapping_key=False):
    """A closed plain collection subtree, with lists made immutable.

    This evaluates no source operation.  Scalar leaves are accepted only
    when the shared declaration-time authority proves them fixed; lists
    are normalized to tuples because the only admitted uses are reads,
    iteration and further static selection.  Any mutation or escape is
    rejected at its own boundary.
    """
    if isinstance(node, ast.Constant):
        if type(node.value) in (bool, int, str, bytes, type(None)):
            return node.value
        return _NO_STATIC_LITERAL
    if mapping_key and isinstance(node, (ast.List, ast.Dict)):
        return _NO_STATIC_LITERAL
    if isinstance(node, (ast.Tuple, ast.List)):
        values = tuple(_closed_literal(low, item, mapping_key=mapping_key)
                       for item in node.elts)
        if any(value is _NO_STATIC_LITERAL for value in values):
            return _NO_STATIC_LITERAL
        return values
    if isinstance(node, ast.Dict):
        result = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                return _NO_STATIC_LITERAL
            key = _closed_literal(low, key_node, mapping_key=True)
            value = _closed_literal(low, value_node)
            if key is _NO_STATIC_LITERAL or value is _NO_STATIC_LITERAL:
                return _NO_STATIC_LITERAL
            try:
                result[key] = value
            except (TypeError, ValueError):
                return _NO_STATIC_LITERAL
        return result
    value = low._source_fixed_value(node)
    return (value if type(value) in (bool, int, str, bytes)
            else _NO_STATIC_LITERAL)


def requires_reference(low, node):
    """Whether selection must retain its finite tag past this expression."""
    if _is_character_call(low, node):
        return True
    if isinstance(node, ast.IfExp):
        truth = low._source_static_truth(node.test)
        if type(truth) is bool:
            return requires_reference(low, node.body if truth else node.orelse)
        if any(requires_reference(low, part)
               for part in (node.body, node.orelse)):
            return True
    values = possible_values(low, node)
    return bool(values) and any(
        not payload_encodable(value) for value in values)


def _is_character_call(low, node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "chr" and low._spelling_is_builtin("chr"))


def character(low, node):
    """Carry a proved-valid finite codepoint domain as source strings.

    Unicode codepoints, including surrogates, are Python characters, not
    bytes or source integers. Existing static selectors own their identity,
    comparisons and lookups. Charge the finite representation to the normal
    construction budget before materializing it; no ASCII-only restriction
    or independent cardinality limit is introduced.
    """
    if node.keywords or len(node.args) != 1:
        raise LoweringError(node, "chr needs one positional argument",
                            code="call-argument-form")
    source = node.args[0]
    value = low._integer_context_expr(source)
    if low._shape(value) or low._source_scalar_type_for_value(
            source, value) not in (value_types.BOOLEAN, value_types.INTEGER):
        raise LoweringError(node, "chr requires an integer codepoint",
                            code="builtin-type-unsupported")
    interval = low._range_intersection(low._value_range_candidates(value, source))
    if interval is None or interval.lo < 0 or interval.hi > 0x10FFFF:
        raise LoweringError(
            node, "chr requires a proved valid finite Unicode codepoint domain",
            code="character-domain-unproved")
    low._spend(interval.hi - interval.lo + 1)
    value = low._capture(value)
    return _StaticRef(tuple(
        (ir.Op("eq", value, ir.Lit(codepoint)), chr(codepoint))
        for codepoint in range(interval.lo, interval.hi + 1)))


def ordinal(low, node):
    """Eliminate a finite one-character string/one-byte source value."""
    if node.keywords or len(node.args) != 1:
        raise LoweringError(node, "ord needs one positional argument",
                            code="call-argument-form")
    argument = node.args[0]
    reference = (lower_reference(low, argument, preserve_reference=True)
                 if is_reference(low, argument) else low.expr(argument))
    if not isinstance(reference, _StaticRef) or not reference.alternatives\
            or any(type(value) not in (str, bytes) or len(value) != 1
                   for _active, value in reference.alternatives):
        raise LoweringError(
            node, "ord requires a finite single-character or single-byte value",
            code="character-domain-unproved")
    return choose_payload(
        low, tuple((active, ord(value)) for active, value in reference.alternatives), node)


def is_reference(low, node):
    """Whether ``node`` starts from a compile-time collection reference."""
    if _is_character_call(low, node):
        return True
    if isinstance(node, ast.Constant) and type(node.value) in (str, bytes):
        return True
    if isinstance(node, (ast.Tuple, ast.List)):
        value = _closed_literal(low, node)
        return (type(value) in (tuple, list)
                and any(type(item) is str for item in value)
                and all(type(item) in (bool, int, str) for item in value))
    declared = low._structured_type_at(node)
    if declared is not None and declared.kind == "enumeration":
        return True
    if isinstance(node, ast.Attribute):
        return (isinstance(node.value, ast.Name) and node.value.id == "self"
                and node.attr in low.static_fields)
    if isinstance(node, ast.Dict):
        value = _closed_literal(low, node)
        # Fixed-string mappings are source records and use the product
        # representation.  This path is for finite lookup tables whose
        # keys are selectors, not fields.
        # An empty literal carries no evidence that it is a lookup table.
        # In ordinary source it is commonly the seed of a record or list
        # builder, whose later writes determine the value contract.  Keep
        # that established builder interpretation; a finite lookup table
        # enters this path only when at least one selector key says so.
        return (value is not _NO_STATIC_LITERAL and bool(value)
                and any(type(key) is not str for key in value))
    if isinstance(node, ast.Name):
        name = low._name(node.id)
        status = low.flow.static_values.get(name)
        if isinstance(status, (_StaticRef, _StaticIssue)):
            return True
        return (low._model_module_name(node)
                and node.id in low.tables)
    if isinstance(node, ast.Subscript) and not isinstance(
            node.slice, ast.Slice):
        return is_reference(low, node.value)
    if isinstance(node, ast.IfExp):
        return (is_reference(low, node.body)
                or is_reference(low, node.orelse))
    if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and not node.keywords
            and len(node.args) in (1, 2)):
        return is_reference(low, node.func.value)
    return False


def possible_values(low, node):
    """Closed source values denoted by a static-reference expression.

    This is deliberately representation-free.  It distinguishes an
    expression whose *result* is still a collection reference from an
    ordinary scalar expression that merely reads through one.  The latter
    belongs to the normal conditional/value lowerer.
    """
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return (node.value,)
    if isinstance(node, (ast.Tuple, ast.List)) and is_reference(low, node):
        return (tuple(_closed_literal(low, node)),)
    declared = low._structured_type_at(node)
    if declared is not None and declared.kind == "enumeration":
        return tuple(member for member, _tag in declared.tag_map)
    if isinstance(node, ast.Attribute) and is_reference(low, node):
        return (low.static_fields[node.attr],)
    if isinstance(node, ast.Dict) and is_reference(low, node):
        value = _closed_literal(low, node)
        return (None if value is _NO_STATIC_LITERAL else (value,))
    if isinstance(node, ast.Name):
        name = low._name(node.id)
        status = low.flow.static_values.get(name)
        if isinstance(status, _StaticIssue):
            return None
        if isinstance(status, _StaticRef):
            return tuple(value for _active, value in status.alternatives)
        if low._model_module_name(node) and node.id in low.tables:
            return (low.tables[node.id],)
        return None
    if isinstance(node, ast.IfExp):
        truth = low._source_static_truth(node.test)
        if type(truth) is bool:
            return possible_values(low, node.body if truth else node.orelse)
        left = possible_values(low, node.body)
        right = possible_values(low, node.orelse)
        if left is None:
            literal = _closed_literal(low, node.body)
            left = (None if literal is _NO_STATIC_LITERAL else
                    (literal,))
        if right is None:
            literal = _closed_literal(low, node.orelse)
            right = (None if literal is _NO_STATIC_LITERAL else
                     (literal,))
        if left is None or right is None:
            return None
        return (*left, *right)
    if isinstance(node, ast.Subscript) and not isinstance(
            node.slice, ast.Slice):
        containers = possible_values(low, node.value)
        if containers is None:
            return None
        fixed = _fixed_key(low, node.slice)
        values = []
        for container in containers:
            if container is None:
                # This source alternative terminates with TypeError at
                # the selector and therefore contributes no normal value.
                continue
            if not isinstance(container, (dict, tuple)):
                return None
            if fixed is None:
                values.extend(container.values() if isinstance(
                    container, dict) else container)
                continue
            try:
                values.append(container[fixed])
            except (KeyError, IndexError, TypeError):
                # This alternative terminates at the source read and has
                # no normal result to classify.
                continue
        return tuple(values)
    if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and not node.keywords
            and len(node.args) in (1, 2)):
        containers = possible_values(low, node.func.value)
        if containers is None or any(
                value is not None and type(value) is not dict
                for value in containers):
            return None
        # A preceding finite lookup may also yield ``None``.  Calling
        # ``.get`` on that alternative has no normal source result; the
        # enclosing conditional, when present, narrows it away before
        # this expression is lowered.  Keep it out of the result-shape
        # calculation rather than treating the optional mapping as an
        # unrelated dynamic value.
        containers = tuple(value for value in containers
                           if type(value) is dict)
        if len(node.args) == 1:
            defaults = (None,)
        elif is_reference(low, node.args[1]):
            defaults = possible_values(low, node.args[1])
        else:
            default = _closed_literal(low, node.args[1])
            defaults = (None if default is _NO_STATIC_LITERAL else
                        (default,))
        if defaults is None:
            return None
        fixed = _fixed_key(low, node.args[0])
        values = []
        for container in containers:
            if fixed is not None:
                values.extend((container[fixed],)
                              if fixed in container else defaults)
            else:
                values.extend(container.values())
                values.extend(defaults)
        return tuple(values)
    return None


def collection_result(low, node):
    """Whether every normal result remains a finite collection."""
    values = possible_values(low, node)
    return reference_result(values)


def _get(low, node):
    """Eliminate ``mapping.get`` over a finite guarded source mapping."""
    if node.keywords or len(node.args) not in (1, 2):
        raise LoweringError(
            node, "static mapping get needs one or two positional "
                  "arguments",
            code="call-argument-form")
    base = lower_reference(low, node.func.value, preserve_reference=True)
    if not isinstance(base, _StaticRef) or any(
            type(value) is not dict
            for _active, value in base.alternatives):
        raise LoweringError(
            node.func.value, "get is applied to something other than a "
                             "finite static mapping",
            code="static-collection-kind-ambiguous")

    fixed_key, key = selector(low, node.args[0])
    if fixed_key is None:
        key = low._capture(key)
    if len(node.args) == 1:
        default = _StaticRef(((ir.Lit(True), None),))
    else:
        default = lower_reference(low, node.args[1], preserve_reference=True)
        if not isinstance(default, _StaticRef):
            literal = _closed_literal(low, node.args[1])
            if literal is _NO_STATIC_LITERAL:
                # Local and module tables share the ordinary scalar
                # lookup encoder. The key was captured before evaluating
                # the default, which Python evaluates even on a hit.
                default = low.expr(node.args[1]) if default is None else default
                if (low._source_scalar_type_for_value(node.args[1], default)
                        not in (value_types.BOOLEAN, value_types.INTEGER)
                        or any(type(value) not in (bool, int)
                               for _active, mapping in base.alternatives
                               for value in mapping.values())):
                    raise LoweringError(
                        node.args[1], "a dynamic static-mapping default "
                                      "has no common scalar representation",
                        code="static-get-default-not-closed")
                result = default
                for active, mapping in reversed(base.alternatives):
                    selected = lookup(
                        low, "<static>", fixed_key if fixed_key is not None else key,
                        node, default, entries=mapping)
                    result = ir.Op("ite", active, selected, result)
                return result
            default = _StaticRef(((ir.Lit(True), literal),))

    alternatives = []
    for active, mapping in base.alternatives:
        if fixed_key is not None:
            if fixed_key in mapping:
                alternatives.append((active, mapping[fixed_key]))
            else:
                alternatives.extend(
                    (ir.Op("land", active, default_active), value)
                    for default_active, value in default.alternatives)
            continue
        choices = [key_equal(key, item, node)
                   for item in mapping]
        present = disjunction(choices)
        alternatives.extend(
            (ir.Op("land", active, choice), value)
            for choice, value in zip(choices, mapping.values()))
        missing = ir.Op("land", active, ir.Op("not", present))
        alternatives.extend(
            (ir.Op("land", missing, default_active), value)
            for default_active, value in default.alternatives)
    low._spend(max(1, len(alternatives)))
    return _StaticRef(tuple(alternatives))


def is_none(low, node):
    """Presence test for a lowering-only ``None | static`` reference."""
    ref = lower_reference(low, node, preserve_reference=True)
    if not isinstance(ref, _StaticRef):
        return None
    return disjunction([active for active, value in ref.alternatives if value is None])


def equal_sources(low, left, right, node):
    """Exact equality of finite scalar discriminators.

    Strings are useful as source-level enum labels but deliberately have
    no SolverIR carrier.  A static reference stores their finite identity
    in its tag, so equality is the disjunction of the matching tag arms.
    Collection equality is excluded because it would expose the Python
    carrier that this representation intentionally erases.
    """
    def lower(source):
        if is_reference(low, source):
            return lower_reference(low, source, preserve_reference=True)
        value = _closed_literal(low, source)
        return (low.expr(source) if value is _NO_STATIC_LITERAL else
                _StaticRef(((ir.Lit(True), value),)))

    left_value, right_value = low._in_order(left, right, lower=lower)
    return equal_values(low, left_value, right_value, left, right, node)


def equal_values(low, left, right, left_source, right_source, node):
    """Compare evaluated logical values, not their unrelated storage tags."""
    scalar_types = (bool, int, str, bytes, type(None))
    if any(type(value) not in scalar_types
           for ref in (left, right) if isinstance(ref, _StaticRef)
           for _active, value in ref.alternatives):
        raise LoweringError(
            node, "equality observes a compile-time collection carrier",
            code="static-collection-equality-observed")
    if not isinstance(left, _StaticRef):
        left, right = right, left
        left_source, right_source = right_source, left_source
    if not isinstance(right, _StaticRef):
        kind = low._source_scalar_type_for_value(right_source, right)
        if kind not in (value_types.BOOLEAN, value_types.INTEGER):
            raise LoweringError(
                node, "a finite discriminator is compared with an "
                      "unrepresented run-time value",
                code="static-discriminator-comparison-unsupported")
        return disjunction([
            ir.Op("land", active, numeric_equal(right, ir.Lit(value)))
            for active, value in left.alternatives if type(value) in (bool, int)])
    equal = []
    for left_active, left_value in left.alternatives:
        for right_active, right_value in right.alternatives:
            if left_value == right_value:
                equal.append(ir.Op(
                    "land", left_active, right_active))
    return disjunction(equal)


def _root(low, node):
    """Resolve a source binding to its lowering-only collection value."""
    if isinstance(node, ast.Attribute):
        if not is_reference(low, node):
            return None
        if "self" in low.activation.substitutions or low._name_is_local("self"):
            raise LoweringError(node, "attribute of a rebound self",
                                code="self-rebound")
        return _StaticRef(((ir.Lit(True), low.static_fields[node.attr]),))
    name = low._name(node.id)
    status = low.flow.static_values.get(name)
    if isinstance(status, _StaticIssue):
        raise LoweringError(
            node, "a static collection reference is not defined "
                  "consistently on every reaching path",
            code=status.code)
    if isinstance(status, _StaticRef):
        return status
    if (low._model_module_name(node) and node.id in low.tables):
        return _StaticRef(((ir.Lit(True), low.tables[node.id]),))
    return None


def _fixed_key(low, node):
    """A source key known without executing the step, preserving strings."""
    if isinstance(node, ast.Constant) and type(node.value) in (
            bool, int, str):
        return node.value
    return low._static_int(node)


def selector(low, node):
    """Return ``(fixed source key, lowered run-time key)``.

    Static string keys remain source values for mapping selection. Finite
    source discriminators retain their labels; ordinary runtime selectors
    must have scalar-integer IR shape. Rejecting other shapes at
    this boundary prevents arrays or unsupported literals from reaching
    ``ir.Lit``/comparison construction and failing as raw host errors.
    """
    if isinstance(node, ast.Tuple):
        items = []
        for item in node.elts:
            fixed, part = selector(low, item)
            if type(fixed) is str:
                part = _StaticRef(((ir.Lit(True), fixed),))
            items.append(low._capture(part))
        return None, _TupleKey(tuple(items))
    fixed = _fixed_key(low, node)
    if type(fixed) is str:
        return fixed, None
    if (isinstance(node, ast.Constant) and fixed is None) or isinstance(
            node, (ast.Dict, ast.List, ast.Set)):
        raise LoweringError(
            node, "a static collection key outside the integer/string "
                  "key subset",
            code="static-collection-key-unsupported")
    key = (ir.Lit(fixed) if fixed is not None else
           lower_reference(low, node, preserve_reference=True)
           if is_reference(low, node) else low.expr(node))
    if isinstance(key, _StaticRef):
        if any(not is_source_key(value) for _active, value in key.alternatives):
            raise LoweringError(node, "an unhashable finite selector",
                                code="static-collection-key-unsupported")
        if (any(type(value) is tuple for _active, value in key.alternatives)
                and not contract_is_deeply_immutable(low._contract_ast(node))):
            raise LoweringError(node, "a finite key has no immutable source carrier",
                                code="static-collection-key-unsupported")
        return fixed, key
    contract = low._contract_for_value(node, key)
    if contract.is_sequence and contract.carrier == "tuple":
        return None, tuple_key(low, key, contract, node)
    if low._shape(key):
        raise LoweringError(
            node, "a run-time static collection key that is not a "
                  "scalar integer",
            code="static-collection-key-unsupported")
    # Python accepts Boolean indices and gives Boolean and integer mapping
    # keys the same numeric/hash image.  SolverIR keeps the two semantic
    # carriers distinct, so the source selector crosses that boundary by
    # an explicit registered conversion.  A fixed selector stays literal
    # so the static-index proof below remains available.
    key = (ir.Lit(int(fixed)) if type(fixed) in (bool, int)
           else ir.Op("toint", key))
    return fixed, key


def tuple_key(low, value, contract, node):
    """Project a fixed immutable tuple without evaluating its source twice."""
    from .lower_returns import _PositionalValue

    if contract.kind == "scalar":
        return low._capture(value)
    if (not contract.is_sequence or contract.carrier != "tuple"
            or contract.exact_length is None):
        raise LoweringError(node, "a tuple key contains an unhashable or "
                            "unrepresented component",
                            code="static-collection-key-unsupported")
    value = low._capture(value)
    return _TupleKey(tuple(
        tuple_key(
            low, value.items[index] if isinstance(value, _PositionalValue)
            else ir.Sel(value, ir.Lit(index)), contract.component(index), node)
        for index in range(contract.exact_length)))


def _select(low, ref, subscript):
    """Apply one Python mapping/sequence selector to guarded subtrees."""
    if not ref.alternatives:
        goal = low._safety_goal(subscript, "indexerror")
        low._discharge(
            goal, "the preceding static selector always fails before "
                  "this selector is reached")
        return ref
    low.reached.add(id(subscript))
    fixed_key, key = selector(low, subscript.slice)
    absent = [active for active, container in ref.alternatives
              if container is None]
    present_alternatives = [
        (active, container) for active, container in ref.alternatives
        if container is not None]
    if absent:
        # A later source guard may make this arm unreachable.  Retain the
        # actual Python failure here rather than using that control-flow
        # fact as a second representation authority.
        low._guard(
            disjunction(absent),
            low._safety_goal(subscript, "indexerror"),
            "TypeError", subscript)
    if not present_alternatives:
        return _StaticRef(())
    kinds = {
        ("mapping" if isinstance(container, dict)
         else "sequence" if isinstance(container, tuple)
         else "scalar")
        for _active, container in present_alternatives
    }
    if "scalar" in kinds:
        raise LoweringError(
            subscript, "a static scalar is indexed as a collection",
            code="static-collection-depth-unsupported")
    if len(kinds) != 1:
        raise LoweringError(
            subscript, "a static reference may denote incompatible "
                       "collection kinds at this read",
            code="static-collection-kind-ambiguous")

    kind = next(iter(kinds))
    if (kind == "sequence" and (isinstance(key, (_StaticRef, _TupleKey))
            or fixed_key is not None and type(fixed_key) not in (bool, int))):
        raise LoweringError(
            subscript.slice,
            "a sequence index that is not an integer",
            code="static-collection-key-unsupported")
    expansion = sum(
        len(container) if fixed_key is None else 1
        for _active, container in present_alternatives)
    low._spend(max(1, expansion))
    following, missing = [], []
    for active, container in present_alternatives:
        if kind == "mapping":
            if fixed_key is not None:
                if fixed_key in container:
                    following.append((active, container[fixed_key]))
                else:
                    missing.append(active)
                continue
            choices = [key_equal(key, item, subscript)
                       for item in container]
            present = disjunction(choices)
            missing.append(ir.Op("land", active, ir.Op("not", present)))
            for (item, value), choice in zip(
                    container.items(), choices):
                following.append(
                    (ir.Op("land", active, choice), value))
            continue

        size = len(container)
        if fixed_key is not None:
            if type(fixed_key) not in (bool, int):
                missing.append(active)
                continue
            index = fixed_key if fixed_key >= 0 else fixed_key + size
            if 0 <= index < size:
                following.append((active, container[index]))
            else:
                missing.append(active)
            continue
        present = ir.Op(
            "land", ir.Op("ge", key, ir.Lit(-size)),
            ir.Op("lt", key, ir.Lit(size)))
        missing.append(ir.Op("land", active, ir.Op("not", present)))
        for index, value in enumerate(container):
            choice = ir.Op(
                "lor", ir.Op("eq", key, ir.Lit(index)),
                ir.Op("eq", key, ir.Lit(index - size)))
            following.append((ir.Op("land", active, choice), value))

    goal = low._safety_goal(subscript, "indexerror")
    low._guard(disjunction(missing), goal,
                "KeyError" if kind == "mapping" else "IndexError",
                subscript)
    return _StaticRef(tuple(following))


def _ifexp(low, node):
    """Select one of two collection references with source short circuit."""
    truth = low._source_static_truth(node.test)
    guard = low._capture(
        low._truth_value(node.test, low.expr(node.test)))
    entering = low._flow_facts()
    then_entry = low._assumed_source_flow_facts(
        entering, node.test, guard, True)
    else_entry = low._assumed_source_flow_facts(
        entering, node.test, guard, False)
    if truth is None:
        if then_entry is None and else_entry is not None:
            truth = False
        elif else_entry is None and then_entry is not None:
            truth = True
    if truth is not None:
        chosen = node.body if truth else node.orelse
        omitted = node.orelse if truth else node.body
        chosen_entry = then_entry if truth else else_entry
        low._set_range_facts(
            entering if chosen_entry is None else chosen_entry)
        ref = lower_reference(low, chosen, preserve_reference=True)
        if not isinstance(ref, _StaticRef):
            raise LoweringError(
                node, "a conditional joins a static collection and an "
                      "ordinary value",
                code="static-collection-join-disagrees")
        low.before(low._marked_if(
            node.test, guard, ir.Skip(), ir.Skip()))
        low.before(low._false_goal_terms([omitted]))
        return ref

    low._set_range_facts(then_entry)
    then_pre, then_value = low._scoped(
        lambda: lower_reference(low, node.body, preserve_reference=True))
    then_facts = low._flow_facts()
    low._set_range_facts(else_entry)
    else_pre, else_value = low._scoped(
        lambda: lower_reference(low, node.orelse, preserve_reference=True))
    else_facts = low._flow_facts()
    if not isinstance(then_value, _StaticRef) or not isinstance(
            else_value, _StaticRef):
        raise LoweringError(
            node, "a conditional joins a static collection and an "
                  "ordinary value",
            code="static-collection-join-disagrees")
    low._set_range_facts(low._joined_branch_flow_facts(
        guard,
        ((guard, then_facts),
         (ir.Op("not", guard), else_facts))))
    joined = low.storage.fresh()
    then_bind, then_ref = then_value.bind(low, joined, node.body)
    else_bind, else_ref = else_value.bind(low, joined, node.orelse)
    # Selector temporaries belong to the branch that evaluated them.
    # Materialize the selected source identity there; eager IR predicates
    # after the join must never read the other branch's undefined locals.
    low.before(low._marked_if(
        node.test, guard, ir.Seq(then_pre, then_bind), ir.Seq(else_pre, else_bind)))
    return _StaticRef.merge_bound((then_ref, else_ref))


def lower_reference(low, node, *, preserve_collection=False,
                  preserve_reference=False):
    """Lower a closed reference expression, or return ``None``.

    With ``preserve_collection``, a collection result remains a
    :class:`_StaticRef` for assignment to another local.  At an ordinary
    expression boundary it must instead materialise as a scalar or flat
    tuple, exactly the values SolverIR can carry.
    """
    declared = low._structured_type_at(node)
    if declared is not None and declared.kind == "enumeration":
        return low.expr(node)
    if _is_character_call(low, node):
        ref = character(low, node)
    elif isinstance(node, ast.Constant) and type(node.value) in (str, bytes):
        ref = _StaticRef(((ir.Lit(True), node.value),))
    elif isinstance(node, (ast.Dict, ast.Tuple, ast.List)) and is_reference(low, node):
        value = _closed_literal(low, node)
        if value is _NO_STATIC_LITERAL:
            return None
        if isinstance(node, (ast.Tuple, ast.List)):
            value = tuple(value)
        ref = _StaticRef(((ir.Lit(True), value),))
    elif isinstance(node, (ast.Name, ast.Attribute)):
        ref = _root(low, node)
        if ref is None:
            return None
    elif isinstance(node, ast.IfExp):
        ref = _ifexp(low, node)
    elif (isinstance(node, ast.Subscript)
          and not isinstance(node.slice, ast.Slice)):
        base = lower_reference(low, node.value, preserve_collection=True)
        if not isinstance(base, _StaticRef):
            return None
        ref = _select(low, base, node)
    elif (isinstance(node, ast.Call)
          and isinstance(node.func, ast.Attribute)
          and node.func.attr == "get"):
        ref = _get(low, node)
    elif preserve_reference:
        # A scalar literal is normally lowered by ``expr``.  Inside a
        # static-reference operation it is instead one finite alternative
        # (most importantly the ``None`` arm of an optional lookup).
        value = _closed_literal(low, node)
        if value is _NO_STATIC_LITERAL:
            return None
        ref = _StaticRef(((ir.Lit(True), value),))
    else:
        return None
    if not isinstance(ref, _StaticRef):
        return ref
    if preserve_reference:
        return ref
    if (preserve_collection
            and (not ref.alternatives or reference_result(tuple((value for _active, value in ref.alternatives))))):
        # No normal value still has to propagate through later selectors.
        # Materializing it as scalar zero here would lose the first error.
        return ref
    return choose_payload(low, ref.alternatives, node)


def table_path(low, node):
    """A complete chain ``TABLE[k0]...[kn]``, innermost first."""
    parts = []
    current = node
    while isinstance(current, ast.Subscript)\
            and not isinstance(current.slice, ast.Slice):
        parts.append(current)
        current = current.value
    if (isinstance(current, ast.Name)
            and low._model_module_name(current)
            and current.id in low.tables):
        return current.id, list(reversed(parts))
    return None


def read_path(low, name, parts, *, length=False):
    """Lower a complete read through a closed finite collection tree.

    Each selector is checked only after the preceding selector succeeds.
    The selected subtree is represented by guarded alternatives, so an
    inner mapping or sequence never has to become a first-class nested IR
    array.  The final value must still be a scalar or flat tuple.
    """
    rom = low.table_arrays.get(name)
    if rom is not None:
        # A uniform sequence is a genuine finite IR value.  Selecting it
        # directly is both the simplest semantics and asymptotically
        # smaller than a 256-arm S-box conditional at every read.
        current = ir.Var(rom)
        for subscript in parts:
            low.reached.add(id(subscript))
            fixed_key, raw = selector(low, subscript.slice)
            if (isinstance(raw, (_StaticRef, _TupleKey))
                    or fixed_key is not None
                    and type(fixed_key) not in (bool, int)):
                raise LoweringError(
                    subscript.slice,
                    "a sequence index that is not an integer",
                    code="static-collection-key-unsupported")
            current = ir.Sel(
                current, low._index(subscript, current, raw))
        if not length:
            return current
        shape = low._shape(current)
        if not shape:
            raise LoweringError(
                parts[-1], "len of a scalar static-table payload",
                code="static-collection-length-unsupported")
        return ir.Lit(shape[0])

    # A one-level mapping with a uniform scalar or flat-tuple payload is a
    # finite ROM even though its source carrier is a dict.  Use the same
    # indexed-table rule as ``TABLE.get`` rather than expanding a lookup
    # into one conditional per key.  The latter is extensionally correct
    # but gives a 256-entry S-box 256 levels of accidental expression
    # nesting.  Nested or nonuniform mappings stay on the guarded-reference
    # path below, which preserves their path-dependent value contracts.
    entries = low.tables[name]
    if len(parts) == 1 and isinstance(entries, dict) and entries:
        shapes = [payload_shape(value)
                  for value in entries.values()]
        if shapes[0] is not None and all(shape == shapes[0]
                                         for shape in shapes):
            subscript = parts[0]
            low.reached.add(id(subscript))
            fixed_key, key = selector(low, subscript.slice)
            selected = lookup(low, name, fixed_key if fixed_key is not None else key, subscript)
            if not length:
                return selected
            shape = low._shape(selected)
            if not shape:
                raise LoweringError(
                    subscript, "len of a scalar static-table payload",
                    code="static-collection-length-unsupported")
            return ir.Lit(shape[0])

    reference = _StaticRef(((ir.Lit(True), low.tables[name]),))
    for subscript in parts:
        reference = _select(low, reference, subscript)

    if length:
        return reference.length(low, parts[-1])
    return choose_payload(low, reference.alternatives, parts[-1])
