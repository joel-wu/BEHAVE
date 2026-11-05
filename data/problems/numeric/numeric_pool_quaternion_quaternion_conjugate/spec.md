# Quaternion Conjugate FP32

This module computes the conjugate of one input quaternion per accepted transaction. The quaternion is represented as four IEEE-754 binary32 values `(a, b, c, d)`, where `a` is the scalar component and `(b, c, d)` are the vector components. The conjugate is `(a, -b, -c, -d)`. The operation is defined as bit-level IEEE-754 sign inversion on the last three components and pass-through of the first component.

## Interface

### RTL interface

```systemverilog
module quaternion_conjugate_fp32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_q0,
    input  logic [31:0] in_q1,
    input  logic [31:0] in_q2,
    input  logic [31:0] in_q3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_q0,
    output logic [31:0] out_q1,
    output logic [31:0] out_q2,
    output logic [31:0] out_q3
);
```

### Behavior interface

```python
class QuaternionConjugateFp32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_q0, in_q1, in_q2, in_q3):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_q0`, `out_q1`, `out_q2`, `out_q3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `quaternion_conjugate_fp32`: top-level module that accepts one quaternion and produces its conjugate.

## Functional Behavior

Each accepted input transaction contains four 32-bit unsigned integers `in_q0`, `in_q1`, `in_q2`, and `in_q3`. Each input is interpreted only as the raw 32-bit bit pattern of one IEEE-754 binary32 value.

For one accepted input quaternion `(in_q0, in_q1, in_q2, in_q3)`, the module produces exactly one output quaternion `(out_q0, out_q1, out_q2, out_q3)` with:

- `out_q0 = in_q0`
- `out_q1 = in_q1 ^ 32'h80000000`
- `out_q2 = in_q2 ^ 32'h80000000`
- `out_q3 = in_q3 ^ 32'h80000000`

This is a pure sign-bit transform. No floating-point arithmetic is performed.

The operation applies uniformly to all input bit patterns, including all IEEE-754 special encodings:

| Input component | Output rule |
|---|---|
| finite nonzero | sign bit inverted for `q1..q3`; unchanged for `q0` |
| `+0` / `-0` | sign bit inverted for `q1..q3`; unchanged for `q0` |
| `+Inf` / `-Inf` | sign bit inverted for `q1..q3`; unchanged for `q0` |
| NaN, quiet or signaling | sign bit inverted for `q1..q3`; payload bits preserved; unchanged for `q0` |
| subnormal | sign bit inverted for `q1..q3`; fraction and exponent bits preserved; unchanged for `q0` |

The module has 1:1 transaction cardinality: every accepted input transaction produces exactly one output transaction.

The module may be implemented with internal buffering and any finite latency consistent with the handshake protocol. The functional mapping above fully defines correctness.

## Reset Behavior

Reset is active-high and synchronous to `clk`.

On reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_q0 = 32'h00000000`
- `out_q1 = 32'h00000000`
- `out_q2 = 32'h00000000`
- `out_q3 = 32'h00000000`

Architectural state after reset:
- no pending buffered output transaction
- stored output quaternion value, if any output register exists, is `(32'h00000000, 32'h00000000, 32'h00000000, 32'h00000000)`
