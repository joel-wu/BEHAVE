# dynamic_equalizer

Continuously clocked complex Q2.13 LMS adaptive FIR equalizer.

## Interface

### RTL interface

```systemverilog
module dynamic_equalizer #(
    parameter int TAP_NUM=7,
    parameter int DATA_WIDTH=16,
    parameter int COEFF_WIDTH=16,
    parameter int MU=15
) (
    input logic clk, rst_n,
    input logic signed [DATA_WIDTH-1:0] data_in_real, data_in_imag,
    input logic signed [DATA_WIDTH-1:0] desired_real, desired_imag,
    output logic signed [DATA_WIDTH-1:0] data_out_real, data_out_imag
);
```

### Behavior interface

```python
class DynamicEqualizer:
    def __init__(self, TAP_NUM=7, DATA_WIDTH=16, COEFF_WIDTH=16, MU=15): ...
    def reset(self): ...
    def process(self, data_in_real, data_in_imag, desired_real, desired_imag): ...
```

Each call returns exactly one dictionary with keys `data_out_real` and
`data_out_imag`, observing the settled output pins immediately BEFORE one
rising edge of `clk`, then advances that edge. Arguments and returned values
are signed 16-bit integers, not floating-point values or packed complex pairs.
Every call represents a clock edge, including zero-valued samples; it returns
both keys even when both values are zero, never `None`. Reuse the instance to
retain histories and weights. `reset()` restores the state described below,
returns `None`, and emits no logical record.

## Modules

- `dynamic_equalizer`: the complete native module above.

## Functional Behavior

The native source has no valid, ready, enable or algorithm-select ports.
Consume a new complex sample and adapt on EVERY rising edge outside reset.
Support odd TAP_NUM from 1 through 31, MU from 0 through 31, and the source's
Q2.13 format with DATA_WIDTH=COEFF_WIDTH=16. The width parameters document that
format; other widths are outside this task. MU denotes the shift exponent,
so the real learning rate is 2**(-MU), not MU itself.

Let clip(v)=min(32767,max(-32768,v)). At an edge, first form the new histories
Xr=[data_in_real, old Xr[0],...,old Xr[TAP_NUM-2]], and likewise Xi. Use the
OLD weights Wr,Wi to compute each contribution, rounding with arithmetic shifts:

    yr = clip(sum((Wr[k]*Xr[k] - Wi[k]*Xi[k]) >> 13))
    yi = clip(sum((Wr[k]*Xi[k] + Wi[k]*Xr[k]) >> 13))
    er = clip(desired_real - yr)
    ei = clip(desired_imag - yi)
    Wr_next[k] = clip(Wr[k] + ((er*Xr[k] + ei*Xi[k]) >> (13+MU)))
    Wi_next[k] = clip(Wi[k] + ((ei*Xr[k] - er*Xi[k]) >> (13+MU)))

All sums run over k=0..TAP_NUM-1. Products and sums are exact signed integers
before their specified shifts/clips; no premature 32-bit overflow. Each shift
rounds toward negative infinity. Update the histories, all weights and the
registered output (yr,yi) together. Thus the new output is visible just after
that edge; the next edge uses the updated weights. Between edges outputs hold.
Zero-valued input cycles are still samples, not idle/no-op cycles.

Source completions: the original leaves saturation, intermediate widths and
pipeline scheduling unspecified. This task fixes per-tap truncation, saturation
and the atomic sample/update recurrence above. Complex LMS uses error*conj(X)
because the forward filter uses W*X. Only LMS is required by the original prompt;
the background notes about CMA/MCMA/RDE do not add hidden modes. With adaptation
frozen, the default center tap is a three-sample delay. The old test's unrelated
five-cycle expectation and its overwritten failure list are not a sound oracle.
No claim of equivalence to an unavailable original answer implementation is made.


## Reset Behavior

`rst_n` is asynchronous, active low. Assertion immediately restores the initial state. Both histories and outputs are zero. Wr[TAP_NUM//2]=8192 (1.0), all other real and imaginary weights are zero.
After reset release, resume the edge-by-edge behavior above.
