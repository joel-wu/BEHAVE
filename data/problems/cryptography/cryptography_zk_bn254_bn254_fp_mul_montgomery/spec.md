# BN254 Fr Montgomery Multiplication

Multiply canonical Montgomery residues over BN254's scalar field with an explicit invalid-input response.

## Interface

### RTL interface

```systemverilog
module Bn254FrMontgomeryMultiply (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [253:0] in_a,
    input logic [253:0] in_b,
    output logic out_valid,
    input logic out_ready,
    output logic [253:0] out_c,
    output logic out_error
);
```

### Behavior interface

```python
class Bn254FrMontgomeryMultiply:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a, in_b): ...
```

process returns one dictionary containing `out_c`, `out_error`.

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c`: 254.
- `out_error`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Ports use unsigned payload bit patterns unless stated otherwise. An input is accepted on a rising edge with in_valid && in_ready, and an output is emitted with out_valid && out_ready. Once presented, valid and its payload remain stable until accepted; reset cancels pending transfers. Latency is unconstrained. Each accepted input transaction produces exactly one output transaction, in input order.

## Modules

`Bn254FrMontgomeryMultiply` is the complete top-level module.

## Functional Behavior

Use the BN254 **scalar field Fr** modulus p=21888242871839275222246405745257275088548364400416034343698204186575808495617 and radix R=2^256. The two 254-bit inputs are canonical Montgomery residues: a=in_a and b=in_b must both be less than p. Invalid requests return out_error=1 and out_c=0.

Valid requests return out_error=0 and the unique c in [0,p) satisfying c*R = a*b (mod p). In REDC form, let n0=52454480824480482120356829342366457550537710351690908576382634413609933864959, so p*n0 = -1 (mod R); form t=a*b, m=(t*n0) mod R, and u=(t+m*p)/R; return u-p if u>=p, otherwise u. Division here is exact and no carry is discarded. There is no automatic conversion into or out of Montgomery form. For a=R mod p and b=R mod p, the output is R mod p.

## Reset Behavior

Reset is synchronous active-high rst. It empties pending output; out_valid is low during reset. There is no persistent arithmetic state. A new request after reset is independent of requests before reset.
