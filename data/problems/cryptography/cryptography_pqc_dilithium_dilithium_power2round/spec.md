# ML-DSA Power-of-Two Rounding

Split one standard ML-DSA coefficient into its high and signed low parts, preserving the exact integer reconstruction and the half-down tie convention.

## Interface

### RTL interface

```systemverilog
module DilithiumPower2Round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [22:0]  in_a,

    output logic         out_valid,
    output logic out_error,
    input  logic         out_ready,
    output logic [13:0]  out_a0,
    output logic [9:0]   out_a1
);
```

Transfers occur on rising clock edges with valid and ready both high and rst low. The input producer holds the whole request stable while stalled. The block holds the whole response, including `out_error`, stable while stalled. Every accepted request produces exactly one ordered response unless reset cancels it. No fixed latency or pipeline architecture is required.

### Behavior interface

```python
class DilithiumPower2Round:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a): ...
```

Arguments and returned values are raw nonnegative port bit patterns. One call returns a dictionary containing `out_a0`, `out_a1` and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a0`: 14.
- `out_a1`: 10.
- `out_error`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `DilithiumPower2Round`: checked integer sub-operator and ready/valid transport.

## Functional Behavior

`in_a` is unsigned and must satisfy `0 <= in_a < Q`, where `Q=8380417`.

An out-of-range request returns `out_error=1` and **zero in every numerical output field**. A valid request returns `out_error=0` and the result below. The error flag is ordinary response data, not a missing transaction. No invalid input is silently clamped, normalized or evaluated as a valid operation.

With D=13, compute `a1=(in_a+4095)//8192` and `a0=in_a-8192*a1`. Return unsigned `out_a1=a1` and the **14-bit** two's-complement encoding of `a0` in `out_a0`.

For every valid coefficient, `0 <= a1 <= 1023`, `-4096 < a0 <= 4096` and `in_a=8192*a1+a0` over the integers. In particular, inputs 4096 and 4097 produce `(a1,a0)=(0,4096)` and `(1,-4095)`. The positive tie +4096 is representable; it is not the signed value -4096.

All arithmetic is integer arithmetic. Signed results use the output width's two's-complement encoding; specified shifts of negative values are arithmetic. There is no unspecified floating-point behavior.

## Reset Behavior

Reset is synchronous, active-high. Reset clears `out_valid`, `out_error` and all output payload fields and cancels pending responses. No transfers are counted while reset is asserted. The operation has no mathematical state across requests. After reset it accepts new requests normally; idle payload values after subsequent transfers are otherwise unspecified.
