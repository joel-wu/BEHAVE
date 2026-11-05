"""The fixed drive-before-clock profile used by both RTL replay drivers.

A scheduled step is a composition, not a simultaneous reset/clock event.  The
policy identifies an execution convention; it is not a correctness certificate
for the source simulator or the encoded relation.
"""

from ..core.stimulus import canonical_json

EXECUTION_POLICY_VERSION = "rtl-scheduled-execution/11"


def execution_policy(*, native_events=False):
    """Return a fresh, JSON-shaped description of the supported phase order."""

    if type(native_events) is not bool:
        raise TypeError("native event observation is an exact Boolean")
    policy = {
        "version": EXECUTION_POLICY_VERSION,
        "phases": [
            "drive-controls-and-payloads",
            "settle-control-events",
            "sample-transfer-boundary",
            "commit-active-clock-batch",
            "settle-clock-events",
            "return-clock-to-inactive",
            "settle-clock-return-events",
            "record-successor",
        ],
        "horizon_unit": "scheduled-step",
        "initial_state": "before-first-drive",
        "transfer_sample": "after-control-settle-before-clock",
        "clock_timebase": "declared-domain-active-edges",
        "clock_return": "inactive-edge-settles-before-successor",
        "reset_clock_order": "control-events-before-clock-batch",
        "structural_sample": "none-interface-only",
        "assertions": "enabled-original-actions",
        "memory_successors": "complete-snapshot-or-memory-successor-writes/1",
        "runtime_stop": "first-determined-protocol-violation/1",
    }
    if native_events:
        # Same physical schedule, explicitly different observation mechanism.
        # Never label an event-time record as the legacy snapshot profile.
        policy["version"] = "rtl-scheduled-execution-native/6"
        policy["structural_sample"] = "native-whole-cycle-events-with-occurrence-evidence"
        from .source_check import VERSION

        policy["build_failure_attribution"] = VERSION
    return policy


def execution_adapter_version(protocol_version, *, native_events=False, observation=None):
    """Bind the physical phase policy alongside the logical protocol adapter."""

    if type(protocol_version) is not str:
        raise TypeError("an RTL protocol adapter version must be an exact string")
    image = {
        "protocol": protocol_version,
        "execution": execution_policy(native_events=native_events),
    }
    if observation is not None:
        from .observation_binding import ObservationBinding

        if type(observation) is not ObservationBinding or observation.native != native_events:
            raise ValueError("execution and admitted observation differ")
        image["observation"] = observation.as_dict()
    return canonical_json(image)
