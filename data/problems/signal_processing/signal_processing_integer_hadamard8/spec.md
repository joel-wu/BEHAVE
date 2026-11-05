# Hadamard8

Compute an exact eight-point Walsh–Hadamard transform in natural Sylvester
order. The integer transform uses only additions and subtractions, with
enough output precision to preserve every result.

## Interface

### RTL interface

```systemverilog
module Hadamard8 (
    input  logic         clk, rst, in_valid, out_ready,
    output logic         in_ready, out_valid,
    input  logic [127:0] in_samples,
    output logic [151:0] out_values
);
endmodule
```

Input lane `j` occupies bits `16*j +: 16` and is a signed two's-complement
integer `x[j]`. Output lane `k` occupies bits `19*k +: 19` and encodes the
signed result `y[k]`. Lane zero is least significant.

### Behavior interface

```python
class Hadamard8:
    def reset(self): ...
    def process(self, in_samples): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_values": packed_result}`. The argument and `packed_result` are Python
integers holding packed port bit patterns, not lists of lanes.

Behavior `reset()` has no numerical state to clear.

### Transaction protocol

Outside reset, rising edges with `in_valid && in_ready` accept a vector;
edges with `out_valid && out_ready` retire a result. There is one ordered
output vector per accepted input. A stalled producer holds valid and its
payload stable. Bubbles and backpressure must not alter the transform or
lose, duplicate or reorder vectors. Latency and throughput are not fixed.

## Modules

`Hadamard8` is the only required public module.

## Functional Behavior

For indices `k,j` from 0 through 7, define
`H[k,j] = (-1)^popcount(k & j)`. Compute
`y[k] = sum(H[k,j] * x[j] for j=0..7)`.

Equivalently, construct `H1=[1]` and repeatedly apply the Sylvester rule
`H(2n)=[[Hn,Hn],[Hn,-Hn]]` until reaching `H8`, then return `H8*x`.
This is natural binary row order, not sequency or bit-reversed order.
There is no normalization, rounding, saturation or intermediate truncation.
All outputs fit in the signed 19-bit range `[-262144,262143]`.

Examples, with vectors listed in increasing lane order:

- `[1,0,0,0,0,0,0,0]` gives `[1,1,1,1,1,1,1,1]`.
- `[1,2,3,4,5,6,7,8]` gives `[36,-4,-8,0,-16,0,0,0]`.
- Eight copies of `-32768` give `[-262144,0,0,0,0,0,0,0]`.

Vectors are independent; there is no numerical state between requests.

## Reset Behavior

`rst` is synchronous and active high. It overrides transfers, cancels all
pending vectors, and clears `out_valid` and `out_values`. No pre-reset result
may reappear.
