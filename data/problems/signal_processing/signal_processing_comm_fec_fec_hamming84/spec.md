# Hamming(8,4) Codec

This block performs one Hamming(8,4) forward-error-correction transaction per accepted input. Each transaction operates on one 8-bit input byte. In encode mode, the input byte is split into its high and low 4-bit nibbles and each nibble is mapped through a fixed 16-entry generator table to produce two 8-bit codewords. In decode mode, two 8-bit received codewords are each mapped through a fixed 256-entry decode table to recover the two 4-bit nibbles, which are then packed back into one 8-bit output byte. The block is stateless and uses fixed lookup tables only.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_fec_fec_hamming84 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_encode,
    input  logic [7:0]  in_data0,
    input  logic [7:0]  in_data1,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_data
);
```

Input transaction meaning:
- `in_encode == 1'b1`: encode one decoded byte. `in_data0` is the 8-bit source byte. `in_data1` is ignored.
- `in_encode == 1'b0`: decode one encoded byte pair. `in_data0` is the first received 8-bit codeword and `in_data1` is the second received 8-bit codeword.

Output transaction meaning:
- The block emits two output transactions for each encode input:
  - first output: encoded high nibble of `in_data0`
  - second output: encoded low nibble of `in_data0`
- The block emits one output transaction for each decode input:
  - output: decoded 8-bit byte reconstructed from `in_data0` and `in_data1`

### Behavior interface

```python
class SignalProcessingCommFecFecHamming84:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_encode, in_data0, in_data1):
        pass
```

Returned dictionaries use key:
- `out_data`

Encode mode returns a list of exactly two dictionaries, in high-nibble then low-nibble
order. Decode mode returns one dictionary. Neither mode returns None.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_fec_fec_hamming84`: top-level stateless Hamming(8,4) encoder/decoder using fixed encode and decode lookup tables.

## Functional Behavior

All ports are unsigned integers. `in_encode` is 1 bit. `in_data0`, `in_data1`, and `out_data` are 8 bits.

### Encode mode

If `in_encode == 1`:

1. Extract the two 4-bit nibbles of `in_data0`:
   - `s0 = (in_data0 >> 4) & 0x0f`
   - `s1 = in_data0 & 0x0f`

2. Map each nibble through the 16-entry encode table `ENC_TABLE`:

   | nibble | codeword |
   |--------|----------|
   | 0x0 | 0x00 |
   | 0x1 | 0xD2 |
   | 0x2 | 0x55 |
   | 0x3 | 0x87 |
   | 0x4 | 0x99 |
   | 0x5 | 0x4B |
   | 0x6 | 0xCC |
   | 0x7 | 0x1E |
   | 0x8 | 0xE1 |
   | 0x9 | 0x33 |
   | 0xA | 0xB4 |
   | 0xB | 0x66 |
   | 0xC | 0x78 |
   | 0xD | 0xAA |
   | 0xE | 0x2D |
   | 0xF | 0xFF |

3. Emit two output transactions in this order:
   - first: `out_data = ENC_TABLE[s0]`
   - second: `out_data = ENC_TABLE[s1]`

This is a 1:2 expansion.

### Decode mode

If `in_encode == 0`:

1. Treat `in_data0` and `in_data1` as two received 8-bit codewords:
   - `r0 = in_data0 & 0xff`
   - `r1 = in_data1 & 0xff`

2. Decode each received codeword independently through the fixed 256-entry decode table `DEC_TABLE`, where table index is the full 8-bit received codeword and table value is the recovered 4-bit nibble.

`DEC_TABLE[0x00..0xFF]` is:

- `0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x03,
   0x00, 0x00, 0x05, 0x05, 0x0e, 0x0e, 0x07, 0x07,
   0x00, 0x00, 0x09, 0x09, 0x02, 0x02, 0x07, 0x07,
   0x04, 0x04, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07,
   0x00, 0x00, 0x09, 0x09, 0x0e, 0x0e, 0x0b, 0x0b,
   0x0e, 0x0e, 0x0d, 0x0d, 0x0e, 0x0e, 0x0e, 0x0e,
   0x09, 0x09, 0x09, 0x09, 0x0a, 0x0a, 0x09, 0x09,
   0x0c, 0x0c, 0x09, 0x09, 0x0e, 0x0e, 0x07, 0x07,
   0x00, 0x00, 0x05, 0x05, 0x02, 0x02, 0x0b, 0x0b,
   0x05, 0x05, 0x05, 0x05, 0x06, 0x06, 0x05, 0x05,
   0x02, 0x02, 0x01, 0x01, 0x02, 0x02, 0x02, 0x02,
   0x0c, 0x0c, 0x05, 0x05, 0x02, 0x02, 0x07, 0x07,
   0x08, 0x08, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b,
   0x0c, 0x0c, 0x05, 0x05, 0x0e, 0x0e, 0x0b, 0x0b,
   0x0c, 0x0c, 0x09, 0x09, 0x02, 0x02, 0x0b, 0x0b,
   0x0c, 0x0c, 0x0c, 0x0c, 0x0c, 0x0c, 0x0f, 0x0f,
   0x00, 0x00, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03,
   0x04, 0x04, 0x0d, 0x0d, 0x06, 0x06, 0x03, 0x03,
   0x04, 0x04, 0x01, 0x01, 0x0a, 0x0a, 0x03, 0x03,
   0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x07, 0x07,
   0x08, 0x08, 0x0d, 0x0d, 0x0a, 0x0a, 0x03, 0x03,
   0x0d, 0x0d, 0x0d, 0x0d, 0x0e, 0x0e, 0x0d, 0x0d,
   0x0a, 0x0a, 0x09, 0x09, 0x0a, 0x0a, 0x0a, 0x0a,
   0x04, 0x04, 0x0d, 0x0d, 0x0a, 0x0a, 0x0f, 0x0f,
   0x08, 0x08, 0x01, 0x01, 0x06, 0x06, 0x03, 0x03,
   0x06, 0x06, 0x05, 0x05, 0x06, 0x06, 0x06, 0x06,
   0x01, 0x01, 0x01, 0x01, 0x02, 0x02, 0x01, 0x01,
   0x04, 0x04, 0x01, 0x01, 0x06, 0x06, 0x0f, 0x0f,
   0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x0b, 0x0b,
   0x08, 0x08, 0x0d, 0x0d, 0x06, 0x06, 0x0f, 0x0f,
   0x08, 0x08, 0x01, 0x01, 0x0a, 0x0a, 0x0f, 0x0f,
   0x0c, 0x0c, 0x0f, 0x0f, 0x0f, 0x0f, 0x0f, 0x0f`

3. Recover:
   - `s0 = DEC_TABLE[r0]`
   - `s1 = DEC_TABLE[r1]`

4. Pack the two decoded nibbles into one output byte:
   - `out_data = ((s0 & 0x0f) << 4) | (s1 & 0x0f)`

5. Emit one output transaction carrying that byte.

This is a 2:1 decode with one accepted input transaction carrying both received codewords.

### Output ordering and widths

- Every emitted `out_data` value is truncated to 8 bits.
- Encode mode always emits exactly two outputs in order: high nibble first, low nibble second.
- Decode mode always emits exactly one output.

### Statelessness

The block has no architectural state beyond reset-defined outputs. Each transaction is independent of all prior transactions.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state after reset:
- no internal state

Output behavior after reset:
- `out_valid = 0`
- `out_data = 8'h00`

Input-side behavior after reset:
- `in_ready = 1`
