# Traffic Light with Pedestrian Request

Sequence red, green and yellow lights with a countdown and a pedestrian request that can shorten the green phase. Preserve the registered lamp-control pipeline described below.

## Interface

### RTL interface

```systemverilog
module traffic_light (
    input logic clk,
    input logic rst_n,
    input logic pass_request,
    output logic [7:0] clock,
    output logic red,
    output logic yellow,
    output logic green
);
```

### Behavior interface

```python
class TrafficLight:
    def __init__(self): ...
    def reset(self): ...
    def process(self, pass_request): ...
```

One call represents one non-reset rising edge. It returns `{"clock": old_clock, "red": old_red, "yellow": old_yellow, "green": old_green}` sampled immediately before that edge, then updates the state. The updated state is observed on the next call. Reset edges produce no observation records.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `clock`: 8.
- `green`, `red`, `yellow`: 1.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `traffic_light`: the top-level native clocked module shown above.

## Functional Behavior

`pass_request` is sampled on every rising edge. `clock` is an unsigned eight-bit countdown output, not a clock input. The three one-bit lamp outputs continuously expose the registered lights. No handshake is used.

The state consists of a phase (`IDLE`, `RED`, `YELLOW`, or `GREEN`), three pending lamp bits, three output lamp bits, and a countdown. All updates below use pre-edge values and occur together:

1. Copy the pending lamp bits to the output lamp bits.
2. Decode the old phase into the pending bits: `IDLE` selects no lamp and changes phase to `RED`; the other phases select their named lamp. When the old countdown is 3, advance the phase `RED -> GREEN -> YELLOW -> RED`; otherwise retain it.
3. Update the countdown using the first matching rule:
   - If `pass_request = 1`, the old green output is 1, and the countdown is greater than 10, set it to 10.
   - Otherwise, if green is 0 and pending green is 1, load 60.
   - Otherwise, if yellow is 0 and pending yellow is 1, load 5.
   - Otherwise, if red is 0 and pending red is 1, load 10.
   - Otherwise decrement modulo 256.

Without requests, the first three non-reset samples have all lamps off and countdowns `10,9,8`. Red then appears for 10 samples (`10..1`), green for 60 (`60..1`), and yellow for 5 (`5..1`); this 75-sample pattern repeats. A request with a displayed green countdown of 11 makes the next countdown 10, not 9. At 10 or below, or outside green, the request does not alter the ordinary update. Holding a request high does not hold the countdown at 10.

## Reset Behavior

`rst_n` is asynchronous and active low. It immediately selects `IDLE`, sets the countdown to 10, and clears all pending and output lamp bits.
