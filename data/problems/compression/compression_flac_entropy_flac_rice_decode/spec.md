# Framed FLAC Rice Stream Decoder

This block decodes raw Rice-coded signed 32-bit residuals from an MSB-first byte stream. It retains a partial codeword across bytes and emits every completed residual, including multiple residuals from the final byte. Each frame uses one Rice parameter and supplies its exact final valid-bit count.

## Interface

### RTL interface

```systemverilog
module compression_flac_entropy_flac_rice_decode (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_byte,
    input  logic [3:0]  in_nbits,
    input  logic        in_last,
    input  logic [4:0]  in_parameter,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_value,
    output logic        out_success
);
```

### Behavior interface

```python
class FlacRiceDecode:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_byte, in_nbits, in_last, in_parameter): ...
```

Each call returns an ordered list of dictionaries with `out_value` and `out_success`. The list is empty when the accepted byte completes no symbol. A call may return multiple symbols and, after them, one error.

Call `process` once per accepted byte, passing nonnegative integers encoding
the four input ports. The return container is always a list, including for one
result: `[{"out_value": value, "out_success": 1}]`. Each record has exactly
these two keys. `value` is an integer in 0..2^32-1 encoding the signed residual;
`out_success` is integer 0 or 1. A successful zero residual is a record with
value zero and success one, not an empty list. Errors use
`{"out_value": 0, "out_success": 0}`. Return `[]`, not `None`, when there are
no records. `reset()` clears the partial codeword, frame and error state.

## Modules

- `compression_flac_entropy_flac_rice_decode`: framed byte reader, Rice parser and signed unfolding.

## Functional Behavior

Inputs transfer on `in_valid && in_ready`; outputs transfer on `out_valid && out_ready`. A stalled input or output retains its valid signal and payload until accepted. Completed symbols may continue to emerge without further input bytes. Backpressure must not drop bits or symbols; there is no required cycle latency.

### Frame and bit boundaries

- The first byte after reset or a previous `in_last=1` starts a new frame. Latch its `in_parameter` as `k` (0–31) for the whole frame. Later parameter fields are ignored.
- Every non-final byte has `in_nbits=8`. The final byte has `in_last=1` and `0 <= in_nbits <= 8`; only its most significant `in_nbits` bits are data.
- A zero-bit final byte closes a frame without adding data; an empty frame is legal.
- Padding bits are ignored, not decoded. After finishing or rejecting a frame, the next frame starts with a fresh parameter and empty parser state.

### Rice parsing

Concatenate the valid bits of accepted bytes in order. For each symbol, consume `q` zero bits followed by a stop bit `1`, then consume `k` remainder bits as unsigned `r`, MSB-first. For `k=0`, the remainder is zero and requires no bits.

Compute `u=(q << k)|r`. A valid value fits in unsigned 32 bits. Emit `out_success=1` and the 32-bit two's-complement pattern of:

- `u/2` when `u` is even;
- `-(u+1)/2` when `u` is odd.

Resume at the next bit immediately after the codeword. Input-byte boundaries do not alter the code. A partial unary prefix or remainder remains pending until more bytes arrive.

### Errors and recovery

Report exactly one `{out_value: 0, out_success: 0}` for the first error in a frame:

- an invalid bit count (over eight, or not eight on a non-final byte);
- a unary quotient exceeding `0xFFFFFFFF >> k`, detected as soon as the excess zero arrives;
- an incomplete unary prefix or remainder at the end of the final valid bits.

Successfully decoded symbols before the error remain outputs, in order. Discard the rest of the faulty frame, including later bytes through `in_last`, without further outputs. An invalid-count byte is rejected before any of its bits are decoded. A final boundary with no pending code is not an error. Reset is not required between frames, including after errors.

For example, final byte `0xFF` with eight valid bits and `k=0` produces **eight zero residuals**. The five valid bits `00110` at `k=2` produce the single value 5. Adding one more valid zero after those five bits produces value 5 followed by a truncation error. The same zero as padding produces no error.

This module handles raw Rice codes, not FLAC headers, partition selection, escape-coded residuals or checksums. End-of-frame bit counts replace the software reader's explicit requested sample count.

## Reset Behavior

Reset is synchronous active-high. At a rising edge with `rst=1`, discard all pending input bits and outputs, clear the parser and frame/error state, and set `out_valid`, `out_value` and `out_success` to zero. After reset, the next request starts a new frame.
