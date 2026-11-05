# BDMA strided-transfer descriptor programming

Validate one NVDLA-style BDMA descriptor and produce the logical configuration
written by processor_bdma_program_slot. This is a descriptor-programming
primitive, not a memory copier or a per-byte address generator. The source
programs base addresses, strides and repeat counts; it does not enumerate bytes.
One accepted descriptor always produces one complete configuration/error record,
including for large transfers.

## Interface

### RTL interface

```systemverilog
module ai_ml_arch_dma_dma_strided_2d (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [63:0] in_source_address,
    input logic [63:0] in_destination_address,
    input logic [31:0] in_line_size,
    input logic [13:0] in_line_repeat,
    input logic [13:0] in_surface_repeat,
    input logic [31:0] in_source_line,
    input logic [31:0] in_destination_line,
    input logic [31:0] in_source_surface,
    input logic [31:0] in_destination_surface,
    input logic [0:0] in_source_type,
    input logic [0:0] in_destination_type,
    input logic out_ready,
    output logic out_valid,
    output logic [63:0] out_source_address,
    output logic [63:0] out_destination_address,
    output logic [26:0] out_line,
    output logic [0:0] out_source_type,
    output logic [0:0] out_destination_type,
    output logic [12:0] out_line_repeat,
    output logic [31:0] out_source_line,
    output logic [31:0] out_destination_line,
    output logic [12:0] out_surface_repeat,
    output logic [31:0] out_source_surface,
    output logic [31:0] out_destination_surface,
    output logic [0:0] out_enable,
    output logic [0:0] out_illegal
);
```

### Behavior interface

```python
class DmaStrided2D:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_source_address, in_destination_address, in_line_size, in_line_repeat, in_surface_repeat, in_source_line, in_destination_line, in_source_surface, in_destination_surface, in_source_type, in_destination_type): ...
```

Each call represents one accepted descriptor. Arguments are unsigned integers
with their corresponding RTL port widths. Return one dictionary with exactly
these keys:

```python
{"out_source_address": ..., "out_destination_address": ..., "out_line": ...,
 "out_source_type": ..., "out_destination_type": ..., "out_line_repeat": ...,
 "out_source_line": ..., "out_destination_line": ..., "out_surface_repeat": ...,
 "out_source_surface": ..., "out_destination_surface": ...,
 "out_enable": ..., "out_illegal": ...}
```

Every value is an unsigned integer with the corresponding output port width;
the one-bit fields are integers `0` or `1`. Include all 13 keys for both legal
and illegal descriptors. Do not return `None`, a list or a per-byte expansion.
There is no persistent descriptor state or model memory access. `reset()`
returns `None` and produces no response.

### Protocol and timing

Input and output records transfer on rising clock edges with valid && ready.
Every accepted input has exactly one ordered response. A stalled sender holds
valid and the whole payload stable. Backpressure is allowed; dropping or
duplicating descriptors is not. No fixed processing latency is prescribed.
All output payload fields are zero while out_valid is zero.

## Modules

ai_ml_arch_dma_dma_strided_2d is the single top-level module.

## Functional Behavior

All ports are unsigned. Addresses are already-translated 64-bit byte addresses.
Line size and strides are byte counts. The repeat ports encode counts, not
count-minus-one. Type selectors 0 and 1 denote MC and CVSRAM respectively.
The result exposes logical register fields, not platform MMIO addresses or the
packed CFG_CMD bit positions. Source/destination addresses combine their low/high
register halves without discarding any bits.

A descriptor is legal exactly when all the following hold:

- Both repeat counts are between 1 and 8192 inclusive.
- line_size is nonzero and divisible by 32.
- Each line stride is at least line_size.
- Each surface stride is at least its line stride times line_repeat.

The stride products are exact (up to 46 bits), not wrapping 32-bit products.
Nonzero counts/size and exact products make the source's otherwise underflowing
or overflowing C boundary explicit. Address alignment is not an extra check.

For a legal descriptor, out_illegal=0, out_enable=1, and:

- out_source_address and out_destination_address preserve the input addresses.
- out_line=(in_line_size/32)-1, the encoded number of 32-byte units per line.
- out_line_repeat=in_line_repeat-1 and out_surface_repeat=in_surface_repeat-1.
- Source/destination line strides, surface strides and type selectors pass
  through unchanged to the correspondingly named output fields.

For an illegal descriptor, out_illegal=1 and every other output field, including
out_enable, is zero.
No byte records, transfer data or implicit memory accesses accompany that record.
Interpreting the legal configuration gives byte address
base + surface_index*surface_stride + line_index*line_stride + byte_index,
modulo 2^64; this describes downstream use, not extra task outputs.

### Source adaptation

The environment supplies translated addresses and an available programming slot;
driver polling, address translation, interrupts and actual DMA execution are
outside this primitive. One atomic logical configuration record replaces the
source's ordered register writes. This retains all geometry and memory-type
fields without materializing a potentially multi-terabyte byte-operation list.

## Reset Behavior

Active-high synchronous reset discards any pending response. After reset,
out_valid and every output payload are zero, and in_ready is one. No descriptor
state persists across reset.
