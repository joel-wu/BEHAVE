# continuous_adder

A clock-driven continuous accumulator with threshold qualification and optional output registers.

## Interface

### RTL interface

```systemverilog
module continuous_adder #(
    parameter int DATA_WIDTH = 32, parameter int ENABLE_THRESHOLD = 0,
    parameter int THRESHOLD = 16, parameter int REGISTER_OUTPUT = 0
) (
    input logic clk, rst_n, valid_in,
    input logic [DATA_WIDTH-1:0] data_in,
    input logic accumulate_enable, flush,
    output logic [DATA_WIDTH-1:0] sum_out,
    output logic sum_valid
);
```

### Behavior interface

```python
class ContinuousAdder:
    def __init__(self, DATA_WIDTH=32, ENABLE_THRESHOLD=0, THRESHOLD=16, REGISTER_OUTPUT=0): ...
    def reset(self): ...
    def process(self, valid_in, data_in, accumulate_enable, flush): ...
```

Constructor arguments match the RTL parameters. Every `process` call supplies
all four arguments, including idle cycles: control flags are 0 or 1 and
`data_in` is an unsigned `DATA_WIDTH`-bit integer. It returns exactly
`{"sum_out": value, "sum_valid": flag}`, with an unsigned `DATA_WIDTH`-bit
value and a 0/1 flag sampled immediately BEFORE one rising edge of `clk`,
then advances that edge. Unqualified or idle cycles also return this
dictionary, never `None` or a list. Accumulator and optional output registers
persist across calls. Construction and `reset()` clear their stored state;
`reset()` returns `None` and does not advance a clock edge. Physical reset
is applied separately; combinational qualification after release follows
the rules below.

## Modules

- `continuous_adder`: the complete native module above.

## Functional Behavior

DATA_WIDTH>=1; ENABLE_THRESHOLD and REGISTER_OUTPUT are 0 or 1;
THRESHOLD is a nonnegative integer. Defaults are 32,0,16,0. Maintain an unsigned
DATA_WIDTH-bit sum. At EVERY rising edge, flush clears it, independently of
valid_in or accumulate_enable. Otherwise add data_in modulo 2**DATA_WIDTH only
when valid_in and accumulate_enable are both one; hold it otherwise. Repeated
valid levels accumulate on repeated edges. Reaching a threshold does not clear
the sum. There is no ready, output acknowledgment or backpressure.

The output qualification predicate is true when thresholding is disabled, or
the CURRENT stored sum is at least THRESHOLD. Compare the entire integer
threshold, without silently truncating it: a threshold above the sum range is
unreachable. As an explicit completion of the source's inactive outputs, the
qualified pair is (sum,1) when qualified and (0,0) otherwise.

With REGISTER_OUTPUT=0, outputs show that pair combinationally, except reset
forces (0,0). They update after each accumulation edge without a second register.
With REGISTER_OUTPUT=1, capture the pair computed from the PRE-EDGE stored sum
into an additional output register. This adds one real clock of latency, as in
the source testbench's delayed expected sum. Flush clears the accumulator at
its edge; the output register still captures the preceding sum at that edge.
On the next edge it captures the qualified zero. No input-valid gate stalls
this output register. This explicitly resolves the source's incomplete output
hold/latency and threshold-disabled wording.

## Reset Behavior

`rst_n` is asynchronous, active low. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.

In combinational mode, reset assertion forces sum_out=0 and sum_valid=0;
after release, qualification of zero applies immediately, even before an edge.
