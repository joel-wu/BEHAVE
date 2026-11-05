# AdcPwmSamplePlanner

Plan voltage/current ADC sample positions for one PWM timer context, retaining
current-channel positions when an undefined bipolar commutation step leaves
them unwritten. DC, detection, bipolar and non-bipolar operation are supported.
Shunt count and both double-sampling options are explicit configuration bits.
This block computes timer counts; it does not generate PWM or ADC trigger pulses.

## Interface

### RTL interface

```systemverilog
module AdcPwmSamplePlanner (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_top,
    input logic [31:0] in_duty,
    input logic [16:0] in_max_duty_uq16,
    input logic in_motor_type_dc,
    input logic in_pwm_mode_bipolar,
    input logic [2:0] in_comm_step,
    input logic in_direction,
    input logic in_is_detecting,
    input logic in_has_3_shunts,
    input logic in_curr1_double_sample,
    input logic in_curr2_double_sample,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_val_sample,
    output logic [31:0] out_curr1_sample,
    output logic [31:0] out_curr2_sample,
    output logic [31:0] out_curr3_sample,
    output logic [2:0] out_curr_samp_volt
);
endmodule
```

### Behavior interface

```python
class AdcPwmSamplePlanner:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_top, in_duty, in_max_duty_uq16, in_motor_type_dc, in_pwm_mode_bipolar, in_comm_step, in_direction, in_is_detecting, in_has_3_shunts, in_curr1_double_sample, in_curr2_double_sample): ...
```

Each `process` call consumes one accepted input transaction, not a clock cycle.
Arguments are unsigned bit patterns of their RTL port widths. It always returns
one dictionary with exactly `out_val_sample`, `out_curr1_sample`,
`out_curr2_sample`, `out_curr3_sample` (unsigned 32-bit integers) and
`out_curr_samp_volt` (an unsigned 3-bit integer), never `None` or a list.
Keep the same instance across calls: retained channel positions follow the
state rules below. Construction initializes them to zero; `reset()` restores
that state, returns `None` and produces no output record.

## Modules

- `AdcPwmSamplePlanner`: one stateful sample-position planner.

## Functional Behavior

All timer counts are unsigned 32-bit words. `in_max_duty_uq16` is an unsigned
17-bit fraction with 16 fractional bits; 65536 represents 1. Clamp larger
values to 65536. Set `D = min(in_duty, floor(in_top * fraction / 65536))`,
using the full product, and `T = in_top`. Thus `0 <= D <= T`.
All remaining count additions and subtractions wrap modulo 2^32; divisions
by 2 or 4 floor their nonnegative argument. In particular, `T-10`, `T-2`,
`T-70`, `T-230` are unsigned wrapped differences, not saturating subtraction.
All bit patterns are defined; a count beyond `T` is a register value, not
a guarantee of a physically usable ADC window.

Let `P = D + floor((T-D)/2)`, `Z = (T-2) mod 2^32`, and
`C = min(P,(T-70) mod 2^32)`. Let `mask` be the three-bit voltage-sampling
selection mask, with bit 0/1/2 corresponding to current channel 1/2/3.
Apply the first matching mode below:

1. **DC** (`in_motor_type_dc=1`): channels 1 and 2 use `(T-10) mod 2^32`.
   Voltage position is `floor(D/2)` and mask is 0 if `D>1000`; otherwise
   voltage position is `D+800` and mask is 7. Channel 3 matches channel 1
   when three shunts are enabled, and is zero otherwise.
2. **Detect** (`in_is_detecting=1`): voltage position is `floor(D/2)`,
   channels 1 and 2 use `P`, mask is 0, and channel 3 is `P` if enabled.
3. **Bipolar** (`in_pwm_mode_bipolar=1`): voltage position is `floor(T/4)`;
   channel 3 is `Z` if enabled. Channels 1/2 and mask follow this table:

| step | direction 0: channel 1, channel 2, mask | direction 1: channel 1, channel 2, mask |
| --- | --- | --- |
| 1 | `Z, P, 0` | `Z, Z, 2` |
| 2 | `P, Z, 0` | `P, Z, 2` |
| 3 | `P, Z, 2` | `P, Z, 0` |
| 4 | `Z, Z, 2` | `Z, P, 0` |
| 5 | `Z, Z, 1` | `Z, P, 1` |
| 6 | `Z, P, 1` | `Z, Z, 1` |
| 0 or 7 | retain previous channels 1/2, mask 0 | retain previous channels 1/2, mask 0 |

4. **Other non-bipolar BLDC operation**: voltage position is `floor(D/2)`;
   channels 1 and 2 initially use `C`, channel 3 uses `C` if enabled, and
   mask is initially 0. If `D > floor(T/2)`, apply both following rules:
   - Override channel 1 with `(D+90) mod 2^32` and channel 2 with
     `(T-230) mod 2^32` when either (a) `in_curr1_double_sample=1` and step
     is 2 or 3, or (b) `in_curr2_double_sample=1` and step is 4 or 5 for
     direction 1, or 1 or 6 for direction 0. These options are independent
     of shunt count. Channel 3 remains at its initial position.
   - Set mask using the following table (0 for steps 0 or 7):

| step | two shunts, direction 0 | two shunts, direction 1 | three shunts, direction 0 | three shunts, direction 1 |
| --- | --- | --- | --- | --- |
| 1 | 1 | 3 | 3 | 5 |
| 2 | 2 | 2 | 6 | 6 |
| 3 | 2 | 2 | 6 | 6 |
| 4 | 3 | 1 | 5 | 3 |
| 5 | 3 | 1 | 5 | 3 |
| 6 | 1 | 3 | 3 | 5 |

`in_has_3_shunts=0` makes channel 3 output zero in every mode. Configuration
is sampled on each accepted input and may change between inputs. Channels
1/2 from the last accepted input are the retained values for the next one.
All other outputs are recomputed; there is no hidden previous mask.

One accepted input (`in_valid && in_ready` on a rising clock edge) produces
one ordered output. State changes exactly once on acceptance. Hold valid
output data unchanged until `out_ready` accepts it; no fixed latency is
required. Offered input data remains stable while stalled.

## Reset Behavior

`rst` is asynchronous, active high. It clears both retained channel positions,
all output data and the pending-output flag to zero. During reset
`in_ready=0` and `out_valid=0`; no transfers occur. An interrupted output is
discarded. The next accepted input starts from the zero retained positions.
