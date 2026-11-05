# Poseidon S-Box

This block computes the Poseidon `Sigma` transformation on one field element. The operation is exponentiation to the 5th power in the prime field used by circomlib Poseidon: `out = in^5 mod p`, where `p = 21888242871839275222246405745257275088548364400416034343698204186575808495617`. The block is stateless and produces one output field element for each accepted input field element.

## Interface

### RTL interface

```systemverilog
module poseidon_sbox (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [253:0] in_in,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [253:0] out_out
);
```

### Behavior interface

```python
class PoseidonSbox:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_in): ...
```

`process()` returns:

```python
{"out_out": ...}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_out`: 254.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `poseidon_sbox`: top-level module implementing the Poseidon `Sigma` map on one prime-field element.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

All arithmetic is over the finite field `Fp` with modulus

- `p = 21888242871839275222246405745257275088548364400416034343698204186575808495617`

`in_in` accepts every unsigned 254-bit integer and represents its residue modulo `p`. `out_out` is the canonical representative in `[0, p-1]`; noncanonical input encodings are reduced, not rejected.

For each accepted input transaction, the block computes:

1. `in2 = (in_in * in_in) mod p`
2. `in4 = (in2 * in2) mod p`
3. `out_out = (in4 * in_in) mod p`

Equivalently:

- `out_out = in_in^5 mod p`

No other reduction, encoding, or permutation is applied.

The function is defined for all 254-bit input bit patterns. If `in_in` is outside the canonical field range, the block first maps it into the field by modular reduction:

- `x = in_in mod p`
- `out_out = x^5 mod p`

Special cases implied by the field arithmetic:
- If `in_in mod p == 0`, then `out_out = 0`.
- If `in_in mod p == 1`, then `out_out = 1`.
- There is no saturation, signed interpretation, or exceptional value encoding.

The block is a 1:1 transaction mapping: each accepted input produces exactly one output transaction.

## Reset Behavior

Reset is synchronous active-high on `rst`.

Architectural state:
- No architectural state is stored.

Output/interface state after reset and before the first accepted input:
- `in_ready = 1`
- `out_valid = 0`
- `out_out = 0`
