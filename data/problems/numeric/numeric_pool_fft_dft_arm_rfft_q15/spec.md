# Real FFT Split Q15 16

Recombines the eight complex FFT bins of an even/odd-packed real sequence into the nine nonredundant bins of a 16-point real FFT. This is the split stage, not the preceding complex FFT. The source kernel's additional factor-of-two scaling and Q15 coefficient quantization are retained.

## Interface

### RTL interface

```systemverilog
module real_fft_split_q15_16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_pSrc_0,
    input  logic [15:0]  in_pSrc_1,
    input  logic [15:0]  in_pSrc_2,
    input  logic [15:0]  in_pSrc_3,
    input  logic [15:0]  in_pSrc_4,
    input  logic [15:0]  in_pSrc_5,
    input  logic [15:0]  in_pSrc_6,
    input  logic [15:0]  in_pSrc_7,
    input  logic [15:0]  in_pSrc_8,
    input  logic [15:0]  in_pSrc_9,
    input  logic [15:0]  in_pSrc_10,
    input  logic [15:0]  in_pSrc_11,
    input  logic [15:0]  in_pSrc_12,
    input  logic [15:0]  in_pSrc_13,
    input  logic [15:0]  in_pSrc_14,
    input  logic [15:0]  in_pSrc_15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_pDst_0,
    output logic [15:0]  out_pDst_1,
    output logic [15:0]  out_pDst_2,
    output logic [15:0]  out_pDst_3,
    output logic [15:0]  out_pDst_4,
    output logic [15:0]  out_pDst_5,
    output logic [15:0]  out_pDst_6,
    output logic [15:0]  out_pDst_7,
    output logic [15:0]  out_pDst_8,
    output logic [15:0]  out_pDst_9,
    output logic [15:0]  out_pDst_10,
    output logic [15:0]  out_pDst_11,
    output logic [15:0]  out_pDst_12,
    output logic [15:0]  out_pDst_13,
    output logic [15:0]  out_pDst_14,
    output logic [15:0]  out_pDst_15,
    output logic [15:0]  out_pDst_16,
    output logic [15:0]  out_pDst_17
);
```

### Behavior interface

```python
class RealFftSplitQ15_16:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_pSrc_0, in_pSrc_1, in_pSrc_2, in_pSrc_3,
        in_pSrc_4, in_pSrc_5, in_pSrc_6, in_pSrc_7,
        in_pSrc_8, in_pSrc_9, in_pSrc_10, in_pSrc_11,
        in_pSrc_12, in_pSrc_13, in_pSrc_14, in_pSrc_15
    ):
        pass
```

`process()` returns exactly one output transaction per accepted input transaction as a single dict with keys `out_pDst_0` through `out_pDst_17`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_pDst_0`, `out_pDst_1`, `out_pDst_10`, `out_pDst_11`, `out_pDst_12`,
  `out_pDst_13`, `out_pDst_14`, `out_pDst_15`, `out_pDst_16`, `out_pDst_17`,
  `out_pDst_2`, `out_pDst_3`, `out_pDst_4`, `out_pDst_5`, `out_pDst_6`, `out_pDst_7`,
  `out_pDst_8`, `out_pDst_9`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `real_fft_split_q15_16`: the fixed-size forward split stage and ordered ready/valid transport.

## Functional Behavior

Each input is a signed 16-bit Q1.15 bit pattern. Pair `pSrc[2*k], pSrc[2*k+1]` is complex bin `Z[k]`, for `k=0..7`. The input is already in complex-frequency order; no bit reversal or CFFT is performed here. The 18 output words are real/imaginary pairs for bins 0 through 8.

Use these exact signed Q1.15 coefficient pairs, indexed by output bin:

| k | A real | A imag | B real | B imag |
| --- | --- | --- | --- | --- |
| 1 | 10114 | -15137 | 22654 | 15137 |
| 2 | 4799 | -11585 | 27969 | 11585 |
| 3 | 1247 | -6270 | 31521 | 6270 |
| 4 | 0 | 0 | 32767 | 0 |
| 5 | 1247 | 6270 | 31521 | -6270 |
| 6 | 4799 | 11585 | 27969 | -11585 |
| 7 | 10114 | 15137 | 22654 | -15137 |

These approximate `A[k]=(1-sin(pi*k/8))/2 - j*cos(pi*k/8)/2` and `B[k]=(1+sin(pi*k/8))/2 + j*cos(pi*k/8)/2`. The table, not a recomputed real-valued trigonometric expression, defines the integer function.

Let `(r,i)=Z[k]` and `(s,t)=Z[8-k]`. For `k=1..7`, compute:

```text
real_acc = signed32(r*A_real - i*A_imag + s*B_real + t*B_imag)
imag_acc = signed32(s*B_imag - t*B_real + i*A_real + r*A_imag)
pDst[2*k]   = low16(real_acc >> 16)
pDst[2*k+1] = low16(imag_acc >> 16)
```

Products are signed 16×16 products; accumulation wraps modulo `2^32`. Shifts are arithmetic, with no rounding or saturation, and `low16` selects the two's-complement output bits. The shift by 16 includes one extra scaling bit beyond the Q15 product conversion.

For the endpoints, first form signed 17-bit sums/differences, then shift:

```text
pDst[0]  = low16((pSrc[0] + pSrc[1]) >> 1)
pDst[1]  = 0
pDst[16] = low16((pSrc[0] - pSrc[1]) >> 1)
pDst[17] = 0
```

All 16-bit input patterns are legal, including values that wrap the internal accumulators. One accepted input vector produces one complete 18-word output vector in order. Transfers occur on rising edges with both valid and ready high. A stalled output holds every output word and `out_valid` stable until accepted. Input backpressure is permitted; latency is not fixed.

## Reset Behavior

`rst` is active high and synchronous. A reset edge discards any pending output, clears all output words, sets `out_valid=0`, and restores `in_ready=1`. There is no arithmetic state between vectors; coefficient constants survive reset.
