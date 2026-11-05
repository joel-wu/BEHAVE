# signed_comparator

A native parameterized signed_comparator with optional output registers.

## Interface

### RTL interface

```systemverilog
module signed_comparator #(
    parameter int DATA_WIDTH = 16, parameter int REGISTER_OUTPUT = 0,
    parameter int ENABLE_TOLERANCE = 0, parameter int TOLERANCE = 0,
    parameter int SHIFT_LEFT = 0
) (
    input logic clk, rst_n, enable, bypass,
    input logic signed [DATA_WIDTH-1:0] a, b,
    output logic gt, lt, eq
);
```

### Behavior interface

```python
class SignedComparator:
    def __init__(self, DATA_WIDTH=16, REGISTER_OUTPUT=0, ENABLE_TOLERANCE=0, TOLERANCE=0, SHIFT_LEFT=0): ...
    def reset(self): ...
    def process(self, enable, bypass, a, b): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `eq`, `gt`, `lt`: 1.

Arguments a and b use the signed integer range of DATA_WIDTH; controls and returned
flags are integers 0 or 1.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `signed_comparator`: the complete native module above.

## Functional Behavior

`DATA_WIDTH>=1`; REGISTER_OUTPUT and ENABLE_TOLERANCE are 0 or 1.
SHIFT_LEFT and TOLERANCE are nonnegative integers. Defaults are 16,0,0,0,0
in the declaration order. Inputs a,b are DATA_WIDTH-bit two's-complement values.

Shift each input left by SHIFT_LEFT, retaining DATA_WIDTH bits, and interpret
the result as signed. This explicitly fixes the source's unspecified shift
precision. A shift at least DATA_WIDTH produces zero; width 8 input 64 shifted
by one becomes -128. Compute the difference and its magnitude without losing
overflow (DATA_WIDTH+1 bits suffice), including the two signed extrema.

Priority: bypass=1 forces (gt,lt,eq)=(0,0,1), even if enable=0. Otherwise
enable=0 produces (0,0,0), an explicit completion of the source's disabled case.
If enabled with tolerance and the magnitude of the shifted difference is at
most TOLERANCE, return equality; the threshold is inclusive. Otherwise compare
the two shifted signed values normally. Tolerance disabled ignores TOLERANCE.

REGISTER_OUTPUT=0: flags follow current inputs combinationally; clk/rst_n
have no effect. REGISTER_OUTPUT=1: capture all three flags on every rising
edge and hold them between edges, until reset. Bypass and enable are part of
the captured computation, not asynchronous overrides of that register.
Pre-edge observations show the preceding captured flags only in registered mode. Do not introduce handshakes or elastic buffering.

## Reset Behavior

With REGISTER_OUTPUT=1, rst_n is asynchronous active-low: assertion immediately
clears all output registers to zero, regardless of input, bypass or enable.
After release, the first rising edge captures the current computation.
With REGISTER_OUTPUT=0, no state exists: rst_n and clk are unused and do not
gate the combinational outputs.
