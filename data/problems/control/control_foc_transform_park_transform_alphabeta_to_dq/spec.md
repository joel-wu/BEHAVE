# control_foc_transform_park_transform_alphabeta_to_dq

This block performs the Park transform used in field-oriented control. For each accepted input transaction, it converts a stationary-frame current vector `(alpha, beta)` into a rotor-frame current vector `(d, q)` using the supplied electrical angle. All signal values use signed fixed-point Q15 arithmetic. The angle-dependent sine and cosine are generated internally from a 65-entry first-quadrant Q15 lookup table with quadrant folding; the transform then computes `d = cos(theta)*alpha + sin(theta)*beta` and `q = cos(theta)*beta - sin(theta)*alpha`, with each multiply narrowed from Q30 to Q15 by arithmetic right shift of 15 bits truncating toward negative infinity, and each final sum saturated to the signed 16-bit Q15 range.

## Interface

### RTL interface

```systemverilog
module control_foc_transform_park_transform_alphabeta_to_dq (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_alpha,
    input  logic [15:0]  in_beta,
    input  logic [15:0]  in_theta,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_d,
    output logic [15:0]  out_q
);
```

### Behavior interface

```python
class ControlFocTransformParkTransformAlphabetaToDq:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_alpha, in_beta, in_theta): ...
```

Each call represents one accepted input transaction and returns exactly
`{"out_d": d_bits, "out_q": q_bits}`. All arguments and returned values are
unsigned 16-bit port encodings: `in_alpha`, `in_beta`, `out_d` and `out_q`
encode signed Q15 values, while `in_theta` encodes the full-turn phase defined
below. Do not pass or return Python floats or negative encoded words.
Every call returns the complete dictionary, including zero results, never
`None` or a list. No handshake signals are returned. There is no state between
transactions; construction and `reset()` establish this empty state, and
`reset()` returns `None`.

## Modules

- `control_foc_transform_park_transform_alphabeta_to_dq`: top-level module implementing angle lookup, Park transform multiply-accumulate, and Q15 saturation.

## Functional Behavior

The data inputs and outputs are signed Q15; the angle is a 16-bit full-turn phase code.

- `in_alpha` is Q15: real value = `raw / 2^15`.
- `in_beta` is Q15: real value = `raw / 2^15`.
- `in_theta` is a signed 16-bit angle code interpreted modulo `2^16` as one full electrical turn. The unsigned phase code `theta_u = in_theta & 16'hFFFF` maps:
  - `0x0000` to `0` radians,
  - `0x4000` to `pi/2`,
  - `0x8000` to `pi`,
  - `0xC000` to `3*pi/2`.

The block is stateless at the transaction level and produces exactly one output transaction for each accepted input transaction.

Angle-to-sine/cosine generation uses a fixed 65-entry first-quadrant Q15 sine table addressed at uniform quarter-wave steps. The table is defined by `T[i] = min(32767, floor(32768*sin(pi*i/128)+0.5))`, for i=0..64. Its exact integer contents are:

```text
0, 804, 1608, 2411, 3212, 4011, 4808, 5602, 6393, 7180, 7962, 8740, 9512, 10279, 11039, 11793, 12540, 13279, 14010, 14733, 15447, 16151, 16846, 17531, 18205, 18868, 19520, 20160, 20788, 21403, 22006, 22595, 23170, 23732, 24279, 24812, 25330, 25833, 26320, 26791, 27246, 27684, 28106, 28511, 28899, 29269, 29622, 29957, 30274, 30572, 30853, 31114, 31357, 31581, 31786, 31972, 32138, 32286, 32413, 32522, 32610, 32679, 32729, 32758, 32767
```

The lookup procedure is:

1. Form `theta_u = in_theta & 16'hFFFF`.
2. Extract quadrant `quad = theta_u[15:14]`.
3. Extract the position within the quadrant `frac = theta_u[13:0]`, an unsigned 14-bit value in `[0, 16383]`.
4. Compute the table step index inside the quadrant as `idx = frac >> 8`, giving an integer in `[0, 63]`.
5. Determine first-quadrant sine and cosine magnitudes:
   - If `quad` is `0`:
     - `sin_mag = table[idx]`
     - `cos_mag = table[64 - idx]`
   - If `quad` is `1`:
     - `sin_mag = table[64 - idx]`
     - `cos_mag = table[idx]`
   - If `quad` is `2`:
     - `sin_mag = table[idx]`
     - `cos_mag = table[64 - idx]`
   - If `quad` is `3`:
     - `sin_mag = table[64 - idx]`
     - `cos_mag = table[idx]`
6. Apply signs by quadrant:
   - `quad = 0`: `sin = +sin_mag`, `cos = +cos_mag`
   - `quad = 1`: `sin = +sin_mag`, `cos = -cos_mag`
   - `quad = 2`: `sin = -sin_mag`, `cos = -cos_mag`
   - `quad = 3`: `sin = -sin_mag`, `cos = +cos_mag`

`sin` and `cos` are signed Q15 integers in the range `[-32767, 32767]`. No interpolation is performed between table entries.

Interpret `in_alpha` and `in_beta` as signed 16-bit integers `alpha` and `beta`.

Compute four Q30 products using signed integer multiplication:

- `p_ca = cos * alpha`
- `p_sb = sin * beta`
- `p_cb = cos * beta`
- `p_sa = sin * alpha`

Narrow each product from Q30 to Q15 by arithmetic right shift 15 bits:

- `t_ca = p_ca >>> 15`
- `t_sb = p_sb >>> 15`
- `t_cb = p_cb >>> 15`
- `t_sa = p_sa >>> 15`

This narrowing truncates toward negative infinity, matching a two's-complement arithmetic shift. Example:
- if `cos = 0x4000` (`+0.5`), `alpha = 0x4000` (`+0.5`), then `p_ca = 268435456`, `t_ca = 8192` (`0x2000`, `+0.25`);
- if `cos = 0xC000` (`-0.5`), `alpha = 0x4000` (`+0.5`), then `p_ca = -268435456`, `t_ca = -8192` (`0xE000`, `-0.25`).

Form the pre-saturation Q15 sums:

- `d_pre = t_ca + t_sb`
- `q_pre = t_cb - t_sa`

Saturate each result to the signed 16-bit range:

- if `d_pre > 32767`, `d_sat = 32767`
- else if `d_pre < -32768`, `d_sat = -32768`
- else `d_sat = d_pre`

- if `q_pre > 32767`, `q_sat = 32767`
- else if `q_pre < -32768`, `q_sat = -32768`
- else `q_sat = q_pre`

Return:
- `out_d = d_sat` as a 16-bit two's-complement bit pattern
- `out_q = q_sat` as a 16-bit two's-complement bit pattern

Special cases:
- `in_theta` is always wrapped modulo `2^16` before quadrant extraction; negative signed encodings are treated by their raw 16-bit pattern.
- `in_alpha = -32768` and `in_beta = -32768` are legal inputs.
- Because the sine table maximum is `32767`, the transform never uses a coefficient of `-32768`.
- No undefined cases exist for any 16-bit input combination.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state: none.

Output and handshake state after reset and before the first accepted input:
- `in_ready = 1`
- `out_valid = 0`
- `out_d = 16'h0000`
- `out_q = 16'h0000`

Because the block is stateless, reset does not alter any stored data beyond forcing the output interface to idle.
