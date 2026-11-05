# DynamicQuantizeU8

Asymmetrically quantize eight signed INT16 samples to UINT8 while returning the exact scale and zero point. Let L=min(0,X[0],...,X[7]), U=max(0,X[0],...,X[7]) and D=U-L, except D=1 when U=L=0. The scale is the exact rational D/255, not a floating-point bit pattern. Define E(n,d) as rounding exact n/d to nearest integer with ties to EVEN, for positive d. Set Z=E(-255*L,D) and Q[i]=clip(E(255*X[i],D)+Z,0,255). Round each scaled sample BEFORE adding Z. out_values[63:0] holds the eight bytes Q; bits [71:64] hold Z; bits [87:72] hold unsigned D. The zero vector yields Q=0, Z=0, D=1, following the archived reference zero-range branch. This preserves affine min/max calibration, inclusion of zero, separate round-to-even stages and returned scale; exact rational arithmetic replaces intermediate float32 arithmetic, so float32 boundary coincidences are not claimed identical.

## Interface

### RTL interface

```systemverilog
module DynamicQuantizeU8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    output logic [87:0] out_values
);
endmodule
```

### Behavior interface

```python
class DynamicQuantizeU8:
    def reset(self): ...
    def process(self, in_data): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 88.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Data buses are unsigned
bit patterns; each signed lane is two's complement. Lane i occupies
`[bits_per_lane*i +: bits_per_lane]`; flat indices are zero-based. Unused
padding bits do not exist. Every input bit pattern has a defined result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`DynamicQuantizeU8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Asymmetrically quantize eight signed INT16 samples to UINT8 while returning the exact scale and zero point. Let L=min(0,X[0],...,X[7]), U=max(0,X[0],...,X[7]) and D=U-L, except D=1 when U=L=0. The scale is the exact rational D/255, not a floating-point bit pattern. Define E(n,d) as rounding exact n/d to nearest integer with ties to EVEN, for positive d. Set Z=E(-255*L,D) and Q[i]=clip(E(255*X[i],D)+Z,0,255). Round each scaled sample BEFORE adding Z. out_values[63:0] holds the eight bytes Q; bits [71:64] hold Z; bits [87:72] hold unsigned D. The zero vector yields Q=0, Z=0, D=1, following the archived reference zero-range branch. This preserves affine min/max calibration, inclusion of zero, separate round-to-even stages and returned scale; exact rational arithmetic replaces intermediate float32 arithmetic, so float32 boundary coincidences are not claimed identical.

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
