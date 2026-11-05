# ML-DSA Montgomery Reduction

Reduce one signed product by the ML-DSA Montgomery radix, preserving the signed representative returned by the reduction primitive.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_dilithium_dilithium_montgomery_reduce (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a,

    output logic         out_valid,
    output logic out_error,
    input  logic         out_ready,
    output logic [31:0]  out_r
);
```

Transfers occur on rising clock edges with valid and ready both high and rst low. The input producer holds the whole request stable while stalled. The block holds the whole response, including `out_error`, stable while stalled. Every accepted request produces exactly one ordered response unless reset cancels it. No fixed latency or pipeline architecture is required.

### Behavior interface

```python
class DilithiumMontgomeryReduce:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a): ...
```

Each call represents one accepted input and returns exactly one dictionary
with `out_r` (unsigned 32-bit two's-complement encoding) and `out_error`
(integer 0 or 1). The argument is a raw unsigned 64-bit integer encoding
signed `a`. Invalid inputs return the same shape, never `None` or a list.
The model is stateless; `reset()` returns `None` without emitting a record.

## Modules

- `cryptography_pqc_dilithium_dilithium_montgomery_reduce`: checked integer sub-operator and ready/valid transport.

## Functional Behavior

Decode `in_a` as signed 64-bit `a`. Require `-(2^31*Q) < a < 2^31*Q`, where `Q=8380417`; the bounds are strict.

An out-of-range request returns `out_error=1` and **zero in every numerical output field**. A valid request returns `out_error=0` and the result below. The error flag is ordinary response data, not a missing transaction. No invalid input is silently clamped, normalized or evaluated as a valid operation.

Let `R=2^32` and `QINV=58728449` (so `Q*QINV ≡ 1 mod R`). Form `t=signed32((a*QINV) mod R)` and `r=(a-t*Q)//R`. The numerator is exactly divisible by R. Return the 32-bit two's-complement encoding of `r`.

The signed reinterpretation of `t` is required; it is not an unsigned multiplier. Valid results satisfy `-Q < r < Q` and `r*R ≡ a (mod Q)`. No canonicalization or saturation follows.

All arithmetic is integer arithmetic. Signed results use the output width's two's-complement encoding; specified shifts of negative values are arithmetic. There is no unspecified floating-point behavior.

## Reset Behavior

Reset is asynchronous assertion, active-high. Reset clears `out_valid`, `out_error` and all output payload fields and cancels pending responses. No transfers are counted while reset is asserted. The operation has no mathematical state across requests. After reset it accepts new requests normally; idle payload values after subsequent transfers are otherwise unspecified.
