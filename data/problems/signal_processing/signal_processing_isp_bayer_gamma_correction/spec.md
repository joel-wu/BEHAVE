# Bayer Gamma Correction

This block performs per-sample gamma correction on a Bayer-domain pixel stream by table lookup. For each accepted input pixel code, the block uses the configured sensor bit depth to select one fixed lookup table and returns the table entry addressed by the input pixel value. The computation is purely a memoryless LUT mapping; no interpolation, arithmetic gamma approximation, or neighborhood processing is performed.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_gamma_correction #(
    parameter int BIT_DEPTH = 10
) (
    input  logic                 clk,
    input  logic                 rst,

    input  logic                 in_valid,
    output logic                 in_ready,
    input  logic [BIT_DEPTH-1:0] in_pixel,

    output logic                 out_valid,
    input  logic                 out_ready,
    output logic [BIT_DEPTH-1:0] out_pixel,

    output logic                 gamma_lut_req_valid,
    output logic [BIT_DEPTH-1:0] gamma_lut_req_addr,
    input  logic                 gamma_lut_req_ready,
    input  logic                 gamma_lut_resp_valid,
    input  logic [BIT_DEPTH-1:0] gamma_lut_resp_rdata
);
```

The block reads the LUT through a request/response read-only memory port. Each accepted input transaction issues exactly one read request with `gamma_lut_req_addr = in_pixel`; the returned `gamma_lut_resp_rdata` is the output pixel.

Transfers are sampled on rising clock edges with reset inactive. A source holds
`in_valid` and its pixel until accepted. The DUT holds a memory request and its
address until `gamma_lut_req_ready`, and holds `out_valid` and its pixel until
`out_ready`. Memory returns one unbackpressured, one-cycle response at least one
cycle after accepting its request; it sends no unsolicited responses. Results
remain in input order. No exact compute latency or initiation interval is required.

### Behavior interface

```python
class BayerGammaCorrection:
    def __init__(self, BIT_DEPTH=10):
        pass

    def reset(self):
        pass

    def process(self, in_pixel):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_pixel`: BIT_DEPTH.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

The memory view self.gamma_lut contains 2**BIT_DEPTH unsigned BIT_DEPTH-bit words.
Standalone construction/reset creates a zero-filled placeholder. The environment binds
the configured image before pixels and after reset; zeros are not a prescribed gamma
curve. Memory-request pins are not return fields.

## Modules

- `signal_processing_isp_bayer_gamma_correction`: top-level gamma-correction block. It accepts one Bayer pixel sample per input transaction and emits one gamma-corrected Bayer pixel sample per output transaction.

## Functional Behavior

The block operates on unsigned pixel codes of width `BIT_DEPTH`, where `BIT_DEPTH` shall be one of 8, 10, 12, or 14.

For the selected `BIT_DEPTH = N`, the block uses a single lookup table `gamma_lut` with exactly `2^N` entries. Every entry is an unsigned `N`-bit value. Table index `i` corresponds directly to input code `i`; there is no offset, scaling, clipping, interpolation, or arithmetic gamma computation.

For each accepted input transaction:

1. Interpret `in_pixel` as an unsigned `N`-bit integer.
2. Form index `idx = in_pixel`.
3. Read `gamma_lut[idx]`.
4. Drive `out_pixel = gamma_lut[idx]`.

The transfer function is therefore:
- `out_pixel = gamma_lut[in_pixel]`

The block is stateless with respect to pixel content. Each input transaction is independent of all previous transactions.

### Lookup-table contents

The LUT contents are external memory state served through the `gamma_lut_*` read-only memory port. The block does not generate a gamma curve internally and does not store the LUT inside the module.

The environment supplies the configured LUT image before processing pixels.
No particular gamma curve is prescribed. LUT entries must fit `BIT_DEPTH`;
unlike the source's generic uint16 container, this task uses N-bit output ports.

### Supported bit depths

- `BIT_DEPTH = 8`: LUT depth 256, output width 8 bits
- `BIT_DEPTH = 10`: LUT depth 1024, output width 10 bits
- `BIT_DEPTH = 12`: LUT depth 4096, output width 12 bits
- `BIT_DEPTH = 14`: LUT depth 16384, output width 14 bits

### Edge cases

- `in_pixel = 0` maps to `gamma_lut[0]`.
- `in_pixel = 2^N - 1` maps to `gamma_lut[2^N - 1]`.
- Because `in_pixel` is exactly `N` bits wide, every possible input code is a valid LUT address at the RTL interface.
- The block has no enable control. Every accepted input transaction produces one table lookup and one output transaction.

## Reset Behavior

Reset is synchronous active-high.

On reset:
- `out_valid` shall be `0`.
- `out_pixel` shall be `0`.

Architectural state after reset:
- No pixel-history, accumulator, or frame state is stored.
- `BIT_DEPTH` is unchanged. Reset cancels pending pixels, memory requests and
  responses; no canceled result may reappear after reset.
- The LUT is external, read-only configuration. DUT reset does not write it.
  The execution environment restores its declared initial memory image at each reset; this leaves the same configured LUT
  available to subsequent pixels.
- The block is ready to accept the next input transaction immediately after reset is deasserted.
