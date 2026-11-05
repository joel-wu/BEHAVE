# MaxPoolBackwardOverlap4

Backward propagation through overlapping max pooling, including tied maxima and gradient accumulation.

## Interface

### RTL interface

```systemverilog
module MaxPoolBackwardOverlap4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_x,
    input logic [63:0] in_grad,
    output logic [511:0] out_values
);
endmodule
```

### Behavior interface

```python
class MaxPoolBackwardOverlap4:
    def reset(self): ...
    def process(self, in_x, in_grad): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 512.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Data buses are unsigned
bit patterns; each signed lane is two's complement. Lane i occupies
`[bits_per_lane*i +: bits_per_lane]`; flat indices are zero-based. Any unused output slots are zero as defined below. Every input bit pattern has a defined result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`MaxPoolBackwardOverlap4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Backward propagation through overlapping max pooling, including tied maxima and gradient accumulation.

Input X is 4x4 signed INT8, and G is 2x2 signed INT16. Forward windows are 3x3, stride one, no padding. Each window selects its first maximum in row-major order (strict greater updates). Add its G value to that selected input position. Return the full 4x4 signed INT32 gradient, row-major; other positions are zero. Multiple windows may select the same position; their gradients must add, not overwrite.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

A unique value 127 at X[1,1], all others zero, is selected by all four windows. G=[1,2,3,4] gives dX[1,1]=10 and all other gradients zero.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
