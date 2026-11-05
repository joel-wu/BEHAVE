# ML-DSA-44 Hint Application

Apply one hint bit to the high part of an ML-DSA-44 coefficient, using the sign of its decomposed low part and modulo-44 wrap.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_dilithium_dilithium_usehint_44 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [22:0] in_a,
    input  logic        in_hint,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [5:0]  out_r
);
```

Transfers occur on rising clock edges with valid and ready both high and rst low. The input producer holds the whole request stable while stalled. The block holds the whole response, including `out_error`, stable while stalled. Every accepted request produces exactly one ordered response unless reset cancels it. No fixed latency or pipeline architecture is required.

### Behavior interface

```python
class DilithiumUseHint44:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a, in_hint): ...
```

Arguments and returned values are raw nonnegative port bit patterns. One call returns a dictionary containing `out_r` and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_r`: 6.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cryptography_pqc_dilithium_dilithium_usehint_44`: checked integer sub-operator and ready/valid transport.

## Functional Behavior

`in_a` is unsigned and must satisfy `0 <= in_a < Q`, where `Q=8380417`.

An out-of-range request returns `out_error=1` and **zero in every numerical output field**. A valid request returns `out_error=0` and the result below. The error flag is ordinary response data, not a missing transaction. No invalid input is silently clamped, normalized or evaluated as a valid operation.

Compute `a1=(in_a+95231)//190464`; replace 44 by 0. Then set `a0=in_a-190464*a1` and subtract Q if `a0>4190208`. Equivalently, the high-part calculation may use `t=(in_a+127)>>7`, then `(t*11275+8388608)>>24`, followed by the same wrap.

The result satisfies `0 <= a1 < 44`, `-95232 <= a0 <= 95232`, and `in_a ≡ 190464*a1+a0 (mod Q)`. At the upper wrap, `in_a=Q-1` gives `(a1,a0)=(0,-1)`.

If `in_hint=0`, return `out_r=a1`. Otherwise return `(a1+1) mod 44` when `a0>0`, and `(a1-1) mod 44` when `a0<=0`. In particular, zero belongs to the decrement branch. `in_hint` is one bit; `out_r` is unsigned and lies in 0..43.

All arithmetic is integer arithmetic. Signed results use the output width's two's-complement encoding; specified shifts of negative values are arithmetic. There is no unspecified floating-point behavior.

## Reset Behavior

Reset is synchronous, active-high. Reset clears `out_valid`, `out_error` and all output payload fields and cancels pending responses. No transfers are counted while reset is asserted. The operation has no mathematical state across requests. After reset it accepts new requests normally; idle payload values after subsequent transfers are otherwise unspecified.
