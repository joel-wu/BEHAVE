"""Closed, operator-bounded public-source acquisition inputs.

Selectors and licence/revision strings are declarations. HTTPS retrieves bytes;
it does not prove that a declared revision or redistribution right is correct.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, relative_path, relative_paths
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, version


def hostname(value: str) -> str:
    if (type(value) is not str or len(value) > 253 or value != value.lower()
            or "." not in value or not re.search("[a-z]", value)
            or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) is None
                   for part in value.split("."))):
        raise RecordError("fetch-host-invalid", "fetch hosts must be explicit lowercase DNS names")
    return value


def public_url(value: str) -> str:
    """No credentials, ports, queries, fragments or alternate URL spellings."""
    if (type(value) is not str or not value
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value) or "\\" in value
            or re.search(r"%(?![0-9a-fA-F]{2})", value)):
        raise RecordError("fetch-url-invalid", "fetch requires an ASCII HTTPS URL")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.netloc == hostname(parsed.hostname)
                 and not parsed.query and not parsed.fragment and "?" not in value and "#" not in value)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise RecordError("fetch-url-invalid", "fetch requires query-free credential-free HTTPS on port 443")
    return value


def _canonical(payload: bytes, result):
    if result.to_bytes() != payload:
        raise RecordError("record-not-canonical", "fetch records must be canonical")
    return result


def _record(payload: bytes, expected: set[str], schema: str) -> dict:
    row = fields(parse_json(payload), expected | {"schema"})
    version(row.pop("schema"), schema)
    return row


@dataclass(frozen=True, slots=True)
class RemoteFile:
    path: str
    url: str
    expected: BlobRef | None = None

    def __post_init__(self):
        relative_path(self.path)
        public_url(self.url)
        if self.expected is not None and type(self.expected) is not BlobRef:
            raise TypeError("expected source content requires BlobRef")

    def as_dict(self):
        return {"path": self.path, "url": self.url,
                "expected": None if self.expected is None else self.expected.as_dict()}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"path", "url", "expected"})
        return cls(row["path"], row["url"], None if row["expected"] is None else BlobRef.from_dict(row["expected"]))


@dataclass(frozen=True, slots=True)
class FetchPlan:
    source_id: str
    origin: str
    revision: str | None
    license: str | None
    files: tuple[RemoteFile, ...]
    entry_file: str
    entry_symbol: str

    def __post_init__(self):
        text(self.source_id, "source_id")
        public_url(self.origin)
        for value in (self.revision, self.license):
            if value is not None:
                text(value, "source declaration")
        if type(self.files) is not tuple or not self.files or any(type(f) is not RemoteFile for f in self.files):
            raise TypeError("fetch requires explicit remote files")
        relative_paths(tuple(f.path for f in self.files))
        object.__setattr__(self, "files", tuple(sorted(self.files, key=lambda f: f.path)))
        if relative_path(self.entry_file) not in {f.path for f in self.files}:
            raise ValueError("entry file must be selected")
        text(self.entry_symbol, "entry_symbol")

    def to_bytes(self):
        return canonical_json({"schema": "behave.source-fetch-plan/1", "source_id": self.source_id,
            "origin": self.origin, "revision": self.revision, "license": self.license,
            "files": [f.as_dict() for f in self.files], "entry_file": self.entry_file, "entry_symbol": self.entry_symbol})

    @classmethod
    def from_bytes(cls, payload):
        row = _record(payload, {"source_id", "origin", "revision", "license", "files", "entry_file", "entry_symbol"},
                      "behave.source-fetch-plan/1")
        row["files"] = tuple(RemoteFile.from_dict(f) for f in array(row["files"]))
        return _canonical(payload, cls(**row))


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """Trusted supervisor configuration, never a model-chosen host grant."""

    allowed_hosts: tuple[str, ...]
    timeout_seconds: int = defaults.FETCH_SECONDS
    max_redirects: int = defaults.FETCH_REDIRECTS
    max_files: int = defaults.FETCH_FILES
    file_bytes: int = defaults.FETCH_FILE_BYTES
    total_bytes: int = defaults.FETCH_TOTAL_BYTES

    def __post_init__(self):
        if type(self.allowed_hosts) is not tuple or not self.allowed_hosts:
            raise ValueError("fetch requires explicit hosts")
        for host in self.allowed_hosts:
            hostname(host)
        if len(set(self.allowed_hosts)) != len(self.allowed_hosts):
            raise ValueError("fetch hosts must be unique")
        object.__setattr__(self, "allowed_hosts", tuple(sorted(self.allowed_hosts)))
        for value, low in ((self.timeout_seconds, 1), (self.max_redirects, 0),
                           (self.max_files, 1), (self.file_bytes, 1), (self.total_bytes, 1)):
            if type(value) is not int or value < low:
                raise ValueError("fetch budgets must be positive integers; redirect count may be zero")

    def check(self, plan: FetchPlan):
        if type(plan) is not FetchPlan:
            raise TypeError("fetch requires FetchPlan")
        if len(plan.files) > self.max_files:
            raise RecordError("fetch-plan-limit", "source selection exceeds fetch limits")
        if (sum(f.expected.size_bytes for f in plan.files if f.expected is not None) > self.total_bytes
                or any(f.expected is not None and f.expected.size_bytes > self.file_bytes for f in plan.files)):
            raise RecordError("fetch-plan-limit", "pinned source sizes exceed fetch limits")

    def permits(self, url: str) -> bool:
        return urlsplit(public_url(url)).hostname in self.allowed_hosts

    def to_bytes(self):
        return canonical_json({"schema": "behave.source-fetch-policy/1", "allowed_hosts": list(self.allowed_hosts),
            "timeout_seconds": self.timeout_seconds, "max_redirects": self.max_redirects, "max_files": self.max_files,
            "file_bytes": self.file_bytes, "total_bytes": self.total_bytes})

    @classmethod
    def from_bytes(cls, payload):
        row = _record(payload, {"allowed_hosts", "timeout_seconds", "max_redirects", "max_files", "file_bytes",
                               "total_bytes"}, "behave.source-fetch-policy/1")
        row["allowed_hosts"] = tuple(array(row["allowed_hosts"]))
        return _canonical(payload, cls(**row))
