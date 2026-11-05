"""Pinned source-only lookup, interoperable with existing synthesis sources.

Import reads only the supplied catalog and its declared source/notice paths.
Queries operate on that frozen snapshot, never the researcher workspace. The
lexical index finds leads; ranking is not an assessment or an admission decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from types import MappingProxyType

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, ContentReader, FileRef, SourceArtifact, read_verified, relative_path
from behave.core.records import RecordError, canonical_json, fields, metadata, parse_json, thaw_json, version
from behave.core.storage import DirectoryStore
from behave.data.drafts import SourceRecord
from behave.data.ingest.snapshot import read_files
from behave.data.selection_records import SourceInventory


@dataclass(frozen=True, slots=True)
class SourceUnit:
    content: FileRef
    origins: tuple[MappingProxyType, ...]
    notices: tuple[FileRef, ...]

    def __post_init__(self):
        if type(self.content) is not FileRef or not self.origins:
            raise ValueError("a source unit requires pinned bytes and an attributed origin")
        object.__setattr__(self, "origins", tuple(metadata(origin) for origin in self.origins))

    @property
    def identifier(self):
        return self.content.content.sha256

    def as_dict(self):
        return {"content": self.content.as_dict(), "origins": [thaw_json(origin) for origin in self.origins],
                "notices": [file.as_dict() for file in self.notices]}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"content", "origins", "notices"})
        return cls(FileRef.from_dict(row["content"]), tuple(row["origins"]),
                   tuple(FileRef.from_dict(file) for file in row["notices"]))


@dataclass(frozen=True, slots=True)
class SourceLibrary:
    catalog: BlobRef
    units: tuple[SourceUnit, ...]

    def __post_init__(self):
        if type(self.catalog) is not BlobRef or not self.units:
            raise ValueError("a source library requires a pinned catalog and source units")
        if len({unit.identifier for unit in self.units}) != len(self.units):
            raise ValueError("source library contains duplicate content identities")

    def to_bytes(self):
        return canonical_json({"schema": "behave.source-library/1", "catalog": self.catalog.as_dict(),
                               "units": [unit.as_dict() for unit in self.units]})

    @property
    def closure(self) -> tuple[BlobRef, ...]:
        return tuple(dict.fromkeys((BlobRef.of(self.to_bytes()), self.catalog,
            *(file.content for unit in self.units for file in (unit.content, *unit.notices)))))

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "catalog", "units"})
        version(row["schema"], "behave.source-library/1")
        result = cls(BlobRef.from_dict(row["catalog"]), tuple(SourceUnit.from_dict(unit) for unit in row["units"]))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "source library must be canonical")
        return result


def open_library(directory: str | Path) -> tuple[BlobRef, DirectoryStore]:
    """Open a prepared library through its trusted configured directory.

    Older studies share a library via a directory alias. Resolve that boundary
    once so selection and objects use the same owner, even if the alias changes.
    Do not resolve the objects directory, owner receipt or content-file paths:
    their normal symlink and content-identity checks still apply.
    """
    from behave.core.runtime_locations import runtime_path

    directory = runtime_path(directory).resolve(strict=True)
    selection = parse_json((directory / "selection.json").read_bytes())
    return BlobRef.from_dict(selection["library"]), DirectoryStore(directory / "objects")


def import_library(root: Path, catalog: BlobRef, objects: DirectoryStore) -> BlobRef:
    """Adapt the prepared resource export; do not discover or read sibling files."""
    catalog_data, = read_files((FileRef("catalog.jsonl", catalog),), root)
    units = []
    origin_keys = {"repository", "file", "revision", "license", "storage",
                   "repository_license_summary", "notice_revision_basis", "notices"}
    for line in catalog_data.data.splitlines():
        row = fields(parse_json(line), {"id", "sha256", "path", "bytes", "origins"})
        ref = BlobRef(row["sha256"], row["bytes"])
        if row["id"] != ref.sha256 or row["path"] != f"source/{ref.sha256}.txt":
            raise RecordError("source-library-path-invalid", "source export must use content-addressed paths")
        origins = tuple(fields(origin, origin_keys) for origin in row["origins"])
        notices = []
        for path in sorted({path for origin in origins for path in origin["notices"]}):
            relative_path(path)
            if re.fullmatch(r"notices/[0-9a-f]{64}\.txt", path) is None:
                raise RecordError("source-library-path-invalid", "notice path must contain its content digest")
            # Size is only an I/O hint. read_files checks both size and digest and
            # rejects symlinks in every component before reading any file bytes.
            size = (root / path).stat(follow_symlinks=False).st_size
            notices.append(FileRef(path, BlobRef(Path(path).stem, size)))
        units.append(SourceUnit(FileRef(row["path"], ref), origins, tuple(notices)))
    library = SourceLibrary(catalog, tuple(sorted(units, key=lambda unit: unit.identifier)))
    selected = {file.path: file for unit in library.units for file in (unit.content, *unit.notices)}
    for file in read_files(tuple(selected.values()), root):
        objects.put(file.data)
    objects.put(catalog_data.data)
    return objects.put(library.to_bytes())


@dataclass(frozen=True, slots=True)
class LookupPolicy:
    page_items: int = defaults.SOURCE_SEARCH_PAGE_ITEMS
    page_characters: int = defaults.SOURCE_READ_PAGE_CHARACTERS

    def __post_init__(self):
        if any(type(n) is not int or n < 1 for n in (self.page_items, self.page_characters)):
            raise ValueError("lookup page sizes must be positive integers")


def _terms(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[^\W_]+", value.casefold()))


def select_library(reader: ContentReader, reference: BlobRef, identifiers: tuple[str, ...],
                   objects: DirectoryStore) -> BlobRef:
    """Publish an explicitly selected source view, including its reduced catalog.

    Split policy belongs to the caller. Unselected source bytes and catalog
    entries are not part of this view's closure; the original remains intact.
    """
    index = LibraryIndex(reader, reference)
    selected = set(identifiers)
    if not selected or len(selected) != len(identifiers):
        raise ValueError("selected source identifiers must be nonempty and unique")
    if not selected <= index.units.keys():
        raise RecordError("source-lookup-id-invalid", "selection is outside this library")
    rows = [parse_json(line) for line in read_verified(reader, index.library.catalog).splitlines()]
    catalog = objects.put(b"\n".join(canonical_json(row) for row in sorted(rows, key=lambda row: row["id"])
                                   if row["id"] in selected) + b"\n")
    units = tuple(index.units[key] for key in sorted(selected))
    objects.publish(reader, tuple(file.content for unit in units for file in (unit.content, *unit.notices)))
    return objects.put(SourceLibrary(catalog, units).to_bytes())


_DEFAULT_LOOKUP_POLICY = LookupPolicy()


class LibraryIndex:
    """An immutable in-memory text index with explicit, deterministic pagination."""

    def __init__(self, reader: ContentReader, library: BlobRef, policy: LookupPolicy = _DEFAULT_LOOKUP_POLICY):
        self.reference, self.policy = library, policy
        self.library = SourceLibrary.from_bytes(read_verified(reader, library))
        self.units = {unit.identifier: unit for unit in self.library.units}
        self.texts, self.notice_texts = {}, {}
        catalog = [parse_json(line) for line in read_verified(reader, self.library.catalog).splitlines()]
        if len(catalog) != len(self.units) or len({row["id"] for row in catalog}) != len(catalog):
            raise RecordError("source-library-catalog-mismatch", "source library differs from its catalog")
        for row in catalog:
            unit = self.units.get(row["id"])
            if unit is None or (unit.content.as_dict(), [thaw_json(origin) for origin in unit.origins]) != (
                    {"path": row["path"], "content": {"sha256": row["sha256"], "size_bytes": row["bytes"]}},
                    row["origins"]):
                raise RecordError("source-library-catalog-mismatch", "source identity or origins changed")
            notice_paths = {path for origin in unit.origins for path in origin["notices"]}
            if (notice_paths != {file.path for file in unit.notices}
                    or any(file.path != f"notices/{file.content.sha256}.txt" for file in unit.notices)):
                raise RecordError("source-library-catalog-mismatch", "source notice identities changed")
        for key, unit in self.units.items():
            self.texts[key] = read_verified(reader, unit.content.content).decode("utf-8")
            for notice in unit.notices:
                if notice not in self.notice_texts:
                    self.notice_texts[notice] = read_verified(reader, notice.content).decode("utf-8")

    @cached_property
    def terms(self):
        # Bash acquisition only needs the pinned files. Build a lexical index
        # when an offline caller explicitly requests this optional search API.
        return {key: _terms(" ".join(origin["repository"] + " " + origin["file"]
                                    for origin in unit.origins) + "\n" + self.texts[key])
                for key, unit in self.units.items()}

    @staticmethod
    def _offset(offset):
        if type(offset) is not int or offset < 0:
            raise RecordError("source-lookup-offset-invalid", "offset must be a nonnegative integer")

    def search(self, query: str, offset: int = 0) -> dict:
        self._offset(offset)
        if not isinstance(query, str):
            raise RecordError("source-lookup-query-invalid", "query must be text")
        terms = _terms(query)
        matches = [(key, terms & tokens) for key, tokens in self.terms.items() if not terms or terms & tokens]
        matches.sort(key=lambda pair: (-len(pair[1]), pair[0]))
        end = offset + self.policy.page_items
        rows = [{"id": key, "matched_terms": sorted(hit),
                 "origins": [thaw_json(origin) for origin in self.units[key].origins],
                 "characters": len(self.texts[key])} for key, hit in matches[offset:end]]
        return {"library": self.reference.as_dict(), "query": query, "offset": offset,
                "total": len(matches), "next_offset": end if end < len(matches) else None, "results": rows}

    def read(self, identifier: str, offset: int = 0, *, notice: str | None = None) -> dict:
        self._offset(offset)
        if identifier not in self.units:
            raise RecordError("source-lookup-id-invalid", "source is outside this library")
        unit = self.units[identifier]
        if notice is None:
            file, content = unit.content, self.texts[identifier]
        else:
            file = next((file for file in unit.notices if file.path == notice), None)
            if file is None:
                raise RecordError("source-lookup-notice-invalid", "notice is not attached to this source")
            content = self.notice_texts[file]
        end = offset + self.policy.page_characters
        return {"id": identifier, "file": file.as_dict(), "offset": offset,
                "content": content[offset:end], "total_characters": len(content),
                "next_offset": end if end < len(content) else None}

    def source_record(self, identifier: str, supporting=()) -> SourceRecord:
        """Bundle a primary source with its requested dependencies and notices."""
        if identifier not in self.units:
            raise RecordError("source-lookup-id-invalid", "selection is outside this library")
        unit = self.units[identifier]
        provenance = BlobRef.of(self._provenance(unit))
        origin = unit.origins[0]
        files = [unit.content, *unit.notices, FileRef("source_origins.json", provenance)]
        for key in dict.fromkeys(supporting):
            if key == identifier:
                continue
            source = self.source_record(key)
            files.extend(FileRef(f"supporting_source_origins/{key}.json", file.content)
                         if file.path == "source_origins.json" else file for file in source.selected.files)
        return SourceRecord(identifier, origin["repository"], origin["revision"] or None,
            origin["license"] or None,
            SourceArtifact(tuple(dict.fromkeys(files)),
                           unit.content.path, "source"))

    def _provenance(self, unit: SourceUnit) -> bytes:
        return canonical_json({"library": self.reference.as_dict(), "unit": unit.identifier,
                               "origins": [thaw_json(origin) for origin in unit.origins]})

    def inventory(self, identifiers: tuple[str, ...], objects: DirectoryStore) -> BlobRef:
        """Freeze chosen units as ordinary sources for screening/construction.

        This is not an approval or a generated-task quota. All recorded origins
        and licence qualifications accompany the source, including deduplicated
        units with several origin records.
        """
        sources = []
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("selected source identifiers must be unique")
        for identifier in identifiers:
            if identifier not in self.units:
                raise RecordError("source-lookup-id-invalid", "selection is outside this library")
            unit = self.units[identifier]
            objects.put(self.texts[identifier].encode())
            for notice in unit.notices:
                objects.put(self.notice_texts[notice].encode())
            objects.put(self._provenance(unit))
            source = self.source_record(identifier)
            sources.append(objects.put(source.to_bytes()))
        return objects.put(SourceInventory(tuple(sources)).to_bytes())
