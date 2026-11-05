# Data Systems Vector Search IVF Probe Accumulate

This block accumulates probe-list scan statistics for one query of an IVF vector search. Each accepted input transaction represents one probed inverted list and provides the list key, whether the list is empty, how many codes were scanned from that list, and whether the per-query probe loop should terminate after this probe. The block maintains per-query running totals matching the probe-loop accounting in IVF search: number of visited non-empty lists and number of computed distances. It uses a streaming N:1 algorithm: intermediate probes update internal accumulators and emit no output; the probe marked as final emits the accumulated totals for the completed query and clears state for the next query.

## Interface

### RTL interface

```systemverilog
module data_systems_vector_search_ivf_probe_accumulate (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_key,
    input  logic         in_list_empty,
    input  logic [31:0]  in_scan_count,
    input  logic         in_last_probe,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_nlist_visited,
    output logic [31:0]  out_ndis
);
```

### Behavior interface

```python
class DataSystemsVectorSearchIvfProbeAccumulate:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_key, in_list_empty, in_scan_count, in_last_probe): ...
```

Each `process` call consumes one accepted probe, not a clock cycle. Supply
`in_key` and `in_scan_count` as unsigned 32-bit bit patterns and both flags as
0 or 1. A non-final probe returns `None`. A final probe returns one dictionary
with exactly `out_nlist_visited` and `out_ndis`, both unsigned 32-bit integers,
including when the probe is skipped or both totals are zero. It never returns
a list. Keep one instance throughout a query; the final call clears its totals
for the next query. Construction initializes zero totals; `reset()` clears
them, returns `None` and produces no output record.

## Modules

- `data_systems_vector_search_ivf_probe_accumulate`: top-level accumulator for one query's IVF probe-loop statistics.

## Functional Behavior

Inputs are interpreted as follows on each accepted input transaction:

- `in_key` is a 32-bit signed probe-list identifier encoded as a 2's-complement bit pattern.
- `in_list_empty` is 1 if the referenced inverted list is empty and therefore contributes no visited-list count and no scanned codes.
- `in_scan_count` is an unsigned 32-bit count of distances computed for this probe. It is used only when the probe is valid for accumulation as defined below.
- `in_last_probe` is 1 when this accepted input is the final probe of the current query.

Internal architectural state:

- `nlist_visited_accum`: unsigned 32-bit running total of visited non-empty probe lists for the current query.
- `ndis_accum`: unsigned 32-bit running total of scanned codes for the current query.

Per accepted input, accumulation is performed in this order:

1. Decode `in_key` as signed 32-bit:
   - Let `key_s = in_key` interpreted as 2's-complement signed integer in range `[-2^31, 2^31 - 1]`.

2. Determine whether this probe contributes to the totals:
   - If `key_s < 0`, the probe contributes nothing. This matches the IVF probe-loop behavior for negative keys, which are skipped.
   - Else if `in_list_empty == 1`, the probe contributes nothing.
   - Else the probe contributes:
     - `visited_inc = 1`
     - `ndis_inc = in_scan_count`

3. Update the running totals with 32-bit unsigned wraparound arithmetic:
   - `nlist_visited_next = (nlist_visited_accum + visited_inc) mod 2^32`
   - `ndis_next = (ndis_accum + ndis_inc) mod 2^32`

4. Output behavior:
   - If `in_last_probe == 0`, the transaction produces no output. The internal state becomes `nlist_visited_next` and `ndis_next`.
   - If `in_last_probe == 1`, the transaction produces exactly one output:
     - `out_nlist_visited = nlist_visited_next`
     - `out_ndis = ndis_next`
     Both outputs are 32-bit unsigned bit patterns.
     After producing this output, the internal state for the next query is cleared to zero:
     - `nlist_visited_accum = 0`
     - `ndis_accum = 0`

Edge-case rules:

- Negative `in_key` always suppresses both increments, regardless of `in_list_empty` and `in_scan_count`.
- Non-negative `in_key` with `in_list_empty == 1` suppresses both increments, regardless of `in_scan_count`.
- `in_scan_count` is added only for a non-negative, non-empty probe.
- If the very first probe of a query has `in_last_probe == 1`, the block outputs the totals for that single probe and then clears state.
- Arithmetic overflow wraps modulo `2^32`; there is no saturation.
- There is no explicit query-start input. Query boundaries are defined only by `in_last_probe == 1` on an accepted input.

Handshake-related requirements:

- The block accepts one probe transaction at a time.
- A correct implementation may deassert `in_ready` while holding a pending final output that has not yet been accepted on the output handshake.
- While `out_valid == 1` and `out_ready == 0`, `out_nlist_visited` and `out_ndis` must remain stable until the output transfer occurs.

## Reset Behavior

`rst` is active-high and synchronous. On any rising clock edge with `rst == 1`, the block shall set:

- `nlist_visited_accum = 32'h00000000`
- `ndis_accum = 32'h00000000`
- `out_valid = 1'b0`
- `out_nlist_visited = 32'h00000000`
- `out_ndis = 32'h00000000`

After reset, the block contains no partial-query state and the first accepted input starts a new query accumulation.
