# Roaring Array Union

Compute the sorted, duplicate-free union of two bounded Roaring array
containers. The active input entries must be strictly increasing; this block
checks that requirement and reports an error for an invalid container. Empty
containers are supported. This is a finite array-container operation, not a
complete Roaring bitmap implementation.

## Interface

### RTL interface

```systemverilog
module data_systems_bitmap_roaring_array_union (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [5:0] in_card_1,
    input logic [5:0] in_card_2,
    input logic [1023:0] in_array_1,
    input logic [1023:0] in_array_2,
    output logic out_valid,
    input logic out_ready,
    output logic [6:0] out_card,
    output logic [2047:0] out_array,
    output logic out_error
);
```

### Behavior interface

```python
class RoaringArrayUnion:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_card_1, in_card_2, in_array_1, in_array_2): ...
```

Each call returns one dictionary with `out_card`, `out_array`, and `out_error`.
All payload fields are Python integers carrying the corresponding unsigned RTL
bit patterns; `out_error` is 0 or 1. `out_array` is one packed integer, not a
Python list. Empty and invalid inputs still return the dictionary specified
below. `reset()` has no persistent algorithmic state to clear and returns `None`.

### Protocol and Timing

Input and output transfers occur on rising `clk` edges with the corresponding
`valid && ready` true and reset inactive. Each accepted input produces exactly
one ordered output. The producer holds input valid and its complete payload
while stalled. The block holds output valid and all output fields while
`out_ready=0`. Input payload changes without an input transfer have no effect.
Arbitrary finite backpressure is supported; transfer latency is not fixed.

## Modules

`data_systems_bitmap_roaring_array_union` implements the public interface and operation specified here.

## Functional Behavior

Each input cardinality is in 0..63. Input lane `k` occupies bits
`16*k +: 16` of its 1024-bit array word, so lane zero is least significant.
Only lanes below that input's cardinality are active. All remaining lanes,
including lane 63, are ignored and may contain arbitrary bits.

If either active sequence contains adjacent entries `a[k] <= a[k-1]`, return
`out_error=1`, `out_card=0`, and `out_array=0`. The same rule applies to the
second sequence. Do not sort or deduplicate an invalid input to conceal this
error. No persistent state is changed by either a valid or invalid request.

Otherwise return `out_error=0` and the mathematical union of the active sets,
in strictly increasing order. A value shared by the two inputs appears once.
`out_card` is the number of union members, at most 126. Output lane `k` is bits
`16*k +: 16` of `out_array`. Every lane at or above `out_card` is zero.
For two empty inputs all outputs are zero. Empty/nonempty union copies the
nonempty sequence. Zero-valued active members are data, not padding markers.

The reference uses an ordered merge. Any implementation producing these
results and satisfying the interface contract is permitted; no merge-network
topology or fixed cycle latency is required.

## Reset Behavior

`rst` is synchronous and active high. A reset edge discards all unfinished
work and pending output, clears the output payload and `out_valid`, and starts
an empty transaction history. No pre-reset response is emitted afterward.
Payload values when `out_valid=0` outside reset are not significant.
