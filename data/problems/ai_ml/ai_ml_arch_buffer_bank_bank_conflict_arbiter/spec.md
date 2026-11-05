# Independent Scratchpad-Bank Arbitration

Decide which Execute and DMA requests can access a banked scratchpad together.
Different banks arbitrate independently. Each bank can grant one read and one
write; in single-ported mode a granted write excludes a read of that bank.
The block computes decisions, not memory data or DMA address translation.

## Interface

### RTL interface

```systemverilog
module ai_ml_arch_buffer_bank_bank_conflict_arbiter (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic in_single_ported,
    input  logic in_ex_read_valid,
    input  logic [7:0] in_ex_read_bank,
    input  logic in_dma_read_valid,
    input  logic [7:0] in_dma_read_bank,
    input  logic in_ex_write_valid,
    input  logic [7:0] in_ex_write_bank,
    input  logic in_dma_write_valid,
    input  logic [7:0] in_dma_write_bank,
    output logic out_valid,
    input  logic out_ready,
    output logic out_ex_read_grant,
    output logic out_dma_read_grant,
    output logic out_ex_write_grant,
    output logic out_dma_write_grant,
    output logic out_ex_read_blocked,
    output logic out_dma_read_blocked
);
```

Bank numbers are unsigned 8-bit identifiers. All 256 patterns are legal;
only equality matters. Each `*_valid` and `in_single_ported` is one bit.
Bank fields of invalid requests are ignored.

### Behavior interface

```python
class BufferBankConflictArbiter:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_single_ported, in_ex_read_valid, in_ex_read_bank, in_dma_read_valid, in_dma_read_bank, in_ex_write_valid, in_ex_write_bank, in_dma_write_valid, in_dma_write_bank): ...
```

Call `process` once per accepted snapshot of the four requests. It returns
one dictionary with exactly these fields:

```python
{"out_ex_read_grant": ..., "out_dma_read_grant": ...,
 "out_ex_write_grant": ..., "out_dma_write_grant": ...,
 "out_ex_read_blocked": ..., "out_dma_read_blocked": ...}
```

Each returned field is the Python integer `0` or `1`. An all-invalid snapshot
still returns one all-zero dictionary, not `None` or an empty list.

### Transaction and Timing Behavior

Input transfer occurs on a rising edge with `in_valid && in_ready` and reset
inactive. Output transfer occurs with `out_valid && out_ready`. The source
must hold a stalled input stable; the block must hold every field of a stalled
output stable. Responses remain in request order, one per accepted snapshot.
No fixed implementation latency is required. A full response buffer may
deassert `in_ready`; simultaneous retirement and acceptance are permitted.
When `out_valid` is 0, all output payload fields are 0.

## Modules

- `ai_ml_arch_buffer_bank_bank_conflict_arbiter`: top-level bank arbitration decision block.

## Functional Behavior

For each bank independently:

1. Select its Execute write if valid, otherwise its DMA write if valid.
2. Select its Execute read if valid, otherwise its DMA read if valid.
3. If single-ported mode is enabled and a write was selected, block the
   selected read; otherwise grant it. Always grant the selected write.

Set the grant bit of each selected, unblocked request. A request losing to
Execute on the same bank has grant 0. Its `*_blocked` output is also 0:
blocked means **selected for reading but denied by a single-port write**,
not loss of read priority. The two blocked bits only describe reads.

For example, Execute read at bank 3 and DMA read at bank 7 can both be
granted. Execute write at bank 3 blocks only the former in single-port mode.
Execute and DMA writes to different banks can both be granted. In dual-port
mode a selected read and write of the same bank are both granted.

This preserves per-bank Execute priority and write-over-read exclusion. It
does not model the source scratchpad's SRAM contents, read-response queues,
zero-fill writer, accumulator, or downstream availability. All banks are
assumed available apart from the conflicts above. Losing requests are not
queued internally; a later decision uses the next accepted snapshot.

## Reset Behavior

Reset is synchronous, active high. `in_ready` is 0 while `rst` is high.
A reset edge takes precedence over transfers and discards any pending
decision, clears `out_valid`, and clears all output payload fields. After
reset is released, a new snapshot is independent of all earlier snapshots.
There is no algorithmic state beyond pending transaction storage.
