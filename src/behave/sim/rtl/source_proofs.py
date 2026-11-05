"""Bounded speculative order proofs; only the audit consumes results in order.

Each active group owns a private Z3 context. The caller's solve function still
owns the deadline, cancellation and physical-query receipts. No solver policy
or timeout is selected here; completed later groups are not admission evidence.
"""

from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from contextlib import ExitStack, contextmanager
from contextvars import copy_context
from threading import Event

import z3

from behave.core import runtime_defaults as defaults

from ..symbolic.exact_solve import ExactCheckResult, SolverPreparation
from ..core.query import SolverAnswer
from ..core.resources import available_cpus
from ..cpu_admission import cpu_cleanup_unconfirmed, cpu_work_width, reserve_cpus
from .source_confluence import _proof_shape, _replica_coordinates
from .symbolic_state import assertion_conjunction


def _source_prefix(label):
    return label.endswith(':prefix') and label.startswith(('confluence:startup:', 'confluence:slot['))


@contextmanager
def order_proof_scope(comparison, solve, check):
    """Speculate within the admitted width; join workers before releasing it."""
    width = cpu_work_width(available_cpus())
    if width == 1:
        yield solve
        return
    with ExitStack() as resources:
        runner = _OrderProofs(comparison, solve, check, width, resources)
        try:
            yield runner
        finally:
            runner.close()


class _OrderProofs:
    def __init__(self, comparison, solve, check, width, resources):
        self.solve, self.check = solve, check
        self.width, self.resources = width, resources
        self.source = comparison.left.alive.ctx
        self.jobs = comparison.order_queries(prefix_proofs=True)
        self.pending, self.pool = deque(), None
        self.prefetched = {}
        self.idle, self.preparations = deque(), {}
        self.local_source = comparison._local_source_steps
        self.proved_prefixes, self.prefix_shapes, self.reused_prefixes = OrderedDict(), {}, set()
        self.stopped = Event()

    def _warm_prefix(self, label):
        # Boundary-cut formulas are independent transitions, not a growing
        # history. Keep the caller's ordinary solver choice for those queries.
        return _source_prefix(label) and not self.local_source

    def _physical(self, context, jobs):
        answers = {}
        for label, roots in jobs:
            if self.stopped.is_set():
                break
            self.check()
            preparation = None
            # Reuse only source-prefix preparation. Monitor cuts and full
            # fallback queries retain their original independent solve path.
            # The packed conjunction's last argument is the varying defect;
            # every other argument is an explicit premise in this very query.
            if self._warm_prefix(label):
                packed = roots[0]
                # A sufficient prefix may already be one packed conjunction.
                # Translation wraps it again; unwrap only unary conjunctions
                # so its actual base remains eligible for owned preparation.
                # A literal/atom (especially False) is still an assertion.
                while z3.is_and(packed) and packed.num_args() == 1:
                    self.check()
                    packed = packed.arg(0)
                roots = tuple(packed.children()) if z3.is_and(packed) else (packed,)
                base = roots[:-1]
                if base:
                    preparation = self.preparations.get(context)
                    if preparation is None:
                        preparation = self.preparations[context] = SolverPreparation(base)
                    else:
                        preparation.extend(base)
            answer = self.solve(label, roots, context=context,
                                **({} if preparation is None else {'preparation': preparation}))
            answers[label] = answer
            if type(answer) is not ExactCheckResult:
                raise TypeError('source proof requires the exact solver result type')
            if label.endswith(':prefix') and answer.answer is not SolverAnswer.SAT:
                break
        return answers

    def _fill(self):
        while len(self.pending) < self.width:
            self.check()
            item = next(self.jobs, None)
            if item is None:
                break
            phase, jobs = item
            label, roots = jobs[0]
            if self._warm_prefix(label):
                context = self.idle.popleft() if self.idle else z3.Context()
            else:
                # Only growing source prefixes benefit from warm solvers.
                # Retire consumed source lanes before the protocol/monitor
                # work, and give each other group its own context lifetime:
                # finite-solver arenas can otherwise accumulate across groups.
                # Active lanes in pending are exclusively worker-owned.
                while self.idle:
                    self.preparations.pop(self.idle.popleft(), None)
                context = z3.Context()
            # A prefix UNSAT already proves the full obligation. Do not copy
            # every later slot into every private context just in case the
            # relaxed prefix is SAT; that fallback is translated only on demand.
            packed = (assertion_conjunction(roots, check=self.check)
                      if self.local_source and _source_prefix(label) else None)
            shape = None if packed is None else _proof_shape(packed, check=self.check)
            if packed is not None:
                self.prefix_shapes[label] = shape
            old = self.proved_prefixes.get(shape)
            if old is not None and _replica_coordinates(((old, packed),),
                    check=self.check, shared_functions=True, independent=False) is not None:
                # All premises and the defect must be alpha-isomorphic. Only
                # consumed UNSAT proofs are stored; SAT models, UNKNOWN and
                # speculative futures never supply a reusable answer.
                bound = jobs[:1]
                future = Future()
                future.set_result({label: ExactCheckResult(SolverAnswer.UNSAT)})
                self.reused_prefixes.add(label)
                self.proved_prefixes.move_to_end(shape)
            else:
                bound = self._translate(context, jobs[:1])
                self.check()
                future = self.pool.submit(copy_context().run, self._physical, context, bound)
            # Keep every translated AST on the owner as well. A Future becomes
            # done before its worker drops the call arguments; without this
            # reference those AST destructors can race with model translation
            # in the same Z3 context on the owner thread.
            self.pending.append((phase, jobs, context, bound, future))

    def _translate(self, context, jobs):
        # Only the owner touches the source context. Keep the translated roots
        # alive in pending until the corresponding worker/model is consumed.
        # Each query asserts the conjunction of ALL its roots. Transfer that
        # one DAG instead of creating Python wrappers for every historical
        # root in every private context. This is only packaging: no slicing,
        # simplification, learned premise or solver answer is introduced.
        bound = []
        for label, roots in jobs:
            packed = (assertion_conjunction(roots, check=self.check) if roots
                      else z3.BoolVal(True, ctx=self.source))
            self.check()
            bound.append((label, (packed.translate(context),)))
            self.check()
        return bound

    def _prefetch_fallbacks(self):
        """Use a finished SAT-prefix lane while an earlier proof is pending.

        These are still conditional, unconsumed results. Keep both packets
        and the prefix model alive until the new worker exits; the owner must
        not translate that model while its private context is solving again.
        No new lane, CPU reservation or extra proof window is introduced.
        """
        for index in range(1, len(self.pending)):
            self.check()
            group, jobs, context, bound, future = self.pending[index]
            if (group in self.prefetched or len(jobs) != 2 or len(bound) != 1
                    or not future.done() or future.cancelled() or future.exception() is not None):
                continue
            answers = future.result()
            if answers[jobs[0][0]].answer is not SolverAnswer.SAT:
                continue
            full = self._translate(context, (jobs[1],))
            self.check()
            # Only this owner touches the source context. The previous lane
            # has finished before translating or starting its full recheck.
            self.prefetched[group] = answers
            next_future = self.pool.submit(copy_context().run, self._physical, context, full)
            self.pending[index] = (group, jobs, context, (*bound, *full), next_future)

    def __call__(self, phase, roots):
        if not phase.startswith('confluence:'):
            return self.solve(phase, roots)
        if self.pool is None:
            self.width = self.resources.enter_context(reserve_cpus(self.width))
            self.pool = ThreadPoolExecutor(max_workers=self.width, thread_name_prefix='rtl-order-proof')
        self._fill()
        group, jobs, context, _bound, future = self.pending[0]
        if phase.removesuffix(':prefix') != group:
            raise ValueError('conditional order proofs must be consumed in history order')
        planned = dict(jobs)[phase]
        if len(planned) != len(roots) or any(not a.eq(b) for a, b in zip(planned, roots)):
            raise ValueError('conditional proof assertions differ from the audited obligation')
        if phase not in dict(_bound):
            # Ordered audit requests a full-domain recheck only after consuming
            # prefix SAT. That completed worker is no longer using this context;
            # _bound still owns its ASTs while the fallback is prepared.
            previous = future.result()
            if (len(jobs) != 2 or phase != jobs[1][0]
                    or previous[jobs[0][0]].answer is not SolverAnswer.SAT):
                raise ValueError('full order proof requires a consumed SAT prefix')
            bound = self._translate(context, (jobs[1],))
            self.check()
            future = self.pool.submit(copy_context().run, self._physical, context, bound)
            self.pending[0] = (group, jobs, context, bound, future)
        while True:
            self.check()
            self._prefetch_fallbacks()
            try:
                answers = future.result(timeout=defaults.ISOLATION_COMPLETION_POLL_SECONDS)
                break
            except TimeoutError:
                if future.done():
                    raise
        self.check()
        answers = {**self.prefetched.get(group, {}), **answers}
        answer = answers[phase]
        model = None if answer.model is None else answer.model.translate(self.source)
        if self.local_source and _source_prefix(phase):
            shape = self.prefix_shapes.pop(phase, None)
            if (answer.answer is SolverAnswer.UNSAT and shape is not None
                    and phase not in self.reused_prefixes):
                # One candidate per coarse shape: a collision triggers a real
                # solve, never a scan over an ever-growing history of proofs.
                self.proved_prefixes[shape] = assertion_conjunction(planned, check=self.check)
                self.proved_prefixes.move_to_end(shape)
                while len(self.proved_prefixes) > defaults.SIM_ANALYSIS_ANSWER_CACHE_ENTRIES:
                    self.proved_prefixes.popitem(last=False)
            self.reused_prefixes.discard(phase)
        if not phase.endswith(':prefix') or answer.answer is not SolverAnswer.SAT:
            self.pending.popleft()
            self.prefetched.pop(group, None)
            if self._warm_prefix(jobs[0][0]):
                self.idle.append(context)
        return ExactCheckResult(answer.answer, model=model, reason=answer.reason,
            solver_load_ns=answer.solver_load_ns, solve_ns=answer.solve_ns,
            unknown_cause=answer.unknown_cause)

    def close(self):
        if self.pool is None:
            return
        self.stopped.set()
        try:
            self.pool.shutdown(wait=False, cancel_futures=True)
            # Repeat interrupts across races with solver creation. Contexts
            # remain alive until all their workers have exited.
            while True:
                running = [(context, future) for _, _, context, _, future in self.pending
                           if not future.done()]
                if not running:
                    break
                for context, _ in running:
                    context.interrupt()
                Event().wait(defaults.ISOLATION_COMPLETION_POLL_SECONDS)
            self.pool.shutdown(wait=True)
            self.pending.clear()
            self.prefetched.clear()
            self.idle.clear()
            self.preparations.clear()
            self.proved_prefixes.clear()
            self.prefix_shapes.clear()
            self.reused_prefixes.clear()
        except BaseException:
            cpu_cleanup_unconfirmed()
            raise
