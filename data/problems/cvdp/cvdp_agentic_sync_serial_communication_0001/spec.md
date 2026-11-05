# sync_serial_communication_tx_rx

Repeated synchronous serial TX/RX transfers with Gray conversion of completed receive data.

## Interface

### RTL interface

```systemverilog
module sync_serial_communication_tx_rx (
    input logic clk, reset_n,
    input logic [63:0] data_in,
    input logic [2:0] sel,
    output logic [63:0] data_out,
    output logic done,
    output logic [63:0] gray_out
);
```

### Behavior interface

```python
class SyncSerialCommunicationTxRx:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in, sel): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`, `gray_out`: 64.
- `done`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `sync_serial_communication_tx_rx`: native top-level loopback.
- `tx_block`: gated-clock bit serializer.
- `rx_block`: bit receiver.
- `binary_to_gray_conversion`: received-word Gray encoding.

## Functional Behavior

The native pins have no request handshake or output backpressure. A valid
sel selects the low 8,16,32,64 bits for values 1,2,3,4 respectively. Other values
select no transfer. On an eligible rising clk edge L, capture data_in and the
width. Transfer one bit LSB first at each of edges L+1 through L+N, where N is
the captured width. The receiver zero-extends the reconstructed word. data_out
changes only after edge L+N and otherwise holds; done is high from that edge
until L+N+1, exactly one clk period. Changes to data_in or sel while busy do not
change the captured transfer. Inputs at idle edges determine the next transfer.

The internal serial_clk runs at clk frequency during those N bit edges and is
low while idle. Gate control changes on falling clk edges so it cannot truncate
a high pulse. tx_block performs the serialization; rx_block receives the actual
bit stream. No serial-bit records or fabricated serial-clock-level output are
added to the native top-level ports.

The exact load-to-first-bit delay and idle-low serial-clock choice resolve source
timing omissions. Inputs on a capture edge are stable before it. Reset discards
partial transfers, clears output/state and leaves the link idle. This task does
not require a physical clock-gating cell implementation.

The first valid idle edge after reset starts a transfer. After completion the
next edge may load another word, including when sel remains high. This resolves
the source's inconsistent initial-done example: done resets LOW, but startup
does not wait for a previously completed receive operation. Invalid selections
do not silently request an 8-bit transfer. This is an explicit completion of
the omitted invalid-selection rule.

gray_out is the combinational 64-bit Gray conversion data_out ^ (data_out>>1).
It changes with the completed received word and holds between completions.
This follows the source's combinational converter definition; the conflicting
description of a separate enabled capture does not introduce an extra cycle.


## Reset Behavior

`reset_n` is asynchronous, active low. Assertion immediately restores the initial state. Data, done, Gray output where present, partial word and bit counters are zero; no transfer is active.
After reset release, resume the edge-by-edge behavior above.
