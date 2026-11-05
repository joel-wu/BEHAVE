# ML-DSA Signed Coefficient Reduction

Reduce one bounded signed 32-bit integer to the representative produced by ML-DSA reduce32, retaining its exact rounding convention.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_dilithium_dilithium_reduce32 (
    input  logic               clk,
    input  logic               rst,

    input  logic               in_valid,
    output logic               in_ready,
    input  logic signed [31:0] in_a,

    output logic               out_valid,
    output logic out_error,
    input  logic               out_ready,
    output logic signed [31:0] out_r
);
```

Transfers occur on rising clock edges with valid and ready both high and rst low. The input producer holds the whole request stable while stalled. The block holds the whole response, including `out_error`, stable while stalled. Every accepted request produces exactly one ordered response unless reset cancels it. No fixed latency or pipeline architecture is required.

### Behavior interface

```python
class DilithiumReduce32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a): ...
```

Arguments and returned values are raw nonnegative port bit patterns. One call returns a dictionary containing `out_r` and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_r`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cryptography_pqc_dilithium_dilithium_reduce32`: checked integer sub-operator and ready/valid transport.

## Functional Behavior

Decode `in_a` as signed 32-bit `a`. Require `-2147483648 <= a <= 2143289343` (INT32_MAX minus 2^22).

An out-of-range request returns `out_error=1` and **zero in every numerical output field**. A valid request returns `out_error=0` and the result below. The error flag is ordinary response data, not a missing transaction. No invalid input is silently clamped, normalized or evaluated as a valid operation.

With `Q=8380417`, compute `t=floor((a+4194304)/8388608)` and `r=a-t*Q`. Division rounds toward negative infinity, including negative inputs. Return `r` as a 32-bit two's-complement bit pattern. Valid results satisfy `-6283009 <= r < 6283009` and `r ≡ a (mod Q)`. There is no further normalization or saturation.

All arithmetic is integer arithmetic. Signed results use the output width's two's-complement encoding; specified shifts of negative values are arithmetic. There is no unspecified floating-point behavior.

## Reset Behavior

Reset is synchronous, active-high. Reset clears `out_valid`, `out_error` and all output payload fields and cancels pending responses. No transfers are counted while reset is asserted. The operation has no mathematical state across requests. After reset it accepts new requests normally; idle payload values after subsequent transfers are otherwise unspecified.
