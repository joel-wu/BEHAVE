"""Local safetensors layout checks shared by provisioning and publication.

These checks establish file availability, not tensor validity, tokenizer
compatibility or that a server loaded the files. They never import a loader.
"""

from collections.abc import Collection
from pathlib import Path

from behave.core.file_input import read_regular_file
from behave.core.records import parse_json


def safetensor_shards(names: Collection[str], index: bytes | None) -> set[str]:
    if index is not None:
        try:
            row = parse_json(index)
        except ValueError as error:
            raise ValueError("local model weight index is invalid") from error
        mapping = row.get("weight_map") if type(row) is dict else None
        if type(mapping) is not dict or not mapping:
            raise ValueError("local model weight index has no weight mapping")
        selected = tuple(mapping.values())
    else:
        selected = tuple(name for name in names if Path(name).name == name and name.endswith(".safetensors"))
    if not selected or any(
        type(name) is not str or "\x00" in name or Path(name).name != name
        or not name.endswith(".safetensors") or name not in names for name in selected
    ):
        raise ValueError("local model snapshot has missing or invalid safetensors files")
    return set(selected)


def inspect_model_files(model: Path) -> set[str]:
    """HF links into a blob cache are allowed; malformed indices never fall back."""
    import os

    index_path = model / "model.safetensors.index.json"
    index = None
    if os.path.lexists(index_path):
        try:
            index = read_regular_file(index_path)
        except (OSError, ValueError) as error:
            raise ValueError("local model weight index is not a readable regular file") from error
    names = {path.name for path in model.glob("*.safetensors")}
    selected = safetensor_shards(names, index)
    if any(not (model / name).is_file() for name in selected):
        raise ValueError("local model snapshot has missing or invalid safetensors files")
    return selected
