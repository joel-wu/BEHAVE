"""Materialize the existing recorded-train inputs without expanding raw traces.

The full source archive remains the recovery/audit copy. Native replay needs
the producer's complete token report, candidate outcomes and prepared task
inputs, not every HTTP exchange. No tokens, rewards or models are recomputed.
"""

import argparse
import json
import shutil
import time
from pathlib import Path

from behave.runtime.records import write_json


def restore(source: Path, destination: Path, archive: Path):
    source, destination, archive = source.resolve(), destination.resolve(), archive.resolve()
    began = time.monotonic()
    destination.mkdir(parents=True)
    for name in ("binding.json", "configuration.json", "training_selection.json", "source_split.json"):
        shutil.copyfile(source / name, destination / name)
    shutil.copytree(source / "inputs", destination / "inputs")
    receipt_name = Path("rounds/native/request_00000000/response.json")
    receipt_path = source / receipt_name
    receipt = json.loads(receipt_path.read_bytes())
    binding = json.loads((source / "binding.json").read_bytes())
    objects_relative = Path("rounds") / Path(receipt["objects"]).relative_to(binding["run_directory"])
    old_objects, new_objects = source / objects_relative, destination / objects_relative
    new_objects.mkdir(parents=True)
    terminal = json.loads((old_objects / receipt["head"]["sha256"]).read_bytes())
    report_ref = terminal["payload"]["result"]
    report = json.loads((old_objects / report_ref["sha256"]).read_bytes())
    refs = [receipt["head"], report_ref, report["plan"],
            *(row["examples"]["result"] for row in report["candidates"] if row["examples"] is not None)]
    for ref in {ref["sha256"]: ref for ref in refs}.values():
        shutil.copyfile(old_objects / ref["sha256"], new_objects / ref["sha256"])
    # A prepared payload is optional and reproducible from the token report.
    # Retain it if the producer already emitted one, without changing its bytes.
    if "prepared_batch" in receipt:
        relative = Path("rounds") / Path(receipt["prepared_batch"]).relative_to(binding["run_directory"])
        (destination / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination / relative)
    (destination / receipt_name).parent.mkdir(parents=True)
    shutil.copyfile(receipt_path, destination / receipt_name)
    result = {"source_plan": str(source), "full_source_archive": str(archive),
        "replay_plan": str(destination), "candidates": len(report["candidates"]),
        "scored_candidates": sum(row["examples"] is not None and row["examples"]["reward"] is not None
                                 for row in report["candidates"]),
        "copied_report_objects": len({ref["sha256"] for ref in refs}),
        "seconds": time.monotonic() - began, "raw_trace_closure_expanded": False}
    write_json(destination / "replay_source.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(restore(args.source, args.destination, args.archive), indent=2))


if __name__ == "__main__":
    main()
