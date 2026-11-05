# Swizzler

Remap independently selected lanes of a packed data bus, with bypass, optional
per-lane parity checking and optional registered outputs. This restores the
native interface; there are no ready/valid signals or backpressure.

## Interface

### RTL interface

```systemverilog
module swizzler #(
    parameter int NUM_LANES = 4, DATA_WIDTH = 8,
    parameter int REGISTER_OUTPUT = 0, ENABLE_PARITY_CHECK = 0
) (
    input logic clk, rst_n, bypass,
    input logic [NUM_LANES*DATA_WIDTH-1:0] data_in,
    input logic [NUM_LANES*((NUM_LANES > 1) ? $clog2(NUM_LANES) : 1)-1:0] swizzle_map_flat,
    output wire [NUM_LANES*DATA_WIDTH-1:0] data_out,
    output wire parity_error
);
```

### Behavior interface

```python
class Swizzler:
    def __init__(self, NUM_LANES=4, DATA_WIDTH=8, REGISTER_OUTPUT=0, ENABLE_PARITY_CHECK=0): ...
    def reset(self): ...
    def process(self, bypass, data_in, swizzle_map_flat): ...
```

Each call returns `{"data_out": word, "parity_error": bit}` for the settled
pins before a non-reset rising edge, then advances any output registers.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: NUM_LANES*DATA_WIDTH.
- `parity_error`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

### Parameter specialization

The default instance uses four eight-bit lanes, combinational outputs and
disabled parity. Both feature switches are 0 or 1; lane count and data width
are positive integers. Runtime field widths must match the chosen parameters.

## Modules

- `swizzler`: lane selection, parity logic and optional output registers.

## Functional Behavior

Let W=DATA_WIDTH and K=max(1,ceil(log2(NUM_LANES))). Input/output lane i is
bits `[i*W +: W]`; map entry i is bits `[i*K +: K]`. With bypass=1, output
lane i takes input lane i, ignoring the map. Otherwise it takes the input
lane selected by map entry i. Repeated selections are legal: a map need not
be a permutation. Unused selector codes for non-power-of-two lane counts
produce a zero lane. For one lane, a one-bit selector is retained and code 1
also selects zero unless bypass is set. These width/domain completions avoid
zero-width ports and undefined indexing; they do not change the default case.

With parity enabled, compute each selected lane's XOR reduction, then OR these
bits. Thus one or more odd-parity output lanes assert parity_error. This is
not XOR parity of the whole bus; two odd lanes must still assert it. With
parity disabled the flag is always zero. Bypass still participates in parity.

REGISTER_OUTPUT=0 makes both outputs combinational functions of current inputs.
REGISTER_OUTPUT=1 captures both remapped data and its parity flag on every
rising edge, without enable or flow control. Registering the flag alongside
the data explicitly completes their timing relationship. Pre-edge observations
therefore report the previous edge's pair in registered mode, but the current
input's pair in combinational mode.

Example: data_in=0xDDCCBBAA and map entries `[2,0,3,1]` produce
data_out=0xBBDDAACC when bypass is zero.

## Reset Behavior

For registered outputs, rst_n is asynchronous active low and clears both
output registers to zero. In combinational mode there is no state to reset;
rst_n does not mask either output. This explicitly completes the original
underspecified reset behavior. Reset slots are not logical observations.
