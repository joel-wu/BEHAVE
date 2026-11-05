"""Explicit broker-only credentials; never shell-evaluate an env file.

The reference is public configuration; the loaded value is not. This is not a
same-UID security boundary: candidate code must remain in its separate sandbox.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from behave.core.records import canonical_json, fields, parse_json, text, thaw_json

from ..model import ModelFailure, ModelReply


@dataclass(frozen=True, slots=True)
class CredentialRef:
    path: str
    key: str
    revision: str

    def __post_init__(self) -> None:
        text(self.path, "credential path")
        text(self.revision, "credential deployment revision")
        if not Path(self.path).is_absolute() or not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.key):
            raise ValueError("invalid credential reference")

    def as_dict(self) -> dict:
        return {"path": self.path, "key": self.key, "revision": self.revision}

    @classmethod
    def from_dict(cls, value: object) -> CredentialRef:
        return cls(**fields(value, {"path", "key", "revision"}))


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str):
        if not value or any(not 33 <= ord(c) <= 126 for c in value):
            raise ValueError("invalid credential value")
        self._value = value

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    def authorization(self, provider: str) -> dict[str, str]:
        if provider == "anthropic":
            return {"x-api-key": self._value, "anthropic-version": "2023-06-01"}
        if provider in {"openai", "fireworks", "deepseek", "openrouter"}:
            return {"Authorization": "Bearer " + self._value}
        raise ValueError("unknown credential destination")

    def reflected_in(self, raw: bytes) -> bool:
        needle = self._value.encode("ascii")
        if needle in raw:
            return True
        try:
            return needle in canonical_json(parse_json(raw))
        except ValueError:
            return False


def redact_reflection(secret: Secret, result: ModelReply | ModelFailure) -> ModelReply | ModelFailure:
    """Reject reflected credentials before returning or persisting an outcome."""
    if not secret.reflected_in(result.raw) and not secret.reflected_in(canonical_json(result.as_dict())):
        return result
    # Independently safe usage and charges survive, but neither a provider's
    # raw envelope nor generated tools/text may carry its reflected credential.
    usage = {} if secret.reflected_in(canonical_json(thaw_json(result.usage))) else result.usage
    charge = result.charge
    if charge is not None and secret.reflected_in(canonical_json(charge.as_dict())):
        charge = None
    return ModelFailure("permanent", "provider-credential-reflected", usage, charge=charge)


def load_credential(ref: CredentialRef) -> Secret:
    """Read a private regular file, without following its final symlink.

    Supports KEY=value, export KEY=value and literal single/double quotes.
    No interpolation, commands, escapes, ambient env fallback or value logging.
    """
    fd = os.open(ref.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise ValueError("credential file must be regular, private and owned")
        selected = None
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
            for line in stream:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                key, sep, value = line.partition("=")
                if not sep or key.strip() != ref.key:
                    continue
                if selected is not None:
                    raise ValueError("duplicate credential binding")
                value = value.strip()
                if value[:1] in {"'", '"'}:
                    if len(value) < 2 or value[-1] != value[0]:
                        raise ValueError("invalid credential quoting")
                    value = value[1:-1]
                if any(c in value for c in "$`\\"):
                    raise ValueError("credential interpolation is not supported")
                selected = Secret(value)
        if selected is None:
            raise ValueError("credential binding missing")
        return selected
    finally:
        os.close(fd)


def create_credential_file(ref: CredentialRef, *, environment: Mapping[str, str]) -> CredentialRef:
    """Explicit operator setup; never an ambient fallback during model calls.

    Publish one selected binding with private permissions. The parent must
    exist, and an existing destination is never replaced, even with the same
    value. No secret enters a content store, digest, command or public record.
    A failed directory sync may leave the complete destination for inspection.
    """
    from tempfile import NamedTemporaryFile

    if type(ref) is not CredentialRef:
        raise TypeError("credential setup requires a CredentialRef")
    value = environment.get(ref.key)
    if type(value) is not str:
        raise ValueError("credential environment binding missing")
    Secret(value)  # Reject multiline/non-header values before creating anything.
    path = Path(ref.path)
    with NamedTemporaryFile(mode="wb", prefix=".behave-credential-", dir=path.parent) as temporary:
        temporary.write(f'{ref.key}="{value}"\n'.encode("ascii"))
        temporary.flush()
        os.fsync(temporary.fileno())
        # Reuse the broker's literal parser and ownership checks. In particular,
        # shell expansions are not accepted simply because setup used an env var.
        load_credential(CredentialRef(temporary.name, ref.key, ref.revision))
        os.link(temporary.name, path)  # Atomic, exclusive; never follow/replace the destination.
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return ref
