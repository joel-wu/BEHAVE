"""Hydrate a verified object closure from packed cold storage into local scratch.

Archives are read sequentially, once. No archive path is extracted, no legacy
mount is read, and no small file is created in cold storage. The returned CAS
uses the ordinary DirectoryReader/DirectoryStore layout. A cache receipt is NOT
a training-ready or evaluation-completed receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile
import tempfile
import time


def reference_sizes(references):
    sizes = {}
    for row in references:
        digest, size = row.get("sha256"), row.get("size_bytes")
        if (not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None
                or type(size) is not int or size < 0):
            raise ValueError("invalid content reference")
        if digest in sizes and sizes[digest] != size:
            raise ValueError("conflicting content sizes")
        sizes[digest] = size
    if not sizes:
        raise ValueError("empty content closure")
    return sizes


def hydrate(archives, references, destination, *, progress=None):
    """Populate a NEW private local directory, checking every selected payload.

    Only regular tar members with requested content-addressed basenames are
    read. Symlinks/devices/hardlinks are never followed. A normal hardlinked
    archive contains the regular payload at its first occurrence; missing
    payloads fail closed at the end. Member paths cannot choose output paths.
    """
    sizes = reference_sizes(references)
    destination = Path(destination).absolute()
    local_root = Path(tempfile.gettempdir()).resolve()
    if (not destination.resolve().is_relative_to(local_root)
            or destination.resolve() == local_root):
        raise ValueError("object cache must be a private node-local scratch directory")
    sources = [Path(path).resolve(strict=True) for path in archives]
    if not sources or any(path.is_relative_to(Path("/legacy")) for path in sources):
        raise ValueError("archives must be independent of legacy")
    destination.mkdir(parents=True, exist_ok=False)
    if destination.stat().st_dev != local_root.stat().st_dev:
        raise ValueError("object cache is not on the local scratch filesystem")
    objects = destination / "objects"
    objects.mkdir()
    missing = set(sizes)
    started = last_report = time.monotonic()
    extracted_bytes = members = 0
    opened = []
    try:
        for source in sources:
            if not missing:
                break
            opened.append(str(source))
            with source.open("rb", buffering=8 * 1024 * 1024) as raw:
                with tarfile.open(fileobj=raw, mode="r|", bufsize=8 * 1024 * 1024) as archive:
                    for member in archive:
                        members += 1
                        # tarfile otherwise retains millions of unrelated headers.
                        archive.members.clear()
                        digest = member.name.rsplit("/", 1)[-1]
                        if member.isreg() and digest in missing:
                            if member.size != sizes[digest]:
                                raise ValueError(f"archive content size differs: {digest}")
                            stream = archive.extractfile(member)
                            if stream is None:
                                raise ValueError("regular tar member has no payload")
                            temporary = objects / (".partial-" + digest)
                            h = hashlib.sha256()
                            count = 0
                            with stream, temporary.open("xb") as output:
                                while block := stream.read(1024 * 1024):
                                    h.update(block)
                                    output.write(block)
                                    count += len(block)
                            if count != sizes[digest] or h.hexdigest() != digest:
                                raise ValueError(f"archive content SHA differs: {digest}")
                            temporary.chmod(0o444)
                            temporary.rename(objects / digest)
                            extracted_bytes += count
                            missing.remove(digest)
                        now = time.monotonic()
                        if progress is not None and now - last_report >= 15:
                            progress({"state": "hydrating_local_cache", "objects": len(sizes) - len(missing),
                                      "required": len(sizes), "bytes": extracted_bytes,
                                      "members_scanned": members, "seconds": round(now - started, 2)})
                            last_report = now
                        if not missing:
                            break
        if missing:
            raise ValueError(f"packed archives lack {len(missing)} required payloads")
        result = {"schema": "behave.local-object-cache/1", "state": "verified_local_cache",
                  "directory": str(destination), "objects": len(sizes), "bytes": extracted_bytes,
                  "archives": opened, "members_scanned": members,
                  "seconds": round(time.monotonic() - started, 2),
                  "legacy_reads": 0, "cold_small_file_writes": 0,
                  "training_readiness_published": False}
        (destination / "cache.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    except BaseException as error:
        # Leave the private cache for inspection; never mark incomplete input ready.
        (destination / "failure.json").write_text(json.dumps({"error": str(error),
            "verified_objects": len(sizes) - len(missing), "required": len(sizes)}) + "\n")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--archive", action="append", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    value = json.loads(args.manifest.read_text())
    result = hydrate(args.archive, value["references"], args.destination,
                     progress=lambda row: print(json.dumps(row), flush=True))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
