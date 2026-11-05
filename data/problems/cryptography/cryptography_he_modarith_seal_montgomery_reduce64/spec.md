# HEXL Variable-Radix Montgomery Reduction

Reduce a two-limb integer by the Montgomery REDC operation with checked radix and inverse parameters.

## Interface

### RTL interface

```systemverilog
module HexlMontgomeryReduce64 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_T_hi,
    input logic [63:0] in_T_lo,
    input logic [63:0] in_q,
    input logic [6:0] in_r,
    input logic [63:0] in_mod_R_msk,
    input logic [63:0] in_inv_mod,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_S,
    output logic out_error
);
```

### Behavior interface

```python
class HexlMontgomeryReduce64:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_T_hi, in_T_lo, in_q, in_r, in_mod_R_msk, in_inv_mod): ...
```

Each call consumes one accepted request and returns one `dict` with exactly
`out_S` and `out_error`. All arguments and fields are Python integers encoding
unsigned port bits. `out_S` is in 0..2^64-1 and `out_error` is in 0..1.
Invalid requests return `{"out_S": 0, "out_error": 1}`, not `None` or an empty
list. A valid zero result is also a complete dictionary. There is no persistent
arithmetic state; `reset()` produces no response.

## Modules

`HexlMontgomeryReduce64` is the complete top-level module.

## Functional Behavior

Payloads use unsigned bit patterns. An input is accepted on a rising edge with in_valid && in_ready, and an output is emitted with out_valid && out_ready. Once presented, valid and its payload remain stable until accepted; reset cancels pending transfers. Latency is unconstrained. Each accepted input transaction produces exactly one output transaction, in input order.

Let r=in_r, q=in_q, R=2^r, mask=in_mod_R_msk, inv=in_inv_mod, and T=in_T_hi*2^64+in_T_lo. A request is valid exactly when 1 <= r <= 63, 0 < q < R, q is odd, mask=R-1, inv<R, (q*inv) mod R=R-1, and T<R*q. Invalid requests return out_error=1 and out_S=0.

For a valid request, m=(T*inv) mod R and u=(T+m*q)/R (an exact integer quotient). Return out_error=0 and out_S=u-q if u>=q, otherwise u. Thus 0<=out_S<q and out_S*R is congruent to T modulo q. All intermediate operations are mathematical integer operations; only the stated modulo R discards bits. This task uses 64-bit limbs regardless of r.

## Reset Behavior

Reset is synchronous active-high rst. It empties pending output; out_valid is low during reset. There is no persistent arithmetic state. A new request after reset is independent of requests before reset.
