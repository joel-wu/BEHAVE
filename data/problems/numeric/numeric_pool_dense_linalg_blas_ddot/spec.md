# Five-pair signed integer dot product

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module dot_product5 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [15:0] in_dx,
    input  logic signed [15:0] in_dy,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [34:0] out_ddot
);
```

### Behavior interface

```python
class DotProduct5:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_dx, in_dy): ...
```

Call process once per accepted input, not once per latency cycle.

Return None for the first four samples of each five-sample group, then one dictionary
for the fifth. Never return a list. Reset discards a partial group.
Every returned dictionary contains exactly these fields (bit widths):

- `out_ddot`: 35.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each group of 5 accepted inputs produces one output transaction; partial groups remain pending until completed or reset.

## Modules

`dot_product5` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Each accepted input contributes signed16(a)*signed16(b) to the current group. The first four accepted inputs produce no transaction; the fifth emits the exact five-term sum encoded in two's-complement 35 bits, then starts a fresh group. Acceptance order, not elapsed cycles, defines groups. This is a 5:1 integer specialization of DDOT. Input gaps and output stalls must not change grouping or require extra flush inputs.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. The partial reduction and accepted-item count are cleared. Output payload registers reset to zero.
