"""Authenticate module-instance contexts without changing the retained RTLIL.

The locked compiler's ``flatten -scopename`` side document may add scope
attributes only. The ordinary document remains the catalogue authority. A scope
must also identify that document's exact flattened process and a module scope
cell; arbitrary scope strings or source-line guesses are not accepted.
"""

import re

_ATTRIBUTE = re.compile(r'\s*attribute \\scopename "([^"\n]+)"\s*')
_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*(?:\[-?\d+\])?(?:\.[A-Za-z_][A-Za-z0-9_$]*(?:\[-?\d+\])?)*")


def process_contexts(original: str, scoped: str) -> dict[str, str]:
    """Read authenticated relative module scopes for every retained process."""
    return execution_contexts(original, scoped)[0]


def execution_contexts(original: str, scoped: str) -> tuple[dict[str, str], dict[str, str]]:
    """Authenticate once, retaining scopes for processes and expression cells."""
    if any(_ATTRIBUTE.fullmatch(line) for line in original.splitlines()):
        raise ValueError("source scopename attributes cannot authenticate compiler contexts")
    normalized, contexts, cells, pending = [], {}, {}, None
    for line in scoped.splitlines(keepends=True):
        attribute = _ATTRIBUTE.fullmatch(line)
        if attribute:
            if pending is not None:
                raise ValueError("duplicate compiler scope attribute")
            pending = attribute[1]
            continue
        normalized.append(line)
        text = line.strip()
        if text.startswith("attribute "):
            continue
        if text.startswith("process "):
            name = text.removeprefix("process ")
            if name in contexts:
                raise ValueError("duplicate compiler process identity")
            contexts[name] = pending or ""
        elif text.startswith("cell "):
            cells[text.split()[2]] = pending or ""
        pending = None
    if pending is not None or "".join(normalized) != original:
        raise ValueError("scope annotations changed the original RTLIL document")
    modules = set(re.findall(r'^  cell \$scopeinfo \\([^\n]+)\n    parameter \\TYPE "module"\n',
                             original, re.MULTILINE))
    for process, scope in (*contexts.items(), *cells.items()):
        if scope:
            if not _PATH.fullmatch(scope) or scope not in modules \
                    or not process.startswith("$flatten\\" + scope + "."):
                raise ValueError("compiler scope does not name that exact module instance")
        elif process.startswith("$flatten\\"):
            raise ValueError("a flattened process is missing its compiler scope")
    return contexts, cells
