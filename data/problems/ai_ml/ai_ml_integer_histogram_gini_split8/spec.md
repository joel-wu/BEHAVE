# HistogramGiniSplit8

Choose the exact minimum weighted-Gini split of an eight-bin two-class histogram.

## Interface

### RTL interface

```systemverilog
module HistogramGiniSplit8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_counts,
    output logic [83:0] out_values
);
endmodule
```

### Behavior interface

```python
class HistogramGiniSplit8:
    def reset(self): ...
    def process(self, in_counts): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 84.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
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

`HistogramGiniSplit8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Choose the exact minimum weighted-Gini split of an eight-bin two-class histogram.

Input has unsigned four-bit counts count[bin,class], flattened 2*bin+class. Consider thresholds t=0..6: bins <=t go left and the rest right. Discard splits with an empty side. Let L0,L1,R0,R1 be class counts, l=L0+L1, r=R0+R1, N=l+r. Weighted impurity is Num/Den, where Den=N*l*r and Num=Den-(L0^2+L1^2)*r-(R0^2+R1^2)*l. Compare exact fractions without floating point; equal impurity chooses smaller t. Return threshold bits 0..2, valid bit 3, four unsigned eight-bit counts L0,L1,R0,R1 at bits 4,12,20,28, then Num in bits 36..59 and Den in bits 60..83. Do not reduce the returned fraction. If no legal split exists, return zero. This is the Gini classification criterion applied to all boundaries of a supplied histogram.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

One class-0 item in bin 0 and one class-1 item in bin 7 allow every threshold with zero impurity. Choose threshold 0, valid 1, counts [1,0,0,1], Num=0, Den=2.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
