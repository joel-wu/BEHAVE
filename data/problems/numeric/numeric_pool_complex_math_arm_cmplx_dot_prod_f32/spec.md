# Complex Dot Product F32x4

This block computes the floating-point complex dot product of two length-4 complex vectors stored in external memories. One accepted input transaction specifies the base addresses of the two vectors. The block reads 8 FP32 words from each memory in interleaved complex format `(real0, imag0, real1, imag1, real2, imag2, real3, imag3)`, then computes the length-4 sum in scalar order using IEEE-754 single-precision operations: each of the four real products per element is added to or subtracted from the appropriate running sum in the order specified below. Each accepted input produces exactly one output transaction carrying the final real and imaginary FP32 results.

## Interface

### RTL interface

```systemverilog
module complex_dot_product_f32x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_base_a,
    input  logic [7:0]  in_base_b,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_real,
    output logic [31:0] out_imag,

    output logic        vec_a_req_valid,
    output logic [7:0]  vec_a_req_addr,
    input  logic        vec_a_req_ready,
    input  logic        vec_a_resp_valid,
    input  logic [31:0] vec_a_resp_rdata,

    output logic        vec_b_req_valid,
    output logic [7:0]  vec_b_req_addr,
    input  logic        vec_b_req_ready,
    input  logic        vec_b_resp_valid,
    input  logic [31:0] vec_b_resp_rdata
);
```

### Behavior interface

```python
class ComplexDotProductF32x4:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_base_a, in_base_b): ...
```

Each call represents one accepted input and returns exactly
`{"out_real": real_bits, "out_imag": imag_bits}`. Arguments are unsigned
8-bit addresses; both fields are unsigned 32-bit FP32 encodings, not Python
floats. The return is never `None` or a list. `reset()` returns `None` and
emits no result.

The behavior model also contains two mirrored external memories:
- `self.vec_a`: list of 256 unsigned 32-bit FP32 bit patterns
- `self.vec_b`: list of 256 unsigned 32-bit FP32 bit patterns

Standalone construction initializes both mirrors to zero. During a run, the
environment binds them to the configured external images, which may contain
arbitrary words. The runtime preserves run-owned external memory across reset;
the model does not issue a request to clear that storage.

## Modules

- `complex_dot_product_f32x4`: top-level module that accepts base addresses, issues external memory reads, accumulates four complex products, and emits one complex result.

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

One input gives unsigned 8-bit base addresses `in_base_a` and
`in_base_b`. Read eight words at offsets 0 through 7 from each memory, as four
interleaved complex values `(real, imag)`. Initialize `real_sum = +0` and
`imag_sum = +0`. For `k = 0, 1, 2, 3`, let `(ar, ai)` and `(br, bi)` be
the kth pair from the two memories, then execute:

```text
real_sum = fp32_add(real_sum, fp32_mul(ar, br))
imag_sum = fp32_add(imag_sum, fp32_mul(ar, bi))
real_sum = fp32_sub(real_sum, fp32_mul(ai, bi))
imag_sum = fp32_add(imag_sum, fp32_mul(ai, br))
```

The outputs are `out_real = real_sum` and `out_imag = imag_sum`. This is the
unconjugated complex dot product. Each product is accumulated separately;
forming a rounded complex product first changes the specified result.
Both `vec_a` and `vec_b` are independent read-only memories of 256
32-bit words.
Their contents are supplied by the environment, not restricted to zero. A
request transfers when its own `req_valid && req_ready`; its address and
valid remain stable until that transfer. Each accepted request receives one
later `resp_valid` response with its `resp_rdata`, in order within that memory.
The two memories may stall and respond independently. Address addition wraps
modulo 256. Reset cancels pending requests/responses on both sides; it does not
require the DUT to clear external storage.

All numerical words are raw IEEE-754 binary32 encodings. `fp32_add`,
`fp32_sub`, `fp32_mul`, `fp32_div`, and `fp32_sqrt` denote the corresponding
operations with a separate round-to-nearest, ties-to-even step at each call;
there is no fused multiply-add or reassociation. Subnormal operands retain
their numerical value. Underflow is gradual: round to the nearest binary32
value, choosing an even significand at a midpoint, including the zero/subnormal
and subnormal/normal boundaries. Overflow produces signed infinity.
Compare the final output encodings exactly, without an error tolerance.

Any NaN operand or invalid operation produces canonical NaN `0x7FC00000`.
Invalid operations include opposite infinities in addition, equal infinities
in subtraction, zero times infinity, zero divided by zero, infinity divided
by infinity, and square root of a negative nonzero value. Multiplication and
division use the XOR of operand signs for zeros and infinities; a nonzero
finite value divided by zero gives signed infinity, and a finite value divided
by infinity gives signed zero. Addition of two negative zeros gives negative
zero; other exact cancellation gives positive zero. Subtraction is addition
after flipping the second sign. Square root preserves signed zero and positive
infinity. `fp32_abs` clears only the sign bit; `fp32_neg` flips only that bit.
Apply these rules in the order below, without a final clamp or special-case
override.

Each input transfers on a rising edge with `in_valid && in_ready`;
each output transfers with `out_valid && out_ready`. Every accepted input
produces exactly one output in input order. The input producer holds valid
and payload stable while stalled; the module does the same for its output.
Latency is implementation-dependent, and accepted work must finish when the
environment continues servicing it. Reset cancels unfinished work and pending
outputs.

## Reset Behavior

Reset is active-high and synchronous to `clk`.

Architectural state after reset:
- no input transaction is in flight
- no partially accumulated sum is retained
- no pending output transaction is retained

Output/interface values after reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_real = 32'h00000000`
- `out_imag = 32'h00000000`
- `vec_a_req_valid = 0`
- `vec_a_req_addr = 8'h00`
- `vec_b_req_valid = 0`
- `vec_b_req_addr = 8'h00`
