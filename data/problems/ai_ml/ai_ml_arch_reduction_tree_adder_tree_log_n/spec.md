# Eight-Lane Signed Integer Reduction

Compute the exact sum of eight signed 16-bit integers.

## Interface

### RTL interface

```systemverilog
module eight_lane_sum (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [127:0] in_values,
    output logic out_valid, input logic out_ready,
    output logic [18:0] out_sum
);
```

### Behavior interface

```python
class EightLaneSum:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_values): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_sum": ...}`, whose value is a Python integer holding the specified
output port bit pattern. One call consumes all eight packed input lanes.

### Transaction protocol

One input
transfer produces one ordered output transfer. Transfers are sampled on rising
edges with valid and ready high. While output ready is low, a valid result and
all its fields remain unchanged. Arithmetic latency is not fixed.

## Modules

- `eight_lane_sum`: arithmetic and output holding.

## Functional Behavior

Sum eight signed 16-bit integers without arithmetic overflow. Lane i is bits
16*i+15:16*i of `in_values`, least-significant lane first. `out_sum` is the
19-bit two's-complement encoding of their exact mathematical sum, whose range
is -262144..262136. There is no saturation or intermediate truncation. All
lanes contribute. The task does not prescribe a reduction topology, pipeline or fixed latency.

## Reset Behavior

Reset is synchronous active-high and has priority over transfers. A reset edge
discards pending output, clears output valid and every output field, and makes
input ready. No arithmetic state survives between transactions.
