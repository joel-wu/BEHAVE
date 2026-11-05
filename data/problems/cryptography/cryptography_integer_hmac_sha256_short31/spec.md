# HMACSHA256Short31

Complete HMAC-SHA256 with a 32-byte key and a 0..31 byte message.

## Interface

### RTL interface

```systemverilog
module HMACSHA256Short31 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_key,
    input logic [247:0] in_message,
    input logic [4:0] in_length,
    output logic [255:0] out_values
);
endmodule
```

### Behavior interface

```python
class HMACSHA256Short31:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_key, in_message, in_length): ...
```

Each `process` call represents one accepted request. Pass `in_key`,
`in_message`, and `in_length` as unsigned 256-bit, 248-bit, and 5-bit integers.
It always returns exactly `{"out_values": digest}`, where `digest` is an
unsigned 256-bit integer with the first digest byte in bits 7:0. This also
applies to an empty message; do not return bytes, a list, or `None`.
There is no state between requests. Construction initializes the model;
`reset()` returns `None` and has no numerical effect. Physical latency and
stall cycles require no additional calls.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`HMACSHA256Short31` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Ports carry unsigned bit patterns. Byte positions and required zero padding
are defined below. Indices are zero-based. Every input bit pattern has a
defined result.

Complete HMAC-SHA256 with a 32-byte key and a 0..31 byte message.

Byte i occupies bits 8*i; use exactly the first L=in_length message bytes, ignoring the rest. Pad the fixed 32-byte key to 64 bytes with zero as K0. Define ipad[i]=K0[i] xor 0x36 and opad[i]=K0[i] xor 0x5c for all 64 byte positions. Compute SHA256(opad || SHA256(ipad || M)). Both inner and outer hashes include standard SHA256 0x80/zero padding and their full big-endian 64-bit bit lengths. Each has exactly two compression blocks for these lengths. Return the 32 digest bytes, first byte least significant.

SHA256 details: load words big-endian; IV words are 6a09e667,bb67ae85,3c6ef372,a54ff53a,510e527f,9b05688c,1f83d9ab,5be0cd19. W[t]=sigma1(W[t-2])+W[t-7]+sigma0(W[t-15])+W[t-16] mod 2^32, for t=16..63; sigma0=ROTR7 xor ROTR18 xor SHR3, sigma1=ROTR17 xor ROTR19 xor SHR10. For each round T1=h+Sigma1(e)+Ch(e,f,g)+K[t]+W[t], T2=Sigma0(a)+Maj(a,b,c), then (a,b,c,d,e,f,g,h)=(T1+T2,a,b,c,d+T1,e,f,g), all modulo 2^32. Sigma0=ROTR2 xor ROTR13 xor ROTR22; Sigma1=ROTR6 xor ROTR11 xor ROTR25; Ch=(x&y) xor (~x&z); Maj=(x&y) xor (x&z) xor (y&z). Add the final working words into the IV/chaining words after each block. Round constants, in order:
```
428a2f98 71374491 b5c0fbcf e9b5dba5 3956c25b 59f111f1 923f82a4 ab1c5ed5
d807aa98 12835b01 243185be 550c7dc3 72be5d74 80deb1fe 9bdc06a7 c19bf174
e49b69c1 efbe4786 0fc19dc6 240ca1cc 2de92c6f 4a7484aa 5cb0a9dc 76f988da
983e5152 a831c66d b00327c8 bf597fc7 c6e00bf3 d5a79147 06ca6351 14292967
27b70a85 2e1b2138 4d2c6dfc 53380d13 650a7354 766a0abb 81c2c92e 92722c85
a2bfe8a1 a81a664b c24b8b70 c76c51a3 d192e819 d6990624 f40e3585 106aa070
19a4c116 1e376c08 2748774c 34b0bcb5 391c0cb3 4ed8aa4a 5b9cca4f 682e6ff3
748f82ee 78a5636f 84c87814 8cc70208 90befffa a4506ceb bef9a3f7 c67178f2
```

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

A 32-byte key whose first 20 bytes are 0x0b and remaining bytes zero, and message "Hi There" (L=8), give RFC 4231 tag b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
