# KeccakTheta

KeccakTheta applies the Theta step of the Keccak-f[1600] permutation to one 1600-bit state. The state is represented as 25 lanes of 64 bits each, ordered as a flat array `a[0]..a[24]` in row-major form where lane index `x + 5*y` corresponds to coordinates `(x,y)`. For each of the 5 columns, the module computes the column parity, forms the Theta correction word `D[x] = C[(x-1) mod 5] XOR ROTL64(C[(x+1) mod 5], 1)`, and XORs `D[x]` into every lane of column `x`. The block is a stateless 1:1 transaction mapper.

## Interface

### RTL interface

```systemverilog
module keccak_theta (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a0,
    input  logic [63:0]  in_a1,
    input  logic [63:0]  in_a2,
    input  logic [63:0]  in_a3,
    input  logic [63:0]  in_a4,
    input  logic [63:0]  in_a5,
    input  logic [63:0]  in_a6,
    input  logic [63:0]  in_a7,
    input  logic [63:0]  in_a8,
    input  logic [63:0]  in_a9,
    input  logic [63:0]  in_a10,
    input  logic [63:0]  in_a11,
    input  logic [63:0]  in_a12,
    input  logic [63:0]  in_a13,
    input  logic [63:0]  in_a14,
    input  logic [63:0]  in_a15,
    input  logic [63:0]  in_a16,
    input  logic [63:0]  in_a17,
    input  logic [63:0]  in_a18,
    input  logic [63:0]  in_a19,
    input  logic [63:0]  in_a20,
    input  logic [63:0]  in_a21,
    input  logic [63:0]  in_a22,
    input  logic [63:0]  in_a23,
    input  logic [63:0]  in_a24,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_a0,
    output logic [63:0]  out_a1,
    output logic [63:0]  out_a2,
    output logic [63:0]  out_a3,
    output logic [63:0]  out_a4,
    output logic [63:0]  out_a5,
    output logic [63:0]  out_a6,
    output logic [63:0]  out_a7,
    output logic [63:0]  out_a8,
    output logic [63:0]  out_a9,
    output logic [63:0]  out_a10,
    output logic [63:0]  out_a11,
    output logic [63:0]  out_a12,
    output logic [63:0]  out_a13,
    output logic [63:0]  out_a14,
    output logic [63:0]  out_a15,
    output logic [63:0]  out_a16,
    output logic [63:0]  out_a17,
    output logic [63:0]  out_a18,
    output logic [63:0]  out_a19,
    output logic [63:0]  out_a20,
    output logic [63:0]  out_a21,
    output logic [63:0]  out_a22,
    output logic [63:0]  out_a23,
    output logic [63:0]  out_a24
);
```

### Behavior interface

```python
class KeccakTheta:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4,
        in_a5, in_a6, in_a7, in_a8, in_a9,
        in_a10, in_a11, in_a12, in_a13, in_a14,
        in_a15, in_a16, in_a17, in_a18, in_a19,
        in_a20, in_a21, in_a22, in_a23, in_a24
    ):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a0`, `out_a1`, `out_a10`, `out_a11`, `out_a12`, `out_a13`, `out_a14`,
  `out_a15`, `out_a16`, `out_a17`, `out_a18`, `out_a19`, `out_a2`, `out_a20`,
  `out_a21`, `out_a22`, `out_a23`, `out_a24`, `out_a3`, `out_a4`, `out_a5`, `out_a6`,
  `out_a7`, `out_a8`, `out_a9`: 64.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `keccak_theta`: top-level module implementing one Theta transform on a 25-lane Keccak-f[1600] state.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

Each accepted input transaction supplies 25 unsigned 64-bit lanes `in_a0..in_a24`. Each output lane `out_a0..out_a24` is the corresponding lane after one Theta step. All arithmetic is on 64-bit words with wrap modulo `2^64`. Bitwise NOT is taken over 64 bits only. The module has no persistent architectural state affecting the mapping.

Lane indexing is:
- `a0,a5,a10,a15,a20` are column `x=0`
- `a1,a6,a11,a16,a21` are column `x=1`
- `a2,a7,a12,a17,a22` are column `x=2`
- `a3,a8,a13,a18,a23` are column `x=3`
- `a4,a9,a14,a19,a24` are column `x=4`

First compute the 5 column parities:
- `C0 = a0 XOR a5 XOR a10 XOR a15 XOR a20`
- `C1 = a1 XOR a6 XOR a11 XOR a16 XOR a21`
- `C2 = a2 XOR a7 XOR a12 XOR a17 XOR a22`
- `C3 = a3 XOR a8 XOR a13 XOR a18 XOR a23`
- `C4 = a4 XOR a9 XOR a14 XOR a19 XOR a24`

Define `ROTL64(x,1)` as a 64-bit rotate-left by 1 bit:
- `ROTL64(x,1) = ((x << 1) | (x >> 63)) mod 2^64`

Then compute the 5 Theta correction words:
- `D0 = C4 XOR ROTL64(C1,1)`
- `D1 = C0 XOR ROTL64(C2,1)`
- `D2 = C1 XOR ROTL64(C3,1)`
- `D3 = C2 XOR ROTL64(C4,1)`
- `D4 = C3 XOR ROTL64(C0,1)`

Finally XOR the correction word of each column into every lane of that column:
- `out_a0  = a0  XOR D0`
- `out_a5  = a5  XOR D0`
- `out_a10 = a10 XOR D0`
- `out_a15 = a15 XOR D0`
- `out_a20 = a20 XOR D0`

- `out_a1  = a1  XOR D1`
- `out_a6  = a6  XOR D1`
- `out_a11 = a11 XOR D1`
- `out_a16 = a16 XOR D1`
- `out_a21 = a21 XOR D1`

- `out_a2  = a2  XOR D2`
- `out_a7  = a7  XOR D2`
- `out_a12 = a12 XOR D2`
- `out_a17 = a17 XOR D2`
- `out_a22 = a22 XOR D2`

- `out_a3  = a3  XOR D3`
- `out_a8  = a8  XOR D3`
- `out_a13 = a13 XOR D3`
- `out_a18 = a18 XOR D3`
- `out_a23 = a23 XOR D3`

- `out_a4  = a4  XOR D4`
- `out_a9  = a9  XOR D4`
- `out_a14 = a14 XOR D4`
- `out_a19 = a19 XOR D4`
- `out_a24 = a24 XOR D4`

Every output port is a 64-bit unsigned bit pattern equal to the low 64 bits of the stated expression.

## Reset Behavior

Reset is synchronous active-high `rst`. The block has no architectural state. After reset, the module is ready to accept an input transaction. Output payload ports `out_a0..out_a24` and `out_valid` shall be `0` whenever no output transaction is being presented. `in_ready` shall be `1` whenever the module is able to accept an input transaction; a compliant implementation may keep `in_ready` high continuously.
