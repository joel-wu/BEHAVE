# Keccak 1600-bit Permutation (12 or 24 Rounds)

Apply all 24 rounds of Keccak-f[1600], or the final 12 rounds of Keccak-p[1600,12], to a caller-supplied state. No sponge padding or absorption is performed.

## Interface

### RTL interface

```systemverilog
module keccak_permutation_1600 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [1599:0] in_state,
    input logic [7:0] in_rounds,
    output logic out_valid,
    input logic out_ready,
    output logic [1599:0] out_state,
    output logic out_error
);
```

### Behavior interface

```python
class KeccakPermutation1600:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_state, in_rounds): ...
```

process returns one dictionary containing `out_state`, `out_error`.

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_state`: 1600.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Ports use unsigned payload bit patterns unless stated otherwise. An input is accepted on a rising edge with in_valid && in_ready, and an output is emitted with out_valid && out_ready. Once presented, valid and its payload remain stable until accepted; reset cancels pending transfers. Latency is unconstrained. Each accepted input transaction produces exactly one output transaction, in input order.

## Modules

`keccak_permutation_1600` is the complete top-level module.

## Functional Behavior

Interpret `in_state` as 25 unsigned 64-bit lanes:
- `aN = (in_state >> (64*N)) & ((1 << 64) - 1)` for `N = 0..24`

All lane values are 64-bit unsigned words. Every intermediate produced by XOR, AND, bitwise NOT, rotate-left, and round-constant XOR is truncated to 64 bits. There is no helper function in the behavioral contract other than the 64-bit rotate-left operator defined below.

Define:
- `MASK64 = 2^64 - 1`
- `MASK1600 = 2^1600 - 1`
- `ROL64(x, s) = ((x << s) | (x >> (64 - s))) & MASK64`

Round constants:
- `RC[0]  = 64'h0000000000000001`
- `RC[1]  = 64'h0000000000008082`
- `RC[2]  = 64'h800000000000808A`
- `RC[3]  = 64'h8000000080008000`
- `RC[4]  = 64'h000000000000808B`
- `RC[5]  = 64'h0000000080000001`
- `RC[6]  = 64'h8000000080008081`
- `RC[7]  = 64'h8000000000008009`
- `RC[8]  = 64'h000000000000008A`
- `RC[9]  = 64'h0000000000000088`
- `RC[10] = 64'h0000000080008009`
- `RC[11] = 64'h000000008000000A`
- `RC[12] = 64'h000000008000808B`
- `RC[13] = 64'h800000000000008B`
- `RC[14] = 64'h8000000000008089`
- `RC[15] = 64'h8000000000008003`
- `RC[16] = 64'h8000000000008002`
- `RC[17] = 64'h8000000000000080`
- `RC[18] = 64'h000000000000800A`
- `RC[19] = 64'h800000008000000A`
- `RC[20] = 64'h8000000080008081`
- `RC[21] = 64'h8000000000008080`
- `RC[22] = 64'h0000000080000001`
- `RC[23] = 64'h8000000080008008`

Rotation constants:
- `ROT_01 = 36`
- `ROT_02 = 3`
- `ROT_03 = 41`
- `ROT_04 = 18`
- `ROT_05 = 1`
- `ROT_06 = 44`
- `ROT_07 = 10`
- `ROT_08 = 45`
- `ROT_09 = 2`
- `ROT_10 = 62`
- `ROT_11 = 6`
- `ROT_12 = 43`
- `ROT_13 = 15`
- `ROT_14 = 61`
- `ROT_15 = 28`
- `ROT_16 = 55`
- `ROT_17 = 25`
- `ROT_18 = 21`
- `ROT_19 = 56`
- `ROT_20 = 27`
- `ROT_21 = 20`
- `ROT_22 = 39`
- `ROT_23 = 8`
- `ROT_24 = 14`

Round range:
- if `in_rounds == 8'd24`, set `start_round = 0`
- if `in_rounds == 8'd12`, set `start_round = 12`
- otherwise emit out_error=1 and out_state=0, without applying a permutation.

For each round `i` from `start_round` through `23`, compute the following in order.

Theta column parities:
- `c0 = a0 ^ a5 ^ a10 ^ a15 ^ a20`
- `c1 = a1 ^ a6 ^ a11 ^ a16 ^ a21`
- `c2 = a2 ^ a7 ^ a12 ^ a17 ^ a22`
- `c3 = a3 ^ a8 ^ a13 ^ a18 ^ a23`
- `c4 = a4 ^ a9 ^ a14 ^ a19 ^ a24`

Theta/Rho/Pi remap:
- `d   = c4 ^ ROL64(c1, 1)`
- `b0  = d ^ a0`
- `b16 = ROL64(d ^ a5,  36)`
- `b7  = ROL64(d ^ a10, 3)`
- `b23 = ROL64(d ^ a15, 41)`
- `b14 = ROL64(d ^ a20, 18)`

- `d   = c0 ^ ROL64(c2, 1)`
- `b10 = ROL64(d ^ a1,  1)`
- `b1  = ROL64(d ^ a6,  44)`
- `b17 = ROL64(d ^ a11, 10)`
- `b8  = ROL64(d ^ a16, 45)`
- `b24 = ROL64(d ^ a21, 2)`

- `d   = c1 ^ ROL64(c3, 1)`
- `b20 = ROL64(d ^ a2,  62)`
- `b11 = ROL64(d ^ a7,  6)`
- `b2  = ROL64(d ^ a12, 43)`
- `b18 = ROL64(d ^ a17, 15)`
- `b9  = ROL64(d ^ a22, 61)`

- `d   = c2 ^ ROL64(c4, 1)`
- `b5  = ROL64(d ^ a3,  28)`
- `b21 = ROL64(d ^ a8,  55)`
- `b12 = ROL64(d ^ a13, 25)`
- `b3  = ROL64(d ^ a18, 21)`
- `b19 = ROL64(d ^ a23, 56)`

- `d   = c3 ^ ROL64(c0, 1)`
- `b15 = ROL64(d ^ a4,  27)`
- `b6  = ROL64(d ^ a9,  20)`
- `b22 = ROL64(d ^ a14, 39)`
- `b13 = ROL64(d ^ a19, 8)`
- `b4  = ROL64(d ^ a24, 14)`

Chi and Iota, with 64-bit bitwise NOT defined as `(~x) & MASK64`:
- `a0  = (b0  ^ (((~b1)  & MASK64) & b2)  ^ RC[i]) & MASK64`
- `a1  = (b1  ^ (((~b2)  & MASK64) & b3)) & MASK64`
- `a2  = (b2  ^ (((~b3)  & MASK64) & b4)) & MASK64`
- `a3  = (b3  ^ (((~b4)  & MASK64) & b0)) & MASK64`
- `a4  = (b4  ^ (((~b0)  & MASK64) & b1)) & MASK64`

- `a5  = (b5  ^ (((~b6)  & MASK64) & b7)) & MASK64`
- `a6  = (b6  ^ (((~b7)  & MASK64) & b8)) & MASK64`
- `a7  = (b7  ^ (((~b8)  & MASK64) & b9)) & MASK64`
- `a8  = (b8  ^ (((~b9)  & MASK64) & b5)) & MASK64`
- `a9  = (b9  ^ (((~b5)  & MASK64) & b6)) & MASK64`

- `a10 = (b10 ^ (((~b11) & MASK64) & b12)) & MASK64`
- `a11 = (b11 ^ (((~b12) & MASK64) & b13)) & MASK64`
- `a12 = (b12 ^ (((~b13) & MASK64) & b14)) & MASK64`
- `a13 = (b13 ^ (((~b14) & MASK64) & b10)) & MASK64`
- `a14 = (b14 ^ (((~b10) & MASK64) & b11)) & MASK64`

- `a15 = (b15 ^ (((~b16) & MASK64) & b17)) & MASK64`
- `a16 = (b16 ^ (((~b17) & MASK64) & b18)) & MASK64`
- `a17 = (b17 ^ (((~b18) & MASK64) & b19)) & MASK64`
- `a18 = (b18 ^ (((~b19) & MASK64) & b15)) & MASK64`
- `a19 = (b19 ^ (((~b15) & MASK64) & b16)) & MASK64`

- `a20 = (b20 ^ (((~b21) & MASK64) & b22)) & MASK64`
- `a21 = (b21 ^ (((~b22) & MASK64) & b23)) & MASK64`
- `a22 = (b22 ^ (((~b23) & MASK64) & b24)) & MASK64`
- `a23 = (b23 ^ (((~b24) & MASK64) & b20)) & MASK64`
- `a24 = (b24 ^ (((~b20) & MASK64) & b21)) & MASK64`

After the last round, pack the 25 lanes back into `out_state` exactly as:
- `out_state = ((a0 << 0) | (a1 << 64) | (a2 << 128) | (a3 << 192) | (a4 << 256) | (a5 << 320) | (a6 << 384) | (a7 << 448) | (a8 << 512) | (a9 << 576) | (a10 << 640) | (a11 << 704) | (a12 << 768) | (a13 << 832) | (a14 << 896) | (a15 << 960) | (a16 << 1024) | (a17 << 1088) | (a18 << 1152) | (a19 << 1216) | (a20 << 1280) | (a21 << 1344) | (a22 << 1408) | (a23 << 1472) | (a24 << 1536)) & MASK1600`

For a valid round count, `out_state` is the computed state and `out_error = 0`.

The mapping is purely combinational at the transaction level:
- every accepted input produces exactly one output
- no architectural state is carried from one accepted transaction to the next

## Reset Behavior

Reset is synchronous active-high rst. It empties pending output; out_valid is low during reset. There is no persistent arithmetic state. A new request after reset is independent of requests before reset.
