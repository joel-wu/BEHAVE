# Ready/valid bit register

## Interface

### RTL interface

```systemverilog
module handshake_register (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic in_d,
    output logic out_valid,
    input logic out_ready,
    output logic out_q
);
```

### Behavior interface

```python
class BitRegister:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_d): ...
```

## Functional Behavior

Every accepted input transaction supplies one unsigned bit `in_d` and produces
exactly one output transaction with `out_q` equal to that bit. Outputs remain
in input order. The implementation stores at most one pending output and may
consume it and accept its replacement on the same rising clock edge. Valid and
the output bit remain stable until the output is consumed.

## Reset Behavior

The active-high asynchronous `rst` clears both the pending-output flag and the
stored bit. No pending output survives reset. Before any post-reset input,
`out_valid` is zero and `out_q` is zero.
