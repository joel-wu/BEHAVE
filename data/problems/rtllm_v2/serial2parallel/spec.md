# Serial To Parallel Converter

Collect eight enabled serial bits into one byte, most-significant bit first. The native interface uses `din_valid` and `dout_valid`, without ready or backpressure.

## Interface

### RTL interface

```systemverilog
module serial2parallel (
    input logic clk,
    input logic rst_n,
    input logic din_serial,
    input logic din_valid,
    output logic [7:0] dout_parallel,
    output logic dout_valid
);
```

### Behavior interface

```python
class SerialToParallel:
    def __init__(self): ...
    def reset(self): ...
    def process(self, din_serial): ...
```

Call `process` only for enabled input samples. The first seven calls in each group return `None`; the eighth returns `{"dout_parallel": byte}`. Physical output latency does not require additional Behavior calls.

Return None for the first seven enabled samples of each eight-sample group, then one
dictionary for the eighth. Never return a list. Reset discards a partial group.
Every returned dictionary contains exactly these fields (bit widths):

- `dout_parallel`: 8.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

Call process only when din_valid is asserted at a rising edge. Disabled edges make no
call.

## Modules

- `serial2parallel`: serial to parallel converter.

## Functional Behavior

Each rising edge with `din_valid = 1` contributes one unsigned bit. Invalid cycles contribute nothing and preserve the partial word. Each non-overlapping group of eight bits produces exactly one byte; fewer than eight pending bits produce none. For inputs `b0,...,b7`, the result is `sum(bj << (7-j))`.

If the eighth bit is captured at edge k, the completed byte and a one-clock `dout_valid` pulse become visible after edge k+1 and are sampled at edge k+2. This retains the original output register stage. Edge k+1 may simultaneously accept the first bit of the next byte: no ninth bit is dropped. `dout_parallel` retains the last emitted byte between output pulses. Continuous streams and arbitrary input gaps are both supported.

## Reset Behavior

`rst_n` is asynchronous and active low. Reset clears the partial byte, bit count, pending completed byte, and both outputs. It cancels a completed byte that has not yet appeared at the output stage.
