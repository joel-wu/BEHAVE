# cic_decimator

A parameterized, phase-zero CIC decimator with native valid/ready pins.

## Interface

### RTL interface

```systemverilog
module cic_decimator #(
    parameter int WIDTH = 16,
    parameter int RMAX = 2,
    parameter int M = 1,
    parameter int N = 2,
    parameter int REG_WIDTH = WIDTH + $clog2((RMAX * M)**N)
) (
    input logic clk, rst,
    input logic [WIDTH-1:0] input_tdata,
    input logic input_tvalid,
    output logic input_tready,
    output logic [REG_WIDTH-1:0] output_tdata,
    output logic output_tvalid,
    input logic output_tready,
    input logic [((RMAX < 2) ? 1 : $clog2(RMAX+1))-1:0] rate
);
```

### Behavior interface

```python
class CicDecimator:
    def __init__(self, WIDTH=16, RMAX=2, M=1, N=2, REG_WIDTH=0): ...
    def reset(self): ...
    def process(self, input_tdata, input_tvalid, output_tready, rate): ...
```

Constructor parameters match the RTL parameters; `REG_WIDTH=0` selects the
default width defined below. Each call returns exactly
`{"input_tready": ready, "output_tdata": data, "output_tvalid": valid}`:
all settled output pin values immediately BEFORE one rising edge of `clk`,
then advances that edge. `ready` and `valid` are 0 or 1; `data` is an unsigned
`REG_WIDTH`-bit integer encoding the signed sample. Arguments likewise use
unsigned port encodings. Every call, including idle and stalled cycles,
returns the full dictionary, never `None` or a list. In particular, an invalid
output still includes the defined comb data. Integrators, phase and comb
histories persist across calls. Construction and `reset()` clear them;
`reset()` returns `None` without a sampled pin record. Reset is controlled
separately from `process`.

## Modules

- `cic_decimator`: the complete native module above.

## Functional Behavior

This is the original native valid/ready CIC interface, not an elastic
transaction wrapper. WIDTH, RMAX, M and N are positive integers; WIDTH is at
least 2. Require (RMAX*M)**N <= 2**30 so the source default-width expression
is representable. REG_WIDTH defaults to WIDTH+ceil(log2((RMAX*M)**N)); an
override must be at least WIDTH.
The rate port has max(1,ceil(log2(RMAX+1))) bits, so RMAX itself is representable.
Interpret input_tdata as a WIDTH-bit two's-complement sample. All internal
additions/subtractions and delay storage wrap modulo 2**REG_WIDTH. Ports
carry unsigned bit patterns; no saturation or gain correction.

Let c be the phase counter, I[0..N-1] the integrators, and D[k][0..M-1]
each comb stage's prior inputs, newest first. Reset all of them to zero.
Outside reset, the exact combinational handshake equations are:

- input_tready = output_tready OR (c != 0).
- output_tvalid = input_tvalid AND (c == 0).
- input acceptance = input_tvalid AND input_tready.
- output acceptance = output_tvalid AND output_tready.

On each INPUT acceptance, using the OLD integrator values simultaneously:
I[0] := I[0]+sign_extend(input_tdata); I[k] := I[k]+I[k-1] for k>0.
Clamp the current rate to R=min(RMAX,max(1,rate)). Set c=0 if old c>=R-1,
otherwise c=old c+1. A rate change applies at that accepted edge; it does not
reset the filter history or retroactively move the current output boundary.
No input acceptance means no integrator or counter update.

The output is the feed-forward comb of the CURRENT stored last integrator:
C[0]=I[N-1], C[k+1]=C[k]-D[k][M-1], output_tdata=C[N], all at REG_WIDTH.
On each OUTPUT acceptance, shift each D[k] toward older positions and store
the pre-edge C[k] in D[k][0]. No output acceptance means comb histories hold.
Thus M is an M-output-sample differential delay, including the nondegenerate
M=1 case. The comb arithmetic is combinational; its histories are registered.

Phase zero is an output boundary BEFORE the corresponding input edge. The
first offered sample after reset therefore presents output zero; at a constant
rate R, boundaries are input ordinals 0,R,2R,..., not R-1,2R-1,... . The output
uses old integrator state, not the input accepted on that same edge. Between
boundaries, inputs can continue when the output is not ready. At a boundary,
backpressure freezes all filter state, and valid output data stays stable while
input_tvalid is held. Source data/valid must obey ordinary held-valid rules for
legal ready/valid traffic. This timed task also defines pin outputs for arbitrary
bit-pattern stimulus; it does not claim such traffic is protocol-compliant.
output_tdata is the stated comb expression even while output_tvalid=0.
While rst is asserted, input_tready and output_tvalid are zero immediately.
output_tdata remains the old comb value until the synchronous reset edge
clears the histories; it is zero after that edge. Reset never accepts data.

Source completions: the original counter-zero equations conflict with its
summary saying to wait R samples before the first output. Keep the explicit
equations. Its comb prose subtracts delay_reg[0] from delay_reg[M-1], which
degenerates for default M=1; use the intended current-minus-M-prior-input CIC
difference. Specify old-value registered integrators, feed-forward comb timing,
signed sample interpretation, rate zero/clamping/change policy, reset synchrony
and modulo arithmetic explicitly. Finite-width integrators can wrap under a
long input stream; the source's claim that the width prevents ALL accumulation
overflow is not valid. No original answer RTL exists, and the supplied Cocotb
test only prints outputs, so unchanged-reference equivalence is not claimed.


## Reset Behavior

`rst` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
