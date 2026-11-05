# Linear Interp F32

This module computes one floating-point linear interpolation result per accepted input transaction. The interpolation table is a fixed uniformly spaced FP32 sample array stored in an external read-only memory. For an input coordinate `x`, the module computes the segment index `i = trunc((x - x1) / xSpacing)` toward zero, clamps below-range inputs to the first table entry and above-range inputs to the last table entry, and otherwise evaluates `y0 + (x - x0) * ((y1 - y0) / (x1 - x0))` using IEEE-754 single-precision operations. The hardware may use an internal multi-cycle sequencer and the external memory request/response handshake to fetch the two neighboring table values.

## Interface

### RTL interface

```systemverilog
module linear_interp_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y,

    output logic        table_req_valid,
    output logic [7:0]  table_req_addr,
    input  logic        table_req_ready,
    input  logic        table_resp_valid,
    input  logic [31:0] table_resp_rdata
);
```

### Behavior interface

```python
class LinearInterpF32:
    def __init__(self, mem_init=None): ...
    def reset(self): ...
    def process(self, in_x): ...
```

`process()` returns one dictionary `{"out_y": ...}` for each accepted input transaction.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
`mem_init` is an optional initialization mapping of the form
`{"table": {address: word}}`, with addresses 0..255 and unsigned 32-bit words.
Construction copies these cells into zero-filled default storage. It is not an
RTL parameter; the execution environment may instead bind the run's table image.

self.table is the read-only memory view of 256 unsigned 32-bit sample words. Bind the
configured environment image before processing. Default construction uses zero-filled
storage; this does not restrict the run image to zero. Memory-request pins are not
return fields.
`reset()` preserves the current table contents and binding. A new independent
run installs its configured image; a reset within that run does not reinstall it.

## Modules

- `linear_interp_f32`: top-level interpolation kernel with one external ROM port `table`.

## Functional Behavior

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

The module has exactly one output transaction for each accepted input transaction.

The interpolation instance parameters are fixed constants:

- `nValues = 256`
- `x1 = 0x00000000` interpreted as FP32 `+0.0`
- `xSpacing = 0x3B800000` interpreted as FP32 `2^-8 = 1/256`

The external memory `table` is a read-only array of 256 words, each word carrying one FP32 sample value. Address `k` returns table entry `pYData[k]` for `0 <= k <= 255`.

All arithmetic is FP32 and must use these operations in this order:

1. Interpret `in_x` as FP32 value `x`.
2. Compute `t = fp32_div(fp32_sub(x, x1), xSpacing)`.
3. Convert `t` to a signed integer index `i` using
   **`fp32_to_int32_sat`** (the saturating FP→int32 protocol from
   `fp_lib`, matching ARM AArch64 `FCVTZS` / Cortex-M `VCVT` / CUDA
   `__float2int_rz`-saturate).
4. If `fp32_cmp(x, x1) < 0`, output `pYData[0]`.
5. Else if `(uint32_t)i >= nValues - 1`, output `pYData[255]`.
6. Else:
   - `i` is guaranteed to satisfy `0 <= i <= 254`.
   - Compute `x0 = fp32_add(x1, fp32_mul(fp32_from_uint8(i), xSpacing))`.
   - Compute `x1_seg = fp32_add(x1, fp32_mul(fp32_from_uint8(i + 1), xSpacing))`.
   - Read `y0 = pYData[i]`.
   - Read `y1 = pYData[i + 1]`.
   - Compute `num = fp32_sub(y1, y0)`.
   - Compute `den = fp32_sub(x1_seg, x0)`.
   - Compute `frac = fp32_div(num, den)`.
   - Compute `dx = fp32_sub(x, x0)`.
   - Output `fp32_add(y0, fp32_mul(dx, frac))`.

`fp32_from_uint8(k)` is defined only for `0 <= k <= 255`. It returns the exact FP32 encoding of integer `k`. Because step 6 is entered only when `0 <= i <= 254`, both `fp32_from_uint8(i)` and `fp32_from_uint8(i + 1)` are always defined.

The integer conversion in step 3 follows `fp32_to_int32_sat` exactly:

- finite values in `[-2^31, 2^31)` truncate fractional bits toward zero;
- `t` with magnitude less than 1 converts to integer `0`;
- NaN converts to signed integer `0`;
- `+Inf` and any value with magnitude ≥ 2^31 saturate to `INT32_MAX`
  (`0x7FFFFFFF`); `-Inf` and any value with magnitude < `-2^31`
  saturate to `INT32_MIN` (`0x80000000`);
- subnormal inputs truncate to integer zero because their magnitude is less than 1; this is not FP flushing.

RTL must reproduce this mapping bit-exactly — saturate based on
the unbiased exponent (≥ 31 → out-of-range), never truncate the
lower 32 bits of an over-range magnitude.

### Implementation equivalences for this fixed configuration

The fixed spacing is `2^-8`. Division by it is multiplication by `2^8`,
rounded to binary32; multiplication by it scales by `2^-8` with the same
rounding rule. These transformations must normalize subnormal operands and
round at underflow boundaries. Merely changing exponent bits is not equivalent
for every input. For example, scaling `0x00000001` by `2^8` gives `0x00000100`.

For segment indices 0 through 254, the two adjacent coordinates are exact
multiples of `2^-8`, and their difference is exactly `0x3B800000`. Implementations
may use that constant denominator without changing the prescribed arithmetic.
The integer index conversion truncates toward zero and saturates as above.

Floating-point edge-case behavior is:

- For an FP32 NaN input, `fp_lib.fp32_cmp(x, x1)` returns `-2`
  (its NaN sentinel), so `fp32_cmp(x, x1) < 0` is true and the
  module takes the low-bound path in step 4: the output is
  exactly `pYData[0]`.
- `+Inf` input satisfies step 5 and outputs `pYData[255]`.
- `-Inf` input satisfies step 4 and outputs `pYData[0]`.
- `+0.0` and `-0.0` both behave as zero for the comparison against `x1 = +0.0`; both select segment `i = 0` and evaluate the interpolation formula.
- Subnormal input values are handled by the FP32 operations and comparisons directly; no flushing or special-case clamping is applied beyond the rules above.
- Table entries may be any FP32 bit patterns, including NaN and infinity. Returned boundary values are the stored bit patterns exactly. In the interpolation path, FP32 arithmetic on those bit patterns determines the result.

Worked examples:

- If `in_x` is FP32 `0x3E800000` (`0.25`), then `t = 64.0`, `i = 64`, `x0 = 64/256 = 0.25`, `x1_seg = 65/256`, `dx = 0.0`, and the specified arithmetic is evaluated, without an endpoint bypass. For finite neighboring values whose subtraction remains finite, the output is `pYData[64]`; infinity times zero can instead produce NaN.
- If `in_x` is FP32 `0xBF000000` (`-0.5`), then `fp32_cmp(x, x1) < 0` is true and the output is exactly `pYData[0]`.
- If `in_x` is FP32 `0x7F800000` (`+Inf`), then the output is exactly `pYData[255]`.

## Reset Behavior

Reset is synchronous active-high.

Reset cancels computation and outstanding memory traffic without changing the
externally supplied table image.

Architectural state after reset:

- no input transaction is pending;
- no output transaction is pending;
- no table request is pending;
- any internal registers holding a latched input, segment index, fetched table values, or intermediate arithmetic results are set to zero.

Output port values after reset and before the first accepted input:

- `in_ready = 1`
- `out_valid = 0`
- `out_y = 32'h00000000`
- `table_req_valid = 0`
- `table_req_addr = 8'h00`
