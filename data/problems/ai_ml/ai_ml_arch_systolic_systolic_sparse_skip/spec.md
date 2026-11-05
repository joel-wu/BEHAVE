# Transaction-Level Dense Signed 8x16 PE

This is a dense dual-register MAC processing element. For each accepted input transaction it forwards metadata (`a`, control, ID, and `last`), updates one of two internal accumulator/register slots (`c1`, `c2`), and emits one output transaction containing a forwarded `a`, a computed or forwarded `b`, a propagated `c`, forwarded control, forwarded ID, forwarded `last`, and a `bad_dataflow` flag. The arithmetic is a single multiply-accumulate per transaction using two's-complement integer arithmetic with modulo-2^16 narrowing. The element supports two dataflow modes selected by the 1-bit `in_control_dataflow` field: output-stationary and weight-stationary. A 1-bit `in_control_propagate` field selects which internal slot is propagated and which slot is updated.

## Interface

### RTL interface

```systemverilog
module systolic_sparse_skip_pe (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_a,
    input  logic [15:0] in_b,
    input  logic [15:0] in_d,

    input  logic        in_control_dataflow,
    input  logic        in_control_propagate,
    input  logic [4:0]  in_control_shift,

    input  logic [7:0]  in_id,
    input  logic        in_last,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_a,
    output logic [15:0] out_b,
    output logic [15:0] out_c,

    output logic        out_control_dataflow,
    output logic        out_control_propagate,
    output logic [4:0]  out_control_shift,

    output logic [7:0]  out_id,
    output logic        out_last,

    output logic        bad_dataflow
);
```

### Behavior interface

```python
class SystolicSparseSkipPE:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a, in_b, in_d,
                in_control_dataflow, in_control_propagate, in_control_shift,
                in_id, in_last):
        pass
```

Call `process(...)` once per accepted input transaction, not for idle or stalled
clock cycles. Arguments are unsigned port bit patterns. Every call returns one
dictionary with exactly these keys: `out_a`, `out_b`, `out_c`,
`out_control_dataflow`, `out_control_propagate`, `out_control_shift`, `out_id`,
`out_last`, and `bad_dataflow`. Values are unsigned integers of the corresponding
port widths: 8 bits for `out_a` and `out_id`, 16 for `out_b` and `out_c`, 5 for
`out_control_shift`, and 1 for the remaining fields. Signed arithmetic is encoded
in those bit patterns; the return is never a list or `None`, including for zero
inputs.

Construction and `reset()` set `c1`, `c2`, and `last_propagate` to zero.
`reset()` returns `None` without producing an output record. Ordinary calls
preserve and update the state according to the transition rules below.

## Modules

- `PEControl`: 1-bit `dataflow`, 1-bit `propagate`, and 5-bit `shift` control bundle.
- `MacUnit`: combinational multiply-accumulate unit computing `in_c + in_a * in_b`.
- `systolic_sparse_skip_pe`: top-level processing element with two internal 16-bit state registers `c1` and `c2`, plus one remembered 1-bit propagate value used to determine whether a shift is applied on output-stationary output selection.

## Functional Behavior

All ports are integer bit patterns.

Interpretation of widths:
- `in_a` is an 8-bit two's-complement integer for multiplication.
- `in_b` and `in_d` are 16-bit two's-complement integer bit patterns.
- `c1` and `c2` are internal 16-bit registers.
- `out_b` and `out_c` are 16-bit output bit patterns.
- `in_control_shift` is a 5-bit unsigned shift amount.
- `in_control_dataflow` encoding:
  - `0`: output-stationary
  - `1`: weight-stationary
- `in_control_propagate` encoding:
  - `0`: compute on `c1`, propagate `c2`
  - `1`: compute on `c2`, propagate `c1`

This block is 1:1: every accepted input transaction produces exactly one output transaction.

For one accepted input transaction, the output values and state update are computed from the old state values of `c1`, `c2`, and `last_propagate`.

Arithmetic rules:
1. Convert `in_a`, `in_b`, `in_d`, `c1`, and `c2` to signed integers of widths 8, 16, 16, 16, and 16 respectively.
2. A multiply-accumulate result is computed as signed integer addition `in_c + in_a * in_b_operand`.
3. After each MAC, narrow to 16 bits by modulo-2^16 wrap, then interpret the stored/output value only as a 16-bit bit pattern.
4. Right shift used for output-stationary propagated output is arithmetic right shift on the signed 16-bit selected register.
5. If the applied shift amount is 16 or greater, the arithmetic right-shift result is:
   - `0x0000` if the shifted signed value is non-negative
   - `0xFFFF` if the shifted signed value is negative

Shift selection:
1. `flip = 1` if `last_propagate != in_control_propagate`, else `flip = 0`.
2. `shift_offset = in_control_shift` when `flip = 1`; otherwise `shift_offset = 0`.
3. `last_propagate` is updated to `in_control_propagate` after the transaction completes.

Forwarded outputs, independent of dataflow:
- `out_a = in_a`
- `out_control_dataflow = in_control_dataflow`
- `out_control_propagate = in_control_propagate`
- `out_control_shift = in_control_shift`
- `out_id = in_id`
- `out_last = in_last`

Dataflow-specific behavior:

### Output-stationary mode (`in_control_dataflow = 0`)

If `in_control_propagate = 1`:
1. `out_c` is the arithmetic-right-shifted value of old `c1` by `shift_offset`, narrowed to 16 bits.
2. `out_b = in_b`.
3. Compute `mac = old_c2 + in_a * in_b`.
4. Store `c2 = mac` narrowed to 16 bits.
5. Store `c1 = in_d`.

If `in_control_propagate = 0`:
1. `out_c` is the arithmetic-right-shifted value of old `c2` by `shift_offset`, narrowed to 16 bits.
2. `out_b = in_b`.
3. Compute `mac = old_c1 + in_a * in_b`.
4. Store `c1 = mac` narrowed to 16 bits.
5. Store `c2 = in_d`.

### Weight-stationary mode (`in_control_dataflow = 1`)

If `in_control_propagate = 1`:
1. `out_c = old_c1`.
2. Compute `mac = signed(in_b) + signed(in_a) * signed(old_c2)`.
3. `out_b = mac` narrowed to 16 bits.
4. Store `c1 = in_d`.
5. `c2` is unchanged.

If `in_control_propagate = 0`:
1. `out_c = old_c2`.
2. Compute `mac = signed(in_b) + signed(in_a) * signed(old_c1)`.
3. `out_b = mac` narrowed to 16 bits.
4. Store `c2 = in_d`.
5. `c1` is unchanged.

`bad_dataflow` is always `0`. No other `in_control_dataflow` encoding exists because the field is 1 bit wide.

Worked example for shift application:
- Old `last_propagate = 0`, current `in_control_propagate = 1`, `in_control_shift = 3`, old `c1 = 0xFFF0` (-16 signed).
- `flip = 1`, so `shift_offset = 3`.
- In output-stationary mode with `in_control_propagate = 1`, `out_c = arithmetic_shift_right(-16, 3) = -2 = 0xFFFE`.

### Source and transport boundary

The selected source is Gemmini `PE.scala`: its two registers, dataflow selection,
propagate selection and previous-propagate shift gating define the retained
transition structure. Signed 8-by-16 multiplication and 16-bit modular state. Zero inputs take the same transition as other inputs; no sparse-skip mechanism is specified.

The supplied source is generic in `Arithmetic`; these concrete widths and
numeric rules are task choices, not a recovered Gemmini configuration. Reset
to zero is explicit in this task, unlike the source's uninitialized registers.
The task advances only on an accepted input and holds each response under
backpressure. Gemmini's native mesh PE instead uses valid-only wires: this is a
transaction-level derivative, not cycle-equivalent replacement of that mesh.
Pipeline depth, vector instructions, sparsity hardware and a complete array are
not claimed. The legacy module and directory names remain stable identifiers.

## Reset Behavior

Reset is synchronous active-high.

On reset:
- `c1 = 0x0000`
- `c2 = 0x0000`
- `last_propagate = 0`

Architectural outputs after reset and before any accepted input are determined only by the handshake implementation. The transaction-level contract is that the first accepted input after reset observes the old state values listed above.
