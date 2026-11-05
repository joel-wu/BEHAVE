# Bayer Digital Gain

This block applies a programmable scalar digital gain to a single Bayer-format pixel sample and, when automatic mode is enabled, updates the gain-table index by one step according to auto-exposure feedback before applying the gain. The computation is pixel-wise: the selected gain multiplies the input sample, and the result is clipped to the sensor output range defined by the bit depth.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_digital_gain (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_pixel,
    input  logic        in_is_auto,
    input  logic signed [1:0] in_ae_feedback,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_pixel,
    output logic [2:0]  out_current_gain
);
```

Port meanings:
- `in_pixel` is an unsigned 16-bit sample; every code is processed and clipped to the fixed 10-bit sensor range.
- `in_is_auto` selects whether the gain index is updated from AE feedback for this transaction.
- `in_ae_feedback` is a signed control code. Negative values request one-step gain increase, positive values request one-step gain decrease, and zero leaves the gain index unchanged.
- `out_pixel` is the gained and clipped unsigned Bayer pixel sample.
- `out_current_gain` is the gain-table index used for the multiplication of this transaction and the architectural gain index after this transaction completes.

The block parameters are fixed at implementation time:
- `BIT_DEPTH = 10`
- `GAIN_COUNT = 5`
- `GAIN_TABLE = [1, 2, 4, 8, 16]`
- `CURRENT_GAIN_RESET = 0`

### Behavior interface

```python
class BayerDigitalGain:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_pixel, in_is_auto, in_ae_feedback):
        pass
```

`process(...)` returns:
- `{"out_pixel": ..., "out_current_gain": ...}`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_current_gain`: 3.
- `out_pixel`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_bayer_digital_gain`: top-level digital-gain block with one architectural state register holding the current gain-table index.

## Functional Behavior

The block contains one architectural state variable, `current_gain`, encoded as an unsigned integer index in the range `[0, GAIN_COUNT - 1]`. Its reset value is `CURRENT_GAIN_RESET`.

Inputs and outputs transfer on rising edges when their respective valid and
ready signals are both high. Each input produces one ordered output; output
valid and both fields remain stable under backpressure. The gain state changes
only when an input is accepted, never merely because it is offered or stalled.

For each accepted input transaction, the block performs the following steps in order:

1. Read the old `current_gain` state.

2. Compute the gain index for this transaction, `selected_gain_index`:
   - If `in_is_auto == 0`, then `selected_gain_index = current_gain`.
   - If `in_is_auto == 1` and `in_ae_feedback < 0`, then
     `selected_gain_index = min(current_gain + 1, GAIN_COUNT - 1)`.
   - If `in_is_auto == 1` and `in_ae_feedback > 0`, then
     `selected_gain_index = max(current_gain - 1, 0)`.
   - If `in_is_auto == 1` and `in_ae_feedback == 0`, then
     `selected_gain_index = current_gain`.

`in_ae_feedback` is interpreted only by sign. Therefore:
- `-2` and `-1` both mean “negative”
- `0` means “no correction”
- `+1` means “positive”
The 2-bit signed code `2'b10` represents `-2`.

3. Update the architectural state:
   - `current_gain` becomes `selected_gain_index`.

4. Look up the gain multiplier:
   - `gain_value = GAIN_TABLE[selected_gain_index]`

5. Multiply the input sample by the selected gain:
   - `product = in_pixel * gain_value`
   - `in_pixel` is treated as an unsigned integer.
   - `gain_value` is a positive integer from the gain table.
   - The multiplication is exact before clipping.

6. Clip the multiplied value to the sensor range:
   - `MAX_PIXEL = 2^BIT_DEPTH - 1 = 1023`
   - If `product < 0`, output `0`.
   - Else if `product > MAX_PIXEL`, output `MAX_PIXEL`.
   - Else output `product`.

Because `in_pixel` is unsigned, the `product < 0` case is unreachable for legal inputs, but the output contract is still defined as above.

7. Emit one output transaction:
   - `out_pixel` is the clipped result, represented as an unsigned 16-bit value.
   - `out_current_gain` is `selected_gain_index`, represented as an unsigned 3-bit value.

Examples:
- With `current_gain = 2`, `in_is_auto = 0`, `in_pixel = 100`, the selected gain is index 2, gain value 4, and `out_pixel = 400`, `out_current_gain = 2`.
- With `current_gain = 2`, `in_is_auto = 1`, `in_ae_feedback = -1`, `in_pixel = 100`, the selected gain index becomes 3, gain value 8, and `out_pixel = 800`, `out_current_gain = 3`.
- With `current_gain = 4`, `in_is_auto = 1`, `in_ae_feedback = -1`, `in_pixel = 100`, the selected gain index saturates at 4, gain value 16, and `out_pixel = 1023`, `out_current_gain = 4`.
- With `current_gain = 0`, `in_is_auto = 1`, `in_ae_feedback = +1`, `in_pixel = 100`, the selected gain index saturates at 0, gain value 1, and `out_pixel = 100`, `out_current_gain = 0`.

## Reset Behavior

Reset is active-high and asynchronous. It cancels pending output and clears
`out_valid`, `out_pixel`, and `out_current_gain` to zero. Architectural state is:
- `current_gain = 0`

After reset, the first accepted input uses `current_gain = 0` as its old state.
