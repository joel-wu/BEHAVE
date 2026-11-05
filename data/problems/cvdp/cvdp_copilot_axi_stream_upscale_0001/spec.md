# AXI Stream Data Upsizer

Convert each 24-bit stream word to one 32-bit word, with optional MSB inversion
and sign extension. Preserve the native single output-register stage.

## Interface

### RTL interface

```systemverilog
module axis_upscale (
    input logic clk,
    input logic resetn,
    input logic dfmt_enable,
    input logic dfmt_type,
    input logic dfmt_se,
    input logic s_axis_valid,
    input logic [23:0] s_axis_data,
    output logic s_axis_ready,
    input logic m_axis_ready,
    output logic m_axis_valid,
    output logic [31:0] m_axis_data
);
```

### Behavior interface

```python
class AxisUpscale:
    def __init__(self): ...
    def reset(self): ...
    def process(self, dfmt_enable, dfmt_type, dfmt_se, s_axis_data): ...
```

One call represents a rising-edge `s_axis_valid && s_axis_ready` transfer.
Arguments are unsigned pin bit patterns; the return dict contains `m_axis_data`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `m_axis_data`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs transfer on a rising-edge `s_axis_valid && s_axis_ready` handshake.
The format controls and payload stay stable while valid is stalled. Each
accepted input yields one output, in order, on a subsequent
`m_axis_valid && m_axis_ready` edge.

## Modules

- `axis_upscale`: format conversion and one elastic output register.

## Functional Behavior

Let `b = s_axis_data[23] XOR (dfmt_enable AND dfmt_type)`.
Set output bits 22:0 to input bits 22:0 and output bit 23 to `b`.
Output bits 31:24 are eight copies of `b` when both `dfmt_enable` and
`dfmt_se` are one, and zero otherwise. Formatting disabled therefore gives
zero-extension of the unchanged 24-bit input. Inversion changes bit 23 itself,
even when sign extension is disabled.

`s_axis_ready = resetn && (!m_axis_valid || m_axis_ready)`. An accepted input
fills the output register on that edge; its output transfer can first occur on
the next edge. Consuming an output and accepting its replacement may happen on
the same edge. While the output is stalled, valid and data remain unchanged.
After consumption without replacement, valid clears and data retains its last
value. No output is produced without an accepted input.

## Reset Behavior

`resetn` is synchronous and active low. On a rising edge with reset asserted,
clear `m_axis_valid` and `m_axis_data`, discarding any pending word.
Input ready is low during reset and high after release when the register is empty.
There are no asynchronous-reset or extra handshake ports.
