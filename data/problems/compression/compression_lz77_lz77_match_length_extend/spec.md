# LZ77 Match Length Extension

This block extends a known matching prefix of two 64-byte windows. It returns the first mismatch position at or after the supplied start, or the requested limit if every examined byte matches. The complete window length 64 is representable.

## Interface

### RTL interface

```systemverilog
module compression_lz77_lz77_match_length_extend (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [511:0] in_str_bytes,
    input  logic [511:0] in_match_bytes,
    input  logic [6:0]   in_start_len,
    input  logic [6:0]   in_max_len,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [6:0]   out_len,
    output logic         out_error
);
```

Byte `i` of each window occupies bits `[8*i+7:8*i]`, for `0 <= i < 64`. Lengths are unsigned 7-bit integers.

### Behavior interface

```python
class CompressionLz77Lz77MatchLengthExtend:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_str_bytes, in_match_bytes, in_start_len, in_max_len): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_len": ..., "out_error": ...}`, including on an error. Returned fields
are Python integers holding the specified port bit patterns; `out_error` is
`0` or `1`.

## Modules

- `compression_lz77_lz77_match_length_extend`: bounded match-extension primitive.

## Functional Behavior

Each rising-edge input handshake (`in_valid && in_ready`, outside reset) produces one ordered output transaction. A pending output remains valid with unchanged payload until `out_ready` is high. Input backpressure is permitted; no fixed processing latency is required.

For `0 <= in_start_len <= in_max_len <= 64`, set `out_error = 0`. Return the smallest index `i` in `[in_start_len, in_max_len)` whose two bytes differ; if there is no such index, return `in_max_len`.

Bytes below `in_start_len` do not affect the result: the caller has already established that prefix. In particular:

- `start_len == max_len` returns that length without examining bytes.
- A mismatch at `start_len` returns `start_len`.
- Equal windows with `start_len=0, max_len=64` return **64**, not zero.
- A mismatch only at byte 63 with `max_len=64` returns 63.

For every other length pair, return `out_error=1, out_len=0`; do not access outside either window. There is no persistent algorithmic state between requests.

## Reset Behavior

Reset is synchronous active-high and overrides both handshakes. Discard any pending result and set `out_valid=0, out_len=0, out_error=0`; the block is ready for a new request. Pre-reset requests cannot produce later responses.
