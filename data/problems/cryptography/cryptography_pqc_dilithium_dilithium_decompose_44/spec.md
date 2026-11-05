# ML-DSA-44 Coefficient Decomposition

Decompose one standard coefficient into the high and centered low parts used by ML-DSA-44, including the wrap at the top of the modulus.

## Interface

### RTL interface

```systemverilog
module DilithiumDecompose44 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [22:0] in_a,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [23:0] out_a0,
    output logic [5:0]  out_a1
);
```

Transfers occur on rising clock edges with valid and ready both high and rst low. The input producer holds the whole request stable while stalled. The block holds the whole response, including `out_error`, stable while stalled. Every accepted request produces exactly one ordered response unless reset cancels it. No fixed latency or pipeline architecture is required.

### Behavior interface

```python
class DilithiumDecompose44:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a): ...
```

Each call represents one accepted request. The argument is an unsigned 23-bit
integer. Return exactly one dictionary with `out_a0` (unsigned 24-bit encoding
of the signed low part), `out_a1` (unsigned 6-bit integer), and `out_error`
(integer 0 or 1). Invalid requests return the same three-field shape, never
`None` or a list. The operation has no persistent model state; `reset()`
returns `None` without emitting a record.

## Modules

- `DilithiumDecompose44`: checked integer sub-operator and ready/valid transport.

## Functional Behavior

`in_a` is unsigned and must satisfy `0 <= in_a < Q`, where `Q=8380417`.

An out-of-range request returns `out_error=1` and **zero in every numerical output field**. A valid request returns `out_error=0` and the result below. The error flag is ordinary response data, not a missing transaction. No invalid input is silently clamped, normalized or evaluated as a valid operation.

Compute `a1=(in_a+95231)//190464`; replace 44 by 0. Then set `a0=in_a-190464*a1` and subtract Q if `a0>4190208`. Equivalently, the high-part calculation may use `t=(in_a+127)>>7`, then `(t*11275+8388608)>>24`, followed by the same wrap.

The result satisfies `0 <= a1 < 44`, `-95232 <= a0 <= 95232`, and `in_a ≡ 190464*a1+a0 (mod Q)`. At the upper wrap, `in_a=Q-1` gives `(a1,a0)=(0,-1)`.

Return the 24-bit two's-complement encoding of `a0` and the unsigned 6-bit encoding of `a1`.

All arithmetic is integer arithmetic. Signed results use the output width's two's-complement encoding; specified shifts of negative values are arithmetic. There is no unspecified floating-point behavior.

## Reset Behavior

Reset is synchronous, active-high. Reset clears `out_valid`, `out_error` and all output payload fields and cancels pending responses. No transfers are counted while reset is asserted. The operation has no mathematical state across requests. After reset it accepts new requests normally; idle payload values after subsequent transfers are otherwise unspecified.
