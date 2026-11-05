# GF256 Multiplier

This module computes one 8-bit product in the finite field GF(2^8) used by AES-style Rijndael arithmetic. Each accepted input transaction supplies two field elements `in_a` and `in_b`, each encoded as an unsigned 8-bit polynomial over GF(2). The module returns their product reduced modulo the irreducible polynomial \(x^8 + x^4 + x^3 + x + 1\), whose binary low-order coefficient encoding is `0x11B`. One equivalent algorithm is 8-step shift-and-add multiplication over GF(2): conditionally XOR the running multiplicand into the accumulator for each set bit of the multiplier, then advance the multiplicand with `xtime`, where reduction on overflow XORs with `0x1B`.

## Interface

### RTL interface

```systemverilog
module cryptography_ecc_gf_gf256_mul (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_a,
    input  logic [7:0] in_b,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_p
);
```

### Behavior interface

```python
class GF256Multiplier:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a, in_b):
        pass
```

Call `process` once per accepted input. It returns one dictionary:

```python
{"out_p": ...}
```

`out_p` is a Python integer in 0..255 carrying the output bit pattern.
A zero product is still one output record, not `None` or an empty sequence.

## Modules

- `cryptography_ecc_gf_gf256_mul`: top-level GF(2^8) multiplier.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

`in_a` and `in_b` are independent unsigned 8-bit inputs. Bit `i` of each input is the coefficient of \(x^i\) in a polynomial over GF(2).

The output `out_p` is the unique 8-bit field element equal to:

- the polynomial product of `in_a` and `in_b`, over GF(2), then
- reduced modulo \(x^8 + x^4 + x^3 + x + 1\).

All additions in the field are XOR operations. There are no carries.

The following 8-iteration algorithm defines the same mapping; an implementation may use any equivalent finite-field multiplier:

1. Initialize:
   - `acc = 0x00`
   - `a = in_a`
   - `b = in_b`

2. For iteration `i = 0` through `7`, in increasing order:
   - If bit 0 of `b` is `1`, update `acc = acc ^ a`.
   - Update `a` with one Rijndael `xtime` step:
     - Let `msb = a[7]`.
     - Compute `a_shift = (a << 1) & 0xFF`.
     - If `msb == 1`, set `a = a_shift ^ 0x1B`.
     - Else set `a = a_shift`.
   - Update `b = b >> 1`, logical right shift, zero-filled.

3. After the eighth iteration, `out_p = acc`.

Equivalent recurrence for `xtime`:
- `xtime(x) = ((x << 1) & 0xFF) ^ 0x1B` when `x[7] = 1`
- `xtime(x) = ((x << 1) & 0xFF)` when `x[7] = 0`

The reduction constant `0x1B` is the low 8 bits of the AES irreducible polynomial `0x11B` after eliminating the \(x^8\) term during one-bit left-shift reduction.

Because the loop count is fixed at 8, the mapping is defined for all 256×256 input pairs.

Required edge-case results:
- If `in_a == 0x00` or `in_b == 0x00`, `out_p = 0x00`.
- If `in_a == 0x01`, `out_p = in_b`.
- If `in_b == 0x01`, `out_p = in_a`.
- `in_a = 0x57`, `in_b = 0x83` shall produce `out_p = 0xC1`.

The module is a 1:1 transaction mapping: each accepted input transaction produces exactly one output transaction carrying `out_p`.

## Reset Behavior

The module uses asynchronous, active-high reset `rst`. Assertion immediately
cancels any outstanding transaction and sets the values below, without waiting
for a rising clock edge.

On reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_p = 0x00`

There is no architectural state that must be preserved across transactions. After reset deasserts, the next accepted input transaction is processed independently of all prior transactions.
