"""How the integers in a witness are read, which nothing said.

`witnesses.json` holds integers and stated nothing about what they are, and its two
producers disagreed with themselves:

* the RTL explorer drew Phase 1 stimulus from `(0, 2**w - 1)` and sign-extended every
  Phase 2 candidate it read out of a model;
* the behavior explorer did exactly the same, and additionally fed the sign-extended
  value straight to `post_model.process` -- so on an unsigned port the search solved a
  goal while running -56 and cocotb replays the same witness at 200.

Neither sign came from a declaration. A `z3.BitVec` has no signedness; yosys's SMT2
header carries `; yosys-smt2-input NAME WIDTH` and nothing more. The reading was being
chosen at read-out, twice, differently.

The fix is not to give each producer a signedness. It is that a bit pattern and a
signed integer are two readings of one value, and the reading belongs beside the
declaration that licenses it -- `match/output_match.normalize_behavior_inputs`, which
masks to the port width and re-signs per the schema at the one boundary where RTL bits
become arguments to a Python model. What is tested here is that everything upstream of
that boundary speaks bit patterns, and that the file says so.
"""
import json

import pytest

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver


# ── the boundary that owns the signed reading ──

def test_the_schema_is_where_a_signed_reading_comes_from():
    from behave.sim.core.schema import Authority, Endpoint, TransactionSchema

    got = TransactionSchema([Endpoint("a", "input", 8, signed=True),
                             Endpoint("b", "input", 8)],
                            authority=Authority.TASK)
    assert got.signedness() == {"a": True, "b": False}
    # And the ends of each range follow from it, so nothing downstream re-derives them.
    assert got["a"].bounds == (-128, 127)
    assert got["b"].bounds == (0, 255)


# ── upstream of it, everything is a bit pattern ──

# ── typed replay records preserve the producer's bit patterns ──

def test_typed_stimulus_roundtrip_preserves_unsigned_bit_patterns():
    from behave.sim import api

    stimulus = api.Stimulus(queues={"in": [[200], [255], [0]]},
                            replay_policy=api.ReplayPolicy())
    restored = api.Stimulus.from_dict(json.loads(json.dumps(stimulus.as_dict())))
    assert restored == stimulus
    assert restored.fingerprint == stimulus.fingerprint
    assert dict(restored.queues)["in"] == ((200,), (255,), (0,))
