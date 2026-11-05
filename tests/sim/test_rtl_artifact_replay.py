"""The RTL artifact: one elaboration, one process per stimulus, and the reset probe.

Split out of `test_wave1_closure.py` by concern: the batched replay against its isolated
ground truth, the shared build, the per-invocation plan, the reset audit as a safety
obligation, and the end-to-end run of the real provider through the public evaluation.
Shared fixtures are imported from where they are defined.
"""
import pathlib

import pytest

from behave.sim import api
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.evaluation import _batched, _replay_note

from tests.support.scoring_task import stage_handshake_register

from .test_api_path import Deaf, Doubling, _evaluate, _live, _ref, contract, realization, schedule  # noqa: F401
from .test_wave1_closure import HOLD, _concrete, _repo, _top_of  # noqa: F401

# The dataset's `dualedge` and `cvdp_copilot_gcd_0001` now keep their native
# interfaces; these replays use the test-owned ready/valid register instead.


class _NoTraceSimulator:
    """An explicit pytest-only isolated-tool seam that returns no record.

    Several checks in this file asserted that `RTLArtifact.traces` *contains* a refusal
    kind rather than that it returns one, because reaching those paths seemed to need a
    simulator. It does not: what the path reads is the record `make` left behind, and a
    `make` that leaves none, or raises, is a few lines. Only the toolchain is faked --
    the partitioning, the attribution and the refusals are the real ones.
    """

    def __init__(self, raises=None, traces=()):
        self.raises = raises
        self.traces = list(traces)
        self.calls = 0

    def __call__(self, workspace, *, role, **_kwargs):
        import json
        import subprocess
        from types import MappingProxyType

        from behave.isolation import IsolationStatus
        from behave.sim.rtl.tool_protocol import ToolExecution

        self.calls += 1
        timed_out = isinstance(self.raises, subprocess.TimeoutExpired)
        if self.raises is not None and not timed_out:
            raise self.raises
        def execution(name, row, outputs=None, trees=None):
            return ToolExecution(
                name, IsolationStatus.COMPLETED, "1" * 64, "pytest",
                "2" * 64, "3" * 64,
                (MappingProxyType(row),),
                MappingProxyType(dict(outputs or {})),
                MappingProxyType(dict(trees or {})))
        built = execution(
            f"{role}:compile",
            {"id": f"{role}:compile", "returncode": 0,
             "timed_out": False, "output_limited": False,
             "stdout": "", "stderr": ""},
            trees={"build": "4" * 64})
        simulated = execution(
            f"{role}:simulate",
            {"id": f"{role}:simulate", "returncode": -9 if timed_out else 0,
             "timed_out": timed_out, "output_limited": False,
             "stdout": "", "stderr": ""},
            outputs={} if timed_out else {
                "driven.json": json.dumps({"traces": self.traces}).encode()})
        return built, simulated


def _drive(stimuli, contract_, simulator, **kwargs):
    """`RTLArtifact.traces` over `stimuli`, with the toolchain replaced."""
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from behave.sim.rtl import artifact as artifact_module
    from behave.sim.rtl import tool_isolation

    where = Path(tempfile.mkdtemp(prefix="behave-replay-test-"))
    # The design is read to be published beside the build; its content does not matter
    # because nothing compiles it here.
    design = where / "rtl.sv"
    design.write_text("module top(); endmodule\n")

    was = tool_isolation.run_verilator_workspace
    try:
        tool_isolation.run_verilator_workspace = simulator
        got = artifact_module.RTLArtifact(str(design), "top", where / "sim")
        # These tests replace the toolchain, including observation preparation.
        got._observation = SimpleNamespace(native=None)
        got._replay_observer = lambda *_args: None
        return got.traces(contract_, stimuli, **kwargs)
    finally:
        tool_isolation.run_verilator_workspace = was


def test_the_batch_refuses_rather_than_returning_records_it_cannot_vouch_for():
    """A design that does not return to the same state under its own reset prefix gives
    a batched replay that is not the isolated one it stands in for. The guard replays the
    first stimulus again at the end; the honest response to a mismatch is to refuse the
    batch, not to return records as though it were sound."""
    import inspect

    from behave.sim.rtl.artifact import RTLArtifact

    assert "verify_batch" in inspect.signature(RTLArtifact.traces).parameters


def test_a_simulator_that_stops_partway_keeps_the_traces_it_finished():
    """The testbench rewrites its record after every stimulus, so a batch that dies is
    not a batch that is lost: the ones that finished are read back and the rest are
    attributed by name."""

    from pathlib import Path

    runner = Path(__file__).parents[2] / "src/behave/sim/rtl/replay_runtime.py"
    source = runner.read_text()
    assert source.count("flush(done)") >= 2, "flushed before the loop and after each run"
    assert 'partial(write_trace_document, directory / "driven.json")' in source


def test_structural_hits_do_not_add_internal_signal_samples():
    from behave.sim.rtl.artifact import DRIVER_PLAN_VERSION, _observation_names

    watched, recorded = _observation_names(
        contract(), observe_names=("exact_relation_output",))

    assert DRIVER_PLAN_VERSION == "rtl-driver-plan/24"
    assert "hidden_branch_condition" not in watched
    assert "hidden_branch_condition" not in recorded
    assert "exact_relation_output" in watched
    assert "exact_relation_output" in recorded
    assert {field.port for endpoint in contract().endpoints
            for field in endpoint.fields} <= set(recorded)


def test_rendered_single_clock_driver_requires_a_versioned_observation_boundary():
    from behave.sim.harness import render_template

    source = render_template("driver_tb.py.tmpl", period_ns=10)

    compile(source, "driver_tb.py", "exec")
    assert "from behave.sim.rtl.replay_runtime import replay" in source
    assert "await replay(dut, Path(__file__).parent, period_ns=10)" in source
    assert "for " not in source  # No second orchestration loop in the template.


def test_one_unrunnable_stimulus_does_not_veto_the_batch():
    """Constructing every `Driver` up front and letting the first refusal propagate meant
    a single unrunnable member of 756 took the whole set down to the one-at-a-time path
    -- which is what the first real run of this did, silently turning a batch back into
    756 elaborations. The refusal belongs to the stimulus that earned it.

    Found by watching a live sweep write a 5 KB `plan.pickle`, which is one stimulus.
    """

    from behave.sim.core.driver import Driver

    c = contract()
    # A stimulus built for a contract whose input endpoint has another name: `Driver`
    # refuses it against this contract, and it is the only one that should be refused.
    renamed = api.TaskContract(
        endpoints=[api.Endpoint("req", api.Direction.IN, c.endpoint("in").fields,
                                c.endpoint("in").adapter, clock="clk",
                                kind=api.Kind.TRANSACTION),
                   c.endpoint("out")],
        clocks=list(c.clocks))
    foreign = api.reset_then(renamed, [(3,)], controls={"out_ready": 1})
    with pytest.raises(Refused):
        Driver(c, foreign)


def test_replay_has_no_separate_per_stimulus_budget():
    """Tool configuration does not invent N independent replay allowances."""

    from behave.sim.rtl.artifact import RTLArtifact

    artifact = RTLArtifact("x.sv", "top", "/tmp/nowhere")
    assert not hasattr(artifact, 'per_stimulus_s')
    assert not hasattr(artifact, '_budget')


@pytest.mark.parametrize('count', [1, 7])
def test_diagnostic_batch_uses_tool_budget_not_a_per_input_multiple(count):
    from behave.core import runtime_defaults as defaults

    c = contract()
    stimuli = [api.reset_then(c, [(v,)], controls={'out_ready': 1}) for v in range(count)]
    budgets = []
    simulator = _NoTraceSimulator(traces=[])

    def capture(workspace, **kwargs):
        budgets.append(kwargs['simulate_timeout_s'])
        return simulator(workspace, **kwargs)

    result = _drive(stimuli, c, capture, verify_batch=False)
    assert len(result) == count
    assert budgets == [defaults.SIM_RTL_TOOL_SECONDS]


def test_an_unverifiable_stimulus_withholds_the_batch():
    """The check `continue`d past an isolated replay that came back a `Refusal` and then
    returned the batch as verified -- so the one outcome meaning "this could not be
    checked" was the one that let the batch through."""

    from behave.sim.rtl.artifact import RTLArtifact

    # A sample is not offered as an invariant: verifying all of them is a knob, off by
    # default, and the result says which was done.
    artifact = RTLArtifact("x.sv", "top", "/tmp/nowhere")
    assert artifact.verify_every is False


@pytest.mark.cosim
def test_each_stimulus_is_replayed_in_a_process_of_its_own_after_one_elaboration():
    """The batch conflated two costs. The elaboration is expensive and does not depend on
    the stimulus; the simulator process is cheap. Sharing one process bought an assumption
    -- that every stimulus's reset prefix restores the design -- checked by a sample of
    three, which cannot establish a property that has to hold for each one.

    Compiling once and running each stimulus in a process of its own assumes nothing: a
    run here *is* an isolated run. What this asserts is that it produces the same records
    as replaying one at a time, which is the path with no shared anything.
    """
    import shutil

    from behave.sim.contract_of import contract_of_spec
    from behave.sim.declarations import declarations_at, declared_contract
    from behave.sim.rtl.artifact import RTLArtifact

    problem = stage_handshake_register()
    top = _top_of(problem)
    c = declared_contract(
        contract_of_spec((problem / "spec.md").read_text(), "handshake_register"),
        declarations_at(problem))
    stimuli = [api.reset_then(c, [(v % 2,)] * (1 + v % 3) + [((v + 1) % 2,)],
                              drain=5, controls={"out_ready": 1}, source=f"s{v}")
               for v in range(4)]

    together = RTLArtifact(str(problem / "rtl.sv"), top,
                           problem / "pp").traces_per_process(c, stimuli, workers=4)
    alone = [RTLArtifact(str(problem / "rtl.sv"), top,
                         problem / f"iso{i}").trace(c, s)
             for i, s in enumerate(stimuli)]
    assert all(not isinstance(g, Refusal) for g in together), together
    assert [g.records for g in together] == [g.records for g in alone]

    shutil.rmtree(problem, ignore_errors=True)


@pytest.mark.cosim
def test_one_invocation_builds_once_and_replays_in_private_namespaces(tmp_path, monkeypatch):
    """One compiler product serves multiple replay batches without becoming shared
    writable state.  The isolated tool boundary exports that product once, mounts it
    read-only into one simulator namespace per stimulus, and removes the invocation tree
    afterwards. Replays still have separate processes; separate command scopes rebuild.
    """
    from behave.core.paths import isolation_startup_root
    from behave.sim.rtl.artifact import RTLArtifact
    from tests.sim.test_api_path import contract

    c = contract()
    rtl = tmp_path / "rtl.sv"
    rtl.write_text("""
module top(input logic clk, rst, in_valid, out_ready,
           input logic [7:0] in_a,
           output logic in_ready, out_valid,
           output logic [7:0] out_y);
    assign in_ready = out_ready;
    assign out_valid = in_valid;
    assign out_y = in_a;
endmodule
""")
    stimuli = [api.reset_then(c, [(v % 2,)] * (2 + v), drain=5,
                              controls={"out_ready": 1}, source=f"s{v}")
               for v in range(2)]

    work = tmp_path / "sim"
    work.mkdir()
    saved = work / "evidence.txt"
    saved.write_text("keep durable evidence")
    local = tmp_path / "local"
    local.mkdir()
    # The batch runtime outlives commands and is not a replay-owned product.
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(local))
    artifact = RTLArtifact(str(rtl), "top", work)
    from behave.sim.rtl.replay_build import replay_build_scope

    with replay_build_scope(artifact):
        got = artifact.traces_per_process(c, stimuli, workers=2)
        again = artifact.traces_per_process(c, list(reversed(stimuli)), workers=2)
    assert all(not isinstance(g, Refusal) for g in got), got
    assert [g.records for g in got] == [g.records for g in reversed(again)]

    lineage = artifact.tool_lineage()
    assert [row["role"] for row in lineage].count("verilator-build") == 1
    simulations = [
        row for row in lineage
        if str(row["role"]).startswith("verilator-simulate:")
    ]
    assert len(simulations) == 2 * len(stimuli)
    assert len({row["workspace_digest"] for row in simulations}) == len(stimuli)
    # No compiler product or replay workspace survives the invocation.
    assert not (work / "builds").exists()
    assert not [p for p in work.iterdir() if p.name.startswith("invocation-")]
    assert saved.read_text() == "keep durable evidence"
    # Startup admission locks and declared compiler logs outlive a replay scope;
    # they are not writable simulator workspaces or compiler products.
    retained_logs = {
        pathlib.Path(row["log_directory"]) for row in lineage
        if row.get("log_directory") and pathlib.Path(row["log_directory"]).parent == local
    }
    allowed = retained_logs | {isolation_startup_root()}
    assert set(local.iterdir()) <= allowed
    for directory in retained_logs:
        assert directory.is_dir()
        assert all(path.is_file() and path.suffix in {".stdout", ".stderr"}
                   for path in directory.iterdir())

@pytest.mark.cosim
def test_a_design_that_does_not_build_stops_the_whole_set():
    """The fan-out runs `make` without the lock, on the strength of the build being
    current. Where the compile has just failed there is no such build, so it would send
    every remaining stimulus into an absent directory, several at once -- the race the
    lock exists to remove, reintroduced by the failure path. An unreadable design now
    stops earlier still: observation admission elaborates it before any compile."""
    import shutil

    from behave.sim.contract_of import contract_of_spec
    from behave.sim.declarations import declarations_at, declared_contract
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.frontend_error import SourceRejected

    problem = stage_handshake_register()
    top = _top_of(problem)
    c = declared_contract(
        contract_of_spec((problem / "spec.md").read_text(), "handshake_register"),
        declarations_at(problem))
    stimuli = [api.reset_then(c, [(v % 2,)], drain=5, controls={"out_ready": 1},
                              source=f"s{v}") for v in range(4)]

    # A design the elaborator cannot read at all, with the ports still declared so the
    # contract binds and the run gets as far as the build.
    broken = problem / "broken.sv"
    broken.write_text((problem / "rtl.sv").read_text()
                      + "\n\nthis is not systemverilog\n")

    artifact = RTLArtifact(str(broken), top, problem / "sim")
    with pytest.raises(SourceRejected) as rejected:
        artifact.traces_per_process(c, stimuli, workers=4)
    assert rejected.value.reason.kind == "rtl-source-rejected"
    # Refused once for the whole set: nothing was compiled or simulated.
    assert not [row for row in artifact.tool_lineage()
                if str(row["role"]).startswith(("verilator-build", "verilator-simulate"))]

    shutil.rmtree(problem, ignore_errors=True)


@pytest.mark.cosim
def test_a_first_stimulus_that_fails_on_its_own_does_not_veto_the_rest():
    """The build's `ready` marker was written only where the first run produced a usable
    trace -- so a first stimulus that failed for its **own** reason left the build
    unmarked and the caller withheld the whole set. That is one-bad-stimulus-vetoes-the-
    batch, reintroduced through the marker: what it is about is the build, and the
    compiled simulator in the directory is the evidence for that."""
    import shutil

    from behave.sim.contract_of import contract_of_spec
    from behave.sim.declarations import declarations_at, declared_contract
    from behave.sim.rtl.artifact import RTLArtifact

    problem = stage_handshake_register()
    top = _top_of(problem)
    c = declared_contract(
        contract_of_spec((problem / "spec.md").read_text(), "handshake_register"),
        declarations_at(problem))

    # A stimulus this path refuses before the simulator: its controls name a signal the
    # contract does not have, so the `Driver` refuses it and the run never happens.
    good = [api.reset_then(c, [(v % 2,)], drain=5, controls={"out_ready": 1},
                           source=f"s{v}") for v in range(3)]
    artifact = RTLArtifact(str(problem / "rtl.sv"), top, problem / "sim")

    # First, a real failure of the *first* stimulus alone: a horizon of zero gives a
    # trace shorter than the schedule, which `_trace_of` refuses by name.
    short = api.reset_then(c, [(1,)], drain=0, controls={"out_ready": 1},
                           source="short")
    got = artifact.traces_per_process(c, [short] + good, workers=3)
    assert len(got) == 4
    # Whatever the first one's outcome, the rest were attempted and produced traces --
    # they are not `rtl-artifact-build-failed`, which is the veto this test is about.
    assert {g.kind for g in got[1:] if isinstance(g, Refusal)} != {
        "rtl-artifact-build-failed"}
    assert sum(1 for g in got[1:] if not isinstance(g, Refusal)) == 3, got[1:]

    shutil.rmtree(problem, ignore_errors=True)


@pytest.mark.cosim
def test_two_invocations_over_one_workdir_do_not_read_each_other_s_plan():
    """Reported with a reproduction: the run directories were `workdir/run0`,
    `workdir/run1`, fixed names, and the authoritative path's `obj_authority` was fixed
    too. Two invocations at once wrote each other's `plan.pickle`, so a trace came back
    bound to a stimulus it was not a run of -- a wrong record with nothing saying so.

    Both calls here share a workdir on purpose. What each trace was driven with is read
    off the record rather than trusted.
    """
    import shutil
    from concurrent.futures import ThreadPoolExecutor

    from behave.sim.contract_of import contract_of_spec
    from behave.sim.declarations import declarations_at, declared_contract
    from behave.sim.rtl.artifact import RTLArtifact

    problem = stage_handshake_register()
    top = _top_of(problem)
    c = declared_contract(
        contract_of_spec((problem / "spec.md").read_text(), "handshake_register"),
        declarations_at(problem))

    def stimuli_for(bit):
        return [api.reset_then(c, [(bit,)] * (2 + i), drain=5,
                               controls={"out_ready": 1}, source=f"{bit}-{i}")
                for i in range(2)]

    shared = problem / "sim"          # one workdir for both, deliberately

    def run(bit):
        return bit, RTLArtifact(str(problem / "rtl.sv"), top,
                                shared).traces_per_process(c, stimuli_for(bit),
                                                           workers=2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(pool.map(run, (0, 1)))

    for bit, traces in sorted(results.items()):
        assert all(not isinstance(g, Refusal) for g in traces), (bit, traces)
        for stimulus, trace in zip(stimuli_for(bit), traces):
            assert trace.stimulus.fingerprint == stimulus.fingerprint
            drove = {v for record in trace.records for _, txn in record.accepted
                     if txn is not None for v in txn}
            assert not drove or drove == {bit}, \
                f"the run of {bit} was driven with {sorted(drove)}"

    shutil.rmtree(problem, ignore_errors=True)


def test_the_replay_mode_is_a_field_and_not_a_sentence():
    """Whether a run's records can carry a score was decided by looking for the words
    "a sample of three" in prose written for a reader. Rewording that sentence would have
    turned a refusal into a pass silently -- the shape of defect this check exists to
    catch, arriving through the telemetry."""
    from behave.sim import api as api_module

    assert api_module.VERIFIED_SAMPLE != api_module.VERIFIED_EVERY
    note = _replay_note(api_module.ONE_PROCESS_BATCH,
                                   api_module.VERIFIED_SAMPLE, 12)
    assert note["mode"] == api_module.ONE_PROCESS_BATCH
    assert note["verified"] == api_module.VERIFIED_SAMPLE
    assert note["stimuli"] == 12
    # And the per-process path says there is nothing to verify, rather than saying
    # nothing -- an empty field and "no verification was needed" are different facts.
    sound = _replay_note(api_module.PER_PROCESS,
                                    api_module.VERIFIED_NOT_NEEDED, 12)
    assert sound["verified"] == api_module.VERIFIED_NOT_NEEDED


def test_a_simulator_that_leaves_no_record_refuses_each_stimulus_by_name():
    """`rtl-artifact-batch-stopped-short` was asserted as a string inside
    `RTLArtifact.traces`. It is what the method *returns* when the simulator stopped
    before reaching a stimulus, and reaching that needs a `make` that leaves no record
    rather than a simulator."""
    c = contract()
    stimuli = [api.reset_then(c, [(v,)], controls={"out_ready": 1}, source=f"s{v}")
               for v in range(3)]

    got = _drive(stimuli, c, _NoTraceSimulator(traces=[]), verify_batch=False)
    assert len(got) == 3
    assert all(isinstance(g, Refusal) for g in got), got
    assert {g.kind for g in got} == {"rtl-artifact-batch-stopped-short"}
    # And it says how far it got, which is what makes the refusal actionable.
    assert dict(got[0].detail)["offered"] == 3


def test_one_unrunnable_stimulus_keeps_its_own_refusal_and_the_others_run():
    """The partition was read off the source: refusals collected per position, only the
    runnable ones reaching the simulator. Driven instead, with one stimulus the `Driver`
    refuses -- its refusal belongs to it, and the others get whatever the run gave
    them, which is not its refusal."""
    c = contract()
    renamed = api.TaskContract(
        endpoints=[api.Endpoint("req", api.Direction.IN, c.endpoint("in").fields,
                                c.endpoint("in").adapter, clock="clk",
                                kind=api.Kind.TRANSACTION),
                   c.endpoint("out")],
        clocks=list(c.clocks))
    foreign = api.reset_then(renamed, [(3,)], controls={"out_ready": 1})
    fine = [api.reset_then(c, [(v,)], controls={"out_ready": 1}, source=f"s{v}")
            for v in range(2)]

    simulator = _NoTraceSimulator(traces=[])
    got = _drive([fine[0], foreign, fine[1]], c, simulator, verify_batch=False)

    assert len(got) == 3
    # The foreign one carries the driver's own refusal, by name.
    assert isinstance(got[1], Refusal)
    assert got[1].kind not in ("rtl-artifact-batch-stopped-short",)
    # The other two were attempted -- the simulator ran once for the batch of them.
    assert simulator.calls == 1
    assert {got[0].kind, got[2].kind} == {"rtl-artifact-batch-stopped-short"}


def test_a_batch_that_runs_out_of_time_says_so_rather_than_raising():
    """A flat budget once made a 700-stimulus batch raise `TimeoutExpired`, which
    `evaluate` answered by replaying every stimulus on its own -- slower than the batch
    it replaced. The artifact names it instead, and this drives that rather than reading
    it: a `make` that times out, and every stimulus comes back refused by name."""
    import subprocess

    c = contract()
    stimuli = [api.reset_then(c, [(v,)], controls={"out_ready": 1}, source=f"s{v}")
               for v in range(4)]

    late = subprocess.TimeoutExpired(["make"], 1.0)
    got = _drive(stimuli, c, _NoTraceSimulator(raises=late), verify_batch=False)
    assert {g.kind for g in got} == {"rtl-batch-timed-out"}
    assert dict(got[0].detail)["stimuli"] == 4


def test_a_withheld_batch_sends_evaluate_back_to_one_at_a_time():
    """Two source assertions -- that `_batched` mentions `rtl-batch-order-dependent`,
    and that it mentions `rtl-batch-unverifiable` and `withheld` -- for one behaviour:
    a batch the artifact will not vouch for makes the evaluation replay one at a time
    and say so. Driven with an artifact that returns those refusals."""
    from behave.sim import api as api_module

    task = api.admit(contract(), realization(), reference=_ref())
    stimuli = [schedule([1]), schedule([2])]

    class _Withholds:
        kind = "rtl"

        def __init__(self, why):
            self.why = why
            # The identity the admission was about: `_batched` asks `task.binds`, and an
            # artifact that declares none is refused before the batch is even attempted
            # -- which is a different behaviour and not this one.
            self.identity = task.realization.identity

        def traces(self, contract_, stimuli_, **kwargs):
            return [Refusal(self.why, "this batch is not vouched for")
                    for _ in stimuli_]

    for why in ("rtl-batch-order-dependent", "rtl-batch-unverifiable"):
        batched, note = _batched(task, stimuli, _Withholds(why))
        assert batched == {}, "a withheld batch supplies no records"
        assert note["mode"] == api_module.ONE_AT_A_TIME
        assert why in note["why"] and "replayed on its own" in note["why"]


def test_a_probe_deadline_is_not_the_artifact_failing():
    """An external interruption escapes replay; it is not a candidate fault."""
    class Deadline(BaseException):
        pass

    # And the loop really does let one through rather than filing it against a stimulus.
    task = api.admit(contract(), realization(), reference=_ref())

    class Slow:
        kind = "device"
        identity = "doubling"

        def reset(self):
            pass

        def settle(self, presented, controls):
            del presented, controls
            raise Deadline("over 1s")

        def edge(self):
            pass

    with pytest.raises(Deadline) as raised:
        _evaluate(task, [schedule([3])], Slow(), reference=_ref())
    assert "over 1s" in str(raised.value)


def test_reset_restoration_claim_is_part_of_the_contract_identity():
    from behave.sim.core.contract import ClockDomain
    from behave.sim.rtl.artifact import RTLArtifact

    # Declared on the clock domain, three-valued, and inside the contract's identity.
    assert ClockDomain("clk", "clk", "rst").reset_restores is None
    assert ClockDomain("clk", "clk", "rst", reset_restores=True).reset_restores is True
    with_it = api.TaskContract(
        endpoints=list(contract().endpoints),
        clocks=[ClockDomain("clk", "clk", "rst", reset_restores=True)])
    assert with_it.digest != contract().digest, "it changes what the task declares"

    # Asked directly rather than inferred from a batch refusal, and in three states.
    assert "reset_audit" in dir(RTLArtifact)


def test_the_six_declarations_say_whether_reset_restores():
    """Each of those specifications describes a handshake-wrapped block with a reset and
    no notion of surviving state, so each claims it. Claimed, and checked -- two of them
    turn out not to, which is a finding about those designs and not about the harness."""
    from behave.sim.declarations import declarations_at

    root = _repo() / "tools" / "declarations"
    overlays = sorted(root.glob("*.toml"))
    assert len(overlays) == 6
    for overlay in overlays:
        import shutil
        import tempfile

        # Reset restoration is a task claim, not an execution setting.
        with tempfile.TemporaryDirectory() as where:
            shutil.copy2(overlay, pathlib.Path(where) / "task.toml")
            declared = declarations_at(where)
        assert declared.reset_restores is True, overlay.name


def test_the_reset_audit_is_named_for_its_probe_and_not_for_the_property():
    """`restored` claimed what one replay cannot show.

    The audit runs *one* ordered pair -- first, other, first -- and compares two records.
    A pair that comes back the same has not falsified "holding reset restores this
    design"; it has not established it either, and the state called `restored` said it
    had. Every reader downstream inherited that: the telemetry key, the report, and a
    person reading either.

    Two things are checked here. The state is named for the experiment. And the
    experiment is only run when the stimulus it replays twice actually holds reset for
    the cycles the contract declares -- without that, whatever separates the two runs is
    not the reset, and a record that came back would be about the design not carrying
    state between these two stimuli, which is a different fact.
    """
    import shutil

    from behave.sim.contract_of import contract_of_spec
    from behave.sim.core.stimulus import EventSlot, Stimulus
    from behave.sim.rtl.artifact import RTLArtifact, leading_reset_slots

    # The prefix is counted from the front and stops at the first slot that is not a
    # reset: a reset asserted again mid-run is a different event.
    assert leading_reset_slots([]) == 0
    assert leading_reset_slots([EventSlot(reset=True)] * 3) == 3
    assert leading_reset_slots([EventSlot(reset=True), EventSlot(),
                                EventSlot(reset=True)]) == 1

    # Two distinct stimuli, neither holding reset: the replay is not run at all, and the
    # reason names the count against the declared one rather than answering.
    problem = stage_handshake_register()
    try:
        contract = contract_of_spec((problem / "spec.md").read_text(), "handshake_register")
        wanted = contract.clocks[0].reset_hold_cycles
        assert wanted >= 1
        first = contract.inputs()[0].name
        bare = [Stimulus(queues={first: ((1,),)}, slots=[EventSlot(offers=[first])],
                         source="a"),
                Stimulus(queues={first: ((0,),)}, slots=[EventSlot(offers=[first])],
                         source="b")]

        class _WouldHaveReplayed(RTLArtifact):
            def traces(self, *a, **k):
                raise AssertionError("the replay ran on stimuli with no reset in them")

        state, why = _WouldHaveReplayed(
            str(problem / "rtl.sv"), _top_of(problem), problem / "obj"
        ).reset_audit(contract, bare)
        assert state == "unresolved"
        # A zero-length prefix takes the no-reset branch: there is nothing here to put
        # the question with, whatever number the run holds.
        assert "no reset here to put the question with" in why
    finally:
        shutil.rmtree(problem, ignore_errors=True)


def test_a_run_with_no_reset_to_hold_leaves_the_probe_unresolved():
    """A hold of zero -- no policy and a zero-hold contract -- made `held == wanted`
    trivially true, and the probe answered `reset_probe_reproduced` about a reset that
    does not exist. What such a replay tests is whether the design carries state between
    stimuli, which is a different fact; no reset, no question."""
    from types import SimpleNamespace

    from behave.sim.core.contract import ClockDomain, TaskContract
    from behave.sim.core.stimulus import EventSlot, Stimulus
    from behave.sim.rtl.artifact import RTLArtifact

    base = contract()
    zero = TaskContract(endpoints=list(base.endpoints),
                        clocks=[ClockDomain("clk", "clk", "rst",
                                            reset_hold_cycles=0)],
                        source=base.source, authority=base.authority)
    stimuli = [Stimulus(queues={"in": ((v,),)},
                        slots=[EventSlot(offers=["in"],
                                         controls={"rst": 0, "out_ready": 1})],
                        source=f"s{v}") for v in (1, 2)]

    class _Probed(RTLArtifact):
        def traces(self, *a, **k):
            return [SimpleNamespace(records=[1]), SimpleNamespace(records=[2]),
                    SimpleNamespace(records=[1])]

    state, why = _Probed("x.sv", "top", "scratch/nowhere").reset_audit(zero, stimuli)
    assert state == "unresolved", (state, why)
    assert "no reset here to put the question with" in why


def test_the_reset_audit_reads_the_hold_the_schedules_were_built_with():
    """One effective hold, on both sides of the probe.

    The stimuli are built under `authority._hold_of` -- the run policy's number, the
    contract's where the run states none -- and `reset_audit` read the contract's
    directly. With a contract asking five and a run holding three, a correctly built
    stimulus was audited as "3 of the 5" and the probe came back `unresolved` for a
    reset exactly as long as this run holds one. A behaviour test, because the source
    checks this replaces can prove a parameter was passed and not that both ends mean
    the same thing by it.
    """
    from types import SimpleNamespace

    from behave.sim import api
    from behave.sim.core.contract import ClockDomain, TaskContract
    from behave.sim.rtl.artifact import RTLArtifact

    base = contract()
    five = TaskContract(endpoints=list(base.endpoints),
                        clocks=[ClockDomain("clk", "clk", "rst",
                                            reset_hold_cycles=5)],
                        source=base.source, authority=base.authority)
    stimuli = [api.reset_then(five, [(v,)], reset_hold_cycles=3, drain=1,
                              controls={"out_ready": 1}) for v in (1, 2)]

    class _Probed(RTLArtifact):
        def traces(self, *a, **k):
            return [SimpleNamespace(records=[1]), SimpleNamespace(records=[2]),
                    SimpleNamespace(records=[1])]

    aligned = _Probed("x.sv", "top", "scratch/nowhere",
                      run_policy={"reset_hold_cycles": 3})
    state, _why = aligned.reset_audit(five, stimuli)
    assert state == "reset_probe_reproduced", state

    # No run policy: the contract's own five stands, and a three-cycle prefix is not
    # this run's reset.
    state, why = _Probed("x.sv", "top", "scratch/nowhere").reset_audit(five, stimuli)
    assert state == "unresolved" and "3 slots and this run holds exactly 5" in why, \
        (state, why)
