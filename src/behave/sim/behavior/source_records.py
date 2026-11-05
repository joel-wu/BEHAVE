"""Fixed-schema source records eliminated before the SolverIR boundary.

A record is a product, not an array: its fields may have different finite
value contracts and are represented by parallel SolverIR values.  This module
owns the canonical fixed-literal syntax and lowering-time product layout.
Flow analysis may establish the same schema for computed builders; emitters
then supply payloads against that established schema.
"""

from __future__ import annotations

from dataclasses import dataclass

from .source_calls import record_items, record_key
from ..symbolic.value_contract import ValueContract, contract_contains

__all__ = [
    "RecordPlanError",
    "RecordValue",
    "leaf_contracts",
    "literal_entries",
    "record_from_leaves",
    "record_leaves",
    "record_value",
]


class RecordPlanError(Exception):
    """A source mapping is not in the fixed-schema product subset."""

    def __init__(self, node, message, code):
        super().__init__(message)
        self.node = node
        self.message = message
        self.code = code


@dataclass(frozen=True)
class RecordValue:
    """One lowering-time product whose payload type is emitter-defined."""

    fields: tuple[tuple[str, object], ...]
    contract: ValueContract
    # A record's storage layout is canonical, but Python iteration observes the
    # order in which its keys were first inserted.  Keep that source fact beside
    # the product when it is known.  ``None`` means that control-flow joining
    # proved the schema but not one common iteration order.
    order: tuple[str, ...] | None = None

    def __post_init__(self):
        names = tuple(name for name, _value in self.fields)
        expected = tuple(sorted(self.contract.field_contracts))
        if not self.contract.is_record or names != expected:
            raise ValueError("record value and contract have different schemas")
        if self.order is not None and (
                len(self.order) != len(set(self.order))
                or set(self.order) != set(names)):
            raise ValueError("record iteration order and schema disagree")
        for name, value in self.fields:
            child = self.contract.field_contracts[name]
            if child.is_record:
                if not isinstance(value, RecordValue) or not contract_contains(
                        child, value.contract):
                    raise ValueError(
                        "a nested record value disagrees with its contract")
            elif isinstance(value, RecordValue):
                raise ValueError(
                    "a scalar record field contains another record")

    @property
    def by_name(self):
        return dict(self.fields)


def literal_entries(node, *, is_builtin=lambda _name: False):
    """Return a fresh fixed mapping's writes in source evaluation order.

    Repeated literal keys are retained here.  Python evaluates every key and
    value and only then lets the last write determine the mapping value;
    :func:`record_value` performs that canonical last-write collapse.
    """
    items = record_items(node, is_builtin=is_builtin)
    if not items:
        raise RecordPlanError(
            node, "an empty mapping has no record schema",
            "record-value-shape-mismatch")
    entries = []
    for key, value in items:
        if key is None:
            raise RecordPlanError(
                node, "record unpacking has no fixed field schema",
                "record-value-shape-mismatch")
        name = record_key(key)
        if name is None:
            raise RecordPlanError(
                key, "a record field name is not a string literal",
                "record-key-not-static")
        entries.append((name, value))
    return tuple(entries)


def record_value(fields, contracts):
    """Build a canonical product after its fields were evaluated."""
    by_name = dict(fields)
    order = tuple(by_name)
    contract = ValueContract.record(contracts)
    if set(by_name) != set(contract.field_contracts):
        raise ValueError("record payload and contract have different fields")
    return RecordValue(tuple(sorted(by_name.items())), contract, order)


def leaf_contracts(contract, prefix=()):
    """Canonical recursive leaf layout of one fixed-schema record."""
    if not isinstance(contract, ValueContract) or not contract.is_record:
        raise ValueError("a record leaf layout needs a record contract")
    rows = []
    for name, child in contract.fields:
        path = tuple(prefix) + (name,)
        if child.is_record:
            rows.extend(leaf_contracts(child, path))
        else:
            rows.append((path, child))
    return tuple(rows)


def record_leaves(value, prefix=()):
    """Flatten a recursive record value to its canonical logical leaves."""
    if not isinstance(value, RecordValue):
        raise TypeError("record leaves need a RecordValue")
    rows = []
    for name, child in value.fields:
        path = tuple(prefix) + (name,)
        if isinstance(child, RecordValue):
            rows.extend(record_leaves(child, path))
        else:
            rows.append((path, child))
    expected = tuple(path for path, _contract in leaf_contracts(
        value.contract, prefix))
    if tuple(path for path, _item in rows) != expected:
        raise ValueError("a record value does not cover its recursive layout")
    return tuple(rows)


def record_from_leaves(contract, leaves, *, order=None):
    """Reconstruct one recursive record from its canonical logical leaves."""
    leaves = dict(leaves)
    expected = dict(leaf_contracts(contract))
    if set(leaves) != set(expected):
        raise ValueError("record leaves and contract have different layouts")

    def build(current, prefix):
        fields = []
        for name, child in current.fields:
            path = prefix + (name,)
            value = (build(child, path) if child.is_record else leaves[path])
            fields.append((name, value))
        return RecordValue(
            tuple(fields), current,
            order if not prefix else None)

    return build(contract, ())
