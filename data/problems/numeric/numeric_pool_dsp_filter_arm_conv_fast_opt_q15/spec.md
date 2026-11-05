# Q15 Fast Convolution Frame

One input transaction carries two signed 16-sample Q15 sequences. The block computes their full convolution with wrapping 32-bit accumulation and final Q15 saturation, then emits the complete 31-sample frame in index order. Input frames are independent; no external memory or length input is required.

## Interface

### RTL interface

```systemverilog
module q15_fast_convolution_frame (
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
    input  logic [15:0]  in_a8,
    input  logic [15:0]  in_a9,
    input  logic [15:0]  in_a10,
    input  logic [15:0]  in_a11,
    input  logic [15:0]  in_a12,
    input  logic [15:0]  in_a13,
    input  logic [15:0]  in_a14,
    input  logic [15:0]  in_a15,
    input  logic [15:0]  in_b0,
    input  logic [15:0]  in_b1,
    input  logic [15:0]  in_b2,
    input  logic [15:0]  in_b3,
    input  logic [15:0]  in_b4,
    input  logic [15:0]  in_b5,
    input  logic [15:0]  in_b6,
    input  logic [15:0]  in_b7,
    input  logic [15:0]  in_b8,
    input  logic [15:0]  in_b9,
    input  logic [15:0]  in_b10,
    input  logic [15:0]  in_b11,
    input  logic [15:0]  in_b12,
    input  logic [15:0]  in_b13,
    input  logic [15:0]  in_b14,
    input  logic [15:0]  in_b15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_data,
    output logic [4:0]   out_index
);
endmodule
```

Each `in_a*` and `in_b*` port carries one signed Q15 sample as a 16-bit two's-complement bit pattern.

### Behavior interface

```python
class Q15FastConvolutionFrame:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
        in_a8, in_a9, in_a10, in_a11, in_a12, in_a13, in_a14, in_a15,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
        in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15
    ):
        pass
```

`process()` returns a list of 31 dictionaries. Each dictionary has keys `out_data` and `out_index`.

Return a list of exactly 31 dictionaries, in increasing out_index order from 0 through
30. Never return None or a bare dictionary.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data`: 16.
- `out_index`: 5.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q15_fast_convolution_frame`: top-level fixed-frame Q15 fast-convolution kernel.
- No helper modules are required.

## Functional Behavior

Interpret each `in_a*` and `in_b*` as a signed 16-bit integer. For `n = 0..30`, compute

`acc[n] = wrap32(sum(A[i] * B[n-i] for i = 0..15 if 0 <= n-i < 16))`.

Products are exact signed 16-by-16 products. `wrap32` keeps the low 32 bits and interprets them as signed two's complement. Applying it after every addition gives the same result; intermediate sums do not saturate.

The output beat at index `n` is:

- `out_index = n`;
- `out_data = clamp(acc[n] >> 15, -32768, 32767)` encoded in 16-bit two's complement.

The shift is arithmetic and rounds toward negative infinity. All 16-bit input patterns are valid, including `0x8000 = -32768`. Wrapping accumulation must not be replaced by an unbounded sum followed by saturation.

An input transfers on a rising clock edge with `in_valid && in_ready`; all 32 operand words belong to that input frame. Each accepted frame produces exactly 31 output transfers, in order `0..30`, with no interleaving between frames. Output transfers use `out_valid && out_ready`. While an output is stalled, valid, index and data remain unchanged. Latency and internal implementation are not fixed.

## Reset Behavior

Reset is synchronous active-high. A rising edge with `rst=1` cancels any pending frame and restores `in_ready=1`, `out_valid=0`, `out_data=0` and `out_index=0`. No transaction transfers during reset. After reset, the next accepted input starts a new independent frame at output index zero.
