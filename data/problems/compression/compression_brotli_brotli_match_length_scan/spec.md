# Brotli Match Length Scan

This block computes the length of the common prefix of two byte strings, limited by a caller-supplied maximum length. Each accepted input transaction provides two candidate byte sequences packed into fixed-width words plus a scan limit. The block returns the number of initial byte positions, starting at index 0, for which the two sequences are equal, stopping at the first mismatch or when the limit is reached. The required algorithm is a bounded prefix scan equivalent to comparing 32-bit chunks while possible and then comparing remaining bytes one by one; the architectural result is the maximal matched byte count in the range `[0, limit]`.

## Interface

### RTL interface

```systemverilog
module compression_brotli_brotli_match_length_scan (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_s1,
    input  logic [255:0] in_s2,
    input  logic [5:0]   in_limit,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [5:0]   out_matched
);
```

### Behavior interface

```python
class CompressionBrotliBrotliMatchLengthScan:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_s1, in_s2, in_limit): ...
```

Call `process` once per accepted request, passing the packed input words and
limit as nonnegative integers. It returns exactly
`{"out_matched": matched}`, where `matched` is an integer from 0 to 32.
A zero-length match returns this dictionary with zero, not `None` or an empty
sequence. The model has no persistent algorithmic state; `reset()` is a no-op.

## Modules

- `compression_brotli_brotli_match_length_scan`: top-level module that scans two 32-byte inputs and returns the common-prefix length, bounded by `in_limit`.

## Functional Behavior

`in_s1` and `in_s2` are each 256-bit unsigned vectors representing 32 bytes. Byte `i` is bits `[8*i+7 : 8*i]`; byte 0 is the least-significant byte, byte 31 is the most-significant byte.

`in_limit` is a 6-bit unsigned integer in the range `[0, 63]`. The effective scan bound is:

- `limit_eff = min(in_limit, 32)`

The output `out_matched` is a 6-bit unsigned integer equal to the largest integer `m` such that:

- `0 <= m <= limit_eff`, and
- for every byte index `i` with `0 <= i < m`, byte `i` of `in_s1` equals byte `i` of `in_s2`, and
- either `m == limit_eff` or byte `m` of `in_s1` differs from byte `m` of `in_s2`.

Equivalently, `out_matched` is the number of consecutive matching bytes from the start of the two inputs, truncated to `limit_eff`.

One equivalent comparison algorithm is:

1. Initialize `matched = 0`.
2. While at least 4 unchecked bytes remain inside `limit_eff`, compare the next 32-bit little-endian word from each input:
   - word at byte offset `matched` is formed from bytes `matched`, `matched+1`, `matched+2`, `matched+3`, with byte `matched` as bits `[7:0]`.
   - If the two 32-bit words are equal, increment `matched` by 4 and continue.
   - If the two 32-bit words differ, stop the 32-bit phase.
3. Compare remaining bytes one by one starting at byte offset `matched`:
   - while `matched < limit_eff` and byte `matched` matches, increment `matched` by 1.
4. Return `matched`.

This algorithm and the mathematical definition above are equivalent for all inputs; the block shall implement that behavior exactly.

Edge cases:
- If `in_limit == 0`, `out_matched = 0`.
- If `in_limit > 32`, bytes beyond index 31 are not examined; `out_matched` cannot exceed 32.
- If the first bytes differ, `out_matched = 0`.
- If all bytes from index 0 through `limit_eff - 1` match, `out_matched = limit_eff`.

Worked example:
- If `in_limit = 6` and bytes 0..3 match, byte 4 differs, then `out_matched = 4`.
- If `in_limit = 6` and bytes 0..5 all match, then `out_matched = 6`.
- If `in_limit = 40` and all 32 bytes match, then `out_matched = 32`.

The block is stateless at the transaction level: each accepted input transaction is processed independently and produces exactly one output transaction.

## Reset Behavior

`rst` is active-high. Reset may be implemented synchronously or asynchronously, but while reset is asserted the architectural state shall be the reset state.

Architectural state after reset:
- no stored input data
- no stored partial match count
- no pending output transaction

Output/reset requirements:
- `out_valid = 0`
- `out_matched = 0`

Because the block is stateless between transactions, reset does not preserve any history from prior inputs.
