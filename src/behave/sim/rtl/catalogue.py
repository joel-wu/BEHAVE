"""Frontend declarations and legacy compiled-action diagnostics.

RTLArtifact uses the typed source catalogue: synthesis does not define its goal
denominator. The Site/RTLIL helpers remain for circuit consumers and historical
action-policy diagnostics during migration; their goals are not source events.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root
from behave.sim.core.source_rules import EMPTY_SOURCE_RULES

from ..core.frozen import Frozen
from ..core.refusal import Refusal, Refused
from ..strict_json import strict_json_loads
from ._rtlil_sites import (
    RTLIL_SITES_VERSION,
    pack_site_rows,
    rtlil_action_projection,
    rtlil_site_rows,
    rtlil_source_projection,
    unpack_site_rows,
)
from .case_match import invalid_label, match_identity, match_rule, match_schema
from .elaboration import SOURCE_FILENAME, compiler_command, elaboration_script

__all__ = [
    "CASE_ARM",
    "DECISION",
    "DEFAULT_STRUCTURAL_POLICY",
    "LOCATION",
    "MAX_ARMS_PER_SWITCH",
    "MAX_STRUCTURAL_GOALS",
    "MAX_STRUCTURAL_OCCURRENCES",
    "NO_MATCH_POLICY",
    "POLICY",
    "POLICY_VERSION",
    "Site",
    "StructuralGoalPolicy",
    "catalogue_manifest_of",
    "catalogue_manifest_with_execution_of",
    "catalogue_of",
    "family_of",
    "goal_id_of",
    "goal_ids_of_sites",
    "occurrence_key_of",
    "read_sites",
    "read_sites_with_execution",
    "shape_of",
    "sites_from_json",
    "sites_of",
    "sites_to_json",
]

#: Public RTL goals name source-projected actions and choices.  Elaboration may create
#: many independently checked occurrences of one goal; the manifest retains all of
#: them and a goal is hit when any occurrence runs.  This is the same distinction the
#: Behavior frontend makes between a public source site and its lowered occurrences.
POLICY = "source-projected-action"
POLICY_VERSION = "rtl-structural/10"

#: Every finite RTL switch arm is one structural coverage occurrence.  The old
#: ``decision`` family covered only binary switches and silently changed the
#: denominator with source shape.  Keep the old name as a source-compatibility alias;
#: new manifests carry only ``case-arm``.
CASE_ARM = "case-arm"
DECISION = CASE_ARM
LOCATION = "location"

#: Policy-owned resource bounds.  They are deliberately part of the policy identity:
#: raising either limit changes which frontend catalogues can exist and therefore is a
#: semantic policy change, not an implementation tuning knob.
MAX_ARMS_PER_SWITCH = defaults.SIM_RTL_SWITCH_ARMS
MAX_STRUCTURAL_GOALS = defaults.SIM_RTL_STRUCTURAL_GOALS
MAX_STRUCTURAL_OCCURRENCES = defaults.SIM_RTL_STRUCTURAL_OCCURRENCES
NO_MATCH_POLICY = "synthetic-no-match/1"


class StructuralGoalPolicy(Frozen):
    """Closed finite-switch policy used before the frontend manifest is frozen."""

    __slots__ = (
        "max_arms_per_switch", "max_structural_goals",
        "max_structural_occurrences", "no_match_policy", "version",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("StructuralGoalPolicy is a closed policy record")

    def __init__(
            self,
            max_arms_per_switch=MAX_ARMS_PER_SWITCH,
            max_structural_goals=MAX_STRUCTURAL_GOALS,
            max_structural_occurrences=MAX_STRUCTURAL_OCCURRENCES,
            *,
            no_match_policy=NO_MATCH_POLICY,
            version=POLICY_VERSION):
        for name, value in (
                ("max_arms_per_switch", max_arms_per_switch),
                ("max_structural_goals", max_structural_goals),
                ("max_structural_occurrences", max_structural_occurrences)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive exact integer")
        for name, value in (("no_match_policy", no_match_policy),
                            ("version", version)):
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} must be nonempty exact text")
        self.max_arms_per_switch = max_arms_per_switch
        self.max_structural_goals = max_structural_goals
        self.max_structural_occurrences = max_structural_occurrences
        self.no_match_policy = no_match_policy
        self.version = version
        self._freeze()

    @property
    def identity(self):
        return (
            f"{self.version}/{POLICY}"
            f";max-arms={self.max_arms_per_switch}"
            f";max-goals={self.max_structural_goals}"
            f";max-occurrences={self.max_structural_occurrences}"
            f";no-match={self.no_match_policy}")

    def as_dict(self):
        return {
            "version": self.version,
            "policy": POLICY,
            "max_arms_per_switch": self.max_arms_per_switch,
            "max_structural_goals": self.max_structural_goals,
            "max_structural_occurrences": self.max_structural_occurrences,
            "no_match_policy": self.no_match_policy,
        }


DEFAULT_STRUCTURAL_POLICY = StructuralGoalPolicy()

class Site(Frozen):
    """One elaborated control arm or action location and what activates it.

    ``target`` identifies one elaborated occurrence.  ``goal`` identifies the stable
    source-projected action or choice to which that occurrence contributes.
    """

    __slots__ = (
        "_activation_frames",
        "action",
        "activation",
        "depth",
        "input_aliases",
        "kind",
        "module",
        "outcome",
        "process",
        "siblings",
        "signal",
        "src",
        "switch_id",
        "triggers",
    )

    def __init__(self, module, process, signal, src, outcome, depth, activation,
                 siblings=(), activation_frames=(), switch_id=None, *,
                 kind=CASE_ARM, action=None, triggers=(("always", ""),), input_aliases=()):
        if kind not in (CASE_ARM, LOCATION):
            raise ValueError("an RTL structural site has an unknown kind")
        if kind == LOCATION:
            if type(action) is not str or not action:
                raise TypeError("an RTL location needs its nonempty elaborated action")
            if outcome != "entry" or siblings:
                raise ValueError("an RTL location has entry as its only event")
        elif action is not None:
            raise ValueError("an RTL case arm does not carry an action")
        self.kind = kind
        self.action = action
        self.module = module
        self.process = process
        self.signal = signal          # the switched expression, as RTLIL wrote it
        self.src = src                # file:line.col-line.col
        if switch_id is None:
            import hashlib

            fallback = repr(
                (module, process, signal, src, depth, tuple(activation)))
            switch_id = hashlib.blake2s(
                fallback.encode("utf-8"), digest_size=8).hexdigest()
        if type(switch_id) is not str or not switch_id:
            raise TypeError("an RTL site identity must be nonempty exact text")
        self.switch_id = switch_id
        if (type(triggers) not in (tuple, list) or not triggers
                or any(type(pair) not in (tuple, list) or len(pair) != 2
                       or any(type(value) is not str for value in pair)
                       for pair in triggers)):
            raise TypeError("an RTL site needs a nonempty list of trigger pairs")
        supported = {"always", "posedge", "negedge"}
        unknown = sorted({pair[0] for pair in triggers} - supported)
        if unknown:
            raise ValueError(
                f"an RTL site has unsupported runtime triggers {unknown}")
        if any(kind == "always" and signal for kind, signal in triggers) \
                or any(kind != "always" and not signal for kind, signal in triggers):
            raise ValueError("an RTL site has a malformed runtime trigger")
        self.triggers = tuple((kind, signal) for kind, signal in triggers)
        if (type(input_aliases) not in (tuple, list)
                or any(type(pair) not in (tuple, list) or len(pair) != 2
                       or any(type(name) is not str or not name.startswith(("\\", "$"))
                              or any(c.isspace() for c in name) for name in pair)
                       or pair[0] == pair[1] for pair in input_aliases)):
            raise ValueError("an RTL input alias needs two distinct whole-wire names")
        self.input_aliases = tuple(tuple(pair) for pair in input_aliases)
        aliases = dict(self.input_aliases)
        if (len(aliases) != len(self.input_aliases)
                or tuple(sorted(self.input_aliases)) != self.input_aliases
                or set(aliases) & set(aliases.values())):
            raise ValueError("RTL input aliases must be unique, sorted and terminal")
        self.outcome = outcome        # the case label, or "default"
        self.depth = depth
        #: The enclosing (signal, case) pairs, outermost first. A site with an empty
        #: activation runs whenever its process does; anything else is reached only
        #: when every pair holds, and **that is the whole point of the item**: a hit
        #: on an inactive site is not a hit.
        self.activation = tuple(tuple(pair) for pair in activation)
        #: Every label of the switch this site belongs to, this one included, in the
        #: order RTLIL wrote them.  It closes the finite match rule: default/no-match
        #: is the negation of every explicit sibling, and the provider's per-switch
        #: resource bound is checked against this complete arm list.
        #:
        #: Filled in when the switch closes rather than when the case is seen, because
        #: a case does not know its siblings yet.
        self.siblings = tuple(siblings)
        # One complete ordered label set per enclosing switch. Production decoding
        # shares immutable tuples; the direct construction API also accepts builders.
        self._activation_frames = tuple(tuple((frame or {}).get("labels", ()))
                                        for frame in activation_frames)
        self._freeze()

    @property
    def activation_siblings(self):
        """The complete ordered labels of each enclosing switch."""
        closed = []
        for index, (_signal, label) in enumerate(self.activation):
            frame = (self._activation_frames[index]
                     if index < len(self._activation_frames) else None)
            labels = frame or ()
            # Compatibility for directly constructed two-way Sites.  Production rows
            # always carry the closed activation frame and never use this inference.
            if not labels:
                labels = (("1'1", "default") if label == "default"
                          else (label, "default"))
            closed.append(labels)
        return tuple(closed)

    @property
    def target(self):
        """The stable identifier of this exact elaborated occurrence."""
        if self.kind == LOCATION:
            return (f"rtl-location:{self.module}:{self.src}:{self.path_id}"
                    f":{self.switch_id}:{self.match_id}:entry")
        return (f"rtl:{self.module}:{self.signal}:{self.src}:{self.outcome}"
                f":{self.path_id}:{self.switch_id}:{self.match_id}")

    @property
    def goal(self):
        """The public source-projected goal shared by elaborated copies."""

        return goal_id_of(self)

    @property
    def path_id(self):
        """A short stable digest of the activation chain.

        Stable in the sense that matters: it changes when the control structure above
        this site changes, which is a real change, and not when something unrelated
        earlier in the file moves.
        """
        import hashlib

        chain = "|".join(f"{s}={c}" for s, c in self.activation)
        return hashlib.blake2s(chain.encode(), digest_size=4).hexdigest()

    @property
    def match_id(self):
        """Digest of the ordered match semantics at this occurrence.

        Arm order is semantic when finite patterns overlap.  Enclosing sibling lists
        likewise determine the meaning of a default activation, so neither may be
        omitted from an occurrence identity.
        """
        return match_identity(self.siblings, self.activation_siblings)[0]

    def as_json(self):
        """One complete row, including enclosing default/no-match semantics."""
        return {"kind": self.kind, "action": self.action,
                "module": self.module, "process": self.process, "signal": self.signal,
                "switch_id": self.switch_id,
                "triggers": [list(pair) for pair in self.triggers],
                "input_aliases": [list(pair) for pair in self.input_aliases],
                "src": self.src, "outcome": self.outcome, "depth": self.depth,
                "activation": [list(pair) for pair in self.activation],
                "siblings": list(self.siblings),
                "activation_frames": [{"labels": list(frame)}
                                      for frame in self._activation_frames]}

    @classmethod
    def from_json(cls, got, *, _labels=None):
        """Read one exact site row. Tuples are restored for stable identity."""

        expected = {
            "kind", "action", "module", "process", "signal", "src", "outcome", "depth",
            "activation", "siblings", "activation_frames", "switch_id", "triggers", "input_aliases",
        }
        if type(got) is not dict or set(got) != expected:
            raise ValueError("an RTLIL site row has an invalid shape")
        labels = {} if _labels is None else _labels

        def read_labels(values):
            if type(values) is not list:
                raise ValueError("an RTLIL label set must be a list")
            cached = labels.get(id(values))
            if cached is not None and cached[0] is values:
                return cached[1]
            if any(type(label) is not str for label in values):
                raise ValueError("an RTLIL label set contains a non-string")
            frozen = tuple(values)
            labels[id(values)] = (values, frozen)
            return frozen

        if (got["kind"] not in (CASE_ARM, LOCATION)
                or (got["action"] is not None
                    and type(got["action"]) is not str)
                or type(got["module"]) is not str or not got["module"]
                or (got["process"] is not None
                    and type(got["process"]) is not str)
                or type(got["signal"]) is not str or not got["signal"]
                or type(got["switch_id"]) is not str or not got["switch_id"]
                or type(got["triggers"]) is not list or not got["triggers"]
                or any(type(pair) is not list or len(pair) != 2
                       or any(type(value) is not str for value in pair)
                       for pair in got["triggers"])
                or type(got["src"]) is not str or not got["src"]
                or type(got["outcome"]) is not str or not got["outcome"]
                or type(got["depth"]) is not int or got["depth"] < 0
                or type(got["activation"]) is not list
                or any(type(pair) is not list or len(pair) != 2
                       or any(type(value) is not str for value in pair)
                       for pair in got["activation"])
                or type(got["activation_frames"]) is not list
                or any(type(frame) is not dict or set(frame) != {"labels"}
                       for frame in got["activation_frames"])):
            raise ValueError("an RTLIL site row has invalid fields")
        if len(got["activation_frames"]) != len(got["activation"]):
            raise ValueError("an RTLIL site row has inconsistent activation frames")
        return cls(
            module=got["module"], process=got["process"], signal=got["signal"],
            src=got["src"], outcome=got["outcome"], depth=got["depth"],
            activation=tuple(tuple(pair) for pair in got.get("activation") or ()),
            siblings=read_labels(got["siblings"]),
            activation_frames=tuple({"labels": read_labels(frame["labels"])}
                                    for frame in got["activation_frames"]),
            switch_id=got["switch_id"], kind=got["kind"], action=got["action"],
            triggers=tuple(tuple(pair) for pair in got["triggers"]),
            input_aliases=got["input_aliases"])

    def read_signal(self, signal):
        """Read a compiler-proved input alias without changing source identity."""
        if not self.input_aliases:
            return signal
        aliases = dict(self.input_aliases)
        return " ".join(aliases.get(token, token) for token in signal.split())

    @property
    def scheduled_triggers(self):
        if not self.input_aliases:
            return self.triggers
        return tuple((edge, self.read_signal(signal)) for edge, signal in self.triggers)

    def active_for(self, events=None, *, include_combinational=True):
        """Whether this site's process runs in one event slot.

        ``events`` contains ``(edge, clock-pin)`` pairs.  ``None`` is the
        single-transition relation, whose one step already denotes execution of its
        admitted process.  Atomic products pass their actual coincident edge set.
        """

        from .control_condition import triggered

        return triggered(self.scheduled_triggers, events,
                         include_combinational=include_combinational)

    def as_dict(self):
        return {"target": self.target, "kind": self.kind,
                "action": self.action, "module": self.module,
                "process": self.process, "signal": self.signal,
                "switch_id": self.switch_id,
                "triggers": [list(pair) for pair in self.triggers],
                "src": self.src,
                "outcome": self.outcome, "depth": self.depth,
                "activation": [list(pair) for pair in self.activation]}

    def __repr__(self):
        under = " under " + " and ".join(f"{s}={c}" for s, c in self.activation) \
            if self.activation else ""
        if self.kind == LOCATION:
            return f"Site({self.action} at {self.src}{under})"
        return f"Site({self.signal}={self.outcome} at {self.src}{under})"


def read_sites(rtl_path, top, timeout=defaults.SIM_RTL_CATALOGUE_SECONDS):
    """Return the design's pre-proc control sites, reduced inside the RTL judge."""

    sites, _execution = read_sites_with_execution(rtl_path, top, timeout=timeout)
    return sites


def read_sites_with_execution(rtl_path, top, timeout=defaults.SIM_RTL_CATALOGUE_SECONDS):
    """Return closed site records and the isolation lineage that produced them."""
    sites, _source, execution = read_frontend_with_execution(rtl_path, top, timeout=timeout)
    return sites, execution


def read_frontend_with_execution(rtl_path, top, timeout=defaults.SIM_RTL_CATALOGUE_SECONDS):
    """One compiler preparation declares source outcomes and elaborated actions."""
    return _read_frontend(rtl_path, top, timeout, source_only=False)


def source_catalogue_with_execution_of(rtl_path, top, timeout=defaults.SIM_RTL_CATALOGUE_SECONDS,
                                       *, source_rules=EMPTY_SOURCE_RULES):
    """Public source-event inventory; synthesis does not define these goals."""
    _sites, catalogue, execution = _read_frontend(rtl_path, top, timeout, source_only=True,
                                                source_rules=source_rules)
    return catalogue, execution


def _read_frontend(rtl_path, top, timeout, *, source_only, source_rules=EMPTY_SOURCE_RULES):

    from ..core.catalogue import CatalogueManifest
    from .frontend_error import SourceRejected
    from .source_rules import REJECTION_EXIT, REJECTION_FILE, read_rejection
    from .tool_isolation import default_limits, run_workspace
    from .tool_protocol import (
        OutputNormalizer,
        ToolCommand,
        ToolGroup,
        ToolIsolationError,
        command_result,
        command_stream,
        require_completed,
    )

    if type(top) is not str or re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_$]*", top) is None:
        raise RuntimeError("the RTL top module is not a SystemVerilog identifier")
    timeout = int(timeout)
    if timeout <= 0:
        raise ValueError("the RTL frontend timeout must be positive")
    role = "rtl-source-catalogue" if source_only else "yosys-rtlil"
    outputs = ("source-goals.json",) if source_only else ("design.rtlil", "source-goals.json")
    with tempfile.TemporaryDirectory(prefix="behave-rtl-catalogue-", dir=scratch_root()) as temporary:
        workspace = Path(temporary)
        (workspace / SOURCE_FILENAME).write_bytes(Path(rtl_path).read_bytes())
        if source_only:
            command = ("/runtime/python/bin/python", "-S", "-m",
                       "behave.sim.rtl.shared_frontend", top, "--declare-source")
            if source_rules:
                (workspace / "source-rules.json").write_text(json.dumps(source_rules.as_dict()))
                command += ("--source-rules", "source-rules.json")
        else:
            (workspace / "catalogue.ys").write_text(
                elaboration_script(top) + "write_rtlil design.rtlil\n")
            command = (*compiler_command(top, "catalogue.ys"), "--source-catalogue")
        execution = require_completed(run_workspace(
            workspace,
            (ToolGroup((ToolCommand(
                role, command,
                timeout_s=timeout),)),),
            outputs=(*outputs, REJECTION_FILE),
            output_normalizers=(() if source_only else (OutputNormalizer(
                "design.rtlil", RTLIL_SITES_VERSION),)),
            role=role,
            runtime_profile="source-catalogue" if source_only else "catalogue",
            limits=default_limits(wall_seconds=timeout + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS),
        ))
        row = command_result(execution, role)
        if row.get("timed_out") or row.get("output_limited"):
            raise ToolIsolationError(
                "rtl-catalogue-resource-limit",
                "the structural frontend exceeded its declared time or output limit",
                execution)
        if row.get("returncode") == REJECTION_EXIT:
            try:
                rejected = read_rejection(execution.outputs[REJECTION_FILE])
            except (KeyError, ValueError, TypeError) as exc:
                raise ToolIsolationError(
                    "rtl-source-evidence-invalid",
                    "the language checker did not return its source rejection evidence",
                    execution) from exc
            raise SourceRejected(rejected.reason(execution))
        if row.get("returncode") == 1:
            # Yosys' structural subset is narrower than the simulator language.
            # Check rejected source with the declared simulator frontend; a valid
            # program outside this catalogue subset is a capability refusal, not 0.
            from .elaborate import schema_of_module

            schema_of_module((workspace / SOURCE_FILENAME).read_text(), top=top)
            diagnostic = command_stream(execution, role, "stderr")
            raise Refused(Refusal(
                "rtl-structural-frontend-unsupported",
                "the simulator accepts this source but the structural frontend does not",
                diagnostic=diagnostic.decode("utf-8", errors="replace"),
                execution=execution.as_dict()))
        if row.get("returncode") != 0 or not set(outputs) <= execution.outputs.keys():
            raise ToolIsolationError(
                "rtl-catalogue-process-failed",
                "the frontend did not produce its requested declarations", execution)
        try:
            source = CatalogueManifest.from_dict(strict_json_loads(
                execution.outputs["source-goals.json"], what="source goal declarations"))
            if source_only:
                return (), source, execution
            payload = execution.outputs["design.rtlil"]
            document = strict_json_loads(
                payload, what="the normalized RTLIL site record")
            if type(document) is not dict or set(document) != {"version", "labels", "sites"} \
                    or document["version"] != RTLIL_SITES_VERSION:
                raise ValueError("the normalized RTLIL site record has an invalid shape")
            labels = {}
            sites = tuple(Site.from_json(row, _labels=labels) for row in
                          unpack_site_rows({key: document[key]
                                            for key in ("labels", "sites")}))
        except (TypeError, ValueError, RecursionError) as error:
            raise ToolIsolationError(
                "rtl-catalogue-output-invalid",
                "the frontend returned invalid site or source declaration records", execution) from error
        return sites, source, execution


def sites_of(rtlil):
    """Parse sites from trusted RTLIL text, using the judge's exact parser."""

    labels = {}
    return [Site.from_json(row, _labels=labels) for row in rtlil_site_rows(rtlil)]


def sites_to_json(sites):
    """Canonical target order and shared label tables; preserve None and empty maps."""
    if sites is None:
        return None
    pairs = (sites.items() if hasattr(sites, "items")
             else ((s.target, s) for s in sites))
    pairs = list(pairs)
    if not pairs:
        return {}
    if any(type(target) is not str or type(site) is not Site
           or target != site.target for target, site in pairs):
        raise ValueError("an RTL site catalogue key does not match its site")
    pairs.sort(key=lambda pair: pair[0])
    if len({target for target, _site in pairs}) != len(pairs):
        raise ValueError("an RTL site catalogue has duplicate occurrences")
    return {"version": "rtl-site-map/2", "targets": [key for key, _site in pairs],
            **pack_site_rows(site.as_json() for _key, site in pairs)}


def sites_from_json(got):
    """The inverse of `sites_to_json`."""
    if got is None:
        return None
    if got == {}:
        return {}  # The no-catalogue driver plan has no site payload.
    if type(got) is not dict or set(got) != {"version", "targets", "labels", "sites"} \
            or got["version"] != "rtl-site-map/2" \
            or type(got["targets"]) is not list \
            or any(type(key) is not str or not key for key in got["targets"]):
        raise ValueError("an RTL site catalogue has an invalid shape or version")
    rows = unpack_site_rows({key: got[key] for key in ("labels", "sites")})
    keys = got["targets"]
    if len(rows) != len(keys) or len(keys) != len(set(keys)):
        raise ValueError("an RTL site catalogue has inconsistent target keys")
    labels = {}
    sites = {key: Site.from_json(row, _labels=labels) for key, row in zip(keys, rows)}
    if any(target != site.target for target, site in sites.items()):
        raise ValueError("an RTL site catalogue key does not match its site")
    return sites


def catalogue_of(rtl_path, top):
    """The control sites of ``rtl_path``, keyed by target identifier."""
    sites = read_sites(rtl_path, top)
    out = {}
    for site in sites:
        # A repeated target means two sites the identifier cannot tell apart, which
        # would make a probe result ambiguous. Reported rather than merged.
        if site.target in out:
            out[site.target] = None
        else:
            out[site.target] = site
    return {k: v for k, v in out.items() if v is not None}, \
        [k for k, v in out.items() if v is None]


def family_of(site):
    """The structural family fixed by the elaborated occurrence kind.

    Arity does not change the family.  Written binary, multiway and single-arm cases,
    defaults, and the synthetic no-match outcome all ask the same question: did this
    finite arm run?  A site without its closed sibling list is malformed input, not a
    smaller denominator.
    """
    if type(site) is not Site:
        raise TypeError("an RTL structural family requires an exact Site")
    if site.kind == LOCATION:
        return LOCATION
    if not site.siblings:
        return Refusal(
            "rtl-structural-site-not-closed",
            "a structural occurrence has no closed switch-arm list")
    return CASE_ARM


def shape_of(site):
    """The site's switch shape, retained as reporting metadata."""
    if site.kind == LOCATION:
        return LOCATION
    labels = site.siblings
    if not labels:
        return "no-labels"
    if len(labels) == 1:
        return "single"
    if len(labels) == 2:
        return "binary-with-default" if "default" in labels else "binary-no-default"
    return "multiway"


def _public_site_projection(site):
    """Finite JSON coordinates of the source site behind one occurrence."""

    if type(site) is not Site:
        raise TypeError("an RTL public goal requires an exact Site")
    triggers = tuple(sorted(site.triggers))
    if site.kind == LOCATION:
        action = rtlil_action_projection(site.action)
        if action is None:
            raise ValueError(
                "an RTL location does not denote an effective design-visible action")
        return (
            site.module, LOCATION, site.src, action, "entry", triggers,
        )
    return (
        site.module,
        CASE_ARM,
        site.src,
        rtlil_source_projection(site.signal),
        site.outcome,
        tuple(site.siblings),
        triggers,
    )


def goal_id_of(site):
    """Public goal of one elaborated RTL occurrence.

    Generated SSA ordinals and concrete loop indices are absent from this name.  They
    remain in :func:`occurrence_key_of`, so merging public goals never merges evidence.
    """

    import hashlib
    import json

    projection = _public_site_projection(site)
    digest = hashlib.blake2s(
        json.dumps(projection, ensure_ascii=True, separators=(",", ":"))
        .encode("utf-8"),
        digest_size=10,
    ).hexdigest()
    prefix = "rtl-location" if site.kind == LOCATION else "rtl-choice"
    event = "entry" if site.kind == LOCATION else site.outcome
    return f"{prefix}:{site.module}:{site.src}:{digest}:{event}"


def goal_ids_of_sites(sites):
    """The public goal set represented by an iterable or occurrence mapping."""

    values = sites.values() if hasattr(sites, "values") else sites
    return frozenset(goal_id_of(site) for site in values)


def occurrence_key_of(site):
    """The canonical elaborated occurrence behind one structural goal.

    The public goal identifier is report-facing.  This key separately records the
    elaborated process, signal and control-tree path that emits its hit, so the exact
    relation and concrete reader can prove they attributed the same occurrence without
    parsing the public name.
    """

    from ..core.catalogue import OccurrenceKey
    from ..core.record_value import canonical_json

    if type(site) is not Site:
        raise TypeError("an RTL occurrence key requires an exact Site")
    trigger_key = ";".join(
        f"{kind}:{signal or '*'}" for kind, signal in site.triggers)
    # The complete ordered image is retained in the artifact's executable sites.
    # A full digest binds it here without quadratic copies in the public manifest.
    match_key = "match-sha256:" + match_identity(
        site.siblings, site.activation_siblings)[1]
    coordinates = (
        site.module, site.process or "<module>", site.kind, site.signal,
        site.action or "<case-arm>",
        site.src, site.depth, site.path_id, site.switch_id,
        match_key,
        trigger_key, "input-aliases:" + canonical_json(site.input_aliases))
    return OccurrenceKey(
        "rtl-structural", coordinates,
        "entry" if site.kind == LOCATION else site.outcome or "default")


def _switch_key(site):
    """The exact elaborated switch shared by one closed set of arm rows."""

    return (site.module, site.process, site.signal, site.switch_id)


def _validate_structural_sites(sites, policy):
    """Validate the whole provider input before any manifest row is constructed."""

    if type(policy) is not StructuralGoalPolicy:
        raise TypeError("the RTL structural catalogue needs a StructuralGoalPolicy")
    sites = tuple(sites)
    if len(sites) > policy.max_structural_occurrences:
        raise Refused(Refusal(
            "rtl-structural-occurrence-policy-bound",
            "the elaborated occurrence relation exceeds its declared finite bound; "
            "the whole frontend catalogue is refused rather than truncated",
            occurrences=len(sites), limit=policy.max_structural_occurrences,
            policy=policy.identity))
    switches = {}
    for site in sites:
        if type(site) is not Site:
            raise TypeError("an RTL structural catalogue contains a non-Site row")
        if site.kind == LOCATION:
            if rtlil_action_projection(site.action) is None:
                raise Refused(Refusal(
                    "rtl-location-not-an-effective-action",
                    "an RTL location occurrence is compiler bookkeeping rather than "
                    "an effective write to a design-visible signal",
                    occurrence=site.target))
            continue
        switches.setdefault(_switch_key(site), []).append(site)
        if len(site.siblings) > policy.max_arms_per_switch:
            raise Refused(Refusal(
                "rtl-switch-arm-policy-bound",
                "an elaborated switch exceeds the declared finite arm bound; the "
                "whole frontend catalogue is refused rather than truncated",
                switch=site.signal, arms=len(site.siblings),
                limit=policy.max_arms_per_switch, policy=policy.identity))

    for key, group in switches.items():
        sibling_sets = {site.siblings for site in group}
        trigger_sets = {site.triggers for site in group}
        if len(sibling_sets) != 1 or len(trigger_sets) != 1:
            raise Refused(Refusal(
                "rtl-structural-switch-inconsistent",
                "the arms of one elaborated switch disagree about its closed arm set "
                "or process trigger",
                switch=key[2]))
        siblings = group[0].siblings
        defaults = siblings.count("default")
        no_matches = siblings.count("no-match")
        if defaults > 1 or no_matches > 1 or (defaults and no_matches):
            raise Refused(Refusal(
                "rtl-structural-switch-inconsistent",
                "a finite switch must have at most one default or synthetic no-match "
                "arm, never both",
                switch=key[2]))
        if not defaults and no_matches != 1:
            raise Refused(Refusal(
                "rtl-structural-no-match-missing",
                "a switch without a default must carry exactly one synthetic no-match "
                "occurrence before its catalogue is frozen",
                switch=key[2], policy=policy.no_match_policy))
        outcomes = [site.outcome for site in group]
        if (len(outcomes) != len(set(outcomes)) or len(outcomes) != len(siblings)
                or set(outcomes) != set(siblings)):
            raise Refused(Refusal(
                "rtl-structural-switch-inconsistent",
                "the emitted occurrences do not equal the switch's closed arm set",
                switch=key[2]))
        unreadable = invalid_label(siblings)
        if unreadable is not None:
            _kind, _payload, why = match_rule(unreadable, siblings)
            raise Refused(Refusal(
                "rtl-structural-arm-unreadable",
                "a finite switch arm has no exact shared concrete/symbolic match "
                "rule, so the whole frontend catalogue is refused",
                switch=key[2], outcome=unreadable, rule_reason=why))
    public_goals = goal_ids_of_sites(sites)
    if len(public_goals) > policy.max_structural_goals:
        raise Refused(Refusal(
            "rtl-structural-goal-policy-bound",
            "the source-projected RTL catalogue exceeds its declared finite goal "
            "bound; the whole frontend catalogue is refused rather than truncated",
            goals=len(public_goals), occurrences=len(sites),
            limit=policy.max_structural_goals, policy=policy.identity))
    return sites


def _goal_catalogue_from_sites(sites, policy=DEFAULT_STRUCTURAL_POLICY):
    """Declare every validated occurrence or refuse before freezing a manifest."""

    import collections

    from ..core import catalogue as core_catalogue
    sites = _validate_structural_sites(sites, policy)
    by_target = collections.defaultdict(list)
    for site in sites:
        by_target[site.target].append(site)

    collisions = {}
    declared_sites = {}
    for target, group in by_target.items():
        if len(group) > 1:
            collisions[target] = {
                "sites": len(group),
                "declarable": False,
                "why": (f"{len(group)} sites share this identifier, "
                        f"shapes {sorted({shape_of(s) for s in group})}"),
            }
            continue
        declared_sites[target] = group[0]

    by_goal = collections.defaultdict(list)
    for site in declared_sites.values():
        by_goal[goal_id_of(site)].append(site)

    goals = {}
    for target, group in by_goal.items():
        projections = {_public_site_projection(site) for site in group}
        if len(projections) != 1:
            collisions[target] = {
                "sites": len(group),
                "declarable": False,
                "why": "distinct public projections share one digest",
            }
            continue
        site, family = group[0], family_of(group[0])
        if isinstance(family, Refusal):
            raise Refused(family)
        rule = (["activation", None] if site.kind == LOCATION
                else match_schema(site.outcome, site.siblings))
        goals[target] = core_catalogue.Goal(
            target,
            core_catalogue.COVERAGE,
            family=family,
            provider="rtl-structural",
            event="entry" if site.kind == LOCATION else site.outcome,
            provenance={
                "module": site.module,
                "signal": (rtlil_action_projection(site.action)
                           if site.kind == LOCATION
                           else rtlil_source_projection(site.signal)),
                "src": site.src,
                "triggers": [list(pair) for pair in site.triggers],
                "action": (rtlil_action_projection(site.action)
                           if site.kind == LOCATION else None),
                "outcome": site.outcome,
                "occurrences": len(group),
                "match_rule": rule,
            },
            policy=policy.identity)
    if collisions:
        first = min(collisions)
        raise Refused(Refusal(
            "rtl-structural-identifier-collision",
            "distinct elaborated rows map to one structural identifier, so their "
            "evidence cannot be reconciled with the manifest",
            first=first,
            count=len(collisions)))
    return goals, declared_sites


def catalogue_manifest_of(
        rtl_path, top, *, policy=DEFAULT_STRUCTURAL_POLICY,
        timeout=defaults.SIM_RTL_CATALOGUE_SECONDS):
    """Build the structural manifest and its elaborated sites in one RTLIL pass."""

    manifest, sites, _execution = \
        catalogue_manifest_with_execution_of(
            rtl_path, top, policy=policy, timeout=timeout)
    return manifest, sites


def catalogue_manifest_with_execution_of(
        rtl_path, top, *, policy=DEFAULT_STRUCTURAL_POLICY,
        timeout=defaults.SIM_RTL_CATALOGUE_SECONDS):
    """Build one manifest and retain the exact compiler policy that produced it."""

    from ..core.catalogue import CatalogueManifest, merge_catalogues

    sites, source, execution = read_frontend_with_execution(rtl_path, top, timeout=timeout)
    goals, declared_sites = _goal_catalogue_from_sites(
        sites, policy)
    manifest = CatalogueManifest(
        tuple(goals.values()),
        occurrences=tuple(
            (occurrence_key_of(site), goal_id_of(site))
            for site in declared_sites.values()))
    # Failure events are source events, including constant/dead assertions.
    # Keep the current structural coverage policy; it is not inferred from
    # whichever source outcomes happen to survive compiler simplification.
    return merge_catalogues(manifest, source), declared_sites, execution
