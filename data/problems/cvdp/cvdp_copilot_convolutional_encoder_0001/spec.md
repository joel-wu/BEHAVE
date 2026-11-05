# Convolutional Encoder

Encode a continuous binary stream using the rate-1/2, K=3 polynomials 111 and 101. The native interface has no ready/valid handshake or backpressure.

## Interface

### RTL interface

```systemverilog
module convolutional_encoder (
    input logic clk,
    input logic rst,
    input logic data_in,
    output logic encoded_bit1,
    output logic encoded_bit2
);
```

### Behavior interface

```python
class ConvolutionalEncoder:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `encoded_bit1`, `encoded_bit2`: 1.

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

- `convolutional_encoder`: the native clocked datapath and its state.

## Functional Behavior

Let `h0` and `h1` be the previous and second-previous input bits. At each edge set `encoded_bit1 = data_in ^ h0 ^ h1` and `encoded_bit2 = data_in ^ h1`, then update `(h1,h0)` to `(h0,data_in)`. History starts at zero. Every edge consumes a bit; no framing, puncturing or automatic tail bits are inserted.

## Reset Behavior

`rst` is asynchronous and active high. Assertion clears all state
and output registers to zero, and they remain zero while reset is active.
After release, each rising edge of `clk` advances normally. Reset samples do
not produce logical observation transactions. There is no input enable beyond
the native data/control pins listed above.
