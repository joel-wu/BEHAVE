# cvdp_copilot_apb_dsp_unit_0001

Implement an APB-configured byte arithmetic unit with independently triggered SRAM writes.

## Interface

### RTL interface

```systemverilog
module apb_dsp_unit(
    input logic pclk_level, presetn, pselx, penable, pwrite,
    input logic [9:0] paddr, input logic [7:0] pwdata,
    input logic sram_valid,
    output wire pready, output logic [7:0] prdata, output logic pslverr
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, pclk_level, presetn, pselx, penable, pwrite, paddr, pwdata, sram_valid): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `prdata`: 8.
- `pready`, `pslverr`: 1.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Input-level observations

Inputs are applied as explicit physical levels, then all settled output pins
are observed. Repeated identical inputs create separate observations but no
new signal transition. No automatic periodic clock is inserted between
observations. All input-level sequences are defined.

## Modules

- `apb_dsp_unit`: the stateful digital circuit below.

## Functional Behavior

This block configures byte-sized addition/multiplication over
an internal 1 KB SRAM through APB. A separate sram_valid edge commits SRAM
writes. Preserve both interfaces: APB configuration alone is not an SRAM write.

| APB byte address | Meaning | Access |
| --- | --- | --- |
| 0 | First operand's SRAM address | Read/write, eight bits |
| 1 | Second operand's SRAM address | Read/write, eight bits |
| 2 | Mode: 0 disabled, 1 add, 2 multiply, 3 SRAM writing | Read/write; values 0..3 only |
| 3 | SRAM write address | Read/write, eight bits |
| 4 | SRAM write data | Read/write, eight bits |
| 5 | Arithmetic result | Read only, eight bits |
| 6..1023 | SRAM byte at the exact APB address | Read only through APB |

Use all ten paddr bits; address 0x100 must not alias CSR address 0. The address
CSRs remain eight bits, matching the eight-bit configuration interface, so
indirect writes reach only SRAM addresses 6..255. The upper SRAM region is
readable and initially zero, not aliased into the low region. Locations 0..5
are reserved: their underlying SRAM bytes remain zero and an indirect write
there has no effect. A CSR can still name such a byte as an operand, yielding
zero rather than reading another CSR.

APB has a setup phase (pselx=1, penable=0) followed by an access phase (both 1).
pready is high whenever presetn=1, so a legal transfer needs no wait state.
At a rising pclk_level edge in access, a valid write changes its CSR and sets
prdata=0; a read captures the addressed old CSR/result/SRAM byte. A write to
address 5..1023 or mode value above 3 changes no target state and sets pslverr.
Every successful access clears pslverr. Idle/setup phases hold prdata and
pslverr. Each access edge in a raw pin sequence commits once.

On EVERY rising pclk_level edge, including idle/setup, mode 1 refreshes the
result with the sum of the two addressed SRAM bytes; mode 2 refreshes it with
their product. Keep the low eight bits (unsigned arithmetic modulo 256).
Modes 0 and 3 hold the result. Computation uses pre-edge mode, operand addresses
and SRAM contents. A read of address 5 on that edge returns the preceding
result. Updating configuration does not bypass this one-edge computation.

Only a 0-to-1 sram_valid transition, while presetn=1 and the pre-event mode is
3, writes the configured data byte to the configured allowed SRAM address.
No pclk edge is required. Holding sram_valid high does not write again. If an
APB edge and sram_valid rise coincide, both use the common old configuration
and memory: an APB read sees the old byte, and an SRAM write uses the old CSR
data. In other modes the SRAM event has no effect.

### Reset, observation and source completions

presetn is asynchronous active low. It clears CSRs, result, prdata and pslverr
and makes pready=0 without requiring either clock/event edge. It inhibits SRAM
writes but does not erase SRAM. Fresh execution initializes SRAM and registers
to zero. That initial memory and its reset retention are explicit completions
of unspecified source behavior, not a claim about physical SRAM power-up.

pclk is renamed pclk_level and supplied as a physical level in each input
record, alongside all other pins. The RTL retains its real pclk and SRAM event
blocks. Fields change together and all outputs are observed after settling;
held levels do not advance clocks. Initial pclk_level and sram_valid are zero.
All digital records are defined; no analog timing/glitch guarantee is implied.

No answer RTL is supplied upstream. Its TB explicitly reads SRAM at addresses
10, 11 and 100, contradicting the prompt's register-only error wording: SRAM
reads 6..1023 are therefore valid. The 1 KB size and eight-bit address CSRs are
both retained, with their reachability stated above. Arithmetic cadence,
coincidence, reserved SRAM bytes, invalid writes/modes and error/data retention
are completed explicitly. The reset prose is interpreted as assertion at
presetn=0. The previous three-bit transaction interface, implicit writes on
every configuration change, and transaction-only computation are removed.


## Reset Behavior

presetn=0 asynchronously clears CSRs, result, prdata and pslverr, and forces pready=0. SRAM retains its contents. Fresh execution initializes SRAM and all registers to zero.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
