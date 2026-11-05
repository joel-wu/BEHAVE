# axis_joiner

Native three-stream packet joiner with independent input backpressure.

## Interface

### RTL interface

```systemverilog
module axis_joiner (
    input logic clk, rst,
    input logic [7:0] s_axis_tdata_1,
    input logic s_axis_tvalid_1, s_axis_tlast_1,
    input logic [7:0] s_axis_tdata_2,
    input logic s_axis_tvalid_2, s_axis_tlast_2,
    input logic [7:0] s_axis_tdata_3,
    input logic s_axis_tvalid_3, s_axis_tlast_3,
    input logic m_axis_tready,
    output logic s_axis_tready_1, s_axis_tready_2, s_axis_tready_3,
    output logic [7:0] m_axis_tdata,
    output logic m_axis_tvalid, m_axis_tlast,
    output logic [1:0] m_axis_tuser,
    output logic busy
);
```

### Behavior interface

```python
class AxisJoiner:
    def __init__(self): ...
    def reset(self): ...
    def process(self, s_axis_tdata_1, s_axis_tvalid_1, s_axis_tlast_1, s_axis_tdata_2, s_axis_tvalid_2, s_axis_tlast_2, s_axis_tdata_3, s_axis_tvalid_3, s_axis_tlast_3, m_axis_tready): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `busy`, `m_axis_tlast`, `m_axis_tvalid`, `s_axis_tready_1`, `s_axis_tready_2`,
  `s_axis_tready_3`: 1.
- `m_axis_tdata`: 8.
- `m_axis_tuser`: 2.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `axis_joiner`: the complete native module above.

## Functional Behavior

Keep all three independent native AXIS inputs and their ready pins.
Source 1/2/3 has tag 1/2/3. The source calls arbitration 'round-robin' but gives
the explicit priority 1 > 2 > 3; that fixed-priority rule governs here.

In idle select the highest-priority valid source. Its first beat can be
captured on that edge. The first accepted beat selects the packet owner;
keep that owner across input valid gaps and output stalls until its buffered
LAST beat is consumed at the output. Only the selected source's ready may
assert. No beat is accepted without its own valid AND ready.

There is one registered output slot. Except while it holds a last beat, the
selected input is ready exactly when the slot is empty or m_axis_tready=1.
Capture at most one beat while the output is stalled, then deassert ready.
Hold data, last, tag and valid unchanged while stalled. An ordinary beat may
retire and be replaced on the same edge. While a LAST beat is buffered, all
input readies are zero; its output handshake returns to idle. Arbitration
resumes on the following edge. This explicit packet-boundary bubble prevents
early owner release. tlast without a valid/ready transfer has no effect.

busy is live: it is high from first acceptance through last-beat output
consumption, including gaps/stalls. It is not an emitted constant. When the
slot is empty, output data/last/tag/valid are zero. Compliant senders hold a
pending beat stable until granted; inactive inputs cannot modify buffered
data. Reset discards the partial packet, clears outputs and forces readies low.


## Reset Behavior

`rst` is asynchronous, active high. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
