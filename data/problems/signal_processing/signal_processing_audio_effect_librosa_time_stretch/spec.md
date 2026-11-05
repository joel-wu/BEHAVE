# Phase-vocoder time stretch

Process a complete 64-sample signed-16 audio clip through a finite STFT,
phase-vocoder and normalized overlap-add reconstruction. Change the clip length while preserving the phase-vocoder frequency evolution.
The transform size is 16 and hop size four; state is local to the clip.

## Interface

### RTL interface

```systemverilog
module audio_time_stretch (
    input logic clk,rst,
    input logic in_valid,output logic in_ready,
    input logic [1023:0] in_samples,
    input logic [9:0] in_rate,
    output logic out_valid,input logic out_ready,
    output logic out_status,
    output logic [7:0] out_length,
    output logic [2047:0] out_samples
);
```

Samples are packed least-significant first, with sample i at [16*i+:16].
in_rate is unsigned Q8.8 and must lie in 128..512 (0.5..2 inclusive).
Other values produce status=1, length=0 and zero samples. Valid requests
produce status=0. The output packs signed-16 samples in the same order;
unused slots are zero. One request gives one complete ordered response.
Transfers require valid && ready on a rising edge, and a stalled sender must
hold its valid and payload. Latency is unconstrained, with no dropped requests.
All output payloads are zero while out_valid=0.

### Behavior interface

```python
class AudioTimeStretch:
    def __init__(self): ...
    def reset(self): ...
    def process(self,in_samples,in_rate): ...
```

Each call represents one accepted clip request. `in_samples` is an unsigned
1024-bit packed integer, not a list, and `in_rate` is an unsigned 10-bit
integer. Return one dictionary with exactly these keys:

```python
{"out_status": ..., "out_length": ..., "out_samples": ...}
```

`out_status` is integer `0` or `1`, `out_length` is an unsigned 8-bit integer,
and `out_samples` is an unsigned 2048-bit packed integer. A valid rate returns
status `0` and the specified length from 32 through 128; an invalid rate
returns status `1`, length `0` and samples `0`. Neither case returns `None`
or a list. Each request begins with independent clip-local transform state.
`reset()` returns `None` and produces no response.

## Modules

`audio_time_stretch` performs analysis frames, phase synthesis, weighted overlap-add and
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

Return L samples (32..128), then zero-fill the unused packed output slots.
No direct resampling substitutes for the phase-vocoder synthesis above.

## Reset Behavior

Active-high synchronous reset cancels an unfinished clip or pending response,
clears all transform/output state and drives out_valid=0 with zero payloads.
After deassertion in_ready=1. No phase or overlap data crosses a reset or a
complete clip boundary.
