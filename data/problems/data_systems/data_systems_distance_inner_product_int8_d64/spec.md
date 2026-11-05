# InnerProductInt8D64

Compute the exact inner-product distance 1 minus the dot product of two 64-element signed-int8 vectors.

## Interface

### RTL interface

```systemverilog
module data_systems_distance_inner_product_int8_d64 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [511:0] in_vec_a,
    input  logic [511:0] in_vec_b,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [20:0]  out_distance
);
```

### Behavior interface

```python
class DataSystemsDistanceInnerProductInt8D64:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_vec_a, in_vec_b): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_distance": ...}`. Arguments and the returned field are Python integers
holding raw unsigned port bit patterns. The result uses the 21-bit output
encoding, including for a negative signed result.

Behavior reset establishes the same architectural starting state; its timing and pending pin transfers are represented by the interface.

## Modules

- `data_systems_distance_inner_product_int8_d64`: the complete operator and its input/output handshake.

## Functional Behavior

Lane i occupies bits [8*i +: 8] of each packed input, with lane 0 least significant. Interpret each byte as two's-complement int8 and compute the mathematical integer

    d = 1 - sum(a[i] * b[i] for i in 0..63).

Return d as a signed 21-bit two's-complement value. Products and partial sums are exact; there is no truncation to 16 bits, saturation, or normalization. The dot product lies in [-1040384,1048576], and the distance lies in [-1048575,1040385].

Zero vectors produce 1. Two vectors filled with -128 produce -1048575; a vector filled with -128 against one filled with 127 produces 1040385. These distances must remain distinguishable for nearest-neighbor ranking.

Input transfers occur on a rising clock edge when in_valid and in_ready are both 1; outputs transfer when out_valid and out_ready are both 1. The source holds every input field and in_valid until acceptance. The design holds every output field and out_valid until consumption. Each accepted input transaction produces exactly one output transaction, in input order. No fixed latency or internal pipeline is required. With reset inactive and the sink accepting outputs, pending work must eventually complete. Reset cancels pending work.

## Reset Behavior

Synchronous, active-high rst. Reset clears out_valid and visible output payloads to zero and restores an input-ready empty state. There is no retained computational state between requests.
