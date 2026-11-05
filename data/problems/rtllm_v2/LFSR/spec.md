# 4-bit XNOR Feedback Shift Register

Advance a four-bit linear-feedback shift register on every rising clock edge, with XNOR feedback from bits 3 and 2.

## Interface

### RTL interface

```systemverilog
module LFSR (
    input logic clk,
    input logic rst,
    output logic [3:0] out
);
```

### Behavior interface

```python
class Lfsr4:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

One call represents one non-reset rising edge. It returns `{"out": old_out}` sampled immediately before that edge, then updates the state. The updated state is observed on the next call. Reset edges produce no observation records.

The return value is always one dictionary with exactly the key `out`. Its value
is an integer in 0..15, not a bit string or a sequence. The zero state is a valid
observation and returns `{"out": 0}`, not `None`. `reset()` restores state zero.

## Modules

- `LFSR`: the top-level native clocked module shown above.

## Functional Behavior

The current unsigned state is exposed by `out`. At each rising edge, compute `feedback = ~(out[3] ^ out[2])` as one bit, then set `out_next = {out[2:0], feedback}`.

The reset-started sequence is `0,1,3,7,14,13,11,6,12,9,2,5,10,4,8,0,...`. It has period 15 and includes zero; `4'hF` is not in that cycle. There is no input-data transaction or valid/ready handshake.

## Reset Behavior

`rst` is asynchronous and active high. It immediately clears `out` to zero.
