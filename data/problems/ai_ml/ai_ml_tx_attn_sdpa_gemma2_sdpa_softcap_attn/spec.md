# Gemma2 SDPA Softcap Attention Logits

This block computes the four pre-softmax attention logits for one query vector against four key vectors using the Gemma2 eager-attention softcap rule. Each accepted input transaction contains one FP32 query of length 4, four FP32 keys of length 4, an optional additive mask for each key, one FP32 scaling factor, and one FP32 softcap factor. The block produces exactly one output transaction containing four FP32 logits. Each add, multiply, and divide rounds separately to binary32, nearest ties to even. Subnormal operands retain their values and underflow is gradual; NaNs and invalid operations become canonical 0x7FC00000; infinities and signed zeros otherwise follow IEEE arithmetic. The explicit piecewise tanh table below is an approximation, not correctly rounded transcendental tanh. Softmax and value accumulation are outside this task. The computation order is fixed: dot product, scaling multiply, division by softcap, tanh, multiply by softcap, then optional mask add.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_sdpa_gemma2_sdpa_softcap_attn (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_q0,
    input  logic [31:0]  in_q1,
    input  logic [31:0]  in_q2,
    input  logic [31:0]  in_q3,
    input  logic [31:0]  in_k0_0,
    input  logic [31:0]  in_k0_1,
    input  logic [31:0]  in_k0_2,
    input  logic [31:0]  in_k0_3,
    input  logic [31:0]  in_k1_0,
    input  logic [31:0]  in_k1_1,
    input  logic [31:0]  in_k1_2,
    input  logic [31:0]  in_k1_3,
    input  logic [31:0]  in_k2_0,
    input  logic [31:0]  in_k2_1,
    input  logic [31:0]  in_k2_2,
    input  logic [31:0]  in_k2_3,
    input  logic [31:0]  in_k3_0,
    input  logic [31:0]  in_k3_1,
    input  logic [31:0]  in_k3_2,
    input  logic [31:0]  in_k3_3,
    input  logic         in_mask_valid,
    input  logic [31:0]  in_mask0,
    input  logic [31:0]  in_mask1,
    input  logic [31:0]  in_mask2,
    input  logic [31:0]  in_mask3,
    input  logic [31:0]  in_scaling,
    input  logic [31:0]  in_softcap,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_attn0,
    output logic [31:0]  out_attn1,
    output logic [31:0]  out_attn2,
    output logic [31:0]  out_attn3
);
endmodule
```

### Behavior interface

```python
class Gemma2SdpaSoftcapAttn:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_q0,
        in_q1,
        in_q2,
        in_q3,
        in_k0_0,
        in_k0_1,
        in_k0_2,
        in_k0_3,
        in_k1_0,
        in_k1_1,
        in_k1_2,
        in_k1_3,
        in_k2_0,
        in_k2_1,
        in_k2_2,
        in_k2_3,
        in_k3_0,
        in_k3_1,
        in_k3_2,
        in_k3_3,
        in_mask_valid,
        in_mask0,
        in_mask1,
        in_mask2,
        in_mask3,
        in_scaling,
        in_softcap,
    ):
        pass
```

`process()` returns exactly one dict per accepted input transaction with keys `out_attn0`, `out_attn1`, `out_attn2`, and `out_attn3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_attn0`, `out_attn1`, `out_attn2`, `out_attn3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_attn_sdpa_gemma2_sdpa_softcap_attn`: top-level module that computes one length-4-query × 4-key softcapped attention-logit vector per accepted input transaction.

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

All data ports except `in_mask_valid` are 32-bit IEEE-754 binary32 values carried as unsigned 32-bit bit patterns.

For each accepted input transaction, define the query vector

- `Q = [in_q0, in_q1, in_q2, in_q3]`

and four key vectors

- `K0 = [in_k0_0, in_k0_1, in_k0_2, in_k0_3]`
- `K1 = [in_k1_0, in_k1_1, in_k1_2, in_k1_3]`
- `K2 = [in_k2_0, in_k2_1, in_k2_2, in_k2_3]`
- `K3 = [in_k3_0, in_k3_1, in_k3_2, in_k3_3]`

The block computes one output logit for each key vector independently. For row `r` in `{0,1,2,3}`, use `Kr` and mask `Mr`, where

- `M0 = in_mask0`
- `M1 = in_mask1`
- `M2 = in_mask2`
- `M3 = in_mask3`

The dot product reduction tree is fixed and shall be implemented as:

- `p0 = fp32_mul(in_q0, Kr[0])`
- `p1 = fp32_mul(in_q1, Kr[1])`
- `p2 = fp32_mul(in_q2, Kr[2])`
- `p3 = fp32_mul(in_q3, Kr[3])`
- `s0 = fp32_add(p0, p1)`
- `s1 = fp32_add(p2, p3)`
- `dot = fp32_add(s0, s1)`

Then apply Gemma2 softcap in the fixed order:

- `scaled = fp32_mul(dot, in_scaling)`
- `divided = fp32_div(scaled, in_softcap)`
- `t = tanh_fp32(divided)`
- `softcapped = fp32_mul(t, in_softcap)`

If `in_mask_valid == 1`, the final row output is

- `out = fp32_add(softcapped, Mr)`

If `in_mask_valid == 0`, the final row output is

- `out = softcapped`

The four output ports are assigned as:

- `out_attn0 = row_output(Q, K0, M0, in_scaling, in_softcap, in_mask_valid)`
- `out_attn1 = row_output(Q, K1, M1, in_scaling, in_softcap, in_mask_valid)`
- `out_attn2 = row_output(Q, K2, M2, in_scaling, in_softcap, in_mask_valid)`
- `out_attn3 = row_output(Q, K3, M3, in_scaling, in_softcap, in_mask_valid)`

`tanh_fp32` is a piecewise FP32 lookup that approximates the hyperbolic tangent. The port-level contract is defined by the following ordered rules, evaluated top-down on the input bit pattern `x` (with `sign = x[31]` and `absx = x & 0x7FFFFFFF`):

- if the input bit pattern encodes NaN, the result is `0x7FC00000`
- if `x == 0x7F800000` (+Inf), the result is `0x3F800000`
- if `x == 0xFF800000` (-Inf), the result is `0xBF800000`
- if `x == 0x00000000` or `x == 0x80000000` (+/-0), the result is `x`
- if the input bit pattern encodes a subnormal number, the result is signed zero with the same sign bit (a rule of this tanh approximation only)
- if `absx < 0x3D800000`, the result is `x` (small-input identity)
- if `absx < 0x3E800000`, the result is `0xBE4C0000` if `sign`, else `0x3E4C0000`
- if `absx < 0x3F000000`, the result is `0xBECC0000` if `sign`, else `0x3ECC0000`
- if `absx < 0x3F400000`, the result is `0xBF180000` if `sign`, else `0x3F180000`
- if `absx < 0x3F800000`, the result is `0xBF400000` if `sign`, else `0x3F400000`
- if `absx < 0x3FC00000`, the result is `0xBF666666` if `sign`, else `0x3F666666`
- if `absx < 0x40000000`, the result is `0xBF733333` if `sign`, else `0x3F733333`
- if `absx < 0x40400000`, the result is `0xBF7A0000` if `sign`, else `0x3F7A0000`
- if `absx < 0x40800000`, the result is `0xBF7D999A` if `sign`, else `0x3F7D999A`
- if `absx < 0x40A00000`, the result is `0xBF7F0000` if `sign`, else `0x3F7F0000`
- otherwise, the result is `0xBF800000` if `sign`, else `0x3F800000`

Worked example for one row: if `in_mask_valid = 0`, `in_scaling = 0x3F000000` (`+0.5`), `in_softcap = 0x40000000` (`+2.0`), and the dot product computes to `0x40800000` (`+4.0`), then `scaled = +2.0`, `divided = +1.0`, `t = 0x3F666666` (the table value near +0.9), and `softcapped = t * 2.0`. No mask is added.

The block is 1:1. Every accepted input transaction produces exactly one output transaction. No accepted input may produce zero outputs or multiple outputs.

## Reset Behavior

Reset is asynchronous active-high and discards pending output.

This block has no architectural state other than the output handshake state required to hold one pending 1:1 result if the implementation pipelines or stalls.

After reset, before the first accepted input transaction:
- `out_valid = 0`
- `out_attn0 = 0x00000000`
- `out_attn1 = 0x00000000`
- `out_attn2 = 0x00000000`
- `out_attn3 = 0x00000000`

There is no other persistent state.
