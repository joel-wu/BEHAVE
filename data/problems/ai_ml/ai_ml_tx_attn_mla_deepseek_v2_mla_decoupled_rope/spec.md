# AiMlTxAttnMlaDeepseekV2MlaDecoupledRope

This block computes the decoupled rotary-position-embedding step used by DeepSeek-V2 MLA attention for one token position. It rotates one query rotary sub-vector and one key rotary sub-vector by the same set of position-dependent RoPE phasors. The vector is interpreted as adjacent (real, imag) lane pairs, and each complex pair is multiplied by a phasor derived from the original RoPE inverse-frequency formula
`inv_freq[m] = 1 / rope_theta^((2*m)/D)`.

To keep the implementation contract decidable in hardware (no runtime `pow`, `cos`, `sin`, or division on arbitrary FP32 angles), the supported domain is restricted to the original-LLaMA-style RoPE parameterization with `rope_theta` exactly equal to the FP32 bit pattern `0x461C4000` (the value `10000.0`) and `in_position_id` in `{0, 1, ..., 15}`. The rotary width is fixed at `D = 16` lanes, equal to 8 complex pairs. Out-of-domain inputs produce all-zero outputs.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_mla_deepseek_v2_mla_decoupled_rope (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_position_id,
    input  logic [31:0]  in_rope_theta_bits,

    input  logic [31:0]  in_q_pe_0,
    input  logic [31:0]  in_q_pe_1,
    input  logic [31:0]  in_q_pe_2,
    input  logic [31:0]  in_q_pe_3,
    input  logic [31:0]  in_q_pe_4,
    input  logic [31:0]  in_q_pe_5,
    input  logic [31:0]  in_q_pe_6,
    input  logic [31:0]  in_q_pe_7,
    input  logic [31:0]  in_q_pe_8,
    input  logic [31:0]  in_q_pe_9,
    input  logic [31:0]  in_q_pe_10,
    input  logic [31:0]  in_q_pe_11,
    input  logic [31:0]  in_q_pe_12,
    input  logic [31:0]  in_q_pe_13,
    input  logic [31:0]  in_q_pe_14,
    input  logic [31:0]  in_q_pe_15,

    input  logic [31:0]  in_k_pe_0,
    input  logic [31:0]  in_k_pe_1,
    input  logic [31:0]  in_k_pe_2,
    input  logic [31:0]  in_k_pe_3,
    input  logic [31:0]  in_k_pe_4,
    input  logic [31:0]  in_k_pe_5,
    input  logic [31:0]  in_k_pe_6,
    input  logic [31:0]  in_k_pe_7,
    input  logic [31:0]  in_k_pe_8,
    input  logic [31:0]  in_k_pe_9,
    input  logic [31:0]  in_k_pe_10,
    input  logic [31:0]  in_k_pe_11,
    input  logic [31:0]  in_k_pe_12,
    input  logic [31:0]  in_k_pe_13,
    input  logic [31:0]  in_k_pe_14,
    input  logic [31:0]  in_k_pe_15,

    output logic         out_valid,
    input  logic         out_ready,

    output logic [31:0]  out_q_pe_rot_0,
    output logic [31:0]  out_q_pe_rot_1,
    output logic [31:0]  out_q_pe_rot_2,
    output logic [31:0]  out_q_pe_rot_3,
    output logic [31:0]  out_q_pe_rot_4,
    output logic [31:0]  out_q_pe_rot_5,
    output logic [31:0]  out_q_pe_rot_6,
    output logic [31:0]  out_q_pe_rot_7,
    output logic [31:0]  out_q_pe_rot_8,
    output logic [31:0]  out_q_pe_rot_9,
    output logic [31:0]  out_q_pe_rot_10,
    output logic [31:0]  out_q_pe_rot_11,
    output logic [31:0]  out_q_pe_rot_12,
    output logic [31:0]  out_q_pe_rot_13,
    output logic [31:0]  out_q_pe_rot_14,
    output logic [31:0]  out_q_pe_rot_15,

    output logic [31:0]  out_k_pe_rot_0,
    output logic [31:0]  out_k_pe_rot_1,
    output logic [31:0]  out_k_pe_rot_2,
    output logic [31:0]  out_k_pe_rot_3,
    output logic [31:0]  out_k_pe_rot_4,
    output logic [31:0]  out_k_pe_rot_5,
    output logic [31:0]  out_k_pe_rot_6,
    output logic [31:0]  out_k_pe_rot_7,
    output logic [31:0]  out_k_pe_rot_8,
    output logic [31:0]  out_k_pe_rot_9,
    output logic [31:0]  out_k_pe_rot_10,
    output logic [31:0]  out_k_pe_rot_11,
    output logic [31:0]  out_k_pe_rot_12,
    output logic [31:0]  out_k_pe_rot_13,
    output logic [31:0]  out_k_pe_rot_14,
    output logic [31:0]  out_k_pe_rot_15
);
```

### Behavior interface

```python
class AiMlTxAttnMlaDeepseekV2MlaDecoupledRope:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_position_id,
        in_rope_theta_bits,
        in_q_pe_0, in_q_pe_1, in_q_pe_2, in_q_pe_3,
        in_q_pe_4, in_q_pe_5, in_q_pe_6, in_q_pe_7,
        in_q_pe_8, in_q_pe_9, in_q_pe_10, in_q_pe_11,
        in_q_pe_12, in_q_pe_13, in_q_pe_14, in_q_pe_15,
        in_k_pe_0, in_k_pe_1, in_k_pe_2, in_k_pe_3,
        in_k_pe_4, in_k_pe_5, in_k_pe_6, in_k_pe_7,
        in_k_pe_8, in_k_pe_9, in_k_pe_10, in_k_pe_11,
        in_k_pe_12, in_k_pe_13, in_k_pe_14, in_k_pe_15,
    ): ...
```

`process(...)` returns one dict whose keys are `out_q_pe_rot_0` through `out_q_pe_rot_15` and `out_k_pe_rot_0` through `out_k_pe_rot_15`. Each value is one unsigned 32-bit FP32 bit pattern.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_k_pe_rot_0`, `out_k_pe_rot_1`, `out_k_pe_rot_10`, `out_k_pe_rot_11`,
  `out_k_pe_rot_12`, `out_k_pe_rot_13`, `out_k_pe_rot_14`, `out_k_pe_rot_15`,
  `out_k_pe_rot_2`, `out_k_pe_rot_3`, `out_k_pe_rot_4`, `out_k_pe_rot_5`,
  `out_k_pe_rot_6`, `out_k_pe_rot_7`, `out_k_pe_rot_8`, `out_k_pe_rot_9`,
  `out_q_pe_rot_0`, `out_q_pe_rot_1`, `out_q_pe_rot_10`, `out_q_pe_rot_11`,
  `out_q_pe_rot_12`, `out_q_pe_rot_13`, `out_q_pe_rot_14`, `out_q_pe_rot_15`,
  `out_q_pe_rot_2`, `out_q_pe_rot_3`, `out_q_pe_rot_4`, `out_q_pe_rot_5`,
  `out_q_pe_rot_6`, `out_q_pe_rot_7`, `out_q_pe_rot_8`, `out_q_pe_rot_9`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_attn_mla_deepseek_v2_mla_decoupled_rope`: top-level decoupled RoPE rotation block for one query rotary vector and one key rotary vector.

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

The block is a 1:1 transaction mapper with fixed rotary width `D = 16` lanes, equal to 8 complex pairs.

`in_position_id` is interpreted as unsigned 32-bit integer `position`.

`in_rope_theta_bits` is interpreted as a raw FP32 bit pattern.

### Domain guard

Out-of-domain inputs yield every `out_q_pe_rot_*` and every `out_k_pe_rot_*` set to `32'h00000000`. The input is in-domain only if both of the following hold:
- `in_rope_theta_bits == 32'h461C4000` (the FP32 bit pattern of `10000.0`)
- `in_position_id <= 32'd15`

For in-domain inputs the block uses a fixed cosine/sine lookup table indexed by `in_position_id` whose entries are the FP32 bit patterns of `cos(p * inv_freq[m])` and `sin(p * inv_freq[m])` for `p = in_position_id` and `m = 0..7` with `inv_freq[m] = 10000.0**(-m/8)`. Evaluate the frequency, angle, and trigonometric function as real numbers, then round the final cosine/sine to binary32, nearest ties to even. This defines every table entry without access to either reference artifact.

### Rotation

For each pair index `m` in `0..7`:
- `c_bits[m] = COS_LUT[position][m]`
- `s_bits[m] = SIN_LUT[position][m]`

Query input pair `m` is:
- `q_real = in_q_pe_(2*m)`
- `q_imag = in_q_pe_(2*m+1)`

Key input pair `m` is:
- `k_real = in_k_pe_(2*m)`
- `k_imag = in_k_pe_(2*m+1)`

Rotated outputs are computed lane-wise using separately rounded binary32 add/multiply, nearest ties to even, preserving subnormal operands and using gradual underflow, canonical 0x7FC00000 for arithmetic NaNs/invalid operations, and the infinity/zero rules below:
- `out_q_pe_rot_(2*m)   = fp32_add(fp32_mul(q_real, c_bits[m]), fp32_neg(fp32_mul(q_imag, s_bits[m])))`
- `out_q_pe_rot_(2*m+1) = fp32_add(fp32_mul(q_real, s_bits[m]), fp32_mul(q_imag, c_bits[m]))`
- `out_k_pe_rot_(2*m)   = fp32_add(fp32_mul(k_real, c_bits[m]), fp32_neg(fp32_mul(k_imag, s_bits[m])))`
- `out_k_pe_rot_(2*m+1) = fp32_add(fp32_mul(k_real, s_bits[m]), fp32_mul(k_imag, c_bits[m]))`

`fp32_neg` is sign-bit flip (negation of a NaN is the canonical quiet NaN `0x7FC00000`).

Each pair is processed independently. There is no persistent functional state.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state:
- No persistent architectural state.

Output port values during reset and before any accepted input:
- `out_valid = 0`
- every `out_q_pe_rot_* = 32'h00000000`
- every `out_k_pe_rot_* = 32'h00000000`

After reset deassertion, the next accepted input is processed independently of all prior transactions.
