# Eight-coefficient negacyclic shift

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module negacyclic_shift_poly_coeffmod (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_c0,
    input  logic [63:0]  in_c1,
    input  logic [63:0]  in_c2,
    input  logic [63:0]  in_c3,
    input  logic [63:0]  in_c4,
    input  logic [63:0]  in_c5,
    input  logic [63:0]  in_c6,
    input  logic [63:0]  in_c7,
    input  logic [2:0]   in_shift,
    input  logic [63:0]  in_modulus,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_c0,
    output logic [63:0]  out_c1,
    output logic [63:0]  out_c2,
    output logic [63:0]  out_c3,
    output logic [63:0]  out_c4,
    output logic [63:0]  out_c5,
    output logic [63:0]  out_c6,
    output logic [63:0]  out_c7
);
```

### Behavior interface

```python
class NegacyclicShiftPolyCoeffMod:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_c0, in_c1, in_c2, in_c3, in_c4, in_c5, in_c6, in_c7, in_shift, in_modulus): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c0`, `out_c1`, `out_c2`, `out_c3`, `out_c4`, `out_c5`, `out_c6`, `out_c7`: 64.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`negacyclic_shift_poly_coeffmod` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Let a[i] be in_ci for i=0..7, shift=in_shift, and q=in_modulus. The coefficients and q are unsigned 64-bit words; shift is in [0,7]. Multiplication by x^shift modulo x^8+1 moves input coefficient a[i] to j=(i+shift) mod 8. If i+shift<8, copy a[i]; otherwise output zero when a[i]=0 and (q-a[i]) mod 2^64 when a[i]!=0.

On q>0 and coefficients in [0,q), this is the negacyclic shift over Z/qZ. The same formula defines q=0 and out-of-range coefficients without treating them as canonical residues. No particular buffering or shift network is required.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
