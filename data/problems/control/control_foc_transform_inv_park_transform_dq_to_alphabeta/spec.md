# Inverse Park Transform dq to alphabeta

This module converts one rotating-frame vector `(d, q)` into one stationary-frame vector `(alpha, beta)` using the inverse Park transform. All data is fixed-point integer Q15. The transform uses the equations `alpha = cos(theta) * d - sin(theta) * q` and `beta = cos(theta) * q + sin(theta) * d`. `sin(theta)` and `cos(theta)` are generated from a 65-entry first-quadrant Q15 lookup table with quadrant folding over an unsigned 16-bit electrical angle, where one full turn equals 65536 angle counts.

## Interface

### RTL interface

```systemverilog
module control_foc_transform_inv_park_transform_dq_to_alphabeta (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_d,
    input  logic [15:0] in_q,
    input  logic [15:0] in_theta,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_alpha,
    output logic [15:0] out_beta
);
```

`in_d` and `in_q` are signed Q15 values carried on 16-bit two's-complement ports.  
`in_theta` is an unsigned 16-bit electrical angle over `[0, 65535]`, with `0x0000 = 0 rad`, `0x4000 = +pi/2`, `0x8000 = pi`, and `0xC000 = 3pi/2`.  
`out_alpha` and `out_beta` are signed Q15 values carried on 16-bit two's-complement ports.

### Behavior interface

```python
class ControlFocTransformInvParkTransformDqToAlphabeta:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_d, in_q, in_theta):
        pass
```

Each `process(in_d, in_q, in_theta)` call represents one accepted request and
returns exactly `{"out_alpha": alpha_bits, "out_beta": beta_bits}`. All
arguments and return values are unsigned 16-bit integers; signed Q15 values
use their two's-complement encodings, not Python floats or negative integers.
Every request returns the complete dictionary, never `None` or a list.
There is no persistent arithmetic state; `reset()` returns `None` and emits
no result.

## Modules

- `control_foc_transform_inv_park_transform_dq_to_alphabeta`: top-level inverse Park transform block.

## Functional Behavior

All arithmetic uses signed two's-complement integers unless stated otherwise.

`in_d` and `in_q` represent Q15 values:
- real value = raw integer / 2^15
- `0x4000` = `+0.5`
- `0x8000` = `-1.0`

The operation for one accepted input transaction is:

1. Interpret `in_d` and `in_q` as signed 16-bit integers.
2. Interpret `in_theta` as an unsigned 16-bit angle count modulo 65536.
3. Compute `sin(theta)` and `cos(theta)` as signed Q15 values from the angle using the lookup-table method defined below.
4. Compute:
   - `alpha_tmp = q15_mul(cos_theta, d) - q15_mul(sin_theta, q)`
   - `beta_tmp  = q15_mul(cos_theta, q) + q15_mul(sin_theta, d)`
5. Saturate each result to the signed 16-bit range `[-32768, 32767]`.
6. Drive the saturated results on `out_alpha` and `out_beta` as unsigned 16-bit two's-complement bit patterns.

### Q15 multiply

`q15_mul(a, b)` is defined as follows:

- Inputs `a` and `b` are signed 16-bit integers.
- Form the signed product `prod_q30 = a * b`.
- Convert from Q30 to Q15 by arithmetic right shift by 15 bits.
- The shift rounds toward negative infinity, matching two's-complement arithmetic shift.
- Saturate the shifted result to `[-32768, 32767]`.

Formally:
- `prod_q30 = a * b`
- `shifted = prod_q30 >> 15`
- if `shifted > 32767`, result = `32767`
- else if `shifted < -32768`, result = `-32768`
- else result = `shifted`

### Sine/cosine generation

The trigonometric outputs are derived from a first-quadrant sine table with 65 entries indexed from 0 to 64 inclusive. Entry values are signed Q15 integers for `sin(i * pi / 128)`.

The table is defined by `T[i] = min(32767, floor(32768*sin(pi*i/128)+0.5))`, for i=0..64. Its exact integer contents are:

```text
0, 804, 1608, 2411, 3212, 4011, 4808, 5602, 6393, 7180, 7962, 8740, 9512, 10279, 11039, 11793, 12540, 13279, 14010, 14733, 15447, 16151, 16846, 17531, 18205, 18868, 19520, 20160, 20788, 21403, 22006, 22595, 23170, 23732, 24279, 24812, 25330, 25833, 26320, 26791, 27246, 27684, 28106, 28511, 28899, 29269, 29622, 29957, 30274, 30572, 30853, 31114, 31357, 31581, 31786, 31972, 32138, 32286, 32413, 32522, 32610, 32679, 32729, 32758, 32767
```

Angle decode uses the 16-bit input as:
- `quadrant = in_theta[15:14]`
- `offset = in_theta[13:8]`

`offset` therefore ranges from 0 to 63 and selects one of 64 equal steps within a quadrant. The extra table entry 64 is used for mirrored cosine endpoints.

For each quadrant, sine and cosine magnitudes are selected from the table without interpolation:

- Quadrant 0 (`0 <= theta < pi/2`):
  - `sin_mag = SIN_Q15_LUT[offset]`
  - `cos_mag = SIN_Q15_LUT[64 - offset]`
  - `sin_theta = +sin_mag`
  - `cos_theta = +cos_mag`

- Quadrant 1 (`pi/2 <= theta < pi`):
  - `sin_mag = SIN_Q15_LUT[64 - offset]`
  - `cos_mag = SIN_Q15_LUT[offset]`
  - `sin_theta = +sin_mag`
  - `cos_theta = -cos_mag`

- Quadrant 2 (`pi <= theta < 3pi/2`):
  - `sin_mag = SIN_Q15_LUT[offset]`
  - `cos_mag = SIN_Q15_LUT[64 - offset]`
  - `sin_theta = -sin_mag`
  - `cos_theta = -cos_mag`

- Quadrant 3 (`3pi/2 <= theta < 2pi`):
  - `sin_mag = SIN_Q15_LUT[64 - offset]`
  - `cos_mag = SIN_Q15_LUT[offset]`
  - `sin_theta = -sin_mag`
  - `cos_theta = +cos_mag`

This definition makes the trigonometric outputs piecewise-constant over angle bins of width 256 counts.

### Output saturation

After forming `alpha_tmp` and `beta_tmp`:
- if value > 32767, output 32767
- else if value < -32768, output -32768
- else output value unchanged

Worked example:
- if `d = 0x4000` (`+0.5`), `q = 0x0000`, `theta = 0x0000`
- then `cos_theta = 0x7FFF`, `sin_theta = 0x0000`
- `out_alpha = 0x3FFF`
- `out_beta = 0x0000`

## Reset Behavior

The module has no architectural state beyond the handshake-facing output
registers required by the implementation. Reset `rst` is asynchronous and
active-high. Assertion immediately cancels any outstanding transaction and
sets the values below, without waiting for a rising clock edge:
- `out_valid = 0`
- `out_alpha = 0`
- `out_beta = 0`

After reset is deasserted, the next accepted input transaction is processed independently of all earlier transactions.
