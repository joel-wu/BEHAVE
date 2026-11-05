# V27 Convolutional Byte-Stream Encoder

Encode framed bytes with the rate-1/2, constraint-length-7 convolutional code.
The encoder preserves bit history across input bytes, emits coded bytes as it
goes, and appends zero termination and byte padding at each frame boundary.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_fec_fec_conv (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_data,
    input  logic       in_last,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);
```

Input and output transfers occur on rising edges with valid and ready both
high, outside reset. A producer holds valid and its payload until transfer;
in particular, pending output data must remain stable under backpressure.
All input bit patterns are legal. Output bytes remain in generation order.
Latency and internal buffering are not prescribed.

### Behavior interface

```python
class SignalProcessingCommFecFecConv:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_data, in_last): ...
```

Each `process` call consumes one accepted input byte, not a clock cycle.
Supply `in_data` as an unsigned 8-bit integer and `in_last` as 0 or 1.
It always returns an ordered list of dictionaries, each with exactly one
field `out_data` whose value is an unsigned 8-bit integer: two dictionaries
when `in_last=0`, four when `in_last=1`. It never returns `None`, an empty list
or a bare dictionary. Keep one instance across the frame's calls to retain
encoder history; a final call clears that history for the next frame.
Construction initializes zero history; `reset()` clears it, returns `None`
and produces no output record.

## Modules

- `signal_processing_comm_fec_fec_conv`: encoder and ordered byte-stream transport.

## Functional Behavior

A frame contains one or more input bytes; only its final byte has `in_last=1`.
The first frame begins after reset, and each final byte closes its frame.

Let `s` be the unsigned 7-bit shift register, initially zero. Process each
payload byte most-significant-bit first. For every payload bit `x`:

1. Set `s = ((s << 1) | x) & 0x7F`.
2. Emit `parity(s & 0x6D)`, then `parity(s & 0x4F)`.

Here parity is the XOR of the seven bits. Bit zero of each polynomial selects
the newly inserted bit. Neither coded bit is inverted. This fixes the
polynomial orientation and output order, without relying on a code-name
convention.

Pack coded bits most-significant-bit first into consecutive bytes. Each input
byte generates 16 coded bits, hence two output bytes; non-final bytes must
not be suppressed or held until the frame ends. Preserve `s` across them.

After a final payload byte, encode six additional zero input bits with the
same rule. Append four literal zero bits (not encoded input bits) to complete
the final byte, then clear `s`. Thus the final input produces its two payload
bytes plus two termination bytes. An M-byte frame produces exactly
`2*M + 2` bytes. There is no empty-frame command. The last output byte's low
four bits are always zero. Adjacent frames have independent zero-initialized
encoder histories.

For example, a one-byte all-zero frame produces four zero bytes. This example
does not replace the parity rule for nonzero data.

## Reset Behavior

`rst` is active high. Reset may be synchronous or asynchronous. It clears the
encoder history and cancels every pending output byte and partial frame.
After reset, `in_ready=1`, `out_valid=0`, and `out_data=0`. The next accepted
byte starts a new frame; no pre-reset output may reappear.
