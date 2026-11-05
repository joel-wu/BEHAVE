# Q15 Fast FIR Decimator

Filters signed Q1.15 samples and retains every `M`-th filtered sample, starting at sample zero. Products accumulate with signed 32-bit wraparound; only the final shifted result saturates.

## Interface

### RTL interface

```systemverilog
module fir_decimate_fast_q15 #(
    parameter int unsigned NUM_TAPS = 8,
    parameter int unsigned M = 4,
    parameter logic [16*NUM_TAPS-1:0] COEFFS = 128'h10003000c0002000f0006000e0004000
) (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [15:0] in_sample,
    output logic out_valid,
    input logic out_ready,
    output logic [15:0] out_sample
);
```

`NUM_TAPS >= 1` and `M >= 1`. Coefficient `c[i]` is the signed Q1.15 value in `COEFFS[16*i +: 16]`, with `c[0]` multiplying the oldest sample. The default low-to-high coefficients are `[16384, -8192, 24576, -4096, 8192, -16384, 12288, 4096]`. Changing `NUM_TAPS` truncates this default at the high end or zero-extends it; overriding `COEFFS` supplies any signed 16-bit coefficients.

### Behavior interface

```python
class FirDecimateFastQ15:
    def __init__(self, num_taps=8, decimation_factor=4, coeffs=None): ...
    def reset(self): ...
    def process(self, in_sample): ...
```

`coeffs` supplies exactly `num_taps` signed values in the same order; `None` selects the RTL default.
`process()` returns `None` until a group is complete, then `{"out_sample": bits}`.

Return None for the first M-1 accepted samples of each M-sample group, then one
dictionary for its final sample. M is decimation_factor; for M=1 every call returns a
dictionary. Never return a list. Reset discards a partial group and clears filter
history.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sample`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor num_taps and decimation_factor match RTL NUM_TAPS and M. coeffs supplies
the signed values of COEFFS from low to high 16-bit slices; None selects the
documented default.

## Modules

- `fir_decimate_fast_q15`: the complete filter and its ordered ready/valid interface.

## Functional Behavior

Number accepted inputs `x[0], x[1], ...`; interpret their 16 bits as signed integers, and let `x[n]=0` for `n<0`. With `T=NUM_TAPS`, define:

```text
A[n] = signed32(sum(c[i] * x[n-T+1+i] for i=0..T-1))
F[n] = clamp(floor(A[n] / 32768), -32768, 32767)
```

`signed32` retains the low 32 bits and interprets them as two's complement. Products are exact signed 16×16 products; accumulation wraps, never saturates. The arithmetic right shift discards 15 low bits without rounding. Outputs carry the two's-complement bits of `F`.

After each complete group of `M` accepted inputs, emit `F[j*M]` for group `j`, in order. Thus the first output is `F[0]`, released after input `x[M-1]`, not `F[M-1]`. Every input still updates the FIR history. An incomplete final group has no output; `M=1` is a 1:1 FIR.

Input and output transfers occur on rising edges with both valid and ready high. Input bubbles do not advance history or group position. A pending output and its valid flag remain stable until accepted; input backpressure is permitted. No fixed computational latency is required.

## Reset Behavior

`rst` is active high and synchronous to `clk`. A reset edge clears all sample history, group state and pending output, sets `out_valid=0` and `out_sample=0`, and restores `in_ready=1`. Reset discards unfinished work but does not alter coefficients. The next accepted sample is again `x[0]`.
