# signal_processing_comm_dotprod_dotprod_cccf

This module computes one 4-tap complex dot product per accepted input transaction. Each input transaction provides four complex single-precision floating-point samples `x[0..3]` and four complex single-precision floating-point coefficients `h[0..3]`. The module forms the left-folded sum `(x0*h0) + (x1*h1) + (x2*h2) + (x3*h3)`, where each complex multiply is `(a+jb)*(c+jd) = (ac-bd) + j(ad+bc)`. Real and imaginary components are IEEE-754 binary32 bit patterns, and the arithmetic and rounding order are defined below.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_dotprod_dotprod_cccf (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0_re,
    input  logic [31:0] in_x0_im,
    input  logic [31:0] in_x1_re,
    input  logic [31:0] in_x1_im,
    input  logic [31:0] in_x2_re,
    input  logic [31:0] in_x2_im,
    input  logic [31:0] in_x3_re,
    input  logic [31:0] in_x3_im,
    input  logic [31:0] in_h0_re,
    input  logic [31:0] in_h0_im,
    input  logic [31:0] in_h1_re,
    input  logic [31:0] in_h1_im,
    input  logic [31:0] in_h2_re,
    input  logic [31:0] in_h2_im,
    input  logic [31:0] in_h3_re,
    input  logic [31:0] in_h3_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y_re,
    output logic [31:0] out_y_im
);
```

### Behavior interface

```python
class signal_processing_comm_dotprod_dotprod_cccf:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_x0_re, in_x0_im, in_x1_re, in_x1_im,
            in_x2_re, in_x2_im, in_x3_re, in_x3_im,
            in_h0_re, in_h0_im, in_h1_re, in_h1_im,
            in_h2_re, in_h2_im, in_h3_re, in_h3_im):
        pass
```

The `process()` return value is one dictionary with keys `out_y_re` and `out_y_im`.

Each call represents one accepted four-tap request. All arguments and both
returned values are unsigned 32-bit port words carrying binary32 encodings,
not Python floats or complex numbers. Both keys are always present, including
zero, infinity and NaN results; the return is not a list or `None`. No clock or
backpressure calls are needed. There is no cross-request state; `reset()`
returns `None` and emits no response.

### Transaction protocol

Input and output transfers occur on rising clock edges with `valid && ready`. A stalled valid payload must remain stable until transfer or reset. Every completed result is delivered once and in order; no fixed implementation latency is required. An implementation may buffer or pipeline commands while preserving these rules.

## Modules

- `signal_processing_comm_dotprod_dotprod_cccf`: top-level module implementing one 4-element complex dot product.

## Functional Behavior

Each input `in_xK_re`, `in_xK_im`, `in_hK_re`, and `in_hK_im` for `K in {0,1,2,3}` is a 32-bit unsigned integer carrying one IEEE-754 binary32 bit pattern.

One accepted input transaction produces exactly one output transaction.

Define the four complex input samples and coefficients as:
- `x0 = in_x0_re + j*in_x0_im`
- `x1 = in_x1_re + j*in_x1_im`
- `x2 = in_x2_re + j*in_x2_im`
- `x3 = in_x3_re + j*in_x3_im`
- `h0 = in_h0_re + j*in_h0_im`
- `h1 = in_h1_re + j*in_h1_im`
- `h2 = in_h2_re + j*in_h2_im`
- `h3 = in_h3_re + j*in_h3_im`

For each tap `k`, compute the complex product `pk = xk * hk` with this exact operation order:
- `pk_rr = fp32_mul(xk_re, hk_re)`
- `pk_ii = fp32_mul(xk_im, hk_im)`
- `pk_ri = fp32_mul(xk_re, hk_im)`
- `pk_ir = fp32_mul(xk_im, hk_re)`
- `pk_re = fp32_sub(pk_rr, pk_ii)`
- `pk_im = fp32_add(pk_ri, pk_ir)`

The final output is the left-folded sum of the four tap products:
- `sum1_re = fp32_add(p0_re, p1_re)`
- `sum2_re = fp32_add(sum1_re, p2_re)`
- `sum3_re = fp32_add(sum2_re, p3_re)`
- `sum1_im = fp32_add(p0_im, p1_im)`
- `sum2_im = fp32_add(sum1_im, p2_im)`
- `sum3_im = fp32_add(sum2_im, p3_im)`

The single output transaction is:
- `out_y_re = sum3_re`
- `out_y_im = sum3_im`

Each multiply, add or subtract rounds its exact result separately to binary32, round-to-nearest with ties to even; there is no fused multiply-add or reassociation. Subnormal operands retain their numerical value and underflow is gradual, with the same rounding rule at the zero/subnormal and subnormal/normal boundaries. Overflow produces signed infinity. Compare final output encodings exactly, without a tolerance.

Any NaN operand, zero times infinity, or addition of opposite infinities returns the canonical quiet NaN `0x7fc00000` (a project sign/payload convention). Multiplication uses the XOR of operand signs, including zero. Adding two zeros produces negative zero only when both are negative; exact cancellation of nonzero operands produces positive zero. Subtraction means sign-negating the second operand followed by addition. Other infinity results retain their arithmetic sign.

No conjugation is applied to either operand.

Worked example of the complex multiply structure for one tap:
- If `x0 = (a + jb)` and `h0 = (c + jd)`, then
  - `p0_re = fp32_sub(fp32_mul(a, c), fp32_mul(b, d))`
  - `p0_im = fp32_add(fp32_mul(a, d), fp32_mul(b, c))`

## Reset Behavior

Reset is active-high. While reset is asserted, `out_valid` is zero and both output payloads are zero. Reset cancels any pending result; it produces no output transaction. There is no arithmetic state between commands. Synchronous or asynchronous reset implementation is permitted.
