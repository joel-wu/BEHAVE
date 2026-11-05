# des_enc

Sixteen-stage DES encryptor with per-block keys, bubbles and full-rate bursts.

## Interface

### RTL interface

```systemverilog
module des_enc #(
    parameter int NBW_DATA=64,
    parameter int NBW_KEY=64
) (
    input logic clk, rst_async_n, i_valid,
    input logic [1:NBW_DATA] i_data,
    input logic [1:NBW_KEY] i_key,
    output logic o_valid,
    output logic [1:NBW_DATA] o_data
);
```

### Behavior interface

```python
class DesEnc:
    def __init__(self, NBW_DATA=64, NBW_KEY=64): ...
    def reset(self): ...
    def process(self, i_valid, i_data, i_key): ...
```

Each call returns all settled output pin values immediately BEFORE one rising
edge of `clk`, then advances that edge. Arguments and outputs are unsigned port bit patterns.
Every call represents a clock edge, including idle cycles.
Reset is controlled separately and produces no logical records.

Use the supported constructor values `NBW_DATA=NBW_KEY=64`. Each call returns
exactly `{"o_valid": valid, "o_data": data}`, with integer `valid` in {0, 1}
and integer `data` in 0..2^64-1. Invalid output cycles return
`{"o_valid": 0, "o_data": 0}`, not `None` or an empty sequence.
An input accepted at call index k is observed in call k+16. This pre-edge
sampling convention adds no hardware stage. The model may compute a block
atomically and retain its result until due. `reset()` clears the pipeline.

## Modules

- `des_enc`: the complete native module above.

## Functional Behavior

Implement bit-exact DES encryption, not decryption. NBW_DATA=NBW_KEY=64
is the supported format. Bit 1 in an ascending [1:64] port is its MSB; no byte
reversal is applied. Key parity bits are ignored by PC-1, not checked or repaired.

On every rising edge with i_valid=1, accept that edge's plaintext/key pair.
There is no ready signal and no output backpressure. Sixteen round stages advance
on EVERY edge, including i_valid=0 gaps. A block accepted at edge e appears with
o_valid=1 just after edge e+15: the acceptance edge is round/cycle 1 and e+15 is
round/cycle 16, matching the upstream latency test. Consecutive accepted blocks
produce consecutive valid outputs; each retains its own key. Input gaps produce
equally delayed output gaps. o_valid is the 16-stage delayed i_valid bit, not a
single pending-result flag. As a completion of unspecified invalid data, o_data=0
whenever o_valid=0. During reset both outputs are zero and all in-flight blocks
are discarded. The first new completion requires sixteen post-reset edges.

The reference RTL implements a round-per-stage pipeline. S-boxes may be helper
functions within rtl.sv; separate S1.sv...S8.sv files are not an external interface
requirement. The following original DES mapping/tables are retained:

The encryption steps are:

1. Apply the Initial Permutation `IP` to `i_data` using this 64-entry table:

   `IP = [58, 50, 42, 34, 26, 18, 10, 2,
          60, 52, 44, 36, 28, 20, 12, 4,
          62, 54, 46, 38, 30, 22, 14, 6,
          64, 56, 48, 40, 32, 24, 16, 8,
          57, 49, 41, 33, 25, 17, 9, 1,
          59, 51, 43, 35, 27, 19, 11, 3,
          61, 53, 45, 37, 29, 21, 13, 5,
          63, 55, 47, 39, 31, 23, 15, 7]`

   Let the 64-bit permuted value be split into `L0` and `R0`, where `L0` is the upper 32 bits and `R0` is the lower 32 bits.

2. Generate the 16 round keys `K1` through `K16` from `i_key`.

   First apply the parity-drop permutation, split into `C0` and `D0`, using these two 28-entry tables:

   `PC1_C = [57, 49, 41, 33, 25, 17, 9,
             1, 58, 50, 42, 34, 26, 18,
             10, 2, 59, 51, 43, 35, 27,
             19, 11, 3, 60, 52, 44, 36]`

   `PC1_D = [63, 55, 47, 39, 31, 23, 15,
             7, 62, 54, 46, 38, 30, 22,
             14, 6, 61, 53, 45, 37, 29,
             21, 13, 5, 28, 20, 12, 4]`

   For round `n` from 1 to 16, rotate both 28-bit halves `C(n-1)` and `D(n-1)` left by `SHIFT[n]`, where:

   `SHIFT = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]`

   Left rotation is circular within 28 bits.

   Concatenate `Cn || Dn` into a 56-bit value and apply the 48-entry compression permutation `PC2`:

   `PC2 = [14, 17, 11, 24, 1, 5,
           3, 28, 15, 6, 21, 10,
           23, 19, 12, 4, 26, 8,
           16, 7, 27, 20, 13, 2,
           41, 52, 31, 37, 47, 55,
           30, 40, 51, 45, 33, 48,
           44, 49, 39, 56, 34, 53,
           46, 42, 50, 36, 29, 32]`

   The result is round key `Kn`.

3. Perform 16 Feistel rounds. For round `n` from 1 to 16:

   - `Ln = R(n-1)`
   - `Rn = L(n-1) XOR F(R(n-1), Kn)`

   The round function `F(R, K)` is defined as follows.

   Apply the 32-to-48 expansion permutation `E` to `R`:

   `E = [32, 1, 2, 3, 4, 5,
         4, 5, 6, 7, 8, 9,
         8, 9, 10, 11, 12, 13,
         12, 13, 14, 15, 16, 17,
         16, 17, 18, 19, 20, 21,
         20, 21, 22, 23, 24, 25,
         24, 25, 26, 27, 28, 29,
         28, 29, 30, 31, 32, 1]`

   XOR the expanded 48-bit value with the 48-bit round key `K`.

   Split the 48-bit XOR result into eight 6-bit groups from most significant to least significant. For group `Bi`, `i = 1..8`, determine the row from its first and last bits as a 2-bit number, and the column from its middle four bits as a 4-bit number. Pass `Bi` through S-box `Si` and obtain a 4-bit result. Concatenate the eight 4-bit S-box outputs in order `S1` through `S8` to form a 32-bit value.

   The eight S-box tables are:

   `S1 = [
     [14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7],
     [0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8],
     [4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0],
     [15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13]
   ]`

   `S2 = [
     [15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10],
     [3,13,4,7,15,2,8,14,12,0,1,10,6,9,11,5],
     [0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15],
     [13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9]
   ]`

   `S3 = [
     [10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8],
     [13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1],
     [13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7],
     [1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12]
   ]`

   `S4 = [
     [7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15],
     [13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9],
     [10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4],
     [3,15,0,6,10,1,13,8,9,4,5,11,12,7,2,14]
   ]`

   `S5 = [
     [2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9],
     [14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6],
     [4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14],
     [11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3]
   ]`

   `S6 = [
     [12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11],
     [10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8],
     [9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6],
     [4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13]
   ]`

   `S7 = [
     [4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1],
     [13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6],
     [1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2],
     [6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12]
   ]`

   `S8 = [
     [13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7],
     [1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2],
     [7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8],
     [2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11]
   ]`

   Then apply permutation `P` to the 32-bit concatenated S-box result:

   `P = [16, 7, 20, 21,
         29, 12, 28, 17,
         1, 15, 23, 26,
         5, 18, 31, 10,
         2, 8, 24, 14,
         32, 27, 3, 9,
         19, 13, 30, 6,
         22, 11, 4, 25]`

   `F(R, K)` is the 32-bit result of that permutation.

4. After round 16, form the 64-bit preoutput block by concatenating `R16 || L16`, in that order.

5. Apply the Final Permutation `FP` to the preoutput block using this 64-entry table:

   `FP = [40, 8, 48, 16, 56, 24, 64, 32,
          39, 7, 47, 15, 55, 23, 63, 31,
          38, 6, 46, 14, 54, 22, 62, 30,
          37, 5, 45, 13, 53, 21, 61, 29,
          36, 4, 44, 12, 52, 20, 60, 28,
          35, 3, 43, 11, 51, 19, 59, 27,
          34, 2, 42, 10, 50, 18, 58, 26,
          33, 1, 41, 9, 49, 17, 57, 25]`

   The result is `o_data`.

The module performs DES encryption only. There is no decrypt mode, no key parity checking, no weak-key handling, and no error signaling. All 64 key bits participate only through the specified `PC1_C` and `PC1_D` selections; the dropped parity bits have no effect.

## Reset Behavior

`rst_async_n` is asynchronous, active low. Assertion immediately restores the initial state. All pipeline valid bits, data and key state are zero; no block is pending.
After reset release, resume the edge-by-edge behavior above.
