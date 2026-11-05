# P-256 canonical reduction

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module cryptography_pk_classic_p256_partial_reduce (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [511:0] in_x,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_r
);
```

### Behavior interface

```python
class CryptographyPkClassicP256PartialReduce:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_r": ...}`, whose value is a Python integer holding the 256-bit output
port pattern. Latency is not encoded as extra calls.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`cryptography_pk_classic_p256_partial_reduce` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Return out_r = in_x mod p, where p = 2^256-2^224+2^192+2^96-1. The input is an unsigned 512-bit integer and the output is its unique representative in [0,p). Despite the legacy directory name, the result is a complete canonical reduction, not a partial limb fold. No elliptic-curve point operation is included.

## Reset Behavior

Reset rst is asynchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
