# SHA3_256Short31

Complete SHA3-256 of a 0..31 byte message: absorb, SHA3 domain separation, Keccak-f[1600] and squeeze.

## Interface

### RTL interface

```systemverilog
module SHA3_256Short31 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [247:0] in_message,
    input logic [4:0] in_length,
    output logic [255:0] out_values
);
endmodule
```

### Behavior interface

```python
class SHA3_256Short31:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_message, in_length): ...
```

Each `process` call represents one accepted request and returns exactly
`{"out_values": digest}`, where `digest` is an unsigned 256-bit word with
digest byte zero in its least-significant byte. `in_message` is an unsigned
248-bit word and `in_length` an unsigned 5-bit count. Length zero returns the
SHA3-256 digest of the empty message in the same dictionary, not `None`, a byte
string, or a list. The model retains no state between requests. Construction
and `reset()` have no numerical effect; `reset()` returns `None`. Idle and
output-stall cycles require no calls.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`SHA3_256Short31` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Ports carry unsigned bit patterns. Bytes are packed least-significant byte first.
Indices are zero-based. Every input bit pattern has a defined result.

Complete SHA3-256 of a 0..31 byte message: absorb, SHA3 domain separation, Keccak-f[1600] and squeeze.

Message byte i is bits 8*i; L=in_length, later bytes ignored. Rate is 136 bytes, capacity 512 bits. Start with zero 25-lane state; xor the L bytes into the rate, then xor 0x06 into byte L and 0x80 into byte 135. Apply all 24 Keccak-f rounds and return the first 32 state bytes. State lane x+5*y is little-endian 64-bit. This is SHA3-256, not legacy Keccak-256 (which has different padding), SHAKE or a raw permutation.

Round: C[x]=xor_y A[x,y]; D[x]=C[x-1] xor ROTL64(C[x+1],1); A[x,y]^=D[x]. Then B[y,(2*x+3*y) mod 5]=ROTL64(A[x,y],rho[x+5*y]). Set A[x,y]=B[x,y] xor ((~B[x+1,y]) & B[x+2,y]); xor RC[r] into A[0,0]. All x/y indices wrap modulo five, and lanes modulo 2^64. rho by lane index:
```
[0, 1, 62, 28, 27, 36, 44, 6, 55, 20, 3, 10, 43, 25, 39, 41, 45, 15, 21, 8, 18, 2, 61, 56, 14]
```
RC values in round order:
```
0000000000000001
0000000000008082
800000000000808a
8000000080008000
000000000000808b
0000000080000001
8000000080008081
8000000000008009
000000000000008a
0000000000000088
0000000080008009
000000008000000a
000000008000808b
800000000000008b
8000000000008089
8000000000008003
8000000000008002
8000000000000080
000000000000800a
800000008000000a
8000000080008081
8000000000008080
0000000080000001
8000000080008008
```

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

ASCII abc, L=3, gives digest bytes 3a985da74fe225b2045c172d6bd390bd855f086e3e9d525b46bfe24511431532.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
