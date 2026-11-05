"""Range-preserving compiler rewrites; generated expressions retain their origin.

Unlike observer insertions, a replacement has an explicit *whole source range*.
An interior generated coordinate projects to that range, never to a guessed
nearby token. Newlines are preserved so compiler-generated line names stay put.
"""

import hashlib
import re
from bisect import bisect_right
from dataclasses import dataclass, field

from .source_edits import Replacement, _lines
from .source_locations import SOURCE_ATTRIBUTE


@dataclass(frozen=True, slots=True)
class SourceReplacements:
    source: str
    replacements: tuple[Replacement, ...]
    text: str = field(init=False)
    _spans: tuple[tuple[int, int, int, int], ...] = field(init=False, repr=False)
    _starts: tuple[int, ...] = field(init=False, repr=False)
    _source_lines: tuple[int, ...] = field(init=False, repr=False)
    _generated_lines: tuple[int, ...] = field(init=False, repr=False)
    _generated_size: int = field(init=False, repr=False)

    def __post_init__(self):
        if type(self.source) is not str or type(self.replacements) is not tuple:
            raise TypeError("compiler rewrites need exact text and an immutable replacement tuple")
        original = self.source.encode("utf-8")
        pieces, spans, cursor, size = [], [], 0, 0
        for edit in self.replacements:
            if type(edit) is not Replacement or type(edit.start) is not int \
                    or type(edit.end) is not int or type(edit.text) is not str:
                raise TypeError("invalid compiler replacement")
            if not cursor <= edit.start < edit.end <= len(original):
                raise ValueError("compiler replacements must be ordered, disjoint source ranges")
            if any(point < len(original) and original[point] & 0xC0 == 0x80
                   for point in (edit.start, edit.end)):
                raise ValueError("a compiler replacement splits a UTF-8 character")
            if "\n" in edit.text or "\r" in edit.text:
                raise ValueError("replacement expressions must occupy one generated line")
            added = edit.text.encode() + b"\n" * original[edit.start:edit.end].count(b"\n")
            unchanged = original[cursor:edit.start]
            pieces.extend((unchanged, added))
            size += len(unchanged)
            spans.append((size, size + len(added), edit.start, edit.end))
            size += len(added)
            cursor = edit.end
        pieces.append(original[cursor:])
        generated = b"".join(pieces)
        for name, value in {
            "text": generated.decode(), "_spans": tuple(spans),
            "_starts": tuple(row[0] for row in spans),
            "_source_lines": _lines(original), "_generated_lines": _lines(generated),
            "_generated_size": len(generated),
        }.items():
            object.__setattr__(self, name, value)

    def position(self, line, column, *, end=False):
        lines = self._generated_lines
        if type(line) is not int or type(column) is not int or type(end) is not bool \
                or not 1 <= line <= len(lines) or column < 1:
            raise ValueError("compiler location outside the normalization source map")
        offset = lines[line - 1] + column - 1
        limit = lines[line] - 1 if line < len(lines) else self._generated_size
        if offset > limit:
            raise ValueError(f"compiler column outside the normalization source map: "
                             f"line {line}, column {column}, last column {limit - lines[line - 1] + 1}")
        index = bisect_right(self._starts, offset) - 1
        if index >= 0:
            lo, hi, first, last = self._spans[index]
            if lo <= offset < hi:
                offset = first if offset == lo or not end else last
            else:
                offset -= hi - last
        original_line = bisect_right(self._source_lines, offset)
        return original_line, offset - self._source_lines[original_line - 1] + 1

    def locations(self, value, filename="design.sv"):
        """Project only exact compiler source ranges, retaining hierarchy parts."""
        def replace(match):
            if match.group(1) != filename or match.group(2) == "0":
                return match.group()
            lo = self.position(int(match[2]), int(match[3]))
            hi = self.position(int(match[4]), int(match[5]), end=True)
            return f"{filename}:{lo[0]}.{lo[1]}-{hi[0]}.{hi[1]}"
        return re.sub(r"([^|\s\"]+):(\d+)\.(\d+)-(\d+)\.(\d+)", replace, value)

    def rtlil(self, text):
        return "".join(self.locations(line) if SOURCE_ATTRIBUTE.fullmatch(line)
                       else line for line in text.splitlines(keepends=True))

    def manifest(self):
        digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
        return {"source": digest(self.source), "transformed": digest(self.text),
                "ranges": [[row.start, row.end, digest(row.text)] for row in self.replacements]}
