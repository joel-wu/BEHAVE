# FLAC Rice Codeword Descriptor

This block computes a lossless descriptor of one signed 32-bit Rice codeword: its right-justified nonzero suffix and its exact total length, including all leading zero bits. It performs signed folding and Rice quotient/remainder coding; it does not concatenate codewords or emit a byte stream.

## Interface

### RTL interface

```systemverilog
module compression_flac_entropy_flac_rice_encode (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_sample,
    input  logic [4:0]  in_parameter,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_codeword,
    output logic [32:0] out_nbits
);
```

### Behavior interface

```python
class CompressionFlacEntropyFlacRiceEncode:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_sample, in_parameter): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_codeword": ..., "out_nbits": ...}`. Returned fields are Python integers
holding the specified port bit patterns, not a list of encoded bits. Supply
the sample as a raw 32-bit two's-complement pattern.

## Modules

- `compression_flac_entropy_flac_rice_encode`: single-codeword descriptor generator.

## Functional Behavior

Each input accepted by `in_valid && in_ready` produces exactly one ordered output. A presented output and both payload fields remain unchanged until `out_ready`; a stalled input must likewise remain unchanged until accepted.

Let `x` be the signed interpretation of `in_sample` and `k=in_parameter`, with `0 <= k <= 31`. Compute:

- `u = 2*x` for `x >= 0`, otherwise `u = -2*x-1`;
- `q = u >> k`, `r = u & ((1 << k)-1)`;
- `out_codeword = (1 << k) | r`;
- `out_nbits = q + 1 + k`, without truncation.

The represented bitstring is `q` zeros, one stop bit, then the `k` remainder bits MSB-first. Equivalently, prepend zeros to the binary representation of `out_codeword` until its length is `out_nbits`. The upper 32 bits of `out_codeword` are zero. This is a compressed description of the bitstring, not a claim that the entire string fits in 64 physical bits.

Examples:

| Signed sample | k | out_codeword | out_nbits |
| --- | --- | --- | --- |
| 0 | 0 | 1 | 1 |
| 64 | 0 | 1 | 129 |
| 5 | 2 | 6 | 5 |
| -2 | 1 | 3 | 3 |
| -2147483648 | 0 | 1 | 4294967296 |
| -2147483648 | 31 | 4294967295 | 33 |

All sample patterns and parameter values have defined outputs. This is the raw Rice primitive: FLAC partition-header selection, escape modes, memory allocation and bitstream packing are outside this module.

## Reset Behavior

Reset is synchronous active-high. An asserted `rst` at a rising edge discards any pending output and clears `out_valid`, `out_codeword` and `out_nbits` to zero. There is no cross-request coding state. After reset, the block can accept a request.
