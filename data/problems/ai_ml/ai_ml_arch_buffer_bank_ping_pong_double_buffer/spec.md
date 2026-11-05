# Byte-Masked Accumulator RAM, 16x32

This module is an accumulator memory transaction endpoint with one accepted command per input handshake. Each command is either a read or a write to an internal memory of `N` entries, where each entry is one `DATA_W`-bit word interpreted as `ROWS*COLS` signed `ELEM_W`-bit elements packed contiguously from least-significant element upward. Write commands either store the supplied word directly or accumulate it element-wise into the existing word before applying a byte write mask. Read commands return the addressed memory word together with pass-through metadata copied from the request. The externally visible contract is purely transactional: every accepted read produces exactly one output transaction, and every accepted write produces no output transaction.

## Interface

### RTL interface

```systemverilog
module ai_ml_arch_buffer_bank_ping_pong_double_buffer #(
    parameter int N = 16,
    parameter int ELEM_W = 32,
    parameter int ROWS = 1,
    parameter int COLS = 1,
    parameter int SCALE_W = 32,
    parameter int ACT_W = 3,
    parameter int ADDR_W = 4,
    parameter int DATA_W = ROWS * COLS * ELEM_W,
    parameter int MASK_W = DATA_W / 8
) (
    input  logic                     clk,
    input  logic                     rst,

    input  logic                     in_valid,
    output logic                     in_ready,
    input  logic                     in_is_read,
    input  logic [ADDR_W-1:0]        in_addr,
    input  logic [DATA_W-1:0]        in_data,
    input  logic                     in_acc,
    input  logic [MASK_W-1:0]        in_mask,
    input  logic [SCALE_W-1:0]       in_scale,
    input  logic [ELEM_W-1:0]        in_igelu_qb,
    input  logic [ELEM_W-1:0]        in_igelu_qc,
    input  logic [ELEM_W-1:0]        in_iexp_qln2,
    input  logic [ELEM_W-1:0]        in_iexp_qln2_inv,
    input  logic [ACT_W-1:0]         in_act,
    input  logic                     in_full,
    input  logic                     in_fromDMA,

    output logic                     out_valid,
    input  logic                     out_ready,
    output logic [DATA_W-1:0]        out_data,
    output logic [SCALE_W-1:0]       out_scale,
    output logic [ELEM_W-1:0]        out_igelu_qb,
    output logic [ELEM_W-1:0]        out_igelu_qc,
    output logic [ELEM_W-1:0]        out_iexp_qln2,
    output logic [ELEM_W-1:0]        out_iexp_qln2_inv,
    output logic [ACT_W-1:0]         out_act,
    output logic                     out_fromDMA,
    output logic [1:0]               out_acc_bank_id
);
```

### Behavior interface

```python
class AiMlArchBufferBankPingPongDoubleBuffer:
    def __init__(self, N=16, ELEM_W=32, ROWS=1, COLS=1, SCALE_W=32, ACT_W=3):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_is_read,
        in_addr,
        in_data,
        in_acc,
        in_mask,
        in_scale,
        in_igelu_qb,
        in_igelu_qc,
        in_iexp_qln2,
        in_iexp_qln2_inv,
        in_act,
        in_full,
        in_fromDMA,
    ):
        pass
```

Use the constructor defaults specified above. Each call consumes one accepted
command. A write updates persistent memory and returns `None`, not an
acknowledgment record. A read returns one `dict` with exactly `out_data`,
`out_scale`, `out_igelu_qb`, `out_igelu_qc`, `out_iexp_qln2`,
`out_iexp_qln2_inv`, `out_act`, `out_fromDMA`, and `out_acc_bank_id`.
All arguments and field values are Python integers encoding unsigned port bits:
the six data/scale/coefficient fields are 32-bit, `out_act` is in 0..7,
`out_fromDMA` is in 0..1, and `out_acc_bank_id` is always 0. Reading a zero word
still returns the complete dictionary. `reset()` clears the memory and returns
no response. Calls do not model RTL waiting cycles.

## Modules

- `ai_ml_arch_buffer_bank_ping_pong_double_buffer`: top-level accumulator memory transaction block.

## Functional Behavior

The block contains an internal memory array `mem[0..N-1]`. `N` is fixed at 16 entries, so `ADDR_W = 4`. `ROWS = 1`, `COLS = 1`, and therefore `DATA_W = ELEM_W = 32` and `MASK_W = 4`. The module accepts two command types distinguished by `in_is_read`.

Addressing uses only the low `ADDR_W` bits of `in_addr`. The effective address is `addr = in_addr & ((1 << ADDR_W) - 1)`. Since `N = 16`, this is equivalent to `addr = in_addr[3:0]`.

Each memory entry is one 32-bit word. For accumulation, that word is interpreted as one signed 32-bit two's-complement element. `in_data` is interpreted the same way. If `in_acc == 1`, the module computes `sum = old_elem + new_elem` in signed 32-bit arithmetic with wrap modulo `2^32`, then reinterprets the 32-bit result as the replacement data word before masking. If `in_acc == 0`, the replacement data word is simply `in_data`.

After choosing the replacement data word, the module applies the byte mask `in_mask[3:0]` to form the written word. For byte lane `j` in `0..3`, byte `j` of the new memory entry is taken from the replacement data word if `in_mask[j] == 1`; otherwise byte `j` remains the previous byte from `mem[addr]`. Byte lane 0 is bits `[7:0]`, byte lane 1 is bits `[15:8]`, byte lane 2 is bits `[23:16]`, and byte lane 3 is bits `[31:24]`.

A write command is any accepted input with `in_is_read == 0`. A write command updates `mem[addr]` exactly as described above and produces no output transaction. The stream is command-dependent, not a fixed N:1 ratio: writes emit nothing and each read emits one response.

A read command is any accepted input with `in_is_read == 1`. A read command produces exactly one output transaction and does not modify memory. The output fields are:
- `out_data = mem[addr]` as stored at the time the read command is processed.
- `out_scale = in_scale`.
- `out_igelu_qb = in_igelu_qb`.
- `out_igelu_qc = in_igelu_qc`.
- `out_iexp_qln2 = in_iexp_qln2`.
- `out_iexp_qln2_inv = in_iexp_qln2_inv`.
- `out_act = in_act`.
- `out_fromDMA = in_fromDMA`.
- `out_acc_bank_id = 0`.

`in_full` is accepted as part of the command but has no effect on memory state or on any output field.

Read and write commands are ordered by accepted-input transaction order. An implementation may internally stall `in_ready` arbitrarily to enforce any required hazard policy, but the committed transaction semantics must match the following sequential model: each accepted command observes all effects of all earlier accepted write commands and none of any later commands.

Worked masked-accumulate example with `mem[3] = 0x11223344`, `in_is_read = 0`, `in_addr = 3`, `in_acc = 1`, `in_data = 0x00000001`, and `in_mask = 4'b0011`:
1. Signed wrapped accumulation gives replacement data word `0x11223345`.
2. Mask `0011` updates byte lanes 0 and 1 only.
3. New `mem[3]` becomes `0x11223345`.

Worked partial overwrite example with `mem[2] = 0xAAAABBBB`, `in_is_read = 0`, `in_acc = 0`, `in_data = 0x11223344`, and `in_mask = 4'b0101`:
1. Replacement data word is `0x11223344`.
2. Byte lanes 0 and 2 are replaced; lanes 1 and 3 remain old.
3. New `mem[2]` becomes `0xAA22BB44`.

### Source and transport boundary

The selected `AccumulatorMem.scala` operation chooses the adder result or write
data and then applies the byte mask. This task fixes one signed 32-bit element
per entry, wraps its addition modulo 2^32, and returns the full stored word.
Scale/activation fields are response metadata, not scaling or activation here;
`in_full` is ignored because only full-word reads are offered.

There is one bank, no producer/consumer bank swap and no ping-pong state. The
ordered command stream and zero-initialized memory are explicit adaptations of
the source's independent channels, delayed writes and hazard controls, not a
cycle-equivalent Gemmini memory. All architectural parameters are fixed to their
listed defaults for this task. The legacy identifier remains for compatibility.

## Reset Behavior

Reset is asynchronous and active-high. Assertion immediately sets every memory
entry `mem[i]` for `i = 0..15` to `32'h00000000`, clears any pending read or
response, and sets all output payload ports below to zero, without waiting for
a rising clock edge. There is no other architectural state in the
transaction-level model.

While reset is asserted, `out_valid` must be low. After reset and before any read command is accepted, the output payload ports hold zero when `out_valid` is low: `out_data = 0`, `out_scale = 0`, `out_igelu_qb = 0`, `out_igelu_qc = 0`, `out_iexp_qln2 = 0`, `out_iexp_qln2_inv = 0`, `out_act = 0`, `out_fromDMA = 0`, and `out_acc_bank_id = 0`.
