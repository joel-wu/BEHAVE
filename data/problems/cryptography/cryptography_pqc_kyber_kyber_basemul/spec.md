# Kyber degree-one base multiplication

Inputs and outputs are signed 16-bit values transported as unsigned bit patterns.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_kyber_kyber_basemul (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a0,
    input  logic [15:0]  in_a1,
    input  logic [15:0]  in_b0,
    input  logic [15:0]  in_b1,
    input  logic [15:0]  in_zeta,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_r0,
    output logic [15:0]  out_r1
);
```

All five input data ports and both output data ports carry 16-bit two's-complement integers. `in_a0`, `in_a1`, `in_b0`, `in_b1`, and `in_zeta` are interpreted as signed `int16_t` values. `out_r0` and `out_r1` are returned as 16-bit two's-complement bit patterns.

### Behavior interface

```python
class KyberBaseMul:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a0, in_a1, in_b0, in_b1, in_zeta): ...
```

`process(...)` returns a dict with keys `out_r0` and `out_r1`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_r0`, `out_r1`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_pqc_kyber_kyber_basemul` implements the public interface and operation specified here.

## Functional Behavior

Inputs and outputs are signed 16-bit values transported as unsigned bit patterns. Define s16(v) as signed reinterpretation of its low 16 bits and M(x)=(x-3329*s16(x*62209)) >> 16. The shift is arithmetic. Define F(a,b)=s16(M(s16(a)*s16(b))).

With signed input coefficients a0,a1,b0,b1,zeta, return:

```
out_r0 = (F(F(a1,b1),zeta) + F(a0,b0)) modulo 2^16
out_r1 = (F(a0,b1) + F(a1,b0)) modulo 2^16
```

The Montgomery temporary is signed, and each F result is narrowed before its next use, as in the source. No final Barrett normalization is performed. This is the Kyber NTT-domain quadratic block with Montgomery-scaled twiddle, not ordinary unscaled integer polynomial multiplication. All five 16-bit patterns are accepted; narrowing is defined as two's-complement wrap.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
