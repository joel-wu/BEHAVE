# hebbian_rule

Native eleven-state Hebbian trainer for a four-sample bipolar logic-gate epoch.

## Interface

### RTL interface

```systemverilog
module hebbian_rule (
    input logic clk, rst, start,
    input logic signed [3:0] a, b,
    input logic [1:0] gate_select,
    output logic signed [3:0] w1, w2, bias,
    output logic [3:0] present_state, next_state
);
```

### Behavior interface

```python
class HebbianRule:
    def __init__(self): ...
    def reset(self): ...
    def process(self, start, a, b, gate_select): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `bias`, `next_state`, `present_state`, `w1`, `w2`: 4.

Process inputs a and b and returned w1, w2 and bias are signed Python integers in
[-8,7], not unsigned encodings. start is 0 or 1, gate_select is in [0,3], and
present_state and next_state use unsigned four-bit integers.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `hebbian_rule`: the complete native module above.

## Functional Behavior

Train exactly four externally supplied samples per start, using the source's
Hebbian updates w1+=a*t, w2+=b*t, bias+=t. The intended bipolar sequence is
(+1,+1), (+1,-1), (-1,+1), (-1,-1). Inputs a/b are captured only in state1;
the environment presents the next sample on each visit. No input/output
ready-valid wrapper is present. Once started, the FSM advances on every clock,
regardless of start. All eleven states are observable, not completion labels.

State numbers, actions on the leaving edge, and successors are:

| State | Action | Successor |
| --- | --- | --- |
| 0 | If start=1, clear weights/bias and sample count; latch gate_select | 1 if start, otherwise0 |
| 1 | Capture signed a and b | 2 |
| 2 | Dispatch according to the latched gate | 3+gate_select |
| 3 | Register the AND target as +1/-1 | 7 |
| 4 | Register the OR target as +1/-1 | 7 |
| 5 | Register the NAND target as +1/-1 | 7 |
| 6 | Register the NOR target as +1/-1 | 7 |
| 7 | Register the three Hebbian deltas | 8 |
| 8 | Commit weights and bias modulo16 | 9 |
| 9 | Advance sample count if fewer than four samples completed | 1, or10 after the fourth |
| 10 | Preserve the final trained parameters | 0 |

The per-sample path is 1,2,(one of3..6),7,8,9: six clock edges. The target
branches are alternatives, not four extra serial computations per sample.
present_state is the stored FSM state; next_state is its combinational successor
under the current start. In idle, start=0 retains the final weights indefinitely.
A new start resets the parameters for a new four-sample training session. Start
while busy is ignored; held-high start launches another session upon returning
to state0. Gate selection is captured at start and cannot change mid-session.

Gate encodings0/1/2/3 mean AND/OR/NAND/NOR. Treat captured +1 as Boolean true,
all other 4-bit values as false; convert true/false targets to +1/-1. For intended
bipolar samples, one full truth-table pass gives signed (w1,w2,bias):
AND=(2,2,-2), OR=(2,2,2), NAND=(-2,-2,2), NOR=(-2,-2,-2).
For total behavior on other input bit patterns, use their signed 4-bit values in
the same products. Deltas use sufficient width (-8 through+8); stored weights
and bias wrap to four bits, without saturation.

Source completions: the prompt specifies eleven states but omits exact target
substeps, start/capture timing, the iteration limit and restart behavior. The
table fixes these while retaining a genuine four-sample clocked FSM. Hebbian
learning has no prediction-error convergence test here. Correct the source's
bias example typo: 0+(+1)=+1. All state is initialized, not just the state/count.
The requested module name hebbian_rule takes precedence over the empty answer
file's hebb_gates filename. The original answer RTL is empty. Its Cocotb test
supplies the four expected final weight triples above, but uses wall-clock
stimulus durations and an ambiguous reset-edge wait. Retain those arithmetic
expectations with explicit capture-edge scheduling; no unchanged-TB pass is claimed.


## Reset Behavior

`rst` is asynchronous, active low. Assertion immediately restores the initial state. All stored state is zero; present_state=0 and next_state follows start.
After reset release, resume the edge-by-edge behavior above.
