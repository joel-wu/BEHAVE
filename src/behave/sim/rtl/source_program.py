"""Shared compiler storage for source activations and their event scheduler."""

from dataclasses import dataclass

from ..core.refusal import Refusal, Refused
from .source_integral import IntegralType
from .source_ir import SourceCompiler, SourceProcedure
from .source_storage import SourceStorage, storage_bindings
from .source_values import ArrayType, source_type


@dataclass(frozen=True, slots=True)
class SourceControlFacts:
    preserved_ranges: dict
    absent_events: frozenset


@dataclass(slots=True)
class SourceProgram:
    compiler: SourceCompiler
    bindings: dict[str, SourceStorage]
    names: dict[tuple, str]
    fields: dict[str, SourceStorage]
    kinds: dict[str, IntegralType | ArrayType]
    processes: tuple[SourceProcedure, ...] = ()

    def storage_name(self, identity):
        view = self.compiler.net_views.get(identity)
        if view is not None:
            identity = view.owner
        field = self.bindings.get(identity)
        if field is None:
            raise Refused(Refusal("rtl-source-storage-owner-unresolved",
                "source storage has no implemented instance or global owner",
                symbol=self.compiler.symbols[identity].get("name")))
        if field.key not in self.names:
            name = f"s{len(self.names)}"
            kind = source_type(self.compiler.symbols[identity]["type"], self.compiler.symbols)
            self.names[field.key] = name
            self.fields[name] = field
            self.kinds[name] = kind
        return self.names[field.key]

    def domain(self, name):
        return self.kinds[name].domain

    def control_facts(self, proposed, *, events=(), check):
        """Share source transfer for register bounds and event exclusion.

        Entry values start in their declared physical types, never suggested
        procedure ranges. With no proposed hypotheses, exclusions therefore
        hold at arbitrary source entry. Otherwise this is conditional
        preservation, not reset establishment. An absent
        event requires the preserved ranges at entry; startup/reset and public
        catalogue binding remain the caller's separate obligations. Witnesses
        visit failed prefixes too, not only normal procedure exits.
        Whole-word NBA queues preserve bounded leaves at commit. Blocking
        writes must preserve the bound at EVERY write, including failed
        prefixes, not merely restore it at procedure exit. Thus notification
        and commit order cannot expose an out-of-range intermediate value.
        Array hypotheses are uniform leaf bounds; untouched cells keep their
        bounded values. Partial NBA writes and unknown transfers reject a
        candidate, never restrict an input or a failed prefix.

        Dropping a hypothesis rechecks its dependent processes. This sparse
        worklist shares one transfer among all queues of a process; it does
        not build a separate SMT relation or repeatedly sweep all processes.
        """
        from collections import deque
        from ..symbolic import ir, ranges

        wanted, declared, observed = frozenset(events), set(), set()
        writers, rejected = {}, set()
        for index, process in enumerate(self.processes):
            check()
            marks = {node.goal for node in ir.walk(process.procedure.body)
                     if isinstance(node, (ir.Mark, ir.Fail))}
            declared.update(marks)
            if marks & wanted:
                observed.add(index)
            for name, now, _ in process.write_masks:
                if now:
                    writers.setdefault(name, set()).add(index)
            for write in process.deferred:
                if not write.whole:
                    rejected.add(write.target)
                writers.setdefault(write.target, set()).add(index)
        if not wanted <= declared:
            raise ValueError('source control facts require declared IR events')
        held = {name: bound for name, bound in proposed.items()
                if name in writers and name not in rejected and not self.fields[name].input
                and type(bound) is ranges.Range}
        active = observed | set().union(*(writers[name] for name in held))
        readers = {name: set() for name in held}
        for index in active:
            process = self.processes[index]
            for name in readers.keys() & set(process.procedure.state):
                readers[name].add(index)
        queued = set(active)
        pending = deque(sorted(queued))
        possible = {}
        while pending:
            check()
            index = pending.popleft()
            queued.remove(index)
            process = self.processes[index]
            targets = [write for write in process.deferred if write.target in held]
            blocking = {name for name, now, _ in process.write_masks if now and name in held}
            if not targets and not blocking and index not in observed:
                continue
            seen, bad = set(), set()

            def bounded(name, value):
                bound = held[name]
                if (value is None or value.lo is None or value.hi is None
                        or value.lo < bound.lo or value.hi > bound.hi):
                    bad.add(name)

            def witness(command, env):
                check()
                if isinstance(command, (ir.Mark, ir.Fail)) and command.goal in wanted:
                    seen.add(command.goal)
                elif isinstance(command, (ir.Assign, ir.Store)) and command.var in blocking:
                    expr = (command.expr if isinstance(command, ir.Assign) else
                            ir.Op('store', ir.Var(command.var), command.index, command.expr))
                    bounded(command.var, ranges.of_expression(expr, env))

            env = {name: self.domain(name) for name in process.procedure.state}
            env.update((name, bound) for name, bound in held.items() if name in env)
            post = ranges.procedure_post(process.procedure, env, witness=witness,
                                         source_paths=True)
            possible[index] = seen
            # No normal exit cannot commit queued values; failure-prefix
            # reachability remains the original event relation's obligation.
            if post is not None:
                for write in targets:
                    bounded(write.target, post.get(write.data))
            for name in sorted(bad):
                del held[name]
                for dependent in sorted(readers[name] - queued):
                    pending.append(dependent)
                    queued.add(dependent)
        return SourceControlFacts(held, wanted - set().union(*possible.values()))

    def storage_range(self, identity, low, high):
        """Translate compiler sensitivity bits through the same storage view."""
        kind = source_type(self.compiler.symbols[identity]["type"], self.compiler.symbols)
        if not 0 <= low <= high < kind.width:
            raise Refused(Refusal("rtl-source-scheduler-read-range-unsupported",
                "the compiler sensitivity range must fit its integral storage", symbol=identity))
        view = self.compiler.net_views.get(identity)
        offset = 0 if view is None else view.offset
        return self.storage_name(identity), low + offset, high + offset


def lower_source_program(source, document, plan, *, filename, contexts=None, defer_domains=False,
                         connect_ports=False, initialization=False, native_assignments=False):
    compiler = SourceCompiler(source, document, plan, filename=filename,
                              native_assignments=native_assignments)
    bindings = storage_bindings(document, symbols=compiler.symbols)
    transfers = ()
    if connect_ports:
        from .source_ports import bind_port_connections

        bindings, compiler.net_views, transfers = bind_port_connections(compiler.symbols, bindings)
    program = SourceProgram(compiler, bindings, {}, {}, {})
    contexts = plan.declarations.contexts if contexts is None else contexts
    program.processes = tuple(compiler.lower(context, namespace=f"p{index}_",
        storage_name=program.storage_name, defer_domains=defer_domains)
        for index, context in enumerate(contexts))
    program.processes += tuple(compiler.lower_transfer(assignment, namespace=f"port{index}_",
        storage_name=program.storage_name, defer_domains=defer_domains)
        for index, assignment in enumerate(transfers))
    if initialization:
        from .source_initial import _initial_nodes

        initial = (node for node in _initial_nodes(document, compiler.symbols)
                   if node.get('kind') == 'ProceduralBlock' and node.get('procedureKind') == 'Initial')
        program.processes += tuple(compiler.lower_initial(node, namespace=f'init{index}_',
            storage_name=program.storage_name, defer_domains=defer_domains)
            for index, node in enumerate(initial))
    return program
