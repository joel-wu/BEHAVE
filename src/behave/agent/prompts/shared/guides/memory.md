# External-memory driver

`from memory_driver import MemoryDriver` provides an external-memory
responder for cocotb simulations. The caller supplies initial data and task
inputs. The driver handles memory requests and responses, not the task's
computation.

The package is installed in the sandbox. Its source is readable at
`/runtime/site/memory_driver/`. Import it as a dependency.

## Supported interface

Use the task specification to choose memory names, port kinds, initialization
and legal timing. The driver binds these signals:

| Port kind | RTL request signals | Memory response signals |
| --- | --- | --- |
| `r` | `req_valid`, `req_addr` | `req_ready`, `resp_valid`, `resp_rdata` |
| `w` | `req_valid`, `req_addr`, `req_wdata` | `req_ready` |
| `rw` | `req_valid`, `req_addr`, `req_we`, `req_wdata` | `req_ready`, `resp_valid`, `resp_rdata` |

A single unnamed port uses `ports={None: "rw"}` and the prefix `<memory>_`.
Named ports use `ports={"a": "rw", "b": "r"}` and prefixes
`<memory>__a_` and `<memory>__b_`. For example, `ram__a_req_addr` is the
address signal of port `a` on memory `ram`. Widths are read from the DUT.
Ports on one memory share address and data widths.

Addresses are word indices. Each accepted write replaces a whole word.
For an `rw` port, `req_we=1` selects a write and `req_we=0` selects a read.
This interface has no byte-write mask, write acknowledgement or
response-ready signal.

## Memory semantics

- A request is accepted when `req_valid && req_ready` at the active edge.
- Reads snapshot the word before any write accepted at that same edge.
- Accepted reads return in FIFO order per port, at most one per active edge.
- `read_latency=L` means a read accepted at edge `k` is available for RTL to
  consume at edge `k+L`. It is a positive integer, defaulting to `1`.
- Every read response is presented for one active edge. Responses cannot be
  stalled. `resp_rdata` is zero while `resp_valid` is zero.
- Two accepted writes to the same address at the same edge raise a protocol
  error, including when their data match. Neither port has priority.
- `initial` maps word addresses to integer bit patterns and is copied.
  Unspecified words use deterministic, name/address/width-dependent
  unwritten-cell values, not zero. `memory.read(address)` returns the current
  value of a selected word, including an unwritten word.
- Domain reset preserves memory contents and previously accepted writes. It
  cancels pending reads and any visible response, and holds `req_ready`,
  `resp_valid` and `resp_rdata` at zero. No request is accepted during reset.
  After release, only new reads can produce responses. A new driver/memory
  instance starts from a new initial state.

## Clock and lifetime

`MemoryDriver(clock, *, period, unit="ns", edge="rising", reset=None,
reset_active_low=False)` manages one clock
domain. `edge` can also be `"falling"`. The driver generates this clock.
Other clock domains can use their own drivers or cocotb clocks.
Bind that domain's reset handle and polarity explicitly, for example
`reset=dut.rst_n, reset_active_low=True`. Use `reset=None` only for a domain
without reset. The testbench drives reset. Assertion cancels reads even between
clock edges and affects only memories served by that driver.

Before starting, call
`driver.add_memory(dut, name, *, ports, initial=None, read_latency=1, ready=None)`
for every memory on that domain. It returns the memory's inspection handle.
Different memories and driver instances have independent state.

`async with driver:` starts the clock and memory service. Exiting the context
cancels and awaits the service task. A driver error fails the cocotb test.
`driver.cycle` counts active edges completed since startup.

The clock starts inactive. At each active edge, the driver settles and samples
requests before changing the clock, so registered request updates in Verilator
are not mistaken for requests accepted on that same edge. It drives the next
memory responses and readiness at the inactive edge. Testbench inputs should
be driven before the active edge, and RTL outputs observed after settling.

`ready=None` keeps all request ports ready. Alternatively,
`ready(cycle, port_id)` returns `0` or `1` for each upcoming active edge,
starting at cycle zero. This controls request stalls, not response stalls.
For example, `lambda cycle, port: int(cycle % 4 != 1)` stalls every fourth
request opportunity. Choose a schedule allowed by the task.

## Example

This helper runs an `exercise(dut, memory)` coroutine with a two-port external
memory. Adapt the memory/port names and values to the specification. The
coroutine can drive reset and task inputs, wait for completion, and inspect
outputs or `memory.read(address)`.

```python
from memory_driver import MemoryDriver


async def run_with_memory(dut, exercise):
    driver = MemoryDriver(dut.clk, period=10, unit="ns", reset=dut.rst)
    memory = driver.add_memory(
        dut, "ram",
        ports={"a": "rw", "b": "rw"},
        initial={0: 17, 1: 42},
        read_latency=2,
    )
    async with driver:
        await exercise(dut, memory)
```
