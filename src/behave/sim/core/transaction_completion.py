"""Response boundaries for budget checkpoints, not comparison or score evidence.

The same counts describe Behavior transactions and their RTL implementation.
Counts come from the reference's actual transaction sequence or the contract;
one output is never assumed to complete an input. Unknown intermediate product
boundaries stay unknown. A caller must still validate the recorded schedule,
all observed safety obligations, output values and external memory.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TransactionCompletion:
    request: str
    inputs: tuple[tuple[str, int], ...]
    outputs: tuple[tuple[str, int], ...]
    # Cumulative output counts after each accepted input, including the empty
    # prefix. This form has one logical source; product requests may provide
    # only their existing full-request totals instead of inventing a pairing.
    prefixes: tuple[tuple[int, ...], ...] | None = None

    def __post_init__(self):
        if type(self.request) is not str or not self.request:
            raise ValueError("completion belongs to a runtime request")
        for rows in (self.inputs, self.outputs):
            if type(rows) is not tuple or any(
                    type(row) is not tuple or len(row) != 2
                    or type(row[0]) is not str or not row[0]
                    or type(row[1]) is not int or row[1] < 0 for row in rows):
                raise ValueError("completion needs named nonnegative counts")
            names = tuple(name for name, _ in rows)
            if names != tuple(sorted(set(names))):
                raise ValueError("completion endpoints must be distinct and sorted")
        if self.prefixes is not None:
            if len(self.inputs) != 1 or type(self.prefixes) is not tuple \
                    or len(self.prefixes) != self.inputs[0][1] + 1:
                raise ValueError("prefix counts need every boundary of one logical input")
            if any(type(row) is not tuple or len(row) != len(self.outputs)
                   or any(type(n) is not int or n < 0 for n in row)
                   for row in self.prefixes):
                raise ValueError("prefix counts must cover every output endpoint")
            if self.prefixes[0] != (0,) * len(self.outputs) \
                    or self.prefixes[-1] != tuple(n for _, n in self.outputs) \
                    or any(any(a > b for a, b in zip(left, right, strict=True))
                           for left, right in zip(self.prefixes, self.prefixes[1:])):
                raise ValueError("prefix counts must accumulate from zero to the full result")

    @property
    def totals(self):
        return dict(self.outputs)

    def targets_after(self, accepted):
        """Required responses for this exact accepted-input prefix, or unknown."""
        _counts(accepted, self.inputs)
        if any(accepted[name] > depth for name, depth in self.inputs):
            raise ValueError("accepted inputs exceed the planned request")
        if self.prefixes is not None:
            row = self.prefixes[accepted[self.inputs[0][0]]]
            return {name: count for (name, _), count in zip(self.outputs, row, strict=True)}
        if all(accepted[name] == depth for name, depth in self.inputs):
            return self.totals
        return None

    def at_response_boundary(self, accepted, emitted, *, memory_quiescent):
        """Whether all accepted inputs have their planned responses so far.

        This permits capturing a checkpoint, never passing it. Matching values,
        the complete memory result and every observed violation are checked by
        the ordinary assessor. An extra response is not hidden as completion.
        """
        targets = self.targets_after(accepted)
        _counts(emitted, self.outputs)
        if type(memory_quiescent) is not bool:
            raise TypeError("memory quiescence must come from the memory driver")
        return (any(accepted.values()) and memory_quiescent and targets is not None
                and all(emitted[name] == count for name, count in targets.items()))

    def at_checkpoint_boundary(self, accepted, emitted, *, memory_quiescent):
        """Require an actual response closing the last accepted transaction.

        Count equality alone also holds immediately after accepting an input
        whose reference emits nothing. Such an input may still be computing or
        writing memory; idle memory queues do not prove it has finished. Keep
        that boundary unknown until a later response-bearing transaction closes
        the prefix. Values, memory and protocols still require host assessment.
        """
        if not self.at_response_boundary(accepted, emitted,
                                         memory_quiescent=memory_quiescent):
            return False
        if self.prefixes is None:
            return False  # No per-transaction physical completion relation.
        count = accepted[self.inputs[0][0]]
        return any(before < after for before, after in zip(
            self.prefixes[count - 1], self.prefixes[count], strict=True))


def _counts(values, endpoints):
    if set(values) != {name for name, _ in endpoints} or any(
            type(value) is not int or value < 0 for value in values.values()):
        raise ValueError("progress must count exactly the declared endpoints")
