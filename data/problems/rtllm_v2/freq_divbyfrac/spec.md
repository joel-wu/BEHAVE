# freq_divbyfrac

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module freq_divbyfrac #(parameter int MUL2_DIV_CLK = 7)(
    input logic clock_level, rst_n, output wire clk_div
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self, MUL2_DIV_CLK=7): ...
    def reset(self): ...
    def process(self, clock_level, rst_n): ...
```

Constructor `MUL2_DIV_CLK` matches the RTL parameter. Each call applies both
one-bit integer inputs together and returns exactly `{"clk_div": value}`,
with `value` integer 0 or 1 after settling. It is never `None` or a list,
including reset and repeated-level observations. Reuse the instance across
records. The explicit `rst_n` argument controls mid-run reset; `reset()`
initializes all model registers and the previous observed clock level to zero,
returns `None`, and emits no record.

### Input-level observations

Inputs and outputs are one-bit unsigned values. Each input record updates
persistent state according to the rules below, then observes all settled output
pins. Repeated identical records create separate observations but no new signal
transition. No automatic periodic clock is inserted between observations.
All input-level sequences are defined.

## Modules

- `freq_divbyfrac`: the actual latch or edge-triggered circuit below.

## Functional Behavior

MUL2_DIV_CLK is an odd integer at least 5, default 7. Let M be
this parameter. The intended division factor is M/2: default 3.5. One modulo-M
counter advances only at rising input edges. A positive-edge pulse register and
a negative-edge adjustment register are OR-ed into clk_div. Reset clears all
three registers and makes clk_div zero.

At a released rising edge, the positive register becomes one exactly when the
OLD count is 0 or M//2+1, then count increments modulo M. At a falling edge,
the adjustment register becomes one exactly when the CURRENT count is 1 or
M//2+1; count and the positive register do not change at this edge. The output
is the OR of these registers. A held level changes neither register.

These are the original two-edge equations, not the prior fabricated seven-token
1100110 pattern. For a regular clock in steady state, pulses repeat every M
half-periods; the high part lasts three half-periods and the low part M-3.
Thus this circuit is NOT a promise of 50% duty for every parameter. M=3 is
excluded because the two pulses merge into a permanently high output in this
construction. The finite register equations define startup as well as steady
state; do not substitute a duty-balanced waveform with different phase.

### Phase-level observation and startup

The source clk input is renamed clock_level. It is still the actual event
source of the RTL posedge/negedge blocks; it is supplied as explicit physical
levels instead of an automatically toggled Sim clock. Each input record applies
clock_level and rst_n together, then observes clk_div after digital settling.
Repeated high/low levels do NOT create extra edges. Supply every clock/reset
change of interest; no hidden periodic edges occur between records. There is
no handshake, enable, result queue or token counter replacing the divider.

rst_n remains asynchronous and active low. A low level resets all divider
registers regardless of clock transitions. Deasserting reset while the clock
stays high does not create a rising edge. If deassertion coincides with a real
edge, process that edge using the released reset and the old initialized state.
The initial observed clock level is zero; all stored values are explicitly
initialized to the reset state even if the first observation has rst_n=1.
That deterministic startup is a completion of the source's unspecified startup,
not a claim that the upstream implementation guarantees power-up values.

The original fixed counter width is replaced by ceil(log2(divisor)), retaining
its recurrence while making the declared parameters executable. No analog
metastability or pre-settle glitch behavior is specified. Changes in absolute
clock period scale the output waveform; they do not change these edge rules.


## Reset Behavior

The native rst_n is an ordinary observed control input: low asynchronously restores the stated divider state, without requiring a clock edge. Each fresh execution also starts in that state as an explicit startup completion.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
