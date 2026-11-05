# gcd_top

gcd_top with its native clocked interface.

## Interface

### RTL interface

```systemverilog
module gcd_top #(parameter int WIDTH = 4) (
    input logic clk, rst,
    input logic [WIDTH-1:0] A, B,
    input logic go,
    output logic [WIDTH-1:0] OUT,
    output logic done
);
```

### Behavior interface

```python
class GcdTop:
    def __init__(self, WIDTH=4): ...
    def reset(self): ...
    def process(self, A, B, go): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `OUT`: WIDTH.
- `done`: 1.

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

- `gcd_top`: integration.
- `gcd_controlpath`: registered action FSM and completion pulse.
- `gcd_datapath`: working operands, subtract/compare and held result.

## Functional Behavior

`WIDTH>=1`, default 4. Accept A and B only at an idle edge with go=1.
While computing, ignore go and changes to A/B. Latch both operands; each
subsequent subtraction edge replaces the larger working value by its difference
with the smaller. After equality is reached, the following edge updates OUT,
pulses done for exactly one cycle and returns to idle. OUT holds its previous
result throughout the next computation. No output acknowledgment is required.

If D is the number of subtractions, there are exactly D+2 edges including
acceptance and publication. Equal nonzero operands therefore take two edges;
(1,2**WIDTH-1) takes 2**WIDTH edges, correcting the source's off-by-one worst-case
bound. As an explicit total-domain extension, gcd(0,b)=b, gcd(a,0)=a and gcd(0,0)=0,
also with two-edge completion. Original nonzero inputs are unchanged.

Implement the source's three-module split: gcd_top instantiates gcd_controlpath
and gcd_datapath. Registered action states S0=0 (idle/load), S1=1 (publish),
S2=2 (subtract B from A), S3=3 (subtract A from B) control the datapath.
Comparison feedback is evaluated on the working values AFTER the current
action, so the next action cannot subtract using a stale direction. Publication
and done occur together on the S1 action edge. A new go may be accepted at
the immediately following edge, including while the previous done is high.

## Reset Behavior

`rst` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
