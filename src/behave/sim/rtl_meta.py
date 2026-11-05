"""Lightweight RTL port metadata over one normalized physical interface.

Verifier-side consumers build it from the closed elaborated interface frozen at
admission.  Isolated solver workers may read the ``; yosys-smt2-*`` comment header; no
Z3 term is needed for either construction.  The dual RTL engine builds its full Z3 model
on top of the same metadata inside its worker.

Single-clock (default, backward compatible):
  ``clk``, ``rst`` (or ``reset``), ``in_valid``/``in_ready``/``in_<sig>``,
  ``out_valid``/``out_ready``/``out_<sig>``, ``<mem>_req_*``/``_resp_*``.

Multi-clock (multi-domain) extension:
  Every group prefixed with a domain name ``<D>``:
  ``<D>_clk``, ``<D>_rst`` / ``<D>_rstn`` / ``<D>_reset``,
  ``<D>_in_*``, ``<D>_out_*``, ``<D>_<mem>_req_*``/``_resp_*``.

Single-clock is treated as one anonymous domain (name = ``""``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .memory import detect_memory_interfaces, memory_port_names

_HANDSHAKE_BARE = {"clk", "rst", "reset", "in_valid", "in_ready",
                   "out_valid", "out_ready"}


# ── Multi-domain detection ────────────────────────────────────────────

# Reset name pattern: <prefix>_rst, <prefix>_rstn, <prefix>_reset, plus
# the bare names rst / rstn / reset.  Returns (domain_name, active_low).
# active_low is True iff the name ends in "n" (e.g. rstn, w_rstn).
_RST_SUFFIXES = ("_rstn", "_rst", "_reset")


def _classify_clock(name: str) -> str | None:
    """Return the domain name for a clock signal, or None if not a clock.

    ``clk``        -> ``""`` (anonymous / single-clock)
    ``<D>_clk``    -> ``"<D>"``
    """
    if name == "clk":
        return ""
    if name.endswith("_clk") and len(name) > 4:
        return name[: -len("_clk")]
    return None


def _classify_reset(name: str) -> tuple[str, bool] | None:
    """Return (domain_name, active_low) for a reset signal, or None.

    ``rst`` / ``rstn`` / ``reset`` -> domain ``""``
    ``<D>_rst`` / ``<D>_rstn`` / ``<D>_reset`` -> domain ``"<D>"``
    """
    if name == "rst" or name == "reset":
        return ("", False)
    if name == "rstn":
        return ("", True)
    for suf in _RST_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf):
            dom = name[: -len(suf)]
            active_low = suf == "_rstn"
            return (dom, active_low)
    return None


def _domain_for_signal(name: str, dom_names: list[str]) -> str | None:
    """Return the longest matching domain prefix for ``name``, or None.

    Prefers longer prefixes so e.g. ``r0_in_valid`` binds to ``r0`` not ``r``.
    Anonymous domain ("") matches anything as the fallback.
    """
    matches = [d for d in dom_names
               if d and (name == d or name.startswith(d + "_"))]
    if matches:
        return max(matches, key=len)
    if "" in dom_names:
        return ""
    return None


@dataclass
class Domain:
    """One clock domain's metadata.

    name           "" for the anonymous (single-clock) domain, else the
                   prefix used in port names (``"w"``, ``"r0"``, etc.)
    clk            full clock signal name in the RTL (``"clk"`` or ``"<D>_clk"``)
    rst            full reset signal name (``"rst"``, ``"<D>_rstn"``, ...) or None
    rst_active_low Task-declared polarity, or the legacy ``rstn`` naming convention
    in_handshake   {"valid": "<D>_in_valid", "ready": "<D>_in_ready"} or {} if absent
    out_handshake  same shape for the output side
    data_inputs    sorted list of input data signals on this domain (excl
                   handshake / clk / rst / mem)
    data_outputs   sorted list of output data signals on this domain
    mems           list of memory dicts (same shape as legacy ``RTLMeta.mems``)
                   that belong to this domain (memname starts with ``<D>_``
                   for named domains, or is unprefixed for the anon domain).
    """
    name: str
    clk: str
    rst: str | None
    rst_active_low: bool
    in_handshake: dict = field(default_factory=dict)
    out_handshake: dict = field(default_factory=dict)
    data_inputs: list = field(default_factory=list)
    data_outputs: list = field(default_factory=list)
    mems: list = field(default_factory=list)


class RTLMeta:
    """Port, width, clock-domain and memory metadata for one RTL module.

    Backward-compatible single-clock attributes (still populated even
    when multi-clock):
        mod, inputs, outputs, wires
        rst_name        primary reset name (anonymous domain's reset, else
                        the first named domain's reset)
        mems            ALL memories across all domains (flat list)
        data_inputs     ALL non-handshake non-mem input signals (flat,
                        sorted)
        data_input_widths()  -> [meta.inputs[n] for n in self.data_inputs]

    New multi-domain attributes:
        domains         list[Domain], one per detected clock domain
        is_multi_clock  True iff len(domains) > 1
    """

    def __init__(self, smt2_path):
        text = Path(smt2_path).read_text()
        self.smt2_path = str(smt2_path)
        self.mod = None
        self.inputs: dict[str, int] = {}
        self.outputs: dict[str, int] = {}
        self.wires: dict[str, int] = {}

        for line in text.split("\n"):
            m = re.match(r"; yosys-smt2-module (\S+)", line)
            if m: self.mod = m.group(1)
            m = re.match(r"; yosys-smt2-input (\S+) (\d+)", line)
            if m: self.inputs[m.group(1)] = int(m.group(2))
            m = re.match(r"; yosys-smt2-output (\S+) (\d+)", line)
            if m: self.outputs[m.group(1)] = int(m.group(2))
            m = re.match(r"; yosys-smt2-wire (\S+) (\d+)", line)
            if m: self.wires[m.group(1)] = int(m.group(2))

        self._finish()

    @classmethod
    def from_ports(cls, module, *, inputs, outputs, wires=None):
        """Build metadata from an already-normalized physical interface.

        Admission obtains this interface from the isolated RTL elaborator.  Consumers
        in the verifier use that frozen result instead of reparsing compiler output in
        the parent process.  SMT workers may still use :meth:`__init__`, because parsing
        the solver artifact there is part of the isolated solver job.
        """

        def widths(kind, values):
            if not hasattr(values, "items"):
                raise TypeError(f"{kind} must be a name-to-width mapping")
            result = {}
            for name, width in values.items():
                if type(name) is not str or not name:
                    raise ValueError(f"{kind} contains an invalid port name")
                if type(width) is not int or width < 1:
                    raise ValueError(
                        f"{kind} port {name!r} has invalid width {width!r}")
                result[name] = width
            return result

        if type(module) is not str or not module:
            raise ValueError("module must be a nonempty string")
        self = cls.__new__(cls)
        self.smt2_path = None
        self.mod = module
        self.inputs = widths("inputs", inputs)
        self.outputs = widths("outputs", outputs)
        self.wires = widths("wires", {} if wires is None else wires)
        overlap = sorted(set(self.inputs) & set(self.outputs))
        if overlap:
            raise ValueError(
                f"ports cannot be both inputs and outputs: {overlap}")
        self._finish()
        return self

    def _finish(self):
        self.mems = detect_memory_interfaces(self.inputs, self.outputs)
        self.domains = self._partition_into_domains()
        self.is_multi_clock = len(self.domains) > 1

        # Legacy flat attributes (computed from domains for consistency):
        self.rst_name = self._pick_legacy_rst_name()
        self.data_inputs = sorted(d for dom in self.domains for d in dom.data_inputs)

    # ── Domain partitioning ─────────────────────────────────────────

    def declared_clock_domains(self, clocks) -> tuple[Domain, ...]:
        """Use task clock/reset bindings, not port spelling, on an exact path.

        The compiler state-ownership check separately verifies actual FF edges
        and resets. This adapter only binds the declared one-bit input ports;
        conventional handshake/memory metadata remains available to legacy users.
        """
        from .core.contract import ClockDomain

        clocks = tuple(clocks)
        if not clocks or any(type(clock) is not ClockDomain for clock in clocks):
            raise TypeError("declared RTL clocks are nonempty exact ClockDomain records")
        if len({clock.name for clock in clocks}) != len(clocks) \
                or len({clock.clock for clock in clocks}) != len(clocks):
            raise ValueError("declared RTL clocks have distinct names and ports")
        conventional = {domain.clk: domain for domain in self.domains}
        result = []
        for clock in clocks:
            for pin in (clock.clock, clock.reset):
                if self.inputs.get(pin) != 1:
                    raise ValueError(f"declared RTL clock/reset {pin!r} is not a one-bit input")
            domain = conventional.get(clock.clock, Domain(
                clock.name, clock.clock, clock.reset, clock.reset_active_low))
            result.append(replace(domain, name=clock.name, rst=clock.reset,
                                  rst_active_low=clock.reset_active_low))
        return tuple(result)

    def _partition_into_domains(self) -> list[Domain]:
        # Step 1: collect clock and reset signals.
        clocks: dict[str, str] = {}   # domain_name -> clock signal
        resets: dict[str, tuple[str, bool]] = {}  # domain_name -> (rst sig, active_low)
        for sig in self.inputs:
            dom = _classify_clock(sig)
            if dom is not None:
                if dom in clocks:
                    raise ValueError(f"duplicate clock for domain {dom!r}: "
                                     f"{clocks[dom]} and {sig}")
                clocks[dom] = sig
                continue
            rc = _classify_reset(sig)
            if rc is not None:
                rdom, active_low = rc
                if rdom in resets:
                    raise ValueError(f"duplicate reset for domain {rdom!r}: "
                                     f"{resets[rdom][0]} and {sig}")
                resets[rdom] = (sig, active_low)

        if not clocks:
            # No clock found; fall back to a fictitious anonymous domain so
            # downstream code never crashes on missing domains.  Combinational
            # designs (rare) end up here.
            clocks[""] = ""

        # Step 2: build domain objects.
        dom_names = list(clocks.keys())
        mem_signal_set = memory_port_names(self.mems)

        domains: list[Domain] = []
        for dname in sorted(dom_names):
            clk = clocks[dname]
            rst = None
            rst_active_low = False
            if dname in resets:
                rst, rst_active_low = resets[dname]
            elif "" in resets and dname == "":
                rst, rst_active_low = resets[""]

            in_handshake = {}
            out_handshake = {}
            data_inputs: list[str] = []
            data_outputs: list[str] = []

            # Per-domain handshake names.
            in_valid  = f"{dname}_in_valid"  if dname else "in_valid"
            in_ready  = f"{dname}_in_ready"  if dname else "in_ready"
            out_valid = f"{dname}_out_valid" if dname else "out_valid"
            out_ready = f"{dname}_out_ready" if dname else "out_ready"

            if in_valid in self.inputs and in_ready in self.outputs:
                in_handshake = {"valid": in_valid, "ready": in_ready}
            if out_valid in self.outputs and out_ready in self.inputs:
                out_handshake = {"valid": out_valid, "ready": out_ready}

            # Data signals (anything that belongs to this domain, isn't the
            # clock / reset, isn't a handshake bit, isn't a memory port).
            handshake_local = {clk}
            if rst:
                handshake_local.add(rst)
            for hs in (in_handshake, out_handshake):
                handshake_local.update(hs.values())

            for sig in self.inputs:
                if _domain_for_signal(sig, dom_names) != dname:
                    continue
                if sig in handshake_local:
                    continue
                if sig in mem_signal_set:
                    continue
                # Skip cross-domain control signals from other domains
                # (they'll be picked up in their own iteration).
                data_inputs.append(sig)
            for sig in self.outputs:
                if _domain_for_signal(sig, dom_names) != dname:
                    continue
                if sig in handshake_local:
                    continue
                if sig in mem_signal_set:
                    continue
                data_outputs.append(sig)

            # Memories assigned to this domain (memory name's domain prefix
            # matches; anonymous domain owns memories with no named-domain
            # prefix).
            dom_mems = []
            for m in self.mems:
                m_dom = _domain_for_signal(m["name"], dom_names)
                if m_dom == dname:
                    dom_mems.append(m)

            domains.append(Domain(
                name=dname,
                clk=clk,
                rst=rst,
                rst_active_low=rst_active_low,
                in_handshake=in_handshake,
                out_handshake=out_handshake,
                data_inputs=sorted(data_inputs),
                data_outputs=sorted(data_outputs),
                mems=dom_mems,
            ))
        return domains

    def _pick_legacy_rst_name(self) -> str:
        """Choose a single rst name for legacy callers.

        Prefer the anonymous domain's reset; fall back to the first named
        domain's reset; final fallback ``"rst"`` (matches old default).
        """
        for d in self.domains:
            if d.name == "" and d.rst:
                return d.rst
        for d in self.domains:
            if d.rst:
                return d.rst
        return "reset" if "reset" in self.inputs else "rst"

    def data_input_widths(self):
        return [self.inputs[n] for n in self.data_inputs]

    def domain_by_name(self, name: str) -> Domain | None:
        for d in self.domains:
            if d.name == name:
                return d
        return None
