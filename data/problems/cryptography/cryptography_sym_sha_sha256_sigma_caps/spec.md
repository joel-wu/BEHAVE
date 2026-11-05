# SHA-256 Sigma Caps

This module computes the two SHA-256 uppercase sigma functions on one 32-bit input word. It is a stateless 1:1 transform intended for use inside the SHA-256 compression round function. The computation is exactly the FIPS 180-4 SHA-256 definitions: `SIGMA_0_256(x) = ROTR32(2,x) ^ ROTR32(13,x) ^ ROTR32(22,x)` and `SIGMA_1_256(x) = ROTR32(6,x) ^ ROTR32(11,x) ^ ROTR32(25,x)`.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_sha_sha256_sigma_caps (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_sigma0,
    output logic [31:0] out_sigma1
);
```

### Behavior interface

```python
class CryptographySymShaSha256SigmaCaps:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x): ...
```

`process()` returns a dict with keys:
- `out_sigma0`
- `out_sigma1`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sigma0`, `out_sigma1`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cryptography_sym_sha_sha256_sigma_caps`: top-level module that accepts one 32-bit word and produces the two SHA-256 uppercase sigma values for that word.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

All values are 32-bit unsigned integers. Every rotation and XOR result is reduced modulo `2^32`.

Define:
- `ROTR32(n, x) = ((x >> n) | (x << (32 - n))) mod 2^32`

The input transaction contains:
- `in_x`: 32-bit unsigned word

For each accepted input transaction, compute:

- `r2  = ROTR32(2,  in_x)`
- `r13 = ROTR32(13, in_x)`
- `r22 = ROTR32(22, in_x)`
- `r6  = ROTR32(6,  in_x)`
- `r11 = ROTR32(11, in_x)`
- `r25 = ROTR32(25, in_x)`

Then compute:
- `out_sigma0 = (r2 ^ r13 ^ r22) mod 2^32`
- `out_sigma1 = (r6 ^ r11 ^ r25) mod 2^32`

Equivalent bit-exact definitions:
- `out_sigma0 = SIGMA_0_256(in_x)`
- `out_sigma1 = SIGMA_1_256(in_x)`

There is no signed interpretation, no saturation, no exceptional case, and no internal accumulation. Every one of the `2^32` possible `in_x` values is legal.

Worked example:
- If `in_x = 0x12345678`:
  - `ROTR32(2,  in_x)  = 0x048D159E`
  - `ROTR32(13, in_x)  = 0xB3C091A2`
  - `ROTR32(22, in_x)  = 0xD159E048`
  - `out_sigma0        = 0x66146474`
  - `ROTR32(6,  in_x)  = 0xE048D159`
  - `ROTR32(11, in_x)  = 0xCF02468A`
  - `ROTR32(25, in_x)  = 0x1A2B3C09`
  - `out_sigma1        = 0x3561ABDA`

## Reset Behavior

Reset is active-high synchronous or asynchronous at the implementer's choice.

The module has no architectural state.

While reset is asserted:
- `in_ready = 0`
- `out_valid = 0`
- `out_sigma0 = 0`
- `out_sigma1 = 0`

After reset is deasserted, the module may accept input transactions and produce outputs according to the handshake protocol.
