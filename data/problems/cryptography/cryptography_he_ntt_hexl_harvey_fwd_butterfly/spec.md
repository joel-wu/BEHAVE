# HEXL forward lazy butterfly

One FwdButterflyRadix2, including its lazy (not canonical) residue representation.

## Interface

### RTL interface

```systemverilog
module cryptography_he_ntt_hexl_harvey_fwd_butterfly (
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
class CryptographyHeNttHexlHarveyFwdButterfly:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x_op, in_y_op, in_w, in_w_precon, in_modulus):
        pass
```

Each call represents one accepted request and returns exactly one dictionary
with keys `out_x_r` and `out_y_r`. Both values and all five input arguments are
Python integers in [0, 2**64 - 1], encoding unsigned port words. The two fields
form one response, not two response records. Return a dictionary even when both
fields are zero, not `None` or a list. No state persists between requests;
`reset()` returns no response.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_he_ntt_hexl_harvey_fwd_butterfly` implements the public interface and operation specified here.

## Functional Behavior

One FwdButterflyRadix2, including its lazy (not canonical) residue representation. Let x,y,w,c,q denote in_x_op,in_y_op,in_w,in_w_precon,in_modulus. Write wrap64 for the low 64 bits. Compute:

```
d = wrap64(2*q)
tx = x-d if x >= d else x
Q = floor(y*c / 2^64)
T = wrap64(y*w - Q*q)
out_x_r = wrap64(tx + T)
out_y_r = wrap64(tx + d - T)
```

There is no extra subtraction of q from T. Its exact representative is observable, not merely its congruence class. The source domain is 2 <= q < 2^62, 0 <= w < q, c=floor(2^64*w/q), and x,y < 4q. Then T is in [0,2q), outputs are in [0,4q), and they are congruent to x+w*y and x-w*y modulo q. Outside this domain, including q=0 or an incorrect preconditioner, the displayed word rules define the result without a modular-correctness guarantee.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
