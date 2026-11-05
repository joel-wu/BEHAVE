"""Optional native observations; not reset-owned state or scoring evidence.

The source compiler supplies physical storage names and widths. The replay loop
samples them before a queue entry's first presentation, at the existing quiet
boundary. Explicit analysis also retains pre-HDL and per-slot observations.
No simulator event, reset or second execution is introduced by capture.
"""

from ..core.refusal import Refusal, Refused

VERSION = "rtl-source-boundaries/5"
HISTORY_VERSION = "rtl-source-history/1"
ObservationField = tuple[str | tuple[str | int, ...], int | dict]


def decode_fields(fields):
    """Restore segmented compiler paths after JSON, retaining literal identifiers."""
    return tuple((name if isinstance(name, str) else tuple(name), shape) for name, shape in fields)


def observation_fields(document):
    """Physical variables and referenced nets in the elaborated design.

    Unsupported value types and storage without a physical binding stay outside
    this capability. Query binding must still require *all* its needed values;
    a missing field is not an invitation to supply a default.
    """
    from .source_integral import typed_symbols
    from .source_storage import storage_bindings
    from .source_values import source_type

    symbols = typed_symbols(document)
    try:
        bindings = storage_bindings(document, symbols=symbols)
    except Refused:
        # This is the optional query representation, not the native goal
        # observer. The source worker reports the same unsupported storage
        # ownership if queried; it must not stop otherwise executable baselines.
        return ()
    referenced = set()

    def visit(node):
        if isinstance(node, dict):
            if node.get("kind") in {"NamedValue", "HierarchicalValue"}:
                referenced.add(node["symbol"].split(" ", 1)[0])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(document["design"])
    # The exact startup layout includes physical variables even when no
    # expression reads them (for example an unused next-state register).
    # Observe those actual declarations too; never invent their initial bits.
    referenced.update(identity for identity in bindings
                      if symbols[identity].get("kind") == "Variable")
    fields = {}
    for identity in referenced:
        storage = bindings.get(identity)
        if storage is None or storage.observation is None:
            continue
        try:
            kind = source_type(symbols[identity]["type"], symbols)
        except Refused:
            continue
        fields[storage.observation] = kind.observation
    return tuple(sorted(fields.items(), key=lambda pair: repr(pair[0])))


class BoundaryCapture:
    """Resolve handles once for local queries and optional whole-history audit."""

    def __init__(self, dut, fields, *, paths=()):
        from .source_handles import exact_handle, path_handle, width_of

        self.dut = dut
        self.fields = decode_fields(fields)
        paths = {name if isinstance(name, str) else tuple(name):
                 None if path is None else tuple(path) for name, path in paths}

        def bind(handle, shape):
            if type(shape) is int:
                return handle if width_of(handle) == shape else None
            left, right = shape["left"], shape["right"]
            step = 1 if left <= right else -1
            return tuple(bind(exact_handle(handle, index), shape["element"])
                         for index in range(left, right + step, step))

        self.handles = [bind(path_handle(dut, paths.get(name,
                            (name,) if isinstance(name, str) else name)), shape)
                        for name, shape in self.fields]
        self.presented = set()
        self.samples = []
        self.history = None
        self.history_handles = {}
        self.paths = paths

    def start_history(self, artifact, contract, *, input_aliases):
        """Read before the first yield; never lower a symbolic model or write DUT bits.

        This compiler observation superset is not a register-deposit capability.
        The exact owner selects and validates its required layout after scoring.
        Optional failure remains evidence, not an ordinary replay failure.
        """
        from .initial_request import InitialStateCoordinates
        from .replay_startup import capture_startup_state
        from .source_handles import physical_name
        from .startup_state import startup_pin_widths

        self.history = dict(version=HISTORY_VERSION, startup=None, refusal=None,
                            widths={}, initial={}, sampled=[], states=[])
        try:
            pins = startup_pin_widths(contract)
            widths, paths = {}, {}

            def leaves(name, shape, handle, path):
                if type(shape) is int:
                    key = physical_name(name)
                    if key in pins:
                        return
                    if key in widths:
                        raise ValueError('source history has ambiguous storage names')
                    widths[key], paths[key], self.history_handles[key] = shape, path, handle
                else:
                    step = 1 if shape['left'] <= shape['right'] else -1
                    for position, index in enumerate(range(shape['left'], shape['right'] + step, step)):
                        leaves((*name, index), shape['element'], handle[position],
                               None if path is None else (*path, index))

            for (name, shape), handle in zip(self.fields, self.handles, strict=True):
                name = (name,) if isinstance(name, str) else name
                lookup = name[0] if len(name) == 1 else name
                leaves(name, shape, handle, self.paths.get(lookup, name))
            self.history['widths'] = widths
            layout = InitialStateCoordinates(artifact, widths, startup_pins=pins)
            self.history['startup'] = capture_startup_state(
                layout, self.dut, self.history_handles, paths=paths,
                input_aliases=input_aliases, observations=True).as_dict()
        except (Refused, TypeError, ValueError, KeyError) as exc:
            self.history['refusal'] = (exc.refusal.as_dict() if isinstance(exc, Refused) else
                Refusal('rtl-source-history-capture-unavailable', str(exc)).as_dict())

    def snapshot(self, phase):
        from .source_handles import read_handle

        if self.history is None or self.history['refusal'] is not None:
            return
        values = {name: read_handle(handle) for name, handle in self.history_handles.items()}
        if phase == 'initial':
            self.history[phase] = values
        else:
            self.history[phase].append(values)

    def observe(self, slot, presentations, pointers):
        from .source_handles import read_handle

        def read(handle):
            return [read(part) for part in handle] if isinstance(handle, tuple) else read_handle(handle)

        current = {(name, pointers[name]) for name, payload in presentations.items()
                   if payload is not None}
        if current - self.presented:
            self.samples.append([slot, [read(handle) for handle in self.handles]])
            self.presented.update(current)

    def as_dict(self):
        image = {"version": VERSION, "fields": [list(pair) for pair in self.fields],
                 "samples": self.samples}
        if self.history is not None:
            image['history'] = self.history
        return image


def boundaries_of(image):
    """Decode query metadata only when search uses it, not as a score gate."""
    if image is None:
        return {}
    if image.get("version") != VERSION:
        raise Refused(Refusal("rtl-source-boundaries-version",
            "source query boundaries require the supported native observation format"))
    names = tuple(name for name, _width in decode_fields(image["fields"]))
    return {slot: dict(zip(names, values, strict=True)) for slot, values in image["samples"]}


def source_history_prefix(trace, horizon):
    """Derive a fixed executed window, never a completed workload or score.

    Keep queues, startup, clock timing and memory policy unchanged. All selected
    records and native phases are original observations; the ordinary exact
    audit still checks their source relation and terminal monitor. A shorter
    input record is never padded and an incomplete run is never promoted.
    """
    from ..core.clock_schedule import GlobalSchedule
    from ..core.record_value import thaw_record_value
    from ..core.stimulus import (
        AuditedTrace, Completion, Stimulus, contract_of_domain, trace_domain,
    )
    from .native_observation import VERSION as NATIVE_VERSION

    if type(trace) is not AuditedTrace or type(horizon) is not int or horizon < 1:
        raise TypeError('a source window needs a trace and a positive slot count')
    size = len(trace.stimulus.slots)
    if size <= horizon or trace.completion is not Completion.NORMAL:
        return trace
    if trace.source_evidence is None:
        return trace  # No source observation to project; retain ordinary admission.

    native = dict(trace.source_evidence)
    fields = {'version', 'plan', 'execution', 'stimulus', 'intervals'}
    whole_cycle = native.get('version') == NATIVE_VERSION
    if whole_cycle:
        fields.add('completed_steps')
    if set(native) != fields or native.get('version') not in {
            NATIVE_VERSION, 'rtl-native-observation/1', 'rtl-native-observation/2'} \
            or native['stimulus'] != trace.stimulus.fingerprint \
            or len(native['intervals']) != 1 + 3 * size \
            or (whole_cycle and native['completed_steps'] != size):
        raise Refused(Refusal('rtl-record-prefix-native-binding-invalid',
            'a source window requires complete native phases bound to its original stimulus'))

    original = trace.stimulus
    clock = original.clock_schedule
    schedule = None if clock is None else GlobalSchedule(clock.timings, clock.slots[:horizon])
    stimulus = Stimulus(dict(original.queues), original.slots[:horizon],
        memory_policy=original.memory_policy, initial_state=original.initial_state,
        clock_schedule=schedule, memory_environment=original.memory_environment,
        source=f'analysis-prefix:{horizon}:{original.fingerprint}')
    native['stimulus'] = stimulus.fingerprint
    native['intervals'] = native['intervals'][:1 + 3 * horizon]
    if whole_cycle:
        native['completed_steps'] = horizon

    boundaries = None if trace.query_boundaries is None else dict(trace.query_boundaries)
    if boundaries is not None:
        if boundaries.get('version') != VERSION:
            raise Refused(Refusal('rtl-record-prefix-history-invalid',
                'a source window requires a supported passive history'))
        if 'history' in boundaries:
            history = dict(boundaries['history'])
            if any(len(history[name]) != size for name in ('sampled', 'states')):
                raise Refused(Refusal('rtl-record-prefix-history-invalid',
                    'passive history must cover the original trace before projection'))
            for name in ('sampled', 'states'):
                history[name] = history[name][:horizon]
            boundaries['history'] = history
        if any(type(row) not in (tuple, list) or len(row) != 2
               or type(row[0]) is not int or not 0 <= row[0] < size
               for row in boundaries['samples']):
            raise Refused(Refusal('rtl-record-prefix-history-invalid',
                'passive samples must name slots in the original trace'))
        boundaries['samples'] = tuple(row for row in boundaries['samples'] if row[0] < horizon)

    return AuditedTrace(stimulus, trace.initial_state, trace.records[:horizon],
        Completion.NORMAL, trace_domain(contract_of_domain(trace.domain), horizon=horizon,
            adapter_version='rtl-analysis-prefix/1'), trace.artifact,
        source_evidence=native, query_boundaries=boundaries,
        initialization=None if trace.initialization is None else thaw_record_value(trace.initialization))


def source_history_view(trace, state_names):
    """Validate/project passive evidence for analysis; never mutate the scored run.

    Explicit construction replays already carry the exact owner's layout. An
    ordinary run instead keeps a compiler observation superset, including nets.
    Only the bound exact owner supplies the register names selected here; its
    request codec independently checks their physical widths and pin layout.
    """
    from ..core.record_value import thaw_record_value
    from ..core.stimulus import AuditedTrace, SlotRecord
    from .initial_state import RTLInitialState
    from .startup_state import RTLStartupState

    if trace.initialization is not None:
        return trace
    image = thaw_record_value(trace.query_boundaries)
    history = image.get('history') if isinstance(image, dict) and image.get('version') == VERSION else None
    if history is None:
        raise Refused(Refusal('rtl-record-construction-unrecorded',
            'the raw run has no passive pre-HDL history; settled state is not a startup choice'))
    try:
        if type(history) is not dict or set(history) != {
                'version', 'startup', 'refusal', 'widths', 'initial', 'sampled', 'states'} \
                or history['version'] != HISTORY_VERSION:
            raise ValueError('source history requires its complete versioned shape')
        if history['refusal'] is not None:
            reason = history['refusal']
            raise Refused(Refusal('rtl-record-construction-unavailable',
                'passive construction capture was unavailable', capture=reason))
        startup = RTLStartupState.from_dict(history['startup'])
        widths = history['widths']
        startup.registers.check_binding(trace.artifact, widths, widths)
        names = set(state_names)
        if not names <= widths.keys():
            raise ValueError('passive history omits required compiler storage')
        selected = {name: widths[name] for name in sorted(names)}

        def state(values, existing):
            if type(values) is not dict or set(values) != set(widths):
                raise ValueError('source history needs every declared observation field')
            got = RTLInitialState.from_values(trace.artifact, selected,
                                              {name: values[name] for name in selected}).values()
            if any(name in got and got[name] != value for name, value in existing):
                raise ValueError('passive history contradicts the mandatory native observation')
            return got

        if len(history['sampled']) != len(trace.records) or len(history['states']) != len(trace.records):
            raise ValueError('source history must retain every control and successor boundary')
        initial = state(history['initial'], trace.initial_state)
        records = tuple(SlotRecord(accepted=record.accepted,
            state=state(successor, record.state), environment=record.environment,
            outputs=dict(record.outputs) | state(sampled, record.outputs),
            hits=record.hits, monitor=record.monitor)
            for record, sampled, successor in zip(trace.records, history['sampled'],
                                                   history['states'], strict=True))
        physical = startup.registers.values()
        choice = RTLStartupState(RTLInitialState.from_values(trace.artifact, selected,
            {name: physical[name] for name in selected}), startup.pins.fields)
        return AuditedTrace(trace.stimulus, initial, records, trace.completion,
            trace.domain, trace.artifact, source_evidence=thaw_record_value(trace.source_evidence),
            request=trace.request, query_boundaries=image, initialization=choice.as_dict())
    except (TypeError, ValueError, KeyError) as exc:
        raise Refused(Refusal('rtl-source-history-invalid', str(exc))) from exc
