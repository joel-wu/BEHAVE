"""Tiny momentum optimizer for process/recovery tests; not an RL implementation.

Its explicit JSON RNG and sampler state allow bit-for-bit cold continuation.
The test invokes this executable through the same SDK as a native trainer.
"""

import json
import sys
import time
from io import BytesIO

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.train.checkpoints import TrainingState, copy_state_file, store_state_file
from behave.train.trainer import BackendUpdate, response_bytes


def main():
    update = BackendUpdate.read()
    config = json.loads(update.configuration)
    assert update.decision
    mode = config.get("mode", "normal")
    if mode == "crash":
        print("fixture backend failed", file=sys.stderr, flush=True)
        return 7
    if mode == "sleep":
        time.sleep(60)
    if mode == "verbose":
        sys.stderr.write("x" * (128 << 10))
        sys.stderr.flush()
    if mode == "invalid_response":
        print("not a checkpoint")
        return 0
    parts = {}
    for file in update.state.files:
        stream = BytesIO()
        copy_state_file(update.inputs, file, stream)
        parts[file.component] = json.loads(stream.getvalue())
    weights, optimizer = parts["model"], parts["optimizer"]
    rng, sampler = parts["rng"], parts["sampler"]
    selected = list(update.iter_examples())
    # Cyclic sample selection and stochastic target perturbation both depend
    # on checkpoint state, not process-global seeding at each restart.
    example = selected[sampler["position"] % len(selected)]
    tokens = example["tokens"]
    active = [token for token, mask in zip(tokens["response_ids"], tokens["response_mask"], strict=True) if mask]
    x = sum(active) / (100 * len(active))
    rng["value"] = (1664525 * rng["value"] + 1013904223) & 0xFFFFFFFF
    target = example["reward"] + (rng["value"] / 2**32 - 0.5) * 0.01
    gradient = 2 * (weights["weight"] * x - target) * x
    optimizer["velocity"] = 0.9 * optimizer["velocity"] + gradient
    weights["weight"] -= config["learning_rate"] * optimizer["velocity"]
    optimizer["steps"] += 1
    sampler["position"] += 1
    files = tuple(
        store_state_file(
            BytesIO(canonical_json(value)), update.output, component=component, path=f"{component}.json", chunk_bytes=11
        )
        for component, value in parts.items()
    )
    state = TrainingState(BlobRef.of(canonical_json(weights)), files, {"step": optimizer["steps"]})
    if mode == "missing_component":
        state = TrainingState(state.policy, files[:-1], state.cursor)
    if mode == "missing_chunk":
        (update.output.directory / files[0].chunks[0].sha256).unlink()
    if mode in {"missing_component", "missing_chunk", "wrong_request", "empty_state"}:
        ref = BlobRef.of(b"wrong") if mode == "wrong_request" else BlobRef.of(update.request.to_bytes())
        response = json.loads(response_bytes(ref, state))
        if mode == "empty_state":
            response["state"]["files"] = []
            print("fixture returned empty state", file=sys.stderr, flush=True)
        sys.stdout.buffer.write(canonical_json(response))
    else:
        update.finish(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
