# Poseidon Width-Three Selected Mix Coordinate

Apply the circomlib width-three field-mixing matrix and return one selected output coordinate.

## Interface

### RTL interface

```systemverilog
module PoseidonMixT3 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [255:0] in_0,
    input logic [255:0] in_1,
    input logic [255:0] in_2,
    input logic [1:0] in_s,
    output logic out_valid,
    input logic out_ready,
    output logic [255:0] out,
    output logic out_error
);
```

### Behavior interface

```python
class PoseidonMixT3:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_0, in_1, in_2, in_s): ...
```

process returns one dictionary containing `out`, `out_error`.

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out`: 256.
- `out_error`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Ports use unsigned payload bit patterns unless stated otherwise. An input is accepted on a rising edge with in_valid && in_ready, and an output is emitted with out_valid && out_ready. Once presented, valid and its payload remain stable until accepted; reset cancels pending transfers. Latency is unconstrained. Each accepted input transaction produces exactly one output transaction, in input order.

## Modules

`PoseidonMixT3` is the complete top-level module.

## Functional Behavior

Let p=21888242871839275222246405745257275088548364400416034343698204186575808495617 and xj=in_j mod p for j=0,1,2. All field results are canonical integers in [0,p). With s=in_s, return out=sum(M[j][s]*xj for j=0..2) mod p and out_error=0 when s<3. When s=3, return out=0 and out_error=1. This selects one coordinate of the width-three Mix transform; it is not a full Poseidon permutation.

The matrix rows M[0], M[1], M[2] are, in hexadecimal:

```
0x109b7f411ba0e4c9b2b70caf5c36a7b194be7c11ad24378bfedb68592ba8118b 0x2969f27eed31a480b9c36c764379dbca2cc8fdd1415c3dded62940bcde0bd771 0x143021ec686a3f330d5f9e654638065ce6cd79e28c5b3753326244ee65a1b1a7
0x16ed41e13bb9c0c66ae119424fddbcbc9314dc9fdbdeea55d6c64543dc4903e0 0x2e2419f9ec02ec394c9871c832963dc1b89d743c8c7b964029b2311687b1fe23 0x176cc029695ad02582a70eff08a6fd99d057e12e58e7d7b6b16cdfabc8ee2911
0x2b90bba00fca0589f617e7dcbfe82e0df706ab640ceb247b791a93b74e36736d 0x101071f0032379b697315876690f053d148d4e109f5fb065c8aacc55a0f89bfa 0x19a3fc0a56702bf417ba7fee3802593fa644470307043f7773279cd71d25d5e0
```

Matrix indexing is M[input coordinate][output coordinate].

## Reset Behavior

Reset is synchronous active-high rst. It empties pending output; out_valid is low during reset. There is no persistent arithmetic state. A new request after reset is independent of requests before reset.
