# ChaChaPolyShort15

Complete IETF ChaCha20-Poly1305 authenticated encryption for 0..15 bytes of AAD and plaintext.

## Interface

### RTL interface

```systemverilog
module ChaChaPolyShort15 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_key,
    input logic [95:0] in_nonce,
    input logic [119:0] in_aad,
    input logic [3:0] in_aad_length,
    input logic [119:0] in_message,
    input logic [3:0] in_length,
    output logic [247:0] out_values
);
endmodule
```

### Behavior interface

```python
class ChaChaPolyShort15:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_key, in_nonce, in_aad, in_aad_length, in_message, in_length): ...
```

Each call represents one accepted encryption request and returns exactly
`{"out_values": packed_result}`. Arguments are unsigned port encodings: key
256 bits, nonce 96 bits, AAD/plaintext 120 bits each, and lengths 0..15.
`packed_result` is one unsigned 248-bit integer containing ciphertext and tag
as specified below, not a byte array or list. Empty plaintext/AAD still returns
the complete dictionary and its authentication tag, never `None`. No handshake
signals are returned. There is no inter-request cipher state. Construction
and `reset()` establish this empty state; `reset()` returns `None`.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`ChaChaPolyShort15` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Ports carry unsigned bit patterns. Byte positions and required zero padding
are defined below. Indices are zero-based. Every input bit pattern has a
defined result.

Complete IETF ChaCha20-Poly1305 authenticated encryption for 0..15 bytes of AAD and plaintext.

All byte strings have byte zero in their least significant byte. Key is 32 bytes, nonce 12 bytes; A=in_aad_length and L=in_length. Ignore later AAD/plaintext bytes. Generate ChaCha20 block counter 0 and use its first 32 bytes as the Poly1305 one-time key. Encrypt the first L plaintext bytes by xor with ChaCha20 block counter 1. MAC AAD || pad16(AAD) || ciphertext || pad16(ciphertext) || LE64(A) || LE64(L), where pad16 appends zero bytes up to a 16-byte boundary and adds none to an empty/aligned string. Return zero-padded 15-byte ciphertext in bits 0..119 and the 16-byte tag in bits 120..247. This is encryption plus authentication, not decryption/tag verification. Nonce uniqueness and side-channel guarantees are outside this functional task.

ChaCha20 words are little-endian uint32. Initial words are 61707865,3320646e,79622d32,6b206574, eight key words, counter, three nonce words. Quarter-round (a,b,c,d): a+=b; d=ROTL(d xor a,16); c+=d; b=ROTL(b xor c,12); a+=b; d=ROTL(d xor a,8); c+=d; b=ROTL(b xor c,7), with sums mod 2^32. Ten double rounds each apply QR to (0,4,8,12),(1,5,9,13),(2,6,10,14),(3,7,11,15), then (0,5,10,15),(1,6,11,12),(2,7,8,13),(3,4,9,14). Add initial words back and serialize.

Poly1305 interprets key halves little-endian: r=key_low128 & 0x0ffffffc0ffffffc0ffffffc0fffffff, s=key_high128. Start h=0; for each full MAC-input block b, h=((h+LE128(b)+2^128)*r) mod (2^130-5). Tag=(h+s) mod 2^128, serialized little-endian. The length block is always present, including A=L=0.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

For all-zero key and nonce and empty AAD/plaintext, ciphertext is empty (all 15 stored bytes zero); tag bytes are 4eb972c9a8fb3a1b382bb4d36f5ffad1. The mandatory length block is still authenticated.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
