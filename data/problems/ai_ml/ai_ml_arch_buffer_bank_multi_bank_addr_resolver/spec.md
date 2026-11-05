# AI ML Arch Buffer Bank Multi Bank Address Resolver

This module resolves a fixed scratchpad row address into a bank index and an in-bank row index. It implements the bank-selection mapping implied by a multi-bank memory organization in which the low-order row bits select an entry within a bank and the next higher bits select the bank. The block is stateless and produces one resolved address per accepted input transaction.

## Interface

### RTL interface

```systemverilog
module ai_ml_arch_buffer_bank_multi_bank_addr_resolver (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_laddr,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_bank,
    output logic [15:0] out_row
);
```

Parameterization is fixed by the architectural configuration embodied in this specification:
- number of banks = 8
- rows per bank = 1024

The input `in_laddr` is a 32-bit unsigned local address bit-pattern. Only bits `[12:0]` participate in resolution. Higher bits `[31:13]` do not affect the output. They are not decoded as accumulator, garbage, or other control flags; this task is only the fixed bank/row projection, not a complete local-address decoder.

### Behavior interface

```python
class AiMlArchBufferBankMultiBankAddrResolver:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_laddr):
        pass
```

Call `process` once per accepted address, passing `in_laddr` as an integer in
0..2^32-1. It returns exactly `{"out_bank": bank, "out_row": row}`, with integer
`bank` in 0..7 and integer `row` in 0..1023. Address zero returns both zero fields,
not `None`. The model has no persistent algorithmic state; `reset()` is a no-op.

### Transaction and Timing Behavior

Each accepted input produces exactly one ordered output record. Transfers
occur on rising edges with the corresponding valid and ready both asserted,
except on reset edges. A stalled input must remain stable; a valid stalled
response must retain both fields until consumed. The implementation may
backpressure inputs and choose its internal latency. Simultaneous output
retirement and input acceptance are allowed. Invalid output payloads are
don't-care; their retained values are not extra responses.

## Modules

- `ai_ml_arch_buffer_bank_multi_bank_addr_resolver`: top-level address resolver.

## Functional Behavior

The module accepts one 32-bit unsigned local address `in_laddr` and returns the bank index and row index for an 8-bank memory with 1024 rows per bank.

Address decomposition is:

- `row_bits = log2(1024) = 10`
- `bank_bits = log2(8) = 3`

Let:
- `row = in_laddr[9:0]`
- `bank = in_laddr[12:10]`

Equivalently, in integer arithmetic:

- `row = in_laddr & 16'h03FF`
- `bank = (in_laddr >> 10) & 8'h07`

Output mapping:
- `out_bank = bank`
- `out_row = row`

All arithmetic is unsigned integer bit extraction. There is no signed interpretation, saturation, rounding, or exceptional case handling.

Bits above bit 12 are ignored:
- if two inputs differ only in `in_laddr[31:13]`, they produce identical outputs.

Output widths:
- `out_bank` is an unsigned 8-bit value whose numeric range is `[0, 7]`
- `out_row` is an unsigned 16-bit value whose numeric range is `[0, 1023]`

Examples:
- `in_laddr = 32'h0000_0000` -> `out_bank = 0`, `out_row = 0`
- `in_laddr = 32'h0000_0001` -> `out_bank = 0`, `out_row = 1`
- `in_laddr = 32'h0000_03FF` -> `out_bank = 0`, `out_row = 1023`
- `in_laddr = 32'h0000_0400` -> `out_bank = 1`, `out_row = 0`
- `in_laddr = 32'h0000_1FFF` -> `out_bank = 7`, `out_row = 1023`

## Reset Behavior

Reset is synchronous active-high `rst`.

The address mapping has no algorithmic state. A reset edge cancels pending responses and resets transaction storage; it takes precedence over acceptance or retirement.

After reset:
- `in_ready = 1`
- `out_valid = 0`

Because the module is stateless, reset does not alter the result produced for any accepted post-reset input transaction.
