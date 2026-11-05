# Blake2s256Short31

Complete unkeyed sequential BLAKE2s-256 of a 0–31 byte message.

## Interface

### RTL interface

```systemverilog
module Blake2s256Short31 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [247:0] in_message,
    input logic [4:0] in_length,
    output logic [255:0] out_values
);
endmodule
```

### Behavior interface

```python
class Blake2s256Short31:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_message, in_length): ...
```

Each call represents one complete message request and returns exactly
`{"out_values": digest_bits}`. `in_message` is an unsigned 248-bit integer,
`in_length` is an integer in 0..31, and `digest_bits` is the unsigned 256-bit
digest packed as defined below. Empty messages also return this dictionary,
never `None`, a byte array or a list. No handshake signals are returned.
Hash state is local to each call. Construction and `reset()` establish this
empty inter-request state, and `reset()` returns `None`.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`Blake2s256Short31` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Complete unkeyed sequential BLAKE2s-256 of a 0–31 byte message.

Use the first L=in_length bytes, byte i at bits 8*i +: 8; later bytes do not affect the digest. Pad to 64 bytes with zeros and load sixteen little-endian 32-bit words m[0..15]. This is NOT SHA-style 0x80/bit-length padding. The output is eight little-endian words, h[i] at bits 32*i +: 32. All additions below wrap modulo 2^32 and ROTR is a 32-bit rotation.

IV=[6a09e667,bb67ae85,3c6ef372,a54ff53a,510e527f,9b05688c,1f83d9ab,5be0cd19] (hex). Initialize h=IV, then h[0] xor=0x01010020 (digest length 32, no key, fanout/depth 1). Initialize v=h followed by IV; xor L into v[12] and 0xffffffff into v[14]. This is the only and final block, including the empty message.

G(a,b,c,d,x,y) updates the selected v words sequentially: a=a+b+x; d=ROTR(d xor a,16); c=c+d; b=ROTR(b xor c,12); a=a+b+y; d=ROTR(d xor a,8); c=c+d; b=ROTR(b xor c,7). For each round r=0..9, execute G on index tuples (0,4,8,12), (1,5,9,13), (2,6,10,14), (3,7,11,15), (0,5,10,15), (1,6,11,12), (2,7,8,13), (3,4,9,14), in that order. Invocation j uses x=m[sigma[r][2*j]], y=m[sigma[r][2*j+1]]. Sigma rows are:

```text
 0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
14 10  4  8  9 15 13  6  1 12  0  2 11  7  5  3
11  8 12  0  5  2 15 13 10 14  3  6  7  1  9  4
 7  9  3  1 13 12 11 14  2  6  5 10  4  0 15  8
 9  0  5  7  2  4 10 15 14  1 11 12  6  8  3 13
 2 12  6 10  0 11  8  3  4 13  7  5 15 14  1  9
12  5  1 15 14 13  4 10  0  7  6  3  9  2  8 11
13 11  7 14 12  1  3  9  5  0 15  4  8  6  2 10
 6 15 14  9 11  3  0  8 12  2 13  7  1  4 10  5
10  2  8  4  7  6  1  5 15 11  9 14  3 12 13  0
```

Finally set h[i]=h[i] xor v[i] xor v[i+8] for i=0..7, and return all eight words. This is the reference blake2s digest API with output size 32 and no key, salt, personalization, tree hashing or output truncation; not BLAKE2b.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

For message bytes `61 62 63` (ASCII abc), L=3 and in_message low bytes `0x636261`, the digest bytes are `508c5e8c327c14e2e1a72ba34eeb452f37458b209ed63a294d999b4c86675982`. The first digest byte 0x50 occupies the low eight output bits.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
