# EncoderIndexLifecycle

Maintain an encoder index-subscription lifecycle and the count/status state updated by a delivered index event. Registration is explicit and a delivered event unsubscribes exactly once. Commands serialize subscription, count loading and events; this is not a raw A/B quadrature decoder or a GPIO synchronizer.

## Interface

### RTL interface

```systemverilog
module EncoderIndexLifecycle (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [1:0] in_op,
    input logic in_use_index,
    input logic in_lockin_only,
    input logic in_override_enable,
    input logic in_use_index_offset,
    input logic in_pre_calibrated,
    input logic in_anticogging_pre_calibrated,
    input logic signed [31:0] in_offset_count,
    input logic signed [31:0] in_circular_count,
    input logic signed [31:0] in_linear_count,
    output logic out_valid,
    input logic out_ready,
    output logic signed [31:0] out_circular_count,
    output logic signed [31:0] out_linear_count,
    output logic out_is_ready,
    output logic out_index_found,
    output logic out_subscribed,
    output logic out_anticogging_valid
);
endmodule
```

### Behavior interface

```python
class EncoderIndexLifecycle:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_op, in_use_index, in_lockin_only, in_override_enable, in_use_index_offset, in_pre_calibrated, in_anticogging_pre_calibrated, in_offset_count, in_circular_count, in_linear_count): ...
```

Call `process` once per accepted input. Every command, including a query or an
ignored index event, returns one dictionary:

```python
{"out_circular_count": ..., "out_linear_count": ..., "out_is_ready": ...,
 "out_index_found": ..., "out_subscribed": ..., "out_anticogging_valid": ...}
```

Returned fields are Python integers holding unsigned port bit patterns.
Counts use 32-bit two's-complement encodings; status bits are `0` or `1`.
Reset is provided separately by `reset`.

## Modules

- `EncoderIndexLifecycle`: the stateful command/selection block.

## Functional Behavior

The architectural state comprises signed 32-bit circular and linear count
words and four bits: ready, index-found, subscribed and anticogging-valid.
Counts use two's-complement bit patterns; this block does not advance or
normalize them. The count-load command supplies values from an external
position tracker. `in_offset_count` is the already-converted integer index
offset in counts; conversion from fractional revolutions and counts/revolution
is outside this task. No floating-point arithmetic or count overflow occurs.

Each accepted command samples its configuration inputs and performs exactly
one operation according to `in_op`:

- **0, query:** leave all state unchanged.
- **1, subscription update:** set subscribed to
  `in_use_index && (in_override_enable || !in_lockin_only)`; leave all other
  state unchanged. Registration is assumed successful; platform allocation
  failure reporting is outside this task.
- **2, count load:** copy `in_circular_count` and `in_linear_count` into
  the corresponding state; leave all four status bits unchanged.
- **3, index edge:** if unsubscribed, do nothing. Otherwise deliver the
  callback and clear subscribed. If `in_use_index` is 1, also set circular
  count to zero, load linear count from `in_offset_count` only when
  `in_use_index_offset` is 1, set ready to `in_pre_calibrated`, and set
  index-found. If both `in_pre_calibrated` and
  `in_anticogging_pre_calibrated` are 1, set anticogging-valid; otherwise
  retain anticogging-valid. If `in_use_index` is 0, only subscribed changes.

All output fields report the resulting state. Configuration inputs do not
alter state except where the selected command uses them. Queries and further
index edges cannot re-enable a consumed subscription; operation 1 is required.
Index-found and anticogging-valid remain set until reset once set. Readiness
may be cleared by a later delivered, enabled, non-precalibrated index event.
All input bit patterns and all four operation codes are defined.

Each accepted input (`in_valid && in_ready` on a rising clock edge)
produces exactly one ordered output. State changes once per accepted input,
never for an unaccepted offer. Output valid and all output data remain stable
until accepted with `out_ready`. No fixed latency is required. Inputs are
stable while offered and stalled. There is no output without an input.

## Reset Behavior

`rst` is synchronous, active high. Each rising edge with `rst=1` clears
all architectural state and output registers to zero and discards any pending
output. During reset `in_ready=0` and `out_valid=0`; no transaction transfers.
The first post-reset command starts from this cleared state.
