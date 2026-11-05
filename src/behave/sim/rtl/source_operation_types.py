"""Specialize capture widths using constants from the selected elaboration.

The frontend already computed every contextual type. This module only joins
those answers to visible elaboration constants; it never re-types arithmetic or
infers a width from values seen during replay.
"""

from dataclasses import dataclass, replace

from .source_elaboration import ElaborationEnvironment, parameter_partition
from .source_operation_events import OperationCapture


@dataclass(frozen=True, slots=True)
class CaptureTypes:
    # Each group holds one compiler-resolved type and its constant environments.
    variants: tuple[tuple[OperationCapture, tuple[ElaborationEnvironment, ...]], ...]
    operand_witnesses: tuple[str, ...] = ()

    def bind(self):
        """Return one type-shape, constant widths and exhaustive variant guards.

        Varying signedness/state kind uses a compiler-checked type witness.
        Computation retains those exact types. Read-only predicate callbacks
        use four-state carriers: bit values embed losslessly, while logic X/Z
        values must not be converted to zero before checking readability.
        """
        captures = tuple(capture for capture, _ in self.variants)
        first = captures[0]
        if len(captures) == 1:
            return first, (), (), ()
        values = tuple((*capture.operands, capture.result) for capture in captures)
        if any(capture.operator != first.operator for capture in captures):
            raise ValueError("operation variants disagree on the source operator")
        observers = tuple(replace(capture,
            operands=tuple(replace(value, four_state=True) for value in capture.operands),
            result=replace(capture.result, four_state=True)) for capture in captures)
        observer_captures = tuple(dict.fromkeys(observers))
        contexts, outcomes = [], []
        for (_, environments), observer in zip(self.variants, observers, strict=True):
            for environment in environments:
                contexts.append(dict(environment.constants))
                outcomes.append(observer)
        type_witnesses = []
        for column_index, column in enumerate(zip(*values)):
            if len({(value.signed, value.four_state) for value in column}) == 1:
                type_witnesses.append("")
                continue
            candidates = None
            fixed_sign = len({value.signed for value in column}) == 1
            for (_, environments), value in zip(self.variants, column):
                for environment in environments:
                    matching = {name for name, (_, signed, four_state) in environment.types
                                if (signed, four_state) == (value.signed, value.four_state)}
                    if fixed_sign:
                        # A Boolean result, for example, is unsigned even if
                        # its operand's type parameter is signed. A neutral
                        # sign cast retains that witness's exact state kind.
                        cast = "signed" if value.signed else "unsigned"
                        matching.update(f"{cast}'({name})" for name, (_, _, four_state) in environment.types
                                        if four_state == value.four_state)
                    candidates = matching if candidates is None else candidates & matching
            if not candidates:
                raise ValueError(f"operation value {column_index} lacks a compiler-bound scalar-kind witness")
            type_witnesses.append(min(candidates))
        partition = parameter_partition(contexts, outcomes)
        guards = ["(" + " || ".join(f"({guard})" for guard, outcome in partition if outcome == capture) + ")"
                  for capture in observer_captures]
        widths = []
        for column in zip(*((*capture.operands, capture.result) for capture in observer_captures)):
            expression = str(column[-1].width)
            for guard, value in reversed(tuple(zip(guards[:-1], column[:-1]))):
                expression = f"({guard} ? {value.width} : {expression})"
            widths.append(expression)
        return first, tuple(widths), tuple(zip(guards, observer_captures)), tuple(type_witnesses)
