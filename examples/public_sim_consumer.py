"""Check a passthrough in RTL and Python using the public simulator API."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from behave.sim import api

RTL_TOP = "public_consumer_passthrough"
RTL_SOURCE = r"""
module public_consumer_passthrough(
    input  wire       clk,
    input  wire       rst,
    input  wire       in_valid,
    output wire       in_ready,
    input  wire [7:0] in_a,
    output wire       out_valid,
    input  wire       out_ready,
    output wire [7:0] out_y
);
    logic [7:0] held;
    initial held = 8'd0;
    always_ff @(posedge clk) begin
        if (rst) held <= 8'd0;
        else begin
            case (in_a[0])
                1'b0: held <= held;
                1'b1: held <= held;
            endcase
        end
    end
    assign in_ready = out_ready && !rst;
    assign out_valid = in_valid && !rst;
    assign out_y = in_a ^ held;
endmodule
"""

BEHAVIOR_SOURCE = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": a}
"""

REFERENCE_SOURCE = """\
class Identity:
    def reset(self):
        pass

    def process(self, in_a):
        return {"out_y": in_a}
"""


def example_contract():
    byte = api.FiniteType(8)
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("a", byte, "in_a")],
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION,
            ),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("y", byte, "out_y")],
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION,
                answers_per_transaction=1,
            ),
        ],
        clocks=[api.ClockDomain(
            "clk", "clk", "rst", reset_hold_cycles=3)],
        source="external public-facade consumer",
    )


def _generation():
    return api.GenerationSpec(
        horizon=5,
        queue_bound=1,
        reset_hold_cycles=3,
        drain=1,
    )


def _run_task(task, handle, generation, reference, run_directory, *, run_analysis):
    """Run the one public command shared by Behavior and RTL realizations."""

    providers = handle.generation_providers(
        reference,
        workdir=run_directory.with_name(f"{run_directory.name}-generation"),
    )
    # Optional bounded Analysis needs an explicit whole-run wall budget.
    analysis_options = ({
        "analysis_wall_timeout_seconds": 120,
        "feasibility_timeout_ms": 30_000,
        "query_timeout_ms": 30_000,
    } if run_analysis else {})
    result = api.handle_command(handle.command_request(
        run_directory=run_directory,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=providers,
        probe_budget=1,
        run_analysis=run_analysis,
        reference=reference,
        **analysis_options,
    ))
    return {
        "admission": task.handoff_identity,
        "artifact": handle.artifact_identity,
        "catalogue": handle.frontend_catalogue.digest(),
        "generation": generation.as_dict(),
        "generation_providers": [
            provider.name for provider in providers
        ],
        "analysis_bound": result.command.analysis_bound,
        "analysis_requested": result.command.run_analysis,
        "command_status": result.status.value,
        "evaluation": None if result.evaluation is None else {
            "identity": result.evaluation.fingerprint,
            "passed": result.evaluation.passed,
            "accepted": result.evaluation.accepted,
        },
        "analysis": None if result.analysis is None else {
            "verdict": result.analysis.verdict.value,
            "concrete": result.analysis.concrete,
            "representation": result.analysis.evidence.get(
                "binding", {}).get("representation"),
            "record_audits": [
                row["disposition"]
                for row in result.analysis.evidence.get("record_audits", ())
            ],
        },
        "record_persisted": bool(
            result.record_path and Path(result.record_path).is_file()),
    }


def run_behavior(workdir, *, run_analysis=True):
    """Evaluate one Behavior against another through the common task command."""

    workdir = Path(workdir)
    path = workdir / "candidate_behavior.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BEHAVIOR_SOURCE, encoding="utf-8")
    contract = example_contract()
    generation = _generation()
    reference = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE_SOURCE.encode()},
        config={"class": "Identity"},
        loader="external public-facade consumer",
    )
    realization = api.realization_of_behavior_source(
        BEHAVIOR_SOURCE,
        contract,
        "external public-facade Behavior",
        file="candidate_behavior.py",
        timeout_s=180,
    )
    task = api.admit(contract, realization, reference=reference)
    handle = api.prepare_behavior(
        task,
        api.BehaviorConfig(
            path, file="candidate_behavior.py", timeout_s=180),
        generation,
        bounded_analysis=run_analysis,
    )
    return _run_task(
        task, handle, generation, reference,
        workdir / "behavior-invocation", run_analysis=run_analysis)


def run_rtl(workdir, *, run_analysis=True):
    """Evaluate and analyse arbitrary RTL through public records."""

    workdir = Path(workdir)
    rtl_path = workdir / f"{RTL_TOP}.sv"
    rtl_path.parent.mkdir(parents=True, exist_ok=True)
    rtl_path.write_text(RTL_SOURCE, encoding="utf-8")

    contract = example_contract()
    reference = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE_SOURCE.encode()},
        config={"class": "Identity"},
        loader="external public-facade consumer",
    )
    realization = api.realization_of_rtl_source(
        RTL_SOURCE, "external public-facade RTL", top=RTL_TOP)
    task = api.admit(contract, realization, reference=reference)
    generation = _generation()
    handle = api.prepare_rtl(
        task,
        api.RTLConfig(
            rtl_path,
            RTL_TOP,
            workdir / "rtl-work",
            timeout_s=180,
        ),
        generation,
    )
    return _run_task(
        task, handle, generation, reference, workdir / "rtl-invocation",
        run_analysis=run_analysis)


def run(workdir):
    """Run both supported external-consumer paths."""

    root = Path(workdir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return {
        "status": "ready",
        "behavior": run_behavior(root),
        "rtl": run_rtl(root),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        raise SystemExit("usage: public_sim_consumer.py WORKDIR")
    try:
        result = run(argv[0])
    except api.Refused as exc:
        result = {"status": "refused", "refusal": exc.refusal.as_dict()}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
