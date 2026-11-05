# Control BLDC Drive BLDC 6-Step Hall Commutation

This block computes one 6-step trapezoidal BLDC commutation command per accepted input transaction. It supports 120-degree and 150-degree trapezoidal phase maps, derives the commutation sector from an electrical angle represented as an unsigned 16-bit turn counter over one full electrical revolution, selects either fixed half-bus centering or adaptive centering, and outputs three per-phase Q15 voltage commands plus three phase-enable controls. The block is purely combinational at the transaction level: one accepted input produces one output.

## Interface

### RTL interface

```systemverilog
module control_bldc_drive_bldc_6step_hall_commutation (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_uq,
    input  logic [15:0] in_voltage_limit,
    input  logic [15:0] in_angle_el,
    input  logic        in_mode_150,
    input  logic        in_modulation_centered,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_ua,
    output logic [15:0] out_ub,
    output logic [15:0] out_uc,
    output logic        out_phase_a_en,
    output logic        out_phase_b_en,
    output logic        out_phase_c_en
);
```

### Behavior interface

```python
class ControlBldcDriveBldc6stepHallCommutation:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_uq, in_voltage_limit, in_angle_el, in_mode_150, in_modulation_centered): ...
```

Call `process` once per accepted input. It returns one dictionary with exactly
`out_ua`, `out_ub`, `out_uc`, `out_phase_a_en`, `out_phase_b_en`, and
`out_phase_c_en`. All values are Python integers carrying unsigned port bit
patterns: encode negative Q15 voltages in 16-bit two's complement and phase
enables as 0 or 1. Zero voltages still produce one output record.

## Modules

- `control_bldc_drive_bldc_6step_hall_commutation`: top-level commutation block.

## Functional Behavior

All arithmetic is integer fixed-point.

- `in_uq` is Q15 signed 16-bit, two's complement.
- `in_voltage_limit` is Q15 signed 16-bit, two's complement.
- `in_angle_el` is an unsigned 16-bit electrical angle covering one full turn. `0x0000` and `0x10000 mod 2^16` represent the same angle.
- `in_mode_150 = 0` selects the 120-degree trapezoidal map.
- `in_mode_150 = 1` selects the 150-degree trapezoidal map.
- `in_modulation_centered = 1` selects fixed centering.
- `in_modulation_centered = 0` selects adaptive centering.

Signed Q15 examples:
- `0x4000` = +0.5
- `0x8000` = -1.0

Let:

- `uq = signed(in_uq)` in [-32768, 32767]
- `voltage_limit = signed(in_voltage_limit)` in [-32768, 32767]
- `angle = unsigned(in_angle_el)` in [0, 65535]

### Center voltage

The signed integer `center` is:

- if `in_modulation_centered = 1`, `center = voltage_limit >>> 1`
- else `center = uq`

`>>> 1` is arithmetic right shift by one on the signed integer value. Examples:
- `3 >>> 1 = 1`
- `-3 >>> 1 = -2`

No saturation is applied to `center` itself.

### Sector selection

The electrical angle is offset by 30 degrees before sector decoding.

Define:

- `angle_off = (angle + 0x1555) mod 65536`

`0x1555` is the 16-bit truncated unsigned phase increment for 30 degrees.

The sector index is computed with unsigned integer multiply followed by truncating right shift:

- in 120-degree mode, `sector = (angle_off * 6) >> 16`, producing a value in [0, 5]
- in 150-degree mode, `sector = (angle_off * 12) >> 16`, producing a value in [0, 11]

### Trapezoidal maps

Each sector selects a 3-element map entry for phases A, B, and C. Entry symbols are:

- `+1`: phase voltage command is `center + uq`, phase enabled
- `-1`: phase voltage command is `center - uq`, phase enabled
- `0`: phase voltage command is `center`, phase disabled

The 120-degree map is:

- sector 0: `[0, +1, -1]`
- sector 1: `[-1, +1, 0]`
- sector 2: `[-1, 0, +1]`
- sector 3: `[0, -1, +1]`
- sector 4: `[+1, -1, 0]`
- sector 5: `[+1, 0, -1]`

The 150-degree map is:

- sector 0:  `[0, +1, -1]`
- sector 1:  `[-1, +1, -1]`
- sector 2:  `[-1, +1, 0]`
- sector 3:  `[-1, +1, +1]`
- sector 4:  `[-1, 0, +1]`
- sector 5:  `[-1, -1, +1]`
- sector 6:  `[0, -1, +1]`
- sector 7:  `[+1, -1, +1]`
- sector 8:  `[+1, -1, 0]`
- sector 9:  `[+1, -1, -1]`
- sector 10: `[+1, 0, -1]`
- sector 11: `[+1, +1, -1]`

### Per-phase output computation

Let the selected map entry be `(ma, mb, mc)`.

For each phase symbol `m`:

- if `m = 0`, raw phase voltage = `center`
- if `m = +1`, raw phase voltage = `center + uq`
- if `m = -1`, raw phase voltage = `center - uq`

Each raw phase voltage is then saturated to the signed 16-bit range [-32768, 32767]:

- if `v > 32767`, saturated value = 32767
- else if `v < -32768`, saturated value = -32768
- else saturated value = `v`

`out_ua`, `out_ub`, and `out_uc` are the saturated signed values encoded as 16-bit two's-complement bit patterns.

### Phase enable outputs

Phase enables depend only on whether the corresponding map entry is high impedance:

- `out_phase_a_en = 0` if `ma = 0`, else `1`
- `out_phase_b_en = 0` if `mb = 0`, else `1`
- `out_phase_c_en = 0` if `mc = 0`, else `1`

In 120-degree mode exactly one phase enable is `0`. In 150-degree mode either one phase enable is `0` or all three are `1`.

## Reset Behavior

Reset is synchronous active-high `rst`.

The block has no architectural state.

Idle output values after reset and before any output transaction are:

- `out_valid = 0`
- `out_ua = 16'h0000`
- `out_ub = 16'h0000`
- `out_uc = 16'h0000`
- `out_phase_a_en = 1'b0`
- `out_phase_b_en = 1'b0`
- `out_phase_c_en = 1'b0`

A correct implementation may keep `in_ready = 1` continuously.
