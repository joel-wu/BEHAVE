# AES128Encrypt

Full AES-128 encryption of one block, including all eleven round keys.

## Interface

### RTL interface

```systemverilog
module AES128Encrypt (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_key,
    input logic [127:0] in_plaintext,
    output logic [127:0] out_values
);
endmodule
```

### Behavior interface

```python
class AES128Encrypt:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_key, in_plaintext): ...
```

Each `process(in_key, in_plaintext)` call represents one accepted block and
returns exactly `{"out_values": ciphertext}`. Both arguments and `ciphertext`
are unsigned 128-bit integers, not byte arrays or hex strings, using the byte
packing defined below. Every block returns one complete dictionary, never
`None` or a list. Keys and cipher state do not persist across calls;
`reset()` returns `None` and emits no result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`AES128Encrypt` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Full AES-128 encryption of one block, including all eleven round keys.

Key, plaintext and ciphertext are 16-byte strings; byte i occupies bits 8*i +: 8 (byte 0 is the least-significant byte). State byte 4*c+r is row r, column c. Encrypt with the AES-128 forward cipher, not AES-192/256, a single round, or decryption. All GF(256) multiplication uses x^8+x^4+x^3+x+1. The S-box maps x to the multiplicative inverse z (0 maps to 0), then z xor ROTL8(z,1) xor ROTL8(z,2) xor ROTL8(z,3) xor ROTL8(z,4) xor 0x63. ShiftRows moves row r left by r columns. MixColumns multiplies each column by the circulant matrix with rows [2,3,1,1], [1,2,3,1], [1,1,2,3], [3,1,1,2].

The first four key words are the input key, in chronological groups of four bytes. For word i=4..43, let T=W[i-1]. If i is divisible by 4, rotate its byte string left one byte, apply the S-box to every byte, and xor Rcon[i/4] into its first byte. Then W[i]=W[i-4] xor T. Rcon[1..10]=01,02,04,08,10,20,40,80,1b,36. Begin by xoring the plaintext with round key 0. Rounds 1..9 perform SubBytes, ShiftRows, MixColumns, AddRoundKey, in that order. Round 10 omits MixColumns. Return the final state bytes in the same column-major order.

This is the complete AES_init_ctx + AES_ECB_encrypt operation in the selected tiny-AES-c AES-128 configuration. The one-block primitive does not specify a multi-message encryption mode, padding, key management or side-channel guarantees.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Byte-string key `000102030405060708090a0b0c0d0e0f` and plaintext `00112233445566778899aabbccddeeff` give ciphertext `69c4e0d86a7b0430d8cdb78070b4c55a`. These are chronological byte strings, not packed-bus hex integers: the packed output is `0x5ac5b47080b7cdd830047b6ad8e0c469`.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
