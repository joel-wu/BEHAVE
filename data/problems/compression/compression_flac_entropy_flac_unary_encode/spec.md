# FLAC Unary Bitstream Encoder

This block appends unary codes to an MSB-first byte stream. Each unsigned input `n` contributes `n` zero bits followed by one bit. Codes are packed together across input transactions; an optional flush pads the final byte with zeros. The maximum zero-run length per input is 255.

## Interface

### RTL interface

```systemverilog
module compression_flac_entropy_flac_unary_encode (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_val,
    input  logic       in_flush,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_byte
);
```

### Behavior interface

```python
class CompressionFlacEntropyFlacUnaryEncode:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_val, in_flush): ...
```

Call `process` once per accepted input. It returns an ordered list of output
records, each shaped as `{"out_byte": ...}`. Each value is a Python integer
holding an 8-bit byte. Return `[]` when no byte is emitted, and a one-element
list when exactly one byte is emitted. The flush rule below determines whether
a partial byte is emitted.

## Modules

- `compression_flac_entropy_flac_unary_encode`: unary coding, cross-transaction byte packing, and optional byte-boundary padding.

## Functional Behavior

`in_val` is an unsigned 8-bit count; all values 0 through 255 are legal. `in_flush` is a one-bit flag. One input is accepted when `in_valid && in_ready` on a rising clock edge outside reset. Output bytes are transferred in order when `out_valid && out_ready`. Input backpressure is permitted; an offered output byte and `out_valid` must remain unchanged while `out_ready` is low. No fixed processing latency is required.

The architectural state is a pending bit string of length 0 through 7. Represent it by `pending_count` and `pending_value`, where the latter holds the bits right-justified and is less than `2**pending_count`.

For each accepted input, in order:

1. Append `in_val` zeros and then a one to the pending bit string. Bits are ordered from earliest to latest.
2. If `in_flush == 1`, append 0 through 7 zero bits to make the length a multiple of eight. An already aligned string receives no padding.
3. Emit all complete bytes, earliest first. The earliest bit of each byte is bit 7 of `out_byte`.
4. Retain any final 0 through 7 bits as the new pending string.

One input can produce 0 through 33 bytes, depending on the previous pending bits and the flush flag. Complete bytes are emitted even when `in_flush == 0`. Flush applies after the current code; it leaves no pending bits but does not add an extra byte when already aligned. A later input starts or continues the next packed sequence. There is no arithmetic truncation of a code or its length.

Examples, starting with no pending bits:

- `(in_val=0, in_flush=1)` emits `80` in hexadecimal: one bit followed by seven padding zeros.
- `(3, 0)` emits nothing and retains `0001`; a following `(2, 1)` emits `12`, representing `0001 001 0`.
- Eight consecutive `(0, 0)` inputs emit one `FF` byte on the eighth input.
- `(8, 1)` emits `00 80`.
- `(255, 1)` emits 31 zero bytes followed by `01`, with no additional padding byte.

## Reset Behavior

Reset is synchronous active-high. A rising edge with `rst == 1` takes priority over both handshakes: discard any input being encoded, any pending bits, and any unconsumed output byte. Set `pending_count = 0`, `pending_value = 0`, `out_valid = 0`, and `out_byte = 0`. The block is then idle with `in_ready = 1`.
