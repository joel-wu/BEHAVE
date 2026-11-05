# Little-Endian 32-bit Stream Reader

Assemble each group of four accepted bytes into one unsigned little-endian 32-bit word.

## Interface

### RTL interface

```systemverilog
module little_endian_u32_reader (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [7:0] in_byte,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_word
);
```

### Behavior interface

```python
class LittleEndianU32Reader:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_byte): ...
```

Call `process` once per accepted input byte. It returns `None` for the first
three bytes of each four-byte frame and one dictionary `{"out_word": word}`
for the fourth. `word` is a Python integer holding the packed 32-bit output.
A completed word of zero returns `{"out_word": 0}`, not `None`.

### Transaction protocol

Payloads use unsigned bit patterns unless stated otherwise. An input is accepted on a rising edge with in_valid && in_ready, and an output is emitted with out_valid && out_ready. Once presented, valid and its payload remain stable until accepted; reset cancels pending transfers. Latency is unconstrained. Outputs follow the order of completed four-byte frames.

## Modules

`little_endian_u32_reader` is the complete top-level module.

## Functional Behavior

Starting from an empty frame, label each four consecutive accepted bytes b0,b1,b2,b3. Emit out_word=b0+(b1<<8)+(b2<<16)+(b3<<24). A byte not accepted by handshake does not advance the frame. Per four accepted input transactions, produce exactly one output transaction. Groups do not overlap; after the fourth byte the next accepted byte starts a new group.

One, two, or three trailing bytes remain a partial frame and emit no output until completed. Backpressure must neither discard nor repeat bytes or words. For input bytes 78,56,34,12 in hexadecimal the output is 0x12345678. There is no error case for any 8-bit byte.

## Reset Behavior

Reset is synchronous active-high rst. It empties pending output; out_valid is low during reset. Reset discards both the partial frame and any pending output word. The next accepted byte is b0 of a new frame; no word may combine bytes from opposite sides of reset.
