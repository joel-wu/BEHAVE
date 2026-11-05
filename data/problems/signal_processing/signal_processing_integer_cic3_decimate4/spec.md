# CIC3Decimate4

A three-stage cascaded-integrator-comb low-pass decimator with rate four
and differential delay one. Each transaction supplies four consecutive
signed samples and returns one decimated sample. Filter history persists
across transactions.

## Interface

### RTL interface

```systemverilog
module CIC3Decimate4 (
    input  logic        clk, rst, in_valid, out_ready,
    output logic        in_ready, out_valid,
    input  logic [63:0] in_samples,
    output logic [21:0] out_values
);
endmodule
```

Lane `j` is the signed 16-bit value in bits `16*j +: 16`, for `j=0..3`.
Lanes are consumed in increasing order. `out_values` is the unsigned port
encoding of a signed 22-bit result.

### Behavior interface

```python
class CIC3Decimate4:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_samples): ...
```

Call `process` once per accepted input containing four packed samples.
It returns one dictionary `{"out_values": encoded_result}`, where
`encoded_result` is a Python integer holding the 22-bit output port pattern.
Do not split the input record into four calls or suppress the returned record.

Behavior construction and `reset()` establish the same empty history.

### Transaction protocol

Outside reset, inputs and outputs transfer on rising edges with
`in_valid && in_ready` and `out_valid && out_ready`, respectively. Every
accepted input block produces one output in acceptance order. Producers
hold valid and payload stable while stalled. Numerical history advances
only for accepted blocks: bubbles, stalls and clock cycles do not insert
zero samples. There is no fixed latency or throughput requirement.

## Modules

`CIC3Decimate4` is the only required public module.

## Functional Behavior

Concatenate the four-sample blocks into a logical stream `x[0],x[1],...`;
samples before index zero are zero. Define

`h = [1,3,6,10,12,12,10,6,3,1]`.

For accepted block number `b`, starting at zero, return the exact integer
`y[b] = sum(h[k] * x[4*b+3-k] for k=0..9)`.
These coefficients are those of `(1+z^-1+z^-2+z^-3)^3`. Sampling is at the
fourth sample of each block; no startup outputs are omitted. The DC gain
is 64, with no normalization, rounding or saturation. The result fits in
signed 22 bits, including `-2097152` for a steady input of `-32768`.

An equivalent implementation uses three zero-initialized integrators. For
each sample, update in order `i0 += x`, `i1 += i0`, `i2 += i1`, using the
new value at each stage. After every fourth sample, compute
`d0=i2-c0`, `d1=d0-c1`, `d2=d1-c2`, then simultaneously save
`(c0,c1,c2)=(i2,d0,d1)` and emit `d2`. Thirty-two-bit modular integrator
and comb registers are sufficient when the final result is decoded from
its low 22 bits; this does not change the specified exact FIR output.

Examples from reset:

- Repeated blocks `[1,1,1,1]` produce `20,60,64,64,...`.
- `[1,0,0,0]` followed by zero blocks produces `10,6,0,0,...`.
- `[0,0,0,1]` followed by zero blocks produces `1,12,3,0,...`.

## Reset Behavior

`rst` is synchronous and active high. It overrides transfers, cancels
pending outputs, clears all filter history, and clears `out_valid` and
`out_values`. The next accepted block starts a new zero-padded stream.
