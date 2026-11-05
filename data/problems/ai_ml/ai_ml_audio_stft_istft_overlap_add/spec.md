# Centered inverse STFT and normalized overlap-add

Reconstruct a sample stream from eight-point inverse-STFT frames using a periodic Hann window, centered cropping and normalized overlap-add.

## Interface

### RTL interface

```systemverilog
module ai_ml_audio_stft_istft_overlap_add (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic in_start,
    input logic [31:0] in_bin0_re,
    input logic [31:0] in_bin0_im,
    input logic [31:0] in_bin1_re,
    input logic [31:0] in_bin1_im,
    input logic [31:0] in_bin2_re,
    input logic [31:0] in_bin2_im,
    input logic [31:0] in_bin3_re,
    input logic [31:0] in_bin3_im,
    input logic [31:0] in_bin4_re,
    input logic [31:0] in_bin4_im,
    input logic out_ready,
    output logic out_valid,
    output logic [31:0] out_sample_idx, out_sample,
    output logic out_clipped
);
```

### Behavior interface

```python
class AiMlAudioStftIstftOverlapAdd:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_start, in_bin0_re, in_bin0_im, in_bin1_re, in_bin1_im, in_bin2_re, in_bin2_im, in_bin3_re, in_bin3_im, in_bin4_re, in_bin4_im): ...
```

Each call represents one accepted frame and returns an ordered list: `[]` for
the first/start frame, otherwise four dictionaries. Each dictionary has exactly
`out_sample_idx` (unsigned 32-bit integer), `out_sample` (unsigned 32-bit
two's-complement encoding), and `out_clipped` (integer 0 or 1). Arguments are
raw unsigned port integers, not Python floats. Never return `None` or a bare
dictionary. Reuse the instance to retain overlap state and sample numbering;
`reset()` clears them, returns `None`, and emits no samples.

## Modules

A streaming finite-width specialization of torchaudio inverse_spectrogram: n_fft=win_length=8, hop_length=4, periodic Hann window, onesided=True, center=True, normalized=False, pad=0 and length=None. It performs the inverse real transform, synthesis window, overlap-add and division by the squared-window envelope. Spectra are inputs, not magnitudes or estimated phases.

## Functional Behavior

The five complex bins are k=0..4, not eight one-sided bins. Real and imaginary words are signed Q16.16. DC and Nyquist imaginary components are ignored as for a real inverse transform. Positive-frequency bins 1..3 supply the negative frequencies by conjugate symmetry.

The first frame after reset, or any frame with in_start=1, primes a new stream and emits no samples. Each subsequent frame with in_start=0 emits four samples in order. For F frames of one stream, exactly 4(F-1) samples are emitted: the first and final four padded samples are cropped, matching the selected centered/default-length operation. There is no flush command; a new start discards the unreturned final tail of the previous stream. A single-frame stream returns an empty sequence by this task's explicit finite extension.

All calculations below operate on signed integer codes. R(v,d) rounds v/d to nearest integer, ties to even; intermediate arithmetic is exact and is not narrowed until specified. Let C=[1073741824, 759250125, 0, -759250125, -1073741824, -759250125, 0, 759250125], W=[0, 157245850, 536870912, 916495974, 1073741824, 916495974, 536870912, 157245850]. These are nearest-even Q30 samples of cos(2*pi*j/8) and periodic Hann (1-cos(2*pi*j/8))/2. Q30 uses 2^30 for exactly one; it is not a signed Q31 fraction that overflows at one.

For each n=0..7, compute T[n]=re[0]*2^30+re[4]*C[(4*n) mod 8]+2*sum(k=1..3, re[k]*C[(k*n) mod 8]-im[k]*C[(2-k*n) mod 8]). All mod indices are nonnegative. Then s[n]=R(T[n],8*2^30), u[n]=R(s[n]*W[n],2^30).

Persistent tail[0..3] stores the previous u[4..7]. For a noninitial frame, y[i]=R((tail[i]+u[i])*2^60,W[i]^2+W[i+4]^2), i=0..3. All four denominators are strictly positive. Saturate y[i] to signed 32 bits for out_sample; out_clipped is one exactly when saturation was needed. Update tail to current u[4..7] after computing outputs. The first/start frame only updates tail and resets the sample counter to zero.

out_sample_idx counts emitted samples from zero within each stream, wrapping modulo 2^32. The counter increments by four per emitting frame. Each output transaction contains out_sample_idx, out_sample and out_clipped. Coefficient and integer rounding make this an explicit fixed-point approximation, not bit-equivalence with every floating FFT backend.

Input/output transfers occur on rising clock edges with valid and ready high. A stalled output retains its valid and full payload; later inputs cannot discard an already promised output.

## Reset Behavior

Synchronous active-high reset clears the overlap tail, primed flag, sample counter and all pending outputs. After reset in_ready=1, out_valid=0 and output payloads are zero. Idle payloads are zero.
