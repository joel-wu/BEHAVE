# Four-Stage 8-Bit Multiplier

Compute the unsigned product of two 8-bit operands using a four-stage pipeline: input registers, paired partial products, accumulated product, and output register. The native interface uses enables, not ready/valid backpressure.

## Interface

### RTL interface

```systemverilog
module multi_pipe_8bit (
    input logic clk,
    input logic rst_n,
    input logic mul_en_in,
    input logic [7:0] mul_a,
    input logic [7:0] mul_b,
    output logic mul_en_out,
    output logic [15:0] mul_out
);
```

### Behavior interface

```python
class Pipelined8BitMultiplier:
    def __init__(self): ...
    def reset(self): ...
    def process(self, mul_a, mul_b): ...
```

Call `process` once per enabled input pair. It returns `{"mul_out": product}` for that pair, including the first and final requests. It does not model pipeline delay by withholding results until later input calls; physical clock latency is checked separately.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `mul_out`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `multi_pipe_8bit`: four-stage 8-bit multiplier.

## Functional Behavior

An input pair is accepted at every rising edge with `mul_en_in = 1`. Each pair produces one ordered output with `mul_out = mul_a * mul_b`, represented exactly as 16 unsigned bits. The maximum is 65025.

For a pair captured at edge k, `mul_en_out = 1` and its product become visible immediately after edge k+3 and are sampled at edge k+4. Every clock advances the pipeline, including disabled input cycles. A single isolated request completes without additional requests; arbitrary input gaps and back-to-back requests are legal. `mul_out` is zero whenever `mul_en_out = 0`. There is no output ready signal, so the receiver must observe every enabled output. This task fixes the original 8-bit configuration.

## Reset Behavior

`rst_n` is asynchronous and active low. Reset clears both operand registers, all partial results and enable bits, and both outputs. It cancels in-flight requests. No records are compared while reset is asserted.
