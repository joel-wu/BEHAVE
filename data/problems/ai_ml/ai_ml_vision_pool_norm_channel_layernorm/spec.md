# Channel Layer Normalization

Normalize one spatial position across four channels, then apply independently supplied per-channel scale and bias. All input and output lanes use signed Q8.8 fixed point. The block retains the channel mean and population variance of layer normalization, with an explicit epsilon and quantization rule.

## Interface

### RTL interface

```systemverilog
module PoolNormChannelLayerNorm (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_x,
    input logic [63:0] in_weight,
    input logic [63:0] in_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_y
);
```

Each bus packs four signed 16-bit lanes. Channel `i` occupies bits `16*i +: 16`, with channel zero least significant. A raw lane `v` represents `v/256`; unit scale is the raw value 256. Every bus bit pattern is valid.

Transfers occur on rising clock edges when valid and ready are both high and reset is low. The producer holds the whole input bundle stable while stalled; the block holds `out_valid` and `out_y` stable while the output is stalled. Each accepted input produces exactly one output, in order, unless reset intervenes. Latency and pipeline structure are implementation choices.

### Behavior interface

```python
class PoolNormChannelLayerNorm:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_weight, in_bias): ...
```

The arguments are the nonnegative packed bit patterns of the RTL inputs. One call represents an accepted transaction and returns `{"out_y": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 64.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `PoolNormChannelLayerNorm`: four-channel normalization and affine transform with ready/valid transport.

## Functional Behavior

Let `x[i]`, `w[i]` and `b[i]` be the signed raw integers in the three input buses. Compute the exact rational mean and population variance in raw-value units:

```text
mean = (x[0] + x[1] + x[2] + x[3]) / 4
variance = sum((x[i] - mean)^2, i=0..3) / 4
n[i] = trunc0(256 * (x[i] - mean) / sqrt(variance + 1))
a[i] = trunc0(n[i] * w[i] / 256) + b[i]
y[i] = min(32767, max(-32768, a[i]))
```

Here `trunc0` truncates toward zero. The added raw variance of one corresponds to epsilon `2^-16` in the real Q8.8 input units. Do not round the mean, variance, or square root before computing `n[i]`. Only the two displayed truncations and final saturation quantize the result.

An equivalent all-integer definition is:

```text
d[i] = 4*x[i] - sum(x)
R = sum(d[i]^2, i=0..3) + 64
magnitude[i] = floor_sqrt(floor((512*d[i])^2 / R))
n[i] = -magnitude[i] if d[i] < 0 else magnitude[i]
```

`floor_sqrt(v)` is the greatest nonnegative integer whose square is at most `v`. These identities specify the same rounding; they do not prescribe a particular square-root circuit. Intermediates do not wrap. Pack `y[0..3]` as signed 16-bit lanes in `out_y`.

For `x=[-256,-256,256,256]`, `w=[256,256,256,256]` and zero bias, the output is `[-255,-255,255,255]`. Equal input channels give zero normalized values and therefore return the supplied biases. Scale and bias are applied separately to every channel; no parameters or samples are retained between transactions.

## Reset Behavior

Reset is active-high and asynchronous. Its assertion discards pending output and sets `out_valid=0` and `out_y=0`; `in_ready` is then one. No transfer is counted during reset. There is no mathematical state beyond the output holding registers.
