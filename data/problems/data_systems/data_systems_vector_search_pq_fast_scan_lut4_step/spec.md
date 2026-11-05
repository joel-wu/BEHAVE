# PQFastScanLUT4Step

Accumulate one 16-entry uint8 lookup-table contribution into each of 32 uint16 distance lanes using a packed four-bit PQ block.

## Interface

### RTL interface

```systemverilog
module data_systems_vector_search_pq_fast_scan_lut4_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_codes_packed,
    input  logic [127:0] in_lut,
    input  logic [511:0] in_accum,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [511:0] out_accum
);
```

### Behavior interface

```python
class DataSystemsVectorSearchPQFastScanLut4Step:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_codes_packed, in_lut, in_accum): ...
```

Each call represents one accepted input and returns exactly
`{"out_accum": value}`, with `value` an unsigned 512-bit packed integer.
Arguments are unsigned packed integers: 128 bits each for `in_codes_packed`
and `in_lut`, and 512 bits for `in_accum`. They are not lists of lanes.
The return is never `None` or a list. Requests are independent; `reset()`
returns `None` without emitting a record or changing any table.

## Modules

- `data_systems_vector_search_pq_fast_scan_lut4_step`: the complete operator and its input/output handshake.

## Functional Behavior

The 128-bit code input contains sixteen bytes for one subquantizer and 32 vectors. Bytes, lookup entries and accumulator lanes are packed least-significant first. Let P = (0,8,1,9,2,10,3,11,4,12,5,13,6,14,7,15). Byte k holds the code for logical vector P[k] in its low nibble and vector P[k]+16 in its high nibble.

For logical lane j, decode its four-bit code c[j], read LUT[c[j]] from the 16 unsigned byte entries in in_lut, and return

    out_accum[j] = (in_accum[j] + LUT[c[j]]) mod 65536.

All 32 accumulator lanes use logical vector order, independent of the code-byte permutation. Every code nibble is used exactly once. Overflow wraps as uint16; there is no saturation. For example, byte 1 contains codes for vectors 8 and 24, not vectors 1 and 17. An accumulator of 65535 plus a table value of 255 becomes 254.

Input transfers occur on a rising clock edge when in_valid and in_ready are both 1; outputs transfer when out_valid and out_ready are both 1. The source holds every input field and in_valid until acceptance. The design holds every output field and out_valid until consumption. Each accepted input transaction produces exactly one output transaction, in input order. No fixed latency or internal pipeline is required. With reset inactive and the sink accepting outputs, pending work must eventually complete. Reset cancels pending work.

## Reset Behavior

Synchronous, active-high rst. Reset clears out_valid and visible output payloads to zero and restores an input-ready empty state. There is no retained computational state between requests.
