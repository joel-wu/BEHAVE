# Observable paged key/value cache placement

This is the native-word-copy (`kAuto`) specialization of vLLM's reshape_and_cache kernel: scatter one token's head vectors into the different key and value physical layouts. It includes raw readback so cache contents and layout, not just an acknowledgement, are observable. The historical task ID contains "quant", but this variant does not claim FP8/NVFP4 conversion or scale arithmetic.

## Interface

### RTL interface

```systemverilog
module AiMlQuantKvPagedQuantKvBlock #(
    parameter int NUM_BLOCKS=4, NUM_HEADS=2, HEAD_SIZE=8, BLOCK_SIZE=4,
    parameter int X=4, ELEM_W=16, SLOT_W=32
) (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [1:0] in_op,
    input logic [SLOT_W-1:0] in_slot,
    input logic [NUM_HEADS*HEAD_SIZE*ELEM_W-1:0] in_key, in_value,
    input logic [31:0] in_address,
    output logic out_valid,
    input logic out_ready,
    output logic out_ok, out_wrote,
    output logic [ELEM_W-1:0] out_data
);
```

### Behavior interface

```python
class AiMlQuantKvPagedQuantKvBlock:
    def __init__(self, num_blocks=4, num_heads=2, head_size=8, block_size=4,
                 x=4, elem_w=16, slot_w=32): ...
    def reset(self): ...
    def process(self, in_op, in_slot, in_key, in_value, in_address): ...
```

Python constructor arguments correspond to uppercase RTL parameters.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data`: ELEM_W.
- `out_ok`, `out_wrote`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings match the uppercase RTL parameters.

### Parameter domain

All dimensions are positive, HEAD_SIZE is divisible by X, 1<=ELEM_W<=32, 2<=SLOT_W<=32, NUM_BLOCKS*BLOCK_SIZE < 2^(SLOT_W-1), and WORDS=NUM_BLOCKS*NUM_HEADS*HEAD_SIZE*BLOCK_SIZE <=4096. Other parameterizations are outside the task. Words are opaque unsigned ELEM_W-bit patterns; no floating conversion occurs.

## Modules

AiMlQuantKvPagedQuantKvBlock is the complete cache-placement/readback block. Implementation latency is free subject to the task execution constraints.

## Functional Behavior

Each accepted input yields one ordered record with out_ok, out_wrote and out_data. Commands are:

- in_op=0: write a token. Interpret in_slot as signed SLOT_W-bit two's complement. Slots outside [0, NUM_BLOCKS*BLOCK_SIZE) produce (0,0,0) and do not modify memory. Otherwise b=slot//BLOCK_SIZE and o=slot%BLOCK_SIZE. For every head h and element e, read input lane h*HEAD_SIZE+e at bits [ELEM_W*(h*HEAD_SIZE+e) +: ELEM_W]. Write the key word to K[b,h,e//X,o,e%X] and the value word to V[b,h,e,o]. Return (1,1,0). All other slots retain their words. in_address is ignored.
- in_op=1: read a key-cache physical word at unsigned in_address.
- in_op=2: read a value-cache physical word at unsigned in_address.
- in_op=3: return (0,0,0), with no state change.

For reads, addresses below WORDS return (1,0,the stored word); other addresses return (0,0,0). in_slot and the input payloads are ignored. Addresses count words, not bytes. Both arrays flatten row-major with the rightmost axis contiguous:

K index = ((((b*NUM_HEADS+h)*(HEAD_SIZE/X)+e//X)*BLOCK_SIZE+o)*X+e%X).
V index = (((b*NUM_HEADS+h)*HEAD_SIZE+e)*BLOCK_SIZE+o).

Reads observe all preceding accepted writes. In particular, a read accepted when a previous write acknowledgement is consumed sees the new data. No operation occurs without input acceptance. A write is committed on acceptance even if its acknowledgement subsequently stalls; no later command may overtake it.

## Reset Behavior

Synchronous active-high reset zeroes every cache word, discards pending responses, deasserts out_valid and zeros out_ok/out_wrote/out_data. After reset in_ready=1. Transfers occur on rising edges with valid and ready high; a pending output and its payload remain stable until accepted. When idle the output payload is zero. Reset takes precedence over simultaneous handshakes and prevents pre-reset data from reappearing.
