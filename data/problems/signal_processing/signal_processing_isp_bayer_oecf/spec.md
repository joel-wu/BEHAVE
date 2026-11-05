# Bayer OECF

Bayer OECF applies an opto-electronic conversion function to one raw Bayer sample per accepted input transaction. The block is a per-pixel lookup-table mapper: it selects the pixel's color plane from the Bayer phase, uses that plane's LUT to translate the input code, and clips the translated value to the sensor bit-depth range before output. This specification uses one LUT per color plane (`R`, `Gr`, `Gb`, `B`); all four LUTs are architecturally visible even if programmed identically.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_oecf #(
    parameter int BPP = 12,
    parameter int LUT_DATA_W = 16
) (
    input  logic                  clk,
    input  logic                  rst,

    input  logic                  in_valid,
    output logic                  in_ready,
    input  logic [BPP-1:0]        in_sample,
    input  logic [1:0]            in_x_lsb,
    input  logic [1:0]            in_y_lsb,
    input  logic [1:0]            in_bayer_pattern,

    output logic                  out_valid,
    input  logic                  out_ready,
    output logic [BPP-1:0]        out_sample,

    output logic                  r_lut_req_valid,
    output logic [BPP-1:0]        r_lut_req_addr,
    input  logic                  r_lut_req_ready,
    input  logic                  r_lut_resp_valid,
    input  logic [LUT_DATA_W-1:0] r_lut_resp_rdata,

    output logic                  gr_lut_req_valid,
    output logic [BPP-1:0]        gr_lut_req_addr,
    input  logic                  gr_lut_req_ready,
    input  logic                  gr_lut_resp_valid,
    input  logic [LUT_DATA_W-1:0] gr_lut_resp_rdata,

    output logic                  gb_lut_req_valid,
    output logic [BPP-1:0]        gb_lut_req_addr,
    input  logic                  gb_lut_req_ready,
    input  logic                  gb_lut_resp_valid,
    input  logic [LUT_DATA_W-1:0] gb_lut_resp_rdata,

    output logic                  b_lut_req_valid,
    output logic [BPP-1:0]        b_lut_req_addr,
    input  logic                  b_lut_req_ready,
    input  logic                  b_lut_resp_valid,
    input  logic [LUT_DATA_W-1:0] b_lut_resp_rdata
);
```

Each accepted transaction selects one of the four color-plane LUTs from the Bayer phase, issues a single read request to that plane's memory port with `<plane>_lut_req_addr = in_sample`, and emits the returned `<plane>_lut_resp_rdata` clipped to `BPP` bits. The other three LUT ports remain idle for that transaction.

Legal parameter configurations satisfy `1 <= BPP <= LUT_DATA_W <= 16`. The default is `(12,16)`.
Transfers occur on rising edges with reset inactive. An offered input and an
issued memory request remain stable until their respective ready signals. The
output remains valid and stable until accepted. Each accepted memory request
receives one unbackpressured, one-cycle response at least one cycle later, with
no unsolicited responses. The DUT has at most one outstanding read and preserves
pixel order; neither fixed compute latency nor back-to-back acceptance is required.

`in_bayer_pattern` encoding:
- `2'b00`: `rggb`
- `2'b01`: `bggr`
- `2'b10`: `grbg`
- `2'b11`: `gbrg`

External configuration memories:
- `r_lut`: depth `2**BPP`, element width `LUT_DATA_W`
- `gr_lut`: depth `2**BPP`, element width `LUT_DATA_W`
- `gb_lut`: depth `2**BPP`, element width `LUT_DATA_W`
- `b_lut`: depth `2**BPP`, element width `LUT_DATA_W`

### Behavior interface

```python
class BayerOECF:
    def __init__(self, BPP=12, LUT_DATA_W=16):
        pass

    def reset(self):
        pass

    def process(self, in_sample, in_x_lsb, in_y_lsb, in_bayer_pattern):
        pass
```

The returned dictionary contains:
- `out_sample`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sample`: BPP.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

The memory views self.r_lut, self.gr_lut, self.gb_lut and self.b_lut each contain
2**BPP unsigned LUT_DATA_W-bit words. Standalone construction/reset zeroes unbound
placeholders. The environment binds the configured images before processing and after
reset; zero placeholders do not prescribe calibration curves. Memory-request pins are
not return fields.

## Modules

- `signal_processing_isp_bayer_oecf`: top-level Bayer OECF lookup block.

## Functional Behavior

Each accepted input transaction represents one Bayer raw pixel sample and its 2x2 phase coordinates.

`in_sample` is an unsigned `BPP`-bit integer in the range `[0, 2**BPP - 1]`.

Only the least-significant bit of `in_x_lsb` and `in_y_lsb` is used to determine the pixel location inside the 2x2 Bayer tile:
- `x = in_x_lsb & 1`
- `y = in_y_lsb & 1`

The Bayer color at phase `(y, x)` is selected from `in_bayer_pattern` as follows.

For `in_bayer_pattern = 2'b00` (`rggb`):
- `(0,0) -> R`
- `(0,1) -> Gr`
- `(1,0) -> Gb`
- `(1,1) -> B`

For `in_bayer_pattern = 2'b01` (`bggr`):
- `(0,0) -> B`
- `(0,1) -> Gb`
- `(1,0) -> Gr`
- `(1,1) -> R`

For `in_bayer_pattern = 2'b10` (`grbg`):
- `(0,0) -> Gr`
- `(0,1) -> R`
- `(1,0) -> B`
- `(1,1) -> Gb`

For `in_bayer_pattern = 2'b11` (`gbrg`):
- `(0,0) -> Gb`
- `(0,1) -> B`
- `(1,0) -> R`
- `(1,1) -> Gr`

After selecting the color plane, the block reads the corresponding LUT at address `in_sample`. The selected LUT output is an unsigned `LUT_DATA_W`-bit integer:
- `lut_value = <selected_lut>[in_sample]`

The output sample is the LUT value clipped to the sensor bit-depth range `[0, 2**BPP - 1]`:
- if `lut_value > 2**BPP - 1`, `out_sample = 2**BPP - 1`
- otherwise, `out_sample = lut_value`

Because `lut_value` is unsigned, the lower bound clip to 0 has no effect.

The output is a 1:1 mapping: every accepted input transaction produces exactly one output transaction.

The four LUTs are independent memories. If software loads identical contents into
all four, this reproduces the archived source's shared `r_lut` configuration.
Independent color calibration completes the source's explicit per-channel-LUT
extension point; it does not change phase selection or clipping. The execution environment supplies each configured memory image before
processing pixels. No particular transfer function is prescribed.

## Reset Behavior

Reset is synchronous active-high.

Reset cancels pending pixels, requests and responses; canceled results cannot
reappear afterward. These LUTs are external read-only configuration: DUT reset
does not clear or write their contents. The execution environment restores the
declared initial memory images at every reset.

Handshake-visible outputs after reset and before the first accepted input:
- `out_valid = 0`
- `out_sample = 0`

The block has no other architectural state.
