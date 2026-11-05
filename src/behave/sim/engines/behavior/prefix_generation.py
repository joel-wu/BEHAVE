"""Joint input-window proposals, still subject to the common native replay.

Single-step search pins the preceding state. These queries instead connect
successive source transitions, allowing earlier inputs to set up a later goal.
Every window starts at its seed's observed state; only inputs inside it vary.
They keep the seed's queue lengths, reset positions and physical schedule.
Local UNSAT is not bounded-analysis evidence.
"""

import copy
import itertools
import time

from ...symbolic import encoder
from ...behavior import residual
from ...symbolic import slicing
from ...symbolic import ranges
from ...symbolic import ir
from ...symbolic import validation as procedure_checks
from ...core.construction_checkpoint import ConstructionCheckpoint
from ...generation_protocol import ProbeRequest
from ...query_plan import widening_windows
from .. import probing
from ..local_ir import _logical_candidate, _model_value


class _PrefixWitness(Exception):
    """Stop construction only for a decoded SAT proposal, not a verdict."""

    def __init__(self, goal, candidate):
        self.goal, self.candidate = goal, candidate


def prefixes(occurrences):
    """Widen across each reset epoch, also starting from observed later states.

    Disjoint tiles at each scale give O(n) windows and O(n log n) total stored
    references, not every O(n²) start/end pair. Visit the reset-origin ladder
    first, then later tiles at each depth. The full epoch remains available.
    """
    epochs = []
    for item in occurrences:
        if item["position"] == 0:
            epochs.append([])
        if epochs:
            epochs[-1].append(item)
    out = {}
    for epoch in epochs:
        for window in widening_windows(epoch):
            out[f"prefix:{window[0]['id']}:{len(window)}"] = window
    return out


def requests(windows, targets, manifest, ruled_out, observed_goals, proc, *, dependencies=None,
             factory=ProbeRequest):
    from .generation import _remaining_goal_groups

    # Input framing is a precondition. Keep its marker sequence fixed while
    # allowing all data fields to change together; the unchanged suffix then
    # remains legal too. Framing/schedule synthesis is a separate capability.
    markers = {logical for framing, projection in proc.input_framing
               for public, logical in projection if public in framing.fields}
    selected = tuple(goal for goal in targets if goal not in observed_goals)
    groups = _remaining_goal_groups(manifest, selected, (), ruled_out, dependencies=dependencies)
    controls = _control_inputs(proc)
    scopes = []
    for identity, window in windows.items():
        fields = tuple(field for item in window for name, field in item["bindings"]
                       if name not in markers)
        if not fields:
            continue
        fixed = {field.coordinate for item in window for name, field in item["bindings"]
                 if name in controls}
        payload = tuple(field for field in fields if field.coordinate not in fixed)
        scopes.append((identity, fields, payload if payload and payload != fields else ()))
    # A restricted command path and the unrestricted query are separate public
    # work items, with separate provenance/outcomes. Try the complete restricted
    # window ladder before a shallow unrestricted question. No nested solver or
    # hidden half-budget can block deeper state setup.
    ordered = [(identity, payload) for identity, _fields, payload in scopes if payload]
    ordered.extend((identity, fields) for identity, fields, _payload in scopes)
    group_span = 1 + max((group for group, _goals in groups), default=0)
    for index, (identity, fields) in enumerate(ordered):
        for group, goals in groups:
            yield factory(goals, occurrence=identity, mutable_fields=fields,
                               rank=2 * (index * group_span + group) + 1,
                               catalogue_digest=manifest.digest())


def command_preparations(proc):
    """Source-derived command effects, used only to propose Search domains.

    Interpret scalar bounds with one command discriminator fixed. Unknown
    branches retain both arms; loops are inspected once with their writes
    forgotten, never unrolled. This neither prunes the exact encoder nor proves
    a goal unreachable. Constant initialization and expensive query commands
    remain alternatives, not assumptions about what a command means.
    """
    from collections import ChainMap
    from behave.isolation.preparation_control import check_preparation

    cuts = {name: set() for name in _control_inputs(proc) if name in proc.inputs}
    for term in ir.expression_nodes(proc.body):
        if isinstance(term, ir.Op) and term.op in ("eq", "ne") and len(term.args) == 2:
            left, right = term.args
            if isinstance(left, ir.Lit):
                left, right = right, left
            if isinstance(left, ir.Var) and left.name in cuts and isinstance(right, ir.Lit) \
                    and type(right.value) is int:
                cuts[left.name].add(right.value)
    bounds = dict(proc.semantics.solver_input_bounds())
    state = set(proc.state)
    result, reads_cache = {}, {}
    for name, values in cuts.items():
        lo, hi = bounds[name]
        values = {value for value in values if lo <= value <= hi}
        complement = lo
        for value in sorted(values):
            if value == complement:
                complement += 1
        if complement <= hi:
            values.add(complement)
        effects = []
        for value in sorted(values):
            root = ChainMap({name: ranges.Bounds(value, value)})
            pending = [("visit", proc.body, root)]
            written, input_written, goals, cost = set(), set(), set(), 0
            payload = set(proc.inputs) - {name}
            while pending:
                check_preparation()
                action, *arguments = pending.pop()
                if action == "merge":
                    env, left, right = arguments
                    for key in left.keys() | right.keys():
                        a, b = left.get(key, env.get(key)), right.get(key, env.get(key))
                        env[key] = a if a == b else None
                    continue
                command, env = arguments
                cost += 1
                if isinstance(command, ir.Seq):
                    pending.extend((("visit", command.second, env),
                                    ("visit", command.first, env)))
                elif isinstance(command, ir.If):
                    bound = ranges.bounds_of_expression(command.guard, env)
                    if bound is not None and bound.lo == bound.hi and bound.lo is not None:
                        pending.append(("visit", command.then if bound.lo else command.orelse, env))
                    else:
                        left, right = env.new_child(), env.new_child()
                        pending.extend((("merge", env, left.maps[0], right.maps[0]),
                                        ("visit", command.orelse, right),
                                        ("visit", command.then, left)))
                elif isinstance(command, ir.For):
                    bound = ranges.bounds_of_expression(command.trip_count, env)
                    if bound is not None and bound.lo == bound.hi == 0:
                        continue
                    changed = {item.var for item in ir.walk(command.body)
                               if isinstance(item, (ir.Assign, ir.Store))}
                    changed.update(item.index for item in ir.walk(command.body)
                                   if isinstance(item, ir.For))
                    changed.add(command.index)
                    for key in changed:
                        env[key] = None
                    inner = env.new_child()
                    pending.extend((("merge", env, inner.maps[0], {}),
                                    ("visit", command.body, inner)))
                elif isinstance(command, (ir.Assign, ir.Store)):
                    reads = set()
                    slicing._command_reads(command, reads, reads_cache)
                    if reads & payload:
                        # May-dependence is deliberately monotone through
                        # branches. This ranks proposals only; it never removes
                        # a writer or constrains the exact transition relation.
                        payload.add(command.var)
                        if command.var in state:
                            input_written.add(command.var)
                    if command.var in state:
                        written.add(command.var)
                    env[command.var] = (ranges.bounds_of_expression(command.expr, env)
                                       if isinstance(command, ir.Assign) else None)
                elif isinstance(command, (ir.Mark, ir.Fail)) and command.goal is not None:
                    goals.add(command.goal)
            effects.append((value, frozenset(written), frozenset(goals), cost,
                            frozenset(input_written)))
        result[name] = tuple(effects)
    return result


def setup_commands(commands, needed, trigger):
    """Rank source writers once, shared by symbolic and concrete proposals."""
    setup = set()
    for state in sorted(needed):
        writers = [row for row in commands if row[0] != trigger and state in row[1]]
        if writers:
            best = min(writers, key=lambda row: (len(row[1] - needed),
                state not in row[4], len(row[1]), row[3], row[0]))
            setup.add(best[0])
    initialization = tuple(sorted(row[0] for row in commands
                                   if row[0] in setup and not row[4]))
    return initialization, tuple(sorted(setup - set(initialization)))


def setup_window(epoch, proc, effects, dependencies):
    """Prefer a source-derived setup writer before the selected trigger.

    Reuse preparation-command ranking: a query may write temporary workspace
    in persistent fields, so the nearest writer need not prepare its inputs.
    This only adds a preferred domain; the caller retains all original windows.
    """
    needed = {name for index, name in enumerate(proc.state)
              if dependencies & (1 << index)}
    starts = []
    for control, commands in effects.items():
        _initialization, filling = setup_commands(
            commands, needed, epoch[-1]['input'].get(control))
        start = next((index for index in range(len(epoch) - 2, -1, -1)
                      if epoch[index]['input'].get(control) in filling), None)
        if start is not None:
            starts.append(start)
    return epoch[max(starts):] if starts else ()


def array_setup_writers(epoch, proc, effects, dependencies, arrays):
    """Try each observed writer path, not just the nearest setup command.

    Different native branches can constrain writes to disjoint address ranges.
    Keep one latest representative per observed command/hit footprint. This
    ranks existing input proposals only: equal footprints are not equivalent
    states, and the ordinary contexts and joint histories remain available.
    """
    needed = {name for index, name in enumerate(proc.state)
              if dependencies & (1 << index)}
    seen = set()
    for control, commands in effects.items():
        _, filling = setup_commands(commands, needed, epoch[-1]['input'].get(control))
        writers = {command for command, _writes, _goals, _cost, input_writes in commands
                   if command in filling and input_writes & needed & arrays}
        for item in reversed(epoch[:-1]):
            command = item['input'].get(control)
            if command not in writers:
                continue
            key = control, command, frozenset(item['hits'])
            if key not in seen:
                seen.add(key)
                yield item


def first_consumer_windows(epoch, writers, effects):
    """Connect each setup to the first observed invocation of its consumer.

    Repeated expensive queries before a later anchor need not all be solved
    just to propose new state contents. These are additional contiguous,
    reset-local windows; the full anchored histories remain available.
    Command matching is an ordering hint, not a dependence or safety proof.
    """
    from bisect import bisect_right

    controls = tuple(name for name in effects if name in epoch[-1]['input'])
    if not controls:
        return
    signature = tuple(epoch[-1]['input'][name] for name in controls)
    consumers = [i for i, item in enumerate(epoch)
                 if tuple(item['input'].get(name) for name in controls) == signature]
    positions = {item['id']: i for i, item in enumerate(epoch)}
    for writer in writers:
        start = positions[writer['id']]
        successor = bisect_right(consumers, start)
        if successor < len(consumers):
            yield epoch[start:consumers[successor] + 1]


def preparation_requests(windows, groups, proc, manifest, dependencies, effects, *, factory):
    """Expose short preparation/trigger paths before fixed-seed ladders.

    Only reset-origin ladders receive these additional proposals: O(log n)
    paths per command/goal family, not every sequence of command choices.
    One-shot initializers precede repeated payload writes; a clear operation
    must not erase every completed round of array preparation. Repeated writes
    can initialize multiple cells of one state.
    Original restricted and unrestricted questions remain available. Each
    proposal's fixed command path is part of its exact local question identity.
    """
    from ...core.record_value import canonical_json
    import hashlib

    markers = {logical for framing, projection in proc.input_framing
               for public, logical in projection if public in framing.fields}
    originals = tuple((key, window) for key, window in windows.items()
                      if window[0]["position"] == 0)
    for group, goals in groups:
        needed = {name for index, name in enumerate(proc.state)
                  if dependencies.get(goals[0], 0) & (1 << index)}
        if not needed:
            continue
        for name, commands in effects.items():
            if name in markers:
                continue
            for trigger, _writes, reachable, _cost, _input_writes in commands:
                selected = tuple(goal for goal in goals if goal in reachable)
                if not selected:
                    continue
                initialization, filling = setup_commands(commands, needed, trigger)
                if not initialization and not filling:
                    continue
                setup = initialization + filling
                for identity, window in originals:
                    if not all(name in dict(item["bindings"]) for item in window):
                        continue
                    start = initialization[:len(window) - 1]
                    repeat = filling or setup
                    path = start + tuple(repeat[i % len(repeat)]
                        for i in range(len(window) - 1 - len(start))) + (trigger,)
                    if all(item["input"][name] == value for item, value in zip(window, path, strict=True)):
                        continue
                    token = hashlib.sha256(canonical_json((identity, name, path)).encode()).hexdigest()
                    occurrence = f"prepare:{token}"
                    windows[occurrence] = tuple({**item, "prepared_inputs": {name: value},
                                                "preparation": (name, setup, trigger)}
                                               for item, value in zip(window, path, strict=True))
                    fields = tuple(field for item in window for logical, field in item["bindings"]
                                   if logical not in markers)
                    yield factory(selected, occurrence=occurrence, mutable_fields=fields,
                                  rank=group, catalogue_digest=manifest.digest())


def continuation_requests(occurrences, windows, groups, proc, manifest, dependencies,
                          effects, *, factory):
    """Prepare one missing write immediately before an observed consumer.

    Keep the reached entry state and the consumer's complete input. Source
    effects select writer commands; their addresses and payloads remain solver
    inputs. Adjacent reset-local pairs give O(n) proposals per source family,
    not a Cartesian search over histories. Broader windows remain available.
    These are Search proposals only, never bounded-unreachability evidence.
    """
    from ...core.record_value import canonical_json
    import hashlib

    rows = tuple(occurrences)
    markers = {logical for framing, projection in proc.input_framing
               for public, logical in projection if public in framing.fields}
    for group, goals in groups:
        needed = {name for index, name in enumerate(proc.state)
                  if dependencies.get(goals[0], 0) & (1 << index)}
        if not needed:
            continue
        for previous, consumer in zip(rows, rows[1:]):
            if consumer['position'] != previous['position'] + 1:
                continue
            for name, commands in effects.items():
                if name in markers or name not in dict(previous['bindings']):
                    continue
                trigger = consumer['input'].get(name)
                reachable = next((row[2] for row in commands if row[0] == trigger), ())
                selected = tuple(goal for goal in goals if goal in reachable)
                if not selected:
                    continue
                _, filling = setup_commands(commands, needed, trigger)
                for writer in filling:
                    if previous['input'].get(name) == writer:
                        continue  # The ordinary writer-to-consumer scope already exists.
                    token = hashlib.sha256(canonical_json((previous['id'], consumer['id'],
                        name, writer)).encode()).hexdigest()
                    identity = f'prepare:continuation:{token}'
                    windows[identity] = (
                        {**previous, 'prepared_inputs': {name: writer},
                         'preparation': (name, (writer,), trigger)},
                        {**consumer, 'prepared_inputs': dict(consumer['input'])})
                    fields = tuple(field for logical, field in previous['bindings']
                                   if logical not in markers)
                    yield factory(selected, occurrence=identity, mutable_fields=fields,
                                  rank=group, catalogue_digest=manifest.digest())


class PrefixPreparation:
    """One latest exact entry/body owner, shared across window questions.

    Syntax/carrier plans and the latest completed transition prefix are shared.
    A changed entry state or projected body replaces the planning owner. The
    unroller independently checks input restrictions and the goal cone before
    reusing a prefix; no growing history or solver answers are retained here.
    """

    def __init__(self, proc):
        self.original = proc
        self.index = slicing.index(proc)
        self.validation = None
        self.key = self.proc = self.prepared = None
        self.construction = None

    def at(self, state, goals, steps):
        from ...behavior.value_records import domain_tree
        from ...core.record_value import freeze_record_value

        original = self.original
        keep = slicing.for_run(original, goals, steps=steps, _index=self.index)
        project = (keep is not None and not slicing.audit(
            original, goals, keep, _index=self.index)
            and any(isinstance(cmd, ir.For) and id(cmd) not in keep
                    for cmd in ir.walk(original.body)))
        key = (frozenset(keep) if project else None,
               freeze_record_value(domain_tree(state)))
        if key != self.key:
            proc = original
            if project:
                if self.validation is None:
                    self.validation = procedure_checks._procedure_validation(original)
                proc = slicing._query_projection(
                    original.derived(graph=None, boundary_complete=False),
                    keep, self.validation.types)
            proc = copy.copy(proc)
            proc.initial = dict(state)
            # Publish only a complete, identity-bound validation. The encoder
            # separately keys every carrier plan by its exact range image.
            validation = procedure_checks._procedure_validation(proc)
            self.key, self.proc, self.prepared = key, proc, validation
            self.construction = None
        return self.proc, self.prepared


class PrefixQuery:
    """One latest prefix formula; never retain SAT models or cancelled solvers."""

    def __init__(self, instance, window, goals, *, preparation=None):
        self.instance, self.window, self.goals = instance, window, tuple(goals)
        self.preparation = preparation or PrefixPreparation(instance.proc)
        if self.preparation.original is not instance.proc:
            raise ValueError("window preparation belongs to another source procedure")
        self.run = None
        self._mutable = None
        self.proc = self.env = None
        self._prepared = None
        self._construction = residual._RunConstruction()
        self._registration = self._formula = self._selected = None
        self._formula_checkpoint = ConstructionCheckpoint()
        self._targets = self.goals

    def probe(self, request, timeout_ms):
        import z3

        started = time.monotonic()
        deadline = started + timeout_ms / 1000
        phase = "construction"

        def check():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"input-prefix {phase} budget exhausted")

        def construction_check():
            if time.monotonic() >= deadline:
                raise encoder._EncodingPaused

        def result(outcome, **kwargs):
            return probing.ProbeResult(outcome, probing.Scope.EXACT_LOCAL_HIT,
                solve_ms=(time.monotonic() - started) * 1000, **kwargs)

        mutable = frozenset(field.coordinate for field in request.mutable_fields)
        if self._mutable != mutable:
            self.run = None
            self.proc = self.env = None
            self._prepared = None
            self._construction = residual._RunConstruction()
            self._registration = self._formula = self._selected = None
            self._mutable = mutable
        if request.targets != self._targets:
            # Registration owns the same definitions/restrictions, but the
            # sliced disjunction belongs to the old requested target set.
            self._selected = None
            self._targets = request.targets

        def early_hit(previous, current, halted, frames):
            nonlocal phase
            steps = (*previous, current)
            hits = {goal: z3.And(z3.Not(halted), term)
                    for goal, term in current.hits.items() if goal in request.targets}
            if not hits or all(z3.is_false(z3.simplify(term)) for term in hits.values()):
                # A narrowed request may no longer ask about this suspended
                # occurrence. Its registration must not leak into the next one.
                self._registration = self._formula = self._selected = None
                return
            # Preserve every preceding definition, input restriction and frame.
            # Only the unexecuted suffix of the current step is absent. Source
            # failure before this occurrence is in its hit guard; a failure in
            # a previous transaction is in ``halted``.
            boundary = {f"{name}@{i}": enc.pre[name]
                        for i, enc in enumerate(steps) for name in proc.inputs}
            boundary.update({name: steps[0].pre[name] for name in proc.state})
            product = encoder.StepEncoding(
                boundary, {}, hits, current.ok, current.error, None, {},
                error_codes=current.error_codes,
                definitions=itertools.chain.from_iterable(enc.definitions for enc in steps),
                restrictions=(*itertools.chain.from_iterable(enc.restrictions for enc in steps),
                              *frames))
            fixed = _fixed_inputs(proc, env, self.window, steps, mutable, construction_check)
            target = z3.And(z3.Or(*hits.values()), *fixed)
            phase = "formula registration"
            self._formula_checkpoint.callback = construction_check
            if self._registration is None:
                self._registration = product._start_query_formula(
                    checkpoint=self._formula_checkpoint, prefix=self._construction.formula)
            if self._formula is None:
                self._formula = self._registration.advance(self._formula_checkpoint)
                # Keep only a completed immutable formula. Across occurrences
                # and steps, registration checks exact definitions, atom roles
                # and restrictions before reusing any of its dependency audit.
                self._construction.formula = self._formula
            phase = "formula slicing"
            if self._selected is None:
                self._selected = self._formula.slice(target)
            selected = self._selected
            construction_check()
            phase = "solver"
            # An early occurrence is optional progress, not the whole prefix
            # question. Share its existing deadline with the remaining step
            # occurrences and the complete-run fallback; a hard short prefix
            # must not monopolize the longer sequence's entire solve budget.
            shares = len(self.window) - len(previous) + 1
            now = time.monotonic()
            bounds = dict(proc.semantics.solver_input_bounds())
            answer, model = _solve_prefix(selected.query_assertions,
                tuple((enc.pre[name], bounds[name]) for enc in steps for name in proc.inputs),
                now + (deadline - now) / shares, check)
            self._registration = self._formula = self._selected = None
            if answer != z3.sat:
                # Neither UNSAT nor UNKNOWN excludes unencoded occurrences.
                # The unroller continues; only its completed run can supply
                # the existing full-prefix LOCAL_UNSAT/representation check.
                check()
                phase = "construction"
                return
            goal = next(goal for goal, term in hits.items()
                        if z3.is_true(model.eval(term, model_completion=True)))
            raise _PrefixWitness(goal, _candidate_values(proc, self.window, steps, model))

        try:
            if self.proc is None:
                # No whole-task bind, output proof or global invariant. Derive
                # finite carriers only for this window, from its observed
                # entry state. The unchanged seed prefix establishes that state.
                proc, validation = self.preparation.at(
                    self.window[0]["state"], self.goals, len(self.window))
                inputs = {name: self.instance.env[name] for name in proc.inputs}
                # These are the window's entry values, not an invariant over
                # all its states. The unroller already derives each successor
                # plan from retained state equations, falling back to actual
                # signed carriers rather than narrowing unknown values.
                # A horizon-wide range pass both repeats that
                # work and loses the concrete first-step carrier.
                env = {**{name: ranges._state_product_range(value)
                          for name, value in proc.initial.items()}, **inputs}
                self.proc, self.env = proc, env
                self._prepared = residual._prepare_unroll(
                    proc, env, len(self.window), None, _procedure_validation=validation)
                self._construction = residual._RunConstruction(
                    previous=self.preparation.construction)
                self.preparation.construction = self._construction
            proc, env = self.proc, self.env
            if self.run is None:
                # Planning belongs to this worker's stage deadline, like the
                # ordinary local provider. A short probe may pause encoding,
                # but cannot repeatedly discard a completed carrier plan.
                self.run = residual.unroll(proc, env, len(self.window),
                                           goals=self.goals, _checkpoint=construction_check,
                                           _plan_checkpoint=lambda: None,
                                           _construction=self._construction,
                                           _prepared=self._prepared,
                                           _on_prefix=early_hit,
                                           _frame_initial=self.window[0].get("framing"),
                                           _input_values=_fixed_input_values(proc, self.window, mutable))
                if self.run is None:
                    return result(probing.Outcome.CONSTRUCTING,
                                  detail=f"input-prefix {phase} paused")
            construction_check()
            run, proc = self.run, self.proc
            fixed = _fixed_inputs(proc, self.env, self.window, run.encodings, mutable, construction_check)
            hits = {goal: run.goal_condition(goal) for goal in request.targets}
            solver = z3.Solver()
            solver.add(run.constraint, *fixed)
            solver.push()
            solver.add(z3.Or(*hits.values()))
            construction_check()
            phase = "solver"
            solver.set(timeout=max(1, int((deadline - time.monotonic()) * 1000)))
            answer = solver.check()
            if answer == z3.unsat:
                # A represented stop (e.g. finite loop unwinding) is not a
                # source failure. It cannot justify a negative source answer.
                # Reuse the same run and input restrictions, under the same
                # remaining budget, to distinguish that case.
                if proc.semantics.analysis_errors:
                    solver.pop()
                    solver.add(z3.Or(*(run.error == encoder._code(
                        code, proc.semantics.error_codes)
                        for code in proc.semantics.analysis_errors)))
                    check()
                    solver.set(timeout=max(1, int((deadline - time.monotonic()) * 1000)))
                    closure = solver.check()
                    if closure != z3.unsat:
                        return result(probing.Outcome.UNKNOWN,
                                      detail="input-prefix representation completion is unproved")
                return result(probing.Outcome.LOCAL_UNSAT,
                              detail=f"no requested hit in this {len(self.window)}-input prefix")
            if answer == z3.unknown:
                return result(probing.Outcome.UNKNOWN, detail=solver.reason_unknown())
            model = solver.model()
            goal = next(goal for goal, term in hits.items()
                        if z3.is_true(model.eval(term, model_completion=True)))
            values = _candidate_values(proc, self.window, run.encodings, model)
            return result(probing.Outcome.SAT, goal=goal, candidate=values,
                          detail=f"joint {len(self.window)}-input prefix; native replay required")
        except _PrefixWitness as witness:
            return result(probing.Outcome.SAT, goal=witness.goal, candidate=witness.candidate,
                          detail="joint input-prefix occurrence; native replay required")
        except encoder._EncodingPaused:
            return result(probing.Outcome.CONSTRUCTING,
                          detail=f"input-prefix {phase} paused")
        except TimeoutError as exc:
            return result(probing.Outcome.UNKNOWN, detail=str(exc))
        except (encoder.EncodingError, KeyError, TypeError, ValueError) as exc:
            return result(probing.Outcome.UNSUPPORTED,
                          detail=f"input-prefix encoding: {type(exc).__name__}: {exc}")
        finally:
            self._formula_checkpoint.callback = None


def _control_inputs(proc):
    """Identify input discriminators once when selecting a query family.

    Multiple literal equality arms identify a command selector without task
    names or port-width heuristics. This is a proposal ordering only: the
    original unrestricted query remains the fallback.
    """
    from behave.isolation.preparation_control import check_preparation

    values = {name: set() for name in proc.inputs}
    for term in ir.expression_nodes(proc.body):
        check_preparation()
        if not isinstance(term, ir.Op) or term.op not in ("eq", "ne") or len(term.args) != 2:
            continue
        left, right = term.args
        if isinstance(left, ir.Lit):
            left, right = right, left
        if isinstance(left, ir.Var) and left.name in values and isinstance(right, ir.Lit):
            values[left.name].add(right.value)
    controls = {name for name, cuts in values.items() if len(cuts) > 1}
    for role, binding in proc.value_bindings:
        if role == "input" and controls.intersection(binding.solver_variables):
            controls.add(binding.name)
    return controls


def _dispatch_partitions(assertions, inputs, check):
    """Split one input discriminator before nonlinear arithmetic is expanded.

    Literal comparisons in the actual query supply the cuts. Retain their
    complement, so no legal input disappears. The number of partitions grows
    with source comparisons, not with the input's bit width. Earlier inputs
    are preferred because their choices determine subsequent symbolic state.
    """
    import z3

    entries = {}
    for term, (lo, hi) in inputs:
        if z3.is_bv(term) and not z3.is_bv_value(term) and lo < hi:
            entries.setdefault(term.get_id(), (term, lo, hi, set()))
    pending, seen, nonlinear = list(assertions), set(), False
    while pending:
        check()
        term = pending.pop()
        identity = term.get_id()
        if identity in seen:
            continue
        seen.add(identity)
        args = term.children()
        nonlinear |= z3.is_app_of(term, z3.Z3_OP_BMUL) and all(
            not z3.is_bv_value(arg) for arg in args)
        if (z3.is_eq(term) or z3.is_distinct(term)) and len(args) == 2 \
                and any(z3.is_bv_value(arg) for arg in args):
            # Normalization can expose a boundary behind a sign extension.
            comparison = z3.simplify(term)
            if (z3.is_eq(comparison) or z3.is_distinct(comparison)) \
                    and comparison.num_args() == 2:
                left, right = comparison.children()
                if z3.is_bv_value(left):
                    left, right = right, left
                entry = entries.get(left.get_id())
                if entry is not None and z3.is_bv_value(right):
                    value = right.as_signed_long()
                    if entry[1] <= value <= entry[2]:
                        entry[3].add(value)
        pending.extend(args)
    if not nonlinear:
        return ()
    for term, lo, hi, values in entries.values():
        if not values:
            continue
        missing = hi - lo + 1 - len(values)
        if missing == 1:
            # Recover a singleton complement without enumerating the domain.
            values.add((lo + hi) * (hi - lo + 1) // 2 - sum(values))
        cuts = tuple(term == value for value in sorted(values))
        return cuts + ((z3.Not(z3.Or(*cuts)),) if missing > 1 else ())
    return ()


def _solve_prefix(assertions, inputs, deadline, check):
    """Solve a complete partition, retaining model conversion after elimination."""
    import z3

    cases = _dispatch_partitions(assertions, inputs, check)
    split = bool(cases)
    cases = cases or (z3.BoolVal(True),)
    unknown = False
    for index, case in enumerate(cases):
        check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return z3.unknown, None
        # A tactic-backed solver restores eliminated input/SSA symbols in its
        # model. Solving a detached transformed Goal would lose that mapping.
        solver = (z3.Then('simplify', 'propagate-values', 'solve-eqs', 'simplify', 'smt').solver()
                  if split else z3.Solver())
        solver.add(*assertions, case)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return z3.unknown, None
        solver.set(timeout=max(1, int(1000 * remaining / (len(cases) - index))))
        answer = solver.check()
        if answer == z3.sat:
            return answer, solver.model()
        unknown |= answer == z3.unknown
    return (z3.unknown if unknown else z3.unsat), None


def _fixed_input_values(proc, window, mutable):
    bindings = {binding.name: binding for role, binding in proc.value_bindings
                if role == "input"}
    rows = []
    for item in window:
        row = {}
        for name, field in item["bindings"]:
            if field.coordinate not in mutable:
                row.update(bindings[name].solver_values(item["input"][name])
                           if name in bindings else {name: item["input"][name]})
        # A preparation query may change command fields, but fixes its chosen
        # command path before construction. The public request still declares
        # those fields mutable relative to the original seed.
        row.update(item.get("prepared_inputs", {}))
        rows.append(row)
    return tuple(rows)


def _fixed_inputs(proc, env, window, encodings, mutable, check):
    kinds = dict(proc.semantics.input_types)
    fixed = []
    rows = _fixed_input_values(proc, window[:len(encodings)], mutable)
    for values, enc in zip(rows, encodings, strict=True):
        for physical, value in values.items():
            term = encoder.encode_concrete_value(value, env[physical], physical,
                                                 kind=kinds[physical])
            fixed.extend(_equal(enc.pre[physical], term))
        check()
    return fixed


def _candidate_values(proc, window, encodings, model):
    kinds, bounds = dict(proc.semantics.input_types), dict(proc.semantics.solver_input_bounds())
    values = [_logical_candidate(proc, {
        name: _model_value(model, enc.pre[name], kinds[name], bounds[name])
        for name in proc.inputs}) for enc in encodings]
    # An early witness changes only its encoded prefix. Queue length, framing
    # markers and every untouched suffix transaction remain the seed's own.
    return values + [dict(item["input"]) for item in window[len(encodings):]]


def _equal(left, right):
    """Recursive equality of already type-checked physical input leaves."""
    from ...symbolic.functional_array import FunctionalArray

    if isinstance(left, FunctionalArray):
        left = left.materialize()
    if isinstance(right, FunctionalArray):
        right = right.materialize()
    if isinstance(right, list):
        for a, b in zip(left, right, strict=True):
            yield from _equal(a, b)
    else:
        yield left == right
