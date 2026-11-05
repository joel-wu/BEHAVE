# packet_controller

Receive and validate eight-byte requests; serialize five-byte arithmetic responses paced by byte completion.

## Interface

### RTL interface

```systemverilog
module packet_controller (
    input logic clk, rst, rx_valid_i,
    input logic [7:0] rx_data_8_i,
    input logic tx_done_tick_i,
    output logic tx_start_o,
    output logic [7:0] tx_data_8_o
);
```

### Behavior interface

```python
class PacketController:
    def __init__(self): ...
    def reset(self): ...
    def process(self, rx_valid_i, rx_data_8_i, tx_done_tick_i): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `tx_data_8_o`: 8.
- `tx_start_o`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `packet_controller`: the complete native module above.

## Functional Behavior

Receive exactly eight bytes, in arrival order, on rising edges with
rx_valid_i=1 while IDLE. A low strobe does not insert a byte. The packet bytes
are BA, CD, num1_hi, num1_lo, num2_hi, num2_lo, opcode, checksum. Both operands
are unsigned 16-bit values. Require the header BACD and the sum of all eight
bytes modulo 256 to be zero. For opcode 0 compute addition modulo 65536;
for opcode 1 compute subtraction modulo 65536; otherwise the result is zero.
The five response bytes are AB, CD, result_hi, result_lo and the unique checksum
that makes their sum modulo 256 zero. Invalid requests produce no transmission.

Preserve the six source FSM phases: IDLE, GOT_8_BYTES, RECV_CHECKSUM,
BUILD_RESPONSE, SEND_FIRST_BYTE and RESPONSE_READY. Let E be the edge that
receives byte 8. Check the header at E+1 and checksum at E+2, build the response
at E+3 and present its first byte with tx_start_o=1 after E+4. A bad header
returns to IDLE after E+1; a bad checksum after E+2. Receive strobes are ignored
on all non-IDLE edges, including preparation and the final transmit completion
edge. There is no receive ready port or buffering of a second packet. The next
packet may start on the first edge whose pre-edge state is IDLE.

tx_start_o is a LEVEL, high throughout transmission, as specified by the source.
While it is high, hold tx_data_8_o until an edge with tx_done_tick_i=1 acknowledges
that byte. After acknowledgement present the next byte; after the fifth clear
both outputs and return to IDLE. Consecutive done edges acknowledge consecutive
bytes. Ignore done while tx_start_o is low, including the first-byte launch edge.
Thus a missing done can stall transmission indefinitely without changing data.

Source completions: byte endianness, modulo overflow/underflow, the exact
one-clock phase schedule, busy receive handling and idle data=0 are fixed here.
The source supplies no implementation or testbench; these completions do not
claim compatibility with an unavailable reference. Do not replace the native
byte strobes/completion signal with a packed 64-bit input or ready/valid wrapper.


## Reset Behavior

`rst` is asynchronous, active high. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
