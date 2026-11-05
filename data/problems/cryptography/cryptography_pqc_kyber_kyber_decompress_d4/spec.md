# Kyber Polynomial Decompress d4

This block deserializes one 4-bit-compressed Kyber polynomial coefficient pair per transaction. Each accepted input byte contains two unsigned 4-bit codewords. The block expands each codeword to an approximate coefficient in the Kyber modulus domain using the exact integer formula `(((t * 3329) + 8) >> 4)`, where `t` is the 4-bit codeword value in `[0, 15]`. The algorithm is the d=4 branch of Kyber polynomial decompression for `KYBER_POLYCOMPRESSEDBYTES = 128`, with `KYBER_N = 256` and `KYBER_Q = 3329`.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_kyber_kyber_decompress_d4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_byte,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [11:0] out_coeff0,
    output logic [11:0] out_coeff1
);
```

### Behavior interface

```python
class KyberDecompressD4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_byte):
        pass
```

`process()` returns one output transaction as:

```python
{"out_coeff0": ..., "out_coeff1": ...}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_coeff0`, `out_coeff1`: 12.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cryptography_pqc_kyber_kyber_decompress_d4`: top-level module that accepts one compressed byte and emits the two decompressed coefficients encoded by that byte.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

The block is stateless and implements a 1:1 transaction mapping.

For each accepted input transaction:

1. Let `a` be `in_byte[7:0]`.

2. Extract the two 4-bit compressed values:
   - `t0 = a[3:0] = a & 4'hF`
   - `t1 = a[7:4] = (a >> 4) & 4'hF`

3. Decompress each 4-bit value to a 12-bit unsigned coefficient using Kyber modulus `Q = 3329`:
   - `coeff0 = (((t0 * 3329) + 8) >> 4)`
   - `coeff1 = (((t1 * 3329) + 8) >> 4)`

4. `out_coeff0` shall equal `coeff0[11:0]`.
   `out_coeff1` shall equal `coeff1[11:0]`.

Arithmetic is pure unsigned integer arithmetic. The right shift in step 3 is a logical right shift by exactly 4 bits. No modular reduction is applied after decompression. Because `t0` and `t1` are each in `[0, 15]`, the decompressed coefficients are in `[0, 3121]` and always fit in 12 bits.

Equivalent explicit mappings for the two output coefficients are:
- `0  -> 0`
- `1  -> 208`
- `2  -> 416`
- `3  -> 624`
- `4  -> 832`
- `5  -> 1040`
- `6  -> 1248`
- `7  -> 1456`
- `8  -> 1665`
- `9  -> 1873`
- `10 -> 2081`
- `11 -> 2289`
- `12 -> 2497`
- `13 -> 2705`
- `14 -> 2913`
- `15 -> 3121`

A stream of 128 accepted input bytes reconstructs one 256-coefficient polynomial in byte order. If the accepted input bytes are numbered `a[0]` through `a[127]`, then transaction `i` produces:
- polynomial coefficient `2*i + 0` on `out_coeff0`
- polynomial coefficient `2*i + 1` on `out_coeff1`

No framing, packet markers, or polynomial-length counter are part of this block's interface.

## Reset Behavior

Reset is active-high. Reset may be implemented synchronously or asynchronously.

The block has no architectural state.

During reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_coeff0 = 12'h000`
- `out_coeff1 = 12'h000`

After reset is deasserted, the block remains stateless. `in_ready` may remain constantly high. `out_valid` shall only assert to present the output transaction corresponding to an accepted input byte.
