# Salsa20 Core Block

This block computes one Salsa-family core transformation on a 512-bit state assembled from a 128-bit input block, a 256-bit key, and a 128-bit constant block. The algorithm is the Salsa quarterround network arranged as alternating column rounds and row rounds. The implementation covered by this specification performs the 20-round Salsa20 core: ten double-rounds, then a word-wise feed-forward addition of the original state, producing one 512-bit output block per accepted input transaction.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_chacha_salsa20_quarterround (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_in,
    input  logic [255:0] in_k,
    input  logic [127:0] in_c,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [511:0] out_out
);
```

### Behavior interface

```python
class CryptographySymChachaSalsa20Quarterround:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_in, in_k, in_c): ...
```

`process()` returns a dict with one key:

- `out_out`: 512-bit unsigned integer

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_out`: 512.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cryptography_sym_chacha_salsa20_quarterround`: top-level Salsa20 core block.

## Functional Behavior

All arithmetic is unsigned 32-bit modulo \(2^{32}\). Every addition discards carry-out above bit 31. Every rotation is a 32-bit rotate-left.

The 128-bit input ports are packed little-endian by 32-bit words:

- `in_in[31:0]` is word `in0`
- `in_in[63:32]` is word `in1`
- `in_in[95:64]` is word `in2`
- `in_in[127:96]` is word `in3`

The 256-bit key port is packed little-endian by 32-bit words:

- `in_k[31:0]` is word `k0`
- `in_k[63:32]` is word `k1`
- `in_k[95:64]` is word `k2`
- `in_k[127:96]` is word `k3`
- `in_k[159:128]` is word `k4`
- `in_k[191:160]` is word `k5`
- `in_k[223:192]` is word `k6`
- `in_k[255:224]` is word `k7`

The 128-bit constant port is packed little-endian by 32-bit words:

- `in_c[31:0]` is word `c0`
- `in_c[63:32]` is word `c1`
- `in_c[95:64]` is word `c2`
- `in_c[127:96]` is word `c3`

The initial 16-word state is:

- `j0  = x0  = c0`
- `j1  = x1  = k0`
- `j2  = x2  = k1`
- `j3  = x3  = k2`
- `j4  = x4  = k3`
- `j5  = x5  = c1`
- `j6  = x6  = in0`
- `j7  = x7  = in1`
- `j8  = x8  = in2`
- `j9  = x9  = in3`
- `j10 = x10 = c2`
- `j11 = x11 = k4`
- `j12 = x12 = k5`
- `j13 = x13 = k6`
- `j14 = x14 = k7`
- `j15 = x15 = c3`

Define `ROTL32(v, s)` as `((v << s) | (v >> (32 - s))) mod 2^32`, with `s` equal to one of 7, 9, 13, or 18.

The block performs 20 rounds as 10 identical double-rounds. Each double-round applies the following 32 updates in this exact order, where every right-hand side uses the current value produced by all earlier assignments in the sequence:

1. `x4  = x4  XOR ROTL32(x0  + x12, 7)`
2. `x8  = x8  XOR ROTL32(x4  + x0, 9)`
3. `x12 = x12 XOR ROTL32(x8  + x4, 13)`
4. `x0  = x0  XOR ROTL32(x12 + x8, 18)`

5. `x9  = x9  XOR ROTL32(x5  + x1, 7)`
6. `x13 = x13 XOR ROTL32(x9  + x5, 9)`
7. `x1  = x1  XOR ROTL32(x13 + x9, 13)`
8. `x5  = x5  XOR ROTL32(x1  + x13, 18)`

9. `x14 = x14 XOR ROTL32(x10 + x6, 7)`
10. `x2  = x2  XOR ROTL32(x14 + x10, 9)`
11. `x6  = x6  XOR ROTL32(x2  + x14, 13)`
12. `x10 = x10 XOR ROTL32(x6  + x2, 18)`

13. `x3  = x3  XOR ROTL32(x15 + x11, 7)`
14. `x7  = x7  XOR ROTL32(x3  + x15, 9)`
15. `x11 = x11 XOR ROTL32(x7  + x3, 13)`
16. `x15 = x15 XOR ROTL32(x11 + x7, 18)`

17. `x1  = x1  XOR ROTL32(x0  + x3, 7)`
18. `x2  = x2  XOR ROTL32(x1  + x0, 9)`
19. `x3  = x3  XOR ROTL32(x2  + x1, 13)`
20. `x0  = x0  XOR ROTL32(x3  + x2, 18)`

21. `x6  = x6  XOR ROTL32(x5  + x4, 7)`
22. `x7  = x7  XOR ROTL32(x6  + x5, 9)`
23. `x4  = x4  XOR ROTL32(x7  + x6, 13)`
24. `x5  = x5  XOR ROTL32(x4  + x7, 18)`

25. `x11 = x11 XOR ROTL32(x10 + x9, 7)`
26. `x8  = x8  XOR ROTL32(x11 + x10, 9)`
27. `x9  = x9  XOR ROTL32(x8  + x11, 13)`
28. `x10 = x10 XOR ROTL32(x9  + x8, 18)`

29. `x12 = x12 XOR ROTL32(x15 + x14, 7)`
30. `x13 = x13 XOR ROTL32(x12 + x15, 9)`
31. `x14 = x14 XOR ROTL32(x13 + x12, 13)`
32. `x15 = x15 XOR ROTL32(x14 + x13, 18)`

After the tenth double-round, compute the feed-forward sums:

- `y0  = x0  + j0`
- `y1  = x1  + j1`
- `y2  = x2  + j2`
- `y3  = x3  + j3`
- `y4  = x4  + j4`
- `y5  = x5  + j5`
- `y6  = x6  + j6`
- `y7  = x7  + j7`
- `y8  = x8  + j8`
- `y9  = x9  + j9`
- `y10 = x10 + j10`
- `y11 = x11 + j11`
- `y12 = x12 + j12`
- `y13 = x13 + j13`
- `y14 = x14 + j14`
- `y15 = x15 + j15`

Each `yN` is reduced modulo \(2^{32}\).

`out_out` packs the 16 output words little-endian by 32-bit words:

- `out_out[31:0]` = `y0`
- `out_out[63:32]` = `y1`
- `out_out[95:64]` = `y2`
- `out_out[127:96]` = `y3`
- `out_out[159:128]` = `y4`
- `out_out[191:160]` = `y5`
- `out_out[223:192]` = `y6`
- `out_out[255:224]` = `y7`
- `out_out[287:256]` = `y8`
- `out_out[319:288]` = `y9`
- `out_out[351:320]` = `y10`
- `out_out[383:352]` = `y11`
- `out_out[415:384]` = `y12`
- `out_out[447:416]` = `y13`
- `out_out[479:448]` = `y14`
- `out_out[511:480]` = `y15`

The block is stateless across transactions. Each accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is active-high synchronous reset.

Architectural state after reset: none.

Output port values while reset is asserted or before the first accepted input/output transaction pair:

- `in_ready = 1`
- `out_valid = 0`
- `out_out = 512'h0`
