"""Resolve a public HF model once and download that exact revision to a shared cache.

Run with the serving interpreter, which already owns huggingface_hub. The output
record pins the resolved revision for subsequent serving and training launches.
This does not execute any model code or download remote Python implementations.
"""

import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def cache(model: str, destination: Path, cache_dir: Path, revision: str = "main") -> dict:
    with TemporaryDirectory(prefix="behave-hf-transfer-", dir="/tmp") as scratch:
        os.environ["HF_XET_CACHE"] = scratch
        from huggingface_hub import HfApi, snapshot_download

        pinned = HfApi().model_info(model, revision=revision).sha
        path = snapshot_download(model, revision=pinned, cache_dir=str(cache_dir),
                                 ignore_patterns=["*.md", "*.py", "*.msgpack", "*.h5", "*.ot"])
    record = {"model": model, "revision": pinned, "path": path}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("output", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--revision", default="main")
    args = parser.parse_args()
    print(json.dumps(cache(args.model, args.output, args.cache_dir, args.revision), indent=2))
