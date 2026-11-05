# Complex Multiplier

Multiply two signed 16-bit complex numbers on every clock edge. The native interface has no ready/valid handshake or backpressure.

## Interface

### RTL interface

```systemverilog
module complex_multiplier (
    input logic clk,
    input logic arst_n,
    input logic [15:0] a_real,
    input logic [15:0] a_imag,
    input logic [15:0] b_real,
    input logic [15:0] b_imag,
    output logic [31:0] result_real,
    output logic [31:0] result_imag
);
```

### Behavior interface

```python
class ComplexMultiplier:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a_real, a_imag, b_real, b_imag): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `result_imag`, `result_real`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `complex_multiplier`: the native clocked datapath and its state.

## Functional Behavior

Interpret each input as a signed two's-complement 16-bit integer. The next `result_real` is `a_real*b_real - a_imag*b_imag`; the next `result_imag` is `a_real*b_imag + a_imag*b_real`. Retain the low 32 bits of each result. This explicitly completes the source's overflow rule (for example, the imaginary sum can equal 2^31). There is no saturation or additional pipeline stage.

## Reset Behavior

`arst_n` is asynchronous and active low. Assertion clears all state
and output registers to zero, and they remain zero while reset is active.
After release, each rising edge of `clk` advances normally. Reset samples do
not produce logical observation transactions. There is no input enable beyond
the native data/control pins listed above.
