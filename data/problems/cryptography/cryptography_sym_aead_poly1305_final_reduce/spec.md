# Poly1305 final reduction and pad addition

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module Poly1305FinalReduce (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_h0,
    input  logic [31:0]  in_h1,
    input  logic [31:0]  in_h2,
    input  logic [31:0]  in_h3,
    input  logic [31:0]  in_h4,
    input  logic [31:0]  in_s0,
    input  logic [31:0]  in_s1,
    input  logic [31:0]  in_s2,
    input  logic [31:0]  in_s3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_h0,
    output logic [31:0]  out_h1,
    output logic [31:0]  out_h2,
    output logic [31:0]  out_h3
);
```

### Behavior interface

```python
class Poly1305FinalReduce:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_h0, in_h1, in_h2, in_h3, in_h4, in_s0, in_s1, in_s2, in_s3): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_h0`, `out_h1`, `out_h2`, `out_h3`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`Poly1305FinalReduce` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Pack five 32-bit h limbs into an unsigned 160-bit H and four s limbs into S, both little-limb first. Twice compute G=(H+5-2^130) mod 2^160; replace H by G only if G<2^159. Return (H+S) mod 2^128 as the four little-endian output limbs out_h0..out_h3.

For a valid lazy Poly1305 accumulator H<2^131 this is (H mod (2^130-5)+S) mod 2^128. The two conditional subtractions also define all other 160-bit patterns, but are not a general reduction of an arbitrary 160-bit number. The pad is not clamped; this task performs no message processing.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
