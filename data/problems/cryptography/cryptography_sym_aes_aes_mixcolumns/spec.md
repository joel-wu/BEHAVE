# AES MixColumns

AES MixColumns computes the forward Rijndael column-mixing transform for one 128-bit AES state. The input state is treated as 16 bytes arranged as four 32-bit columns in big-endian byte order, and each column is transformed independently by multiplication over GF(2^8) with the fixed AES MixColumns matrix. The module implements only the forward MixColumns step; it does not perform SubBytes, ShiftRows, AddRoundKey, inverse MixColumns, key expansion, or whole-block encryption/decryption.

## Interface

### RTL interface

```systemverilog
module aes_mixcolumns (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_state,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_state
);
```

### Behavior interface

```python
class AESMixColumns:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_state): ...
```

`process()` returns `{"out_state": ...}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_state`: 128.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `aes_mixcolumns`: top-level module implementing one forward AES MixColumns transform on a 128-bit state.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

`in_state` is a 128-bit unsigned integer carrying 16 bytes. Define input bytes `b[0]..b[15]` by big-endian slicing:

- `b[0]  = in_state[127:120]`
- `b[1]  = in_state[119:112]`
- ...
- `b[15] = in_state[7:0]`

The state is partitioned into four columns:

- column 0 = `(b[0],  b[1],  b[2],  b[3])`
- column 1 = `(b[4],  b[5],  b[6],  b[7])`
- column 2 = `(b[8],  b[9],  b[10], b[11])`
- column 3 = `(b[12], b[13], b[14], b[15])`

Each byte is an element of GF(2^8) with Rijndael reduction polynomial
`m(x) = x^8 + x^4 + x^3 + x + 1`, represented by the hexadecimal constant `0x11B`.

For any input byte `x` in `[0,255]`, define multiplication by 2 and 3 in GF(2^8):

- `xtime(x) = ((x << 1) & 0xFF) ^ 0x1B` if `x[7] = 1`
- `xtime(x) = (x << 1) & 0xFF` if `x[7] = 0`
- `mul2(x) = xtime(x)`
- `mul3(x) = xtime(x) ^ x`

For each column `(a0, a1, a2, a3)`, compute output column `(c0, c1, c2, c3)` as:

- `c0 = mul2(a0) ^ mul3(a1) ^ a2 ^ a3`
- `c1 = a0 ^ mul2(a1) ^ mul3(a2) ^ a3`
- `c2 = a0 ^ a1 ^ mul2(a2) ^ mul3(a3)`
- `c3 = mul3(a0) ^ a1 ^ a2 ^ mul2(a3)`

All XORs are bitwise XOR on 8-bit values. Each result byte is reduced modulo the AES polynomial by the definitions of `mul2` and `mul3`, so `c0..c3` are each 8-bit values in `[0,255]`.

Equivalently, each column is multiplied by the fixed AES MixColumns matrix:

\[
\begin{bmatrix}
02 & 03 & 01 & 01 \\
01 & 02 & 03 & 01 \\
01 & 01 & 02 & 03 \\
03 & 01 & 01 & 02
\end{bmatrix}
\]

over GF(2^8).

Let output bytes `o[0]..o[15]` be the concatenation of the four transformed columns in the same byte positions:

- `(o[0],  o[1],  o[2],  o[3])   = MixColumn(b[0],  b[1],  b[2],  b[3])`
- `(o[4],  o[5],  o[6],  o[7])   = MixColumn(b[4],  b[5],  b[6],  b[7])`
- `(o[8],  o[9],  o[10], o[11])  = MixColumn(b[8],  b[9],  b[10], b[11])`
- `(o[12], o[13], o[14], o[15])  = MixColumn(b[12], b[13], b[14], b[15])`

`out_state` is the 128-bit unsigned integer formed by big-endian concatenation of `o[0]..o[15]`:

- `out_state[127:120] = o[0]`
- `out_state[119:112] = o[1]`
- ...
- `out_state[7:0]     = o[15]`

The block is stateless at the architectural level. Each accepted input transaction produces exactly one output transaction with the transformed state.

Worked example for one column:

- input column `(0xDB, 0x13, 0x53, 0x45)`
- `mul2(0xDB)=0xAD`, `mul3(0x13)=0x35`, so `c0 = 0xAD ^ 0x35 ^ 0x53 ^ 0x45 = 0x8E`
- `mul2(0x13)=0x26`, `mul3(0x53)=0xF5`, so `c1 = 0xDB ^ 0x26 ^ 0xF5 ^ 0x45 = 0x4D`
- `mul2(0x53)=0xA6`, `mul3(0x45)=0xCF`, so `c2 = 0xDB ^ 0x13 ^ 0xA6 ^ 0xCF = 0xA1`
- `mul3(0xDB)=0x76`, `mul2(0x45)=0x8A`, so `c3 = 0x76 ^ 0x13 ^ 0x53 ^ 0x8A = 0xBC`

Thus that column transforms to `(0x8E, 0x4D, 0xA1, 0xBC)`.

## Reset Behavior

Reset is synchronous active-high `rst`.

There is no architectural state.

After reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_state = 128'h00000000000000000000000000000000`
