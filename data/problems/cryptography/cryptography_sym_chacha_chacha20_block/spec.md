# ChaCha20Block

This module computes one 64-byte ChaCha-family output block from a 256-bit key and a mode-dependent nonce/counter state. It uses the ChaCha20 core algorithm: initialize a 16-word state from four fixed constants, eight little-endian key words, and four words in positions 12..15 selected by nonce mode; apply 20 rounds as 10 column-round/diagonal-round double rounds built from the ChaCha quarter round; then add the original state wordwise modulo 2^32 to form the output block. The transaction also returns the next values of state words 12..15 after the mode-dependent post-block update.

## Interface

### RTL interface

```systemverilog
module ChaCha20Block (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_key,
    input  logic [1:0]   in_nonce_size,
    input  logic [127:0] in_nonce,
    input  logic [63:0]  in_block_counter,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [511:0] out_block,
    output logic [127:0] out_next_state_12_15,
    output logic         out_error
);
```

Port meanings:

- `in_key`: 32-byte key. Byte 0 is `in_key[7:0]`, byte 31 is `in_key[255:248]`.
- `in_nonce_size`: mode selector. `2'd0` = 8-byte nonce, `2'd1` = 12-byte nonce, `2'd2` = 16-byte nonce, `2'd3` = invalid.
- `in_nonce`: nonce bytes packed by byte position in little-endian byte order. Byte 0 is `in_nonce[7:0]`. For 8-byte nonce mode only bytes 0..7 are used. For 12-byte nonce mode only bytes 0..11 are used. For 16-byte nonce mode all 16 bytes are used.
- `in_block_counter`: counter input for modes that use a block counter. In 8-byte nonce mode, `in_block_counter[31:0]` is state word 12 and `in_block_counter[63:32]` is state word 13. In 12-byte nonce mode, `in_block_counter[31:0]` is state word 12 and `in_block_counter[63:32]` is ignored. In 16-byte nonce mode the full port is ignored.
- `out_block`: 64 output bytes packed by byte position in little-endian byte order. Output bytes 0..3 are the little-endian serialization of word 0, bytes 4..7 of word 1, ..., bytes 60..63 of word 15.
- `out_next_state_12_15`: post-block values of state words 12, 13, 14, and 15, packed as four 32-bit little-endian words in bits `[31:0]`, `[63:32]`, `[95:64]`, and `[127:96]`.
- `out_error`: `1` iff `in_nonce_size` is invalid or the post-block counter update overflows the allowed counter space for the selected mode.

### Behavior interface

```python
class ChaCha20Block:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_key, in_nonce_size, in_nonce, in_block_counter): ...
```

`process(...)` returns a dict with keys:
- `out_block`
- `out_next_state_12_15`
- `out_error`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_block`: 512.
- `out_error`: 1.
- `out_next_state_12_15`: 128.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ChaCha20Block`: top-level block generator implementing state initialization, 20 ChaCha rounds, block serialization, and mode-dependent next-state computation.

## Functional Behavior

All ChaCha state words are unsigned 32-bit integers. Every addition in the algorithm wraps modulo 2^32.

Definitions:

- `key_byte[j] = (in_key >> (8*j)) & 8'hFF` for `j = 0..31`.
- `nonce_byte[j] = (in_nonce >> (8*j)) & 8'hFF` for `j = 0..15`.
- `le_word(B, i)` for a byte array `B` means `B[4*i+0] + (B[4*i+1] << 8) + (B[4*i+2] << 16) + (B[4*i+3] << 24)`.
- `rotl32(x, n)` means 32-bit rotate-left of `x` by constant `n`.
- `serialize_word(w)` means the 4 bytes `w[7:0]`, `w[15:8]`, `w[23:16]`, `w[31:24]`.

### Input validity

If `in_nonce_size == 2'd3`, the transaction is invalid and the outputs are:
- `out_block = 512'h0`
- `out_next_state_12_15 = 128'h0`
- `out_error = 1`

For all other `in_nonce_size` values, compute the block and next-state fields as defined below and set `out_error` only from the mode-dependent post-block overflow rule.

### Initial 16-word state

Set:
- `s[0] = 32'h61707865`
- `s[1] = 32'h3320646e`
- `s[2] = 32'h79622d32`
- `s[3] = 32'h6b206574`

For `i = 0..7`:
- `s[4+i] = le_word(key_byte, i)`

Mode-dependent words 12..15:

#### 8-byte nonce mode (`in_nonce_size == 2'd0`)

- `s[12] = in_block_counter[31:0]`
- `s[13] = in_block_counter[63:32]`
- `s[14] = le_word(nonce_byte, 0)`
- `s[15] = le_word(nonce_byte, 1)`

#### 12-byte nonce mode (`in_nonce_size == 2'd1`)

- `s[12] = in_block_counter[31:0]`
- `s[13] = le_word(nonce_byte, 0)`
- `s[14] = le_word(nonce_byte, 1)`
- `s[15] = le_word(nonce_byte, 2)`

#### 16-byte nonce mode (`in_nonce_size == 2'd2`)

- `s[12] = le_word(nonce_byte, 0)`
- `s[13] = le_word(nonce_byte, 1)`
- `s[14] = le_word(nonce_byte, 2)`
- `s[15] = le_word(nonce_byte, 3)`

Create a working copy `h[0..15] = s[0..15]`.

### Quarter round

The ChaCha quarter round on words `(a, b, c, d)` performs these 12 operations in this exact order:

1. `a = (a + b) mod 2^32`
2. `d = d XOR a`
3. `d = rotl32(d, 16)`
4. `c = (c + d) mod 2^32`
5. `b = b XOR c`
6. `b = rotl32(b, 12)`
7. `a = (a + b) mod 2^32`
8. `d = d XOR a`
9. `d = rotl32(d, 8)`
10. `c = (c + d) mod 2^32`
11. `b = b XOR c`
12. `b = rotl32(b, 7)`

### Twenty rounds

Apply 10 identical double rounds to `h`.

Each double round consists of:

Column round:
- quarter round on `(h[0], h[4], h[8], h[12])`
- quarter round on `(h[1], h[5], h[9], h[13])`
- quarter round on `(h[2], h[6], h[10], h[14])`
- quarter round on `(h[3], h[7], h[11], h[15])`

Diagonal round:
- quarter round on `(h[0], h[5], h[10], h[15])`
- quarter round on `(h[1], h[6], h[11], h[12])`
- quarter round on `(h[2], h[7], h[8], h[13])`
- quarter round on `(h[3], h[4], h[9], h[14])`

### Block output

For each `i = 0..15`:
- `y[i] = (h[i] + s[i]) mod 2^32`

Serialize `y[0]` through `y[15]` in order, each with `serialize_word`, to produce 64 output bytes.

Pack output byte `j` into `out_block[8*j +: 8]` for `j = 0..63`.

### Post-block update and error flag

The output block is always generated from the pre-update state `s`.

`out_next_state_12_15` always reports the values of words 12..15 after the following mode-dependent update.

#### 8-byte nonce mode

This mode uses a 64-bit counter across words 12 and 13.

Compute:
- `next12 = (s[12] + 1) mod 2^32`
- if `next12 != 0`, then `next13 = s[13]`
- if `next12 == 0`, then `next13 = (s[13] + 1) mod 2^32`
- `next14 = s[14]`
- `next15 = s[15]`

Set:
- `out_error = 1` iff `next12 == 0` and `next13 == 0`
- otherwise `out_error = 0`

#### 12-byte nonce mode

This mode uses a 32-bit counter in word 12.

Compute:
- `next12 = (s[12] + 1) mod 2^32`
- `next13 = s[13]`
- `next14 = s[14]`
- `next15 = s[15]`

Set:
- `out_error = 1` iff `next12 == 0`
- otherwise `out_error = 0`

#### 16-byte nonce mode

This mode has no counter increment.

Compute:
- `next12 = s[12]`
- `next13 = s[13]`
- `next14 = s[14]`
- `next15 = s[15]`

Set:
- `out_error = 0`

Pack:
- `out_next_state_12_15[31:0]   = next12`
- `out_next_state_12_15[63:32]  = next13`
- `out_next_state_12_15[95:64]  = next14`
- `out_next_state_12_15[127:96] = next15`

### Ignored input bits

- In 8-byte nonce mode, `nonce_byte[8]` through `nonce_byte[15]` are ignored.
- In 12-byte nonce mode, `nonce_byte[12]` through `nonce_byte[15]` are ignored and `in_block_counter[63:32]` is ignored.
- In 16-byte nonce mode, all bits of `in_block_counter` are ignored.

## Reset Behavior

Reset is synchronous active-high `rst`.

This module has no architectural state visible across accepted input transactions. After reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_block = 512'h0`
- `out_next_state_12_15 = 128'h0`
- `out_error = 1'b0`
