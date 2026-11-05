# Dilithium eight-pair NTT block

This is one fixed length-eight butterfly block from the forward Dilithium NTT, not a complete 256-coefficient transform.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_dilithium_dilithium_ntt_butterfly (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_a0,
    input  logic [31:0]  in_a1,
    input  logic [31:0]  in_a2,
    input  logic [31:0]  in_a3,
    input  logic [31:0]  in_a4,
    input  logic [31:0]  in_a5,
    input  logic [31:0]  in_a6,
    input  logic [31:0]  in_a7,
    input  logic [31:0]  in_b0,
    input  logic [31:0]  in_b1,
    input  logic [31:0]  in_b2,
    input  logic [31:0]  in_b3,
    input  logic [31:0]  in_b4,
    input  logic [31:0]  in_b5,
    input  logic [31:0]  in_b6,
    input  logic [31:0]  in_b7,
    input  logic [31:0]  in_zeta,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_a0,
    output logic [31:0]  out_a1,
    output logic [31:0]  out_a2,
    output logic [31:0]  out_a3,
    output logic [31:0]  out_a4,
    output logic [31:0]  out_a5,
    output logic [31:0]  out_a6,
    output logic [31:0]  out_a7,
    output logic [31:0]  out_b0,
    output logic [31:0]  out_b1,
    output logic [31:0]  out_b2,
    output logic [31:0]  out_b3,
    output logic [31:0]  out_b4,
    output logic [31:0]  out_b5,
    output logic [31:0]  out_b6,
    output logic [31:0]  out_b7
);
```

All coefficient and twiddle ports carry 32-bit two's-complement signed integers packed as raw bit patterns.

### Behavior interface

```python
class CryptographyPqcDilithiumDilithiumNttButterfly:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
        in_zeta
    ): ...
```

`process(...)` returns one dict with keys:
`out_a0` through `out_a7`, and `out_b0` through `out_b7`.

One call represents one accepted eight-pair block. Return all sixteen fields
in a single dictionary, not sixteen records or a list. Arguments and fields
are unsigned 32-bit integers carrying signed two's-complement encodings.
The return is never `None`. The model is stateless; `reset()` returns `None`
and emits no record.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_pqc_dilithium_dilithium_ntt_butterfly` implements the public interface and operation specified here.

## Functional Behavior

This is one fixed length-eight butterfly block from the forward Dilithium NTT, not a complete 256-coefficient transform. Interpret all inputs as signed 32-bit. Let q=8380417, qinv=58728449 and s32 denote signed narrowing to 32 bits. For each i=0..7 independently, compute x=in_bi*in_zeta, u=s32(x*qinv), t=(x-u*q) >> 32, then return out_ai=(in_ai+t) modulo 2^32 and out_bi=(in_ai-t) modulo 2^32. The shift is arithmetic; the product and subtraction preceding it are exact signed 64-bit values for these input widths.

All eight pairs participate, using one shared Montgomery-form twiddle. No final canonical reduction, transform permutation or normalization is included. All raw input patterns have the explicit wrapped-output semantics above. The source's tighter NTT range contracts explain its intended residues; they do not discard lanes or restrict this interface to a smaller word width.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
