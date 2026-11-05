# JPEG Huffman Bit-Emission Step

Append codewords to a 64-bit JPEG entropy bit buffer and emit a byte-stuffed burst whenever an insertion crosses its boundary. This is the bit-emission suboperator, not Huffman-table construction or a complete JPEG scan encoder.

## Interface

### RTL interface

```systemverilog
module compression_jpeg_entropy_jpeg_huffman_emit_step (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [63:0] in_code, input logic [6:0] in_size,
    output logic out_valid, input logic out_ready,
    output logic [7:0] out_byte
);
```

### Behavior interface

```python
class CompressionJpegEntropyJpegHuffmanEmitStep:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_code, in_size): ...
```

Each `process` call appends one accepted codeword. Pass `in_code` and `in_size`
as unsigned 64-bit and 7-bit integers. The result is always an ordered list
of dictionaries, each with exactly `{"out_byte": value}`, where `value` is
an integer in 0..255. Return `[]` when no bytes are emitted, including a
zero-length insertion; never return `None` or a bare dictionary. A flush
returns all 8..16 bytes, including stuffing bytes, in transmission order.
The bit buffer and free-bit count persist between calls. Construction and
`reset()` restore the empty buffer and `f=64`; `reset()` returns `None`.
Physical output-drain or stall cycles require no additional calls.

### Transaction protocol

Transfers occur on rising edges with valid and ready both high. While an output is stalled, valid and byte remain unchanged; input codes are appended only when accepted.

## Modules

- `compression_jpeg_entropy_jpeg_huffman_emit_step`: bit buffer and stuffed-byte stream.

## Functional Behavior

The state consists of a 64-bit buffer `B` and free-bit count `f` in 0..64. Only its low `64-f` bits are significant. Let `n=min(in_size,16)`. A zero length is a no-op; otherwise let `c=in_code mod 2^n`.

If `n<=f`, set `B=((B<<n)|c) mod 2^64` and `f=f-n`, emitting nothing. **An exactly full buffer waits for the next nonempty insertion.**

If `n>f`, let `r=n-f`. Complete the word
`W=((B<<f)|(c>>r)) mod 2^64`. Emit its eight bytes most-significant first. After each byte equal to `0xFF`, immediately emit `0x00`. Thus a flush emits 8..16 ordered transactions. Retain the low `r` bits of `c` and set `f=64-r`; bits above those retained bits are irrelevant.

Lengths 17..127 are explicitly clamped to 16. No end-of-scan padding, partial flush, restart markers or predictor reset is part of this operator.

## Reset Behavior

Reset is synchronous active-high and overrides transfers. A reset edge clears the bit buffer, sets `f=64`, discards queued bytes, clears output valid and output byte, and makes input ready.
