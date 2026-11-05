# ALiBi Bias Generator

This module generates the Falcon-style ALiBi positional bias tensor for a single attention-mask row. One accepted input transaction contains one binary attention mask of fixed length 16. The module produces exactly 8 output transactions, one per attention head in head-index order 0 through 7. For each head, the output is a length-16 vector of IEEE-754 FP32 bit patterns representing `slope[head] * position`, where `position` is derived from the cumulative count of enabled mask entries exactly as in Falcon `build_alibi_tensor`: `position[i] = (cumsum(mask)[i] - 1) * mask[i]`.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_pos_other_alibi_bias (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_attention_mask,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [2:0]   out_head_index,
    output logic [511:0] out_alibi
);
```

`out_alibi` packs 16 FP32 words. Bits `[31:0]` are element 0, `[63:32]` are element 1, ..., `[511:480]` are element 15.

### Behavior interface

```python
class AlibiBiasGenerator:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_attention_mask):
        pass
```

`process()` returns a list of 8 dictionaries. Each dictionary has keys `out_head_index` and `out_alibi`.

Return a list of exactly eight dictionaries on every call, with out_head_index=0
through 7 in that order. Never return None or a bare dictionary.
Every returned dictionary contains exactly these fields (bit widths):

- `out_alibi`: 512.
- `out_head_index`: 3.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_pos_other_alibi_bias`: top-level ALiBi bias generator.

## Functional Behavior

The design is single-clock and stateful only for handshake buffering. Algorithmically, each accepted input transaction is independent of prior transactions.

The input `in_attention_mask` is a 16-bit unsigned mask. Bit `i` of the mask defines attention-mask element `mask[i]`, where `mask[i]` is either 0 or 1.

For one accepted input transaction, the module computes a 16-element integer position vector `position[0..15]` in increasing element order:

- Let `running_count` start at 0.
- For each element index `i` from 0 to 15:
  - If `mask[i] == 1`, then `position[i] = running_count` and then `running_count` increments by 1.
  - If `mask[i] == 0`, then `position[i] = 0` and `running_count` does not change.

This is exactly the integer result of `position = (cumsum(mask) - 1) * mask` evaluated elementwise over a length-16 row.

The module then emits exactly 8 output transactions, one for each attention head `h = 0..7`, in ascending head order. Output transaction `h` contains:

- `out_head_index = h`
- `out_alibi` = packed 16-element FP32 vector `bias[h][0..15]`

For this module, the number of heads is fixed at 8. No other head count is legal.

For 8 heads, the ALiBi slopes are fixed to powers of one half:

- `slope[0] = 2^-1`
- `slope[1] = 2^-2`
- `slope[2] = 2^-3`
- `slope[3] = 2^-4`
- `slope[4] = 2^-5`
- `slope[5] = 2^-6`
- `slope[6] = 2^-7`
- `slope[7] = 2^-8`

Equivalently, for head `h`, `slope[h] = 2^-(h+1)`.

Each bias element is the exact real-number product:

- `bias_value[h][i] = position[i] * 2^-(h+1)`

Each `bias_value[h][i]` is encoded to IEEE-754 binary32 and placed into the corresponding 32-bit lane of `out_alibi`.

Because `position[i]` is an integer in `[0, 15]` and the slope is a power of two, every `bias_value[h][i]` is exactly representable in binary32. Therefore the required FP32 encoding is the exact binary32 representation of that real number, with no rounding ambiguity.

The fixed binary32 encodings used by this module are:

- head 0 (`/2`): `0, 0x3F000000, 0x3F800000, 0x3FC00000, 0x40000000, 0x40200000, 0x40400000, 0x40600000, 0x40800000, 0x40900000, 0x40A00000, 0x40B00000, 0x40C00000, 0x40D00000, 0x40E00000, 0x40F00000`
- head 1 (`/4`): `0, 0x3E800000, 0x3F000000, 0x3F400000, 0x3F800000, 0x3FA00000, 0x3FC00000, 0x3FE00000, 0x40000000, 0x40100000, 0x40200000, 0x40300000, 0x40400000, 0x40500000, 0x40600000, 0x40700000`
- head 2 (`/8`): `0, 0x3E000000, 0x3E800000, 0x3EC00000, 0x3F000000, 0x3F200000, 0x3F400000, 0x3F600000, 0x3F800000, 0x3F900000, 0x3FA00000, 0x3FB00000, 0x3FC00000, 0x3FD00000, 0x3FE00000, 0x3FF00000`
- head 3 (`/16`): `0, 0x3D800000, 0x3E000000, 0x3E400000, 0x3E800000, 0x3EA00000, 0x3EC00000, 0x3EE00000, 0x3F000000, 0x3F100000, 0x3F200000, 0x3F300000, 0x3F400000, 0x3F500000, 0x3F600000, 0x3F700000`
- head 4 (`/32`): `0, 0x3D000000, 0x3D800000, 0x3DC00000, 0x3E000000, 0x3E200000, 0x3E400000, 0x3E600000, 0x3E800000, 0x3E900000, 0x3EA00000, 0x3EB00000, 0x3EC00000, 0x3ED00000, 0x3EE00000, 0x3EF00000`
- head 5 (`/64`): `0, 0x3C800000, 0x3D000000, 0x3D400000, 0x3D800000, 0x3DA00000, 0x3DC00000, 0x3DE00000, 0x3E000000, 0x3E100000, 0x3E200000, 0x3E300000, 0x3E400000, 0x3E500000, 0x3E600000, 0x3E700000`
- head 6 (`/128`): `0, 0x3C000000, 0x3C800000, 0x3CC00000, 0x3D000000, 0x3D200000, 0x3D400000, 0x3D600000, 0x3D800000, 0x3D900000, 0x3DA00000, 0x3DB00000, 0x3DC00000, 0x3DD00000, 0x3DE00000, 0x3DF00000`
- head 7 (`/256`): `0, 0x3B800000, 0x3C000000, 0x3C400000, 0x3C800000, 0x3CA00000, 0x3CC00000, 0x3CE00000, 0x3D000000, 0x3D100000, 0x3D200000, 0x3D300000, 0x3D400000, 0x3D500000, 0x3D600000, 0x3D700000`

For head `h`, the `k`th entry in the corresponding list above is the FP32 encoding of `k * 2^-(h+1)` for integer `k` in `[0, 15]`.

Special cases:

- If `position[i] = 0`, the output FP32 word is positive zero `0x00000000`.
- All outputs are non-negative and finite.
- No NaN, infinity, subnormal, or negative zero can be produced.

Packing rule for `out_alibi`:

- lane 0 = FP32 bits of `bias_value[h][0]` in `out_alibi[31:0]`
- lane 1 = FP32 bits of `bias_value[h][1]` in `out_alibi[63:32]`
- ...
- lane 15 = FP32 bits of `bias_value[h][15]` in `out_alibi[511:480]`

Worked example for `in_attention_mask = 16'b0000_0000_0000_1111` with bit 0 as element 0:

- `mask = [1,1,1,1,0,0,...,0]`
- `position = [0,1,2,3,0,0,...,0]`

For head 0, slope = `1/2`, so the first four FP32 outputs are:

- `0 * 1/2 = 0.0  -> 0x00000000`
- `1 * 1/2 = 0.5  -> 0x3F000000`
- `2 * 1/2 = 1.0  -> 0x3F800000`
- `3 * 1/2 = 1.5  -> 0x3FC00000`

For head 3, slope = `1/16`, so the first four FP32 outputs are:

- `0 * 1/16 = 0.0     -> 0x00000000`
- `1 * 1/16 = 0.0625  -> 0x3D800000`
- `2 * 1/16 = 0.125   -> 0x3E000000`
- `3 * 1/16 = 0.1875  -> 0x3E400000`

## Reset Behavior

The reset signal is active-high. Reset may be implemented synchronously or asynchronously. After reset is asserted, all architectural state is initialised so that no output transaction is pending.

Architectural state after reset:

- no buffered output frames are pending
- next accepted input starts a fresh 8-output sequence beginning at head 0

Observable outputs after reset, before any new input is accepted:

- `in_ready = 1`
- `out_valid = 0`
- `out_head_index = 3'b000`
- `out_alibi = 512'b0`
