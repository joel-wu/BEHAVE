# FramedFft64Fixed

A forward 64-point complex fixed-point Fourier transform with two programmable
normalization shifts. It collects 64 signed 16-bit complex samples, computes an
8-by-8 Cooley–Tukey transform with the integer arithmetic below, and emits all
64 signed 19-bit frequency bins in natural order. Its interface is framed
ready/valid; neither a continuous-pipeline throughput nor a fixed latency is
required.

## Interface

### RTL interface

```systemverilog
module FramedFft64Fixed (
    input  logic clk, rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [3:0] in_shift,
    input  logic [15:0] in_dr, in_di,
    output logic out_valid,
    input  logic out_ready,
    output logic out_ovf1, out_ovf2,
    output logic [5:0] out_addr,
    output logic [18:0] out_dor, out_doi
);
```

Transfers occur on rising `clk` edges with the corresponding valid and ready
both high and reset inactive. A sender holds valid and its entire payload
stable while stalled. The design may backpressure inputs while computing or
emitting a frame. Output values and flags remain stable under backpressure.

### Behavior interface

```python
class FramedFft64Fixed:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_shift, in_dr, in_di): ...
```

`process` consumes one accepted input, returning `None` for the first 63 samples
and a list of 64 output mappings for sample 63. Each mapping has `out_addr`,
`out_dor`, `out_doi`, `out_ovf1` and `out_ovf2`. Integer payloads encode the port
bit patterns. Calls for the next frame follow in input order; their timing is
not part of the untimed Behavior.

Return None for samples 0 through 62 of each 64-sample frame. Sample 63 returns a list
of exactly 64 dictionaries, in output-bin order with out_addr=0 through 63. Never
return a bare dictionary. Reset discards a partial frame.
Every returned dictionary contains exactly these fields (bit widths):

- `out_addr`: 6.
- `out_doi`, `out_dor`: 19.
- `out_ovf1`, `out_ovf2`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `FramedFft64Fixed`: frame collection, fixed-point transform and ordered output.

## Functional Behavior

Every 64 accepted inputs form a frame `x[0]` through `x[63]`. Interpret each
`in_dr` and `in_di` as signed two's-complement 16-bit integers. Capture `in_shift`
only from the first accepted input of the frame: `s1 = in_shift & 3` and
`s2 = in_shift >> 2`. Later shift fields in that frame have no effect. A partial
frame produces no outputs and is retained until completed or reset.

All calculations below use unbounded signed integers until an explicit wrap.
`v >> b` means floor division by `2**b`, including for negative values.
`wrap_w(v) = ((v + 2**(w-1)) mod 2**w) - 2**(w-1)`; it does not saturate.

The Q15 cosine table for angles `i*pi/32`, `i=0..16`, is:

```text
C = [32767,32610,32138,31357,30274,28899,27246,25330,
     23170,20788,18205,15447,12540,9512,6393,3212,0]
```

For an integer `a`, let `j = a mod 64`, `q = j // 16`, `d = j mod 16`.
The cosine/sine pair `W(a)` is `(C[d],C[16-d])` for `q=0`,
`(-C[16-d],C[d])` for `q=1`, `(-C[d],-C[16-d])` for `q=2`, and
`(C[16-d],-C[d])` for `q=3`. These exact integers, including unity as 32767,
are used without replacing special angles by exact real-valued rotations.

For complex integer pairs `z[n]=(r[n],i[n])`, define the eight-point transform
`F(z)[k]`, `k=0..7`, by taking `(c[n],s[n]) = W(8*n*k)` and computing:

```text
real = sum(r[n]*c[n] + i[n]*s[n] for n=0..7) >> 15
imag = sum(i[n]*c[n] - r[n]*s[n] for n=0..7) >> 15
```

Products are accumulated before the one shift per component; there is no
per-product rounding. Apply the following stages, independently per component
where a rule acts on a complex pair:

1. For `r=0..7`, compute `T[r,q] = F([x[r+8*n] for n=0..7])[q]`.
   Set `a = (T[r,q] << s1) >> 1` and `A[r,q] = wrap_18(a)`.
   `out_ovf1` is one if any real or imaginary `a` in the frame lies outside
   `[-131072,131071]`.
2. With `(c,s) = W(r*q)` and `A[r,q]=(ar,ai)`, compute
   `B[r,q] = (wrap_18((ar*c+ai*s)>>16), wrap_18((ai*c-ar*s)>>16))`.
3. For `q=0..7`, compute `U[p,q] = F([B[r,q] for r=0..7])[p]`.
   Set `b = (U[p,q] << s2) >> 1` and `Y[q+8*p] = wrap_19(b)`.
   `out_ovf2` is one if any real or imaginary `b` in the frame lies outside
   `[-262144,262143]`.

Output beat `k` has `out_addr=k`, and `out_dor/out_doi` are the 19-bit encodings
of `Y[k]`. Both overflow flags describe the entire frame and are repeated on
every output beat. Overflow wraps data; it neither cancels nor shortens output.
Flags start afresh for each frame. At shifts zero and without overflow, the
nominal gain is one eighth of the unnormalized forward DFT, subject to the
specified coefficient quantization and floor operations.

## Reset Behavior

`rst` is synchronous active-high. A reset edge discards all partial input,
in-flight computation and pending outputs, clears shift/count/overflow state,
and starts a new frame. No transfer is counted with reset high. While reset is
high, `in_ready=0` and `out_valid=0`. Following deassertion after a reset edge,
the design is ready for a new frame. Whenever `out_valid=0`, all output payload
fields and overflow flags are zero. Reset never emits a partial result.
