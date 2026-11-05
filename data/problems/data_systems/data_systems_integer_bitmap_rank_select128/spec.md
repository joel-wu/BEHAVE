# BitmapRankSelect128

Inclusive rank and zero-based select on a 128-bit bitmap.

## Interface

### RTL interface

```systemverilog
module BitmapRankSelect128 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_bitmap,
    input logic [6:0] in_position,
    input logic [7:0] in_ordinal,
    output logic [23:0] out_values
);
endmodule
```

Ports carry unsigned
bit patterns. Field positions and any required zero padding are defined
below. Indices are zero-based. Every input bit pattern has a defined result.

### Behavior interface

```python
class BitmapRankSelect128:
    def reset(self): ...
    def process(self, in_bitmap, in_position, in_ordinal): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_values": packed_result}`, where `packed_result` is a Python integer
holding the packed output port bit pattern, not a list of fields.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`BitmapRankSelect128` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Inclusive rank and zero-based select on a 128-bit bitmap.

Bit i represents member i. Return rank=the number of set bits with index <= in_position; cardinality=the number of all set bits; and select=the index of the in_ordinal-th set bit in ascending order. Ordinal zero selects the first member. When ordinal>=cardinality, return select=0 and valid=0; otherwise valid=1, including when the selected index is zero. Output bits 7:0 hold rank, 15:8 cardinality, 22:16 select, and bit 23 valid. The count fields represent 128 without overflow. This combines CRoaring bitset rank/select semantics on a finite bitmap with start_rank=0; it is not merely a population-count task.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

For a bitmap containing members 0 and 127, position 0 and ordinal 1,
rank=1, cardinality=2, select=127 and valid=1: `out_values=0xff0201`.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
