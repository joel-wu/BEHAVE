# FP25519 Square

This module computes the square of one element of the prime field GF(2^255-19). The input is a 256-bit little-endian byte string representing an integer in the range [0, 2^256-1]. The module interprets that integer modulo p = 2^255 - 19, computes the field square x^2 mod p, and returns the result as a canonical 256-bit little-endian encoding whose numeric value is in [0, p-1]. The required arithmetic is prime-field modular squaring over the fixed modulus p = 0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffed.

## Interface

### RTL interface

```systemverilog
module cryptography_pk_fp25519_fp25519_square (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_x,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_z
);
```

### Behavior interface

```python
class CryptographyPkFp25519Fp25519Square:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x): ...
```

One call represents one accepted field element. Pass `in_x` as an unsigned
256-bit integer encoding, not a Python byte sequence. Every call returns:

```python
{"out_z": ...}
```

This is exactly one dictionary with the single key `out_z`, whose value is an
unsigned 256-bit integer encoding. Zero still produces a full record, never
`None` or a list. Calls retain no field state. Construction and `reset()`
establish the same stateless model; `reset()` returns `None` without a record.

## Modules

- `cryptography_pk_fp25519_fp25519_square`: top-level module that accepts one field element, reduces it modulo p, squares it modulo p, canonicalizes the result into [0, p-1], and returns the 256-bit little-endian encoding.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

Let

- p = 2^255 - 19
- numerically, p = 57896044618658097711785492504343953926634992332820282019728792003956564819949
- as a numeric port value, `256'h7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffed`
- in byte-index order, byte 0 is `0xed`, bytes 1 through 30 are `0xff`, and byte 31 is `0x7f`. This byte sequence is not a big-endian hexadecimal integer literal.

### Input interpretation

`in_x` is a 256-bit unsigned integer carrying a little-endian byte encoding of the input element. Byte 0 is bits [7:0], byte 1 is bits [15:8], ..., byte 31 is bits [255:248].

The module interprets `in_x` as the integer

- x_raw = in_x

with no signed interpretation.

### Canonical reduction of the input

Before squaring, the input is reduced modulo p:

- x = x_raw mod p

where x is the unique integer in [0, p-1] congruent to x_raw modulo p.

This reduction is part of the architectural contract even when `in_x < p`.

### Field squaring

The mathematical result is

- z = (x · x) mod p

Equivalently,

- z = (x_raw^2) mod p

with z chosen as the unique integer in [0, p-1].

No other modulus is used, and the result must be exactly the prime-field square over GF(2^255-19).

### Output encoding

`out_z` is the 256-bit unsigned integer equal to z encoded in the same little-endian convention as the input. Since z is in [0, p-1], bit 255 of `out_z` is always 0.

### Edge cases

- If `in_x = 0`, then `out_z = 0`.
- If `in_x = 1`, then `out_z = 1`.
- If `in_x = p`, then the input reduces to 0 and `out_z = 0`.
- If `in_x = p - 1`, then `out_z = 1`.
- If `in_x = 2^256 - 1`, the input is first reduced modulo p, then squared modulo p; the result is still returned canonically in [0, p-1].

## Reset Behavior

Reset is synchronous active-high via `rst`.

On reset:

- `in_ready` shall be 1.
- `out_valid` shall be 0.
- `out_z` shall be 0.

The block has no architectural state other than any transient handshake/pipeline state required by an implementation. After reset is asserted and then deasserted, the next accepted input transaction must be processed according to the Functional Behavior section with no dependence on any pre-reset input history.
