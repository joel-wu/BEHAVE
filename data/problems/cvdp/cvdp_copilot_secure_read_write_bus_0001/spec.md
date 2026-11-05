# secure_read_write_bus_interface

Secure keyed storage operated by the original capture-pulse clock.

## Interface

### RTL interface

```systemverilog
module secure_read_write_bus_interface #(
    parameter int p_configurable_key=170,
    parameter int p_data_width=8,
    parameter int p_addr_width=8
) (
    input logic i_capture_pulse, i_reset_bar,
    input logic [p_addr_width-1:0] i_addr,
    input logic [p_data_width-1:0] i_data_in,
    input logic [7:0] i_key_in,
    input logic i_read_write_enable,
    output logic [p_data_width-1:0] o_data_out,
    output logic o_error
);
```

### Behavior interface

```python
class SecureReadWriteBusInterface:
    def __init__(self, p_configurable_key=170, p_data_width=8, p_addr_width=8): ...
    def reset(self): ...
    def process(self, i_addr, i_data_in, i_key_in, i_read_write_enable): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_data_out`: p_data_width.
- `o_error`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `i_capture_pulse`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `secure_read_write_bus_interface`: the complete native module above.

## Functional Behavior

i_capture_pulse IS the clock, not an enable sampled by an additional clk.
At every rising capture edge, i_read_write_enable=1 reads and0 writes.
Authorization is exact eight-bit equality i_key_in==p_configurable_key.
An authorized write changes only memory[i_addr] and sets o_data_out=0,o_error=0.
An authorized read registers memory[i_addr] and clears o_error. Either kind of
unauthorized access makes o_data_out=0,o_error=1 and leaves memory unchanged.
Outputs hold between capture/reset edges. There is no ready/valid interface
and no output backpressure; every rising capture pulse is an operation.

p_configurable_key is0..255, p_data_width is1..32 and p_addr_width is1..16.
The memory has2**p_addr_width words, initially cleared by asynchronous reset.
Defaults remain the source's key0xAA and8-bit data/address widths. All addresses
select a valid word. This finite parameter domain and zero result for an
authorized write complete the source's otherwise unspecified details.

## Reset Behavior

`i_reset_bar` is asynchronous, active low. Assertion immediately restores the initial state. All memory words, output data and error are zero.
After reset release, resume the edge-by-edge behavior above.
