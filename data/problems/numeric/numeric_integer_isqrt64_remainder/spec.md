# ISqrt64Remainder

Exact unsigned 64-bit integer square root and remainder.

## Interface

### RTL interface

```systemverilog
module ISqrt64Remainder (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_n,
    output logic [64:0] out_values
);
endmodule
```

Ports carry unsigned
bit patterns. Field positions and any required zero padding are defined
below. Indices are zero-based. Every input bit pattern has a defined result.

### Behavior interface

```python
class ISqrt64Remainder:
    def reset(self): ...
    def process(self, in_n): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_values": packed_result}`, where `packed_result` is a Python integer
holding the packed output port bit pattern, not a pair of separate values.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`ISqrt64Remainder` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Exact unsigned 64-bit integer square root and remainder.

Return the unique integers q,r such that n=q*q+r and 0<=r<2*q+1. Thus q=floor(sqrt(n)), not a rounded real square root. Output bits 31:0 contain q, and bits 64:32 contain r. The remainder needs 33 bits: for n=2^64-1, q=2^32-1 and r=2^33-2. Zero returns zero. No host floating point or signed reinterpretation is used. This preserves LibTomMath mp_sqrt on the selected nonnegative domain and adds an explicitly observable remainder. Hardware may use digit recurrence instead of the source Newton iteration; all 64 input bits remain significant.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

For `in_n=10`, q=3 and r=1, so `out_values=0x100000003`.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
