"""Sufficient local event-bound proofs; all other domain checks remain owned.

Removing older definitions and replacing a boundary expression consistently
can only enlarge a history's possibilities. Only UNSAT is reusable. A failed
local attempt returns to the untouched full-history obligation, not a refusal
or a claim about any execution goal.
"""

from collections import OrderedDict

import z3

from behave.core import runtime_defaults as defaults

from ..symbolic.exact_solve import ExactCheckResult
from ..core.query import SolverAnswer
from .symbolic_state import assertion_conjunction, assertion_disjunction


def boundary_expressions(value, terms):
    """Collect composite boundary coordinates without changing literals."""
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        for child in value.values():
            boundary_expressions(child, terms)
    elif isinstance(value, tuple):
        for child in value:
            boundary_expressions(child, terms)
    elif isinstance(value, z3.ExprRef) and not z3.is_const(value):
        terms.setdefault(value.get_id(), value)


def local_completeness(comparison, solve, *, check):
    from .source_confluence import _proof_shape, _replica_coordinates

    histories = []
    for history in (comparison.left, comparison.right):
        startup = getattr(history, 'startup', None)
        groups = [] if startup is None else [tuple(getattr(startup, 'phases', (startup,)))]
        groups.extend(slot.phases for slot in history.source.slots)
        # Absence is not evidence of completeness; older/non-source owners
        # retain their original solver path. Validate before accepting any row.
        if not groups or any(not group for group in groups) or any(getattr(part, 'entry_terms', None) is None
                             for group in groups for part in group):
            return None
        histories.append(groups)
    proved = OrderedDict()  # Proofs never outlive this domain-admission attempt.
    for side, groups in enumerate(histories):
        for index, group in enumerate(groups):
            check()
            window = groups[max(0, index - 1):index + 1]
            roots = tuple(term for parts in window for part in parts for term in part.constraints)
            defect = assertion_disjunction(tuple(part.unfinished for part in group))
            terms = {term.get_id(): term for term in window[0][0].entry_terms}
            for parts in window:
                boundary_expressions(parts[0].inputs, terms)
            cuts = tuple((term, z3.FreshConst(term.sort(), prefix='complete_boundary'))
                         for term in terms.values())
            query = z3.substitute(assertion_conjunction((*roots, defect), check=check), *cuts)
            check()
            shape = _proof_shape(query, check=check)
            old = proved.get(shape)
            if old is not None and _replica_coordinates(((old, query),), check=check,
                    shared_functions=True, independent=False) is not None:
                proved.move_to_end(shape)
                continue
            answer = solve(f'completeness:local:{side}:{index}', (query,))
            if type(answer) is not ExactCheckResult:
                raise TypeError('local completeness requires the exact solver result type')
            if answer.answer is not SolverAnswer.UNSAT:
                return None
            if shape is not None:
                proved[shape] = query
                proved.move_to_end(shape)
                while len(proved) > defaults.SIM_ANALYSIS_ANSWER_CACHE_ENTRIES:
                    proved.popitem(last=False)
    return ExactCheckResult(SolverAnswer.UNSAT)
