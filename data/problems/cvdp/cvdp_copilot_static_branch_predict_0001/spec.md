# cvdp_copilot_static_branch_predict_0001

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module static_branch_predict (
    input logic [31:0] fetch_rdata_i, fetch_pc_i,
    input logic fetch_valid_i,
    output logic predict_branch_taken_o,
    output logic [31:0] predict_branch_pc_o
);
```

### Behavior interface

```python
class StaticBranchPredict:
    def __init__(self): ...
    def reset(self): ...
    def process(self, fetch_rdata_i, fetch_pc_i, fetch_valid_i): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `predict_branch_pc_o`: 32.
- `predict_branch_taken_o`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `static_branch_predict`: the complete combinational circuit.

## Functional Behavior

All inputs are unsigned bit patterns. Consume already-decompressed
32-bit instructions, not raw compressed halfwords. Decode opcode bits [6:0]:
0x6F is the JAL family, 0x67 the JALR family, 0x63 the conditional-branch family.
Other opcodes have offset zero. This is an opcode-based predictor, not an
instruction-legality checker or a register-data comparator.

JAL offset is the signed 21-bit concatenation
`{instr[31],instr[19:12],instr[20],instr[30:21],1'b0}`.
Branch offset is the signed 13-bit concatenation
`{instr[31],instr[7],instr[30:25],instr[11:8],1'b0}`.
JALR uses the signed 12-bit instr[31:20] for a PC-relative PREDICTION fallback.
There is no rs1 VALUE input: do not add its five-bit register index, invent a
hidden register file, or call this an architectural JALR target. This resolves
the source's contradictory JALR descriptions while preserving its native pins
and explicit PC-plus-immediate prediction rule.

predict_branch_pc_o=(fetch_pc_i+offset) modulo 2**32, even when not taken or
fetch_valid_i=0. A valid JAL/JALR predicts taken; a valid conditional branch
predicts taken exactly when its offset is negative. All other cases predict
not taken. A non-control opcode returns the unchanged PC. Instruction register
indices do not otherwise affect the prediction. Equivalent decompressed RV32C
control transfers use these same immediate layouts; decompression is outside
this module. The incorrect immediate extraction in the source worked example
is superseded by these explicit bit positions.

## Reset Behavior

There is no RTL reset or persistent state.
