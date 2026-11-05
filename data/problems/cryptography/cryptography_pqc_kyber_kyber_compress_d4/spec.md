# Kyber Four-Bit Compression — Eight-Coefficient Group

Compress eight reduced Kyber polynomial coefficients to four packed bytes using the d=4 quantizer. Consecutive groups compose a full polynomial without changing the source's code or nibble order.

## Interface

### RTL interface

```systemverilog
module KyberPolyCompressD4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_coeff0,
    input  logic [15:0] in_coeff1,
    input  logic [15:0] in_coeff2,
    input  logic [15:0] in_coeff3,
    input  logic [15:0] in_coeff4,
    input  logic [15:0] in_coeff5,
    input  logic [15:0] in_coeff6,
    input  logic [15:0] in_coeff7,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [7:0]  out_byte0,
    output logic [7:0]  out_byte1,
    output logic [7:0]  out_byte2,
    output logic [7:0]  out_byte3
);
```

Transfers occur on rising clock edges with valid and ready both high and rst low. The input producer holds the whole request stable while stalled. The block holds the whole response, including `out_error`, stable while stalled. Every accepted request produces exactly one ordered response unless reset cancels it. No fixed latency or pipeline architecture is required.

### Behavior interface

```python
class KyberPolyCompressD4:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_coeff0, in_coeff1, in_coeff2, in_coeff3, in_coeff4, in_coeff5, in_coeff6, in_coeff7): ...
```

Arguments and returned values are raw nonnegative port bit patterns. One call returns a dictionary containing `out_byte0`, `out_byte1`, `out_byte2`, `out_byte3` and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_byte0`, `out_byte1`, `out_byte2`, `out_byte3`: 8.
- `out_error`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `KyberPolyCompressD4`: checked integer sub-operator and ready/valid transport.

## Functional Behavior

Decode each 16-bit input as a signed integer. Every coefficient must satisfy `-3329 < c < 3329`. This accepts both centered and nonnegative reduced representatives; it does not silently reduce arbitrary int16 values.

An out-of-range request returns `out_error=1` and **zero in every numerical output field**. A valid request returns `out_error=0` and the result below. The error flag is ordinary response data, not a missing transaction. No invalid input is silently clamped, normalized or evaluated as a valid operation.

For each coefficient `c[k]`, set `u[k]=c[k]+3329` if negative, otherwise `u[k]=c[k]`. Thus `0 <= u[k] < 3329`. Compute `t[k]=floor((16*u[k]+1664)/3329) mod 16`, i.e. nearest quantization to sixteen levels. The equivalent source reciprocal-multiply formula is `t[k]=((((u[k]<<4)+1665)*80635)>>28)&15`.

Pack `out_byte[j]=t[2*j] | (t[2*j+1]<<4)` for j=0..3. Each transaction corresponds to source coefficients 8*i..8*i+7 and output bytes 4*i..4*i+3. Applying the block to 32 ordered groups yields the 128-byte compressed polynomial. It has no hidden position counter or state.

All arithmetic is integer arithmetic. Signed results use the output width's two's-complement encoding; specified shifts of negative values are arithmetic. There is no unspecified floating-point behavior.

## Reset Behavior

Reset is asynchronous assertion, active-high. Reset clears `out_valid`, `out_error` and all output payload fields and cancels pending responses. No transfers are counted while reset is asserted. The operation has no mathematical state across requests. After reset it accepts new requests normally; idle payload values after subsequent transfers are otherwise unspecified.
