# ai_ml_ssm_retnet_retnet_chunkwise_retn

One scalar, forward retention update is performed per accepted input. This is the one-head, key-width-one, value-width-one specialization of the source recurrence, with persistent state across transactions. The source uses a log-decay parameter internally; the multiplier here is its exponential, **31/32**, not the logarithm. No chunk-size or reverse traversal is exposed.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_retnet_retnet_chunkwise_retn (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_q,
    input  logic [31:0] in_k,
    input  logic [31:0] in_v,
    input  logic        in_has_initial_state,
    input  logic [31:0] in_initial_state,
    input  logic        in_scale_valid,
    input  logic [31:0] in_scale,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_o,
    output logic        out_final_state_valid,
    output logic [31:0] out_final_state
);
```

### Behavior interface

```python
class AiMlSsmRetnetRetnetChunkwiseRetn:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_q,
        in_k,
        in_v,
        in_has_initial_state,
        in_initial_state,
        in_scale_valid,
        in_scale,
    ): ...
```

The `process()` return value is one dictionary with keys:
- `out_o`
- `out_final_state_valid`
- `out_final_state`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_final_state`, `out_o`: 32.
- `out_final_state_valid`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_ssm_retnet_retnet_chunkwise_retn`: top-level retention-update block with one architectural retained-state register.

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

All input and output data ports other than valid/ready are 32-bit unsigned integers carrying IEEE-754 binary32 bit patterns.

The block contains one architectural state register `retained_state`, also a 32-bit binary32 bit pattern.

The decay constant is fixed and does not depend on the input transaction:
- `DECAY = 0x3F780000`

This is exactly binary32 31/32.

For each accepted input transaction, computation is performed in the following order:

1. Select the previous state:
   - If `in_has_initial_state == 1`, then `state_prev = in_initial_state`.
   - If `in_has_initial_state == 0`, then `state_prev = retained_state`.

2. Select the query scale:
   - If `in_scale_valid == 1`, then `scale_used = in_scale`.
   - If `in_scale_valid == 0`, then `scale_used = 0x3F800000`.

   `0x3F800000` is binary32 `+1.0`.

3. Compute the decayed prior state:
   - `decayed_state = fp32_mul(state_prev, DECAY)`

4. Compute the key-value update:
   - `kv_update = fp32_mul(in_k, in_v)`

5. Compute the next retained state:
   - `state_next = fp32_add(decayed_state, kv_update)`

6. Compute the scaled query:
   - `q_scaled = fp32_mul(in_q, scale_used)`

7. Compute the output:
   - `out_o = fp32_mul(q_scaled, state_next)`

8. Update architectural state:
   - `retained_state` becomes `state_next` after the transaction.

9. Emit exactly one output transaction with:
   - `out_o = out_o`
   - `out_final_state_valid = 1`
   - `out_final_state = state_next`

There are no transactions that produce zero outputs, and no transactions that produce more than one output.

Each add/multiply rounds separately to binary32, nearest ties to even; there is no fused multiply-add. Subnormal operands retain their values and underflow is gradual. NaNs and invalid operations return canonical 0x7FC00000; infinities and signed zeros follow IEEE addition/multiplication rules. These rules apply to every raw 32-bit input, independently of any implementation library.

For example, q=2, k=1, v=3 from reset gives state=3 and output=6. A subsequent q=1, k=0, v=0 without replacement gives state=output=93/32, a positive decay of the previous value.

## Reset Behavior

Reset is asynchronous active-high and discards pending output. Transfers use valid and ready on a rising edge. State changes only on an accepted input; pending output payload remains stable while stalled.

Architectural state after reset:
- `retained_state = 0x00000000`

Output port values after reset:
- `out_valid = 0`
- `out_o = 0x00000000`
- `out_final_state_valid = 0`
- `out_final_state = 0x00000000`

No other architectural state is defined.
