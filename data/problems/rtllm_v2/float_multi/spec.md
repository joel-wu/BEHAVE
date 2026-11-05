# float_multi

Periodically sequenced binary32 multiplier with exact RNE, subnormal inputs and gradual underflow.

## Interface

### RTL interface

```systemverilog
module float_multi (
    input logic clk, rst,
    input logic [31:0] a, b,
    output logic [31:0] z
);
```

### Behavior interface

```python
class FloatMultiplier:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b): ...
```

Call `process(a, b)` on every released rising edge, including edges that do
not capture operands or publish a result. It returns exactly `{"z": value}`,
where both arguments and `value` are unsigned 32-bit FP32 encodings, not
Python floats. The record observes the pre-edge output, then the call advances
phase and state. It is never `None` or a list, even when `z` is unchanged.
Reuse the instance across edges. `reset()` clears phase, in-flight result and
output, returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each released rising edge
of `clk`, then advance that edge. Inputs and outputs are unsigned port bit
patterns. Idle cycles are clock edges too. Reset is controlled separately and
produces no logical records.

## Modules

- `float_multi`: the complete native module above.

## Functional Behavior

Multiply two IEEE-754 binary32 bit patterns using round-to-nearest,
ties-to-even (RNE), gradual underflow and signed zero. All 32-bit operand patterns
are admitted. In particular, subnormal inputs are NOT flushed to zero and
subnormal outputs are retained. No exception/status flags are exposed.

### Numeric result

The sign of every non-NaN result is a[31] XOR b[31]. Handle special cases first:
any NaN operand, or infinity times zero, yields the canonical quiet NaN
32'hffc00000 (the sign/payload convention used by the original RTL). Otherwise
an infinite operand yields signed infinity; a zero operand yields signed zero.
Signaling and quiet input NaNs share this canonical result.

For a finite nonzero operand with exponent field e and fraction f, use
(M,E)=(f,-126) when e=0, or (2**23+f,e-127) otherwise. Its magnitude is exactly
M*2**(E-23). Multiply these exact values, then round ONCE to binary32 using RNE.
This means choosing the nearest representable value, with an exact tie choosing
the value whose least significand bit is even. A tiny result can round to signed
zero, a subnormal, or the smallest normal. Overflow rounds to signed infinity;
the positive overflow threshold is 2**128-2**103. The sign is preserved even
when the rounded magnitude is zero. No truncating operand normalization,
intermediate FTZ, double rounding or wrapping exponent arithmetic is allowed.

One exact integer construction is: P=M_a*M_b and L=floor(log2(P)), with P>0.
The normalized unbiased exponent is E=E_a+E_b-46+L. For a normal-range result
(E>=-126), round P/2**(L-23) to an integer with ties-to-even, renormalizing any
24-bit significand carry before checking overflow. Otherwise round
P*2**(E_a+E_b+103) to the nearest even integer in units of 2**-149. A rounded
subnormal significand of 2**23 is the smallest normal, NOT zero. Integer widths
must preserve the full 48-bit product and the exponent range, including shifts
larger than the product width.

### Periodic capture and output

There are no handshake, enable or valid pins. After reset release, number rising
edges 1,2,3,... . Capture (a,b) only at edges 1,9,17,... . Publish that pair's
result immediately after edges 7,15,23,..., respectively. Thus a new pair is
captured every eight edges; the result is published on its seventh processing
edge and remains unchanged between publications. Changing operands on any
other edge does not change the in-flight operation. The phase progresses on
EVERY non-reset edge, not only when data changes. Special values use the same
publication edge; they cannot appear early and later be overwritten by stale
finite arithmetic.

### Original implementation repairs

The original task requests IEEE single-precision multiplication and provides
the periodic counter-based interface above. Its reference normalizes a
subnormal only once, does not consistently round after normalization, allows
later stages to overwrite special results, and has counter-sensitive procedural
blocks rather than a single well-defined clocked state machine. Its supplied TB
checks essentially one product and is insufficient to establish IEEE behavior.
Those bugs are corrected rather than preserved. Reset is made explicitly
asynchronous active-high and initializes the output to zero, in addition to
restarting phase and discarding in-flight work. This resolves the source's
incomplete output initialization and ambiguous `posedge clk or rst` sensitivity.
The original top-level name and ports are restored; no ready/valid wrapper or
FTZ subset is substituted. This is a documented source-intent repair, not
bit-for-bit equivalence to the faulty upstream RTL.

## Reset Behavior

`rst` is asynchronous, active high. Assertion immediately restores the initial state. Phase, in-flight result and output z are zero. The first released edge captures a new operand pair.
After reset release, resume the edge-by-edge behavior above.
