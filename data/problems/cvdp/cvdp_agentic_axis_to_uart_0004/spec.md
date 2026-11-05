# uart_rx_to_axis

Clock-timed UART receiver with parity checking and a native valid-only output.

## Interface

### RTL interface

```systemverilog
module uart_rx_to_axis #(
    parameter int CLK_FREQ=100,
    parameter int BIT_RATE=115200,
    parameter int BIT_PER_WORD=8,
    parameter int PARITY_BIT=0,
    parameter int STOP_BITS_NUM=1
) (
    input logic aclk, aresetn, RX,
    output logic [BIT_PER_WORD-1:0] tdata,
    output logic tuser, tvalid
);
```

### Behavior interface

```python
class UartRxToAxis:
    def __init__(self, CLK_FREQ=100, BIT_RATE=115200, BIT_PER_WORD=8, PARITY_BIT=0, STOP_BITS_NUM=1): ...
    def reset(self): ...
    def process(self, RX): ...
```

Call `process(RX)` at every rising edge, including idle cycles, with `RX`
equal to integer `0` or `1`. Each call returns one dictionary containing all
settled output pin values immediately **before** that edge, then advances
the edge:

```python
{"tdata": ..., "tuser": ..., "tvalid": ...}
```

These are exactly the three keys. `tdata` is an unsigned `BIT_PER_WORD`-bit
integer; `tuser` and `tvalid` are integers `0` or `1`. Return the complete
dictionary even when `tvalid=0`, not `None` or a list. Receiver state and
the held data/error values persist between calls. Reset is controlled
separately: `reset()` restores the specified initial state and returns
`None`, without producing a pin sample or advancing a clock edge.

## Modules

- `uart_rx_to_axis`: the complete native module above.

## Functional Behavior

Parameters: integer CLK_FREQ is in MHz (1..2000); BIT_RATE is a positive
integer; BIT_PER_WORD is 1..32; PARITY_BIT is 0 (none), 1 (odd), or 2 (even);
STOP_BITS_NUM is 1 or 2. Let P=floor(CLK_FREQ*1000000/BIT_RATE); require
2 <= P <= 2147483647. These finite limits and integer rounding complete the
source's unspecified parameter domain without changing its defaults (P=868).
Bit order is LSB first. Parity is XOR of the captured data bits for even mode,
its inverse for odd mode. Serial bits are real P-clock intervals, not logical
ready/valid bit transactions. There is no serial backpressure.

Observe RX at every rising edge, including idle cycles. A sampled falling edge
in IDLE at e begins a frame. Sample the start bit at e+floor(P/2), then sample
each data/parity/stop bit P edges after the previous sample. There is no extra
input synchronizer latency in this digital interface contract. A high start
sample aborts the false start. Data bit i is stored in bit i of tdata.

On the edge sampling the LAST stop bit, if all stop samples were high, update
tdata and tuser and assert tvalid for exactly one clock interval (OUT_RDY),
then return to IDLE. A parity error still delivers the reconstructed word with
tuser=1; with parity disabled tuser=0. Any low stop sample discards that frame
without a pulse and without changing the previous tdata/tuser. Invalid-start
and bad-stop handling are explicit source completions: no framing-error port
is invented, and tuser retains its original parity-only meaning.

tdata/tuser hold between complete frames. Falling edges during an active frame
or OUT_RDY are ignored; RX history is still sampled, so a continuously low line
cannot repeatedly fabricate new starts. Reset initializes that history high,
so a low first released sample starts a frame. There is no tready pin or output
backpressure in the original RX interface, despite its AXIS-like naming.


## Reset Behavior

`aresetn` is asynchronous, active low. Assertion immediately restores the initial state. The receiver is idle; tdata=tuser=tvalid=0 and previous RX=1.
After reset release, resume the edge-by-edge behavior above.
