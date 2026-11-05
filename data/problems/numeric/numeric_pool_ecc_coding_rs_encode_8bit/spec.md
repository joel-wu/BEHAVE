# One-parity single-erasure recovery

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module rs_m1_single_loss_recover64 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [3:0]   in_original_count,
    input  logic [7:0]   in_present_mask,
    input  logic [511:0] in_recovery_data,
    input  logic [511:0] in_original_data0,
    input  logic [511:0] in_original_data1,
    input  logic [511:0] in_original_data2,
    input  logic [511:0] in_original_data3,
    input  logic [511:0] in_original_data4,
    input  logic [511:0] in_original_data5,
    input  logic [511:0] in_original_data6,
    input  logic [511:0] in_original_data7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [2:0]   out_missing_index,
    output logic [511:0] out_recovered_data,
    output logic [2:0]   out_status
);
```

### Behavior interface

```python
class RsM1SingleLossRecover64:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_original_count, in_present_mask, in_recovery_data, in_original_data0, in_original_data1, in_original_data2, in_original_data3, in_original_data4, in_original_data5, in_original_data6, in_original_data7): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_missing_index`, `out_status`: 3.
- `out_recovered_data`: 512.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`rs_m1_single_loss_recover64` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

original_count is a four-bit value. Counts 1..8 activate that many 512-bit original shards; mask bit i says whether active shard i is present. The recovery shard is their XOR parity. Inactive ports and mask bits are ignored.

Return status=1 if count is zero or greater than eight; otherwise status=2 if no active shard is missing, status=3 if more than one is missing, or status=0 for exactly one missing shard. On status=0, return its index and recovery_data XOR every present active shard. The data on the missing port is ignored. On every nonzero status, index and recovered_data are zero.

This preserves the source's M=1 recovery case. It is not a general Reed-Solomon encoder or multi-erasure decoder. Each shard is 64 bytes and all eight original ports are usable.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
