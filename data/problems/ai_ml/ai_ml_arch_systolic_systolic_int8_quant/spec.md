# Transaction-Level INT8 MAC PE

This module is a single systolic processing element with two internal accumulator registers, `c1` and `c2`. Each accepted input transaction produces exactly one output transaction. The element forwards `in_a`, control fields, `id`, `last`, and `valid` metadata, and computes `out_b` and `out_c` according to one of two dataflows: output-stationary or weight-stationary. The arithmetic is signed integer multiply-accumulate. A one-bit `propagate` field selects which internal register is forwarded and which is updated. In output-stationary mode, a signed arithmetic right shift may be applied to the propagated register when the `propagate` value changes relative to the most recent accepted input transaction.

## Interface

### RTL interface

```systemverilog
module ai_ml_arch_systolic_systolic_int8_quant (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic signed [7:0]  in_a,
    input  logic signed [31:0] in_b,
    input  logic signed [31:0] in_d,

    input  logic        in_control_dataflow,
    input  logic        in_control_propagate,
    input  logic [4:0]  in_control_shift,

    input  logic [0:0]  in_id,
    input  logic        in_last,

    output logic        out_valid,
    input  logic        out_ready,
    output logic signed [7:0]  out_a,
    output logic signed [31:0] out_b,
    output logic signed [31:0] out_c,

    output logic        out_control_dataflow,
    output logic        out_control_propagate,
    output logic [4:0]  out_control_shift,

    output logic [0:0]  out_id,
    output logic        out_last,

    output logic        bad_dataflow
);
```

### Behavior interface

```python
class ai_ml_arch_systolic_systolic_int8_quant:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_a,
        in_b,
        in_d,
        in_control_dataflow,
        in_control_propagate,
        in_control_shift,
        in_id,
        in_last,
    ): ...
```

Each `process` call represents one accepted input and returns one dictionary
with exactly these keys:
- `out_a`
- `out_b`
- `out_c`
- `out_control_dataflow`
- `out_control_propagate`
- `out_control_shift`
- `out_id`
- `out_last`
- `bad_dataflow`

All arguments and results use unsigned port bit patterns: `in_a`/`out_a`
are 8-bit words, `in_b`/`in_d`/`out_b`/`out_c` are 32-bit words, shift is
5-bit, and the remaining fields are one-bit. Signed arithmetic interprets
these words as defined below; negative results are returned as their unsigned
two's-complement encodings. `bad_dataflow` is always 0. Zero-valued results
still include all nine keys, not `None` or a list. The model retains `c1`,
`c2`, and the previous propagate bit across calls. Construction and `reset()`
set those three values to zero; `reset()` returns `None`. Idle and output-stall
cycles do not require calls.

## Modules

- `ai_ml_arch_systolic_systolic_int8_quant`: top-level processing element containing state registers `c1`, `c2`, and the remembered previous `propagate` value used for shift-offset selection.

## Functional Behavior

All ports are integer bit patterns. `in_a` is signed 8-bit two's-complement. `in_b` and `in_d` are signed 32-bit two's-complement. `in_control_dataflow` and `in_control_propagate` are 1-bit unsigned control fields. `in_control_shift` is an unsigned 5-bit shift count in the range `[0, 31]`. `in_id` is a 1-bit unsigned tag. `in_last` is a 1-bit flag.

One accepted input transaction produces exactly one output transaction.

Architectural state:
- `c1`: signed 32-bit register
- `c2`: signed 32-bit register
- `last_propagate`: 1-bit register holding the `in_control_propagate` value from the most recent accepted input transaction

The module supports exactly two dataflow encodings:
- `in_control_dataflow = 0`: output-stationary
- `in_control_dataflow = 1`: weight-stationary

For every accepted input transaction, the following fields are passed through unchanged to the output transaction:
- `out_a = in_a`
- `out_control_dataflow = in_control_dataflow`
- `out_control_propagate = in_control_propagate`
- `out_control_shift = in_control_shift`
- `out_id = in_id`
- `out_last = in_last`

Define:
- `flip = 1` if `last_propagate != in_control_propagate`, else `0`
- `shift_offset = in_control_shift` if `flip = 1`, else `0`

`shift_offset` is used only in output-stationary mode.

Signed multiplication:
- Interpret `in_a` as signed 8-bit.
- In weight-stationary mode, when a register is used as a weight operand, use only its least-significant 8 bits, interpreted as a signed 8-bit value.
- The product is the signed 16-bit mathematical result of multiplying two signed 8-bit values.
- Before addition into a 32-bit accumulator or adder input, sign-extend the 16-bit product to signed 32-bit.
- All 32-bit additions and register writes wrap modulo `2^32`, interpreted as two's-complement signed results.

Signed right shift in output-stationary mode:
- The propagated register is shifted right by `shift_offset` using arithmetic right shift.
- Because `in_control_shift` is 5 bits, the shift count is always in `[0, 31]`.
- If `shift_offset = 0`, the shifted result equals the unshifted register value.

Behavior by dataflow and propagate value:

### 1. Output-stationary mode (`in_control_dataflow = 0`)

In this mode, `out_b = in_b` unchanged.

If `in_control_propagate = 1`:
1. `out_c` is the arithmetic-right-shifted value of old `c1` by `shift_offset`.
2. Compute `product32 = sign_extend_32( signed(in_a) * signed(low8(in_b)) )`, where `low8(in_b)` means bits `[7:0]` of `in_b` interpreted as signed 8-bit.
3. New `c2 = old c2 + product32`, wrapping to 32 bits.
4. New `c1 = in_d`.

If `in_control_propagate = 0`:
1. `out_c` is the arithmetic-right-shifted value of old `c2` by `shift_offset`.
2. Compute `product32 = sign_extend_32( signed(in_a) * signed(low8(in_b)) )`.
3. New `c1 = old c1 + product32`, wrapping to 32 bits.
4. New `c2 = in_d`.

### 2. Weight-stationary mode (`in_control_dataflow = 1`)

In this mode, `out_c` is the selected old internal register without shifting.

If `in_control_propagate = 1`:
1. `out_c = old c1`.
2. Let `weight8 = signed(low8(old c2))`.
3. Compute `product32 = sign_extend_32( signed(in_a) * weight8 )`.
4. `out_b = in_b + product32`, wrapping to 32 bits.
5. New `c1 = in_d`.
6. `c2` is unchanged.

If `in_control_propagate = 0`:
1. `out_c = old c2`.
2. Let `weight8 = signed(low8(old c1))`.
3. Compute `product32 = sign_extend_32( signed(in_a) * weight8 )`.
4. `out_b = in_b + product32`, wrapping to 32 bits.
5. New `c2 = in_d`.
6. `c1` is unchanged.

### 3. `bad_dataflow`

`bad_dataflow` is always `0`.

Worked example for shift-offset selection:
- Assume old `last_propagate = 0`, old `c1 = 64`, old `c2 = -8`.
- Input: output-stationary, `in_control_propagate = 1`, `in_control_shift = 2`.
- Since `last_propagate != in_control_propagate`, `shift_offset = 2`.
- `out_c = arithmetic_right_shift(old c1, 2) = 16`.
- The state update for `c1`/`c2` then proceeds from the selected output-stationary case above.
- After the transaction, `last_propagate` becomes `1`.

At the end of every accepted input transaction, update `last_propagate = in_control_propagate`.

### Source and transport boundary

The selected source is Gemmini `PE.scala`: its two registers, dataflow selection,
propagate selection and previous-propagate shift gating define the retained
transition structure. Signed 8-bit multiplicands with 32-bit modular state. There is no quantization, scale selection or requantization operation.

The supplied source is generic in `Arithmetic`; these concrete widths and
numeric rules are task choices, not a recovered Gemmini configuration. Reset
to zero is explicit in this task, unlike the source's uninitialized registers.
The task advances only on an accepted input and holds each response under
backpressure. Gemmini's native mesh PE instead uses valid-only wires: this is a
transaction-level derivative, not cycle-equivalent replacement of that mesh.
Pipeline depth, vector instructions, sparsity hardware and a complete array are
not claimed. The legacy module and directory names remain stable identifiers.

## Reset Behavior

The module uses synchronous active-high reset `rst`.

On reset:
- `c1 = 0`
- `c2 = 0`
- `last_propagate = 0`

Output registers after reset and before any accepted input transaction:
- `out_valid = 0`
- `in_ready` follows the handshake implementation and is not architectural state
- `out_a = 0`
- `out_b = 0`
- `out_c = 0`
- `out_control_dataflow = 0`
- `out_control_propagate = 0`
- `out_control_shift = 0`
- `out_id = 0`
- `out_last = 0`
- `bad_dataflow = 0`
