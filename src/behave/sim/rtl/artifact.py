"""Isolated RTL replay through the task's common Driver and protocol monitors.

Compile once and run each scored stimulus in a fresh simulator process. The
result records pin observations, accepted transactions, goals and requested state;
tool failures retain their typed ownership. Optional shared-process diagnostics
live in batch_replay and do not establish the score.

External-memory requests are served by the task/run-owned product carried in the
stimulus; the artifact does not invent a second initialization, latency, ready schedule
or collision rule.  A single-clock contract still maps one slot to one cycle, while a
multi-clock contract uses the timestamped global schedule and atomic driver.
"""

from __future__ import annotations

import threading

from behave.core import runtime_defaults as defaults
from behave.sim.core.source_rules import EMPTY_SOURCE_RULES

from ..core.failure import RunFailure
from ..core.refusal import Refusal, Refused
from ..core.stimulus import leading_reset_slots, uses_global_events
from .execution_policy import execution_policy
from .replay_failure import ReplayPurpose, budget_failure
from .replay_plan import (
    ATOMIC_DRIVER_PLAN_VERSION,
    DRIVER_PLAN_VERSION,
)
from .replay_plan import (
    ATOMIC_TRACE_VERSION as ATOMIC_DRIVER_TRACE_VERSION,
)

__all__ = ["ATOMIC_DRIVER_TRACE_VERSION", "RTLArtifact", "leading_reset_slots"]


def _sample(count):
    """First/middle/last positions for diagnostic isolated comparisons."""
    if count < 2:
        return ()
    return tuple(sorted({0, count // 2, count - 1}))


def _observation_names(contract, observe_names=()):
    """Return the simulator sample set and the durable trace subset.

    Structural hits come from native callbacks, not sampled internal wires.
    Sample only the protocol/memory boundary and explicitly requested audit values.
    """
    from ..core.memory import memory_artifact_signal_set

    recorded = (
        {field.port for endpoint in contract.endpoints for field in endpoint.fields}
        | {control for endpoint in contract.endpoints
           for control in endpoint.adapter.artifact_controls}
        | {signal for memory in contract.memories
           for signal in memory_artifact_signal_set(memory)}
        | set(observe_names)
    )
    names = tuple(sorted(recorded))
    return names, names


class RTLArtifact:
    """A design, driven through one contract's adapter.

    ``kind`` is `"rtl"`, which the closed evaluator dispatches on rather than sniffing for
    methods: an artifact that answers the wrong protocol by accident is the failure the
    kind exists to make impossible.
    """

    kind = "rtl"

    def __init__(self, rtl_path, top_module, workdir, period_ns=10,
                 timeout_s=defaults.SIM_RTL_TOOL_SECONDS,
                 verify_every=False, run_policy=None,
                 profile_replay=defaults.SIM_RTL_PROFILE_REPLAY, source_rules=EMPTY_SOURCE_RULES):
        #: The run-domain fields a record's domain identity carries beside the
        #: interface: `reset_hold_cycles` and `adapter_version`. `None` where a caller
        #: has none to name -- the tests that drive this directly -- and then the
        #: identity carries the interface and the horizon and says nothing more.
        self.run_policy = dict(run_policy or {})
        self.rtl_path = rtl_path
        self.top_module = top_module
        self.workdir = workdir
        self.period_ns = period_ns
        #: Tool-job ceiling; scored replay instead uses its owner's remaining time.
        self.timeout_s = int(timeout_s)
        self.profile_replay = profile_replay
        self.source_rules = source_rules
        #: Whether to check **every** stimulus against an isolated replay, rather than a
        #: sample of three.
        #:
        #: Batching is sound only if every stimulus gives the same record in the batch as
        #: on its own, and a sample cannot establish that -- three of 761 is a spot
        #: check. Verifying all of them costs one elaboration per stimulus, which is the
        #: cost the batch exists to avoid, so this is off by default and the result says
        #: which was done. A sample is a cheap way to **fail**, not a way to pass.
        self.verify_every = bool(verify_every)
        self._frontend_catalogue = None
        self._frontend_catalogue_identity = None
        self._frontend_catalogue_lock = threading.Lock()
        self._state_ownership_cache = {}
        self._state_ownership_identity = None
        self._state_ownership_lock = threading.Lock()
        self._tool_execution_lock = threading.Lock()
        self._tool_executions = []
        self._observation = None
        self._observation_lock = threading.Lock()

    @property
    def observation_binding(self):
        """The admitted observer, or None before observation preparation."""
        return None if self._observation is None else self._observation.binding

    def bind_observation(self, contract):
        """Freeze one observer before generation, scoring or source audit begins."""
        from pathlib import Path

        from ..core.failure import FailureRaised
        from .observation_binding import prepare_observation

        if self.source_rules != contract.source_rules:
            raise Refused(Refusal("rtl-source-goal-policy-mismatch",
                                  "the artifact and task must use the same source goal declarations"))

        with self._observation_lock:
            identity = self.identity
            catalogue = self.frontend_catalogue(expected_identity=identity)
            if self._observation is not None:
                self._observation.binding.check(identity, contract, catalogue)
                return self._observation.binding
            source = Path(self.rtl_path).read_text()
            import hashlib

            if hashlib.sha256((str(self.top_module) + "\0" + source).encode()).hexdigest() != identity:
                raise Refused(Refusal("rtl-observation-source-changed",
                                      "source changed during observer admission"))
            prepared = prepare_observation(
                source, artifact=identity, top=self.top_module, contract=contract,
                catalogue=catalogue, timeout_s=self.timeout_s)
            if type(prepared) is RunFailure:
                raise FailureRaised(prepared)
            if self.identity != identity:
                raise Refused(Refusal("rtl-observation-source-changed",
                                      "source changed during observer preparation"))
            if prepared.native is not None:
                self._record_tool_execution(prepared.native.execution)
            self._observation = prepared
            return prepared.binding

    def _replay_observer(self, contract, requested):
        """An admitted observer cannot be replaced by a per-call override."""
        if requested is not None and type(requested) is not bool:
            raise TypeError("native source observation is an exact Boolean or absent")
        binding = self.observation_binding
        if binding is None:
            binding = self.bind_observation(contract)
        binding.check(self.identity, contract, self.frontend_catalogue())
        if requested is not None and requested != binding.native:
            raise Refused(Refusal("rtl-observation-override",
                                  "replay cannot replace the admitted observer"))
        return binding.native

    def _record_tool_execution(self, execution):
        """Append one immutable policy/result image to this artifact's lineage."""

        image = execution.as_dict()
        with self._tool_execution_lock:
            self._tool_executions.append(image)

    def _retain_replay_profile(self, execution, stimulus):
        """Keep diagnostic bytes, never reconstruct or change replay evidence."""
        if not self.profile_replay or "cocotb.pstat" not in execution.outputs:
            return
        from pathlib import Path
        from uuid import uuid4

        directory = Path(self.workdir) / "profiles"
        directory.mkdir(parents=True, exist_ok=True)
        # A repeated replay of the same stimulus is a distinct measurement.
        # The tool lineage also records the profile's size and digest.
        with (directory / f"{stimulus.fingerprint}-{uuid4().hex}.pstat").open("xb") as stream:
            stream.write(execution.outputs["cocotb.pstat"])

    def tool_lineage(self):
        """The exact compiler/simulator policies used by this artifact so far."""

        with self._tool_execution_lock:
            return tuple(dict(row) for row in self._tool_executions)

    def state_ownership(self, contract, *, expected_identity=None, timeout_s=None):
        """Freeze the frontend-owned represented-state partition for this task.

        Concrete reset monitoring and exact scheduled products must observe the same
        complete state universe.  This is an elaboration result, not a solver result,
        and its cache is bound to both source bytes and the task contract.
        """

        from ..core.contract import TaskContract
        import math

        if type(contract) is not TaskContract:
            raise TypeError("RTL state ownership needs the exact TaskContract")
        timeout = min(self.timeout_s, defaults.SIM_RTL_CATALOGUE_SECONDS)
        if timeout_s is not None:
            if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
                raise ValueError("RTL state ownership needs a finite positive time allowance")
            timeout = min(timeout, max(1, math.ceil(timeout_s)))
        current = self.identity
        if expected_identity is not None and current != expected_identity:
            raise Refused(Refusal(
                "rtl-state-ownership-artifact-mismatch",
                "the RTL source being partitioned is not the artifact admission bound",
                admitted=str(expected_identity), current=current))
        with self._state_ownership_lock:
            if self._state_ownership_identity is not None \
                    and current != self._state_ownership_identity:
                raise Refused(Refusal(
                    "rtl-state-ownership-source-changed",
                    "the RTL source changed after its represented-state partition "
                    "was frozen",
                    frozen=self._state_ownership_identity, current=current))
            cached = self._state_ownership_cache.get(contract.digest)
            if cached is not None:
                return cached.validate_contract(contract)

            from .state_ownership import read_state_ownership_with_execution

            ownership, execution = read_state_ownership_with_execution(
                self.rtl_path, self.top_module, contract,
                timeout=timeout)
            self._record_tool_execution(execution)
            after = self.identity
            if after != current:
                raise Refused(Refusal(
                    "rtl-state-ownership-source-changed-during-elaboration",
                    "the RTL source changed while its represented-state partition "
                    "was being elaborated",
                    before=current, after=after))
            self._state_ownership_identity = current
            self._state_ownership_cache[contract.digest] = ownership
            return ownership

    def frontend_catalogue(self, *, expected_identity=None):
        """Elaborate and freeze this admitted RTL artifact's structural manifest.

        The cache is bound to the live source identity.  A source change after
        admission or after the first elaboration is a refusal, never a request to build
        a new denominator for the same run.
        """

        with self._frontend_catalogue_lock:
            return self._prepare_frontend_catalogue(expected_identity)

    def _prepare_frontend_catalogue(self, expected_identity):
        from .catalogue import source_catalogue_with_execution_of

        current = self.identity
        if expected_identity is not None and current != expected_identity:
            raise Refused(Refusal(
                "rtl-catalogue-artifact-mismatch",
                "the RTL source being catalogued is not the artifact admission bound",
                admitted=str(expected_identity), current=current))
        if self._frontend_catalogue is not None:
            if current != self._frontend_catalogue_identity:
                raise Refused(Refusal(
                    "rtl-catalogue-source-changed",
                    "the RTL source changed after its structural manifest was frozen",
                    frozen=self._frontend_catalogue_identity, current=current))
            return self._frontend_catalogue

        before = current
        try:
            manifest, execution = \
                source_catalogue_with_execution_of(
                    self.rtl_path, self.top_module, timeout=self.timeout_s,
                    source_rules=self.source_rules)
        except Exception:  # source failures also have to belong to this admission
            if self.identity != before:
                raise Refused(Refusal(
                    "rtl-catalogue-source-changed-during-elaboration",
                    "the RTL source changed before its frontend failure was bound",
                    before=before, after=self.identity)) from None
            raise
        self._record_tool_execution(execution)
        after = self.identity
        if after != before:
            raise Refused(Refusal(
                "rtl-catalogue-source-changed-during-elaboration",
                "the RTL source changed while its structural manifest was being "
                "elaborated",
                before=before, after=after))
        self._frontend_catalogue_identity = before
        self._frontend_catalogue = manifest
        return manifest

    # ── one elaboration, one process per stimulus ──

    def traces_per_process(self, contract, stimuli, workers=None, *,
                           observe_names=(), state_names=(), state_widths=None,
                           _initial_state=None, _startup_state=None, native_events=None,
                           _purpose=ReplayPurpose.CONCRETE, _consume=None, _monitor_plan=None,
                           _completion_targets=None, _stop=None, _capture_query_boundaries=True,
                           _checkpoint_plans=None, _completion_planner=None):
        """Reuse only compiler output; every stimulus runs in a fresh namespace."""
        from .replay_build import replay_build_scope

        with replay_build_scope(self):
            return self._traces_per_process(
                contract, stimuli, workers, observe_names=observe_names,
                state_names=state_names, state_widths=state_widths,
                _initial_state=_initial_state, _startup_state=_startup_state, native_events=native_events,
                _purpose=_purpose, _consume=_consume, _monitor_plan=_monitor_plan,
                _completion_targets=_completion_targets, _stop=_stop,
                _checkpoint_plans=_checkpoint_plans,
                _completion_planner=_completion_planner,
                _capture_query_boundaries=_capture_query_boundaries)

    def _traces_per_process(self, contract, stimuli, workers=None, *,
                            observe_names=(), state_names=(), state_widths=None,
                            _initial_state=None, _startup_state=None, native_events=None,
                            _purpose=ReplayPurpose.CONCRETE, _consume=None, _monitor_plan=None,
                            _completion_targets=None, _stop=None, _capture_query_boundaries=True,
                            _checkpoint_plans=None, _completion_planner=None):
        """Compile once, then replay each stimulus in its own simulator namespace.

        The compiler sees no stimulus. Simulator workers mount its validated build
        read-only and have private writable state; no reset-restoration assumption
        is needed to separate stimuli. By default each entry returns an
        `AuditedTrace` or a typed `RunFailure`. The private scoring continuation
        `_consume` assesses that result before the worker releases its slot;
        admission/build failures before dispatch remain raw failures. Within a
        command, matching compiler inputs reuse the same validated executable.
        """
        import pickle
        import shutil
        from pathlib import Path
        from time import perf_counter

        from ..cpu_admission import reserve_cpus
        from ..replay_parallel import _ordered_map, replay_workers
        from .replay_build import current_replay_build
        from .replay_failure import replay_seconds
        from .replay_progress import progress_detail
        from .trace_decoder import TraceDecoder
        from .trace_postprocessing import RawTrace, TracePlan, TracePostprocessor

        build_scope = current_replay_build(self)

        from .tool_isolation import default_limits, run_workspace
        from .tool_protocol import (
            ToolCommand,
            ToolGroup,
            ToolIsolationError,
            command_result,
            command_stream,
            require_completed,
        )

        workers = replay_workers(workers)
        if type(_purpose) is not ReplayPurpose:
            raise TypeError("RTL replay needs an exact execution purpose")
        # A selected initial valuation is analysis-only even for direct callers.
        has_initial_choice = _initial_state is not None or _startup_state is not None
        purpose = ReplayPurpose.ANALYSIS if has_initial_choice else _purpose
        native_events = self._replay_observer(contract, native_events)

        from ..core.driver import Driver
        from ..harness import generate_makefile, render_template

        stimuli = list(stimuli)
        completion_targets = dict(_completion_targets or {})
        checkpoint_plans = ({key: plan for key, plan in (_checkpoint_plans or {}).items()
                             if plan.prefixes is not None}
                            if _consume is not None and purpose is ReplayPurpose.CONCRETE
                            and len(contract.clocks) <= 1 else {})
        if not stimuli:
            return []
        from ..core.atomic import AtomicDriver

        identity = str(getattr(self, "identity", "") or "")
        # Materialize the requested layout once; generators must not be consumed by
        # the binding check before they reach the simulator plan.
        state_names = tuple(state_names)
        if _startup_state is not None:
            from .startup_state import RTLStartupState

            if type(_startup_state) is not RTLStartupState:
                raise TypeError("HDL construction needs an exact RTLStartupState")
            if _initial_state is not None or len(stimuli) != 1:
                raise ValueError("a startup choice belongs to one replay, without a later state override")
            _startup_state.check_binding(identity, state_names, state_widths, contract)
        if _initial_state is not None:
            from .initial_state import RTLInitialState

            if type(_initial_state) is not RTLInitialState:
                raise TypeError("analysis startup needs an exact RTLInitialState")
            if len(stimuli) != 1:
                raise ValueError("one analysis initial choice belongs to one replay")
            _initial_state.check_binding(identity, state_names, state_widths)
            environment = set(contract.environment_controls()) | {
                field.port for endpoint in contract.inputs()
                for field in endpoint.fields} | {
                clock.clock for clock in contract.clocks} | {
                clock.reset for clock in contract.clocks}
            if set(_initial_state.values()) & environment:
                raise Refused(Refusal(
                    "rtl-initial-state-targets-environment",
                    "an initial register choice cannot write stimulus or clock pins"))
        runnable, refused = [], {}
        def check_request(stimulus):
            if uses_global_events(contract, stimulus):
                AtomicDriver(contract, stimulus, artifact_identity=identity,
                             expected_outputs=completion_targets.get(stimulus.fingerprint))
            else:
                Driver(contract, stimulus,
                       expected_outputs=completion_targets.get(stimulus.fingerprint))

        for position, stimulus in enumerate(stimuli):
            try:
                if _completion_planner is None or stimulus.replay_policy is None:
                    check_request(stimulus)
            except Refused as exc:
                refused[position] = RunFailure.verifier(
                    "stimulus-admission", exc.refusal)
                continue
            runnable.append((position, stimulus))
        if not runnable:
            return [refused[i] for i in range(len(stimuli))]

        if native_events:
            self.frontend_catalogue()
        observe_names = tuple(sorted(set(map(str, observe_names))))
        if any(type(name) is not str or not name for name in state_names) \
                or len(state_names) != len(set(state_names)):
            raise Refused(Refusal(
                "rtl-artifact-state-request-malformed",
                "an exact state observation must name each nonempty field once"))
        state_names = tuple(sorted(state_names))
        if state_widths is not None:
            if type(state_widths) is not dict or set(state_widths) != set(state_names) \
                    or any(type(width) is not int or isinstance(width, bool)
                           or width < 1 for width in state_widths.values()):
                raise Refused(Refusal(
                    "rtl-artifact-state-request-malformed",
                    "an exact state observation needs one positive width for every "
                    "field"))
            state_widths = {
                name: state_widths[name] for name in state_names
            }
        watch, record_names = _observation_names(contract, observe_names)
        # Structural/source audits may name a user-declared internal signal rather
        # than a port.  Public-flat exposure is therefore part of any such audit, not
        # only of explicit state snapshots.
        # Query observations have no reset/state-monitor ownership. They only
        # request read access in the same native execution, before presentations.
        boundary_fields = (self._observation.native.boundary_fields
                           if _capture_query_boundaries and native_events else ())
        capture_history = (_capture_query_boundaries == 'history' and native_events
                           and not has_initial_choice)
        expose_internal = bool(state_names or observe_names)
        boundary_signals = tuple(name if isinstance(name, str) else name[-1]
                                 for name, _shape in boundary_fields)

        tb = render_template("driver_tb.py.tmpl", period_ns=self.period_ns)
        import hashlib

        rtl_source = Path(self.rtl_path).read_text()
        rtl = rtl_source.encode()
        loaded_identity = hashlib.sha256(
            (str(self.top_module) + "\0" + rtl_source).encode()).hexdigest()
        if loaded_identity != identity:
            changed = RunFailure.verifier(
                "artifact-identity",
                Refusal(
                    "rtl-artifact-source-changed-before-replay",
                    "the RTL source changed after the replay artifact identity was "
                    "frozen and before its simulator input was copied",
                    frozen=identity, loaded=loaded_identity))
            return [refused.get(i, changed) for i in range(len(stimuli))]
        native = self._observation.native
        if native is not None:
            rtl = native.source.encode()
        # Compiler layout is reusable even when query-boundary *sampling* is
        # disabled (for example in a multi-clock run). State reads/deposits must
        # never depend on whether optional search observations were requested.
        from .source_handles import state_paths as resolve_state_paths

        source_fields = dict(native.boundary_fields) if native is not None else {}
        state_paths = list(resolve_state_paths(state_names, source_fields,
            native.boundary_paths if native is not None else ()).items())
        # Host staging is transient, including the exported raw trace. Durable
        # results are published by the command's ResultStore, not this directory.
        root = build_scope.directory("invocation-")
        from ..search_policy import search_cleanup

        search_cleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "rtl.sv").write_bytes(rtl)
        if native is not None:
            (root / "collector.cpp").write_text(native.collector)
        if has_initial_choice or capture_history:
            from ..harness import TEMPLATE_DIR

            (root / "initial_state.cpp").write_bytes(
                (TEMPLATE_DIR / "initial_state.cpp.tmpl").read_bytes())

        def prepare(where, stimulus):
            where.mkdir(parents=True, exist_ok=True)
            (where / "driver_tb.py").write_text(tb)
            generate_makefile(
                where, self.top_module, tb_module="driver_tb",
                expose_internal=expose_internal, coverage=False,
                initial_state=has_initial_choice or capture_history,
                native_events=native is not None, boundary_signals=boundary_signals)
            if uses_global_events(contract, stimulus):
                plan = {
                    "version": ATOMIC_DRIVER_PLAN_VERSION,
                    "contract": contract,
                    "stimuli": [stimulus],
                    "watch": list(watch),
                    "record_names": list(record_names),
                    "state_fields": [
                        [name, None if state_widths is None
                         else state_widths[name]]
                        for name in state_names
                    ],
                    "artifact": identity,
                }
            else:
                plan = {
                    "version": DRIVER_PLAN_VERSION,
                    "contract": contract,
                    "stimuli": [stimulus],
                    "watch": list(watch),
                    "record_names": list(record_names),
                    "state_names": list(state_names),
                }
            plan["initial_state"] = (
                None if _initial_state is None else _initial_state.as_dict())
            if capture_history:
                plan['source_history'] = identity
            if _startup_state is not None:
                plan["startup_state"] = _startup_state.as_dict()
            plan["execution_policy"] = execution_policy(native_events=native is not None)
            if state_paths:
                plan["state_paths"] = state_paths
            if native is not None:
                plan["native_events"] = native.plan.as_dict()
            if _startup_state is not None or capture_history:
                plan['startup_aliases'] = None if native is None else native.startup_aliases
            if boundary_fields:
                plan["source_boundary_fields"] = list(boundary_fields)
                plan["source_boundary_paths"] = native.boundary_paths
            if stimulus.replay_policy is not None:
                plan["completion_targets"] = completion_targets.get(stimulus.fingerprint)
            if stimulus.fingerprint in checkpoint_plans:
                from ..core.catalogue import SAFETY

                plan["checkpoint"] = checkpoint_plans[stimulus.fingerprint]
                plan["safety_goals"] = tuple(goal for goal in (self._frontend_catalogue or ())
                                             if self._frontend_catalogue[goal].cls == SAFETY)
            (where / "plan.pickle").write_bytes(
                pickle.dumps(plan))
            return where
        # Only the first Makefile is needed to compile. Each admitted lane
        # prepares its own final plan after its response-count pass completes.
        prepare(root / "run0", runnable[0][1])

        # The compiler receives only its closed source bundle, never a stimulus or
        # replay plan. Reuse is command-scoped and byte/toolchain bound.
        compile_budget = self.timeout_s
        try:
            from .tool_runtime import freeze_runtime

            simulation_runtime = freeze_runtime("simulation")
            compiler_inputs = {"rtl.sv": rtl,
                               "run0/Makefile": (root / "run0/Makefile").read_bytes()}
            if (root / "run0/visibility.vlt").is_file():
                compiler_inputs["run0/visibility.vlt"] = (root / "run0/visibility.vlt").read_bytes()
            for name in ("collector.cpp", "initial_state.cpp"):
                if (root / name).is_file():
                    compiler_inputs[name] = (root / name).read_bytes()
            prepared_build, reused = build_scope.compile(compiler_inputs, timeout_s=compile_budget)
            built = prepared_build.execution
            if not reused:
                self._record_tool_execution(built)
        except ToolIsolationError as error:
            refusal = RunFailure.infrastructure(
                "rtl-build-service",
                Refusal(
                    error.code, error.why,
                    tool_execution=error.execution.as_dict()))
            shutil.rmtree(root, ignore_errors=True)
            return [refused.get(i, refusal) for i in range(len(stimuli))]
        except (OSError, ValueError, RuntimeError) as error:
            refusal = RunFailure.infrastructure(
                "rtl-runtime-preparation", Refusal(
                    "rtl-replay-runtime-unavailable",
                    "the replay build and simulator runtime could not be prepared",
                    detail=f"{type(error).__name__}: {error}"))
            shutil.rmtree(root, ignore_errors=True)
            return [refused.get(i, refusal) for i in range(len(stimuli))]
        build_row = command_result(built, "verilator-build")
        if (build_row.get("timed_out") or build_row.get("returncode") != 0
                or prepared_build.mount is None):
            diagnostic = (
                command_stream(built, "verilator-build", "stdout")
                + command_stream(built, "verilator-build", "stderr")
            ).decode("utf-8", errors="replace")
            reason = Refusal(
                "rtl-artifact-build-failed",
                "the isolated compiler produced no executable simulator",
                timed_out=bool(build_row.get("timed_out")),
                returncode=build_row.get("returncode"),
                output="\n".join(diagnostic.strip().splitlines()),
                tool_execution=built.as_dict())
            if build_row.get("timed_out"):
                refusal = budget_failure(purpose, "build", reason)
            elif purpose is ReplayPurpose.ANALYSIS or native is not None:
                refusal = RunFailure.verifier(
                    "rtl-observation-build" if native is not None else "rtl-analysis-build", reason)
                if purpose is ReplayPurpose.CONCRETE and native is not None:
                    from .source_check import source_compile_failure

                    source_failure, checked = source_compile_failure(
                        rtl_source, self.top_module, timeout_s=compile_budget)
                    self._record_tool_execution(checked)
                    if source_failure is not None:
                        refusal = source_failure
            else:
                refusal = RunFailure.candidate("candidate-build", reason)
            shutil.rmtree(root, ignore_errors=True)
            return [refused.get(i, refusal) for i in range(len(stimuli))]

        build_mount = prepared_build.mount
        trace_plan = TracePlan(
            TraceDecoder(self._frontend_catalogue, dict(self.run_policy)),
            contract, identity, state_widths,
            None if native is None else native.plan, self.observation_binding,
            _initial_state, completion_targets, _startup_state, checkpoint_plans)

        def one(item):
            index, (position, stimulus) = item
            where = root / f"run{index}"
            if _completion_planner is not None and stimulus.replay_policy is not None:
                target = _completion_planner(stimulus)
                if type(target) is RunFailure:
                    return position, target
                if target is not None:
                    completion_targets[stimulus.fingerprint] = target.totals
                    if (stimulus.fingerprint in (_checkpoint_plans or {}) and target.prefixes is not None
                            and _consume is not None and purpose is ReplayPurpose.CONCRETE
                            and len(contract.clocks) <= 1):
                        checkpoint_plans[stimulus.fingerprint] = target
                try:
                    check_request(stimulus)
                except Refused as exc:
                    return position, RunFailure.verifier("stimulus-admission", exc.refusal)
            prepare(where, stimulus)
            budget = replay_seconds(purpose, self.timeout_s)
            checkpoint_service = None
            checkpoint_result = None
            checkpoint_started = perf_counter()
            if stimulus.fingerprint in checkpoint_plans:
                from .replay_checkpoint import CheckpointService

                def consume_checkpoint(payload):
                    nonlocal checkpoint_result
                    elapsed = perf_counter() - checkpoint_started
                    checked = postprocessor.decode(stimulus, payload,
                        {"stage": "live-checkpoint", "stimulus": stimulus.fingerprint}, elapsed)
                    checkpoint_result = _consume(stimulus, checked, replay_seconds=elapsed)
                    return checkpoint_result.result.ok and not (_stop is not None and _stop())

                def invalidate_checkpoint():
                    nonlocal checkpoint_result
                    checkpoint_result = _consume(stimulus, RunFailure.verifier(
                        "rtl-checkpoint-tail", Refusal("rtl-checkpoint-tail-unchecked",
                            "a later execution failure supersedes the checked prefix; "
                            "its complete failure evidence is not yet available")))

                checkpoint_service = CheckpointService(consume_checkpoint, invalidate_checkpoint)
            try:
                execution = require_completed(run_workspace(
                    where,
                    (ToolGroup((ToolCommand(
                        "verilator-simulate", ("/build/Vtop",),
                        environment=(
                            ("COCOTB_RESULTS_FILE", "results.xml"),
                            ("COCOTB_TESTCASE", ""),
                            ("COCOTB_TEST_FILTER", ""),
                            ("COCOTB_TEST_MODULES", "driver_tb"),
                            ("COCOTB_TOPLEVEL", self.top_module),
                            ("PYTHONPATH", "/work:/runtime/behave:/runtime/site"),
                            ("TOPLEVEL_LANG", "verilog"),
                            *((("COCOTB_ENABLE_PROFILING", "1"),) if self.profile_replay else ()),
                        ),
                        timeout_s=budget),)),),
                    outputs=("driven.json", "results.xml", "native_prefix.json", "replay_prefix.json", "replay_refusal.json", "replay_progress.json",
                             *(("cocotb.pstat",) if self.profile_replay else ())),
                    read_only_mounts=(build_mount,),
                    role=f"verilator-simulate:{stimulus.fingerprint[:12]}",
                    limits=default_limits(wall_seconds=budget + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS),
                    runtime_profile="simulation",
                    frozen_runtime=simulation_runtime,
                    _checkpoint_service=checkpoint_service,
                ))
                self._record_tool_execution(execution)
                self._retain_replay_profile(execution, stimulus)
            except ToolIsolationError as error:
                return position, RunFailure.infrastructure(
                    "rtl-simulation-service",
                    Refusal(
                        error.code, error.why,
                        stimulus=stimulus.fingerprint,
                        **progress_detail(error.execution),
                        tool_execution=error.execution.as_dict()))
            row = command_result(execution, "verilator-simulate")
            if row.get("timed_out"):
                # A shared scoring/search deadline is control flow; it must not
                # become a standalone resource-zero after a clean tool shutdown.
                from ..execution_control import check_execution

                check_execution()
                return position, budget_failure(
                    purpose, "run",
                    Refusal(
                        "rtl-artifact-timed-out",
                        "this stimulus did not finish inside its isolated replay "
                        "budget",
                        seconds=budget, stimulus=stimulus.fingerprint,
                        **progress_detail(execution),
                        tool_execution=execution.as_dict()))
            if "native_prefix.json" in execution.outputs:
                from .replay_interruption import interrupted_replay

                return position, interrupted_replay(
                    execution.outputs["native_prefix.json"], stimulus, self._frontend_catalogue,
                    expected_plan=None if native is None else native.plan,
                    execution=execution.as_dict(),
                    replay_payload=execution.outputs.get("replay_prefix.json"),
                    refusal_payload=execution.outputs.get("replay_refusal.json"))
            if (checkpoint_service is not None and checkpoint_service.stop_requested
                    and row.get("returncode") == 0 and "driven.json" not in execution.outputs):
                return position, checkpoint_result
            if "driven.json" not in execution.outputs:
                diagnostic = (
                    command_stream(execution, "verilator-simulate", "stdout")
                    + command_stream(execution, "verilator-simulate", "stderr")
                ).decode("utf-8", errors="replace")
                reason = Refusal(
                    "rtl-artifact-no-trace",
                    "the isolated simulator produced no record for this stimulus",
                    returncode=row.get("returncode"),
                    output="\n".join(diagnostic.strip().splitlines()),
                    stimulus=stimulus.fingerprint,
                    **progress_detail(execution),
                    tool_execution=execution.as_dict())
                failure = (
                    RunFailure.candidate("candidate-run", reason)
                    if purpose is ReplayPurpose.CONCRETE and native is None
                    and row.get("returncode") not in (None, 0)
                    else RunFailure.verifier("rtl-trace-collection", reason)
                )
                return position, failure
            return position, RawTrace(execution.outputs["driven.json"], execution)

        def complete(item):
            start = perf_counter()
            position, got = one(item)
            replay_seconds = perf_counter() - start
            if type(got) is RawTrace:
                got = got.decode(postprocessor, stimuli[position], replay_seconds)
                replay_seconds = perf_counter() - start
            if _consume is not None:
                # Sim's private assessment continuation shares this worker slot.
                # The next simulation is not admitted until it releases the slot.
                try:
                    got = _consume(stimuli[position], got, replay_seconds=replay_seconds)
                except Exception as exc:  # noqa: BLE001 - trusted postprocessor
                    got = RunFailure.verifier(
                        "trace-postprocessing", Refusal(
                            "verifier-postprocessing-failed",
                            "the run postprocessor did not produce its assessment",
                            error=type(exc).__name__))
            return position, got

        try:
            with (
                reserve_cpus(min(workers, len(runnable))) as admitted,
                TracePostprocessor(trace_plan, _monitor_plan, admitted) as postprocessor,
            ):
                out = dict(_ordered_map(complete, enumerate(runnable), admitted,
                    stop_when=(lambda _: _stop()) if _stop is not None else None))
        finally:
            # The scheduler has joined every active worker, including failures,
            # before their build mount and invocation files may be removed.
            shutil.rmtree(root, ignore_errors=True)
        # **Assembled by position, not by `or`.** A `Refusal` refuses to be a truth
        # value, and `refused.get(i) or out.get(i)` asks it to be one -- which is the
        # conflation the type exists to stop, and it stopped this.
        assembled = []
        for position in range(len(stimuli)):
            if position in refused:
                assembled.append(refused[position])
            elif position in out:
                assembled.append(out[position])
            elif _stop is not None and _stop():
                # Private scoring continuation: no process was started. The
                # evaluator retains this member in its explicit unexecuted set.
                assembled.append(None)
            else:
                assembled.append(RunFailure.infrastructure(
                    "rtl-replay-coordination",
                    Refusal(
                        "rtl-artifact-not-attempted",
                        "this stimulus was neither refused before the run nor recorded "
                        "by one, so nothing here says what happened to it",
                        position=position)))
        return assembled

    def exact_trace(self, contract, stimulus, *, observe_names, state_names,
                    state_widths=None, initial_state=None, startup_state=None, native_events=None):
        """Replay one stimulus with the complete observation an exact audit names.

        The ordinary scoring path records the task-visible pins.  A bounded RTL audit
        additionally names every output and represented register carried by its frozen
        Yosys relation.  Those names are supplied by that relation rather than detected
        again here.  The same isolated Verilator process and :class:`Driver` still own
        the run. An optional analysis-only initial choice is installed before the first
        scheduled event and checked against the returned initial-state observation.
        The exact product, not this runner, owns membership in initial support.
        ``startup_state`` instead installs a construction choice before HDL initial
        blocks execute, including its explicit initial environment pin image.
        It cannot be combined with the legacy settled-state ``initial_state``.
        """

        state_names = tuple(state_names)
        got = self.traces_per_process(
            contract, (stimulus,), workers=1,
            observe_names=tuple(dict.fromkeys((*observe_names, *state_names))),
            state_names=state_names,
            state_widths=state_widths, _initial_state=initial_state, _startup_state=startup_state,
            native_events=native_events, _purpose=ReplayPurpose.ANALYSIS)[0]
        if type(got) is RunFailure:
            raise Refused(Refusal(got.reason.kind, got.reason.why,
                **{**got.reason.detail, "failure": got.as_dict()}))
        return got

    @property
    def identity(self):
        """A digest of the source this will run, matching `realization_of_source`.

        Read at use rather than stored, so an artifact pointed at a file that changed
        under it stops matching the admission that was about the old contents -- which is
        the case the identity exists for.
        """
        import hashlib
        from pathlib import Path

        return hashlib.sha256(
            (str(self.top_module) + "\0"
             + Path(self.rtl_path).read_text()).encode()).hexdigest()

    def trace(self, contract, stimulus, *, expected_outputs=None):
        """Replay one stimulus through the same isolated runner used for a set."""
        got = self.traces_per_process(contract, [stimulus], workers=1,
            _completion_targets=None if expected_outputs is None else
            {stimulus.fingerprint: expected_outputs})[0]
        if type(got) is RunFailure:
            from ..core.failure import FailureRaised

            raise FailureRaised(got)
        return got

    def reset_audit(self, contract, stimuli, budget_s=None):
        """Auxiliary reset probe; independent of the concrete score."""
        from .batch_replay import reset_audit

        return reset_audit(self, contract, stimuli, budget_s=budget_s)

    def traces(self, contract, stimuli, verify_batch=True):
        """Diagnostic shared-process replay, optionally checked against isolated runs."""
        from .batch_replay import traces

        return traces(self, contract, stimuli, verify_batch=verify_batch)

    def _verified_against_isolated(self, contract, order, out, runnable):
        from .batch_replay import _verified_against_isolated

        return _verified_against_isolated(self, contract, order, out, runnable)

    def _trace_of(self, contract, stimulus, records, check_channel_shape, identity,
                  *, expected_state_widths=None, expected_native=None, observation=None):
        from .trace_decoder import TraceDecoder

        return TraceDecoder(self._frontend_catalogue, dict(self.run_policy)).decode(
            contract, stimulus, records, check_channel_shape, identity,
            expected_state_widths=expected_state_widths,
            expected_native=expected_native, observation=observation)
