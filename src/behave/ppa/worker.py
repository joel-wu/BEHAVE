"""Private PPA service. Reuse Sim authority, EDA isolation and worker lifetime.

The returned record is private. Only workflow.py's explicit projection may go
to a model. Files and reference evidence are never installed in its workspace.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.paths import eda_root
from behave.core.records import canonical_json, fields, parse_json
from behave.core.storage import DirectoryStore, install_exclusive
from behave.isolation.scratch import ScratchDirectory, retention_record
from behave.task_execution._worker_transport import exchange
from behave.task_execution.comparison import ComparisonInputs

from .config import Experiment
from .records import pack_result, read_document, union_pool, unpack_result, write_document

WORKER_VERSION = "behave.ppa-worker/12"


class NativePPA:
    def __init__(self, *, liberty: Path, sta: Path, cpu_resources=None, technology_name='sky130hd'):
        from .eda import library_description
        from .technology import technology

        self.liberty, self.sta = liberty.resolve(), sta.resolve()
        self.cpu_resources = cpu_resources
        self.cancelled = lambda: False
        library = self.liberty.read_bytes()
        self.technology = library_description(library)
        self.technology_name = technology_name
        profile = technology(technology_name)
        profile.validate_library(self.technology)
        self.technology['physical_profile'] = profile.profile
        self.library_ref = BlobRef.of(library)
        self.sta_ref = BlobRef.of(self.sta.read_bytes())
        self.abc_ref = BlobRef.of((eda_root() / "libexec/yosys-abc").read_bytes())
        from behave.sim.api import prepare_tool_environment

        self.runtime_identity = prepare_tool_environment("eda").identity
        from .mapping_repair import runtime
        repair = runtime(required=False, technology_name=technology_name)
        self.repair_identity = None if repair is None else repair.identity

    @property
    def identity(self):
        from .eda import FLOW_VERSION

        return BlobRef.of(canonical_json({"worker": WORKER_VERSION,
            "flow": FLOW_VERSION,
            "liberty": self.library_ref.as_dict(), "sta": self.sta_ref.as_dict(),
            "abc": self.abc_ref.as_dict(),
            "technology": self.technology_name,
            "mapping_repair": getattr(self, "repair_identity", None),
            "eda_runtime": self.runtime_identity})).sha256

    def run(self, inputs: ComparisonInputs, experiment: Experiment, mode: str, pool: tuple,
            objects: DirectoryStore):
        from contextlib import ExitStack

        from behave.agent.cpu_resources import cpu_job
        from behave.sim import api

        with cpu_job(self.cpu_resources, cancelled=getattr(self, "cancelled", lambda: False)) as lease, ExitStack() as resources:
            endpoint = (None if lease is None else
                        resources.enter_context(api.CpuPool(capacity=len(lease.cpus))).endpoint.as_dict())
            return self._run(inputs, experiment, mode, pool, objects, lease=lease, cpu_pool=endpoint)

    def run_many(self, inputs, experiment, modes, pool, objects):
        """Independent EDA/RTL jobs partition one shared scheduler reservation."""
        from concurrent.futures import ThreadPoolExecutor
        from copy import copy

        from behave.agent.cpu_resources import CpuResources, cpu_job

        with cpu_job(self.cpu_resources, cancelled=getattr(self, "cancelled", lambda: False)) as lease:
            resources = CpuResources(len(modes), cpus=None if lease is None else len(lease.cpus))
            worker = copy(self)
            worker.cpu_resources = resources

            def run(mode):
                began = time.monotonic()
                result = worker.run(inputs, experiment, mode, pool, objects)
                return result, time.monotonic() - began

            try:
                with ThreadPoolExecutor(max_workers=len(modes)) as executor:
                    futures = {mode: executor.submit(run, mode) for mode in modes}
                    return {mode: future.result() for mode, future in futures.items()}
            finally:
                # Executor exit joins both branches before releasing their parent
                # reservation. Unconfirmed descendant cleanup keeps it retired.
                if lease is not None:
                    lease.cleanup = (CleanupState.UNKNOWN if resources.snapshot()["retired"]
                                     else CleanupState.COMPLETE)

    def _run(self, inputs, experiment, mode, pool, objects, *, lease=None, cpu_pool=None):
        from .eda import FLOW_VERSION

        if experiment.mapping.technology != self.technology_name:
            raise ValueError('experiment and PPA runtime technologies differ')

        cleanup = CleanupState.COMPLETE
        _, input_refs = inputs.stage(objects)
        pool_ref, pool_closure = write_document(objects, {"stimuli": list(pool)})
        input_refs = (*input_refs, *pool_closure)
        with ScratchDirectory(prefix="behave-ppa-", cleanup=lambda: cleanup) as scratch:
            if mode != "eda":
                from behave.agent.tools import ToolUnavailable
                from behave.task_execution.judgment import ComponentJudgment

                cleanup = CleanupState.UNKNOWN
                result = execute(inputs, experiment, mode, pool, scratch, objects,
                                 liberty=self.liberty, sta=self.sta, cpu_pool=cpu_pool)
                cleanup = ComponentJudgment.from_dict(result["judgment"]).cleanup
                if lease is not None:
                    lease.cleanup = cleanup
                if not cleanup.quiescent:
                    retained = objects.put(retention_record(scratch, cleanup))
                    raise ToolUnavailable("ppa-cleanup-unconfirmed", artifacts=(
                        *input_refs, *(BlobRef.from_dict(ref) for ref in result["evidence"]), retained))
                return result
            local = DirectoryStore(scratch / "objects")
            identity, _ = inputs.stage(local)
            local.publish(objects, pool_closure)
            packet = canonical_json({"inputs": identity.as_dict(), "experiment": experiment.as_dict(),
                "mode": mode, "pool": pool_ref.as_dict(), "liberty": str(self.liberty), "sta": str(self.sta),
                "library_ref": self.library_ref.as_dict(), "sta_ref": self.sta_ref.as_dict(),
                "abc_ref": self.abc_ref.as_dict(),
                "repair_identity": getattr(self, "repair_identity", None),
                "worker": WORKER_VERSION,
                "flow": FLOW_VERSION,
                "eda_runtime": self.runtime_identity})
            local.put(packet)
            cleanup = CleanupState.UNKNOWN
            seconds = experiment.eda_budget.service_seconds if mode == "eda" else experiment.service_seconds
            result = exchange("ppa", packet, workdir=scratch, objects=objects,
                deadline=time.monotonic() + seconds,
                timeout_seconds=seconds, log_bytes=defaults.LOG_RETAIN_BYTES, cpu_pool=cpu_pool)
            cleanup = result.cleanup
            if lease is not None:
                lease.cleanup = cleanup
            if not cleanup.quiescent:
                from behave.agent.tools import ToolUnavailable

                retained = objects.put(retention_record(scratch, cleanup))
                raise ToolUnavailable("ppa-cleanup-unconfirmed",
                                      artifacts=(*input_refs, *result.evidence, retained))
            if result.code is not None:
                return {"score": None, "code": result.code, "pool": list(pool),
                        "evidence": [r.as_dict() for r in (*input_refs, *result.evidence)]}
            row = fields(result.result, {"record", "closure"})
            closure = tuple(BlobRef.from_dict(ref) for ref in row["closure"])
            objects.publish(local, closure)
            payload = unpack_result(objects, read_document(objects, BlobRef.from_dict(row["record"])))
            # Retain the service's entire declared closure, including the EDA
            # command record, not a second hand-selected subset of its fields.
            payload["evidence"] = [r.as_dict() for r in dict.fromkeys((*closure, *input_refs, *result.evidence))]
            return payload


def execute(inputs, experiment, mode, pool, directory, objects, *, liberty, sta, cpu_pool=None):
    from dataclasses import replace

    from behave.sim import api
    from behave.task_execution.comparison import compare_sources

    from . import eda, performance

    if mode == "eda":
        contract = api.TaskContract.from_dict(parse_json(inputs.contract.data))
        if len(contract.clocks) != 1 or contract.clocks[0].clock != experiment.timing.clock:
            raise ValueError("power and timing require the declared single clock")
        clock = contract.clocks[0]
        return {"eda": eda.measure(inputs.source, experiment.timing, liberty=liberty, sta=sta,
            directory=directory / "eda", objects=objects, power_policy=experiment.power,
            budget=experiment.eda_budget, sat_resource_sharing=experiment.sat_resource_sharing,
            mapping=experiment.mapping,
            constant_inputs={clock.reset: int(clock.reset_active_low)})}
    if mode not in {"check", "final", "performance", "mapped"}:
        raise ValueError("unknown PPA service action")
    workloads = ()
    if mode in {"mapped", "performance", "final"}:
        contract = api.TaskContract.from_dict(parse_json(inputs.contract.data))
        workloads = tuple(performance.stimulus(contract, work) for work in experiment.workloads)
    if mode != "check":
        requested = union_pool(() if mode == "performance" else pool,
                               (stimulus.as_dict() for stimulus in workloads))
        if requested:
            inputs = replace(inputs, replay_stimuli=canonical_json({"stimuli": list(requested)}))
    # The same supervised source comparator used by Eval and its development tool.
    result = compare_sources(inputs, objects=objects, workdir=directory, cpu_pool=cpu_pool)
    replay = result.detail.get("replay_pool")
    generated = (() if replay is None else
                 parse_json(read_verified(objects, BlobRef.from_dict(dict(replay))))["stimuli"])
    row = {"score": result.score, "code": result.code, "judgment": result.as_dict(),
           "pool": list(union_pool(pool, generated)) if mode == "check" else list(pool),
           "evidence": [ref.as_dict() for ref in result.closure]}
    if workloads:
        windows = result.detail.get("workload_windows", {})
        metrics = ([] if result.score != 1 else [
            performance.completion(windows[stimulus.fingerprint], work, experiment.timing.period_ns)
            for stimulus, work in zip(workloads, experiment.workloads, strict=True)
            if stimulus.fingerprint in windows])
        complete = len(metrics) == len(workloads) and all(m["time_ns"] is not None for m in metrics)
        row["performance"] = {"score": result.score, "source": "mapped" if mode == "mapped" else "rtl",
                              "workloads": metrics,
                              "time_ns": max(m["time_ns"] for m in metrics) if complete else None}
    return row


def main():
    from behave.sim import api

    from .eda import FLOW_VERSION

    root = Path(sys.argv[1])
    request = BlobRef(sys.argv[2], int(sys.argv[3]))
    objects = DirectoryStore(root / "objects")
    packet = parse_json(read_verified(objects, request))
    if packet["worker"] != WORKER_VERSION:
        raise ValueError("PPA measurement implementation changed")
    if packet["flow"] != FLOW_VERSION:
        raise ValueError("PPA mapping flow changed")
    inputs = ComparisonInputs.from_dict(parse_json(read_verified(objects, BlobRef.from_dict(packet["inputs"]))), objects)
    experiment = Experiment.from_dict(packet["experiment"])
    if experiment.mapping.repair_design:
        from .mapping_repair import runtime
        if runtime(technology_name=experiment.mapping.technology).identity != packet.get("repair_identity"):
            raise ValueError("PPA mapping repair runtime changed")
    pool = fields(read_document(objects, BlobRef.from_dict(packet["pool"])), {"stimuli"})["stimuli"]
    if api.prepare_tool_environment("eda").identity != packet["eda_runtime"]:
        raise ValueError("PPA EDA runtime changed")
    if BlobRef.of((eda_root() / "libexec/yosys-abc").read_bytes()) != BlobRef.from_dict(packet["abc_ref"]):
        raise ValueError("PPA mapping executable changed")
    for name, ref in (("liberty", "library_ref"), ("sta", "sta_ref")):
        if BlobRef.of(Path(packet[name]).read_bytes()) != BlobRef.from_dict(packet[ref]):
            raise ValueError("PPA external dependency changed")
    directory = root / "execution"
    directory.mkdir()
    try:
        result = execute(inputs, experiment, packet["mode"], pool, directory, objects,
                         liberty=Path(packet["liberty"]), sta=Path(packet["sta"]))
    except api.FailureRaised as error:
        result = {"score": 0 if error.failure.disposition is api.FailureDisposition.SCORE_ZERO else None,
                  "code": error.failure.kind, "pool": pool, "evidence": []}
    except api.Refused as error:
        result = {"score": None, "code": error.refusal.kind, "pool": pool,
                  "evidence": [objects.put(canonical_json({"refusal": error.refusal.as_dict()})).as_dict()]}
    except Exception as error:  # noqa: BLE001 -- private service result, not model feedback
        result = {"score": None, "code": "ppa-service-" + type(error).__name__, "pool": pool,
                  "evidence": [objects.put(canonical_json({"error": str(error)})).as_dict()]}
    packed, dependencies = pack_result(objects, result)
    record, record_closure = write_document(objects, packed)
    closure = [ref.as_dict() for ref in dict.fromkeys((*record_closure, *dependencies))]
    response = canonical_json({"schema": "behave.ppa-worker/1", "request": request.as_dict(),
                                "result": {"record": record.as_dict(), "closure": closure}})
    if not install_exclusive(root, "response.json", response):
        raise ValueError("PPA worker response already exists")
