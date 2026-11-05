# Complex Energy-Smoothed Automatic Gain Control

This block scales each complex sample by a carried gain, smooths the
output energy, and updates the gain multiplicatively from the smoothed energy.
It targets unit output power and caps gain at one million. Gain and filtered
energy both start at one; the output uses the old gain, not the updated gain.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_loop_agc_crcf (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [31:0] in_i,
    input logic [31:0] in_q,
    input logic [31:0] in_bandwidth,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_i,
    output logic [31:0] out_q,
    output logic [31:0] out_gain,
    output logic out_error
);
```

Data ports are unsigned containers for IEEE-754 binary32 bit patterns.
Each accepted command supplies its loop coefficient in_bandwidth; valid coefficients are finite values from 0 through 1, including either signed zero.
`out_gain` reports the updated gain, and `out_error` flags invalid commands.
Input/output transfers occur on rising edges with valid and ready. Pending
valid outputs retain all payload bits until transfer or reset. Results are
ordered; no particular internal latency or architecture is required.

### Behavior interface

```python
class signal_processing_comm_loop_agc_crcf:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_i, in_q, in_bandwidth): ...
```

Each accepted input returns one mapping with `out_i`, `out_q`, `out_gain`, `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_gain`, `out_i`, `out_q`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_loop_agc_crcf`: unlocked, unit-output-scale AGC core.

## Functional Behavior

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

Let `g` and `E` be the old gain and filtered energy, and `a` the loop
coefficient. The following `mul`, `add` and `sub` are separately rounded
binary32 operations; there is no fused operation:
1. y_i=mul(in_i,g), y_q=mul(in_q,g), and P=add(mul(y_i,y_i),mul(y_q,y_q)).
2. `Enew=add(mul(sub(1,a),E),mul(a,P))`.
3. If `Enew <= 0x358637bd` (binary32 1e-6), keep `g`.
   Otherwise compute the log/exp approximation below and set
   `gnew=min(mul(g,exp_approx(z)),0x49742400)` (binary32 1e6).
4. Return the scaled sample and `gnew`; store `gnew,Enew`.
   Output scaling is one and gain lock/squelch are outside this core.

The bit-exact log/exp approximation is defined by integer recurrences.
All integer intermediate arithmetic is exact. `//` and right shifts below
round toward negative infinity, except explicitly specified truncation.
Set `Q=2^48`, `L=195103586505167` (ln(2) rounded in Q48).
For positive normal `Enew` with exponent field `b` and fraction `f`:
- `m=Q+(f<<25)`, `w=((m-Q)<<48)//(m+Q)`, `v=(w*w)>>48`.
- Start `p=w,S=0`. For j=0..8, add `p//(2*j+1)` to S,
  then set `p=(p*v)>>48`.
- `logq=((b-127)*L+2*S)>>16` approximates ln(Enew) in Q32.
- Decode the nonnegative coefficient a (a subnormal rounds to zero on the Q32 grid), and
  let `aq=floor(a*2^32)`. Set `z=trunc_toward_zero(-aq*logq/2^33)`.

For `exp_approx(z)), set `k=(z<<16)//L` and `r=(z<<16)-k*L`.
Start `t=Q,V=Q`; for j=1..18, set `t=((t*r)>>48)//j` and add t to V.
Round `V/2^48 * 2^k` to binary32, nearest with ties to even.
This implements the multiplicative law exp(-a*ln(Enew)/2), not a linear
amplitude-error update. The stated finite recurrence defines the result,
rather than an unspecified math-library implementation.

For binary32 arithmetic, preserve subnormal operands and round each operation separately to binary32, nearest/ties-even, with gradual underflow. Overflow gives signed infinity.
Any NaN operand or invalid operation returns canonical NaN 0x7fc00000.
Products use XOR signs; adding two zeros gives -0 only if both are negative,
and exact nonzero cancellation gives +0. Subtraction negates the second sign.

An input NaN/infinity, invalid coefficient, or nonfinite Enew returns
`out_error=1`, zero sample outputs and the unchanged gain; neither state
updates. Otherwise `out_error=0`. Thus bad inputs cannot poison later state.
At unit input (i,q)=(1,0) from reset, both state values remain one.

## Reset Behavior

Synchronous active-high reset initializes gain and filtered energy to binary32
one, clears output valid and all output payloads, and cancels pending results.
The first post-reset command uses these initial states. No updates occur on
unaccepted input cycles.
