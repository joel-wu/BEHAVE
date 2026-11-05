# AESCMACShort31

Complete AES-128 CMAC authentication of a 0..31 byte message, including empty and full-block finalization.

## Interface

### RTL interface

```systemverilog
module AESCMACShort31 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_key,
    input logic [247:0] in_message,
    input logic [4:0] in_length,
    output logic [127:0] out_values
);
endmodule
```

### Behavior interface

```python
class AESCMACShort31:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_key, in_message, in_length): ...
```

Each call represents one complete message request and returns exactly
`{"out_values": tag_bits}`. Arguments are unsigned port encodings: `in_key`
has 128 bits, `in_message` has 248 bits and `in_length` is in 0..31.
`tag_bits` is the unsigned 128-bit tag packed as defined below, not a byte
array. An empty message also returns this dictionary, never `None` or a list.
No handshake signals are returned. Key expansion and chaining are local to
each call; no message state persists between calls. Construction and `reset()`
establish this empty state, and `reset()` returns `None`.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`AESCMACShort31` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Complete AES-128 CMAC authentication of a 0..31 byte message, including empty and full-block finalization.

All key/message/tag byte strings use byte i at bits 8*i. L=in_length; ignore message bytes at i>=L. Let E be full AES-128 under in_key. Let K1=dbl(E(0)), K2=dbl(K1), where dbl shifts the chronological 128-bit byte string left one bit and xors 0x87 into its last byte iff its first byte had high bit set. Use at least one message block. For a nonempty multiple-of-16 final block xor K1; otherwise append byte 0x80 and zero bytes to the partial/empty final block and xor K2. Starting from C=0, compute C=E(C xor block) for every block, including that modified final block; return C. This is CMAC, not raw CBC-MAC, and does not approve key management or side-channel security.

AES primitive details:
Full AES-128 encryption of one block, including all eleven round keys.

Key, plaintext and ciphertext are 16-byte strings; byte i occupies bits 8*i +: 8 (byte 0 is the least-significant byte). State byte 4*c+r is row r, column c. Encrypt with the AES-128 forward cipher, not AES-192/256, a single round, or decryption. All GF(256) multiplication uses x^8+x^4+x^3+x+1. The S-box maps x to the multiplicative inverse z (0 maps to 0), then z xor ROTL8(z,1) xor ROTL8(z,2) xor ROTL8(z,3) xor ROTL8(z,4) xor 0x63. ShiftRows moves row r left by r columns. MixColumns multiplies each column by the circulant matrix with rows [2,3,1,1], [1,2,3,1], [1,1,2,3], [3,1,1,2].

The first four key words are the input key, in chronological groups of four bytes. For word i=4..43, let T=W[i-1]. If i is divisible by 4, rotate its byte string left one byte, apply the S-box to every byte, and xor Rcon[i/4] into its first byte. Then W[i]=W[i-4] xor T. Rcon[1..10]=01,02,04,08,10,20,40,80,1b,36. Begin by xoring the plaintext with round key 0. Rounds 1..9 perform SubBytes, ShiftRows, MixColumns, AddRoundKey, in that order. Round 10 omits MixColumns. Return the final state bytes in the same column-major order.

This is the complete AES_init_ctx + AES_ECB_encrypt operation in the selected tiny-AES-c AES-128 configuration. The one-block primitive does not specify a multi-message encryption mode, padding, key management or side-channel guarantees.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

RFC 4493 key bytes 2b7e151628aed2a6abf7158809cf4f3c with an empty message gives tag bb1d6929e95937287fa37d129b756746. These are chronological byte strings.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
