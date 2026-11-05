# HEXL inverse lazy butterfly

One InvButterflyRadix2, preserving its exact lazy representatives.

## Interface

### RTL interface

```systemverilog
module cryptography_he_ntt_hexl_harvey_inv_butterfly (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_x_op,
    input  logic [63:0]  in_y_op,
    input  logic [63:0]  in_w,
    input  logic [63:0]  in_w_precon,
    input  logic [63:0]  in_modulus,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_x_r,
    output logic [63:0]  out_y_r
);
```

### Behavior interface

```python
class CryptographyHeNttHexlHarveyInvButterfly:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x_op, in_y_op, in_w, in_w_precon, in_modulus):
        pass
```

Each call represents one accepted request and returns exactly one dictionary
with keys:
- `out_x_r`
- `out_y_r`

Both values and all five input arguments are Python integers in [0, 2**64 - 1],
encoding unsigned port words. The two fields form one response, not separate
records. Return the dictionary even for all-zero inputs, not `None` or a list.
There is no persistent algorithmic state; `reset()` returns no response.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_he_ntt_hexl_harvey_inv_butterfly` implements the public interface and operation specified here.

## Functional Behavior

One InvButterflyRadix2, preserving its exact lazy representatives. Let x,y,w,c,q denote the five unsigned inputs, and wrap64 the low 64 bits. Compute:

```
d = wrap64(2*q)
s = wrap64(x+y)
out_x_r = wrap64(s-d if s >= d else s)
v = wrap64(x+d-y)
Q = floor(v*c / 2^64)
out_y_r = wrap64(v*w - Q*q)
```

Do not normalize the last result by subtracting q. For 2 <= q < 2^62, w<q, c=floor(2^64*w/q), and x,y<2q, both results lie in [0,2q) and are congruent to x+y and w*(x-y) modulo q. This butterfly alone does not apply whole-transform normalization. The displayed word rules also define all other bit patterns, without a modular-correctness guarantee; q=0 is not a special bypass.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
