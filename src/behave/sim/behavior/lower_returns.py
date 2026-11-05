"""Output initialization, return destinations and recursive lowering carriers.

These builders emit commands through their lowerer; they do not plan source
control flow or allocate a separate execution semantics. Public transaction
returns and inlined helper returns share the same typed value contracts.
Output controls and buffers are allocated here before the caller installs ROMs
and writable input views. A buffer's zero image does not count as a transaction.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ..core.value_type import ValueType
from ..symbolic import ir
from . import lower_static_refs
from ..symbolic import ranges
from ..symbolic import typecheck as value_types
from .extract import LoweringError, _walk_function_scope
from .lower_static_refs import _StaticRef
from .output_boundary import OutputBoundaryBuilder
from ..symbolic.procedure_contract import OutputBoundaryError
from .source_batches import BatchForm, BatchPlan, batch_value_contract, record_batch_payload
from .source_calls import record_items, record_key
from .source_options import OptionValue
from .source_records import RecordPlanError, RecordValue, record_from_leaves, record_value
from .source_records import leaf_contracts as record_leaf_contracts
from .source_records import literal_entries as record_literal_entries
from ..symbolic.value_contract import (
    INCOMPATIBLE,
    NONE,
    SCALAR,
    UNKNOWN,
    ValueContract,
    contract_contains,
    join_contracts,
)
from ..symbolic.value_binding import ValueBindingError
from ..symbolic.value_layout import layout_of_value_type

_BATCH_FORM_CODES = {
    BatchForm.NONE: 0,
    BatchForm.MAPPING: 1,
    BatchForm.LIST: 2,
}

def _batch_form_code(form):
    """Stable internal tag for a source batch carrier."""
    try:
        return _BATCH_FORM_CODES[form]
    except KeyError:
        raise TypeError("an unknown batch source form") from None

def _set_batch_form(target, form):
    """Assign a carrier tag, or do nothing at the public boundary."""
    return (ir.Skip() if target is None else
            ir.Assign(target, ir.Lit(_batch_form_code(form))))

class _NamedReturn:
    """Where a procedure's returns put their values.

    What a step produces is a sequence of output transactions, not one value:
    a model may answer nothing on the beats it is still consuming, one thing on
    an ordinary beat, and several when one input expands into many. So each
    output is an array of the values the step produced and the step carries how
    many that was, which is the same shape the comparison reads on the other
    side. One output is the case where that count is one, and none is the case
    where it is zero, so neither is a form of its own.

    The names are fixed once for the procedure. A mapping names them by key, a
    tuple gives them positionally, and a single expression gives the one
    output.
    """

    def __init__(self, done, count, names, capacity=1, held=None, bindings=(),
                 *, boundary=False):
        self.done = done
        self.count = count
        # The names a return may use, which are the ports, and the variable
        # each is held in, which is usually the same and is not where a field
        # of the step shares the port's name.
        self.names = list(names)
        self.held = dict(held or {name: name for name in self.names})
        self.bindings = dict(bindings)
        if set(self.bindings) - set(self.names):
            raise ValueError("an output binding names no logical return field")
        self.capacity = max(capacity, 1)
        self.boundary = bool(boundary)

    @staticmethod
    def _array_value(children):
        return ir.Op("arraylit", *children)

    def _coerce_declared_value(self, low, value_type, value, source):
        """Project an already evaluated value through one declared layout.

        Literal products are lowered recursively by ``_structured_literal``.
        This companion handles values that already live in the lowering: a
        named fixed array, a named record product, or a helper-batch cell.  It
        still follows only the declaration.  In particular, it never reads a
        Python runtime value to discover fields or array lengths.
        """
        if value_type.kind == "record":
            if not isinstance(value, RecordValue):
                raise LoweringError(
                    source, "a declared record output has no fieldwise "
                            "representation",
                    code="declared-value-shape-mismatch")
            children = dict(value_type.fields)
            if set(value.by_name) != set(children):
                raise LoweringError(
                    source, "a declared record output does not have exactly "
                            "its declared fields",
                    code="declared-value-shape-mismatch")
            rows = []
            for field, child_type in value_type.fields:
                child = self._coerce_declared_value(
                    low, child_type, value.by_name[field], source)
                if isinstance(child, _StructuredValue):
                    rows.extend(((field,) + path, expression)
                                for path, expression in child.leaves)
                else:
                    rows.append(((field,), child))
            return _StructuredValue(value_type, tuple(rows))
        if value_type.kind == "array":
            def direct_shape(declared):
                if declared.kind in ("boolean", "integer", "enumeration"):
                    return ()
                if declared.kind != "array":
                    return None
                child = direct_shape(declared.element)
                return (None if child is None else
                        (declared.cells,) + child)

            expected = direct_shape(value_type)
            if expected is None:
                raise LoweringError(
                    source, "an evaluated array of record products has no "
                            "single SolverIR value representation",
                    code="declared-value-shape-mismatch")
            if low._shape(value) != expected:
                raise LoweringError(
                    source, "a declared fixed-array output has another "
                            "representation shape",
                    code="declared-value-shape-mismatch")
            rows = []
            for index in range(value_type.cells):
                child = self._coerce_declared_value(
                    low, value_type.element,
                    ir.Sel(value, ir.Lit(index)), source)
                if isinstance(child, _StructuredValue):
                    rows.extend(((index,) + path, expression)
                                for path, expression in child.leaves)
                else:
                    rows.append(((index,), child))
            return _StructuredValue(value_type, tuple(rows))
        if low._shape(value):
            raise LoweringError(
                source, "a declared scalar output is represented by an array",
                code="declared-value-shape-mismatch")
        if isinstance(value, (RecordValue, _StructuredValue, OptionValue)):
            raise LoweringError(
                source, "a declared scalar output is represented by a product",
                code="declared-value-shape-mismatch")
        if value_type.kind == "integer" and value_type.carrier.signed:
            # The source side of a signed hardware boundary admits either its
            # mathematical signed value or the nonnegative bit pattern models
            # conventionally return after ``to_unsigned``.  Apply ``low_Y``
            # only after the abstract range proves every value belongs to the
            # union of those two domains.  Otherwise leave the term unchanged:
            # the ordinary output-contract query will refuse a reachable
            # out-of-domain value rather than silently mask it.
            known = ranges.bounds_of_expression(
                value, low.storage.carrier_ranges)
            plan = low.sequence_plan
            scope = low.activation.scope
            context = (low.activation.summary)
            if known is None and plan is not None and scope is not None \
                    and isinstance(source, ast.AST):
                known = plan.expression_range(scope, source, context)
            if known is None:
                known = ranges.bounds_of_expression(value, low.flow.bounds)
            # ``bounds_of_expression`` returns possibly open ``Bounds`` while
            # the source execution plan already owns a finite ``Range``.
            # Normalize both representations before checking the boundary.
            finite = (None if known is None else
                      ranges.Bounds.of(known).finite())
            width = value_type.carrier.width
            logical_low = -(1 << (width - 1))
            pattern_high = (1 << width) - 1
            if (finite is not None and finite.lo >= logical_low
                    and finite.hi <= pattern_high):
                return ir.Op("swrap", value, ir.Lit(width))
        return value

    def _declared_value(self, low, binding, value, source):
        if isinstance(value, ast.AST):
            if ((binding.value_type.kind == "record"
                 and record_items(value, is_builtin=low._spelling_is_builtin) is not None)
                    or (binding.value_type.kind == "array"
                        and isinstance(value, (ast.List, ast.Tuple)))
                    or binding.value_type.kind == "enumeration"
                    and isinstance(value, ast.Constant)):
                return low._structured_literal(binding.value_type, value)
            value = low.expr(value)
        return self._coerce_declared_value(
            low, binding.value_type, value, source)

    def _physical_value(self, low, name, value, source):
        """Lower one logical output value to its declared physical leaves."""
        binding = self.bindings.get(name)
        if binding is None:
            return {name: low.expr(value) if isinstance(value, ast.AST) else value}
        lowered = self._declared_value(low, binding, value, source)
        if isinstance(lowered, _StructuredValue):
            if lowered.value_type != binding.value_type:
                raise LoweringError(
                    source, f"output {name!r} has another declared recursive type",
                    code="declared-value-shape-mismatch")
            logical = lowered.by_path
        elif binding.value_type.kind in ("boolean", "integer", "enumeration"):
            if low._shape(lowered):
                raise LoweringError(
                    source, f"output {name!r} is structured where its task "
                            "ValueType is scalar",
                    code="signature-outputs-mismatch")
            logical = {(): lowered}
        else:
            raise LoweringError(
                source, f"output {name!r} has no complete recursive leaf image",
                code="declared-value-shape-mismatch")
        if self.boundary and low.output_boundary is not None:
            try:
                low.output_boundary.record_values(
                    binding, logical,
                    lambda expression: low._output_value_range(
                        expression, source),
                    source)
            except OutputBoundaryError as exc:
                raise LoweringError(
                    exc.node or source, str(exc), code=exc.code) from None
        try:
            return binding.assemble_solver_values(
                logical, array_factory=self._array_value)
        except ValueBindingError as exc:
            raise LoweringError(
                source, f"output {name!r} cannot be assembled from its declared "
                        f"SolverIR leaves: {exc}", code=exc.code) from None

    def _lower_groups(self, low, groups):
        """Evaluate logical transaction fields in exact source order.

        A task declaration types a *logical* output, before ``ValueBinding``
        projects it onto physical SolverIR leaves.  Keep the source expression
        intact until that point: lowering an enclosing mapping first loses the
        declared type needed by recursive records and enumerations.

        Repeated mapping keys still evaluate every value and retain the last
        one, as Python does.  Physical leaves may consequently repeat for one
        logical name, but two distinct logical outputs may never share them.
        """
        out = []
        for group in groups:
            group = tuple(group)
            by_name = {}
            for index, (name, value) in enumerate(group):
                values = self._physical_value(low, name, value, value)
                if isinstance(value, ast.AST) and any(
                        isinstance(later, ast.AST) and low._effectful(later)
                        for _later_name, later in group[index + 1:]):
                    values = {
                        variable: low._capture(expression)
                        for variable, expression in values.items()
                    }
                by_name[name] = values

            physical = {}
            owners = {}
            for name, values in by_name.items():
                overlap = sorted(
                    variable for variable in values
                    if variable in owners and owners[variable] != name)
                if overlap:
                    raise LoweringError(
                        group[-1][1],
                        "two logical outputs share physical SolverIR "
                               f"leaves: {overlap}",
                        code="value-binding-solver-path-duplicate")
                owners.update({variable: name for variable in values})
                physical.update(values)
            out.append(physical)
        return out

    def _store_group(self, low, group, slot):
        command = None
        for variable, value in group.items():
            if variable not in self.held:
                raise ValueError(
                    f"physical output {variable!r} has no batch buffer")
            target = self.held[variable]
            container = low.flow.contracts.get(target, UNKNOWN)
            if (container.is_sequence and container.exact_length is not None
                    and container.element is not None):
                one = low._store_sequence_element(
                    target, slot, value, container.element,
                    container, value)
            else:
                one = ir.Store(target, slot, value)
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def assign(self, low, node):
        """Write the transactions a return produces and record how many."""
        plan = (None if low.sequence_plan is None else
                low.sequence_plan.return_plan(node))
        if plan is not None:
            if not isinstance(plan, BatchPlan):
                raise LoweringError(
                    node, "the source return plan has the wrong type",
                    code="source-plan-inconsistent")
            if plan.contract.maximum > self.capacity:
                raise LoweringError(
                    node, "the source return plan exceeds its allocated "
                          "transaction capacity",
                    code="source-plan-inconsistent")
            if self.boundary and low.output_boundary is not None:
                low.output_boundary.record_return(node, plan)
            # ``count`` is both the public batch length and the private cursor
            # used while this one return value is materialised.  A source
            # return replaces the procedure result; it never appends to a
            # value produced by another return occurrence.  Reset the cursor
            # at that semantic boundary.  The reset is concretely redundant
            # because only one return can execute, but making the replacement
            # explicit also preserves that fact when mutually exclusive early
            # returns are represented by guarded loop copies.
            return ir.Seq(
                ir.Assign(self.count, ir.Lit(0)),
                self._emit_plan(low, node, plan))

        if self.boundary and low.output_boundary is not None:
            low.output_boundary.record_return(node, None)

        value = node.value
        if isinstance(value, ast.IfExp):
            return self._conditional(low, node, value)
        if isinstance(value, ast.Name):
            builder = low.storage.carrier(_BatchBuilder, low._name(value.id))
            if builder is not None:
                cmd = ir.Assign(self.count, ir.Var(builder.count))
                for name in self.names:
                    cmd = ir.Seq(cmd, ir.Assign(
                        self.held[name], ir.Var(builder.buffers[name])))
                return cmd
        if isinstance(value, ast.ListComp):
            return low._return_comprehension(node, value, self)
        if isinstance(value, ast.DictComp):
            return low._return_dict_comprehension(node, value, self)
        groups = self._groups(low, node)
        if len(groups) > self.capacity:
            raise LoweringError(
                node, f"return of {len(groups)} transactions for a step that "
                      f"carries {self.capacity}", code="return-transaction-arity")
        groups = self._lower_groups(low, groups)
        cmd = ir.Assign(self.count, ir.Lit(len(groups)))
        for slot, group in enumerate(groups):
            cmd = ir.Seq(
                cmd, self._store_group(low, group, ir.Lit(slot)))
        return cmd

    def _emit_plan(self, low, returned, plan, *, form_target=None):
        """Execute one source-owned batch plan into the public buffers.

        This method deliberately does not infer a schema or a transaction
        count from syntax.  Those are properties of ``plan``.  It only lowers
        the source occurrences retained by that plan, in their source order,
        and writes the resulting records into the already allocated boundary
        representation.
        """
        if plan.kind == "none":
            return _set_batch_form(form_target, BatchForm.NONE)
        if plan.kind == "record":
            mark = _set_batch_form(form_target, BatchForm.MAPPING)
            if isinstance(plan.node, ast.DictComp):
                emit = low._return_dict_comprehension(
                    returned, plan.node, self, reset=False)
            else:
                emit = self._append_record(low, plan.node)
            return ir.Seq(mark, emit)
        if plan.kind == "list":
            emit = self._emit_many(
                self._emit_plan(low, returned, child, form_target=None)
                for child in plan.children)
            return ir.Seq(
                _set_batch_form(form_target, BatchForm.LIST), emit)
        if plan.kind == "concat":
            emit = self._emit_many(
                self._emit_plan(low, returned, child, form_target=None)
                for child in plan.children)
            return ir.Seq(
                _set_batch_form(form_target, BatchForm.LIST), emit)
        if plan.kind == "repeat":
            return self._emit_repeat(
                low, returned, plan, form_target=form_target)
        if plan.kind == "choice":
            return self._emit_choice(
                low, returned, plan, form_target=form_target)
        if plan.kind in {"sequence", "summary"}:
            if isinstance(plan.node, ast.ListComp):
                emit = low._return_comprehension(
                    returned, plan.node, self, reset=False)
                return ir.Seq(
                    _set_batch_form(form_target, BatchForm.LIST), emit)
            value = None
            if isinstance(plan.node, (ast.Name, ast.Call, ast.Attribute)):
                value = low.expr(plan.node)
                if isinstance(value, ir.Var):
                    builder = low.storage.carrier(_BatchBuilder, value.name)
                    if builder is not None:
                        contract = low.flow.contracts.get(
                            value.name, UNKNOWN)
                        form = (ir.Var(builder.form)
                                if builder.form is not None else
                                ir.Lit(_batch_form_code(BatchForm.LIST)))
                        value = _BatchValue(builder, form, contract)
            if isinstance(value, _BatchValue):
                return self._copy_builder(
                    low, plan, value, form_target=form_target)
            raise LoweringError(
                plan.node, "the planned transaction sequence has no lowered "
                           "batch representation",
                code="source-plan-inconsistent")
        raise LoweringError(
            plan.node, f"unknown source batch-plan kind {plan.kind!r}",
            code="source-plan-inconsistent")

    @staticmethod
    def _emit_many(commands):
        command = None
        for one in commands:
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def _append_record(self, low, node):
        """Evaluate one planned record and append it at the current cursor."""
        # A batch literal evaluates each record completely before starting the
        # next one.  Keep the record's hoisted effects beside its stores;
        # collecting every child first would move a later helper call ahead of
        # an earlier record's field capture.
        if record_items(node, is_builtin=low._spelling_is_builtin) is not None:
            try:
                entries = record_literal_entries(
                    node, is_builtin=low._spelling_is_builtin)
            except RecordPlanError as exc:
                raise LoweringError(
                    exc.node, exc.message, code=exc.code) from None
            # The output declaration, rather than the enclosing untyped
            # mapping, owns recursive field semantics.  In particular, an
            # enumeration string becomes a tag only when its logical output's
            # ValueBinding declares that enumeration at the same path.
            pre, groups = low._scoped(
                lambda: self._lower_groups(low, (entries,)))
        else:
            pre, value = low._scoped(lambda: low.expr(node))
            if not isinstance(value, RecordValue):
                raise LoweringError(
                    node, "a planned output transaction has no fieldwise "
                          "representation",
                    code="source-plan-inconsistent")
            entries = value.fields
            groups = self._lower_groups(low, (entries,))
        if {name for name, _source in entries} != set(self.names):
            raise LoweringError(
                node, "the lowered transaction disagrees with its source "
                      "schema",
                code="source-plan-inconsistent")
        cursor = ir.Var(self.count)
        command = self._store_group(low, groups[0], cursor)
        advance = ir.Assign(
            self.count, ir.Op("add", ir.Var(self.count), ir.Lit(1)))
        emit = ir.Seq(command, advance)
        return ir.Seq(pre, emit)

    def _copy_builder(self, low, plan, value, *, form_target=None):
        """Append a finite source list already held as parallel arrays."""
        builder = value.builder
        if plan.contract.maximum == 0:
            return (ir.Skip() if form_target is None else
                    ir.Assign(form_target, value.form))
        if set(builder.buffers) != set(self.names):
            raise LoweringError(
                plan.node, "the lowered transaction sequence disagrees with "
                           "its source schema",
                code="source-plan-inconsistent")
        if builder.capacity < plan.contract.maximum:
            raise LoweringError(
                plan.node, "the lowered transaction sequence is smaller than "
                           "its source plan",
                code="source-plan-inconsistent")
        base = ir.Var(self.count)
        counter = low.storage.fresh()
        low._remember_source_scalar_type(counter, value_types.INTEGER)
        index = ir.Var(counter)
        destination = ir.Op("add", base, index)
        logical = []
        for name in self.names:
            source = builder.buffers[name]
            contract = low.flow.contracts.get(source, UNKNOWN)
            item = (low._select_sequence_element(
                ir.Var(source), index, contract, plan.node)
                if contract.is_sequence else ir.Sel(ir.Var(source), index))
            logical.append((name, item))
        physical = self._lower_groups(low, (logical,))[0]
        stores = self._store_group(low, physical, destination)
        # Copy the same guarded positions in the same order, but retain the
        # finite loop instead of materializing capacity copies of every field.
        # Source evaluation happened when the builder was produced; these
        # moves introduce no source occurrences or additional goal events.
        commands = [ir.For(counter, ir.Lit(plan.contract.maximum), ir.If(
            ir.Op("lt", index, ir.Var(builder.count)), stores, ir.Skip()))]
        commands.append(ir.Assign(
            self.count,
            ir.Op("add", ir.Var(self.count), ir.Var(builder.count))))
        if form_target is not None:
            commands.append(ir.Assign(form_target, value.form))
        return self._emit_many(commands)

    def _emit_repeat(self, low, returned, plan, *, form_target=None):
        """Evaluate one record list once, then repeat its materialized value.

        Python list multiplication evaluates the two operands from left to
        right and does not reevaluate an element for every copy.  A temporary
        structure-of-arrays carrier preserves that distinction while guarded
        copies implement a possibly data-dependent nonnegative count.
        """
        node = plan.node
        if (not isinstance(node, ast.BinOp)
                or not isinstance(node.op, ast.Mult)
                or len(plan.children) != 1):
            raise LoweringError(
                node, "a transaction-list repetition has no source operands",
                code="source-plan-inconsistent")
        child = plan.children[0]
        if child.node is node.left:
            count_source = node.right
            sequence_first = True
        elif child.node is node.right:
            count_source = node.left
            sequence_first = False
        else:
            raise LoweringError(
                node, "a transaction-list repetition lost its list operand",
                code="source-plan-inconsistent")

        record = child.contract.transaction
        child_value = None

        def lower_count():
            pre, value = low._scoped(
                lambda: low._integer_context_expr(count_source))
            holder = low.storage.fresh()
            low._remember_source_scalar_type(holder, value_types.INTEGER)
            return ir.Seq(pre, ir.Assign(holder, value)), holder

        def lower_child():
            nonlocal child_value
            if child.contract.maximum == 0:
                # There are no record fields to allocate, but evaluating an
                # empty-list helper can still have source-visible effects.
                pre, _discarded = low._scoped(lambda: low.expr(child.node))
                return pre
            if record is None or not record.is_record:
                raise LoweringError(
                    child.node, "a repeated transaction list has no record "
                                "schema",
                    code="source-plan-inconsistent")
            child_builder = _BatchBuilder(
                low.storage.fresh(),
                {field: low.storage.fresh() for field in self.names},
                child.contract.maximum)
            prepare = child_builder.initialize(low, record, child.node)
            child_sink = _NamedReturn(
                None, child_builder.count, tuple(child_builder.buffers),
                child_builder.capacity, child_builder.buffers)
            child_pre, child_body = low._scoped(
                lambda: child_sink._emit_plan(low, returned, child))
            child_command = ir.Seq(
                prepare, ir.Seq(child_pre, child_body))
            child_value = _BatchValue(
                child_builder,
                ir.Lit(_batch_form_code(BatchForm.LIST)),
                batch_value_contract(child.contract))
            return child_command

        # Lowering in source order matters as well as emitting in that order:
        # the first operand may update state facts used to lower the second.
        if sequence_first:
            child_command = lower_child()
            count_command, count = lower_count()
        else:
            count_command, count = lower_count()
            child_command = lower_child()

        if child_value is None:
            copies = ir.Skip()
        else:
            commands = []
            # One nonempty child contributes at least one transaction, so the
            # result capacity is also a sound upper bound on useful copies.
            for index in range(plan.contract.maximum):
                commands.append(ir.If(
                    ir.Op("lt", ir.Lit(index), ir.Var(count)),
                    self._copy_builder(low, child, child_value),
                    ir.Skip()))
            copies = self._emit_many(commands)

        evaluate = (ir.Seq(child_command, count_command)
                    if sequence_first else
                    ir.Seq(count_command, child_command))
        if form_target is not None:
            copies = ir.Seq(
                copies,
                ir.Assign(
                    form_target, ir.Lit(_batch_form_code(BatchForm.LIST))))
        return ir.Seq(evaluate, copies)

    def _emit_choice(self, low, returned, plan, *, form_target=None):
        """Evaluate one conditional batch without lowering an unreachable arm."""
        before, guard = low._atom_of(plan.node.test)
        entering = low._flow_facts()
        then_entry = low._assumed_source_flow_facts(
            entering, plan.node.test, guard, True)
        else_entry = low._assumed_source_flow_facts(
            entering, plan.node.test, guard, False)
        selected = plan.choice_truth
        if selected is None:
            if then_entry is None and else_entry is not None:
                selected = False
            elif else_entry is None and then_entry is not None:
                selected = True

        if type(selected) is bool:
            if type(plan.choice_truth) is bool:
                chosen = plan.children[0]
            else:
                chosen = plan.children[0 if selected else 1]
            omitted = plan.node.orelse if selected else plan.node.body
            chosen_entry = then_entry if selected else else_entry
            low._set_range_facts(
                entering if chosen_entry is None else chosen_entry)
            chosen_pre, chosen_cmd = low._scoped(
                lambda: self._emit_plan(
                    low, returned, chosen, form_target=form_target))
            account = low._marked_if(
                plan.node.test, guard, ir.Skip(), ir.Skip())
            command = ir.Seq(
                account,
                ir.Seq(
                    low._false_goal_terms([omitted]),
                    ir.Seq(chosen_pre, chosen_cmd)))
            return command if before is None else ir.Seq(before, command)

        if then_entry is None or else_entry is None:
            raise LoweringError(
                plan.node,
                "a reachable batch choice has no reachable source arm",
                code="source-plan-inconsistent")
        low._set_range_facts(then_entry)
        then_pre, then = low._scoped(
            lambda: self._emit_plan(
                low, returned, plan.children[0], form_target=form_target))
        then_facts = low._flow_facts()
        low._set_range_facts(else_entry)
        else_pre, orelse = low._scoped(
            lambda: self._emit_plan(
                low, returned, plan.children[1], form_target=form_target))
        else_facts = low._flow_facts()
        low._set_range_facts(low._joined_branch_flow_facts(
            guard,
            ((guard, then_facts), (ir.Op("not", guard), else_facts))))
        choose = low._marked_if(
            plan.node.test, guard, ir.Seq(then_pre, then),
            ir.Seq(else_pre, orelse))
        return choose if before is None else ir.Seq(before, choose)

    def _conditional(self, low, node, value):
        """A conditional return evaluates and writes only its selected arm."""
        before, guard = low._atom_of(value.test)
        then_pre, then = low._scoped(
            lambda: self._assign_value(low, node, value.body))
        else_pre, orelse = low._scoped(
            lambda: self._assign_value(low, node, value.orelse))
        choose = low._marked_if(
            value.test, guard, ir.Seq(then_pre, then),
            ir.Seq(else_pre, orelse))
        return choose if before is None else ir.Seq(before, choose)

    def _assign_value(self, low, node, value):
        proxy = ast.Return(value=value)
        ast.copy_location(proxy, node)
        return self.assign(low, proxy)

    def _groups(self, low, node):
        """The transactions one return produces, each as name and value."""
        value = node.value
        if value is None or (isinstance(value, ast.Constant)
                             and value.value is None):
            return []
        if isinstance(value, ast.List):
            # A list of mappings is several transactions on one beat.
            return [self._pairs(low, node, elt) for elt in value.elts]
        return [self._pairs(low, node, value)]

    def _pairs(self, low, node, value):
        items = record_items(value, is_builtin=low._spelling_is_builtin)
        if items is not None:
            out = []
            for key, val in items:
                field = record_key(key)
                if field not in self.names:
                    raise LoweringError(node, "return of a different set of outputs", code="outputs-disagree-across-returns")
                out.append((field, val))
            return out
        if isinstance(value, ast.Name):
            record = low.storage.carrier(_RecordBuilder, low._name(value.id))
            if record is not None:
                fields = record.value.by_name
                return [(name, fields[name]) for name in self.names]
        elts = value.elts if isinstance(value, ast.Tuple) else [value]
        if len(elts) != len(self.names):
            raise LoweringError(
                node, f"return of {len(elts)} values for {len(self.names)} outputs", code="return-arity-mismatch")
        return list(zip(self.names, elts))

@dataclass(frozen=True)
class _PositionalValue:
    """A fixed ordered product eliminated at an unpacking boundary."""

    items: tuple[object, ...]
    contracts: tuple[ValueContract, ...]
    scalar_types: tuple[object | None, ...]
    contract: ValueContract

    def __post_init__(self):
        size = len(self.items)
        if len(self.contracts) != size or len(self.scalar_types) != size:
            raise ValueError("a positional value has inconsistent arity")
        if (not self.contract.is_sequence
                or self.contract.exact_length != size
                or self.contract.carrier not in {"list", "tuple"}):
            raise ValueError(
                "a positional value needs one fixed sequence contract")

class _PositionalBuilder:
    """Parallel SolverIR locals implementing one fixed positional product."""

    __slots__ = ("contract", "contracts", "names", "scalar_types", "shapes")

    def __init__(self, names, shapes, contracts, scalar_types, contract):
        self.names = tuple(names)
        self.shapes = tuple(tuple(shape) for shape in shapes)
        self.contracts = tuple(contracts)
        self.scalar_types = tuple(scalar_types)
        self.contract = contract
        # Validate the complete layout at its single construction point.
        _PositionalValue(
            tuple(ir.Var(name) for name in self.names), self.contracts,
            self.scalar_types, self.contract)

    @property
    def value(self):
        return _PositionalValue(
            tuple(ir.Var(name) for name in self.names), self.contracts,
            self.scalar_types, self.contract)

class _ValueReturn:
    """Own an inlined return's writes, entry value and caller-visible result.

    The lowerer owns call scopes and command placement. This builder owns the
    result's representation and validates it before exposing it to the caller.
    """

    def __init__(self, done, result, length=None, *, required=True,
                 planned=UNKNOWN, positional_arity=None, result_kind=None):
        if result_kind not in {None, "boolean", "integer"}:
            raise ValueError(f"unknown helper result kind {result_kind!r}")
        self.done = done
        self.result = result
        self.length = length
        self.shape = None
        self.carrier = None
        self.contract = None
        self.seen = False
        self.no_value = False
        self.required = bool(required)
        self.planned = planned
        self.positional_arity = positional_arity
        # Only the caller's numeric/truth-use boundary may discard the source
        # bool/int distinction. Arguments and other helper-local values keep it.
        self.result_kind = result_kind
        self.result_type = None
        self.result_type_seen = False
        self.static_reference = False

    def initialize(self, low, node):
        """Build the dominating entry value after return arms fix its type.

        Both lowerers use this command: the term prepends it to the body and
        the graph inserts it at the helper entry block. Every normally
        returning path overwrites the placeholder. This method does not emit
        the command or inspect the ambient return destination.
        """
        result = self.result
        initialize = ir.Assign(self.done, ir.Lit(0))
        positional = low.storage.carrier(_PositionalBuilder, result)
        if positional is not None:
            for name, shape, kind in zip(
                    positional.names, positional.shapes,
                    positional.scalar_types):
                placeholder = (zero_shape(shape) if shape else
                               zero_type(kind))
                initialize = ir.Seq(
                    initialize, ir.Assign(name, placeholder))
            return initialize
        batch = low.storage.carrier(_BatchBuilder, result)
        batch_payload = record_batch_payload(self.planned)
        builder = low.storage.carrier(_RecordBuilder, result)
        if batch is not None and batch_payload is not None:
            return ir.Seq(
                initialize,
                batch.initialize(low, batch_payload.element, node))
        if builder is not None and low.flow.contracts.get(
                result, UNKNOWN).is_record:
            return ir.Seq(
                initialize, builder.initialize(low, node))
        if self.planned.is_option:
            builder = low.storage.require_carrier(_OptionBuilder, result)
            return ir.Seq(
                initialize, builder.initialize(low, node))
        placeholder = (
            zero_type(self.result_type)
            if self.result_type_seen and self.result_type is not None
            else zero_shape(getattr(self, "shape", ())))
        return ir.Seq(initialize, ir.Assign(result, placeholder))

    def publish(self, low, node, *, helper_name):
        """Validate the completed return and expose its representation.

        Called after the caller's scope has been restored and body placed.
        Result metadata belongs to this builder, not the now-ambient return.
        """
        result, result_length = self.result, self.length
        planned_return = self.planned
        batch_payload = record_batch_payload(planned_return)
        if self.no_value and self.required:
            raise LoweringError(
                node, f"{helper_name} returns None on a reachable path, but its "
                      f"caller uses the result as a value",
                code="helper-return-value-unsupported")
        if batch_payload is not None:
            if (self.contract is None
                    or not contract_contains(
                        planned_return, self.contract)):
                raise LoweringError(
                    node, "the helper's lowered batch return disagrees with "
                          "its planned source contract",
                    code="source-value-contract-disagrees")
            low.flow.contracts[result] = planned_return
            builder = low.storage.require_carrier(_BatchBuilder, result)
            return _BatchValue(
                builder, ir.Var(builder.form), planned_return)
        if self.positional_arity is not None:
            builder = low.storage.carrier(_PositionalBuilder, result)
            if builder is None or not self.seen:
                raise LoweringError(
                    node, "a directly unpacked helper has no positional "
                          "return on every reachable path",
                    code="helper-return-shape-disagrees")
            return builder.value
        if self.static_reference:
            returned = low.flow.static_values.get(result)
            if not isinstance(returned, _StaticRef):
                raise LoweringError(
                    node, "a helper's finite static return lost its encoded "
                          "reference",
                    code="static-get-result-unsupported")
            return returned
        # Commands carry the values, but shape and run-time sequence length
        # are metadata. Publish that contract explicitly so the caller does
        # not see a scalar where the helper returned a recursive value.
        if self.seen and not planned_return.is_record \
                and not planned_return.is_option:
            low.storage.require_shape(
                result, self.shape, node)
        if self.shape and not planned_return.is_record \
                and not planned_return.is_option:
            low.flow.lengths[result] = self.shape
            if self.carrier is None:
                low.flow.carriers.pop(result, None)
            else:
                low.flow.carriers[result] = self.carrier
        elif not planned_return.is_record and not planned_return.is_option:
            low.flow.lengths.pop(result, None)
            low.flow.carriers.pop(result, None)
        if (planned_return.is_known and self.contract is not None):
            if not contract_contains(self.contract, planned_return):
                raise LoweringError(
                    node, "the helper's lowered return representation does "
                          "not contain its reachable source return contract: "
                          f"lowered {self.contract!r}, planned "
                          f"{planned_return!r}",
                    code="source-value-contract-disagrees")
            self.contract = planned_return
        if self.contract is None:
            low.flow.contracts.pop(result, None)
        else:
            low.flow.contracts[result] = self.contract
        if planned_return.is_record:
            if (self.contract is None
                    or not self.contract.is_record
                    or not contract_contains(
                        self.contract, planned_return)):
                raise LoweringError(
                    node, "the helper's lowered record return disagrees with "
                          "its planned schema",
                    code="source-value-contract-disagrees")
            low.flow.contracts[result] = planned_return
            return low._product_for_name(result, node)
        if planned_return.is_option:
            option = low._option_for_name(result, node)
            if not isinstance(option, OptionValue):
                raise LoweringError(
                    node, "a helper's optional return has no tag-and-payload "
                          "representation",
                    code="optional-payload-representation-unsupported")
            return option
        if (self.contract is None
                or not self.contract.is_sequence
                or self.contract.exact_length is not None):
            low.storage.lengths.pop(result, None)
            low.storage.capacities.pop(result, None)
        else:
            if result_length is None or not self.shape:
                raise LoweringError(
                    node, "a helper's reachable return plan did not carry its "
                          "run-time sequence length",
                    code="dynamic-helper-contract-unplanned")
            low._register_dynamic_sequence(
                result, result_length, self.shape[0])
        low._remember_source_scalar_type(
            result,
            (None if self.shape else self.result_type))
        return ir.Var(result)

    def _assign_positional(self, low, node):
        """Write a directly unpacked helper result as an ordered product.

        SolverIR arrays are homogeneous.  Python tuples are not: a helper may
        legitimately return ``(Boolean, Integer)`` and its caller may unpack
        those two values into separately typed names.  When the call is the
        complete right-hand side of a destructuring assignment, the tuple
        carrier is unobservable between producer and consumer.  Carry its
        fields in parallel until that boundary instead of forcing them into
        one array type.
        """
        value = node.value
        if not isinstance(value, (ast.Tuple, ast.List)) \
                or len(value.elts) != self.positional_arity:
            raise LoweringError(
                node, "a directly unpacked helper return changes its "
                      "positional product shape",
                code="helper-return-shape-disagrees")
        values = tuple(low._in_order(*value.elts))
        if any(isinstance(item, (
                _BatchValue, OptionValue, RecordValue, _PositionalValue))
               for item in values):
            raise LoweringError(
                value, "a nested product in a positional helper return",
                code="helper-return-shape-disagrees")
        contracts = tuple(
            low._contract_for_value(source, item)
            for source, item in zip(value.elts, values))
        shapes = tuple(low._shape(item) for item in values)
        scalar_types = tuple(
            None if shape else low._source_scalar_type_for_value(source, item)
            for source, item, shape in zip(value.elts, values, shapes))
        if any(not shape and kind is None
               for shape, kind in zip(shapes, scalar_types)):
            raise LoweringError(
                value, "a positional helper return has an unproved scalar "
                       "component type",
                code="helper-return-component-type-unproved")
        if any(contract.has_dynamic_descendant
               or (contract.is_sequence
                   and contract.exact_length is None)
               for contract in contracts):
            raise LoweringError(
                value, "a positional helper return has a run-time-sized "
                       "component",
                code="dynamic-helper-contract-unplanned")

        builder = low.storage.carrier(_PositionalBuilder, self.result)
        if builder is None:
            builder = _PositionalBuilder(
                tuple(low.storage.fresh() for _ in values), shapes, contracts,
                scalar_types, self.planned)
            low.storage.bind_carrier(self.result, builder)
            for name, shape, contract, kind in zip(
                    builder.names, shapes, contracts, scalar_types):
                low.storage.require_shape(name, shape, value)
                low.flow.contracts[name] = contract
                low._remember_source_scalar_type(name, kind)
                if shape:
                    low.flow.lengths[name] = shape
                    if contract.is_sequence and contract.carrier is not None:
                        low.flow.carriers[name] = contract.carrier
        else:
            if builder.shapes != shapes or builder.scalar_types != scalar_types:
                raise LoweringError(
                    value, "helper return paths give a positional component "
                           "different representation types",
                    code="helper-return-shape-disagrees")
            merged = []
            for previous, current in zip(builder.contracts, contracts):
                contract = join_contracts(previous, current)
                if contract is INCOMPATIBLE:
                    raise LoweringError(
                        value, "helper return paths give a positional "
                               "component incompatible source contracts",
                        code="helper-return-shape-disagrees")
                merged.append(contract)
            builder.contracts = tuple(merged)

        command = None
        for name, item in zip(builder.names, values):
            low._record_range(name, item, join=True)
            one = ir.Assign(name, item)
            command = one if command is None else ir.Seq(command, one)
        self.seen = True
        self.contract = self.planned
        return ir.Skip() if command is None else command

    def _merge_metadata(self, low, node, value):
        """Record one return arm's finite representation contract."""
        if self.static_reference:
            raise LoweringError(
                node, "helper return paths mix a static reference with an "
                      "ordinary represented value",
                code="helper-return-shape-disagrees")
        if isinstance(value, RecordValue):
            contract = value.contract
            if not self.seen:
                self.shape = ()
                self.carrier = None
                self.contract = contract
                self.seen = True
                return
            merged = join_contracts(self.contract, contract)
            if merged is INCOMPATIBLE:
                raise LoweringError(
                    node, "helper return paths have different record schemas",
                    code="helper-return-shape-disagrees")
            self.contract = merged
            return
        # The source occurrence is the first authority for Python's scalar
        # type.  Lowering a Boolean expression commonly produces a temporary
        # ``Var``; inspecting only that final IR node loses the type before the
        # procedure-wide checker has run and makes the helper's dominating
        # placeholder an integer zero.  Fall back to the lowered expression
        # for compiler-generated forms, resolving any already-proved temporary
        # through the same partial scalar map.
        kind = (value_types.INTEGER if self.result_kind == "integer" else
                value_types.BOOLEAN if self.result_kind == "boolean" else
                low._source_scalar_type_for_value(node.value, value))
        if not self.result_type_seen:
            self.result_type = kind
            self.result_type_seen = True
        elif self.result_type != kind:
            self.result_type = None
        shape = low._shape(value)
        contract = low._contract_for_value(node.value, value)
        carrier = contract.carrier if contract.is_sequence else None
        if not self.seen:
            self.shape = shape
            self.carrier = carrier
            self.contract = contract
            self.seen = True
            return
        if shape != self.shape:
            raise LoweringError(
                node, "helper return paths have different value shapes",
                code="helper-return-shape-disagrees")
        if carrier != self.carrier:
            self.carrier = None
        merged = join_contracts(self.contract, contract)
        if merged is INCOMPATIBLE:
            raise LoweringError(
                node, "helper return paths have incompatible source value "
                      "contracts", code="helper-return-shape-disagrees")
        self.contract = merged

    def assign(self, low, node):
        batch_payload = record_batch_payload(self.planned)
        if batch_payload is not None:
            builder = low.storage.carrier(_BatchBuilder, self.result)
            scope = (low.activation.scope)
            context = (low.activation.summary)
            plan = (None if low.sequence_plan is None or scope is None else
                    low.sequence_plan.return_plan(node, scope, context))
            if not isinstance(plan, BatchPlan) or builder is None:
                raise LoweringError(
                    node, "a helper transaction batch has no matching source "
                          "plan and lowered storage",
                    code="source-plan-inconsistent")
            if plan.contract.maximum > builder.capacity:
                raise LoweringError(
                    node, "a helper return plan exceeds its allocated batch "
                          "capacity",
                    code="source-plan-inconsistent")
            transaction = plan.contract.transaction
            if (transaction is not None
                    and not contract_contains(
                        batch_payload.element, transaction)):
                raise LoweringError(
                    node, "a helper return plan disagrees with its batch "
                          "element contract",
                    code="source-value-contract-disagrees")
            self.seen = True
            self.shape = None
            self.carrier = "list"
            self.contract = self.planned
            sink = _NamedReturn(
                self.done, builder.count, tuple(builder.buffers),
                builder.capacity, builder.buffers)
            return sink._emit_plan(
                low, node, plan, form_target=builder.form)
        if node.value is None or (isinstance(node.value, ast.Constant)
                                  and node.value.value is None):
            if self.planned.is_option:
                self.seen = True
                self.contract = self.planned
                self.shape = self.planned.payload.fixed_shape
                return low._bind_option(
                    self.result,
                    OptionValue(ir.Lit(False), None, NONE), node,
                    self.planned, source=node.value or node)
            self.no_value = True
            return ir.Skip()            # a helper that returns nothing
        if (lower_static_refs.is_reference(low, node.value)
                and lower_static_refs.requires_reference(low, node.value)):
            if self.seen and not self.static_reference:
                raise LoweringError(
                    node, "helper return paths mix a static reference with "
                          "an ordinary represented value",
                    code="helper-return-shape-disagrees")
            value = lower_static_refs.lower_reference(low, node.value, preserve_reference=True)
            if not isinstance(value, _StaticRef):
                raise LoweringError(
                    node.value, "a helper's finite static return has no tag "
                                "representation",
                    code="static-get-result-unsupported")
            command, bound = value.bind(low, self.result, node.value)
            low.flow.static_values[self.result] = bound
            low.flow.contracts.pop(self.result, None)
            self.seen = True
            self.static_reference = True
            self.shape = ()
            self.contract = UNKNOWN
            return command
        if self.positional_arity is not None:
            if isinstance(node.value, ast.IfExp):
                return self._conditional(low, node, node.value)
            return self._assign_positional(low, node)
        if self.planned.is_option:
            value = low.expr(node.value)
            self.seen = True
            self.contract = self.planned
            self.shape = self.planned.payload.fixed_shape
            return low._bind_option(
                self.result, value, node, self.planned,
                source=node.value)
        if (isinstance(node.value, ast.ListComp)
                and self.planned.is_sequence
                and self.planned.exact_length is None):
            if self.length is None or self.planned.maximum is None:
                raise LoweringError(
                    node.value, "a dynamic comprehension return has no "
                                "finite length carrier",
                    code="dynamic-helper-contract-unplanned")
            low._register_dynamic_sequence(
                self.result, self.length, self.planned.maximum)
            command = low._new_comprehension_sequence(
                self.result, node.value, planned=self.planned)
            self.seen = True
            self.shape = low.flow.lengths[self.result]
            self.carrier = self.planned.carrier
            self.contract = self.planned
            return command
        if low._contract_ast(node.value).is_record:
            value = low.expr(node.value)
            if not isinstance(value, RecordValue):
                raise LoweringError(
                    node.value, "a helper record return has no fieldwise "
                                "representation",
                    code="helper-return-shape-disagrees")
            self._merge_metadata(low, node, value)
            return low._bind_product(self.result, value, node)
        if isinstance(node.value, (ast.Tuple, ast.List)):
            # Several values are an array, so destructuring the call is
            # reading its elements and needs no separate rule.
            value = ir.Op("arraylit", *low._in_order(*node.value.elts))
        else:
            lower_value = {None: low.expr, "integer": low._integer_context_expr,
                           "boolean": low._truth_expr}[self.result_kind]
            value = lower_value(node.value)
        self._merge_metadata(low, node, value)
        low._record_range(self.result, value, join=True)
        command = ir.Assign(self.result, value)
        contract = low._contract_for_value(node.value, value)
        if self.length is not None:
            command = ir.Seq(
                command,
                ir.Assign(self.length, low._logical_length(
                    node.value, value, contract)))
        return command

class _BatchBuilder:
    """Own the physical columns and count of one transaction batch.

    Initialization and field writes use the invocation's existing layout facts.
    Callers retain allocation, source evaluation, index/capacity guards and
    command placement; a zero-capacity sentinel is not a source transaction.
    """

    __slots__ = ("buffers", "capacity", "count", "form")

    def __init__(self, count, buffers, capacity, form=None):
        self.count = count
        self.buffers = dict(buffers)
        self.capacity = int(capacity)
        self.form = form

    def initialize(self, low, record_contract, node):
        """Initialize one finite parallel-record carrier from its contract."""
        fields = ({} if record_contract is None else
                  record_contract.field_contracts)
        if record_contract is not None and not record_contract.is_record:
            raise LoweringError(
                node, "a transaction-batch element has no record contract",
                code="source-plan-inconsistent")
        if set(fields) != set(self.buffers):
            raise LoweringError(
                node, "a transaction-batch contract and its buffers disagree",
                code="source-plan-inconsistent")
        command = ir.Assign(self.count, ir.Lit(0))
        if self.form is not None:
            command = ir.Seq(command, ir.Assign(self.form, ir.Lit(0)))
        storage = max(self.capacity, 1)
        for field, held in self.buffers.items():
            contract = fields[field]
            # An untyped source field has already crossed ``_lower_groups``
            # before it enters this internal staging buffer.  At that point
            # the task binding has projected it to this physical scalar leaf.
            # The old code allocated the scalar shape but retained UNKNOWN as
            # the element contract, so the subsequent stage-to-target copy
            # rejected the very scalar layout it had just allocated.
            storage_contract = SCALAR if contract is UNKNOWN else contract
            shape = storage_contract.physical_shape
            if shape is None:
                raise LoweringError(
                    node, "a transaction field has a dynamic nested "
                          "representation",
                    code="nested-dynamic-sequence-unsupported")
            buffer_contract = ValueContract.sequence(
                "list", storage, storage, storage_contract)
            low.storage.require_shape(
                held, (storage,) + shape, node)
            # Buffer element reads are ordinary recursive sequence reads.
            # Keep the physical shape beside the representation check so a
            # later internal transaction projection can drop the outer batch
            # dimension and retain the field's fixed nested shape.
            low.flow.lengths[held] = (storage,) + shape
            low.flow.contracts[held] = buffer_contract
            low._ensure_length_layout(held, buffer_contract, node)
            zero = zero_shape(shape)
            command = ir.Seq(
                command,
                ir.Seq(
                    ir.Assign(
                        held, ir.Op("arrayfill", zero, ir.Lit(storage))),
                    low._initialize_length_layout(
                        held, buffer_contract, node)))
        return command

    def buffer_contract(self, low, field, node):
        """The exact sequence layout of one structure-of-arrays buffer."""
        held = self.buffers.get(field)
        contract = low.flow.contracts.get(held, UNKNOWN)
        if (held is None or not contract.is_sequence
                or contract.exact_length is None
                or contract.element is None):
            raise LoweringError(
                node, f"transaction field {field!r} has no physical buffer "
                      "contract",
                code="source-plan-inconsistent")
        return contract

    def store_field(self, low, field, index, value, actual, node):
        """Write data and nested lengths into one batch-field slot."""
        held = self.buffers.get(field)
        container = self.buffer_contract(low, field, node)
        if not contract_contains(container.element, actual):
            raise LoweringError(
                node, f"transaction field {field!r} changes its source "
                      "value contract",
                code="record-value-shape-mismatch")
        return low._store_sequence_element(
            held, index, value, actual, container, node)

@dataclass(frozen=True)
class _BatchValue:
    """One internal transaction batch before boundary normalization.

    ``form`` retains the Python carrier (None, mapping, or list), while the
    builder carries the normalized count and parallel record fields.  Keeping
    both is essential: ``None`` and ``[]`` emit the same empty transaction
    sequence but remain distinguishable to source code inside a helper.
    """

    builder: _BatchBuilder
    form: object
    contract: ValueContract

    def __post_init__(self):
        if not isinstance(self.builder, _BatchBuilder):
            raise TypeError("a batch value needs a batch builder")
        if record_batch_payload(self.contract) is None:
            raise ValueError(
                "a batch value needs a zero-or-more-records source contract")

    def record_at(self, low, index, node):
        """Read one represented transaction as its logical record product.

        The caller establishes the mapping/list carrier and valid source index
        (or guards iteration by the live count). This projection creates no
        additional source goal events.
        """
        sequence = record_batch_payload(self.contract)
        contract = None if sequence is None else sequence.element
        if contract is None or not contract.is_record:
            raise LoweringError(
                node, "a transaction batch has no record element contract",
                code="output-batch-read-unsupported")
        builder = self.builder
        if set(builder.buffers) != set(contract.field_contracts):
            raise LoweringError(
                node, "a transaction batch does not carry its complete record "
                      "schema",
                code="output-batch-read-unsupported")
        return record_value(
            ((field, low._select_sequence_element(
                ir.Var(builder.buffers[field]), index,
                builder.buffer_contract(low, field, node), node))
             for field, _child in contract.fields),
            contract.field_contracts)

class _OptionBuilder:
    """Own storage and value conversion for one finite ``None | T``.

    The lowerer supplies evaluated values and places the returned commands.
    Recursive payloads share the record carrier; allocation does not emit code.
    Occurrence facts come from the caller; refinement does not prove presence.
    """

    __slots__ = ("contract", "payload", "present")

    def __init__(self, present, payload, contract):
        if not contract.is_option:
            raise ValueError("an option builder needs an Option contract")
        self.present = present
        self.payload = payload
        self.contract = contract

    @staticmethod
    def refine(value, at_occurrence, node):
        """Read a tag/payload carrier under this occurrence's presence fact."""
        if at_occurrence.is_none:
            return OptionValue(ir.Lit(False), None, NONE)
        if at_occurrence.is_option or at_occurrence is UNKNOWN:
            return value
        if value.contract.payload is not None and contract_contains(
                value.contract.payload, at_occurrence):
            return value.payload
        raise LoweringError(
            node, "an optional local is consumed without a path proving it "
                  "is present",
            code="optional-value-not-unwrapped")

    @staticmethod
    def coerce(low, value, source, contract, *, actual=None):
        """Coerce one reachable source alternative into ``Option[T]``."""
        if not contract.is_option:
            raise TypeError("the target contract is not optional")
        payload_contract = contract.payload
        if isinstance(value, _BatchValue):
            # ``None | Record`` shares the count-plus-fields carrier used at
            # the transaction boundary.  Inside another product it is still
            # an Option value: the source form is its tag and the sole record
            # slot is its recursive payload.
            if payload_contract is None or not payload_contract.is_record \
                    or not contract_contains(contract, value.contract):
                raise LoweringError(
                    source, "a transaction carrier does not represent this "
                            "optional record payload",
                    code="optional-payload-contract-disagrees")
            return OptionValue(
                ir.Op("eq", value.form, ir.Lit(
                    _batch_form_code(BatchForm.MAPPING))),
                value.record_at(low, ir.Lit(0), source), contract)
        if isinstance(value, OptionValue):
            if value.contract.is_none:
                return OptionValue(
                    ir.Lit(False), zero_contract(payload_contract, source), contract)
            if not value.contract.is_option or value.payload is None \
                    or not contract_contains(
                        payload_contract, value.contract.payload):
                raise LoweringError(
                    source, "an optional value changes its payload contract",
                    code="optional-payload-contract-disagrees")
            _OptionBuilder._check_payload(low, payload_contract, value.payload, source)
            return OptionValue(value.present, value.payload, contract)
        actual = (low._contract_for_value(source, value)
                  if actual is None else actual)
        if not contract_contains(payload_contract, actual):
            raise LoweringError(
                source, "a present optional value disagrees with its "
                        "payload contract",
                code="optional-payload-contract-disagrees")
        _OptionBuilder._check_payload(low, payload_contract, value, source)
        return OptionValue(ir.Lit(True), value, contract)

    @staticmethod
    def _check_payload(low, contract, value, node):
        """Check one already lowered value against a recursive source contract."""
        if contract.is_record:
            if not isinstance(value, RecordValue) \
                    or not contract_contains(contract, value.contract):
                raise LoweringError(
                    node, "a record payload has no matching fieldwise "
                          "representation",
                    code="optional-payload-representation-unsupported")
            return
        shape = contract.fixed_shape
        if shape is None or not isinstance(value, ir.EXPRS) \
                or low._shape(value) != shape:
            raise LoweringError(
                node, "a value has no matching fixed local representation",
                code="optional-payload-representation-unsupported")

    @staticmethod
    def _payload_shape(contract, node):
        """The existing finite representation used by an Option payload."""
        if not contract.is_option or contract.payload is None:
            raise LoweringError(
                node, "an optional value has no payload contract",
                code="optional-payload-representation-unsupported")
        payload = contract.payload
        shape = payload.physical_shape
        if (shape is None or payload.is_option or payload.is_record
                or payload.has_dynamic_descendant
                or (payload.is_sequence and payload.exact_length is None)):
            raise LoweringError(
                node, "an optional payload has no single finite local "
                      "representation",
                code="optional-payload-representation-unsupported")
        return tuple(shape)

    @staticmethod
    def allocate(low, contract, node):
        """Allocate the tag and recursive payload carrier of one Option."""
        if not contract.is_option:
            raise TypeError("an option builder needs an Option contract")
        present = low.storage.fresh()
        low.storage.require_shape(present, (), node)
        low.flow.contracts[present] = SCALAR
        if contract.payload.is_record:
            payload = _RecordBuilder.allocate(low, contract.payload, node)
        else:
            shape = _OptionBuilder._payload_shape(contract, node)
            payload = low.storage.fresh()
            low.storage.require_shape(payload, shape, node)
            if shape:
                low.flow.lengths[payload] = shape
            low.flow.contracts[payload] = contract.payload
        return _OptionBuilder(present, payload, contract)

    def initialize(self, low, node):
        payload = (self.payload.initialize(low, node)
                   if isinstance(self.payload, _RecordBuilder) else
                   ir.Assign(self.payload, zero_shape(
                       _OptionBuilder._payload_shape(self.contract, node))))
        return ir.Seq(ir.Assign(self.present, ir.Lit(False)), payload)

    def assign(self, low, value, node, *, actual=None, join=False):
        """Assign one Option without collapsing a product-valued payload."""
        option = _OptionBuilder.coerce(low, value, node, self.contract, actual=actual)
        low._record_range(self.present, option.present, join=join)
        tag = ir.Assign(self.present, option.present)
        if isinstance(self.payload, _RecordBuilder):
            payload = self.payload.assign(low, option.payload, node, join=join)
        else:
            low._record_range(self.payload, option.payload, join=join)
            payload = ir.Assign(self.payload, option.payload)
        return ir.Seq(tag, payload)

    @property
    def value(self):
        payload = (self.payload.value
                   if isinstance(self.payload, _RecordBuilder)
                   else ir.Var(self.payload))
        return OptionValue(ir.Var(self.present), payload, self.contract)

@dataclass(frozen=True)
class OptionSequence:
    """Fixed optional scalars in parallel presence/payload arrays.

    Allocation, projection, copy and cell stores share this carrier. Source
    values are evaluated by the caller; index checks use its existing lowerer.
    An absent cell is distinct from a present zero.
    """

    present: object
    payload: object
    contract: ValueContract

    def at(self, index):
        if self.contract.element.is_none:
            return OptionValue(ir.Lit(False), None, NONE)
        return OptionValue(ir.Sel(self.present, index), ir.Sel(self.payload, index),
                           self.contract.element)

    @staticmethod
    def literal(low, node, values):
        element = NONE
        for source, value in zip(node.elts, values):
            element = join_contracts(element, low._contract_for_value(source, value))
        if not (element.is_none or element.is_option and element.payload == SCALAR):
            raise LoweringError(node, 'optional sequence elements need scalar payloads',
                                code='optional-sequence-payload-unsupported')
        contract = ValueContract.sequence(
            'list' if isinstance(node, ast.List) else 'tuple',
            len(values), len(values), element)
        slots = [_OptionBuilder.coerce(low, value, source, ValueContract.option(SCALAR))
                 for source, value in zip(node.elts, values)]
        return OptionSequence(ir.Op('arraylit', *(slot.present for slot in slots)),
                              ir.Op('arraylit', *(slot.payload for slot in slots)), contract)

    @staticmethod
    def allocate(low, contract, node):
        present, payload = low.storage.fresh(), low.storage.fresh()
        shape = (contract.exact_length,)
        for variable in (present, payload):
            low.storage.require_shape(variable, shape, node)
            low.flow.lengths[variable] = shape
            low.flow.contracts[variable] = ValueContract.fixed(shape)
        return OptionSequence(ir.Var(present), ir.Var(payload), contract)

    def assign(self, low, value, node):
        if not isinstance(value, OptionSequence) or not contract_contains(
                self.contract, value.contract):
            raise LoweringError(node, 'optional sequence assignment changes its layout',
                                code='optional-sequence-contract-disagrees')
        value = low._capture(value)
        commands = []
        for target, expression in ((self.present, value.present),
                                   (self.payload, value.payload)):
            low._record_range(target.name, expression)
            commands.append(ir.Assign(target.name, expression))
        return ir.Seq(*commands)

    def store(self, low, target, value, source):
        if low._effectful(target.value) or low._effectful(target.slice):
            raise LoweringError(target, 'an effectful store location needs object identity',
                                code='subscript-target-effectful')
        if isinstance(target.slice, ast.Slice):
            raise LoweringError(target, 'optional sequence slice stores are not represented',
                                code='slice-assignment-unsupported')
        if self.contract.carrier != 'list':
            raise LoweringError(target, 'optional tuple elements are immutable',
                                code='store-target-immutable')
        index = low._capture(low._index(
            target, self.present, low._integer_context_expr(target.slice)))
        if self.contract.element.is_none:
            if not isinstance(value, OptionValue) or not value.contract.is_none:
                raise LoweringError(source, 'None-only sequence receives a payload',
                                    code='optional-sequence-contract-disagrees')
        slot = _OptionBuilder.coerce(low, value, source, ValueContract.option(SCALAR))
        commands = []
        for column, item in ((self.present, slot.present),
                             (self.payload, slot.payload)):
            expression = ir.Op('store', column, index, item)
            low._record_range(column.name, expression)
            commands.append(ir.Assign(column.name, expression))
        return ir.Seq(*commands)


class _RecordBuilder:
    """Parallel recursive leaves implementing one immutable source record.

    Scalar and fixed-sequence leaves use one SolverIR local.  A ``None`` leaf
    needs no storage, while an optional leaf uses the same tag/payload product
    as a top-level optional binding.  Keeping that choice in the builder
    prevents record selection from erasing the option tag and makes nested
    products consume the shared ``ValueContract`` recursively rather than
    inventing a second layout rule. Allocation, initialization and field writes
    live here; source evaluation and command placement remain with the lowerer.
    """

    __slots__ = ("contract", "leaves", "order")

    def __init__(self, contract, leaves, order=None):
        expected = tuple(path for path, _child in
                         record_leaf_contracts(contract))
        got = tuple(sorted(dict(leaves)))
        if got != expected:
            raise ValueError(
                "a record builder does not cover its recursive layout")
        contracts = dict(record_leaf_contracts(contract))
        for path, holder in dict(leaves).items():
            child = contracts[path]
            if child.is_none:
                if holder is not None:
                    raise ValueError("a None record leaf has no IR storage")
            elif child.is_option:
                if not isinstance(holder, _OptionBuilder) \
                        or holder.contract != child:
                    raise ValueError(
                        "an optional record leaf needs its tag/payload builder")
            elif type(holder) is not str:
                raise ValueError(
                    "a plain record leaf needs one SolverIR local")
        self.contract = contract
        self.leaves = dict(leaves)
        self.order = order

    @staticmethod
    def allocate(low, contract, node, *, order=None):
        """Allocate the one physical carrier for a recursive record layout."""
        if not contract.is_record:
            raise TypeError("a record builder needs a record contract")
        holders = {}
        for path, child in record_leaf_contracts(contract):
            if child.is_none:
                holders[path] = None
                continue
            if child.is_option:
                holders[path] = _OptionBuilder.allocate(low, child, node)
                continue
            shape = child.physical_shape
            if shape is None:
                raise LoweringError(
                    node, "a record leaf has no finite physical representation",
                    code="record-field-representation-unsupported")
            holder = low.storage.fresh()
            low.storage.require_shape(holder, shape, node)
            if shape:
                low.flow.lengths[holder] = shape
            low.flow.contracts[holder] = child
            low._ensure_length_layout(holder, child, node)
            holders[path] = holder
        return _RecordBuilder(contract, holders, order)

    def initialize(self, low, node):
        """Give every stored leaf its canonical inactive placeholder."""
        contracts = dict(record_leaf_contracts(self.contract))
        command = None
        for path in sorted(self.leaves):
            child, holder = contracts[path], self.leaves[path]
            if child.is_none:
                continue
            if child.is_option:
                one = holder.initialize(low, node)
            else:
                one = ir.Seq(
                    ir.Assign(holder, zero_shape(child.physical_shape)),
                    low._initialize_length_layout(holder, child, node))
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def assign(self, low, value, node, *, join=False):
        """Assign a logical record through its declared recursive carrier."""
        if not isinstance(self, _RecordBuilder) \
                or not isinstance(value, RecordValue) \
                or not contract_contains(self.contract, value.contract):
            raise LoweringError(
                node, "a record value disagrees with its bound layout",
                code="record-value-shape-mismatch")
        items = value.by_name
        actual = value.contract.field_contracts
        wanted = self.contract.field_contracts
        if set(items) != set(wanted) or set(actual) != set(wanted):
            raise LoweringError(
                node, "a record value changes its recursive field layout",
                code="record-value-shape-mismatch")
        if self.order != value.order:
            # Named field reads remain exact, but no single Python iteration
            # order represents both reaching values.
            self.order = None

        command = None
        for field in sorted(wanted):
            one = self.assign_field(
                low, field, items[field], actual[field], node, join=join)
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def assign_field(self, low, field, value, actual, node, *, join=False):
        """Write one logical field through the shared recursive carrier."""
        wanted = self.contract.field_contracts.get(field)
        if wanted is None or not contract_contains(wanted, actual):
            raise LoweringError(
                node, f"record field {field!r} changes its source contract",
                code="record-value-shape-mismatch")
        if wanted.is_record:
            if not isinstance(value, RecordValue):
                raise LoweringError(
                    node, f"record field {field!r} has no fieldwise value",
                    code="record-field-representation-unsupported")
            prefix = (field,)
            nested = _RecordBuilder(
                wanted,
                {path[len(prefix):]: holder
                 for path, holder in self.leaves.items()
                 if path[:len(prefix)] == prefix})
            return nested.assign(low, value, node, join=join)
        holder = self.leaves.get((field,))
        if wanted.is_none:
            if not actual.is_none:
                raise LoweringError(
                    node, f"None field {field!r} changes its contract",
                    code="record-value-shape-mismatch")
            return ir.Skip()
        if wanted.is_option:
            return holder.assign(low, value, node, actual=actual, join=join)
        shape = wanted.physical_shape
        if wanted.is_sequence:
            value = low._coerce_sequence_storage(
                node, value, actual, wanted)
        if not isinstance(value, ir.EXPRS) \
                or type(holder) is not str or shape is None \
                or low._shape(value) != shape:
            raise LoweringError(
                node, f"record field {field!r} has no matching fixed local "
                      "representation",
                code="record-field-representation-unsupported")
        low._record_range(holder, value, join=join)
        data = ir.Assign(holder, value)
        lengths = low._assign_length_layout(
            holder, value, actual, wanted, node)
        return ir.Seq(data, lengths)

    @property
    def value(self):
        contracts = dict(record_leaf_contracts(self.contract))

        def read(path, holder):
            child = contracts[path]
            if child.is_none:
                return OptionValue(ir.Lit(False), None, NONE)
            if child.is_option:
                return holder.value
            return ir.Var(holder)

        return record_from_leaves(
            self.contract,
            {path: read(path, holder)
             for path, holder in self.leaves.items()},
            order=self.order)

@dataclass(frozen=True)
class _StructuredValue:
    """A declared logical value represented by scalar SolverIR expressions."""

    value_type: ValueType
    leaves: tuple[tuple[tuple[str | int, ...], object], ...]

    def __post_init__(self):
        expected = tuple(
            leaf.path for leaf in layout_of_value_type(self.value_type).leaves())
        got = tuple(path for path, _expr in self.leaves)
        if got != expected:
            raise ValueError(
                "a structured lowered value does not cover its declared leaves")

    @property
    def by_path(self):
        return dict(self.leaves)


def zero_contract(contract, node):
    """Canonical unreachable placeholder for one finite recursive value."""
    if contract.is_none:
        return OptionValue(ir.Lit(False), None, NONE)
    if contract.is_record:
        return RecordValue(tuple(
            (name, zero_contract(child, node))
            for name, child in contract.fields), contract)
    if contract.is_option:
        return OptionValue(
            ir.Lit(False), zero_contract(contract.payload, node), contract)
    shape = contract.fixed_shape
    if shape is None:
        raise LoweringError(
            node, "a recursive value has no finite inactive image",
            code="optional-payload-representation-unsupported")
    return zero_shape(shape)


def zero_shape(shape):
    """The recursive zero value of one proved representation shape."""
    shape = tuple(shape or ())
    if not shape:
        return ir.Lit(0)
    child = zero_shape(shape[1:])
    return ir.Op("arrayfill", child, ir.Lit(shape[0]))


def zero_type(kind):
    """Canonical value of one already declared SolverIR semantic type."""
    from ..symbolic import typecheck as types

    if isinstance(kind, types.Array):
        child = zero_type(kind.elem)
        return ir.Op("arrayfill", child, ir.Lit(kind.cells))
    if kind == types.BOOLEAN:
        return ir.Lit(False)
    if kind == types.INTEGER:
        return ir.Lit(0)
    raise TypeError(f"{kind!r} is not a SolverIR value type")


@dataclass(frozen=True, slots=True)
class OutputEntry:
    """Allocated output buffers and their initialization; mappings are not copied."""

    count: str
    output_vars: dict[str, str]
    attributes: set[str]
    entry: ir.Seq


def initialize_outputs(low, prepared, planned, *, signature, fixed) -> OutputEntry:
    """Allocate return controls and typed buffers before ROM/input initialization."""
    model = prepared.model
    fn = model.step
    resolver = prepared.resolver
    signature_output_bindings = prepared.signature_output_bindings
    sequence_plan = planned.sequence_plan
    outputs = planned.outputs
    done, count = low.storage.fresh(), low.storage.fresh()
    capacity = sequence_plan.output_capacity
    low.output_capacity = capacity
    attributes = {n.attr for n in resolver.execution_nodes(fn)
                  if isinstance(n, ast.Attribute)
                  and isinstance(n.value, ast.Name) and n.value.id == "self"
                  and n.attr not in fixed and n.attr not in low.static_fields}
    # An output is held here as the array of transactions the step produced,
    # which is a variable of this lowering and not of the source. A field of
    # the step may share its name and mean something else, so where they
    # collide the buffer takes a name the source cannot write.
    # Output buffers are representation locals, never source variables.  Give
    # every one its own name up front; a reachable helper may access state with
    # the same spelling even when the step body itself does not mention it.
    # ``output_vars`` maps the public physical leaves named by ValueBinding to
    # representation-owned batch buffers.  Scalar compatibility is exact: its
    # sole public leaf has the old logical output name.  A record instead owns
    # one or more declared leaf variables; no field spelling is concatenated
    # or recovered from a runtime value.
    physical_outputs = {}
    if signature_output_bindings:
        for logical, binding in signature_output_bindings.items():
            for variable, kind in binding.solver_types():
                previous = physical_outputs.get(variable)
                if previous is not None and previous[0] != kind:
                    raise LoweringError(
                        fn, f"physical output {variable!r} has conflicting "
                            "ValueBinding types",
                        code="value-binding-type-mismatch")
                physical_outputs.setdefault(variable, (kind, logical))
    else:
        physical_outputs = {port: (None, port) for port in outputs}
    output_vars = {
        variable: low.storage.fresh() for variable in physical_outputs
    }
    low.activation.destination = _NamedReturn(
        done, count, outputs, capacity, output_vars,
        bindings=signature_output_bindings,
        boundary=signature is not None)
    if signature is not None:
        expected_returns = tuple(
            node for node in _walk_function_scope(fn)
            if isinstance(node, ast.Return)
            and sequence_plan.return_reached(node))
        low.output_boundary = OutputBoundaryBuilder(
            signature, capacity, expected_returns)
    # Both start clear, so a path that neither returns nor produces an output
    # still leaves them defined and the step stays total. Each output is an
    # array of the transactions this step produced, so a step that produces
    # several writes several slots and one that produces none writes no slot.
    entry = ir.Seq(ir.Assign(done, ir.Lit(0)), ir.Assign(count, ir.Lit(0)))
    output_contracts = ({} if sequence_plan.output_contract is None else
                        sequence_plan.output_contract.field_contracts)
    storage = max(capacity, 1)
    for port, (kind, logical) in physical_outputs.items():
        contract = output_contracts.get(logical, SCALAR)
        if kind is not None:
            shape = value_types.shape_of(kind)
        else:
            shape = () if contract is UNKNOWN else contract.fixed_shape
        if shape is None:
            raise LoweringError(
                fn, f"the output field {logical} has a dynamic nested "
                    "representation",
                code="nested-dynamic-sequence-unsupported")
        low.storage.require_shape(
            output_vars[port], (storage,) + shape, fn)
        zero = (zero_type(kind) if kind is not None else zero_shape(shape))
        entry = ir.Seq(entry, ir.Assign(
            output_vars[port],
            ir.Op("arrayfill", zero, ir.Lit(storage))))
    return OutputEntry(
        count=count,
        output_vars=output_vars,
        attributes=attributes,
        entry=entry)
