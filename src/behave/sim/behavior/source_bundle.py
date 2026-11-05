"""Captured Python modules, statically linked before the existing Sim boundary.

The linker executes no submitted code. Concrete replay, source goals and exact
lowering consume the *same* retained normalized text, not separate import paths.
Unsupported Python module semantics remain capability refusals, not score zero.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from types import MappingProxyType

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, relative_paths
from behave.core.records import canonical_json

from ..core.failure import CandidateFault, FailureRaised, RunFailure
from ..core.refusal import Refusal, Refused


@dataclass(frozen=True, slots=True)
class BehaviorSourceBundle:
    files: MappingProxyType
    entry: str
    model: str

    def __post_init__(self):
        files = dict(self.files)
        relative_paths(tuple(files))
        if not 1 <= len(files) <= defaults.SIM_SOURCE_BUNDLE_FILES or any(
                type(data) is not bytes for data in files.values()):
            raise ValueError(
                f"Behavior bundle requires 1 to {defaults.SIM_SOURCE_BUNDLE_FILES} byte sources")
        if sum(map(len, files.values())) > defaults.SIM_SOURCE_BYTES:
            raise ValueError(
                f"Behavior source bundle exceeds {defaults.SIM_SOURCE_BYTES} bytes")
        if self.entry not in files or not self.entry.endswith(".py"):
            raise ValueError("Behavior entry must be a captured Python file")
        if type(self.model) is not str or not self.model:
            raise ValueError("Behavior bundle requires an explicit model name")
        object.__setattr__(self, "files", MappingProxyType(files))

    @property
    def identity(self) -> BlobRef:
        return BlobRef.of(canonical_json({
            "schema": "behave.behavior-source-bundle/1", "entry": self.entry, "model": self.model,
            "files": {path: BlobRef.of(data).as_dict() for path, data in self.files.items()},
        }))


@dataclass(frozen=True, slots=True)
class PreparedBehaviorSource:
    source: str
    bundle: BlobRef
    modules: tuple[str, ...]
    symbols: tuple[tuple[str, str, str], ...]
    dependencies: MappingProxyType
    file: str = "linked_behavior.py"
    model: str = ""

    def as_dict(self):
        return {"schema": "behave.prepared-behavior-source/2", "bundle": self.bundle.as_dict(),
                "source": BlobRef.of(self.source.encode()).as_dict(), "file": self.file,
                "model": self.model,
                "modules": list(self.modules), "symbols": [list(row) for row in self.symbols],
                "dependencies": {name: BlobRef.of(data).as_dict() for name, data in self.dependencies.items()}}


def prepare_behavior_bundle(bundle: BehaviorSourceBundle) -> PreparedBehaviorSource:
    """Capture a closed entry unchanged, or link the static-module subset.

    Capturing a file does not certify it. The usual isolated realization, execution
    and optional lowering gates still apply to exactly the retained bytes.
    """
    from .module_linker import link
    from .source_syntax import future_annotations_import, parsed_source, require_model_identifier

    if type(bundle) is not BehaviorSourceBundle:
        raise TypeError("linking requires an exact BehaviorSourceBundle")
    try:
        require_model_identifier(bundle.model, bundle.entry)
        try:
            source = bundle.files[bundle.entry].decode("utf-8")
        except UnicodeError:
            raise CandidateFault(Refusal(
                "behavior-source-not-utf8", "Python module is not UTF-8",
                file=bundle.entry)) from None
        tree = parsed_source(source, bundle.entry)
        imports = any(isinstance(node, (ast.Import, ast.ImportFrom))
                      and not future_annotations_import(node)
                      for node in ast.walk(tree))
        if (len(bundle.files) == 1 or not imports) and any(
                isinstance(node, ast.ClassDef) and node.name == bundle.model
                for node in tree.body):
            # Unreferenced captured files cannot affect a closed entry. Keep its
            # original Python semantics instead of imposing the import linker's
            # narrower subset merely because another file accompanies it.
            return PreparedBehaviorSource(
                source, bundle.identity, (bundle.entry,),
                ((bundle.entry, bundle.model, bundle.model),),
                MappingProxyType({}), file=bundle.entry, model=bundle.model)
        return link(bundle)
    except CandidateFault as exc:
        raise FailureRaised(RunFailure.candidate("candidate-frontend", exc.refusal)) from None
    except (RecursionError, MemoryError):
        raise Refused(Refusal("behavior-bundle-resource-limit", "static linking exceeded its resource budget")) from None
