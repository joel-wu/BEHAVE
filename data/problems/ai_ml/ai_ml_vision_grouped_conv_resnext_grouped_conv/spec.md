# ResNeXtGroupedConvolution

Compute one signed-int8 grouped 3x3 convolution output using persistent, runtime-configurable weights: four groups, four input channels and four output channels per group.

## Interface

### RTL interface

```systemverilog
module grouped_conv_resnext_grouped_conv (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [1:0]   in_group,
    input  logic [1:0]   in_out_chan_in_group,
    input  logic signed [7:0] in_act_0,
    input  logic signed [7:0] in_act_1,
    input  logic signed [7:0] in_act_2,
    input  logic signed [7:0] in_act_3,
    input  logic signed [7:0] in_act_4,
    input  logic signed [7:0] in_act_5,
    input  logic signed [7:0] in_act_6,
    input  logic signed [7:0] in_act_7,
    input  logic signed [7:0] in_act_8,
    input  logic signed [7:0] in_act_9,
    input  logic signed [7:0] in_act_10,
    input  logic signed [7:0] in_act_11,
    input  logic signed [7:0] in_act_12,
    input  logic signed [7:0] in_act_13,
    input  logic signed [7:0] in_act_14,
    input  logic signed [7:0] in_act_15,
    input  logic signed [7:0] in_act_16,
    input  logic signed [7:0] in_act_17,
    input  logic signed [7:0] in_act_18,
    input  logic signed [7:0] in_act_19,
    input  logic signed [7:0] in_act_20,
    input  logic signed [7:0] in_act_21,
    input  logic signed [7:0] in_act_22,
    input  logic signed [7:0] in_act_23,
    input  logic signed [7:0] in_act_24,
    input  logic signed [7:0] in_act_25,
    input  logic signed [7:0] in_act_26,
    input  logic signed [7:0] in_act_27,
    input  logic signed [7:0] in_act_28,
    input  logic signed [7:0] in_act_29,
    input  logic signed [7:0] in_act_30,
    input  logic signed [7:0] in_act_31,
    input  logic signed [7:0] in_act_32,
    input  logic signed [7:0] in_act_33,
    input  logic signed [7:0] in_act_34,
    input  logic signed [7:0] in_act_35,

    input  logic         cfg_valid,
    input  logic         cfg_we,
    input  logic [9:0]   cfg_addr,
    input  logic signed [7:0] cfg_wdata,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [21:0] out_sum
);
```

### Behavior interface

```python
class GroupedConvResNeXtGroupedConv:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_group,
        in_out_chan_in_group,
        in_act_0, in_act_1, in_act_2, in_act_3,
        in_act_4, in_act_5, in_act_6, in_act_7,
        in_act_8, in_act_9, in_act_10, in_act_11,
        in_act_12, in_act_13, in_act_14, in_act_15,
        in_act_16, in_act_17, in_act_18, in_act_19,
        in_act_20, in_act_21, in_act_22, in_act_23,
        in_act_24, in_act_25, in_act_26, in_act_27,
        in_act_28, in_act_29, in_act_30, in_act_31,
        in_act_32, in_act_33, in_act_34, in_act_35,
        cfg_valid, cfg_we, cfg_addr, cfg_wdata,
    ): ...
```

The Behavior arguments and returned fields are raw unsigned pin bit patterns; signed interpretation is specified below. One process call represents one accepted input. Each call returns exactly one output dictionary.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sum`: 22.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `grouped_conv_resnext_grouped_conv`: the complete operator and its input/output handshake.

## Functional Behavior

Select group g=in_group and output channel co=in_out_chan_in_group, both unsigned 0..3. The 36 activation fields represent one spatial patch of the selected group: in_act_(4*tap+ci), with tap=3*kernel_row+kernel_column, kernel rows/columns 0..2, and ci=0..3. Interpret activations and weights as two's-complement int8.

The architectural weight bank has 576 entries W[g,co,tap,ci]. Its linear address is ((g*4+co)*9+tap)*4+ci. All cfg_* fields belong to the same input transaction as the activations: an accepted request writes W at cfg_addr exactly when cfg_valid=cfg_we=1 and cfg_addr<576. Other addresses are ignored. The new weight is used by that same request, then retained for later requests. Neither idle cycles nor an unaccepted held request write weights; cfg_valid is not a separate handshake.

After the optional write, compute

    out_sum = sum(in_act_(4*tap+ci) * W[g,co,tap,ci]
                  for tap in 0..8 for ci in 0..3).

Return the exact signed sum in 22-bit two's-complement representation. Each product lies in [-16256,16384] and the 36-term sum in [-585216,589824], so no rounding, saturation or overflow occurs. Weights from other groups/output channels do not contribute. The input already supplies a patch; image buffering, padding, stride, dilation, batch normalization, activation and residual addition are outside this operator.

Input transfers occur on a rising clock edge when in_valid and in_ready are both 1; outputs transfer when out_valid and out_ready are both 1. The source holds every input field and in_valid until acceptance. The design holds every output field and out_valid until consumption. Each accepted input transaction produces exactly one output transaction, in input order. No fixed latency or internal pipeline is required. With reset inactive and the sink accepting outputs, pending work must eventually complete. Reset cancels pending work.

## Reset Behavior

Asynchronous, active-high rst. Reset clears out_valid and visible output payloads to zero and restores an input-ready empty state. Reset initializes W[g,co,tap,ci] to 1 when tap=4 and ci=0, and to 0 otherwise, for every group/output channel. All later configuration is discarded.
