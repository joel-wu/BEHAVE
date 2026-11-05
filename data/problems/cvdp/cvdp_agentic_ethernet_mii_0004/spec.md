# cvdp_agentic_ethernet_mii_0004

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module ethernet_mii_tx (
    input logic clk_in_level, rst_in, axis_clk_in_level, axis_rst_in,
    input logic axis_valid_in,
    input logic [31:0] axis_data_in,
    input logic [3:0] axis_strb_in,
    input logic axis_last_in,
    output wire axis_ready_out,
    output logic [3:0] mii_txd_out,
    output logic mii_tx_en_out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, clk_in_level, rst_in, axis_clk_in_level, axis_rst_in, axis_valid_in, axis_data_in, axis_strb_in, axis_last_in): ...
```

Each call applies one input-level observation as defined below. Arguments are
unsigned integers of the declared widths, with single-bit pins represented by
0 or 1. Return exactly one dictionary with `axis_ready_out` (0 or 1),
`mii_txd_out` (unsigned 4-bit encoding) and `mii_tx_en_out` (0 or 1), containing
all settled outputs even when transmission is idle. Never return `None` or a
list. Reuse the instance for queued frames, partial input and clock history.
Construction and `reset()` restore the initial state and clock history;
`reset()` returns `None` without emitting a record.

### Input-level observations

All pins are unsigned integers of the widths in the RTL interface. Each input
record updates persistent state according to the rules below, and all settled
output pins are observed. Repeated identical records are separate observations
but create no new signal transition. No automatic periodic clock is inserted
between observations. All input-level sequences are defined.

## Modules

- `ethernet_mii_tx`: the stateful digital circuit below.

## Functional Behavior

Transmit complete AXI-stream frames autonomously over four-bit MII.
Restore the source data/control pins; clk_in and axis_clk_in become
clk_in_level and axis_clk_in_level. The RTL retains actual positive-edge
storage in each domain. No MII valid-input, output-ready or requested-beat
wrapper exists. Only MII clock edges advance the wire. Holds do not advance it.

### Frame queue and AXI acceptance

The source requests complete-frame buffering but supplies only a word FIFO,
with a stale Gray read-pointer update and no frame-commit signal. This task
explicitly completes the CDC as a two-slot COMPLETE-FRAME FIFO. Each slot has
1536 byte cells; the maximum accepted frame length remains 1518 bytes. This
changes the internal 512-word FIFO architecture/capacity, not the source
AXI/MII payload function. The provided FIFO is archived, not reused unchanged.
One slot can transmit while the other receives. Backpressure applies once both
slots are committed and not yet returned by the MII side.

At a rising axis_clk_in_level, accept a word iff axis_valid_in and PRE-edge
axis_ready_out are both one. Append selected axis_data_in bytes in ascending
lane order 0..3 according to axis_strb_in. No CRC/preamble is supplied on AXI.
On an accepted axis_last_in, commit that frame and begin a fresh frame on the
next available slot. Stalls, even with valid=1, change no packet data.

For total finite behavior, arbitrary strobe masks are supported: unselected
bytes contribute nothing, including zero-strobe words. Short frames are emitted
without padding, a deliberate extension below the source's normal 64-byte
minimum. Empty frames and frames with more than 1518 selected bytes are DROPPED
as whole frames. An overlong frame continues accepting/discarding through its
LAST; it must not deadlock on its own buffer or emit a truncated valid frame.
There is no transmission before LAST, even if AXI stalls for many MII clocks.

### Digital CDC sequencing

Producer and consumer sequence numbers w,r are modulo four; slot index is the
low bit. Commit increments w once. Consuming/dropping a frame increments r once.
Each number is Gray coded (g=x^(x>>1)) and crosses through two registers on
EVERY destination rising edge, regardless of data activity. Consumers of the
synchronizers use their OLD second stage. AXI is full when w equals the decoded
read sequence plus two modulo four; axis_ready_out is its complement outside
reset. MII is empty when r equals its decoded synchronized write sequence.
Slot data/length stay fixed from commit through return. Coincident clock edges
sample common PRE-edge crossing values. No analog CDC/metastability claim is made.

### MII wire and CRC

At an idle MII edge seeing a committed nonempty valid frame, emit the first
preamble nibble. Emit fifteen consecutive 0x5 nibbles followed by 0xd: seven
0x55 bytes and SFD=0xd5. Then emit exactly the selected payload bytes, each low
nibble before high nibble, followed immediately by four CRC bytes, each also
low nibble first. tx_en remains one throughout this uninterrupted wire frame.
At the final FCS nibble, return the slot to AXI and enter IFG: exactly 24 further
MII edges with tx_en=0,txd=0, before a next frame may start. Idle is also 0,0.
Dropped frames return their slot at one idle MII edge without wire output/IFG.
Output data/enable hold while the MII clock is stopped.

CRC covers only payload. Initialize c=0xffffffff; for each byte b, replace c
by c XOR b, then eight times replace c by (c>>1) XOR 0xedb88320 if its OLD low
bit was one, otherwise c>>1. FCS=(c XOR 0xffffffff), transmitted least-significant
byte first. For ASCII 123456789, FCS=0xcbf43926 and wire bytes are 26 39 f4 cb.
This reflected recurrence agrees with the original TB's zlib.crc32 oracle.
It resolves the source prose's inconsistent final-reversal descriptions; merely
inverting its MSB-oriented CRC and emitting low nibbles, as the old task did,
does not generally give that FCS. One byte consumes TWO MII edges, not the
one-byte-per-cycle sentence elsewhere in the source specification.

### Reset and implementation scope

Either source active-high reset (rst_in or axis_rst_in) asynchronously resets
BOTH domains, aborts any wire/partial/queued frame and clears sequence numbers,
synchronizers, CRC and outputs. Coupling reset is an explicit correction to the
source's undefined unilateral-reset recovery; stale packets cannot reappear.
axis_ready_out is zero during reset and one in the freshly empty released state.
Fresh storage is zero. Existing bytes after reset are inaccessible until a new
frame commits. Releasing reset at a held-high clock creates no edge.
This is a source-inspired transmitter completion, not equivalence to the empty
answer slot, the defective provided FIFO, or all physical Ethernet requirements.


## Reset Behavior

Source rst_in or axis_rst_in asynchronously resets both domains and flushes queued/partial/transmitting frames; see the documented coupled-reset completion. Fresh clock levels are zero.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
