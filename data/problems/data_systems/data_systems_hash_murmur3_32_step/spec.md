# data_systems_hash_murmur3_32_step

`data_systems_hash_murmur3_32_step` is a single-step 32-bit MurmurHash3 x86 mixing block. Each accepted input transaction consumes one 32-bit hash state and one 32-bit message word, applies the Murmur3 body mix for one 4-byte block, and produces the next 32-bit hash state. The computation is pure integer arithmetic with 32-bit wraparound at every arithmetic stage.

## Interface

### RTL interface

```systemverilog
module data_systems_hash_murmur3_32_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_h,
    input  logic [31:0] in_k,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_h
);
```

### Behavior interface

```python
class DataSystemsHashMurmur332Step:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_h, in_k):
        pass
```

Each `process(in_h, in_k)` call represents one accepted input transaction.
Both arguments are unsigned 32-bit integers. Return one dictionary with
exactly the key `out_h` and an unsigned 32-bit integer value:

```python
{"out_h": ...}
```

Return this shape even for zero inputs, not `None` or a list. The model has
no hidden chaining state: supply `in_h` on every call. `reset()` has no
algorithmic effect, returns `None` and produces no response.

### Protocol and Timing

Transfers occur on rising clock edges with reset inactive and the corresponding
`valid && ready` true. Responses preserve request order. Input valid/payload
remain stable while waiting for input ready; output valid/payload remain stable
under output backpressure. A simultaneous output transfer and new input
transfer is permitted. No fixed cycle latency is required. Reset discards any
pending response, which must not reappear after reset.

## Modules

- `data_systems_hash_murmur3_32_step`: top-level module implementing one MurmurHash3 32-bit body step.

## Functional Behavior

All inputs and outputs are 32-bit unsigned integers. Every addition, multiplication, rotation, and xor result is reduced modulo `2^32`.

Constants:
- `C1 = 32'hcc9e2d51`
- `C2 = 32'h1b873593`
- `C3 = 32'h00000005`
- `C4 = 32'he6546b64`

For each accepted input transaction, compute:

1. Interpret `in_k` as the already loaded 32-bit block word `k`. For message
   bytes `b0,b1,b2,b3` in increasing address order, the caller supplies
   `k = b0 + (b1<<8) + (b2<<16) + (b3<<24)` (little-endian block loading).
2. Compute `k1 = (k * C1) mod 2^32`.
3. Compute `k2 = rol32(k1, 15)`, where `rol32(x, r)` is a 32-bit rotate-left by exactly `r` bit positions:
   - `rol32(x, r) = ((x << r) | (x >> (32 - r))) mod 2^32`
   - Here `r = 15`.
4. Compute `k3 = (k2 * C2) mod 2^32`.
5. Compute `h1 = in_h xor k3`.
6. Compute `h2 = rol32(h1, 13)`.
7. Compute `h3 = (h2 * C3 + C4) mod 2^32`.
8. Drive `out_h = h3`.

There is exactly one output transaction for each accepted input transaction.

This block performs only the Murmur3 body step. It does not perform tail handling, length xor, or finalization (`fmix`).

There is no hidden chaining state: the caller supplies `in_h` for every block.
To process successive complete blocks of one message, feed each returned
`out_h` into the next request. Independent messages may instead supply their
own seeds. This block alone does not return a complete message hash.

### Source boundary

The selected operation is the body loop of Austin Appleby's
`MurmurHash3_x86_32`, in `aappleby/smhasher` at commit
`0ff96f7835817a27d0487325b6c16033e2992eb5`, `src/MurmurHash3.cpp`.
The legacy Faiss Hamming-header attribution was unrelated and is not evidence
for this operation. Block loading is the explicit little-endian rule above;
unaligned memory access and host byte order are not part of the hardware API.

Worked example:
- If `in_h = 32'h00000000` and `in_k = 32'h00000000`, then:
  - `k1 = 0`
  - `k2 = 0`
  - `k3 = 0`
  - `h1 = 0`
  - `h2 = 0`
  - `h3 = 32'he6546b64`
  - therefore `out_h = 32'he6546b64`.

## Reset Behavior

Reset is synchronous active-high `rst`.

The block has no architectural state.

While `rst` is asserted:
- `in_ready = 1`
- `out_valid = 0`
- `out_h = 32'h00000000`

After reset is deasserted, the module continues to have no architectural state. Output values during cycles without an `out_valid` handshake are unconstrained except where required by the handshake protocol.
