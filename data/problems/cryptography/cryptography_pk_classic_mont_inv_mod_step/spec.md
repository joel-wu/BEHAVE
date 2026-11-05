# Single-word Montgomery inverse exponent step

One externally sequenced square-and-multiply step for exponent e=(in_modulus-2) modulo 2^64.

## Interface

### RTL interface

```systemverilog
module cryptography_pk_classic_mont_inv_mod_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_start,
    input  logic [63:0]  in_a,
    input  logic [63:0]  in_modulus,
    input  logic [63:0]  in_accum,
    input  logic [6:0]   in_bit_index,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_accum,
    output logic [6:0]   out_bit_index,
    output logic         out_done
);
```

### Behavior interface

```python
class CryptographyPkClassicMontInvModStep:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_start, in_a, in_modulus, in_accum, in_bit_index): ...
```

Each `process(...)` call represents one accepted request and returns one
dictionary with exactly the keys `out_accum`, `out_bit_index`, and `out_done`.
Arguments and results are unsigned port bit patterns. The accumulator is
64 bits, the bit index is 7 bits, and `out_done` is 1 bit. An unfinished
step (`out_done=0`) still returns the complete dictionary, not `None` or a list.

The caller supplies the accumulator and index for every step; the instance
does not retain them between calls. `reset()` returns `None` and emits no
record. Clock cycles and handshake stalls do not cause model calls.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_pk_classic_mont_inv_mod_step` implements the public interface and operation specified here.

## Functional Behavior

One externally sequenced square-and-multiply step for exponent e=(in_modulus-2) modulo 2^64. With R=2^64, the intended domain is an odd prime q>=3, a=in_a and accum=in_accum in [0,q), both already Montgomery encoded. If in_start=1, begin from R modulo q, NOT ordinary integer1; otherwise use accum. The caller chooses in_bit_index in descending order, starting at the most significant bit of q-2 (leading zero bits are also harmless).

Define MM(x,y): t=x*y; m=(low64(t)*m0) modulo R; u=floor((t+m*q)/R); return low64(u-q if u>=q else u). m0=-q^(-1) modulo R for odd q, and zero for even q. The numerator is exact 129-bit arithmetic. Compute square=MM(effective_accum,effective_accum), then next=MM(square,a) if the selected exponent bit is1, otherwise square. Return out_accum=next, out_bit_index=(in_bit_index-1) modulo128, and out_done=(in_bit_index==0).

For totality, indices64..127 select a zero-valued exponent bit; on start with q=0 use accumulator0. The same word rules cover all other inputs. An inverse is guaranteed only for nonzero canonical a under an odd prime q>=3, after a complete descending-bit chain initialized with in_start=1. Zero remains a legal input and produces zero after that chain, not a multiplicative inverse. With q=13, in_a=5, start=1 and bit_index=3 the output accumulator is5: R modulo13 is3, and MM(3,3)=3. State is supplied explicitly; this unit does not secretly retain the previous accumulator.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
