# axis_to_uart_tx

Clock-timed UART transmitter with native AXI-stream word input.

## Interface

### RTL interface

```systemverilog
module axis_to_uart_tx #(
    parameter int CLK_FREQ=100,
    parameter int BIT_RATE=115200,
    parameter int BIT_PER_WORD=8,
    parameter int PARITY_BIT=0,
    parameter int STOP_BITS_NUM=1
) (
    input logic aclk, aresetn,
    input logic [BIT_PER_WORD-1:0] tdata,
    input logic tvalid,
    output logic tready, TX
);
```

### Behavior interface

```python
class AxisToUartTx:
    def __init__(self, CLK_FREQ=100, BIT_RATE=115200, BIT_PER_WORD=8, PARITY_BIT=0, STOP_BITS_NUM=1): ...
    def reset(self): ...
    def process(self, tdata, tvalid): ...
```

Each call returns all settled output pin values immediately BEFORE one rising
edge of `aclk`, then advances that edge. Supply `tdata` as a Python integer in
[0, 2**BIT_PER_WORD - 1] and `tvalid` as 0 or 1 on every call. Return exactly
one dictionary with keys `tready` and `TX`, both integers 0 or 1, on idle and
busy edges alike; do not return `None` or a list. The captured frame and timing
state persist across calls. `reset()` restores idle state and returns no
response. Reset is controlled separately and produces no logical records.

## Modules

- `axis_to_uart_tx`: the complete native module above.

## Functional Behavior

Parameters: integer CLK_FREQ is in MHz (1..2000); BIT_RATE is a positive
integer; BIT_PER_WORD is 1..32; PARITY_BIT is 0 (none), 1 (odd), or 2 (even);
STOP_BITS_NUM is 1 or 2. Let P=floor(CLK_FREQ*1000000/BIT_RATE); require
2 <= P <= 2147483647. These finite limits and integer rounding complete the
source's unspecified parameter domain without changing its defaults (P=868).
Bit order is LSB first. Parity is XOR of the captured data bits for even mode,
its inverse for odd mode. Serial bits are real P-clock intervals, not logical
ready/valid bit transactions. There is no serial backpressure.

tready=1 only when idle and TX=1 when idle. At an edge e with tvalid AND tready,
latch tdata and start driving the start bit immediately after e. Drive zero
for P full clock intervals, then each data bit for P intervals, then optional
parity and the configured one/two high stop bits, each for P intervals. Thus
the bit at position j begins immediately after edge e+j*P. After the final
stop period return to idle; the next transfer can be accepted on the next edge.
Changes to tdata/tvalid while busy do not affect the captured frame. There is
no queue and no accept on the edge that first returns to idle. A compliant
AXI-stream sender holds a pending word stable until its handshake; the receiver
ignores all busy-cycle pin changes deterministically.

This makes the source's otherwise ambiguous TX-register/FSM phase precise:
TX changes only at clock/reset edges, and the start bit begins at the accept
edge, with no extra hidden cycle. An interrupted frame is discarded on reset.


## Reset Behavior

`aresetn` is asynchronous, active low. Assertion immediately restores the initial state. The transmitter is idle: tready=1, TX=1; no frame is pending.
After reset release, resume the edge-by-edge behavior above.
