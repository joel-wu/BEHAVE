# multi_booth_8bit

Signed 8x8 iterative multiplier with operand-bearing restart and visible partial products.

## Interface

### RTL interface

```systemverilog
module multi_booth_8bit (input logic clk, init_reset, load, input logic [7:0] a, b, output logic [15:0] p, output logic rdy);
```

### Behavior interface

```python
class MultiBooth8Bit:
    def __init__(self): ...
    def reset(self): ...
    def process(self, load, a, b): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `p`: 16.
- `rdy`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `multi_booth_8bit`: the complete native module above.

## Functional Behavior

The task preserves the supplied implementation's signed
8x8 iterative multiplication and externally visible partial products. Despite
the historical name, the supplied algorithm is a sixteen-step binary shift/add
over sign-extended operands, NOT radix-4 Booth recoding.

At a rising edge with load=1, capture a and b, sign-extend each to sixteen bits,
clear product p and rdy, and set iteration count to zero. This discards any
previous operation, even if unfinished. At each following edge with load=0 and
count<16, add the current shifted b to p iff bit `count` of the captured,
sign-extended a is one; then shift b left one bit and increment count. Both
addition and shift wrap modulo 65536. All actions use pre-edge values. p exposes
each intermediate sum, not only the completed product. Changes to a/b during
load=0 have no effect. After sixteen computation edges p is the exact signed
product modulo 65536, but rdy is still zero. The SEVENTEENTH released edge sets
rdy=1. Thereafter p and rdy hold until load or initialization. Repeated load
edges repeatedly capture the latest operands and perform no computation.

Equivalently, after j computation edges (0<=j<=16),
`p = ((sign_extend16(a) & (2**j-1)) * signed8(b)) mod 65536`.

### Explicit operand-reset adaptation

The original asynchronous reset both initializes the machine AND loads the
current operands. That operation becomes the ordinary synchronous input `load`.
The added synchronous high init_reset initializes the working registers, p,
count and rdy to zero independently of a/b. At init_reset=0, the original
non-reset edge recurrence and completion timing are retained. Async operand
capture between clock edges is intentionally not part of this adapted task.
After initialization alone, a zero product computation runs autonomously and
rdy becomes one on its seventeenth released edge; there is no invented idle
state or ready/valid protocol. Startup before initialization is unspecified.

## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All registers are zero; p=0 and rdy=0. Released clocks then execute the zero product operation.
After reset release, resume the edge-by-edge behavior above.
