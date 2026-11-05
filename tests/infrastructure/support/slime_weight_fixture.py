"""Numerical HF-weight change evidence, not a checkpoint-load replacement."""

import json
import sys
from itertools import pairwise
from pathlib import Path

from behave.train.slime_checkpoint import changed_decoder_weight

paths = list(map(Path, sys.argv[1:]))
assert len(paths) == 4
print(json.dumps({"updates": [changed_decoder_weight(a, b) for a, b in pairwise(paths)]}))
