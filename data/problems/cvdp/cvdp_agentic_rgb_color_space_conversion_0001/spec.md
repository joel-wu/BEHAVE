# rgb_color_space_hsv

Programmable reciprocal-table RGB-to-HSV conversion with eight pipeline edges and independent clocked table writes.

## Interface

### RTL interface

```systemverilog
module rgb_color_space_hsv (
    input logic clk, init_reset, rst,
    input logic we,
    input logic [7:0] waddr,
    input logic [24:0] wdata,
    input logic valid_in,
    input logic [7:0] r_component, g_component, b_component,
    output logic [11:0] h_component,
    output logic [12:0] s_component,
    output logic [11:0] v_component,
    output logic valid_out
);
```

### Behavior interface

```python
class RgbColorSpaceHsv:
    def __init__(self): ...
    def reset(self): ...
    def process(self, rst, we, waddr, wdata, valid_in, r_component, g_component, b_component): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `h_component`, `v_component`: 12.
- `s_component`: 13.
- `valid_out`: 1.

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

- `rgb_color_space_hsv`: the complete native module above.

## Functional Behavior

Convert unsigned 8-bit R/G/B into H in quarter-degrees, S in units
of 1/4096 and V as the unscaled maximum channel (0..255). Retain the programmable
256-word, 25-bit unsigned reciprocal RAM L. There is no ready/valid handshake,
output backpressure or queue. `valid_in=1` captures a sample at every rising edge
outside reset, even when another sample is in flight. `valid_in=0` inserts a
bubble. When `valid_out=0`, all three output data pins are zero.

### Arithmetic

For a captured sample, let M=max(R,G,B), m=min(R,G,B), D=M-m. Prefer the red
branch on a maximum tie, then green, then blue. The signed difference f and
quarter-degree offset o are respectively (G-B,0), (B-R,480), or (R-G,960).
Using the two table values A and B sampled at the edges specified below:

- V=M.
- S=0 if M=0; otherwise S=floor((D*A+2048)/4096) modulo 8192. This is
  round-half-up followed by 13-bit wrapping, not saturation.
- H=0 if D=0; otherwise H=(floor(60*f*B/2**22)+o) modulo 1440, with a
  NONNEGATIVE remainder. Negative products round toward negative infinity.
  Keep the full signed product before the shift; do not wrap a 12-bit signed
  intermediate before normalization. Normalize by full modulo, not one add.

All 25-bit table values are legal, including zero and values above 2**24.
With L[0]=0 and L[n]=floor(2**24/n) for n=1..255, these equations implement
the intended fixed-point RGB-to-HSV conversion. Other programming intentionally
changes the computation; it need not represent a physical saturation fraction.
The exact bit-pattern equations, not an independent floating-point tolerance,
define correctness. In particular, H=1439 and H=0 are adjacent on the hue circle.

### Clocked lookup, writes and latency

Number a sample's accepting edge k as pipeline edge 1. Its lookup of A=L[M]
occurs at edge k+2 (edge 3); its lookup of B=L[D] occurs at edge k+3 (edge 4).
Its result and valid_out appear immediately after edge k+7 (edge 8), remain
through the next edge's pre-edge observation, and are replaced on the next
edge by that next sample's result or an all-zero bubble. This definition fixes
edge counting rather than adding eight edges after the first pipeline edge.
Every stage advances on every released edge; bubbles do not stall older work.

Independently, every rising edge with we=1 writes L[waddr]=wdata, including
edges while the original rst pin is high. A lookup and a write at the same
address on the same edge use the OLD word for the lookup (read-before-write).
A later lookup sees the new word. Writes and input capture may coincide; writes
are not conversion transactions and do not themselves assert valid_out.

### Original source and explicit completion

The source gives no answer RTL. Its detailed multiplier description prescribes
hue truncation and saturation rounding, whereas its testbench uses a rounded
floating-point hue with tolerance. The equations above choose the multiplier
rule and resolve contradictory product widths. The source's raw V scaling is
retained; no extra 12-bit input normalization is invented. Its lookup prose and
8-cycle pipeline are made precise by the edge contract above; internal register
placement is otherwise unrestricted. Supporting consecutive valid inputs is a
specified extension of the source's final, more restrictive pacing sentence.

The original asynchronous rst clears ONLY the conversion pipeline and outputs,
discarding every in-flight sample. It does not erase RAM or prevent clocked RAM
writes; the original TB programs the table while rst is asserted. Added
init_reset initializes both RAM and pipeline to zero, separately from rst.
This pin makes initial and reset-restoration state reproducible without changing
the original reset/write behavior. Startup without initialization is unspecified.
No claim of unchanged upstream RTL/TB equivalence is made.

## Reset Behavior

`init_reset` is asynchronous, active high. Assertion immediately restores the initial state. All table words, pipeline registers and output pins are zero. The original rst is a separate pipeline-only asynchronous reset; its sampled pin is included in process inputs.
After reset release, resume the edge-by-edge behavior above.
