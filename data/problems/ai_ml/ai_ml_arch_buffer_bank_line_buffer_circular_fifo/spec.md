# Masked-Write Scratchpad Bank, 16x32

This module is a single-bank scratchpad storage element with one command input and one read-response output. It stores `N` words of `W` bits each and supports one read or write command per accepted input transaction. Reads return the addressed stored word together with a captured 1-bit `fromDMA` tag. Enabled writes replace the addressed stored word. Addresses select one of sixteen entries directly; there are no FIFO pointers or line-window state.

## Interface

### RTL interface

```systemverilog
module buffer_bank_line_buffer_circular_fifo #(
    parameter int N = 16,
    parameter int W = 32,
    parameter int ALIGNED_TO_BYTES = 4,
    parameter bit SINGLE_PORTED = 1'b0
) (
    input  logic                 clk,
    input  logic                 rst,

    input  logic                 in_valid,
    output logic                 in_ready,
    input  logic                 in_write,
    input  logic [3:0]           in_addr,
    input  logic                 in_fromDMA,
    input  logic                 in_mask,
    input  logic [31:0]          in_data,

    output logic                 out_valid,
    input  logic                 out_ready,
    output logic [31:0]          out_data,
    output logic                 out_fromDMA
);
```

Command encoding:
- `in_write = 0`: read command. `in_fromDMA` is captured and returned with the read response. `in_mask` and `in_data` are ignored.
- `in_write = 1`: write command. `in_mask=1` writes `in_data` to the addressed word; `in_mask=0` leaves memory unchanged. `in_fromDMA` is ignored. No output transaction is generated.

The task uses these fixed architectural parameters:
- `N = 16`
- `W = 32`
- `ALIGNED_TO_BYTES = 4`
- `SINGLE_PORTED = 0`

With these fixed parameters, `ALIGNED_TO_BYTES * 8 = W`, so the one mask bit enables or disables the whole word; it is not a byte mask.

### Behavior interface

```python
class BufferBankLineBufferCircularFifo:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_write, in_addr, in_fromDMA, in_mask, in_data): ...
```

Call `process` once per accepted command. Arguments are Python integers carrying
the unsigned input port bit patterns. It returns:
- `None` for an accepted write command.
- `{"out_data": ..., "out_fromDMA": ...}` for an accepted read command.

The read result is one dictionary: `out_data` is a Python integer in
0..4294967295 and `out_fromDMA` is the integer 0 or 1. Reading a zero word still
returns this dictionary. A masked-off write returns `None`, just like any other
write. `reset()` clears the model's memory and returns `None`.

## Modules

- `buffer_bank_line_buffer_circular_fifo`: top-level storage bank with a 16-entry addressed memory and a read-response output path.

## Functional Behavior

The module contains architectural state `mem[0 .. 15]`, where each entry is a 32-bit unsigned word.

Addressing:
1. For every accepted command, compute `addr = in_addr mod 16`.
2. Since `in_addr` is 4 bits wide, this is identical to `addr = in_addr[3:0]`.

Read command behavior:
1. A read command is an accepted input transaction with `in_write = 0`.
2. The command reads `mem[addr]`.
3. One accepted read command produces exactly one output transaction:
   - `out_data = mem[addr]`
   - `out_fromDMA = in_fromDMA`
4. `out_data` is returned as a 32-bit unsigned bit pattern.
5. `out_fromDMA` is returned as a 1-bit unsigned bit pattern.

Write command behavior:
1. A write command is an accepted input transaction with `in_write = 1`.
2. The command updates memory as:
   - `mem[addr] := in_data[31:0]` if `in_mask=1`, otherwise unchanged
3. `in_mask=0` is a no-op write and still produces no response.
4. `in_fromDMA` has no effect on writes.
5. An accepted write command produces no output transaction.

Ordering:
1. Commands are processed in accepted-transaction order.
2. A read observes all writes from earlier accepted transactions and no writes from later accepted transactions.
3. Each accepted command is a read or a write, never both.
4. `SINGLE_PORTED = 0` introduces no additional architectural restriction on accepted command order.

### Source and transport boundary

Gemmini `ScratchpadBank` supplies addressed storage, write masks and the read
`fromDMA` tag. Its native-memory branch uses `io.write.mask` when aligned_to<w;
for the selected 4-byte alignment and 32-bit word that is a one-bit word enable,
not permission to ignore a zero mask. The task explicitly combines read and
write commands into one ordered channel and initializes memory to zero.
It is not a circular FIFO, line buffer, complete Scratchpad subsystem or proof
of the native independent-channel timing. The legacy identifier is retained.

## Reset Behavior

Reset is synchronous active-high `rst`.

On reset:
- `mem[0]` through `mem[15]` are set to `0`.
- The read-response path is empty.
- `out_valid = 0`.
- `out_data = 0`.
- `out_fromDMA = 0`.

There is no other architectural state.
