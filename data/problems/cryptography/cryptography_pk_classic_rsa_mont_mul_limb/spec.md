# Multi-limb Montgomery product

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module cryptography_pk_classic_rsa_mont_mul_limb #(
    parameter int unsigned NW = 4
) (
    input  logic                  clk,
    input  logic                  rst,

    input  logic                  in_valid,
    output logic                  in_ready,
    input  logic [64*NW-1:0]      in_a,
    input  logic [64*NW-1:0]      in_b,
    input  logic [64*NW-1:0]      in_n,
    input  logic [63:0]           in_m0,

    output logic                  out_valid,
    input  logic                  out_ready,
    output logic [64*NW-1:0]      out_result
);
```

### Behavior interface

```python
class ClassicRsaMontMulLimb:
    def __init__(self, NW=4): ...
    def reset(self): ...
    def process(self, in_a, in_b, in_n, in_m0): ...
```

Constructor argument `NW` matches the RTL parameter. Each `process(...)` call
represents one accepted input and returns exactly `{"out_result": value}`, where
`value` is an unsigned `64*NW`-bit integer. Arguments are unsigned port words:
`in_a`, `in_b`, and `in_n` each have `64*NW` bits; `in_m0` has 64 bits. Limbs are
packed in these integers, not supplied or returned as Python lists. A zero result
still returns the dictionary. `reset()` returns `None`; no arithmetic state
persists between requests. Latency is not represented by extra model calls.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`cryptography_pk_classic_rsa_mont_mul_limb` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

NW is a positive integer parameter, default 4. Let B=2^64 and R=B^NW. Inputs a, b and n are unsigned NW-limb integers, with limb i in bits [64*i +: 64]; m0 is one unsigned 64-bit word.

The following integer recurrence defines the result for every input bit pattern. Start T=a*b. For i=0..NW-1 set k=((T // B^i)*m0) mod B, then T=T+k*n*B^i. Set u=T // R. If u>=n, subtract n once; return the low 64*NW bits of u.

On the Montgomery domain (n odd, 0<=a,b<n and m0=-n^-1 mod B), this equals a*b*R^-1 mod n. Other raw inputs use the recurrence above; they do not acquire a modular-inverse interpretation. The recurrence specifies values, not a required datapath or cycle count.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
