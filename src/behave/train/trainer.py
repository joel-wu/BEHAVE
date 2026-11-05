"""Native update/checkpoint ordering shared by Task and Raw learners.

Slime owns optimization and checkpoint serialization. This layer binds its
completed saves to our input history, and permits an explicit new-round parent
without accepting a different input binding as same-run recovery.
"""

from __future__ import annotations

import math
import os
import random
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile, mkdtemp
from types import MappingProxyType
from typing import Any, Protocol

from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import load_chain
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, ContentReader, DirectoryReader, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.paths import scratch_root
from behave.core.records import (
    RecordError,
    array,
    canonical_json,
    fields,
    metadata,
    parse_json,
    text,
    thaw_json,
    version,
)
from behave.core.storage import DirectoryStore
from behave.isolation import service
from behave.isolation.service import ServiceResult

from .checkpoint_storage import retain_checkpoints, retention_policy
from .checkpoints import Checkpoint, TrainingLedger, TrainingState, copy_state_file, store_state_file, verify_state
from .cluster_state import SlurmTraining
from .slime_checkpoint import CheckpointFile, checkpoint_due, checkpoint_inventory
from .update_records import TrainerContract, UpdateRequest


def active_events(events):
    """Logical update history after explicit rollbacks; raw journals stay intact."""
    active = []
    for event in events:
        if event.kind == "round_abandoned":
            step = event.payload["resume_after"]
            end = next((i + 1 for i in range(len(active) - 1, -1, -1)
                        if active[i].kind == "checkpoint_saved" and active[i].payload["round"] == step), 0)
            active = active[:end]
        else:
            active.append(event)
    return tuple(active)


@dataclass(frozen=True, slots=True)
class NativeAnchor:
    head: BlobRef
    step: int
    files: tuple[CheckpointFile, ...]
    route: str
    retention_events: tuple = ()

    @classmethod
    def load(cls, reader, head):
        events = load_chain(reader, head)
        if len(events) < 2 or events[-1].kind != "checkpoint_saved":
            raise RecordError("native-parent-incomplete", "parent must be a committed native checkpoint")
        prior, saved = events[-2:]
        key = {"round_scored": "score", "raw_batch_ready": "batch"}.get(prior.kind)
        if key is None:
            raise RecordError("native-parent-invalid", "checkpoint must follow a prepared learning batch")
        row = fields(thaw_json(saved.payload), {"round", "binding", "native_directory", "files", key})
        if (row[key] != BlobRef.of(prior.to_bytes()).as_dict() or
                row["binding"] != thaw_json(prior.payload)["binding"] or row["round"] != prior.payload["round"]):
            raise RecordError("native-parent-invalid", "parent checkpoint and learning batch disagree")
        if type(row["round"]) is not int or row["round"] < 0:
            raise RecordError("native-parent-invalid", "parent checkpoint has no update cursor")
        files = tuple(CheckpointFile.from_dict(file) for file in row["files"])
        if not files or len({file.path for file in files}) != len(files):
            raise RecordError("native-parent-invalid", "parent checkpoint needs a complete distinct file inventory")
        return cls(head, row["round"], files, "task" if key == "score" else "raw",
                   tuple(event for event in active_events(events) if event.kind == "checkpoint_saved"))


class NativeUpdates:
    def __init__(self, *, args, run, identity: BlobRef, ready_kind: str, evidence_key: str,
                 critic: bool = False, anchor: NativeAnchor | None = None, retention=None):
        self.args, self.run, self.identity = args, run, identity
        self.journal = run.journal("rounds")
        self.ready_kind, self.evidence_key = ready_kind, evidence_key
        self.critic, self.anchor = critic, anchor
        self.retention = None if retention is None else retention_policy(retention)
        if anchor is not None and anchor.route != ("task" if evidence_key == "score" else "raw"):
            raise ValueError("native parent belongs to the other learning route")
        checkpoint_due(args, 0)

    @property
    def first_step(self):
        return 0 if self.anchor is None else self.anchor.step + 1

    def _check_files(self, root, step, expected):
        actual = checkpoint_inventory(Path(root), step, critic=self.critic)
        if actual != tuple(CheckpointFile.from_dict(file) for file in expected):
            raise ValueError("native checkpoint file inventory differs from the saved round")
        return actual

    def load(self, step):
        if type(step) is not int or step < -1:
            raise ValueError("explicit native checkpoint iteration required")
        with self.run.owner():
            events = active_events(self.journal.load())
            if not events:
                if self.anchor is None:
                    if step != -1:
                        raise ValueError("native checkpoint has no matching BEHAVE round history")
                else:
                    if step != self.anchor.step:
                        raise ValueError("native checkpoint disagrees with the new round's parent")
                    self._check_files(self.args.load, step, [file.as_dict() for file in self.anchor.files])
                return
            last = events[-1]
            if (last.kind != "checkpoint_saved" or last.payload["round"] != step or
                    last.payload["binding"] != self.identity.as_dict()):
                raise ValueError("native checkpoint and BEHAVE round history disagree or an update is pending")
            self._check_files(self.args.load, step, thaw_json(last.payload)["files"])

    def abandon(self, *, reason: str, workers_stopped: bool):
        """Operator rollback after fencing old workers, never automatic retry.

        Only the first uncommitted round can reuse a completed scored receipt:
        later rollouts may have been generated after uncommitted weight updates.
        Native checkpoint directories and all prior evidence remain untouched.
        """
        if not workers_stopped:
            raise ValueError("confirm old training/rollout workers have stopped before recovery")
        text(reason, "recovery reason")
        with self.run.owner():
            events = self.journal.load()
            if not events or events[-1].kind in {"checkpoint_saved", "round_abandoned"}:
                raise ValueError("no interrupted update tail to abandon")
            active = active_events(events)
            if any(e.payload["binding"] != self.identity.as_dict() for e in active):
                raise ValueError("native round history belongs to another input binding")
            saved = next((e for e in reversed(active) if e.kind == "checkpoint_saved"), None)
            step = saved.payload["round"] if saved is not None else self.first_step - 1
            checkpoint = (BlobRef.of(saved.to_bytes()) if saved is not None else
                          None if self.anchor is None else self.anchor.head)
            if saved is not None or self.anchor is not None:
                files = thaw_json(saved.payload)["files"] if saved is not None else [f.as_dict() for f in self.anchor.files]
                self._check_files(self.args.load, step, files)
            elif (Path(self.args.load) / "latest_checkpointed_iteration.txt").exists():
                raise ValueError("no committed native checkpoint; restart from the original HF model")
            reuse = None
            if self.ready_kind == "round_scored":
                for event in reversed(active):
                    if event.payload["round"] == step + 1:
                        reuse = event.payload.get("receipt") if event.kind == self.ready_kind else (
                            event.payload.get("recorded_receipt") if event.kind == "round_started" else None)
                        if reuse is not None:
                            break
            row = {"binding": self.identity.as_dict(), "round": events[-1].payload["round"],
                   "resume_after": step, "checkpoint": None if checkpoint is None else checkpoint.as_dict(),
                   "reuse_receipt": None if reuse is None else thaw_json(reuse), "reason": reason}
            refs = () if reuse is None else (BlobRef.from_dict(thaw_json(reuse)),)
            self.journal.append(events, "round_abandoned", row, refs)
            return {**row, "load": str(Path(self.args.load).resolve()), "next_round": step + 1}

    def save(self, step):
        if type(step) is not int or step < 0:
            raise ValueError("explicit nonnegative native checkpoint iteration required")
        with self.run.owner():
            events = self.journal.load()
            if not events or events[-1].kind != self.ready_kind or events[-1].payload["round"] != step:
                raise ValueError("native save must follow this round's verified learning batch")
            if events[-1].payload["binding"] != self.identity.as_dict():
                raise ValueError("native save belongs to another input binding")
            marker = Path(self.args.save) / "latest_checkpointed_iteration.txt"
            if marker.read_text().strip() != str(step):
                raise ValueError("native checkpoint save has not completed for this round")
            files = checkpoint_inventory(Path(self.args.save), step, critic=self.critic)
            self.journal.append(events, "checkpoint_saved", {"round": step,
                "binding": self.identity.as_dict(), "native_directory": str(Path(self.args.save).absolute()),
                "files": [file.as_dict() for file in files],
                self.evidence_key: BlobRef.of(events[-1].to_bytes()).as_dict()})
            if self.retention is not None:
                retain_checkpoints(self.args.save, active_events(self.journal.load()),
                                   self.retention, critic=self.critic,
                                   parent_events=() if self.anchor is None else self.anchor.retention_events)

    def require_next(self, events, step, live_ready):
        events = active_events(events)
        if type(step) is not int or not self.first_step <= step < self.args.num_rollout:
            raise ValueError("native rollout id must be within the explicit stopping round")
        last = events[-1] if events else None
        if last is not None and last.payload["binding"] != self.identity.as_dict():
            raise ValueError("native round history belongs to another input binding")
        expected = self.first_step if last is None else last.payload["round"] + 1
        live_unsaved = (last is not None and last.kind == self.ready_kind
                        and live_ready == BlobRef.of(last.to_bytes())
                        and not checkpoint_due(self.args, last.payload["round"]))
        if step != expected or (last and last.kind != "checkpoint_saved" and not live_unsaved):
            raise ValueError("round already used, out of order, or prior update unresolved")
        if not events and self.anchor is not None:
            self._check_files(self.args.load, self.anchor.step, [file.as_dict() for file in self.anchor.files])


class Stateful(Protocol):
    def state_dict(self) -> Any: ...

    def load_state_dict(self, state: Any) -> Any: ...


class TorchRNG:
    """Explicit Python/torch CPU globals, optional NumPy globals/CUDA devices.

    Independent Generators, worker RNGs and model-parallel trackers must be
    registered separately by the backend. Empty cuda_devices never probes or
    initializes CUDA. Device indices are local to the fixed backend allocation.
    """

    def __init__(self, *, numpy: bool = False, cuda_devices: tuple[int, ...] = ()):
        if type(numpy) is not bool or type(cuda_devices) is not tuple:
            raise TypeError("RNG domains require a boolean and a tuple of device indices")
        if any(type(i) is not int or i < 0 for i in cuda_devices) or len(set(cuda_devices)) != len(cuda_devices):
            raise ValueError("CUDA RNG device indices must be distinct nonnegative integers")
        self.numpy = numpy
        self.cuda_devices = cuda_devices

    def state_dict(self) -> dict:
        import torch

        numpy_state = None
        if self.numpy:
            import numpy as np

            algorithm, keys, position, has_gauss, cached = np.random.get_state()
            # Primitive representation remains loadable with weights_only=True.
            numpy_state = (algorithm, keys.tolist(), position, has_gauss, cached)
        return {
            "schema": "behave.torch-rng/1",
            "python": random.getstate(),
            "cpu": torch.get_rng_state(),
            "numpy": numpy_state,
            "cuda": {i: torch.cuda.get_rng_state(i) for i in self.cuda_devices},
        }

    def load_state_dict(self, state: dict) -> None:
        import torch

        row = fields(state, {"schema", "python", "cpu", "numpy", "cuda"})
        version(row["schema"], "behave.torch-rng/1")
        if (row["numpy"] is not None) != self.numpy or tuple(row["cuda"]) != self.cuda_devices:
            raise RecordError("checkpoint-rng-domains", "restore must use the same explicit RNG domains")
        random.setstate(row["python"])
        torch.set_rng_state(row["cpu"])
        if self.numpy:
            import numpy as np

            algorithm, keys, position, has_gauss, cached = row["numpy"]
            np.random.set_state((algorithm, np.asarray(keys, dtype=np.uint32), position, has_gauss, cached))
        for device, value in row["cuda"].items():
            torch.cuda.set_rng_state(value, device)


class TorchState:
    """Named native objects in explicit restoration order; no algorithm policy.

    Include model, rng and sampler plus every stateful training component in the
    contract (optimizer, scheduler, scaler, generators, etc.). The bridge cannot
    discover omitted state or make dataloader prefetch resumable automatically.
    """

    def __init__(self, components: Mapping[str, Stateful]):
        if not {"model", "rng", "sampler"} <= components.keys():
            raise ValueError("native state must include model, rng and sampler")
        for name, obj in components.items():
            text(name, "native state component")
            if not callable(getattr(obj, "state_dict", None)) or not callable(getattr(obj, "load_state_dict", None)):
                raise TypeError("native components must implement state_dict and load_state_dict")
        self._components = dict(components)

    @property
    def components(self) -> tuple[str, ...]:
        return tuple(self._components)

    @property
    def _order(self) -> tuple[str, ...]:
        return tuple(name for name in self._components if name != "rng") + ("rng",)

    def _layout(self) -> list:
        return [[name, type(obj).__module__ + "." + type(obj).__qualname__] for name, obj in self._components.items()]

    def save(self, objects: DirectoryStore, *, cursor: Mapping) -> TrainingState:
        """Serialize each component to local scratch, then stream to CAS.

        policy identifies these serialized model-state bytes, not a serving
        checkpoint or proof that an endpoint loaded them.
        """
        import torch

        files = []
        for index, name in enumerate(self._order):
            obj = self._components[name]
            payload = {
                "schema": "behave.torch-component/1",
                "torch": str(torch.__version__),
                "layout": self._layout(),
                "component": name,
                "state": obj.state_dict(),
            }
            with TemporaryFile(dir=scratch_root()) as stream:
                torch.save(payload, stream)
                stream.seek(0)
                file = store_state_file(
                    stream, objects, component=name, path=f"native/{index:02d}.pt"
                )
            files.append(file)
        policy = next(file.content for file in files if file.component == "model")
        return TrainingState(policy, tuple(files), cursor)

    def restore(self, reader: ContentReader, state: TrainingState) -> None:
        """Verify/decode the complete native set before invoking any load hook.

        Tensors are decoded on CPU and copied/cast by native load_state_dict.
        Exact torch version and object layout are required. No unsafe-pickle or
        partial/weights-only-resume fallback is attempted. Decoded state can be
        model-sized; this is intentionally not the framework-free coordinator.
        """
        import torch

        files = {file.component: file for file in state.files}
        if len(files) != len(state.files) or files.keys() != self._components.keys():
            raise RecordError("checkpoint-components-mismatch", "expected exactly one file per native component")
        if state.policy != files["model"].content:
            raise RecordError("checkpoint-policy-mismatch", "native policy identity must bind the model file")
        payloads = {}
        for name in self._order:
            with TemporaryFile(dir=scratch_root()) as stream:
                copy_state_file(reader, files[name], stream)
                stream.seek(0)
                row = fields(
                    torch.load(stream, map_location="cpu", weights_only=True),
                    {"schema", "torch", "layout", "component", "state"},
                )
            version(row["schema"], "behave.torch-component/1")
            if row["torch"] != str(torch.__version__) or row["layout"] != self._layout() or row["component"] != name:
                raise RecordError("checkpoint-native-layout", "native version, component or load order changed")
            payloads[name] = row["state"]
        for name in self._order:
            self._components[name].load_state_dict(payloads[name])


@dataclass(frozen=True, slots=True)
class DistributedLaunch:
    """One fixed world, optionally placed on explicit allocated Slurm nodes.

    python names the operator-pinned runtime containing torch.distributed.run.
    The backend selects DDP/FSDP, collectives and the algorithm. No elastic
    restart or automatic world-size change is permitted by this boundary.
    """

    python: str
    ranks: int
    cluster: SlurmTraining | None = None

    def __post_init__(self):
        if type(self.python) is not str or not Path(self.python).is_absolute() or "\0" in self.python:
            raise ValueError("distributed runtime needs an absolute Python executable")
        if type(self.ranks) is not int or self.ranks < 2:
            raise ValueError("distributed world must contain at least two ranks")
        if self.cluster is not None:
            if type(self.cluster) is not SlurmTraining:
                raise TypeError("cluster must be an explicit SlurmTraining")
            if self.ranks % len(self.cluster.nodes):
                raise ValueError("fixed-world ranks must divide evenly across nodes")
            local = self.ranks // len(self.cluster.nodes)
            if any(node.gpus not in (0, local) for node in self.cluster.nodes):
                raise ValueError("GPU placement requires one visible GPU per local rank")
            if len({bool(node.gpus) for node in self.cluster.nodes}) != 1:
                raise ValueError("mixed CPU/GPU placement requires a different backend topology")

    def as_dict(self):
        return {"python": self.python, "ranks": self.ranks,
                **({"cluster": self.cluster.as_dict()} if self.cluster is not None else {})}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"python", "ranks"} | ({"cluster"} if type(value) is dict and "cluster" in value else set()))
        return cls(**{**row, "cluster": SlurmTraining.from_dict(row["cluster"]) if "cluster" in row else None})


def read_record(reader: ContentReader, ref: BlobRef) -> bytes:
    return read_verified(reader, ref)


@dataclass(frozen=True, slots=True)
class TrainerProcess:
    argv: tuple[str, ...]
    environment: MappingProxyType
    wall_seconds: int | None
    cleanup_seconds: int | None = defaults.TRAIN_CLEANUP_SECONDS
    distributed: DistributedLaunch | None = None

    def __post_init__(self):
        if self.distributed is not None and type(self.distributed) is not DistributedLaunch:
            raise TypeError("distributed launch must be an explicit DistributedLaunch")
        if type(self.argv) is not tuple or not self.argv:
            raise ValueError("trainer needs a nonempty command tuple")
        if any(type(arg) is not str or "\0" in arg for arg in self.argv):
            raise ValueError("trainer arguments must be strings without NUL")
        if not Path(self.argv[0]).is_absolute():
            raise ValueError("trainer needs an absolute executable")
        env = metadata(self.environment)
        if (
            any(
                not key or "=" in key or "\0" in key or type(value) is not str or "\0" in value
                for key, value in env.items()
            )
        ):
            raise ValueError("trainer environment must be an explicit string mapping")
        object.__setattr__(self, "environment", env)
        if (self.distributed is not None and self.distributed.cluster is not None
                and "CUDA_VISIBLE_DEVICES" in env):
            raise ValueError("cross-node GPU visibility is owned by each allocated worker")
        for name in ("wall_seconds", "cleanup_seconds"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"invalid trainer {name}")

    def as_dict(self):
        return {
            **{name: getattr(self, name) for name in self.__dataclass_fields__ if name != "distributed"},
            "argv": list(self.argv),
            "environment": thaw_json(self.environment),
            **({"distributed": self.distributed.as_dict()} if self.distributed is not None else {}),
        }

    @classmethod
    def from_dict(cls, value):
        keys = set(cls.__dataclass_fields__) - {"distributed"}
        row = fields(value, keys | ({"distributed"} if type(value) is dict and "distributed" in value else set()))
        return cls(**{**row, "argv": tuple(array(row["argv"])),
                      "distributed": DistributedLaunch.from_dict(row["distributed"]) if "distributed" in row else None})


def invocation_bytes(contract, pending, checkpoint, process: TrainerProcess) -> bytes:
    """The examples are the ledger's verified selection, byte for byte."""
    examples = tuple(canonical_json(row) for row in pending.examples)
    data = canonical_json(
        {
            "schema": "behave.trainer-invocation/2" if process.distributed is not None else "behave.trainer-invocation/1",
            "process": process.as_dict(),
            "contract": parse_json(contract.to_bytes()),
            "request": parse_json(pending.request.to_bytes()),
            "state": parse_json(checkpoint.state.to_bytes()),
            "examples": [BlobRef.of(row).as_dict() for row in examples],
        }
    )
    return data


def verify_invocation(reader, ref, contract, pending, checkpoint):
    data = read_record(reader, ref)
    row = parse_json(data)
    process = TrainerProcess.from_dict(row.get("process"))
    if data != invocation_bytes(contract, pending, checkpoint, process):
        raise RecordError("trainer-invocation-mismatch", "trainer input differs from the pending update")
    refs = tuple(BlobRef.from_dict(item) for item in row["examples"])
    for example, expected in zip(refs, pending.examples, strict=True):
        if read_record(reader, example) != canonical_json(expected):
            raise RecordError("trainer-example-mismatch", "trainer example differs from verified evidence")
    return process, (ref, *refs)


def receipt_bytes(invocation: BlobRef, result: ServiceResult, objects, *, state: BlobRef | None = None,
                  cluster: BlobRef | None = None) -> bytes:
    return canonical_json(
        {
            "schema": "behave.trainer-receipt/4" if cluster is not None else "behave.trainer-receipt/3",
            **({"cluster": cluster.as_dict()} if cluster is not None else {}),
            "invocation": invocation.as_dict(),
            "state": None if state is None else state.as_dict(),
            "status": result.status,
            "execution_status": result.execution_status,
            "returncode": result.returncode,
            "stdout": objects.put(result.stdout).as_dict(),
            "stderr": objects.put(result.stderr).as_dict(),
            "elapsed_seconds": result.elapsed_seconds,
            "cleanup_elapsed_seconds": result.cleanup_elapsed_seconds,
        }
    )


def verify_receipt(reader, ref, invocation: BlobRef, *, require_state: bool = False):
    data = read_record(reader, ref)
    decoded = parse_json(data)
    legacy = decoded.get("schema") in {"behave.trainer-receipt/1", "behave.trainer-receipt/2"}
    row = fields(
        decoded,
        {
            "schema",
            "invocation",
            "state",
            "status",
            "returncode",
            "stdout",
            "stderr",
            "elapsed_seconds",
            "cleanup_elapsed_seconds",
        } | ({"cluster"} if "cluster" in decoded else set())
        | (set() if legacy else {"execution_status"}),
    )
    version(row["schema"], ("behave.trainer-receipt/2" if "cluster" in row else "behave.trainer-receipt/1")
            if legacy else ("behave.trainer-receipt/4" if "cluster" in row else "behave.trainer-receipt/3"))
    if row["invocation"] != invocation.as_dict() or data != canonical_json(row):
        raise RecordError("trainer-receipt-mismatch", "receipt belongs to another invocation or is not canonical")
    call = parse_json(read_record(reader, invocation))
    process = TrainerProcess.from_dict(call["process"])
    if row["status"] not in {
        "exited",
        "timeout",
        "cancelled",
        "output-limit",
        "launch-failed",
        "cleanup-incomplete",
        "supervisor-error",
    }:
        raise RecordError("trainer-status-invalid", "unknown trainer execution outcome")
    code = row["returncode"]
    if (
        code is not None
        and (type(code) is not int or not -255 <= code <= 255)
        or row["status"] == "exited"
        and code is None
    ):
        raise RecordError("trainer-exit-invalid", "invalid trainer return code")
    for name in ("elapsed_seconds", "cleanup_elapsed_seconds"):
        value = row[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise RecordError("trainer-time-invalid", "invalid trainer elapsed time")
    refs = tuple(BlobRef.from_dict(row[name]) for name in ("stdout", "stderr"))
    try:
        result = ServiceResult(
            row["status"], code, *(read_record(reader, item) for item in refs),
            row["elapsed_seconds"], row["cleanup_elapsed_seconds"],
            execution_status=None if legacy else row["execution_status"],
        )
    except ValueError as error:
        raise RecordError("trainer-status-invalid", str(error)) from None
    if process.distributed is not None and process.distributed.cluster is not None:
        if "cluster" not in row:
            if result.status != "supervisor-error" or row["state"] is not None:
                raise RecordError("trainer-cluster-receipt-missing", "remote cleanup requires all node receipts")
        else:
            from .cluster import verify_cluster_receipt

            aggregate, cluster_refs = verify_cluster_receipt(reader, BlobRef.from_dict(row["cluster"]), invocation)
            refs += cluster_refs
            if (result.cleanup_state != aggregate.cleanup_state
                    or result.status == "exited" and result.returncode == 0
                    and (aggregate.status != "exited" or aggregate.returncode != 0 or result.stdout != aggregate.stdout)):
                raise RecordError("trainer-cluster-result-mismatch", "trainer result differs from node cleanup/state evidence")
    elif "cluster" in row:
        raise RecordError("trainer-cluster-unexpected", "local trainer cannot supply cluster receipts")
    if row["state"] is not None:
        state_ref = BlobRef.from_dict(row["state"])
        state = response_state(result, BlobRef.of(canonical_json(call["request"])), process)
        if read_record(reader, state_ref) != state.to_bytes():
            raise RecordError("trainer-state-mismatch", "captured state differs from the process response")
        contract = TrainerContract.from_bytes(canonical_json(call["contract"]))
        verify_state(reader, state, contract.components)
        refs += (state_ref, *state.references)
    elif require_state:
        raise RecordError("trainer-state-not-captured", "receipt has no fully captured successful checkpoint")
    return result, (ref, *refs)


def response_state(result: ServiceResult, request: BlobRef, process: TrainerProcess) -> TrainingState:
    if result.status != "exited" or result.returncode != 0:
        raise RecordError("trainer-not-successful", "only a clean zero exit can publish the next checkpoint")
    row = fields(parse_json(result.stdout), {"schema", "request", "state"})
    version(row["schema"], "behave.trainer-response/1")
    if row["request"] != request.as_dict() or result.stdout != canonical_json(row):
        raise RecordError("trainer-response-mismatch", "trainer response belongs to another update or is not canonical")
    state = TrainingState.from_bytes(canonical_json(row["state"]))
    if process.distributed is not None:
        from .cluster_state import unpack_rank_states

        unpack_rank_states(state, process.distributed.ranks)
    return state


def response_bytes(request: BlobRef, state: TrainingState) -> bytes:
    """Backend stdout protocol; diagnostics belong on stderr."""
    return canonical_json(
        {"schema": "behave.trainer-response/1", "request": request.as_dict(), "state": parse_json(state.to_bytes())}
    )


class TrainerExecutionError(AttemptInterrupted):
    def __init__(self, reason: str, *, receipt: BlobRef, workspace: Path | None = None):
        super().__init__(reason)
        self.receipt = receipt
        self.workspace = workspace  # Unconfirmed live staging; clean staging is archived separately.


def execute_update(
    ledger: TrainingLedger,
    request: UpdateRequest,
    reader: ContentReader,
    process: TrainerProcess,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> Checkpoint:
    """Use an already-owned ledger; return the committed checkpoint on retry.

    No parent credentials are inherited. Local staging uses scratch_root. A cluster uses
    explicit shared CAS exchange with node-local native scratch; only referenced
    output chunks and durable evidence enter the run's object store.
    Checkpoint data is streamed in chunks without an aggregate byte quota.
    Preflight failures leave no pending update;
    after begin, failures require the recorded outcome or explicit reconciliation.
    """
    if type(process) is not TrainerProcess:
        raise TypeError("trainer requires explicit process configuration")
    cluster = process.distributed.cluster if process.distributed is not None else None
    ticket = ledger.prepare(request, reader)
    if isinstance(ticket, Checkpoint):
        return ticket
    if cancelled is not None and cancelled():
        raise AttemptInterrupted("trainer-update-cancelled-before-dispatch")
    state = ledger.checkpoint.state
    if process.distributed is not None:
        from .cluster_state import unpack_rank_states

        unpack_rank_states(state, process.distributed.ranks)
    if cluster is None:
        workspace = Path(mkdtemp(prefix="behave-trainer-", dir=scratch_root(process.environment)))
    else:
        workspace = Path(cluster.shared_directory) / ticket.reference.sha256
        # Never reuse even a previously clean dispatch directory. This marker
        # survives coordinator death and is reconstructible from the invocation.
        workspace.mkdir(mode=0o700, parents=True, exist_ok=False)
    clean = True
    requested = False
    receipt = None
    try:
        inputs, output = DirectoryStore(workspace / "input"), DirectoryStore(workspace / "output")
        inputs.publish(ledger.run.objects, (ledger.contract.configuration, request.decision, *state.references))
        for example in ticket.examples:
            inputs.put(canonical_json(example))
        invocation = inputs.put(invocation_bytes(ledger.contract, ticket, ledger.checkpoint, process))
        if cancelled is not None and cancelled():
            raise AttemptInterrupted("trainer-update-cancelled-before-dispatch")
        # All fallible local preparation precedes launch authorization. begin
        # still rechecks identity/exposures; a lost append acknowledgement never
        # permits redispatch, even if no invocation has yet been recorded.
        requested = True
        ticket = ledger.begin(request, ledger.run.objects)
        ledger.record_dispatch(invocation, inputs)
        # Bound the service itself after staging, before permitting any native code.
        clean = False
        cluster_receipt = None
        try:
            argv = process.argv
            if process.distributed is not None and cluster is None:
                from . import cluster as cluster_module

                argv = (sys.executable, "-I", "-B", cluster_module.__file__)
            if cluster is not None:
                from .cluster import run_cluster

                result, cluster_receipt = run_cluster(
                    process, workspace, invocation, inputs, output, ledger.run.objects, cancelled=cancelled,
                )
            else:
                result = service.run_service(
                    argv,
                    cwd=workspace,
                    environment={**process.environment, "TMPDIR": str(workspace), "BEHAVE_SCRATCH_DIR": str(workspace)},
                    deadline=None if process.wall_seconds is None else time.monotonic() + process.wall_seconds,
                    output_bytes=None,
                    cleanup_seconds=process.cleanup_seconds,
                    input_data=canonical_json(
                        {
                            "invocation": invocation.as_dict(),
                            "input": str(inputs.directory),
                            "output": str(output.directory),
                        }
                    ),
                    network="host" if process.distributed is not None else "none",
                    cancelled=cancelled,
                )
        except Exception:  # noqa: BLE001 -- uncertain supervisor outcomes must remain unresolved
            # The supervisor may have lost an acknowledgement after launch.
            # Do not publish exception text (it can contain operator secrets).
            result = service.ServiceResult("supervisor-error", None, b"", b"", 0.0)
        clean = result.cleanup_state == CleanupState.COMPLETE
        after = state_ref = None
        invalid = None
        try:
            after = response_state(result, ticket.reference, process)
            verify_state(output, after, ledger.contract.components)
            ledger.run.objects.publish(output, after.references)
            state_ref = ledger.run.objects.put(after.to_bytes())
        except (ValueError, OSError) as error:
            invalid = error
        # A success receipt includes all checkpoint chunks *before* its journal
        # append. A lost return acknowledgement can then be completed offline,
        # even when node-local staging has been cleaned up.
        receipt = ledger.run.objects.put(receipt_bytes(invocation, result, ledger.run.objects,
                                                      state=state_ref, cluster=cluster_receipt))
        ledger.record_return(receipt, ledger.run.objects)
        if invalid is not None:
            raise TrainerExecutionError(
                "trainer-update-uncommitted",
                receipt=receipt,
                workspace=None if clean else workspace,
            ) from invalid
        try:
            return ledger.commit(ticket.reference, after, ledger.run.objects)
        except (ValueError, OSError) as error:
            raise TrainerExecutionError(
                "trainer-update-uncommitted",
                receipt=receipt,
                workspace=None if clean else workspace,
            ) from error
    finally:
        # Never move files a live trainer may still be using. Clean staging is
        # retained for the operator, including failed pre-dispatch attempts.
        if clean:
            if cluster is None or not requested:
                retained = Path(mkdtemp(prefix="retained-trainer-", dir=workspace.parent))
                workspace.rename(retained / workspace.name)
            else:
                # Keep node journals/dispatch keys for operator inspection.
                # Move staging aside for explicit operator cleanup, never delete.
                retained = workspace / "retained"
                retained.mkdir(exist_ok=True)
                for name in ("input", "output"):
                    if (workspace / name).exists():
                        (workspace / name).rename(retained / name)


def commit_recorded_update(ledger: TrainingLedger, request: BlobRef) -> Checkpoint:
    """Finish an acknowledged clean process result, without starting a trainer.

    Use only after reopening a lost-acknowledgement ledger. A missing, failed or
    uncertain receipt cannot be reconciled by this operation. Its chunks must
    already be in the verified ledger closure, not an arbitrary staging folder.
    """
    pending = ledger.pending
    if pending is None or pending.reference != request or pending.invocation is None or pending.receipt is None:
        raise RecordError("trainer-reconciliation-unavailable", "no matching recorded process result")
    result, _ = verify_receipt(ledger.run.objects, pending.receipt, pending.invocation, require_state=True)
    call = parse_json(read_record(ledger.run.objects, pending.invocation))
    state = response_state(result, request, TrainerProcess.from_dict(call["process"]))
    return ledger.commit(request, state, ledger.run.objects)


@dataclass(frozen=True, slots=True)
class BackendUpdate:
    request: UpdateRequest
    contract: TrainerContract
    state: TrainingState
    examples: tuple[BlobRef, ...]
    inputs: DirectoryReader
    output: DirectoryStore
    process: TrainerProcess

    @property
    def rank(self) -> int:
        """Rank in the fixed world; selection/batching remain backend decisions."""
        if self.process.distributed is None:
            return 0
        rank = int(os.environ["BEHAVE_TRAIN_RANK"])
        if int(os.environ["BEHAVE_TRAIN_WORLD_SIZE"]) != self.world_size or not 0 <= rank < self.world_size:
            raise RecordError("trainer-rank-mismatch", "rank environment differs from the dispatched world")
        return rank

    @property
    def world_size(self) -> int:
        return self.process.distributed.ranks if self.process.distributed is not None else 1

    @classmethod
    def read(cls) -> BackendUpdate:
        payload = sys.stdin.buffer.read()
        return cls.from_envelope(payload)

    @classmethod
    def from_envelope(cls, payload: bytes) -> BackendUpdate:
        """Decode the complete invocation from a trusted dispatch file."""
        if type(payload) is not bytes:
            raise TypeError("trainer envelope must be bytes")
        envelope = fields(parse_json(payload), {"invocation", "input", "output"})
        if (
            any(type(envelope[key]) is not str or not Path(envelope[key]).is_absolute() for key in ("input", "output"))
            or envelope["input"] == envelope["output"]
        ):
            raise RecordError("trainer-directory-invalid", "trainer input/output require distinct absolute directories")
        inputs = DirectoryReader(envelope["input"])
        data = read_record(inputs, BlobRef.from_dict(envelope["invocation"]))
        row = fields(parse_json(data), {"schema", "process", "contract", "request", "state", "examples"})
        process = TrainerProcess.from_dict(row["process"])
        version(row["schema"], "behave.trainer-invocation/2" if process.distributed is not None else "behave.trainer-invocation/1")
        result = cls(
            UpdateRequest.from_bytes(canonical_json(row["request"])),
            TrainerContract.from_bytes(canonical_json(row["contract"])),
            TrainingState.from_bytes(canonical_json(row["state"])),
            tuple(BlobRef.from_dict(ref) for ref in array(row["examples"])),
            inputs,
            DirectoryStore(envelope["output"]),
            process,
        )
        if data != canonical_json(row) or len(result.examples) != len(result.request.uses):
            raise RecordError(
                "trainer-invocation-mismatch", "trainer invocation must be canonical with one row per use"
            )
        verify_state(inputs, result.state, result.contract.components)
        return result

    def iter_examples(self) -> Iterator[dict]:
        for ref in self.examples:
            yield parse_json(read_record(self.inputs, ref))

    @property
    def configuration(self) -> bytes:
        return read_record(self.inputs, self.contract.configuration)

    @property
    def decision(self) -> bytes:
        return read_record(self.inputs, self.request.decision)

    def finish(self, state: TrainingState) -> None:
        verify_state(self.output, state, self.contract.components)
        sys.stdout.buffer.write(response_bytes(BlobRef.of(self.request.to_bytes()), state))
        sys.stdout.buffer.flush()
