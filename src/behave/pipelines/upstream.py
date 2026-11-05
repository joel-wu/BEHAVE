"""Pinned external sources; no download or installation during a rollout."""

import importlib
import sys
from pathlib import Path
from threading import Lock

from behave.core.paths import repo_root

_LOCK = Lock()


def source_root(name: str) -> Path:
    return repo_root() / "external" / name


def module(name: str, component: str):
    root = source_root(name)
    parent = root / "src" if name == "mage" else root
    if not (parent / name / "__init__.py").is_file():
        raise RuntimeError(f"Install the pinned {name} source with tools.setup_pipeline_sources first")
    with _LOCK:
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        loaded = importlib.import_module(name + "." + component)
        if not Path(loaded.__file__).resolve().is_relative_to(root.resolve()):
            raise RuntimeError(f"{name} was imported from a different installation")
        return loaded
