# Transaction-Level Binary32 Dual-Register PE

This is a transaction-level dual-register processing element. Each accepted input transaction produces exactly one output transaction. The block forwards `a`, control, `id`, and `last`; it updates one of two internal 32-bit state registers `c1` and `c2`; and it computes either output-stationary or weight-stationary multiply-accumulate behavior selected per transaction by `in_control_dataflow`. Arithmetic uses binary32 bit patterns with the task policy stated below. The two state registers are alternately used for propagation versus accumulation according to `in_control_propagate`, and output-stationary mode additionally applies a logical right shift to the propagated register when the propagate selector changes relative to the most recently accepted transaction.

## Interface

### RTL interface

```systemverilog
module AiMlArchSystolicPePipelinedFp (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a,
    input  logic [31:0] in_b,
    input  logic [31:0] in_d,
    input  logic        in_control_dataflow,
    input  logic        in_control_propagate,
    input  logic [4:0]  in_control_shift,
    input  logic [7:0]  in_id,
    input  logic        in_last,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_a,
    output logic [31:0] out_b,
    output logic [31:0] out_c,
    output logic        out_control_dataflow,
    output logic        out_control_propagate,
    output logic [4:0]  out_control_shift,
    output logic [7:0]  out_id,
    output logic        out_last,
    output logic        bad_dataflow
);
```

`in_a`, `in_b`, `in_d`, `out_a`, `out_b`, and `out_c` are raw FP32 bit patterns.  
`in_control_dataflow` encoding: `0` = output-stationary, `1` = weight-stationary.  
`in_control_propagate` encoding: `0` = compute on `c1` / propagate `c2`, `1` = compute on `c2` / propagate `c1`.  
`in_control_shift` is a 5-bit unsigned logical shift count.  
`in_id` is an 8-bit tag forwarded unchanged.  
`in_last` is a 1-bit flag forwarded unchanged.  
`bad_dataflow` is always `0`.

### Behavior interface

```python
class AiMlArchSystolicPePipelinedFp:
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

Each call represents one accepted input and returns exactly one dictionary
with keys `out_a`, `out_b`, `out_c`, `out_control_dataflow`,
`out_control_propagate`, `out_control_shift`, `out_id`, `out_last` and
`bad_dataflow`. Arguments and results are unsigned port encodings: arithmetic
words use 32 bits, shift 5 bits, id 8 bits, and control/last/error fields
1 bit. Use integer binary32 encodings, not Python floats. Forwarded words
retain their bit patterns; arithmetic follows the policy below. Every call
returns the full dictionary, including for zero or exceptional FP operands,
never `None` or a list. No handshake signals are returned. The two registers
and previous-propagate selector persist between calls. Construction and
`reset()` clear them as specified below; `reset()` returns `None`.

## Modules

- `AiMlArchSystolicPePipelinedFp`: top-level processing element with two internal FP32 state registers `c1` and `c2`.
- The arithmetic datapath multiplies, rounds, adds, then rounds again under the policy below. It is not a fused multiply-add.

## Functional Behavior

One accepted input transaction produces exactly one output transaction.

Architectural state:
- `c1`: 32-bit register holding a raw FP32 bit pattern.
- `c2`: 32-bit register holding a raw FP32 bit pattern.
- `last_s`: 1-bit register holding the `in_control_propagate` value from the most recently accepted input transaction.

For every accepted input:
1. `out_a = in_a`.
2. `out_control_dataflow = in_control_dataflow`.
3. `out_control_propagate = in_control_propagate`.
4. `out_control_shift = in_control_shift`.
5. `out_id = in_id`.
6. `out_last = in_last`.
7. `bad_dataflow = 0`.

Before any state update, define:
- `flip = 1` if `last_s != in_control_propagate`, else `0`.
- `shift_offset = in_control_shift` when `flip = 1`, else `0`.
- `shifted_c1` is `c1` logically right-shifted by `shift_offset`.
- `shifted_c2` is `c2` logically right-shifted by `shift_offset`.

The logical right shift is applied to the 32-bit register bit pattern, not to the represented floating-point numeric value. Since `in_control_shift` is 5 bits wide, its legal range is 0 to 31 inclusive.

FP32 arithmetic:
- `mul_ab = fp32_mul(in_a, X)` where `X` is the selected multiplicand for the mode.
- `mac_y = fp32_add(C, mul_ab)` where `C` is the selected addend for the mode.
- `fp32_mul` and `fp32_add` separately round to nearest, ties to even. Subnormal arithmetic inputs retain their numerical value; underflow is gradual with ties-to-even rounding also at the zero/subnormal and subnormal/normal boundaries. Output encodings are compared exactly. Overflow yields signed infinity. A NaN operand or invalid operation (zero times infinity or opposite infinities added) yields canonical 0x7fc00000 (a project sign/payload convention). Multiplication uses the XOR sign for zero; exact cancellation in addition gives positive zero, except adding two negative zeros gives negative zero. Forwarding and raw-word shifting do not perform arithmetic canonicalization.

Dataflow selection is per input transaction:

### Output-stationary mode
This mode is selected when `in_control_dataflow = 0`.

If `in_control_propagate = 1`:
- `out_c = shifted_c1`.
- `out_b = in_b`.
- Compute `mul_ab = fp32_mul(in_a, in_b)`.
- Compute `new_c2 = fp32_add(c2, mul_ab)`.
- Compute `new_c1 = in_d`.
- After the transaction, update `c1 = new_c1`, `c2 = new_c2`.

If `in_control_propagate = 0`:
- `out_c = shifted_c2`.
- `out_b = in_b`.
- Compute `mul_ab = fp32_mul(in_a, in_b)`.
- Compute `new_c1 = fp32_add(c1, mul_ab)`.
- Compute `new_c2 = in_d`.
- After the transaction, update `c1 = new_c1`, `c2 = new_c2`.

Worked example for the shift rule: if the old `last_s = 0`, the new `in_control_propagate = 1`, and `in_control_shift = 3`, then `flip = 1`, `shift_offset = 3`, and `out_c` is `c1 >> 3` in the `in_control_propagate = 1` case. If the next accepted input again has `in_control_propagate = 1`, then `flip = 0`, `shift_offset = 0`, and `out_c` is the unshifted `c1`.

### Weight-stationary mode
This mode is selected when `in_control_dataflow = 1`.

If `in_control_propagate = 1`:
- `out_c = c1`.
- Compute `mul_ab = fp32_mul(in_a, c2)`.
- Compute `out_b = fp32_add(in_b, mul_ab)`.
- Compute `new_c1 = in_d`.
- `c2` is unchanged.
- After the transaction, update `c1 = new_c1`, `c2 = c2`.

If `in_control_propagate = 0`:
- `out_c = c2`.
- Compute `mul_ab = fp32_mul(in_a, c1)`.
- Compute `out_b = fp32_add(in_b, mul_ab)`.
- `c1` is unchanged.
- Compute `new_c2 = in_d`.
- After the transaction, update `c1 = c1`, `c2 = new_c2`.

No shift is applied in weight-stationary mode.

### State update order
All reads of `c1`, `c2`, and `last_s` use their values from before the current accepted input. After output values are determined:
- `c1` and `c2` are updated according to the selected mode and propagate setting.
- `last_s` is updated to `in_control_propagate`.

### Dataflow legality
Only `in_control_dataflow` values `0` and `1` are legal because the port is 1 bit wide. For both legal values, `bad_dataflow = 0`.

### Source and transport boundary

The selected source is Gemmini `PE.scala`: its two registers, dataflow selection,
propagate selection and previous-propagate shift gating define the retained
transition structure. Binary32 multiply followed by separately rounded addition, not fused FMA. The explicitly specified raw-word shift is a task extension, not floating-point division by a power of two.

The supplied source is generic in `Arithmetic`; these concrete widths and
numeric rules are task choices, not a recovered Gemmini configuration. Reset
to zero is explicit in this task, unlike the source's uninitialized registers.
The task advances only on an accepted input and holds each response under
backpressure. Gemmini's native mesh PE instead uses valid-only wires: this is a
transaction-level derivative, not cycle-equivalent replacement of that mesh.
Pipeline depth, vector instructions, sparsity hardware and a complete array are
not claimed. The legacy module and directory names remain stable identifiers.

## Reset Behavior

Reset is asynchronous and active-high. Assertion immediately cancels pending
transactions and initializes all architectural state to zero, without waiting
for a rising clock edge:
- `c1 = 32'h00000000`
- `c2 = 32'h00000000`
- `last_s = 1'b0`

While reset is asserted, the externally visible stored output registers, if implemented, shall hold:
- `out_a = 32'h00000000`
- `out_b = 32'h00000000`
- `out_c = 32'h00000000`
- `out_control_dataflow = 1'b0`
- `out_control_propagate = 1'b0`
- `out_control_shift = 5'b00000`
- `out_id = 8'h00`
- `out_last = 1'b0`
- `bad_dataflow = 1'b0`

After reset deasserts and before the first accepted input, the first transaction uses the reset values of `c1`, `c2`, and `last_s`.
