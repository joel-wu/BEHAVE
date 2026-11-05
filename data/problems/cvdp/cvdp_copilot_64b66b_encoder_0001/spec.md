# 64b/66b Data Encoder

Register one 66-bit encoded word per clock edge. The native interface has no ready/valid handshake or backpressure.

## Interface

### RTL interface

```systemverilog
module encoder_64b66b (
    input logic clk_in,
    input logic rst_in,
    input logic [63:0] encoder_data_in,
    input logic [7:0] encoder_control_in,
    output logic [65:0] encoder_data_out
);
```

### Behavior interface

```python
class Encoder64b66b:
    def __init__(self): ...
    def reset(self): ...
    def process(self, encoder_data_in, encoder_control_in): ...
```

Input arguments and returned fields are unsigned port bit patterns of the
declared widths. Each call returns a dict containing `encoder_data_out`.
It reports the settled output pins immediately **before** one non-reset rising
edge, then advances the state for that edge. The updated result is visible
immediately after the edge and at the next pre-edge observation. This convention
does not add a pipeline register or change the native one-edge update.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `encoder_data_out`: 66.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk_in`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `encoder_64b66b`: the native clocked datapath and its state.

## Functional Behavior

If `encoder_control_in == 0`, the next output is `{2'b01, encoder_data_in}`. Otherwise it is `{2'b10, 64'b0}`. This is the original data-only subset: no scrambling or control-character encoding is added. All 256 control masks are legal; any nonzero mask selects the zero control payload.

## Reset Behavior

`rst_in` is asynchronous and active high. Assertion clears all state
and output registers to zero, and they remain zero while reset is active.
After release, each rising edge of `clk_in` advances normally. Reset samples do
not produce logical observation transactions. There is no input enable beyond
the native data/control pins listed above.
