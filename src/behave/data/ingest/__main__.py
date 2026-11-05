"""Import a hash-pinned local SourceRecord into the construction content store.

This is snapshot acquisition, not source discovery, licence verification or
task approval. The source manifest must already declare every selected digest.
"""

import argparse
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json
from behave.core.storage import DirectoryStore
from behave.data.drafts import SourceRecord

from .snapshot import ingest_source, read_source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-record", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--objects", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = read_regular_file(args.source_record)
        source = SourceRecord.from_bytes(payload)
        if args.dry_run:
            read_source(source, args.source_root)
            reference = BlobRef.of(payload)
        else:
            reference = ingest_source(source, args.source_root, DirectoryStore(args.objects))
        output = {"status": "validated" if args.dry_run else "imported", "source": reference.as_dict(),
                  "files": len(source.selected.files), "publication_approved": False}
    except (RecordError, TypeError, ValueError, OSError) as error:
        print(canonical_json({"status": "unavailable", "code": getattr(error, "code", "source-import-invalid")}).decode())
        return 1
    print(canonical_json(output).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
