# SignalProcessingAudioSpectrumLibrosaPhaseVocoder

This block computes one phase-vocoder update for one STFT frequency bin.
It interpolates the magnitudes of two adjacent complex samples, synthesizes
a complex output at the current accumulated phase, and returns the next
unwrapped phase. The caller supplies and retains the phase accumulator for
each bin; requests may therefore interleave independent bins.

## Interface

### RTL interface

```systemverilog
module signal_processing_audio_spectrum_librosa_phase_vocoder (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [15:0] in_c0_real,
    input  logic [15:0] in_c0_imag,
    input  logic [15:0] in_c1_real,
    input  logic [15:0] in_c1_imag,
    input  logic [63:0] in_phase_acc,
    input  logic [16:0] in_alpha,
    input  logic [63:0] in_phi_advance,
    output logic out_valid,
    input  logic out_ready,
    output logic [16:0] out_real,
    output logic [16:0] out_imag,
    output logic [63:0] out_phase_acc
);
```

Complex input components are signed int16 integers; output components are
signed int17 integers. The 64-bit phases are signed two's-complement integers
in units of 2^-32 turns (one turn is 2*pi radians). Whole turns are retained
in the returned phase; only synthesis uses the phase modulo one turn.
`in_alpha` is unsigned with scale 2^-16; values above 65536 clamp to 65536.

Each rising-edge input transfer (`in_valid && in_ready`) produces one
ordered output transfer (`out_valid && out_ready`). Stalled valid output
payloads remain stable. Latency is not fixed.

### Behavior interface

```python
class SignalProcessingAudioSpectrumLibrosaPhaseVocoder:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_c0_real, in_c0_imag, in_c1_real, in_c1_imag,
                in_phase_acc, in_alpha, in_phi_advance): ...
```

Each call represents one accepted bin update and returns exactly
`{"out_real": real_bits, "out_imag": imag_bits, "out_phase_acc": phase_bits}`.
Arguments and returned values are unsigned port encodings: complex inputs use
16 bits per component, complex outputs 17 bits per component, and phases
64 bits; `in_alpha` uses 17 bits. Decode signs and scales as specified above,
without Python floats or complex numbers. Zero-amplitude inputs still return
the complete dictionary, never `None` or a list. No handshake signals are
returned. The caller supplies the phase on every call; the model retains no
per-bin state. Construction and `reset()` establish this empty state, and
`reset()` returns `None`.

## Modules

- `signal_processing_audio_spectrum_librosa_phase_vocoder`: magnitude
  interpolation, phase-difference unwrapping and complex synthesis for one bin.

## Functional Behavior

Let T=2^32, P=signed64(in_phase_acc), A=signed64(in_phi_advance) and
a=min(in_alpha,65536). For each signed complex pair (x,y), compute
`mag(x,y)=floor(sqrt((x*x+y*y)*2^32))`, an unsigned magnitude with 16
fractional bits. Interpolate
`m=floor(((65536-a)*mag(c0)+a*mag(c1))/65536)`.

The following integer CORDIC rules define the exact phase and synthesis
quantization, not a required pipeline. All right shifts are signed floor
division. Intermediate integers do not wrap. Use G=652032874 and the table
C[i], i=0..29:

```text
536870912, 316933406, 167458907, 85004756, 42667331, 21354465, 10679838, 5340245, 2670163, 1335087, 667544, 333772, 166886, 83443, 41722, 20861, 10430, 5215, 2608, 1304, 652, 326, 163, 81, 41, 20, 10, 5, 3, 1
```

To obtain angle(x,y), return 0 for (0,0), 0 for positive real-axis values,
T/2 for negative real-axis values, and +/-T/4 for positive/negative
imaginary-axis values. Otherwise initialize z=0. If x<0, initialize z to
T/2 when y>0, or -T/2 when y<0, and negate both x and y. Multiply x and y
by 2^24. For i=0..29, update simultaneously from their old values:
when y>=0, `(x,y,z)=(x+(y>>i),y-(x>>i),z+C[i])`;
otherwise `(x,y,z)=(x-(y>>i),y+(x>>i),z-C[i])`.
The resulting z is the angle.

For synthesis, initialize x=floor(m*G/2^30), y=0 and z=signed32(P mod T).
If z>T/4, negate x and subtract T/2 from z; if z<-T/4, negate x and add
T/2 to z. For i=0..29, update simultaneously:
when z>=0, `(x,y,z)=(x-(y>>i),y+(x>>i),z-C[i])`;
otherwise `(x,y,z)=(x+(y>>i),y-(x>>i),z+C[i])`.
Return `out_real=floor((x+32768)/65536)` and
`out_imag=floor((y+32768)/65536)`, encoded as signed int17.
These are approximately m/65536 times cos(P) and sin(P); the integer
rules above resolve every rounding choice.

Compute `d=angle(c1)-angle(c0)-A`, and let q be d/T rounded to nearest
integer, ties to even. The returned phase is
`out_phase_acc=(P+A+d-q*T) mod 2^64`.
It is the **next** phase: the current output uses P, not that updated phase.
Zero-amplitude samples have angle zero. Phase wrap at half turns uses the
same ties-to-even rule for positive and negative d.

For example, c0=(3,4), c1=(0,9), alpha=32768, P=0 yield output (7,0):
the interpolated magnitude is 7. Changing P by a quarter turn rotates that
output to (0,7).
The outer STFT, frame selection, playback-rate scheduling and inverse STFT
are not performed by this single-bin update.

## Reset Behavior

Reset is synchronous active-high. A reset edge clears output validity and
payloads; the reset state has in_ready=1. A pending response is cancelled.
No per-bin phase is stored internally: callers initialize their own phase
accumulators, then feed each returned phase into the next update for that bin.
