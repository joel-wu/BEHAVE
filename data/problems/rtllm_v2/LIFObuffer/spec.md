# LIFObuffer

Four-word four-bit LIFO with enable-qualified clear and held pop data.

## Interface

### RTL interface

```systemverilog
module LIFObuffer (input logic Clk, init_reset, EN, clear, RW, input logic [3:0] dataIn, output logic EMPTY, FULL, output logic [3:0] dataOut);
```

### Behavior interface

```python
class LifoBuffer:
    def __init__(self): ...
    def reset(self): ...
    def process(self, dataIn, RW, EN, clear): ...
```

Each call returns all settled output pin values immediately BEFORE one rising
edge of `Clk`, then advances that edge. Arguments and outputs are unsigned port bit patterns.
Every call represents a clock edge, including idle cycles.
Reset is controlled separately and produces no logical records.

The return value is one `dict` with exactly `EMPTY`, `FULL`, and `dataOut`.
The flags are Python integers in 0..1 and `dataOut` is an integer in 0..15.
Return all three fields even for disabled edges, rejected operations and zero
data; do not use `None`, an empty list or a list of pin records. The stack and
held pop value persist across calls. `reset()` restores the initialization
state described below without advancing an ordinary input edge.

## Modules

- `LIFObuffer`: the complete native module above.

## Functional Behavior

This is a four-entry stack of four-bit unsigned words. On a
rising Clk edge with EN=0, hold ALL state and outputs, including when clear=1.
On an enabled edge, clear has priority: erase the stack, set EMPTY=1, FULL=0
and dataOut=0. Otherwise RW=0 pushes dataIn unless full; RW=1 pops the newest
word unless empty. Overflow pushes and underflow pops leave occupancy unchanged.
A successful pop returns and removes the newest word and clears its storage.
EMPTY/FULL reflect the resulting occupancy immediately after that edge.
On every enabled non-pop edge (including rejected operations), dataOut becomes
zero. On a disabled edge it retains the previous value, including a popped word.
No output register or queue beyond this retained dataOut is added.

### Disclosed source completions and interface adaptation

The source Rst is EN-qualified, so it is renamed ordinary input `clear`, not
treated as the environment's unconditional initialization reset. Added
synchronous active-high init_reset clears the entire stack and dataOut and sets
EMPTY=1,FULL=0 irrespective of EN. With init_reset=0, preserve source every-edge
execution and EN qualification. The source omits assigning FULL in its reset
branch; define FULL=0 there to agree with the empty stack. Its X-valued non-pop
dataOut is completed as zero. Startup before initialization is unspecified.
These are explicit two-state/interface completions, not unchanged pin compatibility.

## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `Clk` with reset asserted restores the initial state. EMPTY=1, FULL=0, dataOut=0; all four stored words are zero.
After reset release, resume the edge-by-edge behavior above.
