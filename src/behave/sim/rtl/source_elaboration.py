"""Compiler-owned elaboration constants, scope indices and disjoint selectors."""

from dataclasses import dataclass

from ..core.refusal import Refused
from .source_integral import integer_literal, integral_type, integral_value_facts

ParameterEnvironment = tuple[tuple[str, tuple[int, str]], ...]


@dataclass(frozen=True, slots=True)
class ElaborationEnvironment:
    constants: ParameterEnvironment
    # A visible constant expression and its compiler scalar type facts.
    types: tuple[tuple[str, tuple[int, bool, bool]], ...]


def elaboration_environments(document, children, symbols, selected):
    """Bind selected nodes to visible module/generate constants, not locals."""
    found = {}

    def visit(node, inherited, inherited_types):
        env, value_types = inherited, inherited_types
        if isinstance(node, dict):
            kind = node.get("kind")
            if kind == "InstanceBody":
                env, value_types = {}, {}
            if kind in {"InstanceBody", "GenerateBlock", "Package"}:
                env, value_types = dict(env), dict(value_types)
                for member in node.get("members", ()):
                    if member.get("kind") not in {"Parameter", "TypeParameter"}:
                        continue
                    name = f"\\{member['name']} "
                    env.pop(name, None)
                    env.pop(f"$bits({name})", None)
                    env.pop(f"({name}'(-1) < {name}'(0))", None)
                    value_types.pop(name, None)
                    value_types.pop(f"{name}'(0)", None)
                    if member["kind"] == "TypeParameter":
                        try:
                            facts = integral_value_facts(member["type"], symbols)
                        except Refused:
                            continue
                        value_types[f"{name}'(0)"] = facts
                        env[f"$bits({name})"] = 32, f"{facts[0]:x}"
                        env[f"({name}'(-1) < {name}'(0))"] = 1, str(int(facts[1]))
                        continue
                    try:
                        type_ = integral_type(member["type"], symbols)
                        value = integer_literal(member["value"])
                    except Refused:
                        continue
                    env[name] = type_.width, f"{value & ((1 << type_.width) - 1):x}"
                    try:
                        value_types[name] = integral_value_facts(member["type"], symbols)
                    except Refused:
                        pass
            if id(node) in selected:
                found[id(node)] = ElaborationEnvironment(tuple(sorted(env.items())),
                                                        tuple(sorted(value_types.items())))
        for _, child in children(node):
            visit(child, env, value_types)

    visit(document["design"], {}, {})
    return found


def constant_environments(document, children, symbols, selected):
    return {key: env.constants for key, env in
            elaboration_environments(document, children, symbols, selected).items()}


def generate_iterations(node):
    """Pair each elaborated member with its actual genvar value, never its offset."""
    variable = node.get("loopVariable", "").split(" ", 1)[-1]
    for index, member in enumerate(node.get("members", ())):
        if member.get("kind") != "GenerateBlock" or member.get("isUninstantiated"):
            continue
        values = [item.get("value") for item in member.get("members", ())
                  if item.get("kind") == "Parameter" and item.get("name") == variable]
        try:
            value, = values
            value = int(value)
        except (ValueError, TypeError):
            value = None
        yield index, member, value


def parameter_partition(bindings, outcomes):
    """Join immutable parameter valuations to their compiler-derived outcome.

    References must be valid in every selected scope. Width is part of each
    valuation: case equality alone would conflate differently sized constants.
    An indistinguishable pair with different outcomes is not a valid partition.
    """
    common = set.intersection(*(set(binding) for binding in bindings))
    varying = sorted(name for name in common if len({binding[name] for binding in bindings}) > 1)
    selected = {}
    for binding, outcome in zip(bindings, outcomes, strict=True):
        key = tuple(binding[name] for name in varying)
        if key in selected and selected[key] != outcome:
            raise ValueError("instance-dependent outcomes lack distinguishing compiler parameters")
        selected[key] = outcome
    return tuple((" && ".join(
        f"($bits({name} ) == {width} && ({name} ) === {width}'h{bits})"
        for name, (width, bits) in zip(varying, key, strict=True)) or "1'b1", outcome)
        for key, outcome in sorted(selected.items()))
