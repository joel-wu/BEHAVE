"""Shared finite-representation settings, independent of frontend and workers.

Keep the established configuration names and values. These bounds describe
representation support, not execution deadlines or goal reachability.
"""

from behave.core import runtime_defaults as defaults


#: Exact representation work after a finite encoding plan chooses loop copies,
#: array components, and scalar carriers. Exceeding this boundary refuses that
#: formula construction, not semantic lowering, concrete replay or a smaller
#: dependency-audited query over the same complete finite domain.
TERM_BUDGET = defaults.SIM_BEHAVIOR_ENCODING_NODES

#: Maximum nesting of a SolverIR command, expression, recursive value type, or
#: concrete initial value.  ``TERM_BUDGET`` bounds how much total work readers
#: perform, but a chain of one-cell arrays has linear work and can still exceed
#: Python's call stack in a recursive reader.  This independent, reported
#: dimension is checked iteratively before any reader or solver allocation.
REPRESENTATION_DEPTH = defaults.SIM_BEHAVIOR_REPRESENTATION_DEPTH

#: Largest scalar bit pattern retained by the shared representation. The same
#: bound applies to declaration-time integers and task-declared port widths:
#: both become integer range endpoints and bit-vector terms downstream.  The
#: 8-Kibit ceiling admits ordinary 1-KiB cryptographic and memory blocks as
#: one hardware value while still bounding every host integer and solver sort.
SCALAR_BITS = defaults.SIM_BEHAVIOR_SCALAR_BITS

#: Largest shift amount the interval transformer evaluates.  This is the
#: scalar capability, not a second smaller language limit: a 1024-bit packed
#: port legitimately uses shifts above 512 while remaining well inside the
#: retained scalar representation.
SHIFT_LIMIT = SCALAR_BITS

#: Rounds the state's intervals are iterated for before a variable still
#: moving is reported as having no bound.
STATE_ROUNDS = defaults.SIM_BEHAVIOR_STATE_ROUNDS
#: Magnitude past which an interval that is still moving is taken to be
#: diverging rather than merely wide, so the iteration stops early.
STATE_MAGNITUDE = defaults.SIM_BEHAVIOR_STATE_MAGNITUDE


def scalar_magnitude_bits(lo, hi=None):
    """Magnitude bits in one integer or the endpoints of one interval."""
    values = (lo,) if hi is None else (lo, hi)
    if any(type(value) is not int for value in values):
        raise TypeError("scalar magnitude is defined for exact integers")
    return max(*(value.bit_length() for value in values), 1)
