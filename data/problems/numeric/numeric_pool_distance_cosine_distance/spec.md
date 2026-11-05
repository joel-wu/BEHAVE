# Cosine Distance FP32

`CosineDistanceFp32` computes the cosine distance between two length-16 vectors of IEEE-754 single-precision values stored in external memories. For one accepted input transaction, it reads 16 elements from vector A and 16 elements from vector B, computes the two vector powers, the dot product, the product of powers, the square root of that product, and finally `1 - dot / sqrt(power_a * power_b)`. Arithmetic is FP32 throughout and follows the `fp_lib` FP32 operators exactly.

## Interface

### RTL interface

```systemverilog
module cosine_distance_fp32 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_base_a,
    input  logic [7:0]   in_base_b,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_distance,

    output logic         vec_a_req_valid,
    output logic [7:0]   vec_a_req_addr,
    input  logic         vec_a_req_ready,
    input  logic         vec_a_resp_valid,
    input  logic [31:0]  vec_a_resp_rdata,

    output logic         vec_b_req_valid,
    output logic [7:0]   vec_b_req_addr,
    input  logic         vec_b_req_ready,
    input  logic         vec_b_resp_valid,
    input  logic [31:0]  vec_b_resp_rdata
);
```

### Behavior interface

```python
class CosineDistanceFp32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_base_a, in_base_b): ...
```

`process()` returns one dict with key `out_distance`.

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

- `cosine_distance_fp32`: top-level cosine-distance kernel with one input transaction and one output transaction.
- `vec_a`: external read-only FP32 memory, depth 256, addressed by `in_base_a + i` for `i = 0..15`.
- `vec_b`: external read-only FP32 memory, depth 256, addressed by `in_base_b + i` for `i = 0..15`.

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

One accepted input transaction consumes `in_base_a[7:0]` and `in_base_b[7:0]` and produces exactly one output transaction carrying `out_distance[31:0]`.

The memories `vec_a` and `vec_b` are arrays of 256 FP32 bit patterns. For element index `i` in `0..15`, the kernel reads:
- `a[i] = vec_a[(in_base_a + i) mod 256]`
- `b[i] = vec_b[(in_base_b + i) mod 256]`

Address arithmetic is 8-bit unsigned wraparound.

The kernel computes three FP32 reductions in this order:
- `power_a = ((((((((((((((fp32_mul(a[0], a[0]) + fp32_mul(a[1], a[1])) + fp32_mul(a[2], a[2])) + fp32_mul(a[3], a[3])) + fp32_mul(a[4], a[4])) + fp32_mul(a[5], a[5])) + fp32_mul(a[6], a[6])) + fp32_mul(a[7], a[7])) + fp32_mul(a[8], a[8])) + fp32_mul(a[9], a[9])) + fp32_mul(a[10], a[10])) + fp32_mul(a[11], a[11])) + fp32_mul(a[12], a[12])) + fp32_mul(a[13], a[13])) + fp32_mul(a[14], a[14])) + fp32_mul(a[15], a[15]))`, with every `+` implemented as `fp32_add`.
- `power_b = ((((((((((((((fp32_mul(b[0], b[0]) + fp32_mul(b[1], b[1])) + fp32_mul(b[2], b[2])) + fp32_mul(b[3], b[3])) + fp32_mul(b[4], b[4])) + fp32_mul(b[5], b[5])) + fp32_mul(b[6], b[6])) + fp32_mul(b[7], b[7])) + fp32_mul(b[8], b[8])) + fp32_mul(b[9], b[9])) + fp32_mul(b[10], b[10])) + fp32_mul(b[11], b[11])) + fp32_mul(b[12], b[12])) + fp32_mul(b[13], b[13])) + fp32_mul(b[14], b[14])) + fp32_mul(b[15], b[15]))`, with every `+` implemented as `fp32_add`.
- `dot = ((((((((((((((fp32_mul(a[0], b[0]) + fp32_mul(a[1], b[1])) + fp32_mul(a[2], b[2])) + fp32_mul(a[3], b[3])) + fp32_mul(a[4], b[4])) + fp32_mul(a[5], b[5])) + fp32_mul(a[6], b[6])) + fp32_mul(a[7], b[7])) + fp32_mul(a[8], b[8])) + fp32_mul(a[9], b[9])) + fp32_mul(a[10], b[10])) + fp32_mul(a[11], b[11])) + fp32_mul(a[12], b[12])) + fp32_mul(a[13], b[13])) + fp32_mul(a[14], b[14])) + fp32_mul(a[15], b[15]))`, with every `+` implemented as `fp32_add`.

The final result is computed in this order:
1. `power_product = fp32_mul(power_a, power_b)`
2. `norm = fp32_sqrt(power_product)`
3. `ratio = fp32_div(dot, norm)`
4. `out_distance = fp32_sub(0x3F800000, ratio)`

`0x3F800000` is the FP32 encoding of `1.0`.

FP32 behavior is exactly the behavior of the `fp_lib` functions `fp32_add`, `fp32_mul`, `fp32_sqrt`, `fp32_div`, and `fp32_sub`, including rounding and exceptional cases.

Edge-case handling is therefore:
- Any NaN input element participating in an operation causes that operation to follow `fp_lib` NaN propagation rules; canonical NaN output bit patterns come from `fp_lib`.
- Subnormal FP32 input elements are handled exactly as `fp_lib` handles them.
- If `norm` is `+0` or `-0`, `ratio` is produced by `fp32_div(dot, norm)` with `fp_lib` semantics.
- If `power_product` is negative because of FP32 exceptional propagation, `norm` is produced by `fp32_sqrt(power_product)` with `fp_lib` semantics.
- `+Inf`, `-Inf`, `+0`, `-0`, and NaN all propagate according to the named `fp_lib` operators and the operation order above.

There is no persistent architectural state other than resettable internal control state required to sequence memory requests and hold one in-flight transaction. The computation is otherwise stateless across transactions.

## Reset Behavior

The module uses active-high synchronous reset `rst`.

On a cycle with `rst = 1`, the architectural state is set to:
- no input transaction in flight
- no output transaction pending
- all internal element counters set to `0`
- all internal FP32 accumulators and temporary registers set to `0x00000000`
- `out_distance = 32'h00000000`

After reset and until an input transaction is accepted:
- `in_ready = 1`
- `out_valid = 0`
- `vec_a_req_valid = 0`
- `vec_a_req_addr = 8'h00`
- `vec_b_req_valid = 0`
- `vec_b_req_addr = 8'h00`

While `out_valid=0`, the output payload is not observed; consuming a response need not clear its data register.
