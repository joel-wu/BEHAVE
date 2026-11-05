"""Prepare fixed structural attribution once, then sample independent RTL slots.

The plan snapshots identities, triggers and match predicates, not signal readings.
Each occurrence still contributes independently to its public goal; a proven hit
dominates an unreadable sibling occurrence, but an unreadable occurrence is no miss.
"""

from ..core.frozen import Frozen
from .case_match import SwitchMatcher, matches
from .sigspec import rtlil_name, signal_value


def _events(events):
    return frozenset((
        'posedge' if edge == 'rising' else 'negedge' if edge == 'falling' else edge,
        rtlil_name(signal),
    ) for edge, signal in events)


class SiteSampler(Frozen):
    """Immutable replay plan; mutable catalogue builders cannot change it later."""

    __slots__ = ('_occurrences', '_prefixes', '_switches', '_triggers', 'goals')

    def __init__(self, sites, *, occurrence_goals=None):
        from .catalogue import goal_id_of

        occurrences, switches, indices, matchers = [], [], {}, {}
        # Each node extends exactly one ordered activation prefix. Occurrences
        # retain their own goal and trigger even when the entire prefix is shared.
        prefixes, prefix_indices = [None], {}
        for site in (sites.values() if hasattr(sites, 'values') else sites):
            goal = (goal_id_of(site) if occurrence_goals is None
                    else occurrence_goals[site.target])
            levels = list(zip(site.activation, site.activation_siblings))
            if site.kind != 'location':
                levels.append(((site.signal, site.outcome), site.siblings))
            prefix = 0
            for (signal, label), labels in levels:
                signal = site.read_signal(signal)
                labels = tuple(labels)
                # Retain the tuple with its matcher: identity cannot be recycled.
                identity = id(labels)
                if identity not in matchers:
                    matchers[identity] = labels, SwitchMatcher(labels)
                key = signal, identity
                if key not in indices:
                    indices[key] = len(switches)
                    switches.append((signal, *matchers[identity]))
                extension = prefix, indices[key], label
                if extension not in prefix_indices:
                    prefix_indices[extension] = len(prefixes)
                    prefixes.append(extension)
                prefix = prefix_indices[extension]
            triggers = (None if any(edge == 'always' for edge, _ in site.triggers)
                        else _events(site.scheduled_triggers))
            occurrences.append((goal, triggers, prefix))
        # A prefix is needed exactly when an eligible occurrence depends on it.
        # Group equal trigger sets once, then propagate those group bits to parents.
        # Node order is topological because each extension follows its parent.
        masks, trigger_indices, prepared = [0] * len(prefixes), {}, []
        for goal, triggers, prefix in occurrences:
            if triggers not in trigger_indices:
                trigger_indices[triggers] = len(trigger_indices)
            bit = 1 << trigger_indices[triggers]
            masks[prefix] |= bit
            prepared.append((goal, prefix, bit))
        for index in range(len(prefixes) - 1, 0, -1):
            masks[prefixes[index][0]] |= masks[index]
        self._occurrences, self._switches = tuple(prepared), tuple(switches)
        self._prefixes = (None, *(tuple(node) + (masks[index],)
                                 for index, node in enumerate(prefixes[1:], 1)))
        self._triggers = tuple(trigger_indices)
        self.goals = frozenset(goal for goal, _triggers, _prefix in occurrences)
        self._freeze()

    def sample(self, values, widths=None, *, active_events=None,
               include_combinational=True):
        active = None if active_events is None else _events(active_events)
        hits, pending, readings, decisions = set(), {}, {}, {}
        enabled = 0
        for index, triggers in enumerate(self._triggers):
            if (include_combinational if triggers is None else
                    active is None or bool(triggers & active)):
                enabled |= 1 << index
        if not enabled:
            return hits, pending
        verdicts = [None] * len(self._prefixes)
        verdicts[0] = (True, None)
        for cursor in range(1, len(self._prefixes)):
            parent, index, label, mask = self._prefixes[cursor]
            if not mask & enabled:
                continue
            verdict, why = verdicts[parent]
            if not verdict:
                verdicts[cursor] = (False, None)
                continue
            signal, labels, matcher = self._switches[index]
            if signal not in readings:
                try:
                    value, missing = signal_value(signal, values, widths)
                    readings[signal] = value, (None if missing is None else
                                               f"this reading does not carry {missing!r}")
                except (TypeError, ValueError) as exc:
                    readings[signal] = None, str(exc)
            value, missing = readings[signal]
            if missing is not None:
                verdicts[cursor] = verdict, why or missing
                continue
            if index not in decisions:
                decisions[index] = matcher.decision(value)
            table = decisions[index]
            try:
                got, bad = table[label]
            except KeyError:
                got, bad = matches(label, labels, value)
            if got is None:
                why = why or bad
            elif not got:
                # False decides the whole conjunction, even after an unknown;
                # otherwise retain the first unknown in source-prefix order.
                verdict, why = False, None
            verdicts[cursor] = verdict, why

        # Preserve occurrence order when choosing a public goal's first reason.
        for goal, prefix, mask in self._occurrences:
            if not mask & enabled:
                continue
            verdict, why = verdicts[prefix]
            if why is not None:
                pending.setdefault(goal, why)
            elif verdict:
                hits.add(goal)
        return hits, {goal: why for goal, why in pending.items() if goal not in hits}
