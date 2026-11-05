# Count-Min Counter Update and Query d4w256

Maintain four independent rows of 256 unsigned 16-bit counters. Each request
supplies two hash residues and a nonnegative increment. Update one cell in
each row and return the four resulting counts and their minimum. An increment
of zero is a read-only query. Hash generation is outside this counter core;
there is no unused key input.

## Interface

### RTL interface

```systemverilog
module data_systems_sketch_count_min_sketch_update_d4w256 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [7:0] in_h1,
    input logic [7:0] in_h2,
    input logic [15:0] in_delta,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_counts,
    output logic [15:0] out_estimate
);
```

### Behavior interface

```python
class CountMinSketchUpdateD4W256:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_h1, in_h2, in_delta): ...
```

Each call represents one accepted request and returns exactly
`{"out_counts": counts, "out_estimate": estimate}`. Hash residues are unsigned
8-bit integers and the increment is an unsigned 16-bit integer. `counts` is
one unsigned 64-bit integer packing four post-update counters, row zero least
significant, not a list; `estimate` is their unsigned 16-bit minimum. A zero
increment is a query and still returns the complete dictionary, never `None`.
No handshake signals are returned. The 1024 counters persist between calls;
construction and `reset()` clear all of them. `reset()` returns `None`.

### Protocol and Timing

Transfers occur at rising clk edges with rst inactive and valid && ready true
on the respective endpoint. Each accepted request produces one ordered response.
State changes only on input acceptance, never merely because input pins change.
Both producers hold valid and payload stable while stalled. The output captures
the result for that request, including when a previous response is consumed
on the same edge. No fixed response latency is required.

## Modules

`data_systems_sketch_count_min_sketch_update_d4w256` implements the public interface and operation specified here.

## Functional Behavior

For row r=0,1,2,3, address column `c[r]=(in_h1+r*in_h2) mod 256`.
Its new counter is `v[r]=min(65535, old[r,c[r]]+in_delta)`, using a wide
addition before saturation. Store v[r] into that cell. Every other counter
is unchanged. Rows remain distinct even when their column numbers are equal.

Output `out_counts[16*r +: 16]=v[r]`, with row zero least significant, and
`out_estimate=min(v[0],v[1],v[2],v[3])`. These are the post-update values.
Delta zero returns the current counts without modifying them. No counter
wraps. Repeating a request accumulates its increment up to saturation.
All input words, including delta=65535 and either zero hash residue, are legal.

### Adaptation boundary

The retained mechanism is Count-Min's row update and minimum query, described
in Kirsch and Mitzenmacher (2008), Section 9. The legacy Faiss Hamming header
does not define it. Width 256, four rows, caller-provided double-hash residues
and saturating 16-bit counters are explicit finite task choices. They do not
inherit the paper's prime-width/independent-hash error guarantee; after
saturation the result is a clipped estimate, not an upper bound on unbounded
true frequency. There is no deletion or negative increment.

## Reset Behavior

Reset is synchronous active-high rst. A reset edge clears all persistent state,
pending responses, output payloads and out_valid. No pre-reset response may
reappear. Output payload
values outside reset when out_valid=0 are not significant.
