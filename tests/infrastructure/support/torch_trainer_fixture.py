"""Real torch CPU numerics and backend SDK fixture; no LLM or RL experiment."""

import json
import pickle
import random
import sys
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import numpy as np
import torch

from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.records import RecordError, canonical_json
from behave.core.storage import DirectoryStore
from behave.train.checkpoints import TrainingState, copy_state_file, store_state_file
from behave.train.trainer import BackendUpdate, TorchRNG, TorchState


class Sampler:
    def __init__(self):
        self.position = 0
        self.generator = torch.Generator().manual_seed(211)

    def state_dict(self):
        return {"position": self.position, "generator": self.generator.get_state()}

    def load_state_dict(self, state):
        self.position = state["position"]
        self.generator.set_state(state["generator"])
        # A load hook may consume global RNG. The bridge must restore rng LAST.
        random.random()
        np.random.random()
        torch.rand(1)


def create(kind="adamw", *, device="cpu"):
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(317)
    random.seed(318)
    np.random.seed(319)
    device = torch.device(device)
    model = torch.nn.Sequential(torch.nn.Linear(2, 3), torch.nn.Dropout(0.35), torch.nn.Linear(3, 1)).double().to(device)
    if kind == "adamw":
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.03, weight_decay=0.01)
    else:
        optimizer = torch.optim.SGD(model.parameters(), lr=0.03, momentum=0.8)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=2, gamma=0.7)
    components = {
        "rng": TorchRNG(numpy=True, cuda_devices=(device.index,) if device.type == "cuda" else ()),
        "model": model,
        "scheduler": scheduler,
        "optimizer": optimizer,
        "sampler": Sampler(),
    }
    return components, TorchState(components)


def advance(components, rows):
    model, optimizer, sampler = (components[key] for key in ("model", "optimizer", "sampler"))
    example = rows[sampler.position % len(rows)]
    tokens = example["tokens"]
    ids = [value for value, mask in zip(tokens["response_ids"], tokens["response_mask"], strict=True) if mask]
    values = torch.tensor(
        [[sum(ids) / len(ids) / 100, torch.rand((), generator=sampler.generator).item()]], dtype=torch.float64,
        device=next(model.parameters()).device,
    )
    target = example["reward"] + (random.random() + np.random.random() - 1) / 10
    optimizer.zero_grad(set_to_none=True)
    loss = (model(values).squeeze() - target).square()
    loss.backward()
    optimizer.step()
    components["scheduler"].step()
    optimizer.zero_grad(set_to_none=True)
    sampler.position += 1
    return loss.item()


ROWS = [{"reward": 0, "tokens": {"response_ids": [10, 99999, 12], "response_mask": [1, 0, 1]}}]


def equal(a, b):
    if isinstance(a, torch.Tensor):
        assert isinstance(b, torch.Tensor) and a.dtype == b.dtype and torch.equal(a, b)
    elif isinstance(a, dict):
        assert a.keys() == b.keys()
        for key in a:
            equal(a[key], b[key])
    elif isinstance(a, (tuple, list)):
        assert type(a) is type(b) and len(a) == len(b)
        for left, right in zip(a, b, strict=True):
            equal(left, right)
    else:
        assert a == b


def decoded(reader, state):
    parts = {}
    for file in state.files:
        stream = BytesIO()
        copy_state_file(reader, file, stream)
        stream.seek(0)
        parts[file.component] = torch.load(stream, map_location="cpu", weights_only=True)["state"]
    return parts


def check(root, kind):
    store = DirectoryStore(root)
    components, bridge = create(kind)
    reference_loss = [advance(components, ROWS) for _ in range(7)]
    expected = bridge.save(store, cursor={"step": 7})
    components, bridge = create(kind)
    resumed_loss = [advance(components, ROWS) for _ in range(3)]
    saved = bridge.save(store, cursor={"step": 3})
    components, bridge = create(kind)
    bridge.restore(store, saved)
    resumed_loss.extend(advance(components, ROWS) for _ in range(4))
    actual = bridge.save(store, cursor={"step": 7})
    assert reference_loss == resumed_loss and len(set(reference_loss)) > 1
    equal(decoded(store, expected), decoded(store, actual))
    # Restoring weights alone is not sufficient: optimizer/RNG/cursor controls
    # must each independently change the resumed numerics.
    controls = []
    for omitted in ("optimizer", "scheduler", "rng", "sampler"):
        parts = decoded(store, saved)
        components, bridge = create(kind)
        for name in bridge._order:
            if name != omitted:
                components[name].load_state_dict(parts[name])
        losses = [advance(components, ROWS) for _ in range(4)]
        assert losses != reference_loss[3:], omitted
        controls.append(omitted)

    failures = []
    # All these must fail before any native component's load hook is called.
    model_file = next(f for f in saved.files if f.component == "model")
    bad_chunk = replace(model_file, content=BlobRef("0" * 64, model_file.content.size_bytes))
    variants = {
        "missing": replace(saved, files=tuple(f for f in saved.files if f.component != "sampler")),
        "policy": replace(saved, policy=BlobRef.of(b"not-model")),
        "hash": replace(
            saved,
            policy=bad_chunk.content,
            files=tuple(bad_chunk if f.component == "model" else f for f in saved.files),
        ),
    }
    stream = BytesIO()
    copy_state_file(store, model_file, stream)
    for attack in ("version", "order", "unsafe_object"):
        stream.seek(0)
        row = torch.load(stream, map_location="cpu", weights_only=True)
        if attack == "version":
            row["torch"] = "unapproved-version"
        elif attack == "order":
            row["layout"] = list(reversed(row["layout"]))
        else:
            row["state"] = torch.nn.Linear(1, 1)
        data = BytesIO()
        torch.save(row, data)
        data.seek(0)
        bad = store_state_file(data, store, component="model", path=model_file.path)
        variants[attack] = replace(
            saved, policy=bad.content, files=tuple(bad if f.component == "model" else f for f in saved.files)
        )
    for attack, bad in variants.items():
        components, bridge = create(kind)
        # state_dict tensors alias live parameters; freeze the comparison oracle.
        before = deepcopy({name: obj.state_dict() for name, obj in components.items()})
        try:
            bridge.restore(store, bad)
        except (RecordError, pickle.UnpicklingError):
            failures.append(attack)
        else:
            raise AssertionError("native restore accepted " + attack)
        equal(before, {name: obj.state_dict() for name, obj in components.items()})
    try:
        TorchRNG(numpy=False).load_state_dict(TorchRNG(numpy=True).state_dict())
    except RecordError:
        failures.append("rng_domains")
    else:
        raise AssertionError("RNG domain change was accepted")
    assert not torch.cuda.is_initialized()
    return {
        "torch": str(torch.__version__),
        "optimizer": kind,
        "losses": reference_loss,
        "negative_controls": controls,
        "refusals": failures,
        "cuda_initialized": False,
    }


def save_with_export(components, bridge, objects, *, export):
    """Test backend composition: native resume plus optional inference tensors."""
    state = bridge.save(objects, cursor={"step": components["sampler"].position})
    if not export:
        return state
    from safetensors.torch import save

    payloads = {
        "config.json": canonical_json({"architecture": "fixture-mlp", "dtype": "float64"}),
        "model.safetensors": save({k: v.contiguous() for k, v in components["model"].state_dict().items()}),
    }
    exported = []
    for name, data in payloads.items():
        file = store_state_file(BytesIO(data), objects, component="serving", path=name, chunk_bytes=64)
        exported.append(file)
    return replace(state, files=(*state.files, *exported))


def main():
    if len(sys.argv) == 1:
        update = BackendUpdate.read()
        config = json.loads(update.configuration)
        components, bridge = create(config["optimizer"])
        native = update.state
        if config.get("export_serving", False):
            # Explicit adapter-owned projection, not a weakened native restore check.
            native = replace(native, files=tuple(f for f in native.files if f.component in bridge.components))
        bridge.restore(update.inputs, native)
        advance(components, list(update.iter_examples()))
        update.finish(
            save_with_export(components, bridge, update.output, export=config.get("export_serving", False))
        )
        return
    command, root = sys.argv[1:3]
    if command == "check":
        result = check(root, sys.argv[3])
    elif command in {"initial", "initial-export", "baseline"}:
        components, bridge = create()
        count = 4 if command == "baseline" else 0
        for _ in range(count):
            advance(components, ROWS)
        state = save_with_export(components, bridge, DirectoryStore(root), export=command == "initial-export")
        sys.stdout.buffer.write(state.to_bytes())
        return
    elif command == "check-weights":
        from safetensors.torch import load_file

        state = TrainingState.from_bytes(Path(sys.argv[3]).read_bytes())
        components, bridge = create()
        native = replace(state, files=tuple(f for f in state.files if f.component in bridge.components))
        bridge.restore(DirectoryReader(root), native)
        expected = components["model"].eval()
        other, _ = create()
        actual = other["model"].eval()
        actual.load_state_dict(load_file(str(Path(sys.argv[4]) / "model.safetensors")), strict=True)
        equal(expected.state_dict(), actual.state_dict())
        values = torch.tensor([[.1, .2], [-.9, .7]], dtype=torch.float64)
        assert torch.equal(expected(values), actual(values))
        assert not torch.cuda.is_initialized()
        result = {"torch": str(torch.__version__), "equal": True, "step": state.cursor["step"],
                  "prediction": actual(values).tolist(), "cuda_initialized": False}
    elif command == "compare":
        left, right = (TrainingState.from_bytes(Path(path).read_bytes()) for path in sys.argv[3:5])
        equal(decoded(DirectoryReader(root), left), decoded(DirectoryReader(root), right))
        result = {"equal": True, "torch": str(torch.__version__), "steps": right.cursor["step"]}
    else:
        raise ValueError(command)
    sys.stdout.buffer.write(canonical_json(result))


if __name__ == "__main__":
    main()
