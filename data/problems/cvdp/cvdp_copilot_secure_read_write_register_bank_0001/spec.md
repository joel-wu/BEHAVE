# secure_read_write_register_bank

Capture-pulse register bank with two consecutive unlock writes and protected user storage.

## Interface

### RTL interface

```systemverilog
module secure_read_write_register_bank #(
    parameter int p_address_width=8,
    parameter int p_data_width=8,
    parameter int p_unlock_code_0=171,
    parameter int p_unlock_code_1=205
) (
    input logic i_capture_pulse, i_rst_n,
    input logic [p_address_width-1:0] i_addr,
    input logic [p_data_width-1:0] i_data_in,
    input logic i_read_write_enable,
    output logic [p_data_width-1:0] o_data_out
);
```

### Behavior interface

```python
class SecureReadWriteRegisterBank:
    def __init__(self, p_address_width=8, p_data_width=8, p_unlock_code_0=171, p_unlock_code_1=205): ...
    def reset(self): ...
    def process(self, i_addr, i_data_in, i_read_write_enable): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o_data_out`: p_data_width.

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

- `secure_read_write_register_bank`: the complete native module above.

## Functional Behavior

i_capture_pulse IS the clock. Every rising edge performs one captured operation:
i_read_write_enable=0 writes,1 reads. Outputs are registered and hold between
capture/reset edges. The source's p_addr_width port-width spelling is resolved
to its declared parameter p_address_width; no new independent clock or
ready/valid signals are added.

Addresses0 and1 are write-only unlock controls: reading either always returns
zero. While locked, all other reads return zero and writes have no effect.
Unlocking requires a correct code0 write to address0 immediately followed at
the NEXT capture edge by a correct code1 write to address1. A read, unrelated
write or wrong second operation breaks that pending pair. Another correct
code0 write restarts it. This interprets the source's contradictory 'sequential'
and 'concurrent' wording as consecutive capture operations; it is not an
arbitrarily delayed two-key sequence.

After unlocking, reads/writes to addresses>=2 access the stored data. Every
write produces o_data_out=0. A wrong code written to either control address
locks the bank again; correct control writes do not revoke existing access.
Relocking preserves data but hides it until a fresh valid pair unlocks the
bank. Control addresses do not expose readable storage. These choices make
the source's ambiguous post-unlock and 'reset/lock' wording explicit.

As a deterministic reset completion, assertion of i_rst_n clears the data
array as well as lock/sequence state and output. The source explicitly resets
the unlock mechanism but does not specify stored-data preservation; no claim
of a mandatory upstream data-clear policy is made. Parameters are
1<=p_address_width<=16,1<=p_data_width<=32 and each unlock code fits that data
width. Defaults are8,8,0xAB,0xCD. A one-bit address domain has only the two
control locations and therefore no readable user data.

## Reset Behavior

`i_rst_n` is asynchronous, active low. Assertion immediately restores the initial state. All memory words and output data are zero; locked, with no pending unlock pair.
After reset release, resume the edge-by-edge behavior above.
