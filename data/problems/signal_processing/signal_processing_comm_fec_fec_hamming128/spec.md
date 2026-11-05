# signal_processing_comm_fec_fec_hamming128

This block implements the binary Hamming(12,8) forward-error-correction code on one byte per accepted input transaction. It supports two modes selected per transaction: encode one 8-bit data byte into one 12-bit codeword, or decode one 12-bit received codeword into one corrected 8-bit data byte. Encoding computes the four parity bits defined by the Hamming(12,8) placement and inserts them into the 12-bit codeword. Decoding computes the 4-bit syndrome from the received 12-bit word, flips the indicated bit if the syndrome index is in the range 1 through 12, and then extracts the eight data bits.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_fec_fec_hamming128 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_encode,
    input  logic [11:0] in_symbol,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [11:0] out_symbol
);
```

Input interpretation:
- `in_encode == 1`: encode mode. `in_symbol[7:0]` is the decoded 8-bit data byte. `in_symbol[11:8]` is ignored.
- `in_encode == 0`: decode mode. `in_symbol[11:0]` is the received 12-bit encoded symbol.

Output interpretation:
- `in_encode == 1`: `out_symbol[11:0]` is the encoded 12-bit Hamming codeword.
- `in_encode == 0`: `out_symbol[7:0]` is the decoded 8-bit data byte and `out_symbol[11:8]` shall be zero.

### Behavior interface

```python
class SignalProcessingCommFecFecHamming128:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_encode, in_symbol):
        pass
```

Each call represents one accepted input and returns exactly
`{"out_symbol": value}`, with `value` an unsigned 12-bit integer in both
modes. Arguments are unsigned 1-bit and 12-bit port encodings. Decode results
have zero upper four bits. The return is never `None` or a list, including
uncorrectable patterns. There is no persistent model state; `reset()` returns
`None` without emitting a record.

## Modules

- `signal_processing_comm_fec_fec_hamming128`: top-level Hamming(12,8) encoder/decoder.

## Functional Behavior

All arithmetic is unsigned integer arithmetic on bit patterns. A parity bit is the XOR reduction of the selected covered bits and is therefore one bit wide.

Bit numbering for the 12-bit codeword is by Hamming position 1 through 12, with position 1 as the most-significant bit of `out_symbol[11]` and position 12 as the least-significant bit of `out_symbol[0]`. The codeword layout is:

- position 1  = parity bit `P1` = `out_symbol[11]`
- position 2  = parity bit `P2` = `out_symbol[10]`
- position 3  = data bit 1      = `out_symbol[9]`
- position 4  = parity bit `P4` = `out_symbol[8]`
- position 5  = data bit 2      = `out_symbol[7]`
- position 6  = data bit 3      = `out_symbol[6]`
- position 7  = data bit 4      = `out_symbol[5]`
- position 8  = parity bit `P8` = `out_symbol[4]`
- position 9  = data bit 5      = `out_symbol[3]`
- position 10 = data bit 6      = `out_symbol[2]`
- position 11 = data bit 7      = `out_symbol[1]`
- position 12 = data bit 8      = `out_symbol[0]`

For encode mode, let `d = in_symbol[7:0]`. Its bits are numbered as:
- data bit 1 = `(d >> 7) & 1`
- data bit 2 = `(d >> 6) & 1`
- data bit 3 = `(d >> 5) & 1`
- data bit 4 = `(d >> 4) & 1`
- data bit 5 = `(d >> 3) & 1`
- data bit 6 = `(d >> 2) & 1`
- data bit 7 = `(d >> 1) & 1`
- data bit 8 = `(d >> 0) & 1`

The parity bits are computed from `d` by XOR reduction of the mask-selected bits:
- `P1 = parity(d & 8'hDA)`
- `P2 = parity(d & 8'hB6)`
- `P4 = parity(d & 8'h71)`
- `P8 = parity(d & 8'h0F)`

`parity(x)` is the XOR of all bits of its operand: eight bits for encoder masks and twelve bits for decoder masks. It produces 0 for even population count and 1 for odd population count.

The encoded 12-bit result is formed exactly as:
- `out_symbol = ((d & 12'h00F) << 0) | ((d & 12'h070) << 1) | ((d & 12'h080) << 2) | (P1 << 11) | (P2 << 10) | (P4 << 8) | (P8 << 4)`

Equivalently, this inserts the parity bits into positions 1, 2, 4, and 8 while preserving the data-bit order listed above.

For decode mode, let `c = in_symbol[11:0]`. The syndrome bits are computed from the received 12-bit codeword by XOR reduction of the mask-selected bits:
- `S1 = parity(c & 12'hAAA)`
- `S2 = parity(c & 12'h666)`
- `S4 = parity(c & 12'h1E1)`
- `S8 = parity(c & 12'h01F)`

The syndrome index is:
- `z = (S8 << 3) | (S4 << 2) | (S2 << 1) | S1`

`z` is interpreted as a 1-based bit position in the 12-bit codeword:
- `z == 0`: no bit is flipped.
- `1 <= z <= 12`: flip codeword position `z`, which is bit index `12 - z` in the 12-bit vector representation. The corrected word is `c ^ (12'h001 << (12 - z))`.
- `z > 12`: no bit is flipped.

After optional correction, the decoded 8-bit data byte is extracted exactly as:
- `d = ((c & 12'h00F) >> 0) | ((c & 12'h0E0) >> 1) | ((c & 12'h200) >> 2)`

The decode-mode output is:
- `out_symbol[7:0] = d`
- `out_symbol[11:8] = 4'b0000`

This decoder corrects any single-bit error in the 12-bit codeword. For patterns producing `z > 12`, or for multiple errors that alias to another valid syndrome, the output is whatever results from the above algorithm; no additional error flag is produced.

The block is 1:1 at the transaction level: each accepted input transaction produces exactly one output transaction.

## Reset Behavior

`rst` is asynchronous active-high. Reset clears the pending output; the coding operation has no retained state.

Immediately after reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_symbol = 12'h000`

There is no retained mode, accumulator, counter, or buffer state.
