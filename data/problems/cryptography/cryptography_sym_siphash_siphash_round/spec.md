# SipHash Round

This block implements one SipHash compression round on a 256-bit internal state represented as four 64-bit words `v0`, `v1`, `v2`, and `v3`. The round function is the standard SipHash `SIPROUND` transform: four modular additions, six fixed left rotations, and four XOR combinations in a fixed order. Each accepted input transaction contains one pre-round state and produces one post-round state.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_siphash_siphash_round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_v0,
    input  logic [63:0]  in_v1,
    input  logic [63:0]  in_v2,
    input  logic [63:0]  in_v3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_v0,
    output logic [63:0]  out_v1,
    output logic [63:0]  out_v2,
    output logic [63:0]  out_v3
);
```

### Behavior interface

```python
class SipHashRound:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_v0, in_v1, in_v2, in_v3):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_v0`, `out_v1`, `out_v2`, `out_v3`: 64.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cryptography_sym_siphash_siphash_round`: top-level module that computes one SipHash `SIPROUND` transform from one 256-bit input state to one 256-bit output state.

## Functional Behavior

All input and output ports are unsigned 64-bit words. Every addition is modulo `2^64` with wraparound. Every rotation is a 64-bit rotate-left. No operation is signed.

Define `ROTL64(x, n)` as:
- `((x << n) | (x >> (64 - n))) mod 2^64`
- with fixed rotation counts `n` in `{13, 16, 17, 21, 32}` only.

For one accepted input transaction, let the initial state be:
- `v0 = in_v0`
- `v1 = in_v1`
- `v2 = in_v2`
- `v3 = in_v3`

The output state is computed by executing the following steps in order:

1. `v0 = (v0 + v1) mod 2^64`
2. `v1 = ROTL64(v1, 13)`
3. `v1 = v1 XOR v0`
4. `v0 = ROTL64(v0, 32)`
5. `v2 = (v2 + v3) mod 2^64`
6. `v3 = ROTL64(v3, 16)`
7. `v3 = v3 XOR v2`
8. `v0 = (v0 + v3) mod 2^64`
9. `v3 = ROTL64(v3, 21)`
10. `v3 = v3 XOR v0`
11. `v2 = (v2 + v1) mod 2^64`
12. `v1 = ROTL64(v1, 17)`
13. `v1 = v1 XOR v2`
14. `v2 = ROTL64(v2, 32)`

The module produces one output transaction per input transaction with:
- `out_v0 = v0`
- `out_v1 = v1`
- `out_v2 = v2`
- `out_v3 = v3`

Because all rotation counts are fixed and in the range 1 to 63, no special shift-count handling is required beyond 64-bit truncation after each rotate construction.

Worked example:
- If `in_v0 = 0`, `in_v1 = 0`, `in_v2 = 0`, and `in_v3 = 0`, then every intermediate value remains `0`, so `out_v0 = out_v1 = out_v2 = out_v3 = 0`.

## Reset Behavior

`rst` is active-high and asynchronous. Reset discards any pending output. The round function has no persistent algorithmic state.

After reset, the output handshake/control state shall be idle:
- `out_valid = 0`

No output data is architecturally meaningful until an output transaction occurs. A combinational implementation may drive any values on `out_v0`, `out_v1`, `out_v2`, and `out_v3` while `out_valid = 0`; a registered implementation may clear them to `0`.
