# Transaction-Level Unsigned Dual-Register PE

This block is a single processing element for a systolic array. On each accepted input transaction it forwards the A operand and sideband control unchanged, performs one multiply-accumulate operation, and updates one of two internal accumulation registers. The block supports two dataflows: output-stationary and weight-stationary. A 1-bit `propagate` selector chooses which internal register is forwarded and which one is updated. In output-stationary mode, the forwarded accumulator is right-shifted before truncation to the output width; in weight-stationary mode, the forwarded accumulator is passed without shift. The arithmetic is integer modulo arithmetic with explicit truncation to the declared port widths.

## Interface

### RTL interface

```systemverilog
module ai_ml_arch_systolic_pe_basic_mac (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_a,
    input  logic [15:0]  in_b,
    input  logic [15:0]  in_d,
    input  logic         in_control_dataflow,
    input  logic         in_control_propagate,
    input  logic [4:0]   in_control_shift,
    input  logic [3:0]   in_id,
    input  logic         in_last,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_a,
    output logic [15:0]  out_b,
    output logic [15:0]  out_c,
    output logic         out_control_dataflow,
    output logic         out_control_propagate,
    output logic [4:0]   out_control_shift,
    output logic [3:0]   out_id,
    output logic         out_last,
    output logic         bad_dataflow
);
```

### Behavior interface

```python
class AiMlArchSystolicPeBasicMac:
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

Each call represents one accepted input and returns one dictionary with exactly
the keys `out_a`, `out_b`, `out_c`, `out_control_dataflow`,
`out_control_propagate`, `out_control_shift`, `out_id`, `out_last` and
`bad_dataflow`. Arguments and returned values are unsigned integers with the
port widths specified below; `bad_dataflow` is always zero. Every call returns
the complete dictionary, including all-zero results, never `None` or a list.
Handshake signals are not returned. The registers `c1`, `c2` and
`last_propagate` persist between calls and update in the order defined below.
Construction and `reset()` clear them to zero; `reset()` returns `None` and
produces no output record.

## Modules

- `ai_ml_arch_systolic_pe_basic_mac`: top-level processing element with two internal registers `c1` and `c2` and a single MAC datapath.

## Functional Behavior

Port widths and integer interpretation:

- `in_a` and `out_a` are 8-bit unsigned bit patterns.
- `in_b`, `in_d`, `out_b`, and `out_c` are 16-bit unsigned bit patterns.
- `in_control_dataflow`, `in_control_propagate`, `out_control_dataflow`, and `out_control_propagate` are 1-bit unsigned values.
- `in_control_shift` and `out_control_shift` are 5-bit unsigned values.
- `in_id` and `out_id` are 4-bit unsigned values.
- `in_last` and `out_last` are 1-bit unsigned values.
- Internal registers `c1` and `c2` are 16-bit registers.

Handshake behavior:

- The block is single-clock and accepts one input transaction per `in_valid && in_ready`.
- The implementation may deassert `in_ready` when it cannot retain another response. It does not accept inputs during reset.
- Each accepted input transaction produces exactly one output transaction.
- `out_valid` and the complete output payload remain stable until `out_ready` accepts the response. An output and the next input may transfer on the same edge.

Forwarded sideband fields:

- `out_a = in_a`.
- `out_control_dataflow = in_control_dataflow`.
- `out_control_propagate = in_control_propagate`.
- `out_control_shift = in_control_shift`.
- `out_id = in_id`.
- `out_last = in_last`.

Internal state used for shift-offset selection:

- The block stores the `in_control_propagate` bit from the previous accepted input in a 1-bit state register `last_propagate`.
- For the current accepted input, `flip = 1` if `last_propagate != in_control_propagate`, else `flip = 0`.
- `shift_offset = in_control_shift` when `flip = 1`, else `0`.
- After the current accepted input is processed, `last_propagate` is updated to `in_control_propagate`.

MAC arithmetic:

- The MAC datapath computes `(mac_c + in_a * mac_b) mod 2^16`.
- `in_a` is zero-extended to 16 bits before multiplication.
- `mac_b` and `mac_c` are 16-bit unsigned operands selected by the dataflow rules below.
- The multiplication is performed in integer arithmetic with enough precision to hold the full product, then the sum is reduced modulo `2^16`.
- Every value written to `c1`, `c2`, `out_b`, and `out_c` is truncated to 16 bits.

Right shift used in output-stationary mode:

- The shift is a logical right shift on a 16-bit unsigned value.
- `shift_offset` is 5 bits, so the only possible shift counts are 0 through 31.
- If `shift_offset >= 16`, the shifted result is `0`.
- The shifted result is then truncated to 16 bits, which leaves it unchanged.

Dataflow encoding:

- `in_control_dataflow = 0` selects output-stationary mode.
- `in_control_dataflow = 1` selects weight-stationary mode.
- `bad_dataflow = 0` for both encodings above.

Propagate encoding:

- `in_control_propagate = 1` selects the `PROPAGATE` branch.
- `in_control_propagate = 0` selects the `COMPUTE` branch.

Per-transaction behavior in output-stationary mode (`in_control_dataflow = 0`):

1. If `in_control_propagate = 1`:
   - `out_c = c1 >> shift_offset` using the logical-right-shift rule above.
   - `out_b = in_b`.
   - `mac_b = in_b`.
   - `mac_c = c2`.
   - `new_c2 = (c2 + in_a * in_b) mod 2^16`.
   - `new_c1 = in_d`.
2. If `in_control_propagate = 0`:
   - `out_c = c2 >> shift_offset` using the logical-right-shift rule above.
   - `out_b = in_b`.
   - `mac_b = in_b`.
   - `mac_c = c1`.
   - `new_c1 = (c1 + in_a * in_b) mod 2^16`.
   - `new_c2 = in_d`.

Per-transaction behavior in weight-stationary mode (`in_control_dataflow = 1`):

1. If `in_control_propagate = 1`:
   - `out_c = c1`.
   - `mac_b = c2`.
   - `mac_c = in_b`.
   - `out_b = (in_b + in_a * c2) mod 2^16`.
   - `new_c1 = in_d`.
   - `c2` is unchanged.
2. If `in_control_propagate = 0`:
   - `out_c = c2`.
   - `mac_b = c1`.
   - `mac_c = in_b`.
   - `out_b = (in_b + in_a * c1) mod 2^16`.
   - `new_c2 = in_d`.
   - `c1` is unchanged.

State update order:

- `out_b` and `out_c` are computed from the old values of `c1`, `c2`, and `last_propagate`.
- `c1`, `c2`, and `last_propagate` are updated after output computation for the accepted input.
- A register not assigned by the selected branch retains its previous value.

Invalid dataflow encodings:

- No invalid encoding exists because `in_control_dataflow` is 1 bit.
- `bad_dataflow` is always `0`.

### Source and transport boundary

The selected source is Gemmini `PE.scala`: its two registers, dataflow selection,
propagate selection and previous-propagate shift gating define the retained
transition structure. Unsigned 8-bit input, 16-bit modular state and output arithmetic.

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
- `in_ready = 0` while reset is asserted
- `out_valid = 0`
- `out_a = 0x00`
- `out_b = 0x0000`
- `out_c = 0x0000`
- `out_control_dataflow = 0`
- `out_control_propagate = 0`
- `out_control_shift = 0x00`
- `out_id = 0x0`
- `out_last = 0`
- `bad_dataflow = 0`
