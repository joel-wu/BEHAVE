# cvdp_copilot_apb_history_shift_register_0001

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module APBGlobalHistoryRegister(
    input logic pclk_level, presetn, pselx, penable, pwrite,
    input logic [9:0] paddr,
    input logic [7:0] pwdata,
    input logic history_shift_valid, clk_gate_en,
    output wire pready,
    output logic [7:0] prdata,
    output wire pslverr, history_full, history_empty, error_flag,
    output wire interrupt_full, interrupt_error
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, pclk_level, presetn, pselx, penable, pwrite, paddr, pwdata, history_shift_valid, clk_gate_en): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `error_flag`, `history_empty`, `history_full`, `interrupt_error`, `interrupt_full`,
  `pready`, `pslverr`: 1.
- `prdata`: 8.

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

- `APBGlobalHistoryRegister`: the stateful digital circuit below.

## Functional Behavior

This is an APB CSR block with an independently edge-triggered 8-bit
branch-history register. Keep all ten paddr bits: only exact addresses 0,1,2
are valid; e.g. 0x100 must NOT alias address 0.

| Address | Register | Writes / reads |
| --- | --- | --- |
| 0 | control_register | Store pwdata[3:0]; reserved upper bits read zero. |
| 1 | train_history | Store pwdata[6:0]; bit 7 reads zero. |
| 2 | predict_history | Read-only, all eight bits. A write is an error. |

The source pclk is renamed pclk_level. A low-transparent gate-enable latch
captures !clk_gate_en while pclk_level=0 and holds while high. The internal
APB clock is pclk_level AND that latched enable. Initial/reset enable is one;
clk_gate_en=1 stops APB clocks after a low phase, not the independent history
clock. A high-phase gate request cannot create or truncate a clock pulse.
Asynchronous active-low presetn clears all CSRs, prediction history, prdata and
the error latch, independently of either clock or gate state.

APB setup has pselx=1,penable=0; access has both high. At each enabled rising
pclk edge with pselx AND penable, perform one access using the current pins.
Reads capture the selected OLD register value into prdata; writes set prdata
to zero and update only the selected writable register. Invalid reads return
zero. Invalid accesses set an error latch; the next valid access clears it.
Idle/setup phases hold prdata and this error latch. pslverr, error_flag and
interrupt_error all report this latch, including after the transfer (the source
TB checks this retention). pready is one when reset is released and the clock
gate is enabled, otherwise zero. Legal enabled transfers have one setup cycle
and one access cycle, no added wait state. Raw pin sequences are also defined:
each access rising edge commits once; no transfer is invented without an edge.

Only a 0-to-1 transition of history_shift_valid updates predict_history. Holding
it high over multiple observations or APB clocks does not shift again. On that
edge, use OLD control_register and train_history: if bit 2 (mispredicted) is
set, load ((train_history << 1) | control_register[3]); otherwise if bit 0
(predict_valid) is set, shift predict_history left by one, append bit 1
(predict_taken), and keep the low eight bits. Otherwise hold. Misprediction
wins even when prediction-valid is also set. An independent history edge still
works while the APB clock is gated off. Coincident history/APB edges read one
common old CSR/history state, then commit their disjoint updates.

history_full=(predict_history==255), history_empty=(predict_history==0), and
interrupt_full=history_full are live outputs, not delayed transaction replies.

### Source completions

The source provides no reference RTL. Its gated-clock description is ambiguous
about the independent history event; the detailed rule says history changes
only on its own rising edge. The contract above preserves that rule and gates
only APB. The source assumes noncoincident events; the old-state rule explicitly
extends this to coincident digital events without an analog CDC claim.
Reset wording is corrected to assertion at presetn=0. Ready during reset/gating,
read-data hold and sticky-until-valid error behavior are made explicit. The
source TB's control value 0x0C has train_taken=1 despite one incorrect comment;
it restores 0xAB from train_history=0x55 as the actual bit equation requires.

### Explicit digital observation

Clock pins are ordinary physical levels supplied in every input record, not
automatically toggled domains. The RTL still contains real edge-sensitive and
clock-gating logic. All record fields change together and outputs are observed
after settling. A repeated high level is not another edge. Every relevant level
or data change must be supplied; there are no hidden clock pulses between calls.
Both clocks/history event levels initially equal zero. Internal state initially
equals reset state even when the first record releases reset: an explicit
completion of unspecified power-up state. Reset assertion takes effect without
any clock edge. Releasing reset at a held high level is not a new edge.

These rules define all digital input records, including coincident events; the
guarantees do not include analog metastability, propagation-delay glitches or
minimum pulse widths in seconds. No ready/valid wrapper may stop the underlying
clock-driven or history-triggered state updates.


## Reset Behavior

The physical active-low reset is an ordinary input and asynchronously restores the stated reset state. Initial state is the same as reset, with clock/history levels initially zero.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
