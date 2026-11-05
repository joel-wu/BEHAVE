"""Concurrent child chains with one completion-order parent writer."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue

from behave.core.records import thaw_json


def run_stage_tasks(items, operation, *, tape, objects, workers, retained_stages=None,
                    should_start=lambda item: True, on_stage=lambda row: None,
                    commit_stage=lambda row: True):
    """Children own durable journals; workers never mutate the parent tape.

    Commit finished chains immediately, rather than waiting for input order.
    Return values still follow input order. Recovery consumes existing commits
    in their recorded order; only new completions depend on scheduling today.
    """
    recorded = dict(retained_stages or {})
    recorded.update({event.payload["name"]: (thaw_json(event.payload), event.artifacts)
                     for event in tape.events[tape.cursor:] if event.kind == "round_stage"})

    def one(item):
        if not should_start(item):
            return None, []
        rows = []

        def stage(name, execute, child, journal):
            if name in recorded:
                row, closure = recorded[name]
            else:
                outcome = execute()
                head, closure = child.seal(journal)
                objects.publish_owned(child.objects, closure)
                row = {"name": name, "head": head.as_dict(), "outcome": outcome}
            if name in recorded or commit_stage(row):
                rows.append((row, closure))
            return row

        outcome = operation(item, stage)
        return outcome, rows

    outcomes = {}
    ready = {}
    committed = set()

    def commit(rows):
        ready.update((row["name"], (row, closure)) for row, closure in rows)
        # Old receipts must keep their original order, including historical
        # input-order runs. A later stage (e.g. publication) is not ours to take.
        while tape.cursor < len(tape.events):
            event = tape.events[tape.cursor]
            if event.kind != "round_stage" or event.payload["name"] not in ready:
                return
            name = event.payload["name"]
            row, closure = ready.pop(name)
            tape.record("round_stage", row, closure)
            committed.add(name)
            on_stage(row)
        for name, (row, closure) in tuple(ready.items()):
            if name not in committed:
                tape.record("round_stage", row, closure)
                committed.add(name)
                on_stage(row)
            del ready[name]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        waiting = iter(enumerate(items))
        futures, completed = {}, Queue()

        def dispatch():
            while len(futures) < workers:
                try:
                    index, item = next(waiting)
                except StopIteration:
                    return
                if should_start(item):
                    future = pool.submit(one, item)
                    futures[future] = index
                    future.add_done_callback(completed.put)

        dispatch()
        while futures:
            future = completed.get()
            index = futures.pop(future)
            outcome, rows = future.result()
            commit(rows)
            if outcome is not None:
                outcomes[index] = outcome
            dispatch()
    return tuple(outcomes[index] for index in sorted(outcomes))
