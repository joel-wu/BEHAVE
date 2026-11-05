# DualDelayHysteresis

Boolean control hysteresis with independent rise/fall confirmation delays and cancellation.

## Interface

### RTL interface

```systemverilog
module DualDelayHysteresis (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [0:0] in_request,
    input logic [3:0] in_rise_delay,
    input logic [3:0] in_fall_delay,
    output logic [0:0] out_values
);
endmodule
```

### Behavior interface

```python
class DualDelayHysteresis:
    def reset(self): ...
    def process(self, in_request, in_rise_delay, in_fall_delay): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_values": packed_result}`, where `packed_result` is a Python integer
holding the packed output port bit pattern, not a list of fields. Field
positions and numerical interpretation are defined below.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`DualDelayHysteresis` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Boolean control hysteresis with independent rise/fall confirmation delays and cancellation.

Each accepted request represents one successive logical control tick t=0,1,2,..., not one RTL clock cycle. Rise/fall delays are integer tick counts 0..15. The state starts false. When a request first differs from the current state, start a pending transition at that tick. Repeating the same request does not restart its timer. On every tick, use the currently supplied delay for the transition direction: if elapsed ticks since the pending request began are >= that delay, change state immediately. A request equal to the current state cancels the pending transition. A later opposite request starts a new timer. Delay zero changes state on the first opposite request; delay d requires d+1 consecutive opposite requests when unchanged.

Return the Boolean state AFTER this tick in out_values[0]. Delay changes while pending apply immediately but do not reset elapsed time. In particular lowering a delay can complete a pending transition on that tick. Only accepted requests advance logical time: idle/stalled hardware cycles have no effect. This is the pinned PX4 Hysteresis(false) API sampled at consecutive integer timestamps, with set_hysteresis_time_from(false,rise) and set_hysteresis_time_from(true,fall) before each set_state_and_update(request,t). It is temporal confirmation, not a threshold comparator or a metastability synchronizer.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

From reset, request sequence `[1,1,1,0,0]` with rise delay 2 and fall delay 1 returns `[0,0,1,1,0]`. With rise delay 2, `[1,1,0,1,1,1]` returns `[0,0,0,0,0,1]`: the intervening zero cancels the first pending transition.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. Reset restores false state and cancels pending transitions. No physical-cycle or host-wall-clock timer is implied.
