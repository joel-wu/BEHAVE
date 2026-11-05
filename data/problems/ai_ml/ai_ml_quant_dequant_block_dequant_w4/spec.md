# Signed INT4 Affine Dequantization to BF16

Dequantize one signed INT4 value using an integer zero point and a BF16 scale. The task retains affine subtraction, scaling, rounding and exceptional values; it is one element of a block sharing these parameters.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_dequant_block_dequant_w4 (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [3:0] in_q,
    input logic [15:0] in_scale,
    input logic [7:0] in_zero_point,
    output logic out_valid, input logic out_ready,
    output logic [15:0] out_data
);
```

### Behavior interface

```python
class AiMlQuantDequantBlockDequantW4:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_q, in_scale, in_zero_point): ...
```

One accepted input produces one ordered mapping `{"out_data": uint16}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Each accepted input produces one output, in order. Transfers occur on rising edges with valid and ready high. An unconsumed output retains valid and data.

## Modules

- `ai_ml_quant_dequant_block_dequant_w4`: arithmetic and output holding.
- `AiMlQuantDequantBlockDequantW4`: untimed arithmetic.

## Functional Behavior

Interpret `in_q` as signed four-bit two's complement and `in_zero_point` as signed eight-bit two's complement. Their exact difference `d=q-zero_point` is in -135..135. Interpret `in_scale` as BF16: sign bit 15, exponent bits 14..7 (bias 127), and seven fraction bits.

Promote BF16 to binary32 by appending 16 zero bits. Convert `d` exactly to binary32, using positive zero when `d=0`. Multiply these values with round-to-nearest, ties-to-even, then round the binary32 product to BF16 with the same rule.

Subnormal BF16 scales retain their numerical values when promoted. Binary32 multiplication and BF16 narrowing both use gradual underflow: round to the nearest representable value, choosing the even significand at a midpoint, including at the zero/subnormal and subnormal/normal boundaries. Overflow is signed infinity. NaN inputs and infinity times zero produce binary32 `0x7FC00000`, so the final BF16 NaN is `0x7FC0`; this canonical NaN is a project convention. Infinities are `0x7F80` and `0xFF80`. A zero product has the XOR of operand signs. BF16 rounding may carry into the exponent and overflow to infinity. Compare the final 16-bit encoding exactly; do not remove either specified rounding step.

Example: `q=7`, `zero_point=3`, `scale=0x3F80` (1.0) produce `0x4080` (4.0). Changing the zero point or scale must change this affine result accordingly.

For `q=1`, `zero_point=0`, `scale=0x0001`, the result is the BF16 subnormal `0x0001`. With the same scale and `q=3`, the result is `0x0003`.

## Reset Behavior

Reset is asynchronous active-high. Assertion discards pending output and clears `out_valid` and `out_data`; input is ready. There is no arithmetic state between transactions.
