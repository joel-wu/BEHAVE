# HyperLogLog Register Update m64

This block performs one HyperLogLog register-update operation for a 64-register sketch. Each accepted input transaction provides a 32-bit hash value and the current 64-register sketch state packed as 64 6-bit registers. The block selects one register from the hash, computes the HyperLogLog rank from the remaining hash bits using a 26-bit leading-zero count plus one, updates the selected register to the larger of its old value and the new rank, and returns the packed updated sketch state.

## Interface

### RTL interface

```systemverilog
module data_systems_sketch_hyperloglog_register_update_m64 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_hash,
    input  logic [383:0] in_registers,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [383:0] out_registers
);
```

`in_registers` and `out_registers` pack 64 registers of 6 bits each. Register `k` occupies bits `[6*k +: 6]` for `k = 0..63`, with register 0 in bits `[5:0]` and register 63 in bits `[383:378]`.

### Behavior interface

```python
class HyperLogLogRegisterUpdateM64:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_hash, in_registers): ...
```

Each `process` call represents one accepted request. Pass `in_hash` as an
unsigned 32-bit integer and `in_registers` as an unsigned 384-bit integer.
It always returns exactly `{"out_registers": image}`, where `image` is an
unsigned 384-bit integer using the same register packing, not a list of
64 integers. An unchanged sketch still returns the full dictionary, never
`None`. The sketch belongs to the caller: feed the returned image into
the next call when desired. The model keeps no sketch state. Construction
initializes the model; `reset()` returns `None` and cannot clear a caller's
image. Physical idle and stall cycles require no calls.

### Protocol and Timing

Transfers occur on rising clock edges with reset inactive and `valid && ready`
true at the corresponding endpoint. Every input yields one ordered response.
Both producers must hold their valid and payload stable while stalled. Input
changes without a transfer do not update a sketch. No fixed response latency
is prescribed. Reset discards pending responses, not a caller-owned image.

## Modules

- `data_systems_sketch_hyperloglog_register_update_m64`: top-level HyperLogLog register update block for one insert into a 64-register sketch.

## Functional Behavior

For each accepted input transaction:

1. Interpret `in_hash` as an unsigned 32-bit integer.

2. Split the hash into:
   - register index `j = (in_hash >> 26) & 0x3F`, an unsigned 6-bit value in `[0, 63]`;
   - suffix `w = in_hash & 0x03FF_FFFF`, the low 26 bits.

3. Compute the rank `r` from `w` as:
   - if bit 25 of `w` is 1, `r = 1`;
   - otherwise `r` is the position of the first 1 bit when scanning `w[25]`, `w[24]`, ..., `w[0]`, counting from 1 at bit 25;
   - if all 26 bits of `w` are 0, `r = 27`.

   Equivalently, `r = clz26(w) + 1`, where `clz26(w)` is the number of leading zero bits in the 26-bit vector `w[25:0]`, so `clz26(0) = 26`. Therefore `r` is always in `[1, 27]`.

   Worked examples:
   - `w = 26'b1xxxxxxxxxxxxxxxxxxxxxxxxx` gives `r = 1`.
   - `w = 26'b001xxxxxxxxxxxxxxxxxxxxxxx` gives `r = 3`.
   - `w = 26'b00000000000000000000000000` gives `r = 27`.

4. Unpack `in_registers` into 64 unsigned 6-bit registers `reg[0]..reg[63]` using:
   - `reg[k] = (in_registers >> (6*k)) & 0x3F`.

5. Read the targeted register value `old = reg[j]`.

6. Compute the updated targeted register value:
   - `new = old` if `old >= r`;
   - `new = r` if `old < r`.

   Since `old` is 6-bit unsigned and `r` is in `[1, 27]`, `new` is also a 6-bit unsigned value in `[0, 63]`.

7. Form the output register array:
   - for `k != j`, output register `k` equals `reg[k]`;
   - for `k == j`, output register `k` equals `new`.

8. Pack the 64 output registers into `out_registers` with the same bit layout as the input:
   - bits `[6*k +: 6]` carry output register `k`.

The block is a 1:1 transaction mapping: each accepted input transaction produces exactly one output transaction.

### Source boundary and finite representation

This is the register-update sub-operator in Figure 2 of Flajolet, Fusy,
Gandouet and Meunier, *HyperLogLog: the analysis of a near-optimal cardinality
estimation algorithm* (AofA 2007), with six index bits and a 26-bit suffix.
It is not an operation from the legacy Faiss Hamming header. Hash generation,
harmonic reduction, bias correction and final cardinality estimation are outside
this task; no estimator-accuracy claim follows from testing this update alone.

A fresh caller-owned register image is all zero. The finite all-zero suffix
has rank 27. Images reachable from zero have values in 0..27. Other six-bit
images are also accepted: values 28..63 remain unchanged by the same maximum
rule. They are a defined interface extension, not reachable sketch states.
To build a stream, the caller feeds `out_registers` into the next request;
the block does not silently retain the sketch between requests.

## Reset Behavior

Reset is active-high and synchronous to `clk`.

There is no architectural state. After reset, the block is idle and able to accept a new input transaction. Output handshake state is cleared so `out_valid = 0`. `out_registers` is `384'b0` whenever no output transaction is being presented.
