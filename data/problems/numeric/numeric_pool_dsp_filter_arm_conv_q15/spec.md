# Q15Convolution8

Q15Convolution8 computes the full discrete convolution of two signed 8-sample Q15 sequences and emits the 15 Q15 output samples in order. Each input sample is interpreted as a 1.15 fixed-point signed value. Every output sample is formed as a sum of signed 16x16 multiplications, using an internal signed accumulator wide enough to hold the exact sum without overflow, followed by an arithmetic right shift by 15 bits and saturation to the signed 16-bit Q15 range.

## Interface

### RTL interface

```systemverilog
module q15_convolution8 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a0,
    input  logic [15:0]  in_a1,
    input  logic [15:0]  in_a2,
    input  logic [15:0]  in_a3,
    input  logic [15:0]  in_a4,
    input  logic [15:0]  in_a5,
    input  logic [15:0]  in_a6,
    input  logic [15:0]  in_a7,
    input  logic [15:0]  in_b0,
    input  logic [15:0]  in_b1,
    input  logic [15:0]  in_b2,
    input  logic [15:0]  in_b3,
    input  logic [15:0]  in_b4,
    input  logic [15:0]  in_b5,
    input  logic [15:0]  in_b6,
    input  logic [15:0]  in_b7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y0,
    output logic [15:0]  out_y1,
    output logic [15:0]  out_y2,
    output logic [15:0]  out_y3,
    output logic [15:0]  out_y4,
    output logic [15:0]  out_y5,
    output logic [15:0]  out_y6,
    output logic [15:0]  out_y7,
    output logic [15:0]  out_y8,
    output logic [15:0]  out_y9,
    output logic [15:0]  out_y10,
    output logic [15:0]  out_y11,
    output logic [15:0]  out_y12,
    output logic [15:0]  out_y13,
    output logic [15:0]  out_y14
);
```

### Behavior interface

```python
class Q15Convolution8:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7
    ):
        pass
```

The return value of `process(...)` is one dictionary with keys `out_y0` through `out_y14`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y10`, `out_y11`, `out_y12`, `out_y13`, `out_y14`, `out_y2`,
  `out_y3`, `out_y4`, `out_y5`, `out_y6`, `out_y7`, `out_y8`, `out_y9`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q15_convolution8`: top-level module implementing one 8-by-8 full convolution transaction.

## Functional Behavior

Inputs `in_a0`..`in_a7` and `in_b0`..`in_b7` are 16-bit two's-complement integers. Each input represents a Q15 number in signed 1.15 format.

Let `a[k]` for `k = 0..7` be the signed interpretation of `in_a{k}`.
Let `b[k]` for `k = 0..7` be the signed interpretation of `in_b{k}`.

The module computes the full linear convolution sequence `s[n]` for `n = 0..14`:

- `s[0]  = a[0]*b[0]`
- `s[1]  = a[0]*b[1] + a[1]*b[0]`
- `s[2]  = a[0]*b[2] + a[1]*b[1] + a[2]*b[0]`
- `s[3]  = a[0]*b[3] + a[1]*b[2] + a[2]*b[1] + a[3]*b[0]`
- `s[4]  = a[0]*b[4] + a[1]*b[3] + a[2]*b[2] + a[3]*b[1] + a[4]*b[0]`
- `s[5]  = a[0]*b[5] + a[1]*b[4] + a[2]*b[3] + a[3]*b[2] + a[4]*b[1] + a[5]*b[0]`
- `s[6]  = a[0]*b[6] + a[1]*b[5] + a[2]*b[4] + a[3]*b[3] + a[4]*b[2] + a[5]*b[1] + a[6]*b[0]`
- `s[7]  = a[0]*b[7] + a[1]*b[6] + a[2]*b[5] + a[3]*b[4] + a[4]*b[3] + a[5]*b[2] + a[6]*b[1] + a[7]*b[0]`
- `s[8]  = a[1]*b[7] + a[2]*b[6] + a[3]*b[5] + a[4]*b[4] + a[5]*b[3] + a[6]*b[2] + a[7]*b[1]`
- `s[9]  = a[2]*b[7] + a[3]*b[6] + a[4]*b[5] + a[5]*b[4] + a[6]*b[3] + a[7]*b[2]`
- `s[10] = a[3]*b[7] + a[4]*b[6] + a[5]*b[5] + a[6]*b[4] + a[7]*b[3]`
- `s[11] = a[4]*b[7] + a[5]*b[6] + a[6]*b[5] + a[7]*b[4]`
- `s[12] = a[5]*b[7] + a[6]*b[6] + a[7]*b[5]`
- `s[13] = a[6]*b[7] + a[7]*b[6]`
- `s[14] = a[7]*b[7]`

Each product `a[i]*b[j]` is a signed 32-bit mathematical product of two signed 16-bit operands and represents a Q30 value in 2.30 format.

Each sum `s[n]` is accumulated exactly in signed integer arithmetic with no wraparound and no intermediate truncation before the final shift.

For each `n = 0..14`, define:

- `t[n] = s[n] >> 15`

The right shift is arithmetic, preserving sign. This discards the low 15 fractional bits and truncates toward negative infinity in two's-complement arithmetic.

Each `t[n]` is then saturated to the signed 16-bit range:

- if `t[n] > 32767`, output `32767`
- else if `t[n] < -32768`, output `-32768`
- else output `t[n]`

The saturated signed result is returned on the corresponding output as a 16-bit two's-complement bit pattern:

- `out_y0`  = saturated `t[0]`
- `out_y1`  = saturated `t[1]`
- `out_y2`  = saturated `t[2]`
- `out_y3`  = saturated `t[3]`
- `out_y4`  = saturated `t[4]`
- `out_y5`  = saturated `t[5]`
- `out_y6`  = saturated `t[6]`
- `out_y7`  = saturated `t[7]`
- `out_y8`  = saturated `t[8]`
- `out_y9`  = saturated `t[9]`
- `out_y10` = saturated `t[10]`
- `out_y11` = saturated `t[11]`
- `out_y12` = saturated `t[12]`
- `out_y13` = saturated `t[13]`
- `out_y14` = saturated `t[14]`

The operation is symmetric in the two input sequences: swapping all `a` inputs with all `b` inputs produces the same 15 outputs.

## Reset Behavior

Reset is active-high. Reset may be implemented synchronously or asynchronously, but while `rst` is asserted the architectural state shall be the reset state.

This block has no architectural data state between transactions.

During reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_y0` through `out_y14` = `16'h0000`

After reset is deasserted, the block accepts an input transaction whenever `in_valid && in_ready` is high and produces exactly one output transaction containing `out_y0` through `out_y14`.
