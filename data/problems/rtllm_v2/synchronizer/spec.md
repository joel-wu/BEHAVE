# synchronizer

Implement a native dual-clock multi-bit synchronizer.

## Interface

### RTL interface

```systemverilog
module synchronizer(
    input logic clk_a_level, clk_b_level, arstn, brstn,
    input logic [3:0] data_in, input logic data_en,
    output logic [3:0] dataout
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, clk_a_level, clk_b_level, arstn, brstn, data_in, data_en): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `dataout`: 4.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Input-level observations

Inputs are applied as explicit physical levels, then all settled output pins
are observed. Repeated identical inputs create separate observations but no
new signal transition. No automatic periodic clock is inserted between
observations. All input-level sequences are defined.

## Modules

- `synchronizer`: the stateful digital circuit below.

## Functional Behavior

Transfer a slowly changing 4-bit word from clock A to clock B
using the source's MUX-based, level-qualified synchronizer. Every rising A edge
captures data_in and data_en, regardless of the enable value. The captured
enable passes through two B-domain stages, advancing on EVERY rising B edge.
The B-domain holding output captures the source-domain data on a rising B edge
when the second enable stage was high before that edge; otherwise it holds.
There is an observable output value at every sample, not only during transfer.

For example, after A captures word 10 with enable high, three subsequent B
rising edges with no intervening A change produce dataout values 0, 0, 10.
An A edge coincident with the first B edge is not an earlier source capture.
Holding enable high allows repeated captures; it is not a one-shot request.

For reliable physical multi-bit transfer, the source requires data to remain
stable while enable is high, enable high for at least three B cycles, and
adjacent data changes separated by at least ten B cycles. Preserve that use
case. Outside those timing assumptions, only the digital sampled behavior
above is specified, not successful asynchronous physical transfer.

arstn clears the A-domain data and enable asynchronously. brstn clears the two
B-domain enable stages and dataout asynchronously; it does not clear A state.
All five registers initially equal zero. The original enable register is
sensitive to arstn but erroneously checks brstn; this task follows the source
description and corrects that condition to arstn. Reset polarity and clock
progress are restored from the source, not the old handshake surrogate.

### Digital observation boundary

Clock pins are renamed with a _level suffix and supplied as physical levels.
The RTL still uses independent real rising-edge blocks. One input record
applies all pin levels together and observes all output pins after settling;
repeated levels do not create edges. Supply every transition of interest.
Coincident clock edges read the common pre-event state, including when another
domain resets on that observation. Each domain's own asserted reset dominates
its edge. There are no automatically inserted clocks, ready/valid wrappers or
output queues. Falling edges alone do not advance these circuits.

Both observed clock levels initially equal zero. The initial state is the
stated zero initialization, completing the source's unspecified startup. All
digital records have defined results. This does not assert analog CDC safety,
metastability resolution or physical setup/hold compliance for arbitrary inputs.


## Reset Behavior

Each active-low reset asynchronously clears only its own domain's control registers. Initial state is explicitly specified below. Physical resets are input pins, not implicit resets between samples.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
