# signal_processing_comm_dotprod_dotprod_rrrf

This block computes a real-valued floating-point dot product using IEEE-754 single-precision operands. Each accepted input transaction supplies one pair of FP32 values, `in_x` and `in_h`. The block multiplies the pair and accumulates it with the separately rounded arithmetic defined below. The block is therefore a streaming N:1 accumulator: most accepted inputs produce no output, and one accepted input that is marked as the final term of the dot-product sequence produces the accumulated FP32 result.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_dotprod_dotprod_rrrf (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,
    input  logic [31:0] in_h,
    input  logic        in_last,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y
);
```

### Behavior interface

```python
class signal_processing_comm_dotprod_dotprod_rrrf:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x, in_h, in_last):
        pass
```

One call represents one accepted product term, not one clock cycle. Pass
`in_x` and `in_h` as unsigned 32-bit binary32 encodings and `in_last` as 0 or 1.
When `in_last` is zero, return `None`; when it is one, return exactly one
dictionary `{"out_y": value}`, with an unsigned 32-bit binary32 encoding.
A zero result still produces this record, not `None` or a list. Reuse the
instance across terms; emitting a result starts a fresh sequence as specified
below. Construction and `reset()` clear the partial sum and active flag;
`reset()` returns `None` without emitting a record.

### Transaction protocol

Input and output transfers occur on rising clock edges with `valid && ready`. A stalled valid payload must remain stable until transfer or reset. Every completed result is delivered once and in order; no fixed implementation latency is required. An implementation may buffer or pipeline commands while preserving these rules.

## Modules

- `signal_processing_comm_dotprod_dotprod_rrrf`: top-level streaming real-floating-point dot-product accumulator.

## Functional Behavior

All FP32 ports carry raw IEEE-754 binary32 bit patterns as unsigned 32-bit integers.

Architectural state:
- `running_sum`: FP32 raw bit pattern.
- `active`: 1-bit flag indicating whether at least one input of the current sequence has been accepted since the last emitted output.

For each accepted input transaction:

1. Interpret `in_x` and `in_h` as FP32 values and compute
   `product = fp32_mul(in_x, in_h)`.

2. Compute the next accumulated sum:
   - if `active == 0`, `next_sum = product`
   - if `active == 1`, `next_sum = fp32_add(running_sum, product)`

3. Update state:
   - `running_sum := next_sum`
   - `active := 1`

4. Output behavior is controlled only by `in_last`:
   - if `in_last == 0`, the transaction produces no output.
   - if `in_last == 1`, the transaction produces exactly one output transaction with `out_y = next_sum`, and then the state is cleared for the next sequence:
     - `running_sum := 0x00000000`
     - `active := 0`

A sequence consists of one or more accepted inputs ending with exactly one input where `in_last == 1`. The result of the sequence is the left-folded sum
`fp32_add(...fp32_add(fp32_mul(x0,h0), fp32_mul(x1,h1))..., fp32_mul(xN,hN))`
with the first term taken directly as the initial `running_sum` rather than added to `+0.0`.

If a sequence contains exactly one accepted input and that input has `in_last == 1`, the block outputs `fp32_mul(in_x, in_h)`.

Each multiply, add or subtract rounds its exact result separately to binary32, round-to-nearest with ties to even; there is no fused multiply-add or reassociation. Subnormal operands retain their numerical value and underflow is gradual, with the same rounding rule at the zero/subnormal and subnormal/normal boundaries. Overflow produces signed infinity. Compare final output encodings exactly, without a tolerance.

Any NaN operand, zero times infinity, or addition of opposite infinities returns the canonical quiet NaN `0x7fc00000` (a project sign/payload convention). Multiplication uses the XOR of operand signs, including zero. Adding two zeros produces negative zero only when both are negative; exact cancellation of nonzero operands produces positive zero. Subtraction means sign-negating the second operand followed by addition. Other infinity results retain their arithmetic sign.

Worked 3-term example:
- beat 0: `product0 = fp32_mul(x0,h0)`, no output, `running_sum = product0`
- beat 1: `product1 = fp32_mul(x1,h1)`, `running_sum = fp32_add(product0, product1)`, no output
- beat 2 with `in_last = 1`: `product2 = fp32_mul(x2,h2)`, `next_sum = fp32_add(fp32_add(product0, product1), product2)`, output `out_y = next_sum`, then clear state

## Reset Behavior

Reset is synchronous active-high.

On reset:
- `running_sum = 0x00000000`
- `active = 0`
- `out_valid = 0`
- `out_y = 0x00000000`

Reset cancels both an unfinished input sequence and any pending output; neither is emitted after reset. After reset, the next accepted input starts a new sequence.
