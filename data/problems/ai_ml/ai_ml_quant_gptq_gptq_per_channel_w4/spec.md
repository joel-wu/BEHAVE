# Framed per-channel W4 GPTQ update

Collect a channel of signed weights and calibrated coupling rows, then emit
their GPTQ-compensated W4 codes. Channels can be explicitly terminated and
have at most N weights. Unlike a diagonal-only quantizer, earlier rounding
errors affect subsequent columns. This is an offline update kernel with a
stream transport, not an activation-inference engine.

## Interface

### RTL interface

```systemverilog
module gptq_channel #(parameter integer N=128) (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic [15:0] in_weight,
    input logic [7:0] in_first, in_second,
    input logic in_last,
    output logic out_valid, input logic out_ready,
    output logic [3:0] out_qweight,
    output logic [15:0] out_scale, out_zero,
    output logic [6:0] out_index,
    output logic out_last
);
```

N is an elaboration-time integer in 1..128, default 128. Each input contributes
one signed 16-bit weight and two signed 8-bit coupling coefficients. An asserted
in_last or the Nth input completes the channel. A completed n-input channel
produces exactly n output records in input order; an incomplete prefix produces
none. The two couplings of input i target columns i+1 and i+2 if those exist;
couplings beyond the completed channel are ignored, not applied to the next one.
out_index counts from zero within the channel and out_last marks only its final
record. No new input need be accepted while draining outputs. There is no fixed
latency requirement. Input/output transfers require valid && ready on a rising
edge; senders hold valid and payload stable while stalled. Invalid output
payloads are zero. In particular, no fixed test input count may suppress a
completed channel's responses.

### Behavior interface

```python
class GPTQPerChannelW4:
    def __init__(self, N=128): ...
    def reset(self): ...
    def process(self, in_weight, in_first, in_second, in_last): ...
```

Returns None for an incomplete channel, and a list of n mappings for completion.
Each mapping contains out_qweight, out_scale, out_zero, out_index and out_last
as unsigned port encodings. The next channel starts with the next accepted input.

Return None while the channel is incomplete. An asserted in_last or the Nth accepted
weight completes an n-weight channel and returns a list of exactly n dictionaries in
input order, with out_index=0 through n-1 and out_last=1 only on the final record.
Never return a bare dictionary. Reset discards the incomplete channel.
Every returned dictionary contains exactly these fields (bit widths):

- `out_index`: 7.
- `out_last`: 1.
- `out_qweight`: 4.
- `out_scale`, `out_zero`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

## Modules

`gptq_channel` contains a finite input buffer, group-grid selection, ordered
compensation and an output stream.

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

The reported scale and zero are repeated on every record in a channel.
out_qweight contains the signed W4 code modulo 16, not the unsigned grid index.
in_last is a framing field and has no role in the numerical update itself.

## Reset Behavior

Active-high synchronous reset discards both the incomplete input channel and
all pending output records, clears buffer/count state, and drives zero outputs
with out_valid=0 and in_ready=1. No pre-reset coupling or residual affects any
post-reset channel.
