# MaxUnpool8

Unpool eight byte values into sixteen byte positions. Input value i occupies byte i and its unsigned output index occupies nibble i. Begin with sixteen zeros, then write input values in index order i=0..7 to their requested positions. For repeated destinations the LAST input wins, matching the archived reference loop as an explicit deterministic extension where portable MaxUnpool collision ordering is not assumed. Unwritten positions stay zero. This is assignment, not sum or maximum.

## Interface

### RTL interface

```systemverilog
module MaxUnpool8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_data,
    input logic [31:0] in_indices,
    output logic [127:0] out_values
);
endmodule
```

Data buses are unsigned
bit patterns; each signed lane is two's complement. Lane i occupies
`[bits_per_lane*i +: bits_per_lane]`; flat indices are zero-based. Unused
padding bits do not exist. Every input bit pattern has a defined result.

### Behavior interface

```python
class MaxUnpool8:
    def reset(self): ...
    def process(self, in_data, in_indices): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_values": packed_result}`, where `packed_result` is a Python integer
holding the packed output port bit pattern, not a list of output elements.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`MaxUnpool8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Unpool eight byte values into sixteen byte positions. Input value i occupies byte i and its unsigned output index occupies nibble i. Begin with sixteen zeros, then write input values in index order i=0..7 to their requested positions. For repeated destinations the LAST input wins, matching the archived reference loop as an explicit deterministic extension where portable MaxUnpool collision ordering is not assumed. Unwritten positions stay zero. This is assignment, not sum or maximum.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked packing example

A byte sequence `[1,2,3,4]` is packed as `0x04030201`; the low byte is element
zero. Signed byte `-1` is `0xff`. This example specifies packing, not a
restriction to small positive inputs.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
