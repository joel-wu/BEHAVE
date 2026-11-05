# door_lock

Native seven-phase configurable decimal password lock with lockout and administration.

## Interface

### RTL interface

```systemverilog
module door_lock #(parameter int PASSWORD_LENGTH=4, parameter int MAX_TRIALS=3)(
    input logic clk, srst,
    input logic [3:0] key_input,
    input logic key_valid, confirm, admin_override, admin_set_mode,
    input logic [PASSWORD_LENGTH*4-1:0] new_password,
    input logic new_password_valid,
    output logic door_unlock, lockout
);
```

### Behavior interface

```python
class DoorLock:
    def __init__(self, PASSWORD_LENGTH=4, MAX_TRIALS=3): ...
    def reset(self): ...
    def process(self, key_input, key_valid, confirm, admin_override, admin_set_mode, new_password, new_password_valid): ...
```

Each `process(...)` call returns one dictionary with exactly the keys
`door_unlock` and `lockout`, both unsigned one-bit values observed immediately
BEFORE the rising edge of `clk`, then advances that edge. All seven arguments
are required on every call, including idle edges. They are unsigned port bit
patterns with the widths declared above. There is no ready/valid handshake,
and an idle edge still returns both fields, not `None` or a list.

Reuse the same instance to preserve the phase, password, partial entry, failure
count and registered outputs. `reset()` restores the stated initial state,
including stored password 1, and returns `None` without a logical record.
The runtime controls reset separately from ordinary calls.

## Modules

- `door_lock`: the complete native module above.

## Functional Behavior

PASSWORD_LENGTH and MAX_TRIALS are positive integers (defaults 4 and 3).
The seven phases are IDLE, ENTER_PASS, CHECK_PASS, PASSWORD_OK, PASSWORD_FAIL,
LOCKED_OUT and ADMIN_MODE. Outputs are registers; unspecified inputs in a phase
are ignored. Each edge normally clears door_unlock to zero.

IDLE clears the partial entry. Priority: admin_override enters ADMIN_MODE if
admin_set_mode is also one, otherwise PASSWORD_OK; either clears failures.
Otherwise confirm is an empty failed attempt. Otherwise a valid digit 0..9
captures the FIRST digit and enters ENTER_PASS. ENTER_PASS appends valid digits
until PASSWORD_LENGTH; extra digits and values 10..15 are ignored. Confirm has
priority over a coincident digit and tests the pre-edge digit count: a complete
entry enters CHECK_PASS, an incomplete entry PASSWORD_FAIL. Digits are packed
MSB-first, so 1,2,3,4 represents 0x1234. CHECK_PASS compares the full packed entry
against the stored password and enters PASSWORD_OK or PASSWORD_FAIL.

An edge executed in PASSWORD_OK sets door_unlock=1, clears lockout and failures,
and returns to IDLE. Thus a successful confirm edge e gives CHECK_PASS after e,
PASSWORD_OK after e+1, and a one-cycle unlock pulse after e+2. PASSWORD_FAIL
increments failures; the MAX_TRIALS-th failure sets lockout and enters LOCKED_OUT,
otherwise returns to IDLE. LOCKED_OUT ignores keys and confirm; admin_override
enters PASSWORD_OK even if admin_set_mode is one. On the following edge unlock
pulses and lockout clears. Admin entry requires a later override+set in IDLE.

ADMIN_MODE waits for new_password_valid, copies the entire packed new_password,
then returns to IDLE. This write edge is distinct from the edge entering admin
mode. Any packed value is stored; a nibble above nine cannot be entered through
the decimal keypad. Synchronous reset returns to IDLE, sets stored password to
the right-aligned value 1, and clears all other state and outputs.

Explicit source completions: simultaneous confirm/key uses pre-edge digits;
empty confirm fails; admin requests only act in IDLE/LOCKED_OUT as in the source
transition table. The table and registered pulse timing take precedence over
the source test checker's immediate, persistent unlock Boolean. No ready/valid
wrapper, extra reset polarity change, or instantaneous seven-phase collapse
is introduced.

## Reset Behavior

`srst` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
