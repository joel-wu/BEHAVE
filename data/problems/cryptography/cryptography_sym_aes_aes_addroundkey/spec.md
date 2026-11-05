# AES AddRoundKey

AES AddRoundKey computes the bytewise exclusive-or of a 128-bit AES state and a 128-bit round key. The block is a stateless 1:1 transform corresponding to the AES AddRoundKey step only; it does not perform SubBytes, ShiftRows, MixColumns, key expansion, or round selection.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_aes_aes_addroundkey (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_state,
    input  logic [127:0] in_round_key,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_state
);
```

### Behavior interface

```python
class CryptographySymAesAesAddroundkey:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_state, in_round_key): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_state": ...}`, whose value is a Python integer holding the packed
output port bit pattern, not a list of state bytes.

## Modules

- `cryptography_sym_aes_aes_addroundkey`: top-level module implementing the 128-bit AddRoundKey transform.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

`in_state` is a 128-bit unsigned bit pattern carrying the AES state.
`in_round_key` is a 128-bit unsigned bit pattern carrying the 16-byte AES round key for the same round.
`out_state` is a 128-bit unsigned bit pattern.

The output is computed as:

- `out_state = in_state XOR in_round_key`

where XOR is bitwise exclusive-or applied independently to all 128 bit positions.

Equivalently, if `in_state` and `in_round_key` are viewed as 16 bytes
`state[15:0]` and `round_key[15:0]`, then for every byte index `i` in `[0, 15]`:

- `out_byte[i] = state_byte[i] XOR round_key_byte[i]`

and `out_state` is the concatenation of those 16 result bytes in the same byte positions as the inputs.

There is no signed arithmetic, no saturation, no carry propagation, no byte reordering, and no dependence on any prior transaction. All 128 output bits are fully determined by the corresponding input bits from the same accepted input transaction.

## Reset Behavior

Reset is synchronous active-high.

The block has no architectural state.

After reset, and until an input transaction is accepted, the functional contents of the output datapath are:

- `in_ready = 1`
- `out_valid = 0`
- `out_state = 128'h00000000000000000000000000000000`

Because the block is stateless, reset does not alter any stored key, state, or mode bits.
