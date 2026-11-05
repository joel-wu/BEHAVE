# LSHSignaturePack

Pack numeric nonnegativity tests on binary32 vector components into an LSH signature, without a rotation or learned threshold.

## Interface

### RTL interface

```systemverilog
module data_systems_vector_search_lsh_signature_pack #(
    parameter int D = 32,
    parameter int NBITS = 32
) (
    input  logic                               clk,
    input  logic                               rst,

    input  logic                               in_valid,
    output logic                               in_ready,
    input  logic [D*32-1:0]                    in_x,

    output logic                               out_valid,
    input  logic                               out_ready,
    output logic [((NBITS + 7) / 8) * 8 - 1:0] out_bytes
);
```

### Behavior interface

```python
class LSHSignaturePack:
    def __init__(self, D=32, NBITS=32): ...
    def reset(self): ...
    def process(self, in_x): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_bytes": ...}`. Arguments and the returned field are Python integers
holding raw unsigned port bit patterns; `out_bytes` is packed, not a Python
list of bytes. Signed interpretation and packing are specified below.

Behavior reset establishes the same architectural starting state; its timing and pending pin transfers are represented by the interface.

## Modules

- `data_systems_vector_search_lsh_signature_pack`: the complete operator and its input/output handshake.

## Functional Behavior

Parameters satisfy 1 <= NBITS <= D. Input component j occupies bits [32*j +: 32] and is an IEEE-754 binary32 bit pattern. Only the first NBITS components participate.

Output bit j is 1 exactly when the numeric comparison x[j] >= 0 is true. Both +0 and -0 yield 1; positive finite values, positive subnormals, and +infinity yield 1. Negative nonzero values and -infinity yield 0. Every NaN, of either sign and with any payload, yields 0. This is a numeric comparison, not merely the complement of the sign bit.

Bit j is stored in byte floor(j/8), at position j mod 8. Unused high bits in the last output byte are zero. For (+0,-0,+1,-1,+infinity,-infinity,NaN,+subnormal), the first output byte is 0x97. Extra input lanes j >= NBITS do not affect the result.

Input transfers occur on a rising clock edge when in_valid and in_ready are both 1; outputs transfer when out_valid and out_ready are both 1. The source holds every input field and in_valid until acceptance. The design holds every output field and out_valid until consumption. Each accepted input transaction produces exactly one output transaction, in input order. No fixed latency or internal pipeline is required. With reset inactive and the sink accepting outputs, pending work must eventually complete. Reset cancels pending work.

## Reset Behavior

Asynchronous, active-high rst. Reset clears out_valid and visible output payloads to zero and restores an input-ready empty state. There is no retained computational state between requests.
