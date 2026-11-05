# HSalsa20 subkey derivation

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_nonce_extend_xsalsa20_hsalsa_extend (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_input,
    input  logic [255:0] in_key,
    input  logic [127:0] in_const,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_subkey
);
```

### Behavior interface

```python
class CryptographySymNonceExtendXsalsa20HsalsaExtend:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_input, in_key, in_const): ...
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

`cryptography_sym_nonce_extend_xsalsa20_hsalsa_extend` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Split const, key and nonce into little-endian 32-bit words. Place const in s[0,5,10,15], key in s[1,2,3,4,11,12,13,14], and nonce in s[6,7,8,9]. The Salsa quarter-round on (a,b,c,d) is:

```text
b ^= ROL32(a+d,7); c ^= ROL32(b+a,9)
d ^= ROL32(c+b,13); a ^= ROL32(d+c,18)
```

Additions wrap modulo 2^32. Repeat ten double rounds: columns (0,4,8,12), (5,9,13,1), (10,14,2,6), (15,3,7,11), then rows (0,1,2,3), (5,6,7,4), (10,11,8,9), (15,12,13,14). Return s[0,5,10,15,6,7,8,9], packed little-endian. Do not add the initial state back. The standard constant is `expand 32-byte k`, encoded little-endian; other constants use the same transform. This is HSalsa20 subkey derivation, not the complete XSalsa20 stream cipher.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
