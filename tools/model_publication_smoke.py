"""Prepare a cached-weight INITIAL checkpoint for detached serving acceptance.

This fixture does not train, approve a corpus or implement a trainer adapter.
Its inert RNG/sampler records explicitly say so. Publication and later serving
use the production checkpoint APIs; the serving job never falls back to cache.
Run on a compute node under the existing bounded job supervisor: capture may
copy tens of GiB. Only explicitly selected files are read, never model code.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory


def publish_fixture(model: Path, revision: str, names: tuple[str, ...], output: Path) -> dict:
    from model_weight_input import WeightInput

    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef, relative_paths
    from behave.core.file_input import read_regular_file
    from behave.core.records import canonical_json, text
    from behave.serving.model_files import safetensor_shards
    from behave.train.checkpoint_storage import publish_weights
    from behave.train.checkpoints import TrainingLedger, TrainingState, store_state_file
    from behave.train.plan import TrainingManifest
    from behave.train.update_records import TrainerContract

    if not model.is_absolute() or not output.is_absolute():
        raise ValueError("explicit absolute source and fresh output directories required")
    text(revision, "operator-declared source revision")
    names = relative_paths(names)
    if "config.json" not in names:
        raise ValueError("select config.json in the inference file set")
    relative_paths((*names, "acceptance/rng.json", "acceptance/sampler.json"))
    index = None
    if "model.safetensors.index.json" in names:
        index = read_regular_file(model / "model.safetensors.index.json")
    safetensor_shards(names, index)
    sizes = {}
    for name in names:
        info = (model / name).stat()  # HF cache links are explicitly allowed.
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("selected source must be a regular file")
        sizes[name] = info.st_size
    total = sum(sizes.values())

    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    run = JournalDirectory(output / "training")
    objects, files = run.objects, []
    for name in names:
        fd = os.open(model / name, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != sizes[name]:
                raise ValueError("source changed before capture")
            captured = store_state_file(stream, objects, component="model", path=name)
            after = os.fstat(stream.fileno())
            if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or captured.content.size_bytes != sizes[name]):
                raise ValueError("source changed during capture")
        if name == "model.safetensors.index.json" and captured.content != BlobRef.of(index):
            raise ValueError("weight index changed after selection")
        files.append(captured)
        print(json.dumps({"phase": "captured", "file": name, "bytes": captured.content.size_bytes}), flush=True)

    policy = objects.put(canonical_json({
        "schema": "behave.cached-weight-acceptance/1", "optimizer_updates": 0,
        "source_model": str(model), "declared_revision": revision,
        "files": {file.path: file.content.as_dict() for file in files},
    }))
    # Fixture-only allowlist: no dataset, rollout or optimizer is consumed here.
    marker = BlobRef.of(b"publication acceptance fixture; not an approved dataset")
    manifest = TrainingManifest(marker, (("publication-acceptance-only", marker),))
    contract = TrainerContract("cached-weight-acceptance-only/1", policy, objects.put(manifest.to_bytes()),
                               components=("model", "rng", "sampler"))
    for component in ("rng", "sampler"):
        data = canonical_json({"acceptance_only": True, "component": component, "initialized_trainer": False})
        files.append(store_state_file(BytesIO(data), objects, component=component, path=f"acceptance/{component}.json"))
    state = TrainingState(policy, tuple(files), {"acceptance_only": True, "optimizer_updates": 0})
    print(json.dumps({"phase": "initial-checkpoint", "bytes": total}), flush=True)
    with TrainingLedger(run, contract) as ledger:
        ledger.initialize(state, objects)
        head, _ = ledger.seal()
    print(json.dumps({"phase": "publication"}), flush=True)
    publication = publish_weights(objects, head, contract=contract, component="model", objects=objects)
    selected = WeightInput(str(objects.directory), publication, head, contract, "model")
    (output / "weights.json").write_bytes(selected.to_bytes())
    (output / "weights.json").chmod(0o444)
    result = {"schema": "behave.publication-acceptance-fixture/1", "optimizer_updates": 0,
              "bytes": total, "files": len(names), "policy": policy.as_dict(),
              "publication": publication.as_dict(), "checkpoint": head.as_dict(),
              "weights_input": str(output / "weights.json")}
    (output / "result.json").write_bytes(canonical_json(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from behave.core.paths import scratch_root
    from behave.runtime.snapshots import source_command, stage

    # The child captures selected bytes; it does not start a model or trainer.
    # Only verified code is temporary, never the published checkpoint output.
    with TemporaryDirectory(prefix="behave-publication-fixture-", dir=scratch_root()) as local:
        source = Path(local) / "source"
        stage(args.snapshot, source)
        command = source_command(source,
            "import json; from tools.model_publication_smoke import publish_fixture; "
            "result=publish_fixture(Path(sys.argv[1]),sys.argv[2],tuple(sys.argv[4:]),Path(sys.argv[3])); "
            "print(json.dumps(result,indent=2),flush=True)",
            args.model, args.revision, args.output, *args.files)
        return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(repository), str(repository / "src"), str(repository / "tools")]
    raise SystemExit(main())
