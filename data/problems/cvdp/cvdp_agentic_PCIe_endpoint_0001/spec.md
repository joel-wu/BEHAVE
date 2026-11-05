# cvdp_agentic_PCIe_endpoint_0001

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module PCIe_endpoint #(
    parameter int ADDR_WIDTH=64,
    parameter int DATA_WIDTH=128
)(
    input logic clk_level, reset_n,
    input logic [DATA_WIDTH-1:0] pcie_rx_tlp,
    input logic pcie_rx_valid, pcie_tx_ready,
    output wire pcie_rx_ready,
    output logic [DATA_WIDTH-1:0] pcie_tx_tlp,
    output logic pcie_tx_valid,
    input logic dma_request,
    input logic [ADDR_WIDTH-1:0] dma_src, dma_dst,
    input logic [7:0] dma_words,
    output wire dma_ready,
    output logic dma_complete, dma_error, msix_interrupt,
    output wire mem_valid, mem_rsp_ready,
    output logic mem_write,
    output logic [ADDR_WIDTH-1:0] mem_addr,
    output logic [DATA_WIDTH-1:0] mem_wdata,
    input logic mem_ready, mem_rsp_valid, mem_error,
    input logic [DATA_WIDTH-1:0] mem_rdata
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self, ADDR_WIDTH=64, DATA_WIDTH=128): ...
    def reset(self): ...
    def process(self, clk_level, reset_n, pcie_rx_tlp, pcie_rx_valid, pcie_tx_ready, dma_request, dma_src, dma_dst, dma_words, mem_ready, mem_rsp_valid, mem_error, mem_rdata): ...
```

All pins are unsigned integers of the widths in the RTL interface. Each call applies one
input record, updates persistent state according to the rules below, and returns
one dictionary of all settled output pins. Repeated identical records are
separate observations but create no new signal transition. No automatic periodic
clock is inserted between calls. All input-level sequences are defined.

Every call returns one dictionary with exactly these keys:
`pcie_rx_ready`, `pcie_tx_tlp`, `pcie_tx_valid`, `dma_ready`, `dma_complete`,
`dma_error`, `msix_interrupt`, `mem_valid`, `mem_write`, `mem_addr`,
`mem_wdata`, and `mem_rsp_ready`. Flags are integers 0 or 1. `mem_addr` fits
`ADDR_WIDTH` bits; `pcie_tx_tlp` and `mem_wdata` fit `DATA_WIDTH` bits. All values
are nonnegative integers. Idle, clock-held and reset records also return this
complete dictionary, not `None` or an empty list. `reset()` restores the fresh
state with remembered clock level zero; ordinary pin-level reset follows
`reset_n` as specified below.

## Modules

- `PCIe_endpoint`: the stateful digital circuit below.

## Functional Behavior

Implement the source's transaction endpoint, independent DMA command and
DMA-completion interrupt. Preserve ADDR_WIDTH=64 and DATA_WIDTH=128 defaults.
Require ADDR_WIDTH>=1 and DATA_WIDTH>=ADDR_WIDTH+9. All pins operate on rising
clk_level, with asynchronous active-low reset_n. Clock holds are not edges.

### Source-inspired executable subset

The original prompt names PCIe packet/data-link/DMA/interrupt blocks but supplies
no packet bit layout, DMA descriptor format, external-memory handshake, error
reply, acknowledgement input, or answer RTL. Its read test assigns the expected
value to its own read_data variable without checking a DUT response. It cannot
serve as a functional oracle. The old converted task removed dma_request,
dma_complete and msix_interrupt and substituted a private 256-word aliased RAM.

This task completes the original concept with a finite SINGLE-FLIT command
format and a serial memory port shared by host requests and a forward-copy DMA.
It is NOT a PCIe-compliant TLP/DLLP/PHY implementation, enumeration engine,
credit manager, or MSI-X table implementation. No unsupported PCIe compliance
claim is inferred from the module or source name. The original packet pins,
independent DMA request/completion and interrupt are retained; explicit DMA
descriptor/status and external memory pins are added below. Original clk and
rst_n are spelled clk_level and reset_n so the observation interface can supply
their actual levels. There is no added whole-module ready/valid wrapper.

### Packet format and endpoint queue

Let P=DATA_WIDTH-ADDR_WIDTH-8. A command flit is {opcode[7:0], address[ADDR_WIDTH-1:0],
payload[P-1:0]}, with opcode at the most-significant end. Addresses denote whole
DATA_WIDTH-bit memory WORDS, not PCIe byte addresses. Opcode 0x01 writes the
zero-extended payload to the given word; 0x02 reads the word. There is no private
RAM and no low-address aliasing. All ADDR_WIDTH address bits reach the memory port.

At a rising edge a packet is accepted iff pcie_rx_valid && pcie_rx_ready. Ready
is high only when reset_n=1, the memory engine is idle, no response flit is
pending, and dma_request=0. Capture the entire accepted command once. The write
reply is {0x81,address,original_payload}; the read reply is {0x82,address,low_P_bits
of returned_word}. An unknown opcode returns {0xff,address,0} without touching
memory. A memory error returns {0xfe,address,0}, whether the command was read or
write. Memory responses do not change any other packet field.

pcie_tx_valid and the complete pcie_tx_tlp remain stable until a rising edge with
pcie_tx_ready=1. Clearing a response does not also accept a new command on that
same edge (one idle-edge bubble). The flit bits hold when valid clears. There is
no separate link NACK input: backpressure means HOLD, not resend or duplicate a
memory transaction. This completes the source's unimplementable WAIT_ACK/RETRY
wording using its actual ready pin.

### Observable external memory port

Outputs mem_valid, mem_write, mem_addr and mem_wdata form one request. They hold
while mem_valid=1 and mem_ready=0. A rising edge with both high accepts it exactly
once, clears mem_valid, and enters the response phase. In that phase mem_rsp_ready
is high; accept mem_rsp_valid, mem_rdata and mem_error only on a later rising edge
with mem_rsp_ready=1. A same-edge request/response is not supported; unsolicited
responses are ignored. Every read AND every write requires a response. Only one
request may be outstanding. mem_wdata is zero for reads. Request fields retain
their last value while mem_valid=0. Check write address, full write data, direction
and acceptance events, not just the final completion flag. External memory is
environment-owned; reset never promises to undo an already accepted write.

### Independent DMA command

Added descriptor inputs dma_src, dma_dst and dma_words[7:0] describe a forward
word copy. Added dma_ready and dma_error report command availability and errors.
dma_ready is high when reset_n=1, the engine is idle and no TX flit is pending.
Accept dma_request only on a LOW-to-HIGH sampled transition at an edge whose
PRE-edge dma_ready=1. It takes priority over a packet. Requests while busy are
not queued; they must be deasserted and raised again after ready. Holding a request
high does not start another DMA. Descriptor inputs are captured on acceptance.

For i=0..dma_words-1, read (dma_src+i) modulo 2^ADDR_WIDTH; after the successful
response, write the FULL DATA_WIDTH-bit word to (dma_dst+i) modulo 2^ADDR_WIDTH;
wait for the write response before proceeding. No burst, snapshot copy or memmove
semantics is implied: overlapping forward copies can read earlier copied words.
Zero words completes immediately without a memory access. Successful completion
pulses dma_complete and msix_interrupt for one clock interval. A failed read or
write stops at that word, pulses dma_complete and dma_error, and does not pulse
msix_interrupt. Previous completed writes are not rolled back. DMA never emits
a packet response. TX backpressure cannot lose a pending host response; DMA and
host commands share the same single-outstanding engine and are never interleaved.

### Reset and observation

Asserting reset_n=0 asynchronously cancels pending commands, memory requests,
responses and DMA progress; clear all output registers and sampled request
history. Both ready outputs and mem_rsp_ready are zero during reset. A clock
held high at reset release does not generate an edge. Fresh state equals reset
state. All arithmetic on addresses/data is explicitly bounded by the declared
widths. Every observation reports all public pins, including memory write data
and the DMA/interrupt/error pulses. Finite tests do not establish physical-link
PCIe compliance or unbounded equivalence.


## Reset Behavior

The source active-low reset is asynchronous. Original clk/rst_n are explicitly named clk_level/reset_n; reset cancels outstanding DUT work but cannot undo accepted external memory writes. Fresh clock level is zero.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
