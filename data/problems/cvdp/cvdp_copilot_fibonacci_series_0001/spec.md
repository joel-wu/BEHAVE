# fibonacci_series

Generate all representable 32-bit Fibonacci values and automatically restart after overflow.

## Interface

### RTL interface

```systemverilog
module fibonacci_series (
    input logic clk, rst,
    output logic [31:0] fib_out,
    output logic overflow_flag
);
```

### Behavior interface

```python
class FibonacciSeries:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `fib_out`: 32.
- `overflow_flag`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
A no-argument process() call still advances one clock edge.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `fibonacci_series`: the complete native module above.

## Functional Behavior

The sequence is F(0)=0, F(1)=1, F(n)=F(n-1)+F(n-2), so the output after
reset is 0 and successive released rising edges produce 1,1,2,3,5,... .
Maintain A=0 and B=1 at reset. On a normal edge publish OLD B and compute A+B
in 33 bits. If it fits, replace (A,B) with (B,A+B). Otherwise retain the pair
and set a pending flag, allowing F(47)=2971215073 to be published on that edge.
At the next edge, publish 0 with overflow_flag=1 and restore (A,B)=(0,1).
The following edge publishes 1 with overflow_flag=0 and resumes normally.
Overflow is a one-cycle restart indication, not a repeated last-value record.
The normal flag is zero. This resolves the source's inconsistent example in
favor of its recurrence, OLD-B rule and test sequence; both initial ones remain.

## Reset Behavior

`rst` is asynchronous, active high. Assertion immediately restores the initial state. A=0, B=1, output=0 and both overflow flags=0. Active-high asynchronous reset is the explicit reset-timing completion.
After reset release, resume the edge-by-edge behavior above.
