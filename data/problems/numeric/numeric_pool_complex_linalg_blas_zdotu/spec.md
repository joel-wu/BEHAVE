# Eight-pair complex integer unconjugated dot product

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module complex_dot_product_streaming (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x_re,
    input  logic [31:0] in_x_im,
    input  logic [31:0] in_y_re,
    input  logic [31:0] in_y_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_z_re,
    output logic [63:0] out_z_im
);
```

### Behavior interface

```python
class ComplexDotProductStreaming:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x_re, in_x_im, in_y_re, in_y_im): ...
```

Each `process` call consumes one accepted complex pair. Pass all four
arguments as unsigned 32-bit integers encoding signed two's-complement
components. Calls 1..7 of a group return `None`. Call 8 returns one dictionary
with exactly `out_z_re` and `out_z_im`, both unsigned 64-bit integers encoding
the modular accumulators, and starts a fresh group. A zero result is still
a dictionary; never return a list. Accumulators and count persist between
calls. Construction and `reset()` clear a partial group; `reset()` returns
`None` without emitting it. Do not add calls for physical idle or stall cycles.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each group of 8 accepted inputs produces one output transaction; partial groups remain pending until completed or reset.

## Modules

`complex_dot_product_streaming` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Each accepted input contains x and y with signed 32-bit real and imaginary components. Add xr*yr-xi*yi to the real accumulator and xr*yi+xi*yr to the imaginary accumulator, modulo 2^64. After eight accepted inputs, emit the two 64-bit accumulators and reset the group; the first seven produce no transaction.

This 8:1 integer ZDOTU specialization does not conjugate x. Grouping follows acceptance order across arbitrary input gaps and output stalls, and a complete group does not require a ninth flush input.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. The partial reduction and accepted-item count are cleared. Output payload registers reset to zero.
