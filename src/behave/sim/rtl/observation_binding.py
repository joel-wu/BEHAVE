"""Admission-owned native occurrence observation, shared by replay and audits.

An empty structural catalogue needs no event collector. Every nonempty one does;
unsupported instrumentation is a capability refusal, never snapshot-derived hits.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..core.catalogue import CatalogueManifest
from ..core.failure import RunFailure
from ..core.record_value import canonical_json
from ..core.refusal import Refused
from .native_preparation import PreparedNativeEvents, prepare_scheduled_observer

VERSION = "rtl-observation-binding/2"


@dataclass(frozen=True, slots=True)
class ObservationBinding:
    """Closed semantic handoff; generated build text stays on the parent side."""

    artifact: str
    contract: str
    catalogue: str
    native_plan: str | None = None
    preparation: str | None = None

    def __post_init__(self):
        for value in (self.artifact, self.contract, self.catalogue):
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError("observation inputs need full SHA256 identities")
        if self.native_plan is None:
            if self.preparation is not None:
                raise ValueError("interface-only observation has no native preparation")
        else:
            for value in (self.native_plan, self.preparation):
                if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                    raise ValueError("native selection needs its plan and preparation identities")

    @property
    def native(self):
        return self.native_plan is not None

    def as_dict(self):
        return {"version": VERSION, "artifact": self.artifact, "contract": self.contract,
                "catalogue": self.catalogue, "native_plan": self.native_plan,
                "preparation": self.preparation}

    @property
    def identity(self):
        return hashlib.sha256(canonical_json(self.as_dict()).encode()).hexdigest()

    @classmethod
    def from_dict(cls, image):
        fields = {"artifact", "contract", "catalogue", "native_plan", "preparation"}
        if type(image) is not dict or set(image) != fields | {"version"} \
                or image["version"] != VERSION:
            raise ValueError("observation binding has a closed versioned schema")
        return cls(**{name: image[name] for name in fields})

    def check(self, artifact, contract, catalogue):
        if type(catalogue) is not CatalogueManifest or (
                self.artifact, self.contract, self.catalogue) != (
                    artifact, contract.digest, catalogue.digest()):
            raise ValueError("observation belongs to a different artifact, task or catalogue")
        if not self.native and (tuple(catalogue) or catalogue.occurrences):
            raise ValueError("a nonempty structural catalogue requires native observation")


@dataclass(frozen=True, slots=True)
class PreparedObservation:
    binding: ObservationBinding
    native: PreparedNativeEvents | None

    def __post_init__(self):
        if type(self.binding) is not ObservationBinding:
            raise TypeError("prepared observation requires an exact binding")
        if self.binding.native:
            if type(self.native) is not PreparedNativeEvents or (
                    self.native.plan.identity, self.native.request_sha256) != (
                        self.binding.native_plan, self.binding.preparation):
                raise ValueError("prepared native build differs from the binding")
        elif self.native is not None:
            raise ValueError("interface-only observation cannot carry a native build")


def prepare_observation(source, *, artifact, top, contract, catalogue, timeout_s):
    """Prepare the structural observer or report why it is unavailable."""
    common = {"artifact": artifact, "contract": contract.digest, "catalogue": catalogue.digest()}

    if not catalogue:
        binding = ObservationBinding(**common)
        binding.check(artifact, contract, catalogue)
        return PreparedObservation(binding, None)
    prepared = prepare_scheduled_observer(
        source, top=top, contract=contract, catalogue=catalogue, timeout_s=timeout_s)
    if type(prepared) is RunFailure:
        if prepared.kind in {"native-events-unscheduled-trigger",
                             "native-events-source-mapping-unavailable"}:
            raise Refused(prepared.reason)
        return prepared
    return PreparedObservation(ObservationBinding(
        **common, native_plan=prepared.plan.identity, preparation=prepared.request_sha256), prepared)
