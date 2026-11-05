"""External-memory interface convention.

A design under test that uses an external memory named ``<name>`` exposes it as
a request/response port pair:

    outputs (design to harness)
        <name>_req_valid   1 bit
        <name>_req_we      1 bit, asserted for a write
        <name>_req_addr    A bits
        <name>_req_wdata   W bits

    inputs (harness to design)
        <name>_req_ready   1 bit
        <name>_resp_valid  1 bit for one slot after the run-declared read latency
        <name>_resp_rdata  W bits

A single-port memory uses the prefix ``<name>_`` directly. A multi-port memory
uses ``<name>__<portid>_``, where the double underscore separates the memory
name from the port identifier and distinguishes a multi-port memory from a
single-port memory whose name contains an underscore.

The versioned protocol and physical signal set live in
``behave.sim.core.memory``.  This module is the compatibility parser for legacy
port-named task specifications and candidate frontends; it delegates all signal
construction to that authority.
"""

from __future__ import annotations

from .core.memory import (
    memory_port_prefix as mem_port_prefix,
)
from .core.memory import (
    memory_port_signal_set as port_signal_set,
)

__all__ = [
    "detect_memory_interfaces",
    "mem_port_prefix",
    "memory_port_names",
    "port_signal_set",
]


def detect_memory_interfaces(inputs_dict: dict, outputs_dict: dict, *,
                             strict: bool = False) -> list[dict]:
    """Detect single-port and multi-port memory interfaces.

    Each port's type follows from the signals that are present:

    * ``_resp_valid`` and ``_resp_rdata`` without ``_req_wdata`` is read-only
    * ``_req_wdata`` without the response signals is write-only, and ``_req_we``
      is optional
    * both sets is read/write, which does require ``_req_we``

    Returns one descriptor per memory::

        {"name": ..., "addr_width": A, "data_width": W, "depth": 2 ** A,
         "readonly": <every port is read-only>,
         "ports": [{"id": <portid or None>, "type": "r" | "w" | "rw"}, ...]}

    All ports of one memory must agree on both widths; a memory whose ports
    disagree is rejected, as is a port whose signal set matches none of the
    three shapes.
    """
    from .core.refusal import Refusal, Refused

    def malformed(prefix, why, **detail):
        if strict:
            raise Refused(Refusal(
                "memory-interface-malformed",
                why,
                prefix=prefix, **detail))

    # Inspect every reserved sideband suffix, not only ``req_valid``.  Otherwise a
    # malformed near-memory interface can disappear from the detector simply by
    # omitting that one signal and later be mistaken for ordinary transaction data.
    artifact_suffixes = ("_req_valid", "_req_addr", "_req_wdata", "_req_we")
    environment_suffixes = ("_req_ready", "_resp_valid", "_resp_rdata")
    all_suffixes = artifact_suffixes + environment_suffixes
    candidates = {}
    for owner, ports in (("environment", inputs_dict),
                         ("artifact", outputs_dict)):
        for signal in ports:
            suffix = next((item for item in all_suffixes
                           if signal.endswith(item)), None)
            if suffix is None:
                continue
            base = signal[:-len(suffix)]
            candidates.setdefault(base, []).append((owner, suffix, signal))

    by_memname: dict[str, list[dict]] = {}
    for base in sorted(candidates):
        seen = candidates[base]
        if "__" in base:
            memname, portid = base.rsplit("__", 1)
        else:
            memname, portid = base, None
        if not memname or portid == "":
            malformed(
                base, "an external-memory prefix names a nonempty memory and, for "
                "a multi-port interface, a nonempty port id")
            continue
        pref = mem_port_prefix(memname, portid)

        wrong_owner = sorted(signal for owner, suffix, signal in seen
                             if ((suffix in artifact_suffixes)
                                 != (owner == "artifact")))
        if wrong_owner:
            malformed(
                pref, "external-memory request pins are artifact-owned and response/"
                "ready pins are environment-owned",
                signals=wrong_owner)
            continue

        if outputs_dict.get(f"{pref}_req_valid") != 1:
            malformed(
                pref, "an external-memory request-valid signal is exactly one bit",
                width=outputs_dict.get(f"{pref}_req_valid"))
            continue
        if f"{pref}_req_addr" not in outputs_dict:
            malformed(
                pref, "an external-memory request names an artifact-driven address")
            continue
        if inputs_dict.get(f"{pref}_req_ready") != 1:
            malformed(
                pref, "an external-memory request has a one-bit environment-owned "
                "ready signal",
                width=inputs_dict.get(f"{pref}_req_ready"))
            continue

        response_present = (
            f"{pref}_resp_valid" in inputs_dict,
            f"{pref}_resp_rdata" in inputs_dict,
        )
        if response_present[0] != response_present[1]:
            malformed(
                pref, "an external-memory read port has both response-valid and "
                "response-data signals")
            continue
        has_read = all(response_present)
        has_write = f"{pref}_req_wdata" in outputs_dict

        if f"{pref}_req_we" in outputs_dict and outputs_dict.get(
                f"{pref}_req_we") != 1:
            malformed(
                pref, "an external-memory write-enable signal is exactly one bit",
                width=outputs_dict.get(f"{pref}_req_we"))
            continue

        if has_read and has_write:
            ptype = "rw"
            if outputs_dict.get(f"{pref}_req_we") != 1:
                malformed(
                    pref, "a read/write memory port has a one-bit write enable",
                    width=outputs_dict.get(f"{pref}_req_we"))
                continue
        elif has_read:
            ptype = "r"
            if f"{pref}_req_we" in outputs_dict:
                malformed(
                    pref, "a read-only memory port does not expose a write enable")
                continue
        elif has_write:
            ptype = "w"
        else:
            malformed(
                pref, "an external-memory port has a complete read response, a write "
                "data signal, or both")
            continue

        if ptype in ("r", "rw") and inputs_dict.get(f"{pref}_resp_valid") != 1:
            malformed(
                pref, "an external-memory response-valid signal is exactly one bit",
                width=inputs_dict.get(f"{pref}_resp_valid"))
            continue

        addr_w = outputs_dict[f"{pref}_req_addr"]
        if type(addr_w) is not int or addr_w < 1:
            malformed(
                pref, "an external-memory address width is a positive exact integer",
                width=addr_w)
            continue
        data_w = None
        if ptype in ("r", "rw"):
            data_w = inputs_dict[f"{pref}_resp_rdata"]
        if ptype in ("w", "rw"):
            wdata_w = outputs_dict[f"{pref}_req_wdata"]
            if data_w is None:
                data_w = wdata_w
            elif data_w != wdata_w:
                malformed(
                    pref, "read and write data of one memory port have one declared "
                    "width",
                    read_width=data_w, write_width=wdata_w)
                continue
        if type(data_w) is not int or data_w < 1:
            malformed(
                pref, "an external-memory data width is a positive exact integer",
                width=data_w)
            continue

        by_memname.setdefault(memname, []).append({
            "id": portid, "type": ptype,
            "addr_width": addr_w, "data_width": data_w,
        })

    mems = []
    for memname, ports in by_memname.items():
        addr_ws = {p["addr_width"] for p in ports}
        data_ws = {p["data_width"] for p in ports}
        if len(addr_ws) > 1 or len(data_ws) > 1:
            malformed(
                memname, "all ports of one external memory have identical address and "
                "data widths",
                address_widths=sorted(addr_ws), data_widths=sorted(data_ws))
            continue
        # Deterministic order: the single-port entry first, then by identifier.
        ports = sorted(ports, key=lambda p: (p["id"] is not None, p["id"] or ""))
        mems.append({
            "name":       memname,
            "addr_width": next(iter(addr_ws)),
            "data_width": next(iter(data_ws)),
            "depth":      1 << next(iter(addr_ws)),
            "readonly":   all(p["type"] == "r" for p in ports),
            "ports":      [{"id": p["id"], "type": p["type"]} for p in ports],
        })

    return sorted(mems, key=lambda m: m["name"])


def memory_port_names(mems: list[dict]) -> set[str]:
    """Every signal name owned by any port of any detected memory."""
    names: set[str] = set()
    for m in mems:
        for p in m["ports"]:
            names |= port_signal_set(m["name"], p["id"], p["type"])
    return names
