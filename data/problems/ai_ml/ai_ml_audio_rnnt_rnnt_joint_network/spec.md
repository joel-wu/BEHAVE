# AI ML Audio RNN-T Joint Network

This specializes `_Joiner.forward` to one source/target position, ReLU activation and signed integer operands; it keeps the complete encoding dimension and dense affine projection. The sum is wide before ReLU, so positive inputs cannot turn negative through overflow. Only the final output is wrapped to 16 bits. Positive `ENCODING_DIM` and `OUTPUT_DIM` are supported.

This block implements the RNN-T joint network for one source encoding vector and one target encoding vector per accepted input transaction. It forms a joint encoding by elementwise addition of the two vectors, applies a ReLU nonlinearity to each summed element, and then computes one output logit vector by a dense affine projection. The block is stateless and emits exactly one output transaction for each accepted input transaction.

## Interface

### RTL interface

```systemverilog
module ai_ml_audio_rnnt_rnnt_joint_network #(
    parameter int ENCODING_DIM = 4,
    parameter int OUTPUT_DIM   = 4
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [16*ENCODING_DIM-1:0] in_source_encodings,
    input  logic [16*ENCODING_DIM-1:0] in_target_encodings,
    input  logic [15:0] in_source_length,
    input  logic [15:0] in_target_length,
    input  logic [16*OUTPUT_DIM*ENCODING_DIM-1:0] in_weight,
    input  logic [16*OUTPUT_DIM-1:0] in_bias,

    output logic out_valid,
    input  logic out_ready,
    output logic [16*OUTPUT_DIM-1:0] out_output,
    output logic [15:0] out_source_length,
    output logic [15:0] out_target_length
);
```

`in_source_encodings` and `in_target_encodings` are packed vectors of `ENCODING_DIM` signed 16-bit two's-complement elements. Element `i` occupies bits `[16*i +: 16]`.

`in_weight` is a packed matrix of signed 16-bit two's-complement elements in row-major order by output index first, then encoding index. Weight element `(o, i)` occupies bits `[16*(o*ENCODING_DIM + i) +: 16]`.

`in_bias` is a packed vector of `OUTPUT_DIM` signed 16-bit two's-complement elements. Bias element `o` occupies bits `[16*o +: 16]`.

`out_output` is a packed vector of `OUTPUT_DIM` signed 16-bit two's-complement elements. Output element `o` occupies bits `[16*o +: 16]`.

### Behavior interface

```python
class AiMlAudioRnntRnntJointNetwork:
    def __init__(self, encoding_dim=4, output_dim=4):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_source_encodings,
        in_target_encodings,
        in_source_length,
        in_target_length,
        in_weight,
        in_bias,
    ):
        pass
```

Constructor values `encoding_dim` and `output_dim` match the positive RTL
parameters `ENCODING_DIM` and `OUTPUT_DIM`. Each call represents one accepted
request and returns exactly one dictionary with `out_output` (unsigned
`16*output_dim`-bit packed integer), `out_source_length` (unsigned 16-bit
integer), and `out_target_length` (unsigned 16-bit integer). All arguments
are unsigned packed integers of the corresponding port widths, not lists or
signed Python scalars. The signed lane interpretation is defined below.
The return is never `None` or a list. Requests retain no model state;
`reset()` returns `None` without emitting a record.

## Modules

- `ai_ml_audio_rnnt_rnnt_joint_network`: top module and complete implementation.

## Functional Behavior

For each encoding element index `i` in `[0, ENCODING_DIM-1]`:

1. Unpack `source_i` from `in_source_encodings[16*i +: 16]` and interpret it as signed 16-bit two's-complement.
2. Unpack `target_i` from `in_target_encodings[16*i +: 16]` and interpret it as signed 16-bit two's-complement.
3. Compute `joint_i = source_i + target_i`.
4. Retain the exact signed 17-bit sum; no narrowing occurs before activation.
5. Apply ReLU:
   - if `joint_i < 0`, `act_i = 0`
   - else `act_i = joint_i`

For each output channel index `o` in `[0, OUTPUT_DIM-1]`:

1. Unpack `bias_o` from `in_bias[16*o +: 16]` and interpret it as signed 16-bit two's-complement.
2. For each encoding index `i`, unpack `weight_o_i` from `in_weight[16*(o*ENCODING_DIM + i) +: 16]` and interpret it as signed 16-bit two's-complement.
3. Compute the full-precision integer sum  
   `sum_o = bias_o + Σ_{i=0}^{ENCODING_DIM-1} (act_i * weight_o_i)`.
4. Truncate `sum_o` to signed 16-bit two's-complement wraparound.
5. Pack the truncated result into `out_output[16*o +: 16]`.

No saturation is applied in the affine projection. Every narrowing to 16 bits is modulo `2^16` wraparound.

`out_source_length` is exactly `in_source_length`.

`out_target_length` is exactly `in_target_length`.

The block does not inspect or modify the length values.

## Reset Behavior

Reset is asynchronous and active-high. Assertion immediately cancels pending
responses and establishes the values below, without waiting for a rising clock
edge. The block has no persistent arithmetic state.

While reset is asserted:
- `in_ready = 1`
- `out_valid = 0`
- `out_output = 0`
- `out_source_length = 0`
- `out_target_length = 0`

After reset deassertion, every accepted input transaction is processed independently of all prior transactions.
