# SwinWindowAttention2D

Compute inference-time Swin window attention for one 2×2 window, eight channels
and two four-channel heads. Each transaction supplies the feature map, all
projection parameters, relative-position biases and a query/key mask. The
fixed-point operator includes dense Q/K/V projections, scaled masked softmax,
value aggregation and a dense output projection; it has no cross-window state.

## Interface

### RTL interface

```systemverilog
module SwinWindowAttention2D (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [511:0] in_x,
    input logic [1023:0] in_q_weight,
    input logic [127:0] in_q_bias,
    input logic [1023:0] in_k_weight,
    input logic [127:0] in_k_bias,
    input logic [1023:0] in_v_weight,
    input logic [127:0] in_v_bias,
    input logic [1023:0] in_proj_weight,
    input logic [127:0] in_proj_bias,
    input logic [287:0] in_rel_pos_bias_table,
    input logic [15:0] in_mask,
    output logic out_valid,
    input logic out_ready,
    output logic [511:0] out_y
);
endmodule
```

Every numeric lane is a signed 16-bit two's-complement integer representing
its value divided by 256 (Q8.8). Lane zero occupies the least significant bits.
`in_x` and `out_y` hold row-major `[4][8]` tensors; the window token at `(y,x)`
has index `2*y+x`. Each weight tensor is `[output_channel][input_channel]`,
row-major `[8][8]`; each bias is `[8]`. The relative-bias tensor is `[9][2]`,
offset first, head second. Bit `4*i+j` of `in_mask` is one to exclude key `j`
for query `i`, independently of head. All input bit patterns are legal.

Inputs transfer on `in_valid && in_ready`, outputs on `out_valid && out_ready`
at rising clock edges outside reset. One accepted window produces one ordered
output window. A sender holds valid and payload while stalled. No fixed latency
is required, and all parameter inputs are sampled with the accepted window.

### Behavior interface

```python
class SwinWindowAttention2D:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_q_weight, in_q_bias, in_k_weight, in_k_bias,
                in_v_weight, in_v_bias, in_proj_weight, in_proj_bias,
                in_rel_pos_bias_table, in_mask): ...
```

Arguments and `{"out_y": packed_window}` use unsigned packed bus integers.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 512.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `SwinWindowAttention2D`: complete bounded two-head window-attention operator.

## Functional Behavior

The following equations operate on decoded signed lane integers. Intermediate
arithmetic is exact; `floor` rounds toward negative infinity, and
`sat16(z)=min(32767,max(-32768,z))`. There is no dropout.

For each of Q, K, V, project all four token rows:
`A[i,d] = sat16(floor((256*b[d] + sum_c X[i,c]*W[d,c])/256))`.
Head `h` uses channels `4*h` through `4*h+3`. Its key score is
`S[i,j,h] = floor(sum_d Q[i,4*h+d]*K[j,4*h+d]/512) + B[r,h]`,
where `d=0..3` and
`r=(floor(i/2)-floor(j/2)+1)*3 + (i mod 2)-(j mod 2)+1`.
The divisor 512 incorporates both Q8.8 rescaling and `1/sqrt(4)`.

For each query/head, let `m` be the largest **unmasked** score. A masked
key has weight zero. For an unmasked key, set
`jexp=floor((m-S+8)/16)` and use integer weight
`E[jexp]=floor(65536*exp(-jexp/16)+1/2)` for `0<=jexp<=128`, otherwise zero.
Here `exp` is the natural exponential. This defines a nearest-1/16 logit grid,
rounded Q16 exponential table and finite tail cutoff, not floating-point
bit equivalence. No interpolation is used. The maximal unmasked score has
weight 65536, so a nonempty row always has a positive denominator.

Let `Z=sum_j weight[j]`. The head context lane is
`C[i,4*h+d]=floor(sum_j weight[j]*V[j,4*h+d]/Z)` when `Z>0`.
An entirely masked row has all context lanes zero. Concatenate the heads in
channel order and apply the output weight/bias with the same projection
equation as above. Thus a fully masked row still produces the output bias.
Pack the 32 resulting signed lanes into `out_y`.

For example, with zero Q/K parameters and relative biases, no masks, identity
V/output weights (diagonal lane 256), and zero V/output biases, every output
token is the componentwise floored mean of the four input tokens. With the
same parameters and only each diagonal key unmasked, the output equals input.
Nonzero Q/K, relative bias and masks can each change the aggregation.

## Reset Behavior

`rst` is synchronous active-high and takes priority over input/output transfer.
Reset discards every unfinished window and pending response; `out_valid=0`
and `out_y=0` after the reset edge. The implementation is ready for a new
window (`in_ready=1`). There is no retained architectural state or implicit
parameter configuration.
