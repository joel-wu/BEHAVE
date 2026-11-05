"""One resource loader for role instructions; capabilities stay with role code.

Templates use literal ${name} substitution, never executable expressions. The
editor's final newline is omitted; every other byte of text is significant.
Rendered messages are persisted by the existing attempt/request journal.
"""

from __future__ import annotations

import re
from importlib.resources import files
from string import Template

_NAME = re.compile(r"[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)*")


def _template(name: str) -> Template:
    if type(name) is not str or _NAME.fullmatch(name) is None:
        raise ValueError("prompt name must be a package-relative template identifier")
    source = files("behave.agent").joinpath("prompts", name + ".md").read_text(encoding="utf-8")
    template = Template(source.removesuffix("\n"))
    if not template.is_valid():
        raise ValueError(f"invalid prompt placeholders: {name}")
    return template


def _render(names: tuple[str, ...], values: dict[str, str]) -> tuple[str, ...]:
    templates = tuple(_template(name) for name in names)
    required = {key for template in templates for key in template.get_identifiers()}
    if required != values.keys():
        raise ValueError(f"prompt variables differ: missing={sorted(required - values.keys())}, "
                         f"unexpected={sorted(values.keys() - required)}")
    if any(type(value) is not str for value in values.values()):
        raise TypeError("prompt variables must be strings; serialize task data at its owning boundary")
    # Substituted data is never parsed as another template.
    return tuple(template.substitute(values) for template in templates)


def render(name: str, /, **values: str) -> str:
    """Render one named template without implicit environment or cwd overrides."""
    return _render((name,), values)[0]


def role_messages(role: str, /, *, system_template: str | None = None,
                  user_template: str | None = None,
                  **values: str) -> tuple[dict[str, str], ...]:
    """Compose a role's system and opening user messages, not its tools or grants."""
    if type(role) is not str:
        raise TypeError("prompt role must be a string")
    system, user = _render((system_template or role + "/system", user_template or role + "/user"), values)
    return ({"role": "system", "content": system}, {"role": "user", "content": user})
