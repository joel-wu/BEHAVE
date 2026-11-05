"""Exact simulator signal lookup and observation, shared by both RTL drivers."""

import re


def physical_name(path):
    """Render compiler-resolved segments for the native register adapter.

    This is not a parser: escaped identifier spelling remains in its original
    segment. The adapter rejects ambiguous spellings rather than choosing one.
    """
    return path[0] + ''.join(f'[{part}]' if type(part) is int else f'.{part}'
                            for part in path[1:])


def state_paths(names, source_fields, native_paths=()):
    """Resolve selected source leaves, preserving hierarchy and array order.

    Compiler-owned native overrides also cover anonymous procedural scopes.
    Ordinary legacy flattened memory names retain their declared-index mapping.
    No complete array expansion is needed just to resolve a few state leaves.
    """
    roots = {}
    for field, shape in source_fields.items():
        path = (field,) if isinstance(field, str) else field
        roots.setdefault(physical_name(path), []).append((field, path, shape))
    overrides = {tuple(key) if isinstance(key, list) else key: path for key, path in native_paths}
    result = {}
    for name in names:
        root, indices = name, []
        while root not in roots:
            match = re.fullmatch(r'(.+)\[(-?\d+)\]', root)
            if match is None:
                break
            root = match[1]
            indices.insert(0, int(match[2]))
        matches = roots.get(root, ())
        if not matches:
            result[name] = (name,)
            continue
        if len(matches) != 1:
            result[name] = None  # Literal dots/brackets can shadow a hierarchy.
            continue
        field, path, shape = matches[0]
        path = overrides.get(field, path)
        if indices:
            dimensions = []
            while type(shape) is dict:
                dimensions.append((shape['left'], shape['right']))
                shape = shape['element']
            if len(indices) == 1 and len(dimensions) > 1:
                mapped = state_path(f'word[{indices[0]}]', {'word': matches[0][2]})
                indices = None if mapped is None else mapped[1:]
            elif (len(indices) != len(dimensions) or any(
                    not min(left, right) <= index <= max(left, right)
                    for index, (left, right) in zip(indices, dimensions))):
                indices = None
        result[name] = None if path is None or indices is None else (*path, *indices)
    return result


def state_path(name, source_fields):
    """Map a Yosys memory word to the compiler-declared native array indices.

    Yosys preserves one-dimensional word indices, but flattens two or more
    unpacked dimensions in declaration order (leftmost element is position 0).
    The native compiler keeps those dimensions. A width check cannot establish
    this mapping: a subarray's length can even equal the expected word width.
    Only an actual fixed-array declaration authorizes index translation.
    """
    if name in source_fields:
        return (name,)  # Includes escaped identifiers containing brackets.
    match = re.fullmatch(r"(.+)\[(-?\d+)\]", name)
    if match is None:
        return (name,)
    root, index = match[1], int(match[2])
    shape = source_fields.get(root)
    dimensions = []
    while type(shape) is dict:
        dimensions.append((shape['left'], shape['right']))
        shape = shape['element']
    if not dimensions:
        return (name,)
    if len(dimensions) == 1:
        return root, index
    indices = []
    for left, right in reversed(dimensions):
        index, position = divmod(index, abs(left - right) + 1)
        indices.append(left + position if left <= right else left - position)
    if index:
        return None  # No declared word at this flattened position.
    return (root, *reversed(indices))


def path_handle(parent, path):
    if path is None:
        return None
    for name in path:
        parent = exact_handle(parent, name)
        if parent is None:
            break
    return parent


def exact_handle(parent, name):
    """Resolve the HDL name, including names that shadow Python handle attributes."""
    try:
        return parent[name]
    except (AttributeError, KeyError, TypeError):
        return None


def read_handle(handle):
    """Read bits without replacing an unknown or unavailable value by zero."""
    if handle is None:
        return None
    raw = handle.value
    try:
        return int(raw)
    except (TypeError, ValueError):
        text = str(raw)
        return text if text and set(text) <= set("01xzXZ-") else None


def width_of(handle):
    if handle is None:
        return None
    try:
        return len(handle)
    except (TypeError, ValueError):
        try:
            return len(handle.value)
        except (TypeError, ValueError):
            return None
