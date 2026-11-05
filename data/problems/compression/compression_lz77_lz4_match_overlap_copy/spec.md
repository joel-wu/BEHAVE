# LZ4 Overlapping Match Copy

This block reconstructs one LZ4-style match from a supplied 32-byte history. Forward copying allows newly produced bytes to become the source of later bytes in the same match. Unavailable history is reported explicitly rather than replaced by invented data.

## Interface

### RTL interface

```systemverilog
module compression_lz77_lz4_match_overlap_copy (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_offset,
    input  logic [5:0]   in_match_length,
    input  logic [255:0] in_history,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_byte,
    output logic         out_error
);
```

`in_offset` and `in_match_length` are unsigned. History byte `i` occupies bits `[8*i+7:8*i]`: byte 0 is oldest, and byte 31 immediately precedes the match.

### Behavior interface

```python
class CompressionLz77Lz4MatchOverlapCopy:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_offset, in_match_length, in_history): ...
```

Call `process` once per accepted input. It returns an ordered list of records
shaped as `{"out_byte": ..., "out_error": ...}`, with Python integer values.
Return `[]` for zero match length, one error record in a list for a nonzero
length with an invalid offset, and one record per copied byte otherwise.
A single copied byte also uses a one-element list. The rules below define
the byte values and error flag.

## Modules

- `compression_lz77_lz4_match_overlap_copy`: overlap-aware bounded-history copy and byte-stream output.

## Functional Behavior

Requests are accepted on rising edges with `in_valid && in_ready`, outside reset. Output transfers occur with `out_valid && out_ready`. All responses to a request precede those of the next request. While stalled, an offered output and its payload remain unchanged; input backpressure is permitted. No fixed latency is required.

Apply these rules in order:

1. If `in_match_length == 0`, emit nothing. The offset is unused.
2. Otherwise, if `in_offset == 0` or `in_offset > 32`, emit exactly one error transaction `{out_byte: 0, out_error: 1}`. No copy is performed.
3. Otherwise emit exactly `in_match_length` data transactions, each with `out_error=0`.

For a valid match, let `d=in_offset`. At output position `i`, read history byte `32+i-d` when `i<d`; otherwise read byte `i-d` already produced by this match. Copying therefore proceeds forward, not from a frozen copy of the source region.

Examples:

- Distance 1 and length 5, with newest history byte `41`, emit `41 41 41 41 41`.
- Distance 2 and length 6, with final history bytes `41 42`, emit `41 42 41 42 41 42`.
- Distance 32 can read the entire supplied history and then overlap it.
- Distance 33 with nonzero length emits one error, not a fabricated zero-filled match.

All lengths 0–63 are supported. The history is supplied anew for every request; no earlier request supplies hidden history or an external dictionary. This block does not parse complete LZ4 blocks.

## Reset Behavior

Reset is synchronous active-high and overrides handshakes. Discard remaining bytes or an error response from the active request and clear the working history. Set `out_valid=0, out_byte=0, out_error=0`; `in_ready=1`.
