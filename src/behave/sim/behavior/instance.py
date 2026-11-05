"""The behavior side as one artifact the closure procedure can work on.

Everything specific to a Python behavior model is behind this: the lowering,
the two readings of the representation, the encoder, and the catalogue of
goals the policy declares. What is on the other side of it is the procedure,
which knows only that an artifact has goals, can be run, can be asked, and can
be audited.

The RTL side implements the same five obligations with a cocotb replay and a
bounded model checker, and nothing in the procedure changes.
"""

from __future__ import annotations

from behave.sim.core.source_rules import EMPTY_SOURCE_RULES

from ..core.audit_plan import AuditPlan
from ..core.conditional_evidence import TrustedAssumption
from ..core.instance import Feasible, Replay, Verdict
from . import audit as audit_mod
from ..symbolic import cfg, ir, ssa, typecheck as types
from . import residual
from ..symbolic import interp
from ..symbolic import ranges
from . import goals as goals_mod
from ..symbolic.validation import EncodingError
from .value_records import domain_tree, record_tree

#: Shared catalogue policy; certificates bind the goal denominator it defines.
POLICY = f"{goals_mod.POLICY_VERSION}/{goals_mod.POLICY}"

#: Which frontend produced the artifact, and its version. A frontend that
#: changes what it lowers changes what is reachable, so numbers from either
#: side of such a change are not numbers about the same thing.
FRONTEND = "behavior/182"

__all__ = ["BehaviorInstance", "feasibility", "lower"]


def _record_states(states):
    return [record_tree(state) for state in states]


class _Where:
    """A position, for a refusal that is about the source rather than a node."""

    __slots__ = ("col_offset", "lineno")

    def __init__(self, lineno, col_offset=0):
        self.lineno = lineno
        self.col_offset = col_offset


class BehaviorInstance:
    """A lowered behavior model, its ranges, and the source they came from."""

    def __init__(self, proc, env, source=None, file="behavior.py",
                 schema=None, bound=None, *, _position_plan=None,
                 _frontend_catalogue=None, _catalogue=None,
                 artifact_identity=None, memory_interfaces=(),
                 memory_environment=None, memory_horizon=None, class_name=None,
                 transaction_goals=None, source_rules=EMPTY_SOURCE_RULES):
        self.proc = proc
        self.env = dict(env)
        #: How many steps this instance's ranges were computed to cover, or `None`
        #: where they came from the fixpoint and cover any number.
        #:
        #: Recorded because it is a premise. A range from `ranges.bounded(N)` covers
        #: runs of N steps and says nothing about step N + 1, so binding this to a
        #: domain of a different bound would make every width, and therefore every
        #: unreachable verdict, a statement about a trace set the analysis was not
        #: about. `bind` refuses it, which is the same fault B12 exists to prevent,
        #: one stage earlier.
        self.bound = bound
        self.source = source
        self.file = file
        self.class_name = class_name
        self.transaction_goals = transaction_goals
        self.source_rules = source_rules
        if artifact_identity is not None and (
                type(artifact_identity) is not str or not artifact_identity):
            raise TypeError("a Behavior artifact identity is absent or nonempty text")
        self.artifact_identity = artifact_identity
        from ..core.memory import MemoryEnvironment, MemoryInterface

        memory_interfaces = tuple(memory_interfaces)
        if any(type(memory) is not MemoryInterface
               for memory in memory_interfaces):
            raise TypeError(
                "a Behavior instance's memories are MemoryInterface records")
        if memory_environment is not None \
                and type(memory_environment) is not MemoryEnvironment:
            raise TypeError(
                "a Behavior instance's memory environment is a MemoryEnvironment")
        if bool(memory_interfaces) != (memory_environment is not None):
            raise ValueError(
                "a Behavior instance binds declared memories and their environment "
                "together")
        if memory_environment is None:
            if memory_horizon is not None:
                raise ValueError(
                    "a memoryless Behavior instance has no memory schedule horizon")
        elif type(memory_horizon) is not int or memory_horizon < 1:
            raise TypeError(
                "a Behavior instance with external memory needs a positive memory "
                "schedule horizon")
        self.memory_interfaces = memory_interfaces
        self.memory_environment = memory_environment
        self.memory_horizon = memory_horizon
        # What the endpoints carry. A schema declared by the task is what a
        # certificate should rest on; one recovered from a candidate is a
        # reading of the artifact under test, and this says which it had.
        self.schema = schema
        if _position_plan is not None:
            from ..symbolic.closure_proof import _PreparedPositionPlan

            if type(_position_plan) is not _PreparedPositionPlan:
                raise TypeError(
                    "a Behavior instance accepts only an internally prepared "
                    "position plan")
            _position_plan.checked_for(proc, self.env, bound)
        self._position_plan = _position_plan
        # Lazily populated by ``audit``.  It contains only the symbolic steps
        # for this sealed position plan; audit results are never cached.
        self._prepared_step_audit = None
        self._prepared_transaction_audit = None
        from ..core.catalogue import CatalogueManifest
        from .catalogue import frontend_catalogue

        if _frontend_catalogue is None:
            _frontend_catalogue = frontend_catalogue(
                proc, source, file, limits=self.analysis_limits(), cls=class_name,
                source_rules=source_rules)
        elif type(_frontend_catalogue) is not CatalogueManifest:
            raise TypeError(
                "a Behavior instance's frontend catalogue is a CatalogueManifest")
        if _catalogue is None:
            _catalogue = _frontend_catalogue
            if transaction_goals is not None:
                from ..core.catalogue import merge_catalogues

                _catalogue = merge_catalogues(_catalogue, transaction_goals.catalogue)
        elif type(_catalogue) is not CatalogueManifest:
            raise TypeError(
                "a Behavior instance's analysis catalogue is a CatalogueManifest")
        self._frontend_catalogue = _frontend_catalogue
        self._catalogue = _catalogue

    def __setattr__(self, name, value):
        if name in {"_position_plan", "_frontend_catalogue", "_catalogue"} \
                and name in self.__dict__:
            raise AttributeError(f"a Behavior instance's {name[1:]} is write-once")
        object.__setattr__(self, name, value)

    # ── what may be reported ──

    def goals(self):
        """The already-frozen manifest, never a new walk of source or IR."""
        return self._catalogue

    def frontend_catalogue_manifest(self):
        """The exact source/IR declaration frozen before generation."""

        return self._frontend_catalogue

    def catalogue_manifest(self):
        """The complete manifest this instance encodes at its current boundary."""

        return self._catalogue

    def analysis_limits(self):
        """Goals that report a limit of this analysis rather than the artifact's.

        These are explicit terminal commands retained by a hand-built or legacy
        procedure, such as a sequence grown past its proved capacity.  Admitted
        source ``while`` loops do not contribute one: lowering proves an exact
        finite trip expression and refuses the procedure before construction
        when its plan is unavailable or exceeds the reported capability.

        Kept out of the catalogue rather than given a class of their own. Three
        outcomes are already distinguishable without touching the goal lattice: the
        artifact completed or halted with a code it models, the analysis was supported
        or hit a limit, and the infrastructure worked or did not. Only the first
        belongs in a goal, and adding a third class would have put an analysis
        property into the lattice that every frontend and the closure procedure read.
        """
        from ..symbolic.validation import bind_procedure_contract

        semantics = bind_procedure_contract(self.proc)
        return {c.goal for c in ir.walk(self.proc.body)
                if isinstance(c, ir.Fail) and c.goal
                and c.code in semantics.analysis_errors}

    def ruled_out(self):
        """Goals the construct rules out, with the reason it does."""
        return {**self.proc.impossible, **self.proc.discharged}

    # ── running ──

    def _run_trace(self, trace, state=None):
        previous = {}
        observer = (None if self.transaction_goals is None else
                    lambda inputs, outputs: self.transaction_goals.observe(previous, inputs, outputs))
        return interp.run_trace_detailed(self.proc, trace, state, observe_step=observer)

    def replay(self, trace, state=None):
        """Run a trace and report what it reached, and whether it ran at all.

        A halt is the artifact's outcome and goes in ``error``. An exception
        from this package is this side failing to carry the replay out and goes
        in ``broken``: a trace that names an input the step does not have, or a
        cell index the representation cannot hold, is not the model failing.

        An analysis limit goes in ``broken`` too, and that is what makes it a limit
        rather than a failure. A trace that outran the `while` bound is a trace this
        side stopped following, so what the model does past that point is unknown and
        the trace is not evidence about it. It used to go in ``error``, which made it
        the artifact's outcome: the goal it reached was in the safety class, so a model
        this side declined to unroll far enough was reported as a design with a defect.
        Reported this way, `close_over` declines to take it as evidence and `certify`
        records that not every trace of the pool was replayed, both of which already
        do the right thing with a trace nobody could carry out.
        """
        try:
            states, outputs, hit_sets, error = self._run_trace(trace, state)
        except Exception as e:                                  # noqa: BLE001
            return Replay((), (), broken=f"{type(e).__name__}: {e}",
                          steps=len(trace))
        try:
            final_state = record_tree(states[-1])
        except Exception as e:                                  # noqa: BLE001
            return Replay(
                (), (), broken=f"the replay state has no finite image: "
                f"{type(e).__name__}: {e}", steps=len(trace))
        hits = set().union(*hit_sets)
        if error in self.proc.semantics.analysis_errors:
            return Replay(hits - self.analysis_limits(), outputs, "", final_state,
                          broken=f"the analysis stopped following the model: "
                                 f"{error}", steps=len(trace))
        return Replay(hits, outputs, error, final_state, steps=len(trace))

    def replay_witness(self, trace, domain):
        """Replay ``trace`` once and retain its complete finite witness.

        ``replay`` deliberately returns the aggregate view used by goal closure.  The
        concrete-record boundary also needs the information that view discards: the
        state before and after every step and the goals hit by that particular step.
        Both views are derived from one interpreter execution here, so the witness is
        not reconstructed from a second run that might disagree with the one audited.
        """
        from ..core.audit import AuditedWitness
        try:
            states, outputs, hit_sets, error = self._run_trace(trace)
        except Exception as exc:                               # noqa: BLE001
            return (Replay((), (), broken=f"{type(exc).__name__}: {exc}",
                           steps=len(trace)), None)
        try:
            record_states = _record_states(states)
        except Exception as exc:                               # noqa: BLE001
            return (
                Replay(
                    (), (), broken=f"the replay states have no finite image: "
                    f"{type(exc).__name__}: {exc}", steps=len(trace)),
                None,
            )
        hits = set().union(*hit_sets)
        if error in self.proc.semantics.analysis_errors:
            return (
                Replay(hits - self.analysis_limits(), outputs, "",
                       record_states[-1],
                       broken=f"the analysis stopped following the model: {error}",
                       steps=len(trace)),
                None,
            )
        replayed = Replay(
            hits, outputs, error, record_states[-1], steps=len(trace))
        try:
            witness = AuditedWitness(
                domain=domain,
                states=record_states,
                inputs=trace,
                outputs=[step.transactions for step in outputs],
                hit_sets=hit_sets,
                terminal_error=error)
        except (TypeError, ValueError) as exc:
            return (
                Replay((), (), broken=f"the replay has no finite witness image: {exc}",
                       steps=len(trace)),
                None,
            )
        return replayed, witness

    def audit_requests(self, traces, domain):
        """Audit decoded SAT requests together without weakening either reading.

        Replay still constructs one complete candidate per request.  The symbolic and
        source readings accept trace batches, so running each one once avoids rebuilding
        an identical solver and source-isolation service for every solver witness.
        """
        from ..core.audit import AuditCheck, AuditCheckStatus

        traces = tuple(traces)
        results = [None] * len(traces)
        pending = []
        for index, trace in enumerate(traces):
            replayed, candidate = self.replay_witness(trace, domain)
            if replayed.broken or candidate is None:
                results[index] = AuditCheck.refused(
                    "behavior-witness-replay-failed",
                    "the decoded solver request could not be replayed completely: "
                    f"{replayed.broken or 'no complete witness was produced'}")
            else:
                pending.append((index, trace, candidate))
        if not pending:
            return tuple(results)

        pending_traces = tuple(trace for _index, trace, _candidate in pending)
        for check in (self.audit(pending_traces),
                      self.source_audit(pending_traces)):
            if type(check) is not AuditCheck:
                check = AuditCheck.refused(
                    "behavior-witness-audit-result-invalid",
                    "a Behavior witness audit returned something outside the typed "
                    "audit algebra")
            if check.status is not AuditCheckStatus.AGREED:
                for index, _trace, _candidate in pending:
                    results[index] = check
                return tuple(results)
        for index, _trace, candidate in pending:
            results[index] = candidate
        return tuple(results)

    def audit_request(self, trace, domain):
        """Turn one decoded SAT request into a complete audited witness."""
        return self.audit_requests((trace,), domain)[0]

    # ── asking ──

    def query_with_feedback(self, goals, steps, *, feedback, **kw):
        """Keep preparation alive while core replays requests for scheduling."""
        from .query_progress import QueryProgress

        return self.query(goals, steps, _progress=QueryProgress(feedback), **kw)

    def query(self, goals, steps, timeout_ms=None, **kw):
        """The verdict on ``goals`` over runs of ``steps`` steps.

        **``steps`` may not exceed the bound the ranges cover, and refusing that is
        a soundness obligation rather than tidiness.** `ranges.bounded(N)` gives
        R(0) = Init and R(t+1) = post#(R(t)) for t < N, so it covers runs of N steps
        and says nothing about step N + 1. The widths follow from it, and a width
        that is too narrow does not fail: the formula wraps where the program does
        not, and a goal the program reaches becomes unsatisfiable.

        Demonstrated on `self.x = self.x + 1` with a goal at `x == 5`. Lowered at a
        bound of 1 the range is [0,1], two bits, and `query(steps=20)` returned
        **unreachable**; lowered at 20 the range is [0,20], six bits, and the same
        query returned reachable. `bind` had this check and `query` did not, so the
        entry point that produces verdicts was the one without it.

        An unbound diagnostic instance may still ask a shorter question because its
        joined ranges cover that prefix.  Once :meth:`bind` fixes the exact analysis
        domain, however, its prepared position plan has one horizon and every formal
        query uses that horizon.
        """
        if self.bound is not None and steps > self.bound:
            raise ValueError(
                f"ranges were computed to cover {self.bound} steps and this query "
                f"is about {steps}: the widths would be too narrow and the "
                f"formula unsatisfiable in places the model reaches. Lower the "
                f"model at {steps} instead")
        if self._position_plan is not None:
            self._position_plan.checked_for(self.proc, self.env, steps)
            if "plan" in kw:
                raise TypeError(
                    "a bound Behavior query cannot replace its position plan")
            kw["plan"] = self._position_plan
        requested = tuple(str(goal) for goal in goals)
        from ..core.refusal import Refusal, Refused

        if len(set(requested)) != len(requested):
            duplicates = sorted(
                goal for goal in set(requested) if requested.count(goal) > 1)
            raise Refused(Refusal(
                "behavior-analysis-goal-duplicate",
                "one bounded query names the same public goal more than once",
                goals=duplicates))
        unknown = sorted(set(requested) - set(self._catalogue))
        if unknown:
            raise Refused(Refusal(
                "behavior-analysis-goal-outside-catalogue",
                "a bounded Behavior query names goals outside the manifest frozen "
                "before generation",
                goals=unknown, catalogue=self._catalogue.digest()))
        frontend = set(self._frontend_catalogue)
        external = tuple(goal for goal in requested if goal not in frontend)
        unsupported = set(external) - (
            self.transaction_goals.catalogue.keys() if self.transaction_goals is not None else set())
        if unsupported:
            raise Refused(Refusal(
                "behavior-analysis-monitor-state-unavailable",
                "the Behavior step encoding does not contain the task adapter "
                "monitor state needed to query these trace-level goals",
                goals=sorted(unsupported)))
        if requested:
            decided = residual.decide_goals(
                self.proc, self.env, requested, steps=steps,
                transactions=self.transaction_goals if external else None,
                **({"timeout_ms": timeout_ms} if timeout_ms else {}), **kw)
            reachable = decided.reachable
            unreachable = decided.unreachable
            undecided = decided.undecided
            transcript = list(decided.queries or ())
        else:
            reachable, unreachable, undecided, transcript = {}, set(), {}, []
        return Verdict(
            reachable, unreachable, undecided, queries=tuple(transcript))

    def feasible(self, steps, timeout_ms=None):
        """Whether the domain at this bound is inhabited.

        Guarded like `query`, and for the same reason: an unsatisfiable answer here
        says the trace set is **empty**, which is as strong a claim as
        unreachability and is wrong in the same way if the widths were computed for
        a shorter run.  A bound instance also consumes the same exact position plan
        as its goal queries and evidence identity.
        """
        if self.bound is not None and steps > self.bound:
            raise ValueError(
                f"ranges were computed to cover {self.bound} steps and this asks "
                f"about {steps}: an empty verdict would be an artefact of the "
                f"widths. Lower the model at {steps} instead")
        plan = self._position_plan
        if plan is not None:
            plan.checked_for(self.proc, self.env, steps)
        return feasibility(
            self.proc, self.env, steps, timeout_ms=timeout_ms, plan=plan)

    # ── the two readings against each other ──

    def audit(self, traces):
        from ..core.audit import AuditCheck
        from ..core.refusal import Refused

        if not traces:
            return AuditCheck.refused(
                "behavior-step-audit-no-traces",
                "the Behavior step audit was given no trace to compare")
        try:
            prepared = self._prepared_step_audit
            required = max((len(trace) for trace in traces), default=0)
            if self._position_plan is not None and (
                    prepared is None or len(prepared.encodings) < required):
                prepared = audit_mod._prepare_position_audit(
                    self.proc, self.env, self._position_plan,
                    required=required, prepared=prepared)
                self._prepared_step_audit = prepared
            wrong = audit_mod.step_audit(
                self.proc, self.env, traces,
                position_plan=self._position_plan,
                _prepared_position=prepared)
            if not wrong and self.transaction_goals is not None:
                wrong = self.transaction_goals.audit_history(self, traces)
        except Refused as exc:
            return AuditCheck.refused(exc.refusal.kind, exc.refusal.why)
        except Exception as exc:                              # noqa: BLE001
            return AuditCheck.refused(
                "behavior-step-audit-failed",
                f"the Behavior step audit could not run: "
                f"{type(exc).__name__}: {exc}")
        return (AuditCheck.inconsistent([str(item) for item in wrong]) if wrong
                else AuditCheck.agreed())

    def audit_against_source(self, model_cls, traces, hits_of=None):
        """The representation against the model the host runs.

        The reading from outside. The other two were written here and share
        whatever this package misunderstands about the source.
        """
        return audit_mod.model_audit(self.proc, model_cls, traces, hits_of)

    def audit_plan(self):
        """Every declarable audit, because this frontend has what each of them needs.

        M5 lets a frontend say which audits its claims require, so that an RTL instance
        with no source to compare against is not held to one. This frontend is the case
        that has all three: it declares a goal catalogue to reconcile against, it holds
        two readings of the step to compare, and it was built from a source it can run.

        Stated rather than defaulted, and `source_audit` is the reason. It is the only
        check in reach that compares the artifact against something *not written to
        match it*, and it found every fault of the last two rounds while being no part
        of any certificate. A frontend that can run it and does not require it would be
        certifying that its two own readings agree with each other.
        """
        return AuditPlan.all()

    def trusted_assumptions(self):
        """What a certificate from this frontend rests on and no audit discharges.

        B14. Passing on finitely many traces does not establish that a translation is
        exact on every input, so these are assumptions an audit can **refute** and
        cannot establish. Listing them is the difference between a conditional
        certificate and one written as if it were unconditional.
        """
        return (
            TrustedAssumption(
                "source-to-ir-fidelity",
                "on every admitted step, lowering preserves the source successor "
                "state, ordered output transactions, emitted goal identifiers and "
                "terminal error; finite source audits can refute but cannot "
                "establish this universal claim",
                scope="frontend-semantics"),
            TrustedAssumption(
                "behavior-representation-plan-validity",
                "the position-specific state and internal carriers contain every "
                "value reached in the bounded domain, and each symbolic primitive "
                "is exact on those carriers; boundary closure queries check the "
                "resulting state and output layers but do not prove these universal "
                "internal premises",
                scope="finite-representation"),
            TrustedAssumption(
                "behavior-domain-replay-fidelity",
                "the task/source-to-IR maps preserve the admitted initial states, "
                "inputs and finite trace predicate, so replay and the symbolic "
                "query range over the same bounded trace domain",
                scope="trace-domain"),
            TrustedAssumption(
                "solver",
                "the solver answers the encoded bounded formula correctly, including "
                "that UNSAT excludes every trace in the bound domain",
                scope="bounded-solver"),
            TrustedAssumption(
                "python-semantics-of-the-host",
                "the isolated CPython execution is the intended source semantics",
                scope="source-semantics"),
        )

    def source_audit(self, traces, *, reset_indices=None, observe_step=None):
        """Compare source and IR runs through the isolated native boundary.

        A semantic disagreement is ``INCONSISTENT``. An unavailable or interrupted
        source execution is ``REFUSED``, not evidence against the translation.
        ``observe_step`` receives this audit's IR observations, usable only when
        the returned check is ``AGREED``; it cannot supply or replace evidence.
        """
        from ..core.audit import AuditCheck

        if not traces or self.source is None:
            return AuditCheck.refused(
                "behavior-source-audit-unavailable",
                "the Behavior source audit needs a source and at least one trace")
        from .extract import source_model
        from .isolated import isolated_model

        # The audit executes the exact source-selected class in a fresh process
        # per trace, under the declared source-audit resource envelope.
        try:
            from .budgets import source_audit_limits

            make = isolated_model(
                self.source, self.file,
                cls=source_model(self.source, self.file, cls=self.class_name).cls.name,
                limits=source_audit_limits(), source_rules=self.source_rules)
        except Exception as e:                                  # noqa: BLE001
            return AuditCheck.refused(
                "behavior-source-audit-load-failed",
                f"the host could not run the model: {type(e).__name__}: {e}")
        try:
            # This live hit view follows the fresh child selected for each trace.
            if self.memory_environment is None:
                model_factory = make
            else:
                def model_factory():
                    model = make()
                    model._bind_external_memory(
                        self.memory_interfaces, self.memory_environment,
                        self.memory_horizon, representation="reference")
                    return model

            wrong = audit_mod.model_audit(
                self.proc, model_factory, traces, make.hits,
                reset_indices=reset_indices,
                external_fields=tuple(memory.name for memory in self.memory_interfaces),
                observe_step=observe_step)
        except Exception as e:                                  # noqa: BLE001
            return AuditCheck.refused(
                "behavior-source-audit-run-failed",
                f"the host could not be run under isolation: "
                f"{type(e).__name__}: {e}")
        finally:
            make.close()
        findings = [str(w) for w in wrong]
        return (AuditCheck.inconsistent(findings) if findings
                else AuditCheck.agreed())

    # ── shape ──

    def well_formed(self):
        """Reasons the artifact is not in the shape the rest assumes.

        Two questions, and they are answered by different things because they
        are different questions. Whether the commands are ones the encoder has
        rules for is about the term, and `normalized` reads the term. Whether a
        use has a definition reaching it is about control flow, and control
        flow is a graph: on the graph it is one sweep in topological order,
        exact, with no intervals and no per-construct cases.

        The term-level version of the second question is still there and still
        agrees with this one on the population. It is not what the certificate
        asks, because "exact" and "agrees with the exact one today" are not the
        same guarantee.

        A third question, and the one the appendix's "well-formed procedures
        denote functions" was missing outright: whether each variable has a type.
        It was being answered by the encoder, from inside a formula, by whether
        the Python object it had built was a list, and only where two of them met.
        So a model with a type error was refused after this method had called it
        well-formed, with no site, and a type error in a local was not refused at
        all until the values happened to meet. It is a question about the program
        and it is asked here.
        """
        out = list(ir.normalized(self.proc, self.env))
        try:
            graph = self.proc.graph
        except ir.RepresentationStructureError as exc:
            # The graph/body seal is an admission fact, not an exception a
            # certificate caller should have to interpret.  Keep the direct
            # property strict for code that explicitly audits the authority,
            # but expose a changed authority here as the well-formedness issue
            # it is.  The certificate can then fail closed while still running
            # independent source/step audits and recording their findings.
            return out + [(
                f"the representation structure is inconsistent ({exc.code}): "
                f"{exc}")]
        if graph is None:
            # Nothing built one, so it is read back out of the term. That is a
            # reading of the flattening rather than of the program, and it is
            # the fallback for exactly that reason.
            try:
                graph = cfg.of_command(self.proc.body)
            except Exception as e:                              # noqa: BLE001
                return out + [(f"the control flow could not be read: "
                               f"{type(e).__name__}: {e}")]
        start = set(self.proc.state) | set(self.proc.inputs)
        # And whether the intervals the widths come from survive one more step. The
        # fixpoint drops what it cannot prove, so this is empty for an instance built
        # the ordinary way; it is asked here because a caller may assemble `env`
        # itself, and an interval a run leaves is the one defect on this list that
        # makes an unsatisfiable answer mean nothing rather than making the step
        # unreadable. Silent is what it was: the width was taken and the query
        # answered.
        boundary = []
        if self.schema is not None:
            from ..symbolic.procedure_contract import ProcedureSignature

            expected = ProcedureSignature.from_schema(
                self.schema, input_names=self.proc.inputs,
                output_names=self.proc.outputs)
            if self.proc.signature is None:
                boundary.append(
                    "the procedure has no task-owned input/output signature")
            elif self.proc.signature != expected:
                boundary.append(
                    "the procedure's input/output signature differs from the task")
        if self.proc.signature is not None:
            from ..symbolic.procedure_contract import OutputContractError
            from .signature import output_contract_evidence

            try:
                evidence = output_contract_evidence(
                    self.proc, self.env, bound=self.bound)
            except OutputContractError as exc:
                boundary.append(f"the output contract is unproved: {exc}")
            else:
                if evidence != self.proc.output_ranges:
                    boundary.append(
                        "the stored output-range evidence does not describe "
                        "this procedure")
        if self.bound is None:
            range_boundary = [
                f"the interval for {name} does not survive one more step, so "
                f"its width cannot carry the states a run reaches"
                for name in ranges.unproved(self.proc, self.env)]
        elif getattr(self.proc, "state_range_bound", None) != self.bound:
            range_boundary = [(
                "the procedure's state ranges were not derived for this "
                f"finite bound ({getattr(self.proc, 'state_range_bound', None)} "
                f"against {self.bound})")]
        else:
            # A bounded environment is the join of R(0)..R(N).  It need not be
            # inductive beyond N; requiring post#(I) subset I here rejects
            # exactly the growing-state programs bounded analysis is for.
            range_boundary = []
        return (out + ssa.undefined_uses(graph, start)
                + types.type_errors(self.proc, graph)
                + range_boundary
                + boundary)

    def may_read_unassigned(self):
        """Variables read on some path that does not assign them.

        Apart from ``well_formed`` because what is left after the exact rules
        have run is a statement about the source rather than about the shape:
        a loop whose count can be zero, followed by a read of what it assigned,
        is a program Python would raise on too.
        """
        return ir.may_read_unassigned(self.proc, self.env)

    def unaccounted_goals(self):
        """The pre-generation declaration/producer audit already passed.

        Lowering reconciles source structural and safety declarations against its
        producers before :class:`BehaviorInstance` exists.  Construction then freezes
        that result as ``_frontend_catalogue``.  Rewalking the source here would create
        a second catalogue authority during bounded analysis; the sealed procedure and
        immutable manifest are the evidence the earlier audit produced.
        """
        return []

    def budgets(self):
        """The limits this frontend works within, for the record to carry.

        A bound on how far a loop is unrolled belongs to whatever does the
        unrolling. The other side's are different numbers about different
        things, and what is common is only that a certificate is relative to
        them and has to name them.
        """
        from . import budgets

        return budgets.as_dict()

    def contract(self):
        return {name: [rng.lo, rng.hi] for name, rng in self.env.items()
                if name in self.proc.inputs}

    def bind(self, bound=None, domain=None, task_contract=None, *,
             catalogue_manifest=None, _position_closure_deadline=None,
             _position_closure_timeout_ms=None):
        """This artifact and the one set of traces every statement will be about.

        What `close_over` should be given, so that the domain is fixed before any
        evidence is gathered rather than assembled afterwards to describe it. Either a
        bound, from which the domain is built, or a domain to be bound to, which is what
        a caller comparing two artifacts over one domain passes.

        A mismatch is refused rather than resolved. Where this instance's ranges were
        computed for a bound, they cover runs of that length and say nothing about a
        longer one, so binding to a domain of a different bound would leave every width
        -- and therefore every unreachable verdict -- a statement about a trace set the
        analysis was never about. Nothing downstream could detect it: the widths would
        simply be too narrow and the formula would be satisfiable in fewer places.
        """
        from ..core.catalogue import CatalogueManifest
        from ..core.instance import BoundedInstance

        target = domain.bound if domain is not None else bound
        if self.bound is not None and target != self.bound:
            raise ValueError(
                f"ranges were computed to cover {self.bound} steps and this "
                f"domain is about {target}: lower the model at {target} instead")
        if domain is not None and task_contract is not None \
                and domain.task_contract != task_contract:
            raise ValueError(
                "the supplied trace domain is bound to a different task contract")
        if type(target) is not int or target < 1:
            raise ValueError(
                "a bound Behavior instance needs a positive finite horizon")
        if catalogue_manifest is None:
            catalogue_manifest = self._catalogue
        elif type(catalogue_manifest) is not CatalogueManifest:
            raise TypeError(
                "a bound Behavior instance needs the exact CatalogueManifest")
        # The complete task catalogue may add trace-level monitor goals, but it may not
        # omit, rename or replace any frontend goal or occurrence.  Nor may it smuggle a
        # second python-structural declaration under the monitor union.
        catalogue_manifest.reconcile(
            self._frontend_catalogue, "behavior-frontend", exhaustive=False)
        catalogue_manifest.reconcile_occurrences(
            self._frontend_catalogue.occurrences,
            "behavior-frontend", exhaustive=False)
        from ..core.refusal import Refusal, Refused

        extra_frontend = sorted(
            goal for goal in catalogue_manifest
            if catalogue_manifest[goal].provider == "python-structural"
            and goal not in self._frontend_catalogue)
        if extra_frontend:
            raise Refused(Refusal(
                "behavior-catalogue-frontend-goal-injected",
                "the complete task catalogue adds python-structural goals that the "
                "selected Behavior frontend did not declare",
                goals=extra_frontend))
        invalid_external = sorted(
            goal for goal in catalogue_manifest
            if goal not in self._frontend_catalogue
            and catalogue_manifest[goal].evidence != "trace")
        if invalid_external:
            raise Refused(Refusal(
                "behavior-catalogue-external-goal-invalid",
                "goals added to a Behavior frontend catalogue must be the task "
                "monitor's trace-level obligations",
                goals=invalid_external))
        if self.transaction_goals is not None:
            catalogue_manifest.reconcile(
                self.transaction_goals.catalogue,
                "behavior-transactions", exhaustive=False)
        from ..symbolic.closure_proof import prepare_positions

        if self._position_plan is None:
            prepared = prepare_positions(
                self.proc, self.env, target,
                _closure_deadline=_position_closure_deadline,
                _closure_timeout_ms=_position_closure_timeout_ms)
        else:
            self._position_plan.checked_for(self.proc, self.env, target)
            prepared = self._position_plan
        planned = BehaviorInstance(
            self.proc,
            self.env,
            source=self.source,
            file=self.file,
            class_name=self.class_name,
            schema=self.schema,
            bound=target,
            _position_plan=prepared,
            _frontend_catalogue=self._frontend_catalogue,
            _catalogue=catalogue_manifest,
            artifact_identity=self.artifact_identity,
            memory_interfaces=self.memory_interfaces,
            memory_environment=self.memory_environment,
            memory_horizon=self.memory_horizon,
            transaction_goals=self.transaction_goals,
            source_rules=self.source_rules,
        )
        exact_domain = domain or planned.domain(
            target, task_contract=task_contract)
        return BoundedInstance(planned, exact_domain)

    @property
    def identity(self):
        """Stable identity of the transition system queried by this instance."""
        from ..symbolic.plan import procedure_identity

        return f"{FRONTEND}:{procedure_identity(self.proc)}"

    def analysis_identity(self):
        """The provider-side half of the bound-domain identity check."""
        return self.artifact_identity or self.identity

    def evidence_identity(self, steps):
        """Identify the finite representation and symbolic transition queried."""
        if type(steps) is not int or steps < 1:
            raise ValueError("analysis evidence requires a positive finite horizon")
        if self.bound is not None and steps != self.bound:
            raise ValueError(
                "analysis evidence was requested at a horizon different from the "
                "one used to derive this instance's ranges")
        if self._position_plan is None:
            raise ValueError(
                "analysis evidence requires a Behavior instance prepared by bind")
        self._position_plan.checked_for(self.proc, self.env, steps)
        from ..symbolic.closure_proof import position_analysis_identities

        return position_analysis_identities(
            self._position_plan, frontend=FRONTEND, policy=POLICY)

    def task_audit(self, task):
        """Check that the procedure boundary is exactly the task boundary.

        Copying ``task.contract.digest`` into a domain is not this check.  In
        particular, a one-bit procedure input can be stamped with a task declaring two
        bits and then prove the values two and three unreachable.  Compare the task's
        field bindings and finite types to the frozen procedure signature before any
        feasibility or goal query is allowed.
        """
        from ..core.audit import AuditCheck
        from ..core.contract import TaskContract

        contract = getattr(task, "contract", None)
        signature = getattr(self.proc, "signature", None)
        if type(contract) is not TaskContract:
            return AuditCheck.refused(
                "behavior-task-contract-invalid",
                "the concrete record does not carry the exact immutable TaskContract")
        if signature is None:
            return AuditCheck.refused(
                "behavior-task-signature-missing",
                "the lowered procedure has no task-owned finite signature")
        findings = []

        def projected(endpoints, declared, direction):
            declared = dict(declared)
            result = {}
            for endpoint in endpoints:
                for field in endpoint.fields:
                    choices = {name for name in (field.port, field.name)
                               if name is not None and name in declared}
                    if len(choices) != 1:
                        findings.append(
                            f"{direction} field {endpoint.name}.{field.name} maps to "
                            f"{sorted(choices)!r}, not one procedure field")
                        continue
                    name = next(iter(choices))
                    if name in result:
                        findings.append(
                            f"procedure {direction} {name} is bound by more than one "
                            "task field")
                        continue
                    result[name] = field.type
            missing = sorted(set(declared) - set(result))
            if missing:
                findings.append(
                    f"procedure {direction} fields are absent from the task binding: "
                    f"{missing}")
            return result, declared

        task_inputs, proc_inputs = projected(
            contract.inputs(), signature.inputs, "input")
        task_outputs, proc_outputs = projected(
            contract.outputs(), signature.outputs, "output")
        for direction, task_fields, proc_fields in (
                ("input", task_inputs, proc_inputs),
                ("output", task_outputs, proc_outputs)):
            for name in sorted(set(task_fields) & set(proc_fields)):
                proc_type = proc_fields[name]
                agrees = proc_type == task_fields[name]
                if not agrees:
                    findings.append(
                        f"procedure {direction} {name} has type {proc_fields[name]!r} "
                        f"where the task declares {task_fields[name]!r}")

        # ``contract()`` is the physical SolverIR carrier.  Structured logical
        # values may occupy several compiler-owned variables, so derive the expected
        # physical rows from the same frozen ProcedureContract/ValueBindings rather
        # than trying to take one scalar ``bounds`` pair from each task field.
        try:
            expected_bounds = {
                name: list(bounds)
                for name, bounds in self.proc.semantics.solver_input_bounds()
            }
        except Exception as exc:  # noqa: BLE001 - audit reports malformed authority
            findings.append(
                "the exact input binding has no finite SolverIR carrier: "
                f"{type(exc).__name__}: {exc}")
            expected_bounds = None
        if expected_bounds is not None and self.contract() != expected_bounds:
            findings.append(
                "the exact input ranges do not equal the task-declared finite input "
                "ranges")
        return (AuditCheck.inconsistent(findings) if findings
                else AuditCheck.agreed())

    def domain(self, bound, task_contract=None):
        """The set of traces a statement at this bound would be about.

        Collected here rather than by whatever is asking. The facts a verdict
        is relative to used to travel separately, and a reader had to gather
        them from several places and trust that they described one run.
        """
        from ..core.domain import TraceDomain
        return TraceDomain(
            bound=bound,
            contract=self.contract(),
            # What construction and reset left, which is where the source run
            # starts.  Entry-dead fields have a canonical value only inside the
            # total IR state vector; the source object has no such attribute at
            # this boundary, so that representation filler is not published as
            # a source initial-state fact.
            initial={
                name: domain_tree(value)
                for name, value in self.proc.initial.items()
                if name not in set(getattr(self.proc, "entry_dead", ()))
            },
            schema=(self.proc.signature.as_dict()
                    if self.proc.signature is not None else
                    {name: [rng.lo, rng.hi]
                     for name, rng in self.env.items()
                     if name in self.proc.inputs}),
            frontend=FRONTEND,
            policy=POLICY,
            # A trace domain is about the transition system M, not the path
            # that happened to contain its source.  Pair the semantic frontend
            # version with the stable structural image used by EncodingPlan so
            # two different procedures in equally named files cannot share a
            # domain identity.
            artifact=self.analysis_identity(),
            task_contract=task_contract)

    # ── what this frontend knows is wrong with itself ──

    def unreadable_initial(self):
        """Starting values construction could read and a step cannot carry, by name.

        Asked by construction rather than by folding the source, because the initial
        state is *defined* as what the harness's model holds after `reset` and folding
        only approximates that. What comes back is not "unknown" but a reason: a value
        outside the recursive scalar/array carrier, or an answer that differed on a
        second construction and therefore is not a stable starting value.

        Empty without the source, since there is nothing to construct.
        """
        if self.source is None or not getattr(self.proc, "guessed_initial", ()):
            return {}
        from .extract import initial_by_construction

        try:
            _read, unsupported = initial_by_construction(
                self.source, self.file, names=self.proc.guessed_initial)
        except Exception as e:                                  # noqa: BLE001
            return {name: f"the model could not be constructed: "
                          f"{type(e).__name__}: {e}"
                    for name in self.proc.guessed_initial}
        return unsupported

    def shared_goals(self):
        """Goals produced in more than one place, and how many places each.

        Reported, and **not** a defect, which is a change of mind rather than a
        change of code. Under `goals.POLICY`, which is `source-site`, a branch inside
        a helper is one goal however many callsites reach it, and covering it on
        either call covers it. That is the policy, so a model conforming to it is not
        overstating anything, and this used to withhold its certificate for doing so:
        267 of 836 models, on the strength of an appendix that claimed a
        callsite-sensitive policy the code has never implemented. The paper is the
        thing that was wrong.

        Kept, because the number is worth reporting. It says how much of the step is
        shared helper code, which is a fact about the model a reader wants, and it is
        what a callsite-sensitive run would multiply the denominator by if one is ever
        wanted: `blas_dsyr2` inlines a helper 115 times, so two of its goals would
        become 230.

        Counted from the step rather than the source, which is what makes it exact:
        the lowering inlines a helper once per call, so a goal marked twice in the
        step is a goal two places share, however the source is written.
        """
        counted: dict[str, int] = {}
        for cmd in ir.walk(self.proc.body):
            if not isinstance(cmd, (ir.Mark, ir.Fail)):
                continue
            for goal in ir.goals_of(cmd):
                counted[goal] = counted.get(goal, 0) + 1
        return {goal: n for goal, n in counted.items() if n > 1}

def lower(source, schema, file="behavior.py", lowerer=None, bound=None):
    """Build an instance from a model and the schema it is driven through.

    The schema says how many bits an endpoint carries and whether it is read as
    signed; turning that into the intervals this side computes with is this
    side's business.

    ``schema`` is a ``TransactionSchema``, and where it should come from is the
    task: `sim/task.schema_of_spec` reads the interface the specification
    declares and elaborates it, so this side needs nothing from the design at all.

    A string is still accepted and read as a candidate design's header, which is
    what the pipeline used to pass, and an instance built that way records among
    its blockers that its contract came from the artifact under test: a candidate
    declaring a port narrower than the task narrows the contract, and narrowing
    the contract can only enlarge the set of goals reported unreachable. Over the
    population, 833 of 836 tasks declare an interface that elaborates, three
    candidates declare ports the task does not, and 91 of the widths are written
    over parameters that the header reader refuses and the elaborator resolves. So
    the string is the fallback and says so, rather than the ordinary way in.

    ``lowerer`` chooses which lowering builds the step, and exists so that the
    graph lowering can be put in front of the encoder and the queries. The
    interpreter has been asked and agrees with the term lowering on the whole
    population; the encoder is a second reader with a different cost, since
    if-conversion introduces a variable per block, and "the interpreter agrees"
    says nothing about what the solver does with them.
    """
    from ..core.schema import TransactionSchema
    from .admission import check as admission_check
    from .lower_prepare import _validate_scalar_width
    from .lowering import (
        LoweringError,
        lower_source,
    )

    # Admission first, and here rather than only in the pipeline that happened
    # to call it. What the certificate says is about a model of the admitted
    # subset; building an instance from a model nobody checked would make that
    # a claim about whatever the lowering did not happen to refuse.
    rejections = admission_check(source, file=file)
    if rejections:
        raise LoweringError(
            _Where(rejections[0].line, rejections[0].col),
            f"{len(rejections)} constructs are not admitted, the first being "
            f"{rejections[0]}", code="admission-rejected")

    if not isinstance(schema, TransactionSchema):
        from ..rtl.interface import read_ports

        schema = read_ports(schema)

    # The contract goes in, so the one fixpoint the lowering runs is the
    # informed one. It used to run twice: once inside with no input ranges,
    # whose answers decide which loop bounds resolve, and once out here with the
    # real bounds. The fixpoint is the whole cost of lowering, and the weaker of
    # the two was the one that mattered.
    #
    # The input names come from the method's signature, not from lowering the
    # model to find out. Lowering it to learn what to tell the lowering would be
    # paying the cost twice to avoid paying it twice.
    # Bounds construct powers of two, so width validation must precede the
    # first call to ``Endpoint.bounds``.  The schema is a task declaration,
    # not permission to make an unreported host allocation in this frontend.
    for name, endpoint in schema.endpoints.items():
        _validate_scalar_width(
            endpoint.width, _Where(0), f"schema endpoint {name}")
    from ..symbolic.procedure_contract import ProcedureSignature

    input_names = _input_names(source, file)
    signature = ProcedureSignature.from_schema(
        schema, input_names=input_names)
    bounds = signature.input_bounds(input_names)
    # What the state holds before the first step, read by constructing the model, which
    # is what the harness does and therefore what the initial state *is*. Folding the
    # source is the approximation, and it was missing parameters computed as
    # `(width - 1).bit_length()` or `to_unsigned(code, p_data_width)`: eight models had
    # such a name taken for a state variable whose starting value nothing could say,
    # and each of those withheld a certificate.
    #
    # Here rather than in the lowering. This runs the artifact, and this function
    # already does for the source audit, whereas `lower_procedure` is asked about
    # models nobody intends to run. Where folding can also say, the two must agree, and
    # the lowering refuses a disagreement rather than choosing.
    from .extract import initial_by_construction

    try:
        read_initial, _unsupported = initial_by_construction(source, file)
    except Exception:                                           # noqa: BLE001
        # A model that cannot be constructed is not a model whose starting values can
        # be read this way, and the folding still applies. What is left over is named
        # by `unreadable_initial`, which withholds the certificate.
        read_initial = None
    # And how many steps the ranges have to cover. With a bound the analysis is
    # R(0) = Init, R(t+1) = post#(R(t)), which is the question the verification asks;
    # without one it is the fixpoint, which asks what interval is closed under the step
    # for all time and is a harder question with a worse answer -- at ten steps it
    # leaves 110 of the population's 614 state variables without a range that the
    # bounded reading gives one to.
    #
    # The fixpoint stays as the fallback rather than being removed, because it is sound
    # at every bound: it over-approximates every reachable state, so a caller that does
    # not know the bound yet still gets an answer it can use at any of them. What it is
    # not is the intent.
    lowered = lower_source(
        source, file=file, outputs=list(signature.output_names),
        input_bounds=bounds, lowerer=lowerer, read_initial=read_initial,
        bound=bound, signature=signature)
    proc = lowered.proc
    env = {**proc.state_ranges,
           **{name: ranges.Range(lo, hi) for name, (lo, hi) in bounds.items()}}
    return BehaviorInstance(proc, env, source=source, file=file,
                            schema=schema, bound=bound,
                            _frontend_catalogue=lowered.catalogue)


def _input_names(source, file="behavior.py", method="process"):
    """What the step is driven with, read off the signature.

    Through `source_model`, so that which method this reads and which method the
    lowering lowers are the same decision. They were two, and agreed by both taking a
    `process` out of the tree: this took the first of an `ast.walk` and Python binds the
    last, so a source with two would have been driven through one signature and lowered
    from the other.
    """
    from .extract import source_model

    return source_model(source, file, step=method).inputs


def encodable(instance):
    """Whether the step has a formula at all, and why not when it has none."""
    try:
        residual.unroll(instance.proc, instance.env, 1)
    except EncodingError as e:
        return False, str(e)
    return True, None


def feasibility(proc, env, steps, initial=None, timeout_ms=None, plan=None,
                memory_mb=None):
    """Construct one trace at this bound from the finite Behavior domain.

    Every statement a certificate makes is about the set of admissible traces.
    If that set is empty, every goal query is unsatisfiable for a reason that
    says nothing about the artifact, so the certificate says so instead of
    inheriting the vacuity.

    A well-formed Behavior step is total: it returns either a normal successor
    or an explicit terminal error, and errors are absorbed.  Its declared input
    domains are nonempty finite intervals.  Their lower endpoints therefore
    construct a trace directly; no satisfiability query can establish more.
    The coordinator replays and audits this request before accepting it as
    evidence, so construction does not bypass the source/encoding checks.
    """
    if type(steps) is not int or isinstance(steps, bool) or steps < 1:
        raise ValueError("a feasibility request has a positive finite bound")
    domains = dict(proc.semantics.solver_input_bounds())
    if set(domains) != set(proc.inputs):
        return Feasible.UNRESOLVED, (
            "the procedure does not declare one finite domain for every input")
    stimulus = [
        {name: domains[name][0] for name in proc.inputs}
        for _position in range(steps)
    ]
    for framing, projection in proc.input_framing:
        public, logical = zip(*projection)
        rows = framing.shape([tuple(row[name] for name in logical) for row in stimulus], public)
        for row, payload in zip(stimulus, rows):
            row.update(zip(logical, payload))
    try:
        interp.run_trace(proc, stimulus, initial)
    except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
        return Feasible.UNRESOLVED, (
            "the canonical finite-domain trace could not be replayed: "
            f"{type(exc).__name__}: {exc}")
    return Feasible.YES, stimulus
