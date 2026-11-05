"""Recombine observed state writers before asking expensive goal queries.

This is a Search proposal heuristic, not a reachability proof. A source-audited
write is useful material, but moving it to another state may invalidate it. The
ordinary replay must check every proposed sequence again. No artificial goals
are added to either catalogue, and no task-specific inputs are introduced.
"""

import copy
import hashlib

from behave.isolation.preparation_control import check_preparation

from ...symbolic.uniform_array import UniformArray
from ...core.record_value import canonical_json


def recipe_token(updates):
    return hashlib.sha256(canonical_json((tuple(item['id'] for item, _ in updates),
        [values for _, values in updates])).encode()).hexdigest()


def recipe_image(binding, recipes):
    """Input-only continuation data; never a state, hit or solver answer."""
    if recipes is None:
        return None
    return dict(binding=binding, recipes=[dict(request=request.request().as_dict(),
        updates=[[identity, copy.deepcopy(values)] for identity, values in recipe])
        for request, recipe in recipes])


def restore_recipes(image, binding, windows, proc, manifest, available):
    """Rebind untrusted input recipes to this freshly audited exact trace.

    Even a well-shaped recipe only proposes an input. The ordinary candidate
    domain check and native replay remain mandatory; no restored record can
    supply state, predicates, coverage or a satisfiability verdict.
    """
    from ...generation_protocol import (ProbeRequest)
    from ...generation_protocol import (QueryTemplate)

    if (type(image) is not dict or set(image) != {'binding', 'recipes'}
            or image['binding'] != binding or type(image['recipes']) is not list):
        raise ValueError('preparation plan belongs to another source or trace')
    epochs = {}
    for window in windows.values():
        if window[0]['position'] == 0 and len(window) > len(epochs.get(window[0]['id'], ())):
            epochs[window[0]['id']] = window
    markers = {logical for framing, projection in proc.input_framing
               for public, logical in projection if public in framing.fields}
    recipes, issued = [], set()
    for row in image['recipes']:
        if type(row) is not dict or set(row) != {'request', 'updates'}:
            raise ValueError('a preparation recipe contains only its request and inputs')
        request, updates = ProbeRequest.from_dict(row['request']), row['updates']
        if (type(updates) is not list or not updates or any(type(pair) is not list
                or len(pair) != 2 or type(pair[0]) is not str or type(pair[1]) is not dict
                for pair in updates)):
            raise ValueError('a preparation recipe has exact input updates')
        window = epochs.get(updates[0][0], ())[:len(updates)]
        if len(window) != len(updates) or any(identity != item['id']
                or set(values) != set(item['input'])
                or any(type(value) not in (int, bool) for value in values.values())
                or any(values[name] != item['input'][name] for name in markers)
                for (identity, values), item in zip(updates, window)):
            raise ValueError('a preparation recipe must preserve its audited epoch and framing')
        restored = tuple((item, values) for item, (_, values) in zip(window, updates))
        expected = QueryTemplate(request.targets, 'state-setup:' + recipe_token(restored),
            tuple(field for item in window for name, field in item['bindings'] if name not in markers),
            request.rank, catalogue_digest=manifest.digest())
        if (request.as_dict() != expected.request().as_dict() or not set(request.targets) <= set(available)
                or request.identity in issued):
            raise ValueError('a preparation request must bind its exact input recipe and catalogue')
        issued.add(request.identity)
        recipes.append((expected, tuple((identity, copy.deepcopy(values)) for identity, values in updates)))
    return tuple(recipes)


def changed_cells(before, after, path=()):
    """Visit finite changed leaves, keeping compact arrays compact."""
    check_preparation()
    if before is after:
        return
    arrays = (list, tuple, UniformArray)
    if type(before) in arrays and type(after) in arrays and len(before) == len(after):
        if type(before) is type(after) is UniformArray and before.base_image == after.base_image:
            indices = sorted({i for i, _ in before.sparse_items()} |
                             {i for i, _ in after.sparse_items()})
        else:
            indices = range(len(before))
        for index in indices:
            yield from changed_cells(before[index], after[index], (*path, index))
    elif type(before) is type(after) is dict and before.keys() == after.keys():
        for key in sorted(before):
            yield from changed_cells(before[key], after[key], (*path, key))
    elif before != after:
        yield path


def entry_facts(occurrences, proc, effects):
    """Cheap ordering facts about native entries, never reachability evidence.

    Inspect a shared state value once, stopping at its first changed cell.
    Goal families then use bit masks instead of rescanning arrays. Command
    effects overapproximate the fixed path; unknown commands stay eligible.
    """
    compared, out = {}, {}
    commands = {name: {row[0]: row[2] for row in rows}
                for name, rows in effects.items()}
    missing = object()
    for item in occurrences:
        active = 0
        for index, name in enumerate(proc.state):
            check_preparation()
            initial, value = proc.initial[name], item['state'][name]
            key = name, id(value)
            if key not in compared:
                compared[key] = next(changed_cells(initial, value), missing) is not missing
            if compared[key]:
                active |= 1 << index
        reachable = None
        for name, choices in commands.items():
            goals = choices.get(item['input'].get(name))
            if goals is not None:
                reachable = goals if reachable is None else reachable & goals
        out[item['id']] = active, reachable
    return out


def write_locations(occurrences):
    """Observed changed array locations, solely for proposing new addresses.

    Reuse the selected trace's states; no additional execution or per-cell
    goals. Reset jumps cannot supply writes. A store leaving its value equal
    is not observed here, and absence is never an unreachability conclusion.
    """
    locations = {}
    for before, after in zip(occurrences, occurrences[1:]):
        if after['position'] != before['position'] + 1:
            continue
        for name, value in before['state'].items():
            if type(value) not in (list, tuple, UniformArray):
                continue
            for path in changed_cells(value, after['state'][name]):
                if path and type(path[0]) is int:
                    locations.setdefault(name, set()).add(path[0])
    return {name: tuple(sorted(indices)) for name, indices in locations.items()}


class PreparationFrontier:
    """Observed writer inputs and command samples, not a state bank.

    The source worker owns this table. Writer storage follows written cells;
    command samples follow distinct observed inputs, not their combinations.
    A written cell keeps its observed alternative inputs instead of silently
    replacing them with the last sample. Recipes choose one alternative per
    cell; they never enumerate the Cartesian product of those alternatives.
    Exact query/proof caches do not use this table: its rows only suggest inputs.
    """

    def __init__(self, effects):
        self.effects = effects
        self.writers = {}
        self.commands = {}
        self._new_writers = {}
        self._new_commands = {}
        # Allocate observed commands across issued recipes instead of restarting
        # at the same high-ranked prefix on every selection. These are planning
        # counts, not executed attempts, state equivalence or goal evidence.
        # Storage follows the input pool, not a state-by-input Cartesian table.
        self._planned_samples = {}
        self._planned_writers = {}

    def rows(self):
        """Compact donor inputs only: no native states, hits or solver facts."""
        return [[control, command, state, list(path), copy.deepcopy(values)]
                for (control, command, state, path), samples in self.writers.items()
                for values in samples.values()]

    def command_rows(self):
        """Source-local ordering hints and inputs; no imported hit evidence."""
        return [[control, command, rank, copy.deepcopy(values)]
                for (control, command), samples in self.commands.items()
                for rank, values in samples.values()]

    def updates(self):
        """Drain newly observed inputs; imported rows are never echoed back.

        Work follows new donor rows, not the entire accumulated pool on every
        Select. Returned rows are detached and contain no execution evidence.
        """
        writers = [[control, command, state, list(path), copy.deepcopy(values)]
                   for (control, command, state, path, _token), values in self._new_writers.items()]
        commands = [[control, command, rank, copy.deepcopy(values)]
                    for (control, command, _token), (rank, values) in self._new_commands.items()]
        self._new_writers.clear()
        self._new_commands.clear()
        return writers, commands

    def extend(self, rows, commands=None):
        if type(rows) is not list:
            raise ValueError("preparation material is a list of source writer rows")
        writers = {}
        for row in rows:
            if type(row) is not list or len(row) != 5:
                raise ValueError("a preparation writer has five fields")
            control, command, state, path, values = row
            if (type(control) is not str or type(command) not in (int, bool)
                    or type(state) is not str or type(path) is not list
                    or any(type(part) not in (int, str) for part in path)
                    or type(values) is not dict
                    or any(type(name) is not str or type(value) not in (int, bool)
                           for name, value in values.items())
                    or values.get(control) != command
                    or not any(choice[0] == command and state in choice[4]
                               for choice in self.effects.get(control, ()))):
                raise ValueError("preparation material disagrees with the source writer")
            writers.setdefault((control, command, state, tuple(path)), {})[
                canonical_json(values)] = copy.deepcopy(values)
        if commands is not None and type(commands) is not list:
            raise ValueError("command material is a list of source input rows")
        samples = []
        for row in commands or ():
            if type(row) is not list or len(row) != 4:
                raise ValueError("a command sample has four fields")
            control, command, rank, values = row
            if (type(control) is not str or type(command) not in (int, bool)
                    or type(rank) is not int or rank < 0 or type(values) is not dict
                    or any(type(name) is not str or type(value) not in (int, bool)
                           for name, value in values.items())
                    or values.get(control) != command
                    or not any(choice[0] == command for choice in self.effects.get(control, ()))):
                raise ValueError("command material disagrees with the source command")
            samples.append((control, command, rank, copy.deepcopy(values)))
        for location, alternatives in writers.items():
            self.writers.setdefault(location, {}).update(alternatives)
        for control, command, rank, values in samples:
            self._remember(control, command, rank, values, observed=False)

    def _remember(self, control, command, rank, values, *, observed=True):
        samples = self.commands.setdefault((control, command), {})
        key = canonical_json(values)
        old = samples.get(key)
        if old is None or rank > old[0]:
            samples[key] = rank, copy.deepcopy(values)
            if observed:
                self._new_commands[control, command, key] = samples[key]

    def observe(self, occurrences):
        for item in occurrences:
            check_preparation()
            for control, commands in self.effects.items():
                command = item['input'].get(control)
                if any(row[0] == command for row in commands):
                    self._remember(control, command, len(set(item.get('hits', ()))), item['input'])
        for before, after in zip(occurrences, occurrences[1:]):
            check_preparation()
            # Across a reset, the next entry is not the preceding post-state.
            if after["position"] != before["position"] + 1:
                continue
            for control, commands in self.effects.items():
                command = before["input"].get(control)
                row = next((row for row in commands if row[0] == command), None)
                if row is None:
                    continue
                token = canonical_json(before['input'])
                donor = self.commands[control, command][token][1]
                # Only payload-dependent writes supply reusable preparation.
                # Constant initializers remain in the source-derived path.
                for name in sorted(row[4]):
                    for path in changed_cells(before["state"][name], after["state"][name]):
                        location = control, command, name, path
                        alternatives = self.writers.setdefault(location, {})
                        if token not in alternatives:
                            alternatives[token] = donor
                            self._new_writers[(*location, token)] = donor

    def proposals(self, windows, groups, proc, manifest, dependencies, *, factory):
        """One writer-composition per reset epoch/goal family/trigger.

        Preserve public reset, framing and queue boundaries. After the original
        trigger, reuse observed inputs for the same source command in remaining
        slots. This explores a prepared state without rebuilding it per input or
        enumerating a prefix-by-input Cartesian product. Prefer samples allocated
        to fewer earlier recipes, then broader observed source paths. An issued
        recipe stays immutable; allocation grants no execution or goal evidence.
        All original single-step and joint queries remain.
        """
        from .prefix_generation import setup_commands

        markers = {logical for framing, projection in proc.input_framing
                   for public, logical in projection if public in framing.fields}
        epochs = {}
        for window in windows.values():
            if window[0]["position"] == 0:
                key = window[0]["id"]
                if len(window) > len(epochs.get(key, ())):
                    epochs[key] = window
        for group, goals in groups:
            needed = {name for i, name in enumerate(proc.state)
                      if dependencies.get(goals[0], 0) & (1 << i)}
            for control, commands in self.effects.items():
                if control in markers:
                    continue
                for trigger, _writes, reachable, _cost, _payload in commands:
                    selected = tuple(g for g in goals if g in reachable)
                    if not selected:
                        continue
                    initialization, filling = setup_commands(commands, needed, trigger)
                    donors = {}
                    for (name, command, state, path), alternatives in self.writers.items():
                        check_preparation()
                        if name == control and command in filling and state in needed:
                            donors.setdefault((state, path), {}).update(alternatives)
                    if not donors:
                        continue
                    samples = self.commands.get((control, trigger), {})
                    planned = self._planned_samples.setdefault((control, trigger), {})
                    planned_writers = self._planned_writers.setdefault((control, trigger), {})
                    for window in epochs.values():
                        values = []
                        used_writers = set()
                        for command in initialization:
                            if len(values) == len(window) - 1:
                                break
                            values.append({**window[len(values)]["input"], control: command})
                        for alternatives in donors.values():
                            if len(values) == len(window) - 1:
                                break
                            item = window[len(values)]
                            choices = ((key, donor) for key, donor in reversed(alternatives.items())
                                if set(donor) == set(item['input']) and all(
                                    donor.get(name) == item['input'].get(name) for name in markers))
                            chosen = min(choices, default=None, key=lambda row: (
                                planned_writers.get(row[0], 0), row[0] not in used_writers))
                            if chosen is None:
                                continue
                            key, donor = chosen
                            if key in used_writers:
                                continue  # One observed input may write several cells.
                            values.append(donor)
                            used_writers.add(key)
                        if len(values) <= len(initialization):
                            continue
                        values.append({**window[len(values)]["input"], control: trigger})
                        present = {canonical_json(value) for value in values}
                        allocated = []
                        for key, (_rank, sample) in sorted(samples.items(),
                                key=lambda item: (planned.get(item[0], 0), -item[1][0], item[0])):
                            check_preparation()
                            if len(values) == len(window):
                                break
                            item = window[len(values)]
                            if (key not in present and set(sample) == set(item['input'])
                                    and all(sample.get(name) == item['input'].get(name) for name in markers)):
                                values.append(sample)
                                present.add(key)
                                allocated.append(key)
                        used = window[:len(values)]
                        # Count planned choices even when the recipe equals the
                        # seed, so the next selection can try another writer.
                        # These are scheduling hints, never native hit evidence.
                        for key in used_writers:
                            planned_writers[key] = planned_writers.get(key, 0) + 1
                        if all(item["input"] == value for item, value in zip(used, values)):
                            continue
                        token = recipe_token(tuple(zip(used, values)))
                        request = factory(selected, occurrence=f"state-setup:{token}", rank=group,
                            mutable_fields=tuple(field for item in used for name, field in item["bindings"]
                                                 if name not in markers), catalogue_digest=manifest.digest())
                        for key in allocated:
                            planned[key] = planned.get(key, 0) + 1
                        # Bind the exact proposal to this immutable selection;
                        # subsequent observations cannot change an issued Probe.
                        yield request, tuple((item, copy.deepcopy(value))
                                             for item, value in zip(used, values))
