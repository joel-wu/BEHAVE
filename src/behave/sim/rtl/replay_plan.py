"""The closed input carrier for the common RTL replay loop, without simulator imports."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.contract import TaskContract
from ..core.stimulus import Stimulus, uses_global_events
from .execution_policy import execution_policy
from .source_boundary_capture import ObservationField, decode_fields

DRIVER_PLAN_VERSION = "rtl-driver-plan/24"
ATOMIC_DRIVER_PLAN_VERSION = "rtl-atomic-driver-plan/25"
ATOMIC_TRACE_VERSION = "rtl-atomic-driver-trace/13"


@dataclass(frozen=True)
class ReplayPlan:
    contract: TaskContract
    stimuli: tuple[Stimulus, ...]
    watch: tuple[str, ...]
    record_names: tuple[str, ...]
    state_fields: tuple[tuple[str, int | None], ...]
    initial_state: dict | None
    native_events: dict | None
    atomic: bool
    artifact: str
    completion_targets: dict | None = None
    source_boundary_fields: tuple[ObservationField, ...] = ()
    state_paths: tuple[tuple[str, tuple[str | int, ...] | None], ...] = ()
    startup_state: dict | None = None
    checkpoint: object = None
    safety_goals: tuple[str, ...] = ()
    source_boundary_paths: tuple = ()
    startup_aliases: tuple | None = ()
    source_history: str | None = None

    @property
    def records_initial_state(self):
        return bool(self.state_fields) or self.initial_state is not None or self.startup_state is not None

    @classmethod
    def from_dict(cls, plan):
        common = {"version", "contract", "stimuli", "watch", "record_names",
                  "initial_state", "execution_policy"}
        if type(plan) is not dict:
            raise RuntimeError("the driver plan has no complete versioned shape")
        version = plan.get("version")
        if version not in (DRIVER_PLAN_VERSION, ATOMIC_DRIVER_PLAN_VERSION):
            raise RuntimeError("the driver plan uses an unsupported version")
        atomic = version == ATOMIC_DRIVER_PLAN_VERSION
        required = common | ({"state_fields", "artifact"} if atomic else {"state_names"})
        if set(plan) - {"native_events", "completion_targets", "source_boundary_fields", "source_boundary_paths", "state_paths", "startup_state", "startup_aliases", "checkpoint", "safety_goals", "source_history"} != required:
            raise RuntimeError("the driver plan has no complete versioned shape")
        if plan["execution_policy"] != execution_policy(native_events="native_events" in plan):
            raise RuntimeError("the driver plan uses another scheduled execution policy")
        if type(plan["contract"]) is not TaskContract:
            raise RuntimeError("the driver plan needs a canonical contract")
        if type(plan["stimuli"]) not in (list, tuple) or any(
                type(stimulus) is not Stimulus for stimulus in plan["stimuli"]):
            raise RuntimeError("the driver plan needs canonical stimuli")
        products = {uses_global_events(plan["contract"], stimulus)
                    for stimulus in plan["stimuli"]} or {len(plan["contract"].clocks) > 1}
        if products != {atomic}:
            raise RuntimeError("the driver plan selects another clock product")
        watch, recorded = plan["watch"], plan["record_names"]
        if (not _canonical_names(watch) or not _canonical_names(recorded)
                or not set(recorded) <= set(watch)):
            raise RuntimeError("the driver plan names no canonical observation boundary")
        artifact = plan.get("artifact", "")
        if atomic:
            if type(artifact) is not str or not artifact:
                raise RuntimeError("the atomic driver plan has no source artifact identity")
            fields = plan["state_fields"]
            if type(fields) is not list or any(
                    type(row) is not list or len(row) != 2
                    or type(row[0]) is not str or not row[0]
                    or (row[1] is not None and (type(row[1]) is not int or row[1] < 1))
                    for row in fields):
                raise RuntimeError("the atomic state capability has an invalid shape")
            if len({row[0] for row in fields}) != len(fields):
                raise RuntimeError("the atomic state capability names one field twice")
        else:
            names = plan["state_names"]
            if not _canonical_names(names):
                raise RuntimeError("the driver plan names no canonical observation boundary")
            fields = [(name, None) for name in names]
        if "native_events" in plan and len(plan["stimuli"]) != 1:
            raise RuntimeError("a native collector belongs to one isolated stimulus")
        if "native_events" in plan and type(plan["native_events"]) is not dict:
            raise RuntimeError("native replay requires a collector plan")
        if plan["initial_state"] is not None and len(plan["stimuli"]) != 1:
            raise RuntimeError("an analysis initial choice belongs to one replay")
        if "startup_state" in plan:
            from .startup_state import RTLStartupState

            RTLStartupState.from_dict(plan["startup_state"])
            if plan["initial_state"] is not None or len(plan["stimuli"]) != 1:
                raise RuntimeError("a startup choice belongs to one replay without a later override")
        from .replay_startup import decode_input_aliases

        if plan.get('source_history') is not None:
            from .initial_state import RTLInitialState

            RTLInitialState(plan['source_history'], ())  # Validate the bound artifact digest.
            if ('native_events' not in plan or 'startup_state' in plan
                    or plan['initial_state'] is not None or len(plan['stimuli']) != 1):
                raise RuntimeError('passive source history belongs to one ordinary native replay')
        return cls(plan["contract"], tuple(plan["stimuli"]), tuple(watch), tuple(recorded),
                   tuple(map(tuple, fields)), plan["initial_state"],
                   plan.get("native_events"), atomic, artifact, plan.get("completion_targets"),
                   decode_fields(plan.get("source_boundary_fields", ())),
                   tuple(plan.get("state_paths", ((name, (name,)) for name, _ in fields))),
                   plan.get("startup_state"), plan.get("checkpoint"),
                   tuple(plan.get("safety_goals", ())),
                   tuple(plan.get("source_boundary_paths", ())),
                   decode_input_aliases(plan.get('startup_aliases', () if 'startup_state' not in plan else None)),
                   plan.get('source_history'))


def _canonical_names(values):
    return (type(values) is list and all(type(name) is str and name for name in values)
            and values == sorted(set(values)))
