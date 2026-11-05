# Hamming3126Codec

Hamming3126Codec implements the binary Hamming (31,26) block code. Each accepted input transaction selects either encoding of a 26-bit data word into a 31-bit codeword with five parity bits, or decoding of a 31-bit received codeword into a corrected 26-bit data word using a 5-bit syndrome and single-bit error correction. The code uses the fixed parity coverage masks and fixed bit placement defined below.

## Interface

### RTL interface

```systemverilog
module Hamming3126Codec (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_encode,
    input  logic [30:0] in_symbol,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [30:0] out_symbol
);
```

### Behavior interface

```python
class Hamming3126Codec:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_encode, in_symbol):
        pass
```

`process()` returns `{"out_symbol": ...}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_symbol`: 31.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `Hamming3126Codec`: top-level single-transaction codec for Hamming (31,26) encode and decode.

## Functional Behavior

`in_encode` selects the operation for the accepted input transaction.

- If `in_encode == 1`, the transaction is an encode operation.
- If `in_encode == 0`, the transaction is a decode operation.

All arithmetic is integer bitwise arithmetic. Parity and syndrome bits are computed as modulo-2 dot products: for a 32-bit integer `x` and 32-bit mask `m`, the result is one bit equal to the XOR reduction of all bit positions where both `x` and `m` contain `1`. Equivalently, it is the parity of `x & m`. Only the low 31 input bits participate because `in_symbol` is 31 bits wide.

### Encode operation

For encoding, only `in_symbol[25:0]` is used. Bits `in_symbol[30:26]` are ignored.

Let `sym_dec = in_symbol[25:0]`, treated as an unsigned 26-bit value.

The five parity bits are:

- `p1  = parity(sym_dec & 32'h036AD555)`
- `p2  = parity(sym_dec & 32'h02D9B333)`
- `p4  = parity(sym_dec & 32'h01C78F0F)`
- `p8  = parity(sym_dec & 32'h003F80FF)`
- `p16 = parity(sym_dec & 32'h00007FFF)`

The 31-bit encoded codeword is formed by placing the 26 data bits into all non-power-of-two positions numbered 1 through 31, with bit position 1 the most significant bit and bit position 31 the least significant bit. The parity bits occupy positions 1, 2, 4, 8, and 16.

Bit-exactly, the encoded word is:

- `((sym_dec & 32'h00007FFF) << 0)`
- `| ((sym_dec & 32'h003F8000) << 1)`
- `| ((sym_dec & 32'h01C00000) << 2)`
- `| ((sym_dec & 32'h02000000) << 3)`
- `| (p1  << 30)`
- `| (p2  << 29)`
- `| (p4  << 27)`
- `| (p8  << 23)`
- `| (p16 << 15)`

`out_symbol[30:0]` is the complete encoded value. No codeword bits are discarded.

### Decode operation

For decoding, `in_symbol[30:0]` is used as an unsigned 31-bit received codeword `sym_enc`.

The five syndrome bits are:

- `s1  = parity(sym_enc & 32'h55555555)`
- `s2  = parity(sym_enc & 32'h33333333)`
- `s4  = parity(sym_enc & 32'h0F0F0F0F)`
- `s8  = parity(sym_enc & 32'h00FF00FF)`
- `s16 = parity(sym_enc & 32'h0000FFFF)`

The syndrome index is assembled as an unsigned 5-bit integer:

- `z = (s16 << 4) | (s8 << 3) | (s4 << 2) | (s2 << 1) | s1`

Error correction uses `z` as a 1-based bit position counted from the most significant bit of the 31-bit codeword.

- If `z == 0`, no bit is flipped.
- If `1 <= z <= 31`, bit position `z` is flipped, implemented as `sym_enc ^= 1 << (31 - z)`.
- `z > 31` cannot occur because `z` is 5 bits assembled from the syndrome bits; no other case exists.

After optional correction, the 26 data bits are extracted from the corrected 31-bit codeword by removing parity positions 1, 2, 4, 8, and 16. Bit-exactly:

- `((sym_enc & 32'h00007FFF) >> 0)`
- `| ((sym_enc & 32'h007F0000) >> 1)`
- `| ((sym_enc & 32'h07000000) >> 2)`
- `| ((sym_enc & 32'h10000000) >> 3)`

The result is a 26-bit unsigned data word returned on `out_symbol[25:0]`; `out_symbol[30:26]` is zero. A single-bit error is corrected. Multiple-bit errors have the result of the specified syndrome algorithm, without an error flag.

### Output behavior

Each accepted input transaction produces exactly one output transaction.

Transactions remain in input order. A transfer occurs on a rising edge with valid and ready high. A presented output and its valid signal remain stable until accepted. Reset discards a pending output. No fixed computation latency is required.

## Reset Behavior

Reset is synchronous active-high.

On reset:

- `in_ready = 1`
- `out_valid = 0`
- `out_symbol = 31'h00000000`

The block has no architectural state beyond the handshake/output registers. After reset, the next accepted input is processed independently of all earlier transactions.
