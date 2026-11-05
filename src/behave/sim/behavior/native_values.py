"""Compatibility name for the shared compiler implementation.

Retained for saved checkpoints and existing imports, not a second owner.
"""
import sys
from ..symbolic import native_values as _implementation

sys.modules[__name__] = _implementation
