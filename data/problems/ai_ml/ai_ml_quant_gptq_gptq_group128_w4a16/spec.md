# Structured-factor GPTQ: a 128-weight W4 group

Quantize one 128-weight row using a calibrated upper inverse-Hessian factor and
ordered GPTQ error feedback. All 128 weights participate. The task deliberately
isolates the quantization/update kernel rather than model loading or calibration.
The W4 codes do not prescribe an activation-inference interface.

## Interface

### RTL interface

```systemverilog
module gptq_group128 (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic [2047:0] in_weights,
    input logic [1015:0] in_first,
    input logic [1007:0] in_second,
    output logic out_valid, input logic out_ready,
    output logic [15:0] out_scale, out_zero,
    output logic [511:0] out_codes
);
```

One accepted request produces one ordered response. Word i of in_weights is
bits [16*i+:16], for i=0..127. Byte i of in_first couples i to i+1 (i=0..126);
byte i of in_second couples i to i+2 (i=0..125). Nibble i of out_codes is code i.
All packed indices are least-significant first. There are no inert trailing
calibration fields. Every input bit pattern denotes a valid request.
Valid and payload remain stable while stalled. Latency is unconstrained;
payloads are zero when out_valid=0. No accepted request may be lost.

### Behavior interface

```python
class GPTQGroup128:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_weights, in_first, in_second): ...
```

Returns a mapping with out_scale, out_zero and out_codes, encoded as unsigned
port integers.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_codes`: 512.
- `out_scale`, `out_zero`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction state

Requests share no numerical state.

## Modules

`gptq_group128` contains group-grid selection, ordered compensation and an
elastic response register. No microarchitecture is prescribed.

## Functional Behavior

### Calibration factor and quantization grid

This task implements the column-quantization/update stage of GPTQ, after
calibration, damping and factorization. The input represents a unit-diagonal
upper-triangular matrix U with two nonzero superdiagonals:
U[i,i]=1, U[i,i+1]=first[i]/256, U[i,i+2]=second[i]/256;
all other off-diagonal entries are zero. Each coefficient is signed 8-bit.
Every such U is invertible, so H=(transpose(U)*U)^(-1) is positive definite
and U is the upper Cholesky factor of H^(-1). Off-diagonal inputs are real
calibration-dependent coupling, not ignored Hessian metadata. Factorization,
activation collection, arbitrary dense U and activation-order permutation are
outside this structured-factor kernel. No particular training dataset is implied.

Let low=min(0,min(W)), high=max(0,max(W)),
scale=max(1,ceil((high-low)/15)) and zero=low+8*scale. Freeze this affine grid
once per completed group. W contains signed 16-bit integer weights; these can
be interpreted as Q8.8, in which case scale and zero have the same unit.
The reported scale is unsigned 16-bit and zero is signed 16-bit (two's complement).
This explicitly quantized grid is a finite adaptation of the source affine W4
quantizer, not TorchAO FP16 qparameter or packed-tensor bit identity.

### Ordered error compensation

R(x,d) rounds the exact rational x/d to nearest integer, ties to the even integer,
for d>0, including negative x. Initialize A[i]=W[i]*65536. In order i=0..n-1:

1. k=clip(R(A[i]-low*65536,scale*65536),0,15).
2. Emit the signed W4 code k-8, encoded modulo 16. Its reconstructed weight is
   low+k*scale, equivalently (k-8)*scale+zero.
3. e=A[i]-(low+k*scale)*65536.
4. If i+1<n, subtract R(e*first[i],256) from A[i+1].
   If i+2<n, subtract R(e*second[i],256) from A[i+2].

Thus later codes depend on earlier quantization errors. The recurrence is the
source update `(w-dq)/U[i,i]` propagated along U's row; the diagonal is one.
Intermediate A values retain 16 fractional bits and each compensation write
has the stated rounding. There is no intermediate clipping or wraparound.
Signed 64-bit intermediates suffice: for n<=128 and |U[i,j]|<=1/2,
|e_i| is bounded by (i+1)*2^33 in A units, so products remain below 2^48.
Final codes alone clip. All-zero coupling reduces to affine RTN; changing a
nonzero coupling can change later codes without changing any weight or diagonal.

## Reset Behavior

Active-high synchronous reset discards a pending response and clears every
output. After deassertion, in_ready=1 and out_valid=0. A new group is independent
of all requests before reset.
