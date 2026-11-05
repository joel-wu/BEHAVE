# Kyber CBD3 byte chunk

One three-byte chunk of the eta=3 sampler.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_kyber_kyber_cbd3 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [23:0] in_t,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [3:0]  out_c0,
    output logic [3:0]  out_c1,
    output logic [3:0]  out_c2,
    output logic [3:0]  out_c3
);
```

### Behavior interface

```python
class cryptography_pqc_kyber_kyber_cbd3:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_t):
        pass
```

The return value of `process()` is a dict with keys `out_c0`, `out_c1`, `out_c2`, and `out_c3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c0`, `out_c1`, `out_c2`, `out_c3`: 4.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_pqc_kyber_kyber_cbd3` implements the public interface and operation specified here.

## Functional Behavior

One three-byte chunk of the eta=3 sampler. The 24-bit word in_t packs byte0 in bits7:0, byte1 in bits15:8, byte2 in bits23:16. For j=0..3, let c[j]=popcount(in_t[6j+2:6j])-popcount(in_t[6j+5:6j+3]). Return one record containing out_c0..out_c3 = c[0]..c[3] modulo 16. Each coefficient lies in [-3,3].

The four coefficients are simultaneous fields of one output record, unlike the CBD2 task's eight-record stream. The transform consumes already-generated random bytes; no RNG or SHAKE is included. All 24-bit inputs are valid, and no multiplier or specific bit-summing circuit is required.

## Reset Behavior

Reset is active-high and asynchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
