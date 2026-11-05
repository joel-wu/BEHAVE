# Pitch shift with preserved duration

Process a complete 64-sample signed-16 audio clip through a finite STFT,
phase-vocoder and normalized overlap-add reconstruction. Resample the stretched clip to shift pitch while returning exactly 64 samples.
The transform size is 16 and hop size four; state is local to the clip.

## Interface

### RTL interface

```systemverilog
module audio_pitch_shift (
    input logic clk,rst,
    input logic in_valid,output logic in_ready,
    input logic [1023:0] in_samples,
    input logic [9:0] in_factor,
    output logic out_valid,input logic out_ready,
    output logic out_status,
    output logic [7:0] out_length,
    output logic [1023:0] out_samples
);
```

Samples are packed least-significant first, with sample i at [16*i+:16].
in_factor is unsigned Q8.8 and must lie in 128..512 (0.5..2 inclusive).
Other values produce status=1, length=0 and zero samples. Valid requests
produce status=0. The output packs signed-16 samples in the same order;
unused slots are zero. One request gives one complete ordered response.
Transfers require valid && ready on a rising edge, and a stalled sender must
hold its valid and payload. Latency is unconstrained, with no dropped requests.
All output payloads are zero while out_valid=0.

### Behavior interface

```python
class AudioPitchShift:
    def __init__(self): ...
    def reset(self): ...
    def process(self,in_samples,in_factor): ...
```

Each call represents one accepted clip and returns exactly one dictionary with
keys `out_status`, `out_length`, and `out_samples`. Values are Python integers:
status is 0 or 1, length is 64 for a valid factor and 0 otherwise, and samples
is one unsigned 1024-bit packed word, not a Python sample list. `in_samples`
uses the same packed-word encoding; `in_factor` is an integer in [0, 1023].
Invalid factors still return the complete error dictionary, not `None` or a
list of responses. Each request starts with independent clip-local transform
state. `reset()` returns no response.

## Modules

`audio_pitch_shift` performs analysis frames, phase synthesis, weighted overlap-add and
output packing. The pitch-shifting form additionally resamples the result.

## Functional Behavior

All arithmetic is exact integer arithmetic until an explicitly stated division
or shift. R(x,d) is nearest-integer rounding of x/d with ties to even for d>0;
right shift of a signed integer rounds downward. SAT16 clips to [-32768,32767].
For stretch, rate=in_rate. For pitch shift, rate=R(65536,in_factor); the achieved
pitch factor is 256/rate. The Q8.8 inverse-rate quantization is explicit.

### Analysis transform

Q=16384. Periodic Hann W, cosine C and sine S tables are:

```
W=[0, 624, 2399, 5057, 8192, 11327, 13985, 15760, 16384, 15760, 13985, 11327, 8192, 5057, 2399, 624]
C=[16384, 15137, 11585, 6270, 0, -6270, -11585, -15137, -16384, -15137, -11585, -6270, 0, 6270, 11585, 15137]
S=[0, 6270, 11585, 15137, 16384, 15137, 11585, 6270, 0, -6270, -11585, -15137, -16384, -15137, -11585, -6270]
```

Frames f=0..16 are centered at input sample 4*f. Zero-pad samples outside 0..63.
For each one-sided bin k=0..8, compute
re[f,k]=R(sum_j x[4*f+j-8]*W[j]*C[(k*j)%16],16*Q^2),
im[f,k]=R(-sum_j x[4*f+j-8]*W[j]*S[(k*j)%16],16*Q^2), j=0..15.
Frame 17 is all zero. This integer spectral boundary is normative: the phase
of a bin rounded to (0,0) is zero, not the phase of a tiny floating FFT residual.

### Fixed-point phase primitives

Angles are signed integers in 2^-32 turns; set T=2^32. The CORDIC angle table is:

```
ATAN=[536870912, 316933406, 167458907, 85004756, 42667331, 21354465, 10679838, 5340245, 2670163, 1335087, 667544, 333772, 166886, 83443, 41722, 20861, 10430, 5215, 2608, 1304, 652, 326, 163, 81, 41, 20, 10, 5, 3, 1]
```

Magnitude(x,y)=floor(sqrt((x*x+y*y)*2^32)). Angle(x,0) is 0 for x>=0, T/2
otherwise; Angle(0,y) is T/4 for y>0, -T/4 for y<0. Otherwise initialize z=0;
if x<0, set z=T/2 for y>0 or -T/2 for y<0 and negate x,y. Scale x,y by 2^24.
For i=0..29, using old x: if y>=0, update
(x,y,z)=(x+(y>>i),y-(old_x>>i),z+ATAN[i]); otherwise subtract/add and subtract
the angle. The final z is Angle.

Rotate(m,p) starts x=(m*652032874)>>30,y=0 and z=signed32(p modulo T).
If z>T/4 negate x and subtract T/2 from z; if z<-T/4 negate x and add T/2.
For i=0..29, using old x: if z>=0 set
(x,y,z)=(x-(y>>i),y+(old_x>>i),z-ATAN[i]); otherwise reverse these signs.
Return ((x+32768)>>16,(y+32768)>>16). These primitives fix all numerical ties.

### Phase-vocoder synthesis and overlap-add

Initialize p[k]=Angle(re[0,k],im[0,k]). Produce F=ceil(17*256/rate) frames.
For synthesis frame f, let left=floor(f*rate/256), alpha=(f*rate)%256.
For every bin, interpolate magnitudes between frames left and left+1 with
integer floor division by 256, then Rotate the magnitude with the current p[k].
Call the resulting components r[k],i[k]. Let advance=k*T/4 and
delta=Angle(next)-Angle(current)-advance. Update
p[k]+=advance+delta-R(delta,T)*T, after synthesizing this frame.

For each j=0..15, inverse-transform using Hermitian symmetry:
v[j]=R(r[0]*Q+r[8]*C[(8*j)%16]+2*sum_(k=1..7)
(r[k]*C[(k*j)%16]-i[k]*S[(k*j)%16]),Q).
The imaginary DC/Nyquist components are discarded. Accumulate v[j]*W[j] at
position 4*f+j and separately accumulate W[j]^2 there. Remove the first eight
positions (the centered padding). For L=R(64*256,rate) positions, the stretched
sample is SAT16(R(accum[i+8]*Q,norm[i+8])). Every used norm is positive.

### Duration-preserving resampling

Return exactly 64 samples. For output i, index=floor(i*256/rate),
frac=(i*256)%rate. Read a=stretched[index], b=stretched[index+1], using zero for
indices outside 0..L-1. Output R(((rate-frac)*a+frac*b),rate). This is explicitly
linear resampling without a separate anti-alias filter or energy normalization;
it is not the default librosa high-quality resampler's bit identity.

## Reset Behavior

Active-high synchronous reset cancels an unfinished clip or pending response,
clears all transform/output state and drives out_valid=0 with zero payloads.
After deassertion in_ready=1. No phase or overlap data crosses a reset or a
complete clip boundary.
