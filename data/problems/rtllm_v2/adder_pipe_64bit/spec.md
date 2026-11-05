# Four-Stage 64-Bit Adder

Compute unsigned 64-bit addition through four clocked carry-propagation stages. The native enable-only interface accepts one operand pair per enabled edge, without backpressure.

## Interface

### RTL interface

```systemverilog
module adder_pipe_64bit
#(
    parameter DATA_WIDTH = 64,
    parameter STG_WIDTH = 16
)
(
    input clk,
    input rst_n,
    input i_en,
    input [DATA_WIDTH-1:0] adda,
    input [DATA_WIDTH-1:0] addb,
    output [DATA_WIDTH:0] result,
    output reg o_en
);
```

### Behavior interface

```python
class Pipelined64BitAdder:
    def __init__(self): ...
    def reset(self): ...
    def process(self, adda, addb): ...
```

Call `process` only for enabled input samples. Each call returns exactly
`{"result": adda + addb}` using unsigned 64-bit operands and an unsigned
65-bit result, including the carry. Zero sums also return this dictionary,
never `None`, a scalar or a list. `o_en` is not returned. The model has no
transaction-delay queue: clock latency and enable pulses belong to the RTL
interface, while the ordered logical output sequence contains one sum per
accepted pair. Construction and `reset()` establish empty model state;
`reset()` returns `None` and produces no logical response.

## Modules

- `adder_pipe_64bit`: four-stage 64-bit adder.

## Functional Behavior

At every rising edge with `i_en = 1`, accept `adda` and `addb`. The result is their full unsigned 65-bit sum, with no saturation or truncation. The four stages propagate carry across the 16-bit chunks from least to most significant, while aligning the already computed chunks.

For an input captured at edge k, the corresponding `result` and `o_en = 1` become visible immediately after edge k+3 and are sampled at edge k+4. Subsequent clocks advance the pipeline even when `i_en = 0`. Consecutive enabled inputs produce consecutive ordered outputs; bubbles produce corresponding gaps in `o_en`. No further request is needed to complete an outstanding addition. `result` is only meaningful while `o_en = 1`, except that reset forces it to zero. This task fixes `DATA_WIDTH = 64` and `STG_WIDTH = 16`; other parameter configurations are outside its scope.

## Reset Behavior

`rst_n` is asynchronous and active low. Reset clears the pipeline, `o_en`, and `result`, and cancels all in-flight additions. No pre-reset result is owed after release.
