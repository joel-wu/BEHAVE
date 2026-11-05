# Six-element integer absolute sum

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module vector_absolute_sum6 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_x0,
    input  logic [31:0]  in_x1,
    input  logic [31:0]  in_x2,
    input  logic [31:0]  in_x3,
    input  logic [31:0]  in_x4,
    input  logic [31:0]  in_x5,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [34:0]  out_sum
);
```

### Behavior interface

```python
class VectorAbsoluteSum6:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sum`: 35.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`vector_absolute_sum6` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Interpret the six 32-bit inputs as signed two's-complement integers and return their exact sum of absolute values. In particular, abs(-2^31)=2^31; six such terms produce 6*2^31. The output is unsigned and zero-extended to its declared width. This keeps the DASUM reduction but specializes it to six integer elements, not IEEE double-precision or an unstated fixed-point format.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
