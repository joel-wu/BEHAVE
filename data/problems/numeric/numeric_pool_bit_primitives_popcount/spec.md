# Thirty-two-bit population count

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module popcount32 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_data,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [5:0]  out_popcount
);
```

### Behavior interface

```python
class Popcount32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_data): ...
```

Each call represents one accepted input and returns exactly
`{"out_popcount": count}`. `in_data` is an unsigned 32-bit integer and `count`
is an integer in 0..32 encoded in the unsigned 6-bit output field. Even a zero
count returns this dictionary, never `None`, a scalar or a list. No handshake
signals are returned, and latency is not represented by extra calls. No state
persists between inputs; construction and `reset()` establish this empty
state, and `reset()` returns `None`.

## Modules

`popcount32` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

Return the number of set bits in the unsigned 32-bit input, an integer in [0,32]. All input patterns are defined. The count is exact; neither a summation order nor a particular adder tree is required.

## Reset Behavior

Reset rst is asynchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
