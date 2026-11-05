# GHASH bounded message segment

Compute up to sixteen consecutive GHASH block updates.

## Interface

### RTL interface

```systemverilog
module ghash_full_message (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_h,
    input  logic [127:0] in_y0,
    input  logic [7:0]   in_len_blocks,
    input  logic [127:0] in_block0,
    input  logic [127:0] in_block1,
    input  logic [127:0] in_block2,
    input  logic [127:0] in_block3,
    input  logic [127:0] in_block4,
    input  logic [127:0] in_block5,
    input  logic [127:0] in_block6,
    input  logic [127:0] in_block7,
    input  logic [127:0] in_block8,
    input  logic [127:0] in_block9,
    input  logic [127:0] in_block10,
    input  logic [127:0] in_block11,
    input  logic [127:0] in_block12,
    input  logic [127:0] in_block13,
    input  logic [127:0] in_block14,
    input  logic [127:0] in_block15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_y
);
```

All 8-bit values of `in_len_blocks` are accepted. Process the first `min(in_len_blocks, 16)` blocks and ignore the rest.

All 128-bit ports are byte strings in big-endian byte order. Bit 127 is the most significant bit of byte 0, and bit 0 is the least significant bit of byte 15.

### Behavior interface

```python
class GHashFullMessage:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_h,
        in_y0,
        in_len_blocks,
        in_block0,
        in_block1,
        in_block2,
        in_block3,
        in_block4,
        in_block5,
        in_block6,
        in_block7,
        in_block8,
        in_block9,
        in_block10,
        in_block11,
        in_block12,
        in_block13,
        in_block14,
        in_block15,
    ):
        pass
```

Call `process` once per accepted input. It returns one dictionary
`{"out_y": ...}`, whose value is a Python integer holding the specified
output port bit pattern.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`ghash_full_message` implements the public interface and operation specified here.

## Functional Behavior

Compute up to sixteen consecutive GHASH block updates. Let n=min(in_len_blocks,16), Y0=in_y0, and for k=0..n-1 let Y(k+1)=(Yk XOR in_blockk)*in_h. Return out_y=Yn. n=0 returns in_y0, and all unused block inputs are ignored. Each request emits one final record, not the intermediate accumulators.

Multiplication uses GF(2^128) modulo z^128+z^7+z^2+z+1, with port bit127 representing z^0 and bit0 representing z^127. Bytes are big-endian on each port. A right shift and conditional XOR of 0xe1000000000000000000000000000000 multiply by z in this ordering. There is no required internal table, pipeline or multiplier topology.

Longer messages can chain out_y into the next request's in_y0. The caller supplies already-padded blocks, including any GCM length block; this unit does not add them or perform AES/tag encryption. All input bit patterns are defined, including counts17..255 (clamped to16).

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
