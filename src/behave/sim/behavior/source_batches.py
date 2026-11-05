"""Compositional contracts for a step's output transaction batch.

The Python boundary accepts three source values with one observable meaning:
``None`` emits no transaction, a mapping emits one transaction, and a list of
mappings emits the list in order.  This module describes that meaning before
any SolverIR storage is allocated.  In particular, it keeps the *batch* layer
separate from :class:`~behave.sim.behavior.source_values.ValueContract`: a
mapping used inside a model is an ordinary record value and becomes a
transaction only when it reaches the step-return boundary.

The algebra is deliberately small.  Alternative control paths join compatible
schemas and widen their count interval.  List concatenation adds the intervals
and preserves left-to-right order.  A bounded comprehension or helper-returned
list reuses the sequence contract already established by the source-value
planner.  No task name, declared harness size, or observed run participates in
the result.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .source_calls import record_items, record_key
from ..symbolic.value_contract import (
    INCOMPATIBLE,
    UNKNOWN,
    ValueContract,
    finite_cardinality_add,
    join_contracts,
)
from .source_values import SourceValueBudget

__all__ = [
    "BatchContract",
    "BatchContractError",
    "BatchForm",
    "BatchPlan",
    "RecordBuilderSummary",
    "RecordFieldWrite",
    "batch_contract_of_value",
    "batch_value_contract",
    "concatenate_batches",
    "join_batch_alternatives",
    "join_batch_plans",
    "join_transaction_schemas",
    "plan_batch_expression",
    "record_batch_payload",
]


class BatchForm(Enum):
    """The source carrier observed at the step-return boundary."""

    NONE = "none"
    MAPPING = "mapping"
    LIST = "list"


class BatchContractError(Exception):
    """A return expression with no faithful finite batch representation."""

    def __init__(self, node, what, code):
        super().__init__(what)
        self.node = node
        self.what = str(what)
        self.code = str(code)


@dataclass(frozen=True)
class RecordFieldWrite:
    """One fixed-key write performed while constructing a record.

    ``bindings`` contains only the finite local values used to choose the key.
    One syntactic write in a finite loop can therefore have several cases.
    Retaining the occurrence lets lowering reuse the summary after it inlines
    the same helper activation.
    """

    occurrence: ast.AST
    field: str
    contract: ValueContract
    bindings: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class RecordBuilderSummary:
    """Fixed schema produced by one lexical helper activation."""

    contract: ValueContract
    writes: tuple[RecordFieldWrite, ...]
    field_ranges: tuple[tuple[str, object], ...] = ()

    def __post_init__(self):
        if not self.contract.is_record:
            raise ValueError("a record-builder summary needs a record contract")
        if any(not isinstance(write, RecordFieldWrite)
               for write in self.writes):
            raise TypeError("record-builder writes have the wrong type")
        if any(not write.contract.is_known for write in self.writes):
            raise ValueError("a record-builder write needs a value contract")
        names = tuple(name for name, _interval in self.field_ranges)
        if len(names) != len(set(names)):
            raise ValueError("record-builder field ranges need unique names")
        if not set(names) <= set(self.contract.field_contracts):
            raise ValueError(
                "a record-builder range names a field outside its contract")

    def key_cases(self, occurrence):
        """Finite ``(field, bindings)`` cases for one source write."""
        found = []
        seen = set()
        for write in self.writes:
            if write.occurrence is not occurrence:
                continue
            case = (write.field, write.bindings)
            if case not in seen:
                seen.add(case)
                found.append(case)
        return tuple(found)


@dataclass(frozen=True)
class BatchContract:
    """Schema and transaction-count interval of one output value.

    ``transaction`` is a fixed-schema record contract when any execution may
    emit a transaction.  It is ``None`` only for an expression that is proved
    always empty.  ``forms`` retains the Python carrier because operations
    before the return boundary can observe it: list concatenation is legal for
    two lists, not for a mapping and a list that merely normalize to the same
    transaction sequence.
    """

    minimum: int
    maximum: int
    transaction: ValueContract | None
    forms: frozenset[BatchForm]

    def __post_init__(self):
        if (type(self.minimum) is not int or type(self.maximum) is not int
                or self.minimum < 0 or self.maximum < self.minimum):
            raise ValueError("invalid transaction-count interval")
        if type(self.forms) is not frozenset:
            raise TypeError("batch source forms must be an immutable set")
        if not self.forms or any(type(form) is not BatchForm
                                 for form in self.forms):
            raise ValueError("a batch contract needs known source forms")
        if self.forms == frozenset({BatchForm.NONE}) \
                and (self.minimum, self.maximum) != (0, 0):
            raise ValueError("a pure None source form is always empty")
        if self.forms == frozenset({BatchForm.MAPPING}) \
                and (self.minimum, self.maximum) != (1, 1):
            raise ValueError(
                "a pure mapping source form emits exactly one transaction")
        if BatchForm.NONE in self.forms and self.minimum != 0:
            raise ValueError("a None source form makes the minimum count zero")
        if BatchForm.MAPPING in self.forms \
                and not self.minimum <= 1 <= self.maximum:
            raise ValueError("a mapping source form emits exactly one transaction")
        if BatchForm.LIST not in self.forms and self.maximum > 1:
            raise ValueError("only a list source form can emit several transactions")
        if self.maximum == 0:
            if self.transaction is not None:
                raise ValueError("an always-empty batch has no transaction schema")
        elif not isinstance(self.transaction, ValueContract) \
                or not self.transaction.is_record:
            raise ValueError("a nonempty batch needs a record schema")

    @classmethod
    def empty(cls, form):
        form = form if isinstance(form, BatchForm) else BatchForm(form)
        if form is BatchForm.MAPPING:
            raise ValueError("a mapping is one transaction, not an empty batch")
        return cls(0, 0, None, frozenset({form}))

    @classmethod
    def one(cls, transaction):
        if not isinstance(transaction, ValueContract) \
                or not transaction.is_record:
            raise ValueError("one transaction needs a record contract")
        return cls(1, 1, transaction, frozenset({BatchForm.MAPPING}))

    @classmethod
    def sequence(cls, sequence):
        """Lift a proved source list of records to the output boundary."""
        if not isinstance(sequence, ValueContract) \
                or not sequence.is_sequence:
            raise ValueError("a transaction batch needs a sequence contract")
        if sequence.carrier != "list":
            raise ValueError("only a Python list denotes several transactions")
        if sequence.maximum == 0:
            return cls.empty(BatchForm.LIST)
        if sequence.element is None or not sequence.element.is_record:
            raise ValueError("every transaction-batch element must be a record")
        return cls(
            sequence.minimum, sequence.maximum, sequence.element,
            frozenset({BatchForm.LIST}))

    @property
    def field_names(self):
        return (() if self.transaction is None else
                tuple(name for name, _contract in self.transaction.fields))

    def ordered_fields(self, declared=()):
        """Use interface order after proving it names exactly this schema."""
        declared = tuple(declared or ())
        if not declared:
            return self.field_names
        if len(declared) != len(set(declared)):
            raise ValueError("declared outputs contain a duplicate name")
        if self.transaction is not None \
                and set(declared) != set(self.field_names):
            raise ValueError("declared outputs and transaction schema disagree")
        return declared


@dataclass(frozen=True)
class BatchPlan:
    """A representation-independent output expression tree.

    ``kind`` is one of ``none``, ``record``, ``list``, ``sequence``,
    ``summary``, ``choice``, ``concat`` or ``repeat``.  ``summary`` is a helper or local
    whose finite contract was composed in its own lexical scope and whose
    source carrier can vary by control path.  Children retain source evaluation
    order.  The lowering can therefore allocate one fixed family of output
    buffers from ``contract.maximum`` and still emit the exact run-time count
    and order.
    """

    kind: str
    node: ast.AST
    contract: BatchContract
    children: tuple[BatchPlan, ...] = ()
    choice_truth: bool | None = None

    def __post_init__(self):
        if self.kind not in {
                "none", "record", "list", "sequence", "summary", "choice",
                "concat", "repeat"}:
            raise ValueError(f"unknown batch-plan kind {self.kind!r}")
        if not isinstance(self.node, ast.AST):
            raise TypeError("a batch plan must retain its source occurrence")
        if not isinstance(self.contract, BatchContract):
            raise TypeError("a batch plan needs a BatchContract")
        if type(self.children) is not tuple \
                or any(not isinstance(child, BatchPlan)
                       for child in self.children):
            raise TypeError("batch-plan children must be an immutable plan tuple")
        if self.kind != "choice" and self.choice_truth is not None:
            raise ValueError("only a choice plan has a proved source truth")

        leaf = self.kind in {"none", "record", "sequence", "summary"}
        if leaf and self.children:
            raise ValueError(f"a {self.kind} batch plan cannot have children")
        if self.kind == "none":
            if not (isinstance(self.node, ast.Constant)
                    and self.node.value is None):
                raise ValueError("a none plan must retain its None occurrence")
            if self.contract != BatchContract.empty(BatchForm.NONE):
                raise ValueError("a none plan must be an always-empty None batch")
        elif self.kind == "record":
            if self.contract.forms != frozenset({BatchForm.MAPPING}):
                raise ValueError("a record plan must emit one source mapping")
        elif self.kind == "sequence":
            if self.contract.forms != frozenset({BatchForm.LIST}):
                raise ValueError("a sequence plan must retain a source list")
        elif self.kind == "summary":
            if self.contract.forms in {
                    frozenset({BatchForm.MAPPING}),
                    frozenset({BatchForm.LIST}),
            }:
                raise ValueError(
                    "a pure mapping or list summary has a more precise plan kind")
        elif self.kind == "choice":
            self._check_choice()
        elif self.kind == "concat":
            self._check_concat()
        elif self.kind == "repeat":
            self._check_repeat()
        elif self.kind == "list":
            self._check_list()

    def _check_choice(self):
        if not isinstance(self.node, ast.IfExp):
            raise TypeError("a choice plan must retain one source IfExp")
        if type(self.choice_truth) is bool:
            if len(self.children) != 1:
                raise ValueError(
                    "a source-proved choice has exactly one reachable arm")
        elif self.choice_truth is None:
            if len(self.children) != 2:
                raise ValueError(
                    "a dynamic choice retains both source arms")
        else:
            raise TypeError("choice_truth must be Boolean or None")
        if len(self.children) not in (1, 2):
            raise ValueError(
                "a choice plan needs one proved arm or both arms of one IfExp")
        if len(self.children) == 1:
            child = self.children[0]
            selected = (self.node.body if self.choice_truth
                        else self.node.orelse)
            if child.node is not selected:
                raise ValueError(
                    "a proved choice child must be one of the source arms "
                    "and must be the selected arm")
            if self.contract != child.contract:
                raise ValueError(
                    "a proved choice contract must equal its reachable arm")
            return
        if (self.children[0].node is not self.node.body
                or self.children[1].node is not self.node.orelse):
            raise ValueError("choice children must be the source arms in order")
        try:
            expected = join_batch_alternatives(
                self.children[0].contract, self.children[1].contract,
                self.node)
        except BatchContractError as exc:
            raise ValueError("choice children have incompatible contracts") \
                from exc
        if self.contract != expected:
            raise ValueError("a choice contract must be the join of its arms")

    def _check_concat(self):
        if (not isinstance(self.node, ast.BinOp)
                or not isinstance(self.node.op, ast.Add)
                or len(self.children) != 2):
            raise ValueError("a concat plan needs the operands of one list addition")
        if (self.children[0].node is not self.node.left
                or self.children[1].node is not self.node.right):
            raise ValueError("concat children must be the source operands in order")
        try:
            expected = concatenate_batches(
                self.children[0].contract, self.children[1].contract,
                self.node)
        except BatchContractError as exc:
            raise ValueError("concat children are not compatible source lists") \
                from exc
        if self.contract != expected:
            raise ValueError("a concat contract must add its operand contracts")

    def _check_repeat(self):
        if (not isinstance(self.node, ast.BinOp)
                or not isinstance(self.node.op, ast.Mult)
                or len(self.children) != 1):
            raise ValueError(
                "a repeat plan needs the list operand of one multiplication")
        child = self.children[0]
        if child.node not in (self.node.left, self.node.right):
            raise ValueError("a repeat child must be one source operand")
        if child.contract.forms != frozenset({BatchForm.LIST}) \
                or self.contract.forms != frozenset({BatchForm.LIST}):
            raise ValueError("a repeat plan must retain a source list")
        if (self.contract.transaction is not None
                and child.contract.transaction is not None
                and self.contract.transaction != child.contract.transaction):
            raise ValueError("a repeat plan changes its transaction schema")

    def _check_list(self):
        if not isinstance(self.node, ast.List) \
                or len(self.children) != len(self.node.elts):
            raise ValueError("a list plan needs one child per source element")
        transaction = None
        for source, child in zip(self.node.elts, self.children):
            if source is not child.node or child.kind != "record" \
                    or child.contract.forms != frozenset({BatchForm.MAPPING}):
                raise ValueError(
                    "list children must be source-order one-record plans")
            try:
                transaction = join_transaction_schemas(
                    transaction, child.contract.transaction, source)
            except BatchContractError as exc:
                raise ValueError("list children have incompatible schemas") \
                    from exc
        expected = BatchContract(
            len(self.children), len(self.children), transaction,
            frozenset({BatchForm.LIST}))
        if self.contract != expected:
            raise ValueError(
                "a list contract must count and join its source elements")


def join_transaction_schemas(left, right, node=None):
    """Join records at the transaction boundary, keeping their key schema.

    A payload marked ``UNKNOWN`` means that source planning does not own its
    representation.  That happens, for example, when a step creates a state
    field before returning it: entry-state/type analysis decides whether the
    read is valid, not the output-schema planner.  A known payload therefore
    supplies the storage envelope for an unknown one; if both sides are known
    they must still have compatible recursive representations.  Lowering
    checks every reached payload against the chosen envelope.
    """
    if left is None:
        return right
    if right is None:
        return left
    if not (left.is_record and right.is_record) \
            or set(left.field_contracts) != set(right.field_contracts):
        raise BatchContractError(
            node, "output alternatives have different transaction schemas",
            "outputs-disagree-across-returns")
    fields = {}
    for name in left.field_contracts:
        left_field = left.field_contracts[name]
        right_field = right.field_contracts[name]
        if left_field is UNKNOWN:
            got = right_field
        elif right_field is UNKNOWN:
            got = left_field
        else:
            got = join_contracts(left_field, right_field)
        if got is INCOMPATIBLE:
            raise BatchContractError(
                node,
                f"output alternatives give {name!r} incompatible values",
                "outputs-disagree-across-returns")
        fields[name] = got
    return ValueContract.record(fields)


def join_batch_alternatives(left, right, node=None):
    """Join two mutually exclusive output expressions."""
    if not isinstance(left, BatchContract) \
            or not isinstance(right, BatchContract):
        raise TypeError("batch alternatives need BatchContract values")
    transaction = join_transaction_schemas(
        left.transaction, right.transaction, node)
    return BatchContract(
        min(left.minimum, right.minimum),
        max(left.maximum, right.maximum),
        transaction,
        left.forms | right.forms,
    )


def join_batch_plans(left, right, node=None):
    """Join observations of one expression in alternative source states.

    Source planning may revisit the same AST occurrence with a wider state
    contract.  The executable expression has not changed; only its finite
    batch envelope may have widened.  Preserve the expression tree and join
    the contracts at matching leaves.  This is distinct from constructing an
    ``if`` plan: no new source control is invented here.
    """
    if not isinstance(left, BatchPlan) or not isinstance(right, BatchPlan):
        raise TypeError("batch-plan alternatives need BatchPlan values")
    source = left.node if node is None else node
    if left.node is not right.node:
        raise BatchContractError(
            source, "batch plans do not describe the same source occurrence",
            "source-plan-inconsistent")
    if left == right:
        return left

    if left.kind == right.kind == "choice":
        def arms(plan):
            if plan.choice_truth is None:
                return {True: plan.children[0], False: plan.children[1]}
            return {plan.choice_truth: plan.children[0]}

        left_arms, right_arms = arms(left), arms(right)
        merged = {}
        for truth in (True, False):
            a, b = left_arms.get(truth), right_arms.get(truth)
            if a is not None and b is not None:
                merged[truth] = join_batch_plans(a, b, a.node)
            else:
                merged[truth] = a if a is not None else b
        if merged[True] is not None and merged[False] is not None:
            children = (merged[True], merged[False])
            contract = join_batch_alternatives(
                children[0].contract, children[1].contract, source)
            return BatchPlan("choice", left.node, contract, children, None)
        truth = merged[True] is not None
        child = merged[truth]
        return BatchPlan(
            "choice", left.node, child.contract, (child,), truth)

    if left.kind == right.kind == "list":
        if len(left.children) != len(right.children):
            raise BatchContractError(
                source, "one list occurrence has incompatible batch plans",
                "source-plan-inconsistent")
        children = tuple(
            join_batch_plans(a, b, a.node)
            for a, b in zip(left.children, right.children))
        contract = join_batch_alternatives(
            left.contract, right.contract, source)
        return BatchPlan("list", left.node, contract, children)

    if left.kind == right.kind == "concat":
        children = tuple(
            join_batch_plans(a, b, a.node)
            for a, b in zip(left.children, right.children))
        if len(children) != 2:
            raise BatchContractError(
                source, "one concatenation has incompatible batch plans",
                "source-plan-inconsistent")
        contract = concatenate_batches(
            children[0].contract, children[1].contract, source)
        return BatchPlan("concat", left.node, contract, children)

    if left.kind == right.kind == "repeat":
        if len(left.children) != 1 or len(right.children) != 1:
            raise BatchContractError(
                source, "one repetition has incompatible batch plans",
                "source-plan-inconsistent")
        child = join_batch_plans(
            left.children[0], right.children[0], left.children[0].node)
        contract = join_batch_alternatives(
            left.contract, right.contract, source)
        return BatchPlan("repeat", left.node, contract, (child,))

    if left.children or right.children:
        raise BatchContractError(
            source, "one source occurrence has incompatible batch-plan shapes",
            "source-plan-inconsistent")

    contract = join_batch_alternatives(
        left.contract, right.contract, source)
    if left.kind == right.kind:
        kind = left.kind
    elif contract.forms == frozenset({BatchForm.MAPPING}):
        kind = "record"
    elif contract.forms == frozenset({BatchForm.LIST}):
        kind = "sequence"
    else:
        kind = "summary"
    return BatchPlan(kind, left.node, contract)


def concatenate_batches(left, right, node=None, *, budget=None):
    """Contract for Python list concatenation, in left-to-right order."""
    if left.forms != frozenset({BatchForm.LIST}) \
            or right.forms != frozenset({BatchForm.LIST}):
        raise BatchContractError(
            node, "output concatenation requires two proved Python lists",
            "output-batch-concat-not-list")
    transaction = join_transaction_schemas(
        left.transaction, right.transaction, node)
    budget = SourceValueBudget() if budget is None else budget
    minimum = finite_cardinality_add(left.minimum, right.minimum)
    maximum = finite_cardinality_add(left.maximum, right.maximum)
    return BatchContract(
        minimum,
        maximum,
        transaction,
        frozenset({BatchForm.LIST}),
    )


def record_batch_payload(contract):
    """Normalize a source record batch to its list-shaped physical carrier.

    ``None | Record`` is the zero-or-one case; ``None | list[Record]`` adds
    zero to the list's length interval.  Plain records remain products until
    they reach the transaction boundary.  Source planning and lowering use
    this one test to choose the count-plus-fields representation.
    """
    if not isinstance(contract, ValueContract):
        return None
    payload = contract.payload if contract.is_option else contract
    if contract.is_option and payload is not None and payload.is_record:
        return ValueContract.sequence("list", 0, 1, payload)
    if (payload is not None and payload.is_sequence
            and payload.carrier == "list" and payload.element is not None
            and payload.element.is_record):
        # ``None | list[Record]`` uses the same count-and-fields carrier as
        # the list, but the absent arm contributes the additional zero count.
        return (payload.with_interval(0, payload.maximum, payload.element)
                if contract.is_option else payload)
    return None


def batch_value_contract(contract):
    """Physical value contract of a represented transaction batch.

    Source ``None``, mapping and list forms remain distinct in
    :class:`BatchContract` and in the run-time form tag.  Their common IR
    carrier is a bounded list-shaped sequence of records.
    """
    if not isinstance(contract, BatchContract):
        raise TypeError("a batch value needs a BatchContract")
    return ValueContract.sequence(
        "list", contract.minimum, contract.maximum, contract.transaction)


def _lift_value(contract, node):
    """Lift a record or list-of-records contract at the return boundary."""
    if not isinstance(contract, ValueContract) or contract is UNKNOWN \
            or contract is INCOMPATIBLE:
        return None
    if contract.is_none:
        return BatchContract.empty(BatchForm.NONE)
    if contract.is_record:
        return BatchContract.one(contract)
    if contract.is_option:
        payload = contract.payload
        if payload is None:
            return None
        lifted = _lift_value(payload, node)
        if lifted is None:
            return None
        return BatchContract(
            0, lifted.maximum, lifted.transaction,
            lifted.forms | {BatchForm.NONE})
    if contract.is_sequence and contract.carrier == "list":
        try:
            return BatchContract.sequence(contract)
        except ValueError as exc:
            raise BatchContractError(
                node, str(exc), "output-batch-element-unsupported") from None
    return None


def batch_contract_of_value(contract, node=None):
    """Lift one ordinary source value into the batch domain, if possible."""
    return _lift_value(contract, node)


def plan_batch_expression(
        node: ast.AST,
        *,
        value_contract: Callable[[ast.AST], ValueContract],
        batch_contract: Callable[[ast.AST], BatchContract | None] | None = None,
        truth_of: Callable[[ast.AST], bool | None] | None = None,
        is_builtin: Callable[[str], bool] = lambda _name: False,
        budget: SourceValueBudget | None = None,
):
    """Build the finite output-batch plan for one return expression.

    ``value_contract`` is the source-value authority used by the rest of the
    frontend.  ``batch_contract`` optionally supplies a callee or local's
    already-composed output summary; this is how helper calls reuse their
    callee layout rather than being reclassified syntactically.

    ``None`` means the expression is not a batch form (for example a scalar or
    positional tuple) and may be handled by the existing positional return
    path.  Once an expression contains a batch operation, an incompatible arm
    is a typed error rather than a scalar fallback.
    """
    budget = SourceValueBudget() if budget is None else budget

    if node is None or (isinstance(node, ast.Constant)
                        and node.value is None):
        source = node if node is not None else ast.Constant(value=None)
        return BatchPlan(
            "none", source, BatchContract.empty(BatchForm.NONE))

    if isinstance(node, ast.IfExp):
        truth = None if truth_of is None else truth_of(node.test)
        if type(truth) is bool:
            selected = node.body if truth else node.orelse
            child = plan_batch_expression(
                selected, value_contract=value_contract,
                batch_contract=batch_contract, truth_of=truth_of,
                budget=budget, is_builtin=is_builtin)
            if child is None:
                return None
            return BatchPlan(
                "choice", node, child.contract, (child,), truth)
        left = plan_batch_expression(
            node.body, value_contract=value_contract,
            batch_contract=batch_contract, truth_of=truth_of, budget=budget,
            is_builtin=is_builtin)
        right = plan_batch_expression(
            node.orelse, value_contract=value_contract,
            batch_contract=batch_contract, truth_of=truth_of, budget=budget,
            is_builtin=is_builtin)
        if left is None and right is None:
            return None
        if left is None or right is None:
            raise BatchContractError(
                node, "only one conditional arm is an output batch",
                "output-batch-arm-disagrees")
        return BatchPlan(
            "choice", node,
            join_batch_alternatives(left.contract, right.contract, node),
            (left, right), None)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = plan_batch_expression(
            node.left, value_contract=value_contract,
            batch_contract=batch_contract, truth_of=truth_of, budget=budget,
            is_builtin=is_builtin)
        right = plan_batch_expression(
            node.right, value_contract=value_contract,
            batch_contract=batch_contract, truth_of=truth_of, budget=budget,
            is_builtin=is_builtin)
        if left is None and right is None:
            return None
        if left is None or right is None:
            raise BatchContractError(
                node, "only one operand is a transaction list",
                "output-batch-concat-not-list")
        return BatchPlan(
            "concat", node,
            concatenate_batches(
                left.contract, right.contract, node, budget=budget),
            (left, right))

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left = plan_batch_expression(
            node.left, value_contract=value_contract,
            batch_contract=batch_contract, truth_of=truth_of, budget=budget,
            is_builtin=is_builtin)
        right = plan_batch_expression(
            node.right, value_contract=value_contract,
            batch_contract=batch_contract, truth_of=truth_of, budget=budget,
            is_builtin=is_builtin)
        children = tuple(plan for plan in (left, right) if plan is not None)
        if not children:
            return None
        if len(children) != 1:
            raise BatchContractError(
                node, "transaction-list repetition needs one integer operand",
                "output-batch-repeat-count-not-integer")
        lifted = _lift_value(value_contract(node), node)
        child = children[0]
        if (lifted is None
                or lifted.forms != frozenset({BatchForm.LIST})
                or child.contract.forms != frozenset({BatchForm.LIST})):
            raise BatchContractError(
                node, "transaction-list repetition has no finite list result",
                "output-batch-repeat-unbounded")
        return BatchPlan("repeat", node, lifted, (child,))

    if isinstance(node, ast.List):
        children = []
        transaction = None
        for element in node.elts:
            contract = _lift_value(value_contract(element), element)
            if (contract is None or contract.maximum != 1
                    or contract.forms != frozenset({BatchForm.MAPPING})):
                raise BatchContractError(
                    element, "an output-list element is not one mapping",
                    "output-batch-element-unsupported")
            transaction = join_transaction_schemas(
                transaction, contract.transaction, element)
            children.append(BatchPlan("record", element, contract))
        size = finite_cardinality_add(len(children))
        contract = BatchContract(
            size, size, transaction,
            frozenset({BatchForm.LIST}))
        return BatchPlan("list", node, contract, tuple(children))

    items = record_items(node, is_builtin=is_builtin)
    if items is not None:
        # The field names determine the transaction schema even when a field
        # expression cannot normally produce a value (for example, a proved
        # missing-key read whose failure is lowered explicitly).  Retain an
        # UNKNOWN field contract here and let value lowering either establish
        # its representation on a normal path or terminate with that failure;
        # refusing the whole mapping early would hide the source operation
        # that actually decides the step.
        fields = {}
        for key, value in items:
            name = record_key(key)
            if name is None:
                raise BatchContractError(
                    node, "the output mapping has no fixed finite schema",
                    "output-fields-not-static")
            fields[name] = value_contract(value)
        if not fields:
            raise BatchContractError(
                node, "the output mapping has no nonempty schema",
                "output-fields-not-static")
        record = ValueContract.record(fields)
        return BatchPlan("record", node, BatchContract.one(record))

    supplied = None if batch_contract is None else batch_contract(node)
    if supplied is not None:
        if not isinstance(supplied, BatchContract):
            raise TypeError("batch_contract callback returned a non-contract")
        kind = ("record" if supplied.forms == frozenset({BatchForm.MAPPING})
                else "sequence" if supplied.forms == frozenset({BatchForm.LIST})
                else "summary")
        return BatchPlan(kind, node, supplied)

    lifted = _lift_value(value_contract(node), node)
    if lifted is None:
        if isinstance(node, ast.DictComp):
            if any(generator.ifs for generator in node.generators):
                raise BatchContractError(
                    node, "a computed output key set selected by a filter",
                    "output-fields-data-dependent")
            raise BatchContractError(
                node, "the computed output mapping has no fixed finite "
                      "schema",
                "output-fields-not-static")
        if isinstance(node, ast.Dict):
            raise BatchContractError(
                node, "the output mapping has no proved fixed schema",
                "output-fields-not-static")
        if isinstance(node, ast.ListComp):
            raise BatchContractError(
                node, "the output comprehension has no finite record-list "
                      "contract",
                "output-capacity-unproved")
        return None
    kind = ("record" if lifted.forms == frozenset({BatchForm.MAPPING})
            else "sequence" if lifted.forms == frozenset({BatchForm.LIST})
            else "summary")
    return BatchPlan(kind, node, lifted)
