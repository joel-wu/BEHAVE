# HashMap8

An eight-entry persistent unsigned 16-bit key/value map with lookup, insertion/update, deletion and clear.

## Interface

### RTL interface

```systemverilog
module HashMap8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [1:0] in_op,
    input logic [15:0] in_key,
    input logic [15:0] in_value,
    output logic [22:0] out_values
);
endmodule
```

### Behavior interface

```python
class HashMap8:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_op, in_key, in_value): ...
```

Each call represents one accepted command. `in_op` is an unsigned 2-bit
integer; `in_key` and `in_value` are unsigned 16-bit integers. Return one
dictionary with exactly the key `out_values`:

```python
{"out_values": ...}
```

The value is an unsigned 23-bit packed integer, using the payload/status/count
fields below. Every operation, including a miss, full-table rejection or
CLEAR, returns this dictionary, not `None` or a list of entries. Map contents
persist between calls. `reset()` empties the map and returns `None`; unlike
CLEAR it produces no response.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`HashMap8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Ports carry unsigned bit patterns. Every input bit pattern has a defined result.

An eight-entry persistent unsigned 16-bit key/value map with lookup, insertion/update, deletion and clear.

Operations are 0=GET, 1=PUT, 2=DELETE, 3=CLEAR. All 16-bit keys, including zero, are valid. GET returns the current value or a miss. PUT updates an existing key even when the table is full, or inserts a missing key when fewer than eight live entries exist. A ninth distinct key is rejected with no state change. DELETE removes a present key and returns its old value; missing keys do not change state. CLEAR removes all entries; key/value inputs are then ignored. Output bits 15:0 hold the retrieved/deleted value, or the new value for successful PUT; otherwise zero. Bits 18:16 hold status: 0=GET/DELETE miss, 1=GET hit, 2=inserted, 3=updated, 4=deleted, 5=PUT full, 6=cleared. Bits 22:19 hold the live entry count AFTER the operation. The reference uses khash integer hashing (key & 7), triangular probing and tombstones; physical placement is not observable and another correct map organization is allowed. Fixed capacity and explicit full status replace dynamic allocation/rehashing. Deletion must not hide later colliding keys. State changes once per accepted command, not on output stalls.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Starting empty, PUT(key=0,value=42) returns payload 42, status 2 and
count 1 (`out_values=0x0a002a`). A following GET(key=0) returns payload
42, status 1 and count 1 (`out_values=0x09002a`).

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. Reset empties the map. Logical CLEAR is itself a command with a response; hardware reset additionally cancels pending responses.
