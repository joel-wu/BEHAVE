# perceptron_gates

Native six-instruction perceptron controller with epoch-wide convergence and a real halt.

## Interface

### RTL interface

```systemverilog
module perceptron_gates (
    input logic clk, rst_n,
    input logic signed [3:0] x1, x2, threshold,
    input logic learning_rate,
    input logic [1:0] gate_select,
    output logic signed [3:0] percep_w1, percep_w2, percep_bias,
    output logic [3:0] present_addr,
    output logic stop,
    output logic [2:0] input_index,
    output logic signed [3:0] y_in, y,
    output logic signed [3:0] prev_percep_wt_1, prev_percep_wt_2, prev_percep_bias
);
```

### Behavior interface

```python
class PerceptronGates:
    def __init__(self): ...
    def reset(self): ...
    def process(self, x1, x2, learning_rate, threshold, gate_select): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `input_index`: 3.
- `percep_bias`, `percep_w1`, `percep_w2`, `present_addr`, `prev_percep_bias`,
  `prev_percep_wt_1`, `prev_percep_wt_2`, `y`, `y_in`: 4.
- `stop`: 1.

Process inputs x1, x2 and threshold and returned percep_w1, percep_w2, percep_bias,
y_in, y, prev_percep_wt_1, prev_percep_wt_2 and prev_percep_bias are signed Python
integers in [-8,7], not unsigned encodings. The other process inputs and returned
fields are nonnegative integers of their declared port widths.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `perceptron_gates`: the complete native module above.

## Functional Behavior

Execute a six-entry microcode sequence after reset release. There is no start,
enable, ready or valid port. present_addr exposes the actual instruction address;
each instruction executes on one rising edge. Weights/bias, y/y_in, previous
updates and control state otherwise hold between their write instructions.

| Address | Action on the edge | Next address |
| --- | --- | --- |
| 0 | Initialize weights, bias, update registers, input_index and epoch flag to0 | 1 |
| 1 | Capture x1/x2, learning_rate and gate_select; compute and register y_in/y using current threshold | 2 |
| 2 | Register the target from captured gate_select and current input_index | 3 |
| 3 | Commit conditional weight/bias updates; record deltas; accumulate epoch-change flag | 4 |
| 4 | If input_index=3 and the whole epoch had zero updates, set stop | 5 |
| 5 | If not stopped, advance input_index modulo4, clear epoch flag after index3 | 1 |

Once stop is1, ALL registers and outputs freeze, including present_addr=5 and
input_index=3. Only reset resumes execution. Do not keep modifying the model
after announcing a halt. Instruction0 runs once after reset, not each sample.
One epoch consists of four samples, indices0..3. Present bipolar inputs in order
(+1,+1), (+1,-1), (-1,+1), (-1,-1) at the respective address1 capture edges.
Changes to input pins at other instruction addresses do not change that sample.

The target table (signed values, indices0..3) is:

| gate_select | Gate | Targets |
| --- | --- | --- |
| 0 | AND | +1,-1,-1,-1 |
| 1 | OR | +1,+1,+1,-1 |
| 2 | NAND | -1,+1,+1,+1 |
| 3 | NOR | -1,-1,-1,+1 |

Correct the source's duplicated NAND/OR and NOR/AND tables using the named
Boolean gates. Target lookup depends on input_index, not inferred x1/x2; the
environment is responsible for the intended training order. Captured gate
selection may vary per sample, though a normal epoch trains one fixed gate.

At address1, interpret x1/x2, weights, bias and threshold as signed 4-bit values.
Compute the exact sum S=bias+x1*w1+x2*w2 with nine signed bits. Report y_in=S
modulo16. Compare the UNTRUNCATED S with signed threshold: y=+1 if greater,
-1 if less, and0 if equal. The reported y_in may have a different sign after
truncation; it is not the value used for the comparison.

At address3, if y differs from the stored target and captured learning_rate=1,
the deltas are (captured_x1*target, captured_x2*target, target); otherwise all
three deltas are0. Add them to weights/bias modulo16. The prev_percep_* outputs
record these most recent deltas modulo16, following the source waveform rather
than its conflicting phrase 'previous weights'. Zero-delta updates clear those
outputs; they are not sticky. Full 4-bit x inputs are totalized by these same
signed equations, although the intended training domain is bipolar.

Convergence means no nonzero delta anywhere in a complete four-sample epoch,
not two consecutive equal deltas or one already-correct sample. This follows
the source's worked example: AND, rate1, threshold0 ends epoch1 at (1,1,-1),
then halts at the end of epoch2 with unchanged weights. With learning_rate=0,
the first epoch has no updates and halts even if predictions are incorrect.
For arbitrary threshold/data, convergence is not guaranteed; no artificial
iteration cap or forced successful stop is introduced.

Source completions: instruction timing/capture points, reset initialization,
width/truncation and halt behavior are explicit above. Weights and y_in can
take any signed 4-bit value, contrary to the source's bipolar-only output claim.
Remove the legacy extra target output and restore present_addr and rst_n.
The original answer RTL is empty. Its Cocotb test logs outputs without checking
correctness and changes gates without resetting a halted model. The source's
worked example instead supplies independent expected weights. Each new training
session resets the controller; no unchanged-TB pass or RTL equivalence is claimed.


## Reset Behavior

`rst_n` is asynchronous, active low. Assertion immediately restores the initial state. All registers and outputs are zero, including present_addr, input_index and stop.
After reset release, resume the edge-by-edge behavior above.
