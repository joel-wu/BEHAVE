# microcode_sequencer

Four-bit microcode sequencer with seven operations, immediate reads, and a sixteen-word multi-cycle stack.

## Interface

### RTL interface

```systemverilog
module microcode_sequencer (
    input logic clk, init_reset,
    input logic [4:0] instr_in,
    input logic [3:0] d_in,
    input logic c_n_in, c_inc_in, r_en, cc, ien, oen,
    output logic [3:0] d_out,
    output logic c_n_out, c_inc_out, full, empty
);
```

### Behavior interface

```python
class MicrocodeSequencer:
    def __init__(self): ...
    def reset(self): ...
    def process(self, instr_in, d_in, c_n_in, c_inc_in, r_en, cc, ien, oen): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `c_inc_out`, `c_n_out`, `empty`, `full`: 1.
- `d_out`: 4.

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

- `microcode_sequencer`: the complete native module above.

## Functional Behavior

This is a four-bit microcode sequencer, not a combinational opcode
lookup and not a ready/valid transaction wrapper. It contains a 4-bit program
counter PC, a 4-bit auxiliary register R, and a sixteen-word stack of 4-bit words.
All pins use unsigned bit patterns. The stack occupancy ranges from 0 through
16; empty=(occupancy==0), full=(occupancy==16), independent of output enable.

### Decode and immediate results

An instruction is enabled iff cc=0 and ien=0. With decoding disabled, or an
unsupported opcode, d_out and both carries are zero. An enabled opcode selects:

| instr_in | Operation | Immediate d_out before output masking | Clocked effect when idle |
| --- | --- | --- | --- |
| 0 | PRST | 0 | PC=c_inc_in, clear occupancy and completed POP result; cancel any pending stack operation |
| 1 | Fetch PC | Current PC | PC=(PC+c_inc_in) modulo 16 |
| 2 | Fetch R | Current R | None |
| 3 | Fetch D | Current d_in | None |
| 4 | Fetch R+D | (R+d_in+c_n_in) modulo 16 | None |
| 11 | PUSH PC | 0 | Start the two-edge PUSH below |
| 14 | POP PC | Last completed POP result | Start the three-edge POP below |

All other opcodes have no instruction effect. No branch instruction is invented.
c_n_out is the overflow bit of R+d_in+c_n_in only during enabled Fetch R+D.
c_inc_out is the overflow bit of PC+c_inc_in only during enabled, idle Fetch PC.
The two carries are zero otherwise. Setting oen=1 masks ONLY d_out to zero;
it does not inhibit state changes, either carry, full or empty.
The fast read results respond to current pins without an output pipeline.

Independently of decoding and stack activity, every released rising edge with
r_en=0 writes d_in into R. With r_en=1, R holds. Arithmetic reads the pre-edge R;
the newly captured R is visible immediately after the edge. None of these seven
instructions implicitly writes arithmetic results back to R.

### Stack timing and instruction issue

There is at most one pending stack operation, and no input/output queue. Count
the issue edge as the FIRST edge of an operation:

- PUSH issued on edge k captures PC. Edge k+1 appends that captured word and
  increments occupancy unless full. A rejected full PUSH still consumes both
  edges, does not overwrite any word, and does not change PC.
- POP issued on edge k selects the top word. Edge k+1 reads it. Edge k+2 publishes
  it as the completed POP result and decrements occupancy. A POP issued empty
  also consumes three edges and publishes zero without changing occupancy.
  POP does not assign PC. Removed RAM contents need not be erased.

While pending, further non-PRST instruction state changes are ignored, including
on the completion edge. Work already issued advances regardless of cc, ien and
oen. Independent R capture and immediate read selection remain active. A new
stack instruction can issue on the NEXT edge after completion. Thus a held PUSH
issues every two edges, and a held POP every three edges. The previous completed
POP result remains visible under opcode 14 until another POP completes or PRST
clears it. An enabled PRST always has priority, including while pending: it
cancels the operation before any write, read or commit on that edge. It resets
occupancy but does not erase stack RAM, and R still follows r_en.

### Source completions and interface adaptation

The source supplies no complete decoder control table or answer RTL. Its final
latency requirement says non-stack results are immediate and PUSH/POP take 2/3
cycles, despite a separate registered-output description. This task follows the
immediate-read requirement and explicitly defines edge counting, issue policy,
PC increment on Fetch PC, output masking, busy priority and stack boundaries
above. The original testbench has scheduler-dependent reads and an unexplained
literal POP expectation; those are not additional semantics. The original
50 MHz prose does not define a frequency-dependent digital recurrence; this
task specifies rising-edge behavior without an analog timing requirement.

Added init_reset provides deterministic initialization separately from the
original PRST instruction, which must remain a pointer-only stack clear. All
original data/control ports and the seven opcodes remain; no ready/valid pins
or artificial transaction latency are added. Startup before initialization is
unspecified. These are disclosed completions, not unchanged upstream equivalence.

## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. PC, R, all sixteen RAM words, occupancy, pending pipeline and completed POP result are zero. Thus empty=1 and full=0; combinational reads still depend on their current input controls.
After reset release, resume the edge-by-edge behavior above.
