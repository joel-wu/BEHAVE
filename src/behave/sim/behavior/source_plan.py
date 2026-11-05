"""Source-owned Behavior goal declarations shared by catalogue and producers.

Like RTL's source plan, this preparation result keeps event identities before
instrumentation or symbolic construction. It contains no solver or runtime state.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, replace

from ..core.catalogue import COVERAGE, SAFETY, CatalogueManifest, Goal, OccurrenceKey, merge_catalogues
from ..core.source_rules import EMPTY_SOURCE_RULES
from . import goals as source_goals
from .declared import SafetySite, declare_safety
from .source_calls import resolve_helper
from .source_operations import OPERATORS, OperationSite, SourceOperationGoal, declare_operations
from .source_reads import SourceReadGoal, declare_reads

PROVIDER = "python-structural"


@dataclass(frozen=True, slots=True)
class SourcePlan:
    source_sha256: str
    file: str
    sites: tuple[source_goals.Site, ...]
    coverage_sites: tuple[source_goals.Site, ...]
    safety_sites: tuple[SafetySite, ...]
    manifest: CatalogueManifest
    operations: tuple[OperationSite, ...]
    operation_goals: tuple[SourceOperationGoal, ...] = ()
    read_goals: tuple[SourceReadGoal, ...] = ()

    def with_operation_rules(self, rules):
        by_operator = {}
        for rule in rules:
            by_operator.setdefault(rule.operator, []).append(rule.bind())
        return self.with_operation_goals(tuple(
            SourceOperationGoal(site, predicate, typed_variant=True)
            for site in self.operations for predicate in by_operator.get(OPERATORS[site.operator], ())))

    def with_operation_goals(self, bindings):
        """Bind a finite producer-owned policy without reparsing the source.

        The source plan already fixed eligible occurrences. Policy/type binding
        may add predicates, never manufacture an occurrence or alter structural
        identities. This is frontend preparation, not a per-hit reconstruction.
        """
        if self.operation_goals:
            raise ValueError("operation goals are bound once, before execution")
        bindings = tuple(sorted(bindings, key=lambda binding: binding.id))
        available = set(self.operations)
        for binding in bindings:
            if binding.site not in available:
                raise ValueError("operation goal names an occurrence outside the selected source closure")
            names = tuple(value.name for value in binding.predicate.values)
            arity = 1 if binding.site.operator in ("UAdd", "USub", "Invert", "Not") else 2
            if names != (*("operand" + str(i) for i in range(arity)), "result"):
                raise ValueError("Python operation producers supply operands and result, not invented flags")
            if any(value.width is not None for value in binding.predicate.values):
                raise ValueError("Python operation values are mathematical scalars, not fixed-width RTL vectors")
        manifest = CatalogueManifest(tuple(binding.declaration() for binding in bindings),
                                     occurrences=tuple((binding.occurrence(), binding.id) for binding in bindings))
        return replace(self, manifest=merge_catalogues(self.manifest, manifest), operation_goals=bindings)

    def check_source(self, source, file):
        if file != self.file or hashlib.sha256(source.encode()).hexdigest() != self.source_sha256:
            raise ValueError("the Behavior goal plan belongs to different source bytes or file")


def source_call_closure(model, resolver=None):
    """Return the one resolver-owned source closure used by every capability."""

    from .extract import ScopeResolver, SourceModel

    if type(model) is not SourceModel:
        raise TypeError("a source closure needs the exact SourceModel type")
    if resolver is None:
        resolver = ScopeResolver(model)
    roots = list(model.steps.values())
    # Atomic replay evaluates a domain's readiness predicate before invoking its
    # handler.  A present predicate is therefore another executable root.
    for domain in model.steps:
        if domain is None:
            continue
        ready = model.helpers.get(f"{domain}_ready")
        if ready is not None:
            roots.append(ready)
    return resolver, resolver.reachable_functions(roots)


def plan_source(model, source, file="behavior.py", *, resolver=None, source_rules=EMPTY_SOURCE_RULES):
    """Declare the selected closure independently of any producer or range plan.

    All lexical structural sites retain their original hook indices. Only the
    selected model-source owners contribute coverage; trusted-library partial
    operations also contribute safety, under their own file identity.
    """
    resolver, reachable = source_call_closure(model, resolver)
    sites = tuple(source_goals.enumerate_sites(source, file))
    source_functions = {id(node) for node in ast.walk(model.tree)
                        if isinstance(node, ast.FunctionDef)}
    owners = {(fn.name, fn.lineno, fn.col_offset) for fn in reachable
              if id(fn) in source_functions}
    coverage_sites = tuple(site for site in sites if site.owner in owners)
    safety = declare_safety(
        model.tree, file, reachable, file_of=resolver.occurrence_file,
        nodes_of=resolver.execution_nodes,
        state_fields=model.state_contract(),
        is_helper=lambda node: resolve_helper(
            resolver, resolver.scope_of(node), node) is not None,
        is_builtin=lambda node, name: resolver.is_builtin(
            resolver.scope_of(node), name))
    safety_sites = tuple(safety[key] for key in sorted(safety))
    plan = SourcePlan(
        hashlib.sha256(source.encode()).hexdigest(), file, sites, coverage_sites,
        safety_sites, _manifest(coverage_sites, safety_sites),
        declare_operations((fn for fn in reachable if id(fn) in source_functions),
                           resolver.execution_nodes, file))
    if source_rules.operations:
        plan = plan.with_operation_rules(source_rules.operations)
    reads = declare_reads((fn for fn in reachable if id(fn) in source_functions),
                          resolver.execution_nodes, file, source_rules.reads) if source_rules.reads else ()
    if reads:
        manifest = CatalogueManifest(tuple(binding.declaration() for binding in reads),
                                     occurrences=tuple((binding.occurrence(), binding.id) for binding in reads))
        plan = replace(plan, manifest=merge_catalogues(plan.manifest, manifest), read_goals=reads)
    return plan


def _manifest(sites, safety_sites):
    goals = []
    occurrences = []
    for site in sites:
        for outcome in site.outcomes:
            goal_id = source_goals.goal_id(site, outcome)
            event = ("entry" if site.family == source_goals.LOCATION
                     else "T" if outcome else "F")
            goals.append(Goal(
                goal_id,
                COVERAGE,
                family=site.family,
                provider=PROVIDER,
                event=event,
                provenance={
                    "file": site.file,
                    "line": site.line,
                    "col": site.col,
                    "ordinal": site.ordinal,
                    "kind": site.kind,
                    "outcome": event,
                },
                policy=f"{source_goals.POLICY_VERSION}/{source_goals.POLICY}",
            ))
            occurrences.append((OccurrenceKey(
                PROVIDER,
                (site.file, site.line, site.col, site.ordinal, "source"),
                f"{site.kind}:{event}",
            ), goal_id))
    # Safety sites belong to the complete resolver-owned call closure.  An
    # approved ``fp_lib`` helper is lowered as source rather than as a trusted
    # primitive, so its partial operations owe the same goals as operations in
    # the model file.  Structural coverage remains limited to instrumented
    # model-source sites above; the native library does not emit branch hits.
    for site in safety_sites:
        goal_id = site.id
        goals.append(Goal(
            goal_id,
            SAFETY,
            family=site.kind,
            provider=PROVIDER,
            event="failure",
            provenance={
                "file": site.file,
                "line": site.line,
                "col": site.col,
                "end_line": site.end_line,
                "end_col": site.end_col,
                "kind": site.kind,
            },
            policy=f"{source_goals.POLICY_VERSION}/{source_goals.POLICY}",
        ))
        occurrences.append((OccurrenceKey(
            PROVIDER,
            (site.file, site.line, site.col, site.end_line, site.end_col,
             site.kind, "source"),
            "safety-failure",
        ), goal_id))
    return CatalogueManifest(goals, occurrences=occurrences)
