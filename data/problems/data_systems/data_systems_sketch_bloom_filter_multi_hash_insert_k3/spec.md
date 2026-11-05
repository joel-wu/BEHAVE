# Bloom Filter Insert and Membership Query K3

Maintain a 1024-bit Bloom filter using three double-hashed bit positions. Each
request either inserts a key or queries it. The response exposes both the
pre-update membership result and the complete post-update bitset, so omitting
the persistent updates is observably incorrect.

## Interface

### RTL interface

```systemverilog
module bloom_filter_multi_hash_insert_k3 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_key,
    input logic in_insert,
    output logic out_valid,
    input logic out_ready,
    output logic [9:0] out_idx0,
    output logic [9:0] out_idx1,
    output logic [9:0] out_idx2,
    output logic out_present,
    output logic [1023:0] out_bits
);
```

### Behavior interface

```python
class BloomFilterMultiHashInsertK3:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_key, in_insert): ...
```

Each call represents one accepted insert or query and returns one dictionary
with exactly the keys `out_idx0`, `out_idx1`, `out_idx2`, `out_present` and
`out_bits`. All values are unsigned integers: `in_key` has 32 bits, `in_insert`
and `out_present` are in 0..1, each index has 10 bits, and `out_bits` has
1024 bits. Membership describes the pre-update filter; `out_bits` contains the
post-update filter as specified below. Every call returns the complete
dictionary, including negative queries, never `None` or a list. Handshake
signals are not returned. The filter persists between calls; construction and
`reset()` clear it to zero. `reset()` returns `None` and produces no response.

### Protocol and Timing

Transfers occur at rising clk edges with rst inactive and valid && ready true
on the respective endpoint. Each accepted request produces one ordered response.
State changes only on input acceptance, never merely because input pins change.
Both producers hold valid and payload stable while stalled. The output captures
the result for that request, including when a previous response is consumed
on the same edge. No fixed response latency is required.

## Modules

`bloom_filter_multi_hash_insert_k3` implements the public interface and operation specified here.

## Functional Behavior

Let `F` be the persistent 1024-bit bitset, initially zero. Define `mix32(x)` by
the following operations, with multiplication reduced modulo 2^32 and all
shifts logical:

1. `x = x xor (x >> 16)`.
2. `x = x * 0x85ebca6b`.
3. `x = x xor (x >> 13)`.
4. `x = x * 0xc2b2ae35`.
5. Return `x xor (x >> 16)`.

Compute `a=mix32(in_key)`, `b=mix32(in_key xor 0x27d4eb2f)`, and
`idx[i]=(a+i*b) mod 1024` for i=0,1,2. These are `out_idx0..2`. Set
`mask=(1<<idx[0]) | (1<<idx[1]) | (1<<idx[2])`.

`out_present` is one exactly when **all three bits were set before this
request**, equivalently `(F & mask)==mask`. It indicates possible membership,
not proof that this exact key was inserted; collisions may yield positives.
If `in_insert=1`, update `F=F | mask`; if zero, leave `F` unchanged. Return
the resulting F in `out_bits`, bit k representing filter position k.
Repeated indices set the same bit once. Repeated insertions are idempotent.
Queries never change the bitset, and unrelated previous bits are preserved.

### Adaptation boundary

This is the Bloom insertion/query and double-hashing mechanism described by
Kirsch and Mitzenmacher (2008), not the unrelated legacy Faiss Hamming header.
The exact 32-bit mixers, salt, 1024 bits and k=3 are explicit task choices;
`mix32` is Appleby's MurmurHash3 `fmix32`, not a complete message hash.
No asymptotic false-positive guarantee is asserted for this fixed mixer pair.
The bitset output makes the finite state transition directly testable.

## Reset Behavior

Reset is synchronous active-high rst. A reset edge clears all persistent state,
pending responses, output payloads and out_valid. No pre-reset response may
reappear. Output payload
values outside reset when out_valid=0 are not significant.
