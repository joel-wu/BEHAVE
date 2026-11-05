# Group-Scaled INT4–BF16 Dot Product

Compute a 16-element dot product between BF16 activations and signed INT4 weights with one BF16 scale per four weights. Dequantization, multiplication and accumulation use binary32; the single result is BF16.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_dequant_fused_dequant_matmul_w4a16 (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [255:0] in_act,
    input logic [63:0] in_w_packed,
    input logic [63:0] in_scale,
    output logic out_valid, input logic out_ready,
    output logic [15:0] out_bf16
);
```

### Behavior interface

```python
class AiMlQuantDequantFusedDequantMatmulW4A16:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_act, in_w_packed, in_scale): ...
```

Each accepted input produces one ordered mapping `{"out_bf16": uint16}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_bf16`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Each accepted input produces one output, in order. Transfers occur on rising edges when valid and ready are high. A stalled output holds valid and data unchanged.

## Modules

- `ai_ml_quant_dequant_fused_dequant_matmul_w4a16`: grouped dequantization, dot product and output holding.
- `AiMlQuantDequantFusedDequantMatmulW4A16`: untimed arithmetic.

## Functional Behavior

Lane `i` (0..15) occupies bits `16*i+15:16*i` of `in_act` and bits `4*i+3:4*i` of `in_w_packed`. Group `g` (0..3) occupies bits `16*g+15:16*g` of `in_scale`. Decode weight nibbles as signed two's-complement integers -8..7. Activations and scales are BF16 (one sign bit, eight exponent bits with bias 127, seven fraction bits); promote them exactly by appending sixteen zero bits.

For each lane, compute `w_i=mul32(q_i,scale[i//4])`, then `p_i=mul32(activation_i,w_i)`. Integer-to-binary32 conversion is exact. Accumulate in lane order: `acc=p_0`, then `acc=add32(acc,p_i)` for i=1..15. Return `acc` rounded to BF16. There is no fused multiply-add and no zero-point subtraction.

Each binary32 add/multiply rounds to nearest, ties to even, preserving subnormal operands and using gradual underflow, including rounding at the zero/subnormal and subnormal/normal boundaries. Overflow yields signed infinity. Any NaN, infinity times zero, or addition of opposite infinities yields positive quiet NaN `0x7FC00000` (a project sign/payload convention). Multiplication uses XOR sign; exact cancellation in addition gives positive zero, while adding two negative zeros gives negative zero. These rules apply at every intermediate operation.

The final conversion rounds the binary32 value to BF16, ties to even, allowing a carry into the exponent. It preserves subnormal values subject to BF16 rounding, signed zeros and infinities and maps NaN to quiet BF16 `0x7FC0`. All bit patterns are legal inputs; results are bit-exact under these rules.

For all activations 1.0, all group scales 0.5, and weight nibbles 0,1,...,15 in lane order, the result is -4.0 (`0xC080`). Distinct group scales apply separately before accumulation.

## Reset Behavior

Reset is asynchronous active-high. Assertion discards a pending result and clears `out_valid` and `out_bf16`. Input is ready after reset; there is no retained arithmetic state.
