# Python functional verification model

The specification defines the model class, constructor, input-method signatures,
output fields, numerical semantics, and reset interface. Use those names and
requirements. The model describes the task's computation, not the RTL's
cycle-by-cycle implementation. Its internal state need not match the RTL
registers.

For a stateful model, retain the same instance across the input sequence.
Invoke each method at the event defined by the specification. For transaction
interfaces, call it once per accepted input transaction. Other interfaces may
require a call on every clock edge of a domain or for each supplied input-level
record, including steps without a data transfer. Observe returned values at
the point defined by the task.

Each model call returns output records in one of these forms:

- `None` or an empty sequence for no output.
- A dictionary for one output record.
- A list or tuple of dictionaries for several output records.

This format applies to transaction interfaces, per-clock-edge observations and
input-level records alike. The specification determines which records each
call produces. Even a single-output model returns a dictionary, such as
`{"q": value}`, rather than the scalar `value`.

Dictionary keys are the output field names specified by the task. Fields
declared as integer bit patterns contain Python `int` values. For integer-valued
one-bit fields, return `0` or `1`. Internal state and conditions may use booleans,
with `int(flag)` converting a boolean to an integer output value. Apply the
task's specified widths and signed interpretation.

A call that returns no output can still update state. Keep expected outputs until
the RTL produces the corresponding observed transactions. Follow the task's
matching rule instead of assuming one output in the same cycle as each input.
For tasks with multiple interfaces or clock domains, use their specified
model entry points. Update model state and pending comparisons according to
the task's reset and cancellation rules, including which domains are affected.

When testing a transaction interface, include consecutive transfers, input gaps
and output stalls where allowed by the task. If the protocol requires valid
and data to remain stable while stalled, follow that rule in the testbench
driver and check it on the RTL. Check specified latency, throughput and
completion conditions separately from output-value matching.

If the RTL and verification model disagree, return to the specification to
determine the expected result. Making the two implementations agree is not
enough if both implement the same mistake.

For external-memory tasks, initialize the verification model from the same
initial data as the memory driver, with separate mutable storage. Compare the
task's output transactions and any memory result locations after the task's
specified completion event. The driver supplies memory responses, not the
reference computation. Its interface is documented in `/docs/memory.md`.

Python integer arithmetic is not implicitly restricted to an RTL bit width.
Apply wrapping, saturation, signed interpretation and rounding where the
specification requires them. These are task semantics, not a request to
imitate the chosen RTL microarchitecture. Floating-point bit patterns must
also follow the specification rather than Python's default float behavior.

Include the model's helper source files in the behavior submission. If you write
a testbench, include the verification model and its helper source files in its dependencies.
For any task-provided numeric library, use its documented semantics together
with the task specification.

## Language scope

Write deterministic Python computation over booleans, integers and finite data
structures, with explicit state and terminating loops. For exact analysis, Sim
must translate the supported Python operations into its typed Behavior IR while
preserving state updates, outputs and failures. It must also establish finite
bounds for reachable values and loop counts.

For multiple clock domains, Sim runs coincident `process_<domain>` handlers
from the same pre-event state and merges their domain-owned writes. Do not
infer coincident edges from Python call order or implement a second batching
mechanism. Persistent state must not contain cycles or share a mutable object
between different state paths, including objects inside saved snapshots.
Independent containers with equal values are supported; shared mutable state
is currently a Sim capability limitation, not a functional counterexample.
