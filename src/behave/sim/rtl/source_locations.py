"""Compiler provenance fields, including flattened hierarchy scope records."""

import re

SOURCE_ATTRIBUTES = frozenset({"src", "cell_src", "module_src"})
SOURCE_ATTRIBUTE = re.compile(
    r'(?P<prefix>\s*attribute \\(?P<name>' + "|".join(sorted(SOURCE_ATTRIBUTES)) + r') ")'
    r'(?P<locations>[^"\n]*)(?P<suffix>"\s*)'
)
