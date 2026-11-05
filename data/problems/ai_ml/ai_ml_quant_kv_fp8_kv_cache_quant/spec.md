# Four-Element FP8 KV-Cache Quantization

Quantize four BF16 key values using a supplied block maximum and return the
E4M3FN bytes, shared scale and paged-cache byte addresses. This is a work-item
suboperator: the enclosing block's maximum reduction and the actual memory
writes are outside the interface.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_kv_fp8_kv_cache_quant (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_k0_bf16,
    input  logic [15:0]  in_k1_bf16,
    input  logic [15:0]  in_k2_bf16,
    input  logic [15:0]  in_k3_bf16,
    input  logic [31:0]  in_amax_fp32,
    input  logic [15:0]  in_head_dim,
    input  logic [15:0]  in_quant_block_size,
    input  logic [15:0]  in_cache_block_size,
    input  logic [31:0]  in_slot_mapping,
    input  logic [15:0]  in_token_idx,
    input  logic [15:0]  in_head_dim_idx,
    input  logic         in_use_ue8m0,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_q0_fp8,
    output logic [7:0]   out_q1_fp8,
    output logic [7:0]   out_q2_fp8,
    output logic [7:0]   out_q3_fp8,
    output logic [31:0]  out_scale_fp32,
    output logic [31:0]  out_dst_offset,
    output logic [31:0]  out_dst_scale_idx,
    output logic         out_write_enable
);
```

### Behavior interface

```python
class AiMlQuantKvFp8KvCacheQuant:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_k0_bf16,
        in_k1_bf16,
        in_k2_bf16,
        in_k3_bf16,
        in_amax_fp32,
        in_head_dim,
        in_quant_block_size,
        in_cache_block_size,
        in_slot_mapping,
        in_token_idx,
        in_head_dim_idx,
        in_use_ue8m0,
    ): ...
```

The returned dictionary keys are `out_q0_fp8`, `out_q1_fp8`, `out_q2_fp8`, `out_q3_fp8`, `out_scale_fp32`, `out_dst_offset`, `out_dst_scale_idx`, and `out_write_enable`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_dst_offset`, `out_dst_scale_idx`, `out_scale_fp32`: 32.
- `out_q0_fp8`, `out_q1_fp8`, `out_q2_fp8`, `out_q3_fp8`: 8.
- `out_write_enable`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

One accepted input produces one ordered result. Transfers occur on rising
clock edges with valid and ready high. A pending output and all its fields
remain stable under backpressure. Computation latency is not prescribed.

## Modules

`ai_ml_quant_kv_fp8_kv_cache_quant` is the stateless quantization/address unit.

## Functional Behavior

Input/output floating-point ports carry raw encodings. Integer metadata is
unsigned except signed-two's-complement `in_slot_mapping`. `in_token_idx`
labels the already-selected key slice and does not enter the computation.
All address calculations use mathematical integers and truncate only their
final outputs modulo 2^32.

If the slot is negative, either block size is zero, or
`in_head_dim_idx >= in_head_dim`, return an all-zero record, including
`out_write_enable=0`. Otherwise set `out_write_enable=1` and compute:

```
block = slot // cache_block_size
offset = slot % cache_block_size
stride = head_dim + (4 * head_dim) // quant_block_size
base = block * cache_block_size * stride
out_dst_offset = base + offset * head_dim + head_dim_idx
out_dst_scale_idx = base + cache_block_size * head_dim
                  + 4 * (offset * head_dim + head_dim_idx) // quant_block_size
```

The last field is a byte offset, despite its historical name. These expressions
define the metadata extension even for dimensions not divisible by the block
size; they do not certify an enclosing memory allocation or vector alignment.

Let `amax` be the larger of `in_amax_fp32` and binary32 `0x38D1B717`
(the rounded value of 1e-4); a NaN maximum selects that floor. Divide it by
binary32 448 (`0x43E00000`) to obtain the scale. If `in_use_ue8m0=1`, round
the positive scale upward to a power of two, preserving exact powers and
positive infinity. Return this binary32 encoding in `out_scale_fp32`.

Widen each BF16 key exactly to binary32, divide by the scale, then encode the
quotient as E4M3FN with round-to-nearest, ties-to-even and finite saturation.
Each binary32 division rounds once to nearest-even, preserving subnormal
operands and using gradual underflow, including zero/subnormal and
subnormal/normal rounding boundaries. A NaN operand, zero/zero or
infinity/infinity produces `0x7FC00000` (a project sign/payload convention).
Other zero/infinity results carry the XOR sign: finite nonzero/zero or
overflow gives signed infinity; finite/infinity gives signed zero.
These rules also apply to the scale division. Compare final encodings
exactly, without a tolerance; E4M3FN saturation below is a separate conversion.

For an E4M3FN byte, sign s, exponent e and fraction f occupy bits 7, 6:3 and
2:0. For e=0 its value is (-1)^s * f * 2^-9. Otherwise its value is
(-1)^s * (8+f) * 2^(e-10), except e=15,f=7 denotes NaN. Thus e=15,f=0..6
are finite (256..448 in magnitude), not infinities. Preserve the sign of zero;
encode any NaN quotient as 0x7F; saturate overflow/infinite quotients to
signed 448 (0x7E or 0xFE). On a midpoint choose the neighboring finite code
whose fraction least-significant bit is even. Return the four bytes in order.

## Reset Behavior

Reset is active-high and asynchronous. It cancels any pending transfer,
deasserts `out_valid` and clears all output fields. There is no architectural
state between commands. Outside reset, output fields are meaningful only when
`out_valid` is asserted.
