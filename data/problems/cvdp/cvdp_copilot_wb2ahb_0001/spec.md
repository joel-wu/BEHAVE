# cvdp_copilot_wb2ahb_0001

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module wishbone_to_ahb_bridge (
    input logic clk_i_level, rst_i, cyc_i, stb_i,
    input logic [3:0] sel_i,
    input logic we_i,
    input logic [31:0] addr_i, data_i,
    input logic hclk_level, hreset_n,
    input logic [31:0] hrdata,
    input logic [1:0] hresp,
    input logic hready,
    output logic [31:0] data_o,
    output logic ack_o, err_o,
    output logic [1:0] htrans,
    output logic [2:0] hsize, hburst,
    output logic hwrite,
    output logic [31:0] haddr, hwdata
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, clk_i_level, rst_i, cyc_i, stb_i, sel_i, we_i, addr_i, data_i, hclk_level, hreset_n, hrdata, hresp, hready): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `ack_o`, `err_o`, `hwrite`: 1.
- `data_o`, `haddr`, `hwdata`: 32.
- `hburst`, `hsize`: 3.
- `htrans`: 2.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

Supply both clock levels and reset levels explicitly on every call. A held level is
not an edge. reset() starts a fresh execution; it is not called for each record.

### Input-level observations

Inputs are applied as explicit physical levels, then all settled output pins
are observed. Repeated identical inputs create separate observations but no
new signal transition. No automatic periodic clock is inserted between
observations. All input-level sequences are defined.

## Modules

- `wishbone_to_ahb_bridge`: the stateful digital circuit below.

## Functional Behavior

Translate one outstanding Wishbone request to one non-pipelined AHB
SINGLE transfer across independently advancing clocks. Restore the source
request, response and AHB bus pins; no ready/valid wrapper is added. The
physical source clocks clk_i and hclk are renamed clk_i_level and hclk_level.
The RTL uses actual positive edges; repeated/held levels are not new edges.
Every observed input change is supplied explicitly. Both clocks can stop,
advance separately or rise together. Coincident edges read common PRE-edge
mailbox/synchronizer values. This specifies digital CDC sequencing, not
metastability or physical synchronizer timing.

### Source ambiguities completed explicitly

The source supplies no answer RTL. It asks for endian conversion but its test
expects unchanged words, and it tests completion before a dual-clock handshake
could finish. This task selects little-endian Wishbone and big-endian AHB
byte lanes with explicit whole-word byte reversal. It is NOT unchanged-testbench
or unspecified original-implementation equivalence. The source's fixed 32-bit
ports and four selects are retained; transfer size, not bus width, is variable.

Add one output err_o because the source has no way to report an invalid select
or non-OKAY slave response without falsely acknowledging a successful write.
This is a disclosed interface completion. hresp=00 means OKAY; 01/10/11 terminate
as an error. RETRY/SPLIT retrying, bursts, arbitration and locks are outside this
single-transfer subset. htrans is IDLE=00 or NONSEQ=10. BUSY/SEQ are never issued:
an address-phase stall HOLDS NONSEQ and its attributes, rather than converting
the outstanding transfer to BUSY as the previous task incorrectly required.

### Wishbone capture and lane mapping

When idle, a rising clk_i_level with cyc_i=stb_i=1 captures sel_i, we_i,
addr_i and data_i. Only one request can be outstanding. Changes to those fields
while pending are ignored. A request is not made when either cyc_i or stb_i is
zero. The master normally holds them through acknowledgement.

Legal sel_i values are 0001,0010,0100,1000 (bytes); 0011,1100 (halfwords);
and 1111 (word). Let L be the lowest selected lane and M the 32-bit mask with
0xff in each selected Wishbone lane. Set hsize to 0,1,2 for 1,2,4 selected bytes;
haddr=(addr_i & 0xfffffffc)+L. The select determines the byte offset; the input
address's low two bits are ignored. hwrite=we_i, hburst=0 and
hwdata=byte_reverse_32(data_i & M), held even for a read. No selected byte is
shifted to a different Wishbone lane. On an OKAY read the return value is
byte_reverse_32(hrdata) & M. Writes and errors return data_o=0.

Every other select (including zero and non-contiguous lanes) gives one local
err_o pulse and data_o=0 with NO AHB transfer. It must not turn into a full-word
write. The following Wishbone edge clears this response without capturing a
new request; the next idle edge can capture another one.

### CDC and AHB phases

Request capture toggles a one-bit request mailbox and holds its complete data
bundle until the round trip finishes. On EVERY rising hclk_level, including
idle/wait cycles, a two-register synchronizer shifts that toggle. The AHB FSM
uses the OLD second stage. When idle and the visible toggle differs from the
last completed toggle, latch the bundle onto AHB and enter the address phase
with htrans=NONSEQ. The edge that launches this phase cannot accept it.

On a later AHB edge with hready=1, the address phase ends and htrans becomes
IDLE for the data phase. Address/size/write/data stay held. Only a FURTHER AHB
edge with hready=1 completes the data phase, captures hrdata/hresp and publishes
the matching completion toggle. With hready=0 either phase holds indefinitely;
hrdata/hresp are ignored before data completion. AHB outputs other than htrans
retain the last descriptor until a later launch or reset.

The completion toggle passes through two registers on EVERY Wishbone rising
edge. The Wishbone FSM also uses the OLD second stage. Once it equals the
pending request toggle, release the pending request and retain its data_o.
If cyc_i=stb_i=1 then pulse ack_o for OKAY or err_o for error, never both. A
withdrawn cyc_i/stb_i suppresses both pulses but does NOT abort an already sent
AHB operation. There is no timeout or synthesized success while a clock stops.
The response lasts until the next Wishbone edge; that edge clears it and does
not accept new work. The following idle edge may capture a back-to-back request.

### Reset

Either active-low reset clears both domains asynchronously, including request
and completion toggles, both synchronizers, FSMs, data_o/ack_o/err_o and every
AHB output. An outstanding AHB transfer is cancelled by that shared reset.
No stale pre-reset response may emerge later. Releasing reset with a held-high
clock does not execute a step. All registers and initial clock levels are zero
on fresh execution, so every finite input-level sequence has a defined result.


## Reset Behavior

Both source resets are active low. Either rst_i=0 or hreset_n=0 asynchronously resets BOTH bridge domains, discarding in-flight work and clearing mailboxes and outputs. They must both be high for operation. Release alone is not a clock edge. Fresh state and the two initial clock levels are zero.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
