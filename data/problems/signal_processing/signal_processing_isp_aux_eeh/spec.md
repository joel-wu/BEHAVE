# Signal Processing ISP Auxiliary EEH

Edge enhancement of a grayscale pixel from a complete 3×5 neighborhood.
A programmable signed kernel produces an edge value. A piecewise gain and
clipping function converts it into a correction, which is added to the
center pixel before final quantization and saturation.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_aux_eeh (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_p00,
    input  logic [7:0]  in_p01,
    input  logic [7:0]  in_p02,
    input  logic [7:0]  in_p03,
    input  logic [7:0]  in_p04,
    input  logic [7:0]  in_p10,
    input  logic [7:0]  in_p11,
    input  logic [7:0]  in_p12,
    input  logic [7:0]  in_p13,
    input  logic [7:0]  in_p14,
    input  logic [7:0]  in_p20,
    input  logic [7:0]  in_p21,
    input  logic [7:0]  in_p22,
    input  logic [7:0]  in_p23,
    input  logic [7:0]  in_p24,

    input  logic signed [7:0]  in_f00,
    input  logic signed [7:0]  in_f01,
    input  logic signed [7:0]  in_f02,
    input  logic signed [7:0]  in_f03,
    input  logic signed [7:0]  in_f04,
    input  logic signed [7:0]  in_f10,
    input  logic signed [7:0]  in_f11,
    input  logic signed [7:0]  in_f12,
    input  logic signed [7:0]  in_f13,
    input  logic signed [7:0]  in_f14,
    input  logic signed [7:0]  in_f20,
    input  logic signed [7:0]  in_f21,
    input  logic signed [7:0]  in_f22,
    input  logic signed [7:0]  in_f23,
    input  logic signed [7:0]  in_f24,

    input  logic signed [15:0] in_gain0,
    input  logic signed [15:0] in_gain1,
    input  logic signed [15:0] in_thres0,
    input  logic signed [15:0] in_thres1,
    input  logic signed [15:0] in_emclip0,
    input  logic signed [15:0] in_emclip1,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_ee_pixel,
    output logic [16:0] out_em_value
);
```

The pixel ports are unsigned 8-bit values. Filter taps are signed 8-bit
two's-complement values; gains, thresholds and enhancement limits are signed
16-bit two's-complement values. `out_em_value` carries the full signed
17-bit edge value as a two's-complement bit pattern. `out_ee_pixel` is unsigned.

Each accepted input produces one output. Transfers occur on rising clock
edges with valid and ready both high. Output valid and both fields remain
stable until accepted; responses retain input order. The caller supplies
all 15 samples, including any image-boundary padding.

### Behavior interface

```python
class SignalProcessingIspAuxEeh:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_p00, in_p01, in_p02, in_p03, in_p04,
        in_p10, in_p11, in_p12, in_p13, in_p14,
        in_p20, in_p21, in_p22, in_p23, in_p24,
        in_f00, in_f01, in_f02, in_f03, in_f04,
        in_f10, in_f11, in_f12, in_f13, in_f14,
        in_f20, in_f21, in_f22, in_f23, in_f24,
        in_gain0, in_gain1, in_thres0, in_thres1, in_emclip0, in_emclip1
    ): ...
```

The method returns `{"out_ee_pixel": ..., "out_em_value": ...}`, with the
edge value encoded as an unsigned integer containing its 17-bit pattern.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_ee_pixel`: 8.
- `out_em_value`: 17.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_aux_eeh`: signed spatial filter, piecewise
  enhancement and one-pixel response.

## Functional Behavior

Let P[i,j] and F[i,j] be the pixel and signed filter inputs for i=0..2,
j=0..4. The center pixel is P[1,2]. Compute exact signed integers:

```
sum = Σ P[i,j]*F[i,j]
e = trunc_toward_zero(sum/8)
```

Thus e ranges from −61200 to 60721 and fits in 17 signed bits.
For example, sum=−25 gives e=−3. No narrowing occurs before enhancement.

Let t0,t1 be the two thresholds and g0,g1 the gains. Select q by this
ordered rule; all inequalities are strict:

1. If e < −t1: q=g1*e.
2. Else if e < −t0 and e > −t1: q=0.
3. Else if e < t0 and e > −t1: q=g0*e.
4. Else if e > t0 and e < t1: q=0.
5. Else if e > t1: q=g1*e.
6. Else: q=0.

No threshold ordering is required. At an equality, later rules still apply;
for example t0=4,t1=8,e=−4 selects rule 3, not zero.

Let lo=`in_emclip0` and hi=`in_emclip1`. Keep the correction in Q8:

```
correction_q8 = max(256*lo, min(q, 256*hi))
enhanced = floor((256*P[1,2] + correction_q8)/256)
out_ee_pixel = min(255, max(0, enhanced))
out_em_value = e mod 2^17
```

There is no rounding before adding the center pixel. For center128,
e=−1, gain0=1, thresholds(2,4) and limits(−128,127), the pixel output is127.
The min-then-max order also defines reversed limits: if lo>hi, the correction
is lo. All products, sums and comparisons use their full mathematical values;
there is no intermediate signed-16 storage wrap.

## Reset Behavior

Reset is active-high and asynchronous. Asserting it discards any pending
response and clears output valid and both fields. No image history survives
between commands. Both output fields are zero when `out_valid=0`.
