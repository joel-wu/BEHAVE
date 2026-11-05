# HardGateGRU2

A complete two-input/two-hidden-unit recurrent GRU step with persistent state H[2], initially zero. This is an explicitly fixed-point HARD-gate adaptation of the source GRU recurrence (linear_before_reset=0), not floating sigmoid/tanh ONNX numerical equivalence. X, H and each weight are signed INT8 in units 1/128. W and R each contain twelve INT8 values: three row-major 2x2 matrices in z,r,h order, index 4*g+2*i+j. Bias contains six INT16 values in units 1/128, index 2*g+i; it represents the sum of input and recurrent biases. Let A(n,d) round n/d to nearest integer with ties away from zero. Read old H (or [0,0] if in_clear=1). For g in {z,r}, P_g[i]=128*b_g[i]+sum_j(W_g[i,j]*X[j]+R_g[i,j]*H[j]); G_g[i]=clip(A(P_g[i],512)+64,0,128). These are hard-sigmoid gates clip(real/4+1/2,0,1). Set V[j]=A(G_r[j]*H[j],128); then C[i]=clip(A(128*b_h[i]+sum_j(W_h[i,j]*X[j]+R_h[i,j]*V[j]),128),-128,127). C is a clipped linear candidate, not tanh. Finally Hnew[i]=clip(A((128-G_z[i])*C[i]+G_z[i]*H[i],128),-128,127). All gates and candidate lanes use the same old state; update both hidden lanes simultaneously and return them. Round at exactly the stated points. Parameters may change between calls and are consumed with that call. Clear discards old state before computing this request; it does not suppress its response.

## Interface

### RTL interface

```systemverilog
module HardGateGRU2 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [15:0] in_data,
    input logic [95:0] in_weights,
    input logic [95:0] in_recurrent,
    input logic [95:0] in_bias,
    input logic [0:0] in_clear,
    output logic [15:0] out_values
);
endmodule
```

### Behavior interface

```python
class HardGateGRU2:
    def reset(self): ...
    def process(self, in_data, in_weights, in_recurrent, in_bias, in_clear): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 16.

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

`HardGateGRU2` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

A complete two-input/two-hidden-unit recurrent GRU step with persistent state H[2], initially zero. This is an explicitly fixed-point HARD-gate adaptation of the source GRU recurrence (linear_before_reset=0), not floating sigmoid/tanh ONNX numerical equivalence. X, H and each weight are signed INT8 in units 1/128. W and R each contain twelve INT8 values: three row-major 2x2 matrices in z,r,h order, index 4*g+2*i+j. Bias contains six INT16 values in units 1/128, index 2*g+i; it represents the sum of input and recurrent biases. Let A(n,d) round n/d to nearest integer with ties away from zero. Read old H (or [0,0] if in_clear=1). For g in {z,r}, P_g[i]=128*b_g[i]+sum_j(W_g[i,j]*X[j]+R_g[i,j]*H[j]); G_g[i]=clip(A(P_g[i],512)+64,0,128). These are hard-sigmoid gates clip(real/4+1/2,0,1). Set V[j]=A(G_r[j]*H[j],128); then C[i]=clip(A(128*b_h[i]+sum_j(W_h[i,j]*X[j]+R_h[i,j]*V[j]),128),-128,127). C is a clipped linear candidate, not tanh. Finally Hnew[i]=clip(A((128-G_z[i])*C[i]+G_z[i]*H[i],128),-128,127). All gates and candidate lanes use the same old state; update both hidden lanes simultaneously and return them. Round at exactly the stated points. Parameters may change between calls and are consumed with that call. Clear discards old state before computing this request; it does not suppress its response.

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
result may reappear. The two hidden lanes reset to zero. State advances once per accepted input, never on idle/stalled cycles or merely on output consumption.
