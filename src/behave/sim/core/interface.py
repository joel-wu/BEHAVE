"""Task-owned protocol-instance membership, separate from clock domains.

Endpoints retain their payload types and pin ownership. An instance groups the
endpoints interpreted by one protocol transition and names its logical mapping.
Declaring a grouping does not establish that this build can execute it.
"""

from __future__ import annotations

from .frozen import Frozen, FrozenMap
from .refusal import Refusal, Refused

__all__ = ["ProtocolInstance", "check_protocol_instances"]


class ProtocolInstance(Frozen):
    """One named group of logical endpoints with a versioned transaction mapping."""

    __slots__ = ("bindings", "endpoints", "mapping", "max_wait_cycles", "name")

    def __init__(self, name: str, endpoints, *, mapping: str = "direct/1",
                 bindings=None, max_wait_cycles: int | None = None):
        if type(name) is not str or not name:
            raise Refused(Refusal(
                "contract-interface-name-invalid",
                "a protocol instance needs a nonempty exact string name"))
        if (type(endpoints) not in (tuple, list) or not endpoints
                or any(type(endpoint) is not str or not endpoint
                       for endpoint in endpoints)):
            raise Refused(Refusal(
                "contract-interface-endpoints-invalid",
                "a protocol instance names a nonempty finite list of endpoints",
                interface=name))
        if len(endpoints) != len(set(endpoints)):
            raise Refused(Refusal(
                "contract-interface-endpoint-duplicate",
                "a protocol instance cannot name an endpoint twice",
                interface=name))
        if type(mapping) is not str:
            raise Refused(Refusal(
                "contract-interface-mapping-invalid",
                "a transaction mapping needs a name/positive-version identity",
                interface=name))
        mapping_name, separator, version = mapping.rpartition("/")
        if (not separator or not mapping_name or "/" in mapping_name
                or not version.isascii() or not version.isdecimal()
                or version.startswith("0")):
            raise Refused(Refusal(
                "contract-interface-mapping-invalid",
                "a transaction mapping needs a name/positive-version identity",
                interface=name))
        self.name = name
        self.endpoints = tuple(sorted(endpoints))
        self.mapping = mapping
        bindings = {} if bindings is None else bindings
        if (type(bindings) is not dict and not isinstance(bindings, FrozenMap)) or any(
                type(role) is not str or not role or type(pin) is not str or not pin
                for role, pin in bindings.items()):
            raise Refused(Refusal(
                "contract-interface-bindings-invalid",
                "interface roles map nonempty names to explicit physical pins",
                interface=name))
        if len(set(bindings.values())) != len(bindings):
            raise Refused(Refusal(
                "contract-interface-binding-duplicate",
                "distinct physical interface roles need distinct pins", interface=name))
        if max_wait_cycles is not None and (
                type(max_wait_cycles) is not int or max_wait_cycles < 0):
            raise Refused(Refusal(
                "contract-interface-wait-limit-invalid",
                "a physical wait bound is a nonnegative exact integer or absent",
                interface=name))
        self.bindings = FrozenMap(dict(bindings))
        self.max_wait_cycles = max_wait_cycles
        self._freeze()

    def as_dict(self):
        image = {"name": self.name, "endpoints": list(self.endpoints),
                 "mapping": self.mapping}
        # Omitted physical configuration preserves existing semantic identities.
        if self.bindings or self.max_wait_cycles is not None:
            image["physical"] = {"bindings": dict(self.bindings),
                                 "max_wait_cycles": self.max_wait_cycles}
        return image

    @classmethod
    def from_dict(cls, value):
        if (type(value) is not dict
                or set(value) not in ({"name", "endpoints", "mapping"},
                                     {"name", "endpoints", "mapping", "physical"})
                or type(value["endpoints"]) is not list):
            raise TypeError("a protocol-instance image needs name, endpoints and mapping")
        physical = value.get("physical", {"bindings": {}, "max_wait_cycles": None})
        if type(physical) is not dict or set(physical) != {"bindings", "max_wait_cycles"}:
            raise TypeError("a physical interface image needs bindings and max_wait_cycles")
        return cls(value["name"], value["endpoints"], mapping=value["mapping"], **physical)

    def __eq__(self, other):
        return type(other) is ProtocolInstance and self.as_dict() == other.as_dict()

    def __hash__(self):
        return hash((self.name, self.endpoints, self.mapping,
                     tuple(sorted(self.bindings.items())), self.max_wait_cycles))


def check_protocol_instances(instances, endpoints):
    """Explicit instances partition endpoints; omitted instances keep legacy grouping."""

    if not instances:
        return
    if len({instance.name for instance in instances}) != len(instances):
        raise Refused(Refusal(
            "contract-interface-name-duplicate", "protocol-instance names are unique"))
    known = {endpoint.name: endpoint for endpoint in endpoints}
    assigned = set()
    for instance in instances:
        members = set(instance.endpoints)
        unknown = members - known.keys()
        duplicate = members & assigned
        if unknown or duplicate:
            raise Refused(Refusal(
                "contract-interface-membership-invalid",
                "each endpoint belongs to exactly one declared protocol instance",
                interface=instance.name, unknown=sorted(unknown),
                duplicate=sorted(duplicate)))
        selected = [known[name] for name in instance.endpoints]
        if len({endpoint.clock for endpoint in selected}) != 1:
            raise Refused(Refusal(
                "contract-interface-clock-conflict",
                "one protocol instance advances in one clock domain; separate "
                "instances may use different domains", interface=instance.name))
        if len({(endpoint.adapter.name, endpoint.adapter.version)
                for endpoint in selected}) != 1:
            raise Refused(Refusal(
                "contract-interface-adapter-conflict",
                "one protocol instance uses one adapter identity",
                interface=instance.name))
        assigned.update(members)
    missing = known.keys() - assigned
    if missing:
        raise Refused(Refusal(
            "contract-interface-membership-incomplete",
            "explicit protocol instances must cover every logical endpoint",
            missing=sorted(missing)))
