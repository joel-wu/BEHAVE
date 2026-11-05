"""Portable PPA study/campaign plans, shared by preparation and launchers."""

import random
import re
import struct
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from behave.core.file_input import read_regular_file
from behave.core.records import canonical_json, fields, parse_json, version
from behave.core.storage import install_exclusive

from .config import Experiment, Workload


def read_study(path):
    return validate_study(parse_json(read_regular_file(path)))


def read_methods(path):
    """Resolve operator paths relative to this recipe, not the invocation cwd.

    Absolute historical paths keep their spelling. Frozen campaign plans still
    require absolute paths; this does not relocate or reinterpret a saved plan.
    Provider/prompt contents and credentials are not read during preparation.
    """
    path = Path(path).absolute()
    methods = fields(parse_json(read_regular_file(path)), {"methods"})["methods"]
    _validate_methods(methods, absolute_paths=False)
    result = deepcopy(methods)
    for method in result:
        for key in ("provider", "prompt"):
            if not Path(method[key]).is_absolute():
                method[key] = str((path.parent / method[key]).resolve())
    return result


def validate_study(row):
    fields(row, {"schema", "task", "workloads", "measurement", "search", "checks"}
           | (set(row) & {"final_checks", "final_service_seconds"}))
    version(row["schema"], "behave.ppa-study/1")
    if not isinstance(row["task"], str) or not row["task"] or not row["workloads"]:
        raise ValueError("study requires a task and workload recipes")
    search = fields(row["search"], {"replicates", "parallelism", "turns", "request_seconds",
                                     "replicate_seconds", "final_seconds"})
    for name, value in search.items():
        if name in {"request_seconds", "replicate_seconds", "final_seconds"} and value is None:
            continue
        if type(value) is not int or value < 1:
            raise ValueError(f"invalid search setting: {name}")
    for phase in ("checks", *(('final_checks',) if 'final_checks' in row else ())):
        for name, value in fields(row[phase], {"baseline_cases", "sequence_length"}).items():
            if type(value) is not int or value < 1:
                raise ValueError(f"invalid {phase} setting: {name}")
    if 'final_service_seconds' in row:
        seconds = row['final_service_seconds']
        if type(seconds) is not int or seconds < 1:
            raise ValueError('final_service_seconds must be a positive integer')
    return deepcopy(row)


def validate_campaign(plan):
    required = {"schema", "task", "objects", "release", "experiment", "liberty", "sta",
                "retained_pool", "methods", "replicates", "parallelism", "turns",
                "request_seconds", "replicate_seconds", "final_seconds"}
    if (not required <= set(plan)
            or set(plan) - required - {"cpus", "cpu_workers", "judge_policy", "final_judge_policy", "final_experiment"}
            or plan["schema"] != "behave.ppa-campaign/2"):
        raise ValueError("invalid campaign schema")
    for key in ("replicates", "parallelism", "turns"):
        if type(plan[key]) is not int or plan[key] < 1:
            raise ValueError(f"invalid campaign {key}")
    for key in ("request_seconds", "replicate_seconds", "final_seconds", "cpus", "cpu_workers"):
        if plan.get(key) is not None and (type(plan[key]) is not int or plan[key] < 1):
            raise ValueError(f"invalid campaign {key}")
    _validate_methods(plan["methods"])
    paths = [plan[k] for k in ("objects", "release", "experiment", "liberty", "sta", "retained_pool")]
    paths += [plan[k] for k in ("judge_policy", "final_judge_policy", "final_experiment") if plan.get(k)]
    for path in paths:
        if not Path(path).is_absolute():
            raise ValueError("campaign inputs must have explicit absolute paths")
    return plan


def _validate_methods(methods, *, absolute_paths=True):
    if not isinstance(methods, (list, tuple)) or not methods:
        raise ValueError("campaign requires uniquely named methods")
    names = set()
    for method in methods:
        required = {"name", "provider", "prompt", "behavior_source"}
        if (not isinstance(method, dict) or not required <= set(method)
                or set(method) - required - {"sim_feedback"}
                or type(method["name"]) is not str
                or not re.fullmatch(r"[a-z][a-z0-9_-]*", method["name"])):
            raise ValueError("invalid method configuration")
        if method["name"] in names:
            raise ValueError("campaign requires uniquely named methods")
        names.add(method["name"])
        if type(method.get("sim_feedback", True)) is not bool:
            raise ValueError("sim_feedback must be boolean")
        if method["behavior_source"] not in ("agent", "provided"):
            raise ValueError("invalid Behavior source")
        for key in ("provider", "prompt"):
            value = method[key]
            if type(value) is not str or not value or any(ord(c) < 32 for c in value):
                raise ValueError("provider and prompt paths must be nonempty text without control characters")
            if absolute_paths and not Path(value).is_absolute():
                raise ValueError("provider and prompt paths must be absolute")


def validate_campaign_group(group):
    required = {"schema", "plans", "cpu_workers"}
    if (not required <= set(group)
            or set(group) - required - {"final_only", "remeasure_final", "remeasure_tasks", "refresh_native"}
            or group["schema"] != "behave.ppa-campaign-group/1"
            or type(group["cpu_workers"]) is not int or group["cpu_workers"] < 1
            or type(group["plans"]) is not dict or not group["plans"]):
        raise ValueError("invalid campaign group")
    if any(type(group.get(key, False)) is not bool for key in ("final_only", "remeasure_final", "refresh_native")):
        raise ValueError("final-only and remeasurement controls must be boolean")
    remeasure = group.get("remeasure_tasks", [])
    if (type(remeasure) is not list or any(type(name) is not str for name in remeasure)
            or len(set(remeasure)) != len(remeasure) or set(remeasure) - set(group["plans"])):
        raise ValueError("remeasure_tasks must name distinct tasks in this group")
    if (group.get("remeasure_final") or remeasure or group.get("refresh_native")) and not group.get("final_only"):
        raise ValueError("explicit final remeasurement requires a final-only campaign")
    for name, path in group["plans"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name) or not Path(path).is_absolute():
            raise ValueError("group plans require safe names and absolute paths")
    return group


def campaign_arguments(plan, *, final=False):
    keys = ("objects", "release", "task", "experiment", "liberty", "sta")
    result = [arg for key in keys for arg in ("--" + key.replace("_", "-"), plan[key])]
    if final and plan.get("final_experiment"):
        result[result.index("--experiment") + 1] = plan["final_experiment"]
    policy = plan.get("final_judge_policy") if final else None
    policy = policy or plan.get("judge_policy")
    return result + (["--judge-policy", policy] if policy else [])


def campaign_slots(plan, root):
    # Interleave providers so a concurrency cap does not run one model first.
    return [(method, replica, root / "runs" / method["name"] / f"r{replica:02d}")
            for replica in range(plan["replicates"]) for method in plan["methods"]]


def campaign_wall(plan, batches):
    seconds = (plan["replicate_seconds"], plan["final_seconds"])
    return None if None in seconds else batches * sum(seconds)


def gated_deltanet_rows(tokens, sequence_tokens, *, key_lanes=8, value_lanes=8):
    for value in (tokens, sequence_tokens, key_lanes, value_lanes):
        if type(value) is not int or value < 1:
            raise ValueError("workload dimensions and lengths must be positive integers")

    def bits(value):
        return struct.unpack(">I", struct.pack(">f", value))[0]

    def packed(values):
        return sum(bits(value) << (32 * i) for i, value in enumerate(values))

    return tuple({"in_start": int(index % sequence_tokens == 0),
        "in_q": packed([((index + 3*i) % 11 - 5)/4 for i in range(key_lanes)]),
        "in_k": packed([((2*index + i) % 7 - 3)/4 for i in range(key_lanes)]),
        "in_v": packed([((index + 5*i) % 13 - 6)/2 for i in range(value_lanes)]),
        "in_g": bits((index % 5 - 2)/2), "in_beta": bits(.25*(1 + index % 4)),
        "in_A_log": bits(-1), "in_dt_bias": bits(.5)} for index in range(tokens))


def mxfp4_rows(blocks, seed):
    if type(blocks) is not int or blocks < 1 or type(seed) is not int:
        raise ValueError("workload requires positive blocks and an integer seed")
    rng = random.Random(seed)
    return tuple(tuple((rng.randrange(2) << 15) |
        (max(1, min(254, 127 + rng.randrange(-8, 9))) << 7) |
        rng.randrange(128) for _ in range(32)) for _ in range(blocks))


def nvfp4_rows(blocks, seed, global_scale):
    """Ordinary BF16 blocks, matching the qualified NVFP4 workload prefix."""
    if type(blocks) is not int or blocks < 1 or type(seed) is not int:
        raise ValueError("workload requires positive blocks and an integer seed")
    if type(global_scale) not in (int, float):
        raise ValueError("NVFP4 global scale must be a positive normal binary32 value")
    scale = struct.unpack(">I", struct.pack(">f", global_scale))[0]
    if not 0x00800000 <= scale < 0x7f800000:
        raise ValueError("NVFP4 global scale must be a positive normal binary32 value")
    rng = random.Random(seed)
    return tuple({**{f"in_x{i}": struct.unpack(">I", struct.pack(">f", rng.uniform(-6, 6)))[0] >> 16
                      for i in range(16)}, "in_global_scale": scale} for _ in range(blocks))


def build_experiment(study, contract):
    """Materialize inputs once; search and remeasurement read the same format."""
    from .performance import endpoints

    workloads = []
    for recipe in study["workloads"]:
        kind = recipe["generator"]
        if kind == "explicit":
            fields(recipe, {"name", "generator", "inputs"})
            rows = tuple(tuple(row) for row in recipe["inputs"])
        else:
            if len(contract.inputs()) != 1:
                raise ValueError("PPA workload needs one input endpoint")
            ports = contract.inputs()[0].fields
            if kind == "gated_deltanet":
                fields(recipe, {"name", "generator", "tokens", "sequence_tokens"})
                widths = {f.name: f.type.width for f in ports}
                if widths.get("in_q", 0) % 32 or widths.get("in_v", 0) % 32:
                    raise ValueError("packed FP32 vectors must have whole lanes")
                named = gated_deltanet_rows(recipe["tokens"], recipe["sequence_tokens"],
                    key_lanes=widths.get("in_q", 0)//32, value_lanes=widths.get("in_v", 0)//32)
                if set(named[0]) != {f.name for f in ports}:
                    raise ValueError("Gated DeltaNet input interface differs from the recipe")
                rows = tuple(tuple(row[f.name] for f in ports) for row in named)
            elif kind == "mxfp4":
                fields(recipe, {"name", "generator", "blocks", "seed"})
                if {f.port for f in ports} != {f"in_x{i}" for i in range(32)} or len(ports) != 32:
                    raise ValueError("MXFP4 input interface differs from the recipe")
                rows = tuple(tuple(row[int(f.port[4:])] for f in ports)
                             for row in mxfp4_rows(recipe["blocks"], recipe["seed"]))
            elif kind == "nvfp4":
                fields(recipe, {"name", "generator", "blocks", "seed", "global_scale"})
                named = nvfp4_rows(recipe["blocks"], recipe["seed"], recipe["global_scale"])
                widths = {f.port: f.type.width for f in ports}
                if widths != {**{f"in_x{i}": 16 for i in range(16)}, "in_global_scale": 32}:
                    raise ValueError("NVFP4 input interface differs from the recipe")
                rows = tuple(tuple(row[f.port] for f in ports) for row in named)
            else:
                raise ValueError(f"unknown workload generator: {kind}")
        workloads.append(Workload(recipe["name"], rows, contract.clocks[0].reset_hold_cycles))
    measurement = dict(study["measurement"])
    if "workloads" in measurement or "schema" in measurement:
        raise ValueError("study measurement uses workload recipes, not duplicate frozen inputs")
    schema = "behave.ppa-experiment/10" if "parallelism_lanes" in measurement else "behave.ppa-experiment/9"
    experiment = Experiment.from_dict({"schema": schema, **measurement,
                                      "workloads": [{"name": w.name, "inputs": w.inputs,
                                                     "reset_cycles": w.reset_cycles} for w in workloads]})
    for work in experiment.workloads:
        endpoints(contract, work)
    if experiment.timing.clock != contract.clocks[0].clock:
        raise ValueError("experiment clock differs from task")
    return experiment


def prepare_study(study, *, task, output, objects, release, methods=None, liberty=None, sta=None):
    """Write immutable inputs. No model, tool environment or measurement is started."""
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy

    study = validate_study(study)
    if study["task"] != task.public.task_id:
        raise ValueError("study and published task differ")
    contract = api.TaskContract.from_dict(parse_json(task.judge.contract.data))
    experiment = build_experiment(study, contract)
    policy = replace(JudgePolicy.from_bytes(task.judge.judge_policy),
        command_version=api.COMMAND_VERSION, authority_version=api.RESULT_VERSION,
        **study['checks'], search_seconds=0, analysis=None, max_retries=0,
        score_timeout_seconds=experiment.service_seconds)
    output = Path(output).resolve()
    documents = {"study.json": study, "experiment.json": experiment.as_dict(),
                 "judge-policy.json": parse_json(policy.to_bytes()),
                 "retained.json": {"stimuli": []},
                 "inputs.json": {"release": task.public.release.as_dict(),
                                  "task": task.public.revision.as_dict(),
                                  "input_field_order": [f.name for f in contract.inputs()[0].fields]}}
    if 'final_checks' in study or 'final_service_seconds' in study:
        documents['final-judge-policy.json'] = parse_json(replace(policy,
            **study.get('final_checks', {}),
            score_timeout_seconds=study.get('final_service_seconds', experiment.service_seconds)).to_bytes())
    if 'final_service_seconds' in study:
        documents['final-experiment.json'] = replace(experiment,
            service_seconds=study['final_service_seconds']).as_dict()
    if methods is not None:
        if not methods or liberty is None or sta is None:
            raise ValueError("campaign preparation requires methods, liberty and sta")
        documents["plan.json"] = {"schema": "behave.ppa-campaign/2", "task": study["task"],
            "objects": str(Path(objects).resolve()), "release": str(Path(release).resolve()),
            "experiment": str(output / "experiment.json"), "retained_pool": str(output / "retained.json"),
            "judge_policy": str(output / "judge-policy.json"),
            "liberty": str(Path(liberty).resolve()), "sta": str(Path(sta).resolve()),
            "methods": methods, **study["search"]}
        if 'final-judge-policy.json' in documents:
            documents['plan.json']['final_judge_policy'] = str(output / 'final-judge-policy.json')
        if 'final-experiment.json' in documents:
            documents['plan.json']['final_experiment'] = str(output / 'final-experiment.json')
        validate_campaign(documents['plan.json'])
    # Precheck all existing files before creating any new records.
    encoded = {name: canonical_json(value) for name, value in documents.items()}
    for name, data in encoded.items():
        path = output / name
        if path.exists() and path.read_bytes() != data:
            raise ValueError("prepared inputs changed; use a new output directory")
    for name, data in encoded.items():
        if not install_exclusive(output, name, data) and (output / name).read_bytes() != data:
            raise ValueError("prepared inputs changed concurrently")
    return {"directory": str(output), "experiment": experiment.identity,
            "files": list(documents)}
