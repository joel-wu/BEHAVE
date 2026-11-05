# vending_machine

Native six-state vending control with button-edge detection, payment, change and sequential error/refund pulses.

## Interface

### RTL interface

```systemverilog
module vending_machine (
    input logic clk,rst,item_button,
    input logic [2:0] item_selected,
    input logic [3:0] coin_input,
    input logic cancel,
    output logic dispense_item,return_change,
    output logic [4:0] item_price,change_amount,
    output logic [2:0] dispense_item_id,
    output logic error,return_money
);
```

### Behavior interface

```python
class VendingMachine:
    def __init__(self): ...
    def reset(self): ...
    def process(self, item_button, item_selected, coin_input, cancel): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `change_amount`, `item_price`: 5.
- `dispense_item`, `error`, `return_change`, `return_money`: 1.
- `dispense_item_id`: 3.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `vending_machine`: the complete native module above.

## Functional Behavior

All controls are sampled at every rising clk edge; there is no ready/valid
interface. item_button and cancel respond only to low-to-high changes between
successive edges. Their history is updated even while dispensing or refunding.
coin_input=0 means no coin; 1,2,5,10 are valid amounts, and a held nonzero value
represents another inserted coin on every payment edge. Other amounts are
invalid and are not credited.

The six states are IDLE, ITEM_SELECTION, PAYMENT_VALIDATION, DISPENSING_ITEM,
RETURN_CHANGE and RETURN_MONEY. In IDLE, a button rising edge enters selection.
Selection waits for item_selected=1..4; zero means no selection, and 5..7 cause
an error. As an explicit completion of the source's omitted price table, prices
are 5,10,15,20 respectively. Selection and payment are separate clock edges.
The selected price is visible throughout payment and dispensing.

During payment, a valid coin is credited. If the total meets the price, enter
DISPENSING_ITEM at that edge: dispense_item pulses for one period and its ID is
visible. On the next edge enter RETURN_CHANGE only if excess exists, pulsing
return_change with change_amount for one period; otherwise enter IDLE. The
change state returns to IDLE on the following edge. Inputs cannot add money or
cancel an already dispensed item during these two output states. Five credit
bits suffice: the largest reachable payment total is 29.

During PAYMENT_VALIDATION, a nonzero coin other than 1, 2, 5 or 10 triggers the
error/refund sequence below. The invalid coin adds no credit; any previously
accepted credit is refunded. A zero coin means no insertion and does not
trigger an error. The cancel priority below still applies.

A cancel rising edge in IDLE, selection or payment has priority over all other
inputs on that edge. Otherwise a nonzero coin before selection completes is an
error, with a valid unexpected coin included in the refund credit; an invalid
coin has no assigned monetary value. This condition takes priority over a
simultaneous item button or item selection. Additional item buttons after
leaving IDLE are ignored.

All errors/cancellations share the following explicit timing completion: at
the detecting edge enter RETURN_MONEY with error high for one period and
return_money low. At the next edge clear error and assert return_money for one
period iff credited money is nonzero. At the following edge clear transaction
state and return to IDLE. Controls during this sequence are ignored, apart from
edge-history tracking. The source inconsistently requests simultaneous and
sequential error/refund pulses; this task consistently uses sequential pulses.
No refund is asserted for an empty balance.

Only a change-return period exposes change_amount; otherwise it is zero.
dispense_item_id is nonzero only during dispensing; item_price is zero outside
payment/dispensing. All flags are low outside their stated periods. Asynchronous
reset clears all state and edge history immediately, without a dispense or
refund pulse. These native output periods must not be stretched by backpressure.


## Reset Behavior

`rst` is asynchronous, active high. Assertion immediately restores the initial state. IDLE, no selected item, zero credit and all outputs/history zero.
After reset release, resume the edge-by-edge behavior above.
