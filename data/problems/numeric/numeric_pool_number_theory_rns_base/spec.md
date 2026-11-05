# Centered CRT base conversion

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module rns_exact_base_convert_q_to_t (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x0,
    input  logic [15:0] in_x1,
    input  logic [15:0] in_x2,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y
);
```

### Behavior interface

```python
class RnsExactBaseConvertQToT:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`rns_exact_base_convert_q_to_t` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Normalize x0,x1,x2 modulo 7,11,13 respectively. There is a unique integer X in [-500,500] with these three residues, since 7*11*13=1001. Return X mod 17 in [0,16], zero-extended to 16 bits.

The representative is centered, not the nonnegative CRT representative in [0,1000]. This is the small fixed-base specialization of the source exact base-conversion routine's nearest-integer correction. All unsigned 16-bit input patterns are normalized as above.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
