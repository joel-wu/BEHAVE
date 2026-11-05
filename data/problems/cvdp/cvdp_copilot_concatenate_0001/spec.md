# enhanced_fsm_signal_processor

Capture and concatenate six input words through an IDLE/PROCESS/READY/FAULT controller, retaining the result until acknowledgement and supporting fault recovery.

## Interface

### RTL interface

```systemverilog
module enhanced_fsm_signal_processor (
    input logic i_clk, i_rst_n, i_enable, i_clear, i_ack, i_fault,
    input logic [4:0] i_vector_1, i_vector_2, i_vector_3,
    input logic [4:0] i_vector_4, i_vector_5, i_vector_6,
    output logic o_ready, o_error,
    output logic [1:0] o_fsm_status,
    output logic [7:0] o_vector_1, o_vector_2, o_vector_3, o_vector_4
);
```

### Behavior interface

```python
class EnhancedFsmSignalProcessor:
    def __init__(self): ...
    def reset(self): ...
    def process(self, i_enable, i_clear, i_ack, i_fault, i_vector_1, i_vector_2, i_vector_3, i_vector_4, i_vector_5, i_vector_6): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_error`, `o_ready`: 1.
- `o_fsm_status`: 2.
- `o_vector_1`, `o_vector_2`, `o_vector_3`, `o_vector_4`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `i_clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `enhanced_fsm_signal_processor`: the complete native module with the ports and parameters above.

## Functional Behavior

States are IDLE=0, PROCESS=1, READY=2 and FAULT=3. `o_fsm_status`
always exposes the current state; `o_ready` is 1 exactly in READY and `o_error`
is 1 exactly in FAULT. After reset, data and status are zero.

On every rising edge, `i_fault` has precedence over all other controls: enter
FAULT and clear data. Otherwise apply the rule for the current state:

- IDLE: `i_enable=1` enters PROCESS; otherwise remain IDLE.
- PROCESS: capture the SIX CURRENT input words, concatenate
  `{i_vector_1,...,i_vector_6,2'b11}`, and enter READY. The four output bytes
  are its bits 31:24, 23:16, 15:8 and 7:0, respectively.
- READY: retain all four bytes and ready until an edge with `i_ack=1`, then
  enter IDLE and clear the bytes. Changing inputs cannot change a pending result.
- FAULT: remain until `i_clear=1` with `i_fault=0`, then enter IDLE with zero data.

`i_clear` is a fault-recovery control and has no effect outside FAULT. `i_ack`
has no effect outside READY; `i_enable` has no effect outside IDLE. If enable
stays high, a new operation starts on the edge AFTER acknowledgement. A request
seen in IDLE at edge n enters PROCESS after n, captures data and enters READY
after n+1; READY is visible at the pre-edge observation n+2. No delayed status register
or extra handshake is present.

## Reset Behavior

`i_rst_n` is asynchronous and active low. Assertion immediately clears all state, pending work and output pins to zero.
