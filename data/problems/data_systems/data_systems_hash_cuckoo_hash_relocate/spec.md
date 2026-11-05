# Faiss int64 open-addressed table operation

Operate on a 256-entry int64-to-int64 table using multiplicative hashing and
bounded linear probing. Each request inserts/overwrites one pair or looks up
one key, returning the value, slot and status. This is a single-key table
operation, not a cuckoo-relocation algorithm or a parallel batch insertion.

## Interface

### RTL interface

```systemverilog
module data_systems_hash_cuckoo_hash_relocate (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic in_lookup,
    input logic [63:0] in_key, in_value,
    output logic out_valid,
    input logic out_ready,
    output logic [1:0] out_status,
    output logic [63:0] out_value,
    output logic [7:0] out_slot,
    output logic table_req_valid,
    input logic table_req_ready,
    output logic table_req_we,
    output logic [7:0] table_req_addr,
    output logic [127:0] table_req_wdata,
    input logic table_resp_valid,
    input logic [127:0] table_resp_rdata
);
```

The external read-write memory `table` has 256 words of 128 bits. Bits 63:0
hold a key and bits 127:64 its value, both represented as raw two's-complement
int64 words. Key 0xffffffffffffffff (-1) denotes an empty entry; empty value
bits do not matter. `in_lookup=0` inserts and `in_lookup=1` looks up; in_value
is ignored for lookup. The request/response memory interface has one outstanding
read, accepts a request on req_valid && req_ready, and returns one resp_valid
with read data. Writes commit on request acceptance and have no response.

### Behavior interface

```python
class FaissInt64Table:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_lookup, in_key, in_value): ...
```

`self.table` is the external-memory image. Call `process` once per accepted
input. It returns one dictionary
`{"out_status": ..., "out_value": ..., "out_slot": ...}` for every operation,
including unsuccessful lookups or insertions. Returned fields are Python
integers holding the specified port bit patterns. No internal table contents
are hidden from the declared memory interface.

The constructor and `reset()` create an empty placeholder image. Before
execution, the external-memory adapter binds `self.table` to the
environment-supplied image. On mid-run reset, it rebinds the retained store,
including committed writes.

## Modules

`data_systems_hash_cuckoo_hash_relocate` is the top-level table controller.

## Functional Behavior

Let signed64(x) interpret the low 64 bits as signed. Compute
P=signed64(signed64(in_key)*1000003), R=P rem 8955327411143, and H=R & 255.
The signed remainder truncates the quotient toward zero. The product explicitly
wraps to 64 bits. Probe slots H,(H+1)&255,...,(H+255)&255 in this order.

- The reserved input key -1 returns status 3 and performs no memory access.
- On insertion, the first slot whose key is empty or equal to in_key is
  overwritten with the full supplied pair. Return status 0, out_value=in_value
  and its slot. All other slots remain unchanged. If no such slot exists,
  return status 2 (full) and do not write anything.
- On lookup, the first equal key returns status 0, its stored value and slot.
  If the initial slot H is empty, return status 1 immediately. Otherwise,
  scan all 256 slots for the key, skipping subsequent empty slots; exhaustion
  returns status 1 (absent). Lookup never writes.
- Every nonzero status returns out_value=0 and out_slot=0. A stored value -1
  is legal and is distinguishable from absence by status.

The environment supplies the initial memory image and does not modify it during
an operation. These rules also define behavior for images with holes or duplicate
keys: the first qualifying probe wins. On images built by insertion, newer
insertions of a key overwrite its prior value. Distinct requests are ordered.
The product wrap and reserved-key error make all input words deterministic;
no undefined signed C overflow or ambiguous sentinel insertion is required.

### Protocol

Input/output records transfer on rising edges with valid && ready. Senders
hold valid and the entire payload stable while stalled. Every accepted request
produces exactly one ordered response unless cancelled by reset. No fixed
latency is required. Memory requests remain stable until accepted. Output
payload is zero while out_valid is zero.

## Reset Behavior

Reset is synchronous active-high. It discards an in-flight operation or pending
response, clears outputs and request signals, and restores in_ready=1. External
memory is not cleared by reset. Writes already accepted remain committed.
The environment supplies the initial contents for each test run, which may be
empty, partially populated or full. The controller must not assume that the
initial table is empty or initialize it on reset.

The external-memory environment cancels pending reads and suppresses responses
while reset is asserted. Reset takes priority over new memory requests,
including writes. No response from before reset reaches a later operation.
