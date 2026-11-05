# VectorAbsF32x4

VectorAbsF32x4 computes the IEEE-754 absolute value of four FP32 elements supplied in one input transaction and returns the four absolute-valued FP32 results in one output transaction. The function implemented is elementwise vector absolute value, matching a four-lane unrolled realization of `pDst[n] = abs(pSrc[n])`. Each lane is independent and the operation on each lane is the FP32 sign-bit clear operation.

## Interface

### RTL interface

```systemverilog
module vector_abs_f32x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a0,
    input  logic [31:0] in_a1,
    input  logic [31:0] in_a2,
    input  logic [31:0] in_a3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_r0,
    output logic [31:0] out_r1,
    output logic [31:0] out_r2,
    output logic [31:0] out_r3
);
```

### Behavior interface

```python
class VectorAbsF32x4:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a0, in_a1, in_a2, in_a3): ...
```

Call `process` once per accepted input. It returns one dictionary with exactly
`out_r0`, `out_r1`, `out_r2`, and `out_r3`. Each value is a Python integer in
0..4294967295 carrying an FP32 bit pattern, not a Python `float`. Even an
all-zero input returns the four-field dictionary, not `None`.

## Modules

- `vector_abs_f32x4`: top-level module implementing one four-element FP32 absolute-value transaction.

## Functional Behavior

Each input port `in_a0` through `in_a3` is a 32-bit unsigned integer carrying the raw bit pattern of one IEEE-754 binary32 datum.

For each lane `i` independently, the output is formed by clearing bit 31 of the corresponding input and leaving bits 30:0 unchanged:

- `out_r0 = in_a0 & 32'h7FFFFFFF`
- `out_r1 = in_a1 & 32'h7FFFFFFF`
- `out_r2 = in_a2 & 32'h7FFFFFFF`
- `out_r3 = in_a3 & 32'h7FFFFFFF`

This is the complete arithmetic definition. No other normalization, rounding, exception handling, saturation, denormal flushing, or canonicalization is performed.

Per-lane edge-case behavior follows directly from sign-bit clear:

| Input class | Output |
|---|---|
| positive finite | unchanged |
| negative finite | same exponent and fraction, sign changed to 0 |
| +0 (`0x00000000`) | `0x00000000` |
| -0 (`0x80000000`) | `0x00000000` |
| +Inf (`0x7F800000`) | `0x7F800000` |
| -Inf (`0xFF800000`) | `0x7F800000` |
| positive NaN | unchanged bit pattern except sign already 0 |
| negative NaN | same NaN payload with sign changed to 0 |

The module is a 1:1 transaction mapper: one accepted input transaction produces exactly one output transaction containing the four lane results. Internal latency is implementation-defined, but output ordering must preserve input ordering.

## Reset Behavior

Reset is active-high. Reset may be implemented synchronously or asynchronously.

On reset, all architectural state is cleared to zero. Since the function is stateless, no data history, counters, or accumulators exist.

While reset is asserted:
- `out_valid = 0`
- `out_r0 = 32'h00000000`
- `out_r1 = 32'h00000000`
- `out_r2 = 32'h00000000`
- `out_r3 = 32'h00000000`

After reset deassertion and before the first accepted input, the module remains ready to accept one input transaction whenever it is not holding an unconsumed output. Any internal output-holding register, if present, shall be initialized to the zero values listed above.
