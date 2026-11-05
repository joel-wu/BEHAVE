# Kyber Polynomial Serialization — Coefficient Pair

Serialize two reduced Kyber coefficients to their three-byte packed representation. Ordered pairs compose the full polynomial serialization.

## Interface

### RTL interface

```systemverilog
module KyberPolyToBytesPair (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_coeff0,
    input  logic [15:0] in_coeff1,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [7:0]  out_byte0,
    output logic [7:0]  out_byte1,
    output logic [7:0]  out_byte2
);
```

Transfers occur on rising clock edges with valid and ready both high and rst low. The input producer holds the whole request stable while stalled. The block holds the whole response, including `out_error`, stable while stalled. Every accepted request produces exactly one ordered response unless reset cancels it. No fixed latency or pipeline architecture is required.

### Behavior interface

```python
class KyberPolyToBytesPair:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_coeff0, in_coeff1): ...
```

Arguments and returned values are raw nonnegative port bit patterns. One call returns a dictionary containing `out_byte0`, `out_byte1`, `out_byte2` and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_byte0`, `out_byte1`, `out_byte2`: 8.
- `out_error`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `KyberPolyToBytesPair`: checked integer sub-operator and ready/valid transport.

## Functional Behavior

Decode each 16-bit input as a signed integer. Every coefficient must satisfy `-3329 < c < 3329`. This accepts both centered and nonnegative reduced representatives; it does not silently reduce arbitrary int16 values.

An out-of-range request returns `out_error=1` and **zero in every numerical output field**. A valid request returns `out_error=0` and the result below. The error flag is ordinary response data, not a missing transaction. No invalid input is silently clamped, normalized or evaluated as a valid operation.

Set `t0=c0+3329` if `c0<0`, otherwise `t0=c0`; apply the same rule to c1. Both mapped coefficients lie in 0..3328. Pack:

```text
out_byte0 = t0 & 255
out_byte1 = ((t0 >> 8) | (t1 << 4)) & 255
out_byte2 = (t1 >> 4) & 255
```

This preserves both 12-bit canonical values. Transaction i serializes coefficients 2*i and 2*i+1; concatenating 128 outputs gives the 384-byte serialization. No polynomial state or framing is stored inside this pair sub-operator.

All arithmetic is integer arithmetic. Signed results use the output width's two's-complement encoding; specified shifts of negative values are arithmetic. There is no unspecified floating-point behavior.

## Reset Behavior

Reset is synchronous, active-high. Reset clears `out_valid`, `out_error` and all output payload fields and cancels pending responses. No transfers are counted while reset is asserted. The operation has no mathematical state across requests. After reset it accepts new requests normally; idle payload values after subsequent transfers are otherwise unspecified.
