# event_scheduler

A clock-driven 16-event priority scheduler with atomic add/cancel/trigger updates.

## Interface

### RTL interface

```systemverilog
module event_scheduler (
    input logic clk, reset, add_event, cancel_event,
    input logic [3:0] event_id,
    input logic [15:0] timestamp,
    input logic [3:0] priority_in,
    output logic event_triggered,
    output logic [3:0] triggered_event_id,
    output logic error,
    output logic [15:0] current_time
);
```

### Behavior interface

```python
class EventScheduler:
    def __init__(self): ...
    def reset(self): ...
    def process(self, add_event, cancel_event, event_id, timestamp, priority_in): ...
```

Call `process` once per released rising edge, supplying all five arguments
even when both request flags are zero. Return one dictionary with exactly
`event_triggered` (1 bit), `triggered_event_id` (4 bits), `error` (1 bit), and
`current_time` (16 bits). Arguments and fields are unsigned integers of their
port widths. The dictionary observes the pre-edge pins, then the call advances
state; it is never `None` or a list. Reuse the instance across idle and request
edges. `reset()` clears its scheduler state, returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each released rising edge
of `clk`, then advance that edge. Inputs and outputs are unsigned port bit
patterns. Idle cycles are clock edges too. Reset is controlled separately and
produces no logical records.

## Modules

- `event_scheduler`: the complete native module above.

## Functional Behavior

The source's fixed configuration is retained: 16 event slots, 16-bit
timestamps, 4-bit priorities, and a time increment of 10 ns per rising clock
edge. current_time is a numeric timestamp; 10 ns is its unit, not a simulator
delay statement. Time advances EVERY non-reset edge, including edges without
add/cancel requests, wrapping modulo 65536. Use unsigned ordinary timestamp
comparison, not modular-age ordering across wrap.

At an edge, first form updated time, copy event arrays to temporary state, and
apply add then cancel. An add to a free ID records timestamp/priority and marks
it active; add to an active ID reports error without replacement. Cancel of an
active ID removes it; cancel of a free ID reports error. Explicit simultaneous
operation completion: BOTH validity tests use pre-edge occupancy. Thus on a
free ID add+cancel retains the new event and reports cancel error; on an active
ID it removes the event and reports add error. Error is their OR.

Select from the updated temporary set those timestamps <= updated time. Emit
at most one event, choosing greatest priority and then LOWEST ID (explicit tie
completion); remove that event. A newly added already-due event may trigger at
the same edge. A canceled event cannot trigger. Priority zero is eligible.
Commit time, arrays and output flags atomically. event_triggered and error
describe that edge, clearing at the next edge unless a new event/error occurs;
consecutive triggers may keep event_triggered high over successive cycles.
triggered_event_id is explicitly zero without a trigger. No handshakes, output
queues or request-paced clock are added. Reset is defined synchronously active
high, completing the source's unspecified assertion timing.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
