"""Frontend-owned clock/reset-domain ownership for represented RTL state."""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

from ..core.refusal import Refusal, Refused
from ..core.stimulus import canonical_json
from ..strict_json import strict_json_loads
from .elaboration import STORAGE_INFERENCE_SCRIPT, compiler_command, elaboration_script

__all__ = [
    "RTLStateField",
    "RTLStateOwnership",
    "read_state_ownership_with_execution",
]


@dataclass(frozen=True, slots=True)
class RTLStateField:
    name: str
    width: int
    domain: str
    clock: str
    edge: str
    reset: str | None
    reset_active_low: bool | None
    observable: bool

    def __post_init__(self):
        if any(type(value) is not str or not value
               for value in (self.name, self.domain, self.clock)):
            raise TypeError("an RTL state field has nonempty exact string names")
        if type(self.width) is not int or isinstance(self.width, bool) \
                or self.width < 1:
            raise TypeError("an RTL state field has a positive exact width")
        if self.edge not in {"rising", "falling"}:
            raise ValueError("an RTL state field edge is rising or falling")
        if self.reset is None:
            if self.reset_active_low is not None:
                raise ValueError("state without a reset has no reset polarity")
        elif type(self.reset) is not str or not self.reset \
                or type(self.reset_active_low) is not bool:
            raise TypeError("a reset-owned state field has an exact reset and polarity")
        if type(self.observable) is not bool:
            raise TypeError("RTL state observability is an exact Boolean")

    def as_dict(self):
        return {
            "name": self.name, "width": self.width, "domain": self.domain,
            "clock": self.clock, "edge": self.edge,
            "reset": self.reset, "reset_active_low": self.reset_active_low,
            "observable": self.observable,
        }


@dataclass(frozen=True, slots=True)
class RTLStateOwnership:
    """A complete state/domain partition with separate observation capability."""

    top: str
    fields: tuple[RTLStateField, ...]

    def __post_init__(self):
        if type(self.top) is not str or not self.top:
            raise TypeError("RTL state ownership needs a nonempty top name")
        if type(self.fields) is not tuple or any(
                type(field) is not RTLStateField for field in self.fields):
            raise TypeError("RTL state ownership fields are exact immutable records")
        names = tuple(field.name for field in self.fields)
        if len(names) != len(set(names)) or names != tuple(sorted(names)):
            raise ValueError("RTL state ownership fields are unique and sorted")

    @classmethod
    def from_frontend(cls, document, contract, *, expected_top):
        """Validate the judge's closed record and bind clock ports to task domains."""

        if type(document) is not dict or set(document) != {
                "version", "top", "fields", "issues"} \
                or document.get("version") != "rtl-state-clock-ownership/1":
            raise TypeError("the RTL state ownership document has an exact schema")
        if document["top"] != expected_top:
            raise Refused(Refusal(
                "rtl-state-clock-ownership-top-mismatch",
                "the ownership frontend and admitted artifact name different tops"))
        issues = document["issues"]
        if type(issues) is not list or any(type(row) is not dict for row in issues):
            raise TypeError("RTL state ownership issues are exact records")
        issue_shapes = {
            "unsupported-state-cell": ({"kind", "cell"}, "cell"),
            "clocked-cell-shape": ({"kind", "cell"}, "cell"),
            "mixed-state-bit": ({"kind", "bit"}, "bit"),
            "duplicate-state-bit": ({"kind", "bit"}, "bit"),
            "generated-clock": ({"kind", "field"}, "field"),
            "ambiguous-clock": ({"kind", "field"}, "field"),
            "generated-reset": ({"kind", "field"}, "field"),
            "ambiguous-reset": ({"kind", "field"}, "field"),
            "reset-semantics-unsupported": ({"kind", "cell"}, "cell"),
            "unnamed-state": ({"kind", "count"}, "count"),
            "overlapping-state-nets": ({"kind", "count"}, "count"),
        }
        for row in issues:
            shape = issue_shapes.get(row.get("kind"))
            if shape is None or set(row) != shape[0]:
                raise TypeError("an RTL state ownership issue has an exact shape")
            value = row[shape[1]]
            if shape[1] in {"bit", "count"}:
                if type(value) is not int or isinstance(value, bool) or value < 1:
                    raise TypeError("an RTL ownership issue has a positive integer")
            elif type(value) is not str or not value:
                raise TypeError("an RTL ownership issue has a nonempty string")
        if issues:
            kind = issues[0].get("kind")
            if type(kind) is not str or not kind:
                raise TypeError("an RTL state ownership issue needs an exact kind")
            raise Refused(Refusal(
                f"rtl-state-clock-ownership-{kind}",
                "the isolated frontend cannot assign every represented state field "
                "to one declared clock domain",
                first=dict(issues[0]), count=len(issues)))
        rows = document["fields"]
        if type(rows) is not list:
            raise TypeError("RTL state ownership fields are an exact list")
        clocks = {clock.clock: clock for clock in contract.clocks}
        if len(clocks) != len(contract.clocks):
            raise Refused(Refusal(
                "rtl-state-clock-ownership-clock-alias",
                "two declared domains use the same physical clock port"))
        fields = []
        seen = set()
        for row in rows:
            if type(row) is not dict or set(row) != {
                    "name", "width", "clock", "edge", "reset",
                    "reset_active_low", "observable"}:
                raise TypeError("an RTL state ownership field has an exact shape")
            name, width = row["name"], row["width"]
            clock_name, edge = row["clock"], row["edge"]
            observable = row["observable"]
            reset = row["reset"]
            reset_active_low = row["reset_active_low"]
            if type(name) is not str or not name or name in seen \
                    or type(width) is not int or isinstance(width, bool) or width < 1 \
                    or type(clock_name) is not str or not clock_name \
                    or edge not in {"rising", "falling"} \
                    or type(observable) is not bool:
                raise TypeError("an RTL state ownership field is not canonical")
            if reset is None:
                if reset_active_low is not None:
                    raise TypeError("state without reset has no reset polarity")
            elif type(reset) is not str or not reset \
                    or type(reset_active_low) is not bool:
                raise TypeError("a state reset and polarity are exact")
            seen.add(name)
            clock = clocks.get(clock_name)
            if clock is None:
                raise Refused(Refusal(
                    "rtl-state-clock-ownership-undeclared-clock",
                    "a represented RTL state field is driven by a clock outside the "
                    "task contract", field=name, clock=clock_name))
            if clock.edge != edge:
                raise Refused(Refusal(
                    "rtl-state-clock-ownership-edge-mismatch",
                    "the ownership frontend and task contract assign different active "
                    "edges to one state field", field=name, clock=clock_name,
                    frontend=edge, contract=clock.edge))
            if reset is not None and (
                    reset != clock.reset
                    or reset_active_low != clock.reset_active_low):
                raise Refused(Refusal(
                    "rtl-state-clock-ownership-reset-mismatch",
                    "the ownership frontend and task contract assign different reset "
                    "semantics to one state field", field=name,
                    frontend_reset=reset, contract_reset=clock.reset,
                    frontend_active_low=reset_active_low,
                    contract_active_low=clock.reset_active_low))
            fields.append(RTLStateField(
                name, width, clock.name, clock_name, edge,
                reset, reset_active_low, observable))
        fields.sort(key=lambda field: field.name)
        return cls(expected_top, tuple(fields)).validate_contract(contract)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"top", "fields"} \
                or type(value["fields"]) is not list:
            raise TypeError("RTL state ownership has an exact wire schema")
        fields = []
        for row in value["fields"]:
            if type(row) is not dict or set(row) != {
                    "name", "width", "domain", "clock", "edge", "reset",
                    "reset_active_low", "observable"}:
                raise TypeError("an RTL state field has an exact wire schema")
            fields.append(RTLStateField(**row))
        got = cls(value["top"], tuple(fields))
        if got.as_dict() != value:
            raise ValueError("RTL state ownership is not canonical")
        return got

    def as_dict(self):
        return {"top": self.top, "fields": [field.as_dict() for field in self.fields]}

    def validate_contract(self, contract):
        """Recheck a serialized ownership record against its task authority."""

        from ..core.contract import TaskContract

        if type(contract) is not TaskContract:
            raise TypeError("RTL state ownership binds the exact TaskContract")
        clocks = {clock.name: clock for clock in contract.clocks}
        physical = [clock.clock for clock in contract.clocks]
        if len(physical) != len(set(physical)):
            raise Refused(Refusal(
                "rtl-state-clock-ownership-clock-alias",
                "two declared domains use the same physical clock port"))
        # Reset restoration is monitored, not assumed at admission. An ordinary
        # clear may hide a direct reset pin; genuinely unreset registers also
        # remain observable so that their restoration violations can be found.
        for field in self.fields:
            clock = clocks.get(field.domain)
            if clock is None:
                raise Refused(Refusal(
                    "rtl-state-clock-ownership-domain-mismatch",
                    "a serialized state field names no declared clock domain",
                    field=field.name, domain=field.domain))
            if (field.clock, field.edge) != (clock.clock, clock.edge) \
                    or (field.reset is not None and (
                        field.reset, field.reset_active_low) != (
                            clock.reset, clock.reset_active_low)):
                raise Refused(Refusal(
                    "rtl-state-clock-ownership-domain-mismatch",
                    "a serialized state field and its declared clock domain carry "
                    "different clock/reset semantics",
                    field=field.name, domain=field.domain))
        # A complete frontend partition may be empty (combinational RTL, or a
        # domain with no storage). This differs from unavailable ownership:
        # unknown/unsupported state is rejected by the frontend issue checks.
        return self

    @property
    def digest(self):
        return hashlib.sha256(canonical_json(self.as_dict()).encode()).hexdigest()

    @property
    def names(self):
        return tuple(field.name for field in self.fields)

    @property
    def observable_names(self):
        return tuple(field.name for field in self.fields if field.observable)

    @property
    def fully_observable(self):
        return all(field.observable for field in self.fields)

    @property
    def widths(self):
        return MappingProxyType({field.name: field.width for field in self.fields})

    def by_domain(self, contract=None):
        """Include declared empty partitions when the task authority is supplied."""
        if contract is not None:
            self.validate_contract(contract)
        result = {} if contract is None else {clock.name: [] for clock in contract.clocks}
        for field in self.fields:
            result.setdefault(field.domain, []).append(field.name)
        return MappingProxyType({name: tuple(fields)
                                 for name, fields in sorted(result.items())})


def read_state_ownership_with_execution(
        rtl_path, top, contract, timeout=defaults.SIM_RTL_CATALOGUE_SECONDS):
    """Derive the state partition through one isolated, normalized Yosys job."""

    from .tool_isolation import default_limits, run_workspace
    from .tool_protocol import (
        OutputNormalizer,
        ToolCommand,
        ToolGroup,
        command_result,
        command_stream,
        require_completed,
    )

    if type(top) is not str or re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_$]*", top) is None:
        raise ValueError("the RTL ownership top is not a SystemVerilog identifier")
    timeout = int(timeout)
    if timeout <= 0:
        raise ValueError("the RTL ownership timeout must be positive")
    with tempfile.TemporaryDirectory(prefix="behave-state-ownership-", dir=scratch_root()) as temporary:
        workspace = Path(temporary)
        (workspace / "design.sv").write_bytes(Path(rtl_path).read_bytes())
        (workspace / "ownership.ys").write_text(
            elaboration_script(top) +
            STORAGE_INFERENCE_SCRIPT +
            f"hierarchy -check -top {top}\n"
            "proc\nflatten\nopt_expr -keepdc\nopt_clean\n"
            # Ownership is later read through original HDL storage handles.
            # `prep` also runs wreduce: e.g. an unused shift-register bit is
            # removed while its net keeps the original source name. That
            # reduced width is not the width of the native handle. Do not
            # optimize storage coordinates before assigning clock ownership.
            "memory_collect\n"
            # ``prep`` leaves a synchronous-reset mux in front of ``$dff``.
            # Ownership is about the elaborated transition, so first ask Yosys to
            # expose that mux as the corresponding reset-bearing state cell.
            # Preserve unknown initial bits: becoming constant after an edge
            # does not make a register constant in the initial-state domain.
            "opt_dff -keepdc\n"
            "write_json ownership.json\n")
        execution = require_completed(run_workspace(
            workspace,
            (ToolGroup((ToolCommand(
                "yosys-state-ownership", compiler_command(top, "ownership.ys"),
                timeout_s=timeout),)),),
            outputs=("ownership.json",),
            output_normalizers=(OutputNormalizer(
                "ownership.json", "rtl-state-clock-ownership/1",
                (("top", top),)),),
            role="yosys-state-ownership",
            runtime_profile="source-events",
            limits=default_limits(wall_seconds=timeout + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS),
        ))
        row = command_result(execution, "yosys-state-ownership")
        if row.get("timed_out") or row.get("returncode") != 0 \
                or "ownership.json" not in execution.outputs:
            diagnostic = command_stream(
                execution, "yosys-state-ownership", "stderr")
            raise RuntimeError(
                "yosys state ownership failed:\n"
                + diagnostic.decode("utf-8", errors="replace"))
        document = strict_json_loads(
            execution.outputs["ownership.json"],
            what="the normalized RTL state ownership record")
        return RTLStateOwnership.from_frontend(
            document, contract, expected_top=top), execution
