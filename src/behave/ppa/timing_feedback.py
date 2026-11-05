"""Small diagnostic projection of OpenSTA's native path reports; never a score."""

import math
import re


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def critical_paths(output, *, capacitance_unit='pF'):
    """Worst reported min/max path, with the largest data-path increments.

    Both archived two-column reports and reports with capacitance/slew/fanout
    fields are supported. Missing or unrecognized diagnostics stay absent.
    Timing acceptance remains the separate OpenSTA slack/check_setup result.
    """
    cap_to_pf = {'pF': 1, 'fF': 0.001}[capacitance_unit]
    worst = {}
    for block in re.split(r"(?m)(?=^Startpoint:)", output):
        metadata = {key: value.strip() for key, value in re.findall(
            r"(?m)^(Startpoint|Endpoint|Path Group|Path Type): (.+)$", block)}
        kind = metadata.get("Path Type")
        slack = re.search(rf"(?m)^\s*({NUMBER})\s+slack \((?:MET|VIOLATED)\)", block)
        if kind not in {"min", "max"} or slack is None or not {"Startpoint", "Endpoint"} <= metadata.keys():
            continue
        value = float(slack[1])
        if not math.isfinite(value):
            continue
        data = block.split("data arrival time", 1)[0]
        increments = []
        for match in re.finditer(
            rf"(?m)^\s*((?:{NUMBER}\s+)+)[\^v]\s+(\S+)\s+\(([^)]+)\)\s*$", data
        ):
            numbers = match[1].split()
            if len(numbers) < 2:
                continue
            delay, arrival = map(float, numbers[-2:])
            if not math.isfinite(delay) or not math.isfinite(arrival):
                continue
            pin = {"pin": match[2], "cell": match[3], "delay_ns": delay, "arrival_ns": arrival}
            if "Fanout" in data and len(numbers) == 5 and numbers[0].isdigit():
                cap, slew = map(float, numbers[1:3])
                if math.isfinite(cap) and math.isfinite(slew):
                    pin.update(fanout=int(numbers[0]), capacitance_pf=cap * cap_to_pf, slew_ns=slew)
            increments.append(pin)
        path = {"path_type": kind, "startpoint": metadata["Startpoint"],
                "endpoint": metadata["Endpoint"], "group": metadata.get("Path Group"),
                "slack_ns": value,
                "largest_increments": sorted(increments, key=lambda row: row["delay_ns"], reverse=True)[:3]}
        loads = [row for row in increments if "fanout" in row]
        if loads:
            path["highest_fanout"] = max(loads, key=lambda row: row["fanout"])
        if kind not in worst or value < worst[kind]["slack_ns"]:
            worst[kind] = path
    return [worst[kind] for kind in ("max", "min") if kind in worst]
