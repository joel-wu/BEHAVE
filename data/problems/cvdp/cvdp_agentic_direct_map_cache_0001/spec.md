# direct_map_cache

Native direct-mapped cache-array operations with live lookups.

## Interface

### RTL interface

```systemverilog
module direct_map_cache #(
    parameter int CACHE_SIZE=256,
    parameter int DATA_WIDTH=16,
    parameter int TAG_WIDTH=5,
    parameter int OFFSET_WIDTH=3
) (
    input logic clk, rst, enable,
    input logic [$clog2(CACHE_SIZE)-1:0] index,
    input logic [OFFSET_WIDTH-1:0] offset,
    input logic comp, write,
    input logic [TAG_WIDTH-1:0] tag_in,
    input logic [DATA_WIDTH-1:0] data_in,
    input logic valid_in,
    output logic hit, dirty,
    output logic [TAG_WIDTH-1:0] tag_out,
    output logic [DATA_WIDTH-1:0] data_out,
    output logic valid, error
);
```

### Behavior interface

```python
class DirectMapCache:
    def __init__(self, CACHE_SIZE=256, DATA_WIDTH=16, TAG_WIDTH=5, OFFSET_WIDTH=3): ...
    def reset(self): ...
    def process(self, enable, index, offset, comp, write, tag_in, data_in, valid_in): ...
```

Construct the model with the RTL parameter values. Call `process(...)` for
each non-reset rising edge, including disabled cycles: it returns settled
pre-edge outputs, then applies that edge's storage update. Every call returns
one dictionary with exactly `hit`, `dirty`, `tag_out`, `data_out`, `valid`, and
`error`. Values are unsigned port encodings: `tag_out` has `TAG_WIDTH` bits,
`data_out` has `DATA_WIDTH` bits, and the other four fields have one bit.
Disabled accesses and address errors also return the complete dictionary,
never `None` or an empty list.

Arguments are unsigned port encodings with the parameterized widths declared
above. Construction and `reset()` clear all data, tags, valid bits, and dirty
bits. `reset()` returns `None` and produces no record; subsequent calls observe
the cleared storage. Ordinary calls retain storage, including idle cycles.

## Modules

- `direct_map_cache`: the complete native module above.

## Functional Behavior

Keep the native cache-array interface; no external ready/valid wrapper
or main-memory controller is present. CACHE_SIZE>=2, DATA_WIDTH>=1, TAG_WIDTH>=1
and OFFSET_WIDTH>=1. There are 2**(OFFSET_WIDTH-1) DATA_WIDTH-bit words per line.
The source's offset[0] alignment rule is retained for every DATA_WIDTH; it is a
fixed two-offset-units-per-word convention, not a promise of byte-size scaling.
Word selection is offset>>1. Non-power-of-two CACHE_SIZE is supported; unused
index encodings are errors, never wrapped to a different line.

All outputs are combinational observations of the CURRENT stored line. enable=0
forces every output to zero and prevents writes. With enable=1, odd offset or
index>=CACHE_SIZE sets error=1, all other outputs zero, and prevents writes.
Otherwise tag_out, data_out, valid and dirty expose the selected stored values,
even for invalid lines or compare misses. hit=comp && valid && (tag_out==tag_in).

At each rising edge outside reset, an enabled valid-address write in direct mode
(comp=0) writes only the selected word, assigns tag_in and valid_in to its line,
and clears that line's dirty bit. Other words in the same line are retained,
including when the tag changes. A compare-mode write occurs ONLY on a hit;
it updates that word and sets dirty, preserving tag and valid. Compare misses,
reads, disabled cycles and errors do not modify storage. Pins reflect the new
storage after the edge.
Repeated enabled write cycles are repeated writes, not one accepted packet.

Source completions: retain the explicit immediate lookup requirement rather
than the contradictory loose reference to output registers. Disabled/miss
visibility, unused index codes and retained sibling words are specified above.
Reset clears all data/tag/valid/dirty storage synchronously; output gating by
enable and error still applies while reset is high. The original logging-only
test is not an unchanged pass oracle and contains no answer RTL.


## Reset Behavior

`rst` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
