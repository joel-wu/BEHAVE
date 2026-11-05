# Sixteen-bit one-hot index mask

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module onehot_to_bin (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_onehot,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [3:0]  out_bin
);
```

### Behavior interface

```python
class OnehotToBin:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_onehot): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_bin": ...}`, whose value is a Python integer holding the unsigned
4-bit output. Latency is not encoded as extra calls.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`onehot_to_bin` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Return the bitwise OR of every index i in 0..15 whose input bit is set. Zero input returns zero. On a one-hot input this is the selected index; on a multi-hot input it is the OR of indices, not a priority encoder. Multi-hot behavior is an explicit total extension of the source one-hot/zero domain. The fixed 16-bit input and four-bit output are this task's specialization.

## Reset Behavior

Reset rst is asynchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
