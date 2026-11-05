# EncoderObserverHysteresis

Select an encoder or observer angle using a persistent five-percent speed hysteresis. Speed is in radians/second and the threshold in revolutions/minute; a declared integer conversion connects them. The selected angle is returned unchanged.

## Interface

### RTL interface

```systemverilog
module EncoderObserverHysteresis (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [31:0] in_obs_angle,
    input logic signed [31:0] in_enc_angle,
    input logic signed [31:0] in_speed_rad_q16,
    input logic [31:0] in_threshold_rpm_q16,
    output logic out_valid,
    input logic out_ready,
    output logic signed [31:0] out_angle,
    output logic out_using_encoder
);
endmodule
```

### Behavior interface

```python
class EncoderObserverHysteresis:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_obs_angle, in_enc_angle, in_speed_rad_q16, in_threshold_rpm_q16): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_angle": ..., "out_using_encoder": ...}`. Returned fields are Python
integers holding unsigned port bit patterns; the selection flag is `0` or `1`.
Reset is provided separately by `reset`.

## Modules

- `EncoderObserverHysteresis`: the stateful command/selection block.

## Functional Behavior

`in_obs_angle`, `in_enc_angle` and `out_angle` are 32-bit binary angles:
one revolution is 2^32 counts; signed interpretation maps [-2^31,2^31)
to [-pi,pi). Selection preserves all 32 bits. There is no offset addition,
interpolation or angle arithmetic.

`in_speed_rad_q16` is signed two's-complement, raw/65536 radians/second.
`in_threshold_rpm_q16` is unsigned, raw/65536 revolutions/minute. All bit
patterns are defined. Take the exact integer magnitude `a` of speed,
including `abs(-2^31) = 2^31`, and let `T` be the unsigned threshold.
The rad/s-to-RPM multiplier is exactly `10013163 / 1048576`, the
nearest 20-fractional-bit approximation to `60/(2*pi)`.

Using mathematical integers without intermediate saturation or wrap, define
`r = 20*a*10013163`, `lo = 19*T*1048576`, and `hi = 21*T*1048576`.
For the persistent bit `using_encoder`:

- If it is 1 and `r > hi`, clear it.
- If it is 0 and `r < lo`, set it.
- Otherwise retain it, including equality to either boundary.

Use the updated bit to select `in_enc_angle` when 1 and `in_obs_angle`
when 0. Report it as `out_using_encoder`. At threshold zero, reset's
observer mode is retained; an already-set bit clears on nonzero speed
but remains set at exactly zero speed. Changing the threshold is allowed
on each accepted transaction.

Each accepted input (`in_valid && in_ready` on a rising clock edge)
produces exactly one ordered output. State changes once per accepted input,
never for an unaccepted offer. Output valid and all output data remain stable
until accepted with `out_ready`. No fixed latency is required. Inputs are
stable while offered and stalled. There is no output without an input.

## Reset Behavior

`rst` is synchronous, active high. Each rising edge with `rst=1` clears
all architectural state and output registers to zero and discards any pending
output. During reset `in_ready=0` and `out_valid=0`; no transaction transfers.
The first post-reset command starts from this cleared state.
