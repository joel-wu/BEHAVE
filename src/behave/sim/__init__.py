"""BEHAVE-Sim's public package boundary.

Library consumers use :mod:`behave.sim.api`.  The remaining modules are frontend,
backend and compatibility implementation; importing them does not establish the typed
admission, replay, concrete-evaluation and bounded-analysis contracts of the public path.
"""

import sys
from pathlib import Path

#: Directory holding package-owned resources such as simulator templates.
PACKAGE_DIR = Path(__file__).resolve().parent

#: Sim records carry exact bit-vectors, such as packed memory images, whose
#: decimal JSON form can exceed Python's default 4,300-digit int/str limit
#: (a 4x256x16-bit sketch already needs 5,241 digits). Raise the limit for
#: processes that use Sim to a bound that keeps conversion cost small; wire
#: payloads are size-limited separately. Never lower a higher setting.
_RECORD_INTEGER_DIGITS = 50_000
if 0 < sys.get_int_max_str_digits() < _RECORD_INTEGER_DIGITS:
    sys.set_int_max_str_digits(_RECORD_INTEGER_DIGITS)

__all__ = ["PACKAGE_DIR", "api"]


def __getattr__(name):
    # Internal workers import their own stage, not the entire public service
    # graph. Public ``from behave.sim import api`` keeps the same module identity;
    # importlib installs it on this package after the first explicit request.
    if name == "api":
        from importlib import import_module

        return import_module(f"{__name__}.api")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
