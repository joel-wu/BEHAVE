"""One fixed-world node, inside JobControl's existing PID-namespace supervisor.

Slurm's exact GPU mask is supplied by the worker, never by the coordinator.
Only verified input/output CAS crosses the shared filesystem. All native reads,
torchrun response files and optimizer scratch stay in this node's configured local root.
"""

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.paths import scratch_root
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.train.trainer import BackendUpdate
from behave.train.cluster import collect_ranks, prepare_ranks


def node_main(path: Path, index: int) -> int:
    with path.open("rb") as stream:
        payload = stream.read()
    original = BackendUpdate.from_envelope(payload)
    cluster = original.process.distributed.cluster
    if cluster is None or not 0 <= index < len(cluster.nodes):
        raise ValueError("node does not belong to this trainer placement")
    local_size = original.world_size // len(cluster.nodes)
    ranks = range(index * local_size, (index + 1) * local_size)
    # JobControl has already verified this mask against the allocated GPU count.
    environment = {**original.process.environment, "CUDA_VISIBLE_DEVICES": os.environ["CUDA_VISIBLE_DEVICES"]}
    with TemporaryDirectory(prefix="behave-trainer-node-", dir=scratch_root(environment)) as scratch:
        root = Path(scratch)
        environment.update(TMPDIR=scratch, BEHAVE_SCRATCH_DIR=scratch)
        inputs, output = DirectoryStore(root / "input"), DirectoryStore(root / "output")
        inputs.publish(original.inputs, (*original.state.references, *original.examples,
                                        original.contract.configuration, original.request.decision))
        update = replace(original, inputs=DirectoryReader(inputs.directory), output=output)
        envelope = prepare_ranks(update, root, ranks)
        # Static rendezvous keeps global rank assignment stable. The unique run
        # identity prevents concurrent runs on one endpoint from sharing keys.
        command = (
            update.process.distributed.python, "-m", "torch.distributed.run",
            f"--nnodes={len(cluster.nodes)}", f"--node-rank={index}",
            f"--nproc-per-node={local_size}", "--max-restarts=0",
            "--rdzv-backend=static", f"--rdzv-id={BlobRef.of(payload).sha256}",
            f"--master-addr={cluster.nodes[0].host}", f"--master-port={cluster.master_port}",
            "--no-python", sys.executable, "-I", "-B", "-m",
            "behave.train.distributed_launch", "rank", str(envelope),
        )
        result = subprocess.run(command, cwd=root, env=environment, stdout=sys.stderr, check=False)
        if result.returncode != 0:
            return result.returncode if result.returncode > 0 else 128 - result.returncode
        after = collect_ranks(update, root, ranks)
        original.output.publish(output, tuple(ref for state in after for ref in state.references))
        data = canonical_json({"schema": "behave.trainer-node-response/1",
                               "request": BlobRef.of(update.request.to_bytes()).as_dict(),
                               "node": index, "states": [parse_json(state.to_bytes()) for state in after]})
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(node_main(Path(sys.argv[1]), int(sys.argv[2])))
