# thermostat

A clocked thermostat with prioritized temperature selection and a sticky fault.

## Interface

### RTL interface

```systemverilog
module thermostat (
    input logic i_clk, i_rst,
    input logic [5:0] i_temp_feedback,
    input logic i_fan_on, i_enable, i_fault, i_clr,
    output logic o_heater_full, o_heater_medium, o_heater_low,
    output logic o_aircon_full, o_aircon_medium, o_aircon_low, o_fan,
    output logic [2:0] o_state
);
```

### Behavior interface

```python
class Thermostat:
    def __init__(self): ...
    def reset(self): ...
    def process(self, i_temp_feedback, i_fan_on, i_enable, i_fault, i_clr): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_aircon_full`, `o_aircon_low`, `o_aircon_medium`, `o_fan`, `o_heater_full`,
  `o_heater_low`, `o_heater_medium`: 1.
- `o_state`: 3.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `i_clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `thermostat`: the complete native module above.

## Functional Behavior

At each rising edge, sample all controls and register the resulting state and
actuator outputs together. Between edges the outputs hold; only reset acts
asynchronously. A new `i_fault=1` latches the fault, taking priority over clear.
Otherwise `i_clr=1` clears it. A latched fault or `i_enable=0` forces AMBIENT and
turns off all seven actuators, including the manual fan. Clear selects AMBIENT
for that edge; on the following edge normal temperature selection resumes.

Without those overrides, select the first asserted feedback bit in priority
order 5, 4, 3, 0, 1, 2. Their states are respectively HEAT_FULL=2, HEAT_MED=1,
HEAT_LOW=0, COOL_FULL=6, COOL_MED=5, COOL_LOW=4. No asserted bit selects
AMBIENT=3. This priority also defines mixed hot/cold input patterns.
Exactly the selected heat/cool output is one; the other five are zero.
The fan is on when a heat/cool output is on or `i_fan_on=1`, unless disabled or
faulted. The state output always reports the state encoding, not an actuator:
AMBIENT is 3, including during reset, disable and fault.

There is no ready/valid interface or output backpressure. Disabling moves the
state to AMBIENT; it does not preserve a previous heating/cooling state.

## Reset Behavior

`i_rst` is asynchronous, active low. Assertion immediately restores the initial state. Fault latch=0, state=AMBIENT (3), and all seven actuator outputs=0.
After reset release, resume the edge-by-edge behavior above.
