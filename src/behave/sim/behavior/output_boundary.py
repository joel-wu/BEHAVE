"""Sealed proof of the finite output boundary produced by lowering.

Source batch planning proves how many transactions a return can emit.  The
lowerer separately sees every scalar leaf just before it is written to the
compiler-owned output buffers. Combining those facts proves the task boundary
without replaying a huge symbolic loop to rediscover its output hull. A proved
count remains available when a leaf still needs an independent exact check;
it does not certify that leaf or the complete output contract.

This is a semantic certificate, not an exact-encoding plan.  A finite but very
large batch may have a certificate here and still exceed the separately
reported exact-representation budget when an analysis is bound.
"""

from __future__ import annotations

from copy import deepcopy

from ..symbolic import typecheck as types
from ..symbolic.procedure_contract import (
    OutputBoundaryError as OutputBoundaryError,
    _OutputBoundaryAuthority as _OutputBoundaryAuthority,
    _restore_output_boundary as _restore_output_boundary,
    _validated_output_boundary as _validated_output_boundary,
    output_boundary_bounds as output_boundary_bounds,
    output_boundary_evidence as output_boundary_evidence,
)

__all__ = [
    "OutputBoundaryBuilder",
    "OutputBoundaryError",
    "bind_output_boundary",
    "output_boundary_bounds",
    "output_boundary_evidence",
]


def _leaf_bounds(leaf):
    if leaf.semantic_type == types.BOOLEAN:
        return (0, 1)
    if leaf.carrier is None:
        raise OutputBoundaryError(
            "signature-outputs-mismatch",
            f"output leaf {leaf.path!r} has no finite task carrier",
        )
    return tuple(leaf.carrier.bounds)


class OutputBoundaryBuilder:
    """Collect the independent local obligations of one lowered boundary."""

    __slots__ = (
        "_active",
        "_capacity",
        "_expected_returns",
        "_ranges",
        "_seen_returns",
        "_signature",
        "_unproved",
    )

    def __init__(self, signature, capacity, expected_returns):
        if signature is None:
            raise TypeError("an output-boundary proof needs a task signature")
        if type(capacity) is not int or capacity < 0:
            raise ValueError("an output-boundary capacity must be natural")
        self._signature = signature
        self._capacity = capacity
        self._expected_returns = frozenset(id(node)
                                           for node in expected_returns)
        self._seen_returns = set()
        self._ranges = {}
        self._unproved = set()
        self._active = False

    def record_return(self, node, plan):
        """Record the source-owned count proof for one reachable return."""
        key = id(node)
        if key not in self._expected_returns:
            return
        self._seen_returns.add(key)
        if plan is None:
            self._unproved.add(("#return", key))
            return
        contract = getattr(plan, "contract", None)
        maximum = getattr(contract, "maximum", None)
        minimum = getattr(contract, "minimum", None)
        if (type(minimum) is not int or type(maximum) is not int
                or minimum < 0 or maximum < minimum):
            raise OutputBoundaryError(
                "source-plan-inconsistent",
                "a reachable return has no finite transaction-count proof",
                node,
            )
        if maximum > self._capacity:
            raise OutputBoundaryError(
                "output-count-outside-capacity",
                f"a return may emit {maximum} transactions, beyond its "
                f"declared capacity {self._capacity}",
                node,
            )
        self._active = self._active or maximum > 0

    def record_values(self, binding, values, range_of, source=None):
        """Prove each logical leaf written through one declared binding."""
        if binding not in self._signature.output_bindings:
            raise OutputBoundaryError(
                "signature-outputs-mismatch",
                f"output {getattr(binding, 'name', '<unknown>')!r} is not "
                "owned by this task signature",
                source,
            )
        values = dict(values)
        leaves = {leaf.path: leaf for leaf in binding.layout.leaves()}
        if set(values) != set(leaves):
            raise OutputBoundaryError(
                "declared-value-shape-mismatch",
                f"output {binding.name!r} does not provide exactly its "
                "declared logical leaves",
                source,
            )
        for path, leaf in leaves.items():
            key = (binding.name, path)
            declared = _leaf_bounds(leaf)
            got = range_of(values[path])
            if got is None:
                self._unproved.add(key)
                continue
            try:
                low, high = int(got.lo), int(got.hi)
            except (AttributeError, TypeError, ValueError):
                self._unproved.add(key)
                continue
            if low < declared[0] or high > declared[1]:
                if high < declared[0] or low > declared[1]:
                    raise OutputBoundaryError(
                        "output-outside-contract",
                        f"output {binding.name!r} leaf {path!r} lies in "
                        f"[{low}, {high}], outside "
                        f"[{declared[0]}, {declared[1]}]",
                        source,
                    )
                # A local abstract hull may include infeasible values.  It
                # neither proves containment nor proves a violation when it
                # overlaps the task carrier, so defer to the exact fallback.
                self._unproved.add(key)
                continue
            previous = self._ranges.get(key)
            self._ranges[key] = (
                (low, high) if previous is None else
                (min(previous[0], low), max(previous[1], high))
            )

    def finish(self, proc):
        """Seal the count independently; missing leaf evidence stays unproved."""
        if self._seen_returns != self._expected_returns:
            return None
        if any(key[0] == "#return" for key in self._unproved):
            return None
        # Output obligations are independent. Retain the proved leaves even
        # when a sibling still needs an exact query. An unknown leaf is absent,
        # never a zero-valued certificate; a later proved write cannot repair
        # an earlier unproved return of the same leaf.
        rows = tuple(
            (binding.name, tuple(
                (leaf.path, self._ranges.get((binding.name, leaf.path)),
                 _leaf_bounds(leaf))
                for leaf in binding.layout.leaves()
                if (binding.name, leaf.path) not in self._unproved
                and (not self._active or
                     (binding.name, leaf.path) in self._ranges)))
            for binding in self._signature.output_bindings
        )
        return _OutputBoundaryAuthority.from_procedure(proc, rows)


def bind_output_boundary(proc, builder):
    """Bind the available frontend proofs to their exact procedure."""
    if type(builder) is not OutputBoundaryBuilder:
        raise TypeError("an output boundary needs its exact frontend builder")
    authority = builder.finish(proc)
    if authority is None:
        return None
    if getattr(proc, "_output_boundary", None) is not None:
        raise AttributeError("a procedure's output-boundary proof is write-once")
    object.__setattr__(proc, "_output_boundary", authority)
    evidence = authority.evidence()
    if evidence is not None:
        proc.output_ranges = evidence
    return deepcopy(evidence)

