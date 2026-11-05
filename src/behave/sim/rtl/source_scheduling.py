"""Source activation controls from compiler-derived dependencies.

The compiler owns dependency and scope resolution. This module only binds its
result to original CST ranges, preserving combinational and explicit-event
scheduling classes. There is no
post-hoc hit filter, predicate evaluator or per-task scheduling rule.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .source_edits import Insertion, Replacement
from .source_elaboration import parameter_partition
from .source_statement_events import RESUME_FUNCTION


@dataclass(frozen=True, slots=True)
class InstanceSensitivity:
    selector: str
    triggers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcessSensitivity:
    start: int
    end: int
    kind: str
    triggers: tuple[str, ...]
    instances: tuple[InstanceSensitivity, ...] = ()
    inlined_task_dependencies: bool = False
    task_frame_inputs: tuple[str, ...] = ()
    stationary_value_events: bool = False


@dataclass(frozen=True, slots=True)
class ActivationGuard:
    declarations: str
    before: str
    after: str
    pending: str | None = None


@dataclass(frozen=True, slots=True)
class PendingActivations:
    """One observer-state layout per source scope, indexed by lexical process.

    Generate alternatives can share their legal scope name while containing
    different processes. A uniform bank exposes the same observer members in
    every alternative; actual instances still have independent storage. This
    also keeps subscriber references independent of per-process declarations.
    """

    slots: Mapping[int, int]

    @classmethod
    def for_processes(cls, starts):
        return cls({start: slot for slot, start in enumerate(sorted(set(starts)))})

    def reference(self, start: int) -> str:
        return f"__behave_native_event_pending_{self.slots[start]}"

    @staticmethod
    def owner_reference(reference: str) -> str:
        """Uniform bank name, including the compiler-bound instance path."""
        return reference.rsplit("_", 1)[0]

    @property
    def declaration(self) -> str:
        # Independent scalars avoid a false dependency through a packed vector.
        # Declare the same bank in every scope, including generate alternatives;
        # notifications may name an inactive alternative's otherwise unused slot.
        # No compiler-specific variable-splitting transformation is required.
        return "".join(f" bit __behave_native_event_pending_{slot}=0;\n"
                       for slot in range(len(self.slots)))


def implicit_process(node) -> bool:
    return (node["kind"] in {"AlwaysCombBlock", "AlwaysLatchBlock", "ImmediateAssertionMember"}
            or node["statement"].get("timingControl", {}).get("kind") == "ImplicitEventControl")


def activation_guard(start: int, triggers: tuple[str, ...], *, pending=None, deferred=(),
                     instances=()) -> ActivationGuard:
    """Guard combinational activations, with optional queued-write events.

    Native evaluation may revisit a dataflow process without a source event.
    Compare its compiler-declared read ranges at the point of evaluation, then
    execute the original body and its probes together. Save the armed values
    after that body, as an event wait would: a process does not wake itself on
    its own blocking writes. Later NBA or other-process updates remain events.
    Read-value comparison alone cannot remember a change that is subsequently
    undone. A scheduler supplying ``pending`` also notifies this bit at each
    relevant primitive write. These values decide activation only, never the
    outcome of a goal. The plain guard is not a general event-history proof.
    """
    declarations, conditions, updates = [], [], []
    # One lexical body and one set of public callback coordinates. Constant
    # selectors choose its exact compiler dependency set in each instance;
    # unrelated instances' changes must not manufacture body entries.
    variants = instances or (InstanceSensitivity("1'b1", triggers),)
    for ordinal, variant in enumerate(variants):
        prefix = f"__behave_native_event_activation_{start}_{ordinal}"
        initialized = prefix + "_initialized"
        previous = [f"{prefix}_{index}" for index in range(len(variant.triggers))]
        declarations.append(f"bit {initialized}=0;\n" + "\n".join(
            f"var type({term} ) {old};" for term, old in zip(variant.triggers, previous, strict=True)))
        changed = [f"(({term} ) !== {old})" for term, old in
                   zip(variant.triggers, previous, strict=True)]
        active = [f"!{initialized}", *([pending] if pending else []), *changed,
                  *(queue.pending for queue in deferred)]
        conditions.append(" ||\n".join(active))
        snapshots = "\n".join(f"{old} = {term} ;" for term, old in
                             zip(variant.triggers, previous, strict=True))
        updates.append(f"{snapshots}\n{initialized}=1;")
    if instances:
        # Select the compiler-proved dependency set during elaboration. An OR
        # over all variants leaves all of their public observer storage in every
        # physical instance (quadratic for a wide generate array). Uniform block
        # names expose the same guard/snapshot interface with only one state.
        scope = f"__behave_native_event_activation_{start}"
        branches = []
        for variant, declaration, condition, update in zip(
                variants, declarations, conditions, updates, strict=True):
            branches.append(f"if ({variant.selector}) begin : {scope}\n"
                + declaration + "\nfunction automatic bit active();\nreturn ("
                + condition + ");\nendfunction\ntask automatic remember();\n"
                + update + "\nendtask\nend")
        # No selector means no source activation, just as in the disjunction.
        declarations = [" else ".join(branches) + f" else begin : {scope}\n"
            "function automatic bit active(); return 1'b0; endfunction\n"
            "task automatic remember(); endtask\nend\n"]
        before = f"begin if ({scope}.active()) begin\n"
        after = f"{scope}.remember();\n"
    else:
        before = "begin if (" + conditions[0] + ") begin\n"
        after = updates[0]
    before += f" {RESUME_FUNCTION}();\n"
    if pending is not None:
        after += f" {pending}=0;"
    after += " end "
    # An unchanged batch still advances the epoch. Acknowledging it outside the
    # activation body prevents the next changed batch from aliasing an old one.
    after += "\n".join(queue.consume for queue in deferred) + " end "
    # This guard selects source activations, not a new scheduling class.
    # An explicit @(read_set) makes the backend treat combinational logic as
    # clocked logic, so consumers can read its old value at the same edge.
    return ActivationGuard("\n".join(declarations), before, after, pending)


def blocking_write_watch(start: int, watches: tuple[tuple[str, str], ...], *,
                         enabled=None, writers=None) -> tuple[str, str]:
    """Latch read-range changes around an original blocking-write statement.

    The compiler resolves each (read expression, consumer pending bit) pair.
    Only those effect-free read ranges are copied; the original lvalue and RHS
    remain in place and execute once. The binder establishes at most one direct
    mutation per subscribed storage variable in this statement. This comparison
    alone cannot catch cancelling writes or deferred NBA commits.
    """
    declarations, before, after = blocking_write_parts(start, watches, enabled=enabled, writers=writers)
    return ("begin\n" + "\n".join(declarations) + "\n" + ";\n".join(before) + ";\n",
            "\n" + ";\n".join(after) + "; end ")


def blocking_write_parts(start: int, watches: tuple[tuple[str, str], ...], *, enabled=None, writers=None):
    """Same snapshot/latch operations for statements and for-header lists.

    Header clients insert these as ordered assignments around the original
    expression. They do not lower the loop or move its break/continue boundary.
    Optional compiler-bound enables select each exact subscriber relation. An
    inactive relation neither reads its term nor changes a pending bit. A
    compile-time writer also avoids elaborating absent consumer scopes.
    """
    subscribers = {}
    for term, pending in watches:
        subscribers.setdefault(term, set()).add(pending)
    declarations, before, after = [], [], []
    for index, (term, pending) in enumerate(subscribers.items()):
        old = f"__behave_native_event_write_{start}_{index}"
        declarations.append(f"var type({term} ) {old};")
        guards = {target: None if enabled is None else enabled.get((term, target)) for target in pending}
        active = " || ".join(f"({guard})" for guard in sorted(set(guards.values()))) if all(guards.values()) else None
        value = f"(({active}) ? ({term} ) : {old})" if active else term
        before.append(f"{old}={value} ")
        changed = f"(({term} ) !== {old})"
        if len(pending) > 1:
            # Every subscriber sees the same post-write source value. These
            # intervening notifications modify observer bits only; repeat a
            # wide/array comparison neither per subscriber nor per instance.
            # Inactive relations must still avoid reading their source term.
            shared = old + "_changed"
            declarations.append(f"bit {shared};")
            comparison = f"(({active}) && {changed})" if active else changed
            after.append(f"{shared}={comparison}")
            changed = shared
        for target in sorted(pending):
            notification = changed
            if guards[target] is not None:
                notification = f"(({guards[target]}) && {notification})"
            writer = None if writers is None else writers.get((term, target))
            after.append(f"{writer}({notification})" if writer else f"{target}={target} | {notification}")
    return tuple(declarations), tuple(before), tuple(after)


def conditional_pending_writer(name: str, enable: str, target: str) -> str:
    """Elaborate a subscriber reference only in the instances owning it.

    Both branches expose the same call. Only observer state is written; reads
    of source values remain at the original write site, including helper locals.
    The result lets for-header clients use their ordered assignment list.
    """
    changed = "__behave_native_event_changed"
    body = f"{target}={target} | {changed}; return 1'b0;"
    return (f"if ({enable}) begin: {name}\n"
            f"function automatic bit latch(input bit {changed}); {body} endfunction\n"
            f"end else begin: {name}\n"
            f"function automatic bit latch(input bit {changed}); return 1'b0; endfunction\n"
            "end\n")


def _instance_sensitivities(rows):
    """Partition by compiler-bound, immutable elaboration parameters.

    A parameter spelling is usable only when the compiler has validated it in
    every instance. Width and unsigned bit value define disjoint selectors.
    If these facts cannot distinguish different read sets, preparation refuses;
    there is no first-instance fallback or union of activation conditions.
    """
    bindings = [{item["reference"]: (item["width"], item["bits"])
                 for item in row.get("instance_parameters", ())} for row in rows]
    return tuple(InstanceSensitivity(selector, triggers) for selector, triggers in parameter_partition(
        bindings, [tuple(sorted(set(row["triggers"]))) for row in rows]))


def sensitivities_from_document(document, *, filename):
    """Bind compiler dependencies to a source process and its physical instances."""
    rows = {}
    instances = {}
    for row in document:
        if row["procedure"] not in {"Always", "AlwaysFF", "AlwaysComb", "AlwaysLatch",
                                     "ContinuousAssign", "NetInitializer"}:
            continue
        if row["file"] != filename:
            raise ValueError("process sensitivity is outside the selected source")
        start, end = row["start"], row["end"]
        triggers = tuple(sorted(set(row["triggers"])))
        if type(start) is not int or type(end) is not int or not 0 <= start < end \
                or any(type(term) is not str or not term for term in triggers):
            raise ValueError("invalid compiler process sensitivity")
        process = ProcessSensitivity(start, end, row["procedure"], triggers,
            inlined_task_dependencies=row.get("inlined_task_dependencies") is True,
            task_frame_inputs=tuple(row.get("task_frame_inputs", ())),
            stationary_value_events=row.get("stationary_value_events") is True)
        key = start, end
        if key in rows and rows[key].kind != process.kind:
            raise ValueError("inconsistent compiler process kind")
        instances.setdefault(key, []).append(row)
        rows[key] = process
    for key, members in instances.items():
        process = replace(rows[key],
            stationary_value_events=all(row.get("stationary_value_events") is True for row in members),
            inlined_task_dependencies=all(row.get("inlined_task_dependencies") is True for row in members),
            task_frame_inputs=tuple(sorted({item for row in members for item in row.get("task_frame_inputs", ())})))
        rows[key] = process
        if len({tuple(sorted(set(row["triggers"]))) for row in members}) > 1:
            variants = _instance_sensitivities(members)
            # `triggers` also indicates whether any instance can wake. Actual
            # comparisons below always use the instance partition, not this list.
            triggers = tuple(sorted({term for row in members for term in row["triggers"]}))
            rows[key] = replace(process, triggers=triggers, instances=variants)
    return rows


def process_wait_edits(node, spans, process, *, pending, deferred=()):
    """Use original syntax for explicit controls and compiler reads for implicit ones."""
    first, last = spans[id(node)]
    if (first, last) != (process.start, process.end):
        raise ValueError("process sensitivity does not match its source range")
    expected = {"AlwaysBlock": "Always", "AlwaysFFBlock": "AlwaysFF",
                "AlwaysCombBlock": "AlwaysComb", "AlwaysLatchBlock": "AlwaysLatch",
                "ImmediateAssertionMember": "AlwaysComb"}
    if expected[node["kind"]] != process.kind:
        raise ValueError("process sensitivity does not match its source kind")
    if node["kind"] == "ImmediateAssertionMember":
        # IEEE 1800-2017 16.4.3 gives static deferred assertions an implicit
        # always_comb process. Keep the original assertion/label as its body;
        # reads and physical-instance partition come from that same compiler
        # process, just as for an explicitly written always_comb.
        if not process.triggers:
            return (Insertion(first, "initial "),), ()
        guard = activation_guard(first, process.triggers, pending=pending, deferred=deferred,
                                 instances=process.instances)
        return (Insertion(first, "always_comb " + guard.before),
                Insertion(last, " " + guard.after + guard.declarations)), ()
    keyword_start, keyword_end = spans[id(node["keyword"])]
    statement = node["statement"]
    timing = statement.get("timingControl", {})
    implicit = timing.get("kind") == "ImplicitEventControl"
    if process.kind in {"AlwaysComb", "AlwaysLatch"} or implicit:
        # The pinned Verilator backend settles nonempty implicit combinational
        # processes at startup, including always @*. Unlike always_comb, an
        # empty @* never wakes. Preserve both rules, then wait for subsequent
        # compiler-derived read changes. Edge processes below still wait for
        # their first actual edge.
        header_end = spans[id(timing)][1] if implicit else keyword_end
        if not process.triggers:
            header = "initial wait (1'b0)" if implicit else "initial"
            return (), (Replacement(keyword_start, header_end, header),)
        instances = process.instances
        if implicit:
            instances = tuple(item for item in instances if item.triggers)
        guard = activation_guard(first, process.triggers, pending=pending, deferred=deferred,
                                 instances=instances)
        return ((Insertion(last, " " + guard.after + guard.declarations),),
                (Replacement(keyword_start, header_end, "always_comb " + guard.before),))
    if statement.get("kind") != "TimingControlStatement":
        raise ValueError("process needs an initial stationary event control")
    if process.stationary_value_events:
        # Keep the explicit wait, including the pinned backend's initial
        # settling of pure value-event controls. A pending rising edge
        # remembers primitive changes undone before native settle.
        # The compiler only certifies static value paths, never @(a+b), iff, or
        # posedge/negedge expressions. No dependency-change approximation enters
        # those controls. Clearing after the body cannot wake its own wait.
        wake = " or ".join((f"posedge {pending}",
                             *(f"posedge ({queue.pending})" for queue in deferred)))
        after = f" {pending}=0; " + " ".join(queue.consume for queue in deferred) + " end "
        # An unchanged committed batch must still advance the acknowledgement.
        # It cannot run the source body just to do that. Otherwise the epoch
        # wraps on the next changed batch and aliases the stale acknowledgement.
        # Changed batches are acknowledged only by the actual consumer above.
        after += "\n".join(f"always @({queue.name}_commit) "
                           f"if (!{queue.name}_commit[0]) {queue.consume}" for queue in deferred)
        if (timing["kind"] == "EventControlWithExpression"
                and timing["expr"]["kind"] == "ParenthesizedEventExpression"):
            event = timing["expr"]
            # Extend the original list at its outer closing delimiter. Wrapping
            # @(a,b) as @((a,b) or ...) turns its comma into an invalid nested
            # expression in the simulator. CST delimiters preserve commas,
            # comments and original event terms without reconstructing them.
            wait = (Insertion(spans[id(event["closeParen"])][0], f" or {wake}"),
                    Insertion(spans[id(timing)][1], " begin "))
        else:
            wait = (Insertion(spans[id(timing["at"])][1], "("),
                    Insertion(spans[id(timing)][1], f" or {wake}) begin "))
        return ((*wait, Insertion(last, after)), ())
    # Keep the original process class as well as its explicit event control.
    # Recasting an always process as an initial coroutine changes the backend's
    # NBA lowering: a loop with callbacks and packed partial writes can retain
    # only the last queued slice. Explicit controls already define when the
    # body (and its callbacks) executes; they need no scheduler replacement.
    return (), ()
