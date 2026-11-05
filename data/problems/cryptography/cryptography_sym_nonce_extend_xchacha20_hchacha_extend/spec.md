# HChaCha20 subkey derivation

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_nonce_extend_xchacha20_hchacha_extend (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_nonce,
    input  logic [255:0] in_key,
    input  logic [127:0] in_const,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_subkey
);
```

### Behavior interface

```python
class CryptographySymNonceExtendXchacha20HchachaExtend:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_nonce, in_key, in_const): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_subkey`: 256.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`cryptography_sym_nonce_extend_xchacha20_hchacha_extend` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Split const, key and nonce into little-endian 32-bit words. Initialize s[0..3]=const, s[4..11]=key, s[12..15]=nonce. A ChaCha quarter-round on (a,b,c,d) is:

```text
a=a+b; d=ROL32(d XOR a,16); c=c+d; b=ROL32(b XOR c,12)
a=a+b; d=ROL32(d XOR a, 8); c=c+d; b=ROL32(b XOR c, 7)
```

Additions wrap modulo 2^32. Repeat ten double rounds: columns (0,4,8,12), (1,5,9,13), (2,6,10,14), (3,7,11,15), then diagonals (0,5,10,15), (1,6,11,12), (2,7,8,13), (3,4,9,14). Pack words s[0..3],s[12..15] little-endian into the 256-bit output. Do not add the initial state back.

The usual constant is the little-endian encoding of the bytes `expand 32-byte k`; arbitrary 128-bit constants are also accepted. This kernel consumes the first 128 nonce bits for subkey derivation, not a complete XChaCha stream or AEAD operation.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
