# Affine W4 Dequantized Dot Product, 32 Lanes

Compute a length-32 dot product using four groups of eight unsigned 4-bit
weight codes, with one affine scale and zero point per group. All 32 signed
activations and all four groups contribute. This is an integer hardware
adaptation of grouped affine dequantization followed by one matrix-product
output; it does not perform GPTQ calibration, Hessian updates, or quantization.

## Interface

### RTL interface

```systemverilog
module AffineW4Dot32 (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [7:0] in_act_0,
    input logic signed [7:0] in_act_1,
    input logic signed [7:0] in_act_2,
    input logic signed [7:0] in_act_3,
    input logic signed [7:0] in_act_4,
    input logic signed [7:0] in_act_5,
    input logic signed [7:0] in_act_6,
    input logic signed [7:0] in_act_7,
    input logic signed [7:0] in_act_8,
    input logic signed [7:0] in_act_9,
    input logic signed [7:0] in_act_10,
    input logic signed [7:0] in_act_11,
    input logic signed [7:0] in_act_12,
    input logic signed [7:0] in_act_13,
    input logic signed [7:0] in_act_14,
    input logic signed [7:0] in_act_15,
    input logic signed [7:0] in_act_16,
    input logic signed [7:0] in_act_17,
    input logic signed [7:0] in_act_18,
    input logic signed [7:0] in_act_19,
    input logic signed [7:0] in_act_20,
    input logic signed [7:0] in_act_21,
    input logic signed [7:0] in_act_22,
    input logic signed [7:0] in_act_23,
    input logic signed [7:0] in_act_24,
    input logic signed [7:0] in_act_25,
    input logic signed [7:0] in_act_26,
    input logic signed [7:0] in_act_27,
    input logic signed [7:0] in_act_28,
    input logic signed [7:0] in_act_29,
    input logic signed [7:0] in_act_30,
    input logic signed [7:0] in_act_31,
    input logic [31:0] in_qweight_0,
    input logic [31:0] in_qweight_1,
    input logic [31:0] in_qweight_2,
    input logic [31:0] in_qweight_3,
    input logic signed [15:0] in_scale_0,
    input logic signed [15:0] in_scale_1,
    input logic signed [15:0] in_scale_2,
    input logic signed [15:0] in_scale_3,
    input logic signed [7:0] in_zero_0,
    input logic signed [7:0] in_zero_1,
    input logic signed [7:0] in_zero_2,
    input logic signed [7:0] in_zero_3,
    output logic out_valid,
    input logic out_ready,
    output logic signed [31:0] out_sum
);
endmodule
```

All fields are bit vectors. `in_act_0` through `in_act_31` are signed 8-bit
activations. Each unsigned 32-bit `in_qweight_g` contains eight unsigned
4-bit codes, with lane 0 in bits [3:0] and lane 7 in bits [31:28].
`in_scale_g` is a signed 16-bit **integer**, not a fixed-point value;
`in_zero_g` is a signed 8-bit integer offset. Negative and zero scales,
all offset values, and every input bit pattern are defined.
`out_sum` is a signed 32-bit port carrying the low 32 result bits.
The dimensions are fixed: four groups, eight lanes per group.

### Behavior interface

```python
class AffineW4Dot32:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_act_0, in_act_1, in_act_2, in_act_3,
        in_act_4, in_act_5, in_act_6, in_act_7,
        in_act_8, in_act_9, in_act_10, in_act_11,
        in_act_12, in_act_13, in_act_14, in_act_15,
        in_act_16, in_act_17, in_act_18, in_act_19,
        in_act_20, in_act_21, in_act_22, in_act_23,
        in_act_24, in_act_25, in_act_26, in_act_27,
        in_act_28, in_act_29, in_act_30, in_act_31,
        in_qweight_0, in_qweight_1, in_qweight_2, in_qweight_3,
        in_scale_0, in_scale_1, in_scale_2, in_scale_3,
        in_zero_0, in_zero_1, in_zero_2, in_zero_3
    ): ...
```

Each call represents one accepted request and returns one dictionary
`{"out_sum": result_bits}`, where `result_bits` is in [0, 2^32-1].

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sum`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction state

There is no persistent arithmetic state between requests.

## Modules

- `AffineW4Dot32`: the complete dequantization and dot-product block.

## Functional Behavior

For group `g = 0,1,2,3` and lane `k = 0,...,7`, define

```
q[g,k]  = (in_qweight_g >> (4*k)) & 15
a[g,k]  = signed8(in_act_{8*g+k})
z[g]    = signed8(in_zero_g)
s[g]    = signed16(in_scale_g)
w[g,k]  = (q[g,k] - z[g]) * s[g]
total   = sum(a[g,k] * w[g,k] for all g,k)
out_sum = total mod 2^32
```

All intermediate arithmetic is exact over integers. Only the final result
wraps; there is no intermediate truncation, rounding, saturation, clipping,
or floating-point arithmetic. A lane with `a=7`, `q=13`, `z=8`, `s=-3`
contributes -105. If it is the only nonzero contribution, the output bits
are `0xffffff97`. The low-nibble-first layout and integer arithmetic above
define this task, not a library-specific packed-tensor ABI.

An input is accepted on a rising clock edge when `in_valid && in_ready`
and reset is inactive. Each accepted request produces exactly one ordered
response, transferred when `out_valid && out_ready`. While blocked,
`out_valid` and `out_sum` remain stable. The producer likewise holds an
unaccepted request stable. The implementation may apply backpressure and
choose its internal latency; no fixed pipeline or exact latency is required.

## Reset Behavior

`rst` is synchronous, active high, and takes priority at the rising edge.
It cancels all pending requests/responses and clears `out_valid` and `out_sum`
to zero. No transfer is counted during reset. After reset the block can accept
a fresh request; previous work must not reappear. The value of `out_sum` when
`out_valid` is low is otherwise irrelevant.
