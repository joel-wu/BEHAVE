"""Bounded, read-only pages from a role's explicit evidence allowlist."""

from behave.agent.tools import Tool, ToolResult, object_schema
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, ContentReader, FileData, FileRef, read_verified
from behave.core.records import canonical_json, fields


def evidence_files(prefix: str, document: bytes, references: tuple[BlobRef, ...], reader: ContentReader):
    """Mount the same explicit evidence allowlist as complete read-only files.

    No recursive store traversal, pagination or extra model tool is involved.
    Binary diagnostics remain byte-exact and can be inspected with shell tools.
    """
    payloads = (document, *(read_verified(reader, ref) for ref in references))
    files = tuple(FileData(FileRef(f"{prefix}/{i}.blob", BlobRef.of(payload)), payload)
                  for i, payload in enumerate(payloads))
    index = canonical_json({"documents": [file.reference.as_dict() for file in files]})
    return (FileData(FileRef(f"{prefix}/index.json", BlobRef.of(index)), index), *files)


def evidence_page(document, references, reader, index, offset):
    """Pure page reduction shared by live tools and offline receipt verification."""
    references = (BlobRef.of(document), *references)
    if (type(index) is not int or not 0 <= index < len(references)
            or type(offset) is not int or offset < 0):
        return {"error": "review-selection-invalid", "documents": len(references)}, None
    reference = references[index]
    payload = document if index == 0 else read_verified(reader, reference)
    try:
        content = payload.decode("utf-8")
    except UnicodeError:
        return {"error": "review-document-not-text", "reference": reference.as_dict()}, None
    end = min(len(content), offset + defaults.SYN_EVIDENCE_PAGE_CHARS)
    return {"reference": reference.as_dict(), "documents": len(references),
        "offset": offset, "total_chars": len(content), "content": content[offset:end],
        "next_offset": end if end < len(content) else None}, payload


def evidence_tool(*, name: str, description: str, revision: str, document: bytes,
                  references: tuple[BlobRef, ...], reader: ContentReader) -> Tool:
    """Document zero is embedded; remaining documents are exact allowlisted blobs.

    This never grants a general content-store capability. Pages bound individual
    replies without rejecting large documents. Each successful read carries
    its full immutable dependency into the consuming attempt's journal.
    """
    def inspect(workspace, arguments):
        row = fields(arguments, {"document", "offset"})
        content, payload = evidence_page(document, references, reader, row["document"], row["offset"])
        return ToolResult(content, () if payload is None else (workspace.store.put(payload),))

    return Tool(name, description,
                object_schema({"document": {"type": "integer", "minimum": 0},
                               "offset": {"type": "integer", "minimum": 0}}),
                inspect, revision + ":" + BlobRef.of(document).sha256)
