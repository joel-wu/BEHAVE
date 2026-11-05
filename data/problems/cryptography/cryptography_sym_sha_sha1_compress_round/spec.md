# SHA-1 single-block compression

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_sha_sha1_compress_round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_h0,
    input  logic [31:0]  in_h1,
    input  logic [31:0]  in_h2,
    input  logic [31:0]  in_h3,
    input  logic [31:0]  in_h4,
    input  logic [31:0]  in_w0,
    input  logic [31:0]  in_w1,
    input  logic [31:0]  in_w2,
    input  logic [31:0]  in_w3,
    input  logic [31:0]  in_w4,
    input  logic [31:0]  in_w5,
    input  logic [31:0]  in_w6,
    input  logic [31:0]  in_w7,
    input  logic [31:0]  in_w8,
    input  logic [31:0]  in_w9,
    input  logic [31:0]  in_w10,
    input  logic [31:0]  in_w11,
    input  logic [31:0]  in_w12,
    input  logic [31:0]  in_w13,
    input  logic [31:0]  in_w14,
    input  logic [31:0]  in_w15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_h0,
    output logic [31:0]  out_h1,
    output logic [31:0]  out_h2,
    output logic [31:0]  out_h3,
    output logic [31:0]  out_h4
);
```

### Behavior interface

```python
class SHA1CompressRound:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_h0, in_h1, in_h2, in_h3, in_h4, in_w0, in_w1, in_w2, in_w3, in_w4, in_w5, in_w6, in_w7, in_w8, in_w9, in_w10, in_w11, in_w12, in_w13, in_w14, in_w15): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_h0`, `out_h1`, `out_h2`, `out_h3`, `out_h4`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`cryptography_sym_sha_sha1_compress_round` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

The five unsigned 32-bit state inputs supply H0..H4. The sixteen input message words supply W[0..15] in message order; each is already a 32-bit SHA-1 word (the byte-level convention is big-endian). Extend W[t]=ROL32(W[t-3] XOR W[t-8] XOR W[t-14] XOR W[t-16],1) for t=16..79.

Start (a,b,c,d,e)=(H0,H1,H2,H3,H4). For t=0..79 compute T=ROL32(a,5)+f(b,c,d)+e+K+W[t] modulo 2^32 and update (a,b,c,d,e)=(T,a,ROL32(b,30),c,d). For t ranges 0..19,20..39,40..59,60..79 respectively, (f,K) is (choose,0x5a827999), (xor,0x6ed9eba1), (majority,0x8f1bbcdc), (xor,0xca62c1d6); choose=(b&c)|(~b&d), majority=(b&c)|(b&d)|(c&d), xor=b^c^d. Return the five initial state words plus the final working words, modulo 2^32.

This is all 80 rounds plus feed-forward, despite the legacy directory name. Padding and the standard initial IV are supplied by the caller, not inserted by the kernel.

## Reset Behavior

Reset rst is asynchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
