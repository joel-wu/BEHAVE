# AESSubBytes16

AESSubBytes16 computes the AES forward SubBytes transformation on a 16-byte state. One accepted input transaction provides 16 independent 8-bit bytes; the module returns the 16 substituted bytes in the same byte order. The substitution is the Rijndael forward S-box, implemented exactly as the byte values encoded by the AES table `Te4`: for input byte `x`, the substituted byte is the least-significant byte of `Te4[x]`, which is equal to every byte of that replicated 32-bit table entry.

## Interface

### RTL interface

```systemverilog
module AESSubBytes16 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_b0,
    input  logic [7:0]  in_b1,
    input  logic [7:0]  in_b2,
    input  logic [7:0]  in_b3,
    input  logic [7:0]  in_b4,
    input  logic [7:0]  in_b5,
    input  logic [7:0]  in_b6,
    input  logic [7:0]  in_b7,
    input  logic [7:0]  in_b8,
    input  logic [7:0]  in_b9,
    input  logic [7:0]  in_b10,
    input  logic [7:0]  in_b11,
    input  logic [7:0]  in_b12,
    input  logic [7:0]  in_b13,
    input  logic [7:0]  in_b14,
    input  logic [7:0]  in_b15,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_b0,
    output logic [7:0]  out_b1,
    output logic [7:0]  out_b2,
    output logic [7:0]  out_b3,
    output logic [7:0]  out_b4,
    output logic [7:0]  out_b5,
    output logic [7:0]  out_b6,
    output logic [7:0]  out_b7,
    output logic [7:0]  out_b8,
    output logic [7:0]  out_b9,
    output logic [7:0]  out_b10,
    output logic [7:0]  out_b11,
    output logic [7:0]  out_b12,
    output logic [7:0]  out_b13,
    output logic [7:0]  out_b14,
    output logic [7:0]  out_b15
);
endmodule
```

### Behavior interface

```python
class AESSubBytes16:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_b0, in_b1, in_b2, in_b3,
        in_b4, in_b5, in_b6, in_b7,
        in_b8, in_b9, in_b10, in_b11,
        in_b12, in_b13, in_b14, in_b15
    ): ...
```

The returned dict keys are:
`out_b0`, `out_b1`, `out_b2`, `out_b3`, `out_b4`, `out_b5`, `out_b6`, `out_b7`, `out_b8`, `out_b9`, `out_b10`, `out_b11`, `out_b12`, `out_b13`, `out_b14`, `out_b15`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_b0`, `out_b1`, `out_b10`, `out_b11`, `out_b12`, `out_b13`, `out_b14`,
  `out_b15`, `out_b2`, `out_b3`, `out_b4`, `out_b5`, `out_b6`, `out_b7`, `out_b8`,
  `out_b9`: 8.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `AESSubBytes16`: top-level module that accepts 16 input bytes and emits the AES forward S-box substitution of each byte.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

Each input `in_bi` is an unsigned 8-bit value in `[0, 255]`. Each output `out_bi` is an unsigned 8-bit value in `[0, 255]`.

The module is stateless at the transaction level. For one accepted input transaction, it produces exactly one output transaction.

Let `SBOX[256]` be the AES forward substitution table:

- `SBOX[0x00] = 0x63`
- `SBOX[0x01] = 0x7c`
- `SBOX[0x02] = 0x77`
- `SBOX[0x03] = 0x7b`
- `SBOX[0x04] = 0xf2`
- `SBOX[0x05] = 0x6b`
- `SBOX[0x06] = 0x6f`
- `SBOX[0x07] = 0xc5`
- `SBOX[0x08] = 0x30`
- `SBOX[0x09] = 0x01`
- `SBOX[0x0a] = 0x67`
- `SBOX[0x0b] = 0x2b`
- `SBOX[0x0c] = 0xfe`
- `SBOX[0x0d] = 0xd7`
- `SBOX[0x0e] = 0xab`
- `SBOX[0x0f] = 0x76`
- `SBOX[0x10] = 0xca`
- `SBOX[0x11] = 0x82`
- `SBOX[0x12] = 0xc9`
- `SBOX[0x13] = 0x7d`
- `SBOX[0x14] = 0xfa`
- `SBOX[0x15] = 0x59`
- `SBOX[0x16] = 0x47`
- `SBOX[0x17] = 0xf0`
- `SBOX[0x18] = 0xad`
- `SBOX[0x19] = 0xd4`
- `SBOX[0x1a] = 0xa2`
- `SBOX[0x1b] = 0xaf`
- `SBOX[0x1c] = 0x9c`
- `SBOX[0x1d] = 0xa4`
- `SBOX[0x1e] = 0x72`
- `SBOX[0x1f] = 0xc0`
- `SBOX[0x20] = 0xb7`
- `SBOX[0x21] = 0xfd`
- `SBOX[0x22] = 0x93`
- `SBOX[0x23] = 0x26`
- `SBOX[0x24] = 0x36`
- `SBOX[0x25] = 0x3f`
- `SBOX[0x26] = 0xf7`
- `SBOX[0x27] = 0xcc`
- `SBOX[0x28] = 0x34`
- `SBOX[0x29] = 0xa5`
- `SBOX[0x2a] = 0xe5`
- `SBOX[0x2b] = 0xf1`
- `SBOX[0x2c] = 0x71`
- `SBOX[0x2d] = 0xd8`
- `SBOX[0x2e] = 0x31`
- `SBOX[0x2f] = 0x15`
- `SBOX[0x30] = 0x04`
- `SBOX[0x31] = 0xc7`
- `SBOX[0x32] = 0x23`
- `SBOX[0x33] = 0xc3`
- `SBOX[0x34] = 0x18`
- `SBOX[0x35] = 0x96`
- `SBOX[0x36] = 0x05`
- `SBOX[0x37] = 0x9a`
- `SBOX[0x38] = 0x07`
- `SBOX[0x39] = 0x12`
- `SBOX[0x3a] = 0x80`
- `SBOX[0x3b] = 0xe2`
- `SBOX[0x3c] = 0xeb`
- `SBOX[0x3d] = 0x27`
- `SBOX[0x3e] = 0xb2`
- `SBOX[0x3f] = 0x75`
- `SBOX[0x40] = 0x09`
- `SBOX[0x41] = 0x83`
- `SBOX[0x42] = 0x2c`
- `SBOX[0x43] = 0x1a`
- `SBOX[0x44] = 0x1b`
- `SBOX[0x45] = 0x6e`
- `SBOX[0x46] = 0x5a`
- `SBOX[0x47] = 0xa0`
- `SBOX[0x48] = 0x52`
- `SBOX[0x49] = 0x3b`
- `SBOX[0x4a] = 0xd6`
- `SBOX[0x4b] = 0xb3`
- `SBOX[0x4c] = 0x29`
- `SBOX[0x4d] = 0xe3`
- `SBOX[0x4e] = 0x2f`
- `SBOX[0x4f] = 0x84`
- `SBOX[0x50] = 0x53`
- `SBOX[0x51] = 0xd1`
- `SBOX[0x52] = 0x00`
- `SBOX[0x53] = 0xed`
- `SBOX[0x54] = 0x20`
- `SBOX[0x55] = 0xfc`
- `SBOX[0x56] = 0xb1`
- `SBOX[0x57] = 0x5b`
- `SBOX[0x58] = 0x6a`
- `SBOX[0x59] = 0xcb`
- `SBOX[0x5a] = 0xbe`
- `SBOX[0x5b] = 0x39`
- `SBOX[0x5c] = 0x4a`
- `SBOX[0x5d] = 0x4c`
- `SBOX[0x5e] = 0x58`
- `SBOX[0x5f] = 0xcf`
- `SBOX[0x60] = 0xd0`
- `SBOX[0x61] = 0xef`
- `SBOX[0x62] = 0xaa`
- `SBOX[0x63] = 0xfb`
- `SBOX[0x64] = 0x43`
- `SBOX[0x65] = 0x4d`
- `SBOX[0x66] = 0x33`
- `SBOX[0x67] = 0x85`
- `SBOX[0x68] = 0x45`
- `SBOX[0x69] = 0xf9`
- `SBOX[0x6a] = 0x02`
- `SBOX[0x6b] = 0x7f`
- `SBOX[0x6c] = 0x50`
- `SBOX[0x6d] = 0x3c`
- `SBOX[0x6e] = 0x9f`
- `SBOX[0x6f] = 0xa8`
- `SBOX[0x70] = 0x51`
- `SBOX[0x71] = 0xa3`
- `SBOX[0x72] = 0x40`
- `SBOX[0x73] = 0x8f`
- `SBOX[0x74] = 0x92`
- `SBOX[0x75] = 0x9d`
- `SBOX[0x76] = 0x38`
- `SBOX[0x77] = 0xf5`
- `SBOX[0x78] = 0xbc`
- `SBOX[0x79] = 0xb6`
- `SBOX[0x7a] = 0xda`
- `SBOX[0x7b] = 0x21`
- `SBOX[0x7c] = 0x10`
- `SBOX[0x7d] = 0xff`
- `SBOX[0x7e] = 0xf3`
- `SBOX[0x7f] = 0xd2`
- `SBOX[0x80] = 0xcd`
- `SBOX[0x81] = 0x0c`
- `SBOX[0x82] = 0x13`
- `SBOX[0x83] = 0xec`
- `SBOX[0x84] = 0x5f`
- `SBOX[0x85] = 0x97`
- `SBOX[0x86] = 0x44`
- `SBOX[0x87] = 0x17`
- `SBOX[0x88] = 0xc4`
- `SBOX[0x89] = 0xa7`
- `SBOX[0x8a] = 0x7e`
- `SBOX[0x8b] = 0x3d`
- `SBOX[0x8c] = 0x64`
- `SBOX[0x8d] = 0x5d`
- `SBOX[0x8e] = 0x19`
- `SBOX[0x8f] = 0x73`
- `SBOX[0x90] = 0x60`
- `SBOX[0x91] = 0x81`
- `SBOX[0x92] = 0x4f`
- `SBOX[0x93] = 0xdc`
- `SBOX[0x94] = 0x22`
- `SBOX[0x95] = 0x2a`
- `SBOX[0x96] = 0x90`
- `SBOX[0x97] = 0x88`
- `SBOX[0x98] = 0x46`
- `SBOX[0x99] = 0xee`
- `SBOX[0x9a] = 0xb8`
- `SBOX[0x9b] = 0x14`
- `SBOX[0x9c] = 0xde`
- `SBOX[0x9d] = 0x5e`
- `SBOX[0x9e] = 0x0b`
- `SBOX[0x9f] = 0xdb`
- `SBOX[0xa0] = 0xe0`
- `SBOX[0xa1] = 0x32`
- `SBOX[0xa2] = 0x3a`
- `SBOX[0xa3] = 0x0a`
- `SBOX[0xa4] = 0x49`
- `SBOX[0xa5] = 0x06`
- `SBOX[0xa6] = 0x24`
- `SBOX[0xa7] = 0x5c`
- `SBOX[0xa8] = 0xc2`
- `SBOX[0xa9] = 0xd3`
- `SBOX[0xaa] = 0xac`
- `SBOX[0xab] = 0x62`
- `SBOX[0xac] = 0x91`
- `SBOX[0xad] = 0x95`
- `SBOX[0xae] = 0xe4`
- `SBOX[0xaf] = 0x79`
- `SBOX[0xb0] = 0xe7`
- `SBOX[0xb1] = 0xc8`
- `SBOX[0xb2] = 0x37`
- `SBOX[0xb3] = 0x6d`
- `SBOX[0xb4] = 0x8d`
- `SBOX[0xb5] = 0xd5`
- `SBOX[0xb6] = 0x4e`
- `SBOX[0xb7] = 0xa9`
- `SBOX[0xb8] = 0x6c`
- `SBOX[0xb9] = 0x56`
- `SBOX[0xba] = 0xf4`
- `SBOX[0xbb] = 0xea`
- `SBOX[0xbc] = 0x65`
- `SBOX[0xbd] = 0x7a`
- `SBOX[0xbe] = 0xae`
- `SBOX[0xbf] = 0x08`
- `SBOX[0xc0] = 0xba`
- `SBOX[0xc1] = 0x78`
- `SBOX[0xc2] = 0x25`
- `SBOX[0xc3] = 0x2e`
- `SBOX[0xc4] = 0x1c`
- `SBOX[0xc5] = 0xa6`
- `SBOX[0xc6] = 0xb4`
- `SBOX[0xc7] = 0xc6`
- `SBOX[0xc8] = 0xe8`
- `SBOX[0xc9] = 0xdd`
- `SBOX[0xca] = 0x74`
- `SBOX[0xcb] = 0x1f`
- `SBOX[0xcc] = 0x4b`
- `SBOX[0xcd] = 0xbd`
- `SBOX[0xce] = 0x8b`
- `SBOX[0xcf] = 0x8a`
- `SBOX[0xd0] = 0x70`
- `SBOX[0xd1] = 0x3e`
- `SBOX[0xd2] = 0xb5`
- `SBOX[0xd3] = 0x66`
- `SBOX[0xd4] = 0x48`
- `SBOX[0xd5] = 0x03`
- `SBOX[0xd6] = 0xf6`
- `SBOX[0xd7] = 0x0e`
- `SBOX[0xd8] = 0x61`
- `SBOX[0xd9] = 0x35`
- `SBOX[0xda] = 0x57`
- `SBOX[0xdb] = 0xb9`
- `SBOX[0xdc] = 0x86`
- `SBOX[0xdd] = 0xc1`
- `SBOX[0xde] = 0x1d`
- `SBOX[0xdf] = 0x9e`
- `SBOX[0xe0] = 0xe1`
- `SBOX[0xe1] = 0xf8`
- `SBOX[0xe2] = 0x98`
- `SBOX[0xe3] = 0x11`
- `SBOX[0xe4] = 0x69`
- `SBOX[0xe5] = 0xd9`
- `SBOX[0xe6] = 0x8e`
- `SBOX[0xe7] = 0x94`
- `SBOX[0xe8] = 0x9b`
- `SBOX[0xe9] = 0x1e`
- `SBOX[0xea] = 0x87`
- `SBOX[0xeb] = 0xe9`
- `SBOX[0xec] = 0xce`
- `SBOX[0xed] = 0x55`
- `SBOX[0xee] = 0x28`
- `SBOX[0xef] = 0xdf`
- `SBOX[0xf0] = 0x8c`
- `SBOX[0xf1] = 0xa1`
- `SBOX[0xf2] = 0x89`
- `SBOX[0xf3] = 0x0d`
- `SBOX[0xf4] = 0xbf`
- `SBOX[0xf5] = 0xe6`
- `SBOX[0xf6] = 0x42`
- `SBOX[0xf7] = 0x68`
- `SBOX[0xf8] = 0x41`
- `SBOX[0xf9] = 0x99`
- `SBOX[0xfa] = 0x2d`
- `SBOX[0xfb] = 0x0f`
- `SBOX[0xfc] = 0xb0`
- `SBOX[0xfd] = 0x54`
- `SBOX[0xfe] = 0xbb`
- `SBOX[0xff] = 0x16`

For each byte lane `i` from 0 to 15, the output is:
`out_bi = SBOX[in_bi]`.

The 16 byte lanes are independent. There is no permutation, packing, unpacking, ShiftRows, MixColumns, AddRoundKey, key input, or state carried across transactions.

Equivalent table relation to the AES implementation tables: if `Te4[x]` is the 32-bit table entry with identical bytes in all four byte positions, then `SBOX[x] = Te4[x] & 8'hff`.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state: none.

While in reset, `out_valid` shall be `0`. `out_b0` through `out_b15` shall all be `8'h00`. After reset, with no accepted input buffered for output, `out_valid` shall be `0` and `out_b0` through `out_b15` shall remain `8'h00` until an output transaction is presented.

`in_ready` may be either continuously asserted when the implementation can accept an input transaction or deasserted while holding a pending output, but reset shall leave the module with no buffered transaction pending.
