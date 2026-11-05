# cvdp_prbs_gen

A continuously clocked, parallel-word PRBS generator or self-synchronizing checker.

## Interface

### RTL interface

```systemverilog
module cvdp_prbs_gen #(
    parameter int CHECK_MODE = 0, parameter int POLY_LENGTH = 31,
    parameter int POLY_TAP = 3, parameter int WIDTH = 16
) (
    input logic clk, rst,
    input logic [WIDTH-1:0] data_in,
    output logic [WIDTH-1:0] data_out
);
```

### Behavior interface

```python
class CvdpPrbsGen:
    def __init__(self, CHECK_MODE=0, POLY_LENGTH=31, POLY_TAP=3, WIDTH=16): ...
    def reset(self): ...
    def process(self, data_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: WIDTH.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `cvdp_prbs_gen`: the complete native module above.

## Functional Behavior

`CHECK_MODE` is 0 or 1, `POLY_LENGTH >= 1`,
`1 <= POLY_TAP <= POLY_LENGTH`, and `WIDTH >= 1`. POLY_TAP is one integer.
The stage vector s is numbered in ordinary RTL order, with bit 0 the LSB.
The tap's position is counted from the MSB, starting at one: each bit-step
uses `feedback = s[POLY_LENGTH-POLY_TAP] XOR s[0]`.

On EVERY released rising edge, perform WIDTH successive bit-steps in order
i=0,...,WIDTH-1. In generator mode, output bit i is feedback, then shift s
right and insert feedback at its MSB. data_in has no effect. In checker mode,
output bit i is feedback XOR data_in[i], and insert data_in[i] rather than
feedback. Thus the checker reconstructs its state from received bits: an error
can affect subsequent checks, and clean input restores synchronization after
POLY_LENGTH received bits have replaced the state.

Register the complete output word and final stage state at that edge. The
earliest generated/checked bit occupies output bit 0. Outputs hold between
edges. Nonzero checker output marks bitwise discrepancies, not a sticky alarm.
There is no enable, ready/valid or backpressure. An arbitrary valid tap pair
need not give a maximal-period sequence; coincident taps are permitted and
their feedback is zero. The one-stage case follows the same equations.

## Reset Behavior

`rst` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All POLY_LENGTH stage bits and all WIDTH output bits are one.
After reset release, resume the edge-by-edge behavior above.
