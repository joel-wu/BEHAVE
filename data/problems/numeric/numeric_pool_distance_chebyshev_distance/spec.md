# Chebyshev Distance FP32

This module computes the Chebyshev distance between two contiguous vectors of IEEE-754 single-precision values stored in external memories. One accepted input transaction specifies the base address of vector A, the base address of vector B, and the vector length. The module reads the two vectors element by element, forms the absolute difference of each pair with FP32 arithmetic semantics, and returns the maximum absolute difference across the full vector. The reduction is a sequential left-to-right max scan over the elements.

## Interface

### RTL interface

```systemverilog
module chebyshev_distance_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_a_base,
    input  logic [7:0]  in_b_base,
    input  logic [7:0]  in_block_size,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_distance,

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
class ChebyshevDistanceF32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a_base, in_b_base, in_block_size):
        pass
```

`process()` returns one dict with key `out_distance` for every accepted input transaction.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_distance`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

The memory views self.vec_a and self.vec_b each contain 256 unsigned 32-bit words.
Bind the environment images before processing; standalone construction uses
zero-filled placeholders. Memory-request pins are not return fields.

## Modules

- `chebyshev_distance_f32`: top-level kernel that accepts one vector-distance request, issues external memory reads to `vec_a` and `vec_b`, performs the max-of-absolute-differences reduction, and emits one FP32 result.

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

Inputs `in_a_base` and `in_b_base` are unsigned 8-bit base
addresses; `in_block_size` is an unsigned 8-bit element count (0 through 255).
For count zero, return `{"out_distance": 0x00000000}` without memory reads.
Otherwise read exactly that many words from each memory at ascending offsets,
and compute `d[i] = fp32_abs(fp32_sub(A[i], B[i]))`.

Initialize the running maximum from `d[0]`. At each later index replace it
only if `d[i]` is numerically greater; a comparison involving any NaN is false,
and the two zero signs compare equal. Thus a NaN at index zero persists, while
a later NaN does not replace a finite maximum. Return the final maximum as
`out_distance`. No element may be skipped, and the zero-count
result does not seed nonempty reductions.
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

Reset is active-high and asynchronous or synchronous reset behavior is not architecturally visible; after reset is asserted, the architectural state shall be:
- No request is in progress.
- `in_ready = 1`.
- `out_valid = 0`.
- `out_distance = 32'h00000000`.
- No buffered partial result is valid.
- The internal running maximum register, index counter, base-address registers, and block-size register are all `0`.
- `vec_a_req_valid = 0`.
- `vec_b_req_valid = 0`.
- `vec_a_req_addr = 8'h00`.
- `vec_b_req_addr = 8'h00`.
