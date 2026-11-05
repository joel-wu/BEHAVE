# FP25519Mul

FP25519Mul computes one prime-field multiplication over GF(2^255-19) per accepted input transaction. The two inputs are 32-byte little-endian encodings of field elements. The result is the canonical little-endian encoding of `(x * y) mod p`, where `p = 2^255 - 19 = 0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffed`. Inputs are first interpreted as unsigned 256-bit integers and reduced modulo `p`; the multiplication is then performed in the field and the output is reduced to the unique representative in `[0, p-1]`.

## Interface

### RTL interface

```systemverilog
module FP25519Mul (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_x,
    input  logic [255:0] in_y,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_z
);
```

`in_x`, `in_y`, and `out_z` are 256-bit unsigned buses carrying 32-byte little-endian field-element encodings: bits `[7:0]` are byte 0, bits `[15:8]` are byte 1, ..., bits `[255:248]` are byte 31.

### Behavior interface

```python
class FP25519Mul:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_y): ...
```

`process()` returns one dict per accepted input transaction:

```python
{"out_z": ...}
```

Both arguments and `out_z` are unsigned 256-bit integers carrying the port
words, not byte arrays. Even a zero result returns the complete one-key
dictionary, never `None`. Calls represent accepted requests, not clock or
backpressure steps. There is no cross-request arithmetic state; `reset()`
returns `None` and emits no response.

## Modules

- `FP25519Mul`: top-level single-transaction field multiplier.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

Let

- `p = 2^255 - 19`
- `X_raw` be the unsigned integer value of `in_x`
- `Y_raw` be the unsigned integer value of `in_y`

The module performs exactly one output transaction for each accepted input transaction.

### Input interpretation

`in_x` and `in_y` are interpreted as unsigned 256-bit integers. No input range restriction is imposed beyond the 256-bit port width.

### Canonical reduction of each input

Before multiplication, each input is reduced modulo `p`:

- `X = X_raw mod p`
- `Y = Y_raw mod p`

The reduced values satisfy `0 <= X <= p-1` and `0 <= Y <= p-1`.

### Field multiplication

The output value is

- `Z = (X * Y) mod p`

Equivalently,

- `Z = ((X_raw mod p) * (Y_raw mod p)) mod p`

The result is the unique canonical representative in `[0, p-1]`.

### Output encoding

`out_z` is the 256-bit unsigned little-endian encoding of `Z`. Since `Z < p < 2^255`, bit 255 of `out_z` is always `0`.

### Worked examples

- If `in_x = 0` and `in_y` is any 256-bit value, `out_z = 0`.
- If `in_x = 1` and `in_y` encodes any value `v`, `out_z = v mod p`.
- If `in_x = p` and `in_y = 1`, then `in_x mod p = 0`, so `out_z = 0`.
- If `in_x = p + 1` and `in_y = 1`, then `in_x mod p = 1`, so `out_z = 1`.
- If `in_x = p - 1` and `in_y = p - 1`, then `out_z = 1`.

## Reset Behavior

The module uses asynchronous, active-high reset `rst`. Assertion immediately clears pending transactions and sets `out_valid = 0` and `out_z = 0`, without waiting for a rising clock edge. No input is accepted while reset is active. After reset is released, the block is empty and ready (`in_ready = 1`); the next accepted input is independent of all pre-reset inputs. The output payload remains zero until that input is processed.
