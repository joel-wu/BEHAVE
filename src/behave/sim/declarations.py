"""Task-owned interface declarations, independent of execution policy."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .core.contract import ClockDomain, Endpoint, Kind, Matching, ResetPolicy, TaskContract
from .core.frozen import Frozen, FrozenMap
from .core.refusal import Refusal, Refused

__all__ = ["Declarations", "contract_of_task", "declarations_at", "declared_contract"]
MANIFEST = "task.toml"
_KINDS = {"transaction": Kind.TRANSACTION, "sampled": Kind.SAMPLED}
_MATCHING = {"ordered": Matching.ORDERED, "identifier": Matching.IDENTIFIER,
             "content": Matching.CONTENT}
_RESET = {"clears": ResetPolicy.CLEARS, "keeps": ResetPolicy.KEEPS}


class Declarations(Frozen):
    """Immutable task claims. Runtime policies and analysis domains are separate."""

    __slots__ = ("clock_edges", "endpoints", "generated", "interface_contract",
                 "reset_restores", "source")

    def __init__(self, endpoints=None, reset_restores=None, source="", generated=None,
                 clock_edges=None, interface_contract: TaskContract | None = None):
        if interface_contract is not None:
            if type(interface_contract) is not TaskContract:
                raise TypeError("an explicit task interface is an exact TaskContract")
            if interface_contract.authority != "task":
                raise Refused(Refusal("declarations-interface-not-task-owned",
                                      "a task interface must be task owned"))
            if endpoints or clock_edges or reset_restores is not None:
                raise Refused(Refusal("declarations-interface-overridden",
                                      "an explicit interface cannot carry overrides"))
        self.interface_contract = interface_contract
        self.endpoints = FrozenMap({str(k): FrozenMap(v)
                                    for k, v in dict(endpoints or {}).items()})
        self.reset_restores = None if reset_restores is None else bool(reset_restores)
        self.clock_edges = FrozenMap(dict(clock_edges or {}))
        self.source = str(source)
        self.generated = FrozenMap(dict(generated or {}))
        self._freeze()

    def for_endpoint(self, name):
        return dict(self.endpoints.get(name, {}))

    def as_dict(self):
        image = {"endpoints": {name: {k: v.as_dict() if k == "framing" else getattr(v, "value", v)
                                        for k, v in sorted(block.items())}
                                for name, block in sorted(self.endpoints.items())},
                 "reset_restores": self.reset_restores,
                 "clock_edges": dict(self.clock_edges.items()), "source": self.source,
                 "generated": dict(self.generated.items())}
        if self.interface_contract is not None:
            image["interface_contract"] = self.interface_contract.as_dict()
        return image


def _read_toml(path, kind):
    import tomllib

    try:
        return tomllib.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001 - normalize all document failures
        raise Refused(Refusal(
            "declarations-unreadable",
            "this task's declarations could not be read, and guessing what they were "
            "meant to say is the thing the file exists to stop",
            path=str(path), which=kind,
            error=f"{type(exc).__name__}: {exc}")) from None


def _generated_provenance(path, block, endpoints, hand=None, *,
                          read_contract: Callable[[], TaskContract]):
    """Validate a manifest's named derivation and rederive all endpoint fields."""

    from .reference_rules import GENERATOR as REFERENCE_GENERATOR
    from .spec_rules import GENERATOR as SPEC_GENERATOR

    if not block:
        if not hand:
            raise Refused(Refusal(
                "declarations-provenance-absent",
                "this manifest says neither that a generator wrote it nor that a person "
                "did, and the difference decides whether its fields are re-derived from "
                "the specification or taken as somebody's claim. Deleting `[generated]` "
                "is not a way to be believed",
                path=str(path)))
        who = str(hand.get("who") or "").strip()
        reason = str(hand.get("why") or "").strip()
        if not who or not reason:
            # The facts are `named_who` and `stated_why` rather than `who` and `why`
            # because `Refusal`'s second positional parameter *is* `why`, and passing a
            # fact of that name is a `TypeError` at the point a refusal is raised -- the
            # one moment this code has to still work.
            raise Refused(Refusal(
                "declarations-hand-written-without-an-author",
                "a hand-written manifest is somebody's claim, so it names who made it "
                "and why -- `who` and `why` under `[written_by_hand]`",
                path=str(path), named_who=who, stated_why=reason))
        return {"written_by": "hand", "who": who, "why": reason}

    generator = block.get("generator")
    if type(generator) is not int or generator not in (
            SPEC_GENERATOR, REFERENCE_GENERATOR):
        raise Refused(Refusal(
            "declarations-generator-unknown",
            "this manifest names a generator this build cannot rederive",
            path=str(path), got=generator,
            known=[SPEC_GENERATOR, REFERENCE_GENERATOR]))
    if generator == REFERENCE_GENERATOR:
        return _reference_generated_provenance(path, block, endpoints, read_contract)
    return _spec_generated_provenance(path, block, endpoints)


def _generated_fields_agree(path, endpoints, derived, *, kind, why):
    from .spec_rules import fields_of

    read = fields_of(endpoints)
    disagree = sorted(key for key in set(derived) | set(read)
                      if derived.get(key) != read.get(key))
    if disagree:
        raise Refused(Refusal(
            kind, why, path=str(path),
            fields=[f"{key}: the file says {read.get(key)!r} and the rule derives "
                    f"{derived.get(key)!r}" for key in disagree]))


def _spec_generated_provenance(path, block, endpoints):
    """Compatibility reader for version-5 manifests derived from prose."""

    from .spec_rules import GENERATOR, derive, spec_digest

    stray = sorted(set(block) - {"generator", "spec_digest", "policy_digest"})
    if stray:
        raise Refused(Refusal(
            "declarations-unknown-provenance-field",
            "this build has no meaning for these provenance fields, and a provenance "
            "record it can only partly read is not one it can check",
            path=str(path), fields=stray))
    spec = Path(path).parent / "spec.md"
    if not spec.exists():
        raise Refused(Refusal(
            "declarations-spec-missing",
            "this manifest was generated from a specification that is not here, so "
            "nothing can check that it still says what the manifest read off it",
            path=str(path), spec=str(spec)))
    text = spec.read_text()
    digest = spec_digest(text)
    if str(block.get("spec_digest", "")) != digest:
        raise Refused(Refusal(
            "declarations-spec-changed",
            "the specification beside this manifest is not the one it was generated "
            "from, so the sentences it quotes may no longer be in it and the fields it "
            "derived from them may no longer follow",
            path=str(path), recorded=str(block.get("spec_digest", "")), found=digest))

    # **The values, re-derived.** The two checks above say the file was written by a rule
    # this build knows, from the specification still beside it -- and say nothing about
    # whether what is *in* it is what that rule produces. A manifest whose
    # `answers_per_transaction` was edited by hand passed both.
    derived, _cited, missing = derive(text)
    if missing:
        raise Refused(Refusal(
            "declarations-rule-would-not-derive-these",
            "this manifest says it was generated from this specification, and the rule "
            "that generates them refuses this specification -- so whatever is in the "
            "file, the rule did not produce it from what is beside it now",
            path=str(path), because=missing))
    _generated_fields_agree(
        path, endpoints, derived,
        kind="declarations-do-not-match-the-specification",
        why="these fields are not what the legacy rule derives from spec.md")
    return {"written_by": "tools/declare_tasks.py",
            "generator": GENERATOR, "spec_digest": digest,
            "fields_rederived": len(derived)}


def _reference_generated_provenance(path, block, endpoints,
                                    read_contract: Callable[[], TaskContract]):
    """Validate declarations derived from Behavior and the explicit task interface."""

    from .reference_rules import GENERATOR, behavior_digest, derive, interface_digest

    stray = sorted(set(block) - {
        "generator", "behavior_digest", "interface_digest",
    })
    if stray:
        raise Refused(Refusal(
            "declarations-unknown-provenance-field",
            "this reference-derived manifest has provenance fields this build "
            "cannot check",
            path=str(path), fields=stray))
    directory = Path(path).parent
    behavior = directory / "behavior.py"
    spec = directory / "spec.md"
    missing_files = [str(item) for item in (behavior, spec) if not item.exists()]
    if missing_files:
        raise Refused(Refusal(
            "declarations-reference-source-missing",
            "a reference-derived manifest needs behavior.py and the explicit task "
            "interface in spec.md to be rederived",
            path=str(path), missing=missing_files))

    source = behavior.read_text()
    found_behavior = behavior_digest(source)
    if str(block.get("behavior_digest", "")) != found_behavior:
        raise Refused(Refusal(
            "declarations-behavior-changed",
            "behavior.py is not the reference source from which this manifest was "
            "derived",
            path=str(path), recorded=str(block.get("behavior_digest", "")),
            found=found_behavior))
    try:
        contract = read_contract()
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize frontend failures
        raise Refused(Refusal(
            "declarations-interface-unreadable",
            "the explicit task interface used by this manifest cannot be read",
            path=str(path), error=f"{type(exc).__name__}: {exc}")) from None
    found_interface = interface_digest(contract)
    if str(block.get("interface_digest", "")) != found_interface:
        raise Refused(Refusal(
            "declarations-interface-changed",
            "the task interface is not the one against which behavior.py was bound",
            path=str(path), recorded=str(block.get("interface_digest", "")),
            found=found_interface))

    derived, _evidence, missing = derive(
        source, contract, file=str(behavior))
    if missing:
        raise Refused(Refusal(
            "declarations-rule-would-not-derive-these",
            "the Behavior/task boundary no longer yields this manifest",
            path=str(path), because=missing))
    _generated_fields_agree(
        path, endpoints, derived,
        kind="declarations-do-not-match-the-reference",
        why="these fields are not what behavior.py declares at the task boundary")
    return {
        "written_by": "tools/declare_tasks.py",
        "generator": GENERATOR,
        "behavior_digest": found_behavior,
        "interface_digest": found_interface,
        "fields_rederived": len(derived),
    }


def declarations_at(directory):
    """The declarations beside a problem, or `None` where the task carries none.

    Only task-owned semantics are read. Runtime policy is passed through Sim's
    public command API, never loaded from a neighbouring file.
    """
    directory = Path(directory)
    return _declarations_at(directory, lambda: _spec_contract(directory))


def _declarations_at(directory: Path, read_contract: Callable[[], TaskContract]):
    """Read declarations, using this read's task-interface authority."""
    path = directory / MANIFEST
    if not path.exists():
        interface_path = directory / "interface.json"
        if interface_path.exists():
            from .task_interface import read_task_interface_file

            contract, provenance = read_task_interface_file(directory)
            return Declarations(interface_contract=contract, source=str(interface_path),
                                generated=provenance)
        return None
    got = _read_toml(path, MANIFEST)

    # **A section this build has no meaning for is refused, not skipped.** A manifest is
    # read for the fields this build knows; one carrying others was written against a
    # different reader, and quietly using the intersection means running a task under
    # rules its file does not state. Measured: a `[bogus]` section went straight through.
    stray = sorted(set(got) - {"endpoint", "generated", "clock", "written_by_hand", "interface"})
    if stray:
        raise Refused(Refusal(
            "declarations-unknown-section",
            "this build has no meaning for these sections, so a file written against a "
            "different reader would be applied as though it said only what this one "
            "understands",
            path=str(path), sections=stray))

    interface, interface_provenance = None, {}
    if "interface" in got:
        if {"endpoint", "clock"} & got.keys():
            raise Refused(Refusal(
                "declarations-interface-overridden",
                "an explicit interface replaces endpoint and clock declaration sections"))
        if got.get("generated"):
            raise Refused(Refusal(
                "declarations-interface-not-derived",
                "legacy generators do not derive a complete physical interface; "
                "the task author must claim it explicitly"))
        from .task_interface import read_task_interface

        interface, interface_provenance = read_task_interface(directory, got["interface"])

    endpoints = {}
    for name, block in dict(got.get("endpoint") or {}).items():
        endpoints[str(name)] = _endpoint_block(path, str(name), dict(block))

    # What the task claims about its clock domain. `reset_restores` is here rather than
    # in the run policy because it is a statement about the design's behaviour that a run
    # can find false, which is the definition of a task declaration.
    claims = dict(got.get("clock") or {})
    stray_claims = sorted(set(claims) - {"reset_restores", "edges"})
    if stray_claims:
        raise Refused(Refusal(
            "declarations-unknown-field",
            "this build has no meaning for these clock declarations",
            path=str(path), fields=stray_claims))
    if "reset_restores" in claims and not isinstance(claims["reset_restores"], bool):
        raise Refused(Refusal(
            "declarations-reset-restores-not-a-claim",
            "`reset_restores` is a claim about the design and is true or false; a value "
            "of another type is neither",
            path=str(path), got=repr(claims["reset_restores"])))
    edges = claims.get("edges", {})
    if type(edges) is not dict or any(
            type(domain) is not str or not domain
            or edge not in ("rising", "falling")
            for domain, edge in (edges.items() if type(edges) is dict else ())):
        raise Refused(Refusal(
            "declarations-clock-edge-invalid",
            "`clock.edges` maps each nonempty domain name to `rising` or `falling`; "
            "the active edge is task semantics and is not inferred from the RTL",
            path=str(path), got=repr(edges)))

    # After the endpoints are parsed, because what it checks is what they say.
    generated = _generated_provenance(path, dict(got.get("generated") or {}),
                                      endpoints, hand=dict(got.get("written_by_hand")
                                                           or {}), read_contract=read_contract)
    generated.update(interface_provenance)
    if edges and generated.get("written_by") != "hand":
        raise Refused(Refusal(
            "declarations-clock-edge-not-derived",
            "this generated manifest declares active clock edges, but its generator "
            "derives only endpoint fields; an edge that was "
            "not derived cannot borrow that generator's provenance",
            path=str(path), clocks=sorted(edges)))
    if "reset_restores" in claims and generated.get("written_by") != "hand":
        raise Refused(Refusal(
            "declarations-reset-claim-not-derived",
            "this generated manifest claims reset restoration, but its generator "
            "does not derive that semantic property",
            path=str(path)))

    return Declarations(endpoints=endpoints, generated=generated,
                        reset_restores=claims.get("reset_restores"), source=str(path),
                        clock_edges=edges, interface_contract=interface)


def _endpoint_block(path, name, block):
    out = {}
    for key, table in (("kind", _KINDS), ("matching", _MATCHING),
                       ("reset_policy", _RESET)):
        if key in block:
            value = table.get(str(block[key]).lower())
            if value is None:
                raise Refused(Refusal(
                    "declarations-unknown-value",
                    "this declaration names something this build has no meaning for, "
                    "and treating an unrecognised word as the default would be the "
                    "assumption the file exists to remove",
                    path=str(path), endpoint=name, field=key,
                    got=str(block[key]), known=sorted(table)))
            out[key] = value
    # **Counts, checked as counts.** `int(block[key])` took whatever was there, so
    # `response_deadline = -5` -- a deadline that has already elapsed before it starts --
    # went into the contract and decided verdicts. Each of these is a number of slots or
    # of transactions; a negative one describes no run, and `answers_per_transaction = 0`
    # is a channel that owes nothing, which is a different declaration than a ratio.
    _LOWEST = {"max_outstanding": 1, "response_deadline": 0,
               "answers_per_transaction": 1}
    for key in ("max_outstanding", "response_deadline", "answers_per_transaction"):
        if key in block:
            value = block[key]
            if not isinstance(value, int) or isinstance(value, bool) \
                    or value < _LOWEST[key]:
                raise Refused(Refusal(
                    "declarations-out-of-range",
                    f"`{key}` counts slots or transactions, so it is a whole number and "
                    f"at least {_LOWEST[key]}; this one describes no run this build can "
                    f"drive",
                    path=str(path), endpoint=name, field=key, got=repr(value),
                    lowest=_LOWEST[key]))
            out[key] = int(value)
    if "terminal_quiescence" in block:
        out["terminal_quiescence"] = bool(block["terminal_quiescence"])
    if "identifier" in block:
        out["identifier"] = str(block["identifier"])
    if "framing" in block:
        from .core.input_framing import InputFraming

        out["framing"] = InputFraming.from_dict(block["framing"])
    unknown = sorted(set(block) - set(out))
    if unknown:
        raise Refused(Refusal(
            "declarations-unknown-field",
            "this build has no meaning for these declarations, so a task that relies on "
            "them would be verified under different rules than it states",
            path=str(path), endpoint=name, fields=unknown))
    return out


def declared_contract(contract, declarations):
    """``contract`` with the task's declarations applied, endpoint by endpoint.

    Rebuilt rather than mutated: a contract's digest is its identity, and applying
    declarations to a frozen record in place is exactly the thing `Frozen` refuses.
    """
    if declarations is None:
        return contract
    if declarations.interface_contract is not None:
        if contract.digest != declarations.interface_contract.digest:
            raise Refused(Refusal(
                "declarations-interface-conflict",
                "the supplied contract differs from the task's explicit interface"))
        return declarations.interface_contract
    endpoints = []
    for endpoint in contract.endpoints:
        extra = declarations.for_endpoint(endpoint.name)
        if not extra:
            endpoints.append(endpoint)
            continue
        endpoints.append(Endpoint(
            endpoint.name, endpoint.direction, endpoint.fields, endpoint.adapter,
            clock=endpoint.clock,
            matching=extra.get("matching", endpoint.matching),
            identifier=extra.get("identifier", endpoint.identifier),
            max_outstanding=extra.get("max_outstanding", endpoint.max_outstanding),
            response_deadline=extra.get("response_deadline",
                                        endpoint.response_deadline),
            answers_per_transaction=extra.get("answers_per_transaction",
                                              endpoint.answers_per_transaction),
            terminal_quiescence=extra.get("terminal_quiescence",
                                          endpoint.terminal_quiescence),
            kind=extra.get("kind", endpoint.kind),
            reset_policy=extra.get("reset_policy", endpoint.reset_policy),
            framing=extra.get("framing", endpoint.framing)))
    # Runtime reset duration does not rewrite the task's clock or reset semantics.
    named_clocks = {clock.name for clock in contract.clocks}
    stray_clocks = sorted(set(declarations.clock_edges) - named_clocks)
    if stray_clocks:
        raise Refused(Refusal(
            "declarations-clock-not-in-contract",
            "these active-edge declarations name clock domains absent from the task "
            "interface, so they cannot define any admitted event",
            declared=stray_clocks, clocks=sorted(named_clocks),
            source=declarations.source))
    clocks = [ClockDomain(c.name, c.clock, c.reset,
                          reset_active_low=c.reset_active_low,
                          reset_hold_cycles=c.reset_hold_cycles,
                          reset_restores=(
                              c.reset_restores
                              if declarations.reset_restores is None
                              else declarations.reset_restores),
                          edge=declarations.clock_edges.get(c.name, c.edge))
              for c in contract.clocks]
    named = {e.name for e in contract.endpoints}
    stray = sorted(set(declarations.endpoints) - named)
    if stray:
        raise Refused(Refusal(
            "declarations-endpoint-not-in-contract",
            "these declarations are about endpoints this task's interface does not "
            "have, so whatever they were meant to say is not being applied to anything",
            declared=stray, endpoints=sorted(named), source=declarations.source))
    return TaskContract(
        endpoints=endpoints,
        clocks=clocks,
        memories=contract.memories,
        protocol_instances=contract.protocol_instances,
        observation_goals=contract.observation_goals,
        source=contract.source,
        authority=contract.authority,
    )


def contract_of_task(directory: str | Path, *, declarations: Declarations | None = None) -> TaskContract:
    """Resolve task facts before reading either artifact.

    Native interface.json files use the complete frozen interface without TOML.
    Historical TOML descriptors remain readable. Legacy manifests keep
    the spec-header convention plus endpoint declarations. One read shares the
    spec elaboration with reference-derived provenance validation; later calls
    read afresh. Already loaded declarations retain their existing snapshot.
    """
    directory = Path(directory)
    if declarations is None:
        return _task_facts_at(directory)[1]
    if type(declarations) is not Declarations:
        raise TypeError("task declarations must be an exact Declarations record")
    return _contract_with_declarations(declarations, lambda: _spec_contract(directory))


def _spec_contract(directory: Path) -> TaskContract:
    from .contract_of import contract_of_spec

    return contract_of_spec((directory / "spec.md").read_text(), directory.name)


def _contract_with_declarations(declarations: Declarations | None,
                                read_contract: Callable[[], TaskContract]) -> TaskContract:
    if declarations is not None and declarations.interface_contract is not None:
        return declarations.interface_contract
    return declared_contract(read_contract(), declarations)


def _task_facts_at(directory: str | Path, *, require_declarations: bool = False
                   ) -> tuple[Declarations | None, TaskContract | None]:
    """Read both task facts once; never reuse an elaboration across invocations."""
    from functools import cache

    directory = Path(directory)

    @cache
    def read_contract():
        return _spec_contract(directory)

    declarations = _declarations_at(directory, read_contract)
    if declarations is None and require_declarations:
        return None, None
    return declarations, _contract_with_declarations(declarations, read_contract)
