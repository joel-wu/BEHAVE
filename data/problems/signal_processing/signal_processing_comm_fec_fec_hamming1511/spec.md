# Hamming(15,11) Encoder/Decoder

This block performs binary Hamming(15,11) forward-error-correction coding on one accepted transaction at a time. Each transaction selects either encode or decode mode. In encode mode, an 11-bit data symbol is mapped to a 15-bit codeword by inserting four parity bits at the standard Hamming positions. In decode mode, a 15-bit received codeword is checked with a 4-bit syndrome, a single erroneous bit is corrected when the syndrome is nonzero, and the corrected 11-bit data symbol is extracted.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_fec_fec_hamming1511 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_encode,
    input  logic [14:0] in_symbol,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [14:0] out_symbol
);
```

### Behavior interface

```python
class SignalProcessingCommFecFecHamming1511:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_encode, in_symbol): ...
```

Each call represents one accepted input and returns exactly
`{"out_symbol": value}`, with `value` an unsigned 15-bit integer in both
modes. Arguments are unsigned 1-bit and 15-bit port encodings. Decode results
have zero upper four bits. The return is never `None` or a list, including
multiple-error patterns. The model is stateless; `reset()` returns `None`
without emitting a record.

## Modules

- `signal_processing_comm_fec_fec_hamming1511`: top-level module implementing both Hamming(15,11) encode and decode.

## Functional Behavior

`in_encode` selects the operation for the accepted input transaction.

- `in_encode = 1`: encode mode.
- `in_encode = 0`: decode mode.

All arithmetic is over integer bit patterns. Parity is XOR reduction, equivalently population-count modulo 2 over the masked bits.

### Bit numbering and codeword layout

The 15-bit encoded symbol uses bit indices `[14:0]`, with bit 14 as MSB and bit 0 as LSB.

Parity bits occupy:
- `p1` at bit 14
- `p2` at bit 13
- `p4` at bit 11
- `p8` at bit 7

The 11 data bits occupy all remaining positions. If the input data symbol is `d[10:0]` with `d[10]` as MSB and `d[0]` as LSB, the encoded output bit placement is:

- `d[6:0]` map to encoded bits `[6:0]`
- `d[9:7]` map to encoded bits `[10:8]`
- `d[10]` maps to encoded bit `[12]`

Equivalently, the encoded symbol is constructed as:
- `(data & 0x007f) << 0`
- `(data & 0x0380) << 1`
- `(data & 0x0400) << 2`
- plus the parity bits inserted at bits 14, 13, 11, and 7.

### Encode mode

Only `in_symbol[10:0]` is used in encode mode. Bits `in_symbol[14:11]` are ignored.

Let:
- `data = in_symbol & 0x07ff`

Compute the four parity bits from the 11-bit data symbol:

- `p1 = parity(data & 0x06d5)`
- `p2 = parity(data & 0x05b3)`
- `p4 = parity(data & 0x038f)`
- `p8 = parity(data & 0x007f)`

where `parity(x)` is 0 when `x` contains an even number of 1 bits and 1 when `x` contains an odd number of 1 bits.

The output codeword is:

- `enc = ((data & 0x007f) << 0)`
- `    | ((data & 0x0380) << 1)`
- `    | ((data & 0x0400) << 2)`
- `    | (p1 << 14)`
- `    | (p2 << 13)`
- `    | (p4 << 11)`
- `    | (p8 << 7)`

`out_symbol[14:0]` is the complete 15-bit codeword `enc`; no codeword bits are discarded.

### Decode mode

All 15 bits of `in_symbol[14:0]` are used in decode mode.

Let:
- `enc = in_symbol & 0x7fff`

Compute the syndrome bits from the received 15-bit symbol:

- `s1 = parity(enc & 0x5555)`
- `s2 = parity(enc & 0x3333)`
- `s4 = parity(enc & 0x0f0f)`
- `s8 = parity(enc & 0x00ff)`

Form the 4-bit syndrome index:
- `z = (s8 << 3) | (s4 << 2) | (s2 << 1) | s1`

If `z != 0`, flip the bit at position `(15 - z)` of `enc`:
- `enc = enc ^ (1 << (15 - z))`

For this Hamming(15,11) code and the masks above, `z` is in the inclusive range `[0, 15]`. No other `z` value occurs.

After optional single-bit correction, extract the 11 data bits:

- `data = ((enc & 0x007f) >> 0)`
- `     | ((enc & 0x0700) >> 1)`
- `     | ((enc & 0x1000) >> 2)`

Return:
- `out_symbol[10:0] = data` and `out_symbol[14:11] = 0`.

### Summary of output meaning

- In encode mode, `out_symbol` is the complete 15-bit encoded codeword.
- In decode mode, `out_symbol` is the decoded 11-bit data symbol, zero-extended to 15 bits. A single-bit error is corrected; multiple-bit errors have the result of the specified syndrome algorithm, without an error flag.

Each accepted input produces one output transaction, in input order. A transfer occurs on a rising edge with valid and ready high. A presented output and its valid signal remain stable until accepted. Reset discards a pending output. No fixed computation latency is required.

The block is stateless across transactions.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state: none.

After reset and until the first accepted input transaction:
- `in_ready = 1`
- `out_valid = 0`
- `out_symbol = 15'b0`

Because the block is stateless, reset does not change any hidden state.
