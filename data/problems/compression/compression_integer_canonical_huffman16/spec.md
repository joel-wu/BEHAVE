# CanonicalHuffman16

Construct canonical prefix codes for sixteen symbols, emitting DEFLATE-style least-significant-bit-first codes.

## Interface

### RTL interface

```systemverilog
module CanonicalHuffman16 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_lengths,
    output logic [128:0] out_values
);
endmodule
```

### Behavior interface

```python
class CanonicalHuffman16:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_lengths): ...
```

Each call represents one accepted request and returns exactly
`{"out_values": packed_result}`. The argument is an unsigned 64-bit integer;
the result is an unsigned 129-bit integer with the code bytes and invalid bit
defined below. Empty trees and rejected requests also return this dictionary,
never `None`, a scalar or a list. No handshake signals are returned. The model
has no state between requests; construction and `reset()` establish this empty
state, and `reset()` returns `None`.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`CanonicalHuffman16` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Ports carry unsigned bit patterns. Field positions and any required zero
padding are defined below. Indices are zero-based. Every input bit pattern
has a defined result.

Construct canonical prefix codes for sixteen symbols, emitting DEFLATE-style least-significant-bit-first codes.

Input symbol i has a four-bit code length L[i]. Zero disables a symbol; valid nonzero lengths are 1..8. Reject if any length exceeds 8 or if sum_i(2^-L[i]) over enabled symbols exceeds one (oversubscription). Incomplete prefix trees and the empty tree are allowed; this is a generic code generator, not a complete DEFLATE block validator. Sort enabled symbols by (length,symbol index). In this order assign the shortest first code zero, increment after each symbol and left-shift when moving to a longer code length. Reverse exactly L[i] bits of each assigned canonical code; return it in byte lane i with upper unused bits zero. Disabled symbols have code byte zero. Output bits 127:0 hold all code bytes; bit 128 is invalid. On any rejection return all code bytes zero and invalid=1. Otherwise invalid=0. This retains zlib gen_codes/bi_reverse and the oversubscription check, with a fixed small alphabet and explicit acceptance of incomplete/empty trees. It does not build a Huffman tree from frequencies.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Lengths `[1,2,3,3,0,...,0]` give canonical codes 0,10,110,111.
Their reversed code bytes are `[0,1,3,7,0,...,0]`;
`in_lengths=0x3321` and `out_values=0x07030100`.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
