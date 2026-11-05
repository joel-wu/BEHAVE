"""Explicit source acquisition; separate from scope and publication decisions."""

from .fetch_evidence import FetchResult, verify_acquisition
from .fetch_records import FetchPlan, FetchPolicy, RemoteFile
from .remote import acquire_source
from .snapshot import ingest_source, read_source

__all__ = [
           "FetchPlan",
           "FetchPolicy",
           "FetchResult",
           "RemoteFile",
           "acquire_source",
           "ingest_source",
           "read_source",
           "verify_acquisition",
]
