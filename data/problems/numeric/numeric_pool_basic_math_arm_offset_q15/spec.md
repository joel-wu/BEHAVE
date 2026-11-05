# Q15 Vector Offset

Q15 Vector Offset adds a signed 16-bit Q15 offset to one signed 16-bit Q15 input sample per accepted transaction and returns the saturated Q15 result for that sample. The computation is a single-element instance of a vector offset kernel: signed addition in 17-bit precision followed by saturation to the Q15 range [-32768, 32767].

## Interface

### RTL interface

```systemverilog
module q15_vector_offset (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_sample,
    input  logic [15:0]  in_offset,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_sample
);
```

### Behavior interface

```python
class Q15VectorOffset:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_sample, in_offset): ...
```

Each `process(in_sample, in_offset)` call represents one accepted input
transaction. Both arguments are unsigned 16-bit integers encoding the signed
Q15 values. Return one dictionary with exactly the key `out_sample`:

```python
{"out_sample": ...}
```

The value is an unsigned 16-bit integer encoding the saturated signed result,
including when the result is negative or zero. Do not return a list or `None`.
There is no persistent algorithmic state. `reset()` returns `None` and
produces no response.

## Modules

- `q15_vector_offset`: top-level module implementing one Q15 sample offset operation per transaction.

## Functional Behavior

For each accepted input transaction, interpret `in_sample` and `in_offset` as 16-bit two's-complement signed integers in Q15 format.

Let:

- `sample = signed(in_sample)`
- `offset = signed(in_offset)`
- `sum17 = sample + offset`

The addition is mathematically exact before saturation. The result is then saturated to the Q15 signed range:

- if `sum17 < -32768`, `result = -32768`
- else if `sum17 > 32767`, `result = 32767`
- else `result = sum17`

The output transaction count is exactly 1 for each accepted input transaction.

`out_sample` is the 16-bit two's-complement bit pattern of `result`.

Worked examples:

- `in_sample = 0x1000`, `in_offset = 0x0100` -> `out_sample = 0x1100`
- `in_sample = 0x7FFF`, `in_offset = 0x0001` -> `out_sample = 0x7FFF`
- `in_sample = 0x8000`, `in_offset = 0xFFFF` -> `out_sample = 0x8000`
- `in_sample = 0xFFFF`, `in_offset = 0x0001` -> `out_sample = 0x0000`

There is no persistent architectural state affecting the arithmetic from one transaction to the next.

## Reset Behavior

Reset is synchronous active-high via `rst`.

On reset:

- `in_ready` shall indicate the module can accept a new input transaction after reset is deasserted.
- `out_valid = 0`
- `out_sample = 16'h0000`

The module has no architectural state other than any implementation-local registers required for handshake or pipelining. Any such state shall be initialized so that no stale output transaction is pending after reset.
