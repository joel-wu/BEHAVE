# Capacity-limited weighted MoE scatter

Combine weighted expert outputs back into four original tokens, retaining the specified per-expert capacity limit and reporting dropped routes.

## Interface

### RTL interface

```systemverilog
module MoeScatterTokens (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [15:0] in_experts,
    input logic [255:0] in_weights,
    input logic [511:0] in_values,
    input logic out_ready,
    output logic out_valid,
    output logic [255:0] out_values,
    output logic [7:0] out_dropped,
    output logic [15:0] out_counts,
    output logic [23:0] out_indices
);
```

### Behavior interface

```python
class MoeScatterTokens:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_experts, in_weights, in_values): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_counts`: 16.
- `out_dropped`: 8.
- `out_indices`: 24.
- `out_values`: 256.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

MoeScatterTokens is the complete fixed-shape operator. Packing is little-index-first as specified below.

## Functional Behavior

Un-route weighted expert outputs to original tokens, following MegaBlocks binned_scatter. The fixed shape is four tokens, top_k=2 routes per token, four experts, capacity=2 entries per expert, and two feature channels. Expert computation is supplied as input; this module does not replace it with an identity function or compute routing logits.

Route r=2*t+k belongs to token t and top-k slot k. Its expert ID is in_experts[2*r+:2]. Its binary32 routing weight is in_weights[32*r+:32]. Group all eight route IDs by expert in increasing expert order, preserving route order inside each group. If route r has zero-based position j within expert e, it survives exactly when j<2. Later entries are dropped, just as entries beyond the source binned-copy launch capacity have zero contribution.

in_values contains the expert output buffer [expert][entry][feature]. Binary32 element (e,j,d) is at [32*(4*e+2*j+d)+:32]. For a surviving route, contribution[r,d]=mul(in_values[e,j,d],weight[r]); a dropped route contributes +0 without reading or multiplying any buffer element. Each output element (t,d) is add(contribution[2*t,d],contribution[2*t+1,d]), packed at out_values[32*(2*t+d)+:32]. Weights index the ORIGINAL route, not its sorted position. Both routes may target the same expert; their surviving contributions must still sum. Empty/padded expert slots are not consumed.

out_dropped bit r records dropping. out_counts[4*e+:4] is the total number of routes assigned to e, BEFORE capacity clipping (0 through 8). out_indices[3*p+:3] is the route at sorted position p, including dropped routes. Thus out_indices is always a permutation of 0 through 7.

mul and add round separately to binary32, nearest with ties to even. Subnormal operands retain their numerical values and underflow is gradual, including rounding at zero/subnormal and subnormal/normal boundaries. Overflow is signed infinity. NaN operands, zero times infinity, or opposite infinities added produce 0x7fc00000 (a project sign/payload convention). Multiplication uses the XOR sign for zero and infinity; adding two negative zeros gives negative zero, while other exact cancellation gives positive zero. Compare output encodings exactly, without a tolerance. There is no fused multiply-add. Dropped routes and unused expert slots cannot introduce NaNs. These operations define the task's numerical specialization; they are not a claim of identical GPU reduction order for arbitrary top_k.

One accepted input returns one mapping with all four output fields. A transfer occurs at a rising edge with valid and ready both high. Output valid and payload remain stable until acceptance. Input data takes effect only on acceptance; there is no cycle-count requirement or persistent numerical state.

## Reset Behavior

Synchronous active-high reset discards pending output and clears out_valid and all output payloads. in_ready=1 after reset. Payloads are zero when out_valid=0.
