"""Bounded cold-load diagnosis through the shared serving and chat interfaces.

These calls are health evidence, not evaluation attempts or accuracy scores.
The original weights and archived solver request are never modified.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import replace
from pathlib import Path

from behave.eval.generation import check_running_session, reply_health
from behave.runtime.records import write_json

__all__ = ["check_running_session", "reply_health", "run"]


def run(config_path):
    from behave.agent.model import ModelRequest
    from behave.agent.providers.chat_worker import exchange
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.core.lifecycle import CleanupState
    from behave.isolation.scratch import ScratchDirectory
    from behave.serving import ServingSession
    from behave.serving.environment import environment, headers
    from behave.serving.spec import ServingSpec

    config = json.loads(Path(config_path).read_text())
    output = Path(config["output"])
    output.mkdir(parents=True, exist_ok=True)
    os.sched_setaffinity(0, config["cpu_ids"])
    clean = True
    with ScratchDirectory(prefix="eval-probe-", parent=Path("/tmp"),
            cleanup=lambda: CleanupState.COMPLETE if clean else CleanupState.UNKNOWN) as scratch:
        spec = ServingSpec.from_dict(config["spec"])
        write_json(output / "status.json", {"state": "staging", "scratch": str(scratch)})
        shutil.copytree(spec.model_path, scratch / "weights")
        spec = replace(spec, model_path=str(scratch / "weights"))
        shared = scratch / "shared"
        env = environment(spec, shared, {"CUDA_VISIBLE_DEVICES": config["devices"], "SLURM_CPUS_PER_TASK": "6"})
        binding = json.loads(Path(config["binding"]).read_text())
        includes = headers(binding["headers"], shared, env, deadline=time.monotonic() + 180)
        service = ServingSession(spec, scratch=scratch / "engine",
            environment=environment(spec, scratch / "engine", {"CUDA_VISIBLE_DEVICES": config["devices"],
                "SLURM_CPUS_PER_TASK": "6"}, includes=includes), records=output / "serving")
        clean = False
        rows = []
        try:
            write_json(output / "status.json", {"state": "starting", "scratch": str(scratch)})
            service.start()
            endpoint = LocalChatEndpoint(f"http://127.0.0.1:{spec.port}/v1/chat/completions",
                spec.served_model, spec.identity, parameters={**config["sampling"], "max_tokens": 256})
            source = ModelRequest.from_dict(json.loads(Path(config["request"]).read_text()))
            for label, messages, tools in (
                    ("simple", ({"role": "user", "content": "What is 2 + 3? Reply briefly."},), ()),
                    ("archived_solver", source.messages, source.tools)):
                request = replace(source, call_id=f"probe-{label}:0:0", turn=0, dispatch=0,
                    messages=messages, tools=tools, timeout_seconds=spec.startup_seconds or 1800,
                    model_policy={"provider": endpoint.identity, "model": endpoint.model})
                write_json(output / f"{label}.request.json", request.as_dict())
                outcome = exchange(endpoint, request)
                (output / f"{label}.response.json").write_bytes(outcome.raw)
                row = {"case": label, **reply_health(outcome.raw), "provider_error": getattr(outcome, "code", None)}
                rows.append(row)
                write_json(output / "status.json", {"state": "probing", "cases": rows})
        finally:
            receipt = service.close()
            clean = receipt["cleanup_complete"]
        write_json(output / "result.json", {"state": "completed", "cases": rows,
            "healthy": len(rows) == 2 and all(row["healthy"] and row["provider_error"] is None for row in rows),
            "cleanup_complete": clean})
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", type=Path)
    args = parser.parse_args()
    raise SystemExit(run(args.configuration))
