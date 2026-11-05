# Hamming74Codec

Hamming74Codec implements the binary Hamming(7,4) block code on one byte per accepted transaction. Each input byte is split into two 4-bit nibbles, each nibble is transformed by a fixed 16-entry generator table into one 7-bit codeword, and the two 7-bit codewords are returned together. In decode mode, each 7-bit received codeword is mapped through a fixed 128-entry table to a corrected 4-bit nibble. In soft-decode mode, each group of seven 8-bit reliability samples is compared against all 16 legal codewords and the nibble with minimum accumulated distance is selected.

## Interface

### RTL interface

```systemverilog
module hamming74_codec (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_mode,
    input  logic [7:0]  in_data,
    input  logic [6:0]  in_code0,
    input  logic [6:0]  in_code1,
    input  logic [7:0]  in_soft0,
    input  logic [7:0]  in_soft1,
    input  logic [7:0]  in_soft2,
    input  logic [7:0]  in_soft3,
    input  logic [7:0]  in_soft4,
    input  logic [7:0]  in_soft5,
    input  logic [7:0]  in_soft6,
    input  logic [7:0]  in_soft7,
    input  logic [7:0]  in_soft8,
    input  logic [7:0]  in_soft9,
    input  logic [7:0]  in_soft10,
    input  logic [7:0]  in_soft11,
    input  logic [7:0]  in_soft12,
    input  logic [7:0]  in_soft13,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_data,
    output logic [6:0]  out_code0,
    output logic [6:0]  out_code1
);
```

`in_mode` encoding:
- `2'b00`: encode
- `2'b01`: hard decode
- `2'b10`: soft decode
- `2'b11`: reserved; treated identically to encode

Exactly one output transaction is produced for every accepted input transaction.

### Behavior interface

```python
class Hamming74Codec:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_mode,
            in_data,
            in_code0,
            in_code1,
            in_soft0,
            in_soft1,
            in_soft2,
            in_soft3,
            in_soft4,
            in_soft5,
            in_soft6,
            in_soft7,
            in_soft8,
            in_soft9,
            in_soft10,
            in_soft11,
            in_soft12,
            in_soft13):
        pass
```

Returned dictionaries use keys `out_data`, `out_code0`, and `out_code1`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_code0`, `out_code1`: 7.
- `out_data`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `hamming74_codec`: top-level codec block implementing encode, hard decode, and soft decode.
- No helper RTL modules are required.

## Functional Behavior

The block is stateless. All arithmetic is unsigned integer arithmetic.

The encoder generator table is:

| nibble | codeword |
|---|---|
| 0x0 | 0x00 |
| 0x1 | 0x69 |
| 0x2 | 0x2a |
| 0x3 | 0x43 |
| 0x4 | 0x4c |
| 0x5 | 0x25 |
| 0x6 | 0x66 |
| 0x7 | 0x0f |
| 0x8 | 0x70 |
| 0x9 | 0x19 |
| 0xa | 0x5a |
| 0xb | 0x33 |
| 0xc | 0x3c |
| 0xd | 0x55 |
| 0xe | 0x16 |
| 0xf | 0x7f |

Each table output is a 7-bit codeword carried in the low 7 bits of the listed hexadecimal value. Bit 6 is the first soft-bit position, and bit 0 is the last soft-bit position.

The hard-decoder table indexed by a 7-bit received codeword is:

- 0x00→0x0, 0x01→0x0, 0x02→0x0, 0x03→0x3, 0x04→0x0, 0x05→0x5, 0x06→0xe, 0x07→0x7
- 0x08→0x0, 0x09→0x9, 0x0a→0x2, 0x0b→0x7, 0x0c→0x4, 0x0d→0x7, 0x0e→0x7, 0x0f→0x7
- 0x10→0x0, 0x11→0x9, 0x12→0xe, 0x13→0xb, 0x14→0xe, 0x15→0xd, 0x16→0xe, 0x17→0xe
- 0x18→0x9, 0x19→0x9, 0x1a→0xa, 0x1b→0x9, 0x1c→0xc, 0x1d→0x9, 0x1e→0xe, 0x1f→0x7
- 0x20→0x0, 0x21→0x5, 0x22→0x2, 0x23→0xb, 0x24→0x5, 0x25→0x5, 0x26→0x6, 0x27→0x5
- 0x28→0x2, 0x29→0x1, 0x2a→0x2, 0x2b→0x2, 0x2c→0xc, 0x2d→0x5, 0x2e→0x2, 0x2f→0x7
- 0x30→0x8, 0x31→0xb, 0x32→0xb, 0x33→0xb, 0x34→0xc, 0x35→0x5, 0x36→0xe, 0x37→0xb
- 0x38→0xc, 0x39→0x9, 0x3a→0x2, 0x3b→0xb, 0x3c→0xc, 0x3d→0xc, 0x3e→0xc, 0x3f→0xf
- 0x40→0x0, 0x41→0x3, 0x42→0x3, 0x43→0x3, 0x44→0x4, 0x45→0xd, 0x46→0x6, 0x47→0x3
- 0x48→0x4, 0x49→0x1, 0x4a→0xa, 0x4b→0x3, 0x4c→0x4, 0x4d→0x4, 0x4e→0x4, 0x4f→0x7
- 0x50→0x8, 0x51→0xd, 0x52→0xa, 0x53→0x3, 0x54→0xd, 0x55→0xd, 0x56→0xe, 0x57→0xd
- 0x58→0xa, 0x59→0x9, 0x5a→0xa, 0x5b→0xa, 0x5c→0x4, 0x5d→0xd, 0x5e→0xa, 0x5f→0xf
- 0x60→0x8, 0x61→0x1, 0x62→0x6, 0x63→0x3, 0x64→0x6, 0x65→0x5, 0x66→0x6, 0x67→0x6
- 0x68→0x1, 0x69→0x1, 0x6a→0x2, 0x6b→0x1, 0x6c→0x4, 0x6d→0x1, 0x6e→0x6, 0x6f→0xf
- 0x70→0x8, 0x71→0x8, 0x72→0x8, 0x73→0xb, 0x74→0x8, 0x75→0xd, 0x76→0x6, 0x77→0xf
- 0x78→0x8, 0x79→0x1, 0x7a→0xa, 0x7b→0xf, 0x7c→0xc, 0x7d→0xf, 0x7e→0xf, 0x7f→0xf

Mode-dependent behavior:

1. Encode mode (`in_mode[1:0]` = `2'b00` or `2'b11`)
   - Split `in_data[7:0]` into `nibble0 = in_data[7:4]` and `nibble1 = in_data[3:0]`.
   - Compute `code0 = ENC_TABLE[nibble0]`.
   - Compute `code1 = ENC_TABLE[nibble1]`.
   - Output:
     - `out_code0 = code0[6:0]`
     - `out_code1 = code1[6:0]`
     - `out_data = 8'h00`

2. Hard decode mode (`in_mode[1:0]` = `2'b01`)
   - Interpret `in_code0[6:0]` and `in_code1[6:0]` as independent received Hamming(7,4) codewords.
   - Compute `nibble0 = DEC_TABLE[in_code0]`.
   - Compute `nibble1 = DEC_TABLE[in_code1]`.
   - Form `decoded_byte = (nibble0 << 4) | nibble1`.
   - Output:
     - `out_data = decoded_byte[7:0]`
     - `out_code0 = 7'h00`
     - `out_code1 = 7'h00`

3. Soft decode mode (`in_mode[1:0]` = `2'b10`)
   - The 14 soft inputs represent two consecutive 7-sample vectors:
     - first symbol samples: `in_soft0` through `in_soft6`
     - second symbol samples: `in_soft7` through `in_soft13`
   - Each soft input is an unsigned 8-bit reliability value in the range 0 to 255.
   - For one 7-sample vector `soft[0]..soft[6]`, evaluate all candidate nibbles `s` from 0 through 15 in ascending numeric order.
   - Let `c = ENC_TABLE[s]`.
   - Compute candidate distance
     - `d = term0 + term1 + term2 + term3 + term4 + term5 + term6`
     - for bit position `j` from 0 through 6, using codeword bit `c[6-j]`:
       - if `c[6-j] == 1`, then `termj = 255 - soft[j]`
       - if `c[6-j] == 0`, then `termj = soft[j]`
   - Select the candidate with minimum `d`.
   - Tie break is strictly first minimum encountered: because candidates are tested in ascending order and replacement occurs only when `d < dmin`, equal distances keep the previously selected nibble.
   - Apply this procedure independently to the first 7-sample vector and the second 7-sample vector, producing `nibble0` and `nibble1`.
   - Form `decoded_byte = (nibble0 << 4) | nibble1`.
   - Output:
     - `out_data = decoded_byte[7:0]`
     - `out_code0 = 7'h00`
     - `out_code1 = 7'h00`

All outputs are pure functions of the accepted input transaction. The block contains no persistent data path state.

## Reset Behavior

Reset is synchronous active-high. After reset, there is no architectural state to retain.

Output port values while no output transaction is being presented are:
- `out_valid = 1'b0`
- `out_data = 8'h00`
- `out_code0 = 7'h00`
- `out_code1 = 7'h00`

Because the block is stateless, reset does not alter any internal registers beyond forcing the idle output values above.
