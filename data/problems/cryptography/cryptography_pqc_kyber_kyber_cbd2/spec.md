# Kyber CBD2 byte chunk

One four-byte chunk of Kyber's centered-binomial sampler with eta=2.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_kyber_kyber_cbd2 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_b0,
    input  logic [7:0]  in_b1,
    input  logic [7:0]  in_b2,
    input  logic [7:0]  in_b3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [3:0]  out_coeff
);
```

`out_coeff` carries one signed coefficient encoded as a 4-bit two's-complement integer. Legal output values are in the range [-2, +2], encoded as:
- -2 = `4'b1110`
- -1 = `4'b1111`
-  0 = `4'b0000`
- +1 = `4'b0001`
- +2 = `4'b0010`

### Behavior interface

```python
class CryptographyPqcKyberKyberCbd2:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_b0, in_b1, in_b2, in_b3):
        pass
```

`process()` returns a list of 8 dictionaries. Each dictionary has one key:
- `out_coeff`

Return a list of exactly eight dictionaries, ordered by coefficient index j=0..7.
Never return None or a bare dictionary.
Every returned dictionary contains exactly these fields (bit widths):

- `out_coeff`: 4.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is eight ordered records per accepted request; requests must not interleave their output groups. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_pqc_kyber_kyber_cbd2` implements the public interface and operation specified here.

## Functional Behavior

One four-byte chunk of Kyber's centered-binomial sampler with eta=2. Form t=in_b0+2^8*in_b1+2^16*in_b2+2^24*in_b3. For j=0..7, coefficient j is popcount(t[4j+1:4j])-popcount(t[4j+3:4j+2]).

Emit exactly eight separate output records in increasing j order, each with out_coeff equal to that coefficient modulo 16. Negative coefficients -2 and -1 therefore encode as 14 and 15, not 6 and 7. The transform has no persistent algorithmic state. This task starts after the random-byte generator; it does not implement SHAKE or sample a whole polynomial.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
