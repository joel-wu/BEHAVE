# Argon2 1024-byte block compression

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_memhard_argon2_g_block_mix (
    input  logic           clk,
    input  logic           rst,

    input  logic           in_valid,
    output logic           in_ready,
    input  logic           in_with_xor,
    input  logic [8191:0]  in_prev_block,
    input  logic [8191:0]  in_ref_block,
    input  logic [8191:0]  in_next_block,

    output logic           out_valid,
    input  logic           out_ready,
    output logic [8191:0]  out_next_block
);
```

### Behavior interface

```python
class CryptographySymMemhardArgon2GBlockMix:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_with_xor, in_prev_block, in_ref_block, in_next_block): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_next_block`: 8192.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`cryptography_sym_memhard_argon2_g_block_mix` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Each packed block contains 128 little-endian 64-bit words (8192 bits, or 1024 bytes). Let R=prev_block XOR ref_block. Starting with V=R, transform each consecutive group of 16 words, then each group [2*j,2*j+1,16+2*j,17+2*j,...,112+2*j,113+2*j] for j=0..7.

The 16-word transform applies G to tuples (0,4,8,12), (1,5,9,13), (2,6,10,14), (3,7,11,15), then (0,5,10,15), (1,6,11,12), (2,7,8,13), (3,4,9,14). For G(a,b,c,d), define f(x,y)=x+y+2*(x mod 2^32)*(y mod 2^32), reduced modulo 2^64, and execute:

```text
a=f(a,b); d=ROTR64(d XOR a,32); c=f(c,d); b=ROTR64(b XOR c,24)
a=f(a,b); d=ROTR64(d XOR a,16); c=f(c,d); b=ROTR64(b XOR c,63)
```

Return V XOR R, additionally XORing the old next_block when with_xor=1. with_xor=0 ignores next_block. This retains the full Argon2 block transform, but not the memory-filling schedule, password processing or complete hash.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
