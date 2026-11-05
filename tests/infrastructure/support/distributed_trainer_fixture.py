"""Small deterministic two-rank SGD exercise of the generic backend protocol."""

import sys
import time
from io import BytesIO

import torch
import torch.distributed as dist

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.train.checkpoints import TrainingState, copy_state_file, store_state_file
from behave.train.trainer import BackendUpdate

update = BackendUpdate.read()
torch.set_num_threads(1)
dist.init_process_group("gloo")
assert (dist.get_rank(), dist.get_world_size()) == (update.rank, update.world_size)
if update.rank == 1:
    if sys.argv[1] in {"fail", "malformed", "hang"}:
        print("fixture-rank-failure:" + sys.argv[1], file=sys.stderr, flush=True)
    if sys.argv[1] == "fail":
        raise SystemExit(7)
    if sys.argv[1] == "malformed":
        print("not a checkpoint")
        dist.destroy_process_group()
        raise SystemExit(0)
    if sys.argv[1] == "hang":
        time.sleep(600)
parts = {}
for file in update.state.files:
    stream = BytesIO()
    copy_state_file(update.inputs, file, stream)
    parts[file.component] = parse_json(stream.getvalue())
# Keep selection/group identities unchanged: the backend, not the runtime,
# chooses which examples and which collective reduction implement its update.
examples = list(update.iter_examples())
gradient = torch.tensor([update.rank + 1 + examples[0]["reward"]], dtype=torch.float64)
dist.all_reduce(gradient)
gradient /= update.world_size
parts["optimizer"]["velocity"] = 0.8 * parts["optimizer"]["velocity"] + gradient.item()
parts["optimizer"]["steps"] += 1
parts["model"]["weight"] -= 0.05 * parts["optimizer"]["velocity"]
parts["sampler"]["position"] += 1
parts["rng"]["value"] += update.rank + 1
files = tuple(store_state_file(BytesIO(canonical_json(value)), update.output,
    component=name, path=f"{name}.json") for name, value in parts.items())
dist.destroy_process_group()
update.finish(TrainingState(BlobRef.of(canonical_json(parts["model"])), files,
                           {"step": parts["optimizer"]["steps"]}))
