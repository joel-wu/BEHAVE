# Eight-Token Greedy Argmax

Select the highest-scoring token from eight binary32 logits, with deterministic ties and an explicit NaN diagnostic.

## Interface

### RTL interface

```systemverilog
module greedy_argmax (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [255:0] in_logits,
    output logic out_valid, input logic out_ready,
    output logic [2:0] out_index, output logic out_has_nan
);
```

### Behavior interface

```python
class GreedyArgmax:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_logits): ...
```

Return one mapping with fields `out_index`, `out_has_nan`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_has_nan`: 1.
- `out_index`: 3.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

One input
transfer produces one ordered output transfer. Transfers are sampled on rising
edges with valid and ready high. While output ready is low, a valid result and
all its fields remain unchanged. Arithmetic latency is not fixed.

## Modules

- `greedy_argmax`: arithmetic and output holding.
- `GreedyArgmax`: the untimed input-to-output mapping.

## Functional Behavior

`in_logits` contains eight IEEE-754 binary32 bit patterns. Token i occupies
bits 32*i+31:32*i, least-significant token first. Return the index of the
numerically largest logit. Ties select the lowest index; positive and negative
zero compare equal. Positive/negative infinity and subnormal values retain
their IEEE ordering (no subnormal flush).

The task gives non-numeric inputs an explicit diagnostic: if any logit is NaN,
return the lowest NaN index and `out_has_nan=1`; otherwise `out_has_nan=0`.
All NaN signs and payloads are recognized. This is only the greedy selection
suboperator, not softmax, temperature/top-k/top-p processing or random sampling.

## Reset Behavior

Reset is synchronous active-high and has priority over transfers. A reset edge
discards pending output, clears output valid and every output field, and makes
input ready. No arithmetic state survives between transactions.
