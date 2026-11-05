# multiplexer

A native parameterized multiplexer with optional output registers.

## Interface

### RTL interface

```systemverilog
module multiplexer #(
    parameter int DATA_WIDTH = 8, parameter int NUM_INPUTS = 4,
    parameter int REGISTER_OUTPUT = 0, parameter int HAS_DEFAULT = 0,
    parameter logic [DATA_WIDTH-1:0] DEFAULT_VALUE = 0
) (
    input logic clk, rst_n,
    input logic [DATA_WIDTH*NUM_INPUTS-1:0] inp,
    input logic [((NUM_INPUTS>1) ? $clog2(NUM_INPUTS) : 1)-1:0] sel,
    input logic bypass,
    output logic [DATA_WIDTH-1:0] out
);
```

### Behavior interface

```python
class Multiplexer:
    def __init__(self, DATA_WIDTH=8, NUM_INPUTS=4, REGISTER_OUTPUT=0, HAS_DEFAULT=0, DEFAULT_VALUE=0): ...
    def reset(self): ...
    def process(self, inp, sel, bypass): ...
```

Constructor arguments match the RTL parameters. Call `process` with all three
arguments for each released rising edge; return exactly `{"out": value}`,
where `value` is an unsigned `DATA_WIDTH`-bit integer. Arguments are unsigned
packed port values, not lists of words. With registered output, return the
preceding captured value and then advance state; otherwise return the current
combinational selection. The return is never `None` or a list. Reuse the
instance across edges. `reset()` clears modeled registers, returns `None`,
and emits no record; it does not change the combinational function.

### Clock-edge observations

Observe settled output pins immediately before each released rising edge of
`clk`, then advance that edge. Inputs and outputs are unsigned port encodings.
Idle cycles are edges too. Reset is controlled separately and emits no records.

## Modules

- `multiplexer`: the complete native module above.

## Functional Behavior

`DATA_WIDTH>=1`, `NUM_INPUTS>=1`; REGISTER_OUTPUT and HAS_DEFAULT are 0 or 1.
Defaults are 8,4,0,0; DEFAULT_VALUE defaults to zero and is a DATA_WIDTH-bit value.
The selector has max(1,ceil(log2(NUM_INPUTS))) bits. The explicit one-bit minimum
completes the source's zero-width selector at NUM_INPUTS=1.

Word i is inp[i*DATA_WIDTH +: DATA_WIDTH]; word zero is least significant.
Split the packed bus into an array using a generate loop, as requested in the
source. bypass=1 always selects word zero, regardless of sel. Otherwise select
word sel if sel<NUM_INPUTS. An invalid index returns DEFAULT_VALUE when
HAS_DEFAULT=1, or zero when HAS_DEFAULT=0. That last zero is an explicit
completion of the source's undefined invalid-index case, not a new valid input.

REGISTER_OUTPUT=0: out is purely combinational and follows the current inp,
sel and bypass. clk and rst_n do not affect it. REGISTER_OUTPUT=1: each rising
edge captures the selected word; out changes after the edge and holds until
the next edge or reset. Bypass is captured in exactly the same way as selection;
it does not bypass the output register. There is no ready/valid or extra delay.
Pre-edge observations therefore see the preceding captured result only in the
registered configuration.

## Reset Behavior

With REGISTER_OUTPUT=1, rst_n is asynchronous active-low: assertion immediately
clears all output registers to zero, regardless of input, bypass or enable.
After release, the first rising edge captures the current computation.
With REGISTER_OUTPUT=0, no state exists: rst_n and clk are unused and do not
gate the combinational outputs.

This resolves conflicting source wording in favor of its detailed asynchronous
active-low reset specification, rather than the brief synchronous summary.
