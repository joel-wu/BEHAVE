"""Structural checks for the documented implementer-facing spec format.

This is not a semantic validator, HDL parser, or replacement for B/R replay.
Fenced source examples do not contribute Markdown headings.
"""
from __future__ import annotations

import re

SECTIONS = ('Interface', 'Modules', 'Functional Behavior', 'Reset Behavior')


def headings(text: str) -> tuple[tuple[int, str, int], ...]:
    result = []
    fence = None
    for line_number, line in enumerate(text.splitlines(), 1):
        if fence is not None:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) +
                            '{' + str(fence[1]) + r',}\s*', line):
                fence = None
            continue
        opening = re.match(r' {0,3}(`{3,}|~{3,})', line)
        if opening:
            marker = opening.group(1)
            fence = (marker[0], len(marker))
            continue
        match = re.match(r' {0,3}(#{1,6})\s+(.+?)\s*$', line)
        if match:
            title = re.sub(r'\s+#+\s*$', '', match.group(2))
            result.append((len(match.group(1)), title, line_number))
    if fence is not None:
        raise ValueError('unclosed Markdown code fence')
    return tuple(result)


def without_behavior_interface(text: str) -> str:
    """Remove labelled model-interface sections, not RTL semantics.

    Functional Behavior and Reset Behavior remain unchanged. Source authors
    must keep model-only instructions inside the Behavior interface section.
    """
    entries = headings(text)
    lines = text.splitlines(keepends=True)
    kept = []
    cursor = 0
    for index, (level, title, start) in enumerate(entries):
        if title.casefold() != "behavior interface":
            continue
        end = next((line - 1 for next_level, _, line in entries[index + 1:]
                    if next_level <= level), len(lines))
        if start - 1 >= cursor:
            kept.extend(lines[cursor:start - 1])
        cursor = max(cursor, end)
    kept.extend(lines[cursor:])
    return "".join(kept)


def behavior_entry(text: str) -> str:
    """Read the class explicitly declared in the model-interface section.

    This is release metadata, not discovery from an implementation or a guess
    based on its filename. Both supported spellings occur in the public corpus.
    """
    entries = headings(text)
    lines = text.splitlines()
    names = set()
    for index, (level, title, start) in enumerate(entries):
        if title.casefold() != "behavior interface":
            continue
        end = next((line - 1 for lev, _, line in entries[index + 1:] if lev <= level), len(lines))
        section = "\n".join(lines[start:end])
        names.update(re.findall(r"^\s*class\s+([A-Za-z_]\w*)\s*[:(]", section, re.MULTILINE))
        names.update(re.findall(r"`([A-Za-z_]\w*)\(\)`\s+provides", section))
    if len(names) != 1:
        raise ValueError("spec must explicitly name one Behavior entry")
    return names.pop()


def issues(text: str) -> tuple[tuple[str, str], ...]:
    """Return all structural findings with explicit, non-inferred categories."""
    entries = headings(text)
    if not entries or entries[0][0] != 1 or sum(level == 1 for level, _, _ in entries) != 1:
        return (('module-title', 'expected one module title before all section headings'),)
    findings = []
    sections = tuple(title for level, title, _ in entries if level == 2)
    if sections != SECTIONS:
        findings.append(('top-level-section-layout', f'expected sections {SECTIONS!r}, found {sections!r}'))
    for title in SECTIONS:
        if title not in sections:
            findings.append(('missing-section', title))
    if sections.count('Interface') == 1:
        interface_start = next(line for level, title, line in entries if level == 2 and title == 'Interface')
        interface_end = next((line for level, _, line in entries
                              if level <= 2 and line > interface_start), len(text.splitlines()) + 1)
        interfaces = tuple(title for level, title, line in entries
                           if level == 3 and interface_start < line < interface_end)
        if interfaces[:2] != ('RTL interface', 'Behavior interface'):
            findings.append(('interface-subsection-labels', 'Interface must begin with RTL interface and Behavior interface'))
    lines = text.splitlines()
    bounds = [(line, level, title) for level, title, line in entries if level <= 2]
    for (start, level, title), (end, _, _) in zip(bounds, bounds[1:] + [(len(lines) + 1, 0, '')]):
        if not '\n'.join(lines[start:end - 1]).strip():
            kind = 'missing-introduction' if level == 1 else 'empty-section'
            findings.append((kind, f'empty introduction/section: {title}'))
    return tuple(findings)


def validate(text: str) -> None:
    findings = issues(text)
    if findings:
        raise ValueError('; '.join(message for _, message in findings))
