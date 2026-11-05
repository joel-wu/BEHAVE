# Butterworth Analog Pole Generator

For each accepted filter order, this block emits the ordered analog poles of a normalized low-pass Butterworth prototype, followed by its unit gain. Orders are bounded at sixteen; the pole coordinates are correctly rounded binary32 constants.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_filter_butter (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_n,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [1:0]  out_kind,
    output logic [31:0] out_real,
    output logic [31:0] out_imag
);
```

in_n is an unsigned 32-bit value. All patterns are legal: the effective order is n=min(in_n,16). out_kind=0 denotes a pole and out_kind=1 denotes the gain; values 2 and 3 are never emitted. out_real and out_imag carry IEEE-754 binary32 bit patterns. Reset is asynchronous active-high.

### Behavior interface

```python
class ButterworthAnalogPoleGenerator:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_n): ...
```

process returns an ordered list of dictionaries with out_kind, out_real and out_imag.

Return a list of min(in_n, 16)+1 dictionaries: ordered poles first and the gain record
last. Even in_n=0 returns a one-element list, not None or a bare dictionary.
Every returned dictionary contains exactly these fields (bit widths):

- `out_imag`, `out_real`: 32.
- `out_kind`: 2.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
FP32 values are raw integer encodings, not host floating-point values.

## Modules

- signal_processing_comm_filter_butter: bounded-order pole generator and output sequence transport.

## Functional Behavior

Let L=floor(n/2). For i=0,...,L-1 define

theta_i = (2*(i+1)+n-1)*pi/(2*n).

Emit the pair (cos(theta_i),sin(theta_i)), then its complex conjugate, in ascending i order. Each mathematical component is rounded independently to binary32, round-to-nearest with ties to even. For odd n, append the real pole (-1,+0). These are stable unit-circle poles in the open left half-plane. There are no finite zeros, so no zero transactions are emitted.

Finally emit gain (out_kind=1, out_real=0x3F800000, out_imag=0x00000000). Every input therefore produces exactly n+1 transactions. For n=0, emit only the gain. A new input must not interleave outputs with an older input's sequence.

The following pairs give the positive-imaginary pole of each conjugate pair, in order. Each tuple is (real_bits,imag_bits). Emit its conjugate by flipping bit31 of imag_bits. Odd orders additionally emit (0xBF800000,0x00000000); orders zero and one have no complex pairs.

```
n=2: (0xBF3504F3,0x3F3504F3)
n=3: (0xBF000000,0x3F5DB3D7)
n=4: (0xBEC3EF15,0x3F6C835E) (0xBF6C835E,0x3EC3EF15)
n=5: (0xBE9E377A,0x3F737871) (0xBF4F1BBD,0x3F167918)
n=6: (0xBE8483EE,0x3F7746EA) (0xBF3504F3,0x3F3504F3) (0xBF7746EA,0x3E8483EE)
n=7: (0xBE63DC87,0x3F7994E0) (0xBF1F9D07,0x3F48261C) (0xBF66A5E5,0x3EDE2602)
n=8: (0xBE47C5C2,0x3F7B14BE) (0xBF0E39DA,0x3F54DB31) (0xBF54DB31,0x3F0E39DA) (0xBF7B14BE,0x3E47C5C2)
n=9: (0xBE31D0D4,0x3F7C1C5C) (0xBF000000,0x3F5DB3D7) (0xBF441B7D,0x3F248DBB) (0xBF708FB2,0x3EAF1D44)
n=10: (0xBE20305B,0x3F7CD925) (0xBEE87171,0x3F641901) (0xBF3504F3,0x3F3504F3) (0xBF641901,0x3EE87171) (0xBF7CD925,0x3E20305B)
n=11: (0xBE11BAFB,0x3F7D64F0) (0xBED4B147,0x3F68DDA4) (0xBF27A4F4,0x3F4178CE) (0xBF575C64,0x3F0A6770) (0xBF75A155,0x3E903F40)
n=12: (0xBE05A8A8,0x3F7DCF55) (0xBEC3EF15,0x3F6C835E) (0xBF1BD7CA,0x3F4B1934) (0xBF4B1934,0x3F1BD7CA) (0xBF6C835E,0x3EC3EF15) (0xBF7DCF55,0x3E05A8A8)
n=13: (0xBDF6DBEF,0x3F7E222B) (0xBEB58EC6,0x3F6F5D39) (0xBF116CB1,0x3F52AF12) (0xBF3F9E67,0x3F29C268) (0xBF62AD3F,0x3EEDF032) (0xBF788FA5,0x3E750F2A)
n=14: (0xBDE54DA2,0x3F7E63EC) (0xBEA91A56,0x3F71A257) (0xBF083341,0x3F58C2EB) (0xBF3504F3,0x3F3504F3) (0xBF58C2EB,0x3F083341) (0xBF71A257,0x3EA91A56) (0xBF7E63EC,0x3DE54DA2)
n=15: (0xBDD61305,0x3F7E98FD) (0xBE9E377A,0x3F737871) (0xBF000000,0x3F5DB3D7) (0xBF2B4C25,0x3F3E3EBD) (0xBF4F1BBD,0x3F167918) (0xBF69DE1D,0x3ED03FC9) (0xBF7A67E2,0x3E54E6CD)
n=16: (0xBDC8BD36,0x3F7EC46D) (0xBE94A031,0x3F74FA0B) (0xBEF15AEA,0x3F61C598) (0xBF226799,0x3F45E403) (0xBF45E403,0x3F226799) (0xBF61C598,0x3EF15AEA) (0xBF74FA0B,0x3E94A031) (0xBF7EC46D,0x3DC8BD36)
```

For n=3 the sequence is (-0.5,+sqrt(3)/2), its conjugate, (-1,+0), then gain (+1,+0), using the stated binary32 rounding. For in_n>16 use the order-sixteen row; do not truncate or wrap the order modulo sixteen.

Outside reset, transfers occur on rising edges with valid && ready. Hold out_valid, out_kind and both components stable while stalled. The input sender holds a pending order stable under backpressure. No runtime trigonometric implementation or fixed output latency is required.

## Reset Behavior

Asynchronous assertion clears the pending sequence and deasserts out_valid. No pre-reset pole or gain is emitted afterward. Output payloads are zero during reset and whenever out_valid=0. The receiver is ready once reset is released; the next accepted order starts a fresh sequence.
