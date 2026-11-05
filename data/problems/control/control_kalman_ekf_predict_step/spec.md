# ControlKalmanEkfPredictStep

This block performs the predict step of a fixed-dimension extended Kalman filter state propagator using fixed-point arithmetic. For each accepted input transaction it computes the prior state vector `x' = F·x + B·u` and the prior covariance matrix `P' = F·P·F^T + Q` for a 2-state, 1-control system. State and matrix elements are stored and transferred as signed Q31 integers. All matrix products use exact integer multiply followed by a fixed arithmetic right shift by 31 bits with truncation toward negative infinity, matching two's-complement hardware `>>> 31`.

## Interface

### RTL interface

```systemverilog
module control_kalman_ekf_predict_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [31:0] in_x0,
    input  logic signed [31:0] in_x1,

    input  logic signed [31:0] in_p00,
    input  logic signed [31:0] in_p01,
    input  logic signed [31:0] in_p10,
    input  logic signed [31:0] in_p11,

    input  logic signed [31:0] in_f00,
    input  logic signed [31:0] in_f01,
    input  logic signed [31:0] in_f10,
    input  logic signed [31:0] in_f11,

    input  logic signed [31:0] in_b0,
    input  logic signed [31:0] in_b1,
    input  logic signed [31:0] in_u,

    input  logic signed [31:0] in_q00,
    input  logic signed [31:0] in_q01,
    input  logic signed [31:0] in_q10,
    input  logic signed [31:0] in_q11,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [31:0] out_x0,
    output logic signed [31:0] out_x1,

    output logic signed [31:0] out_p00,
    output logic signed [31:0] out_p01,
    output logic signed [31:0] out_p10,
    output logic signed [31:0] out_p11
);
```

### Behavior interface

```python
class ControlKalmanEkfPredictStep:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_x0, in_x1,
        in_p00, in_p01, in_p10, in_p11,
        in_f00, in_f01, in_f10, in_f11,
        in_b0, in_b1, in_u,
        in_q00, in_q01, in_q10, in_q11
    ): ...
```

Each `process` call represents one accepted request and returns one dictionary
with exactly these keys:
`out_x0`, `out_x1`, `out_p00`, `out_p01`, `out_p10`, `out_p11`.

All arguments and returned fields are unsigned 32-bit words encoding the signed
Q31 values defined below. Every request returns all six fields, including a
zero result, not `None` or a list. Inputs supply the full state and matrices
anew on every call; the model has no persistent numerical state. Construction
and `reset()` have no numerical effect; `reset()` returns `None`. Idle and
output-stall cycles require no calls.

### Transaction protocol

An input transfers on a rising edge with
`in_valid && in_ready`; an output transfers with `out_valid && out_ready`.
The sender holds an unaccepted request stable, and the design holds every
field of a stalled valid response stable. Responses are ordered, one per
accepted request. Reset cancels pending work. Latency is not prescribed,
subject to the run's declared bounds.

## Modules

- `control_kalman_ekf_predict_step`: top-level predict-step datapath for a 2-state EKF prior update.

## Functional Behavior

All input and output ports other than handshake are signed 32-bit Q31 values. A raw integer `r` represents the real value `r * 2^-31`. Examples: `0x40000000 = +0.5`, `0x80000000 = -1.0`.

The block is stateless across transactions. Each accepted input transaction independently computes one accepted output transaction.

Define signed interpretation of every 32-bit input as two's-complement Q31.

Define the fixed-point multiply primitive

- `mul_q31(a, b) = trunc_asr_31(a * b)`

where:
- `a` and `b` are signed 32-bit integers interpreted as mathematical integers,
- `a * b` is a signed 64-bit mathematical product,
- `trunc_asr_31(v)` is arithmetic right shift by 31 bits on the signed product,
- no saturation is applied,
- the final result is wrapped to signed 32-bit two's-complement.

This is the exact hardware behavior of `(signed'(a) * signed'(b)) >>> 31`, then truncation to 32 bits.

The state prediction is

- `out_x0 = wrap32(mul_q31(in_f00, in_x0) + mul_q31(in_f01, in_x1) + mul_q31(in_b0, in_u))`
- `out_x1 = wrap32(mul_q31(in_f10, in_x0) + mul_q31(in_f11, in_x1) + mul_q31(in_b1, in_u))`

where `wrap32` means modulo `2^32` followed by signed two's-complement interpretation.

The covariance prediction uses the matrix product order `P' = (F·P)·F^T + Q`.

First compute the intermediate matrix `A = F·P` with Q31 matrix multiplication:

- `a00 = wrap32(mul_q31(in_f00, in_p00) + mul_q31(in_f01, in_p10))`
- `a01 = wrap32(mul_q31(in_f00, in_p01) + mul_q31(in_f01, in_p11))`
- `a10 = wrap32(mul_q31(in_f10, in_p00) + mul_q31(in_f11, in_p10))`
- `a11 = wrap32(mul_q31(in_f10, in_p01) + mul_q31(in_f11, in_p11))`

Then compute `Ptmp = A·F^T`:

- `ptmp00 = wrap32(mul_q31(a00, in_f00) + mul_q31(a01, in_f01))`
- `ptmp01 = wrap32(mul_q31(a00, in_f10) + mul_q31(a01, in_f11))`
- `ptmp10 = wrap32(mul_q31(a10, in_f00) + mul_q31(a11, in_f01))`
- `ptmp11 = wrap32(mul_q31(a10, in_f10) + mul_q31(a11, in_f11))`

Then add process noise:

- `out_p00 = wrap32(ptmp00 + in_q00)`
- `out_p01 = wrap32(ptmp01 + in_q01)`
- `out_p10 = wrap32(ptmp10 + in_q10)`
- `out_p11 = wrap32(ptmp11 + in_q11)`

No symmetry correction is applied. If the inputs are not symmetric, the outputs preserve the direct matrix-product result above.

No saturation is applied at any add or multiply stage. Every named intermediate and every output is reduced to signed 32-bit by two's-complement wrap after the stated addition. Multiplication narrowing is only by arithmetic right shift by 31 bits.

Worked multiply example: if `a = 0x40000000` and `b = 0x40000000`, then `a*b = 2^60`, `mul_q31(a,b) = 0x20000000`, which is `+0.25` in Q31.

## Reset Behavior

`rst` is active-high. Reset may be implemented synchronously or asynchronously. The block has no architectural state. After reset, `out_valid` must be 0. Output data ports `out_x0`, `out_x1`, `out_p00`, `out_p01`, `out_p10`, and `out_p11` must be driven to 0 whenever no valid output transaction is being presented.
