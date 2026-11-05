# Kyber Degree-One Base Multiplication

Multiply two degree-one polynomials modulo X²−zeta with Kyber Montgomery products. This is the basemul sub-operator used in NTT-domain polynomial multiplication, not an NTT transform or a forward butterfly.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_kyber_kyber_ntt_butterfly (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_a0,
    input  logic [15:0] in_a1,
    input  logic [15:0] in_b0,
    input  logic [15:0] in_b1,
    input  logic [15:0] in_zeta,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [15:0] out_r0,
    output logic [15:0] out_r1
);
```

Transfers occur on rising clock edges with valid and ready both high and rst low. The input producer holds the whole request stable while stalled. The block holds the whole response, including `out_error`, stable while stalled. Every accepted request produces exactly one ordered response unless reset cancels it. No fixed latency or pipeline architecture is required.

### Behavior interface

```python
class CryptographyPqcKyberKyberNttButterfly:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a0, in_a1, in_b0, in_b1, in_zeta): ...
```

Arguments and returned values are raw nonnegative port bit patterns. One call returns a dictionary containing `out_r0`, `out_r1` and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_r0`, `out_r1`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cryptography_pqc_kyber_kyber_ntt_butterfly`: checked integer sub-operator and ready/valid transport.

## Functional Behavior

Decode each 16-bit input as a signed integer. Every coefficient and `in_zeta` must satisfy `-3329 < c < 3329`. This accepts both centered and nonnegative reduced representatives; it does not silently reduce arbitrary int16 values.

An out-of-range request returns `out_error=1` and **zero in every numerical output field**. A valid request returns `out_error=0` and the result below. The error flag is ordinary response data, not a missing transaction. No invalid input is silently clamped, normalized or evaluated as a valid operation.

Let `q=3329`, `R=65536` and `QINV=62209`. Define `MR(x)=(x-t*q)//R`, with **signed** `t=signed16((x*QINV) mod R)`, and `F(a,b)=MR(a*b)`. These divisions are exact. Coefficients, zeta and every F result are signed integers.

Compute, in order:

```text
p0 = F(a1,b1)
p1 = F(p0,zeta)
r0 = p1 + F(a0,b0)
r1 = F(a0,b1) + F(a1,b0)
```

Return `r0` and `r1` as 16-bit two's-complement values. Do not replace them with canonical residues modulo q: the signed representatives are observable. The admitted coefficient bounds keep all Montgomery calls within their product bounds and both final sums within int16. Zeta is the caller-supplied NTT-domain factor, including its Montgomery scaling; no extra conversion is performed.

All arithmetic is integer arithmetic. Signed results use the output width's two's-complement encoding; specified shifts of negative values are arithmetic. There is no unspecified floating-point behavior.

## Reset Behavior

Reset is synchronous, active-high. Reset clears `out_valid`, `out_error` and all output payload fields and cancels pending responses. No transfers are counted while reset is asserted. The operation has no mathematical state across requests. After reset it accepts new requests normally; idle payload values after subsequent transfers are otherwise unspecified.
