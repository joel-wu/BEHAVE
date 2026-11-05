# SILK NLSF to stabilized LPC coefficients

Convert normalized line spectral frequencies to a monic whitening filter,
preserving the Opus SILK cosine interpolation, polynomial construction,
coefficient fitting and stability correction. Both native orders 10 and 16
are supported. This is the scalar integer kernel, not a speech encoder.

## Interface

### RTL interface

```systemverilog
module silk_nlsf_to_a (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic [4:0] in_d,
    input logic [14:0] in_NLSF_0,
    input logic [14:0] in_NLSF_1,
    input logic [14:0] in_NLSF_2,
    input logic [14:0] in_NLSF_3,
    input logic [14:0] in_NLSF_4,
    input logic [14:0] in_NLSF_5,
    input logic [14:0] in_NLSF_6,
    input logic [14:0] in_NLSF_7,
    input logic [14:0] in_NLSF_8,
    input logic [14:0] in_NLSF_9,
    input logic [14:0] in_NLSF_10,
    input logic [14:0] in_NLSF_11,
    input logic [14:0] in_NLSF_12,
    input logic [14:0] in_NLSF_13,
    input logic [14:0] in_NLSF_14,
    input logic [14:0] in_NLSF_15,
    output logic out_valid, input logic out_ready,
    output logic out_status,
    output logic [15:0] out_a_Q12_0,
    output logic [15:0] out_a_Q12_1,
    output logic [15:0] out_a_Q12_2,
    output logic [15:0] out_a_Q12_3,
    output logic [15:0] out_a_Q12_4,
    output logic [15:0] out_a_Q12_5,
    output logic [15:0] out_a_Q12_6,
    output logic [15:0] out_a_Q12_7,
    output logic [15:0] out_a_Q12_8,
    output logic [15:0] out_a_Q12_9,
    output logic [15:0] out_a_Q12_10,
    output logic [15:0] out_a_Q12_11,
    output logic [15:0] out_a_Q12_12,
    output logic [15:0] out_a_Q12_13,
    output logic [15:0] out_a_Q12_14,
    output logic [15:0] out_a_Q12_15
);
```

An accepted request produces exactly one ordered response. Hold valid and all
payload fields under backpressure. Implementation latency is unconstrained;
no input may be accepted and then silently discarded. Inputs are unsigned
15-bit normalized frequencies (0..32767), interpreted as frequency/pi in Q15.
For order 10, inputs 10..15 are ignored. Other orders return out_status=1 and
all-zero coefficients. Supported orders return out_status=0. Coefficients are
signed two's-complement 16-bit Q12, with positions >=order zero. Payload is
zero whenever out_valid=0.

### Behavior interface

```python
class SilkNLSFToA:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_d, in_NLSF_0, in_NLSF_1, in_NLSF_2, in_NLSF_3, in_NLSF_4, in_NLSF_5, in_NLSF_6, in_NLSF_7, in_NLSF_8, in_NLSF_9, in_NLSF_10, in_NLSF_11, in_NLSF_12, in_NLSF_13, in_NLSF_14, in_NLSF_15): ...
```

Each call consumes one accepted request and returns one `dict` with exactly
`out_status`, `out_a_Q12_0`, `out_a_Q12_1`, `out_a_Q12_2`, `out_a_Q12_3`,
`out_a_Q12_4`, `out_a_Q12_5`, `out_a_Q12_6`, `out_a_Q12_7`, `out_a_Q12_8`,
`out_a_Q12_9`, `out_a_Q12_10`, `out_a_Q12_11`, `out_a_Q12_12`,
`out_a_Q12_13`, `out_a_Q12_14`, and `out_a_Q12_15`. Status is a Python integer
in 0..1. Coefficients are integers in 0..65535 encoding signed Q12 port bits,
not floating-point values. Unused coefficients and invalid-order results retain
all these fields with the values specified above; never return `None` or an
empty list. There is no signal history between requests. `reset()` restores
the initial model state without producing a response.

## Modules

`silk_nlsf_to_a` performs conversion, bounded bandwidth expansion, a fixed-point
Schur recursion and elastic response storage.

## Functional Behavior

The scalar C arithmetic of Opus v1.5.2 `silk_NLSF2A`, `silk_LPC_fit`,
`silk_bwexpander_32` and `silk_LPC_inverse_pred_gain_c` is specialized to the
two supported orders. No optimized architecture-specific approximation is used.
For every integer x, define R(x,s)=(x+2^(s-1))>>s; right shift is arithmetic,
so exact half ties round toward positive infinity. Stored intermediate
coefficients wrap to signed 32 bits; products use signed 64 bits. SAT32 and
SAT16 clamp to the corresponding signed range. No floating operations occur.

### Cosines and polynomials

The exact 129-entry Q12 table of 2*cos(pi*i/128) is:

```
8192, 8190, 8182, 8170, 8152, 8130, 8104, 8072, 8034, 7994, 7946, 7896, 7840, 7778, 7714, 7644, 7568, 7490, 7406, 7318, 7226, 7128, 7026, 6922, 6812, 6698, 6580, 6458, 6332, 6204, 6070, 5934, 5792, 5648, 5502, 5352, 5198, 5040, 4880, 4718, 4552, 4382, 4212, 4038, 3862, 3684, 3502, 3320, 3136, 2948, 2760, 2570, 2378, 2186, 1990, 1794, 1598, 1400, 1202, 1002, 802, 602, 402, 202, 0, -202, -402, -602, -802, -1002, -1202, -1400, -1598, -1794, -1990, -2186, -2378, -2570, -2760, -2948, -3136, -3320, -3502, -3684, -3862, -4038, -4212, -4382, -4552, -4718, -4880, -5040, -5198, -5352, -5502, -5648, -5792, -5934, -6070, -6204, -6332, -6458, -6580, -6698, -6812, -6922, -7026, -7128, -7226, -7318, -7406, -7490, -7568, -7644, -7714, -7778, -7840, -7896, -7946, -7994, -8034, -8072, -8104, -8130, -8152, -8170, -8182, -8190, -8192
```

For input x, let i=x>>8 and f=x&255. Interpolate
R(256*C[i]+f*(C[i+1]-C[i]),4) to Q16. Reorder into c using destinations
[0,9,6,3,4,5,8,1,2,7] for order 10 and
[0,15,8,7,4,11,12,3,2,13,10,5,6,9,14,1] for order 16.
Build P from c[0],c[2],... and Q from c[1],c[3],... . For either sequence z:
P[0]=65536, P[1]=-z[0]. For k=1..d/2-1, first set
P[k+1]=2*P[k-1]-R(z[k]*P[k],16); then for n=k down to 2 update
P[n]+=P[n-2]-R(z[k]*P[n-1],16); finally P[1]-=z[k].
For k=0..d/2-1, let p=P[k+1]+P[k], q=Q[k+1]-Q[k], and set
a[k]=-q-p, a[d-1-k]=q-p (Q17).

### Fitting and bandwidth expansion

Up to ten times find the first largest absolute a[k], and let m=R(abs(a[k]),5).
Stop when m<=32767. Otherwise clamp m to 163838 and expand with
chirp=65470-(((m-32767)<<14)//((m*(k+1))>>2)).
Expansion uses delta=chirp_initial-65536; for each coefficient in order,
a[j]=(a[j]*chirp)>>16, then chirp+=R(wrap32(chirp*delta),16).
After fitting set b[j]=SAT16(R(a[j],5)). If all ten iterations were consumed,
also replace a[j] by b[j]<<5 before stability correction.

### Stability correction

The inverse-gain check first rejects sum(b)>=4096. Otherwise initialize
A[j]=b[j]<<12 and gain=2^30. For k=d-1 down to 0, reject abs(A[k])>16773022;
set rc=-128*A[k], f=2^30-((rc*rc)>>32), gain=((gain*f)>>32)<<2, and reject
gain<107374. For k>0 let m be the bit length of f, normal=f<<(31-m),
v=(2^29-1)//(normal>>16), e=(2^29-((normal*v)>>16))<<3, and
inv=SAT32((v<<16)+((e*v)>>16)). For paired old coefficients l=A[n] and
r=A[k-1-n], set A[n]=R(SAT32(l-R(r*rc,31))*inv,m) and
A[k-1-n]=R(SAT32(r-R(l*rc,31))*inv,m), for n=0..ceil(k/2)-1.
Reject a result outside signed 32-bit range. Surviving all k accepts b.

While rejected, for i=0..15 expand the retained Q17 vector a with
chirp=65536-(2<<i), and replace b by the signed-16 encoding of R(a,5),
then check again. The last chirp is zero, so this bounded fallback terminates.
Frequencies need not be sorted for this total finite-word contract; normally
codec-produced inputs are ordered. No unsupported order accesses the table.

## Reset Behavior

Synchronous active-high reset cancels an in-flight conversion or held response,
clears out_valid and all payload, and makes in_ready=1. There is no inter-request
signal history. A new request starts fresh after reset.
