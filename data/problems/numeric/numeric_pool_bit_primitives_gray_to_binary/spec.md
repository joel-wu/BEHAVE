# Eight-bit Gray-to-binary conversion

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module gray_to_binary_8 (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_a,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_z
);
```

### Behavior interface

```python
class GrayToBinary8:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_z": ...}`, whose value is a Python integer holding the unsigned 8-bit
output. Latency is not encoded as extra calls.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`gray_to_binary_8` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

For unsigned input g, output bit b[i] is the XOR of g[j] for j=i..7. Thus the output is the binary index corresponding to the reflected Gray code g. All 256 inputs are defined. The task adds transaction transport to the source combinational converter; no particular XOR network is required.

## Reset Behavior

Reset rst is asynchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
