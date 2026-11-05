# STFT Windowed Framer

This selects the framing/windowing stage of `spectrogram` with `center=False`, no padding, eight samples and a four-sample hop. The specified sine-window coefficients are fixed; this is not a complete STFT or a Hann window. `sample_count` below is a mathematical indexing device: an implementation can use bounded warm-up and hop phase, and must not restart warm-up on counter overflow.

This block accepts scalar audio samples and emits windowed frames at four-sample hops after an eight-sample warm-up: accepted samples 8, 12, 16, and so on produce frames. The block implements the framing and window-multiplication stage of an STFT front end: samples are stored in a sliding history buffer, frame start positions advance by `hop_length`, each emitted frame contains `win_length` consecutive samples, and each sample in the frame is multiplied by a stored window coefficient. FFT computation is not part of this block.

## Interface

### RTL interface

```systemverilog
module ai_ml_audio_stft_stft_windowed #(
    parameter int SAMPLE_W = 16,
    parameter int COEFF_W = 16,
    parameter int FRAME_LEN = 8,
    parameter int HOP_LEN = 4
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic signed [SAMPLE_W-1:0]   in_sample,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_0,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_1,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_2,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_3,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_4,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_5,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_6,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_7
);
```

`FRAME_LEN` and the number of `out_frame_*` ports are fixed together. The copy-pasteable port list above corresponds to the required instance for this block: `FRAME_LEN = 8`, `HOP_LEN = 4`, `SAMPLE_W = 16`, `COEFF_W = 16`.

The block contains an internal ROM of 8 signed window coefficients, one coefficient per frame position. The coefficients are architectural state and not externally programmable. Their values are, in signed 16-bit integer representation:

- `window[0] = 0`
- `window[1] = 12540`
- `window[2] = 23170`
- `window[3] = 30274`
- `window[4] = 32767`
- `window[5] = 30274`
- `window[6] = 23170`
- `window[7] = 12540`

### Behavior interface

```python
class STFTWindowed:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_sample):
        pass
```

`process()` returns either `None` when no output frame is emitted for the accepted input sample, or one dict with keys:
`out_frame_0`, `out_frame_1`, `out_frame_2`, `out_frame_3`, `out_frame_4`, `out_frame_5`, `out_frame_6`, `out_frame_7`.

Each call represents one accepted sample. The argument is an unsigned 16-bit
two's-complement encoding, and all eight returned fields are unsigned 32-bit
two's-complement encodings, despite the signed RTL port declarations. Never
return a list, partial dictionary, or negative Python field value. Reuse the
instance across samples. `reset()` clears history and frame scheduling, returns
`None`, and emits no frame. The constructor uses the required fixed configuration.

## Modules

- `ai_ml_audio_stft_stft_windowed`: top-level sliding-history STFT windowing block.

## Functional Behavior

All arithmetic is signed two's-complement integer arithmetic.

Input `in_sample` is a signed 16-bit sample. Each accepted sample is appended to an internal sample history. The history retains the most recent samples needed to form future frames.

The block emits frames according to these rules:

1. Let `sample_count` be the total number of accepted input samples since reset.
2. No output is produced until at least `FRAME_LEN` samples have been accepted.
3. After the `FRAME_LEN`-th accepted sample and on every `HOP_LEN`-th accepted sample thereafter, one output frame is produced.
4. Equivalently, an accepted sample with 1-based index `k` produces an output frame iff `k >= FRAME_LEN` and `(k - FRAME_LEN) mod HOP_LEN = 0`.

For each emitted frame, define the unwindowed frame samples as the latest `FRAME_LEN` samples in chronological order from oldest to newest:
- `frame_sample[0]` is the oldest sample in the emitted frame window.
- `frame_sample[FRAME_LEN-1]` is the newest accepted sample.

For the required configuration `FRAME_LEN = 8`, the emitted frame after accepted sample index `k` contains input samples:
- `frame_sample[0] = sample[k-7]`
- `frame_sample[1] = sample[k-6]`
- `frame_sample[2] = sample[k-5]`
- `frame_sample[3] = sample[k-4]`
- `frame_sample[4] = sample[k-3]`
- `frame_sample[5] = sample[k-2]`
- `frame_sample[6] = sample[k-1]`
- `frame_sample[7] = sample[k]`

Each output element is the exact signed product of one frame sample and the corresponding window coefficient, with no rounding, shifting, or saturation:
- `out_frame_i = frame_sample[i] * window[i]`

Because both operands are signed 16-bit values, each output is returned as the 32-bit two's-complement product. Overflow does not occur because the exact product of two signed 16-bit integers fits in signed 32 bits.

The outputs are mapped as:
- `out_frame_0 = frame_sample[0] * 0`
- `out_frame_1 = frame_sample[1] * 12540`
- `out_frame_2 = frame_sample[2] * 23170`
- `out_frame_3 = frame_sample[3] * 30274`
- `out_frame_4 = frame_sample[4] * 32767`
- `out_frame_5 = frame_sample[5] * 30274`
- `out_frame_6 = frame_sample[6] * 23170`
- `out_frame_7 = frame_sample[7] * 12540`

There is exactly one output transaction for each frame emission event and zero output transactions for all other accepted input samples.

## Reset Behavior

Reset is synchronous active-high `rst`.

On reset:
- `sample_count = 0`
- the stored sample history is cleared to eight zeros
- the internal next-frame schedule is reset so that the first emitted frame occurs on the 8th accepted input sample
- `out_valid = 0`
- all output data ports `out_frame_0` through `out_frame_7` are `0`

After reset deasserts, before the first frame emission, accepted input samples update internal state but do not produce outputs.
