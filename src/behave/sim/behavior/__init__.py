"""The behavior frontend: a Python model as an artifact to close over.

What is here answers the obligations in ``sim.instance`` for a Python
behavior model. The lowering, the two readings of the representation and
the goal catalogue are all specific to that, and none of them is visible
to the closure or to the certificate.
"""

from pathlib import Path

#: Where the approved library lives. A model may import it, so this has
#: to be on the path before one is compiled.
LIBRARY_DIR = Path(__file__).resolve().parent

__all__ = ["LIBRARY_DIR"]
