# DWT53Pyramid8

Three-level reversible integer 5/3 wavelet decomposition of eight signed INT16 samples.

## Interface

### RTL interface

```systemverilog
module DWT53Pyramid8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    output logic [255:0] out_values
);
endmodule
```

Ports carry unsigned
bit patterns; signed lanes use two's complement. Field positions are
defined below. Every input bit pattern has a defined result.

### Behavior interface

```python
class DWT53Pyramid8:
    def reset(self): ...
    def process(self, in_data): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_values": packed_result}`, where `packed_result` is a Python integer
holding the packed output port bit pattern, not a list of coefficients.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`DWT53Pyramid8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Three-level reversible integer 5/3 wavelet decomposition of eight signed INT16 samples.

Input sample i occupies bits 16*i +: 16, least-significant sample first. Begin with A equal to these eight signed values. For active lengths n=8,4,2, let m=n/2, E[j]=A[2*j] and O[j]=A[2*j+1] for j=0..m-1. First compute all details D[j]=O[j]-floor((E[j]+E[min(j+1,m-1)])/2). Then compute all lowpass values L[j]=E[j]+floor((D[max(j-1,0)]+D[j]+2)/4). Replace the first n values of A by L followed by D; keep all later values unchanged. This is whole-sample symmetric boundary extension, with the first sample on an even coordinate.

Return all eight coefficients as signed INT32 lanes, in order [L3,D3,D2[0],D2[1],D1[0],D1[1],D1[2],D1[3]]. Division rounds toward negative infinity, not toward zero. There is no saturation or modular intermediate arithmetic; INT32 is sufficient for this INT16 eight-sample, three-level domain. All input bits matter. This composes the pinned OpenJPEG reversible horizontal 5/3 transform at three resolutions; it is not the irreversible 9/7 transform, quantization, or a complete JPEG 2000 codec.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Input `[0,1,2,3,4,5,6,7]` gives `[3,5,0,2,0,0,0,1]`.
A constant input `[c,c,c,c,c,c,c,c]` gives `[c,0,0,0,0,0,0,0]`, including negative c.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
