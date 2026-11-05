# low_pass_filter

A configurable signed frame convolution with registered data and coefficients.

## Interface

### RTL interface

```systemverilog
module low_pass_filter #(
    parameter int DATA_WIDTH = 16,
    parameter int COEFF_WIDTH = 16,
    parameter int NUM_TAPS = 8
) (
    input logic clk, input logic reset,
    input logic valid_in,
    input logic [DATA_WIDTH*NUM_TAPS-1:0] data_in,
    input logic [COEFF_WIDTH*NUM_TAPS-1:0] coeffs,
    output logic valid_out,
    output logic [DATA_WIDTH+COEFF_WIDTH+$clog2(NUM_TAPS)-1:0] data_out
);
```

### Behavior interface

```python
class LowPassFilter:
    def __init__(self, DATA_WIDTH=16, COEFF_WIDTH=16, NUM_TAPS=8): ...
    def reset(self): ...
    def process(self, valid_in, data_in, coeffs): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: DATA_WIDTH+COEFF_WIDTH+$clog2(NUM_TAPS).
- `valid_out`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `low_pass_filter`: the complete native module with the ports and parameters above.

## Functional Behavior

`DATA_WIDTH >= 2`, `COEFF_WIDTH >= 2`, `NUM_TAPS >= 2`.
`NBW_MULT = DATA_WIDTH + COEFF_WIDTH` is an internal derived constant, not an
independent configurable parameter. Number packed lanes from the least-significant
end. Interpret each data/coefficient lane as a signed two's-complement integer.
The result is `sum(data[k] * coeff[NUM_TAPS-1-k])` over every tap: coefficient
order is reversed. The output width adds `ceil(log2(NUM_TAPS))` to NBW_MULT and
accommodates the full signed sum without overflow.

On each rising edge, capture data and coefficients only when `valid_in=1`;
otherwise retain them. Always register `valid_in` as `valid_out`. The output
data is the combinational convolution of the registered frame, with no output
register. Thus new data and its valid bit appear after that capture edge and
are sampled at the following edge. Invalid inputs clear valid but retain data.
Each input is a complete frame; no history of earlier frames is used in the sum.

## Reset Behavior

`reset` is synchronous and active high. A rising edge with reset asserted clears the frame, coefficient and valid registers; the combinational data output becomes zero.
Reset discards incomplete work. The next released edge resumes from zero.
