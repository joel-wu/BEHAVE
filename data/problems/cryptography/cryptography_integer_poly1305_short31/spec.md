# Poly1305Short31

Complete Poly1305 one-time authentication tag for a 0–31 byte message.

## Interface

### RTL interface

```systemverilog
module Poly1305Short31 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_key,
    input logic [247:0] in_message,
    input logic [4:0] in_length,
    output logic [127:0] out_values
);
endmodule
```

### Behavior interface

```python
class Poly1305Short31:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_key, in_message, in_length): ...
```

Each `process` call represents one accepted request. Pass `in_key`,
`in_message`, and `in_length` as unsigned 256-bit, 248-bit, and 5-bit integers.
It always returns exactly `{"out_values": tag}`, where `tag` is an unsigned
128-bit integer with byte zero in bits 7:0. An empty message still returns
the dictionary containing the high key half, not `None`, bytes, or a list.
No accumulator persists between requests. Construction and `reset()` have
no numerical effect; `reset()` returns `None`. Physical idle, latency and
stall cycles require no calls.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`Poly1305Short31` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Complete Poly1305 one-time authentication tag for a 0–31 byte message.

Byte i of each key/message/tag string occupies bits 8*i +: 8. Use only the first L=in_length message bytes; later bytes have no effect. Let r be the low 128 key bits AND 0x0ffffffc0ffffffc0ffffffc0fffffff, and s the high 128 key bits. Begin h=0. Split the L bytes into consecutive nonempty chunks of at most 16 bytes. For each chunk of n bytes, interpret those bytes little-endian as m, append the single bit 2^(8*n), and set h=((h+m+2^(8*n))*r) mod (2^130-5). All arithmetic before reduction is exact. Return (h+s) mod 2^128.

For L=0 there are no chunks and the tag is s. A 16-byte chunk includes bit 128; a partial chunk includes the bit immediately after its last byte, not bit 128. Apply clamping even to arbitrary unmasked key patterns, and add s only once at the end. This is Monocypher crypto_poly1305 restricted to this message capacity, not a lazy accumulator or tag preview. One-time key management and constant-time implementation are outside this functional benchmark; key reuse is not endorsed.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

For an empty message, the tag equals key bytes 16..31. With r=2, s=0 and a 16-byte all-ff message, the tag is the packed integer 3 (bytes `03,00,...,00`).

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
