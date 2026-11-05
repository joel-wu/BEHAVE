"""Immutable bindings from logical ValueType leaves to SolverIR storage.

A logical path and a SolverIR path answer different questions.  In
``rows[2].flag`` the former is ``(2, "flag")``.  A structure-of-arrays
lowering may store it at ``SolverLeafPath("rows#flag", (2,))``; a completely
flattened lowering may use a scalar variable and no index path at all.  This
module records that choice explicitly and checks it against the declared
layout.  Neither source values nor backend terms are inspected to invent it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.value_type import ValueType, as_value_type
from . import types
from .uniform_array import UniformArray
from .value_layout import layout_of_value_type

__all__ = [
    "SOLVER_LEAF_PATH_VERSION",
    "VALUE_BINDING_VERSION",
    "SolverLeafPath",
    "ValueBinding",
    "ValueBindingError",
]


SOLVER_LEAF_PATH_VERSION = "solver-leaf-path/1"
VALUE_BINDING_VERSION = "value-binding/1"


class ValueBindingError(ValueError):
    """A declared value and its SolverIR leaf paths cannot be bound exactly."""

    __slots__ = ("code",)

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SolverLeafPath:
    """One scalar leaf inside one SolverIR variable."""

    variable: str
    indices: tuple[int, ...] = ()

    def __post_init__(self):
        if type(self.variable) is not str or not self.variable:
            raise ValueBindingError(
                "solver-leaf-variable-invalid",
                "a SolverIR leaf path needs a nonempty variable name")
        if type(self.indices) is not tuple or any(
                type(index) is not int or type(index) is bool or index < 0
                for index in self.indices):
            raise ValueBindingError(
                "solver-leaf-indices-invalid",
                "SolverIR leaf indices are an exact tuple of naturals")

    @property
    def identity(self):
        return (SOLVER_LEAF_PATH_VERSION, self.variable, self.indices)

    def read(self, values):
        try:
            value = values[self.variable]
        except (KeyError, TypeError):
            raise ValueBindingError(
                "solver-leaf-value-missing",
                f"SolverIR value {self.variable!r} is absent") from None
        for index in self.indices:
            if type(value) not in (tuple, list, UniformArray) or not 0 <= index < len(value):
                raise ValueBindingError(
                    "solver-leaf-value-shape-mismatch",
                    f"SolverIR value {self.variable!r} has no index path "
                    f"{self.indices!r}")
            value = value[index]
        return value

    def as_dict(self):
        return {"version": SOLVER_LEAF_PATH_VERSION,
                "variable": self.variable, "indices": list(self.indices)}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {
                "version", "variable", "indices"} \
                or value.get("version") != SOLVER_LEAF_PATH_VERSION \
                or type(value.get("variable")) is not str \
                or type(value.get("indices")) is not list \
                or any(type(index) is not int or type(index) is bool
                       for index in value["indices"]):
            raise ValueBindingError(
                "solver-leaf-image-invalid",
                "a SolverIR leaf path image has exact version/name/indices")
        return cls(value["variable"], tuple(value["indices"]))


def _logical_path(path):
    path = tuple(path)
    if any(type(part) not in (str, int) or type(part) is bool
           or (type(part) is str and not part)
           or (type(part) is int and part < 0) for part in path):
        raise ValueBindingError(
            "value-binding-logical-path-invalid",
            "a logical leaf path contains only nonempty fields and naturals")
    return path


def _fold_solver_paths(paths, variable, array_factory):
    """Fold dense physical paths in cell order, partitioning each level once."""
    if () in paths:
        if len(paths) != 1:
            raise ValueBindingError(
                "value-binding-solver-shape-overlap",
                f"SolverIR variable {variable!r} is both scalar and array")
        return paths[()]
    cells = {}
    for path, value in paths.items():
        cells.setdefault(path[0], {})[path[1:]] = value
    heads = sorted(cells)
    if not heads or heads != list(range(len(heads))):
        raise ValueBindingError(
            "value-binding-solver-array-sparse",
            f"SolverIR variable {variable!r} has sparse declared cells")
    return array_factory(tuple(
        _fold_solver_paths(cells[head], variable, array_factory) for head in heads))


class ValueBinding:
    """One logical value type and the exact storage path of every scalar leaf."""

    __slots__ = ("_layout", "_leaf_bindings", "_solver_types", "_solver_variables",
                 "_value_type", "name")

    def __init__(self, name, value_type, leaf_bindings):
        if hasattr(self, "_value_type"):
            raise AttributeError("a ValueBinding is immutable")
        if type(name) is not str or not name:
            raise ValueBindingError(
                "value-binding-name-invalid",
                "a bound logical value needs a nonempty name")
        value_type = as_value_type(value_type)
        layout = layout_of_value_type(value_type)
        layout_leaves = layout.leaves()
        expected = tuple(leaf.path for leaf in layout_leaves)
        supplied = []
        seen_logical = set()
        seen_solver = set()
        for item in leaf_bindings:
            if type(item) not in (tuple, list) or len(item) != 2:
                raise ValueBindingError(
                    "value-binding-row-invalid",
                    "a value binding row is one logical/SolverIR path pair")
            logical, solver = _logical_path(item[0]), item[1]
            if type(solver) is not SolverLeafPath:
                raise ValueBindingError(
                    "value-binding-solver-path-invalid",
                    "a bound leaf uses one exact SolverLeafPath")
            if logical in seen_logical:
                raise ValueBindingError(
                    "value-binding-logical-path-duplicate",
                    f"logical leaf path {logical!r} is bound twice")
            if solver in seen_solver:
                raise ValueBindingError(
                    "value-binding-solver-path-duplicate",
                    f"SolverIR leaf path {solver.identity!r} is bound twice")
            seen_logical.add(logical)
            seen_solver.add(solver)
            supplied.append((logical, solver))
        by_logical = dict(supplied)
        if set(by_logical) != set(expected):
            missing = [path for path in expected if path not in by_logical]
            extra = [path for path in by_logical if path not in set(expected)]
            raise ValueBindingError(
                "value-binding-leaf-set-mismatch",
                f"the binding does not cover exactly the declared leaves "
                f"(missing={missing!r}, extra={extra!r})")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "_value_type", value_type)
        object.__setattr__(self, "_layout", layout)
        # Canonical logical layout order, never caller mapping order.
        object.__setattr__(self, "_leaf_bindings", tuple(
            (path, by_logical[path]) for path in expected))
        # A binding is a complete physical schema, not merely a set of paths.
        # Validate density, recursive homogeneity and scalar/array overlap at
        # construction so no consumer can be the first to discover that this
        # supposedly immutable authority has no SolverIR type.
        leaf_types = {leaf.path: leaf.semantic_type for leaf in layout_leaves}
        by_variable = {}
        for logical, solver in self.leaf_bindings:
            by_variable.setdefault(solver.variable, {})[solver.indices] = leaf_types[logical]

        physical_types = []
        for variable, paths in by_variable.items():
            def array_type(children, variable=variable):
                if any(child != children[0] for child in children[1:]):
                    raise ValueBindingError(
                        "value-binding-solver-array-heterogeneous",
                        f"SolverIR variable {variable!r} has unlike cell types")
                return types.Array(len(children), children[0])

            physical_types.append((variable, _fold_solver_paths(paths, variable, array_type)))
        physical_types = tuple(sorted(physical_types))
        object.__setattr__(self, "_solver_types", physical_types)
        object.__setattr__(self, "_solver_variables", tuple(name for name, _kind in physical_types))

    def __setattr__(self, _name, _value):
        raise AttributeError("a ValueBinding is immutable")

    def __delattr__(self, _name):
        raise AttributeError("a ValueBinding is immutable")

    @classmethod
    def direct(cls, name, value_type):
        """Bind a scalar or ordinary recursive array to its same-named IR value.

        Records require an explicit mapping because a string field cannot be
        an IR array index.  This method is consequently the exact legacy flat
        compatibility case, not a structured-name invention rule.
        """
        value_type = as_value_type(value_type)
        layout = layout_of_value_type(value_type)
        logical = tuple(leaf.path for leaf in layout.leaves())
        if any(any(type(part) is str for part in path) for path in logical):
            raise ValueBindingError(
                "value-binding-structured-path-undeclared",
                "a record-valued declaration needs explicit SolverIR leaf paths")
        return cls(name, value_type, tuple(
            (path, SolverLeafPath(name, path)) for path in logical))

    @property
    def value_type(self):
        return self._value_type

    @property
    def layout(self):
        return self._layout

    @property
    def leaf_bindings(self):
        return self._leaf_bindings

    @property
    def identity(self):
        return (
            VALUE_BINDING_VERSION,
            self.name,
            self.value_type.identity,
            tuple((logical, solver.identity)
                  for logical, solver in self.leaf_bindings),
        )

    @property
    def solver_variables(self):
        return self._solver_variables

    def flatten(self, value):
        """Return canonical ``(SolverLeafPath, scalar)`` assignments."""
        flattened = self.layout.flatten(value)
        return tuple((solver, scalar) for (
            _logical, solver), scalar in zip(self.leaf_bindings, flattened))

    def assemble_solver_values(self, logical_values, *, array_factory=list):
        """Assemble physical SolverIR values from declared logical leaves.

        ``logical_values`` is keyed by the logical paths in :attr:`leaf_bindings`.
        The values themselves are deliberately opaque: concrete callers use Python
        scalars while lowering supplies SolverIR expressions.  Only the declaration
        decides how those leaves form physical variables.  ``array_factory`` is the
        sole representation-specific operation and receives the already ordered child
        values of one fixed array.

        This is the inverse structural operation of :meth:`reconstruct`.  In
        particular, a record may use one physical variable per leaf while an ordinary
        fixed array may use one recursively nested variable.  Neither case is inferred
        from a runtime value.
        """
        try:
            values = dict(logical_values)
        except (TypeError, ValueError):
            raise ValueBindingError(
                "value-binding-logical-image-invalid",
                "logical leaf values are an exact path/value mapping") from None
        expected = tuple(logical for logical, _solver in self.leaf_bindings)
        if set(values) != set(expected):
            missing = [path for path in expected if path not in values]
            extra = [path for path in values if path not in set(expected)]
            raise ValueBindingError(
                "value-binding-logical-image-mismatch",
                f"logical leaf values do not cover the declaration "
                f"(missing={missing!r}, extra={extra!r})")
        if not callable(array_factory):
            raise TypeError("a SolverIR array factory must be callable")

        by_variable = {}
        for logical, solver in self.leaf_bindings:
            by_variable.setdefault(solver.variable, {})[solver.indices] = \
                values[logical]

        return {
            variable: _fold_solver_paths(paths, variable, array_factory)
            for variable, paths in sorted(by_variable.items())
        }

    def reconstruct(self, solver_values):
        """Rebuild the logical value from an exact SolverIR value mapping."""
        return self.layout.reconstruct(tuple(
            solver.read(solver_values) for _logical, solver in self.leaf_bindings))

    def solver_values(self, value):
        """Materialize this logical value as complete SolverIR variables."""
        logical_paths = (logical for logical, _solver in self.leaf_bindings)
        return self.assemble_solver_values(zip(logical_paths, self.layout.flatten(value)))

    def solver_types(self):
        """Return the immutable physical types validated at construction."""
        return self._solver_types

    def finite_domains(self):
        """Finite scalar carriers by physical SolverIR leaf path."""
        leaves = {leaf.path: leaf for leaf in self.layout.leaves()}
        return tuple((solver, leaves[logical].carrier)
                     for logical, solver in self.leaf_bindings)

    def as_dict(self):
        return {
            "version": VALUE_BINDING_VERSION,
            "name": self.name,
            "value_type": self.value_type.as_dict(),
            "leaves": [
                {"logical": list(logical), "solver": solver.as_dict()}
                for logical, solver in self.leaf_bindings
            ],
        }

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {
                "version", "name", "value_type", "leaves"} \
                or value.get("version") != VALUE_BINDING_VERSION \
                or type(value.get("name")) is not str \
                or type(value.get("leaves")) is not list:
            raise ValueBindingError(
                "value-binding-image-invalid",
                "a ValueBinding image has its complete versioned field set")
        rows = []
        for row in value["leaves"]:
            if type(row) is not dict or set(row) != {"logical", "solver"} \
                    or type(row["logical"]) is not list:
                raise ValueBindingError(
                    "value-binding-image-invalid",
                    "a ValueBinding leaf image has logical and solver paths")
            rows.append((tuple(row["logical"]),
                         SolverLeafPath.from_dict(row["solver"])))
        got = cls(value["name"], ValueType.from_dict(value["value_type"]), rows)
        if got.as_dict() != value:
            raise ValueBindingError(
                "value-binding-image-not-canonical",
                "a ValueBinding image is not in canonical leaf order")
        return got

    def __eq__(self, other):
        return type(other) is ValueBinding and self.identity == other.identity

    def __hash__(self):
        return hash(self.identity)

    def __reduce__(self):
        return type(self), (self.name, self.value_type, self.leaf_bindings)

    def __repr__(self):
        return (f"ValueBinding({self.name!r}, {self.value_type!r}, "
                f"{len(self.leaf_bindings)} leaves)")
