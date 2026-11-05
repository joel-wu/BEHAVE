# HEXL Scalar Preconditioned Modular Multiply

Multiply two unsigned operands modulo a checked modulus using the caller's precondition factor.

## Interface

### RTL interface

```systemverilog
module HexlScalarMultiplyMod (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_x,
    input logic [63:0] in_y_operand,
    input logic [63:0] in_y_barrett_factor,
    input logic [63:0] in_modulus,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_value,
    output logic out_error
);
```

### Behavior interface

```python
class HexlScalarMultiplyMod:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_y_operand, in_y_barrett_factor, in_modulus): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_value": ..., "out_error": ...}`, including on an error. Returned
fields are Python integers holding the specified port bit patterns;
`out_error` is `0` or `1`.

### Transaction protocol

Payloads use unsigned bit patterns unless stated otherwise. An input is accepted on a rising edge with in_valid && in_ready, and an output is emitted with out_valid && out_ready. Once presented, valid and its payload remain stable until accepted; reset cancels pending transfers. Latency is unconstrained. Each accepted input transaction produces exactly one output transaction, in input order.

## Modules

`HexlScalarMultiplyMod` is the complete top-level module.

## Functional Behavior

Let x=in_x, y=in_y_operand, q=in_modulus, f=in_y_barrett_factor, and B=2^64. A valid request satisfies 2 <= q < 2^63, y < q, and f*q <= y*B < (f+1)*q. The inequalities use mathematical integers, not wrapped 64-bit arithmetic. They check the precondition factor f=floor(y*B/q). Invalid requests return out_error=1 and out_value=0.

For a valid request, form h=floor(x*f/B) and r=x*y-h*q. Return out_value=r-q if r>=q, otherwise r, with out_error=0. The result is the canonical product (x*y) mod q. x may be any 64-bit value; r<2*q fits 64 bits under the declared q bound. This is one scalar preconditioned multiplication, not a vector elementwise kernel.

## Reset Behavior

Reset is synchronous active-high rst. It empties pending output; out_valid is low during reset. There is no persistent arithmetic state. A new request after reset is independent of requests before reset.
