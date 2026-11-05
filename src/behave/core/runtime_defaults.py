"""Operator-chosen runtime defaults, grouped by the component they configure.

Edit defaults here, or supply explicit per-run policies. Saved policies keep
their explicit values. These are operational choices, not task semantics or
claims about sufficient search/time budgets. Sim exposes these values through
its public policies and owns their validation. Algorithm recipes, hardware
layouts and experiment settings stay with their
respective configurations. Sim construction/transport ceilings are also listed
here, separately from time budgets; their owning boundaries validate and report
them. Increasing time does not silently increase memory or representation work.
No configuration loader, import-time I/O, or runtime-global mutable policy.
"""

# Log retention does not impose a size cap on structured worker records.
LOG_RETAIN_BYTES = 1 << 20
# One post-stop cleanup allowance for source services and their namespace owner.
# Graceful exit, forced stop, status collection and output drain share the
# owner's deadline; nested helpers do not each receive a fresh allowance.
# This is not execution time. An unconfirmed reap must retain the original
# failure and report cleanup separately, never relabel the candidate result.
SERVICE_CLEANUP_SECONDS = 5
ISOLATION_DISCOVERY_SECONDS = 30
# CPU admission already bounds executing jobs and replay waves. Do not add a
# node-wide serial startup queue by default. Operators may explicitly throttle
# namespace startup with BEHAVE_ISOLATION_STARTUP_SLOTS; its wait still consumes
# the caller's deadline. Disabling this gate does not disable sandbox isolation,
# per-job CPU limits, or descendant cleanup.
ISOLATION_STARTUP_SLOTS = None

# Shared agent loop.
AGENT_MAX_TURNS = 60
# Per model call: rendered input/history plus generated (including reasoning) tokens.
AGENT_CONTEXT_TOKENS = 131072
AGENT_MAX_RETRIES = 2
AGENT_RETRY_POLL_SECONDS = 0.05
# Exponential base cap; jitter and the server's minimum wait may exceed it.
AGENT_RETRY_BACKOFF_SECONDS = 60
# Model calls have no default wall-time cutoff. Operators may opt into one.
AGENT_REQUEST_SECONDS = None
# Queueing follows the caller's deadline unless an explicit wait cap is set.
ADMISSION_WAIT_SECONDS = None
ADMISSION_POLL_SECONDS = 0.05
PUBLICATION_WORKERS = 4
# Independent self-improvement task chains; production runtime selects concurrency.
IMPROVEMENT_WORKERS = 1
# Pagination of acquisition tools; neither value limits corpus eligibility.
SOURCE_SEARCH_PAGE_ITEMS = 20
SOURCE_READ_PAGE_CHARACTERS = 12000
# Chunking controls I/O granularity, not total content or record eligibility.
PUBLICATION_CHUNK_REFS = 512
PPA_DOCUMENT_CHUNK_BYTES = 4 << 20

# Same-host model/development RPC uses the caller's execution deadline.
# Polling and post-cancellation cleanup are separate lifecycle settings.
LOCAL_RPC_POLL_SECONDS = 0.05
LOCAL_RPC_CLEANUP_SECONDS = 10.0

# Allocated jobs and the default Python development tool.
JOB_CLEANUP_SECONDS = 10
# Read-only executable/scheduler metadata checks, not scoring or search time.
TOOL_INSPECTION_SECONDS = 30
# Whole allocated development-validation job, including build/install/tests.
# Release qualification continues to use its explicit tracked experiment policy.
VALIDATION_WALL_SECONDS = 720
# Receipt refresh interval only; never an execution deadline.
JOB_HEARTBEAT_SECONDS = 5
# Log retention only. Reaching this value no longer terminates a job.
JOB_OUTPUT_BYTES = 8 << 20
# Slurm step scheduling wait and receipt-publication margin after cleanup.
JOB_SLURM_WAIT_SECONDS = 15
JOB_SLURM_EXIT_SECONDS = 60
PYTHON_CPU_SECONDS = 10
PYTHON_RSS_BYTES = 512 << 20
PYTHON_PROCESSES = 1
PYTHON_FILE_BYTES = 32 << 20
PYTHON_WALL_SECONDS = 15
PYTHON_STDOUT_BYTES = 64 << 10
PYTHON_STDERR_BYTES = 64 << 10

# Solver shell runtime. These are installed tools, not a command-string filter.
SHELL_PROCESSES = 64
# Combined stdout/stderr preview sent to the model, not an execution limit.
SHELL_OUTPUT_PREVIEW_BYTES = 32 << 10
SHELL_TOOLS = (
    "bash", "cat", "ls", "sed", "awk", "grep", "find", "xargs", "head", "tail", "nl",
    "wc", "sort", "uniq", "cut", "tr", "tee", "seq", "od", "mkdir", "rmdir", "cp",
    "mv", "rm", "touch", "chmod", "ln", "readlink", "realpath", "dirname", "basename",
    "stat", "du", "date", "env", "diff", "cmp", "patch", "timeout", "sleep",
)

# Sim baseline and shared search. Length counts requested transactions per
# input endpoint, not DUT cycles or the exact-analysis horizon. Search time
# covers both directions together, not final concrete scoring or analysis.
# One mixed pool: primary typed patterns, then alternating seeded samples and
# pattern continuation pages. At 50 cases this is 42 directed + 8 random
# sequences, not an additional random pool. Exact duplicates merge afterward.
SIM_BASELINE_CASES = 50
SIM_SEQUENCE_LENGTH = 100
SIM_STIMULUS_SEED = "behave"
# Explicit fixed-schedule baseline recipes; not task protocol constants.
SIM_BASELINE_CLOCK_PERIODS = (2, 3, 5, 7, 11, 13)
SIM_BASELINE_MEMORY_READ_LATENCY = 1
SIM_BASELINE_MEMORY_STALL_RATE = 0.0
# Transaction-driven replay: wait for input/result completion, then observe a
# receptive tail. These are execution policy, not a preallocated total horizon.
SIM_RESET_HOLD_CYCLES = 3
SIM_DRAIN_SLOTS = 200
SIM_STALL_EVERY = 3
# Budget stops may retain fully compared transaction prefixes as progress and
# counterexample evidence. Passing still requires the entire fixed baseline.
# Public ScoringPolicy permits an explicit whole-run-only override.
SIM_TRANSACTION_CHECKPOINTS = True
SIM_SEARCH_SECONDS = 600.0
SIM_SEARCH_DEBUG = False
# No independent query cutoff: inherit the search allocation unless explicitly set.
SIM_SEARCH_QUERY_MS = None
# None uses this invocation's admitted CPU ceiling. One preserves serial search.
SIM_SEARCH_WORKERS = None

# Sim execution: per-job ceilings, clamped to the active invocation's remaining
# time. Frontend preparation does not set the whole optional-analysis budget;
# the analysis caller explicitly owns that separate lifetime.
SIM_BEHAVIOR_WORKER_SECONDS = 300
SIM_RTL_TOOL_SECONDS = 1800
SIM_RTL_BUNDLE_SECONDS = 60
# Diagnostic only: retain cocotb's Python execution profile for each replay.
# Profiling adds overhead; these timings are not unprofiled score benchmarks.
SIM_RTL_PROFILE_REPLAY = False
# Offline reference-pair qualification owns a whole-task diagnostic envelope,
# including preparation, all isolated baselines and the continuous workload.
# Its expiry means no result; it is not an execution-policy score or a search
# budget. Do not replace it with a per-task literal in each batch launch.
SIM_BASELINE_QUALIFICATION_SECONDS = 7200
# Automatic replay parallelism is also capped by CPU affinity; an explicit
# workers argument or BEHAVE_REPLAY_WORKERS takes precedence over this default.
SIM_REPLAY_AUTO_WORKERS = 8
# Coordinator-owned, host-local CPU admission. Queueing has no private timeout:
# it consumes the caller's deadline (and the search deadline while searching).
SIM_CPU_ADMISSION_POLL_SECONDS = 0.05
SIM_CPU_ADMISSION_CONTROL_SECONDS = 5.0
SIM_CPU_ADMISSION_MAX_CLIENTS = 512
SIM_CPU_ADMISSION_WIRE_BYTES = 4096

# Concrete source/reference isolation, separate from frontend job envelopes.
SIM_SOURCE_CPU_SECONDS = 20
SIM_SOURCE_WALL_SECONDS = 30  # Standalone reply/session; scoped calls share the owner's deadline.
SIM_SOURCE_RSS_BYTES = 4 << 30
SIM_SOURCE_PROCESSES = 1
SIM_SOURCE_FILE_BYTES = 8 << 20
SIM_SOURCE_WORK_BYTES = 8 << 20
SIM_SOURCE_NAMESPACE_NODES = 200_000
SIM_SOURCE_OPEN_FILES = 128
# stdout/stdin carry structured results/requests, not candidate print logs.
# None removes a traffic quota; process memory and time still bound execution.
SIM_SOURCE_STDOUT_BYTES = None
SIM_SOURCE_PROTOCOL_BYTES = None
SIM_SOURCE_REPLY_BYTES = None  # Optional per-response cap, independent of cumulative traffic.
SIM_SOURCE_STDERR_BYTES = 8 << 20
SIM_SOURCE_FACTS_SECONDS = 30
SIM_REFERENCE_CPU_SECONDS = 20
SIM_REFERENCE_WALL_SECONDS = 20  # Standalone reply/session; scoped calls share the owner's deadline.
SIM_REFERENCE_RSS_BYTES = 2 << 30
SIM_REFERENCE_PROCESSES = 8

# Named jobs and EDA process trees. These are release resource defaults, not
# parser/encoding validation ceilings or task-level capacities.
SIM_JOB_RSS_BYTES = 32 << 30
# Whole RTL Analysis process tree, including its retained transition and solver
# workers. Independent of scoring/search jobs; this does not grow per query.
SIM_RTL_ANALYSIS_RSS_BYTES = 64 << 30
SIM_JOB_WIRE_BYTES = None
SIM_JOB_PROCESSES = 64
SIM_JOB_CPU_FLOOR_SECONDS = 60
SIM_JOB_FILE_BYTES = 256 << 20
SIM_JOB_WORK_BYTES = 240 << 20
SIM_JOB_SHM_BYTES = 16 << 20
SIM_JOB_OPEN_FILES = 1024
SIM_JOB_STDERR_BYTES = 8 << 20
SIM_JOB_CLEANUP_SECONDS = 10
SIM_SEARCH_SESSION_WIRE_BYTES = None
SIM_EDA_COMMAND_SECONDS = 600
SIM_EDA_WALL_SECONDS = 900
SIM_EDA_CPU_FLOOR_SECONDS = 60
SIM_EDA_CPU_MULTIPLIER = 4
SIM_EDA_RSS_BYTES = 8 << 30
# Instrumented mapped RTL can have a much larger compiler AST than its source.
# This applies only to replay compilation, not to each simulation worker.
SIM_RTL_BUILD_RSS_BYTES = 32 << 30
SIM_EDA_PROCESSES = 192
SIM_EDA_FILE_BYTES = 2 << 30
SIM_EDA_OPEN_FILES = 1024
SIM_EDA_STDOUT_BYTES = None
SIM_EDA_STDERR_BYTES = 32 << 20
SIM_EDA_PROTOCOL_BYTES = None
SIM_RTL_CATALOGUE_SECONDS = 180
SIM_RTL_NATIVE_PREPARATION_SECONDS = 120
SIM_RTL_ELABORATION_SECONDS = 60.0
# Total fallback proof time for one source activation's access domains.
# This is translation preparation, not a fresh goal-search budget. Active
# search deadlines still bound it; ordinary interval proofs do not use a solver.
SIM_RTL_SOURCE_DOMAIN_PROOF_MS = 30_000
SIM_CHEAP_PROOF_QUERY_MS = 100

# Sim transport, retention and cleanup. These are operational bounds, not
# accepted-input counts, protocol queue capacities, or exact-analysis horizons.
SIM_SOURCE_BYTES = 16 << 20
SIM_SOURCE_BUNDLE_FILES = 256
SIM_NATIVE_VALUE_WIRE_BYTES = None
SIM_NATIVE_VALUE_WIRE_NODES = None
SIM_AUDITED_WITNESS_NODES = 4_194_304
SIM_EDA_DIAGNOSTIC_BYTES = 4096
SIM_EDA_ENVELOPE_OVERHEAD_SECONDS = 30
SIM_EDA_COMMAND_CLEANUP_SECONDS = 5
SIM_EDA_NORMALIZATION_RECURSION_LIMIT = 20_000
# Diagnostic publication cadence, not an execution/score limit.
SIM_RTL_REPLAY_PROGRESS_SECONDS = 10.0
SIM_RTL_SMT2_SECONDS = 180
SIM_RTL_OBSERVED_SMT2_SECONDS = 600
SIM_GENERATION_QUERY_OVERHEAD_SECONDS = 30
SIM_COCOTB_REPORT_BYTES = 1 << 20
SIM_CONSTRUCTION_POLL_SECONDS = 0.01
SIM_RESULT_LOCK_POLL_SECONDS = 0.02

# Sim frontend/representation work. Exhaustion is a typed refusal or an
# inconclusive analysis, never an unreachable goal or a passing score. The
# Behavior budgets module retains the published names/units and reporting.
SIM_BEHAVIOR_UNROLL_LIMIT = 256
SIM_BEHAVIOR_HELPER_ACTIVATIONS = 8
SIM_BEHAVIOR_SEMANTIC_IR_NODES = 1_000_000
SIM_BEHAVIOR_ENCODING_NODES = 1_000_000
SIM_BEHAVIOR_REPRESENTATION_DEPTH = 256
SIM_BEHAVIOR_SOURCE_PLAN_NODES = 100_000
SIM_BEHAVIOR_SOURCE_TEXT_CHARS = 1 << 20
SIM_BEHAVIOR_SCALAR_BITS = 8192
SIM_BEHAVIOR_ROM_EXPANSION_NODES = 100_000
SIM_BEHAVIOR_STATE_ROUNDS = 8
SIM_BEHAVIOR_STATE_MAGNITUDE = 1 << 40
SIM_BEHAVIOR_CONTROL_PROOF_NODES = 8_192
SIM_BEHAVIOR_POSITION_PROOF_NODES = 8_192
# Memoized syntax rewrites follow the owning construction's deadline. A fixed
# lifetime visit count disables all later narrowing on otherwise large inputs.
SIM_BEHAVIOR_REWRITE_PROOF_NODES = None
SIM_BEHAVIOR_RANGE_COMBINATIONS = 16
SIM_BEHAVIOR_SSA_PATHS = 256
SIM_BEHAVIOR_LOOP_EXIT_PATHS = 256
SIM_CONSTRUCTION_SHARE_ROOTS = 8192
SIM_CONSTRUCTION_SHARE_PAIRS = 100_000
SIM_CONSTRUCTION_WORKERS = 4
SIM_RTL_SWITCH_ARMS = 4096
SIM_RTL_STRUCTURAL_GOALS = 250_000
SIM_RTL_STRUCTURAL_OCCURRENCES = 500_000
SIM_RTL_SOURCE_EVENTS = 1 << 20
# Optional per-observation-interval quota. Normal runs are bounded by execution
# time; repeated source events must not be rejected solely for their count.
SIM_RTL_NATIVE_CALLBACKS = None
SIM_RTL_ROM_ELEMENT_BITS = 1 << 20

# Sim optional reuse/representation strategies. Cache misses take the original
# path; thresholds select equivalent representations, not a smaller task.
SIM_CONSTRUCTION_CACHE_BYTES = 32 << 20
SIM_GENERATION_CACHE_ENTRIES = 256
SIM_GENERATION_CACHE_BYTES = 64 << 20
SIM_GENERATION_CONTEXT_WIRE_BYTES = None
SIM_GENERATION_CONTEXT_IMAGE_BYTES = None
SIM_ANALYSIS_ANSWER_CACHE_ENTRIES = 256
SIM_SOURCE_FACTS_CACHE_ENTRIES = 256
SIM_RTL_SWITCH_CACHE_ENTRIES = 1024
SIM_RTL_SIGSPEC_CACHE_ENTRIES = 4096
# Pure native-event projection, per immutable observer. Zero disables reuse.
# The byte budget covers fixed-width event/goal payloads; the entry budget also
# bounds Python cache-node overhead. No execution evidence is cached or skipped.
SIM_RTL_EVENT_PROJECTION_CACHE_ENTRIES = 256
SIM_RTL_EVENT_PROJECTION_CACHE_BYTES = 4 << 20
SIM_RTL_EVENT_BYTE_PROJECTION_CACHE_ENTRIES = 1024
# Repeated owned rows on the private simulator wire; zero emits all rows inline.
SIM_RTL_TRACE_RECORD_CACHE_ENTRIES = 256
# One immutable projection may encounter the same decoded dictionary many times
# (for example a compact trace's backward references). Reuse its validated copy
# within that projection only; zero disables this storage optimization.
SIM_RECORD_PROJECTION_CACHE_ENTRIES = 256
SIM_FUNCTIONAL_ARRAY_CACHE_ENTRIES = 8
SIM_MEMORY_FULL_STATE_CELLS = 4096
SIM_BEHAVIOR_FUNCTIONAL_ARRAY_MIN_CELLS = 4096
# Equivalent source-loop summary selection, not a maximum admitted trip count.
SIM_BEHAVIOR_LOOP_SUMMARY_MIN_TRIPS = 8
SIM_RTL_BULK_INITIALIZATION_BYTES = 8 << 20
SIM_ANALYSIS_SHAPE_TELEMETRY_ASSERTIONS = 4_096

# Native Behavior bounded-analysis budgets; separate from a consumer's
# optional whole-analysis envelope and from frontend capability limits.
SIM_BEHAVIOR_ANALYSIS_QUERY_MS = 900_000
SIM_BEHAVIOR_ANALYSIS_MS = 600_000
SIM_BEHAVIOR_ANALYSIS_QUERIES = 4_096
# 8 GB killed eleven census models' exact analysis (2026-10-18); the solver
# share keeps its 3/8 of the process-tree ceiling.
SIM_BEHAVIOR_ANALYSIS_MEMORY_MB = 32_768
SIM_BEHAVIOR_SOLVER_MEMORY_MB = 12_288
SIM_ANALYSIS_WORKER_RESERVATION_MB = 1_024
SIM_BEHAVIOR_ANALYSIS_WIRE_MB = None
SIM_BEHAVIOR_POSITION_CLOSURE_MS = 60_000

# Legacy standalone exploration engines, NOT the public SearchPolicy budget.
# Engine selection/order stays in sim.engine_defaults; only defaults live here.
SIM_ENGINE_MAX_WITNESSES = 400
SIM_BEHAVIOR_ENGINE_SEED = 0xC051A
SIM_RTL_ENGINE_SEED = 0x2E17B
SIM_ENGINE_PHASE1_SEQUENCES = 4
# Fractions of the caller's existing budget, not extra execution allowances.
# Standalone dual: concrete seed sweep before symbolic probing.
SIM_DUAL_SEED_BUDGET_FRACTION = 0.25
# Legacy multi-clock diagnostics: generation before concrete replay.
SIM_LEGACY_MULTICLOCK_GENERATION_FRACTION = 0.5
SIM_BEHAVIOR_RANDOM_SECONDS = 300.0
SIM_DUAL_ENGINE_SECONDS = 180.0
SIM_STANDALONE_BEHAVIOR_SECONDS = 120.0
SIM_STANDALONE_RTL_SECONDS = 60.0
SIM_STANDALONE_MULTICLOCK_SECONDS = 60.0
SIM_STANDALONE_N_STEPS = 10
SIM_STANDALONE_MAX_WITNESSES = 200
SIM_DUAL_WORKERS = 8
SIM_RTL_DUAL_QUERY_MS = 30_000
SIM_LEGACY_CLASSIFY_SECONDS = 120
SIM_LEGACY_PROCESS_SECONDS = 5
SIM_LEGACY_STALL_PROBABILITY = 0.4
SIM_LEGACY_MAX_CONSECUTIVE_STALLS = 3
SIM_RESULT_LOCK_SECONDS = 5.0
SIM_LEGACY_DUAL_ARRAY_UNROLL = 64
SIM_LEGACY_RTL_ARRAY_POINTWISE_CELLS = 1 << 16
SIM_LEGACY_RTL_DEAD_CYCLES = 100
SIM_LEGACY_SOLVER_RETRIES = 5
SIM_LEGACY_CROSSHAIR_PATH_SECONDS = 10.0
SIM_LEGACY_CROSSHAIR_ITERATIONS = 20_000
SIM_LEGACY_CROSSHAIR_UNINTERESTING_ITERATIONS = 500

# Shared isolation lifecycle used by Sim and the other execution clients.
# Reaping/pipe cleanup is distinct from the workload wall deadline. Metadata
# settling controls cache reuse, not task eligibility. ABI widths remain with
# their algorithms rather than becoming configurable resource limits.
ISOLATION_CPU_SECONDS = 20
ISOLATION_RSS_BYTES = 4 << 30
ISOLATION_PROCESSES = 1
ISOLATION_FILE_BYTES = 16 << 20
ISOLATION_OPEN_FILES = 128
ISOLATION_STDOUT_BYTES = 32 << 20  # Generic tool logs; structured Sim roles override this.
ISOLATION_STDERR_BYTES = 8 << 20
ISOLATION_PROTOCOL_BYTES = None
ISOLATION_WALL_SECONDS = 30
ISOLATION_STATUS_BYTES = None  # Optional quota; trusted supervisor diagnostics are complete by default.
# Only the explicitly requested installation diagnostic; real launches rely on
# their own isolation setup handshake and workload startup deadline instead.
ISOLATION_CAPABILITY_PROBE_SECONDS = 10
ISOLATION_COMPLETION_POLL_SECONDS = 0.05
ISOLATION_ATTESTATION_ATTEMPTS = 3
ISOLATION_ATTESTATION_WORKERS = 4
ISOLATION_ATTESTATION_SETTLE_SECONDS = 2.0

# Ordinary scoring; one artifact attempt includes preparation, any requested
# search, and concrete comparison. Search does not add to this outer deadline.
EVAL_MAX_RETRIES = 1
EVAL_SCORE_SECONDS = 1200
# Reserved WITHIN the outer scoring deadline for cleanup and result publication.
# This is not extra search/replay time; very short diagnostic deadlines may leave
# no execution time unless the operator reduces this reservation explicitly.
EVAL_SCORE_FINALIZE_SECONDS = 120

# Agent-written testbenches; compilation and execution have distinct budgets.
TB_MAX_RETRIES = 1
TB_COMPILE_SECONDS = 300
TB_RUN_SECONDS = 300
TB_WORKER_OVERHEAD_SECONDS = 120
TB_SEED = 1

# Optional post-score analysis; these do not extend the scoring deadline.
# Analysis is opt-in and its caller must supply a finite wall-clock budget.
ANALYSIS_WALL_SECONDS = None
ANALYSIS_FEASIBILITY_MS = None
ANALYSIS_QUERY_MS = None
ANALYSIS_BATCH = 32

# Task construction: independent stage budgets, not a shared revision counter.
SYN_SCOPE_REVISIONS = 3
SYN_TASK_REVISIONS = 3
SYN_RTL_REVISIONS = 3
SYN_MAX_REOPENS = 2
SYN_MAX_FETCHES = 8
SYN_EVIDENCE_PAGE_CHARS = 16 << 10
FETCH_SECONDS = 60
FETCH_REDIRECTS = 4
FETCH_FILES = 32
FETCH_FILE_BYTES = 4 << 20
FETCH_TOTAL_BYTES = 16 << 20

# Algorithm-independent training transport and checkpoint I/O.
TRAIN_CLEANUP_SECONDS = None
CHECKPOINT_CHUNK_BYTES = 8 << 20
TRAIN_CPU_THREADS = 1
TRAIN_BUILD_JOBS = 2
TRAIN_PREPARATION_WORKERS = 1
TRAIN_PREPARATION_START_METHOD = "spawn"
TRAIN_MISSING_REWARD_POLICY = "error"

# PPA worker/tool lifetimes. Experiment objectives and workload semantics stay
# in the experiment configuration; these defaults do not select a search target.
PPA_SYNTHESIS_SECONDS = 120
PPA_TIMING_SECONDS = 60
PPA_POWER_SECONDS = 60
PPA_OVERHEAD_SECONDS = 60
PPA_SERVICE_SECONDS = 600
# Current Sim EDA interface requires a positive command-output byte budget.
# Unlike trusted-service log retention, Sim reports exhaustion as unavailable.
EDA_BUILD_JOBS = 8

# Standalone serving lifecycle. GPU topology remains explicit in ServingSpec.
SERVING_TENSOR_PARALLEL = 8
SERVING_CONTEXT_TOKENS = 8192
SERVING_PORT = 31000
SERVING_STARTUP_SECONDS = 900
SERVING_CONTROL_SECONDS = 15
SERVING_LIFETIME_SECONDS = 1500
SERVING_LOG_BYTES = 32 << 20
SERVING_SEED = 17
SERVING_CLEANUP_SECONDS = 60
SERVING_POLL_SECONDS = 0.5
SERVING_JOIN_GRACE_SECONDS = 2
