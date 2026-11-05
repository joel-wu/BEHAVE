# QOIRGB8Payload

QOI pixel-opcode payload encoding of eight RGB pixels in original order.

## Interface

### RTL interface

```systemverilog
module QOIRGB8Payload (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [191:0] in_pixels,
    output logic [261:0] out_values
);
endmodule
```

### Behavior interface

```python
class QOIRGB8Payload:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_pixels): ...
```

Each call represents one complete eight-pixel request and returns exactly
`{"out_values": packed_result}`. `in_pixels` is an unsigned 192-bit integer;
the result is an unsigned 262-bit integer containing the payload bytes and
byte count defined below. Return this dictionary, not a byte array, scalar,
list or `None`. No handshake signals are returned. Cache, previous-pixel and
run state are local to each request. Construction and `reset()` establish
this empty inter-request state, and `reset()` returns `None`.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`QOIRGB8Payload` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Ports carry unsigned bit patterns. Field positions and any required zero
padding are defined below. Indices are zero-based. Every input bit pattern
has a defined result.

QOI pixel-opcode payload encoding of eight RGB pixels in original order.

Each pixel is three unsigned bytes R,G,B at bits 24*i,24*i+8,24*i+16. Alpha is always 255. Treat each request as a fresh 8x1 RGB image: previous pixel=(0,0,0,255), 64 RGBA cache entries initially all-zero (including alpha), run length zero. Return exactly the qoi_encode pixel payload, excluding the 14-byte image header and 8-byte end marker. Output bits 255:0 contain up to 32 payload bytes in emission order, first byte in bits 7:0; bits 261:256 contain the byte count. Every unused payload byte is zero.

Equal consecutive pixels form RUN: emit 0xc0|(run-1) before a different pixel or at the end; a run-only pixel does not change the cache. For a different pixel first flush a pending run, then compute cache index (3*R+5*G+7*B+11*255) mod 64. On a complete RGBA cache match emit INDEX=index. Otherwise store this pixel at that index and try DIFF, then LUMA, then RGB. Define s8(z)=((z+128) mod 256)-128. Set dr=s8(R-prev_R), dg=s8(G-prev_G), db=s8(B-prev_B), dgr=s8(dr-dg), dgb=s8(db-dg). If dr,dg,db are all in [-2,1], emit 0x40|((dr+2)<<4)|((dg+2)<<2)|(db+2). Otherwise, if dg is in [-32,31] and dgr,dgb are in [-8,7], emit two bytes 0x80|(dg+32), ((dgr+8)<<4)|(dgb+8). Otherwise emit 0xfe,R,G,B. Update previous pixel after each input pixel. Wrapping deltas, opcode priority, cache collisions, run flush and byte count are part of the result. The maximum RUN length 62 cannot be reached in this eight-pixel specialization. This is the upstream QOI RGB encoder payload, not a complete file or a newly chosen RLE scheme.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Eight black pixels (`in_pixels=0`) encode as the single RUN byte `0xc7`.
The result is `(1 << 256) | 0xc7`: byte count 1, first byte 0xc7,
and all remaining payload bytes zero.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
