# Fast Q31 Cosine

Approximate cosine using 512 uniform phase intervals and linear
interpolation of signed Q31 samples. The input has 2^31 phase codes per
turn; the two halves of the 32-bit code space describe the same period.

## Interface

### RTL interface

```systemverilog
module fast_cos_q31 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_cos
);
```

All ports carry unsigned bit patterns; signed numerical interpretation is
specified below. Transfers occur on rising clock edges with valid and ready
both high, outside reset. Valid and payload must remain stable while stalled.
Every accepted input produces one ordered output. An output may be consumed
on the same edge as the next input is accepted; no fixed latency is required.

### Behavior interface

```python
class FastCosQ31:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x): ...
```

Each call represents an accepted input and returns a dictionary with keys
`out_cos` containing output bit patterns.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_cos`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `fast_cos_q31`: scalar arithmetic and ordered ready/valid transport.

## Functional Behavior

All 32-bit input patterns are defined. The signed Q31 input x denotes
angle 2*pi*x/2^31 radians, modulo a full turn; it is not an angle in radians
stored directly as Q31. The output is a signed Q31 amplitude, carried as bits.

Set `p = (in_x + 536870912) mod 2^31`, `i = floor(p/2^22)`, and
`f = (p mod 2^22)*512`. Thus i is in 0..511 and f is in 0..2^31-512.
The quarter-turn offset implements cosine using the sine table.

For the quarter-wave table Q below, define the full sine samples T[0..512]:

- T[j] = Q[j] for 0 <= j <= 128;
- T[j] = Q[256-j] for 129 <= j <= 256;
- T[j] = -Q[j-256] for 257 <= j <= 383;
- T[384] = -2147483648;
- T[j] = -Q[512-j] for 385 <= j <= 512.

The positive peak is 2147483647 and the negative peak is -2147483648.
Compute with signed, sufficiently wide products:

`a = floor(((2^31 - f)*T[i])/2^32)`

`b = floor((f*T[i+1])/2^32)`

`out_cos = (2*(a+b)) mod 2^32`.

Floor is toward negative infinity, including negative products. In particular,
the weight 2^31-f is nonnegative; at f=0 it is positive 2147483648, not the
signed 32-bit value -2147483648. The two floors occur separately, not after
summing the products. No additional saturation or nearest rounding is applied.
This defines the approximation bits, not a correctly rounded real sine/cosine.

| Index | Signed integer | Index | Signed integer |
|---:|---:|---:|---:|
| 0 | 0 | 1 | 26352928 |
| 2 | 52701887 | 3 | 79042909 |
| 4 | 105372028 | 5 | 131685278 |
| 6 | 157978697 | 7 | 184248325 |
| 8 | 210490206 | 9 | 236700388 |
| 10 | 262874923 | 11 | 289009871 |
| 12 | 315101295 | 13 | 341145265 |
| 14 | 367137861 | 15 | 393075166 |
| 16 | 418953276 | 17 | 444768294 |
| 18 | 470516330 | 19 | 496193509 |
| 20 | 521795963 | 21 | 547319836 |
| 22 | 572761285 | 23 | 598116479 |
| 24 | 623381598 | 25 | 648552838 |
| 26 | 673626408 | 27 | 698598533 |
| 28 | 723465451 | 29 | 748223418 |
| 30 | 772868706 | 31 | 797397602 |
| 32 | 821806413 | 33 | 846091463 |
| 34 | 870249095 | 35 | 894275671 |
| 36 | 918167572 | 37 | 941921200 |
| 38 | 965532978 | 39 | 988999351 |
| 40 | 1012316784 | 41 | 1035481766 |
| 42 | 1058490808 | 43 | 1081340445 |
| 44 | 1104027237 | 45 | 1126547765 |
| 46 | 1148898640 | 47 | 1171076495 |
| 48 | 1193077991 | 49 | 1214899813 |
| 50 | 1236538675 | 51 | 1257991320 |
| 52 | 1279254516 | 53 | 1300325060 |
| 54 | 1321199781 | 55 | 1341875533 |
| 56 | 1362349204 | 57 | 1382617710 |
| 58 | 1402678000 | 59 | 1422527051 |
| 60 | 1442161874 | 61 | 1461579514 |
| 62 | 1480777044 | 63 | 1499751576 |
| 64 | 1518500250 | 65 | 1537020244 |
| 66 | 1555308768 | 67 | 1573363068 |
| 68 | 1591180426 | 69 | 1608758157 |
| 70 | 1626093616 | 71 | 1643184191 |
| 72 | 1660027308 | 73 | 1676620432 |
| 74 | 1692961062 | 75 | 1709046739 |
| 76 | 1724875040 | 77 | 1740443581 |
| 78 | 1755750017 | 79 | 1770792044 |
| 80 | 1785567396 | 81 | 1800073849 |
| 82 | 1814309216 | 83 | 1828271356 |
| 84 | 1841958164 | 85 | 1855367581 |
| 86 | 1868497586 | 87 | 1881346202 |
| 88 | 1893911494 | 89 | 1906191570 |
| 90 | 1918184581 | 91 | 1929888720 |
| 92 | 1941302225 | 93 | 1952423377 |
| 94 | 1963250501 | 95 | 1973781967 |
| 96 | 1984016189 | 97 | 1993951625 |
| 98 | 2003586779 | 99 | 2012920201 |
| 100 | 2021950484 | 101 | 2030676269 |
| 102 | 2039096241 | 103 | 2047209133 |
| 104 | 2055013723 | 105 | 2062508835 |
| 106 | 2069693342 | 107 | 2076566160 |
| 108 | 2083126254 | 109 | 2089372638 |
| 110 | 2095304370 | 111 | 2100920556 |
| 112 | 2106220352 | 113 | 2111202959 |
| 114 | 2115867626 | 115 | 2120213651 |
| 116 | 2124240380 | 117 | 2127947206 |
| 118 | 2131333572 | 119 | 2134398966 |
| 120 | 2137142927 | 121 | 2139565043 |
| 122 | 2141664948 | 123 | 2143442326 |
| 124 | 2144896910 | 125 | 2146028480 |
| 126 | 2146836866 | 127 | 2147321946 |
| 128 | 2147483647 |  | |


## Reset Behavior

Reset `rst` is active high and synchronous.
It cancels pending responses and clears all output payloads to zero, with
`out_valid=0` and `in_ready=1`. No pre-reset output may reappear. There is no
persistent algorithmic state; subsequent results do not depend on history.
