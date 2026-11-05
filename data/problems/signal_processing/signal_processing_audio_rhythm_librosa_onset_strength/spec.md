# Onset Strength Multi

This block computes a streaming spectral-flux onset-strength envelope from a sequence of spectrogram frames. Each accepted input transaction provides one time frame of a pre-computed spectrogram, one signed value per frequency bin. For each frame, the block forms a lagged difference against a stored reference frame, clamps negative differences to zero, averages the positive differences across all bins, and outputs one onset-strength value. The implementation contract is a single full-band channel, arithmetic on signed integers, no local max filtering beyond the identity reference, no detrending, and no centering delay in the transaction stream.

## Interface

### RTL interface

```systemverilog
module onset_strength_multi (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [127:0] in_S_flat,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_onset_env
);
```

Port interpretation:

- `in_S_flat` packs 8 signed 16-bit frequency-bin samples for the current frame.
- Bin `f` occupies bits `16*f +: 16`, with `f=0` in bits `[15:0]` and `f=7` in bits `[127:112]`.
- `out_onset_env` is one unsigned 16-bit onset-strength sample for the accepted input frame.

Fixed configuration required by this specification:

- Number of frequency bins: 8.
- Number of output channels: 1.
- Input bin width: 16 bits signed two's-complement.
- Output width: 16 bits unsigned.
- Lag: 1 frame.
- Reference spectrum: previous input frame exactly; no frequency-axis max filter.
- Aggregation: arithmetic mean over all 8 bins.
- Detrend: disabled.
- Center compensation padding: disabled.

### Behavior interface

```python
class OnsetStrengthMulti:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_S_flat):
        pass
```

Each call represents one accepted frame and returns exactly
`{"out_onset_env": value}`. `in_S_flat` is an unsigned 128-bit integer packing
eight signed 16-bit samples as specified below; `value` is an unsigned 16-bit
integer. Every frame produces the complete dictionary, including the zero
first result, never `None` or a list. No handshake signals are returned. The
previous frame and its presence flag persist between calls. Construction and
`reset()` clear both as specified below; `reset()` returns `None` and produces
no frame result.

## Modules

- `onset_strength_multi`: top-level streaming onset-strength processor.

## Functional Behavior

One accepted input transaction consumes one spectrogram frame and produces exactly one output transaction.

### Input unpacking

Interpret `in_S_flat` as 8 signed 16-bit integers:

- `S[0] = signed(in_S_flat[15:0])`
- `S[1] = signed(in_S_flat[31:16])`
- ...
- `S[7] = signed(in_S_flat[127:112])`

Each `S[f]` is in the integer range `[-32768, 32767]`.

### Architectural state

The block stores one previous frame `ref[0..7]`, each a signed 16-bit integer,
and a flag recording whether a previous frame exists. `ref[f]` is the lag-1
reference value for bin `f`.

### Per-frame computation

For the accepted input frame, compute for each frequency bin `f` from 0 to 7:

1. Signed difference:
   - `diff[f] = S[f] - ref[f]`
2. Half-wave rectification:
   - if `diff[f] > 0`, then `pos[f] = diff[f]`
   - else `pos[f] = 0`

`diff[f]` is mathematical integer subtraction with exact value range `[-65535, 65535]`; it is not truncated before the comparison against zero.

Form the full-band sum:

- `sum_pos = pos[0] + pos[1] + pos[2] + pos[3] + pos[4] + pos[5] + pos[6] + pos[7]`

Then form the onset-strength output as the arithmetic mean over all 8 bins:

- `onset_env = floor(sum_pos / 8)`

Division is unsigned integer division with truncation toward zero. Since `sum_pos` is non-negative, this is equivalent to truncation toward negative infinity.

The returned output transaction is:

- `out_onset_env = onset_env mod 2^16`

Under the stated input range, `sum_pos <= 8 * 65535 = 524280`, so `onset_env <= 65535` and the modulo operation does not change the value.

### State update order

The output for a frame uses the stored `ref[0..7]` from before the current input is accepted. After computing the output for that frame, update the stored reference:

- `ref[f] := S[f]` for all `f` from 0 to 7.

This read-before-write ordering is mandatory.

### First-frame behavior

The first accepted frame after reset produces zero and becomes the reference.
There is no preceding frame to compare with. This is the leading zero required
by the lag-1 envelope; it is independent of the first frame's sign or magnitude.

Worked example:

- Previous reference after reset: all zeros.
- Input bins: `[4, -3, 8, 0, 1, -1, 2, 6]`
- `out_onset_env = 0`; the eight input bins become the next reference.

Second worked example with non-zero reference:

- Stored reference: `[10, 5, -2, 7, 0, 1, 3, 9]`
- Current input: `[13, 1, 4, 7, -5, 8, 2, 20]`
- Differences: `[3, -4, 6, 0, -5, 7, -1, 11]`
- Positive differences: `[3, 0, 6, 0, 0, 7, 0, 11]`
- `sum_pos = 27`
- `out_onset_env = floor(27 / 8) = 3`

## Reset Behavior

Reset is synchronous active-high.

On a rising clock edge with `rst=1`, the architectural state is set to:

- `ref[0]` through `ref[7]` = `16'sd0`
- No preceding frame exists.

Output interface state after reset:

- `out_valid = 0`
- `out_onset_env = 16'h0000`

Transfers require valid and ready. Pending output data and valid remain stable
under backpressure. Only accepted frames advance the reference; reset cancels
any pending output and restarts the envelope with a zero first result.
