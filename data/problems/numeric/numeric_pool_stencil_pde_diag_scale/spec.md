# DiagScaleElementwiseDivider

DiagScaleElementwiseDivider computes one element of a diagonal scaling operation, `x = D^{-1} y`, per accepted input transaction. Each transaction supplies one diagonal matrix element and one vector element, both as 32-bit signed integers. The module returns one 32-bit signed integer quotient formed by truncating division toward zero. This kernel is the elementwise inner operation of diagonal scaling and performs exactly one divide per transaction.

## Interface

### RTL interface

```systemverilog
module DiagScaleElementwiseDivider (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_diag,
    input  logic [31:0]  in_y,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_x
);
```

### Behavior interface

```python
class DiagScaleElementwiseDivider:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_diag, in_y):
        pass
```

The `process()` method returns one dict per accepted input transaction with key `out_x`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `DiagScaleElementwiseDivider`: top-level module that accepts one diagonal/value pair and produces one quotient.

## Functional Behavior

For each accepted input transaction:

1. Interpret `in_diag` as a 32-bit two's-complement signed integer `diag`.
2. Interpret `in_y` as a 32-bit two's-complement signed integer `y`.
3. Compute the signed quotient `q = trunc_zero(y / diag)`, where `trunc_zero` denotes integer division with truncation toward zero.
4. Return `out_x` as the 32-bit two's-complement bit pattern of `q`.

The mapping is 1:1: every accepted input transaction produces exactly one output transaction.

Arithmetic rules:

- Inputs are exactly 32 bits.
- Signed interpretation is two's-complement.
- Division is signed division.
- Rounding mode is truncation toward zero.
- The output is the low 32 bits of the mathematical two's-complement representation of the quotient, which is equivalent to the standard 32-bit signed result when the quotient is representable in 32 bits.

Required edge-case behavior:

- If `diag == 0`, the output is `out_x = 32'h00000000`.
- If `y == 0` and `diag != 0`, the output is `out_x = 32'h00000000`.
- Negative operands follow signed division rules with truncation toward zero. Examples:
  - `in_y = 32'h00000007`, `in_diag = 32'h00000003` produces `out_x = 32'h00000002`.
  - `in_y = 32'hFFFFFFF9`, `in_diag = 32'h00000003` produces `out_x = 32'hFFFFFFFE`.
  - `in_y = 32'h00000007`, `in_diag = 32'hFFFFFFFD` produces `out_x = 32'hFFFFFFFE`.
  - `in_y = 32'hFFFFFFF9`, `in_diag = 32'hFFFFFFFD` produces `out_x = 32'h00000002`.
- The overflow case `y = -2^31` and `diag = -1` returns `out_x = 32'h80000000`.

No fractional scaling, fixed-point shifting, saturation, or remainder output is performed.

## Reset Behavior

Reset is active-high and asynchronous. During reset:

- `in_ready` shall be high.
- `out_valid` shall be low.
- `out_x` shall be `32'h00000000`.

Architectural state after reset:

- No input transaction is pending.
- Any internal register that holds the output value shall be `32'h00000000`.
- Any internal register that holds captured inputs shall be `32'h00000000`.
