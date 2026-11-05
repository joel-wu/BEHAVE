# Bounded-Vector Phase Unwrap

This block removes whole-period discontinuities from up to eight phase samples.
It preserves the first sample and adjusts later samples by accumulated multiples
of a positive period. The resulting adjacent jumps are within a configurable
threshold, without clipping intermediate differences or limiting the number
of periods that may be removed.

## Interface

### RTL interface

```systemverilog
module signal_processing_dsp_math_phase_unwrap (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_n,
    input  logic [63:0] in_lev,
    input  logic [63:0] in_mar,
    input  logic [63:0] in_phi0,
    input  logic [63:0] in_phi1,
    input  logic [63:0] in_phi2,
    input  logic [63:0] in_phi3,
    input  logic [63:0] in_phi4,
    input  logic [63:0] in_phi5,
    input  logic [63:0] in_phi6,
    input  logic [63:0] in_phi7,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_status,
    output logic [127:0] out_phi0,
    output logic [127:0] out_phi1,
    output logic [127:0] out_phi2,
    output logic [127:0] out_phi3,
    output logic [127:0] out_phi4,
    output logic [127:0] out_phi5,
    output logic [127:0] out_phi6,
    output logic [127:0] out_phi7
);
```

`in_n` is unsigned. Phase inputs and `in_lev` are signed int64 in the same
phase unit. `in_mar` is a signed fixed-point number with 16 fractional bits:
65536 means 1.0 and 32768 means 0.5. Outputs are signed int128 encodings in the
input phase unit. They are widened so exact unwrapped values do not overflow.

### Behavior interface

```python
class SignalProcessingDspMathPhaseUnwrap:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_n, in_lev, in_mar, in_phi0, in_phi1, in_phi2, in_phi3, in_phi4, in_phi5, in_phi6, in_phi7): ...
```

The returned mapping has `out_status` and `out_phi0` through `out_phi7`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_phi0`, `out_phi1`, `out_phi2`, `out_phi3`, `out_phi4`, `out_phi5`, `out_phi6`,
  `out_phi7`: 128.
- `out_status`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_dsp_math_phase_unwrap`: finite-vector, whole-period
  phase unwrapping with exact integer arithmetic.

## Functional Behavior

A valid command has length 1–8, period `L=in_lev>0`, and margin
`m=in_mar/65536 >= 0.5`. The threshold is the exact rational `T=m*L`.
Below a half-period, repeated whole-period correction can oscillate, so such
margins are invalid.

Let `p[i]` be decoded input phases. Set `q[0]=p[0]` and correction `C=0`.
For each `i=1,...,in_n-1`, use the **original** adjacent difference
`d=p[i]-p[i-1]`. If `abs(d)>T`, let
`k=ceil((abs(d)-T)/L)` and update `C=C-sign(d)*k*L`.
Otherwise keep `C`. Set `q[i]=p[i]+C`.
Thus exact-threshold jumps are unchanged; larger jumps lose the minimum
number of whole periods needed to enter [-T,T]. All arithmetic is exact,
including the comparison with the fractional threshold.

Return `out_status=0` and the resulting vector. Positions outside the selected
length are unchanged, sign-extended to 128 bits.
Invalid length takes precedence and returns status 1; nonpositive period or
margin below 0.5 returns status 2. On either error, all eight input phases are
returned unchanged with sign extension. There is no iteration-limit status.

Examples: period 10 and margin 1.0 map [0,15,16] to [0,5,6].
Period 10 and margin 0.5 map [0,5,15] to [0,5,5]; the first half-period tie
is preserved. A jump of one million periods is handled by the same rule.

## Reset Behavior

Synchronous active-high reset clears output valid, status and payload, and
cancels any pending result. There is no numerical state across commands.
Each accepted input produces one ordered response. Transfers require valid
and ready; an unaccepted output retains its valid flag and all payload bits.
