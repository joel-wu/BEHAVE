# ML-DSA Conditional Modulus Addition

Convert one signed, reduced ML-DSA coefficient to its canonical nonnegative representative using one conditional addition.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_dilithium_dilithium_caddq (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [31:0] out_r
);
```

Transfers occur on rising clock edges with valid and ready both high and rst low. The input producer holds the whole request stable while stalled. The block holds the whole response, including `out_error`, stable while stalled. Every accepted request produces exactly one ordered response unless reset cancels it. No fixed latency or pipeline architecture is required.

### Behavior interface

```python
class DilithiumCaddq:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a): ...
```

Each `process` call consumes one accepted input transaction, not a clock cycle.
`in_a` is an unsigned 32-bit bit pattern encoding the signed input below.
It always returns one dictionary with exactly `out_r` (an unsigned 32-bit
integer) and `out_error` (0 or 1). Out-of-range requests still return both
fields, never `None` or a list. Calls have no persistent mathematical state.
Construction and `reset()` produce no output record; `reset()` returns `None`.

## Modules

- `cryptography_pqc_dilithium_dilithium_caddq`: checked integer sub-operator and ready/valid transport.

## Functional Behavior

Decode `in_a` as signed 32-bit `a`. Require `-Q < a < Q`, with `Q=8380417`.

An out-of-range request returns `out_error=1` and **zero in every numerical output field**. A valid request returns `out_error=0` and the result below. The error flag is ordinary response data, not a missing transaction. No invalid input is silently clamped, normalized or evaluated as a valid operation.

Return `out_r=a+Q` if `a<0`, otherwise `out_r=a`. The result is an unsigned integer in 0..Q-1. This operation is not a general reduction of an arbitrary int32 input.

All arithmetic is integer arithmetic. Signed results use the output width's two's-complement encoding; specified shifts of negative values are arithmetic. There is no unspecified floating-point behavior.

## Reset Behavior

Reset is synchronous, active-high. Reset clears `out_valid`, `out_error` and all output payload fields and cancels pending responses. No transfers are counted while reset is asserted. The operation has no mathematical state across requests. After reset it accepts new requests normally; idle payload values after subsequent transfers are otherwise unspecified.
