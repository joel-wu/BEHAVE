# AES ShiftRows

AES ShiftRows is a stateless 128-bit permutation that applies the forward Rijndael ShiftRows step to one AES state. The input block is interpreted as 16 bytes arranged in the standard AES 4×4 state matrix in column-major order. The output block contains the same bytes after row-wise cyclic left rotations by 0, 1, 2, and 3 byte positions for rows 0 through 3.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_aes_aes_shiftrows (
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
class CryptographySymAesAesShiftrows:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_state): ...
```

The `process()` method returns one output transaction:
```python
{"out_state": ...}
```

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

- `cryptography_sym_aes_aes_shiftrows`: top-level module implementing the AES forward ShiftRows permutation.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

`in_state` is a 128-bit unsigned integer. Define input bytes `b[0]` through `b[15]` by big-endian extraction:
- `b[0] = in_state[127:120]`
- `b[1] = in_state[119:112]`
- ...
- `b[15] = in_state[7:0]`

These bytes populate the AES state matrix `S[row][col]` in column-major order:
- `S[0][0]=b[0]`, `S[1][0]=b[1]`, `S[2][0]=b[2]`, `S[3][0]=b[3]`
- `S[0][1]=b[4]`, `S[1][1]=b[5]`, `S[2][1]=b[6]`, `S[3][1]=b[7]`
- `S[0][2]=b[8]`, `S[1][2]=b[9]`, `S[2][2]=b[10]`, `S[3][2]=b[11]`
- `S[0][3]=b[12]`, `S[1][3]=b[13]`, `S[2][3]=b[14]`, `S[3][3]=b[15]`

The module computes the forward ShiftRows transform `T[row][col]`:
- row 0 unchanged:
  - `T[0][0]=S[0][0]`, `T[0][1]=S[0][1]`, `T[0][2]=S[0][2]`, `T[0][3]=S[0][3]`
- row 1 rotated left by 1 byte:
  - `T[1][0]=S[1][1]`, `T[1][1]=S[1][2]`, `T[1][2]=S[1][3]`, `T[1][3]=S[1][0]`
- row 2 rotated left by 2 bytes:
  - `T[2][0]=S[2][2]`, `T[2][1]=S[2][3]`, `T[2][2]=S[2][0]`, `T[2][3]=S[2][1]`
- row 3 rotated left by 3 bytes:
  - `T[3][0]=S[3][3]`, `T[3][1]=S[3][0]`, `T[3][2]=S[3][1]`, `T[3][3]=S[3][2]`

`out_state` is formed by writing `T` back in the same column-major order. Define output bytes `o[0]` through `o[15]`:
- `o[0]=T[0][0]`, `o[1]=T[1][0]`, `o[2]=T[2][0]`, `o[3]=T[3][0]`
- `o[4]=T[0][1]`, `o[5]=T[1][1]`, `o[6]=T[2][1]`, `o[7]=T[3][1]`
- `o[8]=T[0][2]`, `o[9]=T[1][2]`, `o[10]=T[2][2]`, `o[11]=T[3][2]`
- `o[12]=T[0][3]`, `o[13]=T[1][3]`, `o[14]=T[2][3]`, `o[15]=T[3][3]`

This is the fixed byte permutation:
- `o[0]=b[0]`
- `o[1]=b[5]`
- `o[2]=b[10]`
- `o[3]=b[15]`
- `o[4]=b[4]`
- `o[5]=b[9]`
- `o[6]=b[14]`
- `o[7]=b[3]`
- `o[8]=b[8]`
- `o[9]=b[13]`
- `o[10]=b[2]`
- `o[11]=b[7]`
- `o[12]=b[12]`
- `o[13]=b[1]`
- `o[14]=b[6]`
- `o[15]=b[11]`

The output word is assembled in big-endian byte order:
- `out_state[127:120]=o[0]`
- `out_state[119:112]=o[1]`
- ...
- `out_state[7:0]=o[15]`

Each accepted input transaction produces exactly one output transaction. The transform is purely combinational at the transaction level and has no data-dependent control flow.

## Reset Behavior

Reset is synchronous active-high via `rst`.

After reset:
- the module is idle
- no architectural state is stored
- `in_ready = 1`
- `out_valid = 0`
- `out_state = 128'h00000000000000000000000000000000`
