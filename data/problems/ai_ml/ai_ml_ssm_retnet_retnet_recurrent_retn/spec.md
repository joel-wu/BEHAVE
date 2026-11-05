# Two-head recurrent matrix retention

This task specializes forward recurrent retention to two heads with key width two and value width two. It preserves the per-head decay, outer-product state update, and query contraction. One transaction is one token; the matrix state persists across transactions. Sequence boundaries are supplied through optional state replacement or reset. Reverse traversal, batching, and variable-length packing are outside this interface.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_retnet_retnet_recurrent_retn (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [127:0] in_q, in_k, in_v,
    input logic in_has_initial_state,
    input logic [255:0] in_initial_state,
    input logic in_scale_valid,
    input logic [31:0] in_scale,
    input logic in_output_final_state,
    output logic out_valid,
    input logic out_ready,
    output logic [127:0] out_o,
    output logic out_final_state_valid,
    output logic [255:0] out_final_state
);
```

### Behavior interface

```python
class MatrixRetention:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_q, in_k, in_v, in_has_initial_state,
                in_initial_state, in_scale_valid, in_scale, in_output_final_state): ...
```

The returned mapping contains `out_o`, `out_final_state_valid`, and `out_final_state`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_final_state`: 256.
- `out_final_state_valid`: 1.
- `out_o`: 128.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Each 32-bit data element is a binary32 raw bit pattern; the one-bit flags are Boolean controls. Vector lane (head h, coordinate j) occupies bits `[32*(2*h+j) +: 32]`, h,j=0..1. State element (h,r,c) occupies `[32*(4*h+2*r+c) +: 32]`. Thus a head's 2x2 state is row-major, and head zero is least significant.

## Modules

The named top-level module is the complete task; the implementation architecture is unrestricted.

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

Select S from `in_initial_state` when `in_has_initial_state=1`, otherwise from retained state. Select scale from `in_scale` when `in_scale_valid=1`, otherwise 0x3F3504F3 (binary32 1/sqrt(2)). The head decays are exactly d[0]=31/32 and d[1]=63/64, encoded 0x3F780000 and 0x3F7C0000. These are multipliers, not their logarithms.

For each h,r,c compute, in order:

1. `D = mul(S[h,r,c], d[h])` and `K = mul(k[h,r], v[h,c])`.
2. `Snew[h,r,c] = add(D,K)`.
3. `Q[h,r] = mul(q[h,r], scale)`.
4. `o[h,c] = add(mul(Q[h,0],Snew[h,0,c]), mul(Q[h,1],Snew[h,1,c]))`.

Each add/multiply rounds separately to binary32, nearest ties to even, with no fusion. Subnormal operands retain their values and underflow is gradual. NaNs and invalid operations return canonical 0x7FC00000; infinities and signed zeros otherwise follow IEEE arithmetic. All raw patterns are admitted under these rules.

Retain Snew regardless of `in_output_final_state`. Emit one output record containing o. Its final-state-valid flag equals `in_output_final_state`; its final-state payload is Snew when requested and zero otherwise. Hiding this payload does not skip the state update. State replacement applies to the current token before decay and outer-product update, not afterward.

For example, from reset with scale=1, each head's q=(1,0), k=(1,2), v=(3,4) produces state [[3,4],[6,8]] and output (3,4). A subsequent zero k/v token with the same q returns (3*d[h],4*d[h]), so the two heads decay differently.

Transfers occur on rising edges with valid and ready both high. A stalled output remains valid with stable payload. Only accepted inputs update state.

## Reset Behavior

Asynchronous active-high reset zeros all eight retained state elements and the output payloads, deasserts out_valid, makes in_ready=1, and discards pending output. The output payloads (out_o, out_final_state_valid, out_final_state) are zero when no output is pending. A new sequence then starts from zero unless an initial state is explicitly supplied.
