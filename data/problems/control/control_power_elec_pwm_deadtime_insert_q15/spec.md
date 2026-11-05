# Timer deadtime register encoding

Encode requested timer deadtime into the four-range DTG register field and report decoded ticks, saturation and invalid-clock status; no PWM waveform is generated.

## Interface

### RTL interface

```systemverilog
module control_power_elec_pwm_deadtime_insert_q15 (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [31:0] in_deadtime_ns, in_core_clock_hz,
    input logic out_ready,
    output logic out_valid,
    output logic [7:0] out_dtg,
    output logic [9:0] out_ticks,
    output logic out_saturated, out_error
);
```

### Behavior interface

```python
class ControlPowerElecPwmDeadtimeInsertQ15:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_deadtime_ns, in_core_clock_hz): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_dtg": ..., "out_ticks": ..., "out_saturated": ..., "out_error": ...}`,
including for saturation or an error. Returned fields are Python integers
holding the specified port bit patterns; both flags are `0` or `1`.

## Modules

Encode a requested deadtime into the four-range eight-bit DTG timer field used by VESC's conf_general_calculate_deadtime. This is a configuration calculation, not a PWM waveform generator or a six-step commutator. The historical task identifier is retained; no Q15 quantity remains in this interface.

## Functional Behavior

Inputs are unsigned 32-bit nanoseconds and clock frequency in hertz. Use the exact unsigned 64-bit product P=in_deadtime_ns*in_core_clock_hz, and G=1,000,000,000. The requested deadtime in clock periods is P/G. Clock frequency zero returns out_error=1 and the other outputs zero. Otherwise out_error=0 and:

| Condition | out_dtg |
| --- | --- |
| P <= 127G | floor(P/G) |
| 127G < P <= 254G | 128 + max(0, floor((P-128G)/(2G))) |
| 254G < P <= 504G | 192 + max(0, floor((P-256G)/(8G))) |
| 504G < P <= 1008G | 224 + max(0, floor((P-512G)/(16G))) |
| P > 1008G | 255 |

out_saturated is one exactly in the last row. The decoded deadtime out_ticks is code for code<128; (64+(code&63))*2 for code<192; (32+(code&31))*8 for code<224; otherwise (32+(code&31))*16. It ranges from 0 through 1008. This implements the source's range selection and truncation; range transitions may round up across a gap, whereas values inside a range are truncated down. It is not a guarantee that the encoded deadtime is at least the request.

This finite-width adaptation replaces the source's floating nanosecond arithmetic with the exact rational P/G; it does not claim identical floating rounding at every frequency. The range prefixes, thresholds and saturation are retained. Negative sub-range payloads at transition gaps truncate toward zero to zero, as in the selected source conversion. All arithmetic above is exact, with no wrap or fractional approximations.

Each accepted input yields one complete four-field output transaction. Transfers occur on rising clock edges with valid and ready high. Output valid and payload remain stable under backpressure; throughput and latency are implementation choices.

## Reset Behavior

Synchronous active-high reset discards a pending output and clears out_valid and all output fields. in_ready=1 after reset. There is no persistent numerical state, and idle output payloads are zero.
