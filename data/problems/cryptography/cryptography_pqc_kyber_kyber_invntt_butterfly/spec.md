# Kyber inverse NTT pair

One pair update from invntt, before its final whole-transform scaling.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_kyber_kyber_invntt_butterfly (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_twiddle_index,
    input  logic [15:0] in_a,
    input  logic [15:0] in_b,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_a,
    output logic [15:0] out_b
);
```

`in_a` and `in_b` are 16-bit two's-complement signed integers representing Kyber field elements in the same representation used by the reference C code's `int16_t`. `in_twiddle_index` is an unsigned 8-bit twiddle selector. The selected table entry is `zetas[in_twiddle_index & 7'h7F]`.

### Behavior interface

```python
class KyberInvnttButterfly:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_twiddle_index, in_a, in_b):
        pass
```

The return value is one dictionary with keys `out_a` and `out_b`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a`, `out_b`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_pqc_kyber_kyber_invntt_butterfly` implements the public interface and operation specified here.

## Functional Behavior

One pair update from invntt, before its final whole-transform scaling. Inputs a,b are signed 16-bit. Use zeta=zetas[in_twiddle_index & 127], with the fixed table below. Let s16 denote signed narrowing to 16 bits. Set v=s16(a+b), d=s16(b-a), u=s16((zeta*d)*62209). Return:

```
out_a = (v - 3329*floor((20159*v+2^25)/2^26)) modulo 2^16
out_b = ((zeta*d - 3329*u) >> 16) modulo 2^16
```

The sum is narrowed at the Barrett function argument and the difference at the source's int16 array assignment, before multiplication. The Montgomery u is signed. These details define every input bit pattern. The caller supplies the actual table index; it is not reversed again inside this task. Factor1441 and the remaining transform layers are not part of this pair update.

zetas[0..127]:
`[-1044, -758, -359, -1517, 1493, 1422, 287, 202, -171, 622, 1577, 182, 962, -1202, -1474, 1468, 573, -1325, 264, 383, -829, 1458, -1602, -130, -681, 1017, 732, 608, -1542, 411, -205, -1571, 1223, 652, -552, 1015, -1293, 1491, -282, -1544, 516, -8, -320, -666, -1618, -1162, 126, 1469, -853, -90, -271, 830, 107, -1421, -247, -951, -398, 961, -1508, -725, 448, -1065, 677, -1275, -1103, 430, 555, 843, -1251, 871, 1550, 105, 422, 587, 177, -235, -291, -460, 1574, 1653, -246, 778, 1159, -147, -777, 1483, -602, 1119, -1590, 644, -872, 349, 418, 329, -156, -75, 817, 1097, 603, 610, 1322, -1285, -1465, 384, -1215, -136, 1218, -1335, -874, 220, -1187, -1659, -1185, -1530, -1278, 794, -1510, -854, -870, 478, -108, -308, 996, 991, 958, -1460, 1522, 1628]`

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
