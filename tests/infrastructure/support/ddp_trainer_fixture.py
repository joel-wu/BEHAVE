"""Actual DDP/autograd/AdamW on an explicit CPU or GPU fixture allocation."""

import os
import sys
from datetime import timedelta
from pathlib import Path

import torch
import torch.distributed as dist
from torch_trainer_fixture import advance, create, decoded, equal

from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.train.checkpoints import TrainingState
from behave.train.cluster_state import pack_rank_states, unpack_rank_states
from behave.train.trainer import BackendUpdate


def device_for(rank):
    if os.environ.get("BEHAVE_FIXTURE_CUDA") != "1":
        return torch.device("cpu")
    assert torch.cuda.is_available() and torch.cuda.device_count() == 2, "requires exactly two allocated GPUs"
    torch.cuda.set_device(rank)
    return torch.device("cuda", rank)


def main():
    if len(sys.argv) == 1:
        update = BackendUpdate.read()
        torch.set_num_threads(1)
        device = device_for(int(os.environ["LOCAL_RANK"]))
        dist.init_process_group("nccl" if device.type == "cuda" else "gloo", timeout=timedelta(seconds=30))
        try:
            assert (dist.get_rank(), dist.get_world_size()) == (update.rank, update.world_size)
            components, bridge = create(device=device)
            # Native construction precedes RNG restoration; serialization keeps
            # the underlying model, not the transport-specific DDP wrapper.
            parallel = torch.nn.parallel.DistributedDataParallel(
                components["model"], device_ids=[device.index] if device.type == "cuda" else None)
            bridge.restore(update.inputs, update.state)
            components["model"] = parallel
            advance(components, list(update.iter_examples()))
            if os.environ.get("BEHAVE_FIXTURE_FAIL_RANK") == str(update.rank):
                print("fixture-nccl-rank-failed-after-update", file=sys.stderr, flush=True)
                raise RuntimeError("intentional failure after the collective optimizer update")
            state = bridge.save(update.output, cursor={"step": components["sampler"].position})
        finally:
            dist.destroy_process_group()
        update.finish(state)
    elif sys.argv[1] == "initial":
        store = DirectoryStore(sys.argv[2])
        states = []
        for rank in range(2):
            components, bridge = create(device=device_for(rank))
            torch.manual_seed(901 + rank)
            components["sampler"].generator.manual_seed(801 + rank)
            states.append(bridge.save(store, cursor={"step": 0}))
        sys.stdout.buffer.write(pack_rank_states(tuple(states)).to_bytes())
    elif sys.argv[1] == "compare":
        store = DirectoryStore(sys.argv[2])
        left, right = (unpack_rank_states(TrainingState.from_bytes(Path(name).read_bytes()), 2)
                       for name in sys.argv[3:])
        for before, after in zip(left, right, strict=True):
            equal(decoded(store, before), decoded(store, after))
            assert after.cursor["step"] == 2
        replicas = [decoded(store, state) for state in right]
        equal(replicas[0]["model"], replicas[1]["model"])
        equal(replicas[0]["optimizer"], replicas[1]["optimizer"])
        assert not torch.equal(replicas[0]["rng"]["cpu"], replicas[1]["rng"]["cpu"])
        evidence = {"equal": True, "steps": 2, "ranks": 2}
        if os.environ.get("BEHAVE_FIXTURE_CUDA") == "1":
            # The saved per-rank CUDA generators must exist, differ and advance.
            for rank in range(2):
                assert tuple(replicas[rank]["rng"]["cuda"]) == (rank,)
                components, _ = create(device=device_for(rank))
                assert not torch.equal(replicas[rank]["rng"]["cuda"][rank],
                                       components["rng"].state_dict()["cuda"][rank])
            assert not torch.equal(replicas[0]["rng"]["cuda"][0], replicas[1]["rng"]["cuda"][1])
            evidence.update(backend="nccl", torch=str(torch.__version__), cuda=torch.version.cuda,
                            nccl=list(torch.cuda.nccl.version()),
                            devices=[{"name": torch.cuda.get_device_name(rank),
                                      "uuid": str(torch.cuda.get_device_properties(rank).uuid)} for rank in range(2)])
        print(canonical_json(evidence).decode())
    else:
        raise ValueError("unknown fixture operation")


if __name__ == "__main__":
    main()
