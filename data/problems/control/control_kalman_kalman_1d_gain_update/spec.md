# ControlKalmanKalman1dGainUpdate

This block computes the scalar Kalman gain update for a 1-dimensional Kalman filter using fixed-point arithmetic. For one accepted input transaction it receives the prior scalar covariance, scalar measurement model, and scalar measurement noise, then computes the innovation covariance `S`, its reciprocal, and the Kalman gain `K = P*H/S`. All externally visible numeric quantities use signed Q31 format. Division is implemented as fixed-point signed division with truncation toward zero; if `S` is zero the reciprocal and gain outputs are forced to zero.

## Interface

### RTL interface

```systemverilog
module control_kalman_kalman_1d_gain_update (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_p,
    input  logic [31:0]  in_h,
    input  logic [31:0]  in_r,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_s,
    output logic [31:0]  out_si,
    output logic [31:0]  out_k
);
```

### Behavior interface

```python
class ControlKalmanKalman1dGainUpdate:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_p, in_h, in_r): ...
```

Call `process()` once per accepted input, passing Python integers encoding the
three signed Q31 ports. Every call returns one dictionary with exactly
`out_s`, `out_si`, and `out_k`, each a Python integer encoding the corresponding
32-bit two's-complement result. `s == 0` still returns a full dictionary with
zero reciprocal and gain, not `None` or an empty sequence. No calls represent
latency or stalls. `reset()` returns `None`; the computation is stateless.

## Modules

- `control_kalman_kalman_1d_gain_update`: top-level scalar Kalman gain computation block.

## Functional Behavior

All input and output ports are 32-bit two's-complement signed Q31 values. For any raw signed integer `x_raw`, the represented real value is `x_raw * 2^-31`. Example: `0x40000000` represents `+0.5`; `0x80000000` represents `-1.0`.

Let:

- `P = in_p` interpreted as signed Q31
- `H = in_h` interpreted as signed Q31
- `R = in_r` interpreted as signed Q31

The block computes the following values in this order.

1. Compute `HP` as a Q31 product of `H` and `P`:
   - form the signed 64-bit product `prod_hp = H * P`, which is Q62
   - convert to Q31 by arithmetic right shift by 31 bits
   - the shift is truncation toward negative infinity, matching two's-complement arithmetic shift
   - clamp the result to the signed 32-bit range `[-2147483648, 2147483647]`
   - call the result `hp_q31`

2. Compute `HPH` as a second Q31 product:
   - form `prod_hph = H * hp_q31`, which is Q62
   - convert to Q31 by arithmetic right shift by 31 bits
   - clamp to signed 32-bit range
   - call the result `hph_q31`

3. Compute the innovation covariance:
   - `s = hph_q31 + R`
   - addition is signed integer addition followed by saturation to the signed 32-bit range
   - `out_s` is the 32-bit two's-complement encoding of `s`

4. Compute the reciprocal of `S` in Q31:
   - if `s == 0`, then `si = 0`
   - otherwise compute the signed quotient
     - numerator is `1.0` in Q31, i.e. integer `2^31`
     - reciprocal raw value is `trunc_toward_zero((2^31 << 31) / s)`
     - equivalently this is `trunc_toward_zero(2^62 / s)` producing a Q31 result
   - clamp the quotient to the signed 32-bit range
   - `out_si` is the 32-bit two's-complement encoding of `si`

5. Compute the Kalman gain:
   - first reuse `hp_q31 = trunc_asr31(H * P)` from step 1
   - if `s == 0`, then `k = 0`
   - otherwise compute
     - `k = trunc_toward_zero((hp_q31 << 31) / s)`
   - clamp the quotient to the signed 32-bit range
   - `out_k` is the 32-bit two's-complement encoding of `k`

The signed division in steps 4 and 5 is defined exactly as follows:

- If the divisor is nonzero, divide the absolute values, truncate the magnitude toward zero, then apply the sign determined by the operand signs.
- Formally, for signed integers `n` and `d != 0`:
  - `q_mag = floor(abs(n) / abs(d))`
  - `q = q_mag` when `n` and `d` have the same sign, else `q = -q_mag`

Saturation to signed 32-bit range is defined exactly as:

- if `x > 2147483647`, use `2147483647`
- else if `x < -2147483648`, use `-2147483648`
- else use `x`

No special NaN, infinity, or invalid encodings exist because all ports are integers.

Worked example:

- `P = 0x40000000` (`+0.5`)
- `H = 0x40000000` (`+0.5`)
- `R = 0x20000000` (`+0.25`)
- `hp_q31 = 0x20000000` (`+0.25`)
- `hph_q31 = 0x10000000` (`+0.125`)
- `s = 0x30000000` (`+0.375`)
- `si = sat32(trunc_toward_zero(2^62 / 0x30000000)) = 0x7FFFFFFF` (the reciprocal exceeds signed Q31)
- `k = trunc_toward_zero((0x20000000 << 31) / 0x30000000) = 0x55555555`

## Reset Behavior

The block contains no architectural state beyond any implementation-specific handshake/pipeline registers.

During reset:

- `in_ready` shall be `0`
- `out_valid` shall be `0`
- `out_s` shall be `0`
- `out_si` shall be `0`
- `out_k` shall be `0`

After reset is deasserted, the first accepted input transaction is processed according to the Functional Behavior section with no dependence on any earlier history.
