# Stateful four-word XOR summer

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module xor_summer64x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_finalize,
    input  logic        in_dest_init,
    input  logic [63:0] in_dest_word,
    input  logic [2:0]  in_src_count,
    input  logic [63:0] in_src0_word,
    input  logic [63:0] in_src1_word,
    input  logic [63:0] in_src2_word,
    input  logic [63:0] in_src3_word,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_dest_word,
    output logic        out_waiting_valid,
    output logic [63:0] out_waiting_word
);
```

### Behavior interface

```python
class XorSummer64x4:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_finalize, in_dest_init, in_dest_word, in_src_count, in_src0_word, in_src1_word, in_src2_word, in_src3_word): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_dest_word`, `out_waiting_word`: 64.
- `out_waiting_valid`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`xor_summer64x4` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

State consists of a 64-bit destination, a pending-valid bit and a pending 64-bit word. Apply each command in order: if dest_init=1, replace the destination with dest_word and clear both pending fields. Consume the first min(src_count,4) source words in port order. A source arriving with no pending word becomes pending; otherwise XOR both it and the pending word into the destination and clear pending-valid. Consuming a pending word does not erase its stored bits.

Finally, if finalize=1 and pending-valid=1, XOR that word into the destination and clear pending-valid. Emit all three state fields after these actions. A command may initialize, add and finalize together. Repeated finalize with no pending word is inert. src_count is three bits so all four source ports are usable. This is the XOR-summation helper used by erasure coding, not GF(256) multiplication; reusable finalize is an explicit task lifecycle extension.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. The destination and both pending fields are cleared. Output payload registers reset to zero.
