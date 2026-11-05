# Unsigned 192-by-64 division

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module uint192_by_uint64_divider (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_numerator_0,
    input  logic [63:0]  in_numerator_1,
    input  logic [63:0]  in_numerator_2,
    input  logic [63:0]  in_denominator,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_quotient_0,
    output logic [63:0]  out_quotient_1,
    output logic [63:0]  out_quotient_2,
    output logic [63:0]  out_remainder
);
```

### Behavior interface

```python
class UInt192ByUInt64Divider:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_numerator_0, in_numerator_1, in_numerator_2, in_denominator): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_quotient_0`, `out_quotient_1`, `out_quotient_2`, `out_remainder`: 64.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`uint192_by_uint64_divider` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Pack the three 64-bit numerator limbs little-endian into N=in_numerator_0+2^64*in_numerator_1+2^128*in_numerator_2. For nonzero unsigned denominator D, return the three little-endian limbs of floor(N/D) and the 64-bit remainder N mod D. All numerator bits participate, including the high limb. For D=0 return zero for every output as an explicit task extension; no quotient or remainder identity is claimed in that case. Any implementation yielding these values is allowed.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
