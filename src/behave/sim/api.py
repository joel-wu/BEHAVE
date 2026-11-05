"""Admit a candidate against a task, evaluate it, and analyse the result.

The public path is::

    TaskContract -> Realization -> admit() -> AdmittedTask -> BehaviorHandle | RTLHandle
                                                               |
                                                               +-> command_request()
                                                                       |
                                                                       +-> handle_command()
                                                                             -> CommandResult

Each stage consumes an immutable record from :mod:`behave.sim.core`.
``Realization`` may come from the isolated Behavior or RTL frontend; everything after
admission is shared. Exact analysis is optional and remains separate from the score:
:func:`analyse` returns a typed refusal when no bounded instance is available and cannot
rewrite the evaluation it was given. Artifact commands are score-only unless
``run_analysis=True`` is requested. :func:`prepare_rtl` freezes the admitted
artifact, catalogue, and deferred analysis request; relation and solver construction
begin only after concrete evaluation.
Alternatively, :func:`analyse_command` consumes an evaluated ``CommandResult``
and returns a separately retained outcome without changing its persisted score
or rerunning generation/replay. Prepare Behavior with ``bounded_analysis=True``
to freeze the corresponding capability before generation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from behave.core import runtime_defaults as defaults
from behave.isolation.runtime_environment import runtime_environment

from .analysis_service import analyse
from .authority import RESULT_VERSION
from .baseline import (
    BaselinePolicy,
    BaselineSet,
    build_baseline,
)
from .behavior.source_bundle import BehaviorSourceBundle, PreparedBehaviorSource, prepare_behavior_bundle
from .cocotb import (
    COCOTB_RUN_VERSION,
    CocotbResult,
    development_limits,
    prepare_development_environment,
    run_cocotb_test,
)
from .command import (
    COMMAND_VERSION,
    CommandRequest,
    CommandResult,
    CommandStatus,
    analyse_command,
    handle_command,
)
from .core.admission import AdmittedTask, Port, Realization, admit
from .core.analysis import AuditDisposition, RecordAudit
from .core.audit import AuditCheck, AuditCheckStatus, AuditedWitness
from .core.audit_plan import AuditKind, AuditPlan
from .core.catalogue import (
    COVERAGE,
    SAFETY,
    CatalogueManifest,
    Goal,
    OccurrenceKey,
)
from .core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from .core.compare import Comparison, compare, normalise
from .core.conditional_evidence import (
    ConditionalEvidence,
    PassedCheck,
    TrustedAssumption,
)
from .core.contract import (
    APB_ACCESS,
    AXI_LITE_READ,
    AXI_LITE_WRITE,
    SAMPLED_EDGE,
    SETTLED,
    START_DONE,
    TASK_CONTRACT_VERSION,
    Adapter,
    ClockDomain,
    Direction,
    Endpoint,
    Field,
    FiniteType,
    Kind,
    Matching,
    ProtocolInstance,
    ResetPolicy,
    TaskContract,
    ValueType,
    apb_access,
    axi_lite_read,
    axi_lite_write,
    ready_valid,
    sampled_edge,
    settled,
    start_done,
)
from .core.failure import (
    FailureDisposition,
    FailureOrigin,
    FailureRaised,
    ObservedGoals,
    RunFailure,
)
from .core.framing import framed, idle_tail, reset_then
from .core.goal_provider import (
    CONCRETE_HIT,
    CONCRETE_PHASE,
    DECLARATION,
    DISABLED,
    ENABLED,
    EXACT_ANALYSIS_PHASE,
    EXACT_BOUNDED_HIT,
    INAPPLICABLE,
    INCOMPLETE,
    LOCAL_GUIDANCE,
    RELEASE_PHASE,
    CapabilityAdapter,
    CapabilityBinding,
    DeclarativeConcreteStep,
    DeclarativeGoalSemantics,
    DeclarativeGuidanceAdapter,
    DeclarativeGuidanceStep,
    DeclarativeGuidanceWitness,
    DeclarativeSymbolicStep,
    FiniteAlgebra,
    FiniteMonitor,
    FiniteMonitorEncoding,
    FiniteMonitorStep,
    FiniteMonitorTransition,
    FinitePredicate,
    FinitePredicateEncoding,
    FiniteVariable,
    GoalCapabilityUse,
    GoalPolicy,
    GoalPolicySelection,
    GoalProvider,
    GoalProviderIdentity,
    GoalProviderRegistry,
    GoalSchemaDefinition,
    VersionedReason,
    standard_goal_schemas,
)
from .core.input_framing import FrameLevel, InputFraming
from .core.latency_goal import LatencyBin, LatencyEvent, LatencyGoal, LatencyTimebase
from .core.memory import (
    EXTERNAL_MEMORY_PROTOCOL,
    MEMORY_ENVIRONMENT_VERSION,
    MemoryEnvironment,
    MemoryEnvironmentState,
    MemoryInterface,
    MemoryPort,
    MemoryRealization,
    MemoryRun,
    MemoryState,
)
from .core.memory_sampling import (
    MEMORY_SAMPLING_VERSION,
    MemorySampleLane,
    MemorySampleRegion,
    MemorySampling,
)
from .core.monitor import Violation
from .core.observation_goal import GoalCondition, GoalField, TransactionGoal
from .core.operation_goal import OperationGoal, OperationRule
from .core.outcome import AnalysisOutcome, ConcreteEvaluation, Verdict
from .core.protocol import receptive_controls
from .core.reference import (
    LoadedReference,
    ReferenceArtifact,
    SlotEvent,
)
from .core.reference import (
    load_reference as _load_reference,
)
from .core.refusal import Refusal, Refused
from .core.run_record import (  # noqa: F401 -- legacy diagnostic attributes
    ONE_AT_A_TIME,
    ONE_PROCESS_BATCH,
    PER_PROCESS,
    VERIFIED_EVERY,
    VERIFIED_NOT_NEEDED,
    VERIFIED_SAMPLE,
    RunResult,
)
from .core.runtime_schedule import ReplayPolicy
from .core.source_rules import SourceReadRule
from .core.stimulus import AuditedTrace, Completion, EventSlot, SlotRecord, Stimulus
from .core.transaction_monitor import ScheduleEvent, TransactionEvent, TransactionMonitorSpec
from .cpu_admission import CpuPool, CpuPoolEndpoint, CpuUsage, cpu_capacity, cpu_scope, cpu_usage
from .declarations import Declarations, contract_of_task, declarations_at
from .declarative_generation import (
    DeclarativeCandidateMapping,
    DeclarativeCandidateRealizer,
    DeclarativeGenerationProvider,
)
from .diagnostics import DiagnosticOptions, command_diagnostics, evaluation_diagnostics, failure_diagnostic
from .evaluation import (
    compare_run,
    expected_for,
    transfers,
)
from .generation_protocol import GenerationProvider, ProbeRequest, ProbeSelection, StimulusField
from .generation import (GeneratedStimulusSet, GenerationPortfolio, generate_stimuli)
from .generation_cache import GenerationCacheTelemetry
from .generation_defaults import (
    DEFAULT_BASELINE_CASES,
    DEFAULT_SEQUENCE_LENGTH,
    DEFAULT_STIMULUS_SEED,
)
from .goal_policy import (
    evaluation_manifest,
    evaluation_policy,
)
from .preparation import prepare_behavior, prepare_rtl
from .public_handle import (
    BehaviorConfig,
    BehaviorHandle,
    RTLConfig,
    RTLHandle,
)
from .replay_suite import ReplaySuite, build_replay_suite, workload_window
from .result_store import CommittedDecision
from .rtl.source_bundle import PreparedRTLSource, RTLSourceBundle
from .scoring_policy import ScoringPolicy, ScoringStopped, scoring_scope
from .search_policy import DEFAULT_SEARCH_SECONDS, SearchPolicy
from .stimulus_set import GenerationSpec
from .tool_execution import (
    ToolEnvironment,
    command_log,
    command_stream,
    prepare_runtime,
    prepare_tool_environment,
)
from .tool_execution import (
    default_limits as tool_limits,
)
from .witness_pool import (
    MinimizedWitnessPool,
    minimize_witnesses,
    secondary_pool_report,
)

if TYPE_CHECKING:
    from .tool_execution import ToolCommand, ToolExecution, ToolGroup

def __getattr__(name):
    if name in {"ToolCommand", "ToolExecution", "ToolGroup"}:
        from . import tool_execution

        return getattr(tool_execution, name)
    raise AttributeError(name)


__all__ = [
    "APB_ACCESS",
    "AXI_LITE_READ",
    "AXI_LITE_WRITE",
    "COCOTB_RUN_VERSION",
    "COMMAND_VERSION",
    "CONCRETE_HIT",
    "CONCRETE_PHASE",
    "COVERAGE",
    "DECLARATION",
    "DEFAULT_BASELINE_CASES",
    "DEFAULT_SEARCH_SECONDS",
    "DEFAULT_SEQUENCE_LENGTH",
    "DEFAULT_STIMULUS_SEED",
    "DISABLED",
    "ENABLED",
    "EXACT_ANALYSIS_PHASE",
    "EXACT_BOUNDED_HIT",
    "EXTERNAL_MEMORY_PROTOCOL",
    "INAPPLICABLE",
    "INCOMPLETE",
    "ISOLATED_REFERENCE_ADAPTER",
    "LOCAL_GUIDANCE",
    "MEMORY_ENVIRONMENT_VERSION",
    "MEMORY_SAMPLING_VERSION",
    "RELEASE_PHASE",
    "RESULT_VERSION",
    "SAFETY",
    "SAMPLED_EDGE",
    "SETTLED",
    "START_DONE",
    "TASK_CONTRACT_VERSION",
    "Adapter",
    "AdmittedTask",
    "AnalysisOutcome",
    "AuditCheck",
    "AuditCheckStatus",
    "AuditDisposition",
    "AuditKind",
    "AuditPlan",
    "AuditedTrace",
    "AuditedWitness",
    "BaselinePolicy",
    "BaselineSet",
    "BehaviorConfig",
    "BehaviorHandle",
    "BehaviorSourceBundle",
    "CapabilityAdapter",
    "CapabilityBinding",
    "CatalogueManifest",
    "ClockDomain",
    "ClockTiming",
    "CocotbResult",
    "CommandRequest",
    "CommandResult",
    "CommandStatus",
    "CommittedDecision",
    "Comparison",
    "Completion",
    "ConcreteEvaluation",
    "ConditionalEvidence",
    "CpuPool",
    "CpuPoolEndpoint",
    "CpuUsage",
    "Declarations",
    "DeclarativeCandidateMapping",
    "DeclarativeCandidateRealizer",
    "DeclarativeConcreteStep",
    "DeclarativeGenerationProvider",
    "DeclarativeGoalSemantics",
    "DeclarativeGuidanceAdapter",
    "DeclarativeGuidanceStep",
    "DeclarativeGuidanceWitness",
    "DeclarativeSymbolicStep",
    "DiagnosticOptions",
    "Direction",
    "Endpoint",
    "EventSlot",
    "FailureDisposition",
    "FailureOrigin",
    "FailureRaised",
    "Field",
    "FiniteAlgebra",
    "FiniteMonitor",
    "FiniteMonitorEncoding",
    "FiniteMonitorStep",
    "FiniteMonitorTransition",
    "FinitePredicate",
    "FinitePredicateEncoding",
    "FiniteType",
    "FiniteVariable",
    "FrameLevel",
    "GeneratedStimulusSet",
    "GenerationCacheTelemetry",
    "GenerationPortfolio",
    "GenerationProvider",
    "GenerationSpec",
    "GlobalEventSlot",
    "GlobalSchedule",
    "Goal",
    "GoalCapabilityUse",
    "GoalCondition",
    "GoalField",
    "GoalPolicy",
    "GoalPolicySelection",
    "GoalProvider",
    "GoalProviderIdentity",
    "GoalProviderRegistry",
    "GoalSchemaDefinition",
    "InputFraming",
    "Kind",
    "LatencyBin",
    "LatencyEvent",
    "LatencyGoal",
    "LatencyTimebase",
    "LoadedReference",
    "Matching",
    "MemoryEnvironment",
    "MemoryEnvironmentState",
    "MemoryInterface",
    "MemoryPort",
    "MemoryRealization",
    "MemoryRun",
    "MemorySampleLane",
    "MemorySampleRegion",
    "MemorySampling",
    "MemoryState",
    "MinimizedWitnessPool",
    "ObservedGoals",
    "OccurrenceKey",
    "OperationGoal",
    "OperationRule",
    "PassedCheck",
    "Port",
    "PreparedBehaviorSource",
    "PreparedRTLSource",
    "ProbeRequest",
    "ProbeSelection",
    "ProtocolInstance",
    "RTLConfig",
    "RTLHandle",
    "RTLSourceBundle",
    "Realization",
    "RecordAudit",
    "ReferenceArtifact",
    "Refusal",
    "Refused",
    "ReplayPolicy",
    "ReplaySuite",
    "ResetPolicy",
    "RunFailure",
    "RunResult",
    "ScheduleEvent",
    "ScoringPolicy",
    "ScoringStopped",
    "SearchPolicy",
    "SlotEvent",
    "SlotRecord",
    "SourceReadRule",
    "Stimulus",
    "StimulusField",
    "TaskContract",
    "ToolCommand",
    "ToolEnvironment",
    "ToolExecution",
    "ToolGroup",
    "TransactionEvent",
    "TransactionGoal",
    "TransactionMonitorSpec",
    "TrustedAssumption",
    "ValueType",
    "Verdict",
    "VersionedReason",
    "Violation",
    "admit",
    "analyse",
    "analyse_command",
    "apb_access",
    "axi_lite_read",
    "axi_lite_write",
    "behavior_source_environment",
    "build_baseline",
    "build_replay_suite",
    "bundle_for_entry",
    "command_diagnostics",
    "command_log",
    "command_stream",
    "compare",
    "compare_run",
    "contract_of_task",
    "cpu_capacity",
    "cpu_scope",
    "cpu_usage",
    "declarations_at",
    "development_limits",
    "evaluation_diagnostics",
    "evaluation_manifest",
    "evaluation_policy",
    "expected_for",
    "failure_diagnostic",
    "framed",
    "generate_stimuli",
    "handle_command",
    "idle_tail",
    "load_reference",
    "minimize_witnesses",
    "normalise",
    "prepare_behavior",
    "prepare_behavior_bundle",
    "prepare_behavior_generation",
    "prepare_development_environment",
    "prepare_rtl",
    "prepare_rtl_bundle",
    "prepare_runtime",
    "prepare_tool_environment",
    "ready_valid",
    "realization_of_behavior_source",
    "realization_of_rtl_source",
    "realization_of_task_interface",
    "receptive_controls",
    "reset_then",
    "run_cocotb_test",
    "runtime_environment",
    "sampled_edge",
    "scoring_scope",
    "secondary_pool_report",
    "settled",
    "standard_goal_schemas",
    "start_done",
    "tool_limits",
    "transfers",
    "workload_window",
]


def behavior_source_environment() -> dict:
    """Describe this build's Python name availability without executing a model.

    This is construction input, not an admission certificate: supported names
    do not imply that every call form, value or program can be lowered. It is
    shared with the reference interpreter, not a second independently curated
    list for generators. Callers should record the returned bytes with their
    construction inputs so a later runtime upgrade cannot change old evidence.
    """
    from .behavior.method_binding import METHOD_DECORATORS
    from .behavior.source_intrinsics import APPROVED_MODULES, BUILTIN_NAMES, CONCRETE_MODULES
    from .behavior.source_syntax import FUTURE_FEATURES

    return {
        "schema": "behave.behavior-source-environment/1",
        "reference_adapter": ISOLATED_REFERENCE_ADAPTER,
        "builtins": sorted(BUILTIN_NAMES),
        "method_decorators": sorted(METHOD_DECORATORS),
        "approved_modules": sorted(CONCRETE_MODULES),
        "exact_modules": sorted(APPROVED_MODULES),
        "future_features": sorted(FUTURE_FEATURES),
        "description": "Only these Python builtins and approved library imports are provided. "
                       "The listed __future__ compiler directives are also supported; "
                       "annotations postpones type expressions without evaluating them. "
                       "Numerical helpers and task steps may use unshadowed @staticmethod "
                       "or @classmethod; reset/construction methods remain instance methods. "
                       "Custom decorators are not supported. Classmethods currently "
                       "support concrete execution, not exact IR lowering. "
                       "Transaction process inputs are passed by name; **kwargs may "
                       "receive remaining inputs by their declared port spelling "
                       "(field name without a port), with *args left empty. "
                       "A positional-only variadic interface has no inferred field order. "
                       "Variadic task entries are not yet exact-lowerable. "
                       "An exact process() method is the transaction entry; "
                       "process_* names beside it are helpers, not inferred clocks. "
                       "Without process(), process_<domain>() handlers must match "
                       "the task's declared multi-clock interface. "
                       "math, struct, map and bytes are available in concrete execution; "
                       "this does not add their exact lowering. Generator expressions "
                       "retain Python's lazy evaluation in single- and multi-file source. "
                       "Wildcard imports are resolved for approved libraries only. "
                       "Name availability is not a guarantee of lowering or bounded analysis.",
    }


ISOLATED_REFERENCE_ADAPTER = "transaction/isolated-6"


def realization_of_behavior_source(
    source, contract, name="the Behavior candidate", *, file="behavior.py",
    timeout_s=defaults.SIM_BEHAVIOR_WORKER_SECONDS,
    model=None,
):
    """Return the task-bound public realization of a Behavior source.

    Model selection, task-boundary checking, and source identity construction run in a
    named isolated service.  Bounded lowering is deliberately not part of realization.
    ``file`` is the stable source-site label used by goal identifiers and must be reused
    by :class:`BehaviorConfig`. ``model`` selects an explicit class identifier; reuse
    that selector in the config too. Omitting it requires an unambiguous source entry.
    The result enters the same :func:`admit` and command path as an RTL realization; no
    Behavior-specific comparison pipeline is introduced here.
    """

    from .behavior.realization import realization_of_source

    return realization_of_source(
        source, contract, name=name, file=file, timeout_s=timeout_s, model=model)


def realization_of_rtl_source(source, name="the candidate", *, top=None, contract=None):
    """Elaborate RTL source into the public :class:`Realization` used by admission.

    External consumers should not import the frontend-owned ``contract_of`` module to
    discover a candidate interface.  This facade operation returns only the immutable
    public record; task semantics still come from the independently supplied
    :class:`TaskContract` and are checked by :func:`admit`.

    :class:`FailureRaised` carries frontend failure ownership and its score
    disposition. A source rejection is a candidate failure; a failed tool or
    verifier produces no result. :class:`Refused` denotes an unsupported boundary.
    """

    if type(source) is not str or not source:
        raise TypeError("RTL realization source must be a nonempty exact string")
    if type(name) is not str or not name:
        raise TypeError("RTL realization name must be a nonempty exact string")
    if top is not None and (type(top) is not str or not top):
        raise TypeError("RTL top is a nonempty exact string or absent")
    from .contract_of import realization_of_source

    return _rtl_frontend_call(realization_of_source, source, name, top=top, contract=contract)


def realization_of_task_interface(spec, name="the task's RTL interface declaration", *, contract=None):
    """Elaborate a specification's interface for admission, not functional replay.

    Header-only declarations are closed without supplying a body. Invalid task
    declarations are reference failures; tool/verifier failures retain their
    attribution. The caller still checks the realization against its contract
    with :func:`admit`.
    """
    from .core.schema import InterfaceError
    from .task import interface_module_source

    if type(spec) is not str:
        raise TypeError("task specification must be an exact string")
    try:
        source = interface_module_source(spec)
    except InterfaceError as exc:
        raise FailureRaised(RunFailure.reference("task-interface", Refusal(
            "task-interface-invalid", "the supplied task declaration could not be read",
            error=str(exc)))) from None
    try:
        return realization_of_rtl_source(source, name, contract=contract)
    except FailureRaised as exc:
        if exc.failure.origin is FailureOrigin.CANDIDATE:
            raise FailureRaised(RunFailure.reference(
                "task-interface", exc.failure.reason)) from exc
        raise


def bundle_for_entry(files, entry):
    """Build a captured RTL manifest with explicit source/verifier attribution."""
    from .rtl.source_bundle import bundle_for_entry as build

    return _rtl_frontend_call(build, files, entry)


def prepare_rtl_bundle(bundle, *, timeout_s=defaults.SIM_RTL_BUNDLE_SECONDS):
    """Normalize a captured RTL bundle with the same failure ownership as elaboration."""
    from .rtl.source_bundle import prepare_rtl_bundle as prepare

    if type(bundle) is not RTLSourceBundle or type(timeout_s) is not int or timeout_s < 1:
        raise TypeError("preprocessing requires an RTLSourceBundle and a positive timeout")
    return _rtl_frontend_call(prepare, bundle, timeout_s=timeout_s)


def _rtl_frontend_call(operation, *args, **kwargs):
    """One attribution rule for public compiler/preprocessor operations."""
    from .core.schema import InterfaceError
    from .rtl.frontend_error import SourceRejected, tool_failure
    from .rtl.tool_protocol import ToolIsolationError

    try:
        return operation(*args, **kwargs)
    except SourceRejected as exc:
        raise FailureRaised(RunFailure.candidate(
            "candidate-frontend", exc.reason)) from None
    except FailureRaised:
        raise
    except ToolIsolationError as exc:
        raise FailureRaised(tool_failure(exc)) from None
    except OSError as exc:
        raise FailureRaised(RunFailure.infrastructure(
            "rtl-frontend", Refusal("rtl-frontend-unavailable",
                "the RTL frontend could not be started", error=type(exc).__name__))) from None
    except InterfaceError as exc:
        raise FailureRaised(RunFailure.verifier(
            "rtl-frontend", Refusal("rtl-frontend-output-invalid",
                "the RTL frontend did not return a valid interface", error=str(exc)))) from None
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - trusted frontend, not source execution
        raise FailureRaised(RunFailure.verifier(
            "rtl-frontend", Refusal("rtl-frontend-failed",
                "the trusted frontend failed while preparing the candidate interface",
                error=type(exc).__name__))) from None


def load_reference(adapter, sources, config=None, loader=""):
    """Load a frozen reference through a production adapter.

    The isolated adapter is registered lazily at this public entry point so its
    availability cannot depend on which implementation module a caller happened to
    import first.  The facade deliberately exposes no registration operation: external
    code may select the production adapter, but cannot install an in-process executor
    for submitted source.
    """

    if type(adapter) is not str:
        raise TypeError("a reference adapter is an exact string")
    if adapter == ISOLATED_REFERENCE_ADAPTER:
        # Importing this trusted module performs the one idempotent registration of the
        # production adapter.  It does not execute the submitted source in this process.
        from . import reference_isolated  # noqa: F401
    return _load_reference(adapter, sources, config=config, loader=loader)


def _bind_behavior_exact(task, behavior, bound):
    """Internal adapter from a lowered Behavior to the frozen complete catalogue.

    The frontend manifest is owned by ``behavior`` and was frozen when it was lowered.
    A transaction-level Behavior procedure has no pin-timing monitor component; this
    function freezes that artifact-aware policy and binds the exact instance to the same
    bytes.  Public callers use :func:`prepare_behavior`; this adapter remains for
    trusted frontend integration and focused representation tests.
    """

    from .behavior.instance import BehaviorInstance

    if type(task) is not AdmittedTask:
        raise TypeError("binding Behavior exact analysis needs an AdmittedTask")
    if type(behavior) is not BehaviorInstance:
        raise TypeError("binding Behavior exact analysis needs a BehaviorInstance")
    if task.realization.identity != behavior.analysis_identity():
        raise Refused(
            Refusal(
                "behavior-exact-artifact-mismatch",
                "the admitted realization and selected Behavior transition system have different identities",
                admitted=task.realization.identity,
                behavior=behavior.analysis_identity(),
            )
        )
    complete = evaluation_manifest(task, behavior.frontend_catalogue_manifest())
    return behavior.bind(bound, task_contract=task.contract.digest, catalogue_manifest=complete)




def prepare_behavior_generation(
    task,
    generation_spec,
    reference,
    trace_of,
    *,
    traces_of=None,
    targets=None,
    timeout_s=defaults.SIM_BEHAVIOR_WORKER_SECONDS,
    timeout_ms=defaults.SIM_SEARCH_QUERY_MS,
    cache_root=None,
):
    """Construct the closed source-Behavior guidance provider for target ``A``.

    Source bytes and construction configuration come only from the authenticated
    :class:`LoadedReference` frozen by admission.  The returned provider is a sealed,
    solver-free parent proxy: parsing, lowering, concrete IR replay, position planning
    and Z3 all run behind registered named jobs.  ``trace_of`` is the read-only target
    replay capability used to observe accepted same-slot endpoint batches.
    """

    from .engines.behavior.generation import (
        BehaviorIsolatedGenerationProvider,
    )

    if type(task) is not AdmittedTask:
        raise TypeError("preparing Behavior guidance needs an AdmittedTask")
    if type(generation_spec) is not GenerationSpec:
        raise TypeError("preparing Behavior guidance needs a GenerationSpec")
    if type(reference) is not LoadedReference:
        raise TypeError("preparing Behavior guidance needs a LoadedReference")
    return BehaviorIsolatedGenerationProvider(
        task,
        task.realization.identity,
        generation_spec,
        reference,
        trace_of,
        traces_of=traces_of,
        targets=targets,
        timeout_s=timeout_s,
        timeout_ms=timeout_ms,
        cache_root=cache_root,
    )
