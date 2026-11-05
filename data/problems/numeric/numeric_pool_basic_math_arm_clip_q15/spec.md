# Q15ClipElement

Q15ClipElement clips one signed 16-bit fixed-point sample to an inclusive low/high range. The module implements the scalar elementwise operation used in Q15 vector clipping: compare the input sample against a signed lower bound and signed upper bound, output the upper bound when the sample is above range, output the lower bound when the sample is below range, otherwise pass the sample through unchanged.

## Interface

### RTL interface

```systemverilog
module q15_clip_element (
    input  logic               clk,
    input  logic               rst,

    input  logic               in_valid,
    output logic               in_ready,
    input  logic [15:0]        in_sample,
    input  logic [15:0]        in_low,
    input  logic [15:0]        in_high,

    output logic               out_valid,
    input  logic               out_ready,
    output logic [15:0]        out_sample
);
```

### Behavior interface

```python
class Q15ClipElement:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_sample, in_low, in_high):
        pass
```

Each call represents one accepted request and returns exactly one dictionary
with key `out_sample`. All three arguments and the result are Python integers
in [0, 65535], encoding signed 16-bit port words. Return this dictionary also
for zero or reversed bounds, not `None` or a list. Clipping has no dependence
on earlier requests; `reset()` restores the initial model state and returns
no response.

## Modules

- `q15_clip_element`: top-level scalar Q15 clipping kernel. Internal latency and buffering are implementation choices; outputs remain ordered and stable while stalled.

## Functional Behavior

All three input data ports carry 16-bit two's-complement signed integers. `in_sample` is the sample to clip. `in_low` is the lower bound. `in_high` is the upper bound.

For each accepted input transaction, interpret:
- `sample = signed(in_sample[15:0])`
- `low = signed(in_low[15:0])`
- `high = signed(in_high[15:0])`

The output is computed by this ordered comparison sequence:
1. If `sample > high`, `result = high`.
2. Else if `sample < low`, `result = low`.
3. Else `result = sample`.

The comparisons are signed comparisons on 16-bit two's-complement values.

The output transaction corresponding to that accepted input carries:
- `out_sample = result` encoded as a 16-bit two's-complement bit pattern.

When `low <= high`, the clipping interval is inclusive. Therefore:
- `sample == high` produces `out_sample = sample`.
- `sample == low` produces `out_sample = sample`.

No arithmetic saturation, rounding, scaling, or overflow processing occurs beyond selecting one of the three 16-bit signed values `high`, `low`, or `sample`.

If `low > high`, the module still applies the same ordered comparison sequence above without modification. In that case:
- any `sample > high` returns `high`;
- otherwise any `sample < low` returns `low`;
- otherwise `sample` is returned.
This behavior is defined by the comparison order and is not treated as an error.

The module is a 1:1 transaction mapping: every accepted input transaction produces exactly one output transaction.

RTL shall use synchronous active-high reset.

## Reset Behavior

On any cycle with `rst == 1`, the module returns to its idle state.

Architectural state after reset:
- no buffered output transaction is pending.

Output and handshake state after reset:
- `out_valid = 0`
- `out_sample = 16'h0000`

Input handshake state after reset:
- `in_ready = 1`

After reset deasserts, the first accepted input transaction is processed according to the Functional Behavior section with no dependence on any pre-reset input history.
