# RadixBucketSortPass8b

Stably group a frame of up to 256 payload records by their full unsigned eight-bit bucket keys.

## Interface

### RTL interface

```systemverilog
module data_systems_sort_rank_radix_bucket_sort_pass_8b (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_last,
    input  logic [7:0]  in_bucket,
    input  logic [31:0] in_payload,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_bucket,
    output logic [31:0] out_payload,
    output logic        out_last
);
```

### Behavior interface

```python
class DataSystemsSortRankRadixBucketSortPass8b:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_last, in_bucket, in_payload): ...
```

Arguments are unsigned port words: `in_last` has one bit, `in_bucket` eight,
and `in_payload` 32. One `process(...)` call represents one accepted input.
Before the final record of a frame, return `None`. The call with `in_last=1`
returns an ordered list containing one dictionary per record in the completed
frame. Every dictionary has exactly `out_bucket` (unsigned eight-bit key),
`out_payload` (unsigned 32-bit payload), and `out_last` (one bit), in the order
defined below. Even a one-record frame returns a one-element list. Keep one
instance across a frame; the terminating call clears its frame state after
forming the result. `reset()` returns `None` and discards any partial frame.
Model calls do not represent output-drain cycles.

## Modules

- `data_systems_sort_rank_radix_bucket_sort_pass_8b`: the complete operator and its input/output handshake.

## Functional Behavior

Each accepted input appends one (in_bucket,in_payload) record to the current frame. A frame has 1..256 records and ends on the accepted record with in_last=1. The 256th record must end the frame. There is no timeout-based delimiter. A partial frame produces no output until its terminating record arrives.

Emit exactly one output per frame record, sorted by the unsigned key 0..255. Records with equal keys remain in input order; payload bits are unchanged. Set out_last only on the final output of the frame. Frames and their outputs remain in input-frame order. The implementation may backpressure subsequent input while draining a frame.

For input (255,A),(2,B),(255,C),(0,D,last), emit (0,D),(2,B),(255,A),(255,C,last). A full 256-record frame is legal, including 256 records with the same key. After its last output, the next frame starts empty.

Input transfers occur on a rising clock edge when in_valid and in_ready are both 1; outputs transfer when out_valid and out_ready are both 1. The source holds every input field and in_valid until acceptance. The design holds every output field and out_valid until consumption. Each completed frame produces as many output transactions as it contains input transactions. No fixed latency or internal pipeline is required. With reset inactive and the sink accepting outputs, pending work must eventually complete. Reset cancels pending work.

## Reset Behavior

Synchronous, active-high rst. Reset clears out_valid and visible output payloads to zero and restores an input-ready empty state. Reset discards the partial input frame and any undelivered sorted frame, returning to an empty frame.
