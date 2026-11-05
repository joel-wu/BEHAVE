# Q15 Vector Add 4

This module performs elementwise addition of two 4-element vectors of signed Q15 samples and returns a 4-element result vector. Each lane is added independently using signed saturating arithmetic: the mathematical sum is formed in a wider signed domain and then clamped to the Q15 range [-32768, 32767]. One accepted input transaction produces one output transaction containing all four saturated sums.

## Interface

### RTL interface

```systemverilog
module q15_vector_add_4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_a0,
    input  logic [15:0] in_a1,
    input  logic [15:0] in_a2,
    input  logic [15:0] in_a3,
    input  logic [15:0] in_b0,
    input  logic [15:0] in_b1,
    input  logic [15:0] in_b2,
    input  logic [15:0] in_b3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_c0,
    output logic [15:0] out_c1,
    output logic [15:0] out_c2,
    output logic [15:0] out_c3
);
```

### Behavior interface

```python
class Q15VectorAdd4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a0, in_a1, in_a2, in_a3, in_b0, in_b1, in_b2, in_b3):
        pass
```

Each call consumes one accepted input transaction and returns one `dict` with
exactly `out_c0`, `out_c1`, `out_c2`, and `out_c3`. Inputs and returned values
are Python integers in 0..65535 encoding the signed Q15 port bit patterns,
not Python floating-point values. All four lanes belong to the same response.
An all-zero result is still a complete dictionary, never `None` or an empty
list. There is no algorithmic history between calls; `reset()` restores the
initial model state without producing a response.

## Modules

- `q15_vector_add_4`: top-level module. Accepts one 4-lane Q15 input pair and produces one 4-lane Q15 saturated-sum output.

## Functional Behavior

Each input port `in_a0`..`in_a3` and `in_b0`..`in_b3` is a 16-bit two's-complement signed integer representing one Q15 sample. The binary point is not used explicitly by this module; arithmetic is ordinary signed integer addition with Q15 range limits.

For each lane `i` in `{0,1,2,3}`:

1. Interpret `in_ai` and `in_bi` as signed 16-bit integers in the range [-32768, 32767].
2. Form the exact mathematical sum in signed integer arithmetic:
   `sum_i = a_i + b_i`.
3. Saturate `sum_i` to the signed 16-bit Q15 range:
   - if `sum_i > 32767`, the result is `32767` (`16'h7FFF`);
   - else if `sum_i < -32768`, the result is `-32768` (`16'h8000`);
   - else the result is `sum_i`.
4. Drive the saturated result on the corresponding output port as a 16-bit two's-complement bit pattern:
   - lane 0 -> `out_c0`
   - lane 1 -> `out_c1`
   - lane 2 -> `out_c2`
   - lane 3 -> `out_c3`

The four lanes are independent. There is no cross-lane carry, rounding, scaling, or shared state.

Equivalent lane formulas:

- `out_c0 = sat_q15(signed(in_a0) + signed(in_b0))`
- `out_c1 = sat_q15(signed(in_a1) + signed(in_b1))`
- `out_c2 = sat_q15(signed(in_a2) + signed(in_b2))`
- `out_c3 = sat_q15(signed(in_a3) + signed(in_b3))`

where `sat_q15(x)` clamps `x` into [-32768, 32767].

Edge cases:
- `16'h7FFF + 16'h0001` outputs `16'h7FFF`.
- `16'h7FFF + 16'h7FFF` outputs `16'h7FFF`.
- `16'h8000 + 16'hFFFF` outputs `16'h8000`.
- `16'h8000 + 16'h8000` outputs `16'h8000`.
- `16'h0000 + y` outputs `y` if `y` is already within Q15 range, which it always is for a 16-bit signed input.

The module is a 1:1 transaction mapping: each accepted input transaction produces exactly one output transaction containing all four lane results.

## Reset Behavior

Reset is active-high and synchronous or asynchronous implementation choice is permitted if the reset behavior below is met.

Architectural state: none.

Output interface state after reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_c0 = 16'h0000`
- `out_c1 = 16'h0000`
- `out_c2 = 16'h0000`
- `out_c3 = 16'h0000`

After reset is deasserted, the module may accept a new input transaction immediately.
