# Transaction-Level Signed 16x32 MAC PE

This module implements one signed integer MAC processing element with two internal accumulator registers. Each accepted input transaction forwards selected metadata unchanged and updates or propagates one of the two internal registers according to the `in_control_dataflow` and `in_control_propagate` fields. The arithmetic operation is a single multiply-accumulate, `mac = in_c + in_a * in_b`, with all arithmetic performed on signed integers and the result wrapped to the output width. The module supports output-stationary and weight-stationary modes and uses the previous accepted transaction's propagate bit to determine whether the configured right shift is applied to the propagated output in output-stationary mode.

## Interface

### RTL interface

```systemverilog
module ai_ml_arch_vector_unit_vector_lane_vfmacc (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [15:0] in_a,
    input  logic signed [31:0] in_b,
    input  logic signed [31:0] in_d,

    input  logic         in_control_dataflow,
    input  logic         in_control_propagate,
    input  logic [4:0]   in_control_shift,

    input  logic [7:0]   in_id,
    input  logic         in_last,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [15:0] out_a,
    output logic signed [31:0] out_b,
    output logic signed [31:0] out_c,

    output logic         out_control_dataflow,
    output logic         out_control_propagate,
    output logic [4:0]   out_control_shift,

    output logic [7:0]   out_id,
    output logic         out_last,
    output logic         bad_dataflow
);
```

### Behavior interface

```python
class VectorLaneVFMACC:
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

Each `process(...)` call represents one accepted input transaction and returns
one dictionary with exactly these keys:
`out_a`, `out_b`, `out_c`, `out_control_dataflow`, `out_control_propagate`,
`out_control_shift`, `out_id`, `out_last`, `bad_dataflow`.
Arguments and returned integers are unsigned port bit patterns: `out_a` is
16 bits, `out_b` and `out_c` are 32 bits, `out_control_shift` is 5 bits,
`out_id` is 8 bits, and the remaining fields are 1 bit. Signed arithmetic is
defined below. A zero-valued response is still a complete dictionary, not
`None` or a list.

Reuse the same instance across calls: `c1`, `c2`, and the remembered propagate
bit persist between accepted transactions. `reset()` restores their specified
zero values and returns `None`. Clock cycles, stalls and output consumption do
not cause additional calls or records.

## Modules

- `ai_ml_arch_vector_unit_vector_lane_vfmacc`: top-level vector lane with two internal state registers `c1` and `c2` and one remembered propagate bit `last_propagate`.
- `MacUnit`: combinational multiply-accumulate subfunction computing `in_c + in_a * in_b`.

## Functional Behavior

All ports carry two's-complement integer bit patterns.

Bit widths:
- `in_a`, `out_a`: 16-bit signed
- `in_b`, `in_d`, `out_b`, `out_c`: 32-bit signed
- `in_control_dataflow`, `out_control_dataflow`: 1 bit
- `in_control_propagate`, `out_control_propagate`: 1 bit
- `in_control_shift`, `out_control_shift`: 5 bits
- `in_id`, `out_id`: 8 bits
- `in_last`, `out_last`: 1 bit
- `bad_dataflow`: 1 bit

The module is a single-clock 1:1 transaction block. One accepted input transaction produces exactly one output transaction. The implementation may deassert `in_ready` to avoid overwriting a pending response. It holds output valid and payload stable until consumption; an output and the next input may transfer together.

Architectural state:
- `c1`: 32-bit signed register
- `c2`: 32-bit signed register
- `last_propagate`: 1-bit register storing the `in_control_propagate` value from the most recent accepted input transaction

For each accepted input transaction, the following steps occur in order using the pre-transaction state values of `c1`, `c2`, and `last_propagate`.

1. Metadata passthrough:
   - `out_a = in_a`
   - `out_control_dataflow = in_control_dataflow`
   - `out_control_propagate = in_control_propagate`
   - `out_control_shift = in_control_shift`
   - `out_id = in_id`
   - `out_last = in_last`

2. Shift-offset calculation for output-stationary mode:
   - `flip = 1` if `last_propagate != in_control_propagate`, else `0`
   - `shift_offset = in_control_shift` when `flip = 1`, else `0`
   - The right shift used below is an arithmetic right shift of a signed 32-bit value.
   - If `shift_offset >= 32`, the shifted value is `0` for non-negative inputs and `-1` for negative inputs. In this design `shift_offset` is 5 bits, so the only possible values are 0 through 31.

3. Dataflow decode:
   - Output-stationary mode is selected when `in_control_dataflow == 0`.
   - Weight-stationary mode is selected when `in_control_dataflow == 1`.
   - `bad_dataflow = 0` for both encodings. Since the control field is 1 bit wide, no other encoding exists.

4. Output-stationary mode (`in_control_dataflow == 0`):

   Let `propagate = in_control_propagate`.

   - If `propagate == 1`:
     - `out_c` is the arithmetic-right-shifted old `c1` by `shift_offset`, then truncated to 32 bits.
     - `out_b = in_b`
     - `mac = old_c2 + in_a * in_b`
     - `c2` is updated to `mac` wrapped to signed 32 bits.
     - `c1` is updated to `in_d` wrapped to signed 32 bits.
   - If `propagate == 0`:
     - `out_c` is the arithmetic-right-shifted old `c2` by `shift_offset`, then truncated to 32 bits.
     - `out_b = in_b`
     - `mac = old_c1 + in_a * in_b`
     - `c1` is updated to `mac` wrapped to signed 32 bits.
     - `c2` is updated to `in_d` wrapped to signed 32 bits.

   The multiplication uses signed operands `in_a` (16-bit signed) and `in_b` (32-bit signed). The product is formed as a mathematical signed integer product, then added to the selected old accumulator value as a mathematical signed integer sum, and the final result is wrapped modulo `2^32` and reinterpreted as signed two's complement for storage and for any output that uses that stored value later.

5. Weight-stationary mode (`in_control_dataflow == 1`):

   Let `propagate = in_control_propagate`.

   - If `propagate == 1`:
     - `out_c = old_c1`
     - `out_b = in_b + in_a * old_c2`
     - `c1` is updated to `in_d` wrapped to signed 32 bits.
     - `c2` is unchanged.
   - If `propagate == 0`:
     - `out_c = old_c2`
     - `out_b = in_b + in_a * old_c1`
     - `c2` is updated to `in_d` wrapped to signed 32 bits.
     - `c1` is unchanged.

   For `out_b`, `in_b` is the addend (`in_c` of the MAC), `in_a` is the multiplicand, and the selected old internal register is the multiplier. The result is wrapped modulo `2^32` and returned as a 32-bit signed bit pattern.

6. State update after every accepted input transaction:
   - `last_propagate` is updated to `in_control_propagate`.
   - Any register not explicitly updated by the selected mode and propagate case retains its previous value.

Output encoding:
- All returned output port values are the corresponding unsigned bit patterns of their stated widths.
- For example, a negative 32-bit signed result `-1` is returned as `0xFFFF_FFFF`.

Edge cases:
- Zero, negative, and most-negative signed operands are all legal and use ordinary two's-complement signed arithmetic with wrap modulo `2^32`.
- `in_control_shift = 0` causes no shift.
- In output-stationary mode, the configured shift is applied only on transactions where `in_control_propagate` differs from `last_propagate`; otherwise propagated `out_c` is unshifted.
- In weight-stationary mode, `in_control_shift` has no effect on arithmetic or outputs.
- `bad_dataflow` is always `0`.

### Source and transport boundary

The selected source is Gemmini `PE.scala`: its two registers, dataflow selection,
propagate selection and previous-propagate shift gating define the retained
transition structure. Signed integer multiplication and 32-bit modular state, not a floating-point vector instruction or fused FMA.

The supplied source is generic in `Arithmetic`; these concrete widths and
numeric rules are task choices, not a recovered Gemmini configuration. Reset
to zero is explicit in this task, unlike the source's uninitialized registers.
The task advances only on an accepted input and holds each response under
backpressure. Gemmini's native mesh PE instead uses valid-only wires: this is a
transaction-level derivative, not cycle-equivalent replacement of that mesh.
Pipeline depth, vector instructions, sparsity hardware and a complete array are
not claimed. The legacy module and directory names remain stable identifiers.

## Reset Behavior

Reset is synchronous active-high `rst`.

On a cycle where `rst` is high, the architectural state is set to:
- `c1 = 0`
- `c2 = 0`
- `last_propagate = 0`

After reset and before the first accepted input transaction:
- `in_ready = 1`
- No output transaction is produced until an input transaction is accepted.
- When an output transaction is produced after reset, it is computed from the reset state unless earlier accepted inputs have updated the state.
