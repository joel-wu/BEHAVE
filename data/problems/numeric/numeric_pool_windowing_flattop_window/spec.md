# HFT144D Window Coefficient

Generate the coefficients of a periodic 256-sample cosine-sum window, represented
as IEEE-754 binary32 bit patterns. The length is fixed; no input samples are
multiplied by the window. Implementations may use a constant table or equivalent
logic producing the specified bits.

## Interface

### RTL interface

```systemverilog
module hft144d_window_coefficient (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_index,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_coeff
);
```

Transfers occur on rising clock edges with valid and ready both high, outside
reset. Valid and payload remain stable while waiting for ready. Output order
is preserved. An input may be accepted on the same edge as the last pending
output; no particular pipeline latency is required.

### Behavior interface

```python
class Hft144dWindowCoefficient:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_index): ...
```

The call represents an accepted input and returns the corresponding output
dictionary.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_coeff`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `hft144d_window_coefficient`: fixed-window generator and ordered ready/valid transport.

## Functional Behavior

Every accepted unsigned index `in_index=0..255` emits one transaction whose `out_coeff` is the coefficient at that index.

For index `i`, set `theta = 2*pi*i/256` and evaluate

`w[i] = (1)*cos(0*theta) + (-1.96760033)*cos(1*theta) + (1.57983607)*cos(2*theta) + (-0.81123644)*cos(3*theta) + (0.22583558)*cos(4*theta) + (-0.02773848)*cos(5*theta) + (0.00090360)*cos(6*theta)`.

The decimal coefficients and pi denote real numbers. Round the final sum once
to binary32, round-to-nearest with ties to even; exact zero is positive zero.
There is no intermediate binary32 rounding. The denominator is 256, not 255:
this is a periodic window, so index zero is not repeated at index 255.
The following exact table fixes the output bits independently of a host math
library. Bits represent floating-point values, not signed integers; negative
coefficients, where present, are retained without clipping or normalization.

| Index | FP32 bits | Index | FP32 bits |
|---:|:---|---:|:---|
| 0 | `0x00000000` | 1 | `0xB579E2A7` |
| 2 | `0xB6818BC5` | 3 | `0xB71A7245` |
| 4 | `0xB7944341` | 5 | `0xB7FE172A` |
| 6 | `0xB84B18F4` | 7 | `0xB89AD333` |
| 8 | `0xB8E3F0F2` | 9 | `0xB9234187` |
| 10 | `0xB964A754` | 11 | `0xB99D1965` |
| 12 | `0xB9D4538B` | 13 | `0xBA0D65B2` |
| 14 | `0xBA39DE7E` | 15 | `0xBA717137` |
| 16 | `0xBA9B1FD7` | 17 | `0xBAC55B12` |
| 18 | `0xBAF8C861` | 19 | `0xBB1B7833` |
| 20 | `0xBB40C459` | 21 | `0xBB6D3AE8` |
| 22 | `0xBB90F33F` | 23 | `0xBBAFF555` |
| 24 | `0xBBD44222` | 25 | `0xBBFE84B5` |
| 26 | `0xBC17BA48` | 27 | `0xBC33EAA3` |
| 28 | `0xBC543AE8` | 29 | `0xBC7917DB` |
| 30 | `0xBC917974` | 31 | `0xBCA920B6` |
| 32 | `0xBCC3BDE6` | 33 | `0xBCE18E30` |
| 34 | `0xBD01678E` | 35 | `0xBD13DEEA` |
| 36 | `0xBD284B27` | 37 | `0xBD3EC93A` |
| 38 | `0xBD577487` | 39 | `0xBD72665E` |
| 40 | `0xBD87DAAE` | 41 | `0xBD97BA66` |
| 42 | `0xBDA8D9FD` | 43 | `0xBDBB3EBD` |
| 44 | `0xBDCEEB35` | 45 | `0xBDE3DED5` |
| 46 | `0xBDFA158E` | 47 | `0xBE08C3B2` |
| 48 | `0xBE151405` | 49 | `0xBE21F33E` |
| 50 | `0xBE2F564D` | 51 | `0xBE3D2F60` |
| 52 | `0xBE4B6DBA` | 53 | `0xBE59FD80` |
| 54 | `0xBE68C796` | 55 | `0xBE77B179` |
| 56 | `0xBE834E91` | 57 | `0xBE8AB476` |
| 58 | `0xBE91F7C2` | 59 | `0xBE9903EC` |
| 60 | `0xBE9FC291` | 61 | `0xBEA61B70` |
| 62 | `0xBEABF47A` | 63 | `0xBEB131D6` |
| 64 | `0xBEB5B5FD` | 65 | `0xBEB961CB` |
| 66 | `0xBEBC149F` | 67 | `0xBEBDAC80` |
| 68 | `0xBEBE0643` | 69 | `0xBEBCFDBC` |
| 70 | `0xBEBA6DF0` | 71 | `0xBEB63152` |
| 72 | `0xBEB02202` | 73 | `0xBEA81A0F` |
| 74 | `0xBE9DF3C0` | 75 | `0xBE9189E3` |
| 76 | `0xBE82B81F` | 77 | `0xBE62B685` |
| 78 | `0xBE3AA340` | 79 | `0xBE0CF6C6` |
| 80 | `0xBDB2EBA8` | 81 | `0xBCFF5213` |
| 82 | `0x3CFEE250` | 83 | `0x3DCC0F61` |
| 84 | `0x3E32BAA9` | 85 | `0x3E83080F` |
| 86 | `0x3EB00D48` | 87 | `0x3EE071C4` |
| 88 | `0x3F0A1AC2` | 89 | `0x3F25A9C5` |
| 90 | `0x3F42E0D4` | 91 | `0x3F61B826` |
| 92 | `0x3F81129A` | 93 | `0x3F920D51` |
| 94 | `0x3FA3C422` | 95 | `0x3FB62D8A` |
| 96 | `0x3FC93E9B` | 97 | `0x3FDCEB02` |
| 98 | `0x3FF1250C` | 99 | `0x4002EEDB` |
| 100 | `0x400D825B` | 101 | `0x40184445` |
| 102 | `0x40232B4B` | 103 | `0x402E2D95` |
| 104 | `0x403940D7` | 105 | `0x40445A55` |
| 106 | `0x404F6EFD` | 107 | `0x405A736B` |
| 108 | `0x40655C06` | 109 | `0x40701D08` |
| 110 | `0x407AAA99` | 111 | `0x40827C6D` |
| 112 | `0x40877E01` | 113 | `0x408C5434` |
| 114 | `0x4090F94F` | 115 | `0x409567C3` |
| 116 | `0x40999A2E` | 117 | `0x409D8B6A` |
| 118 | `0x40A13694` | 119 | `0x40A49713` |
| 120 | `0x40A7A8A5` | 121 | `0x40AA6762` |
| 122 | `0x40ACCFC7` | 123 | `0x40AEDEBA` |
| 124 | `0x40B09192` | 125 | `0x40B1E61A` |
| 126 | `0x40B2DA97` | 127 | `0x40B36DC8` |
| 128 | `0x40B39EEE` | 129 | `0x40B36DC8` |
| 130 | `0x40B2DA97` | 131 | `0x40B1E61A` |
| 132 | `0x40B09192` | 133 | `0x40AEDEBA` |
| 134 | `0x40ACCFC7` | 135 | `0x40AA6762` |
| 136 | `0x40A7A8A5` | 137 | `0x40A49713` |
| 138 | `0x40A13694` | 139 | `0x409D8B6A` |
| 140 | `0x40999A2E` | 141 | `0x409567C3` |
| 142 | `0x4090F94F` | 143 | `0x408C5434` |
| 144 | `0x40877E01` | 145 | `0x40827C6D` |
| 146 | `0x407AAA99` | 147 | `0x40701D08` |
| 148 | `0x40655C06` | 149 | `0x405A736B` |
| 150 | `0x404F6EFD` | 151 | `0x40445A55` |
| 152 | `0x403940D7` | 153 | `0x402E2D95` |
| 154 | `0x40232B4B` | 155 | `0x40184445` |
| 156 | `0x400D825B` | 157 | `0x4002EEDB` |
| 158 | `0x3FF1250C` | 159 | `0x3FDCEB02` |
| 160 | `0x3FC93E9B` | 161 | `0x3FB62D8A` |
| 162 | `0x3FA3C422` | 163 | `0x3F920D51` |
| 164 | `0x3F81129A` | 165 | `0x3F61B826` |
| 166 | `0x3F42E0D4` | 167 | `0x3F25A9C5` |
| 168 | `0x3F0A1AC2` | 169 | `0x3EE071C4` |
| 170 | `0x3EB00D48` | 171 | `0x3E83080F` |
| 172 | `0x3E32BAA9` | 173 | `0x3DCC0F61` |
| 174 | `0x3CFEE250` | 175 | `0xBCFF5213` |
| 176 | `0xBDB2EBA8` | 177 | `0xBE0CF6C6` |
| 178 | `0xBE3AA340` | 179 | `0xBE62B685` |
| 180 | `0xBE82B81F` | 181 | `0xBE9189E3` |
| 182 | `0xBE9DF3C0` | 183 | `0xBEA81A0F` |
| 184 | `0xBEB02202` | 185 | `0xBEB63152` |
| 186 | `0xBEBA6DF0` | 187 | `0xBEBCFDBC` |
| 188 | `0xBEBE0643` | 189 | `0xBEBDAC80` |
| 190 | `0xBEBC149F` | 191 | `0xBEB961CB` |
| 192 | `0xBEB5B5FD` | 193 | `0xBEB131D6` |
| 194 | `0xBEABF47A` | 195 | `0xBEA61B70` |
| 196 | `0xBE9FC291` | 197 | `0xBE9903EC` |
| 198 | `0xBE91F7C2` | 199 | `0xBE8AB476` |
| 200 | `0xBE834E91` | 201 | `0xBE77B179` |
| 202 | `0xBE68C796` | 203 | `0xBE59FD80` |
| 204 | `0xBE4B6DBA` | 205 | `0xBE3D2F60` |
| 206 | `0xBE2F564D` | 207 | `0xBE21F33E` |
| 208 | `0xBE151405` | 209 | `0xBE08C3B2` |
| 210 | `0xBDFA158E` | 211 | `0xBDE3DED5` |
| 212 | `0xBDCEEB35` | 213 | `0xBDBB3EBD` |
| 214 | `0xBDA8D9FD` | 215 | `0xBD97BA66` |
| 216 | `0xBD87DAAE` | 217 | `0xBD72665E` |
| 218 | `0xBD577487` | 219 | `0xBD3EC93A` |
| 220 | `0xBD284B27` | 221 | `0xBD13DEEA` |
| 222 | `0xBD01678E` | 223 | `0xBCE18E30` |
| 224 | `0xBCC3BDE6` | 225 | `0xBCA920B6` |
| 226 | `0xBC917974` | 227 | `0xBC7917DB` |
| 228 | `0xBC543AE8` | 229 | `0xBC33EAA3` |
| 230 | `0xBC17BA48` | 231 | `0xBBFE84B5` |
| 232 | `0xBBD44222` | 233 | `0xBBAFF555` |
| 234 | `0xBB90F33F` | 235 | `0xBB6D3AE8` |
| 236 | `0xBB40C459` | 237 | `0xBB1B7833` |
| 238 | `0xBAF8C861` | 239 | `0xBAC55B12` |
| 240 | `0xBA9B1FD7` | 241 | `0xBA717137` |
| 242 | `0xBA39DE7E` | 243 | `0xBA0D65B2` |
| 244 | `0xB9D4538B` | 245 | `0xB99D1965` |
| 246 | `0xB964A754` | 247 | `0xB9234187` |
| 248 | `0xB8E3F0F2` | 249 | `0xB89AD333` |
| 250 | `0xB84B18F4` | 251 | `0xB7FE172A` |
| 252 | `0xB7944341` | 253 | `0xB71A7245` |
| 254 | `0xB6818BC5` | 255 | `0xB579E2A7` |

All input bit patterns are defined. Coefficients do not depend on transaction
history. Reset affects pending transport, not the constant window.

## Reset Behavior

Reset is active high and synchronous or asynchronous. It cancels pending outputs; after reset,
`in_ready=1`, `out_valid=0`, and `out_coeff=0`. The next accepted input starts a
fresh response. No pre-reset output may reappear.
