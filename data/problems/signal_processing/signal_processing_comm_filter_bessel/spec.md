# Bessel Prototype Pole Table

Return the ordered analog low-pass Bessel poles and gain for orders one through eight. Each request produces one fixed-size vector of raw IEEE-754 binary32 words. The exact table below defines the numerical result; a ROM implementation is permitted.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_filter_bessel (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_n,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_p0_re,
    output logic [31:0] out_p0_im,
    output logic [31:0] out_p1_re,
    output logic [31:0] out_p1_im,
    output logic [31:0] out_p2_re,
    output logic [31:0] out_p2_im,
    output logic [31:0] out_p3_re,
    output logic [31:0] out_p3_im,
    output logic [31:0] out_p4_re,
    output logic [31:0] out_p4_im,
    output logic [31:0] out_p5_re,
    output logic [31:0] out_p5_im,
    output logic [31:0] out_p6_re,
    output logic [31:0] out_p6_im,
    output logic [31:0] out_p7_re,
    output logic [31:0] out_p7_im,
    output logic [31:0] out_k_re,
    output logic [31:0] out_k_im
);
```

### Behavior interface

```python
class SignalProcessingCommFilterBessel:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_n): ...
```

`in_n` is an unsigned 8-bit order. `process` returns one dictionary containing all eighteen output fields shown in the RTL interface. Real and imaginary parts are separate unsigned 32-bit containers, not integer-valued pole coordinates.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_k_im`, `out_k_re`, `out_p0_im`, `out_p0_re`, `out_p1_im`, `out_p1_re`,
  `out_p2_im`, `out_p2_re`, `out_p3_im`, `out_p3_re`, `out_p4_im`, `out_p4_re`,
  `out_p5_im`, `out_p5_re`, `out_p6_im`, `out_p6_re`, `out_p7_im`, `out_p7_re`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
FP32 values are raw integer encodings, not host floating-point values.

## Modules

- `signal_processing_comm_filter_bessel`: bounded Bessel pole-and-gain lookup with an ordered ready/valid interface.

## Functional Behavior

For `1 <= n <= 8`, return `n` poles in slots `p0` through `p(n-1)`; all remaining pole slots contain positive zero in both components. The poles approximate the roots of the degree-`n` reverse Bessel polynomial

```text
P_n(s) = sum(k=0..n) (2*n-k)! / (2^(n-k) * k! * (n-k)!) * s^k
```

divided by `sqrt((2*n-1)*ln(2))`, an approximate 3-dB normalization. Conjugates are adjacent, with the positive-imaginary pole first and pairs in decreasing imaginary magnitude. An odd-order real pole is last. There are no finite zeros. The gain is the product of the normalized poles, with negative real sign for odd orders.

Use these exact binary32 bit patterns. Each pair is `(real, imaginary)`, in output-slot order. The small nonzero imaginary gains for orders five and eight are included in the specified finite-precision results.

```text
n=1: (0xBF99BE61,0x00000000); gain=(0xBF99BE61,0x00000000)
n=2: (0xBF85255B,0x3F19BE61) (0xBF85255B,0xBF19BE61); gain=(0x3FB8AA3B,0x00000000)
n=3: (0xBF7CDF6F,0x3F713FD5) (0xBF7CDF6F,0xBF713FD5) (0xBF9FAA2F,0x00000000); gain=(0xC014CA9E,0x00000000)
n=4: (0xBF74803A,0x3F9A6BEF) (0xBF74803A,0xBF9A6BEF) (0xBFA84C3A,0x3EC99423) (0xBFA84C3A,0xBEC99423); gain=(0x408EB8F1,0x00000000)
n=5: (0xBF6E44F2,0x3FB701E5) (0xBF6E44F2,0xBFB701E5) (0xBFABC7DC,0x3F329D8D) (0xBFABC7DC,0xBF329D8D) (0xBFBAE33E,0x00000000); gain=(0xC11B8DE5,0xB4BAE33E)
n=6: (0xBF694121,0x3FD0428A) (0xBF694121,0xBFD0428A) (0xBFAD2BA5,0x3F737BEF) (0xBFAD2BA5,0xBF737BEF) (0xBFC4EF45,0x3EA0DAF1) (0xBFC4EF45,0xBEA0DAF1); gain=(0x41BB9C9C,0x00000000)
n=7: (0xBF6509F7,0x3FE72492) (0xBF6509F7,0xBFE72492) (0xBFAD8DD7,0x3F95F9A6) (0xBFAD8DD7,0xBF95F9A6) (0xBFCAE5BC,0x3F145444) (0xBFCAE5BC,0xBF145444) (0xBFD40046,0x00000000); gain=(0xC2761D44,0x00000000)
n=8: (0xBF616528,0x3FFC3A4A) (0xBF616528,0xBFFC3A4A) (0xBFAD67D2,0x3FAF3CD8) (0xBFAD67D2,0xBFAF3CD8) (0xBFCE9D19,0x3F4FB4A9) (0xBFCE9D19,0xBF4FB4A9) (0xBFDDD1B7,0x3E89C3CC) (0xBFDDD1B7,0xBE89C3CC); gain=(0x432D7506,0x36C00000)
```

For `in_n = 0` or `in_n >= 9`, all eighteen output fields are zero.

Outside reset, a rising edge with `in_valid && in_ready` accepts one request; `out_valid && out_ready` transfers its complete vector. Results preserve request order. While an output is stalled, its valid bit and all eighteen fields remain stable. Input valid and payload must likewise remain stable until accepted. Consecutive requests and simultaneous input/output transfers are allowed; the contract does not prescribe an internal pipeline.

## Reset Behavior

`rst` is synchronous active-high. A reset edge cancels any pending result, clears `out_valid`, and sets all output fields to zero. The fixed table is unchanged.
