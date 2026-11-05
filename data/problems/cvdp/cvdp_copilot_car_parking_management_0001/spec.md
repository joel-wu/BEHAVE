# car_parking_system

Track parking occupancy from entry and exit sensor transitions, enforce lot capacity and drive the corresponding count, availability and display outputs.

## Interface

### RTL interface

```systemverilog
module car_parking_system #(
    parameter int TOTAL_SPACES = 12
) (
    input logic clk, reset, vehicle_entry_sensor, vehicle_exit_sensor,
    output logic [$clog2(TOTAL_SPACES+1)-1:0] available_spaces, count_car,
    output logic led_status,
    output logic [6:0] seven_seg_display_available_tens,
    output logic [6:0] seven_seg_display_available_units,
    output logic [6:0] seven_seg_display_count_tens,
    output logic [6:0] seven_seg_display_count_units
);
```

### Behavior interface

```python
class CarParkingSystem:
    def __init__(self, TOTAL_SPACES=12): ...
    def reset(self): ...
    def process(self, vehicle_entry_sensor, vehicle_exit_sensor): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `available_spaces`, `count_car`: $clog2(TOTAL_SPACES+1).
- `led_status`: 1.
- `seven_seg_display_available_tens`, `seven_seg_display_available_units`,
  `seven_seg_display_count_tens`, `seven_seg_display_count_units`: 7.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `car_parking_system`: the complete native module with the ports and parameters above.

## Functional Behavior

`TOTAL_SPACES` is an integer in 1..99. Both count ports have
`ceil(log2(TOTAL_SPACES+1))` bits, including the full-capacity value. The clock
is nominally 100 MHz with 50% duty cycle. Sensors are synchronous to it.

Each low-to-high sensor transition requests one car, including a high on the
first edge after reset. A held high does not request more cars. In IDLE or FULL,
choose an eligible exit first (count > 0), otherwise an eligible entry
(count < TOTAL_SPACES). Enter EXIT or ENTRY on this edge; on the following
edge commit the one-car change and return to IDLE, or FULL when capacity is
reached. Requests arriving on that processing edge are ignored, not queued;
sensor history is still sampled. A new request requires a new rising transition.
Reject entries when full and exits when empty. Simultaneous requests therefore
choose exit when nonempty, entry when empty. No count wraps or saturating extra
entry is reported as a car.

At all times `available_spaces = TOTAL_SPACES-count_car` and `led_status` is 1
exactly when space remains. Display both values as two decimal digits, including
leading zeroes. Segments are active-high in bit order A,B,C,D,E,F,G (MSB first).
Digits 0..9 encode as hexadecimal 7E,30,6D,79,33,5B,5F,70,7F,7B.

## Reset Behavior

`reset` is asynchronous and active high. Assertion immediately empties the lot, clears sensor history and pending work, and enters IDLE. Available spaces become TOTAL_SPACES, count becomes zero, LED is 1, and all four digits encode these values.
