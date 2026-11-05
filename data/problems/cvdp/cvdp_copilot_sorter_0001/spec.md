# sorting_engine

sorting_engine with its native clocked interface.

## Interface

### RTL interface

```systemverilog
module sorting_engine #(parameter int N = 8, parameter int WIDTH = 8) (
    input logic clk, rst, start,
    input logic [N*WIDTH-1:0] in_data,
    output logic done,
    output logic [N*WIDTH-1:0] out_data
);
```

### Behavior interface

```python
class SortingEngine:
    def __init__(self, N=8, WIDTH=8): ...
    def reset(self): ...
    def process(self, start, in_data): ...
```

Call `process` at every non-reset rising edge of `clk`, including when
`start=0`. It returns one dictionary `{"done": ..., "out_data": ...}` with
the settled pin values immediately before that edge, then advances the state.
Arguments and returned fields are Python integers holding unsigned port bit
patterns. A zero-valued observation is still a record, not `None`.
Call `reset` separately; it produces no output record.

## Modules

- `sorting_engine`: the complete native module above.

## Functional Behavior

`N>=1`, `WIDTH>=1`, defaults 8 and 8. Element i occupies bits
`[i*WIDTH +: WIDTH]`, with element zero least significant. An idle start=1 edge
captures the complete vector. Use IDLE, SORTING and DONE states and bubble sort:
perform N passes, each comparing adjacent pairs j=0 through N-2, one comparison
per edge. Swap only when the left unsigned element is greater. Thus there are
exactly N*(N-1) comparison edges, not an early-exit or decreasing-length schedule.

The next edge publishes the sorted packed vector, pulses done for one cycle and
returns to IDLE. Including capture and publication, latency is N*(N-1)+2 edges.
For N=1 there are zero comparisons and publication is on the edge after capture.
Ignore start and input changes while SORTING or DONE. A new operation can begin
at the edge following publication. Output holds the preceding result until the
next publication, including while a new vector sorts.

Explicit output-domain refinement: reset defines out_data=0 instead of leaving
it uninitialized as in the source. Subsequent reset also clears it. This changes
only the source's previously undefined/reset output contract, not sorting or
its timing, and is recorded as a task adaptation rather than source equivalence.

## Reset Behavior

`rst` is asynchronous, active high. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
