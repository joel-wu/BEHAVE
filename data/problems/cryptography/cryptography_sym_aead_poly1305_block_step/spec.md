# Poly1305 block accumulation and tag preview

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_aead_poly1305_block_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_h0,
    input  logic [31:0]  in_h1,
    input  logic [31:0]  in_h2,
    input  logic [31:0]  in_h3,
    input  logic [2:0]   in_h4,
    input  logic [31:0]  in_r0,
    input  logic [31:0]  in_r1,
    input  logic [31:0]  in_r2,
    input  logic [31:0]  in_r3,
    input  logic [31:0]  in_s0,
    input  logic [31:0]  in_s1,
    input  logic [31:0]  in_s2,
    input  logic [31:0]  in_s3,
    input  logic [127:0] in_msg,
    input  logic [4:0]   in_len,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_h0,
    output logic [31:0]  out_h1,
    output logic [31:0]  out_h2,
    output logic [31:0]  out_h3,
    output logic [2:0]   out_h4,
    output logic [31:0]  out_tag0,
    output logic [31:0]  out_tag1,
    output logic [31:0]  out_tag2,
    output logic [31:0]  out_tag3
);
```

### Behavior interface

```python
class Poly1305BlockStep:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_h0, in_h1, in_h2, in_h3, in_h4, in_r0, in_r1, in_r2, in_r3, in_s0, in_s1, in_s2, in_s3, in_msg, in_len): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_h0`, `out_h1`, `out_h2`, `out_h3`, `out_tag0`, `out_tag1`, `out_tag2`,
  `out_tag3`: 32.
- `out_h4`: 3.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`cryptography_sym_aead_poly1305_block_step` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Limb arrays use radix B=2^32, least significant limb first. h has four 32-bit limbs and a three-bit top limb. Clamp r[0] with 0x0fffffff and r[1..3] with 0x0ffffffc. The four s limbs are not clamped. Let L=min(in_len,16).

If L=0, leave h unchanged. Otherwise add m=(in_msg mod 2^(8*L))+2^(8*L) to h and split the sum into five base-B limbs h[0..4]. The observable accumulator uses the following lazy representative (not a canonical residue): set a[i]=clamped r[i], b[i]=5*(a[i]>>2), then

```text
x0 = a0*h0 + b0*h4 + b1*h3 + b2*h2 + b3*h1
x1 = a0*h1 + a1*h0 + b1*h4 + b2*h3 + b3*h2
x2 = a0*h2 + a1*h1 + a2*h0 + b2*h4 + b3*h3
x3 = a0*h3 + a1*h2 + a2*h1 + a3*h0 + b3*h4
x4 = (a0 & 3)*h4 + (x3 // B)
x3 = x3 mod B
x0 = x0 + 5*(x4 // 4)
x4 = x4 mod 4
```

Propagate base-B carries once from x0 through x3 into x4; these five limbs are out_h0..out_h4. The tag preview is (H mod (2^130-5) + S) mod 2^128, split into out_tag0..out_tag3, where H is that accumulator and S is the four-limb pad. An empty block still returns this tag preview. No authentication check or hidden key state is performed.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
