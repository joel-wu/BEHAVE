# AI ML TX POS RoPE Standard

This block applies standard rotary position embedding (RoPE) to one query vector and one key vector for a single token position. The implementation is fixed to the original LLaMA-style RoPE parameterization with `inv_freq[i] = rope_theta^(-(2*i)/head_dim)`, head dimension 8, and one output transaction per accepted input transaction. To keep the implementation contract decidable in hardware, the supported domain is restricted to `rope_theta` exactly equal to the FP32 bit pattern `0x461C4000` (the value 10000.0) and `in_position_id` in `{0, 1, ..., 15}`. Out-of-domain inputs produce all-zero outputs. Inputs and outputs are IEEE-754 FP32 bit patterns packed into 256-bit vectors; the block computes the per-dimension cosine and sine for the supplied position index from a fixed lookup table, then rotates the query and key vectors with the standard `x * cos + rotate_half(x) * sin` rule.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_pos_rope_rope_standard (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_position_id,
    input  logic [31:0]  in_rope_theta,
    input  logic [255:0] in_q,
    input  logic [255:0] in_k,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_q_rot,
    output logic [255:0] out_k_rot
);
```

### Behavior interface

```python
class AiMlTxPosRopeRopeStandard:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_position_id, in_rope_theta, in_q, in_k): ...
```

`process(...)` returns one dict per accepted input:
- `{"out_q_rot": <uint256>, "out_k_rot": <uint256>}`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_k_rot`, `out_q_rot`: 256.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_pos_rope_rope_standard`: top-level single-transaction RoPE rotator for one 8-element query vector and one 8-element key vector.

## Functional Behavior

All FP-valued ports carry raw IEEE-754 binary32 bit patterns as unsigned integers.

`in_q` and `in_k` are each packed little-endian by 32-bit lane:
- lane 0 is bits `[31:0]`
- lane 1 is bits `[63:32]`
- lane 2 is bits `[95:64]`
- lane 3 is bits `[127:96]`
- lane 4 is bits `[159:128]`
- lane 5 is bits `[191:160]`
- lane 6 is bits `[223:192]`
- lane 7 is bits `[255:224]`

The block is stateless apart from handshake buffering. Each accepted input transaction produces exactly one output transaction.

The computation uses these fixed parameters:
- `head_dim = 8`
- `attention_scaling = 1.0`

### Domain guard

Out-of-domain inputs yield `out_q_rot = 256'h0` and `out_k_rot = 256'h0`. The input is in-domain only if both of the following hold:
- `in_rope_theta == 32'h461C4000` (the FP32 bit pattern of 10000.0)
- `in_position_id <= 32'd15`

For in-domain inputs the fixed table is defined by `C[p,j] = RN32(cos(p / 10**j))` and `S[p,j] = RN32(sin(p / 10**j))`, for `p = 0..15` and `j = 0..3`. The angles and trigonometric functions here are real-valued; round only the final coefficient to binary32, nearest with ties to even. This choice need not equal a software backend that first rounds its inverse frequencies or angles to FP32.

### Rotation

For lanes `i = 0..3` we use `cos[i] = cos_lut[i]` and `sin[i] = sin_lut[i]`; for lanes `i = 4..7` we use `cos[i] = cos_lut[i-4]` and `sin[i] = sin_lut[i-4]`.

Define `rotate_half(x)` as in standard RoPE:
- `rotate_half(x)[0] = -x[4]`
- `rotate_half(x)[1] = -x[5]`
- `rotate_half(x)[2] = -x[6]`
- `rotate_half(x)[3] = -x[7]`
- `rotate_half(x)[4] =  x[0]`
- `rotate_half(x)[5] =  x[1]`
- `rotate_half(x)[6] =  x[2]`
- `rotate_half(x)[7] =  x[3]`

For each lane `i = 0..7`, compute the FP32 lane result:
- `q_rot[i] = fp32_add(fp32_mul(q[i], cos[i]), fp32_mul(rotate_half(q)[i], sin[i]))`
- `k_rot[i] = fp32_add(fp32_mul(k[i], cos[i]), fp32_mul(rotate_half(k)[i], sin[i]))`

Each `fp32_mul` and `fp32_add` rounds separately to binary32, nearest with ties to even. Subnormal operands retain their numerical value and underflow is gradual, including nearest-even rounding at zero/subnormal and subnormal/normal boundaries. Compare final encodings exactly, without a tolerance. Any NaN operand, zero times infinity, or addition of opposite infinities produces `0x7fc00000` (a project sign/payload convention). Overflow yields signed infinity. Multiplication uses the XOR of operand signs; adding two negative zeros gives negative zero, while other exact cancellations give positive zero. No fused multiply-add or reassociation is permitted. Negation only flips bit 31 and preserves the other 31 bits, including NaN payloads. A later arithmetic operation applies the arithmetic NaN rule.

Transfers occur on rising clock edges with valid and ready both high. Hold pending input and output payloads stable with valid asserted during stalls. Responses preserve input order; reset discards a pending response. Latency is implementation-defined within the declared run bounds.

`out_q_rot` is packed from `q_rot[0]` through `q_rot[7]` in the same 32-bit little-endian lane order as `in_q`. `out_k_rot` is packed the same way from `k_rot[0]` through `k_rot[7]`.

## Reset Behavior

Reset is synchronous active-high.

On any rising clock edge with `rst == 1`:
- `out_valid` is deasserted
- `out_q_rot` is `256'h0`
- `out_k_rot` is `256'h0`

Architectural state after reset:
- no input transaction is buffered
- no output transaction is pending
