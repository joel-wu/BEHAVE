# Width Packer 8 to 16

Concatenate consecutive valid bytes into 16-bit words, first byte high. The native interface accepts a byte on every rising edge with `valid_in=1`, with no ready signal or output backpressure. Gaps between bytes do not discard the first byte.

## Interface

### RTL interface

```systemverilog
module width_8to16 (
    input logic clk,
    input logic rst_n,
    input logic valid_in,
    input logic [7:0] data_in,
    output logic valid_out,
    output logic [15:0] data_out
);
```

### Behavior interface

```python
class WidthPacker8to16:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in): ...
```

Each Behavior call consumes one valid unsigned eight-bit byte. It returns `None` for the first byte of a pair, or one mapping `{"data_out": word}` for the second. The word is unsigned 16-bit. Invalid cycles do not call the Behavior or change its pending byte.

Return None for the first valid byte of each pair, then one dictionary for the second.
Never return a list. Reset discards a pending byte.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: 16.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

Call process only for valid_in input transfers, not for output latency or invalid
cycles.

## Modules

- `width_8to16`: a pending-byte register, a pair-phase bit, and a registered output.

## Functional Behavior

At a rising edge with `valid_in=1`, capture the first byte if none is pending. Otherwise register `(pending_byte << 8) | data_in` into `data_out`, clear the pending flag, and assert `valid_out`. At all other rising edges, deassert `valid_out` and retain `data_out`. Each word is valid for the cycle immediately following capture of its second byte and can be observed just before the next rising edge. Continuous valid input produces one word every two cycles.

`data_in` is irrelevant when `valid_in=0`. The last completed word remains on `data_out` while `valid_out=0`; it is not another output transaction. An unpaired byte produces no word and remains pending across arbitrarily many invalid cycles. Output words preserve pair order.

## Reset Behavior

`rst_n` is asynchronous and active low. Assertion clears the pending byte, pending flag, `valid_out`, and `data_out` to zero. An incomplete pair or unobserved word is canceled. After reset, the next valid byte begins a new pair.
