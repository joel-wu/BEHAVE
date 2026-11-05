# HEXL 64-bit Lazy Barrett Reduction

Compute the lazy Barrett remainder using a checked caller-supplied reciprocal.

## Interface

### RTL interface

```systemverilog
module HexlBarrettReduce64 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_input,
    input logic [63:0] in_modulus,
    input logic [63:0] in_q_barr,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_value,
    output logic out_error
);
```

### Behavior interface

```python
class HexlBarrettReduce64:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_input, in_modulus, in_q_barr): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_value": ..., "out_error": ...}`, including on an error. Returned
fields are Python integers holding the specified port bit patterns;
`out_error` is `0` or `1`.

### Transaction protocol

Payloads use unsigned bit patterns unless stated otherwise. An input is accepted on a rising edge with in_valid && in_ready, and an output is emitted with out_valid && out_ready. Once presented, valid and its payload remain stable until accepted; reset cancels pending transfers. Latency is unconstrained. Each accepted input transaction produces exactly one output transaction, in input order.

## Modules

`HexlBarrettReduce64` is the complete top-level module.

## Functional Behavior

Let x = in_input, q = in_modulus, f = in_q_barr, and B = 2^64. The request is valid exactly when q >= 2 and f*q <= B < (f+1)*q, with these checks evaluated as mathematical integers. Thus f = floor(B/q). Invalid requests return out_error=1 and out_value=0.

A valid request returns out_error=0 and the **lazy** remainder out_value = x - floor(x*f/B)*q. No final subtraction is applied: the result lies in [0, 2*q) and may equal or exceed q. It also lies in [0, x], so it always fits 64 bits. This is BarrettReduce64 with output modulus factor two, not canonical x % q. For x=q=7 and f=floor(B/7), the result is 7.

## Reset Behavior

Reset is synchronous active-high rst. It empties pending output; out_valid is low during reset. There is no persistent arithmetic state. A new request after reset is independent of requests before reset.
