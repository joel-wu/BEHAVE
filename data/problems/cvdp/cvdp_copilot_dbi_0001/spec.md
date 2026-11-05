# Data Bus Inversion Encoder

Encode a 40-bit stream as two independent 20-bit DBI groups. The native interface has no ready/valid handshake or backpressure.

## Interface

### RTL interface

```systemverilog
module dbi_enc (
    input logic clk,
    input logic rst_n,
    input logic [39:0] data_in,
    output logic [39:0] data_out,
    output logic [1:0] dbi_cntrl
);
```

### Behavior interface

```python
class DbiEnc:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: 40.
- `dbi_cntrl`: 2.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `dbi_enc`: the native clocked datapath and its state.

## Functional Behavior

At each edge compare each input group with the corresponding group of the old `data_out`, not the previous unencoded input. Count differing bits. More than 10 differences sets that group's control bit and inverts all 20 input bits; otherwise copy the input and clear its control bit. Exactly 10 differences does not invert. Group 0 occupies bits 19:0 and control bit 0; group 1 occupies bits 39:20 and control bit 1. Register both outputs together; this encoded output is the next comparison baseline.

## Reset Behavior

`rst_n` is asynchronous and active low. Assertion clears all state
and output registers to zero, and they remain zero while reset is active.
After release, each rising edge of `clk` advances normally. Reset samples do
not produce logical observation transactions. There is no input enable beyond
the native data/control pins listed above.
