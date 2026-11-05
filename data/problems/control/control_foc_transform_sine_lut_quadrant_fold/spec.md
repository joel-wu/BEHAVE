# Control FOC Transform Sine LUT Quadrant Fold

This block computes sine and cosine using a 65-entry first-quadrant table, quadrant folding and linear interpolation. A 16-bit unsigned phase represents one full turn. Each output is signed 17-bit with 15 fractional bits, representing both -1 and +1 exactly. One accepted input transaction produces exactly one output transaction.

## Interface

### RTL interface

```systemverilog
module control_foc_transform_sine_lut_quadrant_fold (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_angle,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [16:0] out_sin,
    output logic signed [16:0] out_cos
);
```

### Behavior interface

```python
class ControlFocTransformSineLutQuadrantFold:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_angle): ...
```

Each call represents one accepted phase and returns exactly
`{"out_sin": sin_bits, "out_cos": cos_bits}`. `in_angle` is an unsigned
16-bit integer. Both returned values are unsigned 17-bit encodings of the
signed outputs, not Python floats or negative encoded words. Zero results
still return the complete dictionary, never `None` or a list. No handshake
signals are returned. There is no state between requests; construction and
`reset()` establish this empty state, and `reset()` returns `None`.

## Modules

- `control_foc_transform_sine_lut_quadrant_fold`: phase-to-sine/cosine operator.

## Functional Behavior

Transfers occur on rising edges with valid and ready both high. Both output fields remain valid and unchanged under backpressure; accepted inputs produce ordered responses.

All 65536 phase codes are legal. `in_angle = p` represents `2*pi*p/65536` radians, modulo a full turn. Signed output values are decoded as raw/32768. Thus +1 is `17'h08000` and -1 is `17'h18000`; neither is saturated or wrapped to the opposite sign.

Use this exact table:
```text
LUT = [0,804,1608,2411,3212,4011,4808,5602,6393,7180,7962,8740,
       9512,10279,11039,11793,12540,13279,14010,14733,15447,16151,
       16846,17531,18205,18868,19520,20160,20788,21403,22006,22595,
       23170,23732,24279,24812,25330,25833,26320,26791,27246,27684,
       28106,28511,28899,29269,29622,29957,30274,30572,30853,31114,
       31357,31581,31786,31972,32138,32286,32413,32522,32610,32679,
       32729,32758,32768]
```

For sine, set `q = p >> 14`, `i = (p >> 8) & 63` and `f = p & 255`. Select signed interpolation endpoints:

| q | t1 | t2 |
| --- | --- | --- |
| 0 | LUT[i] | LUT[i+1] |
| 1 | LUT[64-i] | LUT[63-i] |
| 2 | -LUT[i] | -LUT[i+1] |
| 3 | -LUT[64-i] | -LUT[63-i] |

Compute `sin_raw = t1 + floor((t2-t1)*f/256)` with widened signed arithmetic. Floor also applies to negative products, as with an arithmetic right shift. The result lies in [-32768,32768] and is encoded directly in signed 17 bits. Cosine applies the same function to `(p + 16384) mod 65536`.

The decoded raw landmark pairs (sin, cos) are:
- phase 0: (0,32768);
- phase 16384: (32768,0);
- phase 32768: (0,-32768);
- phase 49152: (-32768,0).

## Reset Behavior

Reset is synchronous active-high `rst`. A reset edge cancels any pending response and clears `out_valid`, `out_sin` and `out_cos` to zero. There is no retained algorithmic state. New transactions can be accepted after reset is released.
