# Q15 Normalized LMS Filter Step

One sample update of an eight-tap Q1.15 normalized LMS adaptive filter. Each transaction computes the FIR output and wrapped error, updates the sliding energy, and adjusts all coefficients using a 64-entry reciprocal table and two fixed-point refinement steps. Sample history, coefficients and energy persist between transactions.

## Interface

### RTL interface

```systemverilog
module q15_normalized_lms_filter_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_sample,
    input  logic [15:0]  in_ref,
    input  logic [15:0]  in_mu,
    input  logic  [4:0]  in_postShift,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_sample,
    output logic [15:0]  out_error
);
```

### Behavior interface

```python
class Q15NormalizedLmsFilterStep:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_sample, in_ref, in_mu, in_postShift):
        pass
```

The returned dictionary contains keys `out_sample` and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`, `out_sample`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q15_normalized_lms_filter_step`: top-level 8-tap normalized LMS adaptive FIR step with internal coefficient, state-history, energy, and oldest-sample state.

## Functional Behavior

Data ports carry raw 16-bit two's-complement integers. `in_postShift` is an
unsigned 5-bit value. Define `wrap16(v)` as the signed value congruent to `v`
modulo 65536, and `sat16(v) = min(32767,max(-32768,v))`. Right shifts below are
arithmetic (rounding toward negative infinity); intermediate products and sums
are exact unless a narrowing is explicitly shown.

Persistent state consists of eight signed coefficients `c[0..7]`, seven past
samples `s[0..6]` oldest first, signed 16-bit `energy`, and signed 16-bit `x0`.
For an accepted input, interpret the sample, reference and `mu` as signed values:

1. Form `x = (s[0],...,s[6],sample)` and
   `E = sat16(energy - ((x0*x0)>>15) + ((sample*sample)>>15))`.
2. Compute `A = sum(c[i]*x[i], i=0..7)` from the old coefficients.
   Set `y = sat16(A >> (15-postShift))` when `postShift <= 15`, otherwise
   `y = sat16(A << (postShift-15))`.
3. Compute `e = wrap16(ref-y)`, `g = wrap16((e*mu)>>15)` and
   `d = wrap16(E+5)`. In particular, the error is **not** saturated.
4. Obtain `w` from the reciprocal procedure below. Update each coefficient
   as `c'[i] = sat16(c[i] + ((w*x[i])>>15))`.
5. Commit `c=c'`, `energy=E`, `x0=s[0]`, and `s=(s[1],...,s[6],sample)`.
   Return `out_sample=y` and `out_error=e` as unsigned 16-bit encodings.

The reciprocal procedure uses this fixed 64-entry unsigned hexadecimal table,
indexed from zero:

```text
7F03 7D13 7B31 795E 7798 75E0 7434 7294
70FF 6F76 6DF6 6C82 6B16 69B5 685C 670C
65C4 6484 634C 621C 60F3 5FD0 5EB5 5DA0
5C91 5B88 5A85 5988 5890 579E 56B0 55C8
54E4 5405 532B 5255 5183 50B6 4FEC 4F26
4E64 4DA6 4CEC 4C34 4B81 4AD0 4A23 4978
48D1 482D 478C 46ED 4651 45B8 4521 448D
43FC 436C 42DF 4255 41CC 4146 40C2 4040
```

If `d <= 0`, set `w=0`: coefficients do not change, but sample history, energy
and outputs still advance. Otherwise choose `b` such that
`16384 <= n = d*2^b < 32768` (`0 <= b <= 14`), and initialize
`r = table[(n>>8)&63]`. Perform exactly two refinements:

```text
t = 32767 - ((n*r)>>15)
r = wrap16((r*t)>>14)
```

Finally, `w = sat16((g*r) >> (14-b))`. This table/refinement computation is
part of the required mapping; replacing it with direct division changes the
coefficient trajectory. All port bit patterns are defined. A nonpositive
wrapped denominator freezes learning as specified above, and large post-shifts
use the explicit rescaling rule; neither permits an undefined shift or division.

Each input transfer occurs on a rising edge with `in_valid && in_ready` and
produces one ordered output. The sender holds input valid and payload until
transfer. Output valid and payload remain stable while `out_ready` is low.
Stalls do not repeat state updates. Latency and internal pipeline depth are not
prescribed. Reset cancels pending work; no pre-reset result may reappear.

## Reset Behavior

Reset is synchronous active-high. While `rst` is asserted, the module shall clear all architectural state to zero:

- `coeffs[0..7] = 0`
- `state[0..6] = 0`
- `energy = 0`
- `x0 = 0`

Output interface state after reset:
- `out_valid = 0`
- `out_sample = 16'h0000`
- `out_error = 16'h0000`

After reset deassertion, the first accepted input transaction is processed using the all-zero state above.
