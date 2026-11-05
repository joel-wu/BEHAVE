# SipHash24Short15

Complete 64-bit SipHash-2-4 for a 0–15 byte message and an arbitrary 128-bit key.

## Interface

### RTL interface

```systemverilog
module SipHash24Short15 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_key,
    input logic [119:0] in_message,
    input logic [3:0] in_length,
    output logic [63:0] out_values
);
endmodule
```

### Behavior interface

```python
class SipHash24Short15:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_key, in_message, in_length): ...
```

Call `process(...)` once per accepted request with unsigned 128-bit `in_key`,
120-bit `in_message`, and 4-bit `in_length`. Every call returns exactly
`{"out_values": digest}`, where `digest` is an unsigned 64-bit integer with the
byte order defined below. Length zero returns the hash of an empty message,
not `None` or an empty list. Construction and `reset()` leave no persistent
hash state; `reset()` returns `None` without emitting a record.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`SipHash24Short15` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Complete 64-bit SipHash-2-4 for a 0–15 byte message and an arbitrary 128-bit key.

All byte strings are packed byte 0 at the low end. Only the first L=in_length message bytes are used; later bytes have no effect. The key is two little-endian 64-bit words k0,k1. Initialize (v0,v1,v2,v3) by xoring (k0,k1,k0,k1) into (0x736f6d6570736575,0x646f72616e646f6d,0x6c7967656e657261,0x7465646279746573).

A SipRound performs the following sequential updates (all additions modulo 2^64, all rotations on 64 bits): v0+=v1; v1=ROTL(v1,13) xor v0; v0=ROTL(v0,32); v2+=v3; v3=ROTL(v3,16) xor v2; v0+=v3; v3=ROTL(v3,21) xor v0; v2+=v1; v1=ROTL(v1,17) xor v2; v2=ROTL(v2,32). To absorb a word m, xor m into v3, apply TWO SipRounds, then xor m into v0.

If L>=8, first absorb the first eight bytes as a little-endian word. Always absorb a final word b=(L<<56) OR the remaining L mod 8 bytes, packed at its low end with unused bytes zero. Thus L=8 still absorbs a separate final length word; L=0 absorbs a zero word. Then xor 0xff into v2, apply FOUR SipRounds, and return v0 xor v1 xor v2 xor v3. This is the selected reference siphash(...,outlen=8), not its 128-bit or reduced-round variants. No state persists between requests.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

With key bytes `00,01,...,0f`, an empty message returns `0x726fdb47dd0e0e31`. A one-byte message `00` returns `0x74f839c593dc67fd`. These are packed 64-bit outputs.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
