# Q15 Vector Shift4

This module performs a saturating arithmetic shift on a four-lane vector of signed Q15 samples. Each accepted input transaction contains four independent 16-bit signed samples and one signed 8-bit shift amount. A non-negative shift amount applies a left shift with saturation to the Q15 range. A negative shift amount applies an arithmetic right shift by the magnitude of the shift amount. One accepted input produces exactly one output transaction containing the four shifted results.

## Interface

### RTL interface

```systemverilog
module q15_vector_shift4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x0,
    input  logic [15:0] in_x1,
    input  logic [15:0] in_x2,
    input  logic [15:0] in_x3,
    input  logic [7:0]  in_shiftBits,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y0,
    output logic [15:0] out_y1,
    output logic [15:0] out_y2,
    output logic [15:0] out_y3
);
```

### Behavior interface

```python
class Q15VectorShift4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_shiftBits):
        pass
```

Each `process(...)` call represents one accepted four-lane input and returns
exactly `{"out_y0": y0_bits, "out_y1": y1_bits, "out_y2": y2_bits, "out_y3": y3_bits}`.
Input lanes and return values are unsigned 16-bit integers encoding signed Q15
values. `in_shiftBits` is an unsigned 8-bit integer encoding the signed shift.
Every call returns all four fields in one dictionary, never `None` or a list.
No state persists between vectors; `reset()` returns `None` and emits no result.

## Modules

- `q15_vector_shift4`: top-level single-clock kernel implementing four parallel Q15 arithmetic shifts with one shared signed shift amount.

## Functional Behavior

All four input lanes are processed independently with the same `in_shiftBits` value. `in_x0` through `in_x3` and `out_y0` through `out_y3` are 16-bit two's-complement signed integers in Q15 representation. `in_shiftBits` is an 8-bit two's-complement signed integer.

For each lane, let `x` be the signed value decoded from the 16-bit input lane, and let `s` be the signed value decoded from `in_shiftBits`.

If `s >= 0`, the output is a saturating left shift:
- Compute the mathematical integer `v = x * 2^s`.
- If `v > 32767`, the output for that lane is `32767` (`16'h7FFF`).
- Else if `v < -32768`, the output for that lane is `-32768` (`16'h8000`).
- Else the output for that lane is `v`.

If `s < 0`, the output is an arithmetic right shift:
- Let `r = -s`.
- The output is the signed result of shifting `x` right by `r` bits with sign extension.
- Equivalently, the result is `floor(x / 2^r)` for all signed integer `x`.
- No saturation is applied on right shifts.

The arithmetic right shift contract includes large shift counts:
- If `r == 0`, the result is `x`.
- If `1 <= r <= 15`, the result is the standard 16-bit arithmetic right shift of `x` by `r`.
- If `r >= 16`, the result is `0` when `x >= 0` and `-1` (`16'hFFFF`) when `x < 0`.

The left-shift saturation contract includes large shift counts:
- The mathematical definition above applies for every signed 8-bit `s >= 0`, including values greater than 15.
- Therefore, for sufficiently large non-negative `s`, any positive nonzero `x` saturates to `16'h7FFF`, any negative nonzero `x` saturates to `16'h8000`, and `x == 0` produces `16'h0000`.

Output encoding:
- Each lane result is converted back to a 16-bit two's-complement bit pattern on the corresponding `out_y*` port.

Transaction cardinality:
- Each accepted input transaction produces exactly one output transaction.
- The output transaction contains `out_y0`, `out_y1`, `out_y2`, and `out_y3` for the same accepted input.

Worked examples:
- `x = 16'h1000` and `s = 8'h02` gives `16'h4000`.
- `x = 16'h4000` and `s = 8'h01` gives `16'h7FFF` because `16384 * 2 = 32768`, which saturates.
- `x = 16'h8000` and `s = 8'h01` gives `16'h8000` because `-32768 * 2 = -65536`, which saturates.
- `x = 16'hFFFF` and `s = 8'hFF` gives `16'hFFFF` because `-1 >> 1 = -1`.
- `x = 16'h8000` and `s = 8'hF0` gives `16'hFFFF` because `-32768 >> 16 = -1`.
- `x = 16'h0001` and `s = 8'hF0` gives `16'h0000` because `1 >> 16 = 0`.

## Reset Behavior

The reset is synchronous and active high.

On a cycle with `rst == 1`, all architectural state is set to its initial value. This module has no architectural datapath state beyond its handshake/output registers. After reset:
- `out_y0 = 16'h0000`
- `out_y1 = 16'h0000`
- `out_y2 = 16'h0000`
- `out_y3 = 16'h0000`

Before any post-reset input transaction is accepted, `out_valid` remains low. Once a result is presented, `out_valid` and its payload remain stable until accepted, unless reset discards the pending result.
