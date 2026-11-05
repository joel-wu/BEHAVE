# 24-hour Clock Counter

Maintain a 24-hour time counter. Every rising edge of `CLK` represents one second; no frequency divider or handshake is part of this module.

## Interface

### RTL interface

```systemverilog
module calendar (
    input logic CLK,
    input logic RST,
    output logic [5:0] Hours,
    output logic [5:0] Mins,
    output logic [5:0] Secs
);
```

### Behavior interface

```python
class Calendar:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

One argument-free `process()` call represents one non-reset rising edge.
It returns exactly `{"Hours": old_Hours, "Mins": old_Mins, "Secs": old_Secs}`
sampled immediately before that edge, then updates the retained time. All three
fields are unsigned six-bit integers, within the ranges defined below.
Construction and `reset()` restore `00:00:00`; the first subsequent call
returns that full dictionary, not `None` or a list. Updated time is observed
on the next call. `reset()` returns `None` and advances no second; physical
reset edges produce no observation records.

## Modules

- `calendar`: the top-level native clocked module shown above.

## Functional Behavior

The unsigned outputs continuously expose hours `0..23`, minutes `0..59`, and seconds `0..59`. Each edge increments the time by one second. Seconds wrap at 59 and carry into minutes; minutes wrap at 59 and carry into hours; hours wrap from 23 to 0. All carries use the same pre-edge time, so `23:59:59` becomes `00:00:00` on one edge. Each output uses six bits, including `Hours`; unused encodings do not occur after reset.

## Reset Behavior

`RST` is asynchronous and active high. It immediately sets `Hours`, `Mins`, and `Secs` to zero.
