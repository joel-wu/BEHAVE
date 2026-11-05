"""Finite records shared by ordinary and atomic Behavior execution."""

from ..symbolic.uniform_array import UniformArray


def canonical_memory(value, template):
    """Normalize a memory value against its declared indexed base.

    Dense native audit values and compact IR values must give the same evidence
    even after an explicit write restores the implicit value. A write ledger is
    separate evidence: this function represents the current *contents* only.
    Compact inputs visit only their explicit cells, not every memory address.
    """
    if type(template) is not UniformArray or template.is_uniform:
        raise TypeError("memory normalization needs an indexed base")
    if type(value) not in (list, UniformArray) or len(value) != len(template):
        raise ValueError("the observed memory has a different fixed shape")
    _tag, name, width = template.base_image
    base = UniformArray.memory(len(template), name, width)
    cells = (sorted(value.sparse_items())
             if type(value) is UniformArray and value.base_image == base.base_image
             else ((index, value[index]) for index in range(len(value))))
    for index, item in cells:
        if type(item) is not int:
            raise TypeError("an observed memory cell is an integer")
        if item != base.base_value(index):
            base = base.store(index, item)
    return base


def domain_tree(value):
    """Identity image retaining compact bases and their finite writes."""
    kind = type(value)
    if kind is UniformArray:
        return {
            "representation": "compact-array/2",
            "length": value.length,
            "base": domain_tree(value.base_image),
            "writes": [[index, domain_tree(item)]
                       for index, item in value.sparse_items()],
        }
    if kind in (list, tuple):
        return [domain_tree(item) for item in value]
    if kind is dict:
        return {key: domain_tree(item) for key, item in value.items()}
    return value


def record_tree(value):
    """Replay value image; indexed memories keep their base and sparse writes."""
    kind = type(value)
    if kind is UniformArray:
        if not value.is_uniform:
            return domain_tree(value)
        return [record_tree(value[index]) for index in range(value.length)]
    if kind is list:
        return [record_tree(item) for item in value]
    if kind is tuple:
        return tuple(record_tree(item) for item in value)
    if kind is dict:
        return {key: record_tree(item) for key, item in value.items()}
    return value
