# Compression Hash Fast XXHash 64 Round

This module implements one XXH64 round function from the classic 64-bit xxHash algorithm. For each accepted transaction, it takes a 64-bit accumulator lane and one 64-bit little-endian input word, performs the xxHash round transform `acc += input * PRIME64_2; acc = rotl64(acc, 31); acc *= PRIME64_1;`, and returns the updated 64-bit accumulator. All arithmetic is unsigned modulo 2^64.

## Interface

### RTL interface

```systemverilog
module compression_hash_fast_xxhash_64_round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_acc,
    input  logic [63:0]  in_input,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_acc
);
```

### Behavior interface

```python
class CompressionHashFastXxhash64Round:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_acc, in_input):
        pass
```

Each `process(in_acc, in_input)` call represents one accepted request and
returns exactly `{"out_acc": value}`. Both arguments and `value` are unsigned
64-bit integers. The accumulator is supplied explicitly, not retained between
calls. Every request, including all-zero input, returns one dictionary, never
`None` or a list. `reset()` returns `None` and emits no result.

## Modules

- `compression_hash_fast_xxhash_64_round`: top-level module implementing one XXH64 round transform.

## Functional Behavior

Constants:
- `PRIME64_1 = 0x9E3779B185EBCA87`
- `PRIME64_2 = 0xC2B2AE3D27D4EB4F`

Each accepted input transaction computes one output transaction as follows.

Interpret `in_acc` and `in_input` as unsigned 64-bit integers.

1. Multiply:
   - `product = in_input * PRIME64_2`
   - Keep only the low 64 bits:
   - `product64 = product mod 2^64`

2. Add:
   - `sum64 = (in_acc + product64) mod 2^64`

3. Rotate left by 31 bits:
   - `rot64 = ((sum64 << 31) | (sum64 >> 33)) mod 2^64`

4. Multiply by the second prime:
   - `result = (rot64 * PRIME64_1) mod 2^64`

5. Drive output:
   - `out_acc = result`

The operation is purely a 64-bit modular transform. There is no signed arithmetic, no saturation, and no dependence on previous transactions.

Bit ordering and endianness:
- The input port `in_input` already carries the 64-bit word value to be mixed.
- No byte reordering is performed inside this block.
- If a larger system sources `in_input` from memory bytes, that larger system is responsible for presenting the correct 64-bit value.

Worked example:
- If `in_acc = 0x0000000000000000` and `in_input = 0x0000000000000000`:
  - `product64 = 0x0000000000000000`
  - `sum64 = 0x0000000000000000`
  - `rot64 = 0x0000000000000000`
  - `out_acc = 0x0000000000000000`

## Reset Behavior

Reset is active-high and synchronous. On reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_acc = 0`

This block has no architectural state beyond the handshake/output register contents required by the chosen RTL implementation. After reset is deasserted, each accepted input transaction produces exactly one output transaction equal to the function defined above.
