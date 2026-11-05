# circuit8

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module TopModule(input logic clock_level, a, output logic p, q);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, clock_level, a): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `p`, `q`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Input-level observations

Inputs are applied as explicit physical levels, then all settled output pins
are observed. Repeated identical inputs create separate observations but no
new signal transition. No automatic periodic clock is inserted between
observations. All input-level sequences are defined.

## Modules

- `TopModule`: the actual latch or edge-triggered circuit below.

## Functional Behavior

The circuit consists of a clock-high transparent latch p and a
falling-edge register q. While clock_level=1, p follows a on every input change.
While clock_level=0, p holds. At each 1-to-0 clock_level transition, q captures
the last high-phase value of p. q otherwise holds, including all high-phase
data changes. A new a supplied on the falling transition does not overwrite p
and therefore does not affect that capture. Merely repeating a high level must
not clock q, and changing a during a low level must not change either output.

### Source inconsistency and explicit completion

The source waveform at 115 ns changes a to 0 but requires p=q=1; at 175 ns it
changes a to 1 but requires p=q=0. Its reference instead writes q<=a at the
falling edge, which conflicts with those simultaneous-change observations.
This task follows the supplied waveform and makes the rule q<=p explicit.
The archived reference replay applies exactly this correction; unchanged-source
equivalence is NOT claimed on those cases.

The source clock input is renamed clock_level and supplied as physical levels.
The RTL retains the actual latch and negedge storage, not a posedge emulation.
Each input record changes its fields together, then the digital circuit settles;
include every level/data change to be observed. Initial clock_level,p,q are
explicitly zero, completing unspecified startup state. There is no added clock,
reset or handshake. Analog glitches and pre-settle transients are not specified.

## Reset Behavior

There is no RTL reset pin. Each fresh RTL execution starts with the stated zero
initialization.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
